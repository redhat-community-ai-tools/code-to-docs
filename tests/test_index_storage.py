"""Tests for index storage backends: cache, PR dispatch, and config integration."""

import os
from pathlib import Path
from unittest.mock import patch

from config import get_index_storage
from doc_index import (
    INDEX_DIR,
    restore_indexes_from_cache,
    save_indexes_to_cache,
)

# ── get_index_storage (config.py) ───────────────────────────────────────────


class TestGetIndexStorage:
    def test_default_is_cache(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_index_storage() == "cache"

    def test_returns_cache(self):
        with patch.dict(os.environ, {"INDEX_STORAGE": "cache"}):
            assert get_index_storage() == "cache"

    def test_returns_pr(self):
        with patch.dict(os.environ, {"INDEX_STORAGE": "pr"}):
            assert get_index_storage() == "pr"

    def test_returns_none(self):
        with patch.dict(os.environ, {"INDEX_STORAGE": "none"}):
            assert get_index_storage() == "none"

    def test_case_insensitive(self):
        with patch.dict(os.environ, {"INDEX_STORAGE": "CACHE"}):
            assert get_index_storage() == "cache"

    def test_invalid_falls_back_to_cache(self):
        with patch.dict(os.environ, {"INDEX_STORAGE": "invalid"}):
            assert get_index_storage() == "cache"


# ── save_indexes_to_cache ───────────────────────────────────────────────────


class TestSaveIndexesToCache:
    def test_saves_indexes_to_cache_path(self, tmp_path):
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        index_dir = docs_root / INDEX_DIR
        index_dir.mkdir()
        (index_dir / "manifest.json").write_text('{"folders": {}}')

        cache_path = str(tmp_path / "cache-target")

        with patch("doc_index.get_docs_root", return_value=docs_root):
            with patch("doc_index._CACHE_MANIFEST_PATH", cache_path):
                result = save_indexes_to_cache()

        assert result is True
        assert Path(cache_path).exists()
        assert (Path(cache_path) / "manifest.json").read_text() == '{"folders": {}}'

    def test_returns_false_when_no_indexes(self, tmp_path):
        docs_root = tmp_path / "docs"
        docs_root.mkdir()

        with patch("doc_index.get_docs_root", return_value=docs_root):
            result = save_indexes_to_cache()

        assert result is False

    def test_overwrites_existing_cache(self, tmp_path):
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        index_dir = docs_root / INDEX_DIR
        index_dir.mkdir()
        (index_dir / "new.json").write_text("new")

        cache_path = str(tmp_path / "cache-target")
        Path(cache_path).mkdir()
        (Path(cache_path) / "old.json").write_text("old")

        with patch("doc_index.get_docs_root", return_value=docs_root):
            with patch("doc_index._CACHE_MANIFEST_PATH", cache_path):
                result = save_indexes_to_cache()

        assert result is True
        assert not (Path(cache_path) / "old.json").exists()
        assert (Path(cache_path) / "new.json").read_text() == "new"


# ── restore_indexes_from_cache ──────────────────────────────────────────────


class TestRestoreIndexesFromCache:
    def test_restores_from_cache_path(self, tmp_path):
        docs_root = tmp_path / "docs"
        docs_root.mkdir()

        cache_path = str(tmp_path / "cache-source")
        Path(cache_path).mkdir()
        (Path(cache_path) / "manifest.json").write_text('{"folders": {}}')

        with patch("doc_index.get_docs_root", return_value=docs_root):
            with patch("doc_index._CACHE_MANIFEST_PATH", cache_path):
                result = restore_indexes_from_cache()

        assert result is True
        assert (docs_root / INDEX_DIR / "manifest.json").read_text() == '{"folders": {}}'

    def test_returns_false_when_no_cache(self, tmp_path):
        cache_path = str(tmp_path / "nonexistent")

        with patch("doc_index._CACHE_MANIFEST_PATH", cache_path):
            result = restore_indexes_from_cache()

        assert result is False

    def test_overwrites_existing_indexes(self, tmp_path):
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        index_dir = docs_root / INDEX_DIR
        index_dir.mkdir()
        (index_dir / "old.json").write_text("old")

        cache_path = str(tmp_path / "cache-source")
        Path(cache_path).mkdir()
        (Path(cache_path) / "new.json").write_text("new")

        with patch("doc_index.get_docs_root", return_value=docs_root):
            with patch("doc_index._CACHE_MANIFEST_PATH", cache_path):
                result = restore_indexes_from_cache()

        assert result is True
        assert not (index_dir / "old.json").exists()
        assert (index_dir / "new.json").read_text() == "new"


# ── commit_indexes_to_repo dispatch ─────────────────────────────────────────


class TestCommitIndexesDispatch:
    def test_none_skips_persistence(self):
        with patch.dict(os.environ, {"INDEX_STORAGE": "none"}):
            from doc_index import commit_indexes_to_repo

            result = commit_indexes_to_repo()
        assert result is False

    def test_cache_delegates_to_save(self, tmp_path):
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        index_dir = docs_root / INDEX_DIR
        index_dir.mkdir()
        (index_dir / "manifest.json").write_text("{}")

        cache_path = str(tmp_path / "cache-target")

        with patch.dict(os.environ, {"INDEX_STORAGE": "cache"}):
            with patch("doc_index.get_docs_root", return_value=docs_root):
                with patch("doc_index._CACHE_MANIFEST_PATH", cache_path):
                    from doc_index import commit_indexes_to_repo

                    result = commit_indexes_to_repo()

        assert result is True
        assert Path(cache_path).exists()


# ── fetch_indexes_from_main guard ───────────────────────────────────────────


class TestFetchIndexesFromMainGuard:
    def test_cache_mode_tries_restore(self, tmp_path):
        """When INDEX_STORAGE=cache, fetch_indexes_from_main tries restore_indexes_from_cache."""
        with patch.dict(os.environ, {"INDEX_STORAGE": "cache"}):
            with patch("doc_index.restore_indexes_from_cache", return_value=True) as mock_restore:
                from doc_index import fetch_indexes_from_main

                result = fetch_indexes_from_main()

        assert result is True
        mock_restore.assert_called_once()

    def test_pr_mode_skips_cache_restore(self, tmp_path):
        """When INDEX_STORAGE=pr, fetch_indexes_from_main skips cache and goes to git."""
        docs_root = tmp_path / "docs"
        docs_root.mkdir()

        with patch.dict(os.environ, {"INDEX_STORAGE": "pr", "DOCS_SUBFOLDER": ""}, clear=False):
            with patch("doc_index.restore_indexes_from_cache") as mock_restore:
                with patch("doc_index.get_docs_root", return_value=docs_root):
                    with patch("doc_index.run_command_safe") as mock_cmd:
                        mock_cmd.return_value.returncode = 1
                        mock_cmd.return_value.stdout = ""
                        from doc_index import fetch_indexes_from_main

                        fetch_indexes_from_main()

        mock_restore.assert_not_called()
