"""Tests for discovery.py — AI-powered file discovery and selection."""

import os
from unittest.mock import patch, MagicMock

import pytest

from discovery import (
    summarize_long_file,
    get_file_content_or_summaries,
    _process_file_selection_batch,
    ask_ai_for_relevant_files,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _mock_ai_response(content):
    """Build a mock OpenAI client that returns a canned response."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


# ── summarize_long_file ─────────────────────────────────────────────────────


class TestSummarizeLongFile:
    def test_returns_summary(self):
        mock_client = _mock_ai_response("This file documents health checks.")
        with patch("discovery.get_client", return_value=mock_client), \
             patch("discovery.get_model_name", return_value="test-model"):
            result = summarize_long_file("health-checks.rst", "long content here")
        assert result == "This file documents health checks."
        mock_client.chat.completions.create.assert_called_once()

    def test_retries_on_empty_response(self):
        mock_client = MagicMock()
        # First call returns empty, second returns real content
        empty_response = MagicMock()
        empty_response.choices = [MagicMock()]
        empty_response.choices[0].message.content = ""

        good_response = MagicMock()
        good_response.choices = [MagicMock()]
        good_response.choices[0].message.content = "Summary after retry"

        mock_client.chat.completions.create.side_effect = [empty_response, good_response]

        with patch("discovery.get_client", return_value=mock_client), \
             patch("discovery.get_model_name", return_value="test-model"):
            result = summarize_long_file("file.rst", "content")

        assert result == "Summary after retry"
        assert mock_client.chat.completions.create.call_count == 2

    def test_raises_after_max_retries(self):
        mock_client = MagicMock()
        empty_response = MagicMock()
        empty_response.choices = [MagicMock()]
        empty_response.choices[0].message.content = ""
        mock_client.chat.completions.create.return_value = empty_response

        with patch("discovery.get_client", return_value=mock_client), \
             patch("discovery.get_model_name", return_value="test-model"), \
             pytest.raises(Exception, match="Failed to summarize"):
            summarize_long_file("file.rst", "content", max_retries=1)


# ── get_file_content_or_summaries ───────────────────────────────────────────


class TestGetFileContentOrSummaries:
    def test_finds_doc_files(self, tmp_path, monkeypatch):
        (tmp_path / "guide.rst").write_text("Guide\n=====\n\nSome guide.")
        (tmp_path / "readme.md").write_text("# Readme\n\nSome readme.")
        monkeypatch.chdir(tmp_path)

        # High threshold so no summarization happens
        result = get_file_content_or_summaries(line_threshold=1000)

        paths = [path for path, _ in result]
        assert len(result) == 2
        assert any("guide.rst" in p for p in paths)
        assert any("readme.md" in p for p in paths)

    def test_skips_doc_index_files(self, tmp_path, monkeypatch):
        index_dir = tmp_path / ".doc-index"
        index_dir.mkdir()
        (index_dir / "something.md").write_text("# Index file")
        (tmp_path / "real.md").write_text("# Real doc")
        monkeypatch.chdir(tmp_path)

        result = get_file_content_or_summaries(line_threshold=1000)

        paths = [path for path, _ in result]
        assert len(result) == 1
        assert any("real.md" in p for p in paths)
        assert not any(".doc-index" in p for p in paths)

    def test_summarizes_long_files(self, tmp_path, monkeypatch):
        long_content = "\n".join([f"Line {i}" for i in range(400)])
        (tmp_path / "long.rst").write_text(long_content)
        monkeypatch.chdir(tmp_path)

        with patch("discovery.summarize_long_file", return_value="AI summary") as mock_summarize:
            result = get_file_content_or_summaries(line_threshold=300)

        assert len(result) == 1
        assert result[0][1] == "AI summary"
        mock_summarize.assert_called_once()


# ── _process_file_selection_batch ───────────────────────────────────────────


class TestProcessFileSelectionBatch:
    def test_returns_selected_files(self):
        mock_client = _mock_ai_response("file1.rst\nfile2.md")
        batch = [("file1.rst", "preview1"), ("file2.md", "preview2"), ("file3.rst", "preview3")]

        with patch("discovery.get_client", return_value=mock_client), \
             patch("discovery.get_model_name", return_value="test-model"):
            batch_num, files = _process_file_selection_batch(
                "some diff", batch, batch_num=1, total_batches=1
            )

        assert batch_num == 1
        assert files == ["file1.rst", "file2.md"]

    def test_returns_none_response(self):
        mock_client = _mock_ai_response("NONE")
        batch = [("file1.rst", "preview1")]

        with patch("discovery.get_client", return_value=mock_client), \
             patch("discovery.get_model_name", return_value="test-model"):
            batch_num, files = _process_file_selection_batch(
                "some diff", batch, batch_num=1, total_batches=1
            )

        assert files == []

    def test_filters_non_doc_files(self):
        mock_client = _mock_ai_response("file.rst\nscript.py")
        batch = [("file.rst", "preview1"), ("script.py", "preview2")]

        with patch("discovery.get_client", return_value=mock_client), \
             patch("discovery.get_model_name", return_value="test-model"):
            batch_num, files = _process_file_selection_batch(
                "some diff", batch, batch_num=1, total_batches=1
            )

        assert files == ["file.rst"]
        assert "script.py" not in files


# ── ask_ai_for_relevant_files ───────────────────────────────────────────────


class TestAskAiForRelevantFiles:
    def test_deduplicates_results(self):
        # Mock _process_file_selection_batch to return duplicates across batches
        def fake_process(diff, batch, batch_num, total_batches, max_retries=3):
            # Every batch returns the same file
            return batch_num, ["file1.rst", "file2.md"]

        file_previews = [(f"file{i}.rst", f"preview{i}") for i in range(15)]

        with patch("discovery._process_file_selection_batch", side_effect=fake_process):
            result = ask_ai_for_relevant_files("some diff", file_previews)

        # Should be deduplicated
        assert result == ["file1.rst", "file2.md"]
        assert len(result) == len(set(result))

    def test_strips_docs_subfolder_prefix(self, monkeypatch):
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")

        def fake_process(diff, batch, batch_num, total_batches, max_retries=3):
            return batch_num, ["docs/guide.rst", "docs/ref.md", "standalone.rst"]

        file_previews = [("docs/guide.rst", "preview1"), ("docs/ref.md", "preview2")]

        with patch("discovery._process_file_selection_batch", side_effect=fake_process):
            result = ask_ai_for_relevant_files("some diff", file_previews)

        assert "guide.rst" in result
        assert "ref.md" in result
        assert "standalone.rst" in result
        # Prefix should be stripped
        assert not any(f.startswith("docs/") for f in result)
