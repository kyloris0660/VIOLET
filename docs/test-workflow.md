# V.I.O.L.E.T. Test Workflow

## Overview

This document describes the test infrastructure, environment setup, and execution workflow for V.I.O.L.E.T.

## Reviewer Feedback Handling Policy

For implementation PRs, reviewer feedback is a controlled handoff point, not an automatic code-change trigger.

1. After PR creation or a meaningful PR update, CodeX must trigger reviewer with exactly `@codex review`.
2. CodeX may collect reviewer feedback and verify whether it applies to the current PR head.
3. CodeX must summarize current-head P1/P2/P3 findings in the final report.
4. CodeX must not automatically modify code based on reviewer feedback.
5. CodeX must stop and report reviewer findings to the user/ChatGPT.
6. User/ChatGPT decides whether to fix now, defer, change implementation strategy, split into another PR, or merge.
7. Automatic reviewer-fix loops are disabled by default and may only be used when the user explicitly authorizes them for a specific PR with a specific round limit and scope.
8. Even when explicitly authorized, automatic fix loops must never push `main`, merge, run destructive operations, mutate source/iCloud/staging/DB unless explicitly approved, change phase scope, or start a new phase.
9. Before triggering reviewer, CodeX must perform a local pre-review / same-class self-audit so reviewer is not used as a substitute for engineering judgment.

Plan-only tasks must not create branches, commits, pushes, or PRs unless explicitly approved as documentation PRs. Deliver plan-only output in chat or as a local untracked `.codex/plans/*.md` draft and wait for user/ChatGPT approval.

Default PR lifecycle is a normal open PR. Create a draft PR only when the user/ChatGPT explicitly requests draft, or when the stage is clearly a design draft / not ready for review. Docs-only does not imply draft, and a reviewable plan/design PR may be opened normally. Draft PRs must not become the default way to avoid reviewer or human judgment. Final reports must state whether the PR is draft and why.

## Manual Entity Correction Testing Principle

Entity metadata UI/API validation must reflect the product model: targeted correction, not exhaustive review. Tests should prove that operators can find and correct entities, aliases, assignments, and targeted candidates, while preserving durable evidence/provenance and avoiding broad queue assumptions. Do not design tests that imply every AI/entity suggestion must be manually processed one by one.

## Agent Engineering Judgment and Bugfix Root-Cause Closure Policy

CodeX must provide a meaningful `Engineering judgment / operator notes` section in final delivery reports. It must not be a perfunctory line. For every substantial phase or non-trivial bugfix, CodeX should identify risks, distinguish blockers from deferable issues, and say whether the phase boundary appears too narrow, too broad, or appropriate.

When reviewer feedback, tests, or runtime reports expose a bug, CodeX must not treat the issue only as a single-line patch unless it is clearly isolated.

For every non-trivial bugfix, CodeX must perform a bounded root-cause and pattern audit:

1. Identify the root cause. Examples: raw filesystem probes can raise, stale dry-run proof used for destructive action, report success flag does not match blocked state, failed item can leave target artifact, manifest mapping can silently overwrite operator intent, or per-item failure is incorrectly treated as batch failure.
2. Decide whether the issue belongs to a broader pattern. If yes, search within the current PR scope for adjacent occurrences of the same pattern.
3. Fix the pattern within the current PR scope. The fix should be systematic but bounded. Do not expand into unrelated modules or future phases without user/ChatGPT approval.
4. Add tests for the class of issue, not only the exact reviewed line. Tests should cover the originally reported case, at least one adjacent/similar case when practical, and the expected fail-closed or item-level failure behavior.
5. Report what was searched and what was intentionally left unchanged. Final reports for non-trivial bugfixes must include an `Engineering judgment / bugfix root-cause audit` section with: root cause, related patterns searched, files/functions inspected, fixes applied, tests added, remaining similar risks if any, deferred items and why, and whether the issue suggests the phase boundary is too narrow or too broad.
6. Stop and ask user/ChatGPT if the root-cause fix requires a DB migration, destructive operation, source/iCloud mutation, app-managed storage mutation, large refactor outside current PR scope, new phase, or project strategy change.
7. This policy does not authorize automatic reviewer-fix loops. Default reviewer workflow remains: implement -> test -> push -> `@codex review` -> collect current-head feedback -> stop and report. CodeX must not automatically fix new reviewer feedback unless the user explicitly authorizes a bounded auto-fix loop for that specific PR.
8. This policy does not authorize scope creep. It authorizes bounded same-root-cause cleanup inside the active PR scope. When in doubt, report the pattern and wait for user/ChatGPT decision.

Final `Engineering judgment / operator notes` must include:

