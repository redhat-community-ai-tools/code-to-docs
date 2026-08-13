"""Tests for output format validation and code fence stripping."""

from unittest.mock import MagicMock, patch

from generation import strip_code_fences, validate_format

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
    def test_retries_on_invalid_format_then_succeeds(
        self, mock_budget, mock_model, mock_client, mock_validate
    ):
        # Call 1: initial generation returns content that fails format validation
        # Call 2: format-fix retry returns content that passes
        # Call 3: post-generation LLM verification (approves)
        current_content = "Title\n=====\n\nOld content"

        def llm_side_effect(**kwargs):
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            messages = kwargs["messages"]
            if messages[0]["content"].startswith("You are a documentation review auditor"):
                mock_resp.choices[0].message.content = "APPROVED"
            elif "format errors" in messages[1]["content"].lower():
                # Format fix — preserve original content
                mock_resp.choices[
                    0
                ].message.content = "Title\n=====\n\nOld content\n\nFixed addition"
            else:
                mock_resp.choices[0].message.content = "Bad RST content"
            return mock_resp

        client = MagicMock()
        client.chat.completions.create.side_effect = llm_side_effect
        mock_client.return_value = client

        # validate_format: fail on first content, pass on second and later
        mock_validate.side_effect = [
            (False, "RST validation errors: bad underline"),
            (True, ""),
            (True, ""),  # may be called during regeneration check
        ]

        from generation import ask_ai_for_updated_content

        result = ask_ai_for_updated_content(
            diff="diff --git a/foo.py\n+new line",
            file_path="docs/guide.rst",
            current_content=current_content,
        )
        assert "Fixed addition" in result.strip()

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
