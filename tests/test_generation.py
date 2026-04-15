"""Tests for generation.py — AI content generation and file I/O."""

import pytest
from unittest.mock import patch, MagicMock

from generation import (
    load_full_content,
    overwrite_file,
    ask_ai_for_updated_content,
    generate_updates_parallel,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _mock_ai_response(content):
    """Build a mock OpenAI client that returns *content* from chat completion."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


# ── load_full_content ───────────────────────────────────────────────────────


class TestLoadFullContent:
    def test_reads_valid_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        doc = tmp_path / "readme.rst"
        doc.write_text("Hello RST", encoding="utf-8")
        result = load_full_content("readme.rst")
        assert result == "Hello RST"

    def test_rejects_traversal_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = load_full_content("../../etc/passwd")
        assert result == ""

    def test_returns_empty_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = load_full_content("nonexistent.rst")
        assert result == ""


# ── overwrite_file ──────────────────────────────────────────────────────────


class TestOverwriteFile:
    def test_writes_valid_doc_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "output.rst"
        target.write_text("old", encoding="utf-8")
        result = overwrite_file("output.rst", "new content")
        assert result is True
        assert target.read_text(encoding="utf-8") == "new content"

    def test_rejects_non_doc_extension(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "script.py"
        target.write_text("print('hi')", encoding="utf-8")
        result = overwrite_file("script.py", "hacked")
        assert result is False

    def test_rejects_traversal_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = overwrite_file("../evil.rst", "bad content")
        assert result is False


# ── ask_ai_for_updated_content ──────────────────────────────────────────────


class TestAskAiForUpdatedContent:
    DIFF = "diff --git a/foo.py\n+added line"
    CONTENT = "Some documentation content"

    def test_returns_updated_content(self):
        mock_client = _mock_ai_response("Updated documentation text")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(
                self.DIFF, "docs/guide.md", self.CONTENT
            )
        assert result == "Updated documentation text"

    def test_returns_no_update_needed(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(
                self.DIFF, "docs/guide.md", self.CONTENT
            )
        assert result == "NO_UPDATE_NEEDED"

    def test_detects_rst_format(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF, "docs/guide.rst", self.CONTENT
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "RESTRUCTUREDTEXT" in prompt

    def test_detects_md_format(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF, "docs/guide.md", self.CONTENT
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "MARKDOWN" in prompt

    def test_includes_user_instructions(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF,
                "docs/guide.md",
                self.CONTENT,
                user_instructions="Focus on API changes only",
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "Focus on API changes only" in prompt


# ── generate_updates_parallel ───────────────────────────────────────────────


class TestGenerateUpdatesParallel:
    DIFF = "diff --git a/foo.py\n+added line"

    def test_processes_multiple_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create two doc files
        (tmp_path / "a.rst").write_text("Doc A", encoding="utf-8")
        (tmp_path / "b.rst").write_text("Doc B", encoding="utf-8")

        mock_client = _mock_ai_response("Updated doc")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            results = generate_updates_parallel(
                self.DIFF, ["a.rst", "b.rst"], max_workers=2
            )

        assert len(results) == 2
        paths_returned = {r[0] for r in results}
        assert paths_returned == {"a.rst", "b.rst"}
        for _, original, updated in results:
            assert updated == "Updated doc"

    def test_skips_no_update_needed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.rst").write_text("Doc A", encoding="utf-8")
        (tmp_path / "b.rst").write_text("Doc B", encoding="utf-8")

        mock_client = MagicMock()

        def side_effect(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            if "a.rst" in prompt:
                mock_resp.choices[0].message.content = "Updated A"
            else:
                mock_resp.choices[0].message.content = "NO_UPDATE_NEEDED"
            return mock_resp

        mock_client.chat.completions.create.side_effect = side_effect

        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            results = generate_updates_parallel(
                self.DIFF, ["a.rst", "b.rst"], max_workers=2
            )

        assert len(results) == 1
        assert results[0][0] == "a.rst"
        assert results[0][2] == "Updated A"
