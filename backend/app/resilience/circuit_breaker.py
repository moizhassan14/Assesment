"""Minimal thread-safe circuit breaker (closed → open → half_open → closed).

After ``failure_threshold`` consecutive *retryable* failures (outages, not bad
requests) the breaker opens and calls fail fast with ``circuit_open`` for
``reset_seconds``; then one trial call is allowed (half-open).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from app.resilience.errors import CircuitOpenError


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        reset_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_seconds:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        with self._lock:
            if self._state_locked() == "open":
                assert self._opened_at is not None
                remaining = self.reset_seconds - (self._clock() - self._opened_at)
                raise CircuitOpenError(self.name, max(0.0, remaining))

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self._clock()
