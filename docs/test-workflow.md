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

## Environment Setup

### Standardized Test Environment

Load the test environment in one step (PowerShell):

```powershell
. "$env:USERPROFILE\.violet\test-env.ps1"
```

This sets core test variables: `VIOLET_ENV=test`, `POSTGRES_DB=blombooru_test`, `VIOLET_STORAGE_ROOT`, `VIOLET_TEST_FIXTURE_PATH`, and `APP_PORT=8001`. For E2E runs, agents override in the current session:

```powershell
$env:VIOLET_BASE_URL = "http://127.0.0.1:8011"
$env:VIOLET_RUN_REAL_E2E = "1"
```

### Prerequisites

1. PostgreSQL 17 running on `localhost:5432`
2. Python 3.12 venv with project dependencies
3. Node.js with Playwright installed (`npx playwright install`)

### Test Database

Create the test database (idempotent):

```powershell
$env:POSTGRES_DB = "blombooru_test"
python scripts/setup_test_db.py
```

Run schema migrations on the test database:

```powershell
$env:POSTGRES_DB = "blombooru_test"
$env:VIOLET_ENV = "test"
python scripts/setup_test_db.py --migrate
```

Use `--dry-run` to preview without making changes:

```powershell
python scripts/setup_test_db.py --migrate --dry-run
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
python scripts/inspect_test_fixture.py --path "C:\Users\kyloris\Pictures\VioletTestFixture"
python scripts/inspect_test_fixture.py --path "C:\Users\kyloris\Pictures\VioletTestFixture" --json
```

### Test Storage

Test storage should be a dedicated directory separate from production storage:

```
VIOLET_STORAGE_ROOT=C:\Users\kyloris\VioletStorage\test
```

## Running Tests

### Unit Tests (Tier 1)

```powershell
pytest tests/test_env_safety.py tests/test_destructive_gate.py tests/test_scanner_icloud.py tests/test_content_classification.py -v
```

### Smoke Validation (Tier 1)

```powershell
pytest tests/test_smoke_validation.py -v
```

### Fixture Validation (Tier 2)

```powershell
$env:VIOLET_TEST_FIXTURE_PATH = "C:\Users\kyloris\Pictures\VioletTestFixture"
pytest tests/test_fixture_validation.py -v
```

### Playwright E2E (Tier 3)

**Agents must start a controlled test server themselves** for non-destructive E2E validation. Do not ask the user to start the server unless startup fails for a concrete reason.

**Playwright base URL variable:** `VIOLET_BASE_URL` (read by `playwright.config.ts`). Do not use `PLAYWRIGHT_BASE_URL`.

Agent-started server workflow (PowerShell):

```powershell
# 1. Load test environment
. "$env:USERPROFILE\.violet\test-env.ps1"

# 2. Override port and base URL (prefer 8011+)
$env:VIOLET_BASE_URL = "http://127.0.0.1:8011"

# 3. Start server in background from the PR branch/worktree
#    If worktree has no venv, use the main repo Python:
#    C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe run.py --debug --port 8011
cd <worktree-or-branch-path>
Start-Process -NoNewWindow python -ArgumentList "run.py","--debug","--port","8011"
# Record the PID

# 4. Wait for server readiness
# Verify: http://127.0.0.1:8011/api/health or /admin

# 5. Run E2E
npx playwright test tests/e2e/<spec>.spec.ts --project=edge

# 6. Stop only the PID you started
Stop-Process -Id <recorded-PID>
```

**Required conditions for agent-started servers:**

1. `VIOLET_ENV=test`
2. `POSTGRES_DB=blombooru_test`
3. Dedicated test storage (not dev storage)
4. Dedicated free port (prefer 8011+)
5. Record and only stop the exact PID started
6. No import / AI tagging / LLM translation / cleanup / reset / delete operations
7. No iCloud paths, no VioletTestFixture mutation
8. Final report must include: working directory, branch, server command, PID, port, VIOLET_BASE_URL, E2E command, stop/cleanup result

Manual server start (fallback only — if agent startup fails):

```powershell
. "$env:USERPROFILE\.violet\test-env.ps1"
$env:VIOLET_BASE_URL = "http://127.0.0.1:8011"
python run.py --debug --port 8011
```

Run E2E tests:

```powershell
$env:VIOLET_RUN_REAL_E2E = "1"
$env:VIOLET_BASE_URL = "http://127.0.0.1:8011"
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
6. **CLIP is optional** — Core PR validation must not require a 350 MB CLIP model download. CLIP-dependent tests should be gated or skipped when the model is unavailable.
