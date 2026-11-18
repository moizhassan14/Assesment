"""Anthropic (Claude) adapter using the official ``anthropic`` SDK.

Notes for current models (Claude Opus 5.5 default):
* forced ``tool_choice`` (``any``/``tool``) is rejected, so we use ``auto`` and
  instruct the model to call the tools; outputs are validated anyway;
* sampling parameters (``temperature`` etc.) are not sent;
* the SDK's own retries are disabled (``max_retries=0``) so the shared
  retry/backoff/jitter + circuit-breaker policy applies uniformly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anthropic

from app.integrations.llm.base import GuardedCaller, LLMResponse, RetryObserver, ToolFeedback
from app.resilience.errors import ExternalServiceError
from app.tools.schemas import ToolSpec
from app.tools.validation import RawToolCall

PROVIDER = "anthropic"


def _classify(exc: Exception) -> ExternalServiceError:
    if isinstance(exc, anthropic.RateLimitError):
        retry_after = None
        try:
            retry_after = float(exc.response.headers.get("retry-after", "")) if exc.response is not None else None
        except ValueError:
            retry_after = None
        return ExternalServiceError("llm_rate_limited", "Anthropic API rate limited the request (429).", PROVIDER, 429,
                                    retry_after_seconds=retry_after)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ExternalServiceError("llm_auth_failed", "Anthropic API rejected the API key.", PROVIDER, getattr(exc, "status_code", None))
    if isinstance(exc, anthropic.APITimeoutError):
        return ExternalServiceError("llm_unavailable", "Anthropic API request timed out.", PROVIDER)
    if isinstance(exc, anthropic.APIConnectionError):
        return ExternalServiceError("llm_unavailable", "Network error contacting the Anthropic API.", PROVIDER)
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if status >= 500 or status in (408, 409, 529):
            return ExternalServiceError("llm_unavailable", f"Anthropic API server error ({status}).", PROVIDER, status)
        return ExternalServiceError("llm_invalid_request", f"Anthropic API rejected the request ({status}).", PROVIDER, status)
    return ExternalServiceError("llm_unavailable", f"Unexpected Anthropic client error: {type(exc).__name__}", PROVIDER)


class AnthropicConversation:
    def __init__(self, client: "AnthropicLLM", system: str, user: str, tools: Sequence[ToolSpec], effort: str,
                 on_retry: RetryObserver | None) -> None:
        self._client = client
        self._system = system
        self._tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema()} for t in tools]
        self._effort = effort
        self._on_retry = on_retry
        self._messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        self._last_content: list[Any] | None = None
        self._last_tool_ids: list[str] = []

    def _send(self) -> LLMResponse:
        def call() -> Any:
            try:
                return self._client.sdk.messages.create(
                    model=self._client.model,
                    max_tokens=self._client.max_tokens,
                    system=self._system,
                    messages=self._messages,
                    tools=self._tools,
                    tool_choice={"type": "auto"},
                    output_config={"effort": self._effort},
                )
            except anthropic.AnthropicError as exc:
                raise _classify(exc) from exc

        response, duration_ms, attempts = self._client.guard.run(call, self._on_retry)
        tool_calls: list[RawToolCall] = []
        texts: list[str] = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(RawToolCall(call_id=block.id, name=block.name, arguments=block.input))
            elif block.type == "text":
                texts.append(block.text)
        self._last_content = list(response.content)
        self._last_tool_ids = [c.call_id for c in tool_calls]
        usage = getattr(response, "usage", None)
        return LLMResponse(
            tool_calls=tool_calls,
            text="\n".join(texts),
            stop_reason=response.stop_reason,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            duration_ms=duration_ms,
            attempts=attempts,
        )

    def prime(self, response: LLMResponse) -> None:
        content: list[dict[str, Any]] = [{"type": "text", "text": response.text}] if response.text else []
        content += [{"type": "tool_use", "id": c.call_id, "name": c.name,
                     "input": c.arguments if isinstance(c.arguments, dict) else {}} for c in response.tool_calls]
        self._last_content = content
        self._last_tool_ids = [c.call_id for c in response.tool_calls]

    def ask(self) -> LLMResponse:
        return self._send()

    def reply(self, feedback: Sequence[ToolFeedback], instruction: str) -> LLMResponse:
        if self._last_content is not None:
            self._messages.append({"role": "assistant", "content": self._last_content})
        by_id = {f.call_id: f for f in feedback}
        blocks: list[dict[str, Any]] = []
        for call_id in self._last_tool_ids:
            fb = by_id.get(call_id) or ToolFeedback(call_id, True, "Accepted.")
            blocks.append({"type": "tool_result", "tool_use_id": call_id, "content": fb.message, "is_error": not fb.ok})
        blocks.append({"type": "text", "text": instruction})
        self._messages.append({"role": "user", "content": blocks})
        return self._send()


class AnthropicLLM:
    provider = PROVIDER

    def __init__(self, api_key: str, model: str, *, timeout: float, max_tokens: int, guard: GuardedCaller) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.guard = guard
        self.sdk = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)

    def conversation(self, *, system: str, user: str, tools: Sequence[ToolSpec], effort: str = "low",
                     required_tool: str | None = None, on_retry: RetryObserver | None = None) -> AnthropicConversation:
        if required_tool:
            system = f"{system}\n\nYou must respond by calling the `{required_tool}` tool exactly once."
        return AnthropicConversation(self, system, user, tools, effort, on_retry)
