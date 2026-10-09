"""Tests for doc_index.py — indexing, manifest management, and summary caching."""

import hashlib
import os
from unittest.mock import MagicMock, patch

import pytest

from doc_index import (
    INDEX_DIR,
    SUMMARIES_DIR,
    _get_docs_content_from_ref,
    _get_effective_subfolder,
    _handle_empty_folder_on_ref,
    checkout_docs_from_base_branch,
    folder_needs_reindex,
    get_doc_folders,
    get_docs_in_folder,
    get_docs_root,
    get_folder_doc_hashes,
    get_folder_doc_hashes_from_ref,
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
    remove_index,
    save_index,
    save_manifest,
    save_summaries_manifest,
    save_summary,
    summaries_exist,
    update_indexes_if_needed,
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
        with patch("doc_index.get_folder_doc_hashes_from_ref", return_value=None):
            assert folder_needs_reindex("guides", manifest, docs_root=doc_tree) is False

    def test_changed_file_triggers_reindex(self, doc_tree):
        hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        manifest = {"folders": {"guides": {"doc_hashes": hashes}}}

        # Modify a file
        (doc_tree / "guides" / "operations" / "health-checks.rst").write_text("CHANGED")
        with patch("doc_index.get_folder_doc_hashes_from_ref", return_value=None):
            assert folder_needs_reindex("guides", manifest, docs_root=doc_tree) is True

    def test_added_file_triggers_reindex(self, doc_tree):
        hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        manifest = {"folders": {"guides": {"doc_hashes": hashes}}}

        # Add a new file
        (doc_tree / "guides" / "operations" / "new-doc.rst").write_text("New content")
        with patch("doc_index.get_folder_doc_hashes_from_ref", return_value=None):
            assert folder_needs_reindex("guides", manifest, docs_root=doc_tree) is True

    def test_uses_ref_hashes_over_disk(self, doc_tree):
        """When ref hashes are available, folder_needs_reindex uses them instead of disk."""
        disk_hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        ref_hashes = {"guides/operations/health-checks.rst": "different_hash_from_main"}
        manifest = {"folders": {"guides": {"doc_hashes": disk_hashes}}}
        save_index("guides", "dummy index content", docs_root=doc_tree)

        # Disk hashes match manifest, but ref hashes differ → should trigger reindex
        with patch("doc_index.get_folder_doc_hashes_from_ref", return_value=ref_hashes):
            assert folder_needs_reindex("guides", manifest, docs_root=doc_tree) is True

    def test_falls_back_to_disk_when_ref_unavailable(self, doc_tree):
        """When ref hashes return None, falls back to disk hashes."""
        hashes = get_folder_doc_hashes("guides", docs_root=doc_tree)
        manifest = {"folders": {"guides": {"doc_hashes": hashes}}}
        save_index("guides", "dummy index content", docs_root=doc_tree)

        with patch("doc_index.get_folder_doc_hashes_from_ref", return_value=None):
            assert folder_needs_reindex("guides", manifest, docs_root=doc_tree) is False


class TestGetEffectiveSubfolder:
    @pytest.fixture(autouse=True)
    def _reset_log_state(self):
        yield
        if hasattr(_get_effective_subfolder, "_last_msg"):
            delattr(_get_effective_subfolder, "_last_msg")

    def test_trailing_slash_normalized(self, monkeypatch):
        """DOCS_SUBFOLDER with trailing slash should still match CWD prefix."""
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs/")
        mock_result = MagicMock(returncode=0, stdout="docs/\n")
        with patch("doc_index.run_command_safe", return_value=mock_result):
            assert _get_effective_subfolder() == ""

    def test_dot_slash_prefix_normalized(self, monkeypatch):
        """DOCS_SUBFOLDER with ./ prefix should still match CWD prefix."""
        monkeypatch.setenv("DOCS_SUBFOLDER", "./docs")
        mock_result = MagicMock(returncode=0, stdout="docs/\n")
        with patch("doc_index.run_command_safe", return_value=mock_result):
            assert _get_effective_subfolder() == ""

    def test_no_trailing_slash(self, monkeypatch):
        """DOCS_SUBFOLDER without trailing slash matches normally."""
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        mock_result = MagicMock(returncode=0, stdout="docs/\n")
        with patch("doc_index.run_command_safe", return_value=mock_result):
            assert _get_effective_subfolder() == ""

    def test_cwd_not_in_subfolder(self, monkeypatch):
        """When CWD is not inside DOCS_SUBFOLDER, return the subfolder."""
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        mock_result = MagicMock(returncode=0, stdout="\n")
        with patch("doc_index.run_command_safe", return_value=mock_result):
            assert _get_effective_subfolder() == "docs"

    def test_empty_subfolder(self, monkeypatch):
        monkeypatch.setenv("DOCS_SUBFOLDER", "")
        assert _get_effective_subfolder() == ""

    def test_trailing_slash_pathspec_not_doubled(self, monkeypatch):
        """The actual regression: trailing slash must not cause doubled pathspecs."""
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs/")
        monkeypatch.setenv("DOCS_BASE_BRANCH", "main")

        calls = []

        def capture_run(cmd, **kwargs):
            calls.append(cmd)
            if "rev-parse" in cmd and "--show-prefix" in cmd:
                return MagicMock(returncode=0, stdout="docs/\n")
            if "rev-parse" in cmd and "--verify" in cmd:
                return MagicMock(returncode=0)
            if "ls-tree" in cmd:
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=0, stdout="")

        with patch("doc_index.run_command_safe", side_effect=capture_run):
            get_folder_doc_hashes_from_ref("commands")

        ls_tree_calls = [c for c in calls if "ls-tree" in c]
        assert len(ls_tree_calls) == 1
        assert ls_tree_calls[0][-1] == "commands"


