# iCloud Safe Ingestion (Phase 2.4)

Phase 2.4 makes the Local Library Scan pipeline safe for large iCloud-synced directories on Windows. The core guarantee: **scanning never triggers mass iCloud downloads and never hangs on a single file**.

## Problem

iCloud for Windows syncs photos as lightweight placeholders. When a program calls `open()` on a cloud-only file, Windows automatically requests a download from iCloud — potentially hydrating thousands of files and consuming bandwidth, disk space, and time. The scan pipeline reads every file to compute MD5 hashes, which means a naive scan of `C:\Users\...\iCloud Photos` could trigger a full library download.

Additionally, cloud-only files may block indefinitely when `open()` is called and the network is unavailable, causing the scan to hang.

## Solution Overview

| Layer | Mechanism |
|-------|-----------|
| **Preflight** | Stat-only scan (no `open()`) to preview file counts, sizes, and extensions before committing |
| **Hydrated-only** | Skip files with cloud-only Windows attributes (default ON) |
| **Per-file timeout** | Wrap `calculate_file_hash` in a ThreadPoolExecutor with configurable timeout |
| **Max file size** | Skip files larger than `SCAN_MAX_FILE_SIZE_MB` |
| **Extended skip stats** | Per-reason counters instead of a blanket `skipped_unsupported` |

## Phase 3.8d-I1 Ingestion Reliability Rule

Phase 2.4 solved **scan safety**: avoid unwanted mass downloads and hangs by detecting cloud-only placeholders and skipping them in hydrated-only local-library scans.

Phase 3.8d exposed a separate **ingestion availability** requirement: when a selected pilot manifest intentionally includes real cloud-backed source files, V.I.O.L.E.T. must detect cloud placeholder/recall state, attempt only approved controlled hydration/read-probe flows, retry with bounds, and either copy successfully or fail/backfill with a structured reason.

All ingestion, staging, and copy workflows that can touch iCloud or Windows Cloud Files source paths must pass a cloud availability gate before reading or copying content:

- `stat()`, `exists()`, file size, and `is_file()` are not sufficient for cloud-backed files.
- Windows Cloud Files attributes must be inspected before content reads.
- High cloud-risk selected sets must not proceed directly to `shutil.copy2` or other content reads.
- Manual "Always keep on this device" may be an emergency workaround only; it is not the formal V.I.O.L.E.T. workflow.
- Structured cloud failure reasons are required, including `cloud_offline`, `cloud_recall_on_open`, `cloud_recall_on_data_access`, `cloud_network_unavailable`, and `cloud_hydration_failed`.
- No DB import may run after failed or incomplete staging copy.
- Read-probe/hydration modes are opt-in because they may trigger provider-side downloads.

## Phase 3.8d-I2 Source Ingestion Gate

Cloud/iCloud handling must not live as isolated script patches.  All path-based source ingestion workflows now route source availability policy through `backend/app/services/source_ingestion_gate.py`.

The gate distinguishes source kinds:

| Source kind | Gate behavior |
|-------------|---------------|
| `path_source` | Inspect Cloud Files metadata and block content reads/copies when cloud risk exists unless an approved hydration/read-probe/backfill policy is active |
| `upload_bytes` | No source cloud gate; bytes are already supplied by the client request |
| `staging_file` | No source cloud gate; DB import requires a passed staging audit artifact proving source copy completed |
| `app_managed_file` | No source cloud gate; app storage consistency checks apply separately |

Path-based local source ingestion includes local library scans, preflight scans, candidate manifest generation, cloud availability audit, staging copy validation, and staging copy execution.  Upload endpoints that receive `UploadFile` request bytes are not path-source workflows and should not be forced through Cloud Files checks.

The formal rule is:

- `stat()`, `exists()`, file size, and `is_file()` are insufficient for cloud-backed source paths.
- Cloud attributes must be inspected before source content is read or copied.
- High cloud-risk selected sets must not proceed directly to copy.
- Manual hydrate is not the formal workflow.
- Structured cloud failure reasons are required.
- Staging-to-DB import must require a passed staging audit and must not run after incomplete staging copy.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `SCAN_HYDRATED_ONLY_DEFAULT` | `true` | Skip cloud-only files by default |
| `SCAN_FILE_OPEN_TIMEOUT_SECONDS` | `30` | Per-file timeout for hash calculation |
| `SCAN_MAX_FILE_SIZE_MB` | `200` | Skip files larger than this |

Set these in `.env` and restart the server.

## Preflight Scan

The preflight endpoint analyzes a directory using `os.stat()` only — no files are opened or read. It returns:

- File count (total seen, scannable, skipped by reason)
- Estimated total size in bytes
- Largest file size in bytes
- Extension breakdown (e.g. `{".jpg": 150, ".png": 42, ".webp": 8}`)

### API

```
POST /api/admin/scan-local-library/preflight
Content-Type: application/json

{
  "paths": ["C:\\Users\\kyloris\\Pictures\\iCloud Photos"],
  "max_files": 100,
  "hydrated_only": true
}
```

