# Signal · AI Visibility Platform frontend

Angular 21, strict TypeScript, standalone components, lazy feature routes, Reactive Forms, signals, and RxJS. This is a real Flask client. Application code contains no example brands, scores, volumes, reports, or fake API success responses.

## Run

Use Node 24 (24.14.1 verified here). Start Flask on port 5000 following the root README, then:

```sh
cd frontend
npm install
npm start
```

Open `http://localhost:4200`. Angular proxies `/api` to Flask. For production, build this directory's Dockerfile and connect it to the `backend:5000` service using root Compose. The frontend container serves port 80 and has an independent `/healthz` health check.

```sh
npm run build
npm test
npm run test:e2e
```

Playwright uses an installed Chrome channel. `npx playwright install chrome` can provision Chrome on machines without it. State tests use contract fixtures strictly inside automated tests. **Use saved reports for UI verification**: `RUN_PERSISTED_E2E=1` with `saved-reports.spec.ts`. That test blocks all browser API mutations and checks that the saved run count is unchanged. Routine builds, unit tests, and browser checks never create live analyses or call OpenAI. Lifecycle tests are disabled unless separately and explicitly authorized with `ALLOW_ANALYSIS_CREATION=1` and a provider-mode flag. The backend owns provider integration and usage limits. See `ENVIRONMENT.md` for configuration and `ASSESSMENT_REVIEW.md` for exact executed results and limits.

For repeatable browser integration without provider charges, build the backend's test image and run the explicitly test-only server in `e2e/flask_test_server.py` with the current backend mounted read-only at `/backend`. It imports the backend's existing provider doubles, while keeping the real Flask API, background worker, DAG, database, scores, reports, and comparison code. It is excluded from the production Docker context and never imported by application code. Set `RUN_FLASK_TEST_E2E=1` and point `APP_URL` to a frontend proxied to that test server. `TEST_PARTIAL=1` on the test server simulates an unavailable ChatGPT branch; pair with `EXPECT_PARTIAL=1` in Playwright. `AUDIT_FULL_UI=1` scans the populated views for WCAG A/AA violations. Exact reproduction commands are in `e2e/README.md`.

## Architecture and decisions

- `core/models.ts` mirrors snake_case API contracts. `core/api.ts` owns HttpClient calls, 202 acceptance validation, and status polling. No component calls business HTTP directly.
- Page state uses local signals. Brand and run containers provide scoped stores to child routes. Public configuration is shared with a single cached finite request. There is no persisted frontend business state or redundant global state library.
- Polling uses `expand`: wait 2.5 seconds after the last response before requesting again. Slow requests are never cancelled by a periodic timer. Terminal statuses stop the stream. All component/store subscriptions bind to Angular destruction. Transient status failures retry twice with bounded backoff, then offer manual recovery.
- A fresh idempotency key identifies each analysis submission; retrying the same failed request retains its key. A changed request gets a new key. A backend `run_in_progress` response opens the existing run.
- Opportunity scores and comparisons come directly from Flask. The frontend formats numbers and computes display counts/relative chart widths only. It does not implement a second scoring formula.
- A dash means unknown or unavailable; a displayed 0 is measured zero. Failed/skipped observations never become absent brand visibility. Partial results show failed stages and retained evidence.
- Custom SCSS uses a small set of color, spacing, typography, and component rules. Tables scroll inside their panels on narrow layouts. Navigation becomes a dismissible drawer below 768px. Reduced-motion preferences are respected. Native controls and visible focus indicators support keyboard use.
- The visual product is deliberately evidence-oriented: portfolio → brand context → server pipeline → report → opportunities → content briefs → historical comparison.

## Diagnosing failures

`core/errors.ts` centrally classifies HTTP failures and uses safe user-facing messages. An interceptor adds `X-Request-ID`; error states show the server's support reference. Server/network HTTP failures and unexpected runtime failures are posted to `/api/client-errors` through a separate HttpBackend client so reporting cannot recurse. Reports are throttled. Browser error records contain a generic description, route, request/run ID, and timestamp; provider secrets, form values, raw server errors, and full reports are excluded. Run pages expose the backend run ID and correlation ID.

## Review and ownership

Codex implemented and verified this frontend. The assessment and `docs/api-contract.md` were read in full. Backend files were inspected read-only. Coordination notes are in `INTEGRATION_ISSUES.md`; the final requirement review and reproducible issues are in `ASSESSMENT_REVIEW.md`. Root documentation, Compose, and backend edits belong to the backend workstream.

Trade-offs: no account/authentication system is invented beyond the assessment contract; configuration is public and compile-time; comparison dropdowns list finished runs on the current paginated history page, while the default comparison uses the latest two completed runs across all history. Future work: broader screen-reader testing, authenticated workspaces, and server-sent progress events if supported by the backend.
