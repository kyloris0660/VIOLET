# V.I.O.L.E.T. Test Workflow

## Overview

This document describes the test infrastructure, environment setup, and execution workflow for V.I.O.L.E.T.

## Test Tiers

### Tier 1 — Unit Tests (no external dependencies)

Run with `pytest tests/` from the project root. These tests mock environment variables and never connect to a real database or server.

| Test file | Coverage |
|-----------|----------|
| `tests/test_env_safety.py` | VIOLET_ENV, STORAGE_ROOT, test DB fail-closed, assert_test_db |
| `tests/test_destructive_gate.py` | Destructive gate conditions, storage path containment |
| `tests/test_scanner_icloud.py` | Scanner iCloud safety, preflight, skip mapping |
| `tests/test_content_classification.py` | CLIP + heuristic classifiers |
| `tests/test_smoke_validation.py` | Full pipeline smoke validation (Phase 3.1.1c) |
| `tests/test_server_identity.py` | Server identity endpoint fields, Python runtime identity, no secrets exposed |
| `tests/test_unified_llm.py` | `complete_chat`/`complete_json` success, failure, fallback paths |
| `tests/test_python_env_preflight.py` | Python/venv env preflight, stdlib-only, sys.executable match |
| `tests/test_check_clip_model_ready.py` | CLIP model preflight check (cache-only, HF_HUB_OFFLINE, exit codes) |
| `tests/test_classification_job_clip_precheck.py` | CLIP precheck video-only skip, early fail, `requires_clip_inference` |
| `tests/test_ai_tagging_localization_gate.py` | `AI_TAGGING_AUTO_LOCALIZATION` gate in `_schedule_localization`, config property |
| `tests/test_ai_tagging_content_class_filter.py` | `content_class_filter` request model validation, `ContentClassEnum` values |
| `tests/test_check_server_identity_script.py` | Identity script proxy bypass (`trust_env=False`), `normalize_path`, `normalize_executable_path` |
| `tests/test_media_processor_mime_magic_cache.py` | python-magic availability caching, thread-local detectors, fallback chain, concurrent init safety |
| `tests/test_config_precedence.py` | Config precedence: process env beats `.env`, `TEST_DATABASE_URL` override, translation flag overrides, code defaults |

### Tier 2 — Fixture Validation (read-only, requires fixture path)

Requires `VIOLET_TEST_FIXTURE_PATH` environment variable pointing to a VioletTestFixture directory.

| Test file | Coverage |
|-----------|----------|
| `tests/test_fixture_validation.py` | Fixture structure, file counts, subfolder checks |

### Tier 3 — Playwright E2E (requires running server)

Requires `VIOLET_RUN_REAL_E2E=1` and a running V.I.O.L.E.T. server. Some tests additionally require `VIOLET_TEST_FIXTURE_PATH`.

| Test file | Requires | Coverage |
|-----------|----------|----------|
| `tests/e2e/config-diagnostics-e2e.spec.ts` | `VIOLET_RUN_REAL_E2E=1` | Config diagnostics API sections |
| `tests/e2e/gallery-browse.spec.ts` | `VIOLET_RUN_REAL_E2E=1` | Gallery grid, media detail, thumbnails |
| `tests/e2e/fixture-import.spec.ts` | `VIOLET_RUN_REAL_E2E=1` + `VIOLET_TEST_FIXTURE_PATH` | Preflight, dry-run, import, idempotency |
| `tests/e2e/entity-alias-resolver.spec.ts` | `VIOLET_RUN_REAL_E2E=1` | Entity resolver API, trust policy, admin UI |
| `tests/e2e/tag-localization.spec.ts` | `VIOLET_RUN_REAL_E2E=1` | LLM tag translation status, batch, auto-translate |

**LLM E2E gate variable:** Some E2E tests that call real LLM APIs are additionally gated by `VIOLET_RUN_REAL_LLM_E2E=1`. The deprecated alias `VIOLET_RUN_REAL_LLM_TESTS` is still accepted (via OR logic) but new tests should use `VIOLET_RUN_REAL_LLM_E2E`.

## Environment Setup

