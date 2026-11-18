"""Typed contracts passed between DAG nodes.

These are the explicit interfaces between agents: planner → retrieval →
extraction → analysis → synthesis → report. Nothing crosses a node boundary as
an untyped dict except raw provider payloads (which only extraction reads).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.tools.schemas import (
    CheckChatGPTAnswerArgs,
    CheckGoogleOrganicSerpArgs,
    DiscoverKeywordIdeasArgs,
    GetKeywordMetricsArgs,
)

QuerySource = Literal["keyword_discovery", "research_question", "planner", "seed_keyword", "tracked_query"]
Surface = Literal["chatgpt", "google_organic", "google_ai_overview"]
ObservationStatus = Literal["measured", "not_present", "failed", "skipped"]
Confidence = Literal["high", "medium", "low", "insufficient"]
PlanSource = Literal["llm", "llm_repaired", "rule_based", "fallback_rule_based", "tracked_queries"]


class _Model(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)


class BrandContext(_Model):
    id: str
    name: str
    domain: str
    description: str | None
    location_code: int
    language_code: str
    location_name: str
    language_name: str
    seed_keywords: list[str]
    competitors: list[dict[str, str]]  # [{name, domain}]


class KeywordMetrics(_Model):
    search_volume: int | None = None
    paid_competition: float | None = None
    paid_competition_level: str | None = None
    cpc_usd: float | None = None
    keyword_difficulty: int | None = None
    search_intent: str | None = None
    source: str
    fetched_at: datetime | None = None


class KeywordCandidate(_Model):
    text: str
    source: QuerySource
    metrics: KeywordMetrics | None = None


class DiscoveryPlan(_Model):
    call: DiscoverKeywordIdeasArgs | None
    plan_source: PlanSource
    notes: list[str] = Field(default_factory=list)


class PlannedQuery(_Model):
    text: str
    source: QuerySource
    ai_prompt: str | None = None


class MeasurementPlan(_Model):
    queries: list[PlannedQuery]
    metrics_call: GetKeywordMetricsArgs | None = None
    serp_calls: list[CheckGoogleOrganicSerpArgs] = Field(default_factory=list)
    ai_calls: list[CheckChatGPTAnswerArgs] = Field(default_factory=list)
    plan_source: PlanSource
    notes: list[str] = Field(default_factory=list)


class RawPayload(_Model):
    """Output of a retrieval node for one tool call: the untouched provider result or the error."""

    tool: str
    query_text: str | None
    endpoint: str
    result: list[dict[str, Any]] | None = None
    error: dict[str, Any] | None = None
    fetched_at: datetime | None = None
    cached: bool = False
    cost_usd: float = 0.0
    provider_task_id: str | None = None


class EntityVisibility(_Model):
    mentioned: bool | None = None
    cited: bool | None = None
    rank: int | None = None
    url: str | None = None


class CompetitorVisibility(EntityVisibility):
    name: str
    domain: str


class CitedSource(_Model):
    domain: str | None
    url: str
    title: str | None = None


class SerpResult(_Model):
    rank: int
    domain: str | None
    url: str | None
    title: str | None


class NormalizedObservation(_Model):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query_text: str
    surface: Surface
    status: ObservationStatus
    endpoint: str
    fetched_at: datetime | None = None
    cached: bool = False
    cost_usd: float = 0.0
    brand: EntityVisibility = Field(default_factory=EntityVisibility)
    competitors: list[CompetitorVisibility] = Field(default_factory=list)
    cited_sources: list[CitedSource] = Field(default_factory=list)
    top_results: list[SerpResult] = Field(default_factory=list)
    answer_excerpt: str | None = None
    error: dict[str, Any] | None = None
    provider_task_id: str | None = None


class NormalizedQuery(_Model):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    source: QuerySource
    selected_for_measurement: bool
    ai_prompt: str | None = None
    metrics: KeywordMetrics | None = None


class ScoreComponent(_Model):
    value: float | None
    weight: float
    measured: bool
    raw: dict[str, Any] = Field(default_factory=dict)
    explanation: str


class ScoredOpportunity(_Model):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query_id: str
    query_text: str
    score: float | None
    rank: int | None = None
    confidence: Confidence
    coverage: float
    is_partial: bool
    components: dict[str, ScoreComponent]
    explanation: str


class RecommendationOut(_Model):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    opportunity_id: str
    query_text: str
    priority: Literal["high", "medium", "low"]
    content_type: str
    title: str
    angle: str
    gap_explanation: str
    recommended_action: str
    supporting_observations: list[dict[str, Any]]
    limitations: str
    generated_by: Literal["llm", "rule_based_fallback"]
