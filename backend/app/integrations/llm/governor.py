"""LLM spend governance: persistent quota, per-run cap, input-size guard, response cache, audit log.

Rules enforced server-side BEFORE any request leaves the process:
* total quota (``OPENAI_MAX_CALLS_TOTAL``) — persistent in SQLite, reserved with an atomic
  conditional ``UPDATE … WHERE calls_used < limit`` so concurrent workers and restarts cannot overspend;
* per-run cap (``OPENAI_MAX_CALLS_PER_RUN``) — counted on the run context;
* input-size guard (≈ ``OPENAI_MAX_INPUT_TOKENS``) — oversized prompts are refused, not sent;
* identical successful requests are answered from ``llm_cache`` without a call (and without quota).

A reserved call is counted even if it then fails: once a request is sent, the provider may bill it,
so the counter is deliberately conservative. Prompts and keys are never written to the audit log.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import Settings
from app.extensions import Database
from app.integrations.llm.base import LLMResponse, ToolConversation, ToolFeedback
from app.models import LLMCacheEntry, LLMCall, LLMQuota
from app.resilience.errors import ExternalServiceError
from app.tools.schemas import ToolSpec
from app.tools.validation import RawToolCall
from app.utils.time import utcnow

QUOTA_ID = "global"

# USD per 1M tokens (input, output) — public list prices used only for *estimates* in the audit log.
_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5-mini": (0.25, 2.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def estimate_tokens(*parts: str) -> int:
    """Conservative ~4 chars/token estimate (no tokenizer dependency, no network)."""
    return sum(len(p) for p in parts) // 4 + 1


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    price = next((v for k, v in _PRICES.items() if model.startswith(k)), None)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return round((input_tokens * price[0] + output_tokens * price[1]) / 1_000_000, 6)


def quota_error(message: str) -> ExternalServiceError:
    return ExternalServiceError("llm_quota_exhausted", message, "llm", no_retry=True)


class LLMGovernor:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ quota
    def _ensure_row(self, s) -> None:  # type: ignore[no-untyped-def]
        s.execute(sqlite_insert(LLMQuota).values(id=QUOTA_ID, calls_used=0, updated_at=utcnow()).on_conflict_do_nothing())

    def usage(self) -> dict[str, Any]:
        with self.db.session() as s:
            used = s.scalar(select(LLMQuota.calls_used).where(LLMQuota.id == QUOTA_ID)) or 0
        limit = self.settings.llm_max_calls_total
        return {"used": int(used), "limit": limit, "remaining": max(0, limit - int(used)),
                "per_run_limit": self.settings.llm_max_calls_per_run}

    def reserve_total(self) -> int:
        """Atomically take one call from the persistent quota; returns calls used after reserving."""
        limit = self.settings.llm_max_calls_total
        with self._lock, self.db.session() as s:
            self._ensure_row(s)
            result = s.execute(
                update(LLMQuota)
                .where(LLMQuota.id == QUOTA_ID, LLMQuota.calls_used < limit)
                .values(calls_used=LLMQuota.calls_used + 1, updated_at=utcnow())
            )
            if result.rowcount != 1:
                raise quota_error(f"LLM call quota exhausted ({limit} total calls allowed by OPENAI_MAX_CALLS_TOTAL).")
            return int(s.scalar(select(LLMQuota.calls_used).where(LLMQuota.id == QUOTA_ID)) or 0)

    def record_external_usage(self, calls: int, note: str) -> None:
        """Record calls made before the ledger existed (keeps the total cap honest)."""
        with self.db.session() as s:
            self._ensure_row(s)
            s.execute(update(LLMQuota).where(LLMQuota.id == QUOTA_ID)
                      .values(calls_used=LLMQuota.calls_used + calls, updated_at=utcnow()))
            for _ in range(calls):
                s.add(LLMCall(purpose="pre-quota usage", provider=self.settings.resolved_llm_provider or "unknown",
                              model=self.settings.llm_model or "unknown", status="pre_quota_record", counted=True,
                              note=note[:300]))

    # ------------------------------------------------------------------ cache
    @staticmethod
    def cache_key(provider: str, model: str, material: dict[str, Any]) -> str:
        canonical = json.dumps({"provider": provider, "model": model, **material}, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def cache_get(self, key: str) -> LLMResponse | None:
        ttl = self.settings.llm_cache_ttl_seconds
        if ttl <= 0:
            return None
        with self.db.session() as s:
            entry = s.get(LLMCacheEntry, key)
            if entry is None:
                return None
            created = entry.created_at if entry.created_at.tzinfo else entry.created_at.replace(tzinfo=utcnow().tzinfo)
            if utcnow() - created > timedelta(seconds=ttl):
                return None
            data = dict(entry.response)
        return LLMResponse(
            tool_calls=[RawToolCall(c["call_id"], c["name"], c["arguments"]) for c in data.get("tool_calls", [])],
            text=data.get("text", ""), stop_reason=data.get("stop_reason"), input_tokens=0, output_tokens=0,
            duration_ms=0, attempts=0,
        )

    def cache_put(self, key: str, provider: str, model: str, response: LLMResponse) -> None:
        payload = {
            "tool_calls": [{"call_id": c.call_id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls],
            "text": response.text[:2000], "stop_reason": response.stop_reason,
        }
        with self.db.session() as s:
            entry = s.get(LLMCacheEntry, key)
            if entry is None:
                s.add(LLMCacheEntry(request_hash=key, provider=provider, model=model, response=payload))
            else:
                entry.response = payload
                entry.created_at = utcnow()

    # ------------------------------------------------------------------ audit
    def audit(self, **fields: Any) -> None:
        with self.db.session() as s:
            s.add(LLMCall(**fields))

    def audit_summary(self) -> dict[str, Any]:
        with self.db.session() as s:
            rows = s.execute(
                select(LLMCall.status, func.count(), func.sum(LLMCall.input_tokens), func.sum(LLMCall.output_tokens),
                       func.sum(LLMCall.estimated_cost_usd)).group_by(LLMCall.status)
            ).all()
        return {status: {"count": int(c), "input_tokens": int(i or 0), "output_tokens": int(o or 0),
                         "estimated_cost_usd": round(float(cost or 0.0), 6)} for status, c, i, o, cost in rows}


class GovernedConversation:
    """Wraps a provider conversation with cache → size guard → quota → call → audit."""

    def __init__(
        self,
        inner: ToolConversation,
        governor: LLMGovernor,
        *,
        provider: str,
        model: str,
        run_id: str | None,
        node: str,
        purpose: str,
        system: str,
        user: str,
        tools: Sequence[ToolSpec],
        reserve_run_slot: Callable[[], None],
        release_run_slot: Callable[[], None],
        on_event: Callable[[str, str, str, dict[str, Any]], None],
    ) -> None:
        self.inner = inner
        self.gov = governor
        self.provider = provider
        self.model = model
        self.run_id = run_id
        self.node = node
        self.purpose = purpose
        self._material: dict[str, Any] = {
            "system": system, "user": user,
            "tools": [{"name": t.name, "schema": t.input_schema(), "description": t.description} for t in tools],
            "turns": [],
        }
        self._base_text = system + user + json.dumps(self._material["tools"])
        self._reserve_run_slot = reserve_run_slot
        self._release_run_slot = release_run_slot
        self._on_event = on_event

    def ask(self) -> LLMResponse:
        return self._call(self.inner.ask)

    def reply(self, feedback: Sequence[ToolFeedback], instruction: str) -> LLMResponse:
        self._material["turns"].append({"feedback": [(f.call_id, f.ok, f.message) for f in feedback], "instruction": instruction})
        return self._call(lambda: self.inner.reply(feedback, instruction))

    def _call(self, send: Callable[[], LLMResponse]) -> LLMResponse:
        key = self.gov.cache_key(self.provider, self.model, self._material)
        base = {"run_id": self.run_id, "node": self.node, "purpose": self.purpose[:80], "provider": self.provider,
                "model": self.model, "request_hash": key}
        cached = self.gov.cache_get(key)
        if cached is not None:
            prime = getattr(self.inner, "prime", None)
            if prime is not None:
                prime(cached)
            self.gov.audit(**base, status="cache_hit", counted=False)
            self._on_event("llm_cache_hit", f"{self.purpose}: identical LLM request answered from cache (no API call)",
                           "info", {"request_hash": key[:12]})
            return cached

        estimated = estimate_tokens(self._base_text, json.dumps(self._material["turns"]))
        hard_limit = int(self.gov.settings.llm_max_input_tokens * 1.25)
        if estimated > hard_limit:
            self.gov.audit(**base, status="blocked_input_size", counted=False, estimated_input_tokens=estimated)
            raise ExternalServiceError("llm_invalid_request",
                                       f"Prompt too large (~{estimated} tokens > {hard_limit}); not sent.", "llm", no_retry=True)

        try:
            self._reserve_run_slot()
        except ExternalServiceError:
            self.gov.audit(**base, status="blocked_quota", counted=False, estimated_input_tokens=estimated,
                           error_code="llm_quota_exhausted", note="per-run limit")
            raise
        try:
            used = self.gov.reserve_total()
        except ExternalServiceError:
            self._release_run_slot()
            self.gov.audit(**base, status="blocked_quota", counted=False, estimated_input_tokens=estimated,
                           error_code="llm_quota_exhausted")
            raise
        self._on_event("llm_quota_reserved", f"{self.purpose}: LLM call {used}/{self.gov.settings.llm_max_calls_total} "
                       f"of the total quota (≈{estimated} input tokens)", "info",
                       {"calls_used": used, "limit": self.gov.settings.llm_max_calls_total, "estimated_input_tokens": estimated})
        try:
            response = send()
        except ExternalServiceError as exc:
            self.gov.audit(**base, status="failed", counted=True, estimated_input_tokens=estimated, error_code=exc.code)
            raise
        self.gov.audit(**base, status="success", counted=True, estimated_input_tokens=estimated,
                       input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                       estimated_cost_usd=estimate_cost(self.model, response.input_tokens, response.output_tokens))
        if response.tool_calls:
            self.gov.cache_put(key, self.provider, self.model, response)
        return response
