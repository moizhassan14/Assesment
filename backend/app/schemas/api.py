"""Validated request bodies for the REST API (see docs/api-contract.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.text import normalize_domain

MARKETS: list[dict[str, object]] = [
    {"location_code": 2840, "language_code": "en", "location_name": "United States", "language_name": "English"},
    {"location_code": 2826, "language_code": "en", "location_name": "United Kingdom", "language_name": "English"},
    {"location_code": 2124, "language_code": "en", "location_name": "Canada", "language_name": "English"},
    {"location_code": 2036, "language_code": "en", "location_name": "Australia", "language_name": "English"},
    {"location_code": 2356, "language_code": "en", "location_name": "India", "language_name": "English"},
    {"location_code": 2586, "language_code": "en", "location_name": "Pakistan", "language_name": "English"},
    {"location_code": 2784, "language_code": "en", "location_name": "United Arab Emirates", "language_name": "English"},
    {"location_code": 2276, "language_code": "de", "location_name": "Germany", "language_name": "German"},
    {"location_code": 2250, "language_code": "fr", "location_name": "France", "language_name": "French"},
    {"location_code": 2724, "language_code": "es", "location_name": "Spain", "language_name": "Spanish"},
]
_MARKET_INDEX = {(m["location_code"], m["language_code"]): m for m in MARKETS}


def market_info(location_code: int, language_code: str) -> dict[str, object] | None:
    return _MARKET_INDEX.get((location_code, language_code))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MarketIn(_Strict):
    location_code: int
    language_code: str = Field(..., min_length=2, max_length=5)

    @model_validator(mode="after")
    def _supported(self) -> "MarketIn":
        self.language_code = self.language_code.lower()
        if market_info(self.location_code, self.language_code) is None:
            raise ValueError("Unsupported market; see GET /api/config for supported (location_code, language_code) pairs")
        return self


class CompetitorIn(_Strict):
    name: str = Field(..., min_length=1, max_length=80)
    domain: str = Field(..., min_length=3, max_length=253)

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        return normalize_domain(value)


def _clean_seeds(value: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for raw in value:
        text = " ".join(str(raw).split())
        if not 2 <= len(text) <= 80:
            raise ValueError("each seed keyword must be 2-80 characters")
        if len(text.split()) > 10:
            raise ValueError("each seed keyword must be at most 10 words")
        seen.setdefault(text.lower(), text.lower())
    return list(seen.values())


def _check_competitors(competitors: list[CompetitorIn], brand_domain: str | None) -> None:
    domains = [c.domain for c in competitors]
    if len(set(domains)) != len(domains):
        raise ValueError("competitor domains must be unique")
    if brand_domain and brand_domain in domains:
        raise ValueError("a competitor cannot have the brand's own domain")


class BrandCreate(_Strict):
    name: str = Field(..., min_length=1, max_length=80)
    domain: str = Field(..., min_length=3, max_length=253)
    description: str | None = Field(None, max_length=500)
    market: MarketIn
    seed_keywords: list[str] = Field(..., min_length=1, max_length=5)
    competitors: list[CompetitorIn] = Field(default_factory=list, max_length=5)

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        return normalize_domain(value)

    @field_validator("seed_keywords")
    @classmethod
    def _seeds(cls, value: list[str]) -> list[str]:
        return _clean_seeds(value)

    @model_validator(mode="after")
    def _competitors(self) -> "BrandCreate":
        _check_competitors(self.competitors, self.domain)
        return self


class BrandUpdate(_Strict):
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = Field(None, max_length=500)
    seed_keywords: list[str] | None = Field(None, min_length=1, max_length=5)
    competitors: list[CompetitorIn] | None = Field(None, max_length=5)

    @field_validator("seed_keywords")
    @classmethod
    def _seeds(cls, value: list[str] | None) -> list[str] | None:
        return _clean_seeds(value) if value is not None else None

    @model_validator(mode="after")
    def _competitors(self) -> "BrandUpdate":
        if self.competitors is not None:
            _check_competitors(self.competitors, None)
        return self


class RunCreate(_Strict):
    question: str | None = Field(None, min_length=10, max_length=400)
    mode: Literal["discover", "remeasure"] = "discover"
    force_refresh: bool = False


class ClientErrorIn(_Strict):
    message: str = Field(..., min_length=1, max_length=2000)
    kind: Literal["http", "runtime", "unhandled_rejection", "other"] = "other"
    url: str | None = Field(None, max_length=500)
    request_id: str | None = Field(None, max_length=64)
    run_id: str | None = Field(None, max_length=36)
    stack: str | None = Field(None, max_length=8000)
    user_agent: str | None = Field(None, max_length=500)
    occurred_at: datetime | None = None
