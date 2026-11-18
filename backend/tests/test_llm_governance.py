"""LLM spend governance: per-run cap, persistent total quota (survives restart), cache reuse, no retries."""

from __future__ import annotations

import httpx
import openai
import pytest

from app.integrations.llm.base import GuardedCaller
from app.integrations.llm.factory import build_llm_client
from app.integrations.llm.openai_client import OpenAILLM
from app.models import LLMCall
from app.observability.metrics import MetricsRegistry
from app.resilience.circuit_breaker import CircuitBreaker
from app.resilience.errors import ExternalServiceError
from app.resilience.retry import RetryPolicy
from app.tools.schemas import MEASUREMENT_TOOLS
from tests.conftest import QUESTION, Harness, make_settings


def _audit(h: Harness) -> list[LLMCall]:
    with h.services.db.session() as s:
        rows = s.query(LLMCall).order_by(LLMCall.created_at).all()
        for r in rows:
            s.expunge(r)
        return rows


def test_per_run_cap_blocks_extra_calls_and_falls_back_honestly(tmp_path) -> None:  # type: ignore[no-untyped-def]
    h = Harness(tmp_path, llm_max_calls_per_run=1)
    brand = h.create_brand()
    run_id = h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]
    run = h.run(run_id)
    assert run["status"] == "partial_success"
    assert any(e["code"] == "llm_quota_exhausted" for e in run["errors"])
    assert [t[0] for t in h.llm.transcript] == ["measurement"]  # synthesis never reached the provider
    report = h.report(run_id)
    assert report["recommendations"] and all(r["generated_by"] == "rule_based_fallback" for r in report["recommendations"])
    statuses = [a.status for a in _audit(h)]
    assert statuses == ["success", "blocked_quota"]


def test_total_quota_is_persistent_atomic_and_survives_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    h = Harness(tmp_path, llm_max_calls_total=2)
    brand = h.create_brand()
    h.start_run(brand["id"], {"question": QUESTION})
    assert h.services.governor.usage() == {"used": 2, "limit": 2, "remaining": 0, "per_run_limit": 2}
    sent = len(h.llm.transcript)
    second = h.start_run(brand["id"], {"question": QUESTION + " Compare with Trello."}).get_json()["data"]["run_id"]
    run2 = h.run(second)
    assert len(h.llm.transcript) == sent  # new planner request refused before sending
    assert any(e["code"] == "llm_quota_exhausted" for e in run2["errors"])
    assert run2["status"] == "partial_success"  # live evidence still collected and scored
    assert h.services.governor.usage()["used"] == 2

    # "Restart": a brand-new service stack on the same SQLite file sees the same counter.
    restarted = Harness(tmp_path, llm_max_calls_total=2)
    assert restarted.services.governor.usage()["used"] == 2
    third = restarted.start_run(brand["id"], {"question": QUESTION + " And ClickUp?"}).get_json()["data"]["run_id"]
    assert restarted.llm.transcript == []  # nothing sent once the quota is exhausted
    assert restarted.run(third)["status"] == "partial_success"  # live data still measured, LLM steps fell back


def test_identical_llm_requests_are_served_from_cache_without_quota(tmp_path) -> None:  # type: ignore[no-untyped-def]
    h = Harness(tmp_path)
    brand = h.create_brand()
    h.start_run(brand["id"], {"question": QUESTION})
    used = h.services.governor.usage()["used"]
    calls = len(h.llm.transcript)
    # Same question → identical (cached) provider data → identical LLM requests.
    run2 = h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]
    assert h.run(run2)["status"] == "success"
    assert h.services.governor.usage()["used"] == used
    assert len(h.llm.transcript) == calls
    assert [a.status for a in _audit(h)][-2:] == ["cache_hit", "cache_hit"]
    assert any(e["event"] == "llm_cache_hit" for e in h.events(run2))


def test_oversized_prompt_is_refused_before_sending(tmp_path) -> None:  # type: ignore[no-untyped-def]
    h = Harness(tmp_path, llm_max_input_tokens=500)
    brand = h.create_brand()
    run_id = h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"]
    assert h.llm.transcript == []
    assert h.services.governor.usage()["used"] == 0
    assert {a.status for a in _audit(h)} == {"blocked_input_size"}
    assert h.run(run_id)["status"] == "partial_success"


def test_llm_planned_discovery_is_opt_in(tmp_path) -> None:  # type: ignore[no-untyped-def]
    h = Harness(tmp_path, llm_plan_discovery=True, llm_max_calls_per_run=3)
    brand = h.create_brand()
    run = h.run(h.start_run(brand["id"], {"question": QUESTION}).get_json()["data"]["run_id"])
    assert [t[0] for t in h.llm.transcript] == ["discovery", "measurement", "synthesis"]
    assert run["status"] == "success"


def test_default_client_never_retries_chargeable_calls_on_timeout(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    settings = make_settings(tmp_path, openai_api_key=SecretStr("sk-test-0000000000"), llm_provider="openai")
    llm = build_llm_client(settings, MetricsRegistry())
    assert isinstance(llm, OpenAILLM) and llm.guard.policy.max_attempts == 1 and llm.max_tokens == 400
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    llm.sdk = openai.OpenAI(api_key="sk-test-0000000000", max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ExternalServiceError) as exc:
        llm.conversation(system="s", user="u", tools=MEASUREMENT_TOOLS).ask()
    assert exc.value.code == "llm_unavailable" and len(attempts) == 1


def test_even_with_auto_retry_only_rate_limits_are_retried() -> None:
    guard = GuardedCaller("openai", RetryPolicy(3, 0, 0), CircuitBreaker("llm", 9, 60), sleep=lambda _s: None)
    for code, expected in (("llm_rate_limited", 3), ("llm_unavailable", 1)):
        calls = []

        def fn() -> None:
            calls.append(1)
            raise ExternalServiceError(code, "x", "openai")

        with pytest.raises(ExternalServiceError):
            guard.run(fn, None)
        assert len(calls) == expected
