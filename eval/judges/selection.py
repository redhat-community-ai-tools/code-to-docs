"""Selection-stage judge — precision / recall / F1 vs the expected file set.

Loaded by the vendored scorer as a `module`/`function` judge (see eval.yaml).
The scorer calls `judge_selection(outputs=record, which="index"|"scan")` and reads
the returned `(f1, rationale)` pair. Numeric value → aggregated by mean; gated by
`thresholds.<judge>.min_mean`.

Design refs: DECISIONS.md D-04 (two paths scored separately), D-06 (P/R/F1 + the
empty-expected rule), D-13 (path normalization against the LLM's hallucinated prefix).

`outputs` (the scorer's per-case record) gives us:
  - outputs["files"]["selection_<which>/selected_files.json"]  → the pipeline's pick
  - outputs["annotations"]["expected_files"]                   → the gold set
"""

import json


def normalize_path(path, docs_subfolder=""):
    """Canonicalize a doc path before set comparison (D-13).

    Order: strip whitespace → backslash to slash → drop leading "./" → lowercase →
    drop a leading "src/" segment → drop a leading "<docs_subfolder>/" segment.

    The "src/" strip defends against the empirically-observed LLM behavior of
    parroting a fictional subfolder prefix back from the file previews (D-13:
    2/5 runs returned "src/cli/reference.md" for "cli/reference.md").
    """
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lower()
    if p.startswith("src/"):
        p = p[len("src/"):]
    if docs_subfolder:
        ds = docs_subfolder.strip("/").lower()
        if ds and p.startswith(ds + "/"):
            p = p[len(ds) + 1:]
    return p


def _load_selected(outputs, which):
    """Read selection_<which>/selected_files.json → list[str], or None if absent/bad."""
    files = outputs.get("files") or {}
    base = f"selection_{which}/selected_files.json"
    raw = files.get(base) or files.get(base.replace("/", "\\"))
    if raw is None:
        return None
    if isinstance(raw, dict):  # binary marker from the scorer
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    return [str(x) for x in data]


def judge_selection(outputs=None, which="index", **kwargs):
    """Precision/recall/F1 for one discovery path. Returns (f1: float, rationale: str)."""
    outputs = outputs or {}
    ann = outputs.get("annotations") or {}
    docs_subfolder = ann.get("docs_subfolder", "") or ""

    expected = {normalize_path(p, docs_subfolder) for p in (ann.get("expected_files") or [])}

    selected_raw = _load_selected(outputs, which)
    if selected_raw is None:
        return 0.0, f"[{which}] selection_{which}/selected_files.json missing or unparseable"
    selected = {normalize_path(p, docs_subfolder) for p in selected_raw}

    # Empty-expected (NO_UPDATE_NEEDED at the selection level): an empty selection
    # is a perfect score; any selection is a total miss (D-06).
    if not expected:
        if not selected:
            return 1.0, f"[{which}] correctly selected nothing (negative case)"
        return 0.0, f"[{which}] expected NONE, selected {sorted(selected)}"

    tp = len(selected & expected)
    precision = tp / len(selected) if selected else 0.0
    recall = tp / len(expected) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return f1, (
        f"[{which}] P={precision:.2f} R={recall:.2f} F1={f1:.2f} "
        f"selected={sorted(selected)} expected={sorted(expected)}"
    )
