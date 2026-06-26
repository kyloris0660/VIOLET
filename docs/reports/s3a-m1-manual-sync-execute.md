# S3A-M1 Manual Sync Execute

Status: target_met_dev_test_ready.

S3A-M1 adds a guarded manual sync execute path behind a fresh dry-run plan whose hash is bound to `job.created_at`, exact operator confirmation, registered source root requirement, hydrated-only gate, default/effective execute cap of 5 files, stale-active-run recovery, item-level source/AI failure recording, failure/duration budgets, and single active manual execute guard.

Implemented surfaces:

- Web Admin manual sync plan and execute panel.
- Web Admin keeps separate update-check and manual-execute max-files inputs; manual execute defaults to 5, while normal update checks keep their existing default scan scope.
- Launcher entry that opens the Web Admin Content tab and Dynamic Library Sync section.
- Admin API endpoints for plan, execute, latest job, job status, and cancel.
- CLI runner for public-safe dry-run reports and guarded dev/test execute; default runner reports go under `.local_manifests/s3a_m1_manual_sync_execute/` so committed contract summaries are only overwritten with explicit `--report-json` / `--report-md`, execute reports use the operator-approved plan hash/timestamp, and standalone runs initialize the canonical DB session before opening a session.
- Dev/test execute only; production small-batch acceptance remains pending separate exact operator approval.
- Generic sync serializers, dashboard state, pending summary, latest job, and job status redacts private execute snapshots.

Safety state:

- Production acceptance is pending separate operator approval.
- Localization scheduling is blocked in execute; the report state is blocked, not scheduled.
- Translation LLM background, auto-translation, enabled LLM capability, and live/idle background worker states fail closed.
- Manual execute dry-run and execute defaults are aligned at `max_files=5`; over-cap requests fail closed with `manual_sync_execute_max_files_exceeded`, while the normal update-check flow is not constrained by the execute cap.
- Manual execute fails closed with `ai_job_active_blocks_manual_sync_execute` or `classification_job_active_blocks_manual_sync_execute` while background AI tagging or classification jobs are active or queued in pending/running/cancelling DB state.
- AI tagging and content-classification start paths fail closed with `manual_sync_execute_active_blocks_ai_job` or `manual_sync_execute_active_blocks_classification_job` while manual sync execute is active or queued in pending/running/cancelling DB state.
- Plan hashing is deterministic across source directory traversal order; unchanged trees are not rejected because of filesystem directory enumeration order alone.
- CLIP classification is cache-only/local-only; uncached CLIP is skipped with `classification_model_uncached` and no Hugging Face/model download.
- Heuristic classification runs after AI tagging only when fresh `ai_wd` tags are available; failed/skipped/disabled AI tagging defers classification with stable public reasons instead of writing an `unknown` content class.
- AI tagger model-cache, file-missing, or inference errors are recorded per item with stable public reasons and do not fail the whole run unless the failure budget stops it; raw returned error text is not used as a public reason or outcome key.
- AI-generated character/proper-noun tags written by manual execute remain suggestions/review-only, and no SourceConcept, Entity truth, or confirmed entity assignment is created from those AI predictions.
- Missing, unreadable, timed-out, or changed source files are recorded as per-item failures and continue within the configured failure budget.
- Import attempts commit an `import_in_progress` DynamicSyncRunItem ledger row before media storage/DB writes, then update the same row on success or failure.
- Failure/cancel/duration stops report unprocessed counts and materialize deferred per-item ledger rows without reading, hashing, copying, or mutating remaining source files.
- No production execute, import, classification, AI tag writes, localization writes, source mutation, iCloud mutation, LLM calls, provider calls, model downloads, automatic sync, scheduled sync, startup sync, or system service was authorized or performed.

Validation summary:

- Focused backend and phase-contract tests passed, including private serializer redaction, live translation worker gates, aligned dry-run/execute defaults, execute max-files cap, active/queued reciprocal AI/classification/manual-execute job gates, separate update-check versus execute limits, runner default output paths and standalone DB initialization, approved-plan execute reporting, sanitized AI returned errors, AI proper-noun suggestion-only writes, deterministic directory-order plan hashing, plan replay rejection, heuristic classification ordering/defer behavior, cache-only CLIP handling, stale active run recovery, item-level source/AI failures, durable import pre-ledger rows, deferred unprocessed ledger rows, and failure/duration budgets.
- Phase contract CLI passed for `s3a_m1_manual_sync_execute_contract_v1`.
- Public redaction contract passed for the summary JSON.
- Launcher contract, controller runner, and renderer behavior tests passed.
- Real browser validation passed with a controlled test server, Edge/Playwright, a temp local source root, dry-run plan generation, plan-hash display, exact confirmation phrase display, and execute-button enablement after the exact phrase was pasted. Execute was not clicked.

Production acceptance remains pending after dry-run plan generation and requires a separate exact operator approval before any production execute.
