# AI Visibility Platform — REST API Contract (v1)

Status: **authoritative contract between `backend/` (Flask) and `frontend/` (Angular).**
Owner: backend. Any change is recorded in the [Changelog](#changelog) at the bottom.
All example payloads in this document are **illustrative examples, not live results**.

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Base URL | `/api` (same origin; in Docker the Angular nginx container reverse-proxies `/api/*` to Flask). Local dev: `http://localhost:5000/api`. |
| Content type | Requests with a body must send `Content-Type: application/json`, otherwise `415`. Responses are always JSON (except the CSV export). |
| IDs | UUID v4 strings, e.g. `"8f1c2b7e-6f0e-4b8e-9a51-2a5d3c1e9f10"`. |
| Timestamps | ISO 8601 UTC with `Z` suffix, e.g. `"2026-11-18T14:05:12.345Z"`. |
| Field naming | `snake_case` everywhere. Fields are never omitted: absent values are `null`, empty lists are `[]`. |
| Money | USD as JSON numbers (floats), field names end in `_usd`. |
| Request tracing | Clients may send `X-Request-ID` (≤ 64 chars `[A-Za-z0-9._-]`). The server echoes it (or a generated one) in the `X-Request-ID` response header and in every error body as `request_id`. |
| Success envelope | Single resource: `{ "data": { ... } }`. Collections: `{ "data": [ ... ], "meta": { "total": n, "limit": n, "offset": n } }`. |
| Error envelope | See §2. |
| Pagination | `?limit=` (1–100, default 20) and `?offset=` (≥ 0, default 0) on list endpoints. |

### Enumerations (stable string values)

```text
RunStatus        = "queued" | "running" | "success" | "partial_success" | "failed"
RunMode          = "discover" | "remeasure"
DataMode         = "live" | "sandbox"   ("sandbox" only when the server is explicitly started with DATAFORSEO_MODE=sandbox: synthetic data, must be labelled in the UI)
NodeStatus       = "pending" | "running" | "success" | "degraded" | "failed" | "skipped"
Stage            = "setup" | "planning" | "retrieval" | "extraction" | "analysis" | "report"
Surface          = "chatgpt" | "google_organic" | "google_ai_overview"
ObservationStatus= "measured" | "not_present" | "failed" | "skipped"
Confidence       = "high" | "medium" | "low" | "insufficient"
Priority         = "high" | "medium" | "low"
ContentType      = "comparison_page" | "listicle" | "how_to_guide" | "faq_page" | "landing_page"
                 | "case_study" | "glossary_definition" | "product_page_update" | "blog_post" | "other"
GeneratedBy      = "llm" | "rule_based_fallback"          (recommendations)
SummaryGenerated = "deterministic"                        (report summary: built from computed values only)
QuerySource      = "keyword_discovery" | "research_question" | "planner" | "seed_keyword" | "tracked_query"
ComparisonStatus = "improved" | "declined" | "unchanged" | "new" | "dropped" | "not_comparable"
ErrorCode        = see §2
```

Run status semantics:

* `queued` – accepted, waiting for a worker.
* `running` – DAG executing. Partial data may already be visible via `/report`.
* `success` – every DAG branch completed and every planned measurement was obtained.
* `partial_success` – the run produced a report, but at least one branch/measurement failed or was degraded (e.g. SERP provider rate-limited after retries). The failures are listed in `errors[]` and per-node in `nodes[]`.
* `failed` – no usable evidence was obtained (e.g. invalid provider credentials), or the worker crashed. `errors[]` explains why.

---

## 2. Errors

Every non-2xx response has this body:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request body failed validation.",
    "details": [
      { "field": "competitors[1].domain", "message": "Value is not a valid domain name", "type": "value_error" }
    ],
    "request_id": "c0b8f0f2a1e44a52"
  }
}
```

`details` is always an array (possibly empty). Each detail has `field` (dot/bracket path or `null`), `message`, `type`.

| HTTP | `code` | When |
|---|---|---|
| 400 | `malformed_json` | Body is not valid JSON. |
| 400 | `invalid_query_parameter` | Bad `limit`, `offset`, `format`, etc. |
| 400 | `invalid_idempotency_key` | `Idempotency-Key` header malformed. |
| 404 | `not_found` | Unknown route or resource id. |
| 405 | `method_not_allowed` | Wrong HTTP method. |
| 409 | `duplicate_brand` | A brand with the same domain already exists (`details[0].field = "domain"`, plus `existing_id` in `meta`). |
| 409 | `run_in_progress` | An identical run (same brand, mode, question) is already `queued`/`running`. Body includes `meta.existing_run_id`. |
| 409 | `idempotency_key_conflict` | Same `Idempotency-Key` reused with a different request body. |
| 409 | `nothing_to_remeasure` | `mode = "remeasure"` but the brand has no completed run with measured queries. |
| 409 | `not_comparable` | Comparison requested between runs of different brands or with an unfinished run. |
| 413 | `payload_too_large` | Body > 64 KB. |
| 415 | `unsupported_media_type` | Missing/incorrect `Content-Type`. |
| 422 | `validation_error` | Body parsed but failed schema validation. |
| 429 | `too_many_runs` | Global concurrent-run limit reached (`meta.retry_after_seconds`). Also sets `Retry-After` header. |
| 500 | `internal_error` | Unexpected server error. Message is generic; details are in server logs under `request_id`. |
| 503 | `service_unavailable` | Database unavailable (health check fails). |

Errors may carry an optional `meta` object next to `code` (e.g. `{"existing_run_id": "..."}`).

### Run-level error object (inside run/report payloads, not HTTP errors)

```json
{
  "node": "retrieve_serp",
  "code": "provider_rate_limited",
  "message": "DataForSEO returned 40202 (rate limit) after 3 attempts.",
  "retryable": true,
  "provider": "dataforseo",
  "occurred_at": "2026-11-18T14:06:01.002Z"
}
```

Run-level `code` values:
`provider_auth_failed`, `provider_insufficient_funds`, `provider_rate_limited`, `provider_timeout`, `provider_network_error`,
`provider_server_error`, `provider_invalid_request`, `provider_unexpected_response`, `provider_no_results`,
`budget_exceeded`, `circuit_open`, `llm_unavailable`, `llm_rate_limited`, `llm_auth_failed`, `llm_invalid_request`,
`llm_invalid_output`, `llm_tool_call_rejected`, `llm_quota_exhausted` (server-side LLM call quota reached; the run continues with clearly labelled deterministic fallbacks),
`no_evidence`, `worker_crashed`, `interrupted_by_restart`, `internal_error`.

---

## 3. Data models

### 3.1 Market

```json
{ "location_code": 2840, "language_code": "en", "location_name": "United States", "language_name": "English" }
```

Supported markets are returned by `GET /api/config` (`markets[]`). Requests only send `location_code` + `language_code`; the pair must be one of the supported markets.

### 3.2 Competitor

```json
{ "id": "uuid", "name": "Trello", "domain": "trello.com" }
```

### 3.3 Brand

```json
{
  "id": "uuid",
  "name": "Asana",
  "domain": "asana.com",
  "description": "Work management platform for teams",
  "market": { "location_code": 2840, "language_code": "en", "location_name": "United States", "language_name": "English" },
  "seed_keywords": ["project management software", "task management app"],
  "competitors": [ { "id": "uuid", "name": "Trello", "domain": "trello.com" } ],
  "created_at": "2026-11-18T13:00:00.000Z",
  "updated_at": "2026-11-18T13:00:00.000Z",
  "latest_run": null,
  "stats": { "run_count": 0, "last_run_at": null }
}
```

`latest_run` is a **RunSummary** (§3.5) of the most recent run (any status) or `null`.

### 3.4 Node state

```json
{
  "name": "retrieve_serp",
  "label": "Google organic results",
  "stage": "retrieval",
  "agent": "search_retrieval",
  "status": "success",
  "message": "Fetched 2/2 SERPs (1 from cache)",
  "started_at": "2026-11-18T14:05:20.000Z",
  "finished_at": "2026-11-18T14:05:27.120Z",
  "duration_ms": 7120,
  "retry_count": 0,
  "error": null
}
```

DAG node names (stable; the UI may render them in this order):

| order | `name` | `stage` | `agent` | Purpose |
|---|---|---|---|---|
| 1 | `prepare_run` | setup | `orchestrator` | Snapshot brand, budgets, choose path |
| 2 | `load_tracked_queries` | planning | `query_planner` | Remeasure path only: reuse previously measured queries |
| 3 | `plan_discovery` | planning | `query_planner` | LLM chooses keyword-discovery tool + args |
| 4 | `retrieve_keyword_ideas` | retrieval | `search_retrieval` | DataForSEO Labs keyword ideas |
| 5 | `extract_keyword_candidates` | extraction | `extraction` | Normalize keyword ideas into candidates |
| 6 | `fallback_candidates` | extraction | `extraction` | Degraded path when discovery fails |
| 7 | `plan_measurement` | planning | `query_planner` | LLM selects research queries + measurement tool calls |
| 8 | `retrieve_keyword_metrics` | retrieval | `search_retrieval` | Volume/competition for queries lacking metrics |
| 9 | `retrieve_serp` | retrieval | `search_retrieval` | Google organic SERP (+ AI Overview if shown) |
| 10 | `retrieve_ai_answers` | retrieval | `search_retrieval` | ChatGPT answers via DataForSEO LLM Responses |
| 11 | `extract_observations` | extraction | `extraction` | Normalize raw payloads into observations |
| 12 | `score_opportunities` | analysis | `analysis` | Deterministic opportunity scoring |
| 13 | `synthesize_recommendations` | analysis | `analysis` | LLM content recommendations (validated) |
| 14 | `assemble_report` | report | `report` | Final JSON + human-readable summary |

Nodes not taken on the chosen path end as `skipped`.

### 3.5 RunSummary (used in lists and `brand.latest_run`)

```json
{
  "id": "uuid",
  "brand_id": "uuid",
  "status": "partial_success",
  "mode": "discover",
  "data_mode": "live",
  "question": "How does Asana show up in AI answers and search results for 'best project management software'?",
  "progress": { "percent": 100, "current_node": null, "message": "Report ready (1 branch degraded)" },
  "created_at": "2026-11-18T14:05:00.000Z",
  "started_at": "2026-11-18T14:05:01.000Z",
  "finished_at": "2026-11-18T14:06:10.000Z",
  "duration_ms": 69000,
  "headline": {
    "queries_measured": 2,
    "ai_visibility_rate": 0.5,
    "organic_top10_rate": 0.5,
    "top_opportunity_query": "best project management software",
    "top_opportunity_score": 72.4,
    "recommendation_count": 2
  },
  "error_count": 1,
  "status_url": "/api/runs/uuid",
  "report_url": "/api/runs/uuid/report"
}
```

`headline` is `null` until the run reaches a terminal status (and stays `null` for `failed` runs without evidence).
Rates are `0.0–1.0` or `null` when nothing was measured on that surface.

### 3.6 Run (detail, `GET /api/runs/{id}`)

RunSummary fields **plus**:

```json
{
  "correlation_id": "run-8f1c2b7e",
  "progress": {
    "percent": 55,
    "current_node": "retrieve_ai_answers",
    "message": "Asking ChatGPT (via DataForSEO) 'What is the best project management software?'",
    "completed_nodes": 6,
    "total_nodes": 11
  },
  "nodes": [ /* NodeState[] in DAG order (§3.4) */ ],
  "errors": [ /* run-level error objects (§2) */ ],
  "cost": {
    "provider_cost_usd": 0.0461,
    "provider_calls": 5,
    "provider_cache_hits": 1,
    "llm_calls": 3,
    "llm_input_tokens": 4120,
    "llm_output_tokens": 980
  },
  "config": {
    "force_refresh": false,
    "max_research_queries": 2,
    "max_serp_calls": 2,
    "max_ai_visibility_calls": 2,
    "max_keyword_discovery_calls": 1,
    "llm_provider": "anthropic",
    "llm_model": "claude-sonnet-5-5",
    "ai_answer_model": "gpt-4o-mini",
    "ai_answer_web_search": true
  },
  "brand_snapshot": { /* BrandSnapshot: id, name, domain, description, market, seed_keywords, competitors (competitor ids are null). No created_at/updated_at/latest_run/stats. */ }
}
```

`total_nodes` counts only nodes on the active path. `percent` is monotonic non-decreasing.

### 3.7 ResearchQuery

```json
{
  "id": "uuid",
  "text": "best project management software",
  "source": "keyword_discovery",
  "selected_for_measurement": true,
  "ai_prompt": "What is the best project management software for a growing team?",
  "metrics": {
    "search_volume": 40500,
    "paid_competition": 0.62,
    "paid_competition_level": "HIGH",
    "cpc_usd": 18.4,
    "keyword_difficulty": 71,
    "search_intent": "commercial",
    "source": "dataforseo_labs.keyword_ideas",
    "fetched_at": "2026-11-18T14:05:12.000Z"
  }
}
```

`metrics` is `null` if no metric data was obtained. Inside `metrics`, any individual field may be `null` (missing ≠ zero).
`paid_competition` / `paid_competition_level` are **Google Ads (paid search) competition** — never organic difficulty.
`keyword_difficulty` (0–100) is DataForSEO Labs' organic ranking difficulty estimate.

### 3.8 Observation (one measurement of one query on one surface)

```json
{
  "id": "uuid",
  "query_id": "uuid",
  "query": "best project management software",
  "surface": "chatgpt",
  "status": "measured",
  "provider": "dataforseo",
  "endpoint": "/v3/ai_optimization/chat_gpt/llm_responses/live",
  "fetched_at": "2026-11-18T14:05:40.000Z",
  "cached": false,
  "cost_usd": 0.0291,
  "brand": { "mentioned": true, "cited": false, "rank": null, "url": null },
  "competitors": [
    { "name": "Trello", "domain": "trello.com", "mentioned": true, "cited": true, "rank": null, "url": "https://trello.com/" }
  ],
  "cited_sources": [ { "domain": "zapier.com", "url": "https://zapier.com/blog/best-project-management-software/", "title": "The best project management software" } ],
  "top_results": [],
  "answer_excerpt": "Popular options include Asana, Trello and Monday.com ...",
  "error": null
}
```

Per surface:

* `chatgpt` — `brand.mentioned` = brand name/domain appears in the answer text; `brand.cited` = a citation URL belongs to the brand domain. `rank` is always `null`. `top_results` is `[]`.
* `google_organic` — `brand.rank` = best organic `rank_group` of the brand domain within the fetched depth (or `null` if not found); `mentioned` = `rank != null`; `cited` = `false`. `top_results` = top organic results `[{ "rank": 1, "domain": "...", "url": "...", "title": "..." }]` (≤ 10).
* `google_ai_overview` — derived from the same SERP call. `status = "not_present"` when Google showed no AI Overview for that query. `brand.cited` = brand domain among AI Overview references, `mentioned` = brand name in AI Overview text or cited.

`status = "failed"` → `brand` fields are all `null`/`false` must NOT be read as "not visible"; the UI must render failed measurements as **unknown**. `error` holds a run-level error object.

### 3.9 Opportunity

```json
{
  "id": "uuid",
  "rank": 1,
  "query_id": "uuid",
  "query": "best project management software",
  "score": 72.4,
  "confidence": "high",
  "coverage": 1.0,
  "is_partial": false,
  "components": {
    "demand":              { "value": 1.00, "weight": 0.30, "measured": true, "raw": { "search_volume": 40500 }, "explanation": "Highest search volume in this run's cohort (40,500/mo)." },
    "ai_gap":              { "value": 0.40, "weight": 0.35, "measured": true, "raw": { "chatgpt": "mentioned_not_cited", "google_ai_overview": "not_present" }, "explanation": "Brand is named by ChatGPT but none of its pages are cited." },
    "organic_gap":         { "value": 0.30, "weight": 0.20, "measured": true, "raw": { "best_rank": 6 }, "explanation": "Brand ranks #6 organically." },
    "competitor_pressure": { "value": 1.00, "weight": 0.15, "measured": true, "raw": { "visible": 2, "tracked": 2 }, "explanation": "2 of 2 tracked competitors are visible for this query." }
  },
  "explanation": "High demand query where competitors are visible but the brand is not cited in AI answers.",
  "metrics": { /* ResearchQuery.metrics */ }
}
```

`score` is `0–100` (1 decimal) or `null` when `confidence = "insufficient"` (coverage below the minimum). Opportunities are returned sorted by `score` desc (nulls last); `rank` follows that order (`null` for unscored). Scoring rules are documented in the README.

### 3.10 Recommendation

```json
{
  "id": "uuid",
  "opportunity_id": "uuid",
  "query": "best project management software",
  "priority": "high",
  "content_type": "comparison_page",
  "title": "Asana vs Trello vs Monday.com: Which Project Management Tool Fits Growing Teams?",
  "angle": "Neutral, data-backed comparison focused on teams of 10–50 people.",
  "gap_explanation": "ChatGPT names Asana but cites trello.com and third-party listicles instead of asana.com.",
  "recommended_action": "Publish a comparison page with a feature matrix and pricing table, then pitch it to the listicles ChatGPT already cites.",
  "supporting_observations": [
    { "observation_id": "uuid", "surface": "chatgpt", "summary": "Mentioned but not cited; trello.com cited." }
  ],
  "limitations": "Based on 1 ChatGPT answer sample; AI answers vary between sessions.",
  "generated_by": "llm"
}
```

### 3.11 Report (`GET /api/runs/{id}/report`)

```json
{
  "run": { /* RunSummary */ },
  "is_final": true,
  "data_mode": "live",
  "brand_snapshot": { /* BrandSnapshot (see §3.6) */ },
  "summary": {
    "headline": "Asana is named in 1 of 2 AI answers but cited in none; biggest gap: 'best project management software'.",
    "key_findings": [
      "ChatGPT mentions Asana for 1 of 2 measured questions, with 0 citations to asana.com.",
      "Asana ranks in Google's top 10 for 1 of 2 queries (best rank #6)."
    ],
    "markdown": "## Findings\n- ...",
    "generated_by": "deterministic"
  },
  "visibility": {
    "queries_measured": 2,
    "chatgpt":            { "measured": 2, "failed": 0, "brand_mentioned": 1, "brand_cited": 0 },
    "google_organic":     { "measured": 2, "failed": 0, "brand_top10": 1, "best_rank": 6 },
    "google_ai_overview": { "measured": 2, "present": 1, "brand_cited": 0 },
    "share_of_voice": [
      { "name": "Asana",  "domain": "asana.com",  "is_brand": true,  "ai_mentions": 1, "ai_citations": 0, "organic_top10": 1 },
      { "name": "Trello", "domain": "trello.com", "is_brand": false, "ai_mentions": 2, "ai_citations": 1, "organic_top10": 2 }
    ]
  },
  "opportunities":   [ /* Opportunity[] sorted */ ],
  "recommendations": [ /* Recommendation[] sorted by priority, then opportunity rank */ ],
  "queries":         [ /* ResearchQuery[] (all candidates; selected ones first) */ ],
  "observations":    [ /* Observation[] */ ],
  "errors":          [ /* run-level error objects */ ],
  "scoring": {
    "version": "opportunity-v1",
    "weights": { "demand": 0.30, "ai_gap": 0.35, "organic_gap": 0.20, "competitor_pressure": 0.15 },
    "min_coverage": 0.5
  },
  "plan": {
    "discovery_source": "llm",
    "measurement_source": "llm",
    "notes": []
  },
  "generated_at": "2026-11-18T14:06:10.000Z"
}
```

While a run is `queued`/`running`, the report is still returned with `is_final: false` and whatever has been persisted so far (possibly empty arrays, `summary: null`, `visibility: null`). For a `failed` run without evidence, arrays are empty and `errors[]` explains the failure.

### 3.12 Comparison (`GET /api/brands/{id}/compare`)

```json
{
  "brand_id": "uuid",
  "base_run":   { /* RunSummary (older) */ },
  "target_run": { /* RunSummary (newer) */ },
  "summary": {
    "improved": 1, "declined": 0, "unchanged": 1, "new": 0, "dropped": 0, "not_comparable": 0,
    "ai_visibility_rate_delta": 0.5,
    "organic_top10_rate_delta": 0.0,
    "avg_score_delta": -8.3
  },
  "queries": [
    {
      "query": "best project management software",
      "status": "improved",
      "base":   { "score": 72.4, "chatgpt_mentioned": false, "chatgpt_cited": false, "organic_rank": 6, "ai_overview_cited": false, "search_volume": 40500 },
      "target": { "score": 55.1, "chatgpt_mentioned": true,  "chatgpt_cited": false, "organic_rank": 4, "ai_overview_cited": false, "search_volume": 40500 },
      "delta":  { "score": -17.3, "organic_rank": 2 },
      "notes": ["Now mentioned by ChatGPT", "Organic rank improved 6 → 4"]
    }
  ]
}
```

`status` is derived from the brand's **visibility signals** on surfaces measured in both runs: newly mentioned/cited by ChatGPT, newly cited in Google AI Overview, or a better organic rank count as improvements (the reverse as declines); mixed signals net out; no change → `unchanged`. `delta.score` is reported separately (a lower opportunity score means the gap shrank). `notes` explain each change in plain language. `delta.organic_rank` is `base − target` (positive = moved up). Visibility fields are `null` when that side was not measured (failed) → status `not_comparable` if no shared measured surface.

---

## 4. Endpoints

### 4.1 `GET /api/health`

`200` when the DB is reachable, `503` otherwise.

```json
{
  "data": {
    "status": "ok",
    "version": "1.0.0",
    "time": "2026-11-18T14:00:00.000Z",
    "checks": {
      "database": "ok",
      "dataforseo_credentials": "configured",
      "llm_credentials": "configured",
      "data_mode": "live",
      "llm_provider": "openai",
      "llm_model": "gpt-4.1-mini",
      "llm_quota": { "used": 2, "limit": 4, "remaining": 2, "per_run_limit": 2 },
      "worker": { "active_runs": 0, "max_concurrent_runs": 2 }
    }
  }
}
```

`*_credentials` is `"configured"` or `"missing"` (presence only — no live call, no secrets). `checks.llm_provider` is `"anthropic"`, `"openai"` or `null`.

### 4.2 `GET /api/config`

Public, non-secret configuration for the UI.

```json
{
  "data": {
    "markets": [ { "location_code": 2840, "language_code": "en", "location_name": "United States", "language_name": "English" } ],
    "limits": {
      "max_competitors": 5, "max_seed_keywords": 5, "question_max_length": 400,
      "max_research_queries_per_run": 2, "max_serp_calls_per_run": 2,
      "max_ai_visibility_calls_per_run": 2, "max_keyword_discovery_calls_per_run": 1,
      "max_run_cost_usd": 0.25,
      "llm_max_calls_per_run": 2, "llm_max_calls_total": 4, "llm_max_output_tokens": 400
    },
    "llm_quota": { "used": 2, "limit": 4, "remaining": 2, "per_run_limit": 2 },
    "content_types": ["comparison_page", "listicle", "how_to_guide", "faq_page", "landing_page", "case_study", "glossary_definition", "product_page_update", "blog_post", "other"],
    "scoring": { "version": "opportunity-v1", "weights": { "demand": 0.30, "ai_gap": 0.35, "organic_gap": 0.20, "competitor_pressure": 0.15 }, "min_coverage": 0.5 },
    "data_mode": "live"
  }
}
```

`data_mode` is `"live"` by default. It is `"sandbox"` only when an operator explicitly starts the backend with `DATAFORSEO_MODE=sandbox` (synthetic data in DataForSEO shapes); the UI must then show a prominent sandbox banner. Mocks of providers exist only inside automated tests. `llm_quota` lets the UI show how many LLM calls remain (persistent, server-enforced).

### 4.3 `GET /api/metrics`

In-process operational metrics (resets on restart).

```json
{
  "data": {
    "started_at": "2026-11-18T13:59:00.000Z",
    "runs": { "started": 3, "success": 1, "partial_success": 1, "failed": 1 },
    "nodes": {
      "retrieve_serp": { "success": 2, "failed": 1, "degraded": 0, "latency_ms": { "count": 3, "avg": 6100.5, "p95": 9020.0, "max": 9020.0 } }
    },
    "external_calls": {
      "dataforseo.check_serp_organic": { "calls": 4, "errors": 1, "retries": 2, "cache_hits": 1, "cost_usd": 0.006 },
      "llm.anthropic": { "calls": 6, "errors": 0, "retries": 0 }
    },
    "circuit_breakers": { "dataforseo": "closed", "llm": "closed" }
  }
}
```

### 4.4 `POST /api/brands` → `201`

Request:

```json
{
  "name": "Asana",
  "domain": "asana.com",
  "description": "Work management platform for teams",
  "market": { "location_code": 2840, "language_code": "en" },
  "seed_keywords": ["project management software", "task management app"],
  "competitors": [ { "name": "Trello", "domain": "trello.com" }, { "name": "Monday.com", "domain": "monday.com" } ]
}
```

| Field | Type | Rules |
|---|---|---|
| `name` | string | required, 1–80 chars after trim |
| `domain` | string | required, bare registrable domain; `https://`, `www.` and paths are stripped and lowercased server-side (`https://www.Asana.com/x` → `asana.com`); must contain a dot; ≤ 253 chars |
| `description` | string \| null | optional, ≤ 500 chars |
| `market` | object | required; (`location_code`, `language_code`) must be a supported market |
| `seed_keywords` | string[] | required, 1–5 items, each 2–80 chars, ≤ 10 words, de-duplicated case-insensitively |
| `competitors` | object[] | optional (default `[]`), 0–5 items; each `name` 1–80 chars, `domain` same rules as brand; domains must be unique and different from the brand domain |

