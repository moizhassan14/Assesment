# Integration coordination

Frontend uses `docs/api-contract.md` as the API authority. Backend, root Compose, and root README remain owned by the backend. No changes to those files were made by the frontend implementation.

## Production wiring

- Frontend image context: `./frontend`; static output: `/app/dist/visibility/browser`.
- Nginx listens on container port **80** and forwards `/api/*` unchanged to **`backend:5000`**. Root Compose must name the Flask service `backend` (or provide that network alias) and expose port 5000 internally.
- Frontend container health endpoint: `/healthz`. Flask health: `/api/health`.
- Development uses `npm start` and `proxy.conf.json`, forwarding `/api` to `127.0.0.1:5000`.
- Frontend configuration is compile-time, documented in `ENVIRONMENT.md`; no provider credentials belong in the browser.

## Resolved contract differences found during read-only review

1. **Report summary provenance (medium):** `backend/app/agents/report.py:build_summary` returns `generated_by: "deterministic"`, while contract §3.11 and enum `GeneratedBy` allow `"llm" | "rule_based_fallback"`. Prefer documenting deterministic summaries explicitly in the contract. The frontend will render a factual evidence-summary label for this value, without attributing it to an LLM.
2. **Snapshot shape (low):** `snapshot_as_brand` omits `created_at` and `updated_at` and emits competitor IDs as null. The contract calls this a Brand snapshot without latest_run/stats. Frontend does not rely on snapshot timestamps or competitor IDs, but the contract should explicitly describe these nullable/omitted fields.
3. **Request size limit (low):** contract says 64 KB, but `create_app` configures `MAX_CONTENT_LENGTH = 256 * 1024`. Choose and document one value.

**Resolved by backend owner:** contract v1.1 now explicitly documents deterministic summaries and BrandSnapshot fields; the server enforces 64 KB. Frontend types have been aligned with v1.1. These are no longer blockers.

## Live-provider blocker

**Usage constraints added during implementation:** paid API usage is restricted to a small authorised budget; no real OpenAI requests or automatic analysis creation are allowed during ongoing Angular development/builds/tests/UI verification. The user also reports that DataForSEO requires a $5 top-up they cannot fund and that phone verification is unavailable in their country despite email verification. Do not make more paid provider calls or ask for a payment to finish frontend work. Use saved reports and isolated test fixtures. The backend owns provider integration and usage governance.

At 14:43–14:48 Asia/Karachi on November 18, real DataForSEO POSTs returned **HTTP 403 / provider code 40104**: “Please verify your account before using the API.” A non-billable account GET returned 200/20000. This confirms configured authentication alone is insufficient; account verification at `https://app.dataforseo.com/` is required before live measurements can be verified. No credentials were printed or copied into frontend files.

Reproduction: create Linear (`linear.app`, US/en), start discovery, open run `1ddb1752-b0dd-4174-b2f1-c5accdfe8c02` in the isolated integration container. OpenAI planned actual tool calls; keyword ideas, keyword metrics, organic SERP, and AI-answer retrieval all failed with provider refusal. The run finalized as failed, preserving diagnostic observations and empty opportunities rather than invented data.

The backend owner has since improved classification of non-200 provider envelopes, so this refusal maps to `provider_auth_failed` rather than generic `provider_invalid_request`. The frontend already supports that code.

## Additional review observations

- **Resolved backend governance checks:** An intermediate source snapshot reported **59 passed / 2 failed** while LLM governance was being added: `test_per_run_cap_blocks_extra_calls_and_falls_back_honestly` (missing `blocked_quota` audit row) and `test_total_quota_is_persistent_atomic_and_survives_restart` (quota-error assertion). After the governance changes, the final full suite passed **62 tests**. Command: `docker run --rm --network none --mount type=bind,source=<checkout>/backend,target=/review,readonly -w /review signal-backend-review:local python -m pytest -q -p no:cacheprovider`. The source mount was read-only, networking was disabled, and no real credentials were supplied. Frontend work did not modify backend governance.

- **Medium, non-blocking:** `RunBudget.reserve()` checks incurred cost before starting a call, without reserving an estimated in-flight cost; parallel requests can cross the configured USD ceiling. The root README should call this a soft guard and explain LLM costs are separate. Consider cost reservation/provider balance checks for production.
- **Low:** `run_detail_json()` uses fixed `total_nodes` (12 discovery / 10 remeasure), while inactive-path nodes are skipped and the executed count may differ. The frontend faithfully shows server counts; the backend should calculate active-path totals if exact stage fractions are required.

## Verification

Live integration and final disposition will be recorded in `ASSESSMENT_REVIEW.md`. Example payloads in unit/browser state tests are isolated automated-test fixtures and are never used by the application.
