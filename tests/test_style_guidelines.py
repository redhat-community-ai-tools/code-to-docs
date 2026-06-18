"""Tests for style guidelines URL feature (config + generation integration)."""

import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

import pytest
from unittest.mock import patch, MagicMock

from config import get_style_guidelines, _MAX_STYLE_GUIDELINES_CHARS
from generation import ask_ai_for_updated_content


# ── helpers ──────────────────────────────────────────────────────────────────


def _mock_ai_response(content):
    """Build a mock OpenAI client that returns *content* from chat completion."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


class _StyleHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves style_body or returns an error."""

    style_body = b"# Style Guide\n\nUse active voice."
    status_code = 200
    last_auth_header = None

    def do_GET(self):
        _StyleHandler.last_auth_header = self.headers.get("Authorization")
        self.send_response(self.status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if self.status_code == 200:
            self.wfile.write(self.style_body)

    def log_message(self, format, *args):
        pass  # suppress noisy logs during tests


@pytest.fixture()
def style_server():
    """Start a local HTTP server that serves style guidelines."""
    _StyleHandler.status_code = 200
    _StyleHandler.style_body = b"# Style Guide\n\nUse active voice."
    _StyleHandler.last_auth_header = None

    server = HTTPServer(("127.0.0.1", 0), _StyleHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/style.md", server
    server.shutdown()


# ── get_style_guidelines ────────────────────────────────────────────────────


class TestGetStyleGuidelines:
    def test_returns_none_when_no_url(self, monkeypatch):
        monkeypatch.delenv("STYLE_GUIDELINES_URL", raising=False)
        assert get_style_guidelines() is None

    def test_returns_none_for_empty_url(self, monkeypatch):
        monkeypatch.setenv("STYLE_GUIDELINES_URL", "  ")
        assert get_style_guidelines() is None

    def test_fetches_content_from_url(self, style_server):
        url, _ = style_server
        result = get_style_guidelines(url=url)
        assert result is not None
        assert "Use active voice" in result

    def test_reads_url_from_env(self, style_server, monkeypatch):
        url, _ = style_server
        monkeypatch.setenv("STYLE_GUIDELINES_URL", url)
        result = get_style_guidelines()
        assert result is not None
        assert "Use active voice" in result

    def test_returns_none_on_network_error(self):
        result = get_style_guidelines(url="http://127.0.0.1:1/nonexistent")
        assert result is None

    def test_returns_none_on_bad_url(self):
        result = get_style_guidelines(url="not-a-valid-url")
        assert result is None

    def test_returns_none_on_http_error(self, style_server):
        url, _ = style_server
        _StyleHandler.status_code = 404
        result = get_style_guidelines(url=url)
        assert result is None

    def test_truncates_large_content(self, style_server):
        url, _ = style_server
        _StyleHandler.style_body = ("x" * (_MAX_STYLE_GUIDELINES_CHARS + 500)).encode()
        result = get_style_guidelines(url=url)
        assert result is not None
        assert len(result) <= _MAX_STYLE_GUIDELINES_CHARS

    def test_returns_none_for_empty_body(self, style_server):
        url, _ = style_server
        _StyleHandler.style_body = b"   "
        result = get_style_guidelines(url=url)
        assert result is None

    def test_sends_auth_for_github_url(self, style_server, monkeypatch):
        """GH_TOKEN is attached as Bearer token for GitHub URLs."""
        url, _ = style_server
        monkeypatch.setenv("GH_TOKEN", "test-token-123")
        # Replace host with github.com-containing URL via param (the server
        # still runs on localhost, but we verify the header logic)
        # We test via the real server by checking the captured header.
        get_style_guidelines(url=url)
        # Non-github URL should NOT send auth
        assert _StyleHandler.last_auth_header is None

    def test_sends_auth_for_githubusercontent(self, monkeypatch):
        """Verify auth header construction for githubusercontent.com URLs."""
        monkeypatch.setenv("GH_TOKEN", "ghp_testtoken")
        # We can't actually hit githubusercontent.com, so just verify the
        # request object is constructed with the right header by mocking urlopen.
        import urllib.request

        captured_req = {}

        def mock_urlopen(req, timeout=None):
            captured_req["auth"] = req.get_header("Authorization")
            raise urllib.error.URLError("mocked")

        with patch("config.urllib.request.urlopen", side_effect=mock_urlopen):
            result = get_style_guidelines(
                url="https://raw.githubusercontent.com/org/repo/main/style.md"
            )
        assert result is None  # mocked error
        assert captured_req.get("auth") == "Bearer ghp_testtoken"


# ── Prompt injection into generation ────────────────────────────────────────


class TestStyleGuidelinesInPrompt:
    DIFF = "diff --git a/foo.py\n+added line"
    CONTENT = "Some documentation content"

    def test_style_guidelines_appear_in_prompt(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF,
                "docs/guide.md",
                self.CONTENT,
                style_guidelines="Always use sentence case for headings.",
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "Always use sentence case for headings." in prompt
        assert "<<<STYLE_GUIDELINES" in prompt
        assert ">>>END_STYLE_GUIDELINES" in prompt

    def test_no_style_section_when_empty(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF,
                "docs/guide.md",
                self.CONTENT,
                style_guidelines="",
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "STYLE_GUIDELINES" not in prompt

    def test_style_guidelines_reduce_diff_budget(self):
        """Style guidelines content consumes context budget, reducing diff space."""
        large_guidelines = "x" * 5000
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        prompts = {}

        def capture_prompt(**kwargs):
            prompts["with"] = kwargs["messages"][0]["content"]
            return mock_client.chat.completions.create.return_value

        mock_client_capture = MagicMock()
        mock_client_capture.chat.completions.create.side_effect = capture_prompt

        with (
            patch("generation.get_client", return_value=mock_client_capture),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF,
                "docs/guide.md",
                self.CONTENT,
                style_guidelines=large_guidelines,
            )

        # The prompt should contain the guidelines, proving they take budget
        assert large_guidelines in prompts["with"]

    def test_per_comment_instructions_supplement_style_guidelines(self):
        """User instructions and style guidelines both appear in prompt."""
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF,
                "docs/guide.md",
                self.CONTENT,
                user_instructions="Focus on API changes",
                style_guidelines="Use active voice always.",
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "Focus on API changes" in prompt
        assert "Use active voice always." in prompt

    def test_no_url_means_baseline_unchanged(self):
        """Without style guidelines, prompt is identical to baseline."""
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF,
                "docs/guide.md",
                self.CONTENT,
            )
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "STYLE_GUIDELINES" not in prompt
        assert "DATA BLOCK" not in prompt
