"""End-to-end DAG tests through the public API (providers mocked at the HTTP layer)."""

from __future__ import annotations

from tests.conftest import QUESTION, Harness
from tests.fakes import AI_PATH, KW_PATH, SERP_PATH, FakeDataForSEO, http_error, task_error


def test_happy_path_runs_every_stage_and_persists_report(harness: Harness) -> None:
    brand = harness.create_brand()
    resp = harness.start_run(brand["id"], {"question": QUESTION}, headers={"Idempotency-Key": "happy-path-0001"})
    assert resp.status_code == 202
    accepted = resp.get_json()["data"]
    assert accepted["status_url"] == f"/api/runs/{accepted['run_id']}"
    assert resp.headers["Location"] == accepted["status_url"]

    run = harness.run(accepted["run_id"])
    assert run["status"] == "success", run["errors"]
    assert run["progress"]["percent"] == 100
    statuses = {n["name"]: n["status"] for n in run["nodes"]}
    for node in ("prepare_run", "plan_discovery", "retrieve_keyword_ideas", "extract_keyword_candidates", "plan_measurement",
                 "retrieve_serp", "retrieve_ai_answers", "extract_observations", "score_opportunities",
                 "synthesize_recommendations", "assemble_report"):
        assert statuses[node] == "success", (node, statuses[node])
    # Both selected queries came from discovery with metrics, so the metrics branch is skipped (no spend).
    assert statuses["retrieve_keyword_metrics"] == "skipped"
    assert statuses["fallback_candidates"] == "skipped"
    assert statuses["load_tracked_queries"] == "skipped"
    assert run["cost"]["provider_calls"] == 5  # 1 discovery + 2 SERP + 2 ChatGPT
    assert run["cost"]["provider_cost_usd"] > 0
    assert run["cost"]["llm_calls"] == 2  # cost policy: 1 planner call + 1 synthesis call per run

    report = harness.report(run["id"])
    assert report["is_final"] is True
    opps = report["opportunities"]
    assert [o["rank"] for o in opps] == [1, 2]
    assert opps[0]["score"] >= opps[1]["score"]  # sorting matches the calculated score
    assert all(o["confidence"] == "high" and o["coverage"] == 1.0 for o in opps)
    assert len(report["recommendations"]) == 2
    assert all(r["generated_by"] == "llm" for r in report["recommendations"])
    assert all(r["supporting_observations"] for r in report["recommendations"])
    surfaces = {o["surface"] for o in report["observations"]}
    assert surfaces == {"chatgpt", "google_organic", "google_ai_overview"}
    organic = next(o for o in report["observations"] if o["surface"] == "google_organic")
    assert organic["brand"]["rank"] == 6
    chat = next(o for o in report["observations"] if o["surface"] == "chatgpt")
    assert chat["brand"] == {"mentioned": True, "cited": False, "rank": None, "url": None}
    trello = next(c for c in chat["competitors"] if c["domain"] == "trello.com")
    assert trello["cited"] is True
    # branded discovery keyword ("asana pricing") is never a research query
    assert all("asana" not in q["text"] for q in report["queries"])
    assert report["visibility"]["share_of_voice"][0]["is_brand"] is True
    assert report["summary"]["headline"].startswith("Biggest opportunity")

    events = harness.events(run["id"])
    kinds = {e["event"] for e in events}
    assert {"run_started", "node_started", "tool_call_validated", "provider_call", "llm_call", "run_finished"} <= kinds
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)


def test_transient_rate_limit_is_retried_with_backoff_then_succeeds(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dfs = FakeDataForSEO(failures={SERP_PATH: [task_error(40202, "rate limit"), http_error(503)]})
    h = Harness(tmp_path, dfs=dfs)
    brand = h.create_brand()
    run_id = h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]

    run = h.run(run_id)
    assert run["status"] == "success"
    serp_node = next(n for n in run["nodes"] if n["name"] == "retrieve_serp")
    assert serp_node["status"] == "success"
    assert serp_node["retry_count"] == 2
    assert dfs.count(SERP_PATH) == 4  # 2 failed attempts + 2 successful queries
    retries = [e for e in h.events(run_id) if e["event"] == "retry_scheduled"]
    assert [r["data"]["error_code"] for r in retries] == ["provider_rate_limited", "provider_server_error"]