### Standardized Test Environment

Load the test environment in one step (PowerShell):

```powershell
. "$env:USERPROFILE\.violet\test-env.ps1"
```

This sets core test variables: `VIOLET_ENV=test`, `POSTGRES_DB=blombooru_test`, `VIOLET_STORAGE_ROOT`, `VIOLET_TEST_FIXTURE_PATH`, and `APP_PORT=8001`. For E2E runs, agents override in the current session:

```powershell
$env:APP_PORT = "<chosen-free-port>"   # probe 8012-8024 for availability
$env:VIOLET_BASE_URL = "http://127.0.0.1:$($env:APP_PORT)"
$env:VIOLET_RUN_REAL_E2E = "1"
```

### HuggingFace Hub Offline Mode

If `HTTP_PROXY` / `HTTPS_PROXY` is set in the environment (e.g. for GFW bypass), HuggingFace Hub metadata requests may fail even when the CLIP model is already cached locally. Set `HF_HUB_OFFLINE=1` to skip all Hub network requests and use only the local cache:

```powershell
$env:HF_HUB_OFFLINE = "1"
```

This is recommended for all test/pilot runs where the CLIP model has already been downloaded. See `docs/medium-pilot-workflow.md` § 3.1 for full details.

### Prerequisites

1. PostgreSQL 17 running on `localhost:5432`
2. Python 3.12 venv with project dependencies (`$PY = C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe`). Run `& "$PY" scripts/check_python_env.py --expected-python "$PY"` before any test/server operation — this is a hard gate.
3. Node.js with Playwright installed (`npx playwright install`)

### Test Database

Create the test database (idempotent):

```powershell
$env:POSTGRES_DB = "blombooru_test"
& "$PY" scripts/setup_test_db.py
```

Run schema migrations on the test database:

```powershell
$env:POSTGRES_DB = "blombooru_test"
$env:VIOLET_ENV = "test"
& "$PY" scripts/setup_test_db.py --migrate
```

Use `--dry-run` to preview without making changes:

```powershell
& "$PY" scripts/setup_test_db.py --migrate --dry-run
```

Forbidden DB names (`blombooru`, `production`, `main`, `postgres`) are rejected to prevent accidental use of the production database.

### VioletTestFixture

The test fixture directory contains curated images for E2E testing:

```
VioletTestFixture/
  anime/       (anime images)
  non_anime/   (photos, screenshots, etc.)
  mixed/       (ambiguous or mixed-style images)
```

Validate the fixture (read-only, never modifies files):

```powershell
& "$PY" scripts/inspect_test_fixture.py --path "C:\Users\kyloris\Pictures\VioletTestFixture"
& "$PY" scripts/inspect_test_fixture.py --path "C:\Users\kyloris\Pictures\VioletTestFixture" --json
```

### Test Storage

Test storage should be a dedicated directory separate from production storage:

```
VIOLET_STORAGE_ROOT=C:\Users\kyloris\VioletStorage\test
```

## Running Tests

### Unit Tests (Tier 1)

```powershell
& "$PY" -m pytest tests/test_env_safety.py tests/test_destructive_gate.py tests/test_scanner_icloud.py tests/test_content_classification.py tests/test_server_identity.py tests/test_unified_llm.py tests/test_check_clip_model_ready.py tests/test_classification_job_clip_precheck.py tests/test_ai_tagging_localization_gate.py tests/test_ai_tagging_content_class_filter.py tests/test_check_server_identity_script.py tests/test_media_processor_mime_magic_cache.py tests/test_config_precedence.py -v
```

### Smoke Validation (Tier 1)

```powershell
& "$PY" -m pytest tests/test_smoke_validation.py -v
```

### Fixture Validation (Tier 2)

```powershell
$env:VIOLET_TEST_FIXTURE_PATH = "C:\Users\kyloris\Pictures\VioletTestFixture"
& "$PY" -m pytest tests/test_fixture_validation.py -v
```

### Playwright E2E (Tier 3)

**Agents must start a controlled test server themselves** for non-destructive E2E validation. Do not ask the user to start the server unless startup fails for a concrete reason.

