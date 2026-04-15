"""Tests for comment parsing functions in suggest_docs.py and jira_integration.py."""

import json
import os
import re
import subprocess
import pytest
from unittest.mock import patch, MagicMock

# Import the functions under test — these live in src/ (added to path by conftest)
from comments import (
    parse_update_instructions,
    _resolve_file_instructions,
    parse_previous_review,
)
from jira_integration import parse_feature_command


# ── parse_update_instructions ────────────────────────────────────────────────


class TestParseUpdateInstructions:
    def test_global_instruction_only(self):
        comment = "[update-docs] focus on API changes only"
        global_inst, file_inst = parse_update_instructions(comment)
        assert global_inst == "focus on API changes only"
        assert file_inst == {}

    def test_per_file_instructions_only(self):
        comment = "[update-docs] pools.rst: only update set-quota\nhealth.md: skip intro"
        global_inst, file_inst = parse_update_instructions(comment)
        # First line matches file pattern, so no global instruction
        assert global_inst == ""
        assert file_inst == {
            "pools.rst": "only update set-quota",
            "health.md": "skip intro",
        }

    def test_global_plus_per_file(self):
        comment = "[update-docs] be conservative\npools.rst: only update set-quota"
        global_inst, file_inst = parse_update_instructions(comment)
        assert global_inst == "be conservative"
        assert file_inst == {"pools.rst": "only update set-quota"}

    def test_empty_after_command(self):
        comment = "[update-docs]"
        global_inst, file_inst = parse_update_instructions(comment)
        assert global_inst == ""
        assert file_inst == {}

    def test_no_command_present(self):
        comment = "just a regular comment"
        global_inst, file_inst = parse_update_instructions(comment)
        assert global_inst == ""
        assert file_inst == {}

    def test_case_insensitive(self):
        comment = "[UPDATE-DOCS] do something"
        global_inst, file_inst = parse_update_instructions(comment)
        assert global_inst == "do something"

    def test_adoc_file_pattern(self):
        comment = "[update-docs] guide.adoc: rewrite section 3"
        global_inst, file_inst = parse_update_instructions(comment)
        assert "guide.adoc" in file_inst

    def test_path_with_slashes(self):
        comment = "[update-docs] guides/operations/pools.rst: update quota section"
        global_inst, file_inst = parse_update_instructions(comment)
        assert "guides/operations/pools.rst" in file_inst

    def test_blank_lines_skipped(self):
        comment = "[update-docs] be careful\n\npools.rst: update it\n\nhealth.md: fix it"
        global_inst, file_inst = parse_update_instructions(comment)
        assert global_inst == "be careful"
        assert len(file_inst) == 2

    def test_preceding_text_ignored(self):
        comment = "Some preamble\n\n[update-docs] global instruction"
        global_inst, file_inst = parse_update_instructions(comment)
        assert global_inst == "global instruction"


# ── _resolve_file_instructions ───────────────────────────────────────────────


class TestResolveFileInstructions:
    def test_exact_match(self):
        instructions = {"guides/operations/pools.rst": "update quota"}
        result = _resolve_file_instructions("guides/operations/pools.rst", instructions)
        assert result == "update quota"

    def test_basename_match(self):
        instructions = {"pools.rst": "update quota"}
        result = _resolve_file_instructions("guides/operations/pools.rst", instructions)
        assert result == "update quota"

    def test_suffix_match(self):
        instructions = {"operations/pools.rst": "update quota"}
        result = _resolve_file_instructions("guides/operations/pools.rst", instructions)
        assert result == "update quota"

    def test_no_match(self):
        instructions = {"other.rst": "update it"}
        result = _resolve_file_instructions("guides/operations/pools.rst", instructions)
        assert result == ""

    def test_empty_instructions(self):
        result = _resolve_file_instructions("pools.rst", {})
        assert result == ""

    def test_none_instructions(self):
        result = _resolve_file_instructions("pools.rst", None)
        assert result == ""


# ── parse_previous_review ────────────────────────────────────────────────────


