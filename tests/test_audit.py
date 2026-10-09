"""Tests for audit.py -- scheduled drift audit."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from audit import _assess_doc, format_audit_report, post_audit_issue, run_audit


class TestFormatAuditReport:
    def test_no_findings(self):
        assert "No documentation drift" in format_audit_report([])

    def test_groups_by_severity(self):
        findings = [
            {"file": "a.md", "severity": "stale", "reason": "outdated"},
            {"file": "b.md", "severity": "very-stale", "reason": "missing feature"},
            {"file": "c.md", "severity": "stale", "reason": "old defaults"},
        ]
        report = format_audit_report(findings)
        assert "## Very Stale (1 file)" in report
        assert "## Stale (2 files)" in report
        assert "b.md" in report
        assert "a.md" in report

    def test_skips_empty_groups(self):
        findings = [{"file": "a.md", "severity": "stale", "reason": "outdated"}]
        report = format_audit_report(findings)
        assert "Very Stale" not in report
        assert "Stale" in report


class TestAssessDoc:
    @patch("audit.get_client")
    @patch("audit.get_model_name", return_value="test-model")
    @patch("audit.get_max_context_chars", return_value=400000)
    @patch("audit.truncate_content", side_effect=lambda p, b, label="": p)
    def test_parses_fresh_response(self, _trunc, _max, _model, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="FRESH: looks good"))]
        mock_client.return_value.chat.completions.create.return_value = mock_response

        severity, reason = _assess_doc("readme.md", "some content", "index info")
        assert severity == "fresh"
        assert reason == "looks good"

    @patch("audit.get_client")
    @patch("audit.get_model_name", return_value="test-model")
    @patch("audit.get_max_context_chars", return_value=400000)
    @patch("audit.truncate_content", side_effect=lambda p, b, label="": p)
    def test_parses_stale_response(self, _trunc, _max, _model, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="STALE: refers to old defaults"))
        ]
        mock_client.return_value.chat.completions.create.return_value = mock_response

        severity, reason = _assess_doc("config.md", "old content", "")
        assert severity == "stale"
        assert reason == "refers to old defaults"

    @patch("audit.get_client")
    @patch("audit.get_model_name", return_value="test-model")
    @patch("audit.get_max_context_chars", return_value=400000)
    @patch("audit.truncate_content", side_effect=lambda p, b, label="": p)
    def test_parses_very_stale_response(self, _trunc, _max, _model, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="VERY-STALE: entirely wrong"))]
        mock_client.return_value.chat.completions.create.return_value = mock_response

        severity, reason = _assess_doc("old.md", "wrong content", "")
        assert severity == "very-stale"
        assert reason == "entirely wrong"

    @patch("audit.get_client")
    @patch("audit.get_model_name", return_value="test-model")
    @patch("audit.get_max_context_chars", return_value=400000)
    @patch("audit.truncate_content", side_effect=lambda p, b, label="": p)
    def test_substring_false_positive_avoided(self, _trunc, _max, _model, mock_client):
        """Regression: 'FRESH: docs are not stale' should not match STALE."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="FRESH: docs are not stale"))]
        mock_client.return_value.chat.completions.create.return_value = mock_response

        severity, reason = _assess_doc("readme.md", "content", "")
        assert severity == "fresh"

    @patch("audit.get_client")
    @patch("audit.get_model_name", return_value="test-model")
    @patch("audit.get_max_context_chars", return_value=400000)
    @patch("audit.truncate_content", side_effect=lambda p, b, label="": p)
    def test_unparseable_response_defaults_to_stale(self, _trunc, _max, _model, mock_client):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="I'm not sure about this document"))
        ]
        mock_client.return_value.chat.completions.create.return_value = mock_response

        severity, reason = _assess_doc("mystery.md", "content", "")
        assert severity == "stale"

    @patch("audit.check_context_error")
    @patch("audit.get_client")
    @patch("audit.get_model_name", return_value="test-model")
    @patch("audit.get_max_context_chars", return_value=400000)
    @patch("audit.truncate_content", side_effect=lambda p, b, label="": p)
    def test_api_error_returns_fresh(self, _trunc, _max, _model, mock_client, mock_check):
        mock_client.return_value.chat.completions.create.side_effect = RuntimeError("API down")

        severity, reason = _assess_doc("error.md", "content", "")
        assert severity == "fresh"
        assert reason == ""
        mock_check.assert_called_once()


