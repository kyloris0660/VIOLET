# V.I.O.L.E.T. Production Launcher

`PROD-LAUNCHER-MVP` adds a Windows-first visual launcher for the local
production library. It is a safety wrapper around the existing runtime entry
point:

```powershell
python run.py
```

The launcher does not use `--debug`.

## Start The Launcher

From the canonical repository checkout, double-click:

```text
scripts\start_violet_production_launcher.cmd
```

The command file prefers the canonical project venv:

```text
venv\Scripts\python.exe
```

If that venv is not present, it falls back to `python`, but the production
preflight will still block startup unless the running Python matches the
configured production Python gate.

## Visual Controls

The Tkinter launcher shows:

- Status: `Stopped`, `Starting`, `Running`, `Stopping`, or `Error`.
- Environment, port, URL, DB name, storage root status.
- Last health check, last error, and recent local log tail.

Buttons:

- `Preflight`: runs the production safety gate without starting the server.
- `Start Production`: starts `python run.py` with `VIOLET_ENV=production`.
- `Open Browser`: opens the resolved local URL.
- `Stop`: stops only the launcher-managed V.I.O.L.E.T. process.
- `Restart`: stop, then start.
- `Copy Diagnostic Summary`: copies public-safe `status --json` output.

## Production Gates

Startup is blocked unless all hard gates pass:

- The launcher is running from the canonical repository root, not a worktree.
- `VIOLET_ENV=production`.
- `BLOMBOORU_DEBUG` is not true and `--debug` is not passed.
- The running Python is the configured production/canonical venv Python.
- `.env` exists and is readable.
- `VIOLET_STORAGE_ROOT` is explicit, absolute, production-shaped, and not under
  the repo, temporary agent directories, iCloud/source paths, test paths, or
  fixture paths.
- Initialized `data/settings.json` exists under the configured storage root.
- DB settings are present.
- DB is reachable through a read-only `SELECT 1` check.
- Configured DB port is either absent/defaulted or a valid integer port;
  malformed `POSTGRES_PORT` or settings JSON DB port values block startup.
- `APP_PORT` is configured or defaulted to a valid port; malformed values such
  as `APP_PORT=abc` are reported as preflight failures instead of crashing the
  launcher.
- The target port is free or owned by the launcher-managed process state.
- No stale PID claims a running process.
- Startup automation flags for import/tagging/localization/sync are not enabled.
- `VIOLET_ALLOW_DESTRUCTIVE_E2E` and real E2E flags are not enabled.
- The launcher startup write policy is explicit.

## Startup Write Policy

Normal application startup through plain `python run.py` can perform maintenance
writes before serving requests:

- DB engine/schema initialization through `init_db()`, including
  `create_all()` and `check_and_migrate_schema()`.
- Upload temp chunk cleanup.
- Stale scan, AI-tagging, tag-translation, and classification job recovery.
- Static tag translation seeding.
- Periodic upload temp cleanup task creation.
- Background tag translation worker startup if its normal app settings enable it.

The production launcher does not claim that normal app startup is write-free.
Instead, the launcher child process forces:

```text
VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP=true
```

When this flag is present in production, `backend/app/main.py` initializes the
DB engine only and skips schema create/migrate, upload cleanup, stale job
recovery, translation seeding, periodic cleanup, and background worker startup.
The launcher also forces import/tagging/localization/sync automation flags and
destructive E2E flags to `false` in the child environment.

Public status/report output includes:

```json
{
  "startup_write_policy": {
    "normal_startup_maintenance_documented": true,
    "schema_migration_allowed": false,
    "destructive_cleanup_allowed": false,
    "import_tagging_sync_jobs_allowed": false,
    "operator_intent_required_for_startup_maintenance": true
  }
}
```

This MVP does not add an operator-approved path for allowing startup schema
migration or destructive cleanup from the launcher.

The launcher state and log are local ignored artifacts under:

```text
.local_manifests/production_launcher/
```

Do not commit files from that directory.

## Safe Stop

`Stop` reads the launcher state file and verifies:

- state was created by `violet_production_launcher`;
- state repo root and port match the current launcher config;
- PID still exists;
- PID create time matches the launcher state and is not older than the recorded
  start time;
- process executable or command line matches the configured production Python;
- process command line looks like V.I.O.L.E.T. `run.py` or uvicorn runtime;
- process is not a debug server.
- target port owner matches the state PID when port-owner detection is available.

On Windows, process create time is used as a strong stale-PID and PID-reuse
guard. On POSIX-like platforms where create time is unavailable, stop does not
fail only because create time is missing; it still requires the other identity
checks to match, and ambiguous identity is refused.

If no launcher state exists and the port is occupied, the launcher refuses to
stop anything. It never kills an unknown process merely because it owns the
target port.

The stop path first asks the verified process to exit, waits for shutdown, then
uses force only if the same verified process is still present. It clears stale
state only when the state PID is no longer running.

## Start Lock

Start and Restart are serialized in both the Tkinter UI and controller. The
controller start lock records PID and timestamp. If a previous launcher crashed,
dead-PID, expired-unverified, or malformed locks are reclaimed; active locks
return `start_already_in_progress`.

## Health And Diagnostics

The app exposes:

```text
GET /api/health
```

The route is auth-exempt so launcher polling works when
`BLOMBOORU_REQUIRE_AUTH=true`. The response is public-safe and does not include
storage paths, source paths, filenames, DB URLs, passwords, tokens, or API keys.
Fields include:

```json
{
  "ok": true,
  "app_name": "V.I.O.L.E.T.",
  "version": "1.41.0",
  "env": "production",
  "db_reachable": true,
  "schema_compatible": true,
  "schema_status": "compatible",
  "storage_configured": true,
  "debug": false
}
```

`ok=true` requires DB reachability, read-only core schema compatibility,
configured storage, and debug disabled. Health does not run migrations or
startup maintenance writes.

CLI diagnostics:

```powershell
python scripts\violet_production_control.py status --json
```

Example public-safe shape:

```json
{
  "running": true,
  "managed_by_launcher": true,
  "port": 8000,
  "url": "http://127.0.0.1:8000",
  "env": "production",
  "debug": false,
  "db_reachable": true,
  "schema_compatible": true,
  "schema_status": "compatible",
  "db_port_valid": true,
  "health_ok": true
}
```

Public CLI JSON does not include raw log tail. The recent log tail is shown only
inside the local Tkinter UI and is not serialized into public diagnostics or
reports.

## Known Limitations

- This MVP is Windows-first and uses Tkinter from the Python standard library.
- It does not add a Windows service, tray app, installer, scheduled task, or
  production auto-start.
- It does not add an approved path for running schema migrations from the
  launcher. Launcher safe startup blocks those startup maintenance writes.
- The launcher phase itself does not run imports, tagging, localization, sync,
  provider calls, SourceConcept, or Entity bridge operations.
- Full production start/stop smoke should be run only from the canonical repo
  after the preflight passes.
