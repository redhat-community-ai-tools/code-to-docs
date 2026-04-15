"""Tests for github_ops.py — PR diffs, commit info, and docs environment setup."""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# config.py imports openai at module level; stub it out so tests don't
# require the openai package to be installed.
sys.modules.setdefault("openai", MagicMock())

from github_ops import get_diff, get_commit_info, setup_docs_environment


def _mock_cmd_result(stdout="", returncode=0, stderr=""):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


# -- get_diff -----------------------------------------------------------------


class TestGetDiff:
    def test_returns_diff_content(self, monkeypatch):
        """Happy path: merge-base succeeds, diff content is returned."""
        monkeypatch.setenv("PR_BASE", "origin/main")
        monkeypatch.setenv("PR_NUMBER", "42")

        merge_base_result = _mock_cmd_result(stdout="abc1234def5678\n")
        files_result = _mock_cmd_result(stdout="file_a.py\nfile_b.py\n")
        diff_result = _mock_cmd_result(stdout="diff --git a/file_a.py b/file_a.py\n+new line\n")

        with patch("github_ops.run_command_safe") as mock_cmd:
            mock_cmd.side_effect = [merge_base_result, files_result, diff_result]
            result = get_diff()

        assert "diff --git" in result
        assert "+new line" in result

    def test_fallback_when_merge_base_fails(self, monkeypatch):
        """When merge-base fails, falls back to direct pr_base...HEAD diff."""
        monkeypatch.setenv("PR_BASE", "origin/main")
        monkeypatch.setenv("PR_NUMBER", "99")

        merge_base_fail = _mock_cmd_result(returncode=1, stderr="fatal: not a git repo")
        fallback_diff = _mock_cmd_result(stdout="fallback diff content")

        with patch("github_ops.run_command_safe") as mock_cmd:
            mock_cmd.side_effect = [merge_base_fail, fallback_diff]
            result = get_diff()

        assert result == "fallback diff content"
        # Verify fallback call used pr_base...HEAD
        fallback_call = mock_cmd.call_args_list[1]
        assert "origin/main...HEAD" in fallback_call[0][0]

    def test_returns_empty_on_error(self, monkeypatch):
        """When an exception is raised, returns empty string."""
        monkeypatch.setenv("PR_BASE", "origin/main")
        monkeypatch.setenv("PR_NUMBER", "1")

        with patch("github_ops.run_command_safe") as mock_cmd:
            mock_cmd.side_effect = RuntimeError("unexpected failure")
            result = get_diff()

        assert result == ""


# -- get_commit_info ----------------------------------------------------------


class TestGetCommitInfo:
    def test_returns_commit_info_with_pr(self, monkeypatch):
        """When PR_NUMBER is set, result includes pr_number and pr_url."""
        monkeypatch.setenv("PR_NUMBER", "55")

        rev_parse_result = _mock_cmd_result(stdout="aabbccdd11223344556677889900aabbccddeeff\n")
        remote_url_result = _mock_cmd_result(stdout="https://github.com/org/repo.git\n")

        with patch("github_ops.run_command_safe") as mock_cmd:
            mock_cmd.side_effect = [rev_parse_result, remote_url_result]
            info = get_commit_info()

        assert info is not None
        assert info["pr_number"] == "55"
        assert info["pr_url"] == "https://github.com/org/repo/pull/55"
        assert info["short_hash"] == "aabbccd"
        assert info["current_commit"] == "aabbccdd11223344556677889900aabbccddeeff"
        assert info["repo_url"] == "https://github.com/org/repo"

    def test_returns_commit_info_without_pr(self, monkeypatch):
        """When PR_NUMBER is not set, result has no pr_number key."""
        # PR_NUMBER is already cleared by clean_env fixture

        rev_parse_result = _mock_cmd_result(stdout="aabbccdd11223344556677889900aabbccddeeff\n")
        remote_url_result = _mock_cmd_result(stdout="https://github.com/org/repo\n")

        with patch("github_ops.run_command_safe") as mock_cmd:
            mock_cmd.side_effect = [rev_parse_result, remote_url_result]
            info = get_commit_info()

        assert info is not None
        assert "pr_number" not in info
        assert "pr_url" not in info

    def test_converts_ssh_to_https(self, monkeypatch):
        """SSH remote URL (git@github.com:org/repo.git) is converted to HTTPS."""
        monkeypatch.setenv("PR_NUMBER", "10")

        rev_parse_result = _mock_cmd_result(stdout="1234567890abcdef1234567890abcdef12345678\n")
        remote_url_result = _mock_cmd_result(stdout="git@github.com:myorg/myrepo.git\n")

        with patch("github_ops.run_command_safe") as mock_cmd:
            mock_cmd.side_effect = [rev_parse_result, remote_url_result]
            info = get_commit_info()

        assert info is not None
        assert info["repo_url"] == "https://github.com/myorg/myrepo"
        assert info["pr_url"] == "https://github.com/myorg/myrepo/pull/10"

    def test_returns_none_on_rev_parse_failure(self, monkeypatch):
        """When rev-parse returns non-zero, get_commit_info returns None."""
        monkeypatch.setenv("PR_NUMBER", "7")

        rev_parse_fail = _mock_cmd_result(returncode=128, stderr="fatal: not a git repo")

        with patch("github_ops.run_command_safe") as mock_cmd:
            mock_cmd.side_effect = [rev_parse_fail]
            info = get_commit_info()

        assert info is None


# -- setup_docs_environment ---------------------------------------------------


class TestSetupDocsEnvironment:
    def test_subfolder_mode(self, monkeypatch, tmp_path):
        """When DOCS_SUBFOLDER points to an existing dir, chdir into it and return True."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.chdir(tmp_path)

        result = setup_docs_environment()

        assert result is True
        assert os.path.basename(os.getcwd()) == "docs"

    def test_subfolder_rejects_traversal(self, monkeypatch, tmp_path):
        """Path traversal in DOCS_SUBFOLDER is rejected (returns False)."""
        monkeypatch.setenv("DOCS_SUBFOLDER", "../etc")
        monkeypatch.chdir(tmp_path)

        result = setup_docs_environment()

        assert result is False

    def test_subfolder_missing_returns_false(self, monkeypatch, tmp_path):
        """When DOCS_SUBFOLDER points to a nonexistent dir, returns False."""
        monkeypatch.setenv("DOCS_SUBFOLDER", "nonexistent")
        monkeypatch.chdir(tmp_path)

        result = setup_docs_environment()

        assert result is False
