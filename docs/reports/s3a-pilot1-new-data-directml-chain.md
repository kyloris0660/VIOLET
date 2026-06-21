# S3A-PILOT1: Controlled New Data DirectML AI Tagging Chain

Status: `target_met_with_bounded_write`.

Contract: `s3a_pilot1_new_data_directml_chain_contract_v1`.

Public summary: `docs/reports/s3a-pilot1-new-data-directml-chain-summary.json`.

## Scope

- Input mode: `input_path`.
- Selected sample count: `1`.
- Discovered supported files: `1`.
- Over-cap count: `0`.
- Max items: `1`.
- Full-library fallback: `False`.
- Public path redaction: `paths_and_filenames_redacted`.

## Import / Reuse

- Executed import write: `True`.
- Import write requested: `True`.
- Import exact confirmation present: `True`.
- Import write preconditions passed: `True`.
- Import write blockers: `[]`.
- Imported: `1`.
- Reused: `0`.
- Would import: `0`.
- Skipped: `0`.
- Failed: `0`.
- Downstream media count: `1`.

## Classification

- Executed: `True`.
- Dry run: `True`.
- Classified: `1`.
- Reused classification: `0`.
- Failed: `0`.
- Distribution: `{'unknown': 1}`.

## DirectML AI Tagging

- Executed: `True`.
- Dry run: `False`.
- AI tagging exact confirmation present: `True`.
- Actual provider: `DmlExecutionProvider`.
- Processed: `1`.
- Failed: `0`.
- Tags added: `10`.
- Suggestions added: `4`.
- Skipped locked: `0`.
- Ignored low confidence: `10844`.
- Media tags before/after/delta: `0` / `14` / `14`.
- First-time insertion proven: `True`.

## CPU Fallback

- Executed: `True`.
- Status: `completed`.
- Actual provider: `CPUExecutionProvider`.
- Media tag delta: `0`.

## Localization

- Attempted validation: `True`.
- Reused translations: `9`.
- New translations: `0`.
- Failed: `0`.
- External provider used: `False`.
- Deferred reason: `external_llm_provider_not_approved_for_s3a_pilot1`.

## Public Redaction

- Passed: `True`.
- Finding count: `0`.

## Load Control

- Effective batch size: `1`.
- CPU intra/inter threads: `4` / `1`.
- Preprocess workers: `2`.
- Max concurrent AI jobs: `1`.

## Safety

- Operator-triggered pilot only.
- Production S3A automation remains disabled.
- Unattended S3B remains disabled.
- Provider/Pixiv/gallery-dl/SauceNAO/Google, SourceConcept/R1/R2/R1R, and Entity bridge were not run.
- Cleanup/delete/reset/drop/truncate was not run.
- Public reports are aggregate and path-redacted.