class TestGetFolderDocHashesFromRef:
    def test_returns_hashes_from_git_ref(self, monkeypatch):
        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)
        ls_output = "guides/setup.md\nguides/intro.rst\nguides/.hidden/skip.md\n"
        file_content = b"doc content"
        expected_hash = hashlib.sha256(file_content).hexdigest()

        with (
            patch("doc_index.run_command_safe") as mock_run,
            patch("doc_index.subprocess.run") as mock_subprocess,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=ls_output)
            mock_subprocess.return_value = MagicMock(returncode=0, stdout=file_content)
            result = get_folder_doc_hashes_from_ref("guides")

        assert result is not None
        assert result["guides/setup.md"] == expected_hash
        assert result["guides/intro.rst"] == expected_hash
        assert "guides/.hidden/skip.md" not in result

    def test_returns_none_when_git_fails(self, monkeypatch):
        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = get_folder_doc_hashes_from_ref("guides")

        assert result is None

    def test_returns_empty_dict_when_no_files(self, monkeypatch):
        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = get_folder_doc_hashes_from_ref("guides")

        assert result == {}

    def test_handles_docs_subfolder(self, monkeypatch):
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        ls_output = "docs/commands/export.md\n"
        file_content = b"export docs"

        with (
            patch("doc_index._get_effective_subfolder", return_value="docs"),
            patch("doc_index.run_command_safe") as mock_run,
            patch("doc_index.subprocess.run") as mock_subprocess,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=ls_output)
            mock_subprocess.return_value = MagicMock(returncode=0, stdout=file_content)
            result = get_folder_doc_hashes_from_ref("commands")

        assert result is not None
        assert "commands/export.md" in result

    def test_handles_docs_subfolder_from_inside(self, monkeypatch):
        """When CWD is inside the docs subfolder, pathspecs omit the prefix."""
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        ls_output = "commands/export.md\n"
        file_content = b"export docs"

        with (
            patch("doc_index._get_effective_subfolder", return_value=""),
            patch("doc_index.run_command_safe") as mock_run,
            patch("doc_index.subprocess.run") as mock_subprocess,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=ls_output)
            mock_subprocess.return_value = MagicMock(returncode=0, stdout=file_content)
            result = get_folder_doc_hashes_from_ref("commands")

        assert result is not None
        assert "commands/export.md" in result

    def test_uses_custom_base_branch(self, monkeypatch):
        monkeypatch.setenv("DOCS_BASE_BRANCH", "develop")
        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)

        with (
            patch("doc_index.run_command_safe") as mock_run,
            patch("doc_index.subprocess.run") as mock_subprocess,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="guides/setup.md\n")
            mock_subprocess.return_value = MagicMock(returncode=0, stdout=b"content")
            get_folder_doc_hashes_from_ref("guides")

        ls_call = mock_run.call_args_list[0].args[0]
        assert "origin/develop" in ls_call
        cat_call = mock_subprocess.call_args_list[0].args[0]
        assert "origin/develop:./guides/setup.md" in cat_call

    def test_root_level_folder_excludes_subdirectory_files(self, monkeypatch):
        from doc_index import ROOT_LEVEL_FOLDER

        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)
        ls_output = "README.md\noverview.rst\nguides/setup.md\ncommands/export.md\n"
        file_content = b"root content"

        with (
            patch("doc_index.run_command_safe") as mock_run,
            patch("doc_index.subprocess.run") as mock_subprocess,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=ls_output)
            mock_subprocess.return_value = MagicMock(returncode=0, stdout=file_content)
            result = get_folder_doc_hashes_from_ref(ROOT_LEVEL_FOLDER)

        assert result is not None
        assert "README.md" in result
        assert "overview.rst" in result
        assert "guides/setup.md" not in result
        assert "commands/export.md" not in result

    def test_folder_excludes_files_from_other_folders(self, monkeypatch):
        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)
        ls_output = "guides/setup.md\nguides/ops/health.md\ncommands/export.md\n"
        file_content = b"content"

        with (
            patch("doc_index.run_command_safe") as mock_run,
            patch("doc_index.subprocess.run") as mock_subprocess,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=ls_output)
            mock_subprocess.return_value = MagicMock(returncode=0, stdout=file_content)
            result = get_folder_doc_hashes_from_ref("guides")

        assert result is not None
        assert "guides/setup.md" in result
        assert "guides/ops/health.md" not in result
        assert "commands/export.md" not in result


