"""Persistence helpers used by the DAG nodes (write path) — short transactions only."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from app.extensions import Database
from app.models import AnalysisRun, Observation, Opportunity, Recommendation, ResearchQuery
from app.schemas.domain import (
    NormalizedObservation,
    NormalizedQuery,
    RecommendationOut,
    ScoredOpportunity,
)


def _metrics_json(q: NormalizedQuery) -> dict[str, Any] | None:
    if q.metrics is None:
        return None
    data = q.metrics.model_dump(mode="json")
    return data


def save_queries_and_observations(
    db: Database, run_id: str, queries: list[NormalizedQuery], observations: list[NormalizedObservation]
) -> None:
    ids = {q.text: q.id for q in queries}
    with db.session() as s:
        for pos, q in enumerate(queries):
            s.add(ResearchQuery(
                id=q.id, run_id=run_id, text=q.text, normalized_text=q.text, source=q.source,
                selected_for_measurement=q.selected_for_measurement, ai_prompt=q.ai_prompt, metrics=_metrics_json(q),
                position=pos,
            ))
        s.flush()
        for o in observations:
            query_id = ids.get(o.query_text)
            if query_id is None:
                continue
            s.add(Observation(
                id=o.id, run_id=run_id, query_id=query_id, surface=o.surface, status=o.status, endpoint=o.endpoint,
                fetched_at=o.fetched_at, cached=o.cached, cost_usd=o.cost_usd,
                brand=o.brand.model_dump(mode="json"),
                competitors=[c.model_dump(mode="json") for c in o.competitors],
                cited_sources=[c.model_dump(mode="json") for c in o.cited_sources],
                top_results=[r.model_dump(mode="json") for r in o.top_results],
                answer_excerpt=o.answer_excerpt, error=o.error, provider_task_id=o.provider_task_id,
            ))


def save_opportunities(db: Database, run_id: str, opps: list[ScoredOpportunity]) -> None:
    with db.session() as s:
        s.execute(delete(Opportunity).where(Opportunity.run_id == run_id))
        for o in opps:
            s.add(Opportunity(
                id=o.id, run_id=run_id, query_id=o.query_id, rank=o.rank, score=o.score, confidence=o.confidence,
                coverage=o.coverage, is_partial=o.is_partial,
                components={k: c.model_dump(mode="json") for k, c in o.components.items()}, explanation=o.explanation,
            ))


def save_recommendations(db: Database, run_id: str, recs: list[RecommendationOut]) -> None:
    with db.session() as s:
        s.execute(delete(Recommendation).where(Recommendation.run_id == run_id))
        for pos, r in enumerate(recs):
            s.add(Recommendation(
                id=r.id, run_id=run_id, opportunity_id=r.opportunity_id, priority=r.priority, content_type=r.content_type,
                title=r.title, angle=r.angle, gap_explanation=r.gap_explanation, recommended_action=r.recommended_action,
                supporting_observations=r.supporting_observations, limitations=r.limitations, generated_by=r.generated_by,
                position=pos,
            ))


def save_report(db: Database, run_id: str, report: dict[str, Any], markdown: str, headline: dict[str, Any] | None) -> None:
    with db.session() as s:
        run = s.get(AnalysisRun, run_id)
        if run is not None:
            run.report = report
            run.report_markdown = markdown
            run.headline = headline


def load_tracked_queries(db: Database, brand_id: str, exclude_run_id: str, data_mode: str = "live") -> tuple[str | None, list[ResearchQuery]]:
    """Measured queries of the brand's latest run (same data mode) that produced evidence (for 'remeasure')."""
    with db.session() as s:
        runs = s.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.brand_id == brand_id, AnalysisRun.id != exclude_run_id,
                   AnalysisRun.status.in_(("success", "partial_success")))
            .order_by(AnalysisRun.created_at.desc())
        ).all()
        for run in runs:
            if (run.config or {}).get("data_mode", "live") != data_mode:
                continue  # never mix sandbox and live history
            rows = s.scalars(
                select(ResearchQuery)
                .where(ResearchQuery.run_id == run.id, ResearchQuery.selected_for_measurement.is_(True))
                .order_by(ResearchQuery.position)
            ).all()
            if rows:
                for r in rows:
                    s.expunge(r)
                return run.id, list(rows)
    return None, []
