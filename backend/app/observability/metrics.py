"""Thread-safe in-process metrics registry (counters + latency samples).

Deliberately small: it backs ``GET /api/metrics`` and the per-run summary log
line. A production deployment would export the same series to Prometheus/OTel.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any


class MetricsRegistry:
    def __init__(self, max_samples: int = 500) -> None:
        self._lock = threading.Lock()
        self._max_samples = max_samples
        self.started_at = datetime.now(timezone.utc)
        self._runs: dict[str, int] = defaultdict(int)
        self._node_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._node_latency: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self._max_samples))
        self._external: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._breakers: dict[str, Any] = {}

    def run_event(self, status: str) -> None:
        with self._lock:
            self._runs[status] += 1

    def node_finished(self, node: str, status: str, duration_ms: float) -> None:
        with self._lock:
            self._node_counts[node][status] += 1
            self._node_latency[node].append(duration_ms)

    def external_call(
        self,
        key: str,
        *,
        error: bool = False,
        retries: int = 0,
        cache_hit: bool = False,
        cost_usd: float = 0.0,
        calls: int = 1,
    ) -> None:
        with self._lock:
            bucket = self._external[key]
            bucket["calls"] += calls
            bucket["errors"] += 1 if error else 0
            bucket["retries"] += retries
            bucket["cache_hits"] += 1 if cache_hit else 0
            bucket["cost_usd"] += cost_usd

    def register_breaker(self, name: str, breaker: Any) -> None:
        with self._lock:
            self._breakers[name] = breaker

    @staticmethod
    def _latency_summary(samples: deque[float]) -> dict[str, float | int]:
        values = sorted(samples)
        if not values:
            return {"count": 0, "avg": 0.0, "p95": 0.0, "max": 0.0}
        p95_index = max(0, int(round(0.95 * (len(values) - 1))))
        return {
            "count": len(values),
            "avg": round(sum(values) / len(values), 1),
            "p95": round(values[p95_index], 1),
            "max": round(values[-1], 1),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            nodes = {
                name: {
                    "success": counts.get("success", 0),
                    "failed": counts.get("failed", 0),
                    "degraded": counts.get("degraded", 0),
                    "skipped": counts.get("skipped", 0),
                    "latency_ms": self._latency_summary(self._node_latency[name]),
                }
                for name, counts in self._node_counts.items()
            }
            external = {
                key: {
                    "calls": int(v["calls"]),
                    "errors": int(v["errors"]),
                    "retries": int(v["retries"]),
                    "cache_hits": int(v["cache_hits"]),
                    "cost_usd": round(v["cost_usd"], 6),
                }
                for key, v in self._external.items()
            }
            return {
                "started_at": self.started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "runs": {
                    "started": self._runs.get("started", 0),
                    "success": self._runs.get("success", 0),
                    "partial_success": self._runs.get("partial_success", 0),
                    "failed": self._runs.get("failed", 0),
                },
                "nodes": nodes,
                "external_calls": external,
                "circuit_breakers": {name: b.state for name, b in self._breakers.items()},
            }
