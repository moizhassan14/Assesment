# Browser verification

All provider doubles and HTTP route fixtures in this directory are automated-test tools. They are excluded from the production Docker image; application code never imports them.

## State and accessibility checks

With Node 24, Chrome, and frontend dependencies installed:

```sh
npm run test:e2e -- states.spec.ts accessibility.spec.ts
```

Playwright starts/reuses Angular on port 4200. These tests check first-time onboarding, validation, responsive layouts, drawer navigation, API recovery, missing routes, and automated WCAG A/AA rules.

## Saved reports: preferred, no provider calls

Point `APP_URL` at an existing frontend/backend deployment, then:

```powershell
$env:APP_URL='http://127.0.0.1:8080'
$env:RUN_PERSISTED_E2E='1'
$env:AUDIT_FULL_UI='1'
npm run test:e2e -- saved-reports.spec.ts
```

This opens existing reports, checks all seven data views at four screen widths, filters opportunities, copies saved briefs, and reads existing history/comparisons. It blocks browser API mutations and asserts that the saved run count is unchanged. It never starts analyses, calls providers, or spends OpenAI tokens. Optional `PERSISTED_BRAND_ID` and `PERSISTED_RUN_ID` select existing records.

## Separately authorized lifecycle verification

**Disabled for routine assessment development and UI verification.** The OpenAI key belongs to someone else; the user's instruction is to minimize usage and use persisted results. Do not run this section unless the user separately authorizes analysis creation. The backend owns provider integration and strict usage limits.

Start the full stack using the root README. Then, in PowerShell:

```powershell
$env:APP_URL='http://127.0.0.1:8080'
$env:RUN_LIVE_E2E='1'
$env:ALLOW_ANALYSIS_CREATION='1'
npm run test:e2e -- live.spec.ts
```

This creates Linear (`linear.app`) if missing, runs discovery using actual provider credentials/budgets, checks reports and filters, copies a brief, remeasures, and compares saved runs. It deliberately fails if no live visibility evidence is obtained. Account verification/billing problems are genuine blockers, not bypassed with test data.

## Real Flask with automated-test providers

From the repository root, using PowerShell (replace the source paths with your absolute checkout paths):

```powershell
docker build --target test -t signal-backend-review:local backend
docker run -d --name signal-flask-browser-tests -p 127.0.0.1:5501:5000 `
  --mount "type=bind,source=$PWD\backend,target=/backend,readonly" `
  --mount "type=bind,source=$PWD\frontend\e2e,target=/browser-tests,readonly" `
  signal-backend-review:local python /browser-tests/flask_test_server.py
cd frontend
node node_modules/@angular/cli/bin/ng.js serve --host 127.0.0.1 --port 4202 --proxy-config e2e/proxy.conf.json
```

In another terminal in `frontend/`:

```powershell
$env:APP_URL='http://127.0.0.1:4202'
$env:RUN_FLASK_TEST_E2E='1'
$env:ALLOW_ANALYSIS_CREATION='1'
$env:AUDIT_FULL_UI='1'
npm run test:e2e -- live.spec.ts
```

To verify partial success, create a fresh test container with `-e TEST_PARTIAL=1` and run the same browser command with `$env:EXPECT_PARTIAL='1'`. Keep the test database isolated from real reports. The container stores its test DB in its own writable `/data`; backend source mounts are read-only.

To reuse already saved test reports without creating more analyses, leave `ALLOW_ANALYSIS_CREATION` unset and use `RUN_PERSISTED_E2E=1` with `saved-reports.spec.ts` instead.

Artifacts: screenshots at 1440, 1280, 768, and 390 pixels, report/comparison JSON, and accessibility findings under `test-results/`; HTML output under `playwright-report/`. These are ignored by Git. Test-provider artifacts are prefixed `flask-test`; actual external-provider artifacts are prefixed `live`.

## Limits

Automated accessibility checks supplement keyboard and visual checks; they do not establish full screen-reader conformance. Chrome is the executed browser; other browsers require separate verification. Successful provider-fixture tests establish integration behavior but cannot verify actual DataForSEO account permissions, availability, or live response coverage.
