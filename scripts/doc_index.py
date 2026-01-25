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
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from google import genai
from google.genai import types

# Import security utilities for safe output
from security_utils import sanitize_output, run_command_safe

# Index configuration
INDEX_DIR = ".doc-index"
MANIFEST_FILE = "manifest.json"
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
            if len(content) > 15000:
                content = content[:15000] + "\n\n[... truncated for length ...]"
            docs_content.append({
                "path": str(doc),
                "content": content
            })
        except Exception as e:
            print(f"Warning: Could not read {doc}: {sanitize_output(str(e))}")
    
    if not docs_content:
        return None
    
    # Format docs for the prompt
    docs_text = "\n\n---\n\n".join([
        f"### File: {d['path']}\n\n{d['content'][:5000]}"  # First 5000 chars per file for context
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
    """Build index with retry logic for rate limiting"""
    for attempt in range(max_retries):
        try:
            return build_index_for_folder(folder, client)
        except Exception as e:
            if "ResourceExhausted" in str(e) or "429" in str(e):
                wait_time = 2 ** attempt
                print(f"Rate limited on {folder}, waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise
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


def commit_indexes_to_repo():
    """
    Commit the .doc-index folder to the repository.
    
    This persists the indexes so they don't need to be rebuilt on every run.
    
    Returns:
        bool: True if indexes were committed successfully, False otherwise
    """
    docs_root = get_docs_root()
    index_path = docs_root / INDEX_DIR
    
    if not index_path.exists():
        print("No indexes to commit")
        return False
    
    try:
        # Get the original working directory
        original_cwd = os.getcwd()
        
        # Need to be at repo root for git operations
        # If we're in a subfolder (like docs/), go up
        docs_subfolder = os.environ.get("DOCS_SUBFOLDER", "")
        if docs_subfolder:
            # We're in the docs subfolder, go to repo root
            os.chdir(str(docs_root.parent))
            index_relative_path = f"{docs_subfolder}/{INDEX_DIR}"
        else:
            # We're at repo root or in a separate docs repo
            os.chdir(str(docs_root))
            index_relative_path = INDEX_DIR
        
        # Check if there are any changes to commit
        status_result = run_command_safe(
            ["git", "status", "--porcelain", index_relative_path],
            check=False
        )
        
        if not status_result.stdout.strip():
            print("No index changes to commit")
            os.chdir(original_cwd)
            return False
        
        # Add the index directory
        run_command_safe(["git", "add", index_relative_path], check=True)
        
        # Commit the indexes
        commit_msg = "chore: Update documentation semantic indexes\n\nAuto-generated by code-to-docs action"
        run_command_safe(
            ["git", "commit", "-m", commit_msg],
            check=True
        )
        
        # Push to the base/main branch so indexes are reusable across all PRs
        base_branch = os.environ.get("DOCS_BASE_BRANCH", "main")
        
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
            print(f"Switching to {base_branch} to push indexes...")
            
            # Get the commit hash we just created
            commit_hash_result = run_command_safe(
                ["git", "rev-parse", "HEAD"],
                check=True
            )
            index_commit_hash = commit_hash_result.stdout.strip()
            
            # Checkout base branch
            run_command_safe(["git", "fetch", "origin", base_branch], check=False)
            run_command_safe(["git", "checkout", base_branch], check=True)
            run_command_safe(["git", "pull", "origin", base_branch], check=False)
            
            # Cherry-pick the index commit
            cherry_result = run_command_safe(
                ["git", "cherry-pick", index_commit_hash],
                check=False
            )
            
            if cherry_result.returncode != 0:
                # If cherry-pick fails (conflict), just add and commit the indexes directly
                print("Cherry-pick had conflicts, committing indexes directly...")
                run_command_safe(["git", "cherry-pick", "--abort"], check=False)
                run_command_safe(["git", "add", index_relative_path], check=True)
                run_command_safe(
                    ["git", "commit", "-m", commit_msg],
                    check=False  # May fail if no changes
                )
            
            # Push to base branch
            print(f"Pushing indexes to {base_branch}...")
            run_command_safe(
                ["git", "push", "origin", base_branch],
                check=True
            )
            
            # Return to original branch
            run_command_safe(["git", "checkout", current_branch], check=True)
            
            print(f"✅ Indexes committed and pushed to {base_branch}")
        else:
            # Already on base branch, just push
            print(f"Pushing indexes to {base_branch}...")
            run_command_safe(
                ["git", "push", "origin", base_branch],
                check=True
            )
            print(f"✅ Indexes committed and pushed to {base_branch}")
        os.chdir(original_cwd)
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to commit indexes: {sanitize_output(str(e))}")
        try:
            os.chdir(original_cwd)
        except:
            pass
        return False
    except Exception as e:
        print(f"Warning: Error committing indexes: {sanitize_output(str(e))}")
        try:
            os.chdir(original_cwd)
        except:
            pass
        return False


def find_relevant_areas_from_indexes(diff, client=None):
    """
    Use indexes to find which documentation AREAS are relevant to a code diff.
    
    This is the first stage of the two-stage lookup:
    1. Find relevant areas (this function) - 1 API call
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
    
    # Combine all indexes for the prompt
    all_indexes = "\n\n" + "="*50 + "\n\n".join([
        f"## Documentation Area: {folder}\n\n{content}"
        for folder, content in indexes.items()
    ])
    
    prompt = f"""
You are analyzing a code diff to determine which documentation areas might need updates.

CODE DIFF:
```
{diff[:10000]}
```

DOCUMENTATION AREA INDEXES:
{all_indexes}

TASK:
Based on the code changes and the documentation indexes, identify which documentation AREAS (folders) DIRECTLY need to be checked for updates.

STRICT RULES - BE VERY CONSERVATIVE:
1. For each index, read ALL sections: "Overview", "Code Changes That Would Require Documentation Updates", and "Key Technical Concepts"
2. The "Code Changes That Would Require Documentation Updates" section describes what types of changes are relevant to that folder
3. Select a folder ONLY if the code diff is clearly related to what that folder documents
4. Do NOT select folders that merely "use" or "depend on" the changed code
5. Select the MINIMUM number of areas necessary - prefer fewer with high relevance
6. When in doubt, select FEWER folders - it's better to miss a tangential doc than to process hundreds of irrelevant files

DECISION CRITERIA:
- Would a user reading this folder's docs need to know about this code change? If NO, don't select it.
- Is the code change an internal implementation detail that doesn't affect user-facing documentation? If YES, don't select it.

Return ONLY a JSON array of folder names, like: ["folder-1", "folder-2"]
If no areas seem relevant, return: []
Do not include any explanation, just the JSON array.
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
            
            # Check for empty or malformed response
            if not response.text or not response.text.strip():
                if attempt < max_retries - 1:
                    print(f"Empty response from AI (attempt {attempt + 1}), retrying...")
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                else:
                    print("AI returned empty response after all retries, falling back to full scan")
                    return None
            
            result_text = response.text.strip()
            
            # Clean up response - remove markdown code blocks if present
            if result_text.startswith("```"):
                result_text = result_text.split("\n", 1)[1]
            if result_text.endswith("```"):
                result_text = result_text.rsplit("\n", 1)[0]
            result_text = result_text.strip()
            
            relevant_areas = json.loads(result_text)
            
            if "*" in relevant_areas:
                print("AI requested full scan")
                return None  # Signal to use full scan
            
            if not relevant_areas:
                print("AI found no relevant documentation areas")
                return []  # No areas to check
            
            print(f"Relevant documentation areas ({len(relevant_areas)}): {relevant_areas}")
            return relevant_areas
            
        except json.JSONDecodeError as e:
            if attempt < max_retries - 1:
                print(f"JSON parse error (attempt {attempt + 1}), retrying...")
                time.sleep(2 ** attempt)
                continue
            print(f"Warning: Could not parse AI response as JSON: {sanitize_output(str(e))}")
            # Don't print full response as it might contain sensitive info from the prompt
            print("Response format was invalid, falling back to full scan")
            return None  # Fallback to full scan
        except Exception as e:
            error_str = str(e).lower()
            if "resource" in error_str or "quota" in error_str or "rate" in error_str:
                if attempt < max_retries - 1:
                    wait_time = 2 ** (attempt + 2)  # Longer wait for rate limits
                    print(f"Rate limit hit (attempt {attempt + 1}), waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
            print(f"Error finding relevant areas: {sanitize_output(str(e))}")
            return None  # Fallback to full scan
    
    return None  # Fallback after all retries exhausted


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

