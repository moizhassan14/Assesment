"""LangGraph state for one analysis run.

Keys written by parallel branches use additive reducers so concurrent updates
merge instead of overwriting each other.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from app.schemas.domain import (
    BrandContext,
    DiscoveryPlan,
    KeywordCandidate,
    MeasurementPlan,
    NormalizedObservation,
    NormalizedQuery,
    PlannedQuery,
    RawPayload,
    RecommendationOut,
    ScoredOpportunity,
)


class RunState(TypedDict, total=False):
    # inputs
    run_id: str
    brand: BrandContext
    question: str
    mode: str
    # setup
    abort: dict[str, Any] | None
    question_queries: list[str]
    tracked_queries: list[PlannedQuery]
    tracked_candidates: list[KeywordCandidate]
    # planning / discovery
    discovery_plan: DiscoveryPlan
    discovery_payload: RawPayload
    candidates: list[KeywordCandidate]
    plan: MeasurementPlan
    # retrieval (one key per parallel branch)
    metrics_payloads: list[RawPayload]
    serp_payloads: list[RawPayload]
    ai_payloads: list[RawPayload]
    # extraction / analysis / report
    queries: list[NormalizedQuery]
    observations: list[NormalizedObservation]
    opportunities: list[ScoredOpportunity]
    recommendations: list[RecommendationOut]
    report: dict[str, Any]
    # accumulated across nodes (parallel-safe)
    errors: Annotated[list[dict[str, Any]], operator.add]
    completed_nodes: Annotated[list[str], operator.add]
    degraded_nodes: Annotated[list[str], operator.add]
