# S2G-M1: AI Tagging Execution and Manual Sync Foundation

Contract: `s2g_manual_sync_foundation_contract_v1`.
Status: `target_met`.
Validated implementation SHA: `a44c18a133689e94540db37df9fb05855c1edb7e`.
Validated implementation ancestor of report head: `True`.
Post-validation changes report-only: `True`.

## Purpose

S2G-M1 builds the reusable local AI tagging execution profile, benchmark, dry-run planner, job/ledger vocabulary, and dry-run pipeline planning foundation needed before S3A-M1 implements an explicit production execute path and final manual-sync controls.

Production execution is intentionally out of scope: this phase proves the foundation with local-only model resolution, synthetic benchmark input, temporary fixture storage, and an in-memory test DB plan.

## Source Files Inspected

- `docs/current-handoff.md`.
- `docs/project-roadmap.md`.
- `docs/roadmap/current-mainline-roadmap.md`.
- `docs/roadmap/post-s2-production-roadmap.md`.
- `docs/production-launcher.md`.
- `docs/dynamic-library-sync.md`.
- `docs/ai-auto-tagging.md`.
- `docs/ai-tagging-jobs.md`.
- `docs/ai-tagging-usage-guide.md`.
- `docs/reports/phase-4.7-s2-baseline-full-import-ai-localization-summary.json`.
- `docs/reports/phase-4.6-fulllib-p0-production-import-ai-tagging-plan.md`.
- `docs/reports/phase-4.6-fulllib-p0-production-import-ai-tagging-plan-summary.json`.
- `docs/reports/pd1-a-r1-post-122-roadmap-reconciliation-summary.json`.
- `backend/app/services/ai_tagging_service.py`.
- `backend/app/services/ai_tagging_job_service.py`.
- `backend/app/routes/admin/ai_tagging.py`.
- `backend/app/routes/admin/ai_tagging_jobs.py`.
- `backend/app/services/classification_job_service.py`.
- `backend/app/services/dynamic_library_sync_service.py`.
- `backend/app/routes/admin/dynamic_library_sync.py`.
- `backend/app/routes/admin/dev_tools.py`.
- `frontend/templates/admin.html`.
- `frontend/static/js/admin.js`.
- `frontend/static/locales/zh-cn.json`.
- `frontend/static/locales/en.json`.
- `scripts/run_s2g1_ai_tagging_capability_probe.py`.
- `scripts/run_s2g_real1_bounded_ai_tagging_validation.py`.
- `scripts/run_phase46_fulllib_e1_production_import_ai_tagging.py`.
- `scripts/run_phase47_s2_baseline_full_import_ai_localization.py`.
- `scripts/phase_contracts/`.
- `tests/test_phase_contracts.py`.
- `docs/test-workflow.md`.
- `AGENTS.md`.

## Implementation Summary

- Added a durable AI tagging execution profile for local ONNX Runtime execution.
- Added bounded capability probe/report generation with provider fallback accounting.
- Extended dynamic sync with a manual sync dry-run planner and public-safe per-file ledger records.
- Added admin status/plan endpoints for later UI wiring without adding a production execute endpoint or button.
- Added `s2g_manual_sync_foundation_contract_v1` and focused positive/negative contract tests.

## AI Capability / Benchmark

- Probe attempted: `True`.
- Bounded synthetic samples: `2`.
- Local-files-only model resolution: `True`.
- Model cache status: `cached`.
- Provider benchmark wall-clock timeout: `60.0` seconds.
- GPU acceleration available: `True`.
- CPU fallback available/completed: `True` / `True`.
- Recommended provider: `DmlExecutionProvider`.
- Recommended batch size/concurrency: `2` / `1`.
- Estimated 25-item AI runtime: `1.95` seconds.
- Observed blocker, if any: `None`.

## Provider / Fallback Decision

- Requested provider preference: `['CUDAExecutionProvider', 'DmlExecutionProvider', 'CPUExecutionProvider']`.
- Selected provider: `DmlExecutionProvider`.
- Fallback occurred: `True`.
- Fallback reason: `unavailable_requested_providers=CUDAExecutionProvider`.
- CPU fallback available/completed: `True` / `True`.

