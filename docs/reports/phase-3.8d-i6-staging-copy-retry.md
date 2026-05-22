# Phase 3.8d-I6 Staging Copy Retry

## Summary

- Status: `blocked_dry_run_failed`
- Success: `False`
- Backfilled manifest present: `True`
- Deferred ledger present: `True`
- Duration seconds: `0.969`

## Manifest Validation

- Status: `passed`
- Selected total: `1000`
- Expected selected total: `1000`
- Failed rows absent: `True`
- Replacement rows present: `[1029, 1041]`
- Bucket distribution unchanged from I5c: `True`
- Extension distribution: `{".gif": 1, ".jpeg": 10, ".jpg": 816, ".png": 173}`
- Expected total bytes: `3109318484`
- Duplicate source paths: `0`
- Duplicate target paths: `0`
- Deferred ledger rows: `[98, 881]`
- Errors: `[]`

## Pre-copy Target Check

- Status: `passed`
- Target label: `phase_3_8d_i6_backfilled_staging_target`
- Target exists: `True`
- Target is directory: `True`
- Target under expected staging root: `True`
- Target equals expected staging root: `True`
- File count before copy: `0`
- Bytes before copy: `0`
- Hazard count: `0`
- Errors: `[]`

## Dry-run

- Status: `failed`
- Stage pilot valid: `False`
- Expected copy rows: `1000`
- Copy rows: `1000`
- Source files missing: `0`
- Unsupported extensions: `0`
- Target escapes: `0`
- Target collisions: `0`
- Cloud risk files: `566`
- Cloud risk by reason: `{"cloud_recall_on_data_access": 566}`
- Rows 98/881 absent: `True`
- Rows 1029/1041 present: `[1029, 1041]`
- Errors: `["stage_pilot_dry_run_invalid", "cloud_availability_files_nonzero"]`

## Actual Staging Copy

- Attempted: `False`
- Status: `not_run_dry_run_failed`
- Copied files: `0`
- Copied bytes: `0`
- Failed: `0`
- Failed safe label: `None`
- Failure reason code: `None`

## Post-copy Audit

- Status: `not_run`
- Expected file count: `1000`
- Actual file count: `0`
- Expected total bytes: `None`
- Actual total bytes: `0`
- Missing file count: `None`
- Unexpected file count: `None`
- Size mismatch count: `None`

## DB No-mutation Proof

- Available: `True`
- Before: `{"ai_jobs": 46, "classification_jobs": 14, "media": 995, "media_tags": 53354, "translation_jobs": 15}`
- After: `{"ai_jobs": 46, "classification_jobs": 14, "media": 995, "media_tags": 53354, "translation_jobs": 15}`
- Delta: `{"ai_jobs": 0, "classification_jobs": 0, "media": 0, "media_tags": 0, "translation_jobs": 0}`
- Unchanged: `True`

## Safety

- Source/iCloud write mutation: `False`
- Source content read for staging copy only: `False`
- Provider-side hydration/cache may have occurred: `False`
- App-managed storage mutation: `False`
- DB import: `False`
- Classification: `False`
- AI tagging: `False`
- Localization: `False`
- Entity Resolver: `False`
- Similarity: `False`
- Cleanup/delete: `False`
- Push main: `False`
- Merge: `False`

## Privacy

- Passed: `True`
- Leaks: `[]`

## Next Step

Stop: staging copy retry did not complete; do not import DB or run downstream jobs.
