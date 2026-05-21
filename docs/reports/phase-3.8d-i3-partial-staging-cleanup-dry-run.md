# Phase 3.8d-I3 Partial Staging Cleanup Dry-run

## Summary

- Status: `dry_run_passed`
- Target label: `phase_3_8d_partial_staging`
- Target exists: `True`
- Target is directory: `True`
- Expected staging root explicit: `True`
- Expected staging root is directory: `True`
- Target under expected staging root: `True`
- Target equals expected staging root: `True`
- Target equals expected staging root allowed: `True`
- Dedicated Phase 3.8d target: `True`
- Actual copied file count: `97`
- Expected partial file count: `97`
- Requested expected copy count: `1000`
- Total bytes: `340159586`
- Expected total bytes: `340159586`

## Dedicated Target Evidence

- Cleanup proof basis: `manifest_filesystem_proof`
- Manifest/filesystem check available: `True`
- Manifest/filesystem check passed: `True`
- Expected manifest file count: `97`
- Expected manifest size available count: `97`
- Expected manifest total bytes: `340159586`
- Expected total bytes source: `manifest`
- Staging copy log present: `True`
- Staging log authorization role: `not_used_for_cleanup_authorization`
- Staging log diagnostic status: `matches_manifest_filesystem`
- Staging copy log target/count/copy diagnostic match: `True`
- Staging copy log target exact match: `True`
- Staging copy log expected count correlated: `True`
- Staging copy log files copied present: `True`
- Staging copy log bytes copied present: `True`
- Staging copy log files copied matches: `True`
- Staging copy log bytes copied matches: `True`
- Staging copy log matching entry found: `True`
- Staging copy log entry count: `1`
- Relative target handling: `absolute_target`
- Expected partial file count matches: `True`
- Expected total bytes matches: `True`
- Unexpected files check available: `True`
- Unexpected files check passed: `True`
- Unexpected file count: `0`
- Missing expected file count: `0`
- Size mismatch file count: `0`
- Duplicate expected target count: `0`
- Invalid manifest target count: `0`
- Identity mismatch reasons: `[]`

## Safety Proof

- Not source/iCloud: `True`
- Not repo: `True`
- Not app-managed storage: `True`
- Invalid protected root labels: `[]`
- Missing required protected labels: `[]`
- Unsafe reasons: `[]`

Cleanup authorization is based on explicit target/root inputs, valid protected roots, target/protected-root disjointness, manifest-derived expected staging files, and an actual filesystem scan of the target. Staging logs are diagnostic only and are not used for cleanup authorization. Actual cleanup still requires separate user/ChatGPT approval.

## Extension Distribution

- `.gif`: `1`
- `.jpeg`: `6`
- `.jpg`: `68`
- `.png`: `22`

## Sample Safe Labels

- `staging_file_0001.png`
- `staging_file_0002.jpg`
- `staging_file_0003.jpg`
- `staging_file_0004.jpg`
- `staging_file_0005.jpg`
- `staging_file_0006.jpg`
- `staging_file_0007.jpg`
- `staging_file_0008.jpg`
- `staging_file_0009.jpg`
- `staging_file_0010.jpg`

## Deletion Plan

- Dry-run only: `True`
- Actual delete performed: `False`
- Would delete only under target root: `True`
- Would delete file count: `97`
- Would delete bytes: `340159586`
- Execute requested: `False`
- Execute allowed: `False`
- Confirmation phrase required: `DELETE_PHASE38D_PARTIAL_STAGING`
- Separate user approval required: `True`

## Privacy

- This report uses only safe labels.
- Full local paths remain only in ignored local artifacts.
