"""Extraction / Normalization agent.

Sole responsibility: turn raw provider payloads into validated, normalized
records. Rules:
* every provider item is validated with a lenient Pydantic model; malformed
  items are skipped and counted, never allowed to crash the run;
* missing data stays ``None`` — it is never coerced to zero;
* a failed measurement becomes an observation with ``status="failed"`` (unknown),
  which is different from a measured absence;
* source endpoint, fetch time, cache flag and provider task id are preserved.

Response shapes follow the DataForSEO v3 docs (Labs keyword_ideas/keyword_overview,
SERP organic live advanced, AI Optimization ChatGPT LLM Responses), read 2026-11-18.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.schemas.domain import (
    BrandContext,
    CitedSource,
    CompetitorVisibility,
    EntityVisibility,
    KeywordCandidate,
    KeywordMetrics,
    MeasurementPlan,
    NormalizedObservation,
    NormalizedQuery,
    RawPayload,
    SerpResult,
)
from app.tools.schemas import CHECK_CHATGPT_ANSWER, CHECK_GOOGLE_ORGANIC_SERP
from app.utils.text import domain_matches, domain_of_url, mentions_entity, normalize_query
from app.utils.time import utcnow


# ----------------------------------------------------------------------- provider item models
class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _KeywordInfo(_Lenient):
    search_volume: int | None = None
    competition: float | None = None
    competition_level: str | None = None
    cpc: float | None = None


class _KeywordProperties(_Lenient):
    keyword_difficulty: int | None = None


class _SearchIntent(_Lenient):
    main_intent: str | None = None


class _KeywordItem(_Lenient):
    keyword: str
    keyword_info: _KeywordInfo | None = None
    keyword_properties: _KeywordProperties | None = None
    search_intent_info: _SearchIntent | None = None


class _OrganicItem(_Lenient):
    type: str
    rank_group: int | None = None
    rank_absolute: int | None = None
    domain: str | None = None
    url: str | None = None
    title: str | None = None


class _Reference(_Lenient):
    domain: str | None = None
    url: str | None = None
    title: str | None = None
    source: str | None = None


class _Annotation(_Lenient):
    title: str | None = None
    url: str | None = None


class _Section(_Lenient):
    type: str | None = None
    text: str | None = None
    annotations: list[_Annotation] | None = None


class _LLMItem(_Lenient):
    type: str | None = None
    sections: list[_Section] | None = None


def _metrics_from_item(item: _KeywordItem, source: str, fetched_at: Any) -> KeywordMetrics:
    info = item.keyword_info or _KeywordInfo()
    props = item.keyword_properties or _KeywordProperties()
    intent = item.search_intent_info or _SearchIntent()
    return KeywordMetrics(
        search_volume=info.search_volume,
        paid_competition=info.competition,
        paid_competition_level=info.competition_level,
        cpc_usd=info.cpc,
        keyword_difficulty=props.keyword_difficulty,
        search_intent=intent.main_intent,
        source=source,
        fetched_at=fetched_at,
    )


def _iter_items(result: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in result or []:
        if isinstance(block, dict) and isinstance(block.get("items"), list):
            items.extend(i for i in block["items"] if isinstance(i, dict))
    return items


# ----------------------------------------------------------------------- keyword candidates
def extract_keyword_candidates(
    payload: RawPayload, brand: BrandContext, question_queries: list[str]
) -> tuple[list[KeywordCandidate], dict[str, int]]:
    stats = {"items": 0, "malformed": 0, "branded_skipped": 0}
    candidates: dict[str, KeywordCandidate] = {}
    for q in question_queries:
        candidates[q] = KeywordCandidate(text=q, source="research_question")
    for raw in _iter_items(payload.result):
        stats["items"] += 1
        try:
            item = _KeywordItem.model_validate(raw)
        except ValidationError:
            stats["malformed"] += 1
            continue
        text = normalize_query(item.keyword)
        if not text or len(text) > 80 or len(text.split()) > 10:
            stats["malformed"] += 1
            continue
        if mentions_entity(text, brand.name, brand.domain):
            stats["branded_skipped"] += 1
            continue
        metrics = _metrics_from_item(item, "dataforseo_labs.keyword_ideas", payload.fetched_at)
        if text in candidates:
            candidates[text].metrics = candidates[text].metrics or metrics
        else:
            candidates[text] = KeywordCandidate(text=text, source="keyword_discovery", metrics=metrics)
    return list(candidates.values()), stats


def fallback_keyword_candidates(brand: BrandContext, question_queries: list[str]) -> list[KeywordCandidate]:
    """Degraded path: no discovery data, so measure the user's own queries/seeds (metrics unknown)."""
    out: dict[str, KeywordCandidate] = {}
    for q in question_queries:
        out[q] = KeywordCandidate(text=q, source="research_question")
    for seed in brand.seed_keywords:
        text = normalize_query(seed)
        if text and text not in out and not mentions_entity(text, brand.name, brand.domain):
            out[text] = KeywordCandidate(text=text, source="seed_keyword")
    return list(out.values())


