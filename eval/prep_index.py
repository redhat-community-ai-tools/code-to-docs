#!/usr/bin/env python3
"""eval/prep_index.py — generate a case's frozen .doc-index/ (one-time, needs model key).

The index path of the pipeline consumes a pre-built `.doc-index/` (DECISIONS D-02 option c).
This script builds it ONCE per case via the real `build_all_indexes()` and writes it into the
case fixture, so the eval run consumes a frozen index instead of rebuilding it every time.

CRITICAL (D-19): the index manifest hashes the docs bytes. execute.py materializes the SAME
`docs/` into its workspace, so the hashes match and `update_indexes_if_needed` is a no-op. If
the docs are edited after prepping, RE-RUN this script or the pipeline silently rebuilds the
index (extra LLM cost, non-frozen behavior). Keep docs ASCII / BOM-free (D-14).

Usage (from repo root, model env configured):
    python eval/prep_index.py --case eval/dataset/cases/001_add_cli_flag
    python eval/prep_index.py --all          # every case under eval/dataset/cases
"""

import argparse
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import doc_index  # noqa: E402


@contextmanager
def _chdir(path):
    prev = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(prev)


def prep_case(case_dir):
    """Build .doc-index/ from case_dir/docs and write it into case_dir/.doc-index."""
    docs = case_dir / "docs"
    if not docs.is_dir():
        print(f"  skip {case_dir.name}: no docs/")
        return False

    ws = Path(tempfile.mkdtemp(prefix="c2d_prep_"))
    try:
        # Materialize docs at the workspace root exactly as execute.py will (byte-for-byte),
        # so the manifest hashes match at run time.
        shutil.copytree(docs, ws, dirs_exist_ok=True)
        with _chdir(ws):
            result = doc_index.build_all_indexes(force=True)  # LLM: one call per doc folder
        built = ws / ".doc-index"
        if not built.is_dir():
            print(f"  {case_dir.name}: build produced no .doc-index "
                  f"(no nested doc folders?) status={result.get('status')}")
            return False
        dst = case_dir / ".doc-index"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(built, dst)
        folders = result.get("folders_built") or result.get("folders") or []
        print(f"  {case_dir.name}: index built ({result.get('status')}) folders={folders}")
        return True
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Generate frozen .doc-index/ for eval case(s).")
    ap.add_argument("--case", help="path to one case dir")
    ap.add_argument("--all", action="store_true", help="prep every case under eval/dataset/cases")
    args = ap.parse_args()

    if not os.environ.get("MODEL_API_BASE"):
        sys.exit("MODEL_API_BASE not set — index building calls the model. "
                 "Set MODEL_API_BASE / MODEL_API_KEY / MODEL_NAME.")

    if args.all:
        root = EVAL_DIR / "dataset" / "cases"
        cases = sorted(d for d in root.iterdir() if d.is_dir())
    elif args.case:
        cases = [Path(args.case)]
    else:
        sys.exit("pass --case <dir> or --all")

    for c in cases:
        prep_case(c)


if __name__ == "__main__":
    main()
