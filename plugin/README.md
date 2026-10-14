# Code-to-Docs Claude Code Plugin

A Claude Code plugin for local documentation authoring. Wraps the
code-to-docs MCP server and adds a skill for updating docs with
verification steps.

## Plugin vs Action

These are complements, not alternatives:

- **Plugin** (this): for authoring docs before the PR. A local agent can
  run the CLI, compare `--help` output against docs, build the site, and
  grep for call sites. Use it at the moment you write the code.
- **Action** (the GitHub Action): for enforcement on the PR. Catches docs
  that the author missed and runs the same checks in CI.

## Setup

1. Clone the code-to-docs repo (or add it as a submodule):

   ```bash
   git clone https://github.com/redhat-community-ai-tools/code-to-docs.git
   ```

2. Install dependencies:

   ```bash
   cd code-to-docs
   uv sync
   ```

3. Add the plugin to your Claude Code project. Copy or symlink the `plugin/`
   directory into your project, or add the MCP server config to your
   project's `.mcp.json`:

   ```json
   {
     "mcpServers": {
       "code-to-docs": {
         "command": "uv",
         "args": ["run", "python", "src/mcp_server.py"],
         "cwd": "/path/to/code-to-docs"
       }
     }
   }
   ```

## Usage

Ask Claude Code to update docs after making code changes:

> "Update the documentation for the CLI flags i just added"

The `update-docs` skill guides the agent through:
1. Finding affected doc files via the MCP server
2. Reading style guidelines from `.code-to-docs/style.md`
3. Making the update
4. Verifying against actual tool output (running the CLI, building docs,
   checking code samples)

## Configuration

The plugin respects the same `.code-to-docs/` configuration as the Action:

- `.code-to-docs/style.md` for style guidelines
- `.code-to-docs/ignore` for file exclusions
- `.code-to-docs/config.json` for tool settings
