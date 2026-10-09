"""Tests for detect.py — detect-only mode for docs drift checking."""

import pytest

from detect import exit_with_severity, extract_changed_doc_paths, run_detect_only


class TestExtractChangedDocPaths:
    def test_finds_doc_files(self):
        diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "+added\n"
            "diff --git a/docs/guide.md b/docs/guide.md\n"
            "+updated\n"
            "diff --git a/docs/api.rst b/docs/api.rst\n"
            "+updated\n"
        )
        paths = extract_changed_doc_paths(diff)
        assert paths == {"docs/guide.md", "docs/api.rst"}

    def test_ignores_non_doc_files(self):
        diff = "diff --git a/src/main.py b/src/main.py\n+added\n"
        assert extract_changed_doc_paths(diff) == set()

    def test_empty_diff(self):
        assert extract_changed_doc_paths("") == set()


class TestRunDetectOnly:
    def test_reports_untouched_files(self):
        untouched, lines = run_detect_only(
            diff="",
            relevant_files=["docs/guide.md", "docs/api.md"],
            changed_docs={"docs/guide.md"},
        )
        assert untouched == {"docs/api.md"}
        assert any("docs/api.md" in line for line in lines)

    def test_all_updated(self):
        untouched, lines = run_detect_only(
            diff="",
            relevant_files=["docs/guide.md"],
            changed_docs={"docs/guide.md"},
        )
        assert untouched == set()
        assert any("already updated" in line for line in lines)

    def test_no_relevant_files(self):
        untouched, lines = run_detect_only(
            diff="",
            relevant_files=[],
            changed_docs=set(),
        )
        assert untouched == set()
        assert any("No documentation files were identified" in line for line in lines)


class TestExitWithSeverity:
    def test_error_severity_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            exit_with_severity({"docs/guide.md"}, "error")
        assert exc_info.value.code == 1

    def test_warn_severity_does_not_exit(self):
        # Should return without raising
        exit_with_severity({"docs/guide.md"}, "warn")

    def test_empty_untouched_does_not_exit(self):
        # Should return without raising regardless of severity
        exit_with_severity(set(), "error")

    def test_none_severity_defaults_to_warn(self):
        # None severity should default to warn (no exit)
        exit_with_severity({"docs/guide.md"}, None)
