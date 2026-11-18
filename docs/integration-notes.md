# Integration notes (backend → frontend)

Owner: backend. Frontend items are **recommendations for the frontend owner (Codex)**; the backend did not edit `frontend/`.

## Resolved on the backend side

| # | Item | Resolution |
|---|---|---|
| F1 | `summary.generated_by` differed from contract | Contract v1.1 documents `"deterministic"` (report summary is built only from computed values). |
| F2 | Brand snapshot shape | Contract v1.1 documents `BrandSnapshot` (no timestamps/`latest_run`/`stats`; competitor ids are `null`). |
| F3 | Body size limit 64 KB vs 256 KB | Server now enforces 64 KB (`MAX_CONTENT_LENGTH`), matching the contract. |
| A10 | `progress.completed_nodes` could exceed `total_nodes` on the degraded discovery path | `total_nodes` is now `max(typical path, completed)` while running and the exact executed count when terminal. |

## Recommended frontend follow-ups (low severity, no runtime breakage found)

Read-only audit on 2026-11-18 found **no high-severity mismatches** (paths, methods, bodies, headers, envelopes, enums and polling all match).

1. `core/models.ts`: type report `summary.generated_by` as `'deterministic'` (current `'llm'|'rule_based_fallback'` only works by accident).
2. `core/models.ts`: `BrandSnapshot` has no `created_at`/`updated_at`; snapshot competitor `id` is `null`.
3. Nullable fields typed non-null: `Opportunity.query`/`Recommendation.query` (guard `o.query.toLowerCase()` in `features/opportunities.ts`), `RunIssue.provider`, `Observation.fetched_at`, `cited_sources[].domain|title`, `top_results[].domain|url|title`, `KeywordMetrics.fetched_at`, `progress.message`.
4. `Run.config` additionally carries `request_hash`, `max_run_cost_usd`, `source_run_id`; `llm_provider`/`llm_model` may be `null`.
5. `Health` has `checks.llm_provider`; `worker.active_runs` may be `null`; a 503 still carries `data`.
6. `Report.plan` (planner provenance: `discovery_source`, `measurement_source`, `notes[]`) is available if the UI wants to explain how queries were chosen.
7. Add labels for run-error codes `llm_rate_limited`, `llm_auth_failed`, `llm_invalid_request` (and friendlier text for `provider_auth_failed`, which includes DataForSEO's "verify your account" case).
8. Use `meta.retry_after_seconds` / `Retry-After` on `429 too_many_runs`; add a message for `idempotency_key_conflict`.
9. Client error reports: send `kind: "unhandled_rejection"` where applicable and include `user_agent` and a truncated `stack` (both accepted by `POST /api/client-errors`).
10. `GET /api/runs/{id}/events` (node-by-node trace) is available for a "what happened" timeline on failed/partial runs.

## Contract v1.2 additions the UI should surface (recommended, high value for honesty)

11. **Sandbox banner (important):** `run.data_mode`, `report.data_mode`, `GET /api/config → data_mode` and `GET /api/health → checks.data_mode` are `"live"` or `"sandbox"`. When `"sandbox"`, show a persistent, prominent banner on run/report pages: *"Sandbox data — synthetic DataForSEO-shaped data, not live measurements."* (Sandbox summaries already start with `[SANDBOX DATA]`, and synthetic answer excerpts start with `[SANDBOX]`.) The persisted demo run `1febfbde-a4e8-4a46-a36e-418f8e127250` is a sandbox run.
12. **LLM quota:** `GET /api/config → llm_quota {used, limit, remaining, per_run_limit}`. When `remaining = 0`, tell the user before they start a run that AI planning/recommendations will use labelled rule-based fallbacks. Add a label for run-error code `llm_quota_exhausted`.
13. **Plan provenance:** `report.plan.discovery_source` can be `"rule_based"` (default) and `measurement_source` `"llm" | "llm_repaired" | "fallback_rule_based" | "tracked_queries"`.
14. Trace events now include `llm_quota_reserved` and `llm_cache_hit`.
15. **Do not trigger LLM calls from the UI** other than via an explicit user-initiated `POST /api/brands/{id}/runs` (already the case — no change needed; listed for completeness of the cost policy).
