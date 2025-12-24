import os
import subprocess
import argparse
from pathlib import Path
from google import genai
from google.genai import types

# Import security utilities
from security_utils import (
    sanitize_output,
    run_command_safe,
    validate_file_path,
    setup_git_credentials,
    validate_docs_file_extension,
    validate_docs_subfolder
)

# === CONFIG ===
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
DOCS_REPO_URL = os.environ["DOCS_REPO_URL"]
BRANCH_NAME = "doc-update-from-pr"


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
    elif DOCS_REPO_URL:
        # Separate repo scenario: use docs repo URL
        # Convert SSH URL to HTTPS if needed
        repo_url = DOCS_REPO_URL
        if repo_url.startswith("git@github.com:"):
            repo_url = repo_url.replace("git@github.com:", "https://github.com/").replace(".git", "")
        elif repo_url.endswith(".git"):
            repo_url = repo_url.replace(".git", "")
        return f"{repo_url}/blob/{base_branch}/{file_path}"
    
    return None

def get_diff():
    """
    Get the full diff for the entire PR, not just the latest commit
    Uses safe subprocess execution
    """
    # First, try to get PR base from environment (set by GitHub Actions)
    pr_base = os.environ.get("PR_BASE", "origin/main")
    pr_number = os.environ.get("PR_NUMBER", "unknown")
    
    print(f"Getting diff for PR #{pr_number} against base: {pr_base}")
    
    try:
        # Get the merge-base to ensure we capture all PR changes
        merge_base_result = run_command_safe(
            ["git", "merge-base", pr_base, "HEAD"],
            check=False
        )
        
        if merge_base_result.returncode == 0:
            # Use merge-base to get all changes in the PR branch
            merge_base = merge_base_result.stdout.strip()
            print(f"Using merge-base: {merge_base[:7]}...{merge_base[-7:]}")
            
            # Show which files changed in the entire PR
            files_result = run_command_safe(
                ["git", "diff", "--name-only", f"{merge_base}...HEAD"],
                check=False
            )
            if files_result.returncode == 0:
                changed_files = files_result.stdout.strip().split('\n')
                changed_files = [f for f in changed_files if f.strip()]
                print(f"Files changed in entire PR: {changed_files}")
            
            result = run_command_safe(
                ["git", "diff", f"{merge_base}...HEAD"],
                check=False
            )
            diff_method = f"merge-base ({merge_base[:7]}...HEAD)"
        else:
            # Fallback to the original method
            print("Warning: Could not find merge-base, using fallback diff method")
            result = run_command_safe(
                ["git", "diff", f"{pr_base}...HEAD"],
                check=False
            )
            diff_method = f"direct ({pr_base}...HEAD)"
        
        diff_content = result.stdout.strip() if result.stdout else ""
        print(f"Diff method: {diff_method}")
        print(f"Diff size: {len(diff_content)} characters")
        
        return diff_content
    except Exception as e:
        print(f"❌ Error getting diff: {sanitize_output(str(e))}")
        return ""

def get_commit_info():
    """
    Get PR information for the documentation PR reference
    Uses safe subprocess execution
    """
    try:
        # Get PR number from environment if available
        pr_number = os.environ.get("PR_NUMBER")
        print(f"Debug: PR_NUMBER from environment: '{pr_number}'")
        
        # Get the HEAD commit - this is what GitHub Actions checked out for the PR
        current_commit_result = run_command_safe(
            ["git", "rev-parse", "HEAD"],
            check=False
        )
        if current_commit_result.returncode != 0:
            return None
        commit_hash = current_commit_result.stdout.strip()
        
        # Get remote origin URL to construct proper commit links
        remote_url = run_command_safe(
            ["git", "config", "--get", "remote.origin.url"],
            check=False
        )
        if remote_url.returncode != 0:
            return None
        
        # Convert SSH URL to HTTPS if needed
        repo_url = remote_url.stdout.strip()
        if repo_url.startswith("git@github.com:"):
            repo_url = repo_url.replace("git@github.com:", "https://github.com/").replace(".git", "")
        elif repo_url.endswith(".git"):
            repo_url = repo_url.replace(".git", "")
        
        # Get commit details
        short_hash = commit_hash[:7]
        
        # Return PR information if available, otherwise fallback to commit info
        result = {
            'repo_url': repo_url,
            'current_commit': commit_hash,
            'short_hash': short_hash
        }
        
        # Check if we have a valid PR number (not None, not empty, not "unknown")
        if pr_number and pr_number.strip() and pr_number != "unknown":
            result['pr_number'] = pr_number
            result['pr_url'] = f"{repo_url}/pull/{pr_number}"
            print(f"Debug: Using PR info - PR #{pr_number}")
        else:
            print(f"Debug: No valid PR number, falling back to commit info")
        
        return result
            
    except Exception as e:
        print(f"Warning: Could not get commit info: {sanitize_output(str(e))}")
        return None

