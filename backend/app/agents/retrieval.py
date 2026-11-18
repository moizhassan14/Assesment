"""Search / Retrieval agent(s).

Sole responsibility: execute *validated* DataForSEO tool calls and hand back the
raw provider results (or the classified error). No parsing, scoring or reasoning
happens here. Each logical retrieval path is a separate DAG node so one failing
branch never discards another branch's data.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.graph.context import RunContext, error_from_exception
from app.integrations.dataforseo.client import ProviderResponse
from app.resilience.errors import ExternalServiceError
from app.schemas.domain import RawPayload


def _to_payload(tool: str, endpoint: str, query: str | None, response: ProviderResponse) -> RawPayload:
    return RawPayload(
        tool=tool,
        query_text=query,
        endpoint=endpoint,
        result=response.result,
        fetched_at=response.fetched_at,
        cached=response.cached,
        cost_usd=response.cost_usd,
        provider_task_id=response.task_id,
    )


def execute_calls(
    ctx: RunContext,
    node: str,
    tool_name: str,
    endpoint: str,
    calls: list[tuple[str | None, BaseModel]],
    fn: Callable[[Any, Any, str], ProviderResponse],
    label: Callable[[str | None], str],
) -> tuple[list[RawPayload], list[dict[str, Any]]]:
    """Run calls sequentially inside one branch; failures are captured per call."""
    payloads: list[RawPayload] = []
    errors: list[dict[str, Any]] = []
    tool_ctx = ctx.tool_ctx()
    for index, (query, args) in enumerate(calls, start=1):
        ctx.recorder.progress(node, f"{label(query)} ({index}/{len(calls)})")
        try:
            response = fn(args, tool_ctx, node)
            payloads.append(_to_payload(tool_name, endpoint, query, response))
        except ExternalServiceError as exc:
            err = error_from_exception(node, exc)
            err["query"] = query
            errors.append(err)
            payloads.append(RawPayload(tool=tool_name, query_text=query, endpoint=endpoint, error=err))
    return payloads, errors
