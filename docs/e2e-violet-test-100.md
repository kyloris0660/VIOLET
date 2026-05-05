# E2E Validation Guide — VioletTest100

End-to-end validation of the V.I.O.L.E.T. full pipeline using a small, controlled test directory.

> **Pipeline:** Local Library Scan → AI Auto Tagging → Tag Localization → Review → Chinese Search

## Purpose

Validate that all pipeline stages work together correctly before running against a real library. This guide uses a dedicated `VioletTest100` directory containing ~20–100 anime/illustration images as a safe, repeatable test target.

## 1. Prepare the VioletTest100 Directory

Create a directory with a small set of anime/illustration images:

```
C:\Users\kyloris\Pictures\VioletTest100\
├── image_001.jpg
├── image_002.png
├── image_003.webp
├── ...
└── image_020.jpg    (minimum ~20 images recommended)
```

Requirements:
- Use **anime/illustration** images (the WDv3 model is trained on Danbooru)
- Supported formats: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`
- Avoid photos, screenshots, or non-anime content (will produce low-confidence garbage tags)
- 20–100 images is ideal for a quick pipeline test (~1–5 minutes)

> **提示：** 可以从 Danbooru/Pixiv 下载一些高质量动漫图片作为测试素材。不要使用真实照片。

## 2. Recommended `.env` Configuration

Copy the following block into your `.env` file:

```env
# === E2E Test Configuration ===
LOCAL_LIBRARY_PATHS=C:\Users\kyloris\Pictures\VioletTest100

# AI Tagging
AI_TAGGING_ENABLED=true
AI_TAGGING_BATCH_MAX_ITEMS=20
AI_MODEL_NAME=wd-swinv2-tagger-v3
AI_GENERAL_THRESHOLD=0.35
AI_CHARACTER_THRESHOLD=0.65
AI_SUGGESTION_THRESHOLD=0.20

# Auto-tag after import
AI_AUTO_TAG_AFTER_IMPORT=true
AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS=20
AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW=true
AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN=false
AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS=false

# Tag Localization (optional — requires LLM API)
TAG_TRANSLATION_LLM_ENABLED=true
TAG_TRANSLATION_AUTO_ENABLED=true
TAG_TRANSLATION_AUTO_MAX_ITEMS=20
TAG_TRANSLATION_BATCH_MAX_ITEMS=20
TAG_TRANSLATION_LLM_PROVIDER=openai_compatible
TAG_TRANSLATION_LLM_API_KEY=<your-api-key>
TAG_TRANSLATION_LLM_MODEL=<your-model>
TAG_TRANSLATION_LLM_BASE_URL=<your-api-base-url>
```

> **Note:** If you don't have LLM API access, set `TAG_TRANSLATION_LLM_ENABLED=false`. The pipeline still works — tags just won't have Chinese translations.

## 3. GUI Walkthrough (Step by Step)

### 3.1 Start the Server

```powershell
.\venv\Scripts\Activate.ps1
python run.py --debug
```

### 3.2 Open Admin Panel

Navigate to `http://localhost:8000/admin` and log in with `admin` / `admin123`.

### 3.3 Verify AI Model Status

1. Go to **Content** tab → **AI Auto Tagging** section
2. Check that Model Status shows **Available** with model name `wd-swinv2-tagger-v3`
3. If model is not downloaded, click the model status check — first run will download ~450 MB

### 3.4 Run Local Library Scan

1. In the **Local Library Scan** section:
   - Enter path: `C:\Users\kyloris\Pictures\VioletTest100`
   - Set Max Files: `20`
   - Toggle Dry Run: **ON** first
2. Click **Start Scan** — verify dry-run output shows expected file count
3. Toggle Dry Run: **OFF**
4. Click **Start Scan** again — real import begins
5. Watch progress update in real time
6. After completion, note the imported count

### 3.5 Verify Auto AI Tagging Triggered

1. After scan completes, check the **AI Tagging Jobs** section (refresh if needed)
2. A new job should appear with:
   - `trigger_source: scan_job`
   - `status: running` or `completed`
3. Wait for it to complete (poll interval ~3 seconds in UI)
4. Verify `tags_added > 0` and/or `suggestions_added > 0`

### 3.6 Check Tag Localization

1. Go to **Tag Localization** section (or check `localization_status` on the AI job)
2. If LLM is configured: new tags should show Chinese translations
3. If LLM is disabled: `localization_status` will show `skipped_llm_disabled`

### 3.7 Review AI Tags

1. Go to **AI Tag Review** section
2. Browse suggestion tags — verify they look reasonable for the test images
3. Confirm a few tags to make them searchable

### 3.8 Test Chinese Search

1. Go to the main gallery
2. Search for a common Chinese tag like `蓝眼睛` (blue eyes) or `长发` (long hair)
3. Verify results appear (only works for confirmed tags, not suggestions)

## 4. PowerShell API Verification Flow

Full API-based validation using PowerShell (alternative to GUI):

