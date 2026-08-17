"""Doc-to-code linkage: deterministic file selection from front-matter declarations."""

import re
from pathlib import Path

import yaml


def parse_doc_frontmatter(file_path):
    """Extract code-to-docs front-matter from a documentation file.

    Supports YAML front-matter (--- delimiters) for .md files, and
    comment-based declarations for .rst and .adoc files.

    Returns a dict with a "covers" key (list of source paths), or
    empty dict if no declaration is found.
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except Exception:
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
                    return {"covers": [str(p) for p in covers]}
    except yaml.YAMLError:
        pass
    return {}


def _parse_rst_directive(content):
    """Parse .. code-to-docs:: covers: path1, path2 from rst."""
    match = re.search(r"^\.\.\s+code-to-docs::\s*covers:\s*(.+)$", content, re.MULTILINE)
    if match:
        paths = [p.strip() for p in match.group(1).split(",") if p.strip()]
        return {"covers": paths} if paths else {}
    return {}


def _parse_adoc_comment(content):
    """Parse // code-to-docs: covers: path1, path2 from adoc."""
    match = re.search(r"^//\s*code-to-docs:\s*covers:\s*(.+)$", content, re.MULTILINE)
    if match:
        paths = [p.strip() for p in match.group(1).split(",") if p.strip()]
        return {"covers": paths} if paths else {}
    return {}


def extract_changed_paths(diff_text):
    """Extract all file paths changed in a unified diff."""
    paths = set()
    for match in re.finditer(r"^diff --git a/(.+?) b/", diff_text, re.MULTILINE):
        paths.add(match.group(1))
    return paths


def find_declared_docs(diff_text, doc_root="."):
    """Find doc files whose declared covers paths intersect with the diff.

    Returns a list of (doc_path, "declared") tuples for docs that match,
    and scans all doc files in doc_root.
    """
    changed = extract_changed_paths(diff_text)
    if not changed:
        return []

    doc_extensions = {".md", ".rst", ".adoc"}
    declared = []

    for doc in Path(doc_root).rglob("*"):
        if not doc.is_file() or doc.suffix not in doc_extensions:
            continue
        if ".doc-index" in str(doc):
            continue
        fm = parse_doc_frontmatter(str(doc))
        covers = fm.get("covers", [])
        if not covers:
            continue
        for covered_path in covers:
            if any(c == covered_path or c.startswith(covered_path + "/") for c in changed):
                declared.append((str(doc), "declared"))
                break

    return declared
