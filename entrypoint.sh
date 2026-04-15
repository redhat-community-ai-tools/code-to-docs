#!/bin/bash
set -e

echo "🚀 Starting Upstream Documentation Enhancer GitHub Action"

# Fix Git ownership issue in Docker container
git config --global --add safe.directory /github/workspace

# Configure Git user for commits
git config --global user.email "docbot@github-action.com"
git config --global user.name "Documentation Enhancer Bot"

# Setup environment variables for GitHub Actions context
# PR info is now passed as inputs from the workflow (already set as env vars)
# Just ensure they're exported if they exist
if [ -n "$PR_NUMBER" ]; then
  export PR_NUMBER="$PR_NUMBER"
fi

if [ -n "$PR_BASE" ]; then
  export PR_BASE="$PR_BASE"
else
  export PR_BASE="origin/main"
fi

if [ -n "$PR_HEAD_SHA" ]; then
  export PR_HEAD_SHA="$PR_HEAD_SHA"
fi

if [ -n "$DOCS_SUBFOLDER" ]; then
  export DOCS_SUBFOLDER="$DOCS_SUBFOLDER"
fi

if [ -n "$COMMENT_BODY" ]; then
  export COMMENT_BODY="$COMMENT_BODY"
fi

if [ -n "$JIRA_URL" ]; then
  export JIRA_URL="$JIRA_URL"
fi

if [ -n "$JIRA_USERNAME" ]; then
  export JIRA_USERNAME="$JIRA_USERNAME"
fi

if [ -n "$JIRA_API_TOKEN" ]; then
  export JIRA_API_TOKEN="$JIRA_API_TOKEN"
fi

if [ -n "$GOOGLE_SA_KEY" ]; then
  # Write service account JSON to temp file for gws CLI
  GWS_CREDS_FILE=$(mktemp /tmp/gws-sa-XXXXXX.json)
  printf '%s' "$GOOGLE_SA_KEY" > "$GWS_CREDS_FILE"
  chmod 600 "$GWS_CREDS_FILE"
  export GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE="$GWS_CREDS_FILE"
  echo "Google Workspace CLI credentials configured"
fi

# Validate required inputs
if [ -z "$MODEL_API_BASE" ]; then
  echo "❌ Error: model-api-base input is required"
  exit 1
fi

if [ -z "$MODEL_NAME" ]; then
  echo "❌ Error: model-name input is required"
  exit 1
fi

if [ -z "$DOCS_REPO_URL" ]; then
  echo "❌ Error: docs-repo-url input is required"  
  exit 1
fi

if [ -z "$GH_TOKEN" ]; then
  echo "❌ Error: github-token input is required"
  exit 1
fi

# Build command arguments
ARGS=""
if [ "$DRY_RUN" = "true" ]; then
  ARGS="$ARGS --dry-run"
  echo "🔍 Running in dry-run mode"
fi

echo "📊 Environment:"
echo "  PR_NUMBER: $PR_NUMBER"
echo "  PR_BASE: $PR_BASE" 
echo "  BRANCH_NAME: ${BRANCH_NAME:-doc-update-from-pr}"
echo "  DRY_RUN: ${DRY_RUN:-false}"
echo "  DOCS_REPO_URL: $DOCS_REPO_URL"

echo ""
echo "🎯 Running documentation enhancer with args: $ARGS"

# Cleanup function to remove temp credentials
cleanup() {
  if [ -n "$GWS_CREDS_FILE" ] && [ -f "$GWS_CREDS_FILE" ]; then
    rm -f "$GWS_CREDS_FILE"
  fi
}
trap cleanup EXIT

# Run the documentation enhancer
if python /app/suggest_docs.py $ARGS; then
  echo "✅ Documentation enhancer completed successfully"

  # Set GitHub Actions outputs (if result data is available)
  if [ -n "$GITHUB_OUTPUT" ]; then
    echo "status=success" >> "$GITHUB_OUTPUT"
    echo "pr-created=true" >> "$GITHUB_OUTPUT"
  fi

  exit 0
else
  echo "❌ Documentation enhancer failed"

  # Set GitHub Actions outputs
  if [ -n "$GITHUB_OUTPUT" ]; then
    echo "status=failed" >> "$GITHUB_OUTPUT"
    echo "pr-created=false" >> "$GITHUB_OUTPUT"
  fi

  exit 1
fi
