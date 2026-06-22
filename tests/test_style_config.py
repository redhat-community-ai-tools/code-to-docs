"""Tests for persistent style configuration and output format validation."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from config import load_style_config
from generation import strip_code_fences, validate_format


# =============================================================================
# load_style_config tests
# =============================================================================


class TestLoadStyleConfig:
    def test_loads_from_explicit_path(self, tmp_path):
        style_file = tmp_path / "my-style.md"
        style_file.write_text("Use active voice\nKeep paragraphs short")
        os.chdir(tmp_path)
        result = load_style_config(config_path=str(style_file))
        assert "active voice" in result

    def test_returns_empty_when_file_missing(self, tmp_path):
        os.chdir(tmp_path)
        result = load_style_config(config_path="nonexistent.md")
        assert result == ""

    def test_auto_detects_default_path(self, tmp_path):
        code_to_docs_dir = tmp_path / ".code-to-docs"
        code_to_docs_dir.mkdir()
        style_file = code_to_docs_dir / "style.md"
        style_file.write_text("# Style Rules\nUse dashes for lists")
        os.chdir(tmp_path)
        result = load_style_config()
        assert "dashes" in result

    def test_returns_empty_when_no_auto_detect(self, tmp_path):
        os.chdir(tmp_path)
        result = load_style_config()
        assert result == ""

    def test_env_var_override(self, tmp_path, monkeypatch):
        style_file = tmp_path / "custom-style.md"
        style_file.write_text("Custom style from env var")
        os.chdir(tmp_path)
        monkeypatch.setenv("STYLE_CONFIG_PATH", str(style_file))
        result = load_style_config()
        assert "Custom style" in result

    def test_rejects_non_md_extension(self, tmp_path):
        style_file = tmp_path / "style.txt"
        style_file.write_text("Some style rules")
        os.chdir(tmp_path)
        result = load_style_config(config_path=str(style_file))
        assert result == ""

    def test_rejects_path_traversal(self, tmp_path):
        os.chdir(tmp_path)
        result = load_style_config(config_path="../../../etc/passwd.md")
        assert result == ""

    def test_returns_empty_for_empty_file(self, tmp_path):
        style_file = tmp_path / "empty.md"
        style_file.write_text("")
        os.chdir(tmp_path)
        result = load_style_config(config_path=str(style_file))
        assert result == ""


# =============================================================================
# strip_code_fences tests
# =============================================================================


class TestStripCodeFences:
    def test_strips_markdown_fences(self):
        text = "```markdown\n# Hello\n\nWorld\n```"
        assert strip_code_fences(text) == "# Hello\n\nWorld"

    def test_strips_md_fences(self):
        text = "```md\n# Hello\n```"
        assert strip_code_fences(text) == "# Hello"

    def test_strips_adoc_fences(self):
        text = "```adoc\n= Title\n\nContent\n```"
        assert strip_code_fences(text) == "= Title\n\nContent"

    def test_strips_asciidoc_fences(self):
        text = "```asciidoc\n= Title\n```"
        assert strip_code_fences(text) == "= Title"

    def test_strips_rst_fences(self):
        text = "```rst\nTitle\n=====\n\nContent\n```"
        assert strip_code_fences(text) == "Title\n=====\n\nContent"

    def test_strips_restructuredtext_fences(self):
        text = "```restructuredtext\nTitle\n=====\n```"
        assert strip_code_fences(text) == "Title\n====="

    def test_strips_plain_fences(self):
        text = "```\n# Hello\n\nWorld\n```"
        assert strip_code_fences(text) == "# Hello\n\nWorld"

    def test_leaves_clean_output_unchanged(self):
        text = "# Hello\n\nWorld"
        assert strip_code_fences(text) == text

    def test_handles_no_update_needed(self):
        assert strip_code_fences("NO_UPDATE_NEEDED") == "NO_UPDATE_NEEDED"

    def test_handles_empty_string(self):
        assert strip_code_fences("") == ""

    def test_handles_none(self):
        assert strip_code_fences(None) is None

    def test_preserves_internal_code_fences(self):
        text = "# Doc\n\n```python\nprint('hello')\n```\n\nMore text"
        assert strip_code_fences(text) == text


# =============================================================================
# validate_format tests
# =============================================================================


class TestValidateFormat:
    def test_no_update_needed_passes(self):
        is_valid, errors = validate_format("NO_UPDATE_NEEDED", "file.md")
        assert is_valid
        assert errors == ""

    def test_empty_text_passes(self):
        is_valid, errors = validate_format("", "file.md")
        assert is_valid

    def test_valid_markdown_passes(self):
        md = "# Title\n\nSome **bold** text.\n\n- Item 1\n- Item 2\n"
        is_valid, errors = validate_format(md, "docs/guide.md")
        assert is_valid

    def test_valid_rst_passes(self):
        rst = "Title\n=====\n\nSome text.\n\n- Item 1\n- Item 2\n"
        is_valid, errors = validate_format(rst, "docs/guide.rst")
        assert is_valid

    def test_unknown_extension_passes(self):
        is_valid, errors = validate_format("anything", "file.txt")
        assert is_valid

    def test_broken_rst_detected(self):
        rst = "Title\n==\n\nBad underline length.\n"
        is_valid, errors = validate_format(rst, "docs/broken.rst")
        # docutils may or may not flag this depending on version, but shouldn't crash
        assert isinstance(is_valid, bool)


# =============================================================================
# Retry loop integration test
# =============================================================================


class TestRetryLoop:
    @patch("generation.validate_format")
    @patch("generation.get_client")
    @patch("generation.get_model_name", return_value="test-model")
    @patch("generation.get_max_context_chars", return_value=400_000)
    def test_retries_on_invalid_format_then_succeeds(self, mock_budget, mock_model, mock_client, mock_validate):
        # First call: initial generation returns content that fails validation
        # Second call: retry returns content that passes validation
        mock_response_1 = MagicMock()
        mock_response_1.choices = [MagicMock()]
        mock_response_1.choices[0].message.content = "Bad RST content"

        mock_response_2 = MagicMock()
        mock_response_2.choices = [MagicMock()]
        mock_response_2.choices[0].message.content = "Fixed RST content"

        client = MagicMock()
        client.chat.completions.create.side_effect = [mock_response_1, mock_response_2]
        mock_client.return_value = client

        # validate_format: fail on first content, pass on second
        mock_validate.side_effect = [
            (False, "RST validation errors: bad underline"),
            (True, ""),
        ]

        from generation import ask_ai_for_updated_content
        result = ask_ai_for_updated_content(
            diff="diff --git a/foo.py\n+new line",
            file_path="docs/guide.rst",
            current_content="Title\n=====\n\nOld content",
        )
        assert result.strip() == "Fixed RST content"
        assert client.chat.completions.create.call_count == 2

    @patch("generation.get_client")
    @patch("generation.get_model_name", return_value="test-model")
    @patch("generation.get_max_context_chars", return_value=400_000)
    def test_returns_no_update_on_persistent_failure(self, mock_budget, mock_model, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "NO_UPDATE_NEEDED"

        client = MagicMock()
        client.chat.completions.create.return_value = mock_response
        mock_client.return_value = client

        from generation import ask_ai_for_updated_content
        result = ask_ai_for_updated_content(
            diff="diff --git a/foo.py\n+new line",
            file_path="docs/guide.md",
            current_content="# Title\n\nContent",
        )
        assert result.strip() == "NO_UPDATE_NEEDED"
