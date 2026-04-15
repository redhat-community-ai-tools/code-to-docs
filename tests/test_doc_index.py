"""Tests for doc_index.py — indexing, manifest management, and summary caching."""

import json
import hashlib
import pytest

import os

from doc_index import (
    working_directory,
    hash_file,
    get_docs_root,
    get_doc_folders,
    get_docs_in_folder,
    load_manifest,
    save_manifest,
    get_folder_doc_hashes,
    folder_needs_reindex,
    save_index,
    load_index,
    load_all_indexes,
    indexes_exist,
    get_files_in_areas,
    get_summaries_dir,
    get_summary_filename,
    load_summaries_manifest,
    save_summaries_manifest,
    load_cached_summary,
    save_summary,
    get_or_generate_summary,
    summaries_exist,
    INDEX_DIR,
    SUMMARIES_DIR,
)


# ── working_directory ────────────────────────────────────────────────────────


class TestWorkingDirectory:
    def test_changes_to_target_dir(self, tmp_path):
        target = tmp_path / "subdir"
        target.mkdir()
        with working_directory(target):
            assert os.getcwd() == str(target)

    def test_restores_cwd_on_normal_exit(self, tmp_path):
        original = os.getcwd()
        target = tmp_path / "subdir"
        target.mkdir()
        with working_directory(target):
            pass
        assert os.getcwd() == original

    def test_restores_cwd_on_exception(self, tmp_path):
        original = os.getcwd()
        target = tmp_path / "subdir"
        target.mkdir()
        with pytest.raises(ValueError):
            with working_directory(target):
                raise ValueError("boom")
        assert os.getcwd() == original


# ── hash_file ────────────────────────────────────────────────────────────────


