"""Typed tool contracts exposed to the planner LLM.

One tool per logical DataForSEO call. The Pydantic models are the single source
of truth: their JSON Schema is sent to the LLM as the tool ``input_schema`` and
the same models validate whatever arguments the LLM returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_keyword(value: str) -> str:
    text = " ".join(str(value).split()).strip().lower()
    if len(text) < 2:
        raise ValueError("keyword must be at least 2 characters")
    if len(text) > 80:
        raise ValueError("keyword must be at most 80 characters")
    if len(text.split()) > 10:
        raise ValueError("keyword must be at most 10 words")
    if any(op in text for op in ("site:", "intitle:", "inurl:", "allintitle:")):
        raise ValueError("search operators are not allowed (they multiply provider cost)")
    return text


class _ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DiscoverKeywordIdeasArgs(_ToolArgs):
    """Find real search queries related to seed keywords (DataForSEO Labs Keyword Ideas)."""

    seed_keywords: list[str] = Field(
        ..., min_length=1, max_length=5,
        description="1-5 short seed keywords describing the market/category (not the brand name).",
    )
    location_code: int = Field(..., description="DataForSEO location code of the brand's market, e.g. 2840 for United States.")
    language_code: str = Field(..., min_length=2, max_length=5, description="Language code of the brand's market, e.g. 'en'.")
    limit: int = Field(20, ge=5, le=30, description="Maximum keyword ideas to return (billed per item).")

    @field_validator("seed_keywords")
    @classmethod
    def _seeds(cls, value: list[str]) -> list[str]:
        cleaned = [_clean_keyword(v) for v in value]
        deduped = list(dict.fromkeys(cleaned))
        return deduped


class GetKeywordMetricsArgs(_ToolArgs):
    """Get search volume, paid competition and organic difficulty for specific queries (Keyword Overview)."""

    keywords: list[str] = Field(..., min_length=1, max_length=10, description="Exact queries to fetch metrics for.")
    location_code: int = Field(..., description="Location code of the brand's market.")
    language_code: str = Field(..., min_length=2, max_length=5, description="Language code of the brand's market.")

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(_clean_keyword(v) for v in value))


class CheckGoogleOrganicSerpArgs(_ToolArgs):
    """Fetch the live Google organic results page for one query (also reveals Google's AI Overview when shown)."""

    keyword: str = Field(..., description="The research query exactly as a user would type it into Google.")
    location_code: int = Field(..., description="Location code of the brand's market.")
    language_code: str = Field(..., min_length=2, max_length=5, description="Language code of the brand's market.")
    depth: Literal[10, 20] = Field(10, description="Number of organic results to fetch (10 or 20; 20 costs double).")

    @field_validator("keyword")
    @classmethod
    def _keyword(cls, value: str) -> str:
        return _clean_keyword(value)


class CheckChatGPTAnswerArgs(_ToolArgs):
    """Ask ChatGPT (via DataForSEO LLM Responses) the question a real buyer would ask, to see which brands it names/cites."""

    research_query: str = Field(..., description="The research query this prompt measures (must be one of the selected queries).")
    prompt: str = Field(
        ..., min_length=10, max_length=500,
        description="Natural, neutral buyer question. Must NOT mention the brand being analysed (that would bias the answer).",
    )
    web_search: bool = Field(True, description="Let ChatGPT search the web so the answer includes citations.")
    country_iso_code: str | None = Field(
        None, min_length=2, max_length=2, description="Optional ISO 3166-1 alpha-2 country for web search; requires web_search=true."
    )

    @field_validator("research_query")
    @classmethod
    def _rq(cls, value: str) -> str:
        return _clean_keyword(value)

    @field_validator("country_iso_code")
    @classmethod
    def _country(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.isalpha():
            raise ValueError("country_iso_code must be two letters")
        return value.upper()

    @model_validator(mode="after")
    def _combination(self) -> "CheckChatGPTAnswerArgs":
        if self.country_iso_code and not self.web_search:
            raise ValueError("country_iso_code requires web_search=true")
        return self


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline '$defs' references so every provider accepts the schema; strip titles."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].split("/")[-1]]
                return walk(dict(target))
            return {k: walk(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[_ToolArgs]
    endpoint: str
    budget_key: str

    def input_schema(self) -> dict[str, Any]:
        schema = _inline_refs(self.args_model.model_json_schema())
        schema.pop("title", None)
        schema.pop("description", None)
        schema["additionalProperties"] = False
        return schema


DISCOVER_KEYWORD_IDEAS = ToolSpec(
    name="discover_keyword_ideas",
    description=(
        "Discover real Google search queries people use in the brand's market, with live search volume, "
        "Google Ads (paid) competition and organic keyword difficulty. Call once with 1-5 category seed keywords."
    ),
    args_model=DiscoverKeywordIdeasArgs,
    endpoint="/v3/dataforseo_labs/google/keyword_ideas/live",
    budget_key="keyword_discovery",
)
GET_KEYWORD_METRICS = ToolSpec(
    name="get_keyword_metrics",
    description=(
        "Fetch live search volume and competition for specific queries that are NOT already in the candidate list "
        "with metrics. Batch all such queries into a single call."
    ),
    args_model=GetKeywordMetricsArgs,
    endpoint="/v3/dataforseo_labs/google/keyword_overview/live",
    budget_key="keyword_metrics",
)
CHECK_GOOGLE_ORGANIC_SERP = ToolSpec(
    name="check_google_organic_serp",
    description=(
        "Fetch the live Google organic results for one selected research query to measure where the brand and its "
        "competitors rank. Call once per selected research query."
    ),
    args_model=CheckGoogleOrganicSerpArgs,
    endpoint="/v3/serp/google/organic/live/advanced",
    budget_key="serp",
)
CHECK_CHATGPT_ANSWER = ToolSpec(
    name="check_chatgpt_answer",
    description=(
        "Ask ChatGPT a neutral buyer question for one selected research query and record which brands it mentions and "
        "which sites it cites. Call once per selected research query. Never name the analysed brand in the prompt."
    ),
    args_model=CheckChatGPTAnswerArgs,
    endpoint="/v3/ai_optimization/chat_gpt/llm_responses/live",
    budget_key="ai_visibility",
)

DISCOVERY_TOOLS: tuple[ToolSpec, ...] = (DISCOVER_KEYWORD_IDEAS,)
MEASUREMENT_TOOLS: tuple[ToolSpec, ...] = (GET_KEYWORD_METRICS, CHECK_GOOGLE_ORGANIC_SERP, CHECK_CHATGPT_ANSWER)
ALL_TOOLS: dict[str, ToolSpec] = {t.name: t for t in (*DISCOVERY_TOOLS, *MEASUREMENT_TOOLS)}
