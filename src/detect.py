"""Detect-only mode: identify docs affected by a diff without generating anything."""

import re
import sys
from pathlib import Path


def extract_changed_doc_paths(diff_text):
    """Extract documentation file paths that were modified in the diff."""
    doc_extensions = {".md", ".rst", ".adoc"}
    paths = set()
    for match in re.finditer(r"^diff --git a/(.+?) b/", diff_text, re.MULTILINE):
        path = match.group(1)
        if Path(path).suffix in doc_extensions:
            paths.add(path)
    return paths


def run_detect_only(diff, relevant_files, changed_docs):
    """Compare affected docs against docs actually changed in the PR.

    Returns (affected_but_untouched, summary_lines).
    """
    affected_set = set(relevant_files) if relevant_files else set()
    untouched = affected_set - changed_docs

    lines = []
    if untouched:
        lines.append(f"Found {len(untouched)} doc file(s) that may need updates:")
        for f in sorted(untouched):
            lines.append(f"  - {f}")
        lines.append("")
        lines.append(
            "Comment [review-docs] on the PR to review suggested changes, "
            "or [update-docs] to generate updates directly."
        )
    else:
        lines.append("All affected documentation files are already updated in this PR.")

    return untouched, lines


def exit_with_severity(untouched, severity):
    """Exit with the appropriate code based on severity setting."""
    if not untouched:
        return

    severity = (severity or "warn").lower()
    if severity == "error":
        print(f"Error: {len(untouched)} doc file(s) may need updates but were not changed.")
        sys.exit(1)
