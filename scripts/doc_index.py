"""
Documentation Index Management

This module provides functionality to build and manage semantic indexes
for documentation folders, enabling fast and accurate matching of code
changes to relevant documentation files.

The index system works in two phases:
1. First run: Scan all docs, generate rich semantic indexes per folder
2. Subsequent runs: Use indexes to quickly find relevant docs, only regenerate
   indexes for folders where docs have changed
"""

import os
import json
import hashlib
import subprocess
import shutil
import tempfile
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Thread lock for manifest file operations (prevents race conditions in parallel summary generation)
_manifest_lock = threading.Lock()

from google import genai
from google.genai import types

# Import security utilities for safe output
from security_utils import sanitize_output, run_command_safe

# Index configuration
INDEX_DIR = ".doc-index"
MANIFEST_FILE = "manifest.json"
SUMMARIES_DIR = "summaries"
SUMMARIES_MANIFEST = "summaries_manifest.json"
INDEX_VERSION = "1.0"
MAX_WORKERS_INDEX = 5  # Parallel threads for index generation
MAX_WORKERS_API = 10   # Parallel threads for API calls


def get_client():
    """Get Gemini client"""
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def hash_file(file_path):
    """Generate SHA256 hash of file contents"""
    with open(file_path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def get_docs_root():
    """
    Get the root directory for documentation.
    
    In same-repo scenarios, DOCS_SUBFOLDER env var specifies the docs location.
    If already in the docs directory (after setup_docs_environment), use current dir.
    
    Returns:
        Path: The documentation root directory
    """
    # Check if DOCS_SUBFOLDER is set and we haven't already changed to it
    docs_subfolder = os.environ.get("DOCS_SUBFOLDER")
    
    if docs_subfolder:
        subfolder_path = Path(docs_subfolder)
        # If the subfolder exists from current directory, use it
        if subfolder_path.exists() and subfolder_path.is_dir():
            return subfolder_path
    
    # Default: use current directory (assumes setup_docs_environment already ran)
    return Path(".")


def get_doc_folders(docs_root=None):
    """
    Get list of documentation folders (top-level directories with .rst/.md/.adoc files)
    
    Args:
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    
    Returns:
        list: Sorted list of folder names
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    docs_root = Path(docs_root)
    doc_folders = set()
    
    for ext in ["*.rst", "*.md", "*.adoc"]:
        for doc_file in docs_root.rglob(ext):
            # Get path relative to docs_root
            try:
                rel_path = doc_file.relative_to(docs_root)
            except ValueError:
                continue
            
            # Skip hidden directories and index directory
            if any(part.startswith('.') for part in rel_path.parts):
                continue
            
            # Get the top-level folder within docs
            if len(rel_path.parts) > 1:
                top_folder = rel_path.parts[0]
                # Skip internal folders
                if not top_folder.startswith('_'):
                    doc_folders.add(top_folder)
    
    return sorted(list(doc_folders))


def get_docs_in_folder(folder, docs_root=None):
    """
    Get all documentation files in a folder.
    
    Args:
        folder: Folder name relative to docs root
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    
    Returns:
        list: List of Path objects for doc files
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    folder_path = Path(docs_root) / folder
    docs = []
    
    if folder_path.exists():
        for ext in ["*.rst", "*.md", "*.adoc"]:
            docs.extend(folder_path.rglob(ext))
    
    return docs


def load_manifest(docs_root=None):
    """
    Load the index manifest file.
    
    Args:
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    manifest_path = Path(docs_root) / INDEX_DIR / MANIFEST_FILE
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return {
        "version": INDEX_VERSION,
        "created": datetime.now().isoformat(),
        "folders": {}
    }


def save_manifest(manifest, docs_root=None):
    """
    Save the index manifest file.
    
    Args:
        manifest: The manifest dict to save
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    index_dir = Path(docs_root) / INDEX_DIR
    index_dir.mkdir(exist_ok=True)
    manifest["updated"] = datetime.now().isoformat()
    with open(index_dir / MANIFEST_FILE, 'w') as f:
        json.dump(manifest, f, indent=2)


def get_folder_doc_hashes(folder, docs_root=None):
    """
    Get hashes of all docs in a folder.
    
    Args:
        folder: Folder name relative to docs root
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    hashes = {}
    for doc in get_docs_in_folder(folder, docs_root):
        # Store relative path as key for consistency
        try:
            rel_path = doc.relative_to(Path(docs_root))
            hashes[str(rel_path)] = hash_file(doc)
        except ValueError:
            hashes[str(doc)] = hash_file(doc)
    return hashes


def folder_needs_reindex(folder, manifest, docs_root=None):
    """
    Check if a folder needs its index regenerated.
    
    Args:
        folder: Folder name
        manifest: The loaded manifest
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    """
    if folder not in manifest.get("folders", {}):
        return True
    
    stored_hashes = manifest["folders"][folder].get("doc_hashes", {})
    current_hashes = get_folder_doc_hashes(folder, docs_root)
    
    return stored_hashes != current_hashes


def build_index_for_folder(folder, client=None):
    """
    Build a semantic index for a documentation folder.
    
    The index includes:
    - Overview of what the folder documents
    - Summary of each file's purpose
    - Description of what code changes would affect this documentation
    - Key technical terms and concepts
    """
    if client is None:
        client = get_client()
    
    docs = get_docs_in_folder(folder)
    if not docs:
        return None
    
    # Gather content from all docs in the folder
    docs_content = []
    for doc in docs:
        try:
            content = doc.read_text(encoding='utf-8')
            # Truncate very long files to avoid token limits
            if len(content) > 50000:
                content = content[:50000] + "\n\n[... truncated for length ...]"
            docs_content.append({
                "path": str(doc),
                "content": content
            })
        except Exception as e:
            print(f"Warning: Could not read {doc}: {sanitize_output(str(e))}")
    
    if not docs_content:
        return None
    
    # Format docs for the prompt - use more content for better understanding
    docs_text = "\n\n---\n\n".join([
        f"### File: {d['path']}\n\n{d['content'][:20000]}"  # First 20000 chars per file for better context
        for d in docs_content
    ])
    
    prompt = f"""
Analyze these documentation files from the "{folder}" folder and create a comprehensive semantic index.

Documentation Files:
{docs_text}

Generate a structured index in the following format:

# {folder.upper()} Documentation Index

## Overview
[2-3 sentences describing what this documentation area covers and its purpose]

## Files Summary
[For each file, provide: filename and 1-2 sentence description of its purpose]

## Code Changes That Would Require Documentation Updates
[List specific types of code changes, features, components, or behaviors that would require updating these docs. Be comprehensive and specific - think about what a developer might change in the codebase that would make this documentation outdated.]

## Key Technical Concepts
[List important technical terms, commands, configuration options, APIs, or concepts documented here. These will be used to match against code changes.]

## Related Components
[List related system components, modules, or subsystems that this documentation describes]

Be thorough - this index will be used to automatically match code changes to documentation that needs updates.
"""

    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            ),
        )
        return response.text.strip()
    except Exception as e:
        print(f"Error generating index for {folder}: {sanitize_output(str(e))}")
        return None


def build_index_for_folder_with_retry(folder, client=None, max_retries=3):
    """Build index with retry logic for transient errors"""
    for attempt in range(max_retries):
        try:
            return build_index_for_folder(folder, client)
        except Exception as e:
            wait_time = (attempt + 1) * 3
            print(f"Error building index for {folder} (attempt {attempt + 1}/{max_retries}): {sanitize_output(str(e))}, waiting {wait_time}s...")
            time.sleep(wait_time)
    return None


def save_index(folder, index_content, docs_root=None):
    """
    Save index content to file.
    
    Args:
        folder: Folder name
        index_content: The index content to save
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    index_dir = Path(docs_root) / INDEX_DIR
    index_dir.mkdir(exist_ok=True)
    index_file = index_dir / f"{folder.replace('/', '-')}.index.md"
    index_file.write_text(index_content, encoding='utf-8')
    return index_file


def load_index(folder, docs_root=None):
    """
    Load index content for a folder.
    
    Args:
        folder: Folder name
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    index_file = Path(docs_root) / INDEX_DIR / f"{folder.replace('/', '-')}.index.md"
    if index_file.exists():
        return index_file.read_text(encoding='utf-8')
    return None


def load_all_indexes(docs_root=None):
    """
    Load all index files.
    
    Args:
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    indexes = {}
    index_dir = Path(docs_root) / INDEX_DIR
    if not index_dir.exists():
        return indexes
    
    for index_file in index_dir.glob("*.index.md"):
        folder_name = index_file.stem.replace(".index", "").replace("-", "/")
        # Handle simple folder names (no nested paths in stem)
        if "/" not in folder_name:
            folder_name = index_file.stem.replace(".index", "")
        indexes[folder_name] = index_file.read_text(encoding='utf-8')
    
    return indexes


def build_all_indexes(force=False):
    """
    Build indexes for all documentation folders.
    
    Args:
        force: If True, rebuild all indexes regardless of whether docs changed
    
    Returns:
        dict: Results for each folder
    """
    print("Building documentation indexes...")
    
    manifest = load_manifest()
    doc_folders = get_doc_folders()
    client = get_client()
    
    folders_to_build = []
    for folder in doc_folders:
        if force or folder_needs_reindex(folder, manifest):
            folders_to_build.append(folder)
        else:
            print(f"Skipping {folder} (no changes)")
    
    if not folders_to_build:
        print("All indexes are up to date")
        return {"status": "up_to_date", "folders": doc_folders}
    
    print(f"Building indexes for {len(folders_to_build)} folders: {folders_to_build}")
    
    results = {}
    
    # Build indexes in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_INDEX) as executor:
        futures = {
            executor.submit(build_index_for_folder_with_retry, folder, client): folder
            for folder in folders_to_build
        }
        
        for future in as_completed(futures):
            folder = futures[future]
            try:
                index_content = future.result()
                if index_content:
                    save_index(folder, index_content)
                    manifest["folders"][folder] = {
                        "built": datetime.now().isoformat(),
                        "doc_hashes": get_folder_doc_hashes(folder)
                    }
                    results[folder] = "success"
                    print(f"✅ Built index for {folder}")
                else:
                    results[folder] = "empty"
                    print(f"⚠️ No content for {folder}")
            except Exception as e:
                results[folder] = f"error: {e}"
                print(f"❌ Failed to build index for {folder}: {sanitize_output(str(e))}")
    
    save_manifest(manifest)
    
    return {
        "status": "built",
        "folders_built": list(results.keys()),
        "results": results
    }


def update_indexes_if_needed():
    """
    Check for doc changes and update indexes as needed.
    
    Returns:
        list: Folders that were updated
    """
    manifest = load_manifest()
    doc_folders = get_doc_folders()
    client = get_client()
    
    updated_folders = []
    
    for folder in doc_folders:
        if folder_needs_reindex(folder, manifest):
            print(f"Docs changed in {folder}, regenerating index...")
            index_content = build_index_for_folder_with_retry(folder, client)
            if index_content:
                save_index(folder, index_content)
                manifest["folders"][folder] = {
                    "built": datetime.now().isoformat(),
                    "doc_hashes": get_folder_doc_hashes(folder)
                }
                updated_folders.append(folder)
                print(f"✅ Updated index for {folder}")
    
    if updated_folders:
        save_manifest(manifest)
    
    return updated_folders


def commit_indexes_to_repo(content_type="indexes"):
    """
    Commit the .doc-index folder to the repository.
    
    This persists the indexes/summaries so they don't need to be rebuilt on every run.
    
    Args:
        content_type: What's being committed - "indexes" or "summaries" (for clearer logs)
    
    Returns:
        bool: True if content was committed successfully, False otherwise
    """
    docs_root = get_docs_root().resolve()  # Resolve to absolute path
    index_path = docs_root / INDEX_DIR
    
    if not index_path.exists():
        print(f"No {content_type} to commit")
        return False
    
    try:
        # Get the original working directory
        original_cwd = os.getcwd()
        
        # Need to be at repo root for git operations
        # If we're in a subfolder (like docs/), go up
        docs_subfolder = os.environ.get("DOCS_SUBFOLDER", "")
        if docs_subfolder:
            # We're in the docs subfolder, go to repo root
            repo_root = docs_root.parent
            os.chdir(str(repo_root))
            index_relative_path = f"{docs_subfolder}/{INDEX_DIR}"
            print(f"DEBUG: Changed to repo root: {repo_root}, index path: {index_relative_path}")
        else:
            # We're at repo root or in a separate docs repo
            os.chdir(str(docs_root))
            index_relative_path = INDEX_DIR
            print(f"DEBUG: Staying in docs root: {docs_root}, index path: {index_relative_path}")
        
        # Check if there are any changes to commit
        status_result = run_command_safe(
            ["git", "status", "--porcelain", index_relative_path],
            check=False
        )
        
        if not status_result.stdout.strip():
            print(f"No {content_type} changes to commit")
            os.chdir(original_cwd)
            return False
        
        # Check if the current branch is up-to-date with main before pushing
        # This prevents older branches from overwriting newer indexes
        base_branch = os.environ.get("DOCS_BASE_BRANCH", "main")
        
        # Fetch latest main to get accurate comparison
        run_command_safe(["git", "fetch", "origin", base_branch], check=False)
        
        # Get the merge-base between current HEAD and origin/main
        merge_base_result = run_command_safe(
            ["git", "merge-base", "HEAD", f"origin/{base_branch}"],
            check=False
        )
        
        # Get the latest commit on origin/main
        main_head_result = run_command_safe(
            ["git", "rev-parse", f"origin/{base_branch}"],
            check=False
        )
        
        branch_up_to_date = True
        if merge_base_result.returncode == 0 and main_head_result.returncode == 0:
            merge_base = merge_base_result.stdout.strip()
            main_head = main_head_result.stdout.strip()
            branch_up_to_date = (merge_base == main_head)
        
        # Track what files to add (used both here and after branch switch)
        files_to_add = []
        add_all = False
        
        if branch_up_to_date:
            # Branch is up-to-date, safe to push everything
            add_all = True
            run_command_safe(["git", "add", index_relative_path], check=True)
        else:
            # Branch is not up-to-date - be selective about what we push
            print(f"⚠️  Branch is not up-to-date with {base_branch}")
            
            if content_type == "indexes":
                # For folder indexes: skip entirely (could overwrite newer indexes on main)
                print(f"   Skipping index push to avoid overwriting newer indexes on {base_branch}")
                print(f"   Indexes will be used locally but not committed")
                os.chdir(original_cwd)
                return False
            
            # For summaries: only push if doc content matches main (safe)
            print(f"   Checking which summaries are safe to push...")
            
            safe_summaries = get_safe_summaries_to_push(base_branch)
            
            if safe_summaries:
                print(f"   Found {len(safe_summaries)} summaries safe to push (doc content matches main)")
                # Track the files we're adding (for use after branch switch)
                files_to_add = list(safe_summaries)
                files_to_add.append(f"{index_relative_path}/{SUMMARIES_MANIFEST}")
                
                # Add only the safe summaries and the summaries manifest
                for summary_path in safe_summaries:
                    run_command_safe(["git", "add", summary_path], check=False)
                # Also add the summaries manifest (stored at .doc-index/summaries_manifest.json)
                summaries_manifest_path = f"{index_relative_path}/{SUMMARIES_MANIFEST}"
                run_command_safe(["git", "add", summaries_manifest_path], check=False)
            else:
                print(f"   No summaries safe to push, skipping commit")
                os.chdir(original_cwd)
                return False
        
        # Check if there's actually anything staged
        staged_result = run_command_safe(
            ["git", "diff", "--cached", "--name-only"],
            check=False
        )
        if not staged_result.stdout.strip():
            print("No changes staged for commit")
            os.chdir(original_cwd)
            return False
        
        # Commit the indexes
        commit_msg = "chore: Update documentation semantic indexes\n\nAuto-generated by code-to-docs action"
        run_command_safe(
            ["git", "commit", "-m", commit_msg],
            check=True
        )
        
        # Push to the base/main branch so indexes are reusable across all PRs
        # (base_branch already defined above for the up-to-date check)
        
        # Get current branch to restore later
        current_branch_result = run_command_safe(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True
        )
        current_branch = current_branch_result.stdout.strip()
        
        # If we're not on the base branch, we need to:
        # 1. Stash or commit current changes
        # 2. Checkout base branch
        # 3. Cherry-pick or apply the index commit
        # 4. Push to base branch
        # 5. Return to original branch
        
        if current_branch != base_branch:
            print(f"Switching to {base_branch} to push {content_type}...")
            
            # Save the .doc-index directory content BEFORE switching branches
            # (because cherry-pick often fails and stash doesn't help since we already committed)
            temp_dir = tempfile.mkdtemp()
            index_full_path = Path(repo_root) / index_relative_path
            temp_index_path = Path(temp_dir) / ".doc-index-backup"
            if index_full_path.exists():
                shutil.copytree(index_full_path, temp_index_path)
            
            # Stash any uncommitted changes
            run_command_safe(["git", "stash", "--include-untracked"], check=False)
            
            # Checkout base branch (create or reset local branch from origin)
            run_command_safe(["git", "fetch", "origin", base_branch], check=False)
            # Use -B to create/reset local branch from origin (handles case where local branch doesn't exist)
            run_command_safe(["git", "checkout", "-B", base_branch, f"origin/{base_branch}"], check=True)
            
            # Restore our saved .doc-index directory (overwrites main's version with our updated version)
            if temp_index_path.exists():
                if index_full_path.exists():
                    shutil.rmtree(index_full_path)
                shutil.copytree(temp_index_path, index_full_path)
            
            # Add files - respect the same selective logic we used on PR branch
            if add_all:
                run_command_safe(["git", "add", index_relative_path], check=True)
            else:
                # Add only the specific files we determined were safe
                for file_path in files_to_add:
                    run_command_safe(["git", "add", file_path], check=False)
            run_command_safe(
                ["git", "commit", "-m", commit_msg],
                check=False  # May fail if no changes
            )
            
            # Clean up temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            # Push to base branch
            print(f"Pushing {content_type} to {base_branch}...")
            run_command_safe(
                ["git", "push", "origin", base_branch],
                check=True
            )
            
            # Return to original branch
            run_command_safe(["git", "checkout", current_branch], check=True)
            
            # Restore stashed changes (if any)
            run_command_safe(["git", "stash", "pop"], check=False)
            
            print(f"✅ {content_type.capitalize()} committed and pushed to {base_branch}")
        else:
            # Already on base branch, just push
            print(f"Pushing {content_type} to {base_branch}...")
            run_command_safe(
                ["git", "push", "origin", base_branch],
                check=True
            )
            print(f"✅ {content_type.capitalize()} committed and pushed to {base_branch}")
        os.chdir(original_cwd)
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to commit {content_type}: {sanitize_output(str(e))}")
        try:
            os.chdir(original_cwd)
        except:
            pass
        return False
    except Exception as e:
        print(f"Warning: Error committing {content_type}: {sanitize_output(str(e))}")
        try:
            os.chdir(original_cwd)
        except:
            pass
        return False


def find_relevant_areas_from_indexes(diff, client=None):
    """
    Use indexes to find which documentation AREAS are relevant to a code diff.
    
    This is the first stage of the two-stage lookup:
    1. Find relevant areas (this function) - processes in batches to avoid large prompts
    2. Find exact files within those areas (separate function)
    
    Args:
        diff: The code diff to analyze
        client: Optional Gemini client
    
    Returns:
        list: Folder names that might contain relevant documentation
    """
    if client is None:
        client = get_client()
    
    indexes = load_all_indexes()
    
    if not indexes:
        print("No indexes found, falling back to full scan")
        return None  # Signal to use full scan
    
    # Process indexes in batches to avoid huge prompts
    # Smaller batches = more focused evaluation per batch
    BATCH_SIZE = 5
    all_folders = list(indexes.keys())
    all_relevant_areas = []
    
    total_batches = (len(all_folders) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"Processing {len(all_folders)} doc areas in {total_batches} batches...")
    
    for batch_idx in range(0, len(all_folders), BATCH_SIZE):
        batch_folders = all_folders[batch_idx:batch_idx + BATCH_SIZE]
        batch_num = (batch_idx // BATCH_SIZE) + 1
        
        # Build indexes for this batch only
        batch_indexes = "\n\n" + "="*50 + "\n\n".join([
            f"## Documentation Area: {folder}\n\n{indexes[folder]}"
            for folder in batch_folders
        ])
        
        prompt = f"""
You are analyzing a code diff to determine which documentation areas might need updates.

CODE DIFF:
```
{diff[:10000]}
```

DOCUMENTATION AREAS TO EVALUATE (batch {batch_num}/{total_batches}):
{batch_indexes}

TASK:
From the areas listed above, identify ONLY folders whose documentation would become FACTUALLY INCORRECT without an update.

START WITH THE ASSUMPTION: No documentation needs updating. This is true for most code changes.
Your job is to find EXCEPTIONS to this rule - cases where docs would become WRONG.

BEFORE selecting ANY folder, you MUST be able to answer YES to ALL of these:
1. Based on the index, does this folder document behavior that this code change DIRECTLY modifies?
2. Would the documented instructions/information become WRONG after this change?
3. Can I identify from the index summary WHAT SPECIFICALLY would become incorrect?

If you cannot answer YES to all three → return []

DO NOT SELECT folders for:
- Code that is "related to" or "used by" the documented component
- Changes to implementation details that don't affect documented behavior
- Changes where the documentation is still technically accurate

When in doubt, do NOT include.


IMPORTANT: You MUST respond with a valid JSON array. No other text or explanation.
- If folders need updates: ["folder-1","folder-2"]
- If NO folders need updates: []

You MUST output something. An empty response is not valid - output [] instead.
"""
        
        batch_relevant = _process_area_batch(client, prompt, batch_num, total_batches, batch_folders)
        if batch_relevant:
            all_relevant_areas.extend(batch_relevant)
    
    # Deduplicate
    all_relevant_areas = list(dict.fromkeys(all_relevant_areas))
    
    if not all_relevant_areas:
        print("AI found no relevant documentation areas in any batch")
        return []
    
    print(f"Total relevant documentation areas ({len(all_relevant_areas)}): {all_relevant_areas}")
    return all_relevant_areas


def _process_area_batch(client, prompt, batch_num, total_batches, batch_folders):
    """
    Process a single batch of indexes to find relevant areas.
    
    Args:
        client: Gemini client
        prompt: The prompt for this batch
        batch_num: Current batch number
        total_batches: Total number of batches
        batch_folders: Folders in this batch
    
    Returns:
        list: Relevant folder names from this batch
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0)
                ),
            )
            
            # Check for empty or malformed response (safely access response.text)
            try:
                response_text = response.text
            except Exception as text_err:
                print(f"Batch {batch_num}/{total_batches}: Could not get response text (attempt {attempt + 1})")
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                else:
                    print(f"Batch {batch_num}/{total_batches}: Failed after retries, skipping batch")
                    return []
            
            if not response_text or not response_text.strip():
                if attempt < max_retries - 1:
                    print(f"Batch {batch_num}/{total_batches}: Empty response (attempt {attempt + 1}), retrying...")
                    time.sleep(2 * (attempt + 1))
                    continue
                else:
                    # Treat empty response as "no relevant folders" - this is likely the AI's intent
                    print(f"Batch {batch_num}/{total_batches}: Empty response after retries, treating as no relevant areas")
                    return []
            
            result_text = response_text.strip()
            
            # Clean up response - remove markdown code blocks if present
            if result_text.startswith("```"):
                result_text = result_text.split("\n", 1)[1]
            if result_text.endswith("```"):
                result_text = result_text.rsplit("\n", 1)[0]
            result_text = result_text.strip()
            
            relevant_areas = json.loads(result_text)
            
            # Filter to only include folders from this batch
            relevant_areas = [f for f in relevant_areas if f in batch_folders]
            
            if relevant_areas:
                print(f"Batch {batch_num}/{total_batches}: Found relevant areas: {relevant_areas}")
            else:
                print(f"Batch {batch_num}/{total_batches}: No relevant areas")
            
            return relevant_areas
            
        except json.JSONDecodeError:
            if attempt < max_retries - 1:
                print(f"Batch {batch_num}/{total_batches}: JSON parse error (attempt {attempt + 1}), retrying...")
                time.sleep(2 * (attempt + 1))
                continue
            print(f"Batch {batch_num}/{total_batches}: JSON parse failed, skipping batch")
            return []
        except Exception as e:
            # Retry on any exception
            if attempt < max_retries - 1:
                wait_time = 3 * (attempt + 1)
                print(f"Batch {batch_num}/{total_batches}: Error (attempt {attempt + 1}), waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            print(f"Batch {batch_num}/{total_batches}: Failed after retries - {sanitize_output(str(e))}")
            return []
    
    return []


def get_files_in_areas(areas, docs_root=None):
    """
    Get all documentation files in the specified areas.
    
    Args:
        areas: List of folder names
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    
    Returns:
        list: List of file paths (relative to docs_root)
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    docs_root = Path(docs_root)
    files = []
    
    for area in areas:
        area_path = docs_root / area
        if area_path.exists():
            for ext in ["*.rst", "*.md", "*.adoc"]:
                for f in area_path.rglob(ext):
                    # Return path relative to docs_root for consistency
                    try:
                        rel_path = f.relative_to(docs_root)
                        files.append(str(rel_path))
                    except ValueError:
                        files.append(str(f))
    
    # Also include root-level documentation files (not in subdirectories)
    # These are often important overview/index docs that could be affected by many changes
    for ext in ["*.rst", "*.md", "*.adoc"]:
        for root_doc in docs_root.glob(ext):
            if root_doc.is_file():
                files.append(root_doc.name)
    
    return list(set(files))  # Deduplicate


def fetch_indexes_from_main():
    """
    Fetch indexes and summaries from the main/base branch.
    
    This ensures PRs can benefit from cached indexes and summaries on main,
    even if they were generated by previous PR runs.
    
    Returns:
        bool: True if indexes/summaries were fetched, False otherwise
    """
    docs_root = get_docs_root().resolve()
    index_dir = docs_root / INDEX_DIR
    
    try:
        original_cwd = os.getcwd()
        
        # Determine paths
        docs_subfolder = os.environ.get("DOCS_SUBFOLDER", "")
        if docs_subfolder:
            repo_root = docs_root.parent
            os.chdir(str(repo_root))
            index_relative_path = f"{docs_subfolder}/{INDEX_DIR}"
        else:
            os.chdir(str(docs_root))
            index_relative_path = INDEX_DIR
        
        base_branch = os.environ.get("DOCS_BASE_BRANCH", "main")
        
        print(f"Checking for cached indexes/summaries on {base_branch} branch...")
        
        # Fetch the base branch
        run_command_safe(["git", "fetch", "origin", base_branch], check=False)
        
        # Check if index directory exists on the base branch
        check_result = run_command_safe(
            ["git", "ls-tree", "-r", f"origin/{base_branch}", "--name-only"],
            check=False
        )
        
        if check_result.returncode != 0 or index_relative_path not in check_result.stdout:
            print(f"No cached indexes/summaries found on {base_branch} branch")
            os.chdir(original_cwd)
            return False
        
        # Checkout the index directory from main (includes summaries)
        print(f"Fetching indexes and summaries from {base_branch}...")
        checkout_result = run_command_safe(
            ["git", "checkout", f"origin/{base_branch}", "--", index_relative_path],
            check=False
        )
        
        if checkout_result.returncode == 0:
            print(f"✅ Fetched indexes and summaries from {base_branch}")
            os.chdir(original_cwd)
            return True
        else:
            print(f"Could not fetch indexes/summaries from {base_branch}")
            os.chdir(original_cwd)
            return False
            
    except Exception as e:
        print(f"Warning: Error fetching indexes from main: {sanitize_output(str(e))}")
        try:
            os.chdir(original_cwd)
        except:
            pass
        return False


def indexes_exist(docs_root=None):
    """
    Check if indexes have been built.
    
    Args:
        docs_root: Optional root path for docs. If None, uses get_docs_root()
    
    Returns:
        bool: True if index files exist
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    index_dir = Path(docs_root) / INDEX_DIR
    if not index_dir.exists():
        return False
    
    index_files = list(index_dir.glob("*.index.md"))
    return len(index_files) > 0


# =============================================================================
# FILE SUMMARY CACHING
# =============================================================================
# Caches AI-generated summaries for long documentation files to avoid
# regenerating them on every run.


def get_summaries_dir(docs_root=None):
    """Get the summaries directory path."""
    if docs_root is None:
        docs_root = get_docs_root()
    return Path(docs_root) / INDEX_DIR / SUMMARIES_DIR


def load_summaries_manifest(docs_root=None):
    """Load the summaries manifest file."""
    if docs_root is None:
        docs_root = get_docs_root()
    
    manifest_path = Path(docs_root) / INDEX_DIR / SUMMARIES_MANIFEST
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            # Manifest is corrupted (likely from race condition), start fresh
            print(f"Warning: Corrupted summaries manifest, starting fresh: {e}")
            return {"version": "1.0", "files": {}}
    return {"version": "1.0", "files": {}}


def save_summaries_manifest(manifest, docs_root=None):
    """Save the summaries manifest file."""
    if docs_root is None:
        docs_root = get_docs_root()
    
    index_dir = Path(docs_root) / INDEX_DIR
    index_dir.mkdir(exist_ok=True)
    manifest["updated"] = datetime.now().isoformat()
    with open(index_dir / SUMMARIES_MANIFEST, 'w') as f:
        json.dump(manifest, f, indent=2)


def get_summary_filename(file_path):
    """Convert a file path to a summary filename."""
    # Replace path separators with dashes and add .summary.md extension
    safe_name = str(file_path).replace("/", "-").replace("\\", "-")
    return f"{safe_name}.summary.md"


def load_cached_summary(file_path, docs_root=None):
    """
    Load a cached summary for a file if it exists and is still valid.
    
    Args:
        file_path: Path to the original documentation file
        docs_root: Optional docs root path
    
    Returns:
        str: The cached summary, or None if not found or outdated
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    manifest = load_summaries_manifest(docs_root)
    file_key = str(file_path)
    
    # Debug: show manifest state on first call
    manifest_files = manifest.get("files", {})
    if len(manifest_files) > 0 and not hasattr(load_cached_summary, '_debug_shown'):
        print(f"DEBUG: Summaries manifest has {len(manifest_files)} entries")
        load_cached_summary._debug_shown = True
    
    # Check if we have a cached summary
    if file_key not in manifest_files:
        return None
    
    # Check if the file has changed since the summary was generated
    try:
        current_hash = hash_file(Path(docs_root) / file_path)
    except:
        current_hash = hash_file(file_path)
    
    stored_hash = manifest_files[file_key].get("hash")
    if current_hash != stored_hash:
        return None  # File changed, need to regenerate
    
    # Load the summary file
    summary_file = get_summaries_dir(docs_root) / get_summary_filename(file_path)
    if summary_file.exists():
        return summary_file.read_text(encoding='utf-8')
    
    return None


def save_summary(file_path, summary, docs_root=None):
    """
    Save a generated summary to cache.
    
    Args:
        file_path: Path to the original documentation file
        summary: The generated summary text
        docs_root: Optional docs root path
    """
    if docs_root is None:
        docs_root = get_docs_root()
    
    # Ensure summaries directory exists
    summaries_dir = get_summaries_dir(docs_root)
    summaries_dir.mkdir(parents=True, exist_ok=True)
    
    # Save the summary file
    summary_file = summaries_dir / get_summary_filename(file_path)
    summary_file.write_text(summary, encoding='utf-8')
    
    # Update the manifest (thread-safe to prevent race conditions in parallel generation)
    with _manifest_lock:
        manifest = load_summaries_manifest(docs_root)
        
        # Calculate file hash
        try:
            file_hash = hash_file(Path(docs_root) / file_path)
        except:
            file_hash = hash_file(file_path)
        
        manifest["files"][str(file_path)] = {
            "hash": file_hash,
            "generated": datetime.now().isoformat(),
            "summary_file": str(summary_file.name)
        }
        
        save_summaries_manifest(manifest, docs_root)


def get_or_generate_summary(file_path, content, generate_func, docs_root=None):
    """
    Get a cached summary or generate a new one.
    
    Args:
        file_path: Path to the documentation file
        content: The file content (used if we need to generate)
        generate_func: Function to call to generate summary (takes file_path, content)
        docs_root: Optional docs root path
    
    Returns:
        str: The summary (cached or newly generated)
    """
    # Try to load cached summary
    cached = load_cached_summary(file_path, docs_root)
    if cached:
        return cached
    
    # Generate new summary
    summary = generate_func(file_path, content)
    
    # Cache it for next time
    if summary:
        save_summary(file_path, summary, docs_root)
    
    return summary


def summaries_exist(docs_root=None):
    """Check if any cached summaries exist."""
    if docs_root is None:
        docs_root = get_docs_root()
    
    summaries_dir = get_summaries_dir(docs_root)
    if not summaries_dir.exists():
        return False
    
    summary_files = list(summaries_dir.glob("*.summary.md"))
    return len(summary_files) > 0


def doc_matches_main(doc_file_path, base_branch="main"):
    """
    Check if a local doc file matches the same file on origin/main.
    
    This determines if a summary generated from this doc is safe to push to main.
    
    Args:
        doc_file_path: Path to the doc file (relative to docs root)
        base_branch: The base branch to compare against
    
    Returns:
        bool: True if file matches main (safe to push summary), False otherwise
    """
    try:
        docs_root = get_docs_root()
        
        # Get local file hash
        local_path = Path(docs_root) / doc_file_path
        if not local_path.exists():
            local_path = Path(doc_file_path)
        
        if not local_path.exists():
            return False
        
        local_hash = hash_file(local_path)
        
        # Get file content from origin/main
        docs_subfolder = os.environ.get("DOCS_SUBFOLDER", "")
        if docs_subfolder:
            main_file_path = f"{docs_subfolder}/{doc_file_path}"
        else:
            main_file_path = doc_file_path
        
        result = run_command_safe(
            ["git", "show", f"origin/{base_branch}:{main_file_path}"],
            check=False
        )
        
        if result.returncode != 0:
            # File doesn't exist on main - check if it's a new file
            # New files are safe to push
            return True
        
        # Hash main's content
        main_hash = hashlib.sha256(result.stdout.encode()).hexdigest()
        
        return local_hash == main_hash
    except Exception as e:
        print(f"Warning: Could not compare {doc_file_path} with main: {sanitize_output(str(e))}")
        return False


def get_safe_summaries_to_push(base_branch="main"):
    """
    Get list of summary files that are safe to push to main.
    
    A summary is safe to push if the doc file it was generated from
    matches the same doc file on origin/main.
    
    Args:
        base_branch: The base branch to compare against
    
    Returns:
        list: List of summary file paths that are safe to push
    """
    docs_root = get_docs_root()
    manifest = load_summaries_manifest(docs_root)
    
    safe_summaries = []
    
    for doc_path, info in manifest.get("files", {}).items():
        summary_file = info.get("summary_file")
        if not summary_file:
            continue
        
        # Check if doc matches main
        if doc_matches_main(doc_path, base_branch):
            summary_path = get_summaries_dir(docs_root) / summary_file
            if summary_path.exists():
                safe_summaries.append(str(summary_path))
    
    return safe_summaries


# CLI interface for testing
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Documentation Index Management")
    parser.add_argument("--build", action="store_true", help="Build all indexes")
    parser.add_argument("--force", action="store_true", help="Force rebuild all indexes")
    parser.add_argument("--list", action="store_true", help="List all doc folders")
    parser.add_argument("--show", type=str, help="Show index for a specific folder")
    
    args = parser.parse_args()
    
    if args.list:
        folders = get_doc_folders()
        print(f"Documentation folders ({len(folders)}):")
        for f in folders:
            print(f"  - {f}")
    
    elif args.build:
        result = build_all_indexes(force=args.force)
        print(f"\nResult: {result['status']}")
        if result.get('folders_built'):
            print(f"Built indexes for: {result['folders_built']}")
    
    elif args.show:
        index = load_index(args.show)
        if index:
            print(index)
        else:
            print(f"No index found for {args.show}")
    
    else:
        parser.print_help()

