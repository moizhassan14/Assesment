"""SQLAlchemy ORM models.

History is append-only by design: a run stores its own ``brand_snapshot`` and all
of its queries, observations, opportunities and recommendations reference the
run, so editing a brand or re-running never rewrites earlier measurements.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils.time import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80))
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    location_code: Mapped[int] = mapped_column(Integer)
    language_code: Mapped[str] = mapped_column(String(8))
    seed_keywords: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    competitors: Mapped[list["Competitor"]] = relationship(
        back_populates="brand", cascade="all, delete-orphan", order_by="Competitor.position"
    )
    runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="brand")


class Competitor(Base):
    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("brand_id", "domain", name="uq_competitor_domain"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    domain: Mapped[str] = mapped_column(String(253))
    position: Mapped[int] = mapped_column(Integer, default=0)

    brand: Mapped[Brand] = relationship(back_populates="competitors")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_runs_brand_created", "brand_id", "created_at"),
        UniqueConstraint("brand_id", "idempotency_key", name="uq_run_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    mode: Mapped[str] = mapped_column(String(20), default="discover")
    question: Mapped[str] = mapped_column(Text)
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64))
    source_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    brand_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    errors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    cost: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    headline: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    report_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    brand: Mapped[Brand] = relationship(back_populates="runs")
    nodes: Mapped[list["RunNode"]] = relationship(cascade="all, delete-orphan", order_by="RunNode.position")


class RunNode(Base):
    """Current state of one DAG node within one run (one row per node)."""

    __tablename__ = "run_nodes"
    __table_args__ = (UniqueConstraint("run_id", "name", name="uq_run_node"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class RunEvent(Base):
    """Append-only trace event log for a run (node-by-node timeline)."""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(10), default="info")
    message: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProviderCall(Base):
    """One logical external call (after retries), for cost accounting and debugging."""

    __tablename__ = "provider_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=True)
    node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(64))
    endpoint: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64), index=True)
    request_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16))  # success | cache_hit | failed | blocked
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    provider_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderCacheEntry(Base):
    """Raw successful provider task payloads keyed by a hash of (endpoint, request).

    Re-using an identical completed request inside the TTL costs nothing, which is
    how we avoid paying twice for the same work.
    """

    __tablename__ = "provider_cache"

    request_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    provider_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchQuery(Base):
    __tablename__ = "research_queries"
    __table_args__ = (UniqueConstraint("run_id", "normalized_text", name="uq_run_query"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(200))
    normalized_text: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(32))
    selected_for_measurement: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_prompt: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    query_id: Mapped[str] = mapped_column(ForeignKey("research_queries.id", ondelete="CASCADE"), index=True)
    surface: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32), default="dataforseo")
    endpoint: Mapped[str] = mapped_column(String(200))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    brand: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    competitors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    cited_sources: Mapped[list[Any]] = mapped_column(JSON, default=list)
    top_results: Mapped[list[Any]] = mapped_column(JSON, default=list)
    answer_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    provider_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    query_id: Mapped[str] = mapped_column(ForeignKey("research_queries.id", ondelete="CASCADE"))
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(16))
    coverage: Mapped[float] = mapped_column(Float)
    is_partial: Mapped[bool] = mapped_column(Boolean)
    components: Mapped[dict[str, Any]] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"))
    priority: Mapped[str] = mapped_column(String(10))
    content_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    angle: Mapped[str] = mapped_column(Text)
    gap_explanation: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str] = mapped_column(Text)
    supporting_observations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    limitations: Mapped[str] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=0)


class ClientError(Base):
    __tablename__ = "client_errors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stack: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMQuota(Base):
    """Persistent LLM call counter. Reservation is an atomic conditional UPDATE (survives restarts)."""

    __tablename__ = "llm_quota"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # "global"
    calls_used: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LLMCall(Base):
    """Audit log of every LLM request attempt (never stores prompts or keys)."""

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24))  # success | failed | cache_hit | blocked_quota | blocked_input_size | pre_quota_record
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    counted: Mapped[bool] = mapped_column(Boolean, default=False)  # consumed quota
    estimated_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCacheEntry(Base):
    """Successful LLM responses keyed by a hash of (provider, model, full request)."""

    __tablename__ = "llm_cache"

    request_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(80))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
