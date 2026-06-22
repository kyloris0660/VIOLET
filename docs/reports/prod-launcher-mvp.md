# PROD-LAUNCHER-MVP: Production Library Visual Launcher and Safe Shutdown

## Summary

This phase adds a lightweight Windows-first production launcher for the
V.I.O.L.E.T. local production library. It keeps control logic separate from the
Tkinter UI:

- `scripts/violet_production_control.py`: preflight, status, start, stop,
  restart, browser target, stale PID cleanup, and public-safe diagnostics.
- `scripts/violet_production_launcher.py`: minimal Tkinter UI with production
  status, gates, log tail, and buttons.
- `scripts/start_violet_production_launcher.cmd`: double-click entry that
  prefers the project venv Python.
- `GET /api/health`: public-safe health endpoint for launcher readiness checks.
  The route is auth-exempt for launcher polling under `REQUIRE_AUTH=true`.

## Scope

The launcher wraps the existing runtime command:

```text
python run.py
```

It does not pass `--debug`, does not add a service/tray installer, and does not
enable automatic production import, AI tagging, localization, sync, provider,
SourceConcept, or Entity operations.

## Safety / Hard Constraints

Production start is blocked unless the preflight confirms:

- canonical repo root, not a worktree;
- `VIOLET_ENV=production`;
- debug disabled and no `--debug`;
- canonical/configured production venv Python;
- readable `.env`;
- explicit production-shaped `VIOLET_STORAGE_ROOT`;
- initialized settings JSON under the configured storage root;
- DB settings present and DB reachable through a read-only check;
- valid `APP_PORT`;
- malformed `APP_PORT` blocks preflight cleanly;
- port free or owned by launcher state;
- no stale PID state;
- no startup import/tagging/localization/sync automation flags.
- destructive E2E / real E2E flags disabled.
- explicit startup write policy present.

Stop is stateful and conservative. The launcher refuses to stop any process
unless its local state verifies the PID, repo root, port, and V.I.O.L.E.T.
runtime identity, including process create time, configured/canonical Python,
and target port owner when available. If the target port is occupied without
launcher state, stop is blocked.

Normal plain `python run.py` startup can perform maintenance writes:
`init_db()`/schema checks and migrations, upload temp cleanup, stale job
recovery, static translation seeding, periodic cleanup task creation, and
background tag translation worker startup if enabled by app settings. The
launcher child process sets `VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP=true`, and
`backend/app/main.py` uses that mode to skip schema create/migrate, upload temp
cleanup, stale job recovery, translation seeding, periodic cleanup, and
background workers. The launcher does not claim normal startup is write-free.

## Implementation

- State/logs live under ignored `.local_manifests/production_launcher/`.
- State writes are atomic, and Start/Restart actions are serialized.
- `status --json` returns public-safe fields only: running, managed status,
  port, URL, env, debug, DB reachability, health, destructive E2E denial, and
  startup write policy.
- `GET /api/health` exposes only public fields: app name/version, env,
  `db_reachable`, `storage_configured`, and `debug`.
- The Tkinter UI shows status, environment, port, URL, DB name, storage root
  status, health check time, last error, and recent log tail.
- The Tkinter UI disables Start/Restart while one of those actions is already
  in progress; the controller also returns `start_already_in_progress`.

## Validation

Recorded validation:

```text
python -m py_compile scripts/violet_production_control.py scripts/violet_production_launcher.py backend/app/routes/health.py backend/app/main.py backend/app/auth_middleware.py
pytest tests/test_production_launcher_control.py -v
pytest tests/test_phase_contracts.py -k prod_launcher_mvp -v
pytest tests/test_production_launcher_control.py tests/test_phase_contracts.py -v
python scripts/check_phase_contract.py --contract prod_launcher_mvp_contract_v1 --summary docs/reports/prod-launcher-mvp-summary.json
python -m json.tool docs/reports/prod-launcher-mvp-summary.json
git diff --check
git diff --cached --check
```

The real production start/stop smoke was not run from the agent worktree. That
is intentional: the launcher preflight must reject worktrees for production
startup. The agent-worktree dry-run was blocked with these hard gates:

```text
canonical_repo_root
violet_env_production
canonical_venv_python
dotenv_exists
storage_root_explicit
production_storage_root_shape
settings_initialized
db_readonly_reachable
```

Manual smoke should be run from the canonical repo after merge:

```powershell
python scripts\violet_production_control.py preflight --json
python scripts\violet_production_control.py start --json
python scripts\violet_production_control.py status --json
python scripts\violet_production_control.py stop --json
python scripts\violet_production_control.py status --json
```

Expected smoke result: health passes after start, stop clears launcher state,
the target port is released, and no launcher-managed process remains.

## Test Plan

- [x] Python identity checked.
- [x] Launcher scripts compile.
- [x] Focused launcher control tests passed.
- [x] Phase contract tests for launcher passed.
- [x] Health route auth-exempt behavior covered.
- [x] Startup write policy covered.
- [x] Destructive E2E denial covered.
- [x] Process-stop unverified PID refusal covered.
- [x] Start/Restart serialization covered.
- [x] Malformed `APP_PORT` handling covered.
- [x] `prod_launcher_mvp_contract_v1` passed against the public summary JSON.
- [x] Summary JSON is valid JSON.
- [x] Whitespace diff checks passed.
- [ ] Real production start/stop smoke, not run from worktree by design.
- [ ] Manual review / user validation.

## Reviewer / Codex Status

Reviewer re-review should be requested after PR publication with exactly:

```text
@codex review
```

Latest-head reviewer status is pending until that review completes.

## Safety Confirmation

This phase did not push `main`, did not merge, did not mutate source/iCloud
files, did not run cleanup/delete/reset/drop/truncate, did not run DB import,
did not run classification/AI/localization/sync jobs, did not call providers,
and did not run SourceConcept, similarity, or Entity bridge operations.

## Known Limitations

- Windows-first MVP; no service/tray packaging.
- Production preflight is intentionally strict and may require operators to set
  an accepted production storage root or production Python override.
- This MVP does not add an operator-approved launcher path to allow startup
  schema migrations or destructive cleanup. Safe startup blocks those normal
  maintenance writes instead.

## Next Step

Review and merge the PR, then run the manual canonical start/stop smoke from the
production checkout.
