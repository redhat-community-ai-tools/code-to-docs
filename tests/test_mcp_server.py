"""Tests for mcp_server.py — MCP tool handlers and path validation."""

import asyncio
import json
from unittest.mock import patch

from mcp_server import _check_doc_drift, _find_docs_for_code, _get_doc_index


def run(coro):
    """Helper to run an async coroutine synchronously."""
    return asyncio.run(coro)


# ── _find_docs_for_code ────────────────────────────────────────────────────────


class TestFindDocsForCode:
    def test_returns_matching_docs(self, tmp_path):
        index_text = "This index covers config.py and utils.py modules."
        doc_file = tmp_path / "guide.md"
        doc_file.write_text("# Guide")

        with (
            patch("mcp_server.get_docs_root", return_value=tmp_path),
            patch("mcp_server.load_all_indexes", return_value={"_root": index_text}),
            patch("mcp_server.get_docs_in_folder", return_value=[doc_file]),
        ):
            result = run(_find_docs_for_code(["src/config.py"]))

        assert len(result) == 1
        parsed = json.loads(result[0].text)
        assert len(parsed) == 1
        assert parsed[0]["file"] == "guide.md"
        assert parsed[0]["folder"] == "_root"
        assert "config.py" in parsed[0]["reason"]

    def test_matches_by_stem(self, tmp_path):
        index_text = "Covers the config module and its settings."
        doc_file = tmp_path / "ref.md"
        doc_file.write_text("# Ref")

        with (
            patch("mcp_server.get_docs_root", return_value=tmp_path),
            patch("mcp_server.load_all_indexes", return_value={"_root": index_text}),
            patch("mcp_server.get_docs_in_folder", return_value=[doc_file]),
        ):
            result = run(_find_docs_for_code(["src/config.py"]))

        parsed = json.loads(result[0].text)
        assert len(parsed) == 1

    def test_no_matches_returns_message(self, tmp_path):
        index_text = "This index covers authentication flows."

        with (
            patch("mcp_server.get_docs_root", return_value=tmp_path),
            patch("mcp_server.load_all_indexes", return_value={"auth": index_text}),
        ):
            result = run(_find_docs_for_code(["src/unrelated.py"]))

        assert "No documentation files found" in result[0].text

    def test_empty_indexes(self, tmp_path):
        with (
            patch("mcp_server.get_docs_root", return_value=tmp_path),
            patch("mcp_server.load_all_indexes", return_value={}),
        ):
            result = run(_find_docs_for_code(["src/config.py"]))

        assert "No documentation files found" in result[0].text


# ── _get_doc_index ──────────────────────────────────────────────────────────────


class TestGetDocIndex:
    def test_returns_index_text(self, tmp_path):
        index_content = "# AUTH Documentation Index\n\n## Overview\nCovers auth."

        with (
            patch("mcp_server.get_docs_root", return_value=tmp_path),
            patch("mcp_server.load_index", return_value=index_content),
        ):
            result = run(_get_doc_index("auth"))

        assert result[0].text == index_content

    def test_missing_folder_returns_message(self, tmp_path):
        with (
            patch("mcp_server.get_docs_root", return_value=tmp_path),
            patch("mcp_server.load_index", return_value=None),
        ):
            result = run(_get_doc_index("nonexistent"))

        assert "No index found" in result[0].text


# ── _check_doc_drift ────────────────────────────────────────────────────────────


class TestCheckDocDrift:
    def test_returns_file_metadata(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text("# Guide\n\nSome content.\n\n```python\nprint('hello')\n```\n")

        with patch("mcp_server.get_docs_root", return_value=tmp_path):
            result = run(_check_doc_drift("guide.md"))

        parsed = json.loads(result[0].text)
        assert parsed["file"] == "guide.md"
        assert parsed["lines"] == 7
        assert parsed["has_code_blocks"] is True

    def test_file_without_code_blocks(self, tmp_path):
        doc = tmp_path / "plain.md"
        doc.write_text("# Plain\n\nNo code here.\n")

        with patch("mcp_server.get_docs_root", return_value=tmp_path):
            result = run(_check_doc_drift("plain.md"))

        parsed = json.loads(result[0].text)
        assert parsed["has_code_blocks"] is False

    def test_missing_file(self, tmp_path):
        with patch("mcp_server.get_docs_root", return_value=tmp_path):
            result = run(_check_doc_drift("missing.md"))

        assert "File not found" in result[0].text

    def test_path_traversal_blocked(self, tmp_path):
        # Create a file outside docs root that should not be accessible
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("secret")

        with patch("mcp_server.get_docs_root", return_value=tmp_path):
            result = run(_check_doc_drift("../../secret.txt"))

        assert "Invalid path" in result[0].text

    def test_absolute_path_traversal_blocked(self, tmp_path):
        with patch("mcp_server.get_docs_root", return_value=tmp_path):
            result = run(_check_doc_drift("../../../etc/passwd"))

        assert "Invalid path" in result[0].text

    def test_error_sanitized(self, tmp_path):
        doc = tmp_path / "broken.md"
        doc.write_text("content")

        with (
            patch("mcp_server.get_docs_root", return_value=tmp_path),
            patch("pathlib.Path.read_text", side_effect=PermissionError("access denied")),
        ):
            result = run(_check_doc_drift("broken.md"))

        assert "Error reading" in result[0].text