```powershell
# Variables
$host = "http://localhost:8000"
$cred = @{ username = "admin"; password = "admin123" } | ConvertTo-Json
$path = "C:\Users\kyloris\Pictures\VioletTest100"

# Step 1: Login
$login = Invoke-RestMethod -Uri "$host/api/admin/login" -Method POST -Body $cred -ContentType "application/json"
$token = $login.access_token
$headers = @{ Authorization = "Bearer $token"; Cookie = "admin_mode=true" }

# Step 2: Check auto-tag config
Invoke-RestMethod -Uri "$host/api/admin/ai-tagging/auto-config" -Headers $headers

# Step 3: Dry-run scan
$body = @{ paths = @($path); dry_run = $true; max_files = 20 } | ConvertTo-Json
$dryJob = Invoke-RestMethod -Uri "$host/api/admin/scan-local-library/jobs" -Method POST -Body $body -ContentType "application/json" -Headers $headers
Write-Host "Dry-run job: $($dryJob.id)"

# Poll until done
do {
    Start-Sleep -Seconds 2
    $status = Invoke-RestMethod -Uri "$host/api/admin/scan-local-library/jobs/$($dryJob.id)" -Headers $headers
    Write-Host "  Status: $($status.status) imported=$($status.imported)"
} while ($status.status -in @("pending", "running"))

# Step 4: Real scan
$body = @{ paths = @($path); dry_run = $false; max_files = 20 } | ConvertTo-Json
$scanJob = Invoke-RestMethod -Uri "$host/api/admin/scan-local-library/jobs" -Method POST -Body $body -ContentType "application/json" -Headers $headers
Write-Host "Scan job: $($scanJob.id)"

do {
    Start-Sleep -Seconds 2
    $status = Invoke-RestMethod -Uri "$host/api/admin/scan-local-library/jobs/$($scanJob.id)" -Headers $headers
    Write-Host "  Status: $($status.status) processed=$($status.processed) imported=$($status.imported)"
} while ($status.status -in @("pending", "running"))

# Step 5: Wait for auto AI tagging
Start-Sleep -Seconds 3
$aiJobs = Invoke-RestMethod -Uri "$host/api/admin/ai-tagging/jobs" -Headers $headers
$latest = $aiJobs[0]
Write-Host "AI Job #$($latest.id): status=$($latest.status) trigger=$($latest.trigger_source)"

if ($latest.status -in @("pending", "running")) {
    do {
        Start-Sleep -Seconds 3
        $latest = Invoke-RestMethod -Uri "$host/api/admin/ai-tagging/jobs/$($latest.id)" -Headers $headers
        Write-Host "  AI: $($latest.status) processed=$($latest.processed) tags=$($latest.tags_added)"
    } while ($latest.status -in @("pending", "running", "cancelling"))
}

Write-Host "`n=== AI Job Results ==="
Write-Host "Processed: $($latest.processed)"
Write-Host "Tags added: $($latest.tags_added)"
Write-Host "Suggestions: $($latest.suggestions_added)"
Write-Host "Failed: $($latest.failed)"
Write-Host "Localization: $($latest.localization_status)"

# Step 6: Check translations
$stats = Invoke-RestMethod -Uri "$host/api/admin/tag-localization/stats" -Headers $headers
Write-Host "`n=== Translation Stats ==="
Write-Host "Total tags: $($stats.total_tags)"
Write-Host "Translated: $($stats.translated_db)"

# Step 7: Chinese search test
$search = Invoke-RestMethod -Uri "$host/api/search?q=蓝眼睛" -Headers $headers
Write-Host "`n=== Chinese Search ==="
Write-Host "Results for '蓝眼睛': $($search.total)"
```

## 5. Automated Validation Script

A Python script automates the full E2E flow:

```bash
# Default (20 files from VioletTest100)
python scripts/e2e_validate_violet_workflow.py

# Custom path and file count
python scripts/e2e_validate_violet_workflow.py --path "D:\TestImages" --max-files 50

# Skip scan (only check existing AI jobs)
python scripts/e2e_validate_violet_workflow.py --skip-scan

# Full options
python scripts/e2e_validate_violet_workflow.py \
  --host http://localhost:8000 \
  --username admin \
  --password admin123 \
  --path "C:\Users\kyloris\Pictures\VioletTest100" \
  --max-files 20
