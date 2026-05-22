# Phase 3.8d-I5c Backfill Application

## Summary

- Status: `backfill_applied`
- Success: `True`
- Selected total before: `1000`
- Selected total after: `1000`
- Selected total preserved: `True`
- Bucket distribution before: `{"b01": 63, "b02": 63, "b03": 63, "b04": 63, "b05": 63, "b06": 63, "b07": 63, "b08": 63, "b09": 62, "b10": 62, "b11": 62, "b12": 62, "b13": 62, "b14": 62, "b15": 62, "b16": 62}`
- Bucket distribution after: `{"b01": 63, "b02": 63, "b03": 63, "b04": 63, "b05": 63, "b06": 63, "b07": 63, "b08": 63, "b09": 62, "b10": 62, "b11": 62, "b12": 62, "b13": 62, "b14": 62, "b15": 62, "b16": 62}`

## Replacement Validation

- Status: `passed`
- Attempted: `2`
- Success count: `2`
- Failed count: `0`
- Bytes read: `620591`
- Duration seconds: `2.968`
- Failures by reason: `{}`

### Replacement Row 1029

- Row: `1029`
- Safe label: `replacement_row_1029.png`
- Bucket: `b02`
- Extension: `.png`
- Expected size: `403770`
- Metadata before likely cloud placeholder: `True`
- Metadata before recall_on_data_access: `True`
- Prefix read ok: `True`
- Full read ok: `True`
- Bytes read: `403771`
- Duration seconds: `2.343`
- Failure reason: `None`
- Metadata after likely cloud placeholder: `False`
- Metadata after recall_on_data_access: `False`
- Staging-copy-ready: `True`

### Replacement Row 1041

- Row: `1041`
- Safe label: `replacement_row_1041.jpg`
- Bucket: `b15`
- Extension: `.jpg`
- Expected size: `216819`
- Metadata before likely cloud placeholder: `False`
- Metadata before recall_on_data_access: `False`
- Prefix read ok: `True`
- Full read ok: `True`
- Bytes read: `216820`
- Duration seconds: `0.625`
- Failure reason: `None`
- Metadata after likely cloud placeholder: `False`
- Metadata after recall_on_data_access: `False`
- Staging-copy-ready: `True`

## Backfill Application

- Applied: `True`
- Active backfilled replacements: `[1029, 1041]`
- Unrecovered original rows: `[98, 881]`
- Local manifest written: `True`
- This is not silent skipping: unrecovered original rows are retained in the deferred cloud recovery ledger.

## Deferred Cloud Recovery Ledger

- Status: `deferred_not_abandoned`
- Original `source_row_0098.jpg` is deferred with reason `cloud_hydration_failed` and replacement `replacement_row_1029.png`.
  Final state: failed=`True`, retried=`True`, backfilled=`True`, deferred_for_cloud_recovery=`True`, imported_into_db=`False`, unresolved=`False`.
- Original `source_row_0881.png` is deferred with reason `cloud_hydration_failed` and replacement `replacement_row_1041.jpg`.
  Final state: failed=`True`, retried=`True`, backfilled=`True`, deferred_for_cloud_recovery=`True`, imported_into_db=`False`, unresolved=`False`.

## Ingestion Observability Principle

- Future production ingestion must record a per-run final state for every source item.
- Reports must answer which source items succeeded, failed, retried, backfilled, deferred for cloud recovery, imported into DB, excluded as ineligible, or remain unresolved.
- Failed cloud-backed items must not be mixed with successfully imported items or hidden behind aggregate totals.
- Reporting must be scoped to the current run, manifest, or job rather than only global library totals.
- I5c records this principle and the current deferred cloud recovery ledger only; it does not add a DB migration or full production ledger.

## Safety

- source_content_read_for_replacement_validation_only: `True`
- provider_side_hydration_may_have_occurred: `True`
- source_file_content_write_mutation: `False`
- staging_copy: `False`
- staging_write: `False`
- db_import: `False`
- classification: `False`
- ai_tagging: `False`
- localization: `False`
- entity_resolver: `False`
- similarity: `False`
- cleanup_delete: `False`
- manifest_modified_in_repo: `False`
- backfill_applied_to_local_manifest_artifact_only: `True`
- app_managed_storage_mutation: `False`
- push_main: `False`
- merge: `False`

## Privacy

- Passed: `True`
- Leaks: `[]`
- Local manifest artifact: `phase-3.8d-i5c-backfilled-selected-manifest.csv`
- Local ledger artifact: `phase-3.8d-i5c-deferred-cloud-recovery-ledger.json`
- Local validation details: `phase-3.8d-i5c-backfill-validation-details.json`

## Next Step

Proceed to Phase 3.8d staging copy retry planning if the PR is reviewed and merged.
