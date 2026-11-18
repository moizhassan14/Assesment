"""Provider client error classification, retry policy, circuit breaker and log redaction."""

from __future__ import annotations

import json
import logging
import random

import httpx
import pytest

from app.integrations.dataforseo.client import DataForSEOClient
from app.observability.logging import JsonFormatter, redact
from app.resilience.circuit_breaker import CircuitBreaker
from app.resilience.errors import ExternalServiceError
from app.resilience.retry import RetryPolicy
from tests.fakes import envelope


def _client(handler, attempts: int = 3) -> DataForSEOClient:  # type: ignore[no-untyped-def]
    return DataForSEOClient("login", "secret-password", http=httpx.Client(transport=httpx.MockTransport(handler)),
                            retry_policy=RetryPolicy(attempts, 0, 0), breaker_factory=lambda n: CircuitBreaker(n, 10, 60), sleep=lambda _s: None)


@pytest.mark.parametrize(
    ("response", "code", "expected_attempts"),
    [
        (httpx.Response(401), "provider_auth_failed", 1),
        (httpx.Response(200, json=envelope(None, task_status=40210)), "provider_insufficient_funds", 1),
        (httpx.Response(200, json=envelope(None, task_status=40501)), "provider_invalid_request", 1),
        (httpx.Response(200, json={"unexpected": "shape"}), "provider_unexpected_response", 1),
        (httpx.Response(200, text="<html>gateway</html>"), "provider_unexpected_response", 1),
        (httpx.Response(429), "provider_rate_limited", 3),
        (httpx.Response(502), "provider_server_error", 3),
        (httpx.Response(200, json=envelope(None, task_status=50401)), "provider_timeout", 3),
    ],
)
def test_error_classification_and_retry_decision(response: httpx.Response, code: str, expected_attempts: int) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response

    with pytest.raises(ExternalServiceError) as exc:
        _client(handler).post_live("/v3/x", {"keyword": "a"}, timeout=5)
    assert exc.value.code == code
    assert len(calls) == expected_attempts


def test_network_timeout_is_retried_then_succeeds() -> None:
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=envelope([{"items": []}], cost=0.002))

    resp = _client(handler).post_live("/v3/x", {"keyword": "a"}, timeout=5)
    assert resp.attempts == 2 and resp.cost_usd == 0.002


def test_no_results_is_an_empty_success_not_an_error() -> None:
    resp = _client(lambda r: httpx.Response(200, json=envelope(None, task_status=40102))).post_live("/v3/x", {}, timeout=5)
    assert resp.no_results is True and resp.result == []


def test_backoff_is_exponential_capped_and_jittered() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=1.0, max_delay_seconds=5.0)
    rng = random.Random(7)
    delays = [policy.backoff(a, rng=rng) for a in (1, 2, 3, 4)]
    assert all(0 <= d <= cap for d, cap in zip(delays, (1, 2, 4, 5)))
    assert len(set(delays)) == 4  # jitter
    assert policy.backoff(1, retry_after=30) == 5.0  # Retry-After honoured but capped


def test_circuit_breaker_opens_and_half_opens() -> None:
    now = {"t": 0.0}
    breaker = CircuitBreaker("x", failure_threshold=2, reset_seconds=10, clock=lambda: now["t"])
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"
    with pytest.raises(ExternalServiceError) as exc:
        breaker.before_call()
    assert exc.value.code == "circuit_open"
    now["t"] = 11
    assert breaker.state == "half_open"
    breaker.before_call()
    breaker.record_success()
    assert breaker.state == "closed"


def test_logs_never_contain_credentials() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "provider_call", None, None)
    record.headers = {"Authorization": "Basic dXNlcjpwYXNz", "x": 1}  # type: ignore[attr-defined]
    record.detail = "failed with key sk-abcdefghijklmnop and Bearer abc.def"  # type: ignore[attr-defined]
    line = JsonFormatter().format(record)
    parsed = json.loads(line)
    assert "dXNlcjpwYXNz" not in line and "sk-abcdefghijklmnop" not in line and "abc.def" not in line
    assert parsed["headers"]["Authorization"] == "[REDACTED]"
    assert redact({"input_tokens": 5})["input_tokens"] == 5


def test_run_budget_reserves_estimates_so_parallel_calls_cannot_overshoot() -> None:
    from app.resilience.errors import BudgetExceededError
    from app.tools.budget import RunBudget

    budget = RunBudget({"ai_visibility": 5, "serp": 5}, max_cost_usd=0.05, estimates={"ai_visibility": 0.04, "serp": 0.004})
    first = budget.reserve("ai_visibility")          # 0.04 reserved, nothing spent yet
    with pytest.raises(BudgetExceededError):
        budget.reserve("ai_visibility")              # a concurrent 2nd call would exceed $0.05
    serp = budget.reserve("serp")                    # 0.044 ≤ 0.05 still fits
    budget.settle(first, 0.021)                      # actual cost replaces the estimate
    budget.settle(serp, 0.002)
    assert budget.cost_usd == 0.023
    with pytest.raises(BudgetExceededError):
        budget.reserve("ai_visibility")              # spent 0.023 + next 0.04 = 0.063 > 0.05 → refused
    assert budget.reserve("serp") == 0.004           # 0.027 ≤ 0.05 still fits
