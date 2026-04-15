"""Tests for suggest_docs.py — main orchestrator covering all three command modes."""

import sys
from unittest.mock import patch, MagicMock

# Stub openai before any script imports config
sys.modules.setdefault("openai", MagicMock())

from suggest_docs import main


# ── helpers ──────────────────────────────────────────────────────────────────


def _mock_commit_info():
    return {"short_hash": "abc1234", "repo_url": "https://github.com/org/repo"}


# ── empty diff ───────────────────────────────────────────────────────────────


class TestMainEmptyDiff:
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="")
    def test_empty_diff_returns_early(self, mock_diff, mock_ci, monkeypatch):
        monkeypatch.setenv("COMMENT_BODY", "[review-docs]")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        with patch("suggest_docs.find_relevant_files_optimized") as mock_find:
            main()
            mock_find.assert_not_called()


# ── review mode ──────────────────────────────────────────────────────────────


class TestMainReviewMode:
    @patch("suggest_docs.post_review_comment")
    @patch("suggest_docs.generate_updates_parallel", return_value=[("guide.rst", "old", "new"), ("api.md", "old2", "new2")])
    @patch("suggest_docs.find_relevant_files_optimized", return_value=["guide.rst", "api.md"])
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    def test_review_discovers_and_posts_comment(
        self, mock_diff, mock_ci, mock_setup, mock_find, mock_gen, mock_post, monkeypatch
    ):
        monkeypatch.setenv("COMMENT_BODY", "[review-docs]")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        main()

        mock_find.assert_called_once()
        mock_gen.assert_called_once()
        mock_post.assert_called_once()
        # Check post_review_comment was called with the right files
        args, kwargs = mock_post.call_args
        assert len(args[0]) == 2
        assert args[1] == "42"


# ── update mode ──────────────────────────────────────────────────────────────


class TestMainUpdateMode:
    @patch("suggest_docs.run_command_safe")
    @patch("suggest_docs.push_and_open_pr")
    @patch("suggest_docs.overwrite_file", return_value=True)
    @patch("suggest_docs.generate_updates_parallel", return_value=[("guide.rst", "old content", "new content"), ("api.md", "old2", "new2")])
    @patch("suggest_docs.find_relevant_files_optimized", return_value=["guide.rst", "api.md"])
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch("suggest_docs.parse_previous_review", return_value={"review_found": False, "accepted_files": [], "rejected_files": []})
    @patch("suggest_docs.parse_update_instructions", return_value=("", {}))
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    def test_update_creates_pr(
        self, mock_diff, mock_ci, mock_parse_instr, mock_parse_rev,
        mock_setup, mock_find, mock_gen, mock_overwrite, mock_push, mock_cmd, monkeypatch
    ):
        monkeypatch.setenv("COMMENT_BODY", "[update-docs]")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        main()

        assert mock_overwrite.call_count == 2
        mock_push.assert_called_once()

    @patch("suggest_docs.run_command_safe")
    @patch("suggest_docs.push_and_open_pr")
    @patch("suggest_docs.overwrite_file", return_value=True)
    @patch("suggest_docs.generate_updates_parallel", return_value=[("guide.rst", "old", "new"), ("ref.adoc", "old2", "new2")])
    @patch("suggest_docs.find_relevant_files_optimized")
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch("suggest_docs.parse_previous_review", return_value={
        "review_found": True,
        "accepted_files": ["guide.rst", "ref.adoc"],
        "rejected_files": ["api.md"],
        "review_commit": "abc1234",
    })
    @patch("suggest_docs.parse_update_instructions", return_value=("", {}))
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    def test_previous_review_respected(
        self, mock_diff, mock_ci, mock_parse_instr, mock_parse_rev,
        mock_setup, mock_find, mock_gen, mock_overwrite, mock_push, mock_cmd, monkeypatch
    ):
        monkeypatch.setenv("COMMENT_BODY", "[update-docs]")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        main()

        # Should NOT call file discovery — uses previous review selections instead
        mock_find.assert_not_called()
        # Should generate updates for accepted files only
        mock_gen.assert_called_once()
        args, kwargs = mock_gen.call_args
        assert args[1] == ["guide.rst", "ref.adoc"]


