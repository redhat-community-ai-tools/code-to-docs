"""
File discovery and selection for documentation updates.

This module handles finding which documentation files are relevant to a given
code change. It provides AI-powered file selection (via batched parallel calls),
long-file summarization, and an optimized two-stage discovery pipeline that uses
semantic indexes to narrow candidates before asking the AI to pick exact files.
"""

import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import configuration
from config import (
    get_client, get_model_name, get_max_context_chars,
    truncate_content, check_context_error,
)

# Import security utilities
from security_utils import sanitize_output
from utils import calc_backoff_delay

# Import documentation index module
from doc_index import (
    indexes_exist,
    fetch_indexes_from_main,
    build_all_indexes,
    update_indexes_if_needed,
    find_relevant_files_from_indexes,
    commit_indexes_to_repo,
)


def summarize_long_file(file_path, content, max_retries=3):
    """Generate AI summary for the given file content with retry logic"""
    print(f"Generating summary for long file: {file_path}")

    # Build prompt template without content to compute budget
    prompt_template = f"""
Analyze this documentation file and create a comprehensive summary that captures:

1. **Primary Purpose**: What this file documents
2. **Key Topics Covered**: Main sections, features, components discussed
3. **Technical Keywords**: Important terms, APIs, configuration options, commands
4. **Target Audience**: Who would use this documentation
5. **Related Concepts**: What other systems/features this relates to

File: {file_path}
Content:
{{CONTENT_PLACEHOLDER}}

Provide a detailed summary that would help an AI system understand when this file should be updated based on code changes.
"""

    content_budget = get_max_context_chars() - len(prompt_template)
    truncated_content = truncate_content(content, content_budget, label=f"summary input for {file_path}")

    prompt = prompt_template.replace("{CONTENT_PLACEHOLDER}", truncated_content)

    for attempt in range(max_retries):
        try:
            response = get_client().chat.completions.create(
                model=get_model_name(),
                messages=[{"role": "user", "content": prompt}],
            )

            result_text = response.choices[0].message.content
            if result_text:
                return result_text.strip()
            else:
                print(f"Empty response for {file_path} (attempt {attempt + 1}), retrying...")

        except Exception as e:
            error_str = sanitize_output(str(e))
            wait_time = calc_backoff_delay(attempt, multiplier=3)
            print(f"Error for {file_path} (attempt {attempt + 1}/{max_retries}): {error_str}, waiting {wait_time}s...")

            time.sleep(wait_time)

    raise Exception(f"Failed to summarize {file_path} after {max_retries} attempts")

def get_file_content_or_summaries(line_threshold=300):
    """Get file content - full content for short files, AI summaries for long files"""
    file_data = []
    # Look for .adoc, .md, and .rst documentation files
    doc_files = []
    doc_files.extend(list(Path(".").rglob("*.adoc")))
    doc_files.extend(list(Path(".").rglob("*.md")))
    doc_files.extend(list(Path(".").rglob("*.rst")))

    # Filter out internal index files (.doc-index/) - these are for internal use only
    doc_files = [f for f in doc_files if ".doc-index" not in str(f)]

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

    print(f"Collected {len(file_data)} files for processing")
    return file_data

_FILE_SELECTION_PROMPT_TEMPLATE = """
    You are a precise documentation assistant. Select files that document the feature, component, or behavior being changed or extended in the diff.

    Git diff from this PR:
    {DIFF_PLACEHOLDER}

    Documentation files to evaluate:
    {CONTEXT_PLACEHOLDER}

    SELECT a file if ANY of these apply:
    1. The diff MODIFIES existing behavior that the file documents (docs would become incorrect)
    2. The diff ADDS new functionality that falls within the scope of what the file documents (docs would become incomplete)
    3. The diff CHANGES defaults, error messages, or output that the file references

    DO NOT select a file if:
    4. The connection between the diff and the doc is only superficial (shared keywords but different context or component)
    5. It is for a different subsystem that happens to share dependencies with the changed code
    6. It is a release notes or changelog file (unless the change is breaking)

    When genuinely uncertain, DO NOT select.

    Return ONLY file paths (one per line) that match the criteria above.
    If no files need updates, return "NONE".
    """


MAX_FILES_PER_BATCH = 10


def _batch_file_previews_by_budget(file_previews, available_for_files):
    """
    Split file previews into batches respecting both budget and max files per batch.

    Batches are split when either the budget is full or the batch reaches
    MAX_FILES_PER_BATCH files. Smaller batches produce better selection quality
    because the LLM can give each file more attention.

    Args:
        file_previews: List of (file_path, preview_content) tuples
        available_for_files: Character budget available for file context

    Returns:
        list[list]: List of batches, each batch is a list of (path, preview) tuples
    """
    batches = []
    current_batch = []
    current_size = 0

    for fname, preview in file_previews:
        entry_size = len(f"File: {fname}\nPreview:\n{preview}") + 4  # separator overhead

        if current_batch and (current_size + entry_size > available_for_files
                              or len(current_batch) >= MAX_FILES_PER_BATCH):
            batches.append(current_batch)
            current_batch = []
            current_size = 0

        current_batch.append((fname, preview))
        current_size += entry_size

    if current_batch:
        batches.append(current_batch)

    return batches


