# S3A-PROD1: Operator-Triggered Production Incremental Sync Guard

Status: `target_met_with_bounded_write`.

Contract: `s3a_prod1_operator_incremental_sync_contract_v1`.

Public summary: `docs/reports/s3a-prod1-operator-incremental-sync-summary.json`.

## Scope

- Input mode: `input_path`.
- Selected count: `1`.
- Discovered supported files: `1`.
- Over-cap count: `0`.
- Max items: `1`.
- Full-library fallback: `False`.
- Public path redaction: `paths_and_filenames_redacted`.
- Protected input gate passed: `True`.
- Source safety gate passed: `True`.
- Source safety blockers: `0`.

## Preflight

- Model cache status: `cached`.
- Model download performed: `False`.
- Local files only: `True`.
- DirectML available: `True`.
- CPU fallback available: `True`.
- No full-library fallback: `True`.

## Import / Reuse

- Executed import write: `True`.
- Production confirmation present: `True`.
- Import write preconditions passed: `True`.
- Import write blockers: `[]`.
- Imported: `1`.
- Reused: `0`.
- Would import: `0`.
- Skipped: `0`.
- Failed: `0`.
- App-managed storage writes: `1`.

## Classification

- Executed: `True`.
- Dry run: `True`.
- Classified: `1`.
- Reused classification: `0`.
- Failed: `0`.
- Distribution: `{'unknown': 1}`.

## DirectML AI Tagging

- DirectML prewrite probe status: `completed`.
- DirectML prewrite probe provider: `DmlExecutionProvider`.
- DirectML write gate passed: `True`.
- DirectML write gate blockers: `[]`.
- Executed: `True`.
- Dry run: `False`.
- Actual provider: `DmlExecutionProvider`.
- Provider preference: `['DmlExecutionProvider', 'CPUExecutionProvider']`.
- Processed: `1`.
- Failed: `0`.
- Tags added: `8`.
- Suggestions added: `5`.
- Skipped locked: `0`.
- Ignored low confidence: `10845`.
- Media tags before/after/delta: `0` / `13` / `13`.
- Media with AI tags delta: `1`.
- First-time insertion proven: `True`.

## CPU Fallback

- Executed: `True`.
- Status: `completed`.
- Actual provider: `CPUExecutionProvider`.
- Media tag delta: `0`.

## Localization

- Candidate tags: `13`.
- Reused translations: `10`.
- New translations: `0`.
- Missing/deferred: `3`.
- Failed: `0`.
- External provider used: `False`.
- Deferred reason: `external_llm_provider_not_approved_for_s3a_prod1`.

## Public Redaction

- Passed: `True`.
- Finding count: `0`.

## Load Control

- Effective batch size: `1`.
- CPU intra/inter threads: `4` / `1`.
- Preprocess workers: `2`.
- Max concurrent AI jobs: `1`.

## Safety

- Single operator-triggered run only.
- Production automation remains disabled.
- Unattended S3B remains disabled.
- Provider/Pixiv/gallery-dl/SauceNAO/Google, SourceConcept/R1/R2/R1R, and Entity bridge were not run.
- Cleanup/delete/reset/drop/truncate was not run.
- Public reports are aggregate and path-redacted.
