# Phase 3.8d-I3 Recovery Plan

## Incident State

- Phase 3.8d execute status: `blocked`
- Partial staging preserved: `True`
- Known Cloud Files recall-risk count: `613`
- Cleanup dry-run status: `dry_run_passed`

## Cleanup Dry-run

- Target label: `phase_3_8d_partial_staging`
- File count: `97`
- Total bytes: `340159586`
- Actual delete performed: `False`
- Confirmation phrase required: `DELETE_PHASE38D_PARTIAL_STAGING`

## Controlled Read-probe / Hydration Policy

- Default enabled: `False`
- Approval required before run: `True`
- May trigger provider hydration: `True`
- Prefix read bytes: `1`
- Per-file timeout seconds: `10`
- Retry count: `0`
- CfHydratePlaceholder status: `future_enhancement_only`

## Same-bucket Backfill Policy

- Dry-run only: `True`
- Actual manifest replacement performed: `False`
- Same-bucket first: `True`
- Preserve selected total: `1000`
- Dry-run replacement count: `1`
- Dry-run unresolved count: `0`

### Dry-run Replacements

- Failed `source_row_0098.jpg` -> replacement `replacement_row_1029.png` in bucket `b02`

## Resume vs Cleanup + Rerun

- Recommended: `cleanup_plus_rerun`
- Reason: Only 97 files were copied and no DB/downstream state exists, so empty-target rerun is the lower-complexity recovery path after explicit cleanup approval.

## Safety

- actual_cleanup_delete_performed: `False`
- staging_copy_rerun: `False`
- read_probe_or_hydration_executed: `False`
- source_icloud_mutation: `False`
- app_managed_storage_mutation: `False`
- db_import: `False`
- classification: `False`
- ai_tagging: `False`
- localization: `False`
- entity_resolver: `False`
- similarity: `False`

## Privacy

- Passed: `True`
- Leaks: `[]`
- Local details artifact: `phase-3.8d-i3-recovery-local-details.json`
