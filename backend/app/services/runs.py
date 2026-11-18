"""Run lifecycle: creation (idempotency + duplicate protection), background execution, queries.

Execution uses a bounded in-process ``ThreadPoolExecutor``. Durability caveat
(documented in the README): queued/running work lives in memory, so on restart
any run left in ``queued``/``running`` is marked ``failed`` with
``interrupted_by_restart`` — honest rather than silently stuck.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.extensions import Database
from app.graph.context import run_error
from app.graph.nodes import NODE_ORDER
from app.graph.runner import execute_run
from app.integrations.llm.base import LLMClient
from app.integrations.llm.governor import LLMGovernor
from app.models import AnalysisRun, Brand, RunEvent, RunNode
from app.observability.logging import get_logger, log_context
from app.observability.metrics import MetricsRegistry
from app.repositories.runs import load_tracked_queries
from app.schemas.api import RunCreate
from app.services.errors import ApiError, not_found
from app.services.serializers import brand_snapshot, run_detail_json, run_summary_json
from app.tools.dataforseo_tools import DataForSEOTools
from app.utils.text import normalize_query
from app.utils.time import iso, utcnow

log = get_logger(__name__)
ACTIVE = ("queued", "running")


def default_question(brand: Brand) -> str:
    seeds = ", ".join(f"'{s}'" for s in (brand.seed_keywords or [])[:3])
    return f"How visible is {brand.name} in AI answers and Google search for {seeds}, compared with its competitors?"


class RunService:
    def __init__(self, db: Database, settings: Settings, metrics: MetricsRegistry, tools: DataForSEOTools,
                 llm: LLMClient | None, *, governor: LLMGovernor, synchronous: bool = False) -> None:
        self.db = db
        self.settings = settings
        self.metrics = metrics
        self.tools = tools
        self.llm = llm
        self.governor = governor
        self.synchronous = synchronous
        self._executor = ThreadPoolExecutor(max_workers=settings.max_concurrent_runs, thread_name_prefix="run-worker")
        self._create_lock = threading.Lock()

    # ---------------------------------------------------------------- startup recovery
    def recover_interrupted_runs(self) -> int:
        with self.db.session() as s:
            stuck = s.scalars(select(AnalysisRun).where(AnalysisRun.status.in_(ACTIVE))).all()
            for run in stuck:
                run.status = "failed"
                run.finished_at = utcnow()
                run.errors = list(run.errors or []) + [run_error(
                    "orchestrator", "interrupted_by_restart",
                    "The server restarted while this run was queued or running; start a new run.")]
                run.progress_message = "Interrupted by server restart"
            if stuck:
                log.warning("recovered_interrupted_runs", extra={"count": len(stuck)})
            return len(stuck)

    # ---------------------------------------------------------------- create
    @staticmethod
    def _fingerprint(brand_id: str, body: RunCreate, question: str) -> str:
        canonical = json.dumps({"brand": brand_id, "mode": body.mode, "q": normalize_query(question)}, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _body_hash(body: RunCreate) -> str:
        return hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()[:16]

    def create(self, brand_id: str, body: RunCreate, idempotency_key: str | None) -> tuple[dict[str, Any], bool]:
        """Returns (response payload, replayed)."""
        with self._create_lock, self.db.session() as s:
            brand = s.get(Brand, brand_id, options=[selectinload(Brand.competitors)])
            if brand is None:
                raise not_found("Brand")

            if idempotency_key:
                existing = s.scalar(select(AnalysisRun).where(AnalysisRun.brand_id == brand_id,
                                                              AnalysisRun.idempotency_key == idempotency_key))
                if existing is not None:
                    if (existing.config or {}).get("request_hash") != self._body_hash(body):
                        raise ApiError(409, "idempotency_key_conflict",
                                       "This Idempotency-Key was already used with a different request body.",
                                       meta={"existing_run_id": existing.id})
                    return self._accepted(existing), True

            source_run_id = None
            if body.mode == "remeasure":
                source_run_id, rows = load_tracked_queries(self.db, brand_id, exclude_run_id="", data_mode=self.settings.data_mode)
                if not rows:
                    raise ApiError(409, "nothing_to_remeasure",
                                   "This brand has no completed run with measured queries to re-measure yet.")
                source = s.get(AnalysisRun, source_run_id)
                question = source.question if source else default_question(brand)
            else:
                question = body.question or default_question(brand)

            fingerprint = self._fingerprint(brand_id, body, question)
            duplicate = s.scalar(select(AnalysisRun).where(AnalysisRun.request_fingerprint == fingerprint,
                                                           AnalysisRun.status.in_(ACTIVE)))
            if duplicate is not None:
                raise ApiError(409, "run_in_progress", "An identical run is already queued or running.",
                               meta={"existing_run_id": duplicate.id})

            active = int(s.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.status.in_(ACTIVE))) or 0)
            if active >= self.settings.max_concurrent_runs + self.settings.max_queued_runs:
                raise ApiError(429, "too_many_runs", "Too many analyses are running; try again shortly.",
                               meta={"retry_after_seconds": 30}, headers={"Retry-After": "30"})

            run_id = str(uuid.uuid4())
            run = AnalysisRun(
                id=run_id,
                brand_id=brand_id,
                status="queued",
                mode=body.mode,
                question=question,
                request_fingerprint=fingerprint,
                idempotency_key=idempotency_key,
                correlation_id=f"run-{run_id[:8]}",
                source_run_id=source_run_id,
                brand_snapshot=brand_snapshot(brand),
                config={
                    "force_refresh": body.force_refresh,
                    "data_mode": self.settings.data_mode,
                    "request_hash": self._body_hash(body),
                    "max_research_queries": self.settings.max_research_queries_per_run,
                    "max_serp_calls": self.settings.max_serp_calls_per_run,
                    "max_ai_visibility_calls": self.settings.max_ai_visibility_calls_per_run,
                    "max_keyword_discovery_calls": self.settings.max_keyword_discovery_calls_per_run,
                    "max_run_cost_usd": self.settings.max_run_cost_usd,
                    "llm_provider": self.llm.provider if self.llm else None,
                    "llm_model": self.llm.model if self.llm else None,
                    "llm_max_calls_per_run": self.settings.llm_max_calls_per_run,
                    "llm_max_output_tokens": self.settings.llm_max_output_tokens,
                    "ai_answer_model": self.settings.ai_answer_model,
                    "ai_answer_web_search": self.settings.ai_answer_web_search,
                    "source_run_id": source_run_id,
                },
                progress_message="Queued",
            )
            run.nodes = [RunNode(name=name, position=i, status="pending") for i, name in enumerate(NODE_ORDER)]
            s.add(run)
            s.add(RunEvent(run_id=run_id, seq=1, node=None, event="run_queued", level="info",
                           message=f"Run queued (mode={body.mode})", data={"idempotency_key_present": bool(idempotency_key)}))
            s.flush()
            payload = self._accepted(run)

        with log_context(run_id=run_id, correlation_id=f"run-{run_id[:8]}"):
            log.info("run_queued", extra={"brand_id": brand_id, "mode": body.mode})
        self._submit(run_id)
        return payload, False

    def _accepted(self, run: AnalysisRun) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "status": run.status,
            "status_url": f"/api/runs/{run.id}",
            "report_url": f"/api/runs/{run.id}/report",
            "run": run_summary_json(run),
        }

    def _submit(self, run_id: str) -> None:
        def job() -> None:
            try:
                execute_run(run_id, db=self.db, settings=self.settings, metrics=self.metrics, tools=self.tools, llm=self.llm,
                            governor=self.governor)
            except Exception:  # noqa: BLE001 - last line of defence; execute_run already handles errors
                log.exception("run_worker_unhandled", extra={"run_id": run_id})

        if self.synchronous:
            job()
        else:
            self._executor.submit(job)

    def active_runs(self) -> int:
        with self.db.session() as s:
            return int(s.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.status.in_(ACTIVE))) or 0)

    # ---------------------------------------------------------------- read
    def list_for_brand(self, brand_id: str, limit: int, offset: int, status: str | None) -> tuple[list[dict[str, Any]], int]:
        with self.db.session() as s:
            if s.get(Brand, brand_id) is None:
                raise not_found("Brand")
            stmt = select(AnalysisRun).where(AnalysisRun.brand_id == brand_id)
            cstmt = select(func.count()).select_from(AnalysisRun).where(AnalysisRun.brand_id == brand_id)
            if status:
                stmt = stmt.where(AnalysisRun.status == status)
                cstmt = cstmt.where(AnalysisRun.status == status)
            total = int(s.scalar(cstmt) or 0)
            runs = s.scalars(stmt.order_by(AnalysisRun.created_at.desc()).limit(limit).offset(offset)).all()
            return [run_summary_json(r) for r in runs], total

    def get(self, run_id: str) -> dict[str, Any]:
        with self.db.session() as s:
            run = s.get(AnalysisRun, run_id, options=[selectinload(AnalysisRun.nodes)])
            if run is None:
                raise not_found("Run")
            return run_detail_json(run)

    def events(self, run_id: str, after_seq: int, limit: int) -> tuple[list[dict[str, Any]], int, int | None]:
        with self.db.session() as s:
            if s.get(AnalysisRun, run_id) is None:
                raise not_found("Run")
            total = int(s.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.run_id == run_id)) or 0)
            rows = s.scalars(
                select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after_seq).order_by(RunEvent.seq).limit(limit)
            ).all()
            data = [
                {"seq": e.seq, "timestamp": iso(e.timestamp), "node": e.node, "event": e.event, "level": e.level,
                 "message": e.message, "data": e.data or {}}
                for e in rows
            ]
            return data, total, (rows[-1].seq if rows else after_seq)