## Execution Profile

- Backend: `onnxruntime`.
- Model: `wd-swinv2-tagger-v3`.
- Model repo: `SmilingWolf/wd-swinv2-tagger-v3`.
- Thresholds: `{'general_threshold': 0.35, 'character_threshold': 0.65, 'rating_threshold': 0.5, 'suggestion_threshold': 0.2}`.
- Batch/concurrency/timeouts: `2` / `1` / `60s` per image / `600s` job.
- Production writes enabled: `False`.

## Load-Control Policy

- Max batch size: `2`.
- Max concurrency: `1`.
- Per-image timeout: `60` seconds.
- Job timeout: `600` seconds.
- Single active AI execution guard: `True`.
- Failure isolation per image: `True`.
- No unbounded production loop: `True`.

## Provenance Policy

- AI tag source: `ai_wd`.
- Model/provider recorded: `wd-swinv2-tagger-v3` / `onnxruntime`.
- Confidence and thresholds recorded: `True` / `True`.
- Job id and dry-run/write mode recorded: `True` / `True`.
- Manual/locked tags protected: `True`.
- Suggestions versus confirmed tags recorded: `True`.
- Production writes enabled: `False`.

## Manual Sync Dry-Run Planner

- Planner implemented: `True`.
- Public-safe output: `True`.
- DB/source/app-storage mutation performed: `False` / `False` / `False`.
- Estimated import/classification/AI/localization count: `1` / `1` / `1` / `1`.
- State counts: `{'candidate': 0, 'skipped_unsupported': 1, 'skipped_placeholder': 0, 'skipped_zero_byte': 1, 'skipped_changing': 0, 'skipped_path_policy_error': 0, 'skipped_duplicate': 1, 'skipped_existing_media': 1, 'import_planned': 1, 'imported_in_test': 0, 'classified_in_test': 0, 'ai_tagged_in_test': 0, 'localization_scheduled_in_test': 0, 'failed': 1}`.
- Failure reasons: `{'corrupted_image': 1, 'duplicate_hash': 1, 'existing_media_hash': 1, 'unsupported_extension': 1, 'zero_byte_file': 1}`.

## Job / Ledger Foundation

- Job id present: `True`.
- Mode/state/trigger: `dry_run` / `planned` / `manual_operator`.
- Per-file public state records present: `True`.
- Ledger mode: `ephemeral_public_plan_current_phase`.
- Persistent table family available: `['blombooru_dynamic_source_roots', 'blombooru_dynamic_source_items', 'blombooru_dynamic_sync_runs', 'blombooru_dynamic_sync_run_items']`.

## Controlled Pipeline Foundation

- Pipeline status: `dry_run_planned`.
- Dry-run only this phase: `True`.
- Production execute enabled: `False`.
- Estimated runtime seconds: `0.448`.

- Stage `candidate_discovery`: state `completed`, writes `False`, production execution `False`.
- Stage `import`: state `planned`, writes `False`, production execution `False`.
- Stage `classification`: state `planned`, writes `False`, production execution `False`.
- Stage `ai_tagging`: state `planned`, writes `False`, production execution `False`.
- Stage `localization`: state `handoff_planned`, writes `False`, production execution `False`.
- Stage `summary`: state `planned`, writes `False`, production execution `False`.

## API Surface For Later UI

- Plan endpoint: `POST /api/admin/dynamic-library-sync/manual-sync/plan`.
- Status endpoint: `GET /api/admin/dynamic-library-sync/manual-sync/status`.
- Auth/admin policy: `require_admin_mode`.
- Production write endpoint enabled: `False`.
- Automatic execution endpoint added: `False`.

## Validation

- Focused tests passed before target claim: `True`.
- Runner completed: `True`.
- Public redaction passed: `True`.
- Browser validation required: `False`.
- Browser validation reason: backend route and service foundation only; no visible UI or frontend behavior changed.

