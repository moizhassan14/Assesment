"""Low-level DataForSEO v3 HTTP client.

Responsibilities: HTTP Basic auth from server-side settings, timeouts, envelope
validation, error classification (HTTP *and* DataForSEO ``status_code`` values,
because DataForSEO reports most failures inside an HTTP 200 body), retry with
backoff + jitter, and a circuit breaker. It knows nothing about runs or tools.

Status codes per https://docs.dataforseo.com/v3/appendix/errors/ (verified 2026-11-18).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.observability.logging import get_logger
from app.resilience.circuit_breaker import CircuitBreaker
from app.resilience.errors import ExternalServiceError
from app.resilience.retry import RetryPolicy, call_with_retry
from app.utils.time import utcnow

log = get_logger(__name__)
PROVIDER = "dataforseo"

_AUTH_CODES = {40100, 40104, 40207}
_FUNDS_CODES = {40200, 40210, 40203, 40201}
_RATE_CODES = {40202, 40209}
_TIMEOUT_CODES = {40107, 50401, 50402}
_SERVER_CODES = {40101, 40103, 50000, 50001, 50301, 50302, 50303}
_NO_RESULTS_CODES = {40102}


class _Task(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    status_code: int
    status_message: str | None = None
    cost: float | None = None
    result: list[dict[str, Any]] | None = None


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="allow")
    status_code: int
    status_message: str | None = None
    cost: float | None = None
    tasks: list[_Task] | None = None


@dataclass
class ProviderResponse:
    endpoint: str
    task_id: str | None
    result: list[dict[str, Any]]
    cost_usd: float
    attempts: int
    duration_ms: int
    fetched_at: datetime
    no_results: bool
    cached: bool = False


def classify_status(code: int, message: str | None, http_status: int | None = None) -> ExternalServiceError:
    text = f"DataForSEO {code}: {message or 'no message'}"
    if code in _AUTH_CODES:
        err_code = "provider_auth_failed"
    elif code in _FUNDS_CODES:
        err_code = "provider_insufficient_funds"
    elif code in _RATE_CODES:
        err_code = "provider_rate_limited"
    elif code in _TIMEOUT_CODES:
        err_code = "provider_timeout"
    elif code in _SERVER_CODES or 50000 <= code < 60000 and code != 50304:
        err_code = "provider_server_error"
    elif code == 50304:
        err_code = "provider_server_error"  # temporarily unavailable; still not our fault
    elif 40000 <= code < 50000:
        err_code = "provider_invalid_request"
    else:
        err_code = "provider_unexpected_response"
    retry_after = 5.0 if err_code == "provider_rate_limited" else None
    return ExternalServiceError(
        code=err_code,
        message=text,
        provider=PROVIDER,
        http_status=http_status,
        provider_status_code=code,
        retry_after_seconds=retry_after,
    )


def _classify_http(status: int, retry_after: str | None) -> ExternalServiceError:
    if status in (401, 403):
        return ExternalServiceError("provider_auth_failed", f"DataForSEO refused access (HTTP {status}).", PROVIDER, status)
    if status == 402:
        return ExternalServiceError("provider_insufficient_funds", "DataForSEO reports a billing problem (HTTP 402).", PROVIDER, status)
    if status == 429:
        delay = None
        try:
            delay = float(retry_after) if retry_after else None
        except ValueError:
            delay = None
        return ExternalServiceError("provider_rate_limited", "DataForSEO rate limited the request (HTTP 429).", PROVIDER, status, retry_after_seconds=delay)
    if status >= 500:
        return ExternalServiceError("provider_server_error", f"DataForSEO server error (HTTP {status}).", PROVIDER, status)
    return ExternalServiceError("provider_invalid_request", f"DataForSEO rejected the request (HTTP {status}).", PROVIDER, status)


class DataForSEOClient:
    def __init__(
        self,
        login: str,
        password: str,
        *,
        base_url: str = "https://api.dataforseo.com",
        http: httpx.Client | None = None,
        retry_policy: RetryPolicy | None = None,
        breaker_factory: Callable[[str], CircuitBreaker] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._auth = httpx.BasicAuth(login, password) if login and password else None
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(headers={"User-Agent": "ai-visibility-platform/1.0"})
        self.retry_policy = retry_policy or RetryPolicy()
        self._breaker_factory = breaker_factory or (lambda name: CircuitBreaker(name))
        self._breakers: dict[str, CircuitBreaker] = {}
        self._breaker_lock = threading.Lock()
        self._sleep = sleep

    def breaker_for(self, endpoint: str) -> CircuitBreaker:
        """One breaker per endpoint: an outage of one DataForSEO API must not block the others."""
        with self._breaker_lock:
            if endpoint not in self._breakers:
                self._breakers[endpoint] = self._breaker_factory(f"dataforseo:{endpoint}")
            return self._breakers[endpoint]

    @property
    def configured(self) -> bool:
        return self._auth is not None

    def _attempt(self, endpoint: str, task: dict[str, Any], timeout: float) -> tuple[_Task, float]:
        if self._auth is None:
            raise ExternalServiceError(
                "provider_auth_failed",
                "DataForSEO credentials are not configured (set DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD).",
                PROVIDER,
            )
        breaker = self.breaker_for(endpoint)
        breaker.before_call()
        try:
            response = self._http.post(
                f"{self._base_url}{endpoint}",
                json=[task],
                auth=self._auth,
                timeout=httpx.Timeout(timeout, connect=10.0),
            )
        except httpx.TimeoutException as exc:
            breaker.record_failure()
            raise ExternalServiceError("provider_timeout", f"DataForSEO request timed out after {timeout:.0f}s.", PROVIDER) from exc
        except httpx.HTTPError as exc:
            breaker.record_failure()
            raise ExternalServiceError("provider_network_error", f"Network error contacting DataForSEO: {type(exc).__name__}.", PROVIDER) from exc

        if response.status_code != 200:
            # DataForSEO usually still sends its envelope (e.g. HTTP 403 + 40104 "verify your account"):
            # prefer its specific status_code over the generic HTTP status.
            body_code, body_message = None, None
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("status_code"), int):
                    body_code, body_message = body["status_code"], body.get("status_message")
            except ValueError:
                pass
            if body_code is not None and body_code != 20000 and response.status_code < 500:
                err = classify_status(body_code, body_message, response.status_code)
            else:
                err = _classify_http(response.status_code, response.headers.get("retry-after"))
            if err.retryable:
                breaker.record_failure()
            raise err

        try:
            envelope = _Envelope.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            breaker.record_failure()
            raise ExternalServiceError(
                "provider_unexpected_response", "DataForSEO returned a body that does not match the documented envelope.", PROVIDER, 200
            ) from exc

        if envelope.status_code != 20000:
            err = classify_status(envelope.status_code, envelope.status_message, 200)
            if err.retryable:
                breaker.record_failure()
            raise err
        if not envelope.tasks:
            breaker.record_failure()
            raise ExternalServiceError("provider_unexpected_response", "DataForSEO response contained no tasks.", PROVIDER, 200)

        task_obj = envelope.tasks[0]
        cost = float(task_obj.cost if task_obj.cost is not None else (envelope.cost or 0.0))
        if task_obj.status_code != 20000 and task_obj.status_code not in _NO_RESULTS_CODES:
            err = classify_status(task_obj.status_code, task_obj.status_message, 200)
            err.details["cost_usd"] = cost
            if err.retryable:
                breaker.record_failure()
            raise err
        breaker.record_success()
        return task_obj, cost

    def post_live(
        self,
        endpoint: str,
        task: dict[str, Any],
        *,
        timeout: float,
        on_retry: Callable[[int, ExternalServiceError, float], None] | None = None,
    ) -> ProviderResponse:
        """POST a single live task with retries. Raises :class:`ExternalServiceError`."""
        started = time.perf_counter()
        (task_obj, cost), outcome = call_with_retry(
            lambda _attempt: self._attempt(endpoint, task, timeout),
            self.retry_policy,
            on_retry=on_retry,
            sleep=self._sleep,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "provider_call_succeeded",
            extra={
                "provider": PROVIDER,
                "endpoint": endpoint,
                "attempts": outcome.attempts,
                "retry_count": outcome.retries,
                "duration_ms": duration_ms,
                "cost_usd": cost,
                "provider_status_code": task_obj.status_code,
            },
        )
        return ProviderResponse(
            endpoint=endpoint,
            task_id=task_obj.id,
            result=list(task_obj.result or []),
            cost_usd=cost,
            attempts=outcome.attempts,
            duration_ms=duration_ms,
            fetched_at=utcnow(),
            no_results=task_obj.status_code in _NO_RESULTS_CODES or not task_obj.result,
        )
