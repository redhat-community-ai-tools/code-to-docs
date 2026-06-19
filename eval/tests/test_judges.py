"""Unit tests for the eval judges — pure logic, no model key needed.

Run from the repo root: `pytest eval/tests/test_judges.py` (pytest discovers it automatically).
"""

import json
import sys
from pathlib import Path

# Put eval/ on the path so `judges.*` resolves the same way the scorer loads them.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from judges.selection import normalize_path, judge_selection  # noqa: E402
from judges.meta import judge_annotations_present              # noqa: E402


# --- normalize_path (D-13) -------------------------------------------------

def test_normalize_strips_src_prefix():
    # The LLM parrots a fictional `src/` prefix back from previews (2/5 runs in the REPL).
    assert normalize_path("src/cli/reference.md", "src") == "cli/reference.md"

def test_normalize_strips_dot_slash_and_docs_subfolder_and_lowercases():
    assert normalize_path("./Docs/x.md", "docs") == "x.md"

def test_normalize_passthrough():
    assert normalize_path("api/reference.md") == "api/reference.md"

def test_normalize_lowercases():
    assert normalize_path("CLI/Reference.md") == "cli/reference.md"

def test_normalize_backslashes_to_forward():
    assert normalize_path("config\\reference.md") == "config/reference.md"


# --- judge_selection (D-06) ------------------------------------------------

def _rec(selected, expected, which="index"):
    return {
        "files": {f"selection_{which}/selected_files.json": json.dumps(selected)},
        "annotations": {"expected_files": expected},
    }

def test_selection_perfect_match():
    val, _ = judge_selection(outputs=_rec(["cli/reference.md"], ["cli/reference.md"]))
    assert val == 1.0

def test_selection_empty_expected_empty_selected_is_perfect():
    # Negative case done right: expected nothing, selected nothing.
    val, _ = judge_selection(outputs=_rec([], []))
    assert val == 1.0

def test_selection_empty_expected_but_selected_something_is_zero():
    val, _ = judge_selection(outputs=_rec(["x.md"], []))
    assert val == 0.0

def test_selection_partial_recall_f1():
    # Caught 1 of 2 -> P=1.0 R=0.5 -> F1 = 2/3.
    val, _ = judge_selection(outputs=_rec(["a.md"], ["a.md", "b.md"]))
    assert abs(val - (2 / 3)) < 1e-6

def test_selection_normalizes_before_comparing():
    # Hallucinated src/ prefix should still match after normalization.
    val, _ = judge_selection(outputs=_rec(["src/cli/reference.md"], ["cli/reference.md"]))
    assert val == 1.0

def test_selection_missing_output_scores_zero():
    val, rationale = judge_selection(outputs={"files": {}, "annotations": {"expected_files": ["x.md"]}})
    assert val == 0.0
    assert "missing" in rationale.lower()


# --- judge_annotations_present (the vacuous-pass guard) --------------------

def test_guard_passes_with_both_dimensions():
    val, _ = judge_annotations_present(outputs={
        "annotations": {"expected_files": [], "content": {"x.md": {"expect_no_update": True}}}
    })
    assert val is True

def test_guard_fails_when_annotations_absent():
    val, _ = judge_annotations_present(outputs={"annotations": {}})
    assert val is False

def test_guard_fails_without_expected_files_key():
    val, _ = judge_annotations_present(outputs={"annotations": {"content": {"x.md": {}}}})
    assert val is False

def test_guard_fails_without_content_block():
    # The hole the review flagged: expected_files present, content omitted -> would
    # let content_assertions pass vacuously.
    val, _ = judge_annotations_present(outputs={"annotations": {"expected_files": ["x.md"]}})
    assert val is False

def test_guard_fails_with_empty_content_block():
    val, _ = judge_annotations_present(outputs={"annotations": {"expected_files": [], "content": {}}})
    assert val is False