# ── _get_docs_content_from_ref ───────────────────────────────────────────────


class TestGetDocsContentFromRef:
    def test_returns_content_from_git_ref(self, monkeypatch):
        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)
        ls_output = "guides/setup.md\nguides/intro.rst\n"

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=ls_output)
            return MagicMock(returncode=0, stdout="file content here")

        with patch("doc_index.run_command_safe", side_effect=mock_run):
            result = _get_docs_content_from_ref("guides")

        assert len(result) == 2
        assert result[0]["path"] == "guides/setup.md"
        assert result[0]["content"] == "file content here"

    def test_returns_none_when_git_fails(self, monkeypatch):
        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = _get_docs_content_from_ref("guides")

        assert result is None

    def test_returns_empty_list_when_no_files(self, monkeypatch):
        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)

        with patch("doc_index.run_command_safe") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = _get_docs_content_from_ref("guides")

        assert result == []

    def test_strips_docs_subfolder_from_path(self, monkeypatch):
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        ls_output = "docs/commands/export.md\n"

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=ls_output)
            return MagicMock(returncode=0, stdout="export content")

        with (
            patch("doc_index._get_effective_subfolder", return_value="docs"),
            patch("doc_index.run_command_safe", side_effect=mock_run),
        ):
            result = _get_docs_content_from_ref("commands")

        assert len(result) == 1
        assert result[0]["path"] == "commands/export.md"

    def test_root_level_excludes_subdirectory_files(self, monkeypatch):
        from doc_index import ROOT_LEVEL_FOLDER

        monkeypatch.delenv("DOCS_SUBFOLDER", raising=False)
        ls_output = "README.md\nguides/setup.md\n"

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout=ls_output)
            return MagicMock(returncode=0, stdout="root content")

        with patch("doc_index.run_command_safe", side_effect=mock_run):
            result = _get_docs_content_from_ref(ROOT_LEVEL_FOLDER)

        assert len(result) == 1
        assert result[0]["path"] == "README.md"


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

    def test_remove_index_deletes_file(self, tmp_path):
        save_index("guides", "content", docs_root=tmp_path)
        assert (tmp_path / INDEX_DIR / "guides.index.md").exists()
        assert remove_index("guides", docs_root=tmp_path) is True
        assert not (tmp_path / INDEX_DIR / "guides.index.md").exists()

    def test_remove_index_missing_returns_false(self, tmp_path):
        assert remove_index("nonexistent", docs_root=tmp_path) is False


# ── _handle_empty_folder_on_ref ───────────────────────────────────────────────


