"""Per-run spend guard for paid provider calls.

Each call first *reserves* a conservative cost estimate (documented live prices, rounded up), so
parallel retrieval branches cannot jointly overshoot ``MAX_RUN_COST_USD``; the actual
provider-reported cost replaces the estimate when the call settles.

Local caps are *safeguards*: they bound what a single run can spend, but they are not a view
of the provider account's real balance.
"""

from __future__ import annotations

import threading

from app.resilience.errors import BudgetExceededError

# Upper-bound estimates per call (USD), from DataForSEO's documented live prices (2026-11-18):
# Labs: $0.012/task + $0.00012/item (≤ 30 items); SERP: $0.002 per 10 results (depth ≤ 20);
# ChatGPT LLM responses: $0.0006/task + model cost (web search ≈ $0.03).
ESTIMATED_CALL_COST_USD: dict[str, float] = {
    "keyword_discovery": 0.016,
    "keyword_metrics": 0.014,
    "serp": 0.004,
    "ai_visibility": 0.04,
}


class RunBudget:
    def __init__(self, limits: dict[str, int], max_cost_usd: float, estimates: dict[str, float] | None = None) -> None:
        self.limits = dict(limits)
        self.max_cost_usd = max_cost_usd
        self.estimates = dict(ESTIMATED_CALL_COST_USD if estimates is None else estimates)
        self._used: dict[str, int] = {k: 0 for k in limits}
        self._cost = 0.0
        self._pending = 0.0
        self._lock = threading.Lock()

    def reserve(self, key: str) -> float:
        """Reserve one call of ``key``; returns the reserved cost estimate (pass it to :meth:`settle`)."""
        with self._lock:
            limit = self.limits.get(key, 0)
            if self._used.get(key, 0) >= limit:
                raise BudgetExceededError(f"Per-run limit reached for '{key}' calls ({limit}).")
            estimate = self.estimates.get(key, 0.0)
            if self.max_cost_usd > 0 and self._cost + self._pending + estimate > self.max_cost_usd:
                raise BudgetExceededError(
                    f"Per-run cost ceiling would be exceeded (spent ${self._cost:.4f} + reserved ${self._pending:.4f} "
                    f"+ next ≈${estimate:.4f} > ${self.max_cost_usd:.2f})."
                )
            self._used[key] = self._used.get(key, 0) + 1
            self._pending += estimate
            return estimate

    def settle(self, reserved: float, actual_cost_usd: float) -> None:
        with self._lock:
            self._pending = max(0.0, self._pending - reserved)
            self._cost += max(0.0, actual_cost_usd)

    def add_cost(self, cost_usd: float) -> None:
        self.settle(0.0, cost_usd)

    @property
    def cost_usd(self) -> float:
        with self._lock:
            return round(self._cost, 6)

    def used(self, key: str) -> int:
        with self._lock:
            return self._used.get(key, 0)
