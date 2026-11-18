from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

import pytest
from pydantic import SecretStr

from app import build_services, create_app
from app.config import RetrySettings, Settings
from tests.fakes import FakeDataForSEO, FakeLLM

BRAND_BODY: dict[str, Any] = {
    "name": "Asana",
    "domain": "https://www.Asana.com/",
    "description": "Work management platform for teams",
    "market": {"location_code": 2840, "language_code": "en"},
    "seed_keywords": ["project management software", "Task management app"],
    "competitors": [{"name": "Trello", "domain": "trello.com"}, {"name": "Monday.com", "domain": "monday.com"}],
}


def make_settings(tmp_path: Any, **overrides: Any) -> Settings:
    base = Settings(
        environment="test",
        log_level="WARNING",
        database_url=f"sqlite:///{os.path.join(str(tmp_path), 'test.db')}",
        dataforseo_login=SecretStr("test-login"),
        dataforseo_password=SecretStr("test-password"),
        retry=RetrySettings(max_attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0),
        provider_cache_ttl_seconds=3600,
        llm_max_calls_total=1000,  # quota behaviour has dedicated tests; fakes never reach a real API
    )
    return replace(base, **overrides)


class Harness:
    def __init__(self, tmp_path: Any, dfs: FakeDataForSEO | None = None, llm: Any = "default", **settings: Any) -> None:
        self.dfs = dfs or FakeDataForSEO()
        self.llm = FakeLLM() if llm == "default" else llm
        self.settings = make_settings(tmp_path, **settings)
        self.services = build_services(
            self.settings, http_client=self.dfs.client(), llm=self.llm, use_configured_llm=False,
            synchronous_runs=True, sleep=lambda _s: None,
        )
        self.app = create_app(self.settings, self.services)
        self.client = self.app.test_client()

    def create_brand(self, **overrides: Any) -> dict[str, Any]:
        body = {**BRAND_BODY, **overrides}
        resp = self.client.post("/api/brands", json=body)
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()["data"]

    def start_run(self, brand_id: str, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        return self.client.post(f"/api/brands/{brand_id}/runs", json=body or {}, headers=headers or {})

    def run(self, run_id: str) -> dict[str, Any]:
        return self.client.get(f"/api/runs/{run_id}").get_json()["data"]

    def report(self, run_id: str) -> dict[str, Any]:
        return self.client.get(f"/api/runs/{run_id}/report").get_json()["data"]

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return self.client.get(f"/api/runs/{run_id}/events?limit=500").get_json()["data"]


@pytest.fixture
def harness(tmp_path):  # type: ignore[no-untyped-def]
    return Harness(tmp_path)


QUESTION = "How does Asana show up in AI answers and search results for 'project management software'?"
