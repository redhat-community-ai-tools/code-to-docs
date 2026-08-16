"""Tests for suggest_docs.py — main orchestrator covering all three command modes."""

import sys
from unittest.mock import MagicMock, patch

# Stub openai before any script imports config
sys.modules.setdefault("openai", MagicMock())

# ── load_style_config_from_branch ───────────────────────────────────────────
from config import load_style_config_from_branch
from generation import GenerationResult
from suggest_docs import (
    _get_pr_description,
    _normalize_github_url,
    _push_docs_pr_for_merged,
    _resolve_pr_push_target,
    main,
)


def _gr(content):
    """Shorthand to wrap content in a GenerationResult with 'skipped' status."""
    return GenerationResult(content, "skipped", "")


class TestLoadStyleConfigFromBranch:
    def test_loads_style_from_main(self):
        with patch("config.run_command_safe") as mock_run:
            fetch_result = MagicMock(returncode=0, stdout="")
            show_result = MagicMock(
                returncode=0, stdout="# Style Rules\nAlways include examples.\n"
            )
            mock_run.side_effect = [fetch_result, show_result]
            result = load_style_config_from_branch()
        assert "Always include examples" in result
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["git", "fetch", "origin", "main"] == calls[0]
        assert "git" == calls[1][0] and "show" == calls[1][1]

    def test_returns_empty_when_file_missing(self):
        with patch("config.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert load_style_config_from_branch() == ""

    def test_returns_empty_when_file_empty(self):
        with patch("config.run_command_safe") as mock_run:
            fetch_result = MagicMock(returncode=0, stdout="")
            show_result = MagicMock(returncode=0, stdout="   ")
            mock_run.side_effect = [fetch_result, show_result]
            assert load_style_config_from_branch() == ""

    def test_uses_custom_path(self, monkeypatch):
        monkeypatch.setenv("STYLE_CONFIG_PATH", "custom/style.md")
        with patch("config.run_command_safe") as mock_run:
            fetch_result = MagicMock(returncode=0, stdout="")
            show_result = MagicMock(returncode=0, stdout="Custom rules")
            mock_run.side_effect = [fetch_result, show_result]
            result = load_style_config_from_branch()
        assert result == "Custom rules"
        show_cmd = mock_run.call_args_list[1].args[0]
        assert "custom/style.md" in show_cmd[2]

    def test_uses_custom_base_branch(self, monkeypatch):
        monkeypatch.setenv("DOCS_BASE_BRANCH", "develop")
        with patch("config.run_command_safe") as mock_run:
            fetch_result = MagicMock(returncode=0, stdout="")
            show_result = MagicMock(returncode=0, stdout="Rules")
            mock_run.side_effect = [fetch_result, show_result]
            load_style_config_from_branch()
        fetch_cmd = mock_run.call_args_list[0].args[0]
        assert ["git", "fetch", "origin", "develop"] == fetch_cmd
        show_cmd = mock_run.call_args_list[1].args[0]
        assert "origin/develop:" in show_cmd[2]

    def test_empty_base_branch_falls_back_to_main(self, monkeypatch):
        monkeypatch.setenv("DOCS_BASE_BRANCH", "")
        with patch("config.run_command_safe") as mock_run:
            fetch_result = MagicMock(returncode=0, stdout="")
            show_result = MagicMock(returncode=0, stdout="Rules")
            mock_run.side_effect = [fetch_result, show_result]
            load_style_config_from_branch()
        fetch_cmd = mock_run.call_args_list[0].args[0]
        assert ["git", "fetch", "origin", "main"] == fetch_cmd

    def test_rejects_non_md_path(self, monkeypatch):
        monkeypatch.setenv("STYLE_CONFIG_PATH", "style.txt")
        with patch("config.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="should not be read")
            assert load_style_config_from_branch() == ""
        assert mock_run.call_count == 1

    def test_rejects_path_traversal(self, monkeypatch):
        monkeypatch.setenv("STYLE_CONFIG_PATH", "../../../etc/passwd.md")
        with patch("config.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="should not be read")
            assert load_style_config_from_branch() == ""
        assert mock_run.call_count == 1


# ── _get_pr_description ──────────────────────────────────────────────────────


class TestGetPrDescription:
    def test_returns_empty_without_pr_number(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        assert _get_pr_description(None) == ""
        assert _get_pr_description("") == ""
        assert _get_pr_description("unknown") == ""

    def test_returns_empty_without_gh_token(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert _get_pr_description("42") == ""

    def test_returns_title_and_body(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Add new feature\nThis PR adds a new capability."
            )
            result = _get_pr_description("42")
        assert "Add new feature" in result
        assert "new capability" in result

    def test_returns_empty_on_command_failure(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert _get_pr_description("42") == ""

    def test_returns_empty_on_empty_output(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="   ")
            assert _get_pr_description("42") == ""


# ── _normalize_github_url ────────────────────────────────────────────────────


class TestNormalizeGithubUrl:
    def test_https_with_git_suffix(self):
        assert (
            _normalize_github_url("https://github.com/org/repo.git")
            == "https://github.com/org/repo"
        )

    def test_https_without_git_suffix(self):
        assert _normalize_github_url("https://github.com/org/repo") == "https://github.com/org/repo"

    def test_ssh_shorthand(self):
        assert _normalize_github_url("git@github.com:org/repo.git") == "https://github.com/org/repo"

    def test_ssh_protocol(self):
        assert (
            _normalize_github_url("ssh://git@github.com/org/repo.git")
            == "https://github.com/org/repo"
        )

    def test_trailing_slash(self):
        assert (
            _normalize_github_url("https://github.com/org/repo/") == "https://github.com/org/repo"
        )

    def test_whitespace(self):
        assert (
            _normalize_github_url("  https://github.com/org/repo  ")
            == "https://github.com/org/repo"
        )


# ── _resolve_pr_push_target ─────────────────────────────────────────────────


class TestResolvePrPushTarget:
    def test_returns_none_without_pr_number(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        assert _resolve_pr_push_target(None) == (None, None, False)
        assert _resolve_pr_push_target("") == (None, None, False)
        assert _resolve_pr_push_target("unknown") == (None, None, False)

    def test_returns_none_without_gh_token(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert _resolve_pr_push_target("42") == (None, None, False)

    def test_returns_none_on_command_failure(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert _resolve_pr_push_target("42") == (None, None, False)

    def test_returns_none_on_null_owner_repo(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="my-branch\tnull\tnull\tOPEN")
            assert _resolve_pr_push_target("42") == (None, None, False)

    def test_returns_none_on_null_branch(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="null\towner\trepo\tOPEN")
            assert _resolve_pr_push_target("42") == (None, None, False)

    def test_returns_none_on_invalid_owner(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="branch\tbad owner!\trepo\tOPEN")
            assert _resolve_pr_push_target("42") == (None, None, False)

    def test_returns_none_on_malformed_output(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="only-one-field")
            assert _resolve_pr_push_target("42") == (None, None, False)

    def test_returns_branch_and_url_on_success(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="fix/my-branch\tfork-owner\tmy-project\tOPEN"
            )
            branch, url, merged = _resolve_pr_push_target("42")
        assert branch == "fix/my-branch"
        assert url == "https://github.com/fork-owner/my-project.git"
        assert merged is False

    def test_detects_merged_pr(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="fix/my-branch\towner\trepo\tMERGED"
            )
            branch, url, merged = _resolve_pr_push_target("42")
        assert branch == "fix/my-branch"
        assert merged is True

    def test_branch_with_slashes(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test")
        with patch("suggest_docs.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="feature/deep/nested/branch\towner\trepo\tOPEN"
            )
            branch, _, merged = _resolve_pr_push_target("42")
        assert branch == "feature/deep/nested/branch"
        assert merged is False


# ── _push_docs_pr_for_merged ─────────────────────────────────────────────────


class TestPushDocsPrForMerged:
    def _mock_run(
        self, push_ok=True, pr_list_stdout="[]", create_stdout="https://github.com/org/repo/pull/99"
    ):
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock(returncode=0, stdout="")
            if "push" in cmd:
                if not push_ok:
                    import subprocess

                    raise subprocess.CalledProcessError(1, cmd)
            elif cmd[:3] == ["gh", "pr", "list"]:
                result.stdout = pr_list_stdout
            elif cmd[:3] == ["gh", "pr", "create"]:
                result.stdout = create_stdout
            elif cmd[:3] == ["gh", "pr", "edit"]:
                pass
            return result

        return side_effect, calls

    def test_creates_new_pr_returns_url(self):
        side_effect, calls = self._mock_run()
        with patch("suggest_docs.run_command_safe", side_effect=side_effect):
            url = _push_docs_pr_for_merged(
                "42", "docs/update-from-pr-42", ["docs/guide.md"], "token"
            )
        assert url == "https://github.com/org/repo/pull/99"
        assert any("pr" in str(c) and "create" in str(c) for c in calls)

    def test_updates_existing_pr_returns_url(self):
        pr_list = '[{"number": 55, "url": "https://github.com/org/repo/pull/55"}]'
        side_effect, calls = self._mock_run(pr_list_stdout=pr_list)
        with patch("suggest_docs.run_command_safe", side_effect=side_effect):
            url = _push_docs_pr_for_merged(
                "42", "docs/update-from-pr-42", ["docs/guide.md"], "token"
            )
        assert url == "https://github.com/org/repo/pull/55"
        assert any("edit" in str(c) for c in calls)

    def test_push_failure_returns_none(self):
        side_effect, _ = self._mock_run(push_ok=False)
        with patch("suggest_docs.run_command_safe", side_effect=side_effect):
            result = _push_docs_pr_for_merged(
                "42", "docs/update-from-pr-42", ["docs/guide.md"], "token"
            )
        assert result is None

    def test_existing_pr_bad_json_returns_none(self):
        side_effect, _ = self._mock_run(pr_list_stdout="not-json")
        with patch("suggest_docs.run_command_safe", side_effect=side_effect):
            result = _push_docs_pr_for_merged(
                "42", "docs/update-from-pr-42", ["docs/guide.md"], "token"
            )
        assert result is None

    def test_create_empty_stdout_returns_empty(self):
        side_effect, _ = self._mock_run(create_stdout="")
        with patch("suggest_docs.run_command_safe", side_effect=side_effect):
            url = _push_docs_pr_for_merged(
                "42", "docs/update-from-pr-42", ["docs/guide.md"], "token"
            )
        assert url == ""


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
    @patch(
        "suggest_docs.generate_updates_parallel",
        return_value=[("guide.rst", "old", _gr("new")), ("api.md", "old2", _gr("new2"))],
    )
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
    @patch(
        "suggest_docs.generate_updates_parallel",
        return_value=[
            ("guide.rst", "old content", _gr("new content")),
            ("api.md", "old2", _gr("new2")),
        ],
    )
    @patch("suggest_docs.find_relevant_files_optimized", return_value=["guide.rst", "api.md"])
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch(
        "suggest_docs.parse_previous_review",
        return_value={"review_found": False, "accepted_files": [], "rejected_files": []},
    )
    @patch("suggest_docs.parse_update_instructions", return_value=("", {}))
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    def test_update_creates_pr(
        self,
        mock_diff,
        mock_ci,
        mock_parse_instr,
        mock_parse_rev,
        mock_setup,
        mock_find,
        mock_gen,
        mock_overwrite,
        mock_push,
        mock_cmd,
        monkeypatch,
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
    @patch(
        "suggest_docs.generate_updates_parallel",
        return_value=[("guide.rst", "old", _gr("new")), ("ref.adoc", "old2", _gr("new2"))],
    )
    @patch("suggest_docs.find_relevant_files_optimized")
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch(
        "suggest_docs.parse_previous_review",
        return_value={
            "review_found": True,
            "accepted_files": ["guide.rst", "ref.adoc"],
            "rejected_files": ["api.md"],
            "review_commit": "abc1234",
        },
    )
    @patch("suggest_docs.parse_update_instructions", return_value=("", {}))
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    def test_previous_review_respected(
        self,
        mock_diff,
        mock_ci,
        mock_parse_instr,
        mock_parse_rev,
        mock_setup,
        mock_find,
        mock_gen,
        mock_overwrite,
        mock_push,
        mock_cmd,
        monkeypatch,
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


# ── update mode (merged PR, same-repo) ──────────────────────────────────────


class TestMainUpdateModeMergedPr:
    @patch("suggest_docs.os.chdir")
    @patch("suggest_docs.run_command_safe")
    @patch(
        "suggest_docs._push_docs_pr_for_merged",
        return_value="https://github.com/org/repo/pull/99",
    )
    @patch("suggest_docs.overwrite_file", return_value=True)
    @patch(
        "suggest_docs.generate_updates_parallel",
        return_value=[("guide.md", "old", _gr("new")), ("api.md", "old2", _gr("new2"))],
    )
    @patch("suggest_docs.find_relevant_files_optimized", return_value=["guide.md", "api.md"])
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch(
        "suggest_docs.parse_previous_review",
        return_value={"review_found": False, "accepted_files": [], "rejected_files": []},
    )
    @patch("suggest_docs.parse_update_instructions", return_value=("", {}))
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    @patch(
        "suggest_docs._resolve_pr_push_target",
        return_value=("main-branch", "https://github.com/org/repo.git", True),
    )
    @patch("suggest_docs.checkout_docs_from_base_branch")
    def test_merged_pr_creates_docs_pr(
        self,
        mock_checkout_base,
        mock_resolve,
        mock_diff,
        mock_ci,
        mock_parse_instr,
        mock_parse_rev,
        mock_setup,
        mock_find,
        mock_gen,
        mock_overwrite,
        mock_push_merged,
        mock_cmd,
        mock_chdir,
        monkeypatch,
    ):
        monkeypatch.setenv("COMMENT_BODY", "[update-docs]")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.setenv("GH_TOKEN", "test-token")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        main()

        mock_push_merged.assert_called_once()
        args = mock_push_merged.call_args.args
        assert args[0] == "42"
        assert args[1] == "docs/update-from-pr-42"
        mock_checkout_base.assert_not_called()

    @patch("suggest_docs.os.chdir")
    @patch("suggest_docs.run_command_safe")
    @patch("suggest_docs.checkout_docs_from_base_branch")
    @patch("suggest_docs.find_relevant_files_optimized", return_value=[])
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch(
        "suggest_docs.parse_previous_review",
        return_value={"review_found": False, "accepted_files": [], "rejected_files": []},
    )
    @patch("suggest_docs.parse_update_instructions", return_value=("", {}))
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    def test_branch_switch_failure_falls_back(
        self,
        mock_diff,
        mock_ci,
        mock_parse_instr,
        mock_parse_rev,
        mock_setup,
        mock_find,
        mock_checkout_base,
        mock_cmd,
        mock_chdir,
        monkeypatch,
    ):
        import subprocess as _subprocess

        monkeypatch.setenv("COMMENT_BODY", "[update-docs]")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.setenv("GH_TOKEN", "test-token")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        def resolve_merged(*args, **kwargs):
            return ("branch", "https://github.com/org/repo.git", True)

        monkeypatch.setattr("suggest_docs._resolve_pr_push_target", resolve_merged)

        def cmd_side_effect(cmd, **kwargs):
            if "checkout" in cmd:
                raise _subprocess.CalledProcessError(1, cmd)
            return MagicMock(returncode=0, stdout="")

        mock_cmd.side_effect = cmd_side_effect

        main()

        mock_checkout_base.assert_called_once()


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
    @patch(
        "suggest_docs.format_feature_review_section",
        return_value="## Feature Coverage\nAll covered",
    )
    @patch("suggest_docs.analyze_feature_coverage", return_value="coverage analysis")
    @patch(
        "suggest_docs.fetch_jira_context_sync",
        return_value={
            "error": None,
            "summary": "Implement widget",
            "spec_docs": [],
            "inaccessible_links": [],
        },
    )
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    @patch("suggest_docs.parse_feature_command", return_value=("PROJ-123", ""))
    def test_feature_happy_path(
        self,
        mock_parse,
        mock_diff,
        mock_ci,
        mock_fetch,
        mock_analyze,
        mock_format,
        mock_setup,
        mock_find,
        mock_post,
        monkeypatch,
    ):
        monkeypatch.setenv("COMMENT_BODY", "[review-feature] PROJ-123")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
        monkeypatch.setenv("JIRA_USERNAME", "user")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py"])

        # Patch get_client and get_model_name for analyze_feature_coverage
        with (
            patch("suggest_docs.get_client"),
            patch("suggest_docs.get_model_name", return_value="test-model"),
        ):
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
    @patch(
        "suggest_docs.generate_updates_parallel",
        return_value=[("guide.rst", "old", _gr("new")), ("api.md", "old2", _gr("new2"))],
    )
    @patch("suggest_docs.find_relevant_files_optimized", return_value=["guide.rst", "api.md"])
    @patch("suggest_docs.setup_docs_environment", return_value=True)
    @patch(
        "suggest_docs.parse_previous_review",
        return_value={"review_found": False, "accepted_files": [], "rejected_files": []},
    )
    @patch("suggest_docs.parse_update_instructions", return_value=("", {}))
    @patch("suggest_docs.get_commit_info", return_value=_mock_commit_info())
    @patch("suggest_docs.get_diff", return_value="diff --git a/foo.py b/foo.py")
    def test_dry_run_no_writes(
        self,
        mock_diff,
        mock_ci,
        mock_parse_instr,
        mock_parse_rev,
        mock_setup,
        mock_find,
        mock_gen,
        mock_overwrite,
        mock_push,
        mock_post,
        monkeypatch,
    ):
        monkeypatch.setenv("COMMENT_BODY", "[update-docs]")
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setattr("sys.argv", ["suggest_docs.py", "--dry-run"])

        main()

        mock_overwrite.assert_not_called()
        mock_push.assert_not_called()
