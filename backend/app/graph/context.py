"""Per-run execution context shared by DAG nodes (dependencies, not data)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.extensions import Database
from app.integrations.llm.base import LLMClient, LLMResponse, RetryObserver
from app.integrations.llm.governor import GovernedConversation, LLMGovernor, quota_error
from app.observability.metrics import MetricsRegistry
from app.observability.tracing import EventRecorder
from app.resilience.errors import ExternalServiceError
from app.tools.budget import RunBudget
from app.tools.dataforseo_tools import DataForSEOTools, ToolRunContext
from app.utils.time import iso, utcnow

EXPECTED_NODES = {"discover": 12, "remeasure": 10}


def run_error(node: str, code: str, message: str, *, retryable: bool = False, provider: str | None = None) -> dict[str, Any]:
    return {
        "node": node,
        "code": code,
        "message": message,
        "retryable": retryable,
        "provider": provider,
        "occurred_at": iso(utcnow()),
    }


def error_from_exception(node: str, exc: ExternalServiceError) -> dict[str, Any]:
    return run_error(node, exc.code, exc.message, retryable=exc.retryable, provider=exc.provider)


@dataclass
class RunContext:
    run_id: str
    correlation_id: str
    mode: str
    settings: Settings
    db: Database
    metrics: MetricsRegistry
    recorder: EventRecorder
    budget: RunBudget
    tools: DataForSEOTools
    llm: LLMClient | None
    governor: LLMGovernor | None = None
    force_refresh: bool = False
    _llm_slots_used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _done: set[str] = field(default_factory=set)
    llm_usage: dict[str, int] = field(default_factory=lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0})

    # ------------------------------------------------------------------ LLM access (always governed)
    def _reserve_llm_slot(self) -> None:
        with self._lock:
            if self._llm_slots_used >= self.settings.llm_max_calls_per_run:
                raise quota_error(
                    f"Per-run LLM call limit reached ({self.settings.llm_max_calls_per_run}, OPENAI_MAX_CALLS_PER_RUN)."
                )
            self._llm_slots_used += 1

    def _release_llm_slot(self) -> None:
        with self._lock:
            self._llm_slots_used = max(0, self._llm_slots_used - 1)

    def llm_conversation(self, node: str, purpose: str, *, system: str, user: str, tools, effort: str = "low",  # type: ignore[no-untyped-def]
                         required_tool: str | None = None) -> GovernedConversation:
        """The only way agents talk to an LLM: cache → input-size guard → per-run + persistent quota → call → audit."""
        assert self.llm is not None and self.governor is not None
        inner = self.llm.conversation(system=system, user=user, tools=tools, effort=effort, required_tool=required_tool,
                                      on_retry=self.llm_retry_observer(node))
        return GovernedConversation(
            inner, self.governor, provider=self.llm.provider, model=self.llm.model, run_id=self.run_id, node=node,
            purpose=purpose, system=system, user=user, tools=tools, reserve_run_slot=self._reserve_llm_slot,
            release_run_slot=self._release_llm_slot,
            on_event=lambda event, msg, level, data: self.recorder.record(event, msg, node=node, level=level, data=data),
        )

    def tool_ctx(self) -> ToolRunContext:
        return ToolRunContext(self.run_id, self.recorder, self.budget, self.force_refresh)

    def mark_done(self, node: str) -> int:
        with self._lock:
            self._done.add(node)
            expected = EXPECTED_NODES.get(self.mode, 12)
            return min(99, int(round(100 * len(self._done) / expected)))

    def llm_retry_observer(self, node: str):  # type: ignore[no-untyped-def]
        def observer(attempt: int, err: ExternalServiceError, delay: float) -> None:
            self.recorder.add_retry(node)
            self.recorder.record(
                "retry_scheduled",
                f"LLM {err.code}; retrying in {delay:.1f}s (attempt {attempt + 1})",
                node=node,
                level="warning",
                data={"provider": err.provider, "attempt": attempt + 1, "delay_ms": int(delay * 1000), "error_code": err.code},
            )

        return observer

    def record_llm_call(self, node: str, purpose: str, response: LLMResponse) -> None:
        assert self.llm is not None
        with self._lock:
            self.llm_usage["calls"] += response.attempts
            self.llm_usage["input_tokens"] += response.input_tokens
            self.llm_usage["output_tokens"] += response.output_tokens
        self.metrics.external_call(f"llm.{self.llm.provider}", retries=response.attempts - 1, calls=response.attempts)
        self.recorder.record(
            "llm_call",
            (f"{purpose}: {self.llm.provider}/{self.llm.model} returned {len(response.tool_calls)} tool call(s) "
             f"in {response.duration_ms} ms (stop_reason={response.stop_reason})") if response.attempts else
            f"{purpose}: reused cached {self.llm.provider}/{self.llm.model} response with {len(response.tool_calls)} tool call(s) (no API call)",
            node=node,
            data={
                "provider": self.llm.provider,
                "model": self.llm.model,
                "purpose": purpose,
                "tool_calls": [c.name for c in response.tool_calls],
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "duration_ms": response.duration_ms,
                "attempts": response.attempts,
                "stop_reason": response.stop_reason,
            },
        )

    def record_llm_failure(self, node: str, exc: ExternalServiceError) -> None:
        provider = self.llm.provider if self.llm else "llm"
        self.metrics.external_call(f"llm.{provider}", error=True, retries=max(0, int(exc.details.get("attempts", 1)) - 1),
                                   calls=int(exc.details.get("attempts", 1)))
