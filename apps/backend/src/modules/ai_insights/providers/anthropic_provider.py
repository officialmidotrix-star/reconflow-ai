"""
Cloud AI provider - the MVP implementation, per the Phase 1 decision that
a cloud API is acceptable as long as it only ever receives computed
figures, never raw PII, and the architecture stays swappable for a
self-hosted model later.

Not exercised by the automated test suite. A unit test suite should never
depend on a live, paid, non-deterministic external API - tests use
FakeAIProvider (dependencies.py) instead. This class exists so the
approved architecture decision has a real implementation behind it, ready
to wire in at application start-up once real credentials exist.
"""

from __future__ import annotations

import os
from decimal import Decimal

from ..dependencies import AnalysisFacts

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"


def _build_prompt(facts: AnalysisFacts) -> str:
    lines = [
        "You are writing a short, plain-language executive summary of a restaurant's",
        "delivery-platform reconciliation results, for a restaurant owner who is not",
        "a financial expert.",
        "",
        "Use ONLY the figures listed below. Do not introduce any number, date,",
        "percentage, or comparison that isn't explicitly given here, and do not",
        "perform any calculations of your own - every figure you need is already",
        "computed.",
        "",
        f"Total discrepancies found: {facts.total_discrepancies}",
        f"Critical severity: {facts.critical_count}",
        f"High severity: {facts.high_count}",
        f"Medium severity: {facts.medium_count}",
        f"Low severity: {facts.low_count}",
        f"Total estimated financial impact: {facts.total_estimated_loss}",
        "",
        "Breakdown by category:",
    ]
    for category, count in facts.category_breakdown.items():
        loss = facts.category_loss_breakdown.get(category, Decimal("0.00"))
        lines.append(f"- {category}: {count} occurrence(s), estimated impact {loss}")
    lines.append("")
    lines.append("Write 3-5 sentences summarizing the findings and their significance.")
    return "\n".join(lines)


class AnthropicAIProvider:
    provider_name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not configured - set the environment variable "
                "or pass api_key explicitly."
            )
        self.model_name = model
        self._timeout = timeout

    def generate_summary(self, facts: AnalysisFacts) -> str:
        import httpx  # imported here, not at module load, since this path
        # is only exercised when this provider is actually selected.

        response = httpx.post(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model_name,
                "max_tokens": 500,
                "messages": [{"role": "user", "content": _build_prompt(facts)}],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        text_blocks = [
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        ]
        return "".join(text_blocks).strip()
