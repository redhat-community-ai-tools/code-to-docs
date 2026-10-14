"""Tests for .code-to-docs/ignore exclusion list."""

from unittest.mock import MagicMock, patch

from config import is_path_ignored, load_ignore_patterns


class TestIsPathIgnored:
    def test_no_patterns_returns_false(self):
        assert is_path_ignored("docs/guide.md", []) is False

    def test_exact_match(self):
        assert is_path_ignored("README.md", ["README.md"]) is True

    def test_glob_match(self):
        assert is_path_ignored("docs/api-ref.md", ["docs/api-*.md"]) is True

    def test_no_match(self):
        assert is_path_ignored("docs/guide.md", ["docs/api-*.md"]) is False

    def test_directory_glob(self):
        assert is_path_ignored("generated/openapi/ref.md", ["generated/*"]) is True

    def test_bare_filename_matches_anywhere(self):
        assert is_path_ignored("deep/nested/CHANGELOG.md", ["CHANGELOG.md"]) is True

    def test_multiple_patterns(self):
        patterns = ["CHANGELOG.md", "generated/*", "*.bak"]
        assert is_path_ignored("docs/old.bak", patterns) is True
        assert is_path_ignored("docs/guide.md", patterns) is False


class TestLoadIgnorePatterns:
    def test_loads_patterns_from_branch(self):
        result = MagicMock(returncode=0, stdout="# comment\ngenerated/*\nREADME.md\n\n")
        with patch("config.run_command_safe", return_value=result):
            patterns = load_ignore_patterns()
        assert patterns == ["generated/*", "README.md"]

    def test_returns_empty_when_file_missing(self):
        result = MagicMock(returncode=1, stdout="")
        with patch("config.run_command_safe", return_value=result):
            patterns = load_ignore_patterns()
        assert patterns == []

    def test_returns_empty_on_error(self):
        with patch("config.run_command_safe", side_effect=RuntimeError("git failed")):
            patterns = load_ignore_patterns()
        assert patterns == []

    def test_skips_comments_and_blanks(self):
        result = MagicMock(returncode=0, stdout="# skip this\n\n  \nkeep-this.md\n")
        with patch("config.run_command_safe", return_value=result):
            patterns = load_ignore_patterns()
        assert patterns == ["keep-this.md"]