Unknown fields → `422`. Response: `201` `{ "data": Brand }` with `Location: /api/brands/{id}`.

### 4.5 `GET /api/brands` → `200`

Query: `limit`, `offset`, optional `q` (case-insensitive substring on name/domain).
Returns `{ "data": Brand[], "meta": { "total", "limit", "offset" } }` sorted by `updated_at` desc. Each brand includes `latest_run` (RunSummary or `null`) and `stats` — this is the cross-brand overview.

### 4.6 `GET /api/brands/{brand_id}` → `200` | `404`

`{ "data": Brand }`.

### 4.7 `PATCH /api/brands/{brand_id}` → `200` | `404` | `422`

Partial update. Any subset of `name`, `description`, `seed_keywords`, `competitors` (the full replacement list). `domain` and `market` are immutable (`422` if sent) so history stays comparable — create a new brand for a different domain/market. Past runs keep their own `brand_snapshot`.

### 4.8 `POST /api/brands/{brand_id}/runs` → `202`

Headers: `Content-Type: application/json`, optional `Idempotency-Key: <8–128 chars [A-Za-z0-9_-]>`.

Request (all fields optional; `{}` is valid):

```json
{
  "question": "How does Asana show up in AI answers and search results for 'best project management software'?",
  "mode": "discover",
  "force_refresh": false
}
```

