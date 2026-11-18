"""Run-scoped trace recorder.

Every interesting thing that happens during a run becomes (a) a persisted
``RunEvent`` row with a monotonically increasing ``seq`` — the trace timeline
returned by ``GET /api/runs/{id}/events`` — and (b) a structured JSON log line
carrying the same ``run_id``/``correlation_id``/``node_name``. Node state rows
(``run_nodes``) and run progress are updated here too.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select

from app.extensions import Database
from app.models import AnalysisRun, RunEvent, RunNode
from app.observability.logging import get_logger, log_context, redact
from app.utils.time import utcnow

log = get_logger("app.trace")

_LEVELS = {"info": 20, "warning": 30, "error": 40}


class EventRecorder:
    def __init__(self, db: Database, run_id: str, correlation_id: str) -> None:
        self.db = db
        self.run_id = run_id
        self.correlation_id = correlation_id
        self._lock = threading.Lock()
        self._retry_counts: dict[str, int] = defaultdict(int)
        with db.session() as s:
            current = s.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id))
        self._seq = int(current or 0)

    def record(
        self,
        event: str,
        message: str,
        *,
        node: str | None = None,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        safe_data = redact(data or {})
        with self._lock:
            self._seq += 1
            seq = self._seq
            with self.db.session() as s:
                s.add(
                    RunEvent(
                        run_id=self.run_id,
                        seq=seq,
                        timestamp=utcnow(),
                        node=node,
                        event=event,
                        level=level,
                        message=message[:500],
                        data=safe_data,
                    )
                )
        with log_context(run_id=self.run_id, correlation_id=self.correlation_id, node_name=node):
            log.log(_LEVELS.get(level, 20), event, extra={"detail": message, "seq": seq, "data": safe_data})

    # ---- node state -----------------------------------------------------------------
    def node_started(self, node: str, message: str) -> None:
        now = utcnow()
        with self.db.session() as s:
            row = s.scalar(select(RunNode).where(RunNode.run_id == self.run_id, RunNode.name == node))
            if row is not None:
                row.status = "running"
                row.started_at = now
                row.message = message[:300]
            run = s.get(AnalysisRun, self.run_id)
            if run is not None:
                run.current_node = node
                run.progress_message = message[:300]
        self.record("node_started", message, node=node)

    def node_finished(
        self,
        node: str,
        status: str,
        message: str,
        duration_ms: int,
        error: dict[str, Any] | None = None,
        progress_percent: int | None = None,
    ) -> None:
        with self.db.session() as s:
            row = s.scalar(select(RunNode).where(RunNode.run_id == self.run_id, RunNode.name == node))
            if row is not None:
                row.status = status
                row.finished_at = utcnow()
                row.duration_ms = duration_ms
                row.message = message[:300]
                row.retry_count = self._retry_counts.get(node, 0)
                row.error = error
            run = s.get(AnalysisRun, self.run_id)
            if run is not None and progress_percent is not None:
                run.progress_percent = max(run.progress_percent or 0, progress_percent)
                run.progress_message = message[:300]
        event = {"success": "node_succeeded", "degraded": "node_degraded", "failed": "node_failed", "skipped": "node_skipped"}[status]
        level = {"failed": "error", "degraded": "warning"}.get(status, "info")
        self.record(
            event,
            message,
            node=node,
            level=level,
            data={"status": status, "duration_ms": duration_ms, "retry_count": self._retry_counts.get(node, 0), "error": error},
        )

    def progress(self, node: str, message: str) -> None:
        with self.db.session() as s:
            run = s.get(AnalysisRun, self.run_id)
            if run is not None:
                run.progress_message = message[:300]
                run.current_node = node

    def add_retry(self, node: str | None) -> None:
        if node:
            with self._lock:
                self._retry_counts[node] += 1

    def retry_count(self, node: str) -> int:
        return self._retry_counts.get(node, 0)
