"""
Centralized configuration for code-to-docs GitHub Action.

All environment variable access for configuration lives here.
Runtime env vars (GH_TOKEN, PR_NUMBER, etc.) are still read where needed.
"""

import os
import re

import openai
from openai import OpenAI


def get_client():
    """Get the shared OpenAI-compatible client."""
    return OpenAI(
        base_url=os.environ["MODEL_API_BASE"],
        api_key=os.environ.get("MODEL_API_KEY") or "EMPTY",
    )


def get_model_name():
    """Get the configured model name."""
    return os.environ.get("MODEL_NAME", "default")


def get_docs_repo_url():
    """Get the documentation repository URL."""
    return os.environ.get("DOCS_REPO_URL", "")


def get_branch_name(pr_number=None):
    """Get the docs update branch name, unique per PR to avoid collisions."""
    if pr_number and pr_number != "unknown":
        return f"doc-update-from-pr-{pr_number}"
    return "doc-update-from-pr"


# =============================================================================
# CONTEXT BUDGET AND TRUNCATION
# =============================================================================

_DEFAULT_MAX_CONTEXT_CHARS = 400_000


def get_max_context_chars():
    """
    Get the maximum character budget for LLM prompt content.

    Reads from MAX_CONTEXT_CHARS env var, defaults to 400,000 (~100K tokens).
    """
    raw = os.environ.get("MAX_CONTEXT_CHARS", "")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            print(f"Warning: Invalid MAX_CONTEXT_CHARS='{raw}', using default {_DEFAULT_MAX_CONTEXT_CHARS:,}")
            return _DEFAULT_MAX_CONTEXT_CHARS
        return value
    return _DEFAULT_MAX_CONTEXT_CHARS


def truncate_content(text, max_chars, label="content"):
    """
    Truncate text to max_chars if it exceeds the limit.

    Returns text unchanged if it fits. Otherwise truncates and appends
    a marker indicating how much was kept. Prints a warning.
    """
    if not text or len(text) <= max_chars:
        return text

    pct = max_chars * 100 // len(text)
    print(f"Warning: Truncated {label} from {len(text):,} to {max_chars:,} chars ({pct}% retained)")
    return text[:max_chars] + f"\n\n[... truncated: kept {max_chars:,} of {len(text):,} chars ...]"


def truncate_diff(diff_text, max_chars, label="diff"):
    """
    Truncate a unified diff to max_chars, preserving complete file-level diffs.

    Splits on 'diff --git' boundaries and greedily includes complete file-diffs.
    Falls back to character truncation if the first file-diff alone exceeds budget.
    """
    if not diff_text or len(diff_text) <= max_chars:
        return diff_text

    # Guard against negative or zero budget
    if max_chars <= 0:
        print(f"Warning: No budget remaining for {label}, skipping diff entirely. "
              f"Consider increasing MAX_CONTEXT_CHARS or reducing PR size.")
        return f"[... diff omitted: prompt content already exceeds context budget ...]"

    # Split into per-file sections
    parts = re.split(r'(?=\ndiff --git )', diff_text)

    total_files = sum(1 for p in parts if "diff --git " in p)

    # Greedily include complete file-diffs
    result = ""
    included = 0

    for part in parts:
        is_file_diff = "diff --git " in part
        suffix = f"\n\n[... truncated: showing {included}/{total_files} changed files, kept {len(result):,} of {len(diff_text):,} chars ...]"

        if len(result) + len(part) + len(suffix) <= max_chars:
            result += part
            if is_file_diff:
                included += 1
        else:
            break

    if included == 0:
        # Even one file-diff is too large — fall back to character cut
        suffix = f"\n\n[... truncated: showing 0/{total_files} complete files, kept {max_chars:,} of {len(diff_text):,} chars ...]"
        result = diff_text[:max_chars - len(suffix)]
        pct = (max_chars) * 100 // len(diff_text)
        print(f"Warning: Truncated {label} from {len(diff_text):,} to {max_chars:,} chars ({pct}% retained, 0/{total_files} complete files)")
        return result + suffix

    suffix = f"\n\n[... truncated: showing {included}/{total_files} changed files, kept {len(result):,} of {len(diff_text):,} chars ...]"
    pct = len(result) * 100 // len(diff_text)
    print(f"Warning: Truncated {label} from {len(diff_text):,} to ~{len(result):,} chars ({pct}% retained, {included}/{total_files} files)")
    return result + suffix


def check_context_error(e):
    """
    If e is a context-window error, print actionable guidance.

    Returns True if a context error was detected, False otherwise.
    Does NOT re-raise — the caller decides whether to raise or continue.
    """
    if isinstance(e, openai.BadRequestError):
        msg = str(e).lower()
        if any(kw in msg for kw in [
            "context length",
            "maximum context",
            "number of tokens",
            "token limit",
        ]):
            print(
                "Error: Prompt exceeded model context window. "
                "Set MAX_CONTEXT_CHARS to a lower value "
                "(e.g. 32000 for an 8K-token model)."
            )
            return True
    return False
