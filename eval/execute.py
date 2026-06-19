#!/usr/bin/env python3
"""eval/execute.py — the code-to-docs eval runner.

Per case: materialize a temp workspace (docs at root + frozen .doc-index/), invoke
the REAL code-to-docs pipeline, and write outputs in the scorer's runs/ layout.

No git repo is built: the diff is fed directly as a string and the two index
git-functions are stubbed. See .claude-workspace/DECISIONS.md — D-18 (feed diff
directly / no git), D-19 (workspace lifecycle), D-03 (monkeypatch surface),
D-20 (content-gen on annotated/gold files), D-21 (N samples -> N run dirs),
D-22 (API error -> flake).

Usage (from repo root, model env configured):
    $env:MODEL_API_BASE="https://api.openai.com/v1"
    $env:MODEL_API_KEY="sk-..."
    $env:MODEL_NAME="gpt-4o-mini"
    python eval/execute.py --run-id <id> --samples 1
Outputs: eval/runs/code-to-docs/<run-id>[-sNN]/cases/<case>/{selection_index,selection_scan,content}/...
"""

import argparse
import ast
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
SRC = REPO_ROOT / "src"

# The code-to-docs pipeline uses flat imports (`from config import ...`,
# `import discovery`). Put src/ on the path so they resolve. These imports persist
# across the per-case chdir below — only the pipeline's *file* operations (which read
# cwd) are affected by chdir, not module resolution.
sys.path.insert(0, str(SRC))

import discovery   # noqa: E402
import generation  # noqa: E402

# --- D-03: stub the two git-touching index functions IN DISCOVERY'S NAMESPACE. ---
# discovery did `from doc_index import (fetch_indexes_from_main, commit_indexes_to_repo)`
# (src/discovery.py:28,33), rebinding those names into the `discovery` module. The call
# sites in find_relevant_files_optimized resolve through `discovery`, so we MUST patch
# here, not on doc_index — patching doc_index would silently no-op. Do not "fix" this.
discovery.fetch_indexes_from_main = lambda *a, **k: False
discovery.commit_indexes_to_repo = lambda *a, **k: None

# openai exception base, for flake detection (D-22). NOTE: the pipeline swallows most
# API errors internally into empty results (the 429->[] problem D-22 documents), so this
# only catches errors that *propagate* (e.g. context-window BadRequest, client init).
# The swallowed-empty case is a known v1 limitation — documented in D-22.
try:
    import openai
    _API_ERRORS = (openai.OpenAIError,)
except Exception:  # pragma: no cover
    _API_ERRORS = ()


def _strip_bom(s):
    """Drop a leading BOM (D-14) so it never reaches the LLM or a byte-sensitive judge."""
    return s[1:] if s and s.startswith("﻿") else s


class _Tee:
    """Mirror writes to the real stream while buffering them for later parsing.

    stdout is process-global, so discovery's worker-thread prints land here too; the lock
    keeps concurrent writes from interleaving mid-line.
    """

    def __init__(self, real):
        self._real = real
        self._buf = io.StringIO()
        self._lock = threading.Lock()

    def write(self, s):
        with self._lock:
            self._real.write(s)
            self._buf.write(s)
        return len(s)

    def flush(self):
        self._real.flush()

    def getvalue(self):
        with self._lock:
            return self._buf.getvalue()


@contextmanager
def _capture_stdout():
    """Tee stdout: the user still sees discovery's progress; we keep a copy to parse."""
    real = sys.stdout
    tee = _Tee(real)
    sys.stdout = tee
    try:
        yield tee
    finally:
        sys.stdout = real


_DROP_RE = re.compile(r"Skipping non-documentation files:\s*(\[[^\]]*\])")


def _parse_filter_drops(text):
    """Parse discovery's 'Skipping non-documentation files: [...]' lines (D-15).

    These are files the LLM *tried* to select but the .md/.adoc/.rst suffix filter dropped —
    usually code files it hallucinated as docs. Silent in production; we surface them so a
    rising drop rate becomes a real regression signal. (D-17 fence_rate_discovery is NOT
    capturable this way — it happens inside the LLM response discovery strips internally —
    so it stays deferred to a future src hook.)
    """
    drops = []
    for m in _DROP_RE.finditer(text):
        try:
            val = ast.literal_eval(m.group(1))
            if isinstance(val, list):
                drops.extend(str(x) for x in val)
        except (ValueError, SyntaxError):
            pass
    return drops


@contextmanager
def _workspace(case_dir):
    """Temp dir: docs at ROOT (get_docs_root() -> cwd) + frozen .doc-index/. chdir in.

    SEQUENTIAL-ONLY: this uses process-global os.chdir(), and discovery's internal worker
    threads resolve file paths against cwd — so cases must run one at a time. The runner is
    single-threaded across cases by design (main() loops sequentially). Do NOT parallelize
    run_case without replacing this global-chdir with per-call cwd handling.
    """
    ws = Path(tempfile.mkdtemp(prefix="c2d_eval_"))
    prev = Path.cwd()
    try:
        shutil.copytree(case_dir / "docs", ws, dirs_exist_ok=True)
        frozen = case_dir / ".doc-index"
        if frozen.is_dir():
            shutil.copytree(frozen, ws / ".doc-index")
        os.chdir(ws)
        yield ws
    finally:
        os.chdir(prev)
        shutil.rmtree(ws, ignore_errors=True)


