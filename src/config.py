"""
Centralized configuration for code-to-docs GitHub Action.

All environment variable access for configuration lives here.
Runtime env vars (GH_TOKEN, PR_NUMBER, etc.) are still read where needed.
Also handles loading persistent style guidelines from .code-to-docs/style.md
or a user-specified STYLE_CONFIG_PATH, and repo-level config from
.code-to-docs/config.json.
"""

import json
import os
import re

import openai
from openai import OpenAI

from security_utils import run_command_safe, sanitize_output


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
            print(
                f"Warning: Invalid MAX_CONTEXT_CHARS='{raw}', using default {_DEFAULT_MAX_CONTEXT_CHARS:,}"
            )
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
        print(
            f"Warning: No budget remaining for {label}, skipping diff entirely. "
            f"Consider increasing MAX_CONTEXT_CHARS or reducing PR size."
        )
        return "[... diff omitted: prompt content already exceeds context budget ...]"

    # Split into per-file sections
    parts = re.split(r"(?=\ndiff --git )", diff_text)

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
        result = diff_text[: max_chars - len(suffix)]
        pct = (max_chars) * 100 // len(diff_text)
        print(
            f"Warning: Truncated {label} from {len(diff_text):,} to {max_chars:,} chars ({pct}% retained, 0/{total_files} complete files)"
        )
        return result + suffix

    suffix = f"\n\n[... truncated: showing {included}/{total_files} changed files, kept {len(result):,} of {len(diff_text):,} chars ...]"
    pct = len(result) * 100 // len(diff_text)
    print(
        f"Warning: Truncated {label} from {len(diff_text):,} to ~{len(result):,} chars ({pct}% retained, {included}/{total_files} files)"
    )
    return result + suffix


# =============================================================================
# STYLE CONFIGURATION
# =============================================================================

_AUTO_DETECT_PATHS = [
    ".code-to-docs/style.md",
]

_ALLOWED_STYLE_EXTENSIONS = (".md",)


def load_style_config_from_branch():
    """Load style config from the base branch via git show.

    Reads the style config directly from origin/{base_branch} so the
    repo's current style rules always apply, regardless of when the
    PR branch was created.
    """
    try:
        base_branch = os.environ.get("DOCS_BASE_BRANCH") or "main"
        style_path = os.environ.get("STYLE_CONFIG_PATH", "")

        paths = [style_path] if style_path else _AUTO_DETECT_PATHS

        run_command_safe(["git", "fetch", "origin", base_branch], check=False)

        for path in paths:
            if not path or not path.endswith(_ALLOWED_STYLE_EXTENSIONS):
                if path:
                    print(f"Warning: Style config must be a .md file, got '{path}', skipping")
                continue
            if ".." in path.split("/") or path.startswith("/"):
                print(f"Warning: Style config path rejected (traversal): '{path}', skipping")
                continue
            result = run_command_safe(
                ["git", "show", f"origin/{base_branch}:{path}"],
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                print(
                    f"Loaded style config from {base_branch}:{path}"
                    f" ({len(result.stdout.strip()):,} chars)"
                )
                return result.stdout.strip()
            if result.returncode == 0 and not result.stdout.strip():
                print(f"Warning: Style config '{path}' on {base_branch} is empty, skipping")
            elif style_path:
                print(f"Warning: Style config '{path}' not found on {base_branch}")
    except Exception as e:
        print(f"Warning: Could not load style config from base branch: {sanitize_output(str(e))}")

    return ""


# =============================================================================
# REPO-LEVEL CONFIGURATION (.code-to-docs/config.json)
# =============================================================================

_REPO_CONFIG_PATH = ".code-to-docs/config.json"

_repo_config_cache = None


def load_repo_config():
    """Load repo-level config from the base branch via git show.

    Reads .code-to-docs/config.json from origin/{base_branch} so the
    repo's current settings always apply, regardless of the PR branch.

    Returns a dict (empty if no config file exists).
    """
    global _repo_config_cache
    if _repo_config_cache is not None:
        return _repo_config_cache

    _repo_config_cache = {}

    try:
        base_branch = os.environ.get("DOCS_BASE_BRANCH") or "main"
        path = _REPO_CONFIG_PATH

        if ".." in path.split("/") or path.startswith("/"):
            return _repo_config_cache

        run_command_safe(["git", "fetch", "origin", base_branch], check=False)

        result = run_command_safe(
            ["git", "show", f"origin/{base_branch}:{path}"],
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            _repo_config_cache = json.loads(result.stdout.strip())
            print(
                f"Loaded repo config from {base_branch}:{path}"
                f" ({len(result.stdout.strip()):,} chars)"
            )
        elif result.returncode == 0 and not result.stdout.strip():
            print(f"Warning: Repo config '{path}' on {base_branch} is empty, skipping")
    except json.JSONDecodeError as e:
        print(f"Warning: Invalid JSON in {_REPO_CONFIG_PATH}: {e}")
    except Exception as e:
        print(f"Warning: Could not load repo config: {sanitize_output(str(e))}")

    return _repo_config_cache


def get_pr_title_prefix():
    """Get the PR title prefix from repo config (e.g., ':book: ').

    Returns the prefix with a trailing space if set, empty string otherwise.
    """
    config = load_repo_config()
    raw = config.get("pr-title-prefix", "")
    if not isinstance(raw, str):
        return ""
    prefix = raw.strip()
    return f"{prefix} " if prefix else ""


# =============================================================================
# IGNORE LIST
# =============================================================================

_IGNORE_FILE = ".code-to-docs/ignore"


def load_ignore_patterns():
    """Load gitignore-style exclusion patterns from the base branch.

    Returns a list of pattern strings. Empty list if the file is absent.
    """
    base_branch = os.environ.get("DOCS_BASE_BRANCH") or "main"
    try:
        result = run_command_safe(
            ["git", "show", f"origin/{base_branch}:{_IGNORE_FILE}"],
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        lines = result.stdout.strip().splitlines()
        patterns = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
        if patterns:
            print(f"Loaded {len(patterns)} ignore pattern(s) from {base_branch}:{_IGNORE_FILE}")
        return patterns
    except Exception as e:
        print(f"Warning: Could not load ignore patterns: {sanitize_output(str(e))}")
        return []


def is_path_ignored(path, patterns):
    """Check whether a file path matches any gitignore-style pattern."""
    if not patterns:
        return False
    from fnmatch import fnmatch

    path_str = str(path)
    for pattern in patterns:
        if fnmatch(path_str, pattern) or fnmatch(path_str, f"**/{pattern}"):
            return True
        if "/" in pattern and fnmatch(path_str, pattern):
            return True
    return False


def check_context_error(e):
    """
    If e is a context-window error, print actionable guidance.

    Returns True if a context error was detected, False otherwise.
    Does NOT re-raise — the caller decides whether to raise or continue.
    """
    if isinstance(e, openai.BadRequestError):
        msg = str(e).lower()
        if any(
            kw in msg
            for kw in [
                "context length",
                "maximum context",
                "number of tokens",
                "token limit",
            ]
        ):
            print(
                "Error: Prompt exceeded model context window. "
                "Set MAX_CONTEXT_CHARS to a lower value "
                "(e.g. 32000 for an 8K-token model)."
            )
            return True
    return False
