from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An error that maps directly onto the contract's error envelope."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []
        self.meta = meta
        self.headers = headers or {}


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")
