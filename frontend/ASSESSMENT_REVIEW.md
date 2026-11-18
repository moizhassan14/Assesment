# Assessment review — AI Visibility Platform

Reviewed November 18, 2026 (Asia/Karachi). Assessment source: all paragraphs and tables in `assessment.docx`, extracted from DOCX XML. API source: `docs/api-contract.md`, including v1.1 reconciliation. Codex implemented `frontend/` and inspected backend/root-owned files read-only.

## Submission disposition

**Frontend implemented and integrated with Flask. End-to-end live measurement remains blocked by the DataForSEO account.** This is not a claim that the required live-data submission is ready. The frontend displays unavailable evidence honestly and can read persisted reports without making provider calls.

Paid API usage was then restricted to a small, explicitly authorised budget: no real OpenAI calls or automatic analyses may be triggered during frontend development/verification, and DataForSEO funding is unavailable. All subsequent verification uses saved reports, isolated browser fixtures, mocked HTTP unit tests, or backend tests with networking disabled. OpenAI integration and strict usage limits are owned by the backend. No provider credentials were written to frontend source.

## Executed verification

| Check | Result and limits |
| --- | --- |
| `npm install` | Passed; lockfile generated and maintained. Initial sandbox network denial was handled through the normal approved dependency-install permission. |
| `npm run build` | Passed with strict TypeScript/template compilation. Final Docker build also compiles the frontend successfully; initial bundle approximately 323 KB raw / 88 KB estimated transfer. Lazy feature chunks remain separate. |
| `npm test` | **24 passed, 4 test files.** HttpTestingController isolates every business HTTP call. Tests cover 202 acceptance, idempotency, duplicate-run navigation, changed request keys, terminal polling, cancellation, slow requests, transient retry, exhausted failure recovery, safe errors, form/domain/keyword validation, and null versus measured zero. |
| Browser state/accessibility suite | **5 passed**: `states.spec.ts`, `accessibility.spec.ts`, and `saved-reports.spec.ts` against the saved test-provider database. No new analyses were created; saved run count remained **5**. |
| Responsive verification | Screenshots and document-overflow assertions at **1440, 1280, 768, 390 px** for portfolio, brand, pipeline, visibility report, opportunities, content actions, and history. Onboarding/setup also checked at those widths. Tables scroll inside panels. |
| Accessibility | Automated WCAG A/AA checks pass on onboarding/setup at desktop/tablet/mobile; populated views pass desktop scans; partial-result report passes a mobile scan. Manual keyboard checks include validation focus, drawer navigation, Escape handling, and focusable scrolling tables. This is not full screen-reader certification. |
| Persisted Flask reports | Read-only API/UI flow passes for a completed report: 2 saved opportunities and 2 saved recommendations, filtering, brief copying, history/comparison display, unchanged run count. These saved reports were generated earlier with automated-test provider doubles, not live search evidence. |
| Partial result presentation | Passed using a browser-only fixture derived from a persisted report. ChatGPT observations become failed/unknown, AI visibility displays a dash, successful evidence/opportunities remain visible, and coverage warnings name the missing surface. No backend mutation or provider call. |
| Real API creation/progress | Before the usage restriction, browser created Linear through Flask (`201`), started an actual run (`202`), received a run ID, and followed actual DAG progress. OpenAI planning succeeded. DataForSEO refused every measurement; run finalized failed. This is evidence of integration, not a successful live research result. |
| Frontend Docker | `docker build -t signal-frontend-review:local frontend` passed, including `npm ci` and Angular production compilation. Nginx syntax check passed against the existing backend Docker network. `/healthz`, SPA deep links, and `/api/health` return HTTP 200. |
| Production nginx → actual Flask saved failure | **1 browser test passed** against the final frontend image and existing Compose backend: all seven views, desktop/tablet/mobile overflow checks and accessibility scans. Existing run stayed **1**, with failed status and honestly empty opportunities/recommendations. Container health is **healthy**. No analyses or provider calls. |
| Backend tests, initial snapshot | **49 passed** in a Python 3.12 container, provider doubles only. Predates the newer LLM governance changes. |
| Backend tests, final governance snapshot | **62 passed** in a read-only source mount with `--network none`, without real credentials. This supersedes an intermediate 59-pass/2-fail snapshot taken while backend governance was being updated. |
| Production dependency audit | `npm audit --omit=dev --json`: **0 vulnerabilities**. |
| Development dependency audit | `npm audit --json`: **28 advisories (14 high, 14 moderate)** in development tooling and transitive glob/matching dependencies. Some automatic suggestions downgrade Angular CLI below the required version, and some report no supported fix. No force downgrade or unreviewed major overrides were applied. Development dependencies are absent from the final nginx runtime. |
| Credential review | Common API-key/private-key pattern scan of 132 non-ignored submission text files found no matching secrets at that review point. `git check-ignore` confirms `backend/.env`, frontend dependency caches, node_modules, and test artifacts are ignored. This is a scoped pattern scan, not a comprehensive security audit. No commit/push/reset/switch operations were performed. |