class TestHandleEmptyFolderOnRef:
    """Tests for stale index cleanup when docs are deleted from the ref."""

    def _mock_ref_valid(self):
        """Mock run_command_safe so git rev-parse --verify succeeds."""

        def side_effect(cmd, **kwargs):
            if "rev-parse" in cmd and "--verify" in cmd:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="")

        return side_effect

    def _mock_ref_invalid(self):
        """Mock run_command_safe so git rev-parse --verify fails."""

        def side_effect(cmd, **kwargs):
            if "rev-parse" in cmd and "--verify" in cmd:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="")

        return side_effect

    @patch("doc_index.get_docs_in_folder", return_value=[])
    @patch("doc_index.get_folder_doc_hashes_from_ref", return_value={})
    @patch("doc_index.run_command_safe")
    def test_removes_index_when_docs_deleted_from_ref_and_disk(
        self, mock_run, mock_ref_hashes, mock_disk_docs, tmp_path
    ):
        """Folder has no docs on ref AND no docs on disk — safe to remove."""
        mock_run.side_effect = self._mock_ref_valid()
        save_index("guides", "stale index", docs_root=tmp_path)
        manifest = {"version": "1.0", "folders": {"guides": {"doc_hashes": {"g.md": "old"}}}}

        result = _handle_empty_folder_on_ref("guides", manifest, docs_root=tmp_path)

        assert result is True
        assert not (tmp_path / INDEX_DIR / "guides.index.md").exists()
        assert manifest["folders"]["guides"]["doc_hashes"] == {}

    @patch("doc_index.get_folder_doc_hashes_from_ref", return_value={})
    @patch("doc_index.run_command_safe")
    def test_skips_when_disk_still_has_docs(self, mock_run, mock_ref_hashes, tmp_path):
        """Folder has no docs on ref but docs exist on disk — likely ref mismatch."""
        mock_run.side_effect = self._mock_ref_valid()
        save_index("guides", "valid index", docs_root=tmp_path)
        (tmp_path / "guides").mkdir()
        (tmp_path / "guides" / "install.md").write_text("# Install")
        manifest = {"version": "1.0", "folders": {}}

        result = _handle_empty_folder_on_ref("guides", manifest, docs_root=tmp_path)

        assert result is False
        assert (tmp_path / INDEX_DIR / "guides.index.md").exists()

    @patch("doc_index.run_command_safe")
    def test_skips_when_ref_does_not_exist(self, mock_run, tmp_path):
        """Ref doesn't resolve — don't trust empty results."""
        mock_run.side_effect = self._mock_ref_invalid()
        save_index("guides", "valid index", docs_root=tmp_path)
        manifest = {"version": "1.0", "folders": {}}

        result = _handle_empty_folder_on_ref("guides", manifest, docs_root=tmp_path)

        assert result is False
        assert (tmp_path / INDEX_DIR / "guides.index.md").exists()

    @patch("doc_index.get_docs_in_folder", return_value=[])
    @patch("doc_index.get_folder_doc_hashes_from_ref", return_value=None)
    @patch("doc_index.run_command_safe")
    def test_skips_when_ref_hashes_unavailable(
        self, mock_run, mock_ref_hashes, mock_disk_docs, tmp_path
    ):
        """Ref exists but hashes return None — skip cleanup."""
        mock_run.side_effect = self._mock_ref_valid()
        save_index("guides", "valid index", docs_root=tmp_path)
        manifest = {"version": "1.0", "folders": {}}

        result = _handle_empty_folder_on_ref("guides", manifest, docs_root=tmp_path)

        assert result is False
        assert (tmp_path / INDEX_DIR / "guides.index.md").exists()


# ── update_indexes_if_needed ─────────────────────────────────────────────────


class TestUpdateIndexesIfNeeded:
    @patch("doc_index._handle_empty_folder_on_ref", return_value=False)
    @patch("doc_index.get_client")
    @patch("doc_index.build_index_for_folder_with_retry")
    @patch("doc_index.get_folder_doc_hashes_from_ref")
    @patch("doc_index.folder_needs_reindex", return_value=True)
    @patch("doc_index.get_doc_folders", return_value=["guides"])
    def test_normal_rebuild(
        self,
        mock_folders,
        mock_needs,
        mock_ref_hashes,
        mock_build,
        mock_client,
        mock_handle,
        tmp_path,
        monkeypatch,
    ):
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        monkeypatch.setattr("doc_index.get_docs_root", lambda: docs_root)
        save_manifest({"version": "1.0", "folders": {}}, docs_root=docs_root)

        mock_build.return_value = "# New index content"
        mock_ref_hashes.return_value = {"guides/install.md": "newhash"}

        updated = update_indexes_if_needed()

        assert "guides" in updated
        assert load_index("guides", docs_root=docs_root) == "# New index content"
        mock_handle.assert_not_called()

    @patch("doc_index._handle_empty_folder_on_ref", return_value=True)
    @patch("doc_index.get_client")
    @patch("doc_index.build_index_for_folder_with_retry", return_value=None)
    @patch("doc_index.folder_needs_reindex", return_value=True)
    @patch("doc_index.get_doc_folders", return_value=["guides"])
    def test_delegates_to_handle_empty_when_build_returns_none(
        self,
        mock_folders,
        mock_needs,
        mock_build,
        mock_client,
        mock_handle,
        tmp_path,
        monkeypatch,
    ):
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        monkeypatch.setattr("doc_index.get_docs_root", lambda: docs_root)
        save_manifest({"version": "1.0", "folders": {}}, docs_root=docs_root)

        updated = update_indexes_if_needed()

        assert "guides" in updated
        mock_handle.assert_called_once()


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
