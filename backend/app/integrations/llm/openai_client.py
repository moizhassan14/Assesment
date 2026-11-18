"""OpenAI adapter (Chat Completions function calling) using the official ``openai`` SDK."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import openai

from app.integrations.llm.base import GuardedCaller, LLMResponse, RetryObserver, ToolFeedback
from app.resilience.errors import ExternalServiceError
from app.tools.schemas import ToolSpec
from app.tools.validation import RawToolCall

PROVIDER = "openai"


def _classify(exc: Exception) -> ExternalServiceError:
    if isinstance(exc, openai.RateLimitError):
        return ExternalServiceError("llm_rate_limited", "OpenAI API rate limited the request (429).", PROVIDER, 429)
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ExternalServiceError("llm_auth_failed", "OpenAI API rejected the API key.", PROVIDER, getattr(exc, "status_code", None))
    if isinstance(exc, openai.APITimeoutError):
        return ExternalServiceError("llm_unavailable", "OpenAI API request timed out.", PROVIDER)
    if isinstance(exc, openai.APIConnectionError):
        return ExternalServiceError("llm_unavailable", "Network error contacting the OpenAI API.", PROVIDER)
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status >= 500 or status in (408, 409):
            return ExternalServiceError("llm_unavailable", f"OpenAI API server error ({status}).", PROVIDER, status)
        return ExternalServiceError("llm_invalid_request", f"OpenAI API rejected the request ({status}).", PROVIDER, status)
    return ExternalServiceError("llm_unavailable", f"Unexpected OpenAI client error: {type(exc).__name__}", PROVIDER)


class OpenAIConversation:
    def __init__(self, client: "OpenAILLM", system: str, user: str, tools: Sequence[ToolSpec],
                 required_tool: str | None, on_retry: RetryObserver | None) -> None:
        self._client = client
        self._tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema()}}
            for t in tools
        ]
        self._tool_choice: Any = {"type": "function", "function": {"name": required_tool}} if required_tool else "required"
        self._on_retry = on_retry
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        self._last_message: dict[str, Any] | None = None
        self._last_tool_ids: list[str] = []

    def _send(self) -> LLMResponse:
        def call() -> Any:
            try:
                return self._client.sdk.chat.completions.create(
                    model=self._client.model,
                    messages=self._messages,
                    tools=self._tools,
                    tool_choice=self._tool_choice,
                    max_completion_tokens=self._client.max_tokens,
                )
            except openai.OpenAIError as exc:
                raise _classify(exc) from exc

        response, duration_ms, attempts = self._client.guard.run(call, self._on_retry)
        choice = response.choices[0]
        message = choice.message
        calls = [
            RawToolCall(call_id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in (message.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        # Rebuild the assistant turn from request-valid fields only (response objects carry extra
        # fields such as `annotations`/`refusal` that are not accepted as input on the next turn).
        self._last_message = {
            "role": "assistant",
            "content": message.content or "",
            **({"tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in (message.tool_calls or []) if getattr(tc, "function", None) is not None
            ]} if message.tool_calls else {}),
        }
        self._last_tool_ids = [c.call_id for c in calls]
        usage = getattr(response, "usage", None)
        return LLMResponse(
            tool_calls=calls,
            text=message.content or "",
            stop_reason=choice.finish_reason,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            duration_ms=duration_ms,
            attempts=attempts,
        )

    def prime(self, response: LLMResponse) -> None:
        calls = [
            {"id": c.call_id, "type": "function", "function": {
                "name": c.name, "arguments": c.arguments if isinstance(c.arguments, str) else json.dumps(c.arguments)}}
            for c in response.tool_calls
        ]
        self._last_message = {"role": "assistant", "content": response.text or "", **({"tool_calls": calls} if calls else {})}
        self._last_tool_ids = [c.call_id for c in response.tool_calls]

    def ask(self) -> LLMResponse:
        return self._send()

    def reply(self, feedback: Sequence[ToolFeedback], instruction: str) -> LLMResponse:
        if self._last_message is not None:
            self._messages.append(self._last_message)
        by_id = {f.call_id: f for f in feedback}
        for call_id in self._last_tool_ids:
            fb = by_id.get(call_id) or ToolFeedback(call_id, True, "Accepted.")
            prefix = "" if fb.ok else "ERROR: "
            self._messages.append({"role": "tool", "tool_call_id": call_id, "content": prefix + fb.message})
        self._messages.append({"role": "user", "content": instruction})
        return self._send()


class OpenAILLM:
    provider = PROVIDER

    def __init__(self, api_key: str, model: str, *, timeout: float, max_tokens: int, guard: GuardedCaller) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.guard = guard
        self.sdk = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=0)

    def conversation(self, *, system: str, user: str, tools: Sequence[ToolSpec], effort: str = "low",
                     required_tool: str | None = None, on_retry: RetryObserver | None = None) -> OpenAIConversation:
        return OpenAIConversation(self, system, user, tools, required_tool, on_retry)
