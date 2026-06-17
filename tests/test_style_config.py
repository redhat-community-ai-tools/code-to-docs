"""Tests for style configuration loading and prompt injection."""

import pytest
from unittest.mock import patch, MagicMock

from config import load_style_config, _yaml_to_guidelines, _MAX_STYLE_CONFIG_CHARS
from generation import ask_ai_for_updated_content


# ── helpers ─────────────────────────────────────────────────────────────────


def _mock_ai_response(content):
    """Build a mock OpenAI client that returns *content* from chat completion."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


# ── load_style_config ───────────────────────────────────────────────────────


class TestLoadStyleConfig:
    def test_returns_empty_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = load_style_config()
        assert result == ""

    def test_loads_yaml_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".code-to-docs"
        config_dir.mkdir()
        config_file = config_dir / "style.yml"
        config_file.write_text("voice: active\nheading_style: sentence_case\n", encoding="utf-8")

        result = load_style_config()
        assert "voice" in result
        assert "active" in result
        assert "heading_style" in result
        assert "sentence_case" in result

    def test_loads_markdown_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".code-to-docs"
        config_dir.mkdir()
        config_file = config_dir / "style.md"
        config_file.write_text("# Style Guide\n\nUse active voice.\n", encoding="utf-8")

        result = load_style_config()
        assert "Style Guide" in result
        assert "active voice" in result

    def test_explicit_path_overrides_autodetect(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create auto-detect file
        config_dir = tmp_path / ".code-to-docs"
        config_dir.mkdir()
        (config_dir / "style.yml").write_text("voice: passive\n", encoding="utf-8")

        # Create explicit file
        custom = tmp_path / "custom-style.md"
        custom.write_text("Use imperative mood.\n", encoding="utf-8")

        result = load_style_config(config_path=str(custom))
        assert "imperative mood" in result
        assert "passive" not in result

    def test_env_var_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        custom = tmp_path / "my-style.md"
        custom.write_text("Be concise.\n", encoding="utf-8")
        monkeypatch.setenv("STYLE_CONFIG_PATH", str(custom))

        result = load_style_config()
        assert "Be concise" in result

    def test_missing_explicit_path_warns(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        missing = str(tmp_path / "nonexistent" / "style.yml")
        result = load_style_config(config_path=missing)
        assert result == ""
        captured = capsys.readouterr()
        assert "Warning" in captured.out

    def test_path_traversal_rejected(self, tmp_path, monkeypatch, capsys):
        """Paths outside the working directory are rejected by validate_file_path."""
        monkeypatch.chdir(tmp_path)
        result = load_style_config(config_path="/etc/passwd")
        assert result == ""
        captured = capsys.readouterr()
        assert "security check" in captured.out.lower() or "rejected" in captured.out.lower()

    def test_empty_config_returns_empty(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".code-to-docs"
        config_dir.mkdir()
        (config_dir / "style.yml").write_text("", encoding="utf-8")

        result = load_style_config()
        assert result == ""
        captured = capsys.readouterr()
        assert "empty" in captured.out.lower()

    def test_yaml_auto_detect_order(self, tmp_path, monkeypatch):
        """style.yml is preferred over style.md when both exist."""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".code-to-docs"
        config_dir.mkdir()
        (config_dir / "style.yml").write_text("voice: active\n", encoding="utf-8")
        (config_dir / "style.md").write_text("Use passive voice.\n", encoding="utf-8")

        result = load_style_config()
        assert "active" in result

    def test_yaml_with_nested_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".code-to-docs"
        config_dir.mkdir()
        yaml_content = (
            "formatting:\n"
            "  headings: sentence_case\n"
            "  lists: use_dashes\n"
            "rules:\n"
            "  - No passive voice\n"
            "  - Keep paragraphs short\n"
        )
        (config_dir / "style.yml").write_text(yaml_content, encoding="utf-8")

        result = load_style_config()
        assert "headings" in result
        assert "sentence_case" in result
        assert "No passive voice" in result
        assert "Keep paragraphs short" in result

    def test_rejects_unsupported_extension(self, tmp_path, monkeypatch, capsys):
        """Only .yml, .yaml, and .md extensions are accepted for explicit paths."""
        monkeypatch.chdir(tmp_path)
        txt_file = tmp_path / "style.txt"
        txt_file.write_text("some content", encoding="utf-8")

        result = load_style_config(config_path=str(txt_file))
        assert result == ""
        captured = capsys.readouterr()
        assert ".yml, .yaml, or .md" in captured.out

    def test_large_config_truncated(self, tmp_path, monkeypatch, capsys):
        """Style configs exceeding the size cap are truncated."""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".code-to-docs"
        config_dir.mkdir()
        large_content = "# Style\n" + "x" * (_MAX_STYLE_CONFIG_CHARS + 1000)
        (config_dir / "style.md").write_text(large_content, encoding="utf-8")

        result = load_style_config()
        assert len(result) <= _MAX_STYLE_CONFIG_CHARS
        captured = capsys.readouterr()
        assert "truncated" in captured.out.lower()


# ── _yaml_to_guidelines ────────────────────────────────────────────────────


class TestYamlToGuidelines:
    def test_flat_keys(self):
        raw = "voice: active\ntone: professional\n"
        result = _yaml_to_guidelines(raw, "style.yml")
        assert "voice: active" in result
        assert "tone: professional" in result

    def test_nested_dict(self):
        raw = "formatting:\n  headings: title_case\n"
        result = _yaml_to_guidelines(raw, "style.yml")
        assert "formatting:" in result
        assert "headings: title_case" in result

    def test_list_values(self):
        raw = "rules:\n  - Be concise\n  - Use active voice\n"
        result = _yaml_to_guidelines(raw, "style.yml")
        assert "Be concise" in result
        assert "Use active voice" in result

    def test_returns_raw_on_invalid_yaml(self):
        raw = "this: is: not: valid: yaml: {{{"
        result = _yaml_to_guidelines(raw, "style.yml")
        # Should return raw content when parsing fails
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_raw_for_non_dict(self):
        raw = "just a plain string"
        result = _yaml_to_guidelines(raw, "style.yml")
        assert result == "just a plain string"

    def test_deeply_nested_dict(self):
        raw = "formatting:\n  headings:\n    style: sentence_case\n    capitalize: true\n"
        result = _yaml_to_guidelines(raw, "style.yml")
        assert "formatting:" in result
        assert "headings:" in result
        assert "style: sentence_case" in result
        assert "capitalize: True" in result


# ── prompt injection ────────────────────────────────────────────────────────


class TestStyleGuidelinesInPrompt:
    DIFF = "diff --git a/foo.py\n+added line"
    CONTENT = "Some documentation content"

    def test_style_guidelines_injected_in_prompt(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF, "docs/guide.md", self.CONTENT,
                style_guidelines="Use active voice. Keep paragraphs short.",
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "DOCUMENTATION STYLE GUIDELINES" in prompt
        assert "Use active voice" in prompt
        assert "Keep paragraphs short" in prompt

    def test_no_style_section_when_empty(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF, "docs/guide.md", self.CONTENT,
                style_guidelines="",
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "DOCUMENTATION STYLE GUIDELINES" not in prompt

    def test_style_guidelines_and_user_instructions_coexist(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF, "docs/guide.md", self.CONTENT,
                user_instructions="Focus on API changes",
                style_guidelines="Use active voice.",
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "DOCUMENTATION STYLE GUIDELINES" in prompt
        assert "Use active voice" in prompt
        assert "ADDITIONAL INSTRUCTIONS" in prompt
        assert "Focus on API changes" in prompt

    def test_default_behavior_unchanged_without_config(self):
        """When no style config is provided, prompt is identical to baseline."""
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF, "docs/guide.md", self.CONTENT,
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "DOCUMENTATION STYLE GUIDELINES" not in prompt
        assert "ADDITIONAL INSTRUCTIONS" not in prompt
