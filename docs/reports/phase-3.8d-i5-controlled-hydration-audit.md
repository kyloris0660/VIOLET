# Phase 3.8d-I5 Controlled Hydration Audit

## Summary

- Phase: `3.8d-I5`
- Purpose: run a bounded read-probe / full-read audit for the selected Phase 3.8c/3.8d manifest before any staging-copy retry.
- Phase 3.8d execute status: `blocked`
- Audit status: `blocked_sample_gate_failed`
- Metadata baseline selected total: `1000`
- Baseline recall-risk count: `613`
- Sample gate: `failed`
- Full recall verification: `skipped_sample_gate_failed`
- Remaining recall-risk count after sample hydration: `569`

This phase does not prove the selected set is ready for staging copy. The sample gate exposed two bounded read failures, including the original failed `row_id=98`, so the full recall verification did not run.

## Audit Policy

- Metadata-only baseline: `no content read`
- Prefix read: `1` byte, timeout `10` seconds, retries `1`
- Full read: streaming verification, timeout `60` seconds, retries `1`, chunk size `4194304` bytes
- Full-read verification is required before treating a cloud-backed source file as staging-copy-ready.
- Direct `CfHydratePlaceholder` integration was not used.
- Source content was read only for verification. No source file content write, rename, move, delete, metadata edit, staging copy, DB import, classification, AI tagging, localization, Entity Resolver, similarity, or cleanup/delete was run.

## Metadata Baseline

- Selected total: `1000`
- Exists: `1000`
- Missing: `0`
- Offline: `0`
- Reparse point: `0`
- Recall on open: `0`
- Recall on data access: `613`
- Pinned: `6`
- Unpinned: `6`
- Sparse file: `0`
- Likely cloud placeholder / recall-risk: `613`
- Risky by bucket: `{"b02":24,"b03":63,"b04":62,"b05":63,"b06":62,"b07":61,"b08":53,"b09":12,"b10":14,"b11":30,"b12":51,"b13":27,"b14":27,"b15":42,"b16":22}`
- Risky by extension: `{".jpeg":4,".jpg":514,".png":95}`

## Row 98 Result

- Row: `98`
- Safe label: `source_row_0098.jpg`
- Bucket: `b02`
- Extension: `.jpg`
- Expected size: `256931`
- Baseline state: `recall_on_data_access`
- Included in sample gate: `True`
- Prefix read: `failed`
- Prefix bytes read: `0`
- Prefix duration seconds: `20.031`
- Full read: `skipped` because prefix read failed
- Failure reason: `read_timeout`
- Post-sample state: still `recall_on_data_access`

Row 98 is not abandoned. I5 did not prove row 98 can be hydrated or copied, and it did not replace row 98 in the manifest. The next recovery choice should either approve a targeted controlled retry/hydration attempt for row 98, or approve same-bucket backfill only after accepting the bounded read failure result.

## Sample Gate

- Sample policy: row 98 required, up to `3` risky rows per risky bucket, max `48`
- Attempted: `46`
- Success: `44`
- Failed: `2`
- Bytes read: `121073270`
- Duration seconds: `114.109`
- Failures by reason: `{"read_timeout":2}`
- Failures by bucket: `{"b02":1,"b15":1}`
- Failures by extension: `{".jpg":1,".png":1}`

Failed sample rows:

| Row | Safe label | Bucket | Extension | Reason |
|---:|---|---|---|---|
| `98` | `source_row_0098.jpg` | `b02` | `.jpg` | `read_timeout` |
| `881` | `source_row_0881.png` | `b15` | `.png` | `read_timeout` |

## Full Recall Verification

- Status: `skipped_sample_gate_failed`
- Reason: the sample gate failed; running full verification for the remaining recall-risk rows would violate the staged audit policy.
- Attempted count: `0`

## Post-sample Metadata Recheck

- Remaining likely cloud placeholder / recall-risk count: `569`
- Remaining recall on data access count: `569`
- Remaining risky by bucket: `{"b02":21,"b03":60,"b04":59,"b05":60,"b06":59,"b07":58,"b08":50,"b09":9,"b10":11,"b11":27,"b12":48,"b13":24,"b14":24,"b15":40,"b16":19}`
- Remaining risky by extension: `{".jpeg":4,".jpg":477,".png":88}`

The sample audit likely hydrated/read-verified `44` cloud-backed files successfully. Provider-side hydration/cache changes may have occurred as expected from approved bounded reads.

## Backfill Dry-run

Backfill was not applied in I5. The following is a dry-run-only same-bucket plan for the failed rows:

| Failed row | Failed label | Replacement row | Replacement label | Bucket | Reason |
|---:|---|---:|---|---|---|
| `98` | `source_row_0098.jpg` | `1029` | `replacement_row_1029.png` | `b02` | `same_bucket_backfill_after_bounded_hydration_failure` |
| `881` | `source_row_0881.png` | `1041` | `replacement_row_1041.jpg` | `b15` | `same_bucket_backfill_after_bounded_hydration_failure` |

Backfill remains a future approval decision. The selected manifest was not modified.

## Proxy / Network

- Proxy environment detected: `False`
- Proxy values: `redacted`
- Network-related interpretation: read timeouts are compatible with provider/network availability problems, but I5 does not prove the exact external cause.

## Safety

- Source/iCloud write mutation: `False`
- Provider-side hydration/cache may have occurred: `True`
- Staging copy: `False`
- Staging write: `False`
- DB import: `False`
- Classification: `False`
- AI tagging: `False`
- Localization: `False`
- Entity Resolver: `False`
- Similarity: `False`
- Cleanup/delete: `False`
- App-managed storage mutation: `False`
- Push main: `False`
- Merge: `False`

## Local Artifacts

- `phase-3.8d-i5-metadata-baseline-summary.json`
- `phase-3.8d-i5-metadata-baseline.md`
- `phase-3.8d-i5-metadata-baseline-details.json`
- `phase-3.8d-i5-sample-gate-summary.json`
- `phase-3.8d-i5-sample-gate.md`
- `phase-3.8d-i5-sample-gate-details.json`
- `phase-3.8d-i5-post-sample-metadata-summary.json`
- `phase-3.8d-i5-post-sample-metadata.md`
- `phase-3.8d-i5-post-sample-metadata-details.json`
- `phase-3.8d-i5-hydration-audit-details.json`

These local artifacts remain under ignored local artifact storage and must not be committed.

## Privacy

- Safe labels only: `True`
- Full local paths in public report: `False`
- Source paths redacted: `True`
- Proxy values redacted: `True`
- Secrets exposed: `False`
- Privacy passed: `True`

## Next Step

Phase 3.8d execute remains blocked. Do not retry staging copy yet. The next decision should be a targeted recovery stage for the failed rows (`98` and `881`) or an explicit approval to apply same-bucket backfill for those rows before any staging-copy retry.
