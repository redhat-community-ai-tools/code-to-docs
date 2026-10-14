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


class TestGetValidationConfig:
    def test_defaults_when_no_config(self):
        vc = config.get_validation_config({})
        assert vc["removal_threshold"] == 0.20
        assert vc["min_lines"] == 30
        assert vc["llm_verification"] is True

    def test_overrides_threshold(self):
        vc = config.get_validation_config({"validation": {"removal-threshold": 0.50}})
        assert vc["removal_threshold"] == 0.50

    def test_overrides_min_lines(self):
        vc = config.get_validation_config({"validation": {"min-lines": 10}})
        assert vc["min_lines"] == 10

    def test_disables_llm_verification(self):
        vc = config.get_validation_config({"validation": {"llm-verification": False}})
        assert vc["llm_verification"] is False

    def test_invalid_threshold_falls_back(self):
        vc = config.get_validation_config({"validation": {"removal-threshold": "bad"}})
        assert vc["removal_threshold"] == 0.20

    def test_threshold_out_of_range_falls_back(self):
        vc = config.get_validation_config({"validation": {"removal-threshold": 1.5}})
        assert vc["removal_threshold"] == 0.20

    def test_non_dict_validation_falls_back(self):
        vc = config.get_validation_config({"validation": "not a dict"})
        assert vc["llm_verification"] is True
