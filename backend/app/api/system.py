from __future__ import annotations

from flask import Blueprint, current_app

from app.agents.report import scoring_meta
from app.agents.synthesis import CONTENT_TYPES
from app.api.common import ok, parse_body, services
from app.models import ClientError
from app.observability.logging import get_logger
from app.schemas.api import MARKETS, ClientErrorIn
from app.utils.time import iso, utcnow

bp = Blueprint("system", __name__, url_prefix="/api")
log = get_logger("app.client_errors")


@bp.get("/health")
def health():  # type: ignore[no-untyped-def]
    svc = services()
    settings = svc.settings
    db_ok = svc.db.ping()
    data = {
        "status": "ok" if db_ok else "degraded",
        "version": settings.app_version,
        "time": iso(utcnow()),
        "checks": {
            "database": "ok" if db_ok else "error",
            "dataforseo_credentials": "configured" if settings.dataforseo_configured else "missing",
            "llm_credentials": "configured" if settings.llm_configured else "missing",
            "data_mode": settings.data_mode,
            "llm_provider": settings.resolved_llm_provider,
            "llm_model": settings.llm_model,
            "llm_quota": svc.governor.usage() if db_ok else None,
            "worker": {"active_runs": svc.runs.active_runs() if db_ok else None,
                       "max_concurrent_runs": settings.max_concurrent_runs},
        },
    }
    return ok(data, 200 if db_ok else 503)


@bp.get("/config")
def config():  # type: ignore[no-untyped-def]
    s = services().settings
    return ok({
        "markets": MARKETS,
        "limits": {
            "max_competitors": 5,
            "max_seed_keywords": 5,
            "question_max_length": 400,
            "max_research_queries_per_run": s.max_research_queries_per_run,
            "max_serp_calls_per_run": s.max_serp_calls_per_run,
            "max_ai_visibility_calls_per_run": s.max_ai_visibility_calls_per_run,
            "max_keyword_discovery_calls_per_run": s.max_keyword_discovery_calls_per_run,
            "max_run_cost_usd": s.max_run_cost_usd,
            "llm_max_calls_per_run": s.llm_max_calls_per_run,
            "llm_max_calls_total": s.llm_max_calls_total,
            "llm_max_output_tokens": s.llm_max_output_tokens,
        },
        "llm_quota": services().governor.usage(),
        "content_types": list(CONTENT_TYPES),
        "scoring": scoring_meta(),
        "data_mode": s.data_mode,
    })


@bp.get("/metrics")
def metrics():  # type: ignore[no-untyped-def]
    svc = services()
    return ok({**svc.metrics.snapshot(), "llm_quota": svc.governor.usage(), "llm_audit": svc.governor.audit_summary()})


@bp.post("/client-errors")
def client_errors():  # type: ignore[no-untyped-def]
    body = parse_body(ClientErrorIn)
    with services().db.session() as s:
        row = ClientError(**body.model_dump())
        s.add(row)
        s.flush()
        rid, received = row.id, row.received_at
    log.warning("client_error_reported", extra={
        "client_error_id": rid, "kind": body.kind, "client_message": body.message[:500], "url": body.url,
        "client_request_id": body.request_id, "client_run_id": body.run_id,
    })
    return ok({"id": rid, "received_at": iso(received)}, 202)


@bp.get("/")
def index():  # type: ignore[no-untyped-def]
    return ok({"name": "AI Visibility Platform API", "version": current_app.config.get("APP_VERSION"),
               "docs": "docs/api-contract.md"})
