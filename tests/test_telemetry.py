"""Tests for telemetry.py -- token usage tracking."""

from unittest.mock import MagicMock

from telemetry import UsageTracker


class TestUsageTracker:
    def test_record_with_usage(self):
        tracker = UsageTracker()
        resp = MagicMock()
        resp.usage.prompt_tokens = 100
        resp.usage.completion_tokens = 50
        tracker.record("generation", resp)
        assert tracker.has_records
        summary = tracker.format_summary()
        assert "100" in summary
        assert "50" in summary
        assert "generation" in summary

    def test_record_without_usage(self):
        tracker = UsageTracker()
        resp = MagicMock(spec=[])  # no usage attribute
        tracker.record("generation", resp)
        summary = tracker.format_summary()
        assert "not reported" in summary

    def test_multiple_stages(self):
        tracker = UsageTracker()
        for stage in ("generation", "verification", "generation"):
            resp = MagicMock()
            resp.usage.prompt_tokens = 100
            resp.usage.completion_tokens = 50
            tracker.record(stage, resp)
        summary = tracker.format_summary()
        assert "generation" in summary
        assert "verification" in summary
        assert "**3**" in summary  # total calls

    def test_format_with_cost(self):
        tracker = UsageTracker(cost_per_1m_input=3.0, cost_per_1m_output=15.0)
        resp = MagicMock()
        resp.usage.prompt_tokens = 1_000_000
        resp.usage.completion_tokens = 100_000
        tracker.record("generation", resp)
        summary = tracker.format_summary()
        assert "$" in summary
        assert "4.5000" in summary  # 3.0 + 1.5

    def test_format_without_cost(self):
        tracker = UsageTracker()
        resp = MagicMock()
        resp.usage.prompt_tokens = 500
        resp.usage.completion_tokens = 100
        tracker.record("generation", resp)
        summary = tracker.format_summary()
        assert "$" not in summary

    def test_empty_tracker(self):
        tracker = UsageTracker()
        assert not tracker.has_records
        assert tracker.format_summary() == ""

    def test_cost_suppressed_when_usage_missing(self):
        tracker = UsageTracker(cost_per_1m_input=3.0, cost_per_1m_output=15.0)
        resp = MagicMock(spec=[])
        tracker.record("generation", resp)
        summary = tracker.format_summary()
        assert "$" not in summary

    def test_details_block_structure(self):
        tracker = UsageTracker()
        resp = MagicMock()
        resp.usage.prompt_tokens = 10
        resp.usage.completion_tokens = 5
        tracker.record("test", resp)
        summary = tracker.format_summary()
        assert summary.startswith("<details>")
        assert "</details>" in summary
        assert "Token usage" in summary