class TestHashFile:
    def test_known_content(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert hash_file(f) == expected

    def test_deterministic(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("same content")
        assert hash_file(f) == hash_file(f)

    def test_different_content_different_hash(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("content A")
        b.write_text("content B")
        assert hash_file(a) != hash_file(b)

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        expected = hashlib.sha256(b"").hexdigest()
        assert hash_file(f) == expected


# ── get_docs_root ────────────────────────────────────────────────────────────


class TestGetDocsRoot:
    def test_defaults_to_cwd(self):
        root = get_docs_root()
        assert str(root) == "."

    def test_uses_docs_subfolder(self, monkeypatch, doc_tree):
        monkeypatch.chdir(doc_tree.parent)
        monkeypatch.setenv("DOCS_SUBFOLDER", str(doc_tree))
        root = get_docs_root()
        assert root.resolve() == doc_tree.resolve()

    def test_ignores_nonexistent_subfolder(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOCS_SUBFOLDER", "nonexistent")
        root = get_docs_root()
        assert str(root) == "."


# ── get_doc_folders ──────────────────────────────────────────────────────────


class TestGetDocFolders:
    def test_finds_doc_folders(self, doc_tree):
        folders = get_doc_folders(docs_root=doc_tree)
        assert "guides" in folders
        assert "tutorials" in folders

    def test_skips_hidden_dirs(self, doc_tree):
        folders = get_doc_folders(docs_root=doc_tree)
        assert ".hidden" not in folders

    def test_skips_underscore_dirs(self, doc_tree):
        folders = get_doc_folders(docs_root=doc_tree)
        assert "_build" not in folders

    def test_returns_sorted(self, doc_tree):
        folders = get_doc_folders(docs_root=doc_tree)
        assert folders == sorted(folders)

    def test_empty_dir(self, tmp_path):
        folders = get_doc_folders(docs_root=tmp_path)
        assert folders == []

    def test_root_level_docs_not_folders(self, doc_tree):
        """Root-level docs (overview.rst, README.md) don't create folder entries."""
        folders = get_doc_folders(docs_root=doc_tree)
        assert "overview" not in folders
        assert "README" not in folders


# ── get_docs_in_folder ───────────────────────────────────────────────────────


class TestGetDocsInFolder:
    def test_finds_rst_files(self, doc_tree):
        docs = get_docs_in_folder("guides", docs_root=doc_tree)
        names = [d.name for d in docs]
        assert "health-checks.rst" in names
        assert "monitoring.rst" in names
        assert "config-ref.rst" in names

    def test_finds_md_files(self, doc_tree):
        docs = get_docs_in_folder("tutorials", docs_root=doc_tree)
        names = [d.name for d in docs]
        assert "getting-started.md" in names

    def test_nonexistent_folder(self, doc_tree):
        docs = get_docs_in_folder("nonexistent", docs_root=doc_tree)
        assert docs == []

    def test_empty_folder(self, tmp_path):
        (tmp_path / "empty").mkdir()
        docs = get_docs_in_folder("empty", docs_root=tmp_path)
        assert docs == []


# ── Manifest operations ─────────────────────────────────────────────────────


class TestManifest:
    def test_load_returns_default_when_missing(self, tmp_path):
        manifest = load_manifest(docs_root=tmp_path)
        assert manifest["version"] == "1.0"
        assert manifest["folders"] == {}

    def test_save_creates_index_dir(self, tmp_path):
        manifest = {"version": "1.0", "folders": {}}
        save_manifest(manifest, docs_root=tmp_path)
        assert (tmp_path / INDEX_DIR).is_dir()

    def test_save_and_load_roundtrip(self, tmp_path):
        original = {
            "version": "1.0",
            "folders": {
                "guides": {"doc_hashes": {"file.rst": "abc123"}}
            }
        }
        save_manifest(original, docs_root=tmp_path)
        loaded = load_manifest(docs_root=tmp_path)
        assert loaded["version"] == original["version"]
        assert loaded["folders"] == original["folders"]
        assert "updated" in loaded  # save_manifest adds timestamp

    def test_save_adds_updated_timestamp(self, tmp_path):
        manifest = {"version": "1.0", "folders": {}}
        save_manifest(manifest, docs_root=tmp_path)
        loaded = load_manifest(docs_root=tmp_path)
        assert "updated" in loaded


# ── get_folder_doc_hashes ────────────────────────────────────────────────────


class TestGetFolderDocHashes:
    def test_returns_hashes_for_all_docs(self, doc_tree):
        hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        assert len(hashes) == 3  # health-checks.rst, monitoring.rst, config-ref.rst

    def test_hash_values_are_hex(self, doc_tree):
        hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        for h in hashes.values():
            assert len(h) == 64  # SHA256 hex length
            assert all(c in "0123456789abcdef" for c in h)

    def test_keys_are_relative_paths(self, doc_tree):
        hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        for key in hashes:
            assert key.startswith("guides/")

    def test_empty_folder(self, tmp_path):
        (tmp_path / "empty").mkdir()
        hashes = get_folder_doc_hashes("empty", docs_root=tmp_path)
        assert hashes == {}


# ── folder_needs_reindex ─────────────────────────────────────────────────────


class TestFolderNeedsReindex:
    def test_new_folder_needs_reindex(self, doc_tree):
        manifest = {"folders": {}}
        assert folder_needs_reindex("guides", manifest, docs_root=doc_tree) is True

    def test_unchanged_folder_no_reindex(self, doc_tree):
        hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        manifest = {"folders": {"guides": {"doc_hashes": hashes}}}
        # Index file must exist for the folder to be considered up-to-date
        save_index("guides", "dummy index content", docs_root=doc_tree)
        assert folder_needs_reindex("guides", manifest, docs_root=doc_tree) is False

    def test_changed_file_triggers_reindex(self, doc_tree):
        hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        manifest = {"folders": {"guides": {"doc_hashes": hashes}}}

        # Modify a file
        (doc_tree / "guides" / "operations" / "health-checks.rst").write_text("CHANGED")
        assert folder_needs_reindex("guides", manifest, docs_root=doc_tree) is True

    def test_added_file_triggers_reindex(self, doc_tree):
        hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        manifest = {"folders": {"guides": {"doc_hashes": hashes}}}

        # Add a new file
        (doc_tree / "guides" / "operations" / "new-doc.rst").write_text("New content")
        assert folder_needs_reindex("guides", manifest, docs_root=doc_tree) is True


# ── Index save/load ──────────────────────────────────────────────────────────


class TestIndexSaveLoad:
    def test_save_creates_file(self, tmp_path):
        save_index("guides", "Index content for guides", docs_root=tmp_path)
        index_file = tmp_path / INDEX_DIR / "guides.index.md"
        assert index_file.exists()

    def test_roundtrip(self, tmp_path):
        content = "# Guides Documentation Index\n\nCovers health checks and monitoring."
        save_index("guides", content, docs_root=tmp_path)
        loaded = load_index("guides", docs_root=tmp_path)
        assert loaded == content

    def test_load_missing_returns_none(self, tmp_path):
        assert load_index("nonexistent", docs_root=tmp_path) is None

    def test_load_all_indexes(self, tmp_path):
        save_index("guides", "Guides index", docs_root=tmp_path)
        save_index("tutorials", "Tutorials index", docs_root=tmp_path)
        all_idx = load_all_indexes(docs_root=tmp_path)
        assert "guides" in all_idx
        assert "tutorials" in all_idx
        assert all_idx["guides"] == "Guides index"

    def test_load_all_empty(self, tmp_path):
        all_idx = load_all_indexes(docs_root=tmp_path)
        assert all_idx == {}

    def test_indexes_exist_true(self, tmp_path):
        save_index("guides", "content", docs_root=tmp_path)
        assert indexes_exist(docs_root=tmp_path) is True

    def test_indexes_exist_false(self, tmp_path):
        assert indexes_exist(docs_root=tmp_path) is False

    def test_indexes_exist_empty_dir(self, tmp_path):
        (tmp_path / INDEX_DIR).mkdir()
        assert indexes_exist(docs_root=tmp_path) is False


# ── get_files_in_areas ───────────────────────────────────────────────────────


class TestGetFilesInAreas:
    def test_single_area(self, doc_tree):
        files = get_files_in_areas(["guides"], docs_root=doc_tree)
        assert any("health-checks.rst" in f for f in files)
        assert any("config-ref.rst" in f for f in files)

    def test_multiple_areas(self, doc_tree):
        files = get_files_in_areas(["guides", "tutorials"], docs_root=doc_tree)
        assert any("health-checks.rst" in f for f in files)
        assert any("getting-started.md" in f for f in files)

    def test_includes_root_level_docs(self, doc_tree):
        files = get_files_in_areas(["guides"], docs_root=doc_tree)
        assert "overview.rst" in files or any("overview.rst" in f for f in files)

    def test_nonexistent_area(self, doc_tree):
        files = get_files_in_areas(["nonexistent"], docs_root=doc_tree)
        # Should still include root-level docs
        assert isinstance(files, list)

    def test_deduplicated(self, doc_tree):
        files = get_files_in_areas(["guides", "guides"], docs_root=doc_tree)
        assert len(files) == len(set(files))


# ── Summary filename ─────────────────────────────────────────────────────────


class TestGetSummaryFilename:
    def test_simple_path(self):
        assert get_summary_filename("guide.rst") == "guide.rst.summary.md"

    def test_nested_path(self):
        result = get_summary_filename("guides/operations/health-checks.rst")
        assert result == "guides-operations-health-checks.rst.summary.md"

    def test_no_slashes_passthrough(self):
        result = get_summary_filename("README.md")
        assert result == "README.md.summary.md"


# ── Summaries manifest ───────────────────────────────────────────────────────


class TestSummariesManifest:
    def test_load_returns_default_when_missing(self, tmp_path):
        manifest = load_summaries_manifest(docs_root=tmp_path)
        assert manifest == {"version": "1.0", "files": {}}

    def test_save_creates_dir(self, tmp_path):
        save_summaries_manifest({"version": "1.0", "files": {}}, docs_root=tmp_path)
        assert (tmp_path / INDEX_DIR).is_dir()

    def test_roundtrip(self, tmp_path):
        original = {"version": "1.0", "files": {"guide.rst": {"hash": "abc"}}}
        save_summaries_manifest(original, docs_root=tmp_path)
        loaded = load_summaries_manifest(docs_root=tmp_path)
        assert loaded["files"] == original["files"]

    def test_corrupted_manifest_returns_default(self, tmp_path):
        index_dir = tmp_path / INDEX_DIR
        index_dir.mkdir()
        (index_dir / "summaries_manifest.json").write_text("{invalid json")
        manifest = load_summaries_manifest(docs_root=tmp_path)
        assert manifest == {"version": "1.0", "files": {}}

    def test_get_summaries_dir(self, tmp_path):
        result = get_summaries_dir(docs_root=tmp_path)
        assert result == tmp_path / INDEX_DIR / SUMMARIES_DIR


# ── Summary caching ──────────────────────────────────────────────────────────


class TestSummaryCaching:
    def test_save_and_load_cached_summary(self, doc_tree):
        # Reset the debug flag if it exists from previous tests
        if hasattr(load_cached_summary, '_debug_shown'):
            del load_cached_summary._debug_shown

        file_path = "guides/operations/health-checks.rst"
        summary = "This file documents health checks for monitoring."
        save_summary(file_path, summary, docs_root=doc_tree)

        cached = load_cached_summary(file_path, docs_root=doc_tree)
        assert cached == summary

    def test_cache_invalidated_on_change(self, doc_tree):
        if hasattr(load_cached_summary, '_debug_shown'):
            del load_cached_summary._debug_shown

        file_path = "guides/operations/health-checks.rst"
        save_summary(file_path, "Original summary", docs_root=doc_tree)

        # Modify the source file
        (doc_tree / file_path).write_text("COMPLETELY NEW CONTENT")

        cached = load_cached_summary(file_path, docs_root=doc_tree)
        assert cached is None  # Hash mismatch

    def test_cache_miss_when_no_summary(self, doc_tree):
        if hasattr(load_cached_summary, '_debug_shown'):
            del load_cached_summary._debug_shown

        cached = load_cached_summary("guides/operations/health-checks.rst", docs_root=doc_tree)
        assert cached is None

    def test_summaries_exist_true(self, doc_tree):
        save_summary("guides/operations/health-checks.rst", "summary", docs_root=doc_tree)
        assert summaries_exist(docs_root=doc_tree) is True

    def test_summaries_exist_false(self, tmp_path):
        assert summaries_exist(docs_root=tmp_path) is False

    def test_get_or_generate_uses_cache(self, doc_tree):
        if hasattr(load_cached_summary, '_debug_shown'):
            del load_cached_summary._debug_shown

        file_path = "guides/operations/health-checks.rst"
        content = (doc_tree / file_path).read_text()

        # Save a cached summary
        save_summary(file_path, "Cached summary", docs_root=doc_tree)

        # Generator should NOT be called
        generator_called = False

        def fake_generator(fp, c):
            nonlocal generator_called
            generator_called = True
            return "Generated summary"

        result = get_or_generate_summary(file_path, content, fake_generator, docs_root=doc_tree)
        assert result == "Cached summary"
        assert generator_called is False

    def test_get_or_generate_calls_generator_on_miss(self, doc_tree):
        if hasattr(load_cached_summary, '_debug_shown'):
            del load_cached_summary._debug_shown

        file_path = "tutorials/getting-started.md"
        content = (doc_tree / file_path).read_text()

        def fake_generator(fp, c):
            return "Generated summary for tutorials"

        result = get_or_generate_summary(file_path, content, fake_generator, docs_root=doc_tree)
        assert result == "Generated summary for tutorials"

        # Verify it was cached
        cached = load_cached_summary(file_path, docs_root=doc_tree)
        assert cached == "Generated summary for tutorials"

    def test_save_summary_updates_manifest(self, doc_tree):
        file_path = "guides/operations/health-checks.rst"
        save_summary(file_path, "Test summary", docs_root=doc_tree)

        manifest = load_summaries_manifest(docs_root=doc_tree)
        assert file_path in manifest["files"]
        assert "hash" in manifest["files"][file_path]
        assert "generated" in manifest["files"][file_path]
