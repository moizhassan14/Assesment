"""Application configuration, loaded exclusively from environment variables.

Secrets (provider credentials) are held in ``SecretStr`` so they never appear in
reprs, logs or error messages by accident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from pydantic import SecretStr

LLMProviderName = Literal["anthropic", "openai"]


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value.strip() if value is not None else default


def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"Environment variable {name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"Environment variable {name} must be <= {maximum}")
    return value


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a number") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"Environment variable {name} must be >= {minimum}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RetrySettings:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 20.0


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings. Build with :meth:`from_env`."""

    app_version: str = "1.0.0"
    environment: str = "production"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/ai_visibility.db"

    # Providers
    dataforseo_login: SecretStr = field(default_factory=lambda: SecretStr(""))
    dataforseo_password: SecretStr = field(default_factory=lambda: SecretStr(""))
    dataforseo_base_url: str = "https://api.dataforseo.com"
    dataforseo_mode: str = "live"  # "live" (default) | "sandbox" (explicit opt-in, synthetic data, clearly labelled)
    llm_provider: LLMProviderName | None = None
    anthropic_api_key: SecretStr = field(default_factory=lambda: SecretStr(""))
    anthropic_model: str = "claude-opus-5-5"
    openai_api_key: SecretStr = field(default_factory=lambda: SecretStr(""))
    openai_model: str = "gpt-4.1-mini"
    # LLM spend governance (applies to whichever provider is active)
    llm_max_output_tokens: int = 400
    llm_max_input_tokens: int = 2000
    llm_max_calls_per_run: int = 2
    llm_max_calls_total: int = 4
    llm_auto_retry: bool = False
    llm_cache_ttl_seconds: int = 30 * 24 * 3600
    llm_plan_discovery: bool = False
    llm_repair_attempts: int = 0

    # AI answer measurement (DataForSEO LLM Responses → ChatGPT)
    ai_answer_model: str = "gpt-4o-mini"
    ai_answer_web_search: bool = True
    ai_answer_max_output_tokens: int = 700

    # Timeouts and retries
    dataforseo_timeout_seconds: float = 60.0
    dataforseo_ai_timeout_seconds: float = 130.0
    llm_timeout_seconds: float = 120.0
    retry: RetrySettings = field(default_factory=RetrySettings)
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_reset_seconds: float = 60.0

    # Cost protection
    max_research_queries_per_run: int = 2
    max_ai_visibility_calls_per_run: int = 2
    max_serp_calls_per_run: int = 2
    max_keyword_discovery_calls_per_run: int = 1
    max_keyword_metrics_calls_per_run: int = 1
    max_run_cost_usd: float = 0.25
    keyword_ideas_limit: int = 20
    serp_depth: int = 10
    provider_cache_ttl_seconds: int = 6 * 3600

    # Execution
    max_concurrent_runs: int = 2
    max_queued_runs: int = 4

    @property
    def data_mode(self) -> str:
        return "sandbox" if self.dataforseo_mode == "sandbox" else "live"

    @property
    def dataforseo_configured(self) -> bool:
        if self.data_mode == "sandbox":
            return True  # sandbox transport needs no provider credentials
        return bool(self.dataforseo_login.get_secret_value() and self.dataforseo_password.get_secret_value())

    @property
    def resolved_llm_provider(self) -> LLMProviderName | None:
        if self.llm_provider:
            return self.llm_provider
        if self.anthropic_api_key.get_secret_value():
            return "anthropic"
        if self.openai_api_key.get_secret_value():
            return "openai"
        return None

    @property
    def llm_configured(self) -> bool:
        provider = self.resolved_llm_provider
        if provider == "anthropic":
            return bool(self.anthropic_api_key.get_secret_value())
        if provider == "openai":
            return bool(self.openai_api_key.get_secret_value())
        return False

    @property
    def llm_model(self) -> str | None:
        provider = self.resolved_llm_provider
        if provider == "anthropic":
            return self.anthropic_model
        if provider == "openai":
            return self.openai_model
        return None

    @classmethod
    def from_env(cls) -> "Settings":
        provider_raw = _env_str("LLM_PROVIDER").lower() or None
        if _env_str("DATAFORSEO_MODE", "live").lower() not in ("live", "sandbox"):
            raise ValueError("DATAFORSEO_MODE must be 'live' or 'sandbox'")
        if provider_raw not in (None, "anthropic", "openai"):
            raise ValueError("LLM_PROVIDER must be 'anthropic' or 'openai'")
        return cls(
            app_version=_env_str("APP_VERSION", "1.0.0"),
            environment=_env_str("APP_ENV", "production"),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
            database_url=_env_str("DATABASE_URL", "sqlite:///./data/ai_visibility.db"),
            dataforseo_login=SecretStr(_env_str("DATAFORSEO_LOGIN")),
            dataforseo_password=SecretStr(_env_str("DATAFORSEO_PASSWORD")),
            dataforseo_base_url=_env_str("DATAFORSEO_BASE_URL", "https://api.dataforseo.com").rstrip("/"),
            dataforseo_mode=_env_str("DATAFORSEO_MODE", "live").lower(),
            llm_provider=provider_raw,  # type: ignore[arg-type]
            anthropic_api_key=SecretStr(_env_str("ANTHROPIC_API_KEY")),
            anthropic_model=_env_str("ANTHROPIC_MODEL", "claude-opus-5-5"),
            openai_api_key=SecretStr(_env_str("OPENAI_API_KEY")),
            openai_model=_env_str("OPENAI_MODEL", "gpt-4.1-mini"),
            llm_max_output_tokens=_env_int("OPENAI_MAX_OUTPUT_TOKENS", 400, 64, 16000),
            llm_max_input_tokens=_env_int("OPENAI_MAX_INPUT_TOKENS", 2000, 500, 100000),
            llm_max_calls_per_run=_env_int("OPENAI_MAX_CALLS_PER_RUN", 2, 0, 10),
            llm_max_calls_total=_env_int("OPENAI_MAX_CALLS_TOTAL", 4, 0, 1_000_000),
            llm_auto_retry=_env_bool("OPENAI_AUTO_RETRY", False),
            llm_cache_ttl_seconds=_env_int("LLM_CACHE_TTL_SECONDS", 30 * 24 * 3600, 0),
            llm_plan_discovery=_env_bool("LLM_PLAN_DISCOVERY", False),
            llm_repair_attempts=_env_int("LLM_REPAIR_ATTEMPTS", 0, 0, 3),
            ai_answer_model=_env_str("AI_ANSWER_MODEL", "gpt-4o-mini"),
            ai_answer_web_search=_env_bool("AI_ANSWER_WEB_SEARCH", True),
            ai_answer_max_output_tokens=_env_int("AI_ANSWER_MAX_OUTPUT_TOKENS", 700, 16, 4096),
            dataforseo_timeout_seconds=_env_float("DATAFORSEO_TIMEOUT_SECONDS", 60.0, 1.0),
            dataforseo_ai_timeout_seconds=_env_float("DATAFORSEO_AI_TIMEOUT_SECONDS", 130.0, 1.0),
            llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 120.0, 1.0),
            retry=RetrySettings(
                max_attempts=_env_int("RETRY_MAX_ATTEMPTS", 3, 1, 6),
                base_delay_seconds=_env_float("RETRY_BASE_DELAY_SECONDS", 1.0, 0.0),
                max_delay_seconds=_env_float("RETRY_MAX_DELAY_SECONDS", 20.0, 0.0),
            ),
            circuit_breaker_failure_threshold=_env_int("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3, 1),
            circuit_breaker_reset_seconds=_env_float("CIRCUIT_BREAKER_RESET_SECONDS", 60.0, 1.0),
            max_research_queries_per_run=_env_int("MAX_RESEARCH_QUERIES_PER_RUN", 2, 1, 10),
            max_ai_visibility_calls_per_run=_env_int("MAX_AI_VISIBILITY_CALLS_PER_RUN", 2, 0, 10),
            max_serp_calls_per_run=_env_int("MAX_SERP_CALLS_PER_RUN", 2, 0, 10),
            max_keyword_discovery_calls_per_run=_env_int("MAX_KEYWORD_DISCOVERY_CALLS_PER_RUN", 1, 0, 3),
            max_keyword_metrics_calls_per_run=_env_int("MAX_KEYWORD_METRICS_CALLS_PER_RUN", 1, 0, 3),
            max_run_cost_usd=_env_float("MAX_RUN_COST_USD", 0.25, 0.0),
            keyword_ideas_limit=_env_int("KEYWORD_IDEAS_LIMIT", 20, 5, 50),
            serp_depth=_env_int("SERP_DEPTH", 10, 10, 20),
            provider_cache_ttl_seconds=_env_int("PROVIDER_CACHE_TTL_SECONDS", 6 * 3600, 0),
            max_concurrent_runs=_env_int("MAX_CONCURRENT_RUNS", 2, 1, 8),
            max_queued_runs=_env_int("MAX_QUEUED_RUNS", 4, 0, 50),
        )
