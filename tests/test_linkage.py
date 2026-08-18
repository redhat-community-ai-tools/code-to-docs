"""Tests for doc-to-code linkage front-matter parsing."""

from linkage import (
    extract_changed_paths,
    find_declared_docs,
    parse_doc_frontmatter,
)


class TestParseDocFrontmatter:
    def test_md_yaml_frontmatter(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text(
            "---\ncode-to-docs:\n  covers:\n    - src/cli.py\n    - src/config.py\n---\n# Guide\n",
            encoding="utf-8",
        )
        result = parse_doc_frontmatter(str(doc))
        assert result == {"covers": ["src/cli.py", "src/config.py"]}

    def test_md_no_frontmatter(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text("# Guide\n\nNo front-matter here.\n", encoding="utf-8")
        assert parse_doc_frontmatter(str(doc)) == {}

    def test_rst_directive(self, tmp_path):
        doc = tmp_path / "guide.rst"
        doc.write_text(
            ".. code-to-docs:: covers: src/cli.py, src/config.py\n\nGuide\n=====\n",
            encoding="utf-8",
        )
        result = parse_doc_frontmatter(str(doc))
        assert result == {"covers": ["src/cli.py", "src/config.py"]}

    def test_adoc_comment(self, tmp_path):
        doc = tmp_path / "guide.adoc"
        doc.write_text(
            "// code-to-docs: covers: src/cli.py, src/config.py\n= Guide\n",
            encoding="utf-8",
        )
        result = parse_doc_frontmatter(str(doc))
        assert result == {"covers": ["src/cli.py", "src/config.py"]}

    def test_missing_file(self):
        assert parse_doc_frontmatter("/nonexistent/file.md") == {}

    def test_no_covers_key(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text("---\ntitle: Guide\n---\n# Guide\n", encoding="utf-8")
        assert parse_doc_frontmatter(str(doc)) == {}


class TestExtractChangedPaths:
    def test_extracts_paths(self):
        diff = "diff --git a/src/cli.py b/src/cli.py\n+new\ndiff --git a/src/config.py b/src/config.py\n+new\n"
        assert extract_changed_paths(diff) == {"src/cli.py", "src/config.py"}

    def test_empty_diff(self):
        assert extract_changed_paths("") == set()


class TestFindDeclaredDocs:
    def test_finds_matching_doc(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text(
            "---\ncode-to-docs:\n  covers:\n    - src/cli.py\n---\n# Guide\n",
            encoding="utf-8",
        )
        diff = "diff --git a/src/cli.py b/src/cli.py\n+new\n"
        result = find_declared_docs(diff, str(tmp_path))
        assert len(result) == 1
        assert result[0][1] == "declared"

    def test_no_match(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text(
            "---\ncode-to-docs:\n  covers:\n    - src/other.py\n---\n# Guide\n",
            encoding="utf-8",
        )
        diff = "diff --git a/src/cli.py b/src/cli.py\n+new\n"
        assert find_declared_docs(diff, str(tmp_path)) == []

    def test_skips_undeclared_docs(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text("# Guide\n\nNo declaration.\n", encoding="utf-8")
        diff = "diff --git a/src/cli.py b/src/cli.py\n+new\n"
        assert find_declared_docs(diff, str(tmp_path)) == []
