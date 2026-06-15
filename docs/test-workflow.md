# V.I.O.L.E.T. Test Workflow

## Overview

This document describes the test infrastructure, environment setup, and execution workflow for V.I.O.L.E.T.

## Scope-Based Validation Policy

This file defines test selection and server validation workflow. General reviewer
closeout, artifact lifecycle, phase granularity, and final-report rules live in
`AGENTS.md`, `CLAUDE.md`, and `docs/project-roadmap.md`.

GOV-2 test rule: pick the smallest validation set that proves the current
lifecycle and risk surface. Reliability remains strict for durable contracts and
runtime behavior, but docs-only or one-off work should not be forced through full
runtime suites unless that work actually changes runtime behavior.

| Change lifecycle / scope | Required validation |
|--------------------------|---------------------|
| Docs-only governance, handoff, roadmap, report, or README updates | `git diff --check`; validate JSON reports with `json.tool`; Python identity check if Python is used. Run focused doc consistency tests when the PR adds or changes them. No pytest/E2E/browser/server required unless code, tests, runtime, or UI changed. |
| Durable production runtime code | Focused tests for changed behavior plus broader non-E2E coverage when shared contracts, DB behavior, or cross-module behavior is touched. |
| DB schema or migration work | Migration plan/review, focused migration tests, DB safety checks, and explicit user/ChatGPT approval before implementation. |
| Provider-neutral contract or provider persistence code | Focused contract tests, privacy/public-safe serialization tests, and persistence-readiness tests. DB writes remain gated by phase approval. |
| Reusable validation/safety tool | Strict tests for the tool's safety contract and failure modes; avoid broad platform/framework expansion beyond the approved use case unless promoted. |
| Phase-scoped operational runner | Tests proving current-phase safety, privacy, data integrity, report truthfulness, and fail-closed behavior. Do not require arbitrary future parameter combinations unless the phase uses them. |
| One-off local artifact / ignored output | Keep ignored/untracked; validate only enough to support the immediate decision. |
| UI-affecting change | Real browser validation with a controlled server and identity preflight; E2E in scope must finish with 0 failures. |
| Provider calls/uploads | Provider policy, privacy eligibility, budgets, cache/audit plan, derived-input approval, and explicit run approval before execution. |

### ChatGPT Review Pack Gate

Route-decision phases and large-data audit phases must generate a privacy-safe
ChatGPT review pack unless the user/ChatGPT explicitly waives it. The pack must
include a manifest, checksums, public report copy, audit-data JSON, review
samples, and a redaction report that scans every file in the pack. Until the
pack is uploaded for independent audit, the final route status should remain
`provisional_pending_chatgpt_pack_audit`. If a pipeline fidelity incident is
open, route approval must instead remain
`blocked_pending_pipeline_fidelity_remediation` until remediation and rerun
evidence exist. See
`docs/chatgpt-review-pack-policy.md`.

No-active-server preflight is mandatory before agent-started servers and
manual-validation servers. It is not required for docs-only changes that do not
start a server.

If any Python/runtime code changes occur during a docs-only stage, stop and
explain why before continuing.

### Executable Phase Contract Gate

Every phase that claims `target_met`, `route_approved`, `full_chain_completed`,
or `safe_to_merge` must write a summary that declares a registered executable
contract and passes the checker:

```powershell
& "$PY" scripts/check_phase_contract.py --contract <contract_id> --summary <summary.json>
```

Use `--list-contracts` to inspect registered contracts. For route-decision
phases, run the route-audit contract on the final summary and keep approval
blocked if the upstream pipeline contract is failed, deterministic-only, or
incomplete. For public reports and review packs, run the redaction/review-pack
contracts before treating the artifacts as deliverable.

## Current Active Validation Entry Points

Use these first for current SourceConcept/documentation work:

