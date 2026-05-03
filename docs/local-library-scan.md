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

Multiple paths:

```env
LOCAL_LIBRARY_PATHS=C:\Users\kyloris\Pictures\iCloud Photos|D:\Art\Collection
```

> **Note:** Spaces in paths are fully supported — do **not** add quotes around paths.

### Via API Request Body

You can also pass paths directly in the request, which overrides the `.env` setting:

```json
{
  "paths": ["C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest"]
}
```

## API Usage

### Endpoint

```
POST /api/admin/scan-local-library
```

Requires admin authentication (JWT token + `admin_mode=true` cookie).

### Request Body (JSON)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `paths` | `string[]` | `.env` paths | Directories to scan |
| `dry_run` | `boolean` | `false` | When `true`, scan and report without importing anything |
| `max_files` | `integer` | no limit | Cap the number of candidate files to process |

### curl Example — Dry Run with Max Files

```bash
curl -X POST http://localhost:8000/api/admin/scan-local-library \
  -H "Authorization: Bearer <token>" \
  -H "Cookie: admin_mode=true" \
  -H "Content-Type: application/json" \
  -d '{"paths": ["C:\\\\Users\\\\kyloris\\\\Pictures\\\\iCloud Photos"], "dry_run": true, "max_files": 100}'
```

### curl Example — Using `.env` Paths

```bash
curl -X POST http://localhost:8000/api/admin/scan-local-library \
  -H "Authorization: Bearer <token>" \
  -H "Cookie: admin_mode=true"
```

### curl Example — With Explicit Path (Real Import)

```bash
curl -X POST http://localhost:8000/api/admin/scan-local-library \
  -H "Authorization: Bearer <token>" \
  -H "Cookie: admin_mode=true" \
  -H "Content-Type: application/json" \
  -d "{\"paths\": [\"C:\\\\Users\\\\kyloris\\\\Pictures\\\\AnimeLocalBooruTest\"]}"
```

### PowerShell Example — Dry Run

```powershell
$token = "your_jwt_token_here"
$headers = @{
    "Authorization" = "Bearer $token"
    "Content-Type"  = "application/json"
    "Cookie"        = "admin_mode=true"
}
$body = '{"paths": ["C:\\Users\\kyloris\\Pictures\\iCloud Photos"], "dry_run": true, "max_files": 50}'
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/scan-local-library" `
    -Method POST -Headers $headers -Body $body
```

### Response

```json
{
  "dry_run": true,
  "max_files": 100,
  "total_seen": 1500,
  "imported": 42,
  "skipped_duplicate": 100,
  "skipped_unsupported": 350,
  "skipped_limit": 1008,
  "failed": 0,
  "failed_files": []
}
```

| Field | Description |
|-------|-------------|
| `dry_run` | Whether this was a dry-run (no files copied, no DB writes) |
| `max_files` | The max_files cap that was applied (null if no limit) |
| `total_seen` | Total regular files encountered during recursive scan |
| `imported` | Successfully imported (or *would be* imported if dry_run) |
| `skipped_duplicate` | Skipped because MD5 hash already exists in database |
| `skipped_unsupported` | Skipped due to unsupported extension, symlinks, `.icloud` placeholders, etc. |
| `skipped_limit` | Skipped because the max_files cap was reached |
| `failed` | Files that failed during read, copy, or processing |
| `failed_files` | Up to 50 failed entries with path and reason |

## Admin UI

The Admin Panel → Content tab includes a **Local Library Scan** section where you can:

1. Enter a scan path (or leave empty to use `.env` paths)
2. Set a max_files limit
3. Toggle dry-run mode
4. Click "Start Scan" to begin
5. View a summary of results (imported, skipped, failed counts)
6. Inspect failed files with path and error reason

## Dry Run Mode

When `dry_run` is enabled:

- Files are **not** copied to `media/original/`
- **No** database records are created
- MD5 hashes are still calculated and checked for duplicates
- The `imported` count shows how many files **would** be imported
- Use this to safely preview what a real scan would do before committing

**Recommended workflow for new directories:**

1. Start with `dry_run: true` to see the scan report
2. Use `max_files: 50` or `100` for controlled testing
3. Run a real scan with a small `max_files` to verify imports work correctly
4. Run a full scan when confident

## Important Notes

- **Originals are never touched.** Files are always *copied*, never moved or deleted.
- **Disk space:** Since files are copied, you need enough free space in the Blombooru `media/` directory to hold copies of all imported images.
- **Duplicate detection** uses MD5 hashing. Re-running the scan against the same directory is safe and will skip all previously imported files.
- **Error isolation:** A failure on one file does not stop the scan. Every file is processed independently with its own error handling.
- **max_files counts candidate files** — files that pass the extension/symlink/size filter. Unsupported files are still counted in `total_seen` and `skipped_unsupported` but do not count against the limit.

## Data Model Note

Phase 1 stores the original external file path in the `Media.source` field using a `file://` URI prefix (e.g. `file://C:\Users\...\photo.jpg`). This allows distinguishing locally-scanned imports from web sources.

This is **not the final data model**. In a future phase, a dedicated `original_path` column and database migration may be introduced to support:

- Sync/re-scan workflows
- Audit trails
- Detecting whether the original file has been moved or deleted
