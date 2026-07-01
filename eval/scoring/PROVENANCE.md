# Vendored scoring engine — provenance

This directory is a **verbatim copy** of the scoring half of
[`opendatahub-io/agent-eval-harness`](https://github.com/opendatahub-io/agent-eval-harness).
We reuse its judge dispatch, scoring, and regression machinery; we wrote our own runner
(`eval/execute.py`) instead of its Claude Code execution layer, which doesn't apply here.

## Source

- **Repo:** https://github.com/opendatahub-io/agent-eval-harness
- **Version:** `1.14.0`
- **Commit:** `6fbba9f65c27568433187d735c470fab5acef85a`
- **License:** Apache-2.0 (see the upstream `LICENSE`)

## Files and their upstream paths

| Here | Upstream path | Modifications |
|---|---|---|
| `score.py` | `skills/eval-run/scripts/score.py` | **none — byte-identical** |
| `agent_eval/__init__.py` | `agent_eval/__init__.py` | none |
| `agent_eval/_bootstrap.py` | `agent_eval/_bootstrap.py` | none |
| `agent_eval/config.py` | `agent_eval/config.py` | none |
| `agent_eval/events.py` | `agent_eval/events.py` | none |

`score.py` lives at the repo root upstream but imports `agent_eval` as an installed package.
We vendor `agent_eval/` as a subpackage beside `score.py`, so `score.py`'s imports
(`from agent_eval.config import ...`) resolve unchanged once `eval/scoring/` is on `sys.path`.
**No import rewiring was needed** — hence "byte-identical."

## What is deliberately NOT copied

Only the deterministic scoring path is needed for v1. These upstream surfaces are unused and
not copied; the code paths that reach them are lazy-imported behind branches we never hit
(`check` + `module` judges only — see `.claude-workspace/DECISIONS.md` D-01, D-05):

- `agent_eval/judges/` (the builtin-judge registry) — only reached for `builtin:` judges.
- `agent_eval/mlflow/`, `agent_eval/agent/`, the LLM-judge / pairwise paths — `jinja2`,
  `anthropic`, `mlflow` deps, all lazy.

Core runtime deps of what we copied: **stdlib + `pyyaml`** only.

## How it's used

- **Direct CLI:** `python eval/scoring/score.py judges --run-id <id> --config eval/eval.yaml`
  and `... regression ...`. Run as a script, `eval/scoring/` is auto on `sys.path[0]`, so
  `import agent_eval` resolves with no `PYTHONPATH` needed.
- **From our runner/aggregator:** `sys.path.insert(0, "eval/scoring")` then
  `from score import load_judges, score_cases, detect_regressions` (the N-sample aggregation
  reuses these directly — see DECISIONS.md D-21).

## Re-syncing from upstream

Because every file is byte-identical, re-sync is a re-copy:

```
cp <agent-eval-harness>/skills/eval-run/scripts/score.py   eval/scoring/score.py
cp <agent-eval-harness>/agent_eval/{__init__,_bootstrap,config,events}.py  eval/scoring/agent_eval/
```

After re-syncing, re-check the import surface of `score.py` (top-level imports + the lazy
`from agent_eval.* import ...` lines) in case upstream added a new internal dependency that
would need to be copied too. Update the commit SHA above.
