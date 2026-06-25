# S3A-M1 Manual Sync Execute

Status: target_met_dev_test_ready.

S3A-M1 adds a guarded manual sync execute path behind a fresh dry-run plan whose hash is bound to `job.created_at`, exact operator confirmation, registered source root requirement, hydrated-only gate, hard execute cap of 5 files, stale-active-run recovery, item-level source/AI failure recording, failure/duration budgets, and single active manual execute guard.

Implemented surfaces:

- Web Admin manual sync plan and execute panel.
- Launcher entry that opens the Web Admin manual sync section.
- Admin API endpoints for plan, execute, latest job, job status, and cancel.
- CLI runner for public-safe dry-run reports and guarded dev/test execute; default runner reports go under `.local_manifests/s3a_m1_manual_sync_execute/` so committed contract summaries are only overwritten with explicit `--report-json` / `--report-md`.
- Dev/test execute only; production small-batch acceptance remains pending separate exact operator approval.
- Generic sync serializers, dashboard state, pending summary, latest job, and job status redacts private execute snapshots.

Safety state:

- Production acceptance is pending separate operator approval.
- Localization scheduling is blocked in execute; the report state is blocked, not scheduled.
- Translation LLM background, auto-translation, enabled LLM capability, and live/idle background worker states fail closed.
- Manual execute fails closed with `manual_sync_execute_max_files_exceeded` when requested `max_files` exceeds the S3A-M1 execute cap.
- Manual execute fails closed with `ai_job_active_blocks_manual_sync_execute` or `classification_job_active_blocks_manual_sync_execute` while background AI tagging or classification jobs are active.
- CLIP classification is cache-only/local-only; uncached CLIP is skipped with `classification_model_uncached` and no Hugging Face/model download.
- Heuristic classification runs after AI tagging so it consumes fresh `ai_wd` tags; CLIP keeps the cache-only classification path.
- AI tagger model-cache or inference exceptions are recorded per item with stable reasons and do not fail the whole run unless the failure budget stops it.
- Missing, unreadable, timed-out, or changed source files are recorded as per-item failures and continue within the configured failure budget.
- Failure/cancel/duration stops report unprocessed counts and preserve remaining pending import work instead of zeroing it.
- No production execute, import, classification, AI tag writes, localization writes, source mutation, iCloud mutation, LLM calls, provider calls, model downloads, automatic sync, scheduled sync, startup sync, or system service was authorized or performed.

Validation summary:

- Focused backend and phase-contract tests passed, including private serializer redaction, live translation worker gates, execute max-files cap, active AI/classification job gates, runner default output paths, plan replay rejection, heuristic classification ordering, cache-only CLIP handling, stale active run recovery, item-level source/AI failures, and failure/duration budgets.
- Phase contract CLI passed for `s3a_m1_manual_sync_execute_contract_v1`.
- Public redaction contract passed for the summary JSON.
- Launcher contract, controller runner, and renderer behavior tests passed.
- Real browser validation passed with a controlled test server, Edge/Playwright, a temp local source root, dry-run plan generation, plan-hash display, exact confirmation phrase display, and execute-button enablement after the exact phrase was pasted. Execute was not clicked.

Production acceptance remains pending after dry-run plan generation and requires a separate exact operator approval before any production execute.