**Playwright base URL variable:** `VIOLET_BASE_URL` (read by `playwright.config.ts`). Do not use `PLAYWRIGHT_BASE_URL`.

Agent-started server workflow (PowerShell):

```powershell
# 1. Load test environment
. "$env:USERPROFILE\.violet\test-env.ps1"

# 2. Choose a free port dynamically (probe 8012-8024)
$env:APP_PORT = "<chosen-free-port>"
$env:VIOLET_BASE_URL = "http://127.0.0.1:$($env:APP_PORT)"

# 3. Start server in background from the PR branch/worktree
#    If worktree has no venv, use the main repo Python:
#    C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe run.py --debug
cd <worktree-or-branch-path>
Start-Process -NoNewWindow python -ArgumentList "run.py","--debug"
# Record the PID

# 4. MANDATORY: Verify server identity before running any E2E tests
& "$PY" scripts/check_test_server_identity.py --base-url "http://127.0.0.1:$($env:APP_PORT)" --expected-env test --expected-db blombooru_test --expected-python "$PY" --expected-storage-root "$env:VIOLET_STORAGE_ROOT"
# If identity check fails → STOP. Do not run E2E. Diagnose and restart.

# 5. Run E2E
npx playwright test tests/e2e/<spec>.spec.ts --project=edge

# 6. Stop only the PID you started
Stop-Process -Id <recorded-PID>
```

**Required conditions for agent-started servers:**

1. `VIOLET_ENV=test`
2. `POSTGRES_DB=blombooru_test`
3. Dedicated test storage (not dev storage)
4. Dynamically chosen free port (no fixed default — probe 8012–8024). Use `APP_PORT` env var, not `--port` CLI flag.
5. Record and only stop the exact PID started
6. **Mandatory identity preflight** — `scripts/check_test_server_identity.py` (with `--expected-python "$PY"`) must pass before E2E. This is a hard gate, not optional.
7. No import / AI tagging / LLM translation / cleanup / reset / delete operations
8. No iCloud paths, no VioletTestFixture mutation
9. **Singleton policy** — only one agent-started server per session. Diagnose port conflicts, do not silently skip.
10. Final report must include: working directory, branch, server command, PID, port, VIOLET_BASE_URL, identity check result, E2E command, stop/cleanup result

Manual server start (fallback only — if agent startup fails):

```powershell
. "$env:USERPROFILE\.violet\test-env.ps1"
$env:APP_PORT = "8013"
$env:VIOLET_BASE_URL = "http://127.0.0.1:8013"
& "$PY" run.py --debug
```

Run E2E tests:

```powershell
$env:VIOLET_RUN_REAL_E2E = "1"
$env:VIOLET_BASE_URL = "http://127.0.0.1:$($env:APP_PORT)"
npx playwright test tests/e2e/config-diagnostics-e2e.spec.ts --project=edge
npx playwright test tests/e2e/gallery-browse.spec.ts --project=edge
npx playwright test tests/e2e/fixture-import.spec.ts --project=edge
```

## Test Policies

1. **Idempotency** — All tests must be idempotent. Running them multiple times produces the same result.
2. **No automatic cleanup** — Tests do not automatically wipe the test database or test storage before each run. Cleanup is a manual operation.
3. **Read-only fixtures** — The VioletTestFixture directory is never modified by tests. Tests only read and stat files.
4. **Gating** — E2E tests are gated by `VIOLET_RUN_REAL_E2E=1` so they never run during normal CI or `npx playwright test` without explicit opt-in.
5. **No destructive defaults** — Destructive E2E operations require `VIOLET_ALLOW_DESTRUCTIVE_E2E=1` in addition to `confirm_phrase` and `dry_run=false`.
6. **CLIP is optional** — Core PR validation must not require a 350 MB CLIP model download. CLIP-dependent tests should be gated or skipped when the model is unavailable. The CLIP preflight script (`scripts/check_clip_model_ready.py`) is **cache-only by default** — it forces `HF_HUB_OFFLINE=1` and never downloads models. Video-only classification jobs skip CLIP inference entirely and do not require CLIP readiness.