def setup_docs_environment():
    """
    Set up docs environment - either local subfolder or clone separate repo
    Uses secure git operations
    """
    docs_subfolder = os.environ.get("DOCS_SUBFOLDER")
    
    if docs_subfolder:
        # Use local subfolder (same repo)
        current_dir = os.getcwd()
        print(f"DEBUG: Current working directory before chdir: {current_dir}")
        print(f"DEBUG: DOCS_SUBFOLDER environment variable value: '{docs_subfolder}'")
        
        # Validate subfolder path
        subfolder_path = os.path.join(current_dir, docs_subfolder)
        print(f"DEBUG: Full path to docs subfolder: {subfolder_path}")
        
        # Security: ensure no path traversal
        if not validate_docs_subfolder(docs_subfolder):
            print(f"❌ Security: Invalid docs subfolder path: {docs_subfolder}")
            return False
        
        if not os.path.exists(docs_subfolder):
            print(f"ERROR: Docs subfolder '{docs_subfolder}' not found at {subfolder_path}")
            print(f"DEBUG: Contents of current directory: {os.listdir('.')}")
            return False
        
        print(f"DEBUG: Changing to docs subfolder: {docs_subfolder}")    
        os.chdir(docs_subfolder)
        
        final_dir = os.getcwd()
        print(f"DEBUG: Final working directory after chdir: {final_dir}")
        print(f"DEBUG: Contents of docs directory: {os.listdir('.')[:10]}...")  # Show first 10 items
        return True
    else:
        # Clone separate repository with secure credentials
        try:
            print("Cloning separate docs repository")
            
            # Setup secure credentials before cloning
            gh_token = os.environ.get("GH_TOKEN")
            if gh_token:
                setup_git_credentials(gh_token, DOCS_REPO_URL)
            
            result = run_command_safe(["git", "clone", DOCS_REPO_URL, "docs_repo"], check=True)
            os.chdir("docs_repo")

            # Try to check out the branch if it already exists
            result = run_command_safe(
                ["git", "ls-remote", "--heads", "origin", BRANCH_NAME],
                check=False
            )
            
            if result.stdout and result.stdout.strip():
                print(f"Reusing existing branch: {BRANCH_NAME}")
                run_command_safe(["git", "fetch", "origin", BRANCH_NAME], check=True)
                run_command_safe(["git", "checkout", BRANCH_NAME], check=True)
                run_command_safe(["git", "pull", "origin", BRANCH_NAME], check=True)
            else:
                print(f"Creating new branch: {BRANCH_NAME}")
                run_command_safe(["git", "checkout", "-b", BRANCH_NAME], check=True)
            
            return True
        except Exception as e:
            print(f"❌ Failed to setup docs environment: {sanitize_output(str(e))}")
            return False


def summarize_long_file(file_path, content):
    """Generate AI summary for the given file content"""
    print(f"Generating summary for long file: {file_path}")
    
    prompt = f"""
Analyze this documentation file and create a comprehensive summary that captures:

1. **Primary Purpose**: What this file documents
2. **Key Topics Covered**: Main sections, features, components discussed  
3. **Technical Keywords**: Important terms, APIs, configuration options, commands
4. **Target Audience**: Who would use this documentation
5. **Related Concepts**: What other systems/features this relates to

File: {file_path}
Content:
{content}

Provide a detailed summary that would help an AI system understand when this file should be updated based on code changes.
"""
    
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0)
        ),
    )
    
    return response.text.strip()

