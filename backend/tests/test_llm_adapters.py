"""LLM adapters: request shape, tool-call parsing, repair-turn messages and error mapping (no network)."""

from __future__ import annotations

import json

import httpx
import openai
import pytest

from app.integrations.llm.base import GuardedCaller, ToolFeedback
from app.integrations.llm.openai_client import OpenAILLM
from app.resilience.circuit_breaker import CircuitBreaker
from app.resilience.errors import ExternalServiceError
from app.resilience.retry import RetryPolicy
from app.tools.schemas import MEASUREMENT_TOOLS


def _completion(tool_calls: list[dict]) -> dict:
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "gpt-5-mini",
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": None, "refusal": None, "annotations": [],
                                 "tool_calls": tool_calls}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def _llm(handler) -> OpenAILLM:  # type: ignore[no-untyped-def]
    llm = OpenAILLM("sk-test-key-000000", "gpt-5-mini", timeout=5, max_tokens=1000,
                    guard=GuardedCaller("openai", RetryPolicy(3, 0, 0), CircuitBreaker("llm", 5, 60), sleep=lambda _s: None))
    llm.sdk = openai.OpenAI(api_key="sk-test-key-000000", max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return llm


def test_openai_tool_calls_and_repair_turn_messages_are_request_valid() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        call = {"id": f"call_{len(requests)}", "type": "function",
                "function": {"name": "check_google_organic_serp", "arguments": '{"keyword": "crm"'}}
        return httpx.Response(200, json=_completion([call]))

    conv = _llm(handler).conversation(system="sys", user="usr", tools=MEASUREMENT_TOOLS)
    first = conv.ask()
    assert first.tool_calls[0].arguments == '{"keyword": "crm"'  # raw string kept for validation
    assert requests[0]["tool_choice"] == "required"
    assert {t["function"]["name"] for t in requests[0]["tools"]} == {t.name for t in MEASUREMENT_TOOLS}

    conv.reply([ToolFeedback("call_1", False, "Rejected: not valid JSON")], "fix it")
    assistant, tool_msg, user_msg = requests[1]["messages"][-3:]
    assert set(assistant) == {"role", "content", "tool_calls"}  # no response-only fields re-sent
    assert tool_msg == {"role": "tool", "tool_call_id": "call_1", "content": "ERROR: Rejected: not valid JSON"}
    assert user_msg == {"role": "user", "content": "fix it"}


@pytest.mark.parametrize(("status", "code", "attempts"), [(429, "llm_rate_limited", 3), (500, "llm_unavailable", 1),
                                                          (401, "llm_auth_failed", 1), (400, "llm_invalid_request", 1)])
def test_openai_errors_are_classified_and_only_transient_ones_retried(status: int, code: str, attempts: int) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(status, json={"error": {"message": "x", "type": "x"}})

    with pytest.raises(ExternalServiceError) as exc:
        _llm(handler).conversation(system="s", user="u", tools=MEASUREMENT_TOOLS).ask()
    assert exc.value.code == code
    assert len(seen) == attempts
