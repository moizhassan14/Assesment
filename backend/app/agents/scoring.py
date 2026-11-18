"""Opportunity scoring — deterministic, explainable, no LLM involved.

    opportunity = 100 × Σ(wᵢ·xᵢ) / Σ(wᵢ)   over MEASURED components only

Components (each 0..1, higher = bigger opportunity):

* demand (w=0.30)              log10(1+volume) / log10(1+max volume in this run's cohort)
* ai_gap (w=0.35)              1 − best brand presence across measured AI surfaces
                               (cited=1.0, named-but-not-cited=0.5, absent=0.0).
                               ChatGPT counts when measured; Google AI Overview counts
                               only when Google actually showed one.
* organic_gap (w=0.20)         0 if brand ranks top-3; (rank−3)/(depth−2) for ranks 4..depth;
                               1.0 if the brand is not in the fetched results.
* competitor_pressure (w=0.15) share of tracked competitors visible on any measured surface
                               (AI mention/citation or organic top-10). Not applicable when
                               the brand tracks no competitors.

Missing data is never treated as zero visibility: an unmeasured component is
excluded and lowers ``coverage`` (= measured weight / applicable weight).
coverage < 0.5 → score is ``None`` and confidence ``insufficient`` (not ranked).
Confidence: high ≥ 0.99, medium ≥ 0.7, low ≥ 0.5.
Note: Google Ads competition is paid-search competition and is NOT used as an
organic-difficulty signal; it is reported for context only.
"""

from __future__ import annotations

import math
from typing import Any

from app.schemas.domain import (
    NormalizedObservation,
    NormalizedQuery,
    ScoreComponent,
    ScoredOpportunity,
)

SCORING_VERSION = "opportunity-v1"
WEIGHTS: dict[str, float] = {"demand": 0.30, "ai_gap": 0.35, "organic_gap": 0.20, "competitor_pressure": 0.15}
MIN_COVERAGE = 0.5


def _presence(obs: NormalizedObservation) -> tuple[float, str]:
    if obs.brand.cited:
        return 1.0, "cited"
    if obs.brand.mentioned:
        return 0.5, "mentioned_not_cited"
    return 0.0, "absent"


def _demand(volume: int | None, max_volume: int | None) -> ScoreComponent:
    if volume is None:
        return ScoreComponent(value=None, weight=WEIGHTS["demand"], measured=False, raw={"search_volume": None},
                              explanation="Search volume unavailable for this query (not counted).")
    if not max_volume:
        value = 0.0
    else:
        value = math.log10(1 + volume) / math.log10(1 + max_volume)
    return ScoreComponent(value=round(value, 4), weight=WEIGHTS["demand"], measured=True,
                          raw={"search_volume": volume, "cohort_max_volume": max_volume},
                          explanation=f"{volume:,} monthly Google searches ({value:.0%} of the highest-demand query in this run, log-scaled).")


def _ai_gap(chatgpt: NormalizedObservation | None, aio: NormalizedObservation | None) -> ScoreComponent:
    raw: dict[str, Any] = {
        "chatgpt": chatgpt.status if chatgpt else "not_measured",
        "google_ai_overview": aio.status if aio else "not_measured",
    }
    presences: list[tuple[float, str, str]] = []
    if chatgpt and chatgpt.status == "measured":
        p, label = _presence(chatgpt)
        raw["chatgpt"] = label
        presences.append((p, label, "ChatGPT"))
    if aio and aio.status == "measured":
        p, label = _presence(aio)
        raw["google_ai_overview"] = label
        presences.append((p, label, "Google AI Overview"))
    if not presences:
        return ScoreComponent(value=None, weight=WEIGHTS["ai_gap"], measured=False, raw=raw,
                              explanation="No AI answer could be measured for this query (not counted).")
    best = max(presences, key=lambda t: t[0])
    value = 1.0 - best[0]
    parts = [f"{surface}: {label.replace('_', ' ')}" for _, label, surface in presences]
    return ScoreComponent(value=round(value, 4), weight=WEIGHTS["ai_gap"], measured=True, raw=raw,
                          explanation="Brand in AI answers — " + "; ".join(parts) + ".")


def _organic_gap(organic: NormalizedObservation | None, depth: int) -> ScoreComponent:
    if not organic or organic.status != "measured":
        return ScoreComponent(value=None, weight=WEIGHTS["organic_gap"], measured=False,
                              raw={"best_rank": None, "depth": depth},
                              explanation="Google organic results unavailable for this query (not counted).")
    rank = organic.brand.rank
    if rank is None:
        value, text = 1.0, f"Brand not found in Google's top {depth} organic results."
    elif rank <= 3:
        value, text = 0.0, f"Brand already ranks #{rank} organically."
    else:
        value = min(1.0, (rank - 3) / (depth - 2))
        text = f"Brand ranks #{rank} organically (below the top 3)."
    return ScoreComponent(value=round(value, 4), weight=WEIGHTS["organic_gap"], measured=True,
                          raw={"best_rank": rank, "depth": depth}, explanation=text)