1. Phase boundary assessment: was the requested scope too narrow, too broad, or appropriate; did the task reveal a better phase split or merge?
2. Risk assessment: top remaining risks, classified as safety blockers, quality issues, usability issues, or future hardening.
3. Reviewer feedback assessment: which findings were fixed, which were deferred, and why deferred findings do not affect current phase safety or objective.
4. Artifact lifecycle assessment: whether new scripts/tools/reports are production reusable code, reusable validation/safety tools, phase-scoped operational runners, one-off local artifacts, public reports, handoff, or roadmap updates; whether any phase-scoped code should later be promoted.
5. Prompt critique: whether the prompt missed an obvious issue, over-constrained implementation, or encouraged unnecessary over-engineering.
6. Next-step recommendation: a concrete recommendation with alternatives when relevant. Do not silently continue to the next phase.

This section is advisory only. It does not grant authority to expand scope, merge, auto-fix reviewer feedback, or start a new phase.

## Artifact and Operational Script Lifecycle Policy

V.I.O.L.E.T. allows one-off and phase-scoped validation automation. Automation is encouraged when it reduces human error, catches issues earlier, or makes a risky step more reproducible. The project does not require every validation helper to become reusable production tooling.

Do not automate for automation's sake. New scripts, tools, reports, and artifacts must declare their lifecycle in the PR body or final report:

1. **Production reusable code** - long-term maintained code with strict tests, clear interfaces, and stable semantics.
2. **Reusable validation/safety tool** - cross-phase, parameterized tooling with a stable interface that reduces long-term operator error.
3. **Phase-scoped operational runner** - committed only when it makes a phase reproducible. It must be labeled phase-scoped and must guarantee current phase safety, privacy, data integrity, and report truthfulness. It should not be endlessly generalized into a production orchestrator unless user/ChatGPT explicitly approves.
4. **One-off local artifact / temporary validation output** - must remain ignored and untracked, should not be committed, and should not become code.
5. **Public report / handoff / roadmap** - long-term documentation that records phase facts, decisions, risks, and next steps.

If a validation helper is only for one local run and not needed for reproducibility, keep it ignored and untracked. If it makes a phase reproducible, it may be committed as a phase-scoped operational runner. A phase-scoped runner may hardcode phase-specific labels, counts, or row IDs when necessary to reproduce that phase, but it must not be treated as a long-term generic validator unless user/ChatGPT explicitly approves promotion to reusable tooling.

Forbidden is not one-off automation. Forbidden is: automating for automation's sake; committing throwaway local output files; letting a phase-scoped tool accumulate unbounded production-framework complexity; repeatedly fixing reviewer suggestions that only matter if the phase runner were a future reusable orchestrator; and building generic tools before there is repeated cross-phase need.

## Reviewer Feedback and Artifact Lifecycle Rule

Reviewer feedback must be evaluated according to the lifecycle of the affected code or artifact.

1. Findings that affect current phase correctness, DB/storage/source mutation safety, import eligibility, item ledger truthfulness, privacy/public report safety, data integrity, failure/success classification, or the ability to safely continue the current workflow must be fixed even for phase-scoped runners.
2. Findings that are only about future reuse, generalized parameters, generic parameter combinations not used by the current phase, production-framework polish, cross-phase extensibility, or UI/reporting precision that does not affect current safety or decision-making may be deferred for phase-scoped or one-off code.
3. Phase-scoped operational runners should not be judged as production reusable frameworks unless user/ChatGPT decides to promote them.
4. Production reusable code cannot use "phase-scoped" as an excuse to avoid safety, correctness, maintainability, or tests.
5. Before continuing reviewer fixes, CodeX/ChatGPT should ask: what is this artifact's lifecycle; does this issue affect current phase safety or truthfulness; can it cause DB/source/app-storage mutation risk; can it mix failed items into successful items; can it leak private paths/secrets; is it only future reuse/generalization/polish; would fixing it turn a phase runner into a production orchestrator?
6. This rule does not authorize ignoring safety bugs. It exists to prevent over-engineering one-off or phase-scoped code.

## Test Tiers

### Local Test Output Path Safety

