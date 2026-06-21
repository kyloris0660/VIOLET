# S2G-1X GPU AI Tagging Probe and S3A Integration Decision

## Summary

- Contract: `s2g1x_probe_contract_v1`.
- Status: `target_met`.
- Target met / safe to merge: `True` / `True`.
- Current app WD backend: `CPUExecutionProvider`.
- Configured model: `wd-swinv2-tagger-v3`.
- Model cached locally: `True`.
- Network model download performed: `False`.
- Decision: `share_foundation_split_production_execution`.

## Runtime Environment

- Python executable public name: `python.exe`.
- Python version: `3.12.0`.
- ONNX Runtime version: `1.23.2`.
- ONNX Runtime device: `CPU`.
- ONNX Runtime providers: `AzureExecutionProvider, CPUExecutionProvider`.

## Provider Capability

| Provider | Available | Practical | Loaded | Benchmark status | Items/sec |
| --- | --- | --- | --- | --- | --- |
| CUDA | False | False | False | not_available | N/A |
| DirectML | False | False | False | not_available | N/A |
| CPU | True | True | True | completed | 1.1534 |

## Threshold And Model Config

- General threshold: `0.35`.
- Character threshold: `0.65`.
- Rating threshold: `0.5`.
- Suggestion threshold: `0.2`.
- Batch max items setting: `10`.

## Load-Control Evidence

- Conservative probe batch size: `2`.
- Worker count: `1`.
- Max concurrent jobs: `1`.
- CPU intra/inter op thread cap: `4` / `1`.
- Current risk flags: `ai_tag_job_has_cancel_but_no_durable_pause_resume, current_app_forces_cpu_provider, current_app_uses_all_cpu_threads_by_default, pause_resume_not_yet_implemented, provider_backend_not_persisted_in_media_tag_provenance, s3a_requires_shared_per_item_ledgers_before_production_execution`.

## S2G/S3A Decision

- S2G and S3A should share one job/progress/throttle/ledger foundation.
- Production S3A execution should stay split into a later operator-approved phase.
- Recommended boundary: `background_job_runner_with_admin_api_trigger_and_cli_probe`.
- Recommended next phase: `combined_s2g_s3a_foundation_before_any_production_s3a_execution`.

## Safety

- No DB connection or production DB writes.
- No production import, classification, AI tagging, or localization.
- No production S3A execution or unattended S3B automation.
- No provider/Pixiv/gallery-dl/SauceNAO/Google calls.
- No SourceConcept, Entity bridge, confirmed assignment, cleanup, delete, reset, drop, or truncate.
