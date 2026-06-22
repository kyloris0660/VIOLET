# PROD-LAUNCHER-UX1/PF1: Electron Production Launcher With Separate Production Profile

## Summary

This repair phase separates production startup from the development `.env` and
adds an Electron launcher as the primary daily production UI.

The phase is implementation-complete but not merge-ready until real manual
acceptance is completed from the canonical production checkout.

```json
{
  "manual_acceptance_required_before_merge": true,
  "manual_acceptance_completed": false,
  "merge_allowed": false
}
```

## Production / Development Separation

Production now uses a local ignored profile:

```text
.local_manifests/production_launcher/production-profile.json
```

The launcher does not modify development `.env`, does not require
`VIOLET_ENV=production` in development `.env`, and does not require
`VIOLET_STORAGE_ROOT` in development `.env`.

The profile builds the child process environment for production startup:

```text
VIOLET_ENV=production
BLOMBOORU_DEBUG=false
VIOLET_STORAGE_ROOT=<profile storage root>
APP_PORT=<profile app port>
VIOLET_PRODUCTION_PROFILE_ACTIVE=true
VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP=true
automation flags=false
destructive E2E flags=false
```

The backend DB settings override is deliberately narrow: only profile-active
production safe-startup prefers profile-provided DB environment values over
storage settings. Normal development and test startup behavior is unchanged.

## Profile Discovery

Safe inferred values:

- Profile id: `production-default`
- Environment: `production`
- Repo identity for the current checkout
- Production Python candidate
- App port default or safe `.env` default
- DB host, port, name, and user defaults

Not inferred:

- Production storage root
- DB password, if the production DB requires one

The reports available in the repo are public-safe and path-redacted, so this
phase does not invent a private storage root. The UI shows `Profile Incomplete`
until the operator selects the storage root.

## Electron UI Behavior

Primary entrypoint:

```text
scripts\start_violet_production_launcher.cmd
```

Electron files:

```text
launcher/package.json
launcher/main.js
launcher/preload.js
launcher/renderer/index.html
launcher/renderer/renderer.js
launcher/renderer/styles.css
```

Main screen states:

- No Production Profile
- Profile Incomplete
- Ready
- Blocked
- Starting
- Running
- Unhealthy
- Stopped
- Error

Main screen controls:

- Create / Repair Production Profile
- Select Production Storage Root
- Save Profile Fields
- Test Database
- Run Preflight
- Start Production
- Open Browser
- Stop
- Restart
- Copy Diagnostic Summary

Raw JSON is hidden from the main screen. Advanced diagnostics are collapsed by
default and contain only public-safe JSON.

## Current Blocker Mapping

Observed #121 blockers are mapped to user-facing checklist rows:

- `violet_env_production`: Development `.env` is not used for production.
  Create or repair the production profile.
- `storage_root_explicit`: Production profile is missing storage root.
- `production_storage_root_shape`: Production storage root is invalid or unsafe.
- `db_readonly_reachable`: Database check is skipped until production profile
  and storage gates pass.
- `no_startup_mutation_automation`: Production profile must disable startup
  automation flags.

## Controller Changes

New or updated commands:

```powershell
python scripts\violet_production_control.py profile-status --profile production-default --json
python scripts\violet_production_control.py profile-discover --profile production-default --json
python scripts\violet_production_control.py profile-init --profile production-default --json
python scripts\violet_production_control.py profile-update --profile production-default --json
python scripts\violet_production_control.py preflight --profile production-default --json
python scripts\violet_production_control.py test-db --profile production-default --json
python scripts\violet_production_control.py start --profile production-default --json
python scripts\violet_production_control.py status --profile production-default --json
python scripts\violet_production_control.py stop --profile production-default --json
```

Safety preserved or improved:

- `/api/health` remains auth-exempt and public-safe.
- Health checks required core columns, not only table names.
- Start success verifies launched PID, process identity, and health identity.
- Managed unhealthy processes show `Unhealthy`.
- Stop refuses unknown or unverified processes.
- Stale start locks remain recoverable.
- Public JSON excludes raw log tail.
- Malformed app and DB ports fail preflight.
- Safe startup requires initialized settings with `secret_key` before launch.
- POSIX unknown target port owner fails closed.

## Validation

Passed:

```powershell
python scripts\check_python_env.py --expected-python <canonical venv python>
python -m py_compile scripts\violet_production_control.py backend\app\routes\health.py backend\app\main.py backend\app\auth_middleware.py backend\app\config.py scripts\violet_production_launcher.py
python -m pytest tests\test_production_launcher_control.py tests\test_phase_contracts.py -v
cd launcher
npm install
npm test
npm run lint
npm audit --json
```

Results:

- Python env preflight: PASS
- Py compile: PASS
- Focused Python tests: `195 passed`
- Electron install: PASS
- Electron test: PASS
- Electron lint: PASS
- Electron audit: 0 vulnerabilities

Manual acceptance:

- Not run by CodeX from this worktree.
- Required before merge from the real canonical production checkout.

## Merge Recommendation

Do not merge until the user completes real Electron launcher acceptance from
the canonical production checkout.

Recommended status:

```text
manual_acceptance_required_before_merge
```

## Artifact Lifecycle

- `scripts/violet_production_control.py`: reusable validation/safety tool.
- `launcher/`: durable production launcher UI.
- `backend/app/routes/health.py`: durable production health endpoint.
- `backend/app/config.py`: durable narrow profile-active config behavior.
- `.local_manifests/production_launcher/production-profile.json`: local/private,
  ignored, not committed.
- `docs/production-launcher.md` and this report family: public report/handoff.

## Safety Confirmation

- No push to `main`.
- No merge.
- No development `.env` modification.
- No source/iCloud mutation.
- No cleanup/delete/reset/drop/truncate.
- No DB import.
- No classification, AI tagging, localization, sync, provider, SourceConcept,
  Entity bridge, or LLM run.
