# Frontend configuration

No `.env` loader is required. Angular compiles public configuration from `src/environments/environment.ts`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `apiBaseUrl` | `/api` | Same-origin Flask API prefix. Keep relative for Docker routing. |
| `pollIntervalMs` | `2500` | Delay after each status response while a run is queued/running. |

Local development proxy target: `http://127.0.0.1:5000` in `proxy.conf.json`.
Production reverse proxy target: `http://backend:5000` in `nginx.conf`.
Provider keys are configured only on Flask. No secrets, auth tokens, or provider responses are saved to browser storage.

Browser test settings, used only by Playwright:

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `APP_URL` | `http://127.0.0.1:4200` | Test a separately running frontend, including a Compose deployment. |
| `RUN_PERSISTED_E2E` | unset / disabled | Enable read-only browser verification of already saved reports. |
| `PERSISTED_BRAND_ID` / `PERSISTED_RUN_ID` | first saved brand / latest run | Optionally choose existing records for read-only verification. |
| `AUDIT_FULL_UI` | unset / disabled | Run accessibility scans on the populated saved-report views. |

Routine tests never create live analyses. `e2e/live.spec.ts` is disabled unless a developer deliberately supplies `ALLOW_ANALYSIS_CREATION=1` plus its provider-mode flag. This explicit opt-in is for separately authorized lifecycle verification only. OpenAI integration and strict usage limits are owned by the backend. Do not enable live creation during Angular development, builds, tests, or UI checks for this assessment.

