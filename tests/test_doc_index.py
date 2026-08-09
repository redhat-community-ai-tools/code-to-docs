"""Tests for doc_index.py — indexing, manifest management, and summary caching."""

import hashlib
import os
from unittest.mock import MagicMock, patch

import pytest

from doc_index import (
    INDEX_DIR,
    SUMMARIES_DIR,
    checkout_docs_from_base_branch,
    fetch_indexes_from_main,
    folder_needs_reindex,
    get_doc_folders,
    get_docs_in_folder,
    get_docs_root,
    get_folder_doc_hashes,
    get_or_generate_summary,
    get_summaries_dir,
    get_summary_filename,
    hash_file,
    indexes_exist,
    load_all_indexes,
    load_cached_summary,
    load_index,
    load_manifest,
    load_summaries_manifest,
    save_index,
    save_manifest,
    save_summaries_manifest,
    save_summary,
    summaries_exist,
    working_directory,
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
        with pytest.raises(ValueError), working_directory(target):
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
        from doc_index import ROOT_LEVEL_FOLDER

        folders = get_doc_folders(docs_root=doc_tree)
        assert "guides/operations" in folders
        assert "guides/configuration" in folders
        assert "tutorials" in folders
        assert ROOT_LEVEL_FOLDER in folders

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
    def test_finds_direct_rst_files(self, doc_tree):
        docs = get_docs_in_folder("guides/operations", docs_root=doc_tree)
        names = [d.name for d in docs]
        assert "health-checks.rst" in names
        assert "monitoring.rst" in names

    def test_does_not_recurse_into_subfolders(self, doc_tree):
        docs = get_docs_in_folder("guides", docs_root=doc_tree)
        names = [d.name for d in docs]
        assert "health-checks.rst" not in names
        assert "config-ref.rst" not in names

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
        original = {"version": "1.0", "folders": {"guides": {"doc_hashes": {"file.rst": "abc123"}}}}
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
        hashes = get_folder_doc_hashes("guides/operations", docs_root=doc_tree)
        assert len(hashes) == 2  # health-checks.rst, monitoring.rst

    def test_hash_values_are_hex(self, doc_tree):
        hashes = get_folder_doc_hashes("guides/operations", docs_root=doc_tree)
        for h in hashes.values():
            assert len(h) == 64  # SHA256 hex length
            assert all(c in "0123456789abcdef" for c in h)

    def test_keys_are_relative_paths(self, doc_tree):
        hashes = get_folder_doc_hashes("guides/operations", docs_root=doc_tree)
        for key in hashes:
            assert key.startswith("guides/operations/")

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


# ── checkout_docs_from_base_branch ───────────────────────────────────────────


class TestCheckoutDocsFromBaseBranch:
    def test_skipped_without_docs_subfolder(self, monkeypatch):
        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)
        assert checkout_docs_from_base_branch() is False

    def _mock_ls_tree(self, files):
        """Helper: return a mock run_command_safe that simulates ls-tree output."""

        def side_effect(cmd, **kwargs):
            result = MagicMock(returncode=0)
            if cmd[:3] == ["git", "ls-tree", "-r"]:
                result.stdout = "\n".join(files)
            else:
                result.stdout = ""
            return result

        return side_effect

    def test_checks_out_only_missing_files(self, monkeypatch, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "existing.md").write_text("already here")
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.setenv("DOCS_BASE_BRANCH", "main")
        monkeypatch.chdir(docs_dir)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_ls_tree(["docs/existing.md", "docs/new-file.md"])
            result = checkout_docs_from_base_branch()

        assert result is True
        checkout_calls = [
            c.args[0] for c in mock_run.call_args_list if "checkout" in str(c.args[0])
        ]
        assert ["git", "checkout", "origin/main", "--", "docs/new-file.md"] in checkout_calls
        assert not any("existing.md" in str(c) for c in checkout_calls)

    def test_returns_false_when_all_files_present(self, monkeypatch, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "guide.md").write_text("guide")
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.chdir(docs_dir)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_ls_tree(["docs/guide.md"])
            result = checkout_docs_from_base_branch()

        assert result is False

    def test_uses_custom_base_branch(self, monkeypatch, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.setenv("DOCS_BASE_BRANCH", "develop")
        monkeypatch.chdir(docs_dir)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_ls_tree(["docs/new.md"])
            checkout_docs_from_base_branch()

        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["git", "fetch", "origin", "develop"] in calls
        assert ["git", "checkout", "origin/develop", "--", "docs/new.md"] in calls

    def test_empty_base_branch_falls_back_to_main(self, monkeypatch, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.setenv("DOCS_BASE_BRANCH", "")
        monkeypatch.chdir(docs_dir)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_ls_tree(["docs/new.md"])
            checkout_docs_from_base_branch()

        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["git", "fetch", "origin", "main"] in calls

    def test_defaults_to_main_branch(self, monkeypatch, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.delenv("DOCS_BASE_BRANCH", raising=False)
        monkeypatch.chdir(docs_dir)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_ls_tree(["docs/new.md"])
            checkout_docs_from_base_branch()

        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["git", "fetch", "origin", "main"] in calls

    def test_returns_false_on_ls_tree_failure(self, monkeypatch, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.chdir(docs_dir)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = checkout_docs_from_base_branch()

        assert result is False

    def test_returns_false_on_exception(self, monkeypatch, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.chdir(docs_dir)

        with patch("doc_index.run_command_safe", side_effect=OSError("git not found")):
            result = checkout_docs_from_base_branch()

        assert result is False


# ── fetch_indexes_from_main ──────────────────────────────────────────────────


class TestFetchIndexesFromMain:
    """Tests for fetch_indexes_from_main() — fetch cached .doc-index from base branch."""

    def _mock_git(
        self,
        ls_tree_stdout="",
        ls_tree_returncode=0,
        fetch_returncode=0,
        checkout_returncode=0,
        checkout_stderr="",
        fetch_stderr="",
    ):
        """Return a run_command_safe side_effect that simulates the three git calls."""

        def side_effect(cmd, **kwargs):
            result = MagicMock()
            if cmd[:2] == ["git", "fetch"]:
                result.returncode = fetch_returncode
                result.stdout = ""
                result.stderr = fetch_stderr
            elif cmd[:2] == ["git", "ls-tree"]:
                result.returncode = ls_tree_returncode
                result.stdout = ls_tree_stdout
                result.stderr = ""
            elif cmd[:2] == ["git", "checkout"]:
                result.returncode = checkout_returncode
                result.stdout = ""
                result.stderr = checkout_stderr
            else:
                raise AssertionError(
                    f"Unexpected command in mock: {cmd}\n"
                    "If this is intentional, extend _mock_git to handle it."
                )
            return result

        return side_effect

    def test_returns_true_when_index_fetched_successfully(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_git(ls_tree_stdout="040000 tree abc\t.doc-index\n")
            result = fetch_indexes_from_main()

        assert result is True
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["git", "checkout", "origin/main", "--", ".doc-index"] in calls

    def test_returns_false_when_no_index_on_base(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        with patch("doc_index.run_command_safe") as mock_run:
            # ls-tree returns 0 but empty stdout — path is not tracked on the branch
            mock_run.side_effect = self._mock_git(ls_tree_stdout="")
            result = fetch_indexes_from_main()

        assert result is False
        out = capsys.readouterr().out
        assert "No cached indexes/summaries found" in out
        # And the checkout should never have been attempted
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert not any(c[:2] == ["git", "checkout"] for c in calls)

    def test_returns_false_on_fetch_failure(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_git(
                fetch_returncode=128, fetch_stderr="fatal: could not read from remote"
            )
            result = fetch_indexes_from_main()

        assert result is False
        out = capsys.readouterr().out
        assert "Could not fetch" in out
        # ls-tree should not have been called after fetch failed
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert not any(c[:2] == ["git", "ls-tree"] for c in calls)

    def test_returns_false_on_ls_tree_failure(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_git(ls_tree_returncode=128)
            result = fetch_indexes_from_main()

        assert result is False
        out = capsys.readouterr().out
        assert "No cached indexes/summaries found" in out

    def test_returns_false_on_checkout_failure(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_git(
                ls_tree_stdout="040000 tree abc\t.doc-index\n",
                checkout_returncode=1,
                checkout_stderr="error: pathspec did not match any file",
            )
            result = fetch_indexes_from_main()

        assert result is False
        out = capsys.readouterr().out
        assert "Could not checkout indexes" in out

    def test_uses_custom_base_branch(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOCS_BASE_BRANCH", "develop")
        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_git(ls_tree_stdout="040000 tree abc\t.doc-index\n")
            fetch_indexes_from_main()

        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["git", "fetch", "origin", "develop"] in calls
        assert ["git", "ls-tree", "origin/develop", "--", ".doc-index"] in calls
        assert ["git", "checkout", "origin/develop", "--", ".doc-index"] in calls

    def test_empty_base_branch_falls_back_to_main(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOCS_BASE_BRANCH", "")
        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_git(ls_tree_stdout="040000 tree abc\t.doc-index\n")
            fetch_indexes_from_main()

        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["git", "fetch", "origin", "main"] in calls

    def test_docs_subfolder_uses_prefixed_index_path(self, monkeypatch, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        monkeypatch.chdir(docs_dir)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.side_effect = self._mock_git(
                ls_tree_stdout="040000 tree abc\tdocs/.doc-index\n"
            )
            fetch_indexes_from_main()

        calls = [c.args[0] for c in mock_run.call_args_list]
        # Both the existence check and the checkout should use the subfolder-prefixed path
        assert ["git", "ls-tree", "origin/main", "--", "docs/.doc-index"] in calls
        assert ["git", "checkout", "origin/main", "--", "docs/.doc-index"] in calls

    def test_returns_false_on_unexpected_exception(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        with patch("doc_index.run_command_safe", side_effect=OSError("git not found")):
            result = fetch_indexes_from_main()

        assert result is False
        out = capsys.readouterr().out
        assert "Error fetching indexes from main" in out


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
        if hasattr(load_cached_summary, "_debug_shown"):
            del load_cached_summary._debug_shown

        file_path = "guides/operations/health-checks.rst"
        summary = "This file documents health checks for monitoring."
        save_summary(file_path, summary, docs_root=doc_tree)

        cached = load_cached_summary(file_path, docs_root=doc_tree)
        assert cached == summary

    def test_cache_invalidated_on_change(self, doc_tree):
        if hasattr(load_cached_summary, "_debug_shown"):
            del load_cached_summary._debug_shown

        file_path = "guides/operations/health-checks.rst"
        save_summary(file_path, "Original summary", docs_root=doc_tree)

        # Modify the source file
        (doc_tree / file_path).write_text("COMPLETELY NEW CONTENT")

        cached = load_cached_summary(file_path, docs_root=doc_tree)
        assert cached is None  # Hash mismatch

    def test_cache_miss_when_no_summary(self, doc_tree):
        if hasattr(load_cached_summary, "_debug_shown"):
            del load_cached_summary._debug_shown

        cached = load_cached_summary("guides/operations/health-checks.rst", docs_root=doc_tree)
        assert cached is None

    def test_summaries_exist_true(self, doc_tree):
        save_summary("guides/operations/health-checks.rst", "summary", docs_root=doc_tree)
        assert summaries_exist(docs_root=doc_tree) is True

    def test_summaries_exist_false(self, tmp_path):
        assert summaries_exist(docs_root=tmp_path) is False

    def test_get_or_generate_uses_cache(self, doc_tree):
        if hasattr(load_cached_summary, "_debug_shown"):
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
        if hasattr(load_cached_summary, "_debug_shown"):
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