def get_file_content_or_summaries(line_threshold=300):
    """Get file content - full content for short files, AI summaries for long files"""
    file_data = []
    # Look for .adoc, .md, and .rst documentation files
    doc_files = []
    doc_files.extend(list(Path(".").rglob("*.adoc")))
    doc_files.extend(list(Path(".").rglob("*.md")))
    doc_files.extend(list(Path(".").rglob("*.rst")))
    
    # Deduplicate file paths BEFORE processing to avoid duplicate work
    seen_paths = set()
    unique_doc_files = []
    for path in doc_files:
        path_str = str(path)
        if path_str not in seen_paths:
            seen_paths.add(path_str)
            unique_doc_files.append(path)
    
    if len(unique_doc_files) != len(doc_files):
        print(f"Removed {len(doc_files) - len(unique_doc_files)} duplicate file path(s)")
    
    for path in unique_doc_files:
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
                
            # Check file length and decide what to use
            line_count = len(content.split('\n'))
            
            if line_count > line_threshold:
                # Long file - generate summary
                content_to_use = summarize_long_file(str(path), content)
                print(f"Processed {path}: {line_count} lines (using AI summary)")
            else:
                # Short file - use full content
                content_to_use = content
                print(f"Processed {path}: {line_count} lines (using full content)")
            
            file_data.append((str(path), content_to_use))
            
        except Exception as e:
            print(f"Skipping file {path}: {sanitize_output(str(e))}")
    
    print(f"DEBUG: Returning {len(file_data)} files for processing")
    return file_data

def ask_gemini_for_relevant_files(diff, file_previews):
    all_relevant_files = []
    batch_size = 10
    
    # Process files in batches of 10
    for i in range(0, len(file_previews), batch_size):
        batch = file_previews[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(file_previews) + batch_size - 1) // batch_size
        
        print(f"Processing batch {batch_num}/{total_batches} ({len(batch)} files)...")
        
        # Create context for this batch of 10 files
        context = "\n\n".join(
            [f"File: {fname}\nPreview:\n{preview}" for fname, preview in batch]
        )

        prompt = f"""
        You are an ULTRA-CONSERVATIVE documentation assistant. Select ONLY files that DIRECTLY document the EXACT code being changed.

        Git diff from this PR:
        {diff}

        Documentation files to evaluate:
        {context}

        STRICT SELECTION RULES:
        1. ONLY select files that document the EXACT code, module, or component being modified in the diff
        2. DO NOT select files just because they mention related concepts or technologies
        3. DO NOT select overview or index files unless absolutely necessary
        4. Select the MINIMUM number of files necessary
        5. When in doubt, DO NOT select the file
        6. Prefer returning NONE over selecting uncertain files
        
        AVOID COMMON OVER-SELECTION MISTAKES:
        7. If a doc file mentions the same technology (e.g., a library, tool, or protocol) but for a DIFFERENT component or purpose, DO NOT select it
        8. If a doc file is about USER-CONFIGURED items (e.g., custom configs, user containers, plugins) but the code change is about INTERNAL/SYSTEM behavior, DO NOT select it
        9. If a doc file is for a different subsystem that happens to share dependencies with the changed code, DO NOT select it
        10. Release notes and changelogs should ONLY be selected if explicitly requested or if the change is a breaking change

        Return ONLY file paths (one per line) that DIRECTLY match the code changes.
        If no files need updates, return "NONE".
        """

        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            ),
        )
        
        result_text = response.text.strip()
        if result_text.upper() == "NONE":
            print(f"Batch {batch_num}: No relevant files found")
            continue
        
        # Filter out source code files - only keep documentation files (.adoc, .md, and .rst)
        suggested_files = [line.strip() for line in result_text.splitlines() if line.strip()]
        filtered_files = [f for f in suggested_files if f.endswith('.adoc') or f.endswith('.md') or f.endswith('.rst')]
        
        if len(filtered_files) != len(suggested_files):
            skipped = [f for f in suggested_files if not (f.endswith('.adoc') or f.endswith('.md') or f.endswith('.rst'))]
            print(f"Batch {batch_num}: Skipping non-documentation files: {skipped}")
        
        all_relevant_files.extend(filtered_files)
        print(f"Batch {batch_num}: Found {len(filtered_files)} relevant files")
    
    # Deduplicate while preserving order
    seen = set()
    unique_files = []
    for f in all_relevant_files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)
    
    if len(unique_files) != len(all_relevant_files):
        print(f"Removed {len(all_relevant_files) - len(unique_files)} duplicate file(s)")
    
    print(f"Total relevant files found: {len(unique_files)}")
    return unique_files

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

