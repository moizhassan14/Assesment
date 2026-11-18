"""Executable DataForSEO tools — one method per logical API call.

Each tool takes its *validated* argument model, builds the documented request
payload, and runs it through the same guarded path:

    cache lookup → budget reservation → client (timeouts, retries, breaker)
    → cost accounting → cache write → provenance row + trace events

Raw payloads are returned to the caller (retrieval nodes) untouched; turning them
into normalized records is the extraction agent's job.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.config import Settings
from app.extensions import Database
from app.integrations.dataforseo.client import DataForSEOClient, ProviderResponse
from app.models import ProviderCacheEntry, ProviderCall
from app.observability.metrics import MetricsRegistry
from app.observability.tracing import EventRecorder
from app.resilience.errors import BudgetExceededError, ExternalServiceError
from app.tools.budget import RunBudget
from app.tools.schemas import (
    CHECK_CHATGPT_ANSWER,
    CHECK_GOOGLE_ORGANIC_SERP,
    DISCOVER_KEYWORD_IDEAS,
    GET_KEYWORD_METRICS,
    CheckChatGPTAnswerArgs,
    CheckGoogleOrganicSerpArgs,
    DiscoverKeywordIdeasArgs,
    GetKeywordMetricsArgs,
    ToolSpec,
)
from app.utils.time import utcnow


def request_hash(endpoint: str, task: dict[str, Any]) -> str:
    cacheable = {k: v for k, v in task.items() if k != "tag"}
    canonical = json.dumps({"endpoint": endpoint, "task": cacheable}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class ToolRunContext:
    run_id: str
    recorder: EventRecorder
    budget: RunBudget
    force_refresh: bool


class DataForSEOTools:
    def __init__(self, client: DataForSEOClient, db: Database, settings: Settings, metrics: MetricsRegistry,
                 sandbox: Any = None) -> None:
        self.client = client
        self.sandbox = sandbox  # SandboxTransport when DATAFORSEO_MODE=sandbox (explicit opt-in), else None
        self.db = db
        self.settings = settings
        self.metrics = metrics

    # ------------------------------------------------------------------ public tools
    def discover_keyword_ideas(self, args: DiscoverKeywordIdeasArgs, ctx: ToolRunContext, node: str) -> ProviderResponse:
        task = {
            "keywords": args.seed_keywords,
            "location_code": args.location_code,
            "language_code": args.language_code,
            "limit": min(args.limit, self.settings.keyword_ideas_limit),
            "tag": ctx.run_id,
        }
        return self._execute(DISCOVER_KEYWORD_IDEAS, task, ctx, node, self.settings.dataforseo_timeout_seconds)

    def get_keyword_metrics(self, args: GetKeywordMetricsArgs, ctx: ToolRunContext, node: str) -> ProviderResponse:
        task = {
            "keywords": args.keywords,
            "location_code": args.location_code,
            "language_code": args.language_code,
            "tag": ctx.run_id,
        }
        return self._execute(GET_KEYWORD_METRICS, task, ctx, node, self.settings.dataforseo_timeout_seconds)

    def check_google_organic_serp(self, args: CheckGoogleOrganicSerpArgs, ctx: ToolRunContext, node: str) -> ProviderResponse:
        task = {
            "keyword": args.keyword,
            "location_code": args.location_code,
            "language_code": args.language_code,
            "depth": min(args.depth, self.settings.serp_depth) if self.settings.serp_depth >= 10 else 10,
            "device": "desktop",
            "tag": ctx.run_id,
        }
        return self._execute(CHECK_GOOGLE_ORGANIC_SERP, task, ctx, node, self.settings.dataforseo_timeout_seconds)

    def check_chatgpt_answer(self, args: CheckChatGPTAnswerArgs, ctx: ToolRunContext, node: str) -> ProviderResponse:
        task: dict[str, Any] = {
            "user_prompt": args.prompt,
            "model_name": self.settings.ai_answer_model,
            "web_search": bool(args.web_search and self.settings.ai_answer_web_search),
            "max_output_tokens": self.settings.ai_answer_max_output_tokens,
            "tag": ctx.run_id,
        }
        if args.country_iso_code and task["web_search"]:
            task["web_search_country_iso_code"] = args.country_iso_code
        return self._execute(CHECK_CHATGPT_ANSWER, task, ctx, node, self.settings.dataforseo_ai_timeout_seconds)

    # ------------------------------------------------------------------ guarded execution
    def _summary(self, task: dict[str, Any]) -> dict[str, Any]:
        summary = {k: v for k, v in task.items() if k != "tag"}
        if "user_prompt" in summary:
            summary["user_prompt"] = str(summary["user_prompt"])[:200]
        return summary

    def _cache_lookup(self, key: str) -> ProviderCacheEntry | None:
        ttl = self.settings.provider_cache_ttl_seconds
        if ttl <= 0:
            return None
        with self.db.session() as s:
            entry = s.get(ProviderCacheEntry, key)
            if entry is None:
                return None
            created = entry.created_at if entry.created_at.tzinfo else entry.created_at.replace(tzinfo=utcnow().tzinfo)
            if utcnow() - created > timedelta(seconds=ttl):
                return None
            s.expunge(entry)
            return entry

    def _record_call(self, ctx: ToolRunContext, node: str, spec: ToolSpec, key: str, task: dict[str, Any], **fields: Any) -> None:
        with self.db.session() as s:
            s.add(
                ProviderCall(
                    run_id=ctx.run_id,
                    node=node,
                    provider="dataforseo",
                    tool=spec.name,
                    endpoint=spec.endpoint,
                    request_hash=key,
                    request_summary=self._summary(task),
                    **fields,
                )
            )

    def _execute(self, spec: ToolSpec, task: dict[str, Any], ctx: ToolRunContext, node: str, timeout: float) -> ProviderResponse:
        # Sandbox responses get their own cache namespace so they can never be served in live mode.
        key = request_hash(spec.endpoint if self.settings.data_mode == "live" else f"sandbox:{spec.endpoint}", task)
        metric_key = f"dataforseo.{spec.name}"

        if not ctx.force_refresh:
            cached = self._cache_lookup(key)
            if cached is not None:
                payload = cached.payload
                response = ProviderResponse(
                    endpoint=spec.endpoint,
                    task_id=cached.provider_task_id,
                    result=list(payload.get("result") or []),
                    cost_usd=0.0,
                    attempts=0,
                    duration_ms=0,
                    fetched_at=cached.created_at,
                    no_results=not payload.get("result"),
                    cached=True,
                )
                self._record_call(ctx, node, spec, key, task, status="cache_hit", attempts=0, duration_ms=0, cost_usd=0.0,
                                  provider_task_id=cached.provider_task_id)
                self.metrics.external_call(metric_key, cache_hit=True, calls=0)
                ctx.recorder.record(
                    "cache_hit",
                    f"{spec.name}: reused identical request completed at {cached.created_at.isoformat()} (no charge)",
                    node=node,
                    data={"tool": spec.name, "request_hash": key[:12]},
                )
                return response

        try:
            reserved = ctx.budget.reserve(spec.budget_key)
        except BudgetExceededError as exc:
            self._record_call(ctx, node, spec, key, task, status="blocked", attempts=0, error=exc.to_dict())
            ctx.recorder.record("budget_blocked", f"{spec.name}: {exc.message}", node=node, level="warning",
                                data={"tool": spec.name})
            raise

        def on_retry(attempt: int, err: ExternalServiceError, delay: float) -> None:
            ctx.recorder.add_retry(node)
            ctx.recorder.record(
                "retry_scheduled",
                f"{spec.name}: {err.code} ({err.provider_status_code or err.http_status or 'n/a'}); retrying in {delay:.1f}s "
                f"(attempt {attempt + 1}/{self.client.retry_policy.max_attempts})",
                node=node,
                level="warning",
                data={"tool": spec.name, "attempt": attempt + 1, "delay_ms": int(delay * 1000), "error_code": err.code},
            )

        try:
            response = self.client.post_live(spec.endpoint, task, timeout=timeout, on_retry=on_retry)
        except ExternalServiceError as exc:
            attempts = int(exc.details.get("attempts", 1))
            cost = float(exc.details.get("cost_usd", 0.0) or 0.0)
            ctx.budget.settle(reserved, cost)
            self._record_call(ctx, node, spec, key, task, status="failed", attempts=attempts, cost_usd=cost, error=exc.to_dict())
            self.metrics.external_call(metric_key, error=True, retries=max(0, attempts - 1), cost_usd=cost)
            ctx.recorder.record(
                "provider_call",
                f"{spec.name} failed: {exc.message}",
                node=node,
                level="error",
                data={"tool": spec.name, "status": "failed", "attempts": attempts, "error_code": exc.code},
            )
            raise

        ctx.budget.settle(reserved, response.cost_usd)
        with self.db.session() as s:
            entry = s.get(ProviderCacheEntry, key)
            payload = {"result": response.result}
            if entry is None:
                s.add(ProviderCacheEntry(request_hash=key, endpoint=spec.endpoint, payload=payload, cost_usd=response.cost_usd,
                                         provider_task_id=response.task_id, created_at=response.fetched_at))
            else:
                entry.payload = payload
                entry.cost_usd = response.cost_usd
                entry.provider_task_id = response.task_id
                entry.created_at = response.fetched_at
        self._record_call(ctx, node, spec, key, task, status="success", attempts=response.attempts,
                          duration_ms=response.duration_ms, cost_usd=response.cost_usd, provider_task_id=response.task_id)
        self.metrics.external_call(metric_key, retries=response.attempts - 1, cost_usd=response.cost_usd)
        ctx.recorder.record(
            "provider_call",
            f"{spec.name} succeeded in {response.duration_ms} ms (${response.cost_usd:.4f}, {response.attempts} attempt(s))",
            node=node,
            data={"tool": spec.name, "status": "success", "attempts": response.attempts, "cost_usd": response.cost_usd,
                  "duration_ms": response.duration_ms, "provider_task_id": response.task_id},
        )
        return response
