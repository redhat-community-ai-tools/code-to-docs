"""Tests for doc-to-code linkage front-matter parsing."""

from linkage import (
    _is_safe_covers_path,
    extract_changed_paths,
    find_declared_docs,
    parse_doc_frontmatter,
)


class TestIsSafeCoversPath:
    def test_normal_path(self):
        assert _is_safe_covers_path("src/cli.py") is True

    def test_traversal_rejected(self):
        assert _is_safe_covers_path("../etc/passwd") is False
        assert _is_safe_covers_path("src/../../etc/passwd") is False

    def test_absolute_path_rejected(self):
        assert _is_safe_covers_path("/etc/passwd") is False

    def test_null_byte_rejected(self):
        assert _is_safe_covers_path("src/cli\x00.py") is False

    def test_empty_rejected(self):
        assert _is_safe_covers_path("") is False
        assert _is_safe_covers_path(None) is False


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

    def test_malformed_yaml(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text("---\n: [invalid yaml\n---\n# Guide\n", encoding="utf-8")
        assert parse_doc_frontmatter(str(doc)) == {}

    def test_traversal_path_filtered(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text(
            "---\ncode-to-docs:\n  covers:\n    - ../etc/passwd\n---\n# Guide\n",
            encoding="utf-8",
        )
        assert parse_doc_frontmatter(str(doc)) == {}

    def test_absolute_path_filtered(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text(
            "---\ncode-to-docs:\n  covers:\n    - /etc/passwd\n---\n# Guide\n",
            encoding="utf-8",
        )
        assert parse_doc_frontmatter(str(doc)) == {}

    def test_base_dir_validation(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        doc = other / "guide.md"
        doc.write_text(
            "---\ncode-to-docs:\n  covers:\n    - src/cli.py\n---\n# Guide\n",
            encoding="utf-8",
        )
        # Valid base_dir containing the file
        result = parse_doc_frontmatter(str(doc), base_dir=str(other))
        assert result == {"covers": ["src/cli.py"]}

    def test_rst_traversal_path_filtered(self, tmp_path):
        doc = tmp_path / "guide.rst"
        doc.write_text(
            ".. code-to-docs:: covers: ../etc/passwd, src/cli.py\n\nGuide\n=====\n",
            encoding="utf-8",
        )
        result = parse_doc_frontmatter(str(doc))
        assert result == {"covers": ["src/cli.py"]}


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
        assert str(doc) in result[0]

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

    def test_directory_prefix_matching(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text(
            "---\ncode-to-docs:\n  covers:\n    - src/cli\n---\n# Guide\n",
            encoding="utf-8",
        )
        diff = "diff --git a/src/cli/flags.py b/src/cli/flags.py\n+new\n"
        result = find_declared_docs(diff, str(tmp_path))
        assert len(result) == 1

    def test_skips_symlinks(self, tmp_path):
        real = tmp_path / "real.md"
        real.write_text(
            "---\ncode-to-docs:\n  covers:\n    - src/cli.py\n---\n# Real\n",
            encoding="utf-8",
        )
        link = tmp_path / "link.md"
        link.symlink_to(real)
        diff = "diff --git a/src/cli.py b/src/cli.py\n+new\n"
        result = find_declared_docs(diff, str(tmp_path))
        # Only the real file should be found, not the symlink
        paths = [r for r in result]
        assert str(link) not in paths
        assert len(paths) == 1

    def test_returns_strings_not_tuples(self, tmp_path):
        doc = tmp_path / "guide.md"
        doc.write_text(
            "---\ncode-to-docs:\n  covers:\n    - src/cli.py\n---\n# Guide\n",
            encoding="utf-8",
        )
        diff = "diff --git a/src/cli.py b/src/cli.py\n+new\n"
        result = find_declared_docs(diff, str(tmp_path))
        assert len(result) == 1
        assert isinstance(result[0], str)
