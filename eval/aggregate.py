#!/usr/bin/env python3
"""eval/aggregate.py — the N-sample layer (DECISIONS D-08, D-21).

execute.py --samples N writes run dirs <base>-s01..sNN, each holding every case. This
scores each sample (reusing the VENDORED scorer's judge dispatch + record loading — no
reimplementation, D-21) and reports per-(case, judge) PASS-RATES across the N samples,
which is what tells systematic behavior apart from LLM noise. Flaked samples (D-22) are
excluded per judge-stage.

Why this and not score.py directly: score.py aggregates across CASES within one run (a
blended mean). We need per-CASE rates across SAMPLES.

Usage (from eval/):
    $env:PYTHONPATH = "."
    python aggregate.py --run-id <base> --samples N
"""

import argparse
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR / "scoring"))   # agent_eval + score
sys.path.insert(0, str(EVAL_DIR))                # judges.*

from agent_eval.config import EvalConfig   # noqa: E402
import score                                # noqa: E402

# Which flake key (written by execute.py meta/flakes.json) invalidates which judge.
_JUDGE_STAGE = {
    "selection_f1_index": "selection_index",
    "selection_f1_scan": "selection_scan",
    "content_assertions": "content:",   # prefix match (any content:<doc>)
    "no_fence_wrapper": "content:",
}


def _passes(value, judge_name, thresholds):
    """Per-sample pass for a judge: numeric -> >= min_mean; bool/min_pass_rate -> truthy."""
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    th = thresholds.get(judge_name, {})
    if "min_mean" in th:
        return value >= th["min_mean"]
    return bool(value)


def _flaked(flake_keys, judge_name):
    stage = _JUDGE_STAGE.get(judge_name, "")
    if not stage:
        return False
    if stage.endswith(":"):
        return any(k.startswith(stage) for k in flake_keys)
    return stage in flake_keys


def _load_meta(case_dir):
    """Read per-case meta.json (flakes D-22 + filter_drops D-15) directly from the runs tree.

    Read from disk, not record["files"] — `meta/` is intentionally not an eval.yaml output, so
    the scorer never loads it into the judge record.
    """
    p = case_dir / "meta" / "meta.json"
    if not p.is_file():
        return set(), []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set((data.get("flakes") or {}).keys()), list(data.get("filter_drops") or [])
    except Exception:
        return set(), []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True, help="base run id (sample dirs: <base>-sNN)")
    ap.add_argument("--samples", type=int, required=True)
    ap.add_argument("--config", default=str(EVAL_DIR / "eval.yaml"))
    ap.add_argument("--runs-dir", default=str(EVAL_DIR / "runs"))
    args = ap.parse_args()

    cfg = EvalConfig.from_yaml(args.config)
    judges = score.load_judges(cfg, project_root=EVAL_DIR)
    judge_names = [j[0] for j in judges]
    thresholds = cfg.thresholds
    runs_root = Path(args.runs_dir) / "code-to-docs"

    # results[case][judge] = list of per-sample pass (True/False) ; None entries excluded
    results = {}
    flake_total = 0
    drops_total = 0
    samples_seen = 0

    for s in range(1, args.samples + 1):
        rid = args.run_id if args.samples == 1 else f"{args.run_id}-s{s:02d}"
        cases_dir = runs_root / rid / "cases"
        if not cases_dir.is_dir():
            print(f"  warn: missing {cases_dir}", file=sys.stderr)
            continue
        samples_seen += 1
        for case_dir in sorted(d for d in cases_dir.iterdir() if d.is_dir()):
            cid = case_dir.name
            rec = score.load_case_record(case_dir, cfg, run_id=rid)
            flake_keys, drops = _load_meta(case_dir)
            flake_total += len(flake_keys)
            drops_total += len(drops)
            for name, scorer, _cond, _jtype, _n in judges:
                if _flaked(flake_keys, name):
                    continue  # exclude flaked sample for this judge (D-22)
                val, _rat = score._normalize_result(scorer(outputs=rec))
                p = _passes(val, name, thresholds)
                if p is None:
                    continue
                results.setdefault(cid, {}).setdefault(name, []).append(p)

    # ---- report ----
    print(f"\nAggregate over {samples_seen} sample(s)  "
          f"(flakes excluded: {flake_total}, discovery filter-drops: {drops_total})\n")
    overall = {n: [] for n in judge_names}
    for cid in sorted(results):
        print(f"case {cid}:")
        for name in judge_names:
            passes = results[cid].get(name, [])
            if not passes:
                print(f"    {name:22s} -- (no scored samples)")
                continue
            rate = sum(passes) / len(passes)
            overall[name].append(rate)
            mark = "OK " if rate >= 0.999 else ("!! " if rate == 0 else " ~ ")
            print(f"    {name:22s} {mark} {sum(passes)}/{len(passes)} pass  ({rate:.0%})")
        print()

    print("OVERALL (mean per-case pass-rate per judge):")
    for name in judge_names:
        rates = overall[name]
        if not rates:
            print(f"    {name:22s} -- (no data)")
            continue
        m = sum(rates) / len(rates)
        print(f"    {name:22s} {m:.0%}   across {len(rates)} case(s)")


if __name__ == "__main__":
    main()
