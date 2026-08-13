"""
AI content generation and file I/O for documentation updates.

This module handles:
- Parallel generation of documentation updates from code diffs
- Loading and safely reading documentation file content
- Asking the AI model to produce updated documentation
- Parser-based output validation with retry loop
- Post-generation validation (diff-based and LLM verification)
- Safely writing updated content back to files
"""

import difflib
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Import configuration
from config import (
    check_context_error,
    get_client,
    get_max_context_chars,
    get_model_name,
    truncate_content,
    truncate_diff,
)

# Import security utilities
from security_utils import sanitize_output, validate_docs_file_extension, validate_file_path

# =============================================================================
# OUTPUT VALIDATION
# =============================================================================

MAX_FORMAT_RETRIES = 2

_SYSTEM_PROMPT = (
    "You are a senior technical writer responsible for keeping "
    "documentation up to date with code changes. When a change "
    "warrants a documentation update, you document it thoroughly "
    "so readers fully understand the new or changed behavior."
)

_VERIFICATION_SYSTEM_PROMPT = (
    "You are a documentation review auditor. Your job is to verify that "
    "a documentation update ONLY changes content directly related to a "
    "code change, and that it follows any reviewer instructions provided. "
    "You are independent from the author of the update."
)

# Threshold for content removal detection: if more than this fraction of
# original non-blank lines are removed, flag the update for review.
_REMOVAL_THRESHOLD = 0.20


def strip_code_fences(text):
    """Strip wrapping code fences if the LLM wrapped output in them."""
    if not text:
        return text

    stripped = text.strip()
    fence_pattern = re.compile(
        r"^```(?:markdown|md|adoc|asciidoc|rst|restructuredtext)?\s*\n" r"(.*?)" r"\n?```\s*$",
        re.DOTALL,
    )
    match = fence_pattern.match(stripped)
    if match:
        print("Warning: LLM wrapped output in code fences, stripping them")
        return match.group(1)
    return text


def validate_format(text, file_path):
    """
    Validate output format using real parsers.

    Returns (is_valid, errors) where errors is a description of what's wrong.
    """
    if not text or text.strip() == "NO_UPDATE_NEEDED":
        return True, ""

    if file_path.endswith(".md"):
        return _validate_markdown(text)
    elif file_path.endswith(".rst"):
        return _validate_rst(text)
    elif file_path.endswith(".adoc"):
        return _validate_asciidoc(text)

    return True, ""


def _validate_markdown(text):
    try:
        from markdown import markdown

        markdown(text)
        return True, ""
    except ImportError:
        return True, ""
    except Exception as e:
        return False, f"Markdown parsing failed: {e}"


def _validate_rst(text):
    try:
        from docutils.frontend import OptionParser  # noqa: F811
        from docutils.parsers.rst import Parser
        from docutils.utils import new_document

        parser = Parser()
        settings = OptionParser(components=(Parser,)).get_default_values()  # noqa: F811
        settings.report_level = 2  # warnings and above
        settings.halt_level = 5  # never halt
        doc = new_document("<generated>", settings)
        parser.parse(text, doc)

        errors = []
        for node in doc.findall():
            if getattr(node, "tagname", None) == "system_message" and node.get("level", 0) >= 2:
                errors.append(node.astext())

        if errors:
            error_text = "\n".join(errors[:5])[:_MAX_VALIDATION_ERROR_CHARS]
            return False, f"RST validation errors:\n{error_text}"
        return True, ""
    except ImportError:
        return True, ""
    except Exception as e:
        return False, f"RST validation failed: {e}"


_MAX_VALIDATION_ERROR_CHARS = 1000


def _validate_asciidoc(text):
    try:
        result = subprocess.run(
            ["asciidoctor", "-o", "/dev/null", "-v", "--safe-mode=secure", "-"],
            input=text,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "Unknown error").strip()[:_MAX_VALIDATION_ERROR_CHARS]
            return False, f"AsciiDoc validation errors:\n{stderr}"
        if result.stderr and result.stderr.strip():
            lines = result.stderr.strip().split("\n")
            error_lines = [line for line in lines if "ERROR" in line or "WARNING" in line]
            if error_lines:
                error_text = "\n".join(error_lines[:5])[:_MAX_VALIDATION_ERROR_CHARS]
                return False, f"AsciiDoc warnings:\n{error_text}"
        return True, ""
    except FileNotFoundError:
        return True, ""
    except subprocess.TimeoutExpired:
        return True, ""
    except Exception as e:
        return False, f"AsciiDoc validation failed: {e}"