def extract_metrics_payload(payload: RawPayload) -> dict[str, KeywordMetrics]:
    out: dict[str, KeywordMetrics] = {}
    for raw in _iter_items(payload.result):
        try:
            item = _KeywordItem.model_validate(raw)
        except ValidationError:
            continue
        out[normalize_query(item.keyword)] = _metrics_from_item(item, "dataforseo_labs.keyword_overview", payload.fetched_at)
    return out


# ----------------------------------------------------------------------- SERP
def _failed(query: str, surface: str, endpoint: str, error: dict[str, Any] | None) -> NormalizedObservation:
    return NormalizedObservation(
        query_text=query, surface=surface, status="failed", endpoint=endpoint,  # type: ignore[arg-type]
        brand=EntityVisibility(), error=error, fetched_at=None,
    )


def _ai_overview_parts(item: dict[str, Any]) -> tuple[str, list[_Reference]]:
    texts: list[str] = []
    refs: list[_Reference] = []
    if isinstance(item.get("markdown"), str):
        texts.append(item["markdown"])
    for ref in item.get("references") or []:
        if isinstance(ref, dict):
            refs.append(_Reference.model_validate(ref))
    for sub in item.get("items") or []:
        if not isinstance(sub, dict):
            continue
        for key in ("text", "markdown"):
            if isinstance(sub.get(key), str):
                texts.append(sub[key])
                break
        for ref in sub.get("references") or []:
            if isinstance(ref, dict):
                refs.append(_Reference.model_validate(ref))
    return "\n".join(texts), refs


def normalize_serp(payload: RawPayload, brand: BrandContext, depth: int = 10) -> list[NormalizedObservation]:
    query = payload.query_text or ""
    endpoint = CHECK_GOOGLE_ORGANIC_SERP.endpoint
    if payload.error is not None or payload.result is None:
        return [_failed(query, "google_organic", endpoint, payload.error), _failed(query, "google_ai_overview", endpoint, payload.error)]

    organic: list[SerpResult] = []
    ai_items: list[dict[str, Any]] = []
    for raw in _iter_items(payload.result):
        kind = raw.get("type")
        if kind == "organic":
            try:
                item = _OrganicItem.model_validate(raw)
            except ValidationError:
                continue
            rank = item.rank_group or item.rank_absolute
            if rank is None:
                continue
            organic.append(SerpResult(rank=rank, domain=(item.domain or domain_of_url(item.url) or "").lower() or None,
                                      url=item.url, title=item.title))
        elif kind == "ai_overview":
            ai_items.append(raw)
    organic.sort(key=lambda r: r.rank)

    def best(domain: str) -> SerpResult | None:
        return next((r for r in organic if domain_matches(r.domain, domain)), None)

    brand_hit = best(brand.domain)
    competitors = []
    for comp in brand.competitors:
        hit = best(comp["domain"])
        competitors.append(CompetitorVisibility(name=comp["name"], domain=comp["domain"], mentioned=hit is not None,
                                                cited=False, rank=hit.rank if hit else None, url=hit.url if hit else None))
    organic_obs = NormalizedObservation(
        query_text=query, surface="google_organic", status="measured", endpoint=endpoint,
        fetched_at=payload.fetched_at, cached=payload.cached, cost_usd=payload.cost_usd,
        brand=EntityVisibility(mentioned=brand_hit is not None, cited=False, rank=brand_hit.rank if brand_hit else None,
                               url=brand_hit.url if brand_hit else None),
        competitors=competitors, top_results=organic[:10], provider_task_id=payload.provider_task_id,
    )

    if not ai_items:
        aio = NormalizedObservation(query_text=query, surface="google_ai_overview", status="not_present", endpoint=endpoint,
                                    fetched_at=payload.fetched_at, cached=payload.cached, provider_task_id=payload.provider_task_id)
        return [organic_obs, aio]

    text, refs = _ai_overview_parts(ai_items[0])
    cited = [CitedSource(domain=(r.domain or domain_of_url(r.url) or None), url=r.url, title=r.title) for r in refs if r.url]
    unique_cited = list({c.url: c for c in cited}.values())

    def visibility(name: str, domain: str) -> tuple[bool, bool, str | None]:
        cite = next((c for c in unique_cited if domain_matches(c.domain, domain)), None)
        return mentions_entity(text, name, domain) or cite is not None, cite is not None, cite.url if cite else None

    b_m, b_c, b_u = visibility(brand.name, brand.domain)
    comps = []
    for comp in brand.competitors:
        m, c, u = visibility(comp["name"], comp["domain"])
        comps.append(CompetitorVisibility(name=comp["name"], domain=comp["domain"], mentioned=m, cited=c, url=u))
    aio = NormalizedObservation(
        query_text=query, surface="google_ai_overview", status="measured", endpoint=endpoint,
        fetched_at=payload.fetched_at, cached=payload.cached,
        brand=EntityVisibility(mentioned=b_m, cited=b_c, url=b_u), competitors=comps,
        cited_sources=unique_cited[:15], answer_excerpt=text[:1200] or None, provider_task_id=payload.provider_task_id,
    )
    return [organic_obs, aio]


