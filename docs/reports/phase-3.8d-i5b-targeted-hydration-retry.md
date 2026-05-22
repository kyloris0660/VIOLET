# Phase 3.8d-I5b Targeted Hydration Retry

## Summary

- Status: `targeted_retry_failed`
- Success: `False`
- Manifest selected total: `1000`
- Target rows: `[98, 881]`
- Attempted: `2`
- Success count: `0`
- Failed count: `2`
- Bytes read: `0`
- Duration seconds: `699.062`
- Failure reasons: `{"cloud_hydration_failed": 2}`

## Retry Policy

- Prefix read bytes: `1`
- Prefix timeout seconds: `30`
- Prefix retries: `2`
- Full read timeout seconds: `180`
- Full read retries: `2`
- Retry wait seconds: `10`
- Full read runs even if prefix times out: `True`
- CfHydratePlaceholder called: `False`

## Row 98 Result

- Safe label: `source_row_0098.jpg`
- Bucket: `b02`
- Extension: `.jpg`
- Expected size: `256931`
- Metadata before likely cloud placeholder: `True`
- Metadata before recall_on_data_access: `True`
- Prefix read ok: `False`
- Prefix attempts: `3`
- Full read ok: `False`
- Full read attempts: `3`
- Full read ran even if prefix failed: `True`
- Bytes read: `0`
- Duration seconds: `365.234`
- Failure reason: `cloud_hydration_failed`
- Metadata after recall_on_data_access: `True`
- Still recall_on_data_access: `True`
- Staging-copy-ready: `False`

## Row 881 Result

- Safe label: `source_row_0881.png`
- Bucket: `b15`
- Extension: `.png`
- Expected size: `3447687`
- Metadata before likely cloud placeholder: `True`
- Metadata before recall_on_data_access: `True`
- Prefix read ok: `False`
- Prefix attempts: `3`
- Full read ok: `False`
- Full read attempts: `3`
- Full read ran even if prefix failed: `True`
- Bytes read: `0`
- Duration seconds: `333.828`
- Failure reason: `cloud_hydration_failed`
- Metadata after recall_on_data_access: `True`
- Still recall_on_data_access: `True`
- Staging-copy-ready: `False`

## Backfill Dry-run

- Applied: `False`
- Replacement count: `2`
- Unresolved count: `0`
- Backfill remains dry-run-only and was not applied to the manifest.
- Failed `source_row_0098.jpg` -> replacement `replacement_row_1029.png` in bucket `b02`
- Failed `source_row_0881.png` -> replacement `replacement_row_1041.jpg` in bucket `b15`

## Proxy / Network

- Proxy detected: `False`
- Proxy values: `redacted`

## Safety

- source_content_read_for_verification_only: `True`
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
- manifest_modified: `False`
- backfill_applied: `False`
- app_managed_storage_mutation: `False`
- push_main: `False`
- merge: `False`

## Privacy

- Passed: `True`
- Leaks: `[]`
- Local details artifact: `phase-3.8d-i5b-targeted-hydration-details.json`

## Next Step

Do not apply backfill automatically; user/ChatGPT must decide backfill, provider/network investigation, lower-level hydration API investigation, or another approved policy.
