"""MCP server exposing read-only documentation retrieval tools.

Provides three tools for agents and editors:
- find_docs_for_code: which docs cover given source files
- get_doc_index: folder-level index summary
- check_doc_drift: staleness assessment for a single doc
"""

import json
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

sys.path.insert(0, str(Path(__file__).resolve().parent))

from doc_index import get_docs_in_folder, get_docs_root, load_manifest

app = Server("code-to-docs")


@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="find_docs_for_code",
            description="Find documentation files that cover the given source file paths",
            inputSchema={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Source file paths to find docs for",
                    },
                },
                "required": ["paths"],
            },
        ),
        Tool(
            name="get_doc_index",
            description="Get the semantic index summary for a documentation folder",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Folder path relative to docs root (use '_root' for root-level docs)",
                    },
                },
                "required": ["folder"],
            },
        ),
        Tool(
            name="check_doc_drift",
            description="Assess whether a documentation file is likely stale",
            inputSchema={
                "type": "object",
                "properties": {
                    "doc_path": {
                        "type": "string",
                        "description": "Path to the doc file relative to docs root",
                    },
                },
                "required": ["doc_path"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name, arguments):
    if name == "find_docs_for_code":
        return await _find_docs_for_code(arguments.get("paths", []))
    elif name == "get_doc_index":
        return await _get_doc_index(arguments.get("folder", ""))
    elif name == "check_doc_drift":
        return await _check_doc_drift(arguments.get("doc_path", ""))
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _find_docs_for_code(paths):
    """Find docs whose folder index mentions the given source paths."""
    docs_root = get_docs_root().resolve()
    manifest = load_manifest(docs_root)
    results = []

    for folder, info in manifest.get("folders", {}).items():
        index_text = info.get("index", "")
        for path in paths:
            basename = Path(path).stem
            if basename in index_text or path in index_text:
                docs = get_docs_in_folder(folder, docs_root)
                for doc in docs:
                    rel = str(doc.relative_to(docs_root))
                    results.append(
                        {"file": rel, "folder": folder, "reason": f"Index mentions {path}"}
                    )
                break

    if not results:
        return [TextContent(type="text", text="No documentation files found for the given paths.")]
    return [TextContent(type="text", text=json.dumps(results, indent=2))]


async def _get_doc_index(folder):
    """Return the index summary for a folder."""
    docs_root = get_docs_root().resolve()
    manifest = load_manifest(docs_root)
    folder_info = manifest.get("folders", {}).get(folder, {})
    index_text = folder_info.get("index", "")
    if not index_text:
        return [TextContent(type="text", text=f"No index found for folder: {folder}")]
    return [TextContent(type="text", text=index_text)]


async def _check_doc_drift(doc_path):
    """Assess staleness of a single doc file."""
    docs_root = get_docs_root().resolve()
    full_path = docs_root / doc_path
    if not full_path.exists():
        return [TextContent(type="text", text=f"File not found: {doc_path}")]
    try:
        content = full_path.read_text(encoding="utf-8")
        line_count = len(content.splitlines())
        has_code_blocks = "```" in content
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "file": doc_path,
                        "lines": line_count,
                        "has_code_blocks": has_code_blocks,
                        "assessment": "Run with an LLM endpoint configured for full drift assessment",
                    },
                    indent=2,
                ),
            )
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"Error reading {doc_path}: {e}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
