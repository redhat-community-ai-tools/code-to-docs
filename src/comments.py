"""
PR comment generation, parsing, and review interactions.

Handles constructing review comments for documentation suggestions,
parsing user responses (checkbox selections, update instructions),
and posting comments back to GitHub PRs.
"""

import difflib
import json
import os
import re
from pathlib import Path

from config import get_client, get_model_name, get_docs_repo_url
from security_utils import sanitize_output, run_command_safe


def get_docs_file_url(file_path, commit_info=None):
    """
    Construct a GitHub URL to view a documentation file.

    Args:
        file_path: Path to the doc file (relative to docs root)
        commit_info: Optional commit info dict with 'repo_url'

    Returns:
        GitHub URL to the file, or None if URL cannot be constructed
    """
    docs_subfolder = os.environ.get("DOCS_SUBFOLDER")
    base_branch = os.environ.get("DOCS_BASE_BRANCH", "main")

    if docs_subfolder and commit_info and 'repo_url' in commit_info:
        # Same repo scenario: use source repo URL + subfolder
        repo_url = commit_info['repo_url']
        # Construct full path including docs subfolder
        full_path = f"{docs_subfolder}/{file_path}" if not file_path.startswith(docs_subfolder) else file_path
        return f"{repo_url}/blob/{base_branch}/{full_path}"

    docs_repo_url = get_docs_repo_url()
    if docs_repo_url:
        # Separate repo scenario: use docs repo URL
        # Convert SSH URL to HTTPS if needed
        repo_url = docs_repo_url
        if repo_url.startswith("git@github.com:"):
            repo_url = repo_url.replace("git@github.com:", "https://github.com/").replace(".git", "")
        elif repo_url.endswith(".git"):
            repo_url = repo_url.replace(".git", "")
        return f"{repo_url}/blob/{base_branch}/{file_path}"

    return None


def generate_file_summary(file_path, original, updated):
    """Generate a summary for a single file's changes"""
    prompt = f"""You are explaining a PROPOSED documentation change to developers who may not remember the docs well.

File: {file_path}

ORIGINAL CONTENT:
{original if original else "(new file)"}

PROPOSED UPDATED CONTENT:
{updated}

FIRST: Compare the original and proposed content carefully.
- If they are identical or nearly identical (no meaningful changes), return ONLY the word: SKIP
- If there ARE meaningful changes, continue below.

Write 1-2 sentences explaining:
1. What this documentation file covers
2. What change you SUGGEST making (comparing original vs proposed)

IMPORTANT: Write as a SUGGESTION, not as if the change is already done.
Use phrases like "I suggest adding...", "The suggested update is to...", "I recommend updating..."

Be concise. Return ONLY the explanation (or SKIP if no changes).
Do NOT use line breaks - write as a single paragraph.
"""

    try:
        client = get_client()
        model_name = get_model_name()
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        # Clean up: replace newlines with spaces for consistent formatting
        result = (response.choices[0].message.content or "").strip()
        result = ' '.join(result.split())  # Collapse all whitespace to single spaces
        return result
    except Exception as e:
        print(f"Warning: Could not generate summary for {file_path}: {sanitize_output(str(e))}")
        return ""


def generate_summary_explanation(files_with_content, commit_info=None):
    """Generate a plain-English summary of what documentation changes are proposed"""
    if not files_with_content:
        return "", []

    # Generate summary for each file individually to avoid context overflow
    summaries = []
    filtered_files = []
    for item in files_with_content:
        file_path = item[0]
        original = item[1] if len(item) > 2 else ""
        updated = item[2] if len(item) > 2 else item[1]

        print(f"Generating summary for {file_path}...")
        summary = generate_file_summary(file_path, original, updated)

        # Skip files where AI returned SKIP (no changes needed)
        if summary and summary.strip().upper() != "SKIP":
            # Create clickable link if possible
            file_url = get_docs_file_url(file_path, commit_info)
            if file_url:
                file_display = f"[{file_path}]({file_url})"
            else:
                file_display = f"**{file_path}**"
            summaries.append(f"- [x] {file_display}: {summary}")
            filtered_files.append(item)
        elif summary.strip().upper() == "SKIP":
            print(f"Filtering out {file_path}: no changes needed")

    return "\n".join(summaries) if summaries else "", filtered_files


