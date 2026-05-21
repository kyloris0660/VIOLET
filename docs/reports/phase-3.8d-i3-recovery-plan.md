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
- Expected staging root explicit: `True`
- Target under expected staging root: `True`
- Protected roots valid: `True`
- Staging log exact target/count/copy correlation: `True`
- Staging log files copied present: `True`
- Staging log bytes copied present: `True`
- Actual delete performed: `False`
- Confirmation phrase required: `DELETE_PHASE38D_PARTIAL_STAGING`

Cleanup dry-run is not approved for actual delete unless the expected staging root is explicit, all required protected roots exist and resolve as directories, the staging log has exact target + expected count + copied count + copied bytes from the same run entry, and a separate user/ChatGPT cleanup approval is granted. Missing copied count or copied bytes fails closed.

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

## Can this handle the original row 98 failure?

Short answer: I3 defines the recovery path for row 98, but it has not yet proven that row 98 can be hydrated or copied.
Row 98 is not abandoned; it remains the preferred original candidate unless bounded recovery fails after explicit approval.

Current I3 status:
- row 98 abandoned: `False`
- row 98 retried: `False`
- read-probe/hydration executed: `False`
- actual backfill manifest replacement applied: `False`
- cleanup performed: `False`
- proves row 98 can be hydrated/copied: `False`

Planned recovery path:
1. If the user approves cleanup, clean only the dedicated partial staging target after dry-run plus confirmation.
2. If the user approves controlled read-probe/hydration, test row 98 and other recall-risk rows with bounded prefix read, timeout, and retry.
3. If row 98 read-probe/hydration succeeds, keep original row 98 in the selected set and rerun staging copy from a clean target.
4. If row 98 fails after bounded hydrate/read-probe, use the same-bucket backfill candidate or equivalent while preserving selected_total=1000 and temporal bucket distribution.
5. DB import remains forbidden until staging copy and post-copy audit fully pass.

Policy notes:
- Manual download is the formal solution: `False`
- Skip-only is acceptable: `False`
- Backfill is primary strategy: `False`
- Backfill is last resort after bounded recovery failure: `True`
- Cloud placeholder is permanent failure: `False`

Manual download is not the formal solution because V.I.O.L.E.T. must handle iCloud-backed libraries at scale through deterministic availability gates. Skip-only is not acceptable because cloud placeholder status means the file needs a controlled availability workflow, not silent abandonment. Backfill is only a fallback after bounded read-probe/hydration failure so the original cloud-backed item remains usable whenever the provider can make it readable.

Dry-run row 98 backfill candidate:
- `source_row_0098.jpg` -> `replacement_row_1029.png` in bucket `b02`

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
