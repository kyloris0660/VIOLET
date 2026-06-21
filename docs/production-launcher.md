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
- `APP_PORT` is configured or defaulted to a valid port.
- The target port is free or owned by the launcher-managed process state.
- No stale PID claims a running process.
- Startup automation flags for import/tagging/localization/sync are not enabled.

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
- process command line looks like V.I.O.L.E.T. `run.py` or uvicorn runtime;
- process is not a debug server.

If no launcher state exists and the port is occupied, the launcher refuses to
stop anything. It never kills an unknown process merely because it owns the
target port.

The stop path first asks the verified process to exit, waits for shutdown, then
uses force only if the same verified process is still present. It clears stale
state only when the state PID is no longer running.

## Health And Diagnostics

The app exposes:

```text
GET /api/health
```

The response is public-safe and does not include storage paths, source paths,
filenames, DB URLs, passwords, tokens, or API keys. Fields include:

```json
{
  "ok": true,
  "app_name": "V.I.O.L.E.T.",
  "version": "1.41.0",
  "env": "production",
  "db_reachable": true,
  "storage_configured": true,
  "debug": false
}
```

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
  "health_ok": true
}
```

## Known Limitations

- This MVP is Windows-first and uses Tkinter from the Python standard library.
- It does not add a Windows service, tray app, installer, scheduled task, or
  production auto-start.
- It does not change the existing app startup lifecycle. The launcher phase
  itself does not run migrations, imports, tagging, localization, sync,
  provider calls, SourceConcept, or Entity bridge operations.
- Full production start/stop smoke should be run only from the canonical repo
  after the preflight passes.
