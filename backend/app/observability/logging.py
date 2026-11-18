"""Structured JSON logging with request/run correlation context.

Every log line is one JSON object. Context (request_id, run_id, correlation_id,
node_name) is carried in ``contextvars`` so it follows the work across function
calls; worker threads set it explicitly when they start a run or node.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)
node_name_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("node_name", default=None)

_SENSITIVE_KEYS = re.compile(r"(authorization|password|secret|api[_-]?key|token|cookie|credential)", re.I)
_BEARER = re.compile(r"(?i)(bearer|basic)\s+[a-z0-9._~+/=-]+")
_SK = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})")
REDACTED = "[REDACTED]"


def redact(value: Any, depth: int = 0) -> Any:
    """Recursively redact secrets from structures destined for logs/events."""
    if depth > 6:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        return {
            str(k): (REDACTED if _SENSITIVE_KEYS.search(str(k)) and not str(k).endswith("_tokens") else redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:50]]
    if isinstance(value, str):
        cleaned = _SK.sub(REDACTED, _BEARER.sub(r"\1 " + REDACTED, value))
        return cleaned if len(cleaned) <= 2000 else cleaned[:2000] + "…[truncated]"
    return value


class JsonFormatter(logging.Formatter):
    """Render records as single-line JSON with correlation context."""

    _RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": request_id_var.get(),
            "run_id": run_id_var.get(),
            "correlation_id": correlation_id_var.get(),
            "node_name": node_name_var.get(),
        }
        for key, val in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = val
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        payload = {k: v for k, v in redact(payload).items() if v is not None}
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    # Third-party HTTP loggers can print URLs/headers at DEBUG; keep them quiet.
    for noisy in ("httpx", "httpcore", "anthropic", "openai", "urllib3", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@contextlib.contextmanager
def log_context(**values: str | None) -> Iterator[None]:
    """Temporarily bind correlation fields for the current thread/context."""
    mapping = {
        "request_id": request_id_var,
        "run_id": run_id_var,
        "correlation_id": correlation_id_var,
        "node_name": node_name_var,
    }
    tokens = []
    for key, value in values.items():
        var = mapping[key]
        tokens.append((var, var.set(value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