def _call(fn, *args, **kwargs):
    """Invoke a pipeline call; return (result, flake_reason|None)."""
    try:
        return fn(*args, **kwargs), None
    except _API_ERRORS as e:
        return None, f"{type(e).__name__}: {e}"


def _content_filename(doc):
    """Map a doc path to its content output filename (slashes -> '__')."""
    return doc.replace("/", "__")


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else "", encoding="utf-8")


def run_case(case_dir, run_root):
    """Run both discovery paths + content-gen for one case; write the runs/ layout."""
    case_id = case_dir.name
    diff = _strip_bom((case_dir / "diff.patch").read_text(encoding="utf-8"))
    ann = yaml.safe_load((case_dir / "annotations.yaml").read_text(encoding="utf-8")) or {}
    content_targets = list((ann.get("content") or {}).keys())  # gold files (D-20)

    flakes = {}
    idx_sel = []
    scan_sel = []
    contents = {}
    filter_drops = []

    with _workspace(case_dir):
        # --- discovery: both paths, stdout captured to surface filtered drops (D-15) ---
        with _capture_stdout() as cap:
            # index discovery path (frozen index present, git stubbed)
            idx_sel, fl = _call(discovery.find_relevant_files_optimized, diff)
            if fl:
                flakes["selection_index"] = fl
                idx_sel = []
            elif idx_sel is None:
                idx_sel = []  # None == pipeline's "fall back to full scan" signal (D-19)

            # full-scan path (independent of the index path)
            previews, fl = _call(discovery.get_file_content_or_summaries)
            if fl:
                flakes["selection_scan"] = fl
                scan_sel = []
            else:
                scan_sel, fl = _call(discovery.ask_ai_for_relevant_files, diff, previews)
                if fl:
                    flakes["selection_scan"] = fl
                    scan_sel = []
                elif scan_sel is None:
                    scan_sel = []
        filter_drops = _parse_filter_drops(cap.getvalue())

        # --- content generation on the annotated/gold files (D-20) ---
        for doc in content_targets:
            doc_path = Path(doc)
            current = _strip_bom(doc_path.read_text(encoding="utf-8")) if doc_path.is_file() else ""
            out, fl = _call(generation.ask_ai_for_updated_content, diff, doc, current)
            if fl:
                flakes[f"content:{doc}"] = fl
            else:
                contents[doc] = out

    # --- write the runs/ layout the scorer reads (eval.yaml `outputs`) ---
    case_out = run_root / "cases" / case_id
    _write_json(case_out / "selection_index" / "selected_files.json", idx_sel)
    _write_json(case_out / "selection_scan" / "selected_files.json", scan_sel)
    for doc, text in contents.items():
        _write_text(case_out / "content" / _content_filename(doc), text)
    if flakes or filter_drops:
        # per-case run metadata for the aggregator: flakes (D-22) + filtered drops (D-15)
        _write_json(case_out / "meta" / "meta.json",
                    {"flakes": flakes, "filter_drops": filter_drops})

    return case_id, flakes, filter_drops


def main():
    ap = argparse.ArgumentParser(description="Run the code-to-docs pipeline over eval cases.")
    ap.add_argument("--cases", default=str(EVAL_DIR / "dataset" / "cases"),
                    help="dataset cases dir (default: eval/dataset/cases)")
    ap.add_argument("--run-id", required=True, help="base run id; samples append -sNN")
    ap.add_argument("--samples", type=int, default=1,
                    help="N pipeline runs per case -> N run dirs (D-21). PR=3, daily=10 (D-08)")
    ap.add_argument("--runs-dir", default=str(EVAL_DIR / "runs"),
                    help="runs root (default: eval/runs)")
    args = ap.parse_args()

    if not os.environ.get("MODEL_API_BASE"):
        sys.exit("MODEL_API_BASE not set — the pipeline needs an OpenAI-compatible endpoint "
                 "(e.g. https://api.openai.com/v1). Also set MODEL_API_KEY and MODEL_NAME.")

    # A runnable case needs a diff, a docs tree, and an answer key. This also skips the
    # throwaway _smoke_* fixtures (annotations only, no diff/docs).
    cases = sorted(d for d in Path(args.cases).iterdir()
                   if d.is_dir()
                   and (d / "annotations.yaml").is_file()
                   and (d / "diff.patch").is_file()
                   and (d / "docs").is_dir())
    if not cases:
        sys.exit(f"No runnable cases (diff.patch + docs/ + annotations.yaml) under {args.cases}")

    runs_dir = Path(args.runs_dir)
    total_flakes = 0
    for s in range(1, args.samples + 1):
        rid = args.run_id if args.samples == 1 else f"{args.run_id}-s{s:02d}"
        run_root = runs_dir / "code-to-docs" / rid
        print(f"=== sample {s}/{args.samples}  ->  {run_root}")
        for case_dir in cases:
            cid, flakes, drops = run_case(case_dir, run_root)
            total_flakes += len(flakes)
            tags = []
            if flakes:
                tags.append(f"FLAKES: {list(flakes)}")
            if drops:
                tags.append(f"filtered-drops: {drops}")
            print(f"    {cid}" + ("   " + "  ".join(tags) if tags else ""))
    print(f"done. cases={len(cases)} samples={args.samples} flakes={total_flakes}")


if __name__ == "__main__":
    main()