Screenshots, report snapshots, Playwright traces, and accessibility JSON are generated under ignored `frontend/test-results/`. Test-provider/fixture evidence is explicitly distinguished from live evidence. Later verification commands may replace these temporary artifacts.

Final local handoff: the verified frontend image now serves the existing Compose app at `http://localhost:8080`. Only the frontend container was replaced; the backend start timestamp was unchanged by that operation. Health, public config, SPA history deep link, and an existing final report return successfully. A final read observed three persisted runs in the concurrently maintained backend; no analyses were created by these handoff checks. Temporary Codex review containers and Angular preview servers were stopped, preserving saved container data and leaving the main app running.

## Frontend requirements

| Requirement | Status | Evidence |
| --- | --- | --- |
| All brands overview | PASS | `src/app/features/overview.ts` / `.html`: paginated server search, identity/domain, competitor count, latest status/time, backend visibility, report-derived opportunity counts, and server comparison deltas. Page-level totals are labeled as such. |
| Create/configure brand and competitors | PASS | `features/brand-form.ts` / `.html`: typed Reactive Forms, live market config, domain/name/keyword validation, dynamic competitors, immutable domain/market on edit, PATCH/POST separation, duplicate brand navigation, server field mapping, disabled submission, success feedback. Optional question is carried to the analysis form rather than sent as an invented brand field. |
| Brand detail and saved context | PASS | `features/brand-detail.*`, `brand-summary.*`, scoped `brand-store.ts`: market, competitors, seed context, latest metrics/status, recent analyses, configuration/report/history navigation. |
| Start analysis and handle 202 | PASS | `shared/start-analysis.ts`, `core/api.ts:80`: a user action starts a real endpoint request. 202 plus run ID required. Stable idempotency on ambiguous retry; changed input creates a new key; 409 existing-run reference opens that run. No automatic start on routing or brand creation. |
| Live analysis progress | PASS | `core/api.ts:124`, `features/run-store.ts`, `progress.*`: immediate status request, 2.5-second post-response polling, RxJS cancellation, no timer simulation, terminal completion, capped transient retries, reconnect UX, real node states and trace IDs. |
| Visibility overview | PASS | `features/report.*`: backend rates/counts, measured surface table, actual relative competitor mention bars, findings, priority counts, selected/measured query counts, stored retrieval timestamps, report/scoring metadata, raw observation status summary. |
| Opportunity explorer | PASS | `features/opportunities.*`: backend scores/ranks only; search, score/volume/alphabetic sorting, confidence/coverage filters, component explanations/weights, paid competition labeled correctly, organic difficulty separate, supporting observations and competitor context. |
| Content recommendations | PASS | `features/recommendations.*`: query/topic, title, angle, content format, priority, gap, recommended action, evidence and limitations; clipboard action with selectable-text fallback. No pretend publishing. |
| History and comparison | PASS | `features/history.*`: paginated saved runs, previous reports, explicit comparison selection, server-provided movement, unavailable comparison state, and rerun navigation. Dropdown choices cover completed runs on the current history page; default comparison uses the server's latest two completed reports. |
| Angular 17+, strict TypeScript | PASS | Angular 21 and strict compiler/template options in `package.json`, `tsconfig.json`. |
| Standalone/lazy architecture | PASS | `app.routes.ts` lazy loads standalone features. Long templates are separate HTML files; core API/models/diagnostics, scoped stores, and genuinely shared UI are separate concerns. |
| HttpClient/RxJS and shared state | PASS | Business HTTP is centralized in `core/api.ts`. Signals hold view state; RxJS controls request cancellation/polling. Config is shared through a finite replayed request. No redundant state library or local-storage report cache. |
| Actual API response compatibility | PASS with external result limit | `core/models.ts` follows snake_case v1.1 contracts; real Flask creation, status, report/history/config/health reads exercised. Actual live measurement payloads remain unverified because the provider refuses access. |
| No invented metrics/default demo | PASS | Runtime code contains no sample brands, report fixtures, fake success responses, or client opportunity formula. Examples exist only in automated tests. Missing evidence remains unavailable. |
| Responsive/a11y | PASS with stated testing limits | `styles.scss`, native semantic controls and labels, visible focus, skip link, reduced motion, drawer behavior, bounded table scroll regions, screenshots/overflow assertions and axe results. Chrome was tested; other browsers and comprehensive assistive-technology review remain untested. |
| Diagnosable browser failures | PASS | `core/errors.ts:49` reports safe generic failures via a non-intercepted HttpBackend client to `/api/client-errors`, with request/run reference and route. Interceptor adds X-Request-ID, recursion is prevented, telemetry is throttled. No form values/provider secrets/raw server traceback is sent. |
| Docker/API routing/configuration | PASS | Multi-stage `Dockerfile`, SPA fallback and unchanged `/api` proxy to `backend:5000` in `nginx.conf`, static health check; public compile-time settings documented in `ENVIRONMENT.md`. Root Compose service/port matches; root files untouched by Codex. |

