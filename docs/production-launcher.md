# V.I.O.L.E.T. Production Launcher

`PROD-LAUNCHER-UX1/PF1` replaces the daily production launcher experience with
an Electron UI backed by the Python control plane.

The launcher starts the existing runtime entry point:

```powershell
python run.py
```

It never passes `--debug`.

## Production Profile

Production startup is no longer treated as a mode of the development `.env`.
The launcher uses a separate local profile:

```text
.local_manifests/production_launcher/production-profile.json
```

`.local_manifests/` is gitignored. Do not commit this file; it may contain
machine-specific paths and DB credentials.

The profile is the production source of truth for launcher startup:

- `profile_id`
- `env=production`
- `repo_root`
- `python`
- `app_port`
- `storage_root`
- DB host, port, name, user, optional private DB credential
- `safe_startup=true`
- startup automation flags set to false

The existing development `.env` and local `data/settings.json` may be read for
best-effort local bootstrap. Values inferred from those local records are
written only to the ignored production profile and are not returned in public
launcher JSON. The launcher does not modify `.env` and does not require
`VIOLET_ENV=production` or `VIOLET_STORAGE_ROOT` in `.env`.

If a value truly cannot be inferred, the Electron UI shows `Profile Incomplete`
and asks the operator to select or enter only the missing value. `Create /
Repair Production Profile` also resets profile invariants: `env=production`,
`safe_startup=true`, and startup automation/destructive flags disabled.

## Start The Launcher

Daily use should be through the packaged Windows executable. Build it once from
the canonical repository checkout:

```powershell
cd launcher
npm install
npm run package
```

Then double-click:

```text
launcher\dist\V.I.O.L.E.T. Production Launcher.exe
```

The fallback development entrypoint remains:

```text
scripts\start_violet_production_launcher.cmd
```

The command starts the Electron launcher from:

```text
launcher/
```

It starts the Electron launcher from `launcher/` with `npm start`; it is useful
for development or troubleshooting but is no longer the preferred production
entrypoint.

If npm or Electron downloads hang behind the local proxy, create a local ignored
launcher npm config:

```powershell
.\scripts\setup_launcher_npm_proxy.ps1 -Proxy http://127.0.0.1:7897
```

Clear it with:

```powershell
.\scripts\setup_launcher_npm_proxy.ps1 -Clear
```

The generated `launcher/.npmrc` is ignored and must not be committed.

## Controller Commands

Electron calls the Python controller and does not duplicate safety decisions in
JavaScript:

```powershell
python scripts\violet_production_control.py profile-status --profile production-default --json
python scripts\violet_production_control.py profile-discover --profile production-default --json
python scripts\violet_production_control.py profile-init --profile production-default --json
python scripts\violet_production_control.py profile-repair --profile production-default --json
python scripts\violet_production_control.py profile-update --profile production-default --json
python scripts\violet_production_control.py preflight --profile production-default --json
python scripts\violet_production_control.py test-db --profile production-default --json
python scripts\violet_production_control.py start --profile production-default --json
python scripts\violet_production_control.py status --profile production-default --json
python scripts\violet_production_control.py stop --profile production-default --json
```

## Electron UI

The main screen shows:

- Production profile status.
- Environment, storage, database, schema, port, safety flags, startup policy,
  and health checklist groups.
- `Create / Repair Production Profile`.
- `Select Production Storage Root`.
- `Test Database`.
- `Run Preflight`.
- `Start Production`.
- `Open Browser`.
- `Stop`.
- `Restart`.
- `Copy Diagnostic Summary`.

Raw JSON is not shown on the main screen. `Show Advanced Diagnostics` is
collapsed by default and contains only public-safe JSON.

Observed #121 blockers are mapped to user-facing actions:

- `violet_env_production`: development `.env` is not used for production;
  create or repair the production profile.
- `storage_root_explicit`: production profile is missing storage root.
- `production_storage_root_shape`: production storage root is invalid or unsafe.
- `db_readonly_reachable`: DB check is skipped until profile and storage gates
  pass.
- `no_startup_mutation_automation`: production profile must disable startup
  automation flags.

## Startup Environment

The production child process environment is built from a clean allowlisted
baseline plus the profile:

```text
VIOLET_ENV=production
BLOMBOORU_DEBUG=false
VIOLET_STORAGE_ROOT=<profile storage root>
APP_PORT=<profile port>
VIOLET_PRODUCTION_PROFILE_ACTIVE=true
VIOLET_SKIP_DOTENV=1
VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP=true
DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED=false
AI_AUTO_TAG_AFTER_IMPORT=false
CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT=false
TAG_TRANSLATION_AUTO_ENABLED=false
TAG_TRANSLATION_BACKGROUND_ENABLED=false
VIOLET_ALLOW_DESTRUCTIVE_E2E=false
VIOLET_RUN_REAL_E2E=false
```

Profile updates that include private DB values are sent from Electron to the
Python controller through stdin JSON, not command-line argv.

In this profile-active safe-startup mode, backend DB settings prefer the profile
environment over storage `settings.json`. This override is deliberately narrow
and does not affect normal development or test startup.

`run.py` and backend config both honor `VIOLET_SKIP_DOTENV=1`, so production
profile startup does not reload the development `.env` after the controller has
constructed the production child environment.

## Startup Write Policy

Normal application startup can perform maintenance writes such as schema
create/migrate, upload cleanup, stale job recovery, tag translation seeding, and
background worker startup.

The production launcher safe-start path blocks those startup writes. It also
requires production `data/settings.json` to already contain `secret_key`, so
importing settings will not write a generated secret before the safe-start
guard.

This phase does not add a launcher path for schema migration, import, tagging,
localization, sync, provider calls, SourceConcept, Entity bridge, cleanup,
delete, reset, drop, or truncate.

## Health And Stop Safety

`GET /api/health` remains auth-exempt and public-safe. It now checks required
core tables and required core columns for:

- `blombooru_media`
- `blombooru_tags`
- `blombooru_media_tags`
- `blombooru_users`

Start success verifies:

- launched PID still exists;
- launcher state still verifies process identity;
- health comes from expected V.I.O.L.E.T. production app;
- health reports DB reachable, schema compatible, storage configured, and
  debug disabled;
- port owner matches when owner detection is available.

Managed but unhealthy processes are reported as `Unhealthy`, not `Running`.
Stop refuses unknown or unverified processes. On POSIX-like platforms, an open
target port with unknown owner fails closed instead of being marked managed.

Launcher stop uses a bounded graceful shutdown and may force only the verified
managed process if the process identity still matches. The user emergency stop
for a launcher-owned process is:

```powershell
taskkill /PID <launcher-managed-pid> /T
```

Use `/F` only if the graceful command does not exit and the PID is still the
same launcher-managed process. Do not kill unknown Python or Node processes.

## Direct Safe-Start Notes

Do not document or use `--log-config none`; it is not a valid `uvicorn` CLI
value. Prefer `python run.py` through the production launcher profile, or use
normal `uvicorn` log-level options only when running a temporary manual command.

The shutdown hang observed after a temporary direct startup was investigated in
this phase. Safe-start mode skips periodic/background tasks. Normal startup
periodic tasks are now tracked and cancelled during FastAPI shutdown. A
temporary safe-start run on a non-production test port reached `/api/health`,
accepted Ctrl+Break, exited without force, and released the port.

## Manual Acceptance

This phase is not merge-ready until real manual acceptance is completed from
the canonical production checkout:

1. Launch Electron from the canonical repo.
2. Confirm missing or incomplete profile does not ask to edit development `.env`.
3. Create or repair the production profile.
4. Select production storage root.
5. Run preflight until green.
6. Start production.
7. Confirm health OK.
8. Open browser.
9. Stop production.
10. Confirm port release and restart.

Until that happens:

```json
{
  "manual_acceptance_required_before_merge": true,
  "manual_acceptance_completed": false,
  "merge_allowed": false
}
```
