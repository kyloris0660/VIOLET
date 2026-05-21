# Phase 3.8d-I1 iCloud / Windows Cloud Files Ingestion Incident

## Severity

This is a foundational ingestion reliability incident. It blocks Phase 3.8d guarded execute, Phase 4, and any larger-scale import until the source ingestion/staging path can handle cloud-backed files deterministically.

## Incident Summary

During the Phase 3.8d guarded +1000 medium pilot execute, the real staging copy stopped at manifest `row_id=98`, temporal bucket `b02`, while reading a cloud-backed source file. Windows returned error `388`: `The cloud sync provider failed to perform the operation due to network being unavailable`.

The failed file had cloud placeholder evidence: `Offline`, `ReparsePoint`, `SparseFile`, and later metadata-only audit evidence of `RECALL_ON_DATA_ACCESS`.

This incident exposes a missing cloud availability/hydration gate in the formal ingestion/staging workflow.

## Timeline

| Step | Result |
|------|--------|
| Phase 3.8c candidate selection | Selected `1000` temporally diverse candidates from a `33032` candidate pool across `16` buckets. |
| Phase 3.8d prewrite checks | Repo, Python, DB, storage, source labels, local manifest, staging target, active jobs, and background job isolation checks passed before writes. |
| DB backup | Backup artifact `phase-3.8d-before-20260521-182953.dump`, size `1400100` bytes. |
| Staging validation | Initial manifest/staging validation passed before copy. |
| Staging copy rows 1-97 | Copied `97` files to dedicated partial staging label `phase_3_8d_partial_staging`, total `340159586` bytes. |
| Failure row 98 | Copy failed on `source_row_0098.jpg` / `target_row_0098.jpg` with WinError `388`. |
| Stop condition | Workflow stopped before DB import. No cleanup was performed. |

## Impact

- DB import did not run.
- Content classification did not run.
- AI tagging did not run.
- Localization did not run.
- Entity Resolver did not run.
- Similarity/clustering did not run.
- App-managed storage import path was not touched.
- Source/iCloud files were not modified.
- Partial staging contains evidence only: `97` copied files, `340159586` bytes.

## Detection

The copy layer surfaced a Windows cloud-provider read failure:

- Error code: `388`
- Message: `The cloud sync provider failed to perform the operation due to network being unavailable`
- Immediate attributes: `Offline`, `ReparsePoint`, `SparseFile`
- Later metadata-only audit: `RECALL_ON_DATA_ACCESS`

Microsoft documents `FILE_ATTRIBUTE_OFFLINE` as data not immediately available, `FILE_ATTRIBUTE_REPARSE_POINT` as reparse-point backed, and `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` as data not fully present locally and potentially fetched from a remote store when read. Microsoft also documents `CfHydratePlaceholder` as a Windows Cloud Files API that ensures a byte range is present on disk, but this incident PR does not call it.

References:

