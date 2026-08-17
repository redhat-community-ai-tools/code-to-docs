"""Tests for detect-only mode."""

from detect import extract_changed_doc_paths, run_detect_only


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