def parse_update_instructions(comment_body):
    """
    Parse global and per-file instructions from an [update-docs] comment.

    Supports two forms:
      [update-docs] global instruction here
      pools.rst: only update the set-quota usage line
      health-checks.rst: don't modify existing sections

    The first line after [update-docs] is the global instruction.
    Subsequent lines matching "<filename>: <instruction>" are per-file.

    Args:
        comment_body: The full comment text

    Returns:
        tuple: (global_instructions: str, file_instructions: dict[str, str])
    """
    global_instructions = ""
    file_instructions = {}

    # Find [update-docs] and everything after it
    match = re.search(r'\[update-docs\]\s*(.*)', comment_body, re.IGNORECASE | re.DOTALL)
    if not match:
        return global_instructions, file_instructions

    after_command = match.group(1).strip()
    if not after_command:
        return global_instructions, file_instructions

    lines = after_command.split('\n')

    # Match lines where the part before ":" looks like a doc file path
    file_pattern = re.compile(
        r'^([\w./_-]*\.(?:rst|md|adoc))\s*:\s*(.+)$',
        re.IGNORECASE
    )

    # First line is global instructions, unless it matches the per-file pattern
    first_line = lines[0].strip()
    first_match = file_pattern.match(first_line)
    if first_match:
        file_instructions[first_match.group(1).strip()] = first_match.group(2).strip()
    else:
        global_instructions = first_line

    # Remaining lines: check for per-file instructions
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        file_match = file_pattern.match(line)
        if file_match:
            file_instructions[file_match.group(1).strip()] = file_match.group(2).strip()

    if global_instructions:
        print(f"Global instructions: {global_instructions}")
    if file_instructions:
        print(f"Per-file instructions: {file_instructions}")

    return global_instructions, file_instructions


def _resolve_file_instructions(file_path, file_instructions):
    """
    Find per-file instructions for a given file path.

    Matches by exact path, basename, or path suffix to allow users to write
    just 'config.rst' instead of 'docs/admin/config.rst'.

    Args:
        file_path: Full relative path (e.g. 'docs/admin/config.rst')
        file_instructions: Dict mapping filename patterns to instructions

    Returns:
        str: The matching instruction, or empty string if none
    """
    if not file_instructions:
        return ""

    basename = os.path.basename(file_path)

    for pattern, instruction in file_instructions.items():
        # Exact match
        if pattern == file_path:
            return instruction
        # Basename match (e.g. 'config.rst' matches 'docs/admin/config.rst')
        if pattern == basename:
            return instruction
        # Suffix match (e.g. 'admin/config.rst' matches 'docs/admin/config.rst')
        if file_path.endswith('/' + pattern):
            return instruction

    return ""


def parse_previous_review(pr_number):
    """
    Find the most recent bot review comment on the PR and parse interactive selections.

    Looks for a comment containing the '## \U0001f4da Documentation Review' header,
    then extracts:
    - Checked files (accepted): lines matching '- [x] ...'
    - Unchecked files (rejected): lines matching '- [ ] ...'

    Args:
        pr_number: The PR number to search comments on

    Returns:
        dict with keys:
            review_found (bool): Whether a review comment was found
            accepted_files (list[str]): File paths the user kept checked
            rejected_files (list[str]): File paths the user unchecked
            review_commit (str|None): The commit hash the review was based on
    """
    result = {
        "review_found": False,
        "accepted_files": [],
        "rejected_files": [],
        "review_commit": None,
    }

    gh_token = os.environ.get("GH_TOKEN")
    if not gh_token or not pr_number or pr_number == "unknown":
        return result

    try:
        # Fetch all comments on the PR as JSON
        cmd_result = run_command_safe(
            ["gh", "pr", "view", str(pr_number), "--json", "comments"],
            env={**os.environ, "GH_TOKEN": gh_token},
            check=False,
        )

        if cmd_result.returncode != 0:
            print(f"Warning: Could not fetch PR comments: {sanitize_output(cmd_result.stderr or '')}")
            return result

        data = json.loads(cmd_result.stdout)
        comments = data.get("comments", [])

        # Find the most recent review comment (search from newest to oldest)
        review_body = None
        for comment in reversed(comments):
            body = comment.get("body", "")
            if "## \U0001f4da Documentation Review" in body and "Select files to update" in body:
                review_body = body
                break

        if review_body is None:
            print("No previous interactive review comment found")
            return result

        result["review_found"] = True
        print("Found previous interactive review comment")

        # Parse checked/unchecked file lines
        # Patterns: '- [x] [path](url): summary' or '- [x] **path**: summary'
        # and:      '- [ ] [path](url): summary' or '- [ ] **path**: summary'
        checkbox_pattern = re.compile(
            r'^- \[([ xX])\] '           # checkbox
            r'(?:'
            r'\[([^\]]+)\]\([^)]+\)'     # [path](url) form
            r'|'
            r'\*\*([^*]+)\*\*'           # **path** form
            r')'
            r':'                          # colon separator
        , re.MULTILINE)

        for match in checkbox_pattern.finditer(review_body):
            checked = match.group(1).lower() == 'x'
            file_path = match.group(2) or match.group(3)
            file_path = file_path.strip()
            if checked:
                result["accepted_files"].append(file_path)
            else:
                result["rejected_files"].append(file_path)

        # Parse the commit hash from 'Latest commit: `abc1234`'
        commit_match = re.search(r'Latest commit: `([a-f0-9]+)`', review_body)
        if commit_match:
            result["review_commit"] = commit_match.group(1)

        print(f"  Accepted files: {result['accepted_files']}")
        print(f"  Rejected files: {result['rejected_files']}")
        if result["review_commit"]:
            print(f"  Review was based on commit: {result['review_commit']}")

        return result

    except Exception as e:
        print(f"Warning: Could not parse previous review: {sanitize_output(str(e))}")
        return result


