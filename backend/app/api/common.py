"""Shared request/response helpers for the API blueprints."""

from __future__ import annotations

import re
from typing import Any, TypeVar

from flask import current_app, jsonify, request
from pydantic import BaseModel, ValidationError

from app.services.errors import ApiError

M = TypeVar("M", bound=BaseModel)
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def services() -> Any:
    return current_app.extensions["services"]


def ok(data: Any, status: int = 200, headers: dict[str, str] | None = None, meta: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    body: dict[str, Any] = {"data": data}
    if meta is not None:
        body["meta"] = meta
    response = jsonify(body)
    response.status_code = status
    for k, v in (headers or {}).items():
        response.headers[k] = v
    return response


def pydantic_details(exc: ValidationError) -> list[dict[str, Any]]:
    details = []
    for err in exc.errors():
        parts: list[str] = []
        for p in err.get("loc", ()):
            if isinstance(p, int):
                parts[-1:] = [f"{parts[-1]}[{p}]"] if parts else [f"[{p}]"]
            else:
                parts.append(str(p))
        msg = str(err.get("msg", "Invalid value")).removeprefix("Value error, ")
        details.append({"field": ".".join(parts) or None, "message": msg, "type": err.get("type", "value_error")})
    return details


def parse_body(model: type[M]) -> M:
    if request.content_length and request.content_length > 64 * 1024:
        raise ApiError(413, "payload_too_large", "Request body exceeds 64 KB.")
    if not request.is_json:
        raise ApiError(415, "unsupported_media_type", "Content-Type must be application/json.")
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(cache=True)
        if raw.strip():
            raise ApiError(400, "malformed_json", "Request body is not valid JSON.")
        payload = {}
    if not isinstance(payload, dict):
        raise ApiError(422, "validation_error", "Request body failed validation.",
                       details=[{"field": None, "message": "Body must be a JSON object", "type": "type_error"}])
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ApiError(422, "validation_error", "Request body failed validation.", details=pydantic_details(exc)) from exc


def int_arg(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_query_parameter", f"'{name}' must be an integer.",
                       details=[{"field": name, "message": "must be an integer", "type": "type_error"}]) from exc
    if not minimum <= value <= maximum:
        raise ApiError(400, "invalid_query_parameter", f"'{name}' must be between {minimum} and {maximum}.",
                       details=[{"field": name, "message": f"must be between {minimum} and {maximum}", "type": "range_error"}])
    return value


def idempotency_key() -> str | None:
    key = request.headers.get("Idempotency-Key")
    if key is None:
        return None
    if not _IDEMPOTENCY_RE.match(key):
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key must be 8-128 characters of [A-Za-z0-9_-].")
    return key