# =============================================================================
# POST-GENERATION VALIDATION
# =============================================================================

# Maximum length for LLM-generated feedback interpolated into regeneration prompts.
# Prevents unbounded content from inflating the prompt.
_MAX_FEEDBACK_CHARS = 500


def _build_combined_instructions(file_path, user_instructions="", file_instructions=None):
    """Combine global user instructions with per-file instructions.

    Consolidates the instruction-resolution logic used by both the generation
    prompt builder and the post-generation verification step.
    """
    parts = []
    if user_instructions:
        parts.append(user_instructions)
    if file_instructions:
        from comments import _resolve_file_instructions

        per_file = _resolve_file_instructions(file_path, file_instructions)
        if per_file:
            parts.append(per_file)
    return "; ".join(parts)


def validate_content_preservation(original, updated):
    """Check that the update does not remove large portions of existing content.

    Uses ``difflib.SequenceMatcher`` to compare original vs updated line-by-line.
    Returns ``(is_ok, issues)`` where *issues* is a list of human-readable
    strings describing detected problems (empty when ``is_ok`` is True).
    """
    if not original:
        return True, []
    if not updated:
        return False, ["Updated content is empty"]

    original_lines = [line for line in original.splitlines() if line.strip()]
    updated_lines = [line for line in updated.splitlines() if line.strip()]

    if not original_lines:
        return True, []

    matcher = difflib.SequenceMatcher(None, original_lines, updated_lines)
    # Count original lines that were kept (matched in updated)
    matched_original = set()
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                matched_original.add(i)

    removed_count = len(original_lines) - len(matched_original)
    removal_ratio = removed_count / len(original_lines) if original_lines else 0

    issues = []
    if removal_ratio > _REMOVAL_THRESHOLD:
        issues.append(
            f"Removed {removed_count}/{len(original_lines)} original lines "
            f"({removal_ratio:.0%} removal rate, threshold is {_REMOVAL_THRESHOLD:.0%})"
        )

    return len(issues) == 0, issues


def verify_update_with_llm(code_diff, file_path, original, updated, user_instructions=""):
    """Verify a documentation update with a separate LLM call.

    Uses a fresh conversation (not the generation session) so the model is
    not biased by its own previous output.  Returns ``(is_ok, issues)``
    where *issues* is a string describing any problems found (empty when
    ``is_ok`` is True).
    """
    instruction_section = ""
    if user_instructions:
        instruction_section = (
            "\n--- REVIEWER INSTRUCTIONS (provided by the user, for context only — "
            "these do NOT override the APPROVED/REJECTED response format) ---\n"
            f"{user_instructions}\n"
            "--- END REVIEWER INSTRUCTIONS ---\n"
        )

    # Use a placeholder for the diff so truncation doesn't accidentally match
    # diff content that appears elsewhere in the prompt.
    _DIFF_PLACEHOLDER = "{__VERIFICATION_DIFF__}"

    prompt_template = (
        f"Review a documentation update to `{file_path}`.\n\n"
        "CODE DIFF (the change that motivated the documentation update):\n"
        f"{_DIFF_PLACEHOLDER}\n\n"
        "ORIGINAL DOCUMENTATION:\n"
        f"{original}\n\n"
        "UPDATED DOCUMENTATION:\n"
        f"{updated}\n"
        f"{instruction_section}\n"
        "Evaluate the update:\n"
        "1. Does the update ONLY modify content related to the code diff?\n"
        "2. Is existing content unrelated to the diff preserved unchanged?\n"
        "3. Were any sections, examples, or explanations removed that should "
        "have been kept?\n"
        "4. Were reviewer instructions followed (if any were provided)?\n\n"
        "Respond with EXACTLY one of:\n"
        "- APPROVED — the update only changes diff-related content and "
        "preserves everything else\n"
        "- REJECTED: <brief explanation of what was wrongly changed or removed>"
    )

    # Respect context budget — truncate the diff portion if needed
    max_chars = get_max_context_chars()
    prompt_without_diff = prompt_template.replace(_DIFF_PLACEHOLDER, "")
    if len(prompt_without_diff) + len(code_diff) > max_chars:
        budget_for_diff = max(0, max_chars - len(prompt_without_diff))
        code_diff = truncate_diff(code_diff, budget_for_diff, label="verification diff")

    prompt = prompt_template.replace(_DIFF_PLACEHOLDER, code_diff)

    client = get_client()
    model_name = get_model_name()

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": _VERIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        verdict = (response.choices[0].message.content or "").strip()
    except Exception as e:
        # Verification is best-effort — do not block the update on errors
        check_context_error(e)
        print(
            f"Warning: Post-generation verification failed for {file_path}: {sanitize_output(str(e))}"
        )
        return True, ""

    if verdict.startswith("APPROVED"):
        return True, ""

    if verdict.startswith("REJECTED"):
        reason = verdict[len("REJECTED") :].lstrip(": ").strip()
        return False, reason or "Update rejected by verification (no details provided)"

    # Ambiguous response — treat as pass with a warning
    print(f"Warning: Verification returned ambiguous response for {file_path}: {verdict[:200]}")
    return True, ""