def post_review_comment(files_with_content, pr_number, commit_info=None, include_full_content=True, feature_section=""):
    """
    Post a review comment on the PR with documentation suggestions

    Args:
        files_with_content: List of (file_path, original, updated) tuples
        pr_number: PR number
        commit_info: Commit information dict
        include_full_content: If True, include full content; if False, only summary
    """
    if not pr_number or pr_number == "unknown":
        print("Warning: No PR number available, cannot post review comment")
        return False

    # Build the review comment in markdown
    comment_parts = []
    comment_parts.append("## \U0001f4da Documentation Review")
    comment_parts.append("")

    if commit_info:
        if 'pr_number' in commit_info:
            comment_parts.append(f"Analyzed PR: {commit_info['pr_url']}")
        comment_parts.append(f"Latest commit: `{commit_info['short_hash']}`")
        comment_parts.append("")

    if not files_with_content:
        comment_parts.append("\u2705 **No documentation updates needed** - all docs are up to date!")
        comment_body = "\n".join(comment_parts)
    else:
        # Generate plain-English summary and filter out files with no real changes
        print("Generating summary explanation...")
        summary, filtered_files = generate_summary_explanation(files_with_content, commit_info)

        # Use filtered files (excludes files where AI said "no changes")
        if not filtered_files:
            comment_parts.append("\u2705 **No documentation updates needed** - all docs are up to date!")
            comment_body = "\n".join(comment_parts)
        else:
            comment_parts.append(f"Found **{len(filtered_files)} file(s)** that may need updates:")
            comment_parts.append("")

            if summary:
                comment_parts.append("### \U0001f4cb Select files to update")
                comment_parts.append("")
                comment_parts.append("Uncheck any files you do **not** want updated:")
                comment_parts.append("")
                comment_parts.append(summary)
                comment_parts.append("")

            # Include diff of changes if requested (for [update-docs] without previous review)
            if include_full_content:
                comment_parts.append("### \U0001f4c4 Proposed Changes")
                comment_parts.append("")

                for item in filtered_files:
                    file_path = item[0]
                    original = item[1] if len(item) > 2 else ""
                    new_content = item[2] if len(item) > 2 else item[1]
                    # Create clickable link if possible
                    file_url = get_docs_file_url(file_path, commit_info)
                    if file_url:
                        comment_parts.append(f"#### \U0001f4c4 [{file_path}]({file_url})")
                    else:
                        comment_parts.append(f"#### \U0001f4c4 `{file_path}`")
                    comment_parts.append("")
                    comment_parts.append("<details>")
                    comment_parts.append(f"<summary><b>View proposed changes</b></summary>")
                    comment_parts.append("")
                    # Show diff instead of full content
                    diff_lines = list(difflib.unified_diff(
                        original.splitlines(keepends=True),
                        new_content.splitlines(keepends=True),
                        fromfile=f"a/{file_path}",
                        tofile=f"b/{file_path}",
                        n=3,
                    ))
                    if diff_lines:
                        comment_parts.append("```diff")
                        comment_parts.append("".join(diff_lines))
                        comment_parts.append("```")
                    else:
                        comment_parts.append("No changes detected.")
                    comment_parts.append("")
                    comment_parts.append("</details>")
                    comment_parts.append("")

        comment_parts.append("---")
        comment_parts.append("")
        comment_parts.append("\U0001f4a1 **Next Steps**:")
        comment_parts.append("- **Uncheck** any files above that you don't want updated")
        if not include_full_content:
            comment_parts.append("- When ready, comment `[\u200bupdate-docs]` to generate a PR with only the checked files")
        else:
            comment_parts.append("- When ready, comment `[\u200bupdate-docs]` to create a PR with only the checked files")
        comment_parts.append("- You can add instructions in your `[\u200bupdate-docs]` comment:")
        comment_parts.append("  - **Global** (first line): `[\u200bupdate-docs] keep changes minimal, don't add new sections`")
        comment_parts.append("  - **Per-file** (next lines): `config-ref.rst: only update the CLI usage example`")
        comment_parts.append("")
        comment_parts.append("*Powered by code-to-docs AI* \u2728")

        comment_body = "\n".join(comment_parts)

    # Append feature coverage section if present
    if feature_section:
        comment_body += "\n" + feature_section

    # Write comment to temp file
    comment_file = Path("/tmp/review_comment.md")
    comment_file.write_text(comment_body, encoding="utf-8")

    # Post comment using GitHub CLI
    gh_token = os.environ.get("GH_TOKEN")
    if not gh_token:
        print("Error: GH_TOKEN not found, cannot post comment")
        return False

    try:
        result = run_command_safe(
            ["gh", "pr", "comment", str(pr_number), "--body-file", str(comment_file)],
            env={**os.environ, "GH_TOKEN": gh_token},
            check=False
        )

        if result.returncode == 0:
            print(f"\u2705 Posted review comment on PR #{pr_number}")
            return True
        else:
            error_msg = sanitize_output(result.stderr) if result.stderr else "Unknown error"
            print(f"\u274c Failed to post comment: {error_msg}")
            return False
    except Exception as e:
        print(f"\u274c Error posting comment: {sanitize_output(str(e))}")
        return False
