"""Tests for URL construction and path helpers in suggest_docs.py."""

from comments import get_docs_file_url


class TestGetDocsFileUrl:
    """Tests for get_docs_file_url().

    get_docs_repo_url() reads DOCS_REPO_URL from env at call time (not import),
    so monkeypatch.setenv is sufficient.
    """

    def test_separate_repo_https(self, monkeypatch):
        monkeypatch.setenv("DOCS_REPO_URL", "https://github.com/org/docs.git")
        url = get_docs_file_url("guide.md")
        assert url == "https://github.com/org/docs/blob/main/guide.md"

    def test_separate_repo_ssh(self, monkeypatch):
        monkeypatch.setenv("DOCS_REPO_URL", "git@github.com:org/docs.git")
        url = get_docs_file_url("guide.md")
        assert url == "https://github.com/org/docs/blob/main/guide.md"

    def test_same_repo_with_subfolder(self, monkeypatch):
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        commit_info = {"repo_url": "https://github.com/org/code"}
        url = get_docs_file_url("guide.md", commit_info=commit_info)
        assert url == "https://github.com/org/code/blob/main/docs/guide.md"

    def test_same_repo_no_double_prefix(self, monkeypatch):
        monkeypatch.setenv("DOCS_SUBFOLDER", "docs")
        commit_info = {"repo_url": "https://github.com/org/code"}
        url = get_docs_file_url("docs/guide.md", commit_info=commit_info)
        # Should not produce docs/docs/guide.md
        assert url == "https://github.com/org/code/blob/main/docs/guide.md"

    def test_custom_base_branch(self, monkeypatch):
        monkeypatch.setenv("DOCS_REPO_URL", "https://github.com/org/docs.git")
        monkeypatch.setenv("DOCS_BASE_BRANCH", "develop")
        url = get_docs_file_url("guide.md")
        assert "/blob/develop/" in url

    def test_no_repo_url_returns_none(self):
        # DOCS_REPO_URL is cleared by clean_env fixture
        url = get_docs_file_url("guide.md")
        assert url is None

    def test_no_commit_info_no_subfolder(self, monkeypatch):
        monkeypatch.setenv("DOCS_REPO_URL", "https://github.com/org/docs.git")
        url = get_docs_file_url("api/reference.adoc")
        assert url.endswith("/api/reference.adoc")


class TestGetDocsFileUrlEdgeCases:
    """Edge cases for URL construction."""

    def test_url_without_git_suffix(self, monkeypatch):
        monkeypatch.setenv("DOCS_REPO_URL", "https://github.com/org/docs")
        url = get_docs_file_url("guide.md")
        assert "//blob" not in url
        assert url == "https://github.com/org/docs/blob/main/guide.md"
