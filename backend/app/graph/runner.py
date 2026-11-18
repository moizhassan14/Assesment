"""Executes one analysis run end-to-end on a background worker thread."""

from __future__ import annotations

import time
from typing import Any

from app.config import Settings
from app.extensions import Database
from app.graph.builder import build_graph
from app.graph.context import RunContext, run_error
from app.integrations.llm.base import LLMClient
from app.integrations.llm.governor import LLMGovernor
from app.models import AnalysisRun, RunNode
from app.observability.logging import get_logger, log_context
from app.observability.metrics import MetricsRegistry
from app.observability.tracing import EventRecorder
from app.schemas.domain import BrandContext
from app.tools.budget import RunBudget
from app.tools.dataforseo_tools import DataForSEOTools
from app.utils.time import utcnow

log = get_logger(__name__)


def final_status(state: dict[str, Any]) -> str:
    observations = state.get("observations") or []
    if state.get("abort") or not any(o.status == "measured" for o in observations):
        return "failed"
    if state.get("errors") or state.get("degraded_nodes"):
        return "partial_success"
    return "success"


def execute_run(
    run_id: str,
    *,
    db: Database,
    settings: Settings,
    metrics: MetricsRegistry,
    tools: DataForSEOTools,
    llm: LLMClient | None,
    governor: LLMGovernor | None = None,
) -> str:
    with db.session() as s:
        run = s.get(AnalysisRun, run_id)
        if run is None:
            return "missing"
        run.status = "running"
        run.started_at = utcnow()
        correlation_id = run.correlation_id
        mode = run.mode
        question = run.question
        snapshot = dict(run.brand_snapshot)
        force_refresh = bool((run.config or {}).get("force_refresh"))

    recorder = EventRecorder(db, run_id, correlation_id)
    if getattr(tools, "sandbox", None) is not None:
        tools.sandbox.entities[run_id] = [{"name": snapshot.get("name", ""), "domain": snapshot.get("domain", ""), "is_brand": "1"}] + [
            {"name": c["name"], "domain": c["domain"], "is_brand": "0"} for c in snapshot.get("competitors", [])]
        recorder.record("run_started", "SANDBOX MODE: DataForSEO responses are synthetic (DATAFORSEO_MODE=sandbox)",
                        level="warning", data={"data_mode": "sandbox"})
    budget = RunBudget(
        {
            "keyword_discovery": settings.max_keyword_discovery_calls_per_run,
            "keyword_metrics": settings.max_keyword_metrics_calls_per_run,
            "serp": settings.max_serp_calls_per_run,
            "ai_visibility": settings.max_ai_visibility_calls_per_run,
        },
        settings.max_run_cost_usd,
    )
    ctx = RunContext(run_id=run_id, correlation_id=correlation_id, mode=mode, settings=settings, db=db, metrics=metrics,
                     recorder=recorder, budget=budget, tools=tools, llm=llm, governor=governor,
                     force_refresh=force_refresh)
    started = time.perf_counter()
    status = "failed"
    final_state: dict[str, Any] = {}
    with log_context(run_id=run_id, correlation_id=correlation_id):
        metrics.run_event("started")
        recorder.record("run_started", f"Run started (mode={mode})", data={"mode": mode, "force_refresh": force_refresh})
        try:
            brand = BrandContext.model_validate(snapshot)
            graph = build_graph(ctx)
            final_state = graph.invoke({"run_id": run_id, "brand": brand, "question": question, "mode": mode, "errors": []})
            status = final_status(final_state)
            errors = list(final_state.get("errors") or [])
            if status == "failed" and not any(e.get("code") for e in errors):
                errors.append(run_error("assemble_report", "no_evidence", "No visibility evidence could be collected."))
        except Exception as exc:  # noqa: BLE001 - worker must always leave the run in a terminal state
            log.exception("run_crashed")
            errors = [run_error("orchestrator", "worker_crashed", f"Run worker crashed: {type(exc).__name__}")]
            status = "failed"

        duration_ms = int((time.perf_counter() - started) * 1000)
        with db.session() as s:
            run = s.get(AnalysisRun, run_id)
            assert run is not None
            run.status = status
            run.finished_at = utcnow()
            run.progress_percent = 100
            run.current_node = None
            run.errors = errors
            run.cost = {
                "provider_cost_usd": budget.cost_usd,
                **_call_counts(db, run_id),
                "llm_calls": ctx.llm_usage["calls"],
                "llm_input_tokens": ctx.llm_usage["input_tokens"],
                "llm_output_tokens": ctx.llm_usage["output_tokens"],
            }
            suffix = {"success": "Report ready", "partial_success": f"Report ready ({len(errors)} issue(s))",
                      "failed": "Run failed"}[status]
            run.progress_message = suffix
            for node in s.query(RunNode).filter(RunNode.run_id == run_id, RunNode.status.in_(("pending", "running"))):
                node.status = "skipped"
                node.message = "Not on the executed path"
        metrics.run_event(status)
        recorder.record(
            "run_finished",
            f"Run finished with status {status} in {duration_ms} ms",
            level="info" if status == "success" else "warning",
            data={"status": status, "duration_ms": duration_ms, "error_codes": [e.get("code") for e in errors],
                  "provider_cost_usd": budget.cost_usd, "llm": dict(ctx.llm_usage),
                  "completed_nodes": final_state.get("completed_nodes", [])},
        )
    return status


def _call_counts(db: Database, run_id: str) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.models import ProviderCall

    with db.session() as s:
        rows = s.execute(
            select(ProviderCall.status, func.count()).where(ProviderCall.run_id == run_id).group_by(ProviderCall.status)
        ).all()
    counts = {status: int(c) for status, c in rows}
    return {
        "provider_calls": counts.get("success", 0) + counts.get("failed", 0),
        "provider_cache_hits": counts.get("cache_hit", 0),
    }