```

The script outputs a summary with pass/fail status for each pipeline stage.

## 6. Acceptance Criteria

| # | Criterion | How to Verify |
|---|-----------|--------------|
| 1 | Scan imports > 0 images | Scan job response: `imported > 0` |
| 2 | AI tag job auto-created | `trigger_source = "scan_job"` in AI jobs list |
| 3 | AI job completes | `status = "completed"` |
| 4 | Tags produced | `tags_added > 0` or `suggestions_added > 0` |
| 5 | No excessive failures | `failed / processed < 0.1` (< 10% failure rate) |
| 6 | Localization scheduled | `localization_status` starts with `scheduled_` (if LLM enabled) |
| 7 | Translations created | Translation stats show `translated_db > 0` (if LLM enabled) |
| 8 | Chinese search works | Search for `蓝眼睛` or `长发` returns results (for confirmed tags) |
| 9 | No server errors | No 500 errors in server logs during the test |
| 10 | Cancel works | Create a job, cancel it, verify `status = "cancelled"` |

## 7. Troubleshooting

### Model Not Available

```
GET /api/admin/ai-tagging/model-status → { "available": false, "error": "..." }
```

**Fix:**
- Ensure `AI_TAGGING_ENABLED=true` in `.env`
- Check internet connection (model downloads from HuggingFace on first use)
- Check disk space (~450 MB for default model)
- Check `~/.cache/huggingface/` for download issues
- Try running the server once with internet access to cache the model

### LLM Translation Unavailable

If `TAG_TRANSLATION_LLM_ENABLED=true` but translations aren't generated:

- Verify `TAG_TRANSLATION_LLM_API_KEY` is set (and not expired)
- Check `TAG_TRANSLATION_LLM_BASE_URL` is reachable
- Look for errors in server logs: `tag_localization_service`
- The pipeline still works without LLM — tags just won't have Chinese names

> **提示：** 如果没有 LLM API，可以设置 `TAG_TRANSLATION_LLM_ENABLED=false`。流水线其余部分照常工作，只是标签不会有中文翻译。

### Auto-tag Not Triggered After Scan

- Verify `AI_AUTO_TAG_AFTER_IMPORT=true` in `.env`
- Verify `AI_TAGGING_ENABLED=true`
- Check that the scan actually imported > 0 new images
- Check server logs for `auto-tag skipped` messages

### Scan Returns 409

Another scan job is already running. Wait for it to complete or cancel it:

```powershell
# List jobs to find the running one
Invoke-RestMethod -Uri "$host/api/admin/scan-local-library/jobs" -Headers $headers

# Cancel it
Invoke-RestMethod -Uri "$host/api/admin/scan-local-library/jobs/{id}/cancel" -Method POST -Headers $headers
```

### AI Job Shows Many Failures

- Check `failed_items` in the job response for specific error messages
- Common causes: corrupted images, unsupported formats passed through, out-of-memory
- Reduce `max_items` and retry with smaller batches

### Chinese Search Returns 0 Results

- AI tags are written as **suggestions** by default — suggestions are excluded from search
- Go to Admin → AI Tag Review → confirm tags to make them searchable
- Or set `AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS=false` to confirm tags directly (less safe)

## 8. Clean Up After Testing

To reset and re-run the test:

1. Delete imported media (via Admin UI or directly in DB)
2. Or simply re-run — duplicate detection will skip already-imported files
3. AI tagging respects `only_without_ai_tags` — already-tagged images are skipped

To fully reset:
```sql
-- WARNING: This deletes ALL imported media and tags
DELETE FROM blombooru_ai_tag_jobs;
DELETE FROM blombooru_scan_job_media;
DELETE FROM blombooru_scan_jobs;
DELETE FROM blombooru_media_tags;
DELETE FROM blombooru_media;
```

> **⚠️ 注意：** 上面的 SQL 会删除所有媒体数据。仅在测试环境使用。

## Reset Test Data

When you need to re-run the full E2E pipeline from scratch, reset test data first.

### GUI Reset

1. Go to **Admin → System → Developer / E2E Tools**
2. In the "Reset E2E Test Data" section, enter the source path:
   `C:\Users\kyloris\Pictures\VioletTest100`
3. Click **Dry Run Preview** to see what will be deleted
4. Review the summary (media count, files, thumbnails, scan jobs, AI jobs)
5. Click **Execute Reset** and confirm

### API Reset

```powershell
# Dry-run (preview only)
$body = @{ source_path = "C:\Users\kyloris\Pictures\VioletTest100"; dry_run = $true; confirm = $false } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/dev/reset-e2e-test-data" -Method POST -Body $body -ContentType "application/json" -Headers @{Authorization = "Bearer $token"; Cookie = "admin_mode=true"}

# Real reset
$body = @{ source_path = "C:\Users\kyloris\Pictures\VioletTest100"; dry_run = $false; confirm = $true } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/dev/reset-e2e-test-data" -Method POST -Body $body -ContentType "application/json" -Headers @{Authorization = "Bearer $token"; Cookie = "admin_mode=true"}
```

### CLI Reset

```bash
# Dry-run (default)
python scripts/reset_e2e_test_data.py --source-path "C:\Users\kyloris\Pictures\VioletTest100"

# Real deletion
python scripts/reset_e2e_test_data.py --source-path "C:\Users\kyloris\Pictures\VioletTest100" --yes
```

### What Gets Deleted

- Media records imported from the specified source path
- Copied files in `media/original/` and thumbnails
- Tag associations (media-tag links)
- Related scan jobs and scan-job-media links
- Related AI tagging jobs
- Tag `post_count` is recalculated

### What Is NOT Deleted

- Original files in `C:\Users\kyloris\Pictures\VioletTest100\`
- Tags themselves (they remain in the database)
- Tag translations
- Other media not from this source path

### Config Diagnostics

If config values seem wrong (e.g., batch_max_items shows 20 when you set 200), use the config diagnostics tool:

1. **Admin → System → Developer / E2E Tools → Config Diagnostics**
2. Or call: `GET /api/admin/dev/config-diagnostics`

Important: After changing `.env`, you **must restart the server** for changes to take effect.
