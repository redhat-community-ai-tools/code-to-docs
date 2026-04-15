"""Tests for security_utils.py — the highest-risk module in the action."""

import os
import subprocess
import pytest

from security_utils import (
    sanitize_output,
    run_command_safe,
    validate_file_path,
    setup_git_credentials,
    validate_docs_file_extension,
    validate_docs_subfolder,
)


# ── sanitize_output ──────────────────────────────────────────────────────────


class TestSanitizeOutput:
    def test_replaces_token_in_middle(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_secret123")
        result = sanitize_output("Error: auth failed with ghp_secret123 on remote")
        assert "ghp_secret123" not in result
        assert "***TOKEN***" in result

    def test_replaces_multiple_env_vars(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_aaa")
        monkeypatch.setenv("MODEL_API_KEY", "sk-bbb")
        result = sanitize_output("ghp_aaa and sk-bbb leaked")
        assert "ghp_aaa" not in result
        assert "sk-bbb" not in result

    def test_replaces_additional_tokens(self, monkeypatch):
        result = sanitize_output("token=xyz123", sensitive_tokens=["xyz123"])
        assert "xyz123" not in result
        assert "***TOKEN***" in result

    def test_returns_none_for_none(self):
        assert sanitize_output(None) is None

    def test_returns_empty_for_empty(self):
        assert sanitize_output("") == ""

    def test_no_env_vars_set_passes_through(self):
        result = sanitize_output("clean text with no secrets")
        assert result == "clean text with no secrets"

    def test_token_appears_multiple_times(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "secret")
        result = sanitize_output("secret at start, middle secret, end secret")
        assert "secret" not in result
        assert result.count("***TOKEN***") == 3

    def test_jira_and_google_tokens(self, monkeypatch):
        monkeypatch.setenv("JIRA_API_TOKEN", "jira_tok")
        monkeypatch.setenv("GOOGLE_SA_KEY", "gsa_key_value")
        result = sanitize_output("jira_tok and gsa_key_value")
        assert "jira_tok" not in result
        assert "gsa_key_value" not in result


# ── run_command_safe ─────────────────────────────────────────────────────────


class TestRunCommandSafe:
    def test_captures_stdout(self):
        result = run_command_safe(["echo", "hello"])
        assert result.stdout.strip() == "hello"
        assert result.returncode == 0

    def test_raises_on_failure_when_check_true(self):
        with pytest.raises(subprocess.CalledProcessError):
            run_command_safe(["false"], check=True)

    def test_does_not_raise_on_failure_when_check_false(self):
        result = run_command_safe(["false"], check=False)
        assert result.returncode != 0

    def test_sanitizes_stderr_on_error(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_leaked")
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            run_command_safe(
                ["bash", "-c", "echo 'error: ghp_leaked' >&2; exit 1"],
                check=True,
            )
        assert "ghp_leaked" not in str(exc_info.value.stderr)

    def test_sanitizes_stdout_on_error(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_leaked")
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            run_command_safe(
                ["bash", "-c", "echo 'ghp_leaked'; exit 1"],
                check=True,
            )
        assert "ghp_leaked" not in str(exc_info.value.output)


# ── validate_file_path ───────────────────────────────────────────────────────


class TestValidateFilePath:
    def test_valid_relative_path(self, tmp_tree):
        assert validate_file_path("docs/guide.md", base_dir=tmp_tree) is True

    def test_valid_nested_path(self, tmp_tree):
        assert validate_file_path("docs/sub/nested.md", base_dir=tmp_tree) is True

    def test_rejects_traversal(self, tmp_tree):
        assert validate_file_path("../../etc/passwd", base_dir=tmp_tree) is False

    def test_rejects_absolute_path_outside(self, tmp_tree):
        assert validate_file_path("/etc/passwd", base_dir=tmp_tree) is False

    def test_accepts_current_dir(self, tmp_tree):
        assert validate_file_path(".", base_dir=tmp_tree) is True

    def test_defaults_to_cwd_when_no_base(self):
        # Should not raise, and should accept a file in cwd
        result = validate_file_path("conftest.py")
        # Result depends on whether conftest.py exists relative to cwd,
        # but the function should not crash
        assert isinstance(result, bool)

    def test_rejects_double_traversal(self, tmp_tree):
        assert validate_file_path("docs/../../secret", base_dir=tmp_tree) is False


# ── setup_git_credentials ────────────────────────────────────────────────────


class TestSetupGitCredentials:
    def test_returns_true_on_success(self, monkeypatch, tmp_path):
        # Use a local git repo to avoid polluting global config
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        monkeypatch.chdir(tmp_path)
        result = setup_git_credentials("fake-token", "https://github.com/org/repo")
        assert result is True

    def test_does_not_print_token(self, monkeypatch, tmp_path, capsys):
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        monkeypatch.chdir(tmp_path)
        setup_git_credentials("super_secret_token", "https://github.com/org/repo")
        captured = capsys.readouterr()
        assert "super_secret_token" not in captured.out
        assert "super_secret_token" not in captured.err


# ── validate_docs_file_extension ─────────────────────────────────────────────


class TestValidateDocsFileExtension:
    @pytest.mark.parametrize(
        "path",
        ["guide.md", "docs/ref.adoc", "path/to/file.rst"],
    )
    def test_accepts_valid_extensions(self, path):
        assert validate_docs_file_extension(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "script.py",
            "config.yaml",
            "image.png",
            "README",
            "file.md.bak",
            "file.txt",
        ],
    )
    def test_rejects_invalid_extensions(self, path):
        assert validate_docs_file_extension(path) is False

    def test_rejects_empty_string(self):
        assert validate_docs_file_extension("") is False


# ── validate_docs_subfolder ──────────────────────────────────────────────────


class TestValidateDocsSubfolder:
    def test_accepts_simple_path(self):
        assert validate_docs_subfolder("docs") is True

    def test_accepts_nested_path(self):
        assert validate_docs_subfolder("path/to/docs") is True

    def test_accepts_empty(self):
        assert validate_docs_subfolder("") is True

    def test_accepts_none(self):
        assert validate_docs_subfolder(None) is True

    def test_rejects_traversal(self):
        assert validate_docs_subfolder("../etc") is False

    def test_rejects_embedded_traversal(self):
        assert validate_docs_subfolder("docs/../secrets") is False

    def test_rejects_absolute_path(self):
        assert validate_docs_subfolder("/etc/passwd") is False
