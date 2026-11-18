"""Retry with capped exponential backoff and full jitter.

``delay = uniform(0, min(max_delay, base * 2 ** (attempt - 1)))`` — the AWS
"full jitter" scheme, which avoids synchronized retry storms. A provider-supplied
``Retry-After`` takes precedence when present (still capped by ``max_delay``).
Only :class:`ExternalServiceError` instances with ``retryable=True`` are retried.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from app.resilience.errors import ExternalServiceError

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 20.0

    def backoff(self, attempt: int, retry_after: float | None = None, rng: random.Random | None = None) -> float:
        """Delay before attempt ``attempt + 1`` (``attempt`` is 1-based)."""
        if retry_after is not None and retry_after >= 0:
            return min(self.max_delay_seconds, retry_after)
        cap = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 1)))
        return (rng or random).uniform(0, cap)


@dataclass
class RetryOutcome:
    attempts: int
    retries: int


def call_with_retry(
    fn: Callable[[int], T],
    policy: RetryPolicy,
    *,
    on_retry: Callable[[int, ExternalServiceError, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, RetryOutcome]:
    """Run ``fn(attempt)`` until it succeeds, a non-retryable error occurs, or attempts run out.

    The final error is re-raised with ``details["attempts"]`` set so callers can
    report how hard we tried.
    """
    attempt = 1
    while True:
        try:
            result = fn(attempt)
            return result, RetryOutcome(attempts=attempt, retries=attempt - 1)
        except ExternalServiceError as exc:
            exc.details["attempts"] = attempt
            if not exc.retryable or attempt >= policy.max_attempts:
                raise
            delay = policy.backoff(attempt, exc.retry_after_seconds)
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            sleep(delay)
            attempt += 1