| Field | Type | Rules |
|---|---|---|
| `question` | string \| null | 10–400 chars. Default when absent: generated from brand name + seed keywords. Ignored in `remeasure` mode (the previous run's question is reused). |
| `mode` | RunMode | default `"discover"`. `"remeasure"` re-measures the queries measured in the brand's latest run with evidence (no discovery spend) → used for "re-check and see what changed". |
| `force_refresh` | boolean | default `false`. When `false`, identical provider requests completed within the cache TTL are served from cache (no re-billing). `true` bypasses the cache. |

Response `202 Accepted`, header `Location: /api/runs/{run_id}`:

```json
{
  "data": {
    "run_id": "uuid",
    "status": "queued",
    "status_url": "/api/runs/uuid",
    "report_url": "/api/runs/uuid/report",
    "run": { /* RunSummary */ }
  }
}
```

Duplicate protection:

* Same `Idempotency-Key` + same body for the same brand → `202` with the **original** run (header `Idempotent-Replayed: true`), no new run is created.
* Same `Idempotency-Key` + different body → `409 idempotency_key_conflict`.
* No key, but an identical run (same brand + mode + normalized question) is still `queued`/`running` → `409 run_in_progress` with `meta.existing_run_id` (the UI should navigate to that run).
* Too many runs executing globally → `429 too_many_runs` + `Retry-After`.

### 4.9 `GET /api/brands/{brand_id}/runs` → `200` | `404`

History, newest first. Query: `limit`, `offset`, optional `status`. `{ "data": RunSummary[], "meta": {...} }`.

### 4.10 `GET /api/runs/{run_id}` → `200` | `404`

`{ "data": Run }` (§3.6). Poll every 2–3 s while `status` is `queued`/`running`; stop on a terminal status.

### 4.11 `GET /api/runs/{run_id}/events` → `200` | `404`

Trace timeline for one run (node-by-node). Query: `after_seq` (int, default 0), `limit` (1–500, default 200).

```json
{
  "data": [
    {
      "seq": 7,
      "timestamp": "2026-11-18T14:05:21.004Z",
      "node": "retrieve_serp",
      "event": "retry_scheduled",
      "level": "warning",
      "message": "DataForSEO rate limited (40202); retrying in 1.7s (attempt 2/3)",
      "data": { "tool": "check_serp_organic", "attempt": 2, "delay_ms": 1700, "error_code": "provider_rate_limited" }
    }
  ],
  "meta": { "total": 31, "limit": 200, "offset": 0, "next_after_seq": 7 }
}
```

`event` ∈ `run_queued, run_started, node_started, node_succeeded, node_degraded, node_failed, node_skipped, tool_call_validated, tool_call_rejected, provider_call, retry_scheduled, cache_hit, budget_blocked, llm_quota_reserved, llm_call, llm_cache_hit, fallback_used, run_finished`.
`level` ∈ `info, warning, error`. `data` never contains credentials or full raw payloads.

### 4.12 `GET /api/runs/{run_id}/report` → `200` | `404`

`{ "data": Report }` (§3.11).
`?format=csv` returns `text/csv` of the opportunities table (`rank,query,score,confidence,coverage,search_volume,paid_competition,keyword_difficulty,chatgpt_brand_mentioned,chatgpt_brand_cited,organic_rank,ai_overview_brand_cited,recommendation_title`) with `Content-Disposition: attachment`.

### 4.13 `GET /api/brands/{brand_id}/compare` → `200` | `404` | `409`

Query: optional `base_run_id`, `target_run_id`. Defaults: `target` = latest run with a report (`success`/`partial_success`), `base` = the one before it. `409 not_comparable` if fewer than two finished runs, runs of different brands, or either run not finished. Returns `{ "data": Comparison }` (§3.12).

### 4.14 `POST /api/client-errors` → `202`

Frontend error reporting so browser failures are traceable server-side.

```json
{
  "message": "Http failure response for /api/runs/123: 500 Internal Server Error",
  "kind": "http",
  "url": "/brands/abc/runs/123",
  "request_id": "c0b8f0f2a1e44a52",
  "run_id": "uuid-or-null",
  "stack": "optional, ≤ 8000 chars",
  "user_agent": "optional",
  "occurred_at": "2026-11-18T14:05:00.000Z"
}
```

`kind` ∈ `http, runtime, unhandled_rejection, other`; `message` 1–2000 chars; other fields optional/nullable. Response: `{ "data": { "id": "uuid", "received_at": "..." } }`. Logged as structured JSON with the `request_id` so it can be joined with backend logs.

---

## 5. Client guidance (frontend)

1. Create brand → `POST /api/brands`. On `409 duplicate_brand` offer to open `meta.existing_id`.
2. Start run → `POST /api/brands/{id}/runs` with a fresh `Idempotency-Key` per user click (re-use it on network retry). On `409 run_in_progress` navigate to `meta.existing_run_id`.
3. Poll `status_url` every 2–3 s; render `nodes[]` as a stepper and `progress.message` as live text; render `errors[]` humanely.
4. When terminal, fetch `report_url`. For `partial_success` show which surfaces failed using `observations[].status = "failed"` as **unknown**, never as "not visible".
5. History: `GET /api/brands/{id}/runs`; comparison: `GET /api/brands/{id}/compare`; re-check: `POST .../runs` with `{"mode": "remeasure"}`.
6. Overview: `GET /api/brands` (each item has `latest_run.headline`).
7. Report browser errors to `POST /api/client-errors` including the `X-Request-ID` of the failed call.

---

## Changelog

| Date | Change |
|---|---|
| 2026-11-18 | v1 initial contract. |
| 2026-11-18 | v1.1 (backward compatible): `summary.generated_by` is `"deterministic"`; BrandSnapshot shape documented; added LLM error codes `llm_rate_limited`, `llm_auth_failed`, `llm_invalid_request`; report gains `plan` (planner provenance); comparison status semantics clarified; `checks.llm_provider` in health; body limit 64 KB enforced by the server. Resolves frontend notes 1–3 in `frontend/INTEGRATION_ISSUES.md`. |
| 2026-11-18 | v1.2 (backward compatible): `data_mode` on RunSummary, Report, health and config (`"live"` default, `"sandbox"` explicit opt-in); `llm_quota` in health/config/metrics (+ `llm_audit` in metrics); error code `llm_quota_exhausted`; trace events `llm_quota_reserved`, `llm_cache_hit`; `report.plan.discovery_source` may be `"rule_based"` (default cost policy: 1 planner LLM call per run). |