def _process_file_selection_batch(diff, batch, batch_num, total_batches, max_retries=3):
    """Process a single batch of files for relevance selection."""
    context = "\n\n".join(
        [f"File: {fname}\nPreview:\n{preview}" for fname, preview in batch]
    )

    prompt = _FILE_SELECTION_PROMPT_TEMPLATE.replace("{DIFF_PLACEHOLDER}", diff).replace("{CONTEXT_PLACEHOLDER}", context)

    for attempt in range(max_retries):
        try:
            response = get_client().chat.completions.create(
                model=get_model_name(),
                messages=[{"role": "user", "content": prompt}],
            )

            result_text = (response.choices[0].message.content or "").strip()
            if not result_text:
                if attempt < max_retries - 1:
                    time.sleep(calc_backoff_delay(attempt, multiplier=2))
                    continue
                return batch_num, []

            if result_text.upper() == "NONE":
                print(f"Batch {batch_num}: No relevant files found")
                return batch_num, []

            # Filter to only documentation files
            suggested_files = [line.strip() for line in result_text.splitlines() if line.strip()]
            filtered_files = [f for f in suggested_files if f.endswith('.adoc') or f.endswith('.md') or f.endswith('.rst')]

            if len(filtered_files) != len(suggested_files):
                skipped = [f for f in suggested_files if not (f.endswith('.adoc') or f.endswith('.md') or f.endswith('.rst'))]
                print(f"Batch {batch_num}: Skipping non-documentation files: {skipped}")

            print(f"Batch {batch_num}: Found {len(filtered_files)} relevant files")
            return batch_num, filtered_files

        except Exception as e:
            if check_context_error(e):
                return batch_num, []
            if attempt < max_retries - 1:
                wait_time = calc_backoff_delay(attempt, multiplier=3)
                print(f"Batch {batch_num}: Error (attempt {attempt + 1}), waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"Batch {batch_num}: Failed after retries - {sanitize_output(str(e))}")
                return batch_num, []

    return batch_num, []


def ask_ai_for_relevant_files(diff, file_previews, max_workers=5):
    all_relevant_files = []

    # Compute budget available for file previews (diff size already validated at startup)
    budget = get_max_context_chars()
    prompt_overhead = len(_FILE_SELECTION_PROMPT_TEMPLATE)
    available_for_files = budget - prompt_overhead - len(diff)

    # Create budget-aware batches
    batches_raw = _batch_file_previews_by_budget(file_previews, available_for_files)
    batches = [(batch, i + 1) for i, batch in enumerate(batches_raw)]

    total_batches = len(batches)
    print(f"Processing {len(file_previews)} files in {total_batches} batches (parallel, {max_workers} workers)...")

    # Process batches in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_file_selection_batch, diff, batch, batch_num, total_batches): batch_num
            for batch, batch_num in batches
        }

        for future in as_completed(futures):
            _, files = future.result()
            all_relevant_files.extend(files)

    # Strip DOCS_SUBFOLDER prefix if AI included it (common issue)
    docs_subfolder = os.environ.get("DOCS_SUBFOLDER", "")
    if docs_subfolder:
        cleaned_files = []
        for f in all_relevant_files:
            # Remove the subfolder prefix if present (e.g., "subfolder/file.rst" -> "file.rst")
            if f.startswith(docs_subfolder + "/"):
                cleaned_files.append(f[len(docs_subfolder) + 1:])
            elif f.startswith(docs_subfolder):
                cleaned_files.append(f[len(docs_subfolder):].lstrip("/"))
            else:
                cleaned_files.append(f)
        all_relevant_files = cleaned_files

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


def find_relevant_files_optimized(diff):
    """
    Optimized file discovery using semantic indexes.

    Uses per-folder indexes (which include per-file descriptions) to identify
    the exact files that need updating in a single LLM step — without loading
    the actual file content for filtering.

    Falls back to full scan if indexes don't exist or AI requests it.

    Args:
        diff: The code diff to analyze

    Returns:
        list: List of relevant file paths, or None to signal full scan needed
    """
    fetch_indexes_from_main()

    indexes_changed = False
    if not indexes_exist():
        print("No indexes found. Building indexes first...")
        build_all_indexes()
        indexes_changed = True
    else:
        updated = update_indexes_if_needed()
        if updated:
            print(f"Updated indexes for: {updated}")
            indexes_changed = True

    # Commit new/updated indexes so they're cached for future runs
    if indexes_changed:
        print("Committing indexes to repository...")
        commit_indexes_to_repo(content_type="indexes")

    print("Finding relevant documentation files from indexes...")
    relevant_files = find_relevant_files_from_indexes(diff, get_client())

    if relevant_files is None:
        print("Falling back to full scan...")
        return None

    return relevant_files
