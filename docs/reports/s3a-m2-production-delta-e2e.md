# S3A-M2 Production Delta Manual Sync E2E + GPU Telemetry

## Identity

- Phase: `S3A-M2` / `Production Delta Manual Sync E2E + GPU Telemetry`.
- Status: `target_met`.
- Contract: `s3a_m2_production_delta_e2e_contract_v1`; target met: `True`.
- Branch: `codex/s3a-m2-production-delta-e2e-gpu-telemetry`.
- Head SHA: `51c3e33001d3a0d1d675657fb5d730dd7901d8d4`.
- Production acceptance performed: `True`.
- Source root: `153684ac810c2191`.

## Counts

- Cap used: `1000`; cap exceeded: `False`.
- Dry-run total/import: `453` / `300`.
- Execute total/imported: `453` / `300`.
- Classification count/failures: `300` / `0`.
- AI tagging count/failures: `300` / `0`.
- Localization translated/failures/skipped: `3` / `0` / `25`.
- Localization provider/calls/retries: `fallback(primary->fallback)` / `1` / `0`.
- Skipped/failed/deferred: `{'deferred': 0, 'failed': 0, 'skipped_duplicate': 0, 'skipped_existing_media': 19, 'skipped_placeholder': 36, 'skipped_unsupported': 98}`.

## Telemetry

- GPU provider: `DmlExecutionProvider`; GPU validation: `passed`.
- GPU name: `NVIDIA GeForce RTX 4070 Ti`.
- Peak GPU memory MiB: `3752.0`; peak GPU util: `65.0`.
- Runtime seconds: `392.343`; stage durations: `{'dry_run_plan': 0.016, 'init': 1.187, 'localization': 5.016, 'manual_execute_import_classification_ai': 386.031, 'summary': 0.031}`.

## Validation

- Ledger consistency: `passed`; represented items: `453` / `453`.
- DB count delta: media `300`, source items `369`.
- Public redaction: `True`; findings: `0`.
- Launcher/Web Admin: `passed`; browser: `msedge`; dry-run clicked: `True`; execute clicked: `False`.
- Latest job observed by UI/API: run `7`, status `completed`, imported `300`.

## Safety

- Source/iCloud mutation attempted: `False`.
- Automatic/scheduled/startup/system-service sync enabled: `False` / `False` / `False` / `False`.
- Provider/source expansion run: `False`.
- Private paths or hashes in public report: `False`.

## Not Completed

- Automatic/scheduled/startup/system-service sync was not implemented; it remains out of scope.
- Pixiv/provider/gallery-dl/SauceNAO/Google/source metadata expansion was not run.
- SourceConcept/Entity bridge work was not run.

No source paths, filenames, content hashes, API keys, prompts, source URLs, or original image bytes are included in this public report.
