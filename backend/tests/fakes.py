"""Test doubles. Mocked provider responses exist ONLY here, never in the application.

Shapes mirror the DataForSEO v3 documentation examples (trimmed).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.integrations.llm.base import LLMResponse, ToolFeedback
from app.tools.schemas import ToolSpec
from app.tools.validation import RawToolCall

KW_PATH = "/v3/dataforseo_labs/google/keyword_ideas/live"
OVERVIEW_PATH = "/v3/dataforseo_labs/google/keyword_overview/live"
SERP_PATH = "/v3/serp/google/organic/live/advanced"
AI_PATH = "/v3/ai_optimization/chat_gpt/llm_responses/live"


def envelope(result: list[dict[str, Any]] | None, *, cost: float = 0.01, task_status: int = 20000,
             top_status: int = 20000, task_message: str = "Ok.") -> dict[str, Any]:
    return {
        "version": "0.1.20261101", "status_code": top_status, "status_message": "Ok." if top_status == 20000 else "Error.",
        "time": "0.5 sec.", "cost": cost, "tasks_count": 1, "tasks_error": 0 if task_status == 20000 else 1,
        "tasks": [{"id": "11181405-0000-0000-0000-000000000001", "status_code": task_status, "status_message": task_message,
                   "time": "0.4 sec.", "cost": cost, "result_count": 1 if result else 0, "path": ["v3"], "data": {},
                   "result": result}],
    }


def keyword_ideas_result() -> list[dict[str, Any]]:
    def item(kw: str, vol: int | None, comp: float, level: str, kd: int, intent: str) -> dict[str, Any]:
        return {"se_type": "google", "keyword": kw,
                "keyword_info": {"search_volume": vol, "competition": comp, "competition_level": level, "cpc": 12.5},
                "keyword_properties": {"keyword_difficulty": kd}, "search_intent_info": {"main_intent": intent}}

    return [{"se_type": "google", "seed_keywords": ["project management software"], "total_count": 4, "items_count": 4,
             "items": [
                 item("project management software", 40500, 0.62, "HIGH", 71, "commercial"),
                 item("best project management tools", 9900, 0.55, "HIGH", 64, "commercial"),
                 item("asana pricing", 5400, 0.4, "MEDIUM", 30, "navigational"),  # branded → skipped
                 {"se_type": "google", "keyword_info": {"search_volume": 10}},  # malformed (no keyword) → skipped
             ]}]


def serp_result(keyword: str) -> list[dict[str, Any]]:
    return [{"keyword": keyword, "type": "organic", "items_count": 5, "items": [
        {"type": "ai_overview", "rank_group": 1, "markdown": "Popular tools include Trello and Monday.com.",
         "items": [{"type": "ai_overview_element", "text": "Trello is a visual tool.",
                    "references": [{"source": "Zapier", "domain": "zapier.com", "url": "https://zapier.com/blog/best-pm/",
                                    "title": "Best PM software"}]}]},
        {"type": "organic", "rank_group": 1, "rank_absolute": 2, "domain": "www.trello.com", "url": "https://www.trello.com/", "title": "Trello"},
        {"type": "organic", "rank_group": 2, "rank_absolute": 3, "domain": "zapier.com", "url": "https://zapier.com/blog/best-pm/", "title": "Best PM"},
        {"type": "organic", "rank_group": 3, "rank_absolute": 4, "domain": "monday.com", "url": "https://monday.com/", "title": "monday"},
        {"type": "organic", "rank_group": 6, "rank_absolute": 7, "domain": "asana.com", "url": "https://asana.com/uses/pm", "title": "Asana"},
    ]}]


def ai_result(text: str, urls: list[str]) -> list[dict[str, Any]]:
    return [{"model_name": "gpt-4o-mini-2024-07-18", "input_tokens": 900, "output_tokens": 200, "web_search": True,
             "money_spent": 0.02, "datetime": "2026-11-18 09:00:00 +00:00",
             "items": [{"type": "message", "sections": [{"type": "text", "text": text,
                                                         "annotations": [{"title": u, "url": u} for u in urls]}]}],
             "fan_out_queries": [], "search_results": []}]


@dataclass
class FakeDataForSEO:
    """Routes requests by path. ``failures[path]`` is a list of responses served before the success."""

    failures: dict[str, list[Callable[[], httpx.Response]]] = field(default_factory=dict)
    always_fail: dict[str, Callable[[], httpx.Response]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    ai_text: str = "For most teams Trello and Monday.com are strong picks; Asana is also popular."
    ai_urls: list[str] = field(default_factory=lambda: ["https://trello.com/", "https://www.g2.com/categories/pm"])

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content)[0]
        self.calls.append((path, body))
        assert request.headers.get("authorization", "").startswith("Basic ")
        if path in self.always_fail:
            return self.always_fail[path]()
        queue = self.failures.get(path)
        if queue:
            return queue.pop(0)()
        if path == KW_PATH:
            return httpx.Response(200, json=envelope(keyword_ideas_result(), cost=0.0148))
        if path == OVERVIEW_PATH:
            items = [{"keyword": k, "keyword_info": {"search_volume": 720, "competition": 0.3, "competition_level": "LOW", "cpc": 3.1},
                      "keyword_properties": {"keyword_difficulty": 22}} for k in body["keywords"]]
            return httpx.Response(200, json=envelope([{"items": items}], cost=0.0121))
        if path == SERP_PATH:
            return httpx.Response(200, json=envelope(serp_result(body["keyword"]), cost=0.002))
        if path == AI_PATH:
            return httpx.Response(200, json=envelope(ai_result(self.ai_text, self.ai_urls), cost=0.0206))
        return httpx.Response(404, json={"status_code": 40400, "status_message": "not found"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def count(self, path: str) -> int:
        return sum(1 for p, _ in self.calls if p == path)


def http_error(status: int) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(status, json={"status_code": 50000, "status_message": "internal error"})


def task_error(code: int, message: str = "error") -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(200, json=envelope(None, cost=0.0, task_status=code, task_message=message))


# --------------------------------------------------------------------------- LLM
Script = Callable[[str, list[ToolFeedback] | None, int], list[RawToolCall]]


class FakeConversation:
    def __init__(self, llm: "FakeLLM", kind: str, user: str) -> None:
        self.llm = llm
        self.kind = kind
        self.user = user
        self.turn = 0

    def _respond(self, feedback: list[ToolFeedback] | None) -> LLMResponse:
        script = self.llm.scripts[self.kind]
        calls = script(self.user, feedback, self.turn)
        self.turn += 1
        self.llm.transcript.append((self.kind, self.turn, [c.name for c in calls], feedback))
        return LLMResponse(tool_calls=calls, text="", stop_reason="tool_use", input_tokens=500, output_tokens=120,
                           duration_ms=5, attempts=1)

    def ask(self) -> LLMResponse:
        return self._respond(None)

    def reply(self, feedback: Sequence[ToolFeedback], instruction: str) -> LLMResponse:
        return self._respond(list(feedback))


class FakeLLM:
    provider = "fake"
    model = "fake-model"

    def __init__(self, scripts: dict[str, Script] | None = None) -> None:
        self.scripts = {**default_scripts(), **(scripts or {})}
        self.transcript: list[Any] = []

    def conversation(self, *, system: str, user: str, tools: Sequence[ToolSpec], effort: str = "low",
                     required_tool: str | None = None, on_retry: Any = None) -> FakeConversation:
        names = {t.name for t in tools}
        kind = "discovery" if "discover_keyword_ideas" in names else "synthesis" if "submit_recommendations" in names else "measurement"
        return FakeConversation(self, kind, user)


def discovery_ok(_user: str, _fb: Any, _turn: int) -> list[RawToolCall]:
    return [RawToolCall("call_d1", "discover_keyword_ideas",
                        {"seed_keywords": ["project management software"], "location_code": 2840, "language_code": "en", "limit": 20})]


def measurement_ok(_user: str, _fb: Any, _turn: int) -> list[RawToolCall]:
    calls = []
    for i, q in enumerate(["project management software", "best project management tools"]):
        calls.append(RawToolCall(f"s{i}", "check_google_organic_serp",
                                 {"keyword": q, "location_code": 2840, "language_code": "en", "depth": 10}))
        calls.append(RawToolCall(f"a{i}", "check_chatgpt_answer",
                                 {"research_query": q, "prompt": f"What is the best {q} for a growing team?", "web_search": True}))
    return calls


def synthesis_ok(user: str, _fb: Any, _turn: int) -> list[RawToolCall]:
    recs = []
    for ref, evidence in re.findall(r"(Q\d+): .*?evidence_refs=\[([^\]]*)\]", user):
        ev = [e.strip().strip("'\"") for e in evidence.split(",") if e.strip()]
        recs.append({
            "opportunity_ref": ref, "content_type": "comparison_page",
            "title": "Asana vs Trello vs Monday.com: Which Fits Growing Teams?",
            "angle": "Neutral comparison with a feature matrix for 10-50 person teams.",
            "gap_explanation": "ChatGPT names competitors and cites trello.com but not asana.com.",
            "recommended_action": "Publish a comparison page and pitch it to the listicles that AI answers cite.",
            "evidence_refs": ev[:3],
        })
    return [RawToolCall("r1", "submit_recommendations", {"recommendations": recs})]


def default_scripts() -> dict[str, Script]:
    return {"discovery": discovery_ok, "measurement": measurement_ok, "synthesis": synthesis_ok}
