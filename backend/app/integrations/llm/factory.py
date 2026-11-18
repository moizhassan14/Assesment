from __future__ import annotations

from app.config import Settings
from app.integrations.llm.base import GuardedCaller, LLMClient
from app.observability.metrics import MetricsRegistry
from app.resilience.circuit_breaker import CircuitBreaker
from app.resilience.retry import RetryPolicy


def build_llm_client(settings: Settings, metrics: MetricsRegistry) -> LLMClient | None:
    """Return the configured LLM client, or ``None`` when no credentials exist.

    Construction makes NO network request: nothing is sent to the provider until a
    user-initiated analysis run reaches an LLM node and passes the quota checks.
    """
    provider = settings.resolved_llm_provider
    if provider is None or not settings.llm_configured:
        return None
    breaker = CircuitBreaker("llm", settings.circuit_breaker_failure_threshold, settings.circuit_breaker_reset_seconds)
    metrics.register_breaker("llm", breaker)
    # OPENAI_AUTO_RETRY=false (default): exactly one attempt per request. When enabled, only HTTP 429
    # (never billed) is retried — timeouts, network errors and 5xx are ambiguous and never retried.
    attempts = settings.retry.max_attempts if settings.llm_auto_retry else 1
    guard = GuardedCaller(
        provider=provider,
        policy=RetryPolicy(attempts, settings.retry.base_delay_seconds, settings.retry.max_delay_seconds),
        breaker=breaker,
        retry_codes=frozenset({"llm_rate_limited"}) if settings.llm_auto_retry else frozenset(),
    )
    if provider == "anthropic":
        from app.integrations.llm.anthropic_client import AnthropicLLM

        return AnthropicLLM(settings.anthropic_api_key.get_secret_value(), settings.anthropic_model,
                            timeout=settings.llm_timeout_seconds, max_tokens=settings.llm_max_output_tokens, guard=guard)
    from app.integrations.llm.openai_client import OpenAILLM

    return OpenAILLM(settings.openai_api_key.get_secret_value(), settings.openai_model,
                     timeout=settings.llm_timeout_seconds, max_tokens=settings.llm_max_output_tokens, guard=guard)