def _competitor_pressure(obs: list[NormalizedObservation], tracked: list[dict[str, str]]) -> ScoreComponent | None:
    if not tracked:
        return None  # not applicable
    measured = [o for o in obs if o.status == "measured"]
    if not measured:
        return ScoreComponent(value=None, weight=WEIGHTS["competitor_pressure"], measured=False,
                              raw={"visible": None, "tracked": len(tracked)},
                              explanation="Competitor visibility unavailable (no surface measured).")
    visible: list[str] = []
    for comp in tracked:
        for o in measured:
            cv = next((c for c in o.competitors if c.domain == comp["domain"]), None)
            if cv is None:
                continue
            if o.surface == "google_organic" and cv.rank is not None and cv.rank <= 10:
                visible.append(comp["name"])
                break
            if o.surface != "google_organic" and (cv.mentioned or cv.cited):
                visible.append(comp["name"])
                break
    value = len(visible) / len(tracked)
    names = ", ".join(visible) if visible else "none"
    return ScoreComponent(value=round(value, 4), weight=WEIGHTS["competitor_pressure"], measured=True,
                          raw={"visible": len(visible), "tracked": len(tracked), "visible_names": visible},
                          explanation=f"{len(visible)} of {len(tracked)} tracked competitors visible ({names}).")


def score_query(
    query: NormalizedQuery,
    observations: list[NormalizedObservation],
    competitors: list[dict[str, str]],
    cohort_max_volume: int | None,
    depth: int = 10,
) -> ScoredOpportunity:
    by_surface = {o.surface: o for o in observations}
    volume = query.metrics.search_volume if query.metrics else None
    components: dict[str, ScoreComponent] = {
        "demand": _demand(volume, cohort_max_volume),
        "ai_gap": _ai_gap(by_surface.get("chatgpt"), by_surface.get("google_ai_overview")),
        "organic_gap": _organic_gap(by_surface.get("google_organic"), depth),
    }
    pressure = _competitor_pressure(observations, competitors)
    if pressure is not None:
        components["competitor_pressure"] = pressure

    applicable = sum(c.weight for c in components.values())
    measured = [c for c in components.values() if c.measured and c.value is not None]
    measured_weight = sum(c.weight for c in measured)
    coverage = round(measured_weight / applicable, 4) if applicable else 0.0

    if coverage < MIN_COVERAGE or measured_weight == 0:
        score = None
        confidence = "insufficient"
    else:
        score = round(100 * sum(c.weight * (c.value or 0.0) for c in measured) / measured_weight, 1)
        confidence = "high" if coverage >= 0.99 else "medium" if coverage >= 0.7 else "low"

    explanation = _explain(components, score, coverage)
    # A surface that failed (or was skipped by budget) is unknown evidence even when another surface
    # covered the same component: flag the opportunity as partial and lower confidence one level.
    unknown = sorted({o.surface for o in observations if o.status in ("failed", "skipped")})
    if unknown:
        if confidence == "high":
            confidence = "medium"
        elif confidence == "medium":
            confidence = "low"
        explanation += f" Unknown (measurement failed): {', '.join(s.replace('_', ' ') for s in unknown)}."
    return ScoredOpportunity(
        query_id=query.id, query_text=query.text, score=score, confidence=confidence, coverage=coverage,  # type: ignore[arg-type]
        is_partial=coverage < 0.99 or bool(unknown), components=components, explanation=explanation,
    )


def _explain(components: dict[str, ScoreComponent], score: float | None, coverage: float) -> str:
    if score is None:
        missing = [k.replace("_", " ") for k, c in components.items() if not c.measured]
        return f"Not scored: only {coverage:.0%} of the scoring inputs were measured (missing: {', '.join(missing)})."
    contrib = sorted(
        ((k, c.weight * (c.value or 0.0), c) for k, c in components.items() if c.measured),
        key=lambda t: t[1], reverse=True,
    )
    drivers = [c.explanation for _, v, c in contrib[:2] if v > 0]
    text = " ".join(drivers) if drivers else "The brand is already well covered on every measured surface."
    if coverage < 0.99:
        missing = [k.replace("_", " ") for k, c in components.items() if not c.measured]
        text += f" Partially measured ({coverage:.0%} coverage; missing: {', '.join(missing)})."
    return text


def rank_opportunities(opps: list[ScoredOpportunity], volumes: dict[str, int | None]) -> list[ScoredOpportunity]:
    """Sort by score desc (unscored last), ties by volume desc then query text — fully deterministic."""
    ordered = sorted(
        opps,
        key=lambda o: (o.score is None, -(o.score or 0.0), -(volumes.get(o.query_text) or 0), o.query_text),
    )
    rank = 0
    for opp in ordered:
        if opp.score is not None:
            rank += 1
            opp.rank = rank
        else:
            opp.rank = None
    return ordered


def score_all(
    queries: list[NormalizedQuery],
    observations: list[NormalizedObservation],
    competitors: list[dict[str, str]],
    depth: int = 10,
) -> list[ScoredOpportunity]:
    selected = [q for q in queries if q.selected_for_measurement]
    volumes = {q.text: (q.metrics.search_volume if q.metrics else None) for q in selected}
    measured_volumes = [v for v in volumes.values() if v is not None]
    cohort_max = max(measured_volumes) if measured_volumes else None
    opps = [
        score_query(q, [o for o in observations if o.query_text == q.text], competitors, cohort_max, depth)
        for q in selected
    ]
    return rank_opportunities(opps, volumes)


def priority_for(opp: ScoredOpportunity) -> str:
    if opp.score is None:
        return "low"
    if opp.score >= 60 and opp.confidence in ("high", "medium"):
        return "high"
    if opp.score >= 35:
        return "medium"
    return "low"