def ask_gemini_for_updated_content(diff, file_path, current_content):
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

    prompt = f"""
You are updating documentation based on a code diff. Be EXTREMELY conservative.

{format_instructions}
- Ensure consistent indentation and spacing

Git diff:
{diff}

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


    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0)
        ),
    )
    return response.text.strip()

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
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            ),
        )
        # Clean up: replace newlines with spaces for consistent formatting
        result = response.text.strip()
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
            summaries.append(f"- {file_display}: {summary}")
            filtered_files.append(item)
        elif summary.strip().upper() == "SKIP":
            print(f"Filtering out {file_path}: no changes needed")
    
    return "\n".join(summaries) if summaries else "", filtered_files

def post_review_comment(files_with_content, pr_number, commit_info=None, include_full_content=True):
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
    comment_parts.append("## 📚 Documentation Review")
    comment_parts.append("")
    
    if commit_info:
        if 'pr_number' in commit_info:
            comment_parts.append(f"Analyzed PR: {commit_info['pr_url']}")
        comment_parts.append(f"Latest commit: `{commit_info['short_hash']}`")
        comment_parts.append("")
    
    if not files_with_content:
        comment_parts.append("✅ **No documentation updates needed** - all docs are up to date!")
        comment_body = "\n".join(comment_parts)
    else:
        # Generate plain-English summary and filter out files with no real changes
        print("Generating summary explanation...")
        summary, filtered_files = generate_summary_explanation(files_with_content, commit_info)
        
        # Use filtered files (excludes files where AI said "no changes")
        if not filtered_files:
            comment_parts.append("✅ **No documentation updates needed** - all docs are up to date!")
            comment_body = "\n".join(comment_parts)
        else:
            comment_parts.append(f"Found **{len(filtered_files)} file(s)** that may need updates:")
            comment_parts.append("")
            
            if summary:
                comment_parts.append("### 📋 Summary")
                comment_parts.append("")
                comment_parts.append(summary)
                comment_parts.append("")
            
            # Include full content only if requested (for [update-docs])
            if include_full_content:
                comment_parts.append("### 📄 Proposed Changes")
                comment_parts.append("")
                
                for item in filtered_files:
                    file_path = item[0]
                    new_content = item[2] if len(item) > 2 else item[1]
                    # Create clickable link if possible
                    file_url = get_docs_file_url(file_path, commit_info)
                    if file_url:
                        comment_parts.append(f"#### 📄 [{file_path}]({file_url})")
                    else:
                        comment_parts.append(f"#### 📄 `{file_path}`")
                    comment_parts.append("")
                    comment_parts.append("<details>")
                    comment_parts.append(f"<summary><b>View full updated content</b></summary>")
                    comment_parts.append("")
                    comment_parts.append("```" + ("markdown" if file_path.endswith('.md') else "rst" if file_path.endswith('.rst') else "asciidoc"))
                    comment_parts.append(new_content)
                    comment_parts.append("```")
                    comment_parts.append("")
                    comment_parts.append("</details>")
                    comment_parts.append("")
        
        comment_parts.append("---")
        comment_parts.append("")
        comment_parts.append("💡 **Next Steps**:")
        comment_parts.append("- Review the suggestions above")
        if not include_full_content:
            comment_parts.append("- To see full proposed changes and create a PR, comment with the update-docs command in brackets")
        else:
            comment_parts.append("- To create a PR with these changes, comment with the update-docs command in brackets")
        comment_parts.append("")
        comment_parts.append("*Powered by Gemini AI* ✨")
        
        comment_body = "\n".join(comment_parts)
    
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
            print(f"✅ Posted review comment on PR #{pr_number}")
            return True
        else:
            error_msg = sanitize_output(result.stderr) if result.stderr else "Unknown error"
            print(f"❌ Failed to post comment: {error_msg}")
            return False
    except Exception as e:
        print(f"❌ Error posting comment: {sanitize_output(str(e))}")
        return False

def push_and_open_pr(modified_files, commit_info=None):
    """
    Push changes and create PR in docs repository
    Uses secure credential helper to prevent token leakage
    """
    try:
        # Add files
        result = run_command_safe(["git", "add"] + modified_files, check=True)
        
        # Build commit message with useful links
        commit_msg = "Auto-generated doc updates from code changes"
        
        if commit_info:
            if 'pr_number' in commit_info:
                commit_msg += f"\n\nPR Link: {commit_info['pr_url']}"
                commit_msg += f"\nLatest commit: {commit_info['short_hash']}"
            else:
                # Fallback to commit reference if no PR info available
                commit_url = f"{commit_info['repo_url']}/commit/{commit_info['current_commit']}"
                commit_msg += f"\n\nCommit Link: {commit_url}"
                commit_msg += f"\nLatest commit: {commit_info['short_hash']}"
        
        commit_msg += "\n\nAssisted-by: Gemini"
        
        # Commit changes
        result = run_command_safe([
            "git", "commit",
            "-m", commit_msg
        ], check=True)
        
        # Setup secure git credentials
        gh_token = os.environ.get("GH_TOKEN")
        if not gh_token:
            raise ValueError("GH_TOKEN environment variable not set")
        
        # Clear GitHub Actions default authentication that interferes with our PAT
        run_command_safe(
            ["git", "config", "--unset-all", "http.https://github.com/.extraheader"],
            check=False,
            capture_output=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Setup credential helper (secure - no token in URL)
        setup_git_credentials(gh_token, DOCS_REPO_URL)
        
        # Set remote URL without token (credentials come from helper)
        run_command_safe(["git", "remote", "set-url", "origin", DOCS_REPO_URL], check=True)
        
        # Push changes
        print(f"Pushing to branch {BRANCH_NAME}...")
        result = run_command_safe(
            ["git", "push", "--set-upstream", "origin", BRANCH_NAME, "--force"],
            check=True
        )
        
        if result.returncode == 0:
            print("✅ Successfully pushed changes")
        
        # Build PR body with source reference
        pr_body = "This PR updates the following documentation files based on code changes:\n\n"
        pr_body += "\n".join([f"- `{f}`" for f in modified_files])
        
        # Add source reference
        if commit_info:
            pr_body += "\n\n---\n**Source:**\n"
            if 'pr_number' in commit_info:
                pr_body += f"- PR: {commit_info['pr_url']}\n"
            pr_body += f"- Commit: `{commit_info['short_hash']}`"
        
        pr_body += "\n\n*Assisted by Gemini*"
        
        # Check if PR already exists for this branch
        print("Checking for existing pull request...")
        check_pr = run_command_safe([
            "gh", "pr", "list",
            "--head", BRANCH_NAME,
            "--state", "open",
            "--json", "number"
        ], check=False, env={**os.environ, "GH_TOKEN": gh_token})
        
        existing_pr = check_pr.stdout.strip() if check_pr.returncode == 0 else "[]"
        
        if existing_pr and existing_pr != "[]":
            # PR exists - just update it (push already updated the branch)
            print("✅ Existing PR found - branch updated with new changes")
            print("   (The push already updated the PR with the latest commits)")
        else:
            # Create new PR
            print("Creating pull request...")
            result = run_command_safe([
                "gh", "pr", "create",
                "--title", "Auto-Generated Doc Updates from Code PR",
                "--body", pr_body,
                "--base", os.environ.get("DOCS_BASE_BRANCH", "main"),
                "--head", BRANCH_NAME
            ], check=True, env={**os.environ, "GH_TOKEN": gh_token})
            
            if result.returncode == 0:
                print("✅ Successfully created PR")
            
    except subprocess.CalledProcessError as e:
        print(f"❌ Git operation failed: {sanitize_output(str(e))}")
        if e.stderr:
            print(f"Error details: {sanitize_output(e.stderr)}")
        raise
    except Exception as e:
        print(f"❌ Error in push_and_open_pr: {sanitize_output(str(e))}")
        raise

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Simulate changes without writing files or pushing PR")
    args = parser.parse_args()

    # Detect which command was used: [review-docs] or [update-docs]
    comment_body = os.environ.get("COMMENT_BODY", "")
    
    # Determine mode based on comment
    review_mode = "[review-docs]" in comment_body.lower()
    update_mode = "[update-docs]" in comment_body.lower()
    
    if not review_mode and not update_mode:
        # Fallback to update mode if no command detected (backward compatibility)
        update_mode = True
    
    print(f"Mode: {'Review' if review_mode and not update_mode else 'Update' if update_mode and not review_mode else 'Review + Update'}")

    diff = get_diff()
    if not diff:
        print("No changes detected.")
        return
    
    # Get commit info before switching to docs repo
    commit_info = get_commit_info()
    if commit_info:
        print(f"Source repository: {commit_info['repo_url']}")
        print(f"Latest commit: {commit_info['short_hash']}")
    
    # Get PR number for posting comments
    pr_number = os.environ.get("PR_NUMBER", "unknown")
    
    if not setup_docs_environment():
        print("Failed to set up docs environment")
        return
        
    file_previews = get_file_content_or_summaries()
    print(f"DEBUG: Collected {len(file_previews)} file previews")

    if not file_previews:
        print("No documentation files found to process.")
        return

    print("Asking Gemini for relevant files...")
    relevant_files = ask_gemini_for_relevant_files(diff, file_previews)
    if not relevant_files:
        print("Gemini did not suggest any files.")
        # Still post a comment saying no updates needed
        if review_mode or update_mode:
            # No content to show, so include_full_content doesn't matter
            post_review_comment([], pr_number, commit_info, include_full_content=False)
        return

    print("Files selected by Gemini:", relevant_files)

    # Collect files with their updated content
    files_with_content = []
    modified_files = []
    
    for file_path in relevant_files:
        current = load_full_content(file_path)
        if not current:
            continue

        print(f"Checking if {file_path} needs an update...")
        updated = ask_gemini_for_updated_content(diff, file_path, current)

        if updated.strip() == "NO_UPDATE_NEEDED":
            print(f"No update needed for {file_path}")
            continue

        # Store both original and updated content for review comment
        files_with_content.append((file_path, current, updated))

        # Only write files if in update mode (not review-only mode)
        if update_mode and not args.dry_run:
            print(f"Updating {file_path}...")
            if overwrite_file(file_path, updated):
                modified_files.append(file_path)
        elif args.dry_run:
            print(f"[Dry Run] Would update {file_path}")

    # Handle different modes
    if files_with_content:
        # Always post review comment if [review-docs] or [update-docs]
        if (review_mode or update_mode) and not args.dry_run:
            print(f"Posting review comment on PR #{pr_number}...")
            # [review-docs]: only summary, [update-docs]: summary + full content
            include_full = update_mode  # Show full content if [update-docs] is present
            post_review_comment(files_with_content, pr_number, commit_info, include_full_content=include_full)
        
        # Create PR only if [update-docs] was used
        if update_mode and modified_files:
            if args.dry_run:
                print("[Dry Run] Would push and open PR for the following files:")
                for f in modified_files:
                    print(f"- {f}")
                
                if commit_info:
                    # Show what the commit message would look like
                    commit_msg = "Auto-generated doc updates from code changes"
                    
                    if 'pr_number' in commit_info:
                        commit_msg += f"\n\nPR Link: {commit_info['pr_url']}"
                        commit_msg += f"\nLatest commit: {commit_info['short_hash']}"
                    else:
                        # Fallback to commit reference if no PR info available
                        commit_url = f"{commit_info['repo_url']}/commit/{commit_info['current_commit']}"
                        commit_msg += f"\n\nCommit Link: {commit_url}"
                        commit_msg += f"\nLatest commit: {commit_info['short_hash']}"
                    
                    commit_msg += "\n\nAssisted-by: Gemini"
                    
                    print(f"\n[Dry Run] Commit message would be:")
                    print("=" * 50)
                    print(commit_msg)
                    print("=" * 50)
            else:
                # Handle same-repo vs separate-repo scenarios
                docs_subfolder = os.environ.get("DOCS_SUBFOLDER")
                if docs_subfolder:
                    print("Same-repo scenario: preparing for PR creation...")
                    # Go back to repo root for git operations
                    os.chdir("..")
                    # Create and switch to docs branch
                    try:
                        run_command_safe(["git", "checkout", "-b", BRANCH_NAME], check=True)
                    except subprocess.CalledProcessError:
                        # Branch might already exist, try checking it out
                        run_command_safe(["git", "checkout", BRANCH_NAME], check=True)
                    # Convert file paths to include docs subfolder prefix
                    docs_files = [f"{docs_subfolder}/{f}" if not f.startswith(docs_subfolder) else f for f in modified_files]
                    push_and_open_pr(docs_files, commit_info)
                else:
                    print("Separate-repo scenario: creating PR...")
                    push_and_open_pr(modified_files, commit_info)
        elif update_mode and not modified_files and not args.dry_run:
            print("All documentation is already up to date — no PR created.")
    else:
        # No files need updates
        if (review_mode or update_mode) and not args.dry_run:
            print("Posting comment that no updates are needed...")
            # No content to show, so include_full_content doesn't matter
            post_review_comment([], pr_number, commit_info, include_full_content=False)
        else:
            print("All documentation is already up to date — no PR created.")

if __name__ == "__main__":
    main()