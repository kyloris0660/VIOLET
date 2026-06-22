# PROD-LAUNCHER-UX1/PF1: Electron Production Launcher With Separate Production Profile

## Summary

This repair round keeps PR #122 on the same branch and fixes the current-head
reviewer P1/P2/P3 findings. Production startup is now isolated from
development `.env`, the production profile can be bootstrapped from local
durable evidence, the Electron launcher is zh-CN first, and the launcher can be
packaged as a double-clickable Windows portable executable.

The phase remains implementation-complete but not merge-ready until real user
manual acceptance is completed from the canonical production checkout.

```json
{
  "manual_acceptance_required_before_merge": true,
  "manual_acceptance_completed": false,
  "merge_allowed": false
}
```

## Production / Development Separation

Production uses a local ignored profile:

```text
.local_manifests/production_launcher/production-profile.json
```

The launcher does not modify development `.env`, does not require
`VIOLET_ENV=production` in development `.env`, and does not require
`VIOLET_STORAGE_ROOT` in development `.env`.

Profile launch now sets `VIOLET_SKIP_DOTENV=1`. Both `run.py` and backend
config honor that flag, so the development `.env` is not reloaded after the
controller has constructed the production child environment.

In profile mode the child environment comes from a clean allowlisted baseline
plus profile values. Arbitrary development `BLOMBOORU_*`, `VIOLET_*`, provider,
LLM, Redis, sync, test, and E2E variables are not inherited.

## Profile Bootstrap

The controller now supports:

```powershell
python scripts\violet_production_control.py profile-discover --profile production-default --json
python scripts\violet_production_control.py profile-init --profile production-default --json
python scripts\violet_production_control.py profile-repair --profile production-default --json
python scripts\violet_production_control.py profile-status --profile production-default --json
```

`profile-discover` and `profile-repair` inspect local `.env`,
`data/settings.json`, `.local_manifests`, and public reports. Values inferred
from local records are written only to the ignored profile and are not printed
in public JSON.

This CodeX run created the ignored production profile in the canonical checkout.
Public `profile-status` reports `ready`, and public preflight reports `passed`.
The profile file itself was not committed and must stay local.

If a profile is hand-edited incorrectly, repair now forces:

- `profile_id=production-default`
- `env=production`
- `safe_startup=true`
- startup automation and destructive flags disabled

If a profile file exists with a mismatched embedded `profile_id`, status shows
`Profile Error`, preflight cannot pass, and start cannot run until repair.

## Electron UI Behavior

The Electron UI remains the primary production launcher. It keeps raw JSON out
of the main screen, keeps Advanced Diagnostics collapsed by default, preserves
missing/incomplete/profile-error states over generic runtime states, and maps
blocked preflight gates to checklist rows.

Visible daily-use UI labels are zh-CN first, including status badges, summary
labels, action buttons, checklist group labels, and common blocker messages.
English technical identifiers remain available inside Advanced Diagnostics.
Copy Diagnostic Summary now copies and refreshes advanced diagnostics without
replacing the current status badge or checklist.

Observed blocker mapping remains:

- `violet_env_production`: Development `.env` is not used for production.
  Create or repair the production profile.
- `storage_root_explicit`: Production profile is missing storage root.
- `production_storage_root_shape`: Production storage root is invalid or unsafe.
- `db_readonly_reachable`: Database check is skipped until production profile
  and storage gates pass.
- `no_startup_mutation_automation`: Production profile must disable startup
  automation flags.

The renderer now calls `profile-repair` for `Create / Repair Production
Profile`, preserves existing DB users, and only submits changed non-empty form
fields.

## Windows Executable

Daily launch should use the portable executable:

```text
launcher/dist/V.I.O.L.E.T. Production Launcher.exe
```

Build command:

```powershell
cd launcher
npm install
npm run package
```

`scripts\start_violet_production_launcher.cmd` remains as a fallback and
development entrypoint, not the preferred daily production path.

## NPM / Electron Proxy

Added:

```text
scripts/setup_launcher_npm_proxy.ps1
```

Usage:

```powershell
.\scripts\setup_launcher_npm_proxy.ps1 -Proxy http://127.0.0.1:7897
.\scripts\setup_launcher_npm_proxy.ps1 -Clear
```