def test_failed_branch_degrades_to_partial_success_and_keeps_other_evidence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dfs = FakeDataForSEO(always_fail={AI_PATH: http_error(500)})
    h = Harness(tmp_path, dfs=dfs)
    brand = h.create_brand()
    run_id = h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]

    run = h.run(run_id)
    assert run["status"] == "partial_success"
    nodes = {n["name"]: n for n in run["nodes"]}
    assert nodes["retrieve_ai_answers"]["status"] == "failed"
    assert nodes["retrieve_serp"]["status"] == "success"
    assert dfs.count(AI_PATH) == 3  # first query: 3 attempts, then the circuit breaker opens (threshold 3)
    codes = {e["code"] for e in run["errors"]}
    assert "provider_server_error" in codes and "circuit_open" in codes

    report = h.report(run_id)
    chat = [o for o in report["observations"] if o["surface"] == "chatgpt"]
    assert chat and all(o["status"] == "failed" for o in chat)
    assert all(o["brand"]["mentioned"] is None for o in chat)  # unknown, not "not visible"
    organic = [o for o in report["observations"] if o["surface"] == "google_organic"]
    assert all(o["status"] == "measured" for o in organic)  # SERP evidence preserved
    for opp in report["opportunities"]:
        assert opp["is_partial"] is True
        assert opp["components"]["ai_gap"]["measured"] is True  # Google AI Overview still measured it
    assert report["recommendations"]


def test_invalid_credentials_fail_fast_without_retry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dfs = FakeDataForSEO(always_fail={KW_PATH: task_error(40100, "not authorized"),
                                      SERP_PATH: task_error(40100, "not authorized"),
                                      AI_PATH: task_error(40100, "not authorized")})
    h = Harness(tmp_path, dfs=dfs)
    brand = h.create_brand()
    run_id = h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]
    run = h.run(run_id)
    assert run["status"] == "failed"
    assert dfs.count(KW_PATH) == 1  # auth errors are not retried
    assert any(e["code"] == "provider_auth_failed" for e in run["errors"])
    nodes = {n["name"]: n["status"] for n in run["nodes"]}
    assert nodes["fallback_candidates"] == "degraded"  # discovery failed → degraded path taken
    assert nodes["score_opportunities"] == "skipped"  # no evidence → straight to report
    report = h.report(run_id)
    assert report["opportunities"] == [] and report["errors"]


def test_missing_credentials_abort_before_any_spend(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    h = Harness(tmp_path, dataforseo_login=SecretStr(""), dataforseo_password=SecretStr(""))
    brand = h.create_brand()
    run_id = h.start_run(brand["id"]).get_json()["data"]["run_id"]
    run = h.run(run_id)
    assert run["status"] == "failed"
    assert h.dfs.calls == [] and h.llm.transcript == []
    assert run["errors"][0]["code"] == "provider_auth_failed"


def test_sandbox_mode_is_explicit_labelled_and_never_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app import build_services, create_app
    from app.config import Settings
    from tests.conftest import BRAND_BODY, make_settings
    from tests.fakes import FakeLLM

    assert Settings().data_mode == "live"  # default is live
    settings = make_settings(tmp_path, dataforseo_mode="sandbox")
    svc = build_services(settings, llm=FakeLLM(), use_configured_llm=False, synchronous_runs=True, sleep=lambda _s: None)
    client = create_app(settings, svc).test_client()
    assert client.get("/api/config").get_json()["data"]["data_mode"] == "sandbox"
    brand = client.post("/api/brands", json=BRAND_BODY).get_json()["data"]
    run_id = client.post(f"/api/brands/{brand['id']}/runs", json={"question": QUESTION}).get_json()["data"]["run_id"]
    run = client.get(f"/api/runs/{run_id}").get_json()["data"]
    # (the scripted fake LLM requests one query that is not a sandbox candidate → validation rejects it → partial)
    assert run["status"] in ("success", "partial_success") and run["data_mode"] == "sandbox"
    assert run["cost"]["provider_cost_usd"] == 0
    report = client.get(f"/api/runs/{run_id}/report").get_json()["data"]
    assert report["data_mode"] == "sandbox" and report["summary"]["headline"].startswith("[SANDBOX DATA]")
    chat = [o for o in report["observations"] if o["surface"] == "chatgpt" and o["status"] == "measured"]
    assert chat and all(o["answer_excerpt"].startswith("[SANDBOX]") for o in chat)
    assert report["opportunities"] and all(o["score"] is not None for o in report["opportunities"])
