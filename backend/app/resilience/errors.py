"""Error taxonomy shared by every external integration.

The ``code`` values are part of the public API contract (run-level error codes).
``retryable`` drives the retry policy: only transient failures are retried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RETRYABLE_CODES = frozenset(
    {
        "provider_rate_limited",
        "provider_timeout",
        "provider_network_error",
        "provider_server_error",
        "llm_unavailable",
        "llm_rate_limited",
    }
)


@dataclass(eq=False)
class ExternalServiceError(Exception):
    """A classified failure from an external dependency (DataForSEO or the LLM)."""

    code: str
    message: str
    provider: str
    http_status: int | None = None
    provider_status_code: int | None = None
    retry_after_seconds: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    no_retry: bool = False  # set by callers whose policy forbids retrying this error (e.g. chargeable LLM calls)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES and not self.no_retry

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "provider": self.provider,
            "http_status": self.http_status,
            "provider_status_code": self.provider_status_code,
        }


class BudgetExceededError(ExternalServiceError):
    def __init__(self, message: str, provider: str = "dataforseo") -> None:
        super().__init__(code="budget_exceeded", message=message, provider=provider)


class CircuitOpenError(ExternalServiceError):
    def __init__(self, provider: str, retry_in_seconds: float) -> None:
        super().__init__(
            code="circuit_open",
            message=f"{provider} circuit breaker is open after repeated failures; retry in {retry_in_seconds:.0f}s.",
            provider=provider,
            retry_after_seconds=retry_in_seconds,
        )


class LLMOutputError(Exception):
    """The LLM answered, but the output failed validation (never retried blindly)."""

    def __init__(self, message: str, *, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []
