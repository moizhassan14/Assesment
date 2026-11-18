"""Validation of LLM-proposed tool calls *before* any paid external call runs.

Two layers:
1. Schema: unknown tool, malformed JSON, wrong types, missing/extra fields,
   invalid combinations (Pydantic models in ``schemas.py``).
2. Context: the call must be consistent with the run (brand market, allowed
   queries, no brand name in "neutral" AI prompts).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.tools.schemas import (
    CheckChatGPTAnswerArgs,
    CheckGoogleOrganicSerpArgs,
    DiscoverKeywordIdeasArgs,
    GetKeywordMetricsArgs,
    ToolSpec,
)
from app.utils.text import mentions_entity, normalize_query


@dataclass(frozen=True)
class RawToolCall:
    """A tool call as emitted by the LLM (arguments may be a dict or a JSON string)."""

    call_id: str
    name: str
    arguments: Any


@dataclass(frozen=True)
class ValidatedToolCall:
    call_id: str
    name: str
    args: BaseModel


@dataclass(frozen=True)
class RejectedToolCall:
    call_id: str
    name: str
    reasons: list[str]


@dataclass
class ToolContext:
    location_code: int
    language_code: str
    brand_name: str
    brand_domain: str
    allowed_queries: set[str] | None = None  # normalized; None = any query allowed
    competitor_names: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    accepted: list[ValidatedToolCall] = field(default_factory=list)
    rejected: list[RejectedToolCall] = field(default_factory=list)


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    reasons = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        reasons.append(f"{loc}: {err.get('msg', 'invalid value')}")
    return reasons


def _context_errors(args: BaseModel, ctx: ToolContext) -> list[str]:
    reasons: list[str] = []
    location = getattr(args, "location_code", None)
    language = getattr(args, "language_code", None)
    if location is not None and location != ctx.location_code:
        reasons.append(f"location_code must be {ctx.location_code} (the brand's market), got {location}")
    if language is not None and language.lower() != ctx.language_code.lower():
        reasons.append(f"language_code must be '{ctx.language_code}' (the brand's market), got '{language}'")

    if isinstance(args, DiscoverKeywordIdeasArgs):
        for seed in args.seed_keywords:
            if mentions_entity(seed, ctx.brand_name, ctx.brand_domain):
                reasons.append(f"seed_keywords: '{seed}' names the brand; use category terms instead")
    if isinstance(args, CheckChatGPTAnswerArgs):
        if mentions_entity(args.prompt, ctx.brand_name, ctx.brand_domain):
            reasons.append("prompt: must not mention the analysed brand (it would bias the AI answer)")
        if ctx.allowed_queries is not None and normalize_query(args.research_query) not in ctx.allowed_queries:
            reasons.append(f"research_query: '{args.research_query}' is not one of the candidate queries")
    if isinstance(args, CheckGoogleOrganicSerpArgs):
        if ctx.allowed_queries is not None and normalize_query(args.keyword) not in ctx.allowed_queries:
            reasons.append(f"keyword: '{args.keyword}' is not one of the candidate queries")
    if isinstance(args, GetKeywordMetricsArgs) and ctx.allowed_queries is not None:
        unknown = [k for k in args.keywords if normalize_query(k) not in ctx.allowed_queries]
        if unknown:
            reasons.append(f"keywords: not candidate queries: {unknown}")
    return reasons


def validate_tool_call(raw: RawToolCall, available: dict[str, ToolSpec], ctx: ToolContext) -> ValidatedToolCall | RejectedToolCall:
    spec = available.get(raw.name)
    if spec is None:
        return RejectedToolCall(raw.call_id, raw.name, [f"unsupported tool '{raw.name}'; allowed: {sorted(available)}"])

    arguments = raw.arguments
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return RejectedToolCall(raw.call_id, raw.name, [f"arguments are not valid JSON: {exc.msg} at position {exc.pos}"])
    if not isinstance(arguments, dict):
        return RejectedToolCall(raw.call_id, raw.name, ["arguments must be a JSON object"])

    try:
        args = spec.args_model.model_validate(arguments)
    except ValidationError as exc:
        return RejectedToolCall(raw.call_id, raw.name, _format_pydantic_errors(exc))

    reasons = _context_errors(args, ctx)
    if reasons:
        return RejectedToolCall(raw.call_id, raw.name, reasons)
    return ValidatedToolCall(raw.call_id, raw.name, args)


def validate_tool_calls(calls: list[RawToolCall], available: dict[str, ToolSpec], ctx: ToolContext) -> ValidationResult:
    result = ValidationResult()
    for call in calls:
        outcome = validate_tool_call(call, available, ctx)
        if isinstance(outcome, ValidatedToolCall):
            result.accepted.append(outcome)
        else:
            result.rejected.append(outcome)
    return result
