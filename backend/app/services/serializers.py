"""ORM → API JSON serializers (shapes defined in docs/api-contract.md)."""

from __future__ import annotations

from typing import Any

from app.graph.nodes import NODE_AGENTS, NODE_LABELS, NODE_ORDER
from app.models import AnalysisRun, Brand, Observation, Opportunity, Recommendation, ResearchQuery, RunNode
from app.schemas.api import market_info
from app.utils.time import iso


def market_json(location_code: int, language_code: str) -> dict[str, Any]:
    info = market_info(location_code, language_code) or {}
    return {
        "location_code": location_code,
        "language_code": language_code,
        "location_name": info.get("location_name"),
        "language_name": info.get("language_name"),
    }


def brand_snapshot(brand: Brand) -> dict[str, Any]:
    """Immutable copy stored on each run (also the BrandContext used by the DAG)."""
    market = market_json(brand.location_code, brand.language_code)
    return {
        "id": brand.id,
        "name": brand.name,
        "domain": brand.domain,
        "description": brand.description,
        "location_code": brand.location_code,
        "language_code": brand.language_code,
        "location_name": market["location_name"] or str(brand.location_code),
        "language_name": market["language_name"] or brand.language_code,
        "seed_keywords": list(brand.seed_keywords or []),
        "competitors": [{"name": c.name, "domain": c.domain} for c in brand.competitors],
    }


def brand_json(brand: Brand, latest_run: AnalysisRun | None, run_count: int) -> dict[str, Any]:
    return {
        "id": brand.id,
        "name": brand.name,
        "domain": brand.domain,
        "description": brand.description,
        "market": market_json(brand.location_code, brand.language_code),
        "seed_keywords": list(brand.seed_keywords or []),
        "competitors": [{"id": c.id, "name": c.name, "domain": c.domain} for c in brand.competitors],
        "created_at": iso(brand.created_at),
        "updated_at": iso(brand.updated_at),
        "latest_run": run_summary_json(latest_run) if latest_run else None,
        "stats": {"run_count": run_count, "last_run_at": iso(latest_run.created_at) if latest_run else None},
    }


def snapshot_as_brand(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": snapshot.get("id"),
        "name": snapshot.get("name"),
        "domain": snapshot.get("domain"),
        "description": snapshot.get("description"),
        "market": market_json(snapshot.get("location_code", 0), snapshot.get("language_code", "")),
        "seed_keywords": snapshot.get("seed_keywords", []),
        "competitors": [{"id": None, **c} for c in snapshot.get("competitors", [])],
    }


def _duration(run: AnalysisRun) -> int | None:
    if run.started_at and run.finished_at:
        return int((run.finished_at - run.started_at).total_seconds() * 1000)
    return None


def run_summary_json(run: AnalysisRun) -> dict[str, Any]:
    terminal = run.status in ("success", "partial_success", "failed")
    return {
        "id": run.id,
        "brand_id": run.brand_id,
        "status": run.status,
        "mode": run.mode,
        "data_mode": (run.config or {}).get("data_mode", "live"),
        "question": run.question,
        "progress": {
            "percent": 100 if terminal else int(run.progress_percent or 0),
            "current_node": None if terminal else run.current_node,
            "message": run.progress_message or ("Waiting for a worker" if run.status == "queued" else None),
        },
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "duration_ms": _duration(run),
        "headline": run.headline if terminal else None,
        "error_count": len(run.errors or []),
        "status_url": f"/api/runs/{run.id}",
        "report_url": f"/api/runs/{run.id}/report",
    }


def node_json(node: RunNode) -> dict[str, Any]:
    label, stage = NODE_LABELS.get(node.name, (node.name, "setup"))
    return {
        "name": node.name,
        "label": label,
        "stage": stage,
        "agent": NODE_AGENTS.get(node.name, "orchestrator"),
        "status": node.status,
        "message": node.message,
        "started_at": iso(node.started_at),
        "finished_at": iso(node.finished_at),
        "duration_ms": node.duration_ms,
        "retry_count": node.retry_count or 0,
        "error": node.error,
    }


