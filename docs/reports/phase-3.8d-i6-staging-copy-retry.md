# Phase 3.8d-I6 Staging Copy Retry

## Summary

- Status: `completed_with_item_failures`
- Success: `True`
- Backfilled manifest present: `True`
- Deferred ledger present: `True`
- Setup errors: `[]`
- Duration seconds: `638.86`

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
- Target root hazard count: `0`
- Errors: `[]`

## Cloud-aware Copy Policy

- Enabled: `True`
- Confirmation phrase accepted: `True`
- Recall-risk rows are metadata-level cloud-backed rows, not proven failures: `True`
- Failure budget: `{"max_consecutive_failures": 10, "max_failure_rate": 0.05, "max_item_failures": 20, "max_same_reason_failures": 20}`

## Dry-run

- Status: `passed_with_item_level_risks`
- Stage pilot valid: `False`
- Expected copy rows: `1000`
- Copy rows: `1000`
- Source files missing: `0`
- Unsupported extensions: `0`
- Target escapes: `0`
- Target collisions: `0`
- Cloud risk files: `566`
- Item-level failures allowed: `True`
- Cloud recall allowed by policy: `True`
- Cloud recall allowed reasons: `['cloud_recall_on_data_access']`
- Cloud recall blocking errors: `[]`
- Non-cloud dry-run errors: `[]`
- Structural dry-run errors: `[]`
- Item-level risk counts: `{"cloud_risk_files": 566, "source_files_missing": 0, "unsupported_extensions": 0}`
- Cloud risk by reason: `{"cloud_recall_on_data_access": 566}`
- Rows 98/881 absent: `True`
- Rows 1029/1041 present: `[1029, 1041]`
- Errors: `[]`

## Actual Staging Copy

- Attempted: `True`
- Status: `completed_with_item_failures`
- Attempted count: `1000`
- Staged success count: `994`
- Item failure count: `6`
- Failure rate: `0.006`
- Failure budget exceeded: `False`
- Max consecutive failures observed: `3`
- Failure reason distribution: `{"cloud_network_unavailable": 6}`
- Copied files: `994`
- Copied bytes: `3063523992`
- Failed rows: `[{"row_id": 799, "safe_label": "source_row_0799.jpg", "bucket": "b13", "extension": ".jpg", "reason": "cloud_network_unavailable", "status": "failed_cloud_hydration"}, {"row_id": 839, "safe_label": "source_row_0839.jpg", "bucket": "b14", "extension": ".jpg", "reason": "cloud_network_unavailable", "status": "failed_cloud_hydration"}, {"row_id": 922, "safe_label": "source_row_0922.jpg", "bucket": "b15", "extension": ".jpg", "reason": "cloud_network_unavailable", "status": "failed_cloud_hydration"}, {"row_id": 970, "safe_label": "source_row_0970.png", "bucket": "b16", "extension": ".png", "reason": "cloud_network_unavailable", "status": "failed_cloud_hydration"}, {"row_id": 971, "safe_label": "source_row_0971.png", "bucket": "b16", "extension": ".png", "reason": "cloud_network_unavailable", "status": "failed_cloud_hydration"}, {"row_id": 972, "safe_label": "source_row_0972.jpg", "bucket": "b16", "extension": ".jpg", "reason": "cloud_network_unavailable", "status": "failed_cloud_hydration"}]`

## Post-copy Audit

- Status: `completed_with_item_failures`
- Expected selected total: `1000`
- Expected success file count: `994`
- Actual file count: `994`
- Expected total bytes: `3063523992`
- Actual total bytes: `3063523992`
- Known failed item count: `6`
- Missing due to known failed items: `6`
- Missing file count: `0`
- Unexpected file count: `0`
- Size mismatch count: `0`
- Rows 98/881 staged: `[]`
- Rows 1029/1041 staged: `[1029, 1041]`
- Errors: `[]`

## DB Import Eligibility

- Eligible for full 1000 import planning: `False`
- Eligible for partial import planning: `True`
- Full import blocked reason: `staged_success_count_less_than_selected_total`
- Future DB import must use item ledger staged-success set: `True`
- Failed rows must not be imported: `True`
- Partial import requires separate approval: `True`

## DB No-mutation Proof

- Available: `True`
- Before: `{"ai_jobs": 46, "classification_jobs": 14, "media": 995, "media_tags": 53354, "translation_jobs": 15}`
- After: `{"ai_jobs": 46, "classification_jobs": 14, "media": 995, "media_tags": 53354, "translation_jobs": 15}`
- Delta: `{"ai_jobs": 0, "classification_jobs": 0, "media": 0, "media_tags": 0, "translation_jobs": 0}`
- Unchanged: `True`

## Safety

- Source/iCloud write mutation: `False`
- Source content read for staging copy only: `True`
- Provider-side hydration/cache may have occurred: `True`
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

Stop: item failures stayed within budget; user/ChatGPT must approve backfill or partial import planning.
