# Local Library Scan

Scan one or more local image directories and import supported files into AnimeLocalBooru.

## How It Works

1. The scanner recursively walks every configured directory.
2. For each supported image file it calculates an MD5 hash and checks whether the image already exists in the database.
3. New images are **copied** into `media/original/` — the original file is **never moved, renamed, or deleted**.
4. A thumbnail is generated and the image is added to the gallery just like a normal upload.

## Supported Formats

| Extension | Status |
|-----------|--------|
| `.jpg` / `.jpeg` | Supported |
| `.png` | Supported |
| `.webp` | Supported |
| `.gif` | Supported |
| `.heic` | Not yet supported (skipped) |
| `.mp4` / `.webm` / `.mov` | Not yet supported (skipped) |

Files with an `.icloud` extension (iCloud placeholder files that have not been downloaded) are automatically skipped.

## Configuration

### Via `.env`

Add `LOCAL_LIBRARY_PATHS` to your `.env` file. Use `|` (pipe) to separate multiple paths:

```env
LOCAL_LIBRARY_PATHS=C:\Users\kyloris\Pictures\iCloud Photos
```

### Via API Request Body

Pass paths directly in the request, which overrides the `.env` setting:

```json
{
  "paths": ["C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest"]
}
```

## API — Synchronous Scan (Legacy)

The original synchronous endpoint from Phase 1/1.5. Blocks until the scan completes.

```
POST /api/admin/scan-local-library
```

Good for small directories or scripted use. For large directories, use the Job API instead.

### Request Body

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `paths` | `string[]` | `.env` paths | Directories to scan |
| `dry_run` | `boolean` | `false` | Scan only, no import |
| `max_files` | `integer` | no limit | Cap candidate files to process |

## API — Background Scan Jobs (Recommended)

The job-based API returns immediately with a `job_id`. The scan runs in a background thread. Poll for progress or check history.

### Create a Scan Job

```
POST /api/admin/scan-local-library/jobs
```

Returns immediately with the job object (status = `pending`).

Only one scan job can run at a time. Returns `409` if another job is already running.

#### Request Body

Same as the synchronous endpoint:

```json
{
  "paths": ["C:\\Users\\kyloris\\Pictures\\iCloud Photos"],
  "dry_run": true,
  "max_files": 100
}
```

### Get Job Status

```
GET /api/admin/scan-local-library/jobs/{job_id}
```

Returns the current status and progress counters. Poll every 1–2 seconds for real-time progress.

### List Recent Jobs

```
GET /api/admin/scan-local-library/jobs
```

Returns the 20 most recent scan jobs, newest first. Includes completed, failed, cancelled, and interrupted jobs.

### Cancel a Running Job

```
POST /api/admin/scan-local-library/jobs/{job_id}/cancel
```

Requests cancellation. The scanner checks the cancel flag after each file and stops gracefully. **Already-imported files are kept** — cancel does not roll back imports.

### Job Response

```json
{
  "id": 1,
  "status": "completed",
  "paths": ["C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest"],
  "dry_run": true,
  "max_files": 100,
  "started_at": "2026-05-03T15:00:00+00:00",
  "finished_at": "2026-05-03T15:00:05+00:00",
  "created_at": "2026-05-03T15:00:00+00:00",
  "total_seen": 1500,
  "processed": 100,
  "imported": 42,
  "skipped_duplicate": 58,
  "skipped_unsupported": 350,
  "skipped_limit": 0,
  "failed": 0,
  "limit_reached": true,
  "failed_files": [],
  "error_message": null
}
```

### Job Statuses

| Status | Meaning |
|--------|---------|
| `pending` | Job created, waiting to start |
| `running` | Scan in progress |
| `cancelling` | Cancel requested, stopping soon |
| `completed` | Scan finished successfully |
| `cancelled` | Scan was cancelled by user |
| `failed` | Scan failed with an error |
| `interrupted` | Application stopped while job was running |

## Dry Run Mode

When `dry_run` is enabled:

- Files are **not** copied to `media/original/`
- **No** database records are created
- MD5 hashes are still calculated and checked for duplicates
- The `imported` count shows how many files **would** be imported

## max_files Semantics

`max_files` limits the number of **candidate files** processed — files that pass the extension/symlink/size filter (.jpg, .jpeg, .png, .webp, .gif).

- Unsupported files (.heic, .txt, directories, symlinks, .icloud) do **not** count against the limit
- When the limit is reached, the scanner stops immediately (does not continue walking the directory)
- `limit_reached` is set to `true` in the response
- Both dry-run and real scans support `max_files`

## Recommended Workflow for New Directories

1. **Dry-run + max_files=100**: Preview what would happen
   ```json
   {"paths": ["..."], "dry_run": true, "max_files": 100}
   ```
2. **Real scan + max_files=100**: Import a small batch and verify
   ```json
   {"paths": ["..."], "max_files": 100}
   ```
3. **Full scan**: When confident, remove max_files
   ```json
   {"paths": ["..."]}
   ```

**Never scan the real iCloud Photos directory without a prior dry-run.**

## Path Safety

The scanner refuses to scan project-internal directories:

- Project root directory
- `venv/`, `data/`, `media/`, `storage/`, `.git/`, `__pycache__/`

This prevents accidental scanning of application files.

## Admin UI

The Admin Panel → Content tab includes a **Local Library Scan** section:

1. Enter a scan path (or leave empty to use `.env` paths)
2. Set a max_files limit
3. Toggle dry-run mode
4. Click **Start Scan** to create a background job
5. Watch real-time progress (polling every 1.5 seconds)
6. Click **Cancel** to stop a running scan
7. View scan results (imported, skipped, failed counts, failed file details)
8. Browse **Scan History** — click any row to view its details
9. If a job is running when you open the page, polling auto-resumes

## Stale Job Recovery

If the application stops while a scan is running (crash, restart, etc.), any `pending`/`running`/`cancelling` jobs are automatically marked as `interrupted` on the next startup. This prevents the Admin UI from showing a permanently "running" job.

## Important Notes

- **Originals are never touched.** Files are always *copied*, never moved or deleted.
- **Duplicate detection** uses MD5 hashing. Re-running the scan is safe.
- **Error isolation:** A failure on one file does not stop the scan.
- **Cancel does not roll back:** Already-imported files are kept.
- **Single job limit:** Only one scan can run at a time to prevent conflicts.

## Data Model Note

Imported files store the original path in `Media.source` as a `file://` URI. Scan jobs are persisted in the `blombooru_scan_jobs` table.
