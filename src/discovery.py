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
    find_relevant_areas_from_indexes,
    get_files_in_areas,
    commit_indexes_to_repo,
    get_or_generate_summary,
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
    You are an ULTRA-CONSERVATIVE documentation assistant. Select ONLY files that DIRECTLY document the EXACT code being changed.

    Git diff from this PR:
    {DIFF_PLACEHOLDER}

    Documentation files to evaluate:
    {CONTEXT_PLACEHOLDER}

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

    This is a two-stage approach:
    1. Use indexes to find relevant documentation AREAS (1 API call)
    2. Load files from those areas and use AI to pick exact files (1 API call)

    Falls back to full scan if indexes don't exist or AI requests it.

    Args:
        diff: The code diff to analyze

    Returns:
        list: List of relevant file paths, or None to signal full scan needed
    """
    # Try to fetch indexes from main branch if they don't exist locally
    fetch_indexes_from_main()

    # Check if indexes exist
    indexes_changed = False
    if not indexes_exist():
        print("No indexes found. Building indexes first...")
        build_all_indexes()
        indexes_changed = True
    else:
        # Update indexes for any changed docs
        updated = update_indexes_if_needed()
        if updated:
            print(f"Updated indexes for: {updated}")
            indexes_changed = True

    # Stage 1: Find relevant AREAS using indexes (1 API call)
    print("Finding relevant documentation areas from indexes...")
    relevant_areas = find_relevant_areas_from_indexes(diff, get_client())

    if relevant_areas is None:
        # AI requested full scan or error occurred
        print("Falling back to full scan...")
        return None

    if not relevant_areas:
        print("No relevant areas found")
        return []

    # Stage 2: Get files from relevant areas
    candidate_files = get_files_in_areas(relevant_areas)
    print(f"Found {len(candidate_files)} candidate files in areas: {relevant_areas}")

    if not candidate_files:
        return []

    # Load content/summaries for candidate files only (parallel for speed)
    file_previews = []
    files_needing_summary = []
    files_with_content = []

    # First pass: identify which files need summaries
    for file_path in candidate_files:
        try:
            content = Path(file_path).read_text(encoding='utf-8')
            line_count = len(content.split('\n'))

            if line_count > 300:
                files_needing_summary.append((file_path, content))
            else:
                files_with_content.append((file_path, content))
        except Exception as e:
            print(f"Skipping {file_path}: {sanitize_output(str(e))}")

    # Generate summaries in parallel for long files (with caching)
    summaries_generated = False
    if files_needing_summary:
        # Check how many need actual generation vs cached
        cached_count = 0
        to_generate = []

        for file_path, content in files_needing_summary:
            from doc_index import load_cached_summary
            cached = load_cached_summary(file_path)
            if cached:
                file_previews.append((file_path, cached))
                cached_count += 1
            else:
                to_generate.append((file_path, content))

        if cached_count > 0:
            print(f"Using {cached_count} cached summaries")

        if to_generate:
            print(f"Generating {len(to_generate)} new summaries in parallel...")
            summaries_generated = True

            def generate_summary_task(args):
                file_path, content = args
                try:
                    # Generate and cache the summary
                    summary = get_or_generate_summary(file_path, content, summarize_long_file)
                    return (file_path, summary)
                except Exception as e:
                    print(f"Error summarizing {file_path}: {sanitize_output(str(e))}")
                    # Fallback to full content — downstream prompt handles truncation
                    return (file_path, content)

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(generate_summary_task, args): args[0]
                          for args in to_generate}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        file_previews.append(result)

    # Add files that didn't need summaries
    file_previews.extend(files_with_content)

    # Commit indexes and summaries in a single push to avoid the branch
    # going stale between two separate pushes
    if indexes_changed or summaries_generated:
        content_parts = []
        if indexes_changed:
            content_parts.append("indexes")
        if summaries_generated:
            content_parts.append("summaries")
        content_label = " and ".join(content_parts)
        print(f"Committing {content_label} to repository...")
        commit_indexes_to_repo(content_type=content_label)

    if not file_previews:
        return []

    # Stage 3: Use AI to pick exact files from candidates (1 API call typically)
    # Since we have fewer files now, this is much faster
    print(f"AI selecting exact files from {len(file_previews)} candidates...")
    relevant_files = ask_ai_for_relevant_files(diff, file_previews)

    return relevant_files
