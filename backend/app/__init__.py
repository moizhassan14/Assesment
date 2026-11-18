"""Flask application factory."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

from app.config import Settings
from app.extensions import Database
from app.integrations.dataforseo.client import DataForSEOClient
from app.integrations.llm.base import LLMClient
from app.integrations.llm.factory import build_llm_client
from app.integrations.llm.governor import LLMGovernor
from app.observability.logging import configure_logging, get_logger, request_id_var
from app.observability.metrics import MetricsRegistry
from app.resilience.circuit_breaker import CircuitBreaker
from app.resilience.retry import RetryPolicy
from app.services.brands import BrandService
from app.services.errors import ApiError
from app.services.reports import ReportService
from app.services.runs import RunService
from app.tools.dataforseo_tools import DataForSEOTools

log = get_logger("app.http")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HTTP_CODES = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large", 415: "unsupported_media_type"}


@dataclass
class Services:
    settings: Settings
    db: Database
    metrics: MetricsRegistry
    governor: LLMGovernor
    brands: BrandService
    runs: RunService
    reports: ReportService


def build_services(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    llm: LLMClient | None = None,
    use_configured_llm: bool = True,
    synchronous_runs: bool = False,
    sleep: Any = None,
) -> Services:
    db = Database(settings.database_url)
    db.create_all()
    metrics = MetricsRegistry()
    def breaker_factory(name: str) -> CircuitBreaker:
        breaker = CircuitBreaker(name, settings.circuit_breaker_failure_threshold, settings.circuit_breaker_reset_seconds)
        metrics.register_breaker(name, breaker)
        return breaker

    client_kwargs: dict[str, Any] = {}
    if sleep is not None:
        client_kwargs["sleep"] = sleep
    sandbox = None
    login, password = settings.dataforseo_login.get_secret_value(), settings.dataforseo_password.get_secret_value()
    if settings.data_mode == "sandbox" and http_client is None:
        from app.integrations.dataforseo.sandbox import SandboxTransport

        sandbox = SandboxTransport()
        http_client = httpx.Client(transport=sandbox)
        login, password = "sandbox", "sandbox"
    client = DataForSEOClient(
        login,
        password,
        base_url=settings.dataforseo_base_url,
        http=http_client,
        retry_policy=RetryPolicy(settings.retry.max_attempts, settings.retry.base_delay_seconds, settings.retry.max_delay_seconds),
        breaker_factory=breaker_factory,
        **client_kwargs,
    )
    tools = DataForSEOTools(client, db, settings, metrics, sandbox=sandbox)
    llm_client = llm if llm is not None else (build_llm_client(settings, metrics) if use_configured_llm else None)
    governor = LLMGovernor(db, settings)
    runs = RunService(db, settings, metrics, tools, llm_client, governor=governor, synchronous=synchronous_runs)
    return Services(settings, db, metrics, governor, BrandService(db), runs, ReportService(db))


def _error_response(status: int, code: str, message: str, details: list[dict[str, Any]] | None = None,
                    meta: dict[str, Any] | None = None, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    body: dict[str, Any] = {"code": code, "message": message, "details": details or [],
                            "request_id": getattr(g, "request_id", None)}
    if meta:
        body["meta"] = meta
    response = jsonify({"error": body})
    response.status_code = status
    for k, v in (headers or {}).items():
        response.headers[k] = v
    return response


def create_app(settings: Settings | None = None, services: Services | None = None) -> Flask:
    settings = settings or (services.settings if services else Settings.from_env())
    configure_logging(settings.log_level)
    app = Flask(__name__)
    app.config["APP_VERSION"] = settings.app_version
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # contract: bodies > 64 KB → 413
    app.json.sort_keys = False  # type: ignore[attr-defined]

    svc = services or build_services(settings)
    if services is None:
        recovered = svc.runs.recover_interrupted_runs()
        log.info("app_started", extra={
            "version": settings.app_version, "recovered_runs": recovered,
            "dataforseo_configured": settings.dataforseo_configured, "data_mode": settings.data_mode,
            "llm_provider": settings.resolved_llm_provider, "llm_model": settings.llm_model,
        })
    app.extensions["services"] = svc

    @app.before_request
    def _start() -> None:
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _REQUEST_ID_RE.match(incoming or "") else uuid.uuid4().hex[:16]
        g.started = time.perf_counter()
        g.rid_token = request_id_var.set(g.request_id)

    @app.after_request
    def _finish(response):  # type: ignore[no-untyped-def]
        response.headers["X-Request-ID"] = g.get("request_id", "")
        if request.path != "/api/health":
            log.info("http_request", extra={
                "method": request.method, "path": request.path, "status": response.status_code,
                "duration_ms": int((time.perf_counter() - g.get("started", time.perf_counter())) * 1000),
            })
        return response

    @app.teardown_request
    def _teardown(_exc):  # type: ignore[no-untyped-def]
        token = g.pop("rid_token", None)
        if token is not None:
            request_id_var.reset(token)

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):  # type: ignore[no-untyped-def]
        return _error_response(exc.status, exc.code, exc.message, exc.details, exc.meta, exc.headers)

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):  # type: ignore[no-untyped-def]
        status = exc.code or 500
        code = _HTTP_CODES.get(status, "bad_request" if status < 500 else "internal_error")
        message = {404: "Resource not found.", 405: "Method not allowed."}.get(status, exc.description or "HTTP error")
        return _error_response(status, code, message)

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):  # type: ignore[no-untyped-def]
        log.exception("unhandled_exception", extra={"path": request.path})
        return _error_response(500, "internal_error", "An unexpected error occurred. Quote the request_id when reporting it.")

    from app.api import brands, runs, system

    app.register_blueprint(system.bp)
    app.register_blueprint(brands.bp)
    app.register_blueprint(runs.bp)
    return app
