# Phase 3.8d-G2 - Startup / Import Path Consistency

## Summary

Phase 3.8d-G2 removes the manual validation dependency on `PYTHONPATH=<repo>\backend` for normal repo-root startup via the approved venv Python and `run.py --debug`.

The runtime code fix is intentionally small: the startup-critical backend module `backend/app/services/source_ingestion_gate.py` now uses a package-relative import for `cloud_files`, matching the `backend.app.*` import context used by `run.py`.

## Root Cause

- `run.py` starts Uvicorn with `backend.app.main:app`.
- Backend modules imported under `backend.app.*` should not rely on a top-level `app.*` package unless `<repo>\backend` is on `PYTHONPATH`.
- `backend/app/services/source_ingestion_gate.py` used `from app.utils.cloud_files import ...`, which failed during manual startup without the backend `PYTHONPATH` workaround.
- The old workaround made `app.*` importable, but it was fragile and operator-memory dependent.
- Desired behavior is repo-root startup without setting `PYTHONPATH=<repo>\backend`.

## Import Pattern Search And Classification

Searches run:

```powershell
rg -n "^\s*(from|import)\s+app(\.|\s|$)" backend/app tests scripts run.py
rg -n "^\s*(from|import)\s+backend\.app(\.|\s|$)" backend/app tests scripts run.py
rg -n "^\s*from\s+\.+[^\n]*\s+import" backend/app
```

Findings:

| Class | Finding | Action |
|-------|---------|--------|
| startup-path critical | `backend/app/services/source_ingestion_gate.py` imported `app.utils.cloud_files` | Fixed to `from ..utils.cloud_files import ...` |
| internal backend/app module imports | Existing backend runtime imports are package-relative (`.` / `..` / `...`) | Left unchanged |
| backend/app top-level `app.*` imports after fix | `0` remaining | N/A |
| intentional startup entry import | `run.py` imports `backend.app.utils.logger` and `backend.app.config`, then starts `backend.app.main:app` | Left unchanged |
| script-only `app.*` imports | `18` scripts still use `app.*`, usually with explicit script path setup | Deferred; not in `run.py` server startup path |
| test-only `app.*` imports | `25` test files still use `app.*`, relying on pytest/test path setup | Deferred; not in `run.py` server startup path |
| explicit `backend.app.*` test import | `tests/test_llm_translation_provider.py` imports `backend.app.services.llm_translation_provider` | Safe compatibility check; left unchanged |

## What Changed

- `backend/app/services/source_ingestion_gate.py`
  - Changed `from app.utils.cloud_files import CloudFileState, classify_cloud_file_state` to `from ..utils.cloud_files import CloudFileState, classify_cloud_file_state`.
- `tests/test_server_startup_imports.py`
  - Added a subprocess regression test that removes `<repo>\backend` from `PYTHONPATH`, asserts the backend path is not on `sys.path`, then imports:
    - `backend.app.services.source_ingestion_gate`
    - `backend.app.main`
- `docs/manual-validation.md`
  - Removed the backend `PYTHONPATH` workaround from the required manual validation commands.
  - Added a historical note that Phase 3.8d-G2 removed the earlier G1 requirement.
- `docs/test-workflow.md`
  - Added the new startup import test to Tier 1 coverage.
  - Updated manual development validation guidance to say no backend `PYTHONPATH` is required.
- `docs/current-handoff.md`
  - Updated the current phase and manual validation server instructions.
- `docs/project-roadmap.md`
  - Added Phase 3.8d-G2 and marked import/startup path consistency hardening as completed.

## Deferred

The remaining `app.*` imports in scripts and tests were intentionally not rewritten. They are outside the `run.py` startup path, many are tied to script/test harness path setup, and changing them would be a broader import refactor beyond this hardening stage.

## Validation

Python identity:

```powershell
& "$PY" scripts/check_python_env.py --expected-python "$PY"
```

Result:

- PASS
- `sys.executable`: `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`
- Python: `3.12.0`
- `is_venv`: `True`

Static / focused validation:

```powershell
git diff --check
& "$PY" -m py_compile run.py backend/app/services/source_ingestion_gate.py backend/app/utils/local_library_scanner.py backend/app/main.py
& "$PY" -m pytest tests/test_server_startup_imports.py -v
```

Result:

- `git diff --check`: exit 0, no whitespace errors
- `py_compile`: exit 0
- `tests/test_server_startup_imports.py`: `1 passed`

Full non-E2E suite:

```powershell
& "$PY" -m pytest tests/ -v --ignore=tests/e2e
```

Result:

- `1195 passed`
- `12 skipped`
- `12 warnings`

## Controlled Startup Validation

Environment:

- Working directory: `C:\Users\kyloris\Documents\AnimeLocalBooru`
- Command: `C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe run.py --debug`
- `VIOLET_ENV=development`
- `POSTGRES_DB=blombooru`
- `APP_PORT=8012`
- `VIOLET_BASE_URL=http://127.0.0.1:8012`
- `PYTHONPATH` for server: empty
- Translation/background safety switches set false:
  - `AI_TAGGING_AUTO_LOCALIZATION=false`
  - `TAG_TRANSLATION_BACKGROUND_ENABLED=false`
  - `TAG_TRANSLATION_AUTO_ENABLED=false`
  - `TAG_TRANSLATION_LLM_ENABLED=false`

Startup result:

- Started wrapper PID: `53172`
- Uvicorn reloader PID from logs: `58596`
- Server PID from identity endpoint: `48788`
- Startup log showed:
  - `VIOLET_ENV=development`
  - `DB_NAME=blombooru`
  - `CODE_ROOT=C:\Users\kyloris\Documents\AnimeLocalBooru`
  - `STORAGE_ROOT=C:\Users\kyloris\Documents\AnimeLocalBooru`
  - `Python runtime: executable=C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`
  - `Background tag translation worker disabled`
  - `V.I.O.L.E.T. started successfully`

Read-only probes:

- `GET /api/media/?page=1&limit=1`: HTTP 200
- `scripts/check_test_server_identity.py` with expected env/db/code root/branch/storage root/python: PASS

Shutdown:

- Stopped the validation server child PID identified from the same startup log / identity result.
- Port `8012` release confirmed: `PORT_RELEASED_AFTER_CLEANUP=YES`.

## PYTHONPATH Status

`PYTHONPATH=<repo>\backend` is no longer required for repo-root manual validation startup through `run.py --debug`.

## Runtime Mutation Confirmation

This stage did not run:

- DB import
- classification
- AI tagging
- localization
- staging copy
- source/iCloud mutation
- app-managed storage mutation
- cleanup/delete/reset/drop/truncate
- Entity Resolver
- similarity/clustering
- Phase 4 work

The controlled startup validation performed startup plus read-only API/identity probes only. The background translation worker was disabled for the validation server.

## Artifact Lifecycle

- Runtime code import fix: production reusable / runtime safety
- Startup import test: reusable validation/safety test
- `docs/manual-validation.md` update: public runbook / handoff
- `docs/test-workflow.md`, `docs/current-handoff.md`, `docs/project-roadmap.md` updates: public workflow / handoff / roadmap
- G2 report and summary JSON: public report / handoff

## Engineering Judgment

The scope was appropriate. The root cause was one startup-critical mixed import, and fixing only that backend runtime import avoided a broad package refactor.

The remaining script/test `app.*` imports should stay deferred unless a future phase explicitly chooses to standardize script execution contexts. Rewriting them here would add risk without improving the manual validation startup path.
