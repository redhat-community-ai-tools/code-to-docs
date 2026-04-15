"""
AI content generation and file I/O for documentation updates.

This module handles:
- Parallel generation of documentation updates from code diffs
- Loading and safely reading documentation file content
- Asking the AI model to produce updated documentation
- Safely writing updated content back to files
"""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import configuration
from config import get_client, get_model_name, get_max_context_chars, truncate_diff, check_context_error

# Import security utilities
from security_utils import sanitize_output, validate_file_path, validate_docs_file_extension


def generate_updates_parallel(diff, relevant_files, max_workers=5, user_instructions="", file_instructions=None):
    """
    Generate documentation updates in parallel.

    Args:
        diff: The code diff
        relevant_files: List of file paths to update
        max_workers: Maximum parallel threads
        user_instructions: Optional global reviewer instructions to pass to the AI
        file_instructions: Optional dict mapping filenames to per-file instructions

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
            diff, file_path, current,
            user_instructions=user_instructions,
            file_instructions=file_instructions
        )

        if updated.strip() == "NO_UPDATE_NEEDED":
            print(f"No update needed for {file_path}")
            return None

        return (file_path, current, updated)

    # Process files in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_file, file_path): file_path
            for file_path in relevant_files
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

def ask_ai_for_updated_content(diff, file_path, current_content, user_instructions="", file_instructions=None):
    # Determine file format based on extension
    is_markdown = file_path.endswith('.md')
    is_asciidoc = file_path.endswith('.adoc')
    is_rst = file_path.endswith('.rst')

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
        format_name = "Markdown"
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
        format_name = "AsciiDoc"
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
        format_name = "reStructuredText"
    else:
        # Default to treating as text/markdown
        format_instructions = """
FORMATTING REQUIREMENTS:
- Maintain the existing format and syntax of the file
- Keep all links and references intact and properly formatted
- Use consistent indentation and spacing
"""
        format_name = "the existing format"

    # Build prompt template without diff to compute budget
    prompt_template = f"""
You are updating documentation based on a code diff. Be EXTREMELY conservative.

{format_instructions}
- Ensure consistent indentation and spacing

Git diff:
{{DIFF_PLACEHOLDER}}

Current documentation file `{file_path}`:
--------------------
{current_content}
--------------------

DECISION LOGIC:
1. Does this file document the EXACT thing being changed in the diff?
   - If NO → return `NO_UPDATE_NEEDED`
   - If YES → continue

2. Does the diff add something NEW that should be documented?
   - If NO → return `NO_UPDATE_NEEDED`
   - If YES → continue

3. Is that new thing already documented in this file?
   - If YES → return `NO_UPDATE_NEEDED`
   - If NO → add ONLY that specific change

WHAT YOU CAN ADD:
- Only content that directly reflects what was added/changed in the diff

WHAT YOU MUST NOT ADD:
- New sections or paragraphs not justified by the diff
- "Helpful" additions you think users might want
- Restructured or rewritten content

Return ONLY:
- `NO_UPDATE_NEEDED` (strongly preferred if changes aren't essential), OR
- The complete updated file with ONLY the minimal necessary changes
"""

    # Build combined instructions from global + per-file
    combined_instructions = []
    if user_instructions:
        combined_instructions.append(f"Global: {user_instructions}")
    if file_instructions:
        # Local import to avoid circular dependencies — _resolve_file_instructions
        # lives in comments.py but is needed here for per-file instruction matching.
        from comments import _resolve_file_instructions
        per_file = _resolve_file_instructions(file_path, file_instructions)
        if per_file:
            combined_instructions.append(f"For this file specifically: {per_file}")

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
            messages=[{"role": "user", "content": prompt}],
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        check_context_error(e)
        raise

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
