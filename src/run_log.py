"""Structured JSONL run log for code-to-docs.

Writes one record per LLM call so failed runs can be debugged without
re-running. Optionally includes full prompt/response text behind the
debug-artifacts flag.
"""

import json
import threading
import time
from pathlib import Path

from security_utils import sanitize_output


class RunLog:
    """Append-only JSONL log of LLM calls."""

    def __init__(self, path="/tmp/code-to-docs-run.jsonl", include_prompts=False):
        self._path = Path(path)
        self._include_prompts = include_prompts
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._path.unlink()

    @property
    def path(self):
        return str(self._path)

    @property
    def has_entries(self):
        return self._path.exists() and self._path.stat().st_size > 0

    def record(self, stage, file_path, prompt, response_text, usage_obj, latency_ms, outcome):
        """Write a single log record."""
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stage": stage,
            "file_path": file_path or "",
            "prompt_length": len(prompt) if prompt else 0,
            "response_length": len(response_text) if response_text else 0,
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", None) if usage_obj else None,
            "completion_tokens": (
                getattr(usage_obj, "completion_tokens", None) if usage_obj else None
            ),
            "latency_ms": round(latency_ms),
            "outcome": outcome,
        }

        if self._include_prompts:
            entry["prompt"] = sanitize_output(prompt or "")
            entry["response"] = sanitize_output(response_text or "")

        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