Response:

```json
{
  "job": {
    "id": 42,
    "status": "completed",
    "is_preflight": true,
    "processed": 95,
    "imported": 87,
    "skipped_cloud_placeholder": 5,
    "skipped_hidden": 2,
    "skipped_too_large": 1,
    ...
  },
  "estimated_size_bytes": 1234567890,
  "largest_file_bytes": 52428800,
  "extensions": {".jpg": 60, ".png": 20, ".webp": 7}
}
```

### Admin UI

The "Preflight" button in the Local Library Scan section runs a preflight analysis and displays results inline — estimated size, largest file, and extension breakdown.

## iCloud Cloud-Only Detection

On Windows, files synced by iCloud/OneDrive have file attributes indicating they are not locally available:

| Attribute | Value | Meaning |
|-----------|-------|---------|
| `FILE_ATTRIBUTE_OFFLINE` | `0x1000` | File is not immediately available |
| `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` | `0x400000` | Accessing data triggers recall from remote |
| `FILE_ATTRIBUTE_RECALL_ON_OPEN` | `0x40000` | Opening triggers recall from remote |

The metadata helper in `backend/app/utils/cloud_files.py` checks these via `ctypes.windll.kernel32.GetFileAttributesW`. On non-Windows platforms, it reports `supported_platform=false` and does not mark files as cloud-risk. The local library scanner's `_is_cloud_only()` wrapper now delegates to the shared Source Ingestion Gate, which uses this helper so scan safety and pilot staging/copy preflights use one source of truth.

When `hydrated_only=True` (the default), files with any of these attributes are skipped and counted as `skipped_cloud_placeholder`.

## Per-File Timeout

Even with hydrated-only mode, some files may take unexpectedly long to read (slow disk, network-mounted storage, partially-hydrated cloud files). The scanner runs `calculate_file_hash()` in a separate **subprocess** (`multiprocessing.Process`) with a hard timeout. Results are returned via `multiprocessing.Pipe` (not Queue) to avoid feeder-thread race conditions.

On timeout:
- The subprocess is terminated (`proc.terminate()` / `proc.kill()`)
- The file is skipped and counted as `skipped_timeout`
- The scan continues to the next file immediately
- No thread leaks — the terminated subprocess releases all resources

## Extended Skip Statistics

Each scan job now tracks detailed skip reasons:

| Column | Counts |
|--------|--------|
| `skipped_cloud_placeholder` | Files with cloud-only attributes or `.icloud` extension |
| `skipped_zero_byte` | Empty files (0 bytes) |
| `skipped_timeout` | Files that exceeded the hash timeout |
| `skipped_unreadable` | Permission errors, stat errors, OS errors |
| `skipped_hidden` | Dotfiles or Windows hidden attribute |
| `skipped_too_large` | Files exceeding `SCAN_MAX_FILE_SIZE_MB` |

The existing `skipped_unsupported` column remains for unsupported file extensions and is still used for backward compatibility.

## Admin UI

The Local Library Scan section in the Admin Panel includes:

- **Hydrated-only checkbox** (initial state from `SCAN_HYDRATED_ONLY_DEFAULT` server config) — controls whether cloud-only files are skipped
- **Preflight button** — runs stat-only analysis before committing to a full scan
- **iCloud safety note** — explains the purpose of hydrated-only mode
- **6 extended stat cards** — shown during/after scan with per-reason skip counts
- **Preflight results** — estimated size, largest file, extension breakdown displayed after preflight

## Recommended Workflow for iCloud Libraries

1. **Preflight first** — click Preflight to see file counts and sizes without opening any files
2. **Review results** — check cloud placeholder count, total size, extension breakdown
3. **Dry-run scan** — run with dry_run=true and a small max_files to test actual import behavior
4. **Full scan** — if dry-run looks good, run without dry_run, keeping hydrated_only=true

## Files Modified

| File | Change |
|------|--------|
| `backend/app/config.py` | 3 new scan safety properties |
| `backend/app/models.py` | 8 new `ScanJob` columns |
| `backend/app/database.py` | `migrate_add_scan_job_icloud_stats` migration |
| `backend/app/utils/local_library_scanner.py` | `_is_cloud_only`, `_is_hidden`, enhanced `_is_scannable_file`, `preflight_analyze`, timeout, extended stats |
| `backend/app/routes/admin/media.py` | Preflight endpoint, hydrated_only, extended serializer |
| `backend/app/routes/admin/dev_tools.py` | Server info in config diagnostics |
| `frontend/static/locales/en.json` | ~16 new i18n keys |
| `frontend/static/locales/zh-cn.json` | Matching Chinese translations |
| `frontend/templates/admin.html` | Preflight UI, hydrated-only, extended stats |
| `frontend/static/js/admin.js` | Preflight handler, extended progress, server info |
| `example.env` | 3 new env vars |
| `tests/test_scanner_icloud.py` | 22 unit tests |
| `tests/e2e/test_icloud_scan.spec.ts` | 7 E2E tests |
