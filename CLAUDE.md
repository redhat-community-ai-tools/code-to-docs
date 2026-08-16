# Code-to-Docs

AI-powered GitHub Action that analyzes code changes and generates documentation updates using any OpenAI-compatible LLM.

## Architecture

- **Entry point**: `entrypoint.sh` → `src/suggest_docs.py`
- **Runs as**: Docker-based GitHub Action triggered by `issue_comment` events
- **Commands**: `[review-docs]`, `[update-docs]`, `[review-feature] PROJ-123`

### Source modules (`src/`)

| Module | Purpose |
|--------|---------|
| `suggest_docs.py` | Main orchestrator — command detection, file discovery, generation, PR/comment posting |
| `config.py` | Environment configuration, LLM client setup, style config loading |
| `discovery.py` | File discovery — index-based optimized path and full-scan fallback |
| `generation.py` | LLM content generation, post-generation validation, file reading/writing, summary caching |
| `doc_index.py` | Semantic index system — build, cache, fetch, commit indexes |
| `comments.py` | PR comment building, parsing previous reviews, posting |
| `github_ops.py` | Git operations, docs environment setup, pushing/creating PRs |
| `jira_integration.py` | Jira/Confluence/Google Docs integration for `[review-feature]` |
| `security_utils.py` | Credential sanitization, safe subprocess execution, path validation |
| `utils.py` | Retry logic, backoff calculations |

## Development

```bash
# Run tests
uv run pytest tests/ -x -q

# Run linter
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/
```

### Key conventions

- **Python 3.12+**, managed with `uv`
- **Testing**: pytest, tests mirror source modules (`test_<module>.py`)
- **Linting**: ruff (line length 100, see `pyproject.toml` for rules)
- **Coverage**: minimum 60%, run with `uv run pytest --cov=src --cov-report=term-missing`

## Authentication

The action receives a single token via the `github-token` input, mapped to `GH_TOKEN` env var internally. All git and `gh` CLI operations use this token.

- **Same-repo mode** (`DOCS_SUBFOLDER` set): `GITHUB_TOKEN` is sufficient
- **Separate docs repo** (`DOCS_REPO_URL` points elsewhere): requires a PAT with `repo` scope
- **Fork PRs**: cannot push to fork branches — shows suggested changes as diffs in a PR comment

Tokens are never logged or interpolated into strings. `security_utils.sanitize_output()` scrubs `GH_TOKEN`, `MODEL_API_KEY`, `JIRA_API_TOKEN`, and `GOOGLE_SA_KEY` from all output.

## Environment variables

Set by the GitHub Action via `action.yml`:

| Variable | Required | Description |
|----------|----------|-------------|
| `MODEL_API_BASE` | Yes | OpenAI-compatible API base URL |
| `MODEL_API_KEY` | No | API key for the model endpoint |
| `MODEL_NAME` | Yes | Model name for inference |
| `DOCS_REPO_URL` | Yes | Documentation repository URL |
| `GH_TOKEN` | Yes | GitHub token for git/gh operations |
| `DOCS_SUBFOLDER` | No | Docs subfolder path for same-repo mode |
| `DOCS_BASE_BRANCH` | No | Base branch for docs PRs (default: `main`) |
| `PR_NUMBER` | No | PR number being analyzed |
| `PR_BASE` | No | Base ref for diff (default: `origin/main`) |
| `PR_HEAD_SHA` | No | Head SHA for the PR |
| `COMMENT_BODY` | No | Comment text containing the command |
| `STYLE_CONFIG_PATH` | No | Path to style guidelines file |
| `DRY_RUN` | No | If `true`, simulate changes without writing or pushing |
| `JIRA_URL` | No | Jira instance URL (for `[review-feature]`) |
| `JIRA_USERNAME` | No | Jira username/email (for `[review-feature]`) |
| `JIRA_API_TOKEN` | No | Jira API token (for `[review-feature]`) |
| `GOOGLE_SA_KEY` | No | Google service account JSON key for fetching Google Docs |
| `MAX_CONTEXT_CHARS` | No | Max chars for LLM prompt content (default: 400000) |

## Command flows

All three commands share a common pipeline: get diff → discover relevant doc files → generate updates via LLM. They differ in what happens with the results.

### `[review-docs]` flow

Posts a review comment on the PR with checkboxes for each file the AI identified as needing updates. Does NOT modify any files or create PRs. The comment includes per-file descriptions and suggested changes so the reviewer can accept or reject each one.

If combined with `[review-feature]`, adds a Spec vs Code Analysis section comparing Jira requirements against the PR changes.

### `[update-docs]` flow

Generates doc updates and pushes them. If a previous `[review-docs]` comment exists, only updates files that were checked (accepted) in that review.

Push target depends on PR state and origin:

1. **Merged PR**: creates a `docs/update-from-pr-{N}` branch from main, commits docs, creates a PR via `_push_docs_pr_for_merged()`
2. **Fork PR (open)**: skips push, posts diffs as suggestions in a PR comment with guidance to re-run after merge
3. **Same-repo PR (open)**: pushes directly to the PR branch

### `[review-feature] PROJ-123` flow

Runs the `[review-docs]` flow plus fetches the Jira ticket and its linked spec docs (Confluence, Google Docs). Compares requirements from the spec against the actual code changes and posts a coverage analysis showing what's covered, missing, and unplanned.

Requires `JIRA_URL`, `JIRA_USERNAME`, and `JIRA_API_TOKEN` secrets. Optionally uses `GOOGLE_SA_KEY` for fetching Google Docs.

## Index system (in `doc_index.py`)

Semantic indexes speed up doc file discovery by caching LLM-generated summaries of documentation folders. Key functions:

- `fetch_indexes_from_main()` — fetches cached indexes from the base branch
- `folder_needs_reindex()` — compares SHA256 doc hashes against `origin/main` (via `get_folder_doc_hashes_from_ref`) to detect changes, with fallback to disk hashes
- `build_all_indexes()` / `update_indexes_if_needed()` — regenerate via LLM only for changed folders
- `commit_indexes_to_repo()` — pushes indexes to a persistent `code-to-docs/update-indexes` branch and creates/updates a PR

Indexes are stored in `.doc-index/` within the docs root. The manifest (`manifest.json`) tracks per-folder doc hashes to avoid unnecessary LLM regeneration.
