"""Tests for audit.py -- scheduled drift audit."""

from audit import format_audit_report


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
