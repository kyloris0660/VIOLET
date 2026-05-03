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
| `.jpg` / `.jpeg` | ✅ Supported |
| `.png` | ✅ Supported |
| `.webp` | ✅ Supported |
| `.gif` | ✅ Supported |
| `.heic` | ❌ Not yet supported (skipped) |
| `.mp4` / `.webm` / `.mov` | ❌ Not yet supported (skipped) |

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

### curl Example — Using `.env` Paths

```bash
curl -X POST http://localhost:8000/api/admin/scan-local-library \
  -H "Authorization: Bearer <token>" \
  -H "Cookie: admin_mode=true"
```

### curl Example — With Explicit Path

```bash
curl -X POST http://localhost:8000/api/admin/scan-local-library \
  -H "Authorization: Bearer <token>" \
  -H "Cookie: admin_mode=true" \
  -H "Content-Type: application/json" \
  -d "{\"paths\": [\"C:\\\\Users\\\\kyloris\\\\Pictures\\\\AnimeLocalBooruTest\"]}"
```

### PowerShell Example

```powershell
$token = "your_jwt_token_here"
$headers = @{
    "Authorization" = "Bearer $token"
    "Content-Type"  = "application/json"
    "Cookie"        = "admin_mode=true"
}
$body = '{"paths": ["C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest"]}'
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/scan-local-library" `
    -Method POST -Headers $headers -Body $body
```

### Response

```json
{
  "total_seen": 150,
  "imported": 42,
  "skipped_duplicate": 100,
  "skipped_unsupported": 5,
  "failed": 3,
  "failed_files": [
    {"path": "C:\\...\\broken.jpg", "reason": "Permission denied"},
    {"path": "C:\\...\\locked.png", "reason": "The process cannot access the file"}
  ]
}
```

| Field | Description |
|-------|-------------|
| `total_seen` | Total regular files encountered during recursive scan |
| `imported` | Successfully imported into the gallery |
| `skipped_duplicate` | Skipped because MD5 hash already exists in database |
| `skipped_unsupported` | Skipped due to unsupported extension, symlinks, `.icloud` placeholders, etc. |
| `failed` | Files that failed during read, copy, or processing |
| `failed_files` | Up to 50 failed entries with path and reason |

## Important Notes

- **Originals are never touched.** Files are always *copied*, never moved or deleted.
- **Disk space:** Since files are copied, you need enough free space in the Blombooru `media/` directory to hold copies of all imported images.
- **Duplicate detection** uses MD5 hashing. Re-running the scan against the same directory is safe and will skip all previously imported files.
- **Error isolation:** A failure on one file does not stop the scan. Every file is processed independently with its own error handling.

## Data Model Note

Phase 1 stores the original external file path in the `Media.source` field using a `file://` URI prefix (e.g. `file://C:\Users\...\photo.jpg`). This allows distinguishing locally-scanned imports from web sources.

This is **not the final data model**. In a future phase, a dedicated `original_path` column and database migration may be introduced to support:

- Sync/re-scan workflows
- Audit trails
- Detecting whether the original file has been moved or deleted