class TestParsePreviousReview:
    def _make_review_body(self, files_checked, files_unchecked, commit="abc1234"):
        """Build a realistic review comment body."""
        lines = [
            "## 📚 Documentation Review",
            "",
            "Select files to update, then comment `[update-docs]`:",
            "",
        ]
        for f in files_checked:
            lines.append(f"- [x] [{f}](https://github.com/org/repo/blob/main/{f}): Updated section")
        for f in files_unchecked:
            lines.append(f"- [ ] [{f}](https://github.com/org/repo/blob/main/{f}): Updated section")
        lines.append("")
        lines.append(f"Latest commit: `{commit}`")
        return "\n".join(lines)

    def test_no_gh_token_returns_empty(self, monkeypatch):
        # GH_TOKEN is cleared by clean_env fixture
        result = parse_previous_review("42")
        assert result["review_found"] is False

    def test_unknown_pr_returns_empty(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "tok")
        result = parse_previous_review("unknown")
        assert result["review_found"] is False

    def test_parses_checked_and_unchecked(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "tok")
        body = self._make_review_body(
            files_checked=["guide.md", "api.rst"],
            files_unchecked=["old.adoc"],
        )
        gh_response = json.dumps({"comments": [{"body": body}]})

        with patch("comments.run_command_safe") as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = gh_response
            mock_cmd.return_value = mock_result

            result = parse_previous_review("42")

        assert result["review_found"] is True
        assert result["accepted_files"] == ["guide.md", "api.rst"]
        assert result["rejected_files"] == ["old.adoc"]
        assert result["review_commit"] == "abc1234"

    def test_no_review_comment_found(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "tok")
        gh_response = json.dumps({"comments": [{"body": "just a regular comment"}]})

        with patch("comments.run_command_safe") as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = gh_response
            mock_cmd.return_value = mock_result

            result = parse_previous_review("42")

        assert result["review_found"] is False
        assert result["accepted_files"] == []

    def test_all_unchecked(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "tok")
        body = self._make_review_body(
            files_checked=[],
            files_unchecked=["a.md", "b.rst"],
        )
        gh_response = json.dumps({"comments": [{"body": body}]})

        with patch("comments.run_command_safe") as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = gh_response
            mock_cmd.return_value = mock_result

            result = parse_previous_review("42")

        assert result["review_found"] is True
        assert result["accepted_files"] == []
        assert len(result["rejected_files"]) == 2

    def test_bold_path_format(self, monkeypatch):
        """Test the **path** format (alternative to [path](url))."""
        monkeypatch.setenv("GH_TOKEN", "tok")
        body = (
            "## 📚 Documentation Review\n\n"
            "Select files to update, then comment `[update-docs]`:\n\n"
            "- [x] **guide.md**: Updated\n"
            "- [ ] **old.adoc**: Updated\n\n"
            "Latest commit: `def5678`"
        )
        gh_response = json.dumps({"comments": [{"body": body}]})

        with patch("comments.run_command_safe") as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = gh_response
            mock_cmd.return_value = mock_result

            result = parse_previous_review("42")

        assert result["review_found"] is True
        assert result["accepted_files"] == ["guide.md"]
        assert result["rejected_files"] == ["old.adoc"]
        assert result["review_commit"] == "def5678"

    def test_gh_command_failure_returns_empty(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "tok")
        with patch("comments.run_command_safe") as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "not found"
            mock_cmd.return_value = mock_result

            result = parse_previous_review("42")

        assert result["review_found"] is False

    def test_picks_most_recent_review(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "tok")
        old_body = self._make_review_body(
            files_checked=["old.md"], files_unchecked=[], commit="aaa1111"
        )
        new_body = self._make_review_body(
            files_checked=["new.md"], files_unchecked=[], commit="bbb2222"
        )
        gh_response = json.dumps({
            "comments": [
                {"body": old_body},
                {"body": "unrelated comment"},
                {"body": new_body},
            ]
        })

        with patch("comments.run_command_safe") as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = gh_response
            mock_cmd.return_value = mock_result

            result = parse_previous_review("42")

        assert result["accepted_files"] == ["new.md"]
        assert result["review_commit"] == "bbb2222"


# ── parse_feature_command ────────────────────────────────────────────────────


class TestParseFeatureCommand:
    def test_simple_key(self):
        key, instructions = parse_feature_command("[review-feature] PROJ-123")
        assert key == "PROJ-123"
        assert instructions == ""

    def test_key_with_instructions(self):
        key, instructions = parse_feature_command(
            "[review-feature] PROJ-456 focus on auth changes"
        )
        assert key == "PROJ-456"
        assert instructions == "focus on auth changes"

    def test_case_insensitive(self):
        key, instructions = parse_feature_command("[Review-Feature] PROJ-789")
        assert key == "PROJ-789"

    def test_no_key_returns_none(self):
        key, instructions = parse_feature_command("[review-feature]")
        assert key is None
        assert instructions is None

    def test_invalid_key_format(self):
        key, instructions = parse_feature_command("[review-feature] invalid")
        assert key is None

    def test_no_command_returns_none(self):
        key, instructions = parse_feature_command("just a comment")
        assert key is None

    def test_key_uppercased(self):
        key, _ = parse_feature_command("[review-feature] proj-123")
        assert key == "PROJ-123"

    def test_multiline_instructions(self):
        key, instructions = parse_feature_command(
            "[review-feature] PROJ-100 first line\nsecond line"
        )
        assert key == "PROJ-100"
        assert "first line" in instructions
        assert "second line" in instructions

    def test_surrounded_by_other_text(self):
        key, _ = parse_feature_command(
            "Some preamble\n[review-feature] DATA-42\nmore text"
        )
        assert key == "DATA-42"