The script writes only ignored `launcher/.npmrc`; personal proxy values are not
committed. Packaging initially stalled before the proxy file existed. After
writing the local proxy/mirror config, `npm install` and `npm run package`
completed successfully.

## Shutdown Investigation

The invalid `--log-config none` command is not documented. `run.py` accepts
only `--debug`; temporary direct uvicorn commands should use ordinary log-level
options or omit log configuration.

The likely shutdown hang risk was an untracked periodic startup task. Safe-start
mode already skips periodic/background jobs; this round also tracks normal
startup background tasks and cancels them during FastAPI shutdown.

Validation performed:

- temporary safe-start service launched on a non-production test port;
- `/api/health` returned 200 from V.I.O.L.E.T.;
- Ctrl+Break shutdown exited without force;
- follow-up active-server audit reported no occupied V.I.O.L.E.T. ports.

Launcher stop remains bounded and refuses unknown processes. If an emergency
stop is needed, the operator should target only the verified launcher-managed
PID:

```powershell
taskkill /PID <launcher-managed-pid> /T
```

Use `/F` only after confirming the same PID is still the launcher-managed
process.

## Reviewer Fix Round

Current-head P1/P2 findings fixed:

- P1: `run.py` and backend config now skip development `.env` when
  `VIOLET_SKIP_DOTENV=1`.
- P2: mismatched profile IDs fail closed for status, preflight, and start.
- P2: forward-slash Windows paths are redacted without corrupting `http://`
  URLs.
- P2: profile repair resets invalid production invariants.
- P2: Start now returns `ok=false`, `status=unhealthy` for an existing
  launcher-managed but unhealthy process; healthy existing managed processes
  still return success.
- P2: when a newly launched process fails post-start identity or health
  verification, the launcher attempts bounded cleanup of that exact launch and
  clears matching launcher state.
- P2: controller stderr redacts production profile path suffixes with mixed
  separators.
- P2: Electron visible UI is zh-CN first.
- P3: Copy Diagnostic Summary no longer clears the current UI state.
- Previous P1/P2 fixes are preserved: clean environment allowlist, DB user
  preservation, no-profile state precedence, structured controller errors,
  stderr redaction, stdin JSON profile updates, POSIX fail-closed ownership, and
  validation-status contract requirements.

## Validation

Passed during this round:

```powershell
python -m pytest tests\test_production_launcher_control.py tests\test_phase_contracts.py -q
cd launcher
npm install
npm test
npm run lint
npm audit --json
npm run package
```

Observed results:

- Focused Python tests: `220 passed`
- Electron tests: passed
- Electron lint: passed
- Electron audit: 0 vulnerabilities
- Electron package: passed
- Canonical ignored profile status: ready
- Canonical public preflight: passed
- Direct safe-start shutdown validation: passed

The full required validation command set is recorded in the JSON summary and
must remain green before final handoff.

## Safety Confirmation

- No push to `main`.
- No merge.
- No development `.env` modification.
- No source/iCloud/media mutation.
- No cleanup/delete/reset/drop/truncate.
- No DB import.
- No DB migration.
- No classification, AI tagging, localization, sync, provider, SourceConcept,
  Entity bridge, or LLM run.
- Canonical writes were limited to the ignored local production profile.
- Build outputs and local `.npmrc` are ignored and not committed.

## Merge Recommendation

Do not merge until the user completes real Electron launcher acceptance from
the canonical production checkout.

Recommended status:

```text
manual_acceptance_required_before_merge
```

## Artifact Lifecycle

- `scripts/violet_production_control.py`: reusable validation/safety tool.
- `run.py` and `backend/app/config.py`: durable production profile startup
  guard.
- `backend/app/main.py`: durable startup/shutdown safety.
- `launcher/`: durable production launcher UI.
- `scripts/setup_launcher_npm_proxy.ps1`: reusable local setup helper.
- `.local_manifests/production_launcher/production-profile.json`: local/private,
  ignored, not committed.
- `launcher/dist/`: local build output, ignored, not committed.
- `docs/production-launcher.md` and this report family: public report/handoff.
