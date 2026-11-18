"""Tool-call argument validation and planner repair / fallback behaviour."""

from __future__ import annotations

import pytest

from app.tools.schemas import ALL_TOOLS, MEASUREMENT_TOOLS
from app.tools.validation import RawToolCall, RejectedToolCall, ToolContext, ValidatedToolCall, validate_tool_call
from tests.conftest import QUESTION, Harness

CTX = ToolContext(location_code=2840, language_code="en", brand_name="Asana", brand_domain="asana.com",
                  allowed_queries={"project management software"})


def _check(name: str, args: object) -> ValidatedToolCall | RejectedToolCall:
    return validate_tool_call(RawToolCall("c1", name, args), ALL_TOOLS, CTX)


def test_valid_call_is_accepted_and_normalized() -> None:
    out = _check("check_google_organic_serp",
                 {"keyword": "  Project   Management Software ", "location_code": 2840, "language_code": "en"})
    assert isinstance(out, ValidatedToolCall)
    assert out.args.keyword == "project management software"  # type: ignore[attr-defined]
    assert out.args.depth == 10  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("name", "args", "fragment"),
    [
        ("call_dataforseo", {"anything": 1}, "unsupported tool"),
        ("check_google_organic_serp", '{"keyword": "x", "location_code": ', "not valid JSON"),
        ("check_google_organic_serp", ["not", "an", "object"], "must be a JSON object"),
        ("check_google_organic_serp", {"location_code": 2840, "language_code": "en"}, "keyword: Field required"),
        ("check_google_organic_serp", {"keyword": "project management software", "location_code": "US", "language_code": "en"},
         "location_code"),
        ("check_google_organic_serp", {"keyword": "project management software", "location_code": 2840, "language_code": "en",
                                       "depth": 100}, "depth"),
        ("check_google_organic_serp", {"keyword": "project management software", "location_code": 2840, "language_code": "en",
                                       "unexpected": True}, "Extra inputs are not permitted"),
        ("check_google_organic_serp", {"keyword": "site:asana.com project", "location_code": 2840, "language_code": "en"},
         "search operators"),
        ("check_google_organic_serp", {"keyword": "project management software", "location_code": 2826, "language_code": "en"},
         "brand's market"),
        ("check_google_organic_serp", {"keyword": "crm software", "location_code": 2840, "language_code": "en"},
         "not one of the candidate queries"),
        ("check_chatgpt_answer", {"research_query": "project management software", "prompt": "Is Asana the best tool for teams?"},
         "must not mention the analysed brand"),
        ("check_chatgpt_answer", {"research_query": "project management software", "prompt": "What is the best PM tool?",
                                  "web_search": False, "country_iso_code": "US"}, "requires web_search"),
        ("discover_keyword_ideas", {"seed_keywords": [], "location_code": 2840, "language_code": "en"}, "seed_keywords"),
        ("discover_keyword_ideas", {"seed_keywords": ["asana alternatives"], "location_code": 2840, "language_code": "en"},
         "names the brand"),
    ],
)
def test_invalid_tool_calls_are_rejected_with_reasons(name: str, args: object, fragment: str) -> None:
    out = _check(name, args)
    assert isinstance(out, RejectedToolCall), out
    assert any(fragment in r for r in out.reasons), out.reasons


def test_tool_schemas_are_strict_json_schema() -> None:
    for spec in MEASUREMENT_TOOLS:
        schema = spec.input_schema()
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "$defs" not in schema and "required" in schema


def test_planner_repairs_malformed_arguments_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tests.fakes import FakeLLM, RawToolCall as RTC, measurement_ok

    def measurement(user, feedback, turn):  # type: ignore[no-untyped-def]
        if turn == 0:  # malformed JSON string + missing required field + brand-biased prompt
            return [
                RTC("bad1", "check_google_organic_serp", '{"keyword": "project management software", '),
                RTC("bad2", "check_chatgpt_answer", {"research_query": "project management software",
                                                    "prompt": "Why is Asana the best project management software?"}),
            ]
        assert feedback is not None and all(not f.ok for f in feedback)
        assert any("not valid JSON" in f.message for f in feedback)
        return measurement_ok(user, feedback, turn)

    h = Harness(tmp_path, llm=FakeLLM({"measurement": measurement}), llm_repair_attempts=1, llm_max_calls_per_run=3)
    brand = h.create_brand()
    run_id = h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]
    run = h.run(run_id)
    assert run["status"] == "success", run["errors"]  # the repaired plan is fully valid
    assert not any(e["code"] == "llm_tool_call_rejected" for e in run["errors"])
    plan_node = next(n for n in run["nodes"] if n["name"] == "plan_measurement")
    assert plan_node["status"] == "success"
    assert "llm_repaired" in plan_node["message"]
    rejected = [e for e in h.events(run_id) if e["event"] == "tool_call_rejected"]
    assert len(rejected) == 2
    assert len([t for t in h.llm.transcript if t[0] == "measurement"]) == 2


def test_planner_falls_back_to_rules_when_llm_never_produces_valid_calls(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tests.fakes import FakeLLM, RawToolCall as RTC

    def garbage(user, feedback, turn):  # type: ignore[no-untyped-def]
        return [RTC(f"g{turn}", "check_google_organic_serp", {"keyword": 12345})]

    h = Harness(tmp_path, llm=FakeLLM({"measurement": garbage}))
    brand = h.create_brand()
    run_id = h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]
    run = h.run(run_id)
    assert run["status"] == "partial_success"
    plan_node = next(n for n in run["nodes"] if n["name"] == "plan_measurement")
    assert plan_node["status"] == "degraded"
    assert any(e["code"] == "llm_tool_call_rejected" for e in run["errors"])
    report = h.report(run_id)
    assert len(report["opportunities"]) == 2  # deterministic plan still measured real data
    assert len([t for t in h.llm.transcript if t[0] == "measurement"]) == 1  # repairs disabled by default (cost policy)


def test_invalid_recommendation_output_never_reaches_the_user(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tests.fakes import FakeLLM, RawToolCall as RTC

    def bad_recs(user, feedback, turn):  # type: ignore[no-untyped-def]
        return [RTC(f"r{turn}", "submit_recommendations", {"recommendations": [{
            "opportunity_ref": "Q9", "content_type": "viral_tiktok", "title": "x", "angle": "y",
            "gap_explanation": "See https://invented.example.com/stats", "recommended_action": "z",
            "evidence_refs": ["O99"], "limitations": "none"}]})]

    h = Harness(tmp_path, llm=FakeLLM({"synthesis": bad_recs}))
    brand = h.create_brand()
    run_id = h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]
    report = h.report(run_id)
    recs = report["recommendations"]
    assert recs and all(r["generated_by"] == "rule_based_fallback" for r in recs)
    assert all("invented.example.com" not in (r["gap_explanation"] + r["recommended_action"]) for r in recs)
    assert any(e["code"] == "llm_invalid_output" for e in report["errors"])
