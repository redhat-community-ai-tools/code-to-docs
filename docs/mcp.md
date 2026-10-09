# MCP Server

code-to-docs exposes its documentation retrieval layer as a read-only
[MCP](https://modelcontextprotocol.io/) server. Any MCP-compatible agent
(Claude Code, Cowork, IDE extensions) can query which docs cover a given
source file, browse folder indexes, and check for drift.

## Tools

| Tool | Description |
|------|-------------|
| `find_docs_for_code` | Given source file paths, returns ranked doc files with reasons |
| `get_doc_index` | Returns the semantic index summary for a docs folder |
| `check_doc_drift` | Assesses whether a single doc file is likely stale |

All tools are **read-only**. The server does not modify any files.

## Setup

### Claude Code

Add to your project's `.mcp.json`:

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

### Other MCP Clients

Run the server via stdio:

```bash
cd /path/to/code-to-docs
uv run python src/mcp_server.py
```

The server communicates over stdin/stdout using the MCP protocol.

## Example

Ask your agent: "Which docs cover src/config.py?"

The agent calls `find_docs_for_code(paths=["src/config.py"])` and gets
back a list of doc files whose folder index references that module.