Unless the user explicitly authorizes otherwise, CodeX/local agent tests must not use `Z:\`, `\\192.168.71.230\Storage`, or any NAS/network-share path as a test output directory, pytest tmpdir, working directory, staging directory, log directory, or default script output directory.

Allowed local artifact locations:

- repo-local gitignored directories;
- local machine temporary directories from `tmp_path`, `tempfile`, or OS local temp.

Do not use fake-looking drive letters or UNC paths to simulate write failures. A path like `Z:\nonexistent_drive\...` may resolve to a real NAS/share on the user's machine. To force write failures, use deterministic local constructs such as an existing directory at the intended output file path, a file where a directory is expected, or another contained local temp conflict.

### Tier 1 — Unit Tests (no external dependencies)

Run with `pytest tests/` from the project root. These tests mock environment variables and never connect to a real database or server.

Phase 3.8d-I1 adds a cloud availability gate requirement for ingestion/staging/copy workflows: metadata-only audits must not open or read source file contents, read-probe/hydration behavior must be opt-in, and staging copy failures must use structured cloud reason codes.

Phase 3.8d-I2 unifies this behind a Source Ingestion Gate. Tests must prove that path-based source ingestion blocks cloud-risk files, while upload-bytes, staging-file, and app-managed storage workflows are explicitly classified and do not receive inappropriate source cloud checks.

| Test file | Coverage |
|-----------|----------|
| `tests/test_env_safety.py` | VIOLET_ENV, STORAGE_ROOT, test DB fail-closed, assert_test_db |
| `tests/test_destructive_gate.py` | Destructive gate conditions, storage path containment |
| `tests/test_cloud_files.py` | Windows Cloud Files attribute helper, structured cloud error classification, non-Windows safety |
| `tests/test_source_ingestion_gate.py` | Source kind classification, path-source cloud blocking, upload/staging/app-managed gate semantics, privacy-safe public summaries |
| `tests/test_scanner_icloud.py` | Scanner iCloud safety, preflight, skip mapping |
| `tests/test_audit_cloud_availability.py` | Metadata-only manifest cloud availability audit, opt-in read-probe, privacy-safe reports, same-bucket backfill, cleanup dry-run policy |
| `tests/test_phase38d_i3_recovery.py` | Phase 3.8d recovery planner, manifest/filesystem cleanup proof, controlled cleanup executor safety, row 98 recovery semantics |
| `tests/test_stage_pilot_files.py` | Staging manifest validation, cloud availability gate, structured copy failure reasons |
| `tests/test_content_classification.py` | CLIP + heuristic classifiers |
| `tests/test_smoke_validation.py` | Full pipeline smoke validation (Phase 3.1.1c) |
| `tests/test_server_identity.py` | Server identity endpoint fields, Python runtime identity, no secrets exposed |
| `tests/test_server_startup_imports.py` | Startup/import path smoke: repo-root `backend.app.*` imports do not require `<repo>\backend` in `PYTHONPATH` |
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

### Manual Development Validation

Use [Manual Validation Workflow](manual-validation.md) for local `development` / `blombooru` validation after explicitly approved import or pipeline phases. Do not load `. "$env:USERPROFILE\.violet\test-env.ps1"` for development DB validation; that script is for isolated test DB/test storage validation.

As of Phase 3.8d-G2, development manual validation startup from repo root no longer requires `PYTHONPATH=<repo>\backend`. `run.py` loads `backend.app.main:app`, and startup-critical backend runtime imports must remain package-relative or otherwise importable through `backend.app.*`. If the startup flow changes again, update `docs/manual-validation.md` and obtain user/ChatGPT approval.

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
& "$PY" -m pytest tests/test_env_safety.py tests/test_destructive_gate.py tests/test_cloud_files.py tests/test_source_ingestion_gate.py tests/test_scanner_icloud.py tests/test_audit_cloud_availability.py tests/test_stage_pilot_files.py tests/test_content_classification.py tests/test_server_identity.py tests/test_server_startup_imports.py tests/test_unified_llm.py tests/test_check_clip_model_ready.py tests/test_classification_job_clip_precheck.py tests/test_ai_tagging_localization_gate.py tests/test_ai_tagging_content_class_filter.py tests/test_check_server_identity_script.py tests/test_media_processor_mime_magic_cache.py tests/test_config_precedence.py -v
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

## Final Delivery Report Standard

Every CodeX final report for implementation or review stages must be written in Chinese and include:

Section headings in final delivery reports and stage summaries must also be Chinese. Technical identifiers may remain English.

1. PR URL, branch, head SHA
2. Whether the PR was created, pushed, and merged
3. Docs/code read
4. Python identity and exact sys.executable
5. Exact files changed
6. Implementation summary
7. Exact tests run and exact results
8. Real validation / dry-run results
9. Reviewer status, including whether the latest head was reviewed
10. Local artifacts generated and confirmation they were not committed
11. Safety confirmation:
    - no push main
    - no merge
    - no source/iCloud mutation unless explicitly approved
    - no cleanup/delete/reset/drop/truncate unless explicitly approved
    - no DB import unless explicitly approved
    - no classification/AI/localization unless explicitly approved
    - no Entity Resolver / similarity unless explicitly approved
12. Current blocked/ready status
13. Recommended next step
14. If stopped by a rule, the exact stop condition
15. Engineering judgment / operator notes, including the `Engineering judgment / bugfix root-cause audit` section for non-trivial bugfixes.

A short summary alone is not acceptable. If any item is not applicable, say "N/A" and why. Do not force the user to inspect the PR body or old logs to reconstruct test results.

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
