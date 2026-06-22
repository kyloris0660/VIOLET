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

In profile mode the controller now starts from a clean allowlisted process
baseline instead of inheriting arbitrary development process variables. Profile
startup does not inherit development-only `BLOMBOORU_*`, `VIOLET_*`, provider,
LLM, Redis, sync, test, or E2E variables unless they are explicitly generated
by the production profile path and forced to safe values.

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
- Private DB credential, if the production DB requires one

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

Profile status takes precedence over runtime status on first launch: a missing
or incomplete profile remains visible as `No Production Profile` or
`Profile Incomplete` and is not overwritten by generic `Stopped`.

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
- Safe startup requires already-initialized production settings before launch.
- POSIX port owner lookup uses `lsof` then `ss` where available, and unknown
  target port owners still fail closed.
- Controller failures with empty stdout, missing Python, or pre-JSON crashes
  return structured public-safe errors instead of `{}`.
- Controller stderr is redacted and capped before it reaches renderer
  diagnostics.
- Private DB credential profile updates use stdin JSON, not command-line argv.
- Existing custom DB users are preserved by profile status/discovery and the
  renderer only sends changed non-empty form fields.

## Reviewer Fix Round

Current-head P1/P2 findings fixed in PR #122:

- P1: profile launches no longer inherit the full development process
  environment; they use an allowlisted production baseline plus profile values.
- P2: profile status/discovery expose public-safe DB user and renderer preserves
  custom users such as `violet_prod`.
- P2: missing/incomplete profile state is not overwritten by `Stopped`.
- P2: controller empty stdout, invalid Python, and crash-before-JSON paths
  surface actionable `Error` payloads.
- P2: controller stderr is path/private-value redacted and length capped.
- P2: private DB credential profile updates use stdin JSON rather than argv.
- P2: POSIX port owner lookup was implemented with `lsof`/`ss`; unknown owners
  remain fail-closed.
- P2: the UX1 phase contract now requires Python and Electron validation status
  to be `passed`.

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
- Focused Python tests: `204 passed`
- Electron install: PASS
- Electron test: PASS (`launcher contract checks passed`; `controller runner
  tests passed`; `renderer behavior tests passed`)
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