class TestRunAudit:
    @patch("audit.load_manifest", return_value={"folders": {}})
    @patch("audit.get_doc_folders", return_value=["guides"])
    @patch("audit.get_docs_root")
    @patch("audit._assess_doc", return_value=("stale", "outdated"))
    def test_respects_budget(self, mock_assess, mock_root, mock_folders, mock_manifest, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        guides_dir = docs_dir / "guides"
        guides_dir.mkdir()
        (guides_dir / "a.md").write_text("doc a")
        (guides_dir / "b.md").write_text("doc b")
        (guides_dir / "c.md").write_text("doc c")

        mock_root.return_value = docs_dir

        with patch(
            "audit.get_docs_in_folder",
            return_value=[guides_dir / "a.md", guides_dir / "b.md", guides_dir / "c.md"],
        ):
            findings = run_audit(max_files=2)

        assert len(findings) == 2
        assert mock_assess.call_count == 2

    @patch("audit.load_manifest", return_value={"folders": {}})
    @patch("audit.get_doc_folders", return_value=["guides"])
    @patch("audit.get_docs_root")
    @patch("audit._assess_doc", return_value=("fresh", ""))
    def test_excludes_fresh_from_findings(
        self, mock_assess, mock_root, mock_folders, mock_manifest, tmp_path
    ):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        guides_dir = docs_dir / "guides"
        guides_dir.mkdir()
        (guides_dir / "a.md").write_text("doc a")

        mock_root.return_value = docs_dir

        with patch("audit.get_docs_in_folder", return_value=[guides_dir / "a.md"]):
            findings = run_audit(max_files=10)

        assert findings == []

    def test_invalid_max_files_defaults(self, capsys):
        with (
            patch("audit.get_docs_root") as mock_root,
            patch("audit.get_doc_folders", return_value=[]),
            patch("audit.load_manifest", return_value={}),
        ):
            mock_root.return_value = Path("/fake")
            findings = run_audit(max_files=0)
        assert findings == []
        assert "must be at least 1" in capsys.readouterr().out


class TestPostAuditIssue:
    @patch.dict("os.environ", {"GH_TOKEN": "", "GITHUB_REPOSITORY": "org/repo"}, clear=False)
    def test_missing_gh_token_warns(self, capsys):
        post_audit_issue([{"file": "a.md", "severity": "stale", "reason": "old"}])
        assert "GH_TOKEN not set" in capsys.readouterr().out

    @patch.dict("os.environ", {"GH_TOKEN": "tok123", "GITHUB_REPOSITORY": ""}, clear=False)
    def test_missing_repo_warns(self, capsys):
        post_audit_issue([{"file": "a.md", "severity": "stale", "reason": "old"}], repo="")
        assert "GITHUB_REPOSITORY not set" in capsys.readouterr().out

    @patch("audit.run_command_safe")
    @patch.dict("os.environ", {"GH_TOKEN": "tok123"}, clear=False)
    def test_creates_new_issue(self, mock_run, capsys):
        # Label create succeeds
        mock_run.side_effect = [
            MagicMock(returncode=0),  # label create
            MagicMock(returncode=0, stdout="[]"),  # issue list (empty)
            MagicMock(returncode=0, stdout="https://github.com/org/repo/issues/1"),  # issue create
        ]
        post_audit_issue(
            [{"file": "a.md", "severity": "stale", "reason": "old"}],
            repo="org/repo",
        )
        # Verify --repo is passed to all gh commands
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert "--repo" in cmd, f"--repo missing from: {cmd}"
            assert "org/repo" in cmd

    @patch("audit.run_command_safe")
    @patch.dict("os.environ", {"GH_TOKEN": "tok123"}, clear=False)
    def test_updates_existing_issue(self, mock_run, capsys):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # label create
            MagicMock(returncode=0, stdout=json.dumps([{"number": 42}])),  # issue list
            MagicMock(returncode=0),  # issue edit
        ]
        post_audit_issue(
            [{"file": "a.md", "severity": "stale", "reason": "old"}],
            repo="org/repo",
        )
        assert "Updated existing audit issue #42" in capsys.readouterr().out
