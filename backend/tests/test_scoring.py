"""Opportunity scoring: determinism, documented formula, and missing-data handling."""

from __future__ import annotations

import copy

import pytest

from app.agents.scoring import WEIGHTS, score_all
from app.schemas.domain import (
    CompetitorVisibility,
    EntityVisibility,
    KeywordMetrics,
    NormalizedObservation,
    NormalizedQuery,
)

COMPETITORS = [{"name": "Trello", "domain": "trello.com"}, {"name": "Monday.com", "domain": "monday.com"}]


def q(text: str, volume: int | None) -> NormalizedQuery:
    return NormalizedQuery(text=text, source="keyword_discovery", selected_for_measurement=True,
                           metrics=KeywordMetrics(search_volume=volume, source="test"))


def obs(query: str, surface: str, status: str = "measured", *, mentioned=False, cited=False, rank=None,
        comp_rank=None, comp_mentioned=False) -> NormalizedObservation:
    return NormalizedObservation(
        query_text=query, surface=surface, status=status, endpoint="/test",  # type: ignore[arg-type]
        brand=EntityVisibility(mentioned=mentioned if status == "measured" else None,
                               cited=cited if status == "measured" else None, rank=rank),
        competitors=[CompetitorVisibility(name="Trello", domain="trello.com", mentioned=comp_mentioned, cited=False, rank=comp_rank),
                     CompetitorVisibility(name="Monday.com", domain="monday.com", mentioned=False, cited=False, rank=None)],
    )


def test_worked_example_matches_documented_formula() -> None:
    queries = [q("a", 40500), q("b", 9900)]
    observations = [
        obs("a", "chatgpt", mentioned=True, comp_mentioned=True),          # named, not cited → presence 0.5
        obs("a", "google_organic", rank=6, comp_rank=1),                   # (6-3)/(10-2) = 0.375
        obs("a", "google_ai_overview", status="not_present"),              # not shown → ignored
        obs("b", "chatgpt"),                                               # absent → gap 1.0
        obs("b", "google_organic", rank=None),                             # not ranked → gap 1.0
        obs("b", "google_ai_overview", status="not_present"),
    ]
    opps = score_all(queries, observations, COMPETITORS)
    a = next(o for o in opps if o.query_text == "a")
    # demand 1.0, ai_gap 0.5, organic 0.375, competitor 0.5 (1 of 2)
    expected = 100 * (0.30 * 1.0 + 0.35 * 0.5 + 0.20 * 0.375 + 0.15 * 0.5) / sum(WEIGHTS.values())
    assert a.score == pytest.approx(round(expected, 1))
    assert a.confidence == "high" and a.coverage == 1.0 and not a.is_partial
    b = next(o for o in opps if o.query_text == "b")
    import math
    demand_b = math.log10(1 + 9900) / math.log10(1 + 40500)
    expected_b = 100 * (0.30 * demand_b + 0.35 * 1.0 + 0.20 * 1.0 + 0.15 * 0.0)
    assert b.score == pytest.approx(round(expected_b, 1))
    assert [o.query_text for o in opps] == sorted([o.query_text for o in opps], key=lambda t: -next(x.score for x in opps if x.query_text == t))
    assert [o.rank for o in opps] == [1, 2]


def test_scoring_is_deterministic() -> None:
    queries = [q("a", 1000), q("b", 1000), q("c", None)]
    observations = [obs(x, "chatgpt") for x in "abc"] + [obs(x, "google_organic", rank=12) for x in "abc"]
    first = score_all(copy.deepcopy(queries), copy.deepcopy(observations), COMPETITORS)
    for _ in range(5):
        again = score_all(copy.deepcopy(queries), copy.deepcopy(observations), COMPETITORS)
        assert [(o.query_text, o.score, o.rank) for o in again] == [(o.query_text, o.score, o.rank) for o in first]
    # equal scores → tie broken by volume then text, so 'a' precedes 'b'
    assert [o.query_text for o in first][:2] == ["a", "b"]


def test_missing_data_is_not_treated_as_zero_visibility() -> None:
    queries = [q("a", 5000)]
    measured_absent = score_all(queries, [obs("a", "chatgpt"), obs("a", "google_organic", rank=None)], COMPETITORS)[0]
    failed = score_all(queries, [obs("a", "chatgpt", status="failed"), obs("a", "google_organic", rank=None)], COMPETITORS)[0]

    assert measured_absent.components["ai_gap"].measured is True
    assert measured_absent.components["ai_gap"].value == 1.0
    # A failed ChatGPT call is "unknown": the component is excluded, not scored as a maximal gap.
    assert failed.components["ai_gap"].measured is False
    assert failed.components["ai_gap"].value is None
    assert failed.is_partial is True
    assert failed.coverage == pytest.approx(0.65)
    assert failed.confidence == "low"
    assert "Partially measured" in failed.explanation


def test_insufficient_coverage_is_not_scored_or_ranked() -> None:
    queries = [q("a", None), q("b", 100)]
    observations = [obs("a", "chatgpt", status="failed"), obs("a", "google_organic", status="failed"),
                    obs("b", "chatgpt"), obs("b", "google_organic", rank=2)]
    opps = score_all(queries, observations, COMPETITORS)
    a = next(o for o in opps if o.query_text == "a")
    assert a.score is None and a.confidence == "insufficient" and a.rank is None
    assert opps[-1].query_text == "a"  # unscored opportunities sort last


def test_no_competitors_means_competitor_pressure_not_applicable() -> None:
    opp = score_all([q("a", 100)], [obs("a", "chatgpt"), obs("a", "google_organic", rank=1)], [])[0]
    assert "competitor_pressure" not in opp.components
    assert opp.coverage == 1.0
