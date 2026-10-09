"""Tests for repo-level configuration (.code-to-docs/config.json)."""

import json
import sys
from unittest.mock import MagicMock, patch

sys.modules.setdefault("openai", MagicMock())

import config  # noqa: E402


class TestLoadRepoConfig:
    def setup_method(self):
        config._repo_config_cache = None

    def test_loads_config_from_base_branch(self, monkeypatch):
        monkeypatch.delenv("DOCS_BASE_BRANCH", raising=False)
        config_data = {"pr-title-prefix": ":book:"}

        with patch("config.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(config_data))
            result = config.load_repo_config()

        assert result == config_data
        cmd = mock_run.call_args.args[0]
        assert "origin/main:.code-to-docs/config.json" in cmd[-1]

    def test_uses_custom_base_branch(self, monkeypatch):
        monkeypatch.setenv("DOCS_BASE_BRANCH", "develop")
        config_data = {"pr-title-prefix": ":seedling:"}

        with patch("config.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(config_data))
            result = config.load_repo_config()

        assert result == config_data
        cmd = mock_run.call_args.args[0]
        assert "origin/develop" in cmd[-1]

    def test_returns_empty_dict_when_file_missing(self, monkeypatch):
        monkeypatch.delenv("DOCS_BASE_BRANCH", raising=False)

        with patch("config.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = config.load_repo_config()

        assert result == {}

    def test_returns_empty_dict_on_invalid_json(self, monkeypatch):
        monkeypatch.delenv("DOCS_BASE_BRANCH", raising=False)

        with patch("config.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json{")
            result = config.load_repo_config()

        assert result == {}

    def test_caches_result(self, monkeypatch):
        monkeypatch.delenv("DOCS_BASE_BRANCH", raising=False)
        config_data = {"pr-title-prefix": ":book:"}

        with patch("config.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(config_data))
            result1 = config.load_repo_config()
            result2 = config.load_repo_config()

        assert result1 == result2
        # First call: git fetch + git show = 2 calls. Second call: cached, 0 calls.
        assert mock_run.call_count == 2


class TestGetPrTitlePrefix:
    def setup_method(self):
        config._repo_config_cache = None

    def test_returns_prefix_with_trailing_space(self):
        config._repo_config_cache = {"pr-title-prefix": ":book:"}
        assert config.get_pr_title_prefix() == ":book: "

    def test_returns_empty_string_when_not_set(self):
        config._repo_config_cache = {}
        assert config.get_pr_title_prefix() == ""

    def test_returns_empty_string_when_no_config(self):
        config._repo_config_cache = {}
        assert config.get_pr_title_prefix() == ""

    def test_strips_whitespace_from_prefix(self):
        config._repo_config_cache = {"pr-title-prefix": "  :book:  "}
        assert config.get_pr_title_prefix() == ":book: "

    def test_non_string_prefix_returns_empty(self):
        config._repo_config_cache = {"pr-title-prefix": 42}
        assert config.get_pr_title_prefix() == ""

    def test_list_prefix_returns_empty(self):
        config._repo_config_cache = {"pr-title-prefix": [":book:"]}
        assert config.get_pr_title_prefix() == ""