- [Microsoft file attribute constants](https://learn.microsoft.com/en-us/windows/win32/fileio/file-attribute-constants)
- [Microsoft CfHydratePlaceholder](https://learn.microsoft.com/en-us/windows/win32/api/cfapi/nf-cfapi-cfhydrateplaceholder)
- [Microsoft CfGetPlaceholderStateFromAttributeTag](https://learn.microsoft.com/en-us/windows/win32/api/cfapi/nf-cfapi-cfgetplaceholderstatefromattributetag)

## Root Cause

The staging/copy workflow lacked a cloud availability/hydration gate. It treated `stat()`, existence, size, and `is_file()` checks as sufficient, but Windows Cloud Files placeholders can pass metadata checks and still fail when content is opened or copied.

Phase 2.4 had already solved scan safety for iCloud-backed directories by detecting cloud placeholders and skipping them in hydrated-only scan mode. Phase 3.8d did not reuse or generalize that cloud-file detection for the manifest staging/copy path.

## Contributing Factors

- Metadata-only audit found `613 / 1000` selected files with recall-risk attributes.
- `shutil.copy2` can trigger provider/network-dependent recall when source content is read.
- The copy layer had no cloud-specific structured failure reason, bounded retry, hydration/read-probe gate, backfill policy, or resume policy.
- Direct staging copy did not fail closed on high cloud-risk selected sets.
- Phase 2.4 was designed to avoid unwanted downloads/hangs; Phase 3.8d newly requires controlled ingestion availability for selected real cloud-backed files.

## What Went Well

- The Phase 3.8d stop condition prevented DB import.
- A prewrite DB backup existed before any DB mutation.
- No cleanup, delete, reset, drop, or truncate was performed.
- No downstream classification, AI tagging, localization, Entity Resolver, or similarity work ran.
- Partial staging was preserved as evidence.
- The later metadata-only audit confirmed the risk is systemic, not just a single-row copy error.

## What Failed

- The preflight did not block a selected set with high cloud recall risk.
- The staging copy path did not reuse the Phase 2.4 cloud-only detection logic.
- The copy failure was not originally classified with a structured cloud-specific reason.
- There was no approved hydrate/read-probe policy before content read.
- There was no same-bucket backfill policy after bounded hydrate failure.
- There was no explicit resume policy for partial staging.

## Cloud Availability Audit Result

The Phase 3.8d-I1 metadata-only audit did not open source file contents and did not request hydration.

| Metric | Value |
|--------|-------|
| Selected total | `1000` |
| Exists | `1000` |
| Missing | `0` |
| Stat errors | `0` |
| Already copied in partial staging | `97` |
| Not yet copied | `903` |
| Offline | `0` |
| Reparse point | `0` |
| Recall on open | `0` |
| Recall on data access | `613` |
| Pinned | `6` |
| Unpinned | `6` |
| Sparse file | `0` |
| Likely cloud placeholder / recall-risk | `613` |
| Copy gate | `blocked_requires_hydration_policy` |

Risk by bucket:

| Bucket | Risky count |
|--------|-------------|
| b02 | `24` |
| b03 | `63` |
| b04 | `62` |
| b05 | `63` |
| b06 | `62` |
| b07 | `61` |
| b08 | `53` |
| b09 | `12` |
| b10 | `14` |
| b11 | `30` |
| b12 | `51` |
| b13 | `27` |
| b14 | `27` |
| b15 | `42` |
| b16 | `22` |

Risk by extension: `.jpg=514`, `.png=95`, `.jpeg=4`.

The first risky example is `source_row_0098.jpg` in bucket `b02`; it is the original failed copy row and was not already copied.

## Corrective Actions

Implemented in this incident hardening PR:

- Added shared metadata-only Windows Cloud Files helper: `backend/app/utils/cloud_files.py`.
- Refactored Phase 2.4 scanner cloud-only detection to use the shared helper.
- Added manifest cloud availability audit: `scripts/audit_cloud_availability.py`.
- Added formal copy gate: any likely cloud placeholder blocks direct staging copy until an approved hydration/read-probe/backfill policy passes.
- Added structured copy failure classification, including `cloud_network_unavailable` for WinError `388`.
- Added optional `--read-probe` hook, default off, because it may trigger hydration.
- Added dry-run-only same-bucket backfill planning.
- Added dry-run cleanup/resume policy documentation; this PR does not delete or clean partial staging.
- Added tests for helper flags, audit privacy/counts, opt-in read-probe, copy error classification, backfill, and cleanup safety.

## Preventive Actions

- All ingestion/staging/copy workflows touching iCloud or Windows Cloud Files source paths must pass a cloud availability gate before reading/copying content.
- `stat()`, `exists()`, size, and `is_file()` are insufficient for cloud-backed source paths.
- Manual "Always keep on this device" may be an emergency workaround only; it is not the formal V.I.O.L.E.T. workflow.
- Cloud failures must be reported with structured reason codes.
- No DB import may run after failed or incomplete staging copy.
- High cloud-risk selected sets must not proceed directly to copy.

## Required Tests

- Windows attribute detection: offline, reparse point, recall-on-open, recall-on-data-access, pinned, unpinned, sparse file.
- Non-Windows safe behavior.
- Metadata-only audit does not open/read source files.
- Read-probe is opt-in only.
- WinError `388` maps to `cloud_network_unavailable`.
- Structured cloud state is included in local copy-failure artifacts.
- Same-bucket backfill preserves selected total.
- Cleanup remains dry-run-only and requires separate approval before any real deletion.
- Public reports remain privacy-safe.

## Recovery Plan

Recommended recovery path:

1. Keep Phase 3.8d execute blocked until this hardening PR is reviewed and merged.
2. Obtain explicit approval for one controlled next step:
   - cleanup the partial staging target after a dry-run delete report and confirmation phrase, then rerun from an empty staging target; or
   - resume from row `98` only after already-copied files are verified by size/hash and no overwrite is possible.
3. Obtain explicit approval before any read-probe/hydration attempt. The default audit mode must remain metadata-only.
4. If bounded hydrate/read-probe fails for specific rows, use same-bucket backfill planning to preserve selected total and temporal diversity.
5. Only after staging copy is complete and verified may Phase 3.8d DB import be reconsidered.

Because only `97` files were copied and no DB downstream state exists, cleanup plus rerun is likely simpler than resume, but cleanup requires a separate dry-run report and explicit user/ChatGPT approval.

## Safety Confirmation

- No cleanup/delete was performed.
- No source/iCloud mutation was performed.
- No DB import was performed.
- No classification was performed.
- No AI tagging was performed.
- No localization was performed.
- No Entity Resolver was run.
- No similarity/clustering was run.
- No push to `main` was performed.
- No merge was performed.
