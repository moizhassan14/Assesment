"""REST API validation, run history, idempotency, duplicate protection and comparison."""

from __future__ import annotations

from app.models import AnalysisRun
from tests.conftest import BRAND_BODY, QUESTION, Harness
from tests.fakes import KW_PATH, SERP_PATH


def test_brand_validation_errors_use_the_contract_envelope(harness: Harness) -> None:
    bad = {**BRAND_BODY, "domain": "not a domain", "market": {"location_code": 9999, "language_code": "en"},
           "competitors": [{"name": "X", "domain": "x.com"}, {"name": "Y", "domain": "x.com"}], "extra": 1}
    resp = harness.client.post("/api/brands", json=bad, headers={"X-Request-ID": "test-req-1"})
    assert resp.status_code == 422
    err = resp.get_json()["error"]
    assert err["code"] == "validation_error" and err["request_id"] == "test-req-1"
    fields = {d["field"] for d in err["details"]}
    assert {"domain", "market", "extra"} <= fields
    assert resp.headers["X-Request-ID"] == "test-req-1"


def test_api_error_statuses(harness: Harness) -> None:
    c = harness.client
    assert c.post("/api/brands", data="{bad json", content_type="application/json").status_code == 400
    assert c.post("/api/brands", data="name=x", content_type="text/plain").status_code == 415
    assert c.get("/api/brands/does-not-exist").get_json()["error"]["code"] == "not_found"
    assert c.get("/api/nope").status_code == 404
    assert c.delete("/api/brands").status_code == 405
    assert c.get("/api/brands?limit=0").get_json()["error"]["code"] == "invalid_query_parameter"
    brand = harness.create_brand()
    dup = c.post("/api/brands", json={**BRAND_BODY, "domain": "asana.com"})
    assert dup.status_code == 409 and dup.get_json()["error"]["meta"]["existing_id"] == brand["id"]
    assert brand["domain"] == "asana.com" and brand["seed_keywords"] == ["project management software", "task management app"]
    patch = c.patch(f"/api/brands/{brand['id']}", json={"domain": "other.com"})
    assert patch.status_code == 422
    bad_run = harness.start_run(brand["id"], {"question": "short"})
    assert bad_run.status_code == 422
    assert c.post(f"/api/brands/{brand['id']}/runs", json={}, headers={"Idempotency-Key": "x"}).status_code == 400


def test_health_config_and_metrics(harness: Harness) -> None:
    health = harness.client.get("/api/health").get_json()["data"]
    assert health["status"] == "ok" and health["checks"]["dataforseo_credentials"] == "configured"
    config = harness.client.get("/api/config").get_json()["data"]
    assert config["data_mode"] == "live" and config["limits"]["max_serp_calls_per_run"] == 2
    assert harness.client.get("/api/metrics").status_code == 200
    resp = harness.client.post("/api/client-errors", json={"message": "boom", "kind": "runtime", "request_id": "abc"})
    assert resp.status_code == 202


def test_idempotency_key_replays_and_conflicts(harness: Harness) -> None:
    brand = harness.create_brand()
    headers = {"Idempotency-Key": "click-000000001"}
    first = harness.start_run(brand["id"], {"question": QUESTION}, headers)
    calls_after_first = len(harness.dfs.calls)
    replay = harness.start_run(brand["id"], {"question": QUESTION}, headers)
    assert replay.status_code == 202 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.get_json()["data"]["run_id"] == first.get_json()["data"]["run_id"]
    assert len(harness.dfs.calls) == calls_after_first  # no new provider spend
    conflict = harness.start_run(brand["id"], {"question": QUESTION + " now"}, headers)
    assert conflict.status_code == 409 and conflict.get_json()["error"]["code"] == "idempotency_key_conflict"


def test_identical_run_in_progress_is_rejected(harness: Harness) -> None:
    brand = harness.create_brand()
    first_id = harness.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]
    with harness.services.db.session() as s:  # simulate the first run still executing
        s.get(AnalysisRun, first_id).status = "running"
    dup = harness.start_run(brand["id"], {"question": QUESTION})
    assert dup.status_code == 409
    assert dup.get_json()["error"]["meta"]["existing_run_id"] == first_id


def test_history_is_append_only_and_remeasure_reuses_queries_and_cache(harness: Harness) -> None:
    brand = harness.create_brand()
    first_id = harness.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]
    first_report = harness.report(first_id)
    kw_calls = harness.dfs.count(KW_PATH)
    serp_calls = harness.dfs.count(SERP_PATH)

    # The brand changes afterwards; the old run keeps its own snapshot.
    harness.client.patch(f"/api/brands/{brand['id']}", json={"competitors": [{"name": "ClickUp", "domain": "clickup.com"}]})

    second = harness.start_run(brand["id"], {"mode": "remeasure"})
    assert second.status_code == 202
    second_id = second.get_json()["data"]["run_id"]
    run2 = harness.run(second_id)
    assert run2["status"] in ("success", "partial_success")
    assert run2["mode"] == "remeasure"
    assert harness.dfs.count(KW_PATH) == kw_calls  # no discovery spend on re-measure
    assert harness.dfs.count(SERP_PATH) == serp_calls  # identical SERP requests served from cache
    assert run2["cost"]["provider_cache_hits"] >= 2
    statuses = {n["name"]: n["status"] for n in run2["nodes"]}
    assert statuses["load_tracked_queries"] == "success" and statuses["plan_discovery"] == "skipped"

    history = harness.client.get(f"/api/brands/{brand['id']}/runs").get_json()
    assert history["meta"]["total"] == 2 and [r["id"] for r in history["data"]] == [second_id, first_id]
    old = harness.run(first_id)
    assert [c["domain"] for c in old["brand_snapshot"]["competitors"]] == ["trello.com", "monday.com"]
    assert harness.report(first_id)["opportunities"] == first_report["opportunities"]

    comparison = harness.client.get(f"/api/brands/{brand['id']}/compare").get_json()["data"]
    assert comparison["base_run"]["id"] == first_id and comparison["target_run"]["id"] == second_id
    assert {q["query"] for q in comparison["queries"]} == {o["query"] for o in first_report["opportunities"]}

    overview = harness.client.get("/api/brands").get_json()["data"][0]
    assert overview["latest_run"]["id"] == second_id and overview["stats"]["run_count"] == 2

    csv_resp = harness.client.get(f"/api/runs/{first_id}/report?format=csv")
    assert csv_resp.status_code == 200 and csv_resp.mimetype == "text/csv"
    assert csv_resp.get_data(as_text=True).splitlines()[0].startswith("rank,query,score")


def test_remeasure_without_history_is_a_conflict(harness: Harness) -> None:
    brand = harness.create_brand()
    resp = harness.start_run(brand["id"], {"mode": "remeasure"})
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "nothing_to_remeasure"