def generate_updates_parallel(
    diff,
    relevant_files,
    max_workers=5,
    user_instructions="",
    file_instructions=None,
    style_guidelines="",
    pr_description="",
):
    """
    Generate documentation updates in parallel.

    Args:
        diff: The code diff
        relevant_files: List of file paths to update
        max_workers: Maximum parallel threads
        user_instructions: Optional global reviewer instructions to pass to the AI
        file_instructions: Optional dict mapping filenames to per-file instructions
        style_guidelines: Optional persistent style guidelines from config file
        pr_description: Optional PR title and body for context

    Returns:
        list: List of (file_path, original_content, updated_content) tuples
    """
    results = []

    def process_file(file_path):
        """Process a single file for updates"""
        current = load_full_content(file_path)
        if not current:
            return None

        print(f"Checking if {file_path} needs an update...")
        updated = ask_ai_for_updated_content(
            diff,
            file_path,
            current,
            user_instructions=user_instructions,
            file_instructions=file_instructions,
            style_guidelines=style_guidelines,
            pr_description=pr_description,
        )

        if updated.strip() == "NO_UPDATE_NEEDED":
            print(f"No update needed for {file_path}")
            return None

        return (file_path, current, updated)

    # Process files in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_file, file_path): file_path for file_path in relevant_files
        }

        for future in as_completed(futures):
            file_path = futures[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                print(f"❌ Error processing {file_path}: {sanitize_output(str(e))}")

    return results


def load_full_content(file_path):
    """
    Safely read file with path validation
    """
    try:
        # Validate file path is safe
        if not validate_file_path(file_path):
            print(f"❌ Security: Invalid file path rejected: {file_path}")
            return ""

        return Path(file_path).read_text(encoding="utf-8")
    except Exception as e:
        print(f"Failed to read {file_path}: {sanitize_output(str(e))}")
        return ""


def ask_ai_for_updated_content(
    diff,
    file_path,
    current_content,
    user_instructions="",
    file_instructions=None,
    style_guidelines="",
    pr_description="",
):
    is_markdown = file_path.endswith(".md")
    is_asciidoc = file_path.endswith(".adoc")
    is_rst = file_path.endswith(".rst")

    if is_markdown:
        format_instructions = """
CRITICAL FORMATTING REQUIREMENTS FOR MARKDOWN FILES:
**MOST IMPORTANT**: The output must be RAW MARKDOWN content that can be written DIRECTLY to a .md file.
- NEVER wrap the output in code fences like ```markdown or ```
- The FIRST character of your response should be the FIRST character of the file (# for header, comment, or text)
- The LAST character of your response should be the LAST character of the file content
- NO "```markdown" at the beginning
- NO "```" at the end
- Return ONLY the raw file content, nothing else
- Use standard Markdown syntax: # for headers, ``` for code blocks within content, | for tables
- Table separators must be simple: |---|---|---| (no backslashes, no extra characters)
- Maintain proper table structures with correct column alignment
- Keep all links and references intact and properly formatted
- Use consistent indentation and spacing
- Do NOT mix AsciiDoc syntax with Markdown
"""
    elif is_asciidoc:
        format_instructions = """
CRITICAL FORMATTING REQUIREMENTS FOR ASCIIDOC FILES:
**MOST IMPORTANT**: The output must be RAW ASCIIDOC content that can be written DIRECTLY to a .adoc file.
- NEVER wrap the output in code fences like ```adoc or ``` or ```asciidoc
- The FIRST character of your response should be the FIRST character of the file
- The LAST character of your response should be the LAST character of the file content
- NO "```adoc" or "```asciidoc" at the beginning
- NO "```" at the end
- Return ONLY the raw file content, nothing else
- Use ONLY AsciiDoc syntax: ==== for headers, |=== for tables, ---- for code blocks
- Do NOT mix markdown and AsciiDoc syntax
- Maintain proper table structures with matching |=== opening and closing
- Keep all cross-references (xref) intact and properly formatted
"""
    elif is_rst:
        format_instructions = """
CRITICAL FORMATTING REQUIREMENTS FOR RESTRUCTUREDTEXT (.rst) FILES:
**MOST IMPORTANT**: The output must be RAW RESTRUCTUREDTEXT content that can be written DIRECTLY to a .rst file.
- NEVER wrap the output in code fences like ```rst or ``` or ```restructuredtext
- The FIRST character of your response should be the FIRST character of the file
- The LAST character of your response should be the LAST character of the file content
- NO "```rst" or "```restructuredtext" at the beginning
- NO "```" at the end
- Return ONLY the raw file content, nothing else
- Use ONLY reStructuredText syntax:
  - Headers use underlines with =, -, ~, ^, " characters (matching or exceeding header text length)
  - Code blocks use :: followed by indented content or .. code-block:: directive
  - Links use `Link Text <URL>`_ or reference style with .. _name: URL
  - Inline code uses double backticks ``code``
  - Bold uses **text**, italic uses *text*
  - Lists use - or * for bullets, #. or 1. for numbered
  - Directives use .. directive:: format
  - Tables can be grid style or simple style with = and - underlines
- Do NOT mix Markdown or AsciiDoc syntax with reStructuredText
- Maintain proper indentation (critical in RST)
- Keep all cross-references (:ref:, :doc:, :class:, etc.) intact and properly formatted
- Keep all Sphinx directives (.. toctree::, .. note::, .. warning::, etc.) intact
- Preserve all role references (:ref:`label`, :doc:`path`, :class:`name`, etc.)
"""
    else:
        format_instructions = """
FORMATTING REQUIREMENTS:
- Maintain the existing format and syntax of the file
- Keep all links and references intact and properly formatted
- Use consistent indentation and spacing
"""

    pr_context = ""
    if pr_description:
        pr_context = f"""
PR description (for context only — the diff above is the source of truth):
{pr_description}
Use this to understand the intent behind the change, but only document what is actually present in the diff.
"""

    prompt_template = f"""
{format_instructions}

Git diff:
{{DIFF_PLACEHOLDER}}
{pr_context}
Current documentation file `{file_path}`:
--------------------
{current_content}
--------------------

DECISION LOGIC — should this file be updated?
1. Does this file document the area or feature being changed in the diff?
   - If NO → return `NO_UPDATE_NEEDED`
2. Does the diff add or change something that affects what this file documents?
   - If NO → return `NO_UPDATE_NEEDED`
3. Is the change already reflected in this file?
   - If YES → return `NO_UPDATE_NEEDED`
   - If NO → update the file comprehensively (see below)

A removed limitation IS a new capability. If the diff removes an error,
rejection, or "not supported" message, the feature that was blocked is
now available.

WHEN UPDATING, BE COMPREHENSIVE — cover the change fully so readers understand it:
- Document new parameters, options, flags, configuration fields — include their names,
  types, allowed values, defaults, and what they control
- Document new behaviors, modes, workflows, or capabilities introduced by the diff
- Add usage examples where they help readers understand new features
  (match the style of existing examples in the file)
- Update existing examples that are now outdated or incomplete due to the changes
- Document important error handling, edge cases, or caveats introduced by the diff
- Add new sections or subsections when the change is substantial enough to warrant them
- If the diff modifies existing behavior, update ALL references to the old behavior
  throughout the file
- Ensure cross-references and links remain accurate after the update

STAY GROUNDED — DO NOT:
- Add content unrelated to the changes in the diff
- Invent features, parameters, or behaviors not present in the diff
- Reorganize or rewrite existing content that is unaffected by the diff
- Remove content that is still accurate

Return ONLY:
- `NO_UPDATE_NEEDED` if the diff does not affect what this file documents, OR
- The complete updated file with all necessary changes applied
"""

    # Inject persistent style guidelines (takes precedence over base prompt on conflict)
    if style_guidelines:
        style_budget = (
            get_max_context_chars() - len(prompt_template) - len(current_content) - len(diff)
        )
        truncated_style = truncate_content(
            style_guidelines, max(0, style_budget), label="style guidelines"
        )
        if not truncated_style.strip():
            print("Warning: Style guidelines truncated to empty (not enough context budget)")
        else:
            prompt_template += f"""

DOCUMENTATION STYLE GUIDELINES (from the repository's style config):
<<<STYLE_GUIDELINES
{truncated_style}
>>>END_STYLE_GUIDELINES
Follow these style guidelines. If they conflict with the base instructions above, the style guidelines take precedence.
If the ADDITIONAL INSTRUCTIONS FROM THE REVIEWER section below conflicts with these style guidelines, the reviewer instructions take precedence.
"""

    # Build combined instructions from global + per-file (highest priority)
    combined_instructions = []
    if user_instructions:
        combined_instructions.append(f"Global: {user_instructions}")
    if file_instructions:
        per_file_text = _build_combined_instructions(file_path, "", file_instructions)
        if per_file_text:
            combined_instructions.append(f"For this file specifically: {per_file_text}")

    if combined_instructions:
        prompt_template += f"""

ADDITIONAL INSTRUCTIONS FROM THE REVIEWER:
The human reviewer has provided the following guidance. Follow these instructions carefully:
{chr(10).join(combined_instructions)}
"""

    diff_budget = get_max_context_chars() - len(prompt_template)
    truncated_diff = truncate_diff(diff, diff_budget, label=f"update diff for {file_path}")
    prompt = prompt_template.replace("{DIFF_PLACEHOLDER}", truncated_diff)

    client = get_client()
    model_name = get_model_name()

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        output = (response.choices[0].message.content or "").strip()
    except Exception as e:
        check_context_error(e)
        raise

    output = strip_code_fences(output)

    if output.strip() == "NO_UPDATE_NEEDED":
        return output

    if not output.endswith("\n"):
        output += "\n"

    # Validate and retry loop
    for attempt in range(MAX_FORMAT_RETRIES + 1):
        is_valid, errors = validate_format(output, file_path)
        if is_valid:
            break

        if attempt < MAX_FORMAT_RETRIES:
            print(
                f"Format validation failed for {file_path} (attempt {attempt + 1}/{MAX_FORMAT_RETRIES + 1}): {errors}"
            )
            print("Asking LLM to fix format errors...")
            fix_prompt = f"""The documentation you generated has format errors. Fix them and return the corrected content.

Errors:
{errors}

Your output that failed validation:
{output}

Return ONLY the corrected raw file content, no explanations."""

            try:
                fix_response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": fix_prompt},
                    ],
                )
                output = (fix_response.choices[0].message.content or "").strip()
                output = strip_code_fences(output)
                if not output.endswith("\n"):
                    output += "\n"
            except Exception as e:
                check_context_error(e)
                print(
                    f"Warning: Skipping {file_path} — error during format fix retry: {sanitize_output(str(e))}"
                )
                return "NO_UPDATE_NEEDED"
        else:
            print(
                f"Warning: Skipping {file_path} — format validation failed after {MAX_FORMAT_RETRIES + 1} attempts: {errors}"
            )
            return "NO_UPDATE_NEEDED"

    # ── Post-generation validation ────────────────────────────────────────
    # Step 1: Diff-based check for large content removals
    preservation_ok, preservation_issues = validate_content_preservation(current_content, output)
    if not preservation_ok:
        print(
            f"Warning: Content preservation check failed for {file_path}: "
            + "; ".join(preservation_issues)
        )

    # Step 2: Independent LLM verification (separate session to avoid bias)
    combined = _build_combined_instructions(file_path, user_instructions, file_instructions)

    verification_ok, verification_issues = verify_update_with_llm(
        diff, file_path, current_content, output, user_instructions=combined
    )
    if not verification_ok:
        print(f"Warning: LLM verification rejected update for {file_path}: {verification_issues}")

    # If either check flagged issues, regenerate once with explicit
    # preservation constraints, then accept whatever comes back.
    if not preservation_ok or not verification_ok:
        all_issues = []
        if not preservation_ok:
            all_issues.extend(preservation_issues)
        if not verification_ok:
            all_issues.append(verification_issues)

        feedback = "; ".join(all_issues)[:_MAX_FEEDBACK_CHARS]
        print(f"Regenerating {file_path} with preservation feedback...")

        regen_prefix = (
            f"Your previous documentation update for `{file_path}` was "
            f"rejected because: {feedback}\n\n"
            "Please try again. You MUST preserve all existing content that "
            "is not directly affected by the code diff. Only add or modify "
            "content that documents the changes shown in the diff. Do NOT "
            "remove, rewrite, or reorganize existing sections, examples, "
            "or explanations unless they are directly contradicted by the "
            "diff.\n\n"
        )

        # Apply context budget to the regeneration prompt (finding: regen can
        # roughly double prompt size without a budget check).
        max_chars = get_max_context_chars()
        regen_budget = max(0, max_chars - len(regen_prefix))
        if len(prompt) > regen_budget:
            # Re-truncate the diff portion to fit within budget
            regen_diff_budget = max(0, regen_budget - (len(prompt) - len(truncated_diff)))
            regen_truncated_diff = truncate_diff(
                diff, regen_diff_budget, label=f"regen diff for {file_path}"
            )
            regen_prompt = regen_prefix + prompt.replace(truncated_diff, regen_truncated_diff)
        else:
            regen_prompt = regen_prefix + prompt

        try:
            regen_response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": regen_prompt},
                ],
            )
            regen_output = (regen_response.choices[0].message.content or "").strip()
            regen_output = strip_code_fences(regen_output)

            if regen_output.strip() == "NO_UPDATE_NEEDED":
                return regen_output

            if not regen_output.endswith("\n"):
                regen_output += "\n"

            regen_valid, _ = validate_format(regen_output, file_path)
            if regen_valid:
                # Re-run preservation check on the regenerated output
                regen_pres_ok, regen_pres_issues = validate_content_preservation(
                    current_content, regen_output
                )
                if not regen_pres_ok:
                    print(
                        f"Warning: Regenerated output for {file_path} still "
                        f"has preservation issues: {'; '.join(regen_pres_issues)}. "
                        f"Skipping update."
                    )
                    return "NO_UPDATE_NEEDED"
                output = regen_output
            else:
                print(
                    f"Warning: Regenerated output for {file_path} failed "
                    f"format validation. Skipping update."
                )
                return "NO_UPDATE_NEEDED"
        except Exception as e:
            check_context_error(e)
            print(
                f"Warning: Regeneration failed for {file_path}: "
                f"{sanitize_output(str(e))}. Skipping update."
            )
            return "NO_UPDATE_NEEDED"

    return output


def overwrite_file(file_path, new_content):
    """
    Safely write file with path validation
    Prevents writing to unauthorized locations
    """
    try:
        # Validate file path is safe
        if not validate_file_path(file_path):
            print(f"❌ Security: Invalid file path rejected: {file_path}")
            return False

        # Additional check: ensure it's a documentation file
        if not validate_docs_file_extension(file_path):
            print(f"❌ Security: Only .adoc, .md, and .rst files allowed: {file_path}")
            return False

        Path(file_path).write_text(new_content, encoding="utf-8")
        return True
    except Exception as e:
        print(f"Failed to write {file_path}: {sanitize_output(str(e))}")
        return False
