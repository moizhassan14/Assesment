"""DAG node implementations. Each node delegates to exactly one agent function.

A node returns a :class:`NodeResult`; the instrumentation wrapper in
``builder.py`` turns it into a state update, persists node state, emits trace
events/metrics, and converts unexpected exceptions into a failed node instead
of a crashed run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents import extraction, query_planner, report, scoring, synthesis
from app.agents.retrieval import execute_calls
from app.graph.context import RunContext, run_error
from app.graph.state import RunState
from app.repositories import runs as repo
from app.schemas.domain import KeywordCandidate, KeywordMetrics, NormalizedObservation, PlannedQuery, RawPayload
from app.tools.schemas import (
    CHECK_CHATGPT_ANSWER,
    CHECK_GOOGLE_ORGANIC_SERP,
    DISCOVER_KEYWORD_IDEAS,
    GET_KEYWORD_METRICS,
)
from app.resilience.errors import ExternalServiceError
from app.graph.context import error_from_exception


@dataclass
class NodeResult:
    update: dict[str, Any] = field(default_factory=dict)
    status: str = "success"  # success | degraded | failed | skipped
    message: str = ""
    error: dict[str, Any] | None = None


NODE_LABELS: dict[str, tuple[str, str]] = {
    "prepare_run": ("Preparing run", "setup"),
    "load_tracked_queries": ("Loading tracked queries", "planning"),
    "plan_discovery": ("Planning keyword discovery", "planning"),
    "retrieve_keyword_ideas": ("Discovering search queries", "retrieval"),
    "extract_keyword_candidates": ("Normalizing keyword ideas", "extraction"),
    "fallback_candidates": ("Using fallback queries", "extraction"),
    "plan_measurement": ("Selecting research queries", "planning"),
    "retrieve_keyword_metrics": ("Fetching keyword metrics", "retrieval"),
    "retrieve_serp": ("Google organic results", "retrieval"),
    "retrieve_ai_answers": ("ChatGPT answers", "retrieval"),
    "extract_observations": ("Normalizing evidence", "extraction"),
    "score_opportunities": ("Scoring opportunities", "analysis"),
    "synthesize_recommendations": ("Writing recommendations", "analysis"),
    "assemble_report": ("Assembling report", "report"),
}
NODE_AGENTS: dict[str, str] = {
    "prepare_run": "orchestrator",
    "load_tracked_queries": "query_planner",
    "plan_discovery": "query_planner",
    "plan_measurement": "query_planner",
    "retrieve_keyword_ideas": "search_retrieval",
    "retrieve_keyword_metrics": "search_retrieval",
    "retrieve_serp": "search_retrieval",
    "retrieve_ai_answers": "search_retrieval",
    "extract_keyword_candidates": "extraction",
    "fallback_candidates": "extraction",
    "extract_observations": "extraction",
    "score_opportunities": "analysis",
    "synthesize_recommendations": "analysis",
    "assemble_report": "report",
}
NODE_ORDER: list[str] = list(NODE_LABELS)


# ------------------------------------------------------------------------------ setup
def prepare_run(ctx: RunContext, state: RunState) -> NodeResult:
    brand = state["brand"]
    if not ctx.settings.dataforseo_configured:
        err = run_error("prepare_run", "provider_auth_failed",
                        "DataForSEO credentials are not configured; no live data can be fetched.", provider="dataforseo")
        return NodeResult({"abort": err, "errors": [err]}, "failed", "Cannot run: DataForSEO credentials missing", err)
    q_queries = query_planner.queries_from_question(state["question"], brand)
    msg = f"Mode '{state['mode']}', market {brand.location_name}/{brand.language_code}, {len(brand.competitors)} competitor(s)"
    return NodeResult({"abort": None, "question_queries": q_queries}, "success", msg)


def load_tracked_queries(ctx: RunContext, state: RunState) -> NodeResult:
    brand = state["brand"]
    source_run, rows = repo.load_tracked_queries(ctx.db, brand.id, ctx.run_id, ctx.settings.data_mode)
    if not rows:
        err = run_error("load_tracked_queries", "no_evidence", "No previously measured queries found to re-measure.")
        return NodeResult({"tracked_queries": [], "tracked_candidates": [], "errors": [err]}, "failed", err["message"], err)
    tracked = [PlannedQuery(text=r.text, source="tracked_query", ai_prompt=r.ai_prompt) for r in rows]
    cands = [
        KeywordCandidate(text=r.text, source="tracked_query",
                         metrics=KeywordMetrics.model_validate(r.metrics) if r.metrics else None)
        for r in rows
    ]
    return NodeResult({"tracked_queries": tracked, "tracked_candidates": cands}, "success",
                      f"Re-measuring {len(tracked)} queries from run {source_run}")


# ------------------------------------------------------------------------------ discovery
def plan_discovery(ctx: RunContext, state: RunState) -> NodeResult:
    plan, errors = query_planner.plan_discovery(ctx, "plan_discovery", state["brand"], state["question"])
    status = "success" if plan.plan_source in ("llm", "llm_repaired", "rule_based") else "degraded"
    seeds = ", ".join(plan.call.seed_keywords) if plan.call else "none"
    return NodeResult({"discovery_plan": plan, "errors": errors}, status, f"Discovery seeds ({plan.plan_source}): {seeds}")


def retrieve_keyword_ideas(ctx: RunContext, state: RunState) -> NodeResult:
    plan = state.get("discovery_plan")
    if plan is None or plan.call is None:
        return NodeResult({}, "skipped", "No discovery call planned")
    payloads, errors = execute_calls(
        ctx, "retrieve_keyword_ideas", DISCOVER_KEYWORD_IDEAS.name, DISCOVER_KEYWORD_IDEAS.endpoint,
        [(None, plan.call)], ctx.tools.discover_keyword_ideas, lambda _q: "Discovering related search queries",
    )
    payload = payloads[0]
    if payload.error:
        return NodeResult({"discovery_payload": payload, "errors": errors}, "failed",
                          f"Keyword discovery failed: {payload.error['message']}", payload.error)
    count = sum(len(b.get("items") or []) for b in payload.result or [] if isinstance(b, dict))
    return NodeResult({"discovery_payload": payload}, "success",
                      f"Received {count} keyword ideas{' (cached)' if payload.cached else ''}")


def extract_keyword_candidates(ctx: RunContext, state: RunState) -> NodeResult:
    cands, stats = extraction.extract_keyword_candidates(state["discovery_payload"], state["brand"], state.get("question_queries", []))
    with_metrics = sum(1 for c in cands if c.metrics and c.metrics.search_volume is not None)
    status = "success" if with_metrics else "degraded"
    return NodeResult({"candidates": cands}, status,
                      f"{len(cands)} candidate queries ({with_metrics} with volume; {stats['malformed']} malformed, "
                      f"{stats['branded_skipped']} branded skipped)")


def fallback_candidates(ctx: RunContext, state: RunState) -> NodeResult:
    cands = extraction.fallback_keyword_candidates(state["brand"], state.get("question_queries", []))
    ctx.recorder.record("fallback_used", "Discovery unavailable — measuring seed keywords / question queries instead",
                        node="fallback_candidates", level="warning")
    return NodeResult({"candidates": cands, "degraded_nodes": ["fallback_candidates"]}, "degraded",
                      f"Using {len(cands)} fallback queries (no discovery data)")


# ------------------------------------------------------------------------------ measurement planning
def plan_measurement(ctx: RunContext, state: RunState) -> NodeResult:
    brand = state["brand"]
    if state["mode"] == "remeasure":
        tracked = state.get("tracked_queries") or []
        cands = state.get("tracked_candidates") or []
        plan = query_planner.build_plan_from_queries(ctx, brand, tracked, {c.text: c for c in cands}, "tracked_queries",
                                                     ["Re-measuring previously tracked queries with identical prompts."])
        ctx.recorder.record("tool_call_validated", f"Deterministic re-measure plan for {len(plan.queries)} queries",
                            node="plan_measurement")
        return NodeResult({"plan": plan, "candidates": cands}, "success" if plan.queries else "failed",
                          f"Re-measure plan: {len(plan.queries)} queries")
    cands = state.get("candidates") or []
    plan, errors = query_planner.plan_measurement(ctx, "plan_measurement", brand, state["question"], cands)
    status = "success" if plan.plan_source in ("llm", "llm_repaired") else ("failed" if not plan.queries else "degraded")
    queries = ", ".join(f"'{q.text}'" for q in plan.queries) or "none"
    return NodeResult({"plan": plan, "errors": errors}, status, f"Selected {len(plan.queries)} queries ({plan.plan_source}): {queries}")


# ------------------------------------------------------------------------------ retrieval branches (parallel)
def retrieve_keyword_metrics(ctx: RunContext, state: RunState) -> NodeResult:
    plan = state.get("plan")
    if plan is None or plan.metrics_call is None:
        return NodeResult({"metrics_payloads": []}, "skipped", "All selected queries already have metrics")
    payloads, errors = execute_calls(
        ctx, "retrieve_keyword_metrics", GET_KEYWORD_METRICS.name, GET_KEYWORD_METRICS.endpoint,
        [(None, plan.metrics_call)], ctx.tools.get_keyword_metrics, lambda _q: "Fetching search volume",
    )
    status = "failed" if errors else "success"
    return NodeResult({"metrics_payloads": payloads, "errors": errors}, status,
                      "Keyword metrics fetched" if not errors else f"Keyword metrics failed: {errors[0]['message']}",
                      errors[0] if errors else None)


def retrieve_serp(ctx: RunContext, state: RunState) -> NodeResult:
    plan = state.get("plan")
    calls = [(c.keyword, c) for c in (plan.serp_calls if plan else [])]
    if not calls:
        return NodeResult({"serp_payloads": []}, "skipped", "No SERP checks planned")
    payloads, errors = execute_calls(
        ctx, "retrieve_serp", CHECK_GOOGLE_ORGANIC_SERP.name, CHECK_GOOGLE_ORGANIC_SERP.endpoint, calls,
        ctx.tools.check_google_organic_serp, lambda q: f"Checking Google results for '{q}'",
    )
    ok = len(payloads) - len(errors)
    status = "success" if not errors else ("degraded" if ok else "failed")
    cached = sum(1 for p in payloads if p.cached)
    return NodeResult({"serp_payloads": payloads, "errors": errors}, status,
                      f"Fetched {ok}/{len(payloads)} SERPs" + (f" ({cached} from cache)" if cached else ""),
                      errors[0] if errors else None)


def retrieve_ai_answers(ctx: RunContext, state: RunState) -> NodeResult:
    plan = state.get("plan")
    calls = [(c.research_query, c) for c in (plan.ai_calls if plan else [])]
    if not calls:
        return NodeResult({"ai_payloads": []}, "skipped", "No AI answer checks planned")
    payloads, errors = execute_calls(
        ctx, "retrieve_ai_answers", CHECK_CHATGPT_ANSWER.name, CHECK_CHATGPT_ANSWER.endpoint, calls,
        ctx.tools.check_chatgpt_answer, lambda q: f"Asking ChatGPT about '{q}'",
    )
    ok = len(payloads) - len(errors)
    status = "success" if not errors else ("degraded" if ok else "failed")
    cached = sum(1 for p in payloads if p.cached)
    return NodeResult({"ai_payloads": payloads, "errors": errors}, status,
                      f"Collected {ok}/{len(payloads)} ChatGPT answers" + (f" ({cached} from cache)" if cached else ""),
                      errors[0] if errors else None)


# ------------------------------------------------------------------------------ extraction / analysis / report
def extract_observations(ctx: RunContext, state: RunState) -> NodeResult:
    brand = state["brand"]
    plan = state.get("plan")
    if plan is None:
        return NodeResult({"queries": [], "observations": []}, "skipped", "Nothing was planned")
    fetched: dict[str, KeywordMetrics] = {}
    for p in state.get("metrics_payloads") or []:
        if p.error is None:
            fetched.update(extraction.extract_metrics_payload(p))
    observations: list[NormalizedObservation] = []
    serp_by_q = {p.query_text: p for p in state.get("serp_payloads") or []}
    ai_by_q = {p.query_text: p for p in state.get("ai_payloads") or []}
    for q in plan.queries:
        serp = serp_by_q.get(q.text)
        if serp is not None:
            observations.extend(extraction.normalize_serp(serp, brand, ctx.settings.serp_depth))
        else:
            observations.append(extraction.skipped_observation(q.text, "google_organic", CHECK_GOOGLE_ORGANIC_SERP.endpoint,
                                                               "SERP check not run (per-run budget)"))
        ai = ai_by_q.get(q.text)
        if ai is not None:
            observations.append(extraction.normalize_ai_answer(ai, brand))
        else:
            observations.append(extraction.skipped_observation(q.text, "chatgpt", CHECK_CHATGPT_ANSWER.endpoint,
                                                               "ChatGPT check not run (per-run budget)"))
    queries = extraction.build_queries(plan, state.get("candidates") or [], fetched)
    repo.save_queries_and_observations(ctx.db, ctx.run_id, queries, observations)
    measured = sum(1 for o in observations if o.status in ("measured", "not_present"))
    failed = sum(1 for o in observations if o.status == "failed")
    status = "success" if not failed else ("degraded" if measured else "failed")
    return NodeResult({"queries": queries, "observations": observations}, status,
                      f"{measured} observations measured, {failed} failed, {len(queries)} queries stored")


def score_opportunities(ctx: RunContext, state: RunState) -> NodeResult:
    brand = state["brand"]
    opps = scoring.score_all(state.get("queries") or [], state.get("observations") or [], brand.competitors,
                             ctx.settings.serp_depth)
    repo.save_opportunities(ctx.db, ctx.run_id, opps)
    scored = [o for o in opps if o.score is not None]
    partial = sum(1 for o in opps if o.is_partial)
    status = "success" if scored and not partial else ("degraded" if scored else "failed")
    top = f"; top: '{scored[0].query_text}' {scored[0].score:.1f}" if scored else ""
    return NodeResult({"opportunities": opps}, status, f"Scored {len(scored)}/{len(opps)} opportunities{top}")


def synthesize_recommendations(ctx: RunContext, state: RunState) -> NodeResult:
    recs, errors, status = synthesis.synthesize(
        ctx, "synthesize_recommendations", state["brand"], state.get("opportunities") or [],
        state.get("observations") or [], state.get("queries") or [],
    )
    repo.save_recommendations(ctx.db, ctx.run_id, recs)
    llm = sum(1 for r in recs if r.generated_by == "llm")
    return NodeResult({"recommendations": recs, "errors": errors}, status,
                      f"{len(recs)} recommendations ({llm} LLM, {len(recs) - llm} rule-based)")


def assemble_report(ctx: RunContext, state: RunState) -> NodeResult:
    brand = state["brand"]
    observations = state.get("observations") or []
    opps = state.get("opportunities") or []
    recs = state.get("recommendations") or []
    errors = state.get("errors") or []
    plan = state.get("plan")
    has_evidence = any(o.status == "measured" for o in observations)
    visibility = report.build_visibility(brand, observations, len(plan.queries) if plan else 0) if has_evidence else None
    summary = (report.build_summary(brand, visibility, opps, recs, errors) if visibility is not None else {
        "headline": "No visibility evidence could be collected in this run.",
        "key_findings": [e["message"] for e in errors[:5]],
        "markdown": "## No visibility evidence could be collected\n" + "\n".join(f"- {e['message']}" for e in errors[:5]),
        "generated_by": "deterministic",
    })
    if ctx.settings.data_mode == "sandbox":
        from app.integrations.dataforseo.sandbox import SANDBOX_NOTICE

        summary = {**summary, "headline": "[SANDBOX DATA] " + summary["headline"],
                   "key_findings": [SANDBOX_NOTICE] + list(summary["key_findings"]),
                   "markdown": f"> **{SANDBOX_NOTICE}**\n\n" + summary["markdown"]}
    payload = {
        "summary": summary,
        "data_mode": ctx.settings.data_mode,
        "visibility": visibility,
        "scoring": report.scoring_meta(),
        "plan": {
            "discovery_source": state["discovery_plan"].plan_source if state.get("discovery_plan") else None,
            "measurement_source": plan.plan_source if plan else None,
            "notes": (state["discovery_plan"].notes if state.get("discovery_plan") else []) + (plan.notes if plan else []),
        },
    }
    headline = report.build_headline(visibility, opps, recs)
    repo.save_report(ctx.db, ctx.run_id, payload, summary["markdown"], headline)
    return NodeResult({"report": payload}, "success", summary["headline"][:280])


# ------------------------------------------------------------------------------ routing (conditional edges)
def route_after_prepare(state: RunState) -> str:
    if state.get("abort"):
        return "abort"
    return "remeasure" if state.get("mode") == "remeasure" else "discover"


def route_after_discovery(state: RunState) -> str:
    payload: RawPayload | None = state.get("discovery_payload")
    if payload is None or payload.error is not None or not payload.result:
        return "fallback"
    return "extract"


def route_after_candidate_extraction(state: RunState) -> str:
    cands = state.get("candidates") or []
    return "plan" if cands else "fallback"


def route_after_observations(state: RunState) -> str:
    observations = state.get("observations") or []
    if any(o.status == "measured" for o in observations):
        return "analyze"
    return "report"


__all__ = ["NodeResult", "NODE_ORDER", "NODE_LABELS", "NODE_AGENTS", "ExternalServiceError", "error_from_exception"]