# ── feature mode ─────────────────────────────────────────────────────────────


class TestMainFeatureMode:
    @patch("suggest_docs.run_command_safe")
    @patch("suggest_docs.parse_feature_command", return_value=(None, None))
    def test_missing_jira_key_posts_error(self, mock_parse, mock_cmd, monkeypatch):
        monkeypatch.setenv("COMMENT_BODY", "[review-feature]")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setenv("GH_TOKEN", "test-token")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        main()

        # Should post an error comment about missing key
        mock_cmd.assert_called()
        # The gh pr comment call should include the PR number
        gh_calls = [c for c in mock_cmd.call_args_list if "gh" in str(c)]
        assert len(gh_calls) > 0

    @patch("suggest_docs.run_command_safe")
    @patch("suggest_docs.parse_feature_command", return_value=("PROJ-123", ""))
    def test_missing_jira_credentials_posts_error(self, mock_parse, mock_cmd, monkeypatch):
        monkeypatch.setenv("COMMENT_BODY", "[review-feature] PROJ-123")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setenv("GH_TOKEN", "test-token")
        # Deliberately NOT setting JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        main()

        # Should post error about missing credentials
        mock_cmd.assert_called()
        gh_calls = [c for c in mock_cmd.call_args_list if "gh" in str(c)]
        assert len(gh_calls) > 0

    @patch("suggest_docs.post_review_comment")
    @patch("suggest_docs.find_relevant_files_optimized", return_value=[])
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch("suggest_docs.format_feature_review_section", return_value="## Feature Coverage\nAll covered")
    @patch("suggest_docs.analyze_feature_coverage", return_value="coverage analysis")
    @patch("suggest_docs.fetch_jira_context_sync", return_value={
        "error": None,
        "summary": "Implement widget",
        "spec_docs": [],
        "inaccessible_links": [],
    })
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    @patch("suggest_docs.parse_feature_command", return_value=("PROJ-123", ""))
    def test_feature_happy_path(
        self, mock_parse, mock_diff, mock_ci, mock_fetch, mock_analyze,
        mock_format, mock_setup, mock_find, mock_post, monkeypatch
    ):
        monkeypatch.setenv("COMMENT_BODY", "[review-feature] PROJ-123")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
        monkeypatch.setenv("JIRA_USERNAME", "user")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        # Patch get_client and get_model_name for analyze_feature_coverage
        with patch("suggest_docs.get_client") as mock_client, \
             patch("suggest_docs.get_model_name", return_value="test-model"):
            main()

        mock_fetch.assert_called_once_with("PROJ-123")
        mock_analyze.assert_called_once()
        mock_format.assert_called_once()
        # post_review_comment should be called with feature_section
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert "Feature Coverage" in kwargs.get("feature_section", "")


# ── dry run ──────────────────────────────────────────────────────────────────


class TestMainDryRun:
    @patch("suggest_docs.post_review_comment")
    @patch("suggest_docs.push_and_open_pr")
    @patch("suggest_docs.overwrite_file")
    @patch("suggest_docs.generate_updates_parallel", return_value=[("guide.rst", "old", "new"), ("api.md", "old2", "new2")])
    @patch("suggest_docs.find_relevant_files_optimized", return_value=["guide.rst", "api.md"])
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch("suggest_docs.parse_previous_review", return_value={"review_found": False, "accepted_files": [], "rejected_files": []})
    @patch("suggest_docs.parse_update_instructions", return_value=("", {}))
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    def test_dry_run_no_writes(
        self, mock_diff, mock_ci, mock_parse_instr, mock_parse_rev,
        mock_setup, mock_find, mock_gen, mock_overwrite, mock_push, mock_post, monkeypatch
    ):
        monkeypatch.setenv("COMMENT_BODY", "[update-docs]")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py", "--dry-run"])

        main()

        mock_overwrite.assert_not_called()
        mock_push.assert_not_called()
