"""Tests for truncation helpers in config.py."""

import os
import pytest
from unittest.mock import MagicMock

from config import get_max_context_chars, truncate_content, truncate_diff, check_context_error


# =============================================================================
# get_max_context_chars
# =============================================================================


class TestGetMaxContextChars:
    def test_returns_default_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("MAX_CONTEXT_CHARS", raising=False)
        assert get_max_context_chars() == 400_000

    def test_reads_from_env_var(self, monkeypatch):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "50000")
        assert get_max_context_chars() == 50_000

    def test_falls_back_on_invalid_value(self, monkeypatch):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "not_a_number")
        assert get_max_context_chars() == 400_000

    def test_warns_on_invalid_value(self, monkeypatch, capsys):
        monkeypatch.setenv("MAX_CONTEXT_CHARS", "not_a_number")
        get_max_context_chars()
        output = capsys.readouterr().out
        assert "Warning" in output
        assert "not_a_number" in output


# =============================================================================
# truncate_content
# =============================================================================


class TestTruncateContent:
    def test_no_op_when_under_limit(self):
        text = "short text"
        result = truncate_content(text, 100)
        assert result == text

    def test_no_op_when_exactly_at_limit(self):
        text = "x" * 100
        result = truncate_content(text, 100)
        assert result == text

    def test_truncates_when_over_limit(self):
        text = "x" * 200
        result = truncate_content(text, 50)
        assert result.startswith("x" * 50)
        assert "[... truncated:" in result
        assert "50" in result
        assert "200" in result

    def test_empty_text_returns_unchanged(self):
        assert truncate_content("", 100) == ""
        assert truncate_content(None, 100) is None

    def test_label_appears_in_warning(self, capsys):
        text = "x" * 200
        truncate_content(text, 50, label="my-file.rst")
        output = capsys.readouterr().out
        assert "my-file.rst" in output

    def test_percentage_in_warning(self, capsys):
        text = "x" * 1000
        truncate_content(text, 250, label="test")
        output = capsys.readouterr().out
        assert "25%" in output


# =============================================================================
# truncate_diff
# =============================================================================

SAMPLE_DIFF = """diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,4 @@
 line1
+added_line
 line2
 line3
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1,2 +1,3 @@
 alpha
+beta
 gamma
diff --git a/file3.py b/file3.py
--- a/file3.py
+++ b/file3.py
@@ -1 +1,2 @@
 only
+more
"""


class TestTruncateDiff:
    def test_no_op_when_under_limit(self):
        result = truncate_diff(SAMPLE_DIFF, 10000)
        assert result == SAMPLE_DIFF

    def test_preserves_complete_file_diffs(self):
        # Total diff is ~300 chars. Set budget less than total so truncation kicks in,
        # but enough to fit first two file-diffs (~210 chars) plus the suffix (~90 chars).
        budget = len(SAMPLE_DIFF) - 1  # just under total → forces truncation
        result = truncate_diff(SAMPLE_DIFF, budget)
        assert "file1.py" in result
        assert "file2.py" in result
        assert "showing 2/3 changed files" in result

    def test_falls_back_to_char_truncation_when_first_file_too_large(self):
        # Budget smaller than first file-diff
        result = truncate_diff(SAMPLE_DIFF, 50)
        assert "[... truncated:" in result
        assert "0/3 complete files" in result

    def test_empty_diff_returns_unchanged(self):
        assert truncate_diff("", 100) == ""
        assert truncate_diff(None, 100) is None

    def test_diff_without_git_markers(self):
        plain_text = "some random text that is not a diff"
        result = truncate_diff(plain_text, 10)
        assert "[... truncated:" in result

    def test_correct_file_count(self):
        result = truncate_diff(SAMPLE_DIFF, 10000)
        # No truncation needed, so no suffix
        assert "[... truncated:" not in result

    def test_warning_printed(self, capsys):
        truncate_diff(SAMPLE_DIFF, 50, label="test-diff")
        output = capsys.readouterr().out
        assert "test-diff" in output
        assert "Warning:" in output

    def test_single_file_diff(self):
        single = """diff --git a/only.py b/only.py
--- a/only.py
+++ b/only.py
@@ -1 +1,2 @@
 one
+two
"""
        # Budget larger than the diff
        result = truncate_diff(single, 10000)
        assert result == single

        # Budget smaller than the diff
        result = truncate_diff(single, 30)
        assert "[... truncated:" in result


# =============================================================================
# check_context_error
# =============================================================================


class TestCheckContextError:
    def _make_bad_request_error(self, message):
        """Create a mock openai.BadRequestError with the given message."""
        import openai
        err = openai.BadRequestError(
            message=message,
            response=MagicMock(status_code=400),
            body={"error": {"message": message}},
        )
        return err

    def test_detects_context_length_error(self, capsys):
        err = self._make_bad_request_error(
            "This model's maximum context length is 4097 tokens."
        )
        assert check_context_error(err) is True
        output = capsys.readouterr().out
        assert "MAX_CONTEXT_CHARS" in output

    def test_detects_token_limit_error(self, capsys):
        err = self._make_bad_request_error(
            "The input token count exceeds the maximum number of tokens allowed."
        )
        assert check_context_error(err) is True

    def test_returns_false_for_unrelated_bad_request(self):
        err = self._make_bad_request_error("Invalid model name")
        assert check_context_error(err) is False

    def test_returns_false_for_non_bad_request(self):
        err = ValueError("some other error")
        assert check_context_error(err) is False

    def test_does_not_raise(self):
        err = self._make_bad_request_error(
            "maximum context length exceeded"
        )
        # Should return True without raising
        result = check_context_error(err)
        assert result is True
