# Code-to-Docs - AI Documentation Assistant

[![GitHub Action](https://img.shields.io/badge/GitHub-Action-blue.svg)](https://github.com/marketplace/actions/upstream-docs-enhancer)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

AI-powered GitHub Action that automatically analyzes code changes and updates documentation using Gemini AI.

## Usage

Comment on any Pull Request:
- **`[review-docs]`** - Posts a comment with a summary of which doc files need updates (no full content, no PR created)
- **`[update-docs]`** - Posts a comment with the full proposed changes AND creates a PR in docs repo

## How It Works

1. **Triggered by PR Comments** - When someone comments `[review-docs]` or `[update-docs]` on a Pull Request
2. **Analyzes Code Changes** - Examines git diffs from your PRs using AI
3. **Smart File Selection** - Identifies relevant documentation files automatically
4. **Content Generation** - Generates updated documentation content in proper AsciiDoc/Markdown format

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
       contains(github.event.comment.body, '[update-docs]'))
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
        uses: csoceanu/code-to-docs@main
        with:
          gemini-api-key: ${{ secrets.GEMINI_API_KEY }}
          docs-repo-url: ${{ secrets.DOCS_REPO_URL }}
          github-token: ${{ secrets.GH_PAT }}
          pr-number: ${{ github.event.issue.number }}
          pr-base: origin/${{ steps.pr_info.outputs.base_ref || 'main' }}
          pr-head-sha: ${{ steps.pr_info.outputs.head_ref }}
          docs-subfolder: ${{ secrets.DOCS_SUBFOLDER }}
          comment-body: ${{ github.event.comment.body }}
          docs-base-branch: ${{ secrets.DOCS_BASE_BRANCH || 'main' }}
```

### 2. Configure Secrets

Add these in **Settings → Secrets → Actions**:

| Secret | Description |
|--------|-------------|
| `GEMINI_API_KEY` | Get from [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `DOCS_REPO_URL` | Docs repository URL (e.g., `https://github.com/org/docs`) |
| `GH_PAT` | GitHub token with `repo` + `pull_requests:write` permissions |
| `DOCS_SUBFOLDER` | _(Optional)_ Docs subfolder path (e.g., `docs`) |
| `DOCS_BASE_BRANCH` | _(Optional)_ Base branch for docs PRs (default: `main`) |

## Features

- 🤖 **AI-Powered Analysis** - Uses Gemini to identify relevant docs
- 📝 **Smart Suggestions** - Only updates what's necessary
- 🔍 **Review Mode** - See changes before applying
- ⚡ **Auto-Update Mode** - Create PRs automatically
- 📚 **Format Support** - AsciiDoc and Markdown
