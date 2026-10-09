"""
Doc-to-code linkage: deterministic file selection from front-matter declarations.

Parses front-matter from .md, .rst, and .adoc files to find declared
source-file coverage, then selects docs whose covers paths intersect
with the diff.
"""

import os
import re
from pathlib import Path

import yaml

from security_utils import validate_file_path


def _is_safe_covers_path(path_str):
    """Check that a covers path is safe (no traversal, absolute paths, or null bytes).

    Args:
        path_str: A path string from a covers declaration.

    Returns:
        True if the path is safe for matching, False otherwise.
    """
    if not path_str or not isinstance(path_str, str):
        return False
    if "\x00" in path_str:
        return False
    if os.path.isabs(path_str):
        return False
    if ".." in Path(path_str).parts:
        return False
    return True


def parse_doc_frontmatter(file_path, base_dir=None):
    """Extract code-to-docs front-matter from a documentation file.

    Supports YAML front-matter (--- delimiters) for .md files, and
    comment-based declarations for .rst and .adoc files.

    Args:
        file_path: Path to the documentation file.
        base_dir: Base directory for path validation. If provided,
            file_path is validated to be within this directory.

    Returns:
        A dict with a "covers" key (list of source paths), or
        empty dict if no declaration is found.
    """
    if base_dir is not None and not validate_file_path(file_path, base_dir=base_dir):
        return {}

    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except Exception as e:
        print(f"Warning: could not read {file_path}: {e}")
        return {}

    suffix = Path(file_path).suffix

    if suffix == ".md":
        return _parse_yaml_frontmatter(content)
    elif suffix == ".rst":
        return _parse_rst_directive(content)
    elif suffix == ".adoc":
        return _parse_adoc_comment(content)
    return {}


def _parse_yaml_frontmatter(content):
    """Parse YAML front-matter between --- delimiters."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        fm = yaml.safe_load(parts[1])
        if isinstance(fm, dict) and "code-to-docs" in fm:
            ctd = fm["code-to-docs"]
            if isinstance(ctd, dict) and "covers" in ctd:
                covers = ctd["covers"]
                if isinstance(covers, list):
                    safe = [str(p) for p in covers if _is_safe_covers_path(str(p))]
                    return {"covers": safe} if safe else {}
    except yaml.YAMLError:
        pass
    return {}


def _parse_rst_directive(content):
    """Parse .. code-to-docs:: covers: path1, path2 from rst."""
    match = re.search(r"^\.\.\s+code-to-docs::\s*covers:\s*(.+)$", content, re.MULTILINE)
    if match:
        paths = [p.strip() for p in match.group(1).split(",") if p.strip()]
        safe = [p for p in paths if _is_safe_covers_path(p)]
        return {"covers": safe} if safe else {}
    return {}


def _parse_adoc_comment(content):
    """Parse // code-to-docs: covers: path1, path2 from adoc."""
    match = re.search(r"^//\s*code-to-docs:\s*covers:\s*(.+)$", content, re.MULTILINE)
    if match:
        paths = [p.strip() for p in match.group(1).split(",") if p.strip()]
        safe = [p for p in paths if _is_safe_covers_path(p)]
        return {"covers": safe} if safe else {}
    return {}


def extract_changed_paths(diff_text):
    """Extract all file paths changed in a unified diff.

    Args:
        diff_text: Unified diff text.

    Returns:
        A set of file paths changed in the diff.
    """
    paths = set()
    for match in re.finditer(r"^diff --git a/(.+?) b/", diff_text, re.MULTILINE):
        paths.add(match.group(1))
    return paths


def find_declared_docs(diff_text, doc_root="."):
    """Find doc files whose declared covers paths intersect with the diff.

    Scans all doc files in doc_root for front-matter declarations, then
    returns those whose covers paths overlap with the changed files.

    Args:
        diff_text: Unified diff text.
        doc_root: Root directory to scan for documentation files.

    Returns:
        A list of doc file paths (strings) that declare coverage of
        changed source files.
    """
    changed = extract_changed_paths(diff_text)
    if not changed:
        return []

    doc_extensions = {".md", ".rst", ".adoc"}
    declared = []

    for doc in Path(doc_root).rglob("*"):
        if doc.is_symlink():
            continue
        if not doc.is_file() or doc.suffix not in doc_extensions:
            continue
        if ".doc-index" in str(doc):
            continue
        fm = parse_doc_frontmatter(str(doc), base_dir=doc_root)
        covers = fm.get("covers", [])
        if not covers:
            continue
        for covered_path in covers:
            if any(c == covered_path or c.startswith(covered_path + "/") for c in changed):
                declared.append(str(doc))
                break

    return declared
