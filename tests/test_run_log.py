"""Tests for run_log.py -- structured JSONL run log."""

import json
from unittest.mock import MagicMock

from run_log import RunLog


class TestRunLog:
    def test_writes_jsonl_record(self, tmp_path):
        log = RunLog(path=str(tmp_path / "test.jsonl"))
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        log.record("generation", "docs/guide.md", "prompt text", "response text", usage, 1500, "ok")
        assert log.has_entries
        lines = (tmp_path / "test.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["stage"] == "generation"
        assert entry["file_path"] == "docs/guide.md"
        assert entry["prompt_tokens"] == 100
        assert entry["latency_ms"] == 1500

    def test_excludes_prompts_by_default(self, tmp_path):
        log = RunLog(path=str(tmp_path / "test.jsonl"))
        log.record("generation", "f.md", "secret prompt", "secret response", None, 100, "ok")
        entry = json.loads((tmp_path / "test.jsonl").read_text().strip())
        assert "prompt" not in entry
        assert "response" not in entry

    def test_includes_prompts_when_enabled(self, tmp_path):
        log = RunLog(path=str(tmp_path / "test.jsonl"), include_prompts=True)
        log.record("generation", "f.md", "the prompt", "the response", None, 100, "ok")
        entry = json.loads((tmp_path / "test.jsonl").read_text().strip())
        assert entry["prompt"] == "the prompt"
        assert entry["response"] == "the response"

    def test_empty_log_has_no_entries(self, tmp_path):
        log = RunLog(path=str(tmp_path / "test.jsonl"))
        assert not log.has_entries

    def test_handles_none_usage(self, tmp_path):
        log = RunLog(path=str(tmp_path / "test.jsonl"))
        log.record("generation", "f.md", "p", "r", None, 100, "ok")
        entry = json.loads((tmp_path / "test.jsonl").read_text().strip())
        assert entry["prompt_tokens"] is None
        assert entry["completion_tokens"] is None