# ----------------------------------------------------------------------- ChatGPT answer
def normalize_ai_answer(payload: RawPayload, brand: BrandContext) -> NormalizedObservation:
    query = payload.query_text or ""
    endpoint = CHECK_CHATGPT_ANSWER.endpoint
    if payload.error is not None or payload.result is None:
        return _failed(query, "chatgpt", endpoint, payload.error)

    texts: list[str] = []
    cites: list[CitedSource] = []
    for block in payload.result:
        if not isinstance(block, dict):
            continue
        for raw in block.get("items") or []:
            try:
                item = _LLMItem.model_validate(raw)
            except ValidationError:
                continue
            if item.type != "message":
                continue
            for section in item.sections or []:
                if section.text:
                    texts.append(section.text)
                for ann in section.annotations or []:
                    if ann.url:
                        cites.append(CitedSource(domain=domain_of_url(ann.url), url=ann.url, title=ann.title))
    answer = "\n".join(texts).strip()
    if not answer:
        err = {"node": "extract_observations", "code": "provider_unexpected_response",
               "message": "ChatGPT response contained no answer text.", "retryable": False, "provider": "dataforseo",
               "occurred_at": utcnow().isoformat()}
        return _failed(query, "chatgpt", endpoint, err)

    unique = list({c.url: c for c in cites}.values())

    def visibility(name: str, domain: str) -> tuple[bool, bool, str | None]:
        cite = next((c for c in unique if domain_matches(c.domain, domain)), None)
        return mentions_entity(answer, name, domain) or cite is not None, cite is not None, cite.url if cite else None

    b_m, b_c, b_u = visibility(brand.name, brand.domain)
    comps = []
    for comp in brand.competitors:
        m, c, u = visibility(comp["name"], comp["domain"])
        comps.append(CompetitorVisibility(name=comp["name"], domain=comp["domain"], mentioned=m, cited=c, url=u))
    return NormalizedObservation(
        query_text=query, surface="chatgpt", status="measured", endpoint=endpoint, fetched_at=payload.fetched_at,
        cached=payload.cached, cost_usd=payload.cost_usd, brand=EntityVisibility(mentioned=b_m, cited=b_c, url=b_u),
        competitors=comps, cited_sources=unique[:20], answer_excerpt=answer[:1500], provider_task_id=payload.provider_task_id,
    )


# ----------------------------------------------------------------------- assemble
def build_queries(
    plan: MeasurementPlan,
    candidates: list[KeywordCandidate],
    fetched_metrics: dict[str, KeywordMetrics],
) -> list[NormalizedQuery]:
    selected = {q.text: q for q in plan.queries}
    out: list[NormalizedQuery] = []
    seen: set[str] = set()
    for q in plan.queries:
        cand = next((c for c in candidates if c.text == q.text), None)
        metrics = (cand.metrics if cand else None) or fetched_metrics.get(q.text)
        out.append(NormalizedQuery(text=q.text, source=q.source, selected_for_measurement=True, ai_prompt=q.ai_prompt, metrics=metrics))
        seen.add(q.text)
    for c in candidates:
        if c.text not in seen and c.text not in selected:
            out.append(NormalizedQuery(text=c.text, source=c.source, selected_for_measurement=False, metrics=c.metrics))
            seen.add(c.text)
    return out


def skipped_observation(query: str, surface: str, endpoint: str, reason: str) -> NormalizedObservation:
    return NormalizedObservation(query_text=query, surface=surface, status="skipped", endpoint=endpoint,  # type: ignore[arg-type]
                                 error={"code": "budget_exceeded", "message": reason, "retryable": False})
