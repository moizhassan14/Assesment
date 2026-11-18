"""LangGraph wiring for the analysis DAG.

```
START → prepare_run ─┬─(abort)──────────────────────────────────────────────┐
                     ├─(remeasure)→ load_tracked_queries ───────────┐       │
                     └─(discover)→ plan_discovery → retrieve_keyword_ideas  │
                                     ├─(ok)→ extract_keyword_candidates ─┬─(has candidates)─┐
                                     └─(failed)→ fallback_candidates ←───┘(none)            │
                                                   └──────────────→ plan_measurement ←──────┘
                plan_measurement ─┬→ retrieve_keyword_metrics ─┐
                   (parallel)     ├→ retrieve_serp ────────────┼→ extract_observations
                                  └→ retrieve_ai_answers ──────┘     ├─(evidence)→ score_opportunities
                                                                     │              → synthesize_recommendations
                                                                     └─(none)──────────────→ assemble_report → END
```
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph import nodes as n
from app.graph.context import RunContext, run_error
from app.graph.state import RunState
from app.observability.logging import get_logger, log_context

log = get_logger(__name__)

NodeFn = Callable[[RunContext, RunState], n.NodeResult]


def instrument(ctx: RunContext, name: str, fn: NodeFn) -> Callable[[RunState], dict[str, Any]]:
    label = n.NODE_LABELS[name][0]

    def wrapper(state: RunState) -> dict[str, Any]:
        with log_context(run_id=ctx.run_id, correlation_id=ctx.correlation_id, node_name=name):
            ctx.recorder.node_started(name, f"{label}…")
            started = time.perf_counter()
            try:
                result = fn(ctx, state)
            except Exception as exc:  # noqa: BLE001 - a node bug must not crash the whole run
                log.exception("node_crashed", extra={"node_name": name})
                err = run_error(name, "internal_error", f"Unexpected error in {name}: {type(exc).__name__}")
                result = n.NodeResult({"errors": [err]}, "failed", f"{label} failed unexpectedly", err)
            duration_ms = int((time.perf_counter() - started) * 1000)
            percent = ctx.mark_done(name)
            ctx.recorder.node_finished(name, result.status, result.message or label, duration_ms, result.error, percent)
            ctx.metrics.node_finished(name, result.status, duration_ms)
            update = dict(result.update)
            update["completed_nodes"] = [name]
            if result.status in ("degraded", "failed"):
                update["degraded_nodes"] = list(update.get("degraded_nodes", [])) + [name]
            return update

    return wrapper


def build_graph(ctx: RunContext):  # type: ignore[no-untyped-def]
    g: StateGraph = StateGraph(RunState)
    for name in n.NODE_ORDER:
        g.add_node(name, instrument(ctx, name, getattr(n, name)))

    g.add_edge(START, "prepare_run")
    g.add_conditional_edges(
        "prepare_run", n.route_after_prepare,
        {"abort": "assemble_report", "remeasure": "load_tracked_queries", "discover": "plan_discovery"},
    )
    g.add_edge("plan_discovery", "retrieve_keyword_ideas")
    g.add_conditional_edges(
        "retrieve_keyword_ideas", n.route_after_discovery,
        {"extract": "extract_keyword_candidates", "fallback": "fallback_candidates"},
    )
    g.add_conditional_edges(
        "extract_keyword_candidates", n.route_after_candidate_extraction,
        {"plan": "plan_measurement", "fallback": "fallback_candidates"},
    )
    g.add_edge("fallback_candidates", "plan_measurement")
    g.add_edge("load_tracked_queries", "plan_measurement")

    # Fan-out: three independent retrieval branches run in the same super-step (in parallel).
    for branch in ("retrieve_keyword_metrics", "retrieve_serp", "retrieve_ai_answers"):
        g.add_edge("plan_measurement", branch)
    # Fan-in: extraction waits for all three branches, whatever their outcome.
    g.add_edge(["retrieve_keyword_metrics", "retrieve_serp", "retrieve_ai_answers"], "extract_observations")

    g.add_conditional_edges(
        "extract_observations", n.route_after_observations,
        {"analyze": "score_opportunities", "report": "assemble_report"},
    )
    g.add_edge("score_opportunities", "synthesize_recommendations")
    g.add_edge("synthesize_recommendations", "assemble_report")
    g.add_edge("assemble_report", END)
    return g.compile()


def mermaid() -> str:
    """Mermaid source of the compiled DAG (nodes are not executed when drawing)."""
    dummy = RunContext.__new__(RunContext)  # type: ignore[call-arg]
    return build_graph(dummy).get_graph().draw_mermaid()
