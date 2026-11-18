"""Provider-neutral LLM tool-calling interface.

Agents only depend on this module. A *conversation* is used so a planner can
send structured validation feedback back to the model (one repair turn) when its
tool-call arguments are rejected.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.resilience.circuit_breaker import CircuitBreaker
from app.resilience.errors import ExternalServiceError
from app.resilience.retry import RetryPolicy, call_with_retry
from app.tools.schemas import ToolSpec
from app.tools.validation import RawToolCall


@dataclass
class LLMResponse:
    tool_calls: list[RawToolCall]
    text: str
    stop_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    attempts: int = 1


@dataclass
class ToolFeedback:
    call_id: str
    ok: bool
    message: str


RetryObserver = Callable[[int, ExternalServiceError, float], None]


class ToolConversation(Protocol):
    def ask(self) -> LLMResponse: ...

    def prime(self, response: LLMResponse) -> None:
        """Adopt a (cached) response as the last assistant turn without calling the API."""
        ...

    def reply(self, feedback: Sequence[ToolFeedback], instruction: str) -> LLMResponse: ...


class LLMClient(Protocol):
    provider: str
    model: str

    def conversation(
        self,
        *,
        system: str,
        user: str,
        tools: Sequence[ToolSpec],
        effort: str = "low",
        required_tool: str | None = None,
        on_retry: RetryObserver | None = None,
    ) -> ToolConversation: ...


@dataclass
class GuardedCaller:
    """Shared retry + circuit-breaker wrapper used by every LLM adapter."""

    provider: str
    policy: RetryPolicy
    breaker: CircuitBreaker
    sleep: Callable[[float], None] = time.sleep
    calls: int = field(default=0)
    # Codes that may be retried. Chargeable LLM calls are never retried after a timeout or an
    # ambiguous network/5xx failure (the request may already be billed); only 429 is safe.
    retry_codes: frozenset[str] = frozenset({"llm_rate_limited"})

    def run(self, fn: Callable[[], Any], on_retry: RetryObserver | None) -> tuple[Any, int, int]:
        started = time.perf_counter()

        def attempt(_n: int) -> Any:
            self.breaker.before_call()
            try:
                result = fn()
            except ExternalServiceError as exc:
                if exc.retryable:
                    self.breaker.record_failure()
                if exc.code not in self.retry_codes:
                    exc.no_retry = True
                raise
            self.breaker.record_success()
            return result

        result, outcome = call_with_retry(attempt, self.policy, on_retry=on_retry, sleep=self.sleep)
        self.calls += outcome.attempts
        return result, int((time.perf_counter() - started) * 1000), outcome.attempts
