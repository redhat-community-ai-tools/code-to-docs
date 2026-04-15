# Code-to-Docs - AI Documentation Assistant

[![GitHub Action](https://img.shields.io/badge/GitHub-Action-blue.svg)](https://github.com/marketplace/actions/upstream-docs-enhancer)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[**Try the interactive demo**](https://redhat-community-ai-tools.github.io/code-to-docs/demo/)

AI-powered GitHub Action that automatically analyzes code changes and updates documentation using any OpenAI-compatible LLM (vLLM on OpenShift AI, Gemini, OpenAI, etc.).

## Usage

Comment on any Pull Request:
- **`[review-docs]`** - Analyzes code changes, identifies relevant doc files, and posts a review comment with checkboxes to accept or reject each suggestion
- **`[update-docs]`** - Creates a docs PR with only the accepted files from a previous review (or runs the full pipeline if no review exists)
- **`[review-feature] PROJ-123`** - Runs `[review-docs]` and adds a **Spec vs Code Analysis** section that fetches the Jira ticket and its linked spec docs (Confluence, Google Docs), compares requirements against the PR code changes, and identifies covered, missing, and unplanned changes

### Recommended Workflow

1. Comment `[review-docs]` to see which doc files the AI identified as needing updates
2. Uncheck any files you don't want updated
3. Comment `[update-docs]` to create a PR with only the checked files

You can guide how the AI generates doc updates by adding instructions in your `[update-docs]` comment — global on the first line, per-file on subsequent lines:

```
[update-docs] keep changes minimal
config-ref.rst: only update the CLI usage example
```

## How It Works

1. **Triggered by PR Comments** - When someone comments `[review-docs]`, `[update-docs]`, or `[review-feature]` on a Pull Request
2. **Analyzes Code Changes** - Examines git diffs from your PRs using AI
3. **Smart File Selection** - Identifies relevant documentation files automatically
4. **Interactive Review** - Presents suggestions with checkboxes for user curation
5. **Content Generation** - Generates updated documentation in AsciiDoc, Markdown, or reStructuredText
6. **Spec vs Code Analysis** - Fetches Jira tickets and linked spec docs (Confluence, Google Docs) to identify gaps between requirements and the PR implementation

## Setup

### 1. Add Workflow

Create `.github/workflows/docs-assistant.yml`:

```yaml
name: Documentation Assistant

on:
  issue_comment:
    types: [created]

permissions:
  contents: read
  issues: write
  pull-requests: write

jobs:
  docs-assistant:
    runs-on: ubuntu-latest
    if: |
      github.event.issue.pull_request && 
      (contains(github.event.comment.body, '[review-docs]') ||
       contains(github.event.comment.body, '[update-docs]') ||
       contains(github.event.comment.body, '[review-feature]'))
    steps:
      - name: Get PR information
        id: pr_info
        if: github.event.issue.pull_request
        env:
          GH_TOKEN: ${{ secrets.GH_PAT }}
        run: |
          PR_NUMBER=${{ github.event.issue.number }}
          echo "Extracting PR information for PR #$PR_NUMBER"
          PR_DATA=$(gh api repos/${{ github.repository }}/pulls/$PR_NUMBER)
          
          HEAD_REF=$(echo "$PR_DATA" | jq -r '.head.ref')
          HEAD_REPO=$(echo "$PR_DATA" | jq -r '.head.repo.full_name')
          BASE_REF=$(echo "$PR_DATA" | jq -r '.base.ref')
          
          echo "head_ref=$HEAD_REF" >> $GITHUB_OUTPUT
          echo "head_repo=$HEAD_REPO" >> $GITHUB_OUTPUT
          echo "base_ref=$BASE_REF" >> $GITHUB_OUTPUT
          echo "pr_number=$PR_NUMBER" >> $GITHUB_OUTPUT
          
          echo "PR info extracted: #$PR_NUMBER, base: $BASE_REF, head: $HEAD_REF"

      - name: Checkout PR Code
        uses: actions/checkout@v4
        with:
          repository: ${{ steps.pr_info.outputs.head_repo || github.repository }}
          ref: ${{ steps.pr_info.outputs.head_ref || github.ref }}
          fetch-depth: 0
          token: ${{ secrets.GH_PAT }}
          
      - name: Documentation Assistant
        uses: redhat-community-ai-tools/code-to-docs@main
        with:
          model-api-base: ${{ secrets.MODEL_API_BASE }}
          model-api-key: ${{ secrets.MODEL_API_KEY }}
          model-name: ${{ secrets.MODEL_NAME }}
          docs-repo-url: ${{ secrets.DOCS_REPO_URL }}
          github-token: ${{ secrets.GH_PAT }}
          pr-number: ${{ github.event.issue.number }}
          pr-base: origin/${{ steps.pr_info.outputs.base_ref || 'main' }}
          pr-head-sha: ${{ steps.pr_info.outputs.head_ref }}
          docs-subfolder: ${{ secrets.DOCS_SUBFOLDER }}
          comment-body: ${{ github.event.comment.body }}
          docs-base-branch: ${{ secrets.DOCS_BASE_BRANCH || 'main' }}
          jira-url: ${{ secrets.JIRA_URL }}
          jira-username: ${{ secrets.JIRA_USERNAME }}
          jira-api-token: ${{ secrets.JIRA_API_TOKEN }}
          google-sa-key: ${{ secrets.GOOGLE_SA_KEY }}
          max-context-chars: ${{ secrets.MAX_CONTEXT_CHARS }}
```

### 2. Configure Secrets

Add these in **Settings → Secrets → Actions**:

| Secret | Description |
|--------|-------------|
| `MODEL_API_BASE` | Base URL of an OpenAI-compatible API (see examples below) |
| `MODEL_API_KEY` | API key for the model endpoint (leave empty if not required) |
| `MODEL_NAME` | Model name to use (e.g., `meta-llama/Llama-3.1-8B-Instruct`, `gemini-2.0-flash`) |
| `DOCS_REPO_URL` | Docs repository URL (e.g., `https://github.com/org/docs`) |
| `GH_PAT` | GitHub token with `repo` + `pull_requests:write` permissions |
| `DOCS_SUBFOLDER` | _(Optional)_ Docs subfolder path (e.g., `docs`) |
| `DOCS_BASE_BRANCH` | _(Optional)_ Base branch for docs PRs (default: `main`) |
| `JIRA_URL` | _(Optional, for `[review-feature]`)_ Jira instance URL (e.g., `https://your-company.atlassian.net`) |
| `JIRA_USERNAME` | _(Optional, for `[review-feature]`)_ Jira username/email |
| `JIRA_API_TOKEN` | _(Optional, for `[review-feature]`)_ Jira API token ([create here](https://id.atlassian.com/manage-profile/security/api-tokens)) |
| `GOOGLE_SA_KEY` | _(Optional, for `[review-feature]`)_ Google service account JSON key for fetching Google Docs. Docs must be shared with the service account email. |
| `MAX_CONTEXT_CHARS` | _(Optional)_ Maximum characters for LLM prompt content (default: `400000`, ~100K tokens). Decrease for models with smaller context windows (e.g., `32000` for an 8K-token model). |

### Supported Model Backends

Any OpenAI-compatible API works. Common examples:

| Backend | `MODEL_API_BASE` | `MODEL_NAME` |
|---------|-----------------|--------------|
| vLLM on OpenShift AI | `https://my-model-predictor-namespace.apps.cluster.example.com/v1` | Your InferenceService name (check `/v1/models`) |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.0-flash` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| Ollama | `http://localhost:11434/v1` | `llama3.1` |

## Features

- 🤖 **AI-Powered Analysis** - Uses any OpenAI-compatible LLM to identify relevant docs
- 📝 **Smart Suggestions** - Only updates what's necessary
- 🔍 **Review Mode** - See changes before applying
- ⚡ **Auto-Update Mode** - Create PRs automatically
- 📚 **Format Support** - AsciiDoc, Markdown, and reStructuredText (.rst)
- 🚀 **Optimized Performance** - Semantic indexes and cached summaries reduce API calls
- 🔍 **Spec vs Code Analysis** - Compare PR changes against Jira spec docs to find covered, missing, and unplanned changes

## Feature Review with [review-feature]

The `[review-feature]` command runs the full `[review-docs]` flow (identifying relevant doc files and suggesting updates) and adds a **Spec vs Code Analysis** section. It fetches the Jira ticket and its linked specification documents, extracts requirements, and compares them against the PR code changes to identify covered, missing, and unplanned changes.

[**See it in action**](https://redhat-community-ai-tools.github.io/code-to-docs/demo/review-feature)

### Spec Doc Locations

Spec docs can be linked from the Jira ticket description or comments. Supported formats:

- **Google Docs** — requires a Google service account key (`GOOGLE_SA_KEY` secret). Docs must be shared with the service account email.
- **Confluence** — fetched using the same Jira credentials (same Atlassian instance)

Links that cannot be fetched automatically will be flagged in the review for manual review.

### Important: Content Visibility

The `[review-feature]` comment includes content from the Jira ticket and linked spec documents (requirements, descriptions, analysis). This comment will be visible to anyone with access to the PR. Ensure that your repository's visibility settings are appropriate for the sensitivity of your Jira and spec doc content.

## Performance Optimization

The action uses a two-stage caching system stored in `.doc-index/`:

1. **Folder Indexes** - AI-generated semantic summaries of each documentation folder, used to quickly identify relevant areas without scanning all files
2. **File Summaries** - Cached summaries of long documentation files, reused across runs

These are automatically committed to your main branch and shared across all PRs, reducing runtime from ~20 minutes to ~4 minutes on large projects.