| Scope | Entry point |
|-------|-------------|
| DOC1/DOC1-R1 documentation consolidation | `git diff --check`; `git diff --cached --check`; `& "$PY" scripts/check_python_env.py --expected-python "$PY"`; `& "$PY" -m json.tool <changed-summary-json>`; `& "$PY" -m pytest tests/test_phase45_doc1_documentation_state.py -v` when that test exists or changes |
| SC1 SourceConcept resolver core | `& "$PY" -m pytest tests/test_phase45_sc1_source_concept_resolver.py -v`; runner validation pack only when the phase explicitly authorizes DB/apply work |
| SC2 SourceConcept search/evidence UI | `& "$PY" -m pytest tests/test_phase44p2r_f6_source_layer_search.py tests/test_phase45_sc2_source_concept_search_evidence_ui.py -v`; gated Playwright Edge E2E when UI/runtime behavior is in scope |
| SCV1 expanded validation planning | Start read-only: coverage inventory, larger current-data samples, alias-gap analysis, `needs_review` clusters, redacted evidence review, and search-symmetry checks. Do not run imports/providers/LLMs/source enrichment without separate approval |
| SCV2-P0 controlled medium expansion policy | `& "$PY" scripts/run_phase45_scv2_p0_controlled_medium_expansion_policy.py --output-dir ".local_manifests\phase-4.5-scv2-p0-controlled-medium-expansion-policy" --write-public-report --read-only`; `& "$PY" -m pytest tests/test_phase45_scv2_p0_controlled_medium_expansion_policy.py -v`; no server/browser/import/provider/AI jobs |
| SCV2-A1 post-expansion audit / route decision | `& "$PY" scripts/run_phase45_scv2_a1_post_expansion_audit_route_decision.py --output-dir ".local_manifests\phase-4.5-scv2-a1-post-expansion-audit-route-decision" --write-public-report --read-only --write-chatgpt-review-pack`; `& "$PY" -m pytest tests/test_phase45_scv2_a1_post_expansion_audit_route_decision.py -v`; no server/browser/import/provider/AI jobs. During INC1, final route approval remains `blocked_pending_pipeline_fidelity_remediation`; R2/PX1-B/Provider-2/scale-up remain blocked until R1R+A1R complete |
| GOV3 executable pipeline contracts | `& "$PY" -m pytest tests/test_phase_contracts.py tests/test_phase45_doc1_documentation_state.py -v`; `& "$PY" scripts/check_phase_contract.py --list-contracts`; run `route_audit_contract_v1` on A1 summary, `public_redaction_contract_v1` on INC1 summary, and `source_concept_full_chain_contract_v1` on passing/failing mock fixtures. No server/browser/import/provider/LLM/DB writes |
| FULLLIB-P0 production import / AI tagging plan | Docs/report/JSON only: `git diff --check`; `git diff --cached --check`; `& "$PY" scripts/check_python_env.py --expected-python "$PY"`; `& "$PY" -m json.tool docs/reports/phase-4.6-fulllib-p0-production-import-ai-tagging-plan-summary.json`; `& "$PY" -m pytest tests/test_phase45_doc1_documentation_state.py -v`. No server/browser/import/provider/LLM/DB writes/classification/AI jobs |
| FULLLIB-E1 production utility execution | Requires approved P0, production DB/storage identity, backup/recovery proof, inventory dry-run, source/app-storage separation proof, per-item ledgers, offline model preflight, and GOV3 contract checks for `python_env_contract_v1`, `postgres_db_contract_v1`, `media_import_contract_v1`, `classification_contract_v1`, `ai_tagging_contract_v1`, `mutation_safety_contract_v1`, `artifact_lifecycle_contract_v1`, and `public_redaction_contract_v1`. Provider/LLM/SourceConcept/Entity/R1R/A1R/R2 remain forbidden |
| Entity bridge / SourceConcept editing | Not covered by current validation. Must add preview, confirmation, audit, rollback/supersede, write guards, and no-truth-pollution tests before implementation |
| Provider/source-enrichment scale | Requires separate provider policy, privacy eligibility, budget/cache/audit gates, run ledger, and Phase 3.9-style source item ledger discipline before execution. This row does not authorize provider/SourceConcept work inside FULLLIB-E1 |