def run_detail_json(run: AnalysisRun) -> dict[str, Any]:
    data = run_summary_json(run)
    nodes = sorted(run.nodes, key=lambda n: NODE_ORDER.index(n.name) if n.name in NODE_ORDER else 99)
    finished = sum(1 for n in nodes if n.status in ("success", "degraded", "failed"))
    if run.status in ("success", "partial_success", "failed"):
        expected = finished  # terminal: the executed path is known exactly
    else:
        # Typical path length; the degraded discovery path adds one node, so never report fewer than done.
        expected = max(10 if run.mode == "remeasure" else 12, finished)
    data["progress"]["completed_nodes"] = finished
    data["progress"]["total_nodes"] = expected
    cost = run.cost or {}
    config = dict(run.config or {})
    data.update({
        "correlation_id": run.correlation_id,
        "nodes": [node_json(n) for n in nodes],
        "errors": list(run.errors or []),
        "cost": {
            "provider_cost_usd": float(cost.get("provider_cost_usd", 0.0)),
            "provider_calls": int(cost.get("provider_calls", 0)),
            "provider_cache_hits": int(cost.get("provider_cache_hits", 0)),
            "llm_calls": int(cost.get("llm_calls", 0)),
            "llm_input_tokens": int(cost.get("llm_input_tokens", 0)),
            "llm_output_tokens": int(cost.get("llm_output_tokens", 0)),
        },
        "config": config,
        "brand_snapshot": snapshot_as_brand(run.brand_snapshot or {}),
    })
    return data


def query_json(q: ResearchQuery) -> dict[str, Any]:
    return {
        "id": q.id,
        "text": q.text,
        "source": q.source,
        "selected_for_measurement": q.selected_for_measurement,
        "ai_prompt": q.ai_prompt,
        "metrics": q.metrics,
    }


def observation_json(o: Observation, query_text: str) -> dict[str, Any]:
    brand = o.brand or {}
    return {
        "id": o.id,
        "query_id": o.query_id,
        "query": query_text,
        "surface": o.surface,
        "status": o.status,
        "provider": o.provider,
        "endpoint": o.endpoint,
        "fetched_at": iso(o.fetched_at),
        "cached": bool(o.cached),
        "cost_usd": float(o.cost_usd or 0.0),
        "brand": {"mentioned": brand.get("mentioned"), "cited": brand.get("cited"), "rank": brand.get("rank"),
                  "url": brand.get("url")},
        "competitors": [
            {k: c.get(k) for k in ("name", "domain", "mentioned", "cited", "rank", "url")} for c in (o.competitors or [])
        ],
        "cited_sources": list(o.cited_sources or []),
        "top_results": list(o.top_results or []),
        "answer_excerpt": o.answer_excerpt,
        "error": o.error,
    }


def opportunity_json(o: Opportunity, query: ResearchQuery | None) -> dict[str, Any]:
    return {
        "id": o.id,
        "rank": o.rank,
        "query_id": o.query_id,
        "query": query.text if query else None,
        "score": o.score,
        "confidence": o.confidence,
        "coverage": o.coverage,
        "is_partial": o.is_partial,
        "components": o.components,
        "explanation": o.explanation,
        "metrics": query.metrics if query else None,
    }


def recommendation_json(r: Recommendation, query_text: str | None) -> dict[str, Any]:
    return {
        "id": r.id,
        "opportunity_id": r.opportunity_id,
        "query": query_text,
        "priority": r.priority,
        "content_type": r.content_type,
        "title": r.title,
        "angle": r.angle,
        "gap_explanation": r.gap_explanation,
        "recommended_action": r.recommended_action,
        "supporting_observations": list(r.supporting_observations or []),
        "limitations": r.limitations,
        "generated_by": r.generated_by,
    }
