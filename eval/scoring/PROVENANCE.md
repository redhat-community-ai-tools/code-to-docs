# Scoring engine — provenance (copied from agent-eval-harness)

This directory is a **verbatim copy** of the scoring half of
[`opendatahub-io/agent-eval-harness`](https://github.com/opendatahub-io/agent-eval-harness).
It reuses that project's judge dispatch, scoring, and regression machinery. The runner that
drives it (`eval/execute.py`) and the config it reads (`eval/eval.yaml`) are added in the
follow-up engine PR — in this PR, `eval/scoring/` stands alone as the copied grader.

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

## Verifying the copy

Confirm the files match upstream at the pinned commit (no output = identical):

```
git clone https://github.com/opendatahub-io/agent-eval-harness /tmp/aeh
git -C /tmp/aeh checkout 6fbba9f
diff /tmp/aeh/skills/eval-run/scripts/score.py eval/scoring/score.py
for f in __init__ _bootstrap config events; do diff /tmp/aeh/agent_eval/$f.py eval/scoring/agent_eval/$f.py; done
```

## What is deliberately NOT copied

Only the deterministic scoring path is needed here. These upstream surfaces are unused and
not copied; the code paths that reach them are lazy-imported behind branches we never hit
(only `check` + `module` judges are used):

- `agent_eval/judges/` (the builtin-judge registry) — only reached for `builtin:` judges.
- `agent_eval/hooks.py` — lifecycle hooks; only reached when `hooks.before_scoring` (etc.)
  is set in `eval.yaml`, so `before_scoring` hooks are not supported here.
- `agent_eval/mlflow/`, `agent_eval/agent/`, the LLM-judge / pairwise paths — `jinja2`,
  `anthropic`, `mlflow` deps, all lazy.

Core runtime deps of what we copied: **stdlib + `pyyaml`** only.

## How it's used (once the engine PR lands)

- **Direct CLI:** `python eval/scoring/score.py judges --run-id <id> --config eval/eval.yaml`
  and `... regression ...`. Run as a script, `eval/scoring/` is auto on `sys.path[0]`, so
  `import agent_eval` resolves with no `PYTHONPATH` needed.
- **From the runner/aggregator:** `sys.path.insert(0, "eval/scoring")` then
  `from score import load_judges, score_cases, detect_regressions` — the N-sample aggregation
  reuses these directly.

## Re-syncing from upstream

Because every file is byte-identical, re-sync is a re-copy:

```
cp <agent-eval-harness>/skills/eval-run/scripts/score.py   eval/scoring/score.py
cp <agent-eval-harness>/agent_eval/{__init__,_bootstrap,config,events}.py  eval/scoring/agent_eval/
```

After re-syncing, re-check the import surface of `score.py` (top-level imports + the lazy
`from agent_eval.* import ...` lines) in case upstream added a new internal dependency that
would need to be copied too. Update the commit SHA above.
