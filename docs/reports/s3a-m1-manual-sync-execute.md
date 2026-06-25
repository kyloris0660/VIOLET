# S3A-M1 Manual Sync Execute

Status: target_met_dev_test_ready.

S3A-M1 adds a guarded manual sync execute path behind a fresh dry-run plan, stable plan hash, exact operator confirmation, registered source root requirement, hydrated-only gate, stale-active-run recovery, item-level source failure recording, failure/duration budgets, and single active manual execute guard.

Implemented surfaces:

- Web Admin manual sync plan and execute panel.
- Launcher entry that opens the Web Admin manual sync section.
- Admin API endpoints for plan, execute, latest job, job status, and cancel.
- CLI runner for public-safe dry-run reports and guarded dev/test execute.
- Dev/test execute only; production small-batch acceptance remains pending separate exact operator approval.

Safety state:

- Production acceptance is pending separate operator approval.
- Localization scheduling is blocked in execute; the report state is blocked, not scheduled.
- Translation LLM background and auto-translation side-effect paths fail closed when LLM translation is enabled.
- CLIP classification is cache-only/local-only; uncached CLIP is skipped with `classification_model_uncached` and no Hugging Face/model download.
- Missing, unreadable, timed-out, or changed source files are recorded as per-item failures and continue within the configured failure budget.
- No production execute, import, classification, AI tag writes, localization writes, source mutation, iCloud mutation, LLM calls, provider calls, model downloads, automatic sync, scheduled sync, startup sync, or system service was authorized or performed.

Validation summary:

- Focused backend and phase-contract tests passed, including translation side-effect gates, cache-only CLIP handling, stale active run recovery, item-level source failures, and failure/duration budgets.
- Phase contract CLI passed for `s3a_m1_manual_sync_execute_contract_v1`.
- Public redaction contract passed for the summary JSON.
- Launcher contract, controller runner, and renderer behavior tests passed.
- Real browser validation passed with a controlled test server, Edge/Playwright, a temp local source root, dry-run plan generation, plan-hash display, exact confirmation phrase display, and execute-button enablement after the exact phrase was pasted. Execute was not clicked.

Production acceptance remains pending after dry-run plan generation and requires a separate exact operator approval before any production execute.
