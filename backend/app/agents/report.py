"""Report agent.

Sole responsibility: assemble the final structured report (JSON) and a concise
human-readable summary from data that earlier stages already produced. It does
no fetching and no new analysis — every sentence is built from computed values,
so the summary cannot contain an LLM-invented number.
"""

from __future__ import annotations

from typing import Any

from app.agents.scoring import MIN_COVERAGE, SCORING_VERSION, WEIGHTS
from app.schemas.domain import (
    BrandContext,
    NormalizedObservation,
    RecommendationOut,
    ScoredOpportunity,
)


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def build_visibility(brand: BrandContext, observations: list[NormalizedObservation], queries_measured: int) -> dict[str, Any]:
    chat = [o for o in observations if o.surface == "chatgpt"]
    org = [o for o in observations if o.surface == "google_organic"]
    aio = [o for o in observations if o.surface == "google_ai_overview"]
    chat_m = [o for o in chat if o.status == "measured"]
    org_m = [o for o in org if o.status == "measured"]
    aio_m = [o for o in aio if o.status == "measured"]
    ranks = [o.brand.rank for o in org_m if o.brand.rank is not None]

    def entity_row(name: str, domain: str, is_brand: bool) -> dict[str, Any]:
        def pick(o: NormalizedObservation):  # type: ignore[no-untyped-def]
            if is_brand:
                return o.brand
            return next((c for c in o.competitors if c.domain == domain), None)

        ai_obs = chat_m + aio_m
        ai_mentions = sum(1 for o in ai_obs if (v := pick(o)) is not None and v.mentioned)
        ai_cites = sum(1 for o in ai_obs if (v := pick(o)) is not None and v.cited)
        top10 = sum(1 for o in org_m if (v := pick(o)) is not None and v.rank is not None and v.rank <= 10)
        return {"name": name, "domain": domain, "is_brand": is_brand, "ai_mentions": ai_mentions,
                "ai_citations": ai_cites, "organic_top10": top10}

    sov = [entity_row(brand.name, brand.domain, True)] + [entity_row(c["name"], c["domain"], False) for c in brand.competitors]
    return {
        "queries_measured": queries_measured,
        "chatgpt": {
            "measured": len(chat_m),
            "failed": sum(1 for o in chat if o.status == "failed"),
            "brand_mentioned": sum(1 for o in chat_m if o.brand.mentioned),
            "brand_cited": sum(1 for o in chat_m if o.brand.cited),
        },
        "google_organic": {
            "measured": len(org_m),
            "failed": sum(1 for o in org if o.status == "failed"),
            "brand_top10": sum(1 for r in ranks if r <= 10),
            "best_rank": min(ranks) if ranks else None,
        },
        "google_ai_overview": {
            "measured": len(aio_m) + sum(1 for o in aio if o.status == "not_present"),
            "present": len(aio_m),
            "brand_cited": sum(1 for o in aio_m if o.brand.cited),
        },
        "share_of_voice": sov,
    }


def build_summary(
    brand: BrandContext,
    visibility: dict[str, Any],
    opportunities: list[ScoredOpportunity],
    recommendations: list[RecommendationOut],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    chat = visibility["chatgpt"]
    org = visibility["google_organic"]
    aio = visibility["google_ai_overview"]
    findings: list[str] = []
    if chat["measured"]:
        findings.append(
            f"ChatGPT mentions {brand.name} in {chat['brand_mentioned']} of {chat['measured']} measured answers "
            f"and cites {brand.domain} in {chat['brand_cited']}."
        )
    if org["measured"]:
        best = f" (best rank #{org['best_rank']})" if org["best_rank"] else ""
        findings.append(f"{brand.name} ranks in Google's top 10 for {org['brand_top10']} of {org['measured']} queries{best}.")
    if aio["measured"]:
        findings.append(f"Google showed an AI Overview for {aio['present']} of {aio['measured']} queries; "
                        f"{brand.domain} was cited in {aio['brand_cited']}.")
    leaders = sorted((r for r in visibility["share_of_voice"] if not r["is_brand"]),
                     key=lambda r: (r["ai_mentions"] + r["organic_top10"]), reverse=True)
    if leaders and (leaders[0]["ai_mentions"] + leaders[0]["organic_top10"]) > 0:
        top = leaders[0]
        findings.append(f"Most visible competitor: {top['name']} ({top['ai_mentions']} AI mentions, "
                        f"{top['organic_top10']} top-10 rankings).")
    failed = chat["failed"] + org["failed"]
    if failed or errors:
        findings.append(f"{len(errors)} issue(s) occurred during the run; failed measurements are shown as unknown, not as absent.")

    scored = [o for o in opportunities if o.score is not None]
    if scored:
        top = scored[0]
        headline = (f"Biggest opportunity for {brand.name}: '{top.query_text}' (score {top.score:.0f}/100, "
                    f"{top.confidence} confidence).")
    elif opportunities:
        headline = f"Not enough data was measured to score {brand.name}'s opportunities reliably."
    else:
        headline = f"No visibility evidence could be collected for {brand.name} in this run."

    md = [f"## {headline}", "", "### Key findings"] + [f"- {f}" for f in findings]
    if scored:
        md += ["", "### Ranked opportunities"]
        md += [f"{o.rank}. **{o.query_text}** — {o.score:.1f}/100 ({o.confidence}). {o.explanation}" for o in scored]
    if recommendations:
        md += ["", "### Recommended content"]
        md += [f"- [{r.priority}] **{r.title}** ({r.content_type.replace('_', ' ')}) — {r.recommended_action}" for r in recommendations]
    return {"headline": headline, "key_findings": findings, "markdown": "\n".join(md), "generated_by": "deterministic"}


def build_headline(visibility: dict[str, Any] | None, opportunities: list[ScoredOpportunity], recommendations: list[RecommendationOut]) -> dict[str, Any] | None:
    if visibility is None:
        return None
    chat = visibility["chatgpt"]
    org = visibility["google_organic"]
    scored = [o for o in opportunities if o.score is not None]
    ai_measured = chat["measured"]
    return {
        "queries_measured": visibility["queries_measured"],
        "ai_visibility_rate": _rate(chat["brand_mentioned"], ai_measured),
        "organic_top10_rate": _rate(org["brand_top10"], org["measured"]),
        "top_opportunity_query": scored[0].query_text if scored else None,
        "top_opportunity_score": scored[0].score if scored else None,
        "recommendation_count": len(recommendations),
    }


def scoring_meta() -> dict[str, Any]:
    return {"version": SCORING_VERSION, "weights": WEIGHTS, "min_coverage": MIN_COVERAGE}