## Global UX states

| State | Status | Verification/evidence |
| --- | --- | --- |
| First-time onboarding | PASS | Empty portfolio fixture and create-brand action. |
| Initial loading | PASS | Shared accessible skeleton state; API-driven page/store loading. |
| Form submission loading | PASS | Submission signals disable repeat actions; mocked submission tests and prior real brand create. |
| Long-running analysis | PASS | Actual status polling, node pipeline, user can leave/reopen; slow-request/cancellation tests. |
| Complete success | PASS | Existing completed Flask test-provider report and saved history. |
| Partial success | PASS | Saved-report-derived automated fixture, explicit surface failures, unknown AI rate, retained opportunities. |
| External data unavailable | PASS | Actual failed DataForSEO run, no-evidence report state, pipeline diagnostics. |
| API/network error | PASS | 503 fixture with request reference, retry/recovery, raw server text hidden. |
| Invalid/nonexistent route | PASS | Dedicated 404 page; resource errors are safe and recoverable. |
| Empty data/filters | PASS | Empty portfolio and opportunity-filter tests. |
| No comparison yet | PASS | Backend 409 maps to a designed baseline/comparison-unavailable state; no invented delta. |

## Backend and overall assessment requirements (read-only review)

| Requirement | Status | Evidence and limitation |
| --- | --- | --- |
| Flask API, consistent validation/persistence | PASS | `backend/app/api/`, `schemas/api.py`, SQLAlchemy models/repositories/services; real resource/status/error envelopes exercised. |
| Single-responsibility agents | PASS | `agents/query_planner.py`, `retrieval.py`, `extraction.py`, `scoring.py`, `synthesis.py`, `report.py`. Retrieval does not perform synthesis; normalization and final report assembly are distinct. |
| Non-linear DAG and routing | PASS | `graph/builder.py:62`: discovery/extraction fallback, remeasure branch, three parallel retrieval branches with fan-in, evidence/no-evidence conditional routing. |
| True function/tool calling | PASS | Official OpenAI/Anthropic adapters send structured tools; planner parses actual tool calls, validates arguments and context before external execution. The pre-restriction live run recorded LLM-selected planning stages. |
| Strict LLM output shape and repair/fallback | PASS | `tools/schemas.py`, `tools/validation.py`; `agents/synthesis.py` checks schema, opportunity/evidence references, allowed URLs, and missing recommendations. Invalid drafts use visible rule-based fallback. Tests include malformed arguments and invalid recommendation output. |
| Live external data | BLOCKED | Real DataForSEO endpoints are implemented, but actual account returns HTTP 403 / 40104. No live search/AI visibility result was obtained; no live-positive integration success is claimed. |
| Explainable opportunity scoring | PASS | `agents/scoring.py`: deterministic backend calculation, measured-only components, coverage/confidence, sorted ranking, distinct paid competition/difficulty. Formula and trade-offs are documented in root README. |
| Retry/backoff/error classification | PASS for inspected provider flow | Provider timeout/network/rate-limit/server errors have capped jittered retries, non-retryable account/request errors fail fast, circuit breaker exists, partial results survive. New LLM retry/usage behavior is deliberately constrained by the user and owned by the backend. |
| Duplicate cost protection/history | PASS with limitation | Request idempotency/fingerprint protection, persistent provider cache and append-only reports/snapshots. Provider USD guard is soft with in-flight concurrency; see findings. |
| OpenAI strict usage governance | PASS in offline tests | Persistent governor/call audit/cache and call/token limits exist; final offline suite passes all 62 tests, including governance refusal/audit and persistent quota checks. Backend owns implementation; real paid requests were not used to verify limits. |
| Partial failure and honest no evidence | PASS | Branch errors persist, normalized observations distinguish failed/skipped/not_present, no evidence yields failed run/report rather than zero metrics. Backend happy/partial/failure tests and actual provider refusal support this. |
| Logs/traces/metrics/health | PASS | Structured redacted JSON logging, per-run correlation IDs, node timing/retries, persisted events, `/metrics`, `/health`, and `/client-errors`. Final offline quota-refusal audit tests pass. |
| Containers, environment setup, one-command startup | PASS | Existing root Compose starts Flask/nginx; API proxy and health/deep-link checks exercised. Backend env example, frontend public config docs and root setup instructions exist. |
| Documentation/communication/AI disclosure | PASS | Root README covers architecture/DAG/resilience/scoring/setup/limits and assistant usage; frontend README/config/integration issues/review add implementation and exact verification limits. |
| Risk-based tests | PASS with live-provider limit | Frontend 24 unit and 5 browser checks pass, plus one production saved-report browser check. Current backend suite: 62 passed offline. Successful live measurement remains blocked externally. |
| Optional bonuses | PARTIAL | Background progress, CSV export, and CI workflow source exist. Dark mode and a screen recording were not added. CI execution on a hosted runner was not performed. |