## Not Executed In This Phase

- No production DB mutation, production media import, production classification, production AI tagging writes, or production localization writes.
- No source/iCloud mutation and no app-managed production storage mutation.
- No provider/gallery-dl/Pixiv/SauceNAO/Google calls and no LLM calls.
- No SourceConcept mutation, Entity truth writes, confirmed assignment writes, or production `media_tags` mutation.
- No automatic sync, scheduled sync, system service, startup task, or long-running daemon.
- No production execute endpoint or production execute runner.
- No final production acceptance; that remains S3A-M1.

## Why Production Writes Are Deferred

This phase creates production-capable planning code paths only where they are guarded and disabled by default. S3A-M1 must implement or wire the explicit execute path before production acceptance, then run a separate approval flow with production runtime identity, backup/recovery proof where applicable, a dry-run plan, a small explicit batch, and post-run diagnostics.

## Final Execute / Button Recommendation

- Placement: `both_launcher_and_web_admin`.
- Backend call: `POST /api/admin/dynamic-library-sync/manual-sync/plan first; S3A-M1 must implement or wire an explicit execute endpoint/runner before production acceptance`.
- Startup pending check: `lightweight_count_only_ok`.
- Intrusive launcher prompt: `False`.
- Safe default max files: `25`.
- Safe default max duration: `600` seconds.
- Safe default AI batch size/concurrency: `2` / `1`.
- Partial failure behavior: `complete successful items, keep failed/deferred item ledger visible, stop only on hard safety gate or failure budget`.
- First real acceptance batch size: `5`.
- Rollback/supersede/diagnostic plan: `ledger-driven retry/supersede; no source mutation; diagnose by job id, safe labels, reason counts, and provider provenance`.

## Safety / No-Mutation Proof

- `production_db_mutation`: `False`.
- `production_import`: `False`.
- `production_classification`: `False`.
- `production_ai_tagging_writes`: `False`.
- `production_localization_writes`: `False`.
- `source_icloud_mutation`: `False`.
- `app_managed_production_storage_mutation`: `False`.
- `external_provider_calls`: `False`.
- `gallery_dl_pixiv_saucenao_google_calls`: `False`.
- `sourceconcept_mutation`: `False`.
- `entity_truth_writes`: `False`.
- `confirmed_assignment_writes`: `False`.
- `production_media_tags_mutation`: `False`.
- `llm_calls`: `False`.
- `automatic_sync_enabled`: `False`.
- `scheduled_sync_enabled`: `False`.
- `system_service_enabled`: `False`.
- `startup_task_enabled`: `False`.
- `long_running_daemon_enabled`: `False`.
- `final_production_acceptance_completed`: `False`.

## Artifact Lifecycle

- `backend/app/services/job_control.py`: durable production code.
- `backend/app/services/dynamic_library_sync_service.py`: durable production code.
- `backend/app/routes/admin/dynamic_library_sync.py`: durable production code.
- `scripts/run_s2g_m1_ai_manual_sync_foundation.py`: reusable validation/safety tool.
- `scripts/phase_contracts/contract_registry.py`: reusable validation/safety tool.
- `scripts/phase_contracts/contract_checks.py`: reusable validation/safety tool.
- `docs/reports/s2g-m1-ai-manual-sync-foundation.md`: public report / handoff.
- `docs/reports/s2g-m1-ai-manual-sync-foundation-summary.json`: public report / handoff.

## Known Limitations

- No production execute endpoint or production execute runner is implemented in this PR.
- No final visible production manual-sync button or production acceptance is included in this PR.
- Persistent production execution ledgers remain disabled until S3A-M1 acceptance.
- Controlled pipeline support is a dry-run planning foundation; S3A-M1 must implement or wire explicit execution before production acceptance.
- Capability probe uses synthetic tensors and local model cache only; it does not prove full production throughput.

## Next Phase

S3A-M1 must implement or wire the explicit manual-sync execute path, then add final visible controls and run small-batch production acceptance.
