"""Automated browser-test server, never imported by the application or production image.

Runs the real Flask API and DAG with backend-owned test doubles at external
provider boundaries. Use only for RUN_FLASK_TEST_E2E=1, without real credentials.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "/backend")

import httpx
from pydantic import SecretStr
from app import build_services, create_app
from app.config import Settings, RetrySettings
from tests.fakes import FakeDataForSEO, FakeLLM, AI_PATH, task_error


class BrowserTestProvider(FakeDataForSEO):
    def handler(self, request: httpx.Request) -> httpx.Response:
        time.sleep(0.5)  # Test-only delay makes background polling observable.
        return super().handler(request)


provider = BrowserTestProvider()
if os.environ.get("TEST_PARTIAL") == "1":
    provider.always_fail[AI_PATH] = task_error(40202, "Simulated provider rate limit")
settings = Settings(
    environment="test",
    log_level="WARNING",
    database_url="sqlite:////data/browser-tests.db",
    dataforseo_login=SecretStr("automated-test-login"),
    dataforseo_password=SecretStr("automated-test-password"),
    retry=RetrySettings(max_attempts=2, base_delay_seconds=0, max_delay_seconds=0),
)
services = build_services(settings, http_client=provider.client(), llm=FakeLLM(),
                          use_configured_llm=False, synchronous_runs=False,
                          sleep=lambda seconds: None)
app = create_app(settings, services)
app.run(host="0.0.0.0", port=5000, threaded=True)