## Reproducible findings and required actions

### P0 — live DataForSEO evidence unavailable (external account blocker)

- **Observed:** HTTP 403 with provider code **40104** and account-verification message. An account-info GET succeeds, proving credential presence/authentication is not sufficient for measurement endpoints.
- **Evidence:** prior real run `1ddb1752-b0dd-4174-b2f1-c5accdfe8c02` in the isolated integration container; existing Compose brand Asana has failed saved run `efe9fa5c-3ae8-4e9c-9301-2be2046155d8`. Public reads are sufficient to inspect failures; do not restart analysis to reproduce during frontend work.
- **User-reported additional restriction:** email verified, phone verification unsupported in their country, provider asks for a $5 top-up they cannot fund.
- **Action:** record as a submission limitation; provider access requires an account-owner/provider resolution. Do not ask for payment to finish frontend work, use fake default data, or spend another person's OpenAI quota to retry it. An alternative live provider would be a backend-owned scope change, not a frontend workaround.
- DataForSEO documents account verification under [40104](https://docs.dataforseo.com/v3/appendix/errors/). Its [official contact page](https://dataforseo.com/contact) is available to ask about an alternate verification method; no specific exception was found or promised.

### Resolved P1 — intermediate backend LLM governance failures (backend-owned)

- `tests/test_llm_governance.py::test_per_run_cap_blocks_extra_calls_and_falls_back_honestly`: expected success plus `blocked_quota` audit record, received only success.
- `tests/test_llm_governance.py::test_total_quota_is_persistent_atomic_and_survives_restart`: expected quota exhaustion in run-level errors, assertion failed.
- **Intermediate result:** 59 pass / 2 fail. The first failure showed a refusal/fallback with missing audit evidence; this review does **not** conclude that real paid calls bypassed the cap.
- **Reproduce safely:** run current backend tests in the provided test image with a read-only mount and `--network none`, as recorded in `INTEGRATION_ISSUES.md`. No real credentials are supplied.
- **Resolution:** after the governance update, the final full offline run passed **62 tests**, including these checks. No backend files were changed by frontend work, and no real provider calls were made during this verification.

### P2 — provider USD ceiling is a soft guard

- `backend/app/tools/budget.py:RunBudget.reserve` checks already incurred cost without reserving an estimated maximum for each in-flight request. Parallel calls can cross the threshold before their actual costs are recorded.
- **Action:** document soft-limit semantics; reserve estimates/reconcile actual spend before advertising a hard dollar ceiling. OpenAI governance is separate and owned by the backend.

### P2 — development toolchain advisories remain

- `npm audit --json` reports 28 transitive/dev advisories (glob/minimatch/picomatch and dependent tooling). Production dependency audit is clean, and the shipped nginx image contains no Node development runtime.
- **Action:** track compatible upstream patched toolchain versions. Avoid the suggested Angular-16 downgrade because the assessment requires Angular 17+; do not force major dependency overrides merely to suppress the audit count.

### P3 — fixed server stage totals can differ from executed nodes

- `backend/app/services/serializers.py:run_detail_json` uses fixed totals of 12/10, while inactive-path stages become skipped. A terminal run can show fewer completed nodes than the fixed denominator at 100%.
- **Action:** compute active-path totals from the selected path, or expose a more precise progress contract. Frontend uses the actual server values and does not simulate extra stage completion.

## Frontend defects fixed during review

- Low secondary-text contrast, inactive tabs, and recommendation action text: darkened and rescanned.
- History-page mobile overflow: positioned the scrolling table wrapper to contain absolutely positioned screen-reader labels; corrected flexible comparison controls. Document width now remains within all tested viewports.
- Scrollable evidence tables lacked a keyboard focus target: added labeled, focusable regions and inset focus indicators; mobile partial-result accessibility scan passes.
- Report request from an old route could outlive the run switch: route loading now cancels the pending report stream.
- Backend deterministic summary provenance initially differed from the contract: backend owner resolved v1.1; frontend models/presentation now match and do not attribute deterministic summaries to an LLM.
- Portfolio comparison could be unrelated to a latest unfinished/failed attempt: comparison fetching now requires a latest headline, avoiding a stale delta beside an unavailable latest measurement.

## Honest untested areas

- A successful real DataForSEO measurement, live-positive scores/recommendations, and live remeasurement comparison: blocked by account verification/funding. Test-provider results are not substitutes for this requirement.
- Real-provider billing reconciliation and paid-call behavior of backend-owned LLM governance; offline governance tests pass.
- Full screen-reader usability, browser coverage beyond installed Chrome, production load/security hardening, actual hosted CI execution, and provider billing reconciliation.
- No account login/multi-tenancy contract was supplied, so no authentication system was invented.

## Final blockers

1. Live DataForSEO account access/funding cannot currently produce required real measurements.

No additional paid calls are required to review or use the implemented frontend with saved reports.
