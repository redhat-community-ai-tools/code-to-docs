"""Token usage tracking and cost estimation for code-to-docs."""

import threading


class UsageTracker:
    """Thread-safe accumulator for LLM API token usage.

    Records prompt and completion tokens per call, tagged by stage.
    Optionally computes estimated cost when per-token pricing is provided.
    """

    def __init__(self, cost_per_1m_input=None, cost_per_1m_output=None):
        self._records = []
        self._lock = threading.Lock()
        self._cost_input = cost_per_1m_input
        self._cost_output = cost_per_1m_output

    def record(self, stage, response):
        """Extract usage from an OpenAI-compatible response and store it."""
        usage = getattr(response, "usage", None)
        prompt_tokens = None
        completion_tokens = None
        if usage:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
        with self._lock:
            self._records.append(
                {
                    "stage": stage,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
            )

    @property
    def has_records(self):
        return len(self._records) > 0

    def _aggregate(self):
        """Aggregate token counts by stage."""
        stages = {}
        for r in self._records:
            s = r["stage"]
            if s not in stages:
                stages[s] = {"calls": 0, "prompt": 0, "completion": 0, "reported": True}
            stages[s]["calls"] += 1
            if r["prompt_tokens"] is not None:
                stages[s]["prompt"] += r["prompt_tokens"]
            else:
                stages[s]["reported"] = False
            if r["completion_tokens"] is not None:
                stages[s]["completion"] += r["completion_tokens"]
            else:
                stages[s]["reported"] = False
        return stages

    def format_summary(self):
        """Return a Markdown <details> block with per-stage token breakdown."""
        if not self._records:
            return ""

        stages = self._aggregate()
        total_prompt = 0
        total_completion = 0
        all_reported = True

        lines = []
        lines.append("| Stage | Calls | Input tokens | Output tokens |")
        lines.append("|-------|------:|-------------:|--------------:|")
        for stage, data in stages.items():
            if data["reported"]:
                lines.append(
                    f"| {stage} | {data['calls']} | {data['prompt']:,} | {data['completion']:,} |"
                )
                total_prompt += data["prompt"]
                total_completion += data["completion"]
            else:
                lines.append(f"| {stage} | {data['calls']} | not reported | not reported |")
                all_reported = False

        lines.append(
            f"| **Total** | **{len(self._records)}** "
            f"| **{total_prompt:,}** | **{total_completion:,}** |"
        )

        if self._cost_input and self._cost_output and all_reported:
            cost = (total_prompt / 1_000_000) * self._cost_input + (
                total_completion / 1_000_000
            ) * self._cost_output
            lines.append(f"\nEstimated cost: ${cost:.4f}")

        table = "\n".join(lines)
        return f"<details>\n<summary>Token usage</summary>\n\n{table}\n\n</details>"