## Manual Entity Correction Testing Principle

Entity metadata UI/API validation must reflect the product model: targeted correction, not exhaustive review. Tests should prove that operators can find and correct entities, aliases, assignments, and targeted candidates, while preserving durable evidence/provenance and avoiding broad queue assumptions. Do not design tests that imply every AI/entity suggestion must be manually processed one by one.

## Reference: Test Inventory And Environment

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
| `tests/test_phase45_doc1_documentation_state.py` | DOC1-R1 active documentation state, stale SC2 wording prevention, handoff slimming guard, summary JSON guard classification schema |
| `tests/test_phase44p2r_f6_source_layer_search.py` | F6 source-layer media chips, source assertion/source tag AND search with ordinary tags, promotion preview no-op, and no truth-path writes |
| `tests/test_phase45_sc1_source_concept_resolver.py` | SC1 SourceConcept schema, multi-source signal adapters, alias-edge linking, AI-only trust guard, short-name overmerge guards, F7a final-pack local backfill, and no truth-path writes |
| `tests/test_phase45_sc2_source_concept_search_evidence_ui.py` | SC2 SourceConcept search expansion, redaction, visible-status gates, cache invalidation, alias closure, `needs_review` behavior, promotion preview no-op, and no truth-path writes |

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
| `tests/e2e/source-layer-search.spec.ts` | `VIOLET_RUN_REAL_E2E=1` + F6 source-layer rows in test DB | Media detail source assertions, visual multi-select source/tag search, admin Content section navigation |
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

### SourceConcept Resolver Validation (Phase 4.5-SC1)

Phase 4.5-SC1 is a DB/schema/resolver phase, not a UI/search integration phase.
Use focused tests plus the runner-generated validation pack:

```powershell
& "$PY" -m pytest tests/test_phase45_sc1_source_concept_resolver.py -v
& "$PY" scripts/run_phase45_sc1_source_concept_resolver.py --apply-db --apply-f7a-final-pack --use-llm-adjudication --max-llm-calls 300 --max-llm-budget-usd 50
```

Required checks:

- F7a final pack candidates are persisted locally if the final validated run is absent from the F7a DB tables.
- The runner produces `source-signal-inventory.json`, `source-signals.jsonl`, `source-concepts.jsonl`, aliases, evidence, links, search-preview, positive/negative case review, consistency check, redaction check, and a zip under `.local_manifests`.
- `artifact-consistency-check.json` must pass.
- `public-redaction-check.txt` must pass.
- `forbidden_truth_table_write_count` must be `0` for both F7a local backfill and SC1 resolver persistence.
- Bounded text-only LLM pair adjudication is allowed only when explicitly enabled by `--use-llm-adjudication`, capped by `--max-llm-calls` and `--max-llm-budget-usd`, cache-backed, primary-provider-only, and recorded in the validation pack. It must never upload images, run provider/gallery-dl/Pixiv/SauceNAO/Google enrichment, run localization batches, use fallback providers by default, or create Entity truth.
- SC1 must not start full SC2 search/UI integration.

### SourceConcept Search and Evidence UI Validation (Phase 4.5-SC2)

Phase 4.5-SC2 touches search/API behavior and user-visible media-detail UI, so real browser validation is required. Do not treat SC1 resolver readiness as proof that SC2 user workflows work.

Focused pytest:

```powershell
& "$PY" -m pytest tests/test_phase44p2r_f6_source_layer_search.py tests/test_phase45_sc2_source_concept_search_evidence_ui.py -v
```

Controlled real-browser fixture and E2E:

```powershell
. "$env:USERPROFILE\.violet\test-env.ps1"
$env:VIOLET_ENV = "test"
$env:POSTGRES_DB = "blombooru_test"
$env:VIOLET_STORAGE_ROOT = Join-Path $env:USERPROFILE "VioletStorage\test"
$env:VIOLET_TEST_STORAGE_ROOT = $env:VIOLET_STORAGE_ROOT
& "$PY" scripts/seed_phase45_sc2_e2e_fixture.py

$env:VIOLET_RUN_REAL_E2E = "1"
$env:VIOLET_BASE_URL = "http://127.0.0.1:<agent-started-port>"
npx playwright test tests/e2e/source-concept-search-evidence.spec.ts --project=edge
```

Required SC2 validation shape:

- API/search tests for SourceConcept alias expansion through the search-preview index, including exact alias examples, bidirectional concept-level alias expansion, normal tag search preservation, mixed normal tag + SourceConcept queries, negative/exact-query boundaries, and clearly labeled `needs_review` SourceConcept expansion for explicit alias `q=` search.
- Read-only SourceConcept detail/evidence endpoint tests covering aliases, providers, signal origins, trust tiers, concept status, evidence count, redaction, no local paths/secrets, no public exposure of raw or canonicalized path-derived `concept_key` values, and no filename-derived alias/search-key leakage such as `vacation_2024_jpg`, `img_1234_jpeg`, or `private_png`.
- Detail and promotion-preview direct-ID lookups must be gated to visible statuses: `active` and intentionally visible `needs_review` return data, while `rejected`, `ambiguous`, and `superseded` return safe not-found responses.
- Cache-invalidation tests covering SourceConcept resolver/fixture writes and stale cached `q=<alias>` search responses.
- Search filtering tests proving display caps do not cap the actual SourceConcept ids used for media filtering.
- SourceConcept `q=` chip/search URL tests must quote parser metacharacters such as `:`, leading `-`, wildcard characters, brackets, parentheses, and whitespace while leaving safe ordinary tags such as `kamisato_ayaka` unquoted.
- No-truth-write assertions proving SC2 does not create or mutate `Entity`, `EntityAlias`, `EntityEvidence`, `MediaEntityCandidate`, `MediaEntityAssignment`, `LocalSourceHint`, confirmed assignments, `TagTranslation`, or `media_tags`.
- Playwright/browser E2E on a controlled test server for compact media-detail SourceConcept grouping, same-name chip dedupe, concept/alias chip click through global `q=` search, search expansion explanation, mixed normal tag + SourceConcept search, collapsed evidence preview expansion, no console errors, and no truth writes.
- F6 behavior must remain intact: user-facing chips default to global `q=` search, while scoped source filters remain advanced/debug routes.
- Real SourceConcept management/editing remains out of scope for SC2 validation; if present before SC3 it must be read-only or disabled/no-op.

### Expanded SourceConcept Validation and Coverage Audit (Phase 4.5-SCV1)

SCV1 should validate whether SC1/SC2 coverage holds beyond the current small-medium fixtures before any Entity bridge or promotion work. Start with current DB data and read-only reporting; do not run new imports, providers, LLMs, AI tagging/classification, localization, or source enrichment unless a separate phase explicitly approves that run.

Implemented SCV1 entry points:

- Runner: `& "$PY" scripts/run_phase45_scv1_source_concept_coverage_audit.py --output-dir ".local_manifests\phase-4.5-scv1-source-concept-coverage-audit" --write-public-report --read-only`.
- Focused tests: `& "$PY" -m pytest tests/test_phase45_scv1_source_concept_coverage_audit.py -v`.
- Public report: `docs/reports/phase-4.5-scv1-source-concept-coverage-audit.md`.
- Public summary: `docs/reports/phase-4.5-scv1-source-concept-coverage-audit-summary.json`.

Recommended SCV1 validation shape:

- Coverage inventory: media count, AI tag coverage, Pixiv/source signal coverage, F7a candidate coverage, SourceConcept coverage, search-preview coverage, active versus `needs_review` concepts, and orphan/gap counts.
- Larger sample validation: current-data concept samples across providers, signal origins, trust tiers, and statuses, with redacted evidence review artifacts.
- Alias gap analysis: Japanese/English/Chinese/romaji variants, ordinary tags versus Pixiv/source tags versus AI tags, `needs_review` clusters, and examples such as Nahida / `纳西妲` / `草神` / `nahida_(genshin_impact)`.
- Search symmetry checks: aliases linked to the same visible SourceConcept should produce the same SourceConcept-linked media set unless status or trust gates intentionally prevent expansion.
- Guard carry-forward: reuse SC2 redaction, visible-status, cache-invalidation, no-truth-write, F6 q-chip, and promotion no-op tests when SCV1 code or reports touch those paths.

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

# 2. No-active-server preflight on common local ports
& "$PY" scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree
# If an unexpected V.I.O.L.E.T. server is active, STOP. Diagnose and report.

# 3. Choose a free port dynamically (probe 8012-8024)
$env:APP_PORT = "<chosen-free-port>"
$env:VIOLET_BASE_URL = "http://127.0.0.1:$($env:APP_PORT)"

# 4. Start server in background from the PR branch/worktree
#    If worktree has no venv, use the main repo Python:
#    C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe run.py --debug
cd <worktree-or-branch-path>
Start-Process -NoNewWindow python -ArgumentList "run.py","--debug"
# Record the command, APP_PORT, VIOLET_BASE_URL, parent/reloader PID,
# worker/identity PID, process tree, code root, git SHA, env, DB,
# storage root, and Python executable.

# 5. MANDATORY: Verify server identity before running any E2E tests
& "$PY" scripts/check_test_server_identity.py --base-url "http://127.0.0.1:$($env:APP_PORT)" --expected-env test --expected-db blombooru_test --expected-python "$PY" --expected-storage-root "$env:VIOLET_STORAGE_ROOT"
# If identity check fails → STOP. Do not run E2E. Diagnose and restart.

# 6. Run E2E
npx playwright test tests/e2e/<spec>.spec.ts --project=edge

# 7. Stop only the exact process tree you started, then verify port release
Stop-Process -Id <recorded-reloader-PID>,<recorded-worker-PID>
& "$PY" scripts/audit_active_violet_servers.py --ports $env:APP_PORT --fail-if-any
```

**Required conditions for agent-started servers:**

1. `VIOLET_ENV=test`
2. `POSTGRES_DB=blombooru_test`
3. Dedicated test storage (not dev storage)
4. Dynamically chosen free port (no fixed default — probe 8012–8024). Use `APP_PORT` env var, not `--port` CLI flag.
5. Record the full server process tree and only stop the exact identified process tree started by this task
6. **Mandatory identity preflight** — `scripts/check_test_server_identity.py` (with `--expected-python "$PY"`) must pass before E2E. This is a hard gate, not optional.
7. No import / AI tagging / LLM translation / cleanup / reset / delete operations
8. No iCloud paths, no VioletTestFixture mutation
9. **Singleton policy** — only one agent-started server per session. Diagnose port conflicts, do not silently skip.
10. Final report must include: working directory, branch, server command, parent/reloader PID, worker/identity PID, process tree, port, VIOLET_BASE_URL, identity check result, E2E command, stop/cleanup result, and port-free verification

**Additional S1 server lifecycle guard:** Before any agent-started server, run `scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree` and stop if an unexpected V.I.O.L.E.T. server is active. Do not silently choose another port around a stale server. Record command, `APP_PORT`, `VIOLET_BASE_URL`, parent/reloader PID, worker/identity PID, process tree, code root, git SHA, `VIOLET_ENV`, DB, storage root, and Python executable. `run.py --debug` uses uvicorn reload and may leave a worker child if only a wrapper/reloader PID is stopped; cleanup must stop only the exact identified process tree started by the task. After cleanup, verify the port is no longer `LISTENING`, and include port-free verification in the final report.

The active S1 audit tool is scoped to the current Windows local validation environment. On non-Windows hosts, use a platform-specific equivalent or future tested implementation; an unsupported listener backend must fail closed and must not be treated as a clean no-active-server preflight.

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
