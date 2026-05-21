# Phase 3.8d-I2 Source Ingestion Gate Unification

## Status

Phase 3.8d execute remains blocked. This phase unifies cloud availability checks behind a shared Source Ingestion Gate so path-based source workflows cannot bypass Cloud Files metadata checks.

No staging cleanup, staging copy rerun, DB import, classification, AI tagging, localization, Entity Resolver, similarity, source mutation, or app-managed storage mutation was performed.

## Entrypoint Audit

| Entrypoint | Source type | Reads local source path | Cloud gate required | Current gate status | Required change / result | Tests |
|---|---:|---:|---:|---|---|---|
| `backend/app/routes/admin/media.py::scan_local_library` -> `backend/app/utils/local_library_scanner.py::scan_and_import` | `path_source` | yes | yes | delegated through scanner | scanner cloud-only check now uses `SourceIngestionGate` | `tests/test_scanner_icloud.py` |
| `backend/app/routes/admin/media.py::preflight_scan` -> `backend/app/utils/local_library_scanner.py::preflight_analyze` | `path_source` | yes | yes | delegated through scanner | hydrated-only preflight uses the same gate path | `tests/test_scanner_icloud.py` |
| `scripts/generate_candidate_manifest.py` | `path_source` | yes | yes | updated | placeholder detection now consults `SourceIngestionGate` before legacy size heuristics | py_compile / non-E2E suite |
| `scripts/plan_phase38c_medium_pilot_preflight.py` | `path_source` | metadata-only | yes | updated | candidate summary now reports source gate risk for pool and selected rows | `tests/test_phase38c_medium_pilot_preflight.py` |
| `scripts/audit_cloud_availability.py` | `path_source` | metadata-only | yes | updated | selected-row audit now records `source_ingestion_gate` results | `tests/test_audit_cloud_availability.py` |
| `scripts/stage_pilot_files.py::validate_manifest` | `path_source` | metadata-only | yes | updated | manifest validation routes cloud risk through the gate | `tests/test_stage_pilot_files.py` |
| `scripts/stage_pilot_files.py::execute_copy` | `path_source` | yes | yes | updated | copy refuses blocked gate results before `shutil.copy2` | `tests/test_stage_pilot_files.py` |
| `scripts/run_phase38d_medium_pilot_execute.py` | future orchestrator | yes | yes | not present on merged main | future execute orchestration must call the staged copy/cloud gate before copy/import | documented |
| `scripts/import_staged_manifest.py` | `staging_file` | no source path; reads staging files | no source cloud gate | updated | public report records `staging_file` source kind and requires passed staging audit artifact | `tests/test_import_staged_manifest.py` |
| `backend/app/routes/media.py::upload_media` upload bytes path | `upload_bytes` | no | no | explicitly out of scope | server receives file content from request; upload validation remains separate | `tests/test_source_ingestion_gate.py` |
| `backend/app/routes/media.py::upload_media` scanned path under app storage | `app_managed_file` | no source path | no | explicitly out of scope | path is constrained to app-managed original storage, not source/iCloud | documented |
| `backend/app/routes/media.py` file, thumbnail, metadata endpoints | `app_managed_file` | no source path | no | explicitly out of scope | app storage consistency checks apply separately | documented |
| `backend/app/utils/file_scanner.py` | `app_managed_file` | no source path | no | explicitly out of scope | scans app-managed originals only | documented |
| `backend/app/routes/booru_import.py` | remote/uploaded bytes | no local source path | no | explicitly out of scope | remote content is downloaded into app-managed storage; source cloud gate is not applicable | documented |

## Gate Design

`backend/app/services/source_ingestion_gate.py` defines the shared abstraction:

- `path_source`: runs metadata-only Cloud Files inspection and blocks cloud risk unless an approved hydration/read-probe/backfill policy is active.
- `upload_bytes`: allowed without source cloud gate because bytes are already supplied by the client request.
- `staging_file`: allowed only when the prior staging audit passed; source cloud gate is not repeated during DB import.
- `app_managed_file`: allowed without source cloud gate; storage consistency checks apply separately.

Gate results are structured:

- `allowed`
- `blocked`
- `source_kind`
- `reason`
- `required_policy`
- `cloud_state`
- `safe_label`
- `paths_redacted`

Public summaries do not include local absolute paths.

## Upload Endpoint Clarification

Upload-bytes routes are not path-based source ingestion. They receive content from the HTTP request body, so Cloud Files/iCloud availability belongs to the client or to source-path workflows before upload. Server-side upload validation remains required, but the Source Ingestion Gate is not applied to `UploadFile` request bytes.

Server-local paths accepted only under app-managed storage are classified as `app_managed_file`, not `path_source`.

## Remaining Blocked State

Phase 3.8d execute remains blocked because the selected Phase 3.8c manifest contains substantial Cloud Files recall risk. Before retry, the project still needs explicit approval for the recovery path: cleanup or resume plan, plus controlled read-probe/hydration/backfill policy.

## Safety

- No cleanup/delete performed.
- No source/iCloud mutation.
- No staging copy rerun.
- No DB import.
- No classification.
- No AI tagging.
- No localization.
- No Entity Resolver.
- No push main.
- No merge.
