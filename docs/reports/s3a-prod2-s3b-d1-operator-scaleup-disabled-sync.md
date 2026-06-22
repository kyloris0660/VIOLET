# S3A-PROD2/S3B-D1: Bounded Operator Sync Scale-Up and Disabled Unattended Sync Design

Status: `target_met_with_bounded_write`.

Contract: `s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1`.

Public summary: `docs/reports/s3a-prod2-s3b-d1-operator-scaleup-disabled-sync-summary.json`.

## Operator Scope

- Input mode: `input_path`.
- Selected input count: `3`.
- Discovered supported files: `3`.
- Over-cap count: `0`.
- Max items: `5`.
- Full-library fallback: `False`.
- Public path redaction: `paths_and_filenames_redacted`.

## Source File Preflight

- Evaluated: `3`.
- Eligible: `3`.
- Skipped: `0`.
- Failed: `0`.
- Cloud placeholder skipped: `0`.
- Zero-byte skipped: `0`.
- Unstable/recent skipped: `0`.
- Hidden/temp/system skipped: `0`.

## Import / Reuse

- Executed write: `True`.
- Exact confirmation present: `True`.
- Imported: `1`.
- Reused: `2`.
- Skipped: `0`.
- Failed: `0`.
- App-managed storage writes: `1`.
- Source/iCloud mutation: `False`.

## Classification

- Executed: `True`.
- Classified: `3`.
- Reused classification: `0`.
- Failed: `0`.
- Distribution: `{'anime': 2, 'unknown': 1}`.

## DirectML AI Tagging

- Executed: `True`.
- Dry run: `False`.
- Provider preference: `['DmlExecutionProvider', 'CPUExecutionProvider']`.
- Actual provider: `DmlExecutionProvider`.
- Processed: `3`.
- Failed: `0`.
- Tags added: `117`.
- Suggestions added: `52`.
- Skipped locked: `0`.
- Ignored low confidence: `32405`.
- Media tags delta: `23`.
- Media with AI tags delta: `1`.
- First-time insertion count: `1`.
- Prewrite DirectML probe status: `completed`.
- Prewrite DirectML probe provider: `DmlExecutionProvider`.
- Provider write gate passed: `True`.
- Provider write gate blockers: `[]`.

## CPU Fallback

- Executed: `True`.
- Status: `completed`.
- Actual provider: `CPUExecutionProvider`.
- Failed: `0`.
- Media tags delta: `0`.

## Localization

- Reused translations: `138`.
- New local/static translations: `0`.
- Missing/deferred: `1`.
- Failed: `0`.
- External/LLM provider used: `False`.

## Failure Budget

- Passed: `True`.
- Import failures: `0`.
- Classification failures: `0`.
- AI tagging failures: `0`.
- CPU fallback failures: `0`.
- Public redaction findings: `0`.

## Load Control

- Effective batch size: `2`.
- CPU intra/inter threads: `4` / `1`.
- Preprocess workers: `2`.
- Max concurrent AI jobs: `1`.

## S3B Disabled Scaffold

- Scaffold status: `disabled_scaffold_ready`.
- Unattended enabled: `False`.
- Scheduled enabled: `False`.
- Scheduler started: `False`.
- Background job started: `False`.
- Automatic writes started: `False`.
- Root count: `0`.
- Roots redacted: `True`.

## Cloud / iCloud Policy

- Conservative fallback: `skip unless local readable, nonzero, stable size and mtime, supported extension, and no cloud placeholder metadata risk`.
- Paths redacted: `True`.
- Source mutation: `False`.

## Public Redaction

- Passed: `True`.
- Finding count: `0`.

## Next Recommended Phase

- Move to a dedicated S3A ledger/watermark production design or a bounded follow-up scale run only after this PR is reviewed and merged.
