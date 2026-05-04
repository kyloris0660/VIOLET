# AI Tagging Usage Guide

Complete guide to using the WDv3 AI Auto Tagging feature in V.I.O.L.E.T.: what it can do, how to use it, what its limits are, and how to verify everything works.

---

## What the AI Tagger Can Do

V.I.O.L.E.T. uses the **WDv3 (SmilingWolf) ONNX tagger** — a Danbooru-trained image classification model that predicts visual tags from anime/illustration images. It is **not** a reverse image search engine, not an internet source identifier, and not a character database lookup tool.

### What It IS

- A visual feature classifier trained on millions of Danbooru-tagged anime images
- Predicts `general` tags (visual descriptors), some `character` tags, and `rating` tags
- Outputs confidence scores per tag (0.0 to 1.0)
- Writes tags with full provenance tracking (`source=ai_wd`, confidence, is_suggestion)

### What It Is NOT

- NOT a reverse image search (cannot find the original Pixiv/Twitter post)
- NOT an artist identifier (cannot reliably identify who drew an image)
- NOT a source/URL identifier (cannot find where an image came from)
- NOT a character database (does not have a complete list of all anime characters)
- NOT a copyright/franchise identifier (recognizes some popular series, misses many)

---

## 1. Model Information

### Current Model

| Setting | Value |
|---------|-------|
| Default model | `wd-swinv2-tagger-v3` |
| Model source | SmilingWolf on HuggingFace Hub |
| Model size | ~450 MB |
| Inference | CPU (via `onnxruntime`) |
| Speed | ~1-5 seconds per image (depending on image size and CPU) |

### Model File Location

Model files are stored in the HuggingFace cache directory:
- **Windows**: `C:\Users\<username>\.cache\huggingface\hub\models--SmilingWolf--wd-swinv2-tagger-v3\`
- **Linux**: `~/.cache/huggingface/hub/models--SmilingWolf--wd-swinv2-tagger-v3/`

These are **never** committed to git.

### First-Time Download

On the first AI tagging request, the model is downloaded automatically from HuggingFace Hub (~450 MB). This requires internet access. Subsequent uses load from the local cache.

### When Model Is Unavailable

If the model has not been downloaded or dependencies are missing:
- The application **starts normally** — AI tagging is optional
- The Admin UI shows "Model: Not downloaded (will download on first use)"
- `GET /api/admin/ai-tagging/model-status` returns `available: true, model_downloaded: false`
- Attempting to run AI tagging triggers automatic download (if internet is available)
- If download fails, a clear error message is returned

---

## 2. Configuration

### Required `.env` Settings

Add these to your `.env` file:

```env
# Enable AI tagging (required to use the feature)
AI_TAGGING_ENABLED=true

# Confidence thresholds (all optional, shown with defaults)
AI_GENERAL_THRESHOLD=0.35
AI_CHARACTER_THRESHOLD=0.65
AI_RATING_THRESHOLD=0.50
AI_SUGGESTION_THRESHOLD=0.20

# Batch limits
AI_TAGGING_BATCH_MAX_ITEMS=10

# Model selection (optional)
AI_MODEL_NAME=wd-swinv2-tagger-v3
```

### Enabling / Disabling

| `AI_TAGGING_ENABLED` | Behavior |
|-----------------------|----------|
| `false` (default) | AI tagging endpoints return 400 "disabled" |
| `true` | AI tagging is available in Admin UI and API |

To enable: add `AI_TAGGING_ENABLED=true` to `.env` and restart the server.
To disable: remove the line or set to `false` and restart.

---

## 3. Tag Types and Accuracy

### General Tags — RELIABLE

The model excels at predicting visual descriptors (Danbooru "general" tags):

| Category | Examples | Reliability |
|----------|----------|-------------|
| Body/appearance | `1girl`, `solo`, `long_hair`, `blue_eyes`, `blonde_hair` | Very high |
| Expression | `smile`, `blush`, `open_mouth`, `looking_at_viewer` | High |
| Clothing | `school_uniform`, `dress`, `hat`, `thighhighs` | High |
| Scene/composition | `white_background`, `simple_background`, `outdoors` | High |
| Actions | `sitting`, `standing`, `holding` | Moderate-high |
| Objects | `sword`, `book`, `flower` | Moderate |
| Style | `monochrome`, `comic`, `chibi` | High |

### Character Tags — PARTIAL

The model can identify characters from its training data (popular Danbooru characters), but:

| Scenario | Reliability |
|----------|-------------|
| Top-1000 most popular characters (Hatsune Miku, Artoria, etc.) | Moderate |
| Characters in typical/official outfits | Better |
| Characters in alternate outfits or styles | Poor |
| Obscure / niche characters | Very poor / not recognized |
| OC (original characters) | Never recognized |
| Characters from post-training-data works | Not recognized |
| Cropped / multi-character / obstructed images | Unreliable |

**Important**: Character threshold is set higher (0.65 default) specifically because character predictions are less reliable.

### Copyright / Work Tags — NOT SUPPORTED

WDv3 does **not** output copyright/work tags in a reliable way. The model's training data categories are limited to `general`, `character`, and `rating`.

### Artist / Source Tags — NOT SUPPORTED

WDv3 cannot identify artists, cannot find source URLs, and cannot determine where an image was originally posted. These require different tools (reverse image search, SauceNAO, IQDB).

### Rating Tags — FUNCTIONAL

WDv3 predicts content rating using Danbooru's rating system:
- `general` — safe for work
- `sensitive` — slightly suggestive
- `questionable` — moderately explicit
- `explicit` — explicitly adult content

These are mapped to the `meta` tag category. They do NOT automatically set the media's `rating` field (that remains a manual operation).

---

## 4. Confirmed vs. Suggestion Tags

### The Dual Threshold System

For each predicted tag, the AI compares its confidence against two thresholds:

```
confidence >= confirm_threshold     → CONFIRMED tag (is_suggestion=false)
suggestion_threshold <= confidence < confirm_threshold → SUGGESTION tag (is_suggestion=true)
confidence < suggestion_threshold   → IGNORED (not stored)
```

### Behavior Differences

| Aspect | Confirmed Tag | Suggestion Tag |
|--------|---------------|----------------|
| Stored in DB | Yes | Yes |
| `source` | `ai_wd` | `ai_wd` |
| `is_suggestion` | `false` | `true` |
| Visible in media detail | Yes | Yes (with provenance info) |
| Participates in normal search | **Yes** | **No** |
| Shows in tag autocomplete | Yes (counted) | No (excluded from counts) |
| Can be overwritten by manual tag | N/A (manual always wins) | N/A |

### Manual / Locked Tag Protection

AI tags **never** overwrite existing tags that are:
- `is_locked = true` (explicitly locked by user)
- `source = 'manual'` (added by human)

If a media already has a manual tag with the same name, the AI silently skips it (reported as `skipped_locked` in the summary).

---

## 5. Using the GUI

### A. Starting the Application (Windows)

```powershell
cd C:\path\to\AnimeLocalBooru  # V.I.O.L.E.T. project directory
git checkout main
git pull origin main
.\venv\Scripts\Activate.ps1
python run.py --debug
```

Open in browser: http://localhost:8000

### Logging In

1. Click the user/admin icon in the navigation bar
2. Enter credentials:
   - Username: `admin`
   - Password: `admin123`
3. Toggle **Admin Mode** on (required for all admin operations)

> **Note**: These are local development credentials created during onboarding. Never use these for a public-facing deployment.

### B. Manual Verification: Local Library Scan

1. Open **Admin Panel** → **Content** tab
2. Scroll to **Local Library Scan** section
3. Enter test path: `C:\Users\kyloris\Pictures\AnimeLocalBooruTest`
4. Check **Dry Run** ✓
5. Set **Max Files**: `10`
6. Click **Start Scan**
7. Watch the progress bar and stats update
8. After completion, check **Scan History** table below

**Important**: Do NOT directly scan `C:\Users\kyloris\Pictures\iCloud Photos` without:
- Dry-run first
- max_files=100 to test
- Verifying results before full import

The iCloud Photos directory contains thousands of images (many non-anime). Always test incrementally.

### C. Checking AI Model Status

1. Open **Admin Panel** → **Content** tab
2. Find the **AI Auto Tagging** section (between Local Library Scan and Tags Management)
3. The **Model Status** box shows:
   - **AI Tagging**: Enabled/Disabled
   - **Model**: Name of the configured model
   - **Dependencies**: Available/Unavailable
   - **Model**: Downloaded/Not downloaded
   - **Runtime**: Loaded/Not loaded
   - **Thresholds**: Current configuration values

Click **Refresh** to re-check status.

**API Verification** (PowerShell):

```powershell
# Login
$resp = Invoke-WebRequest -Uri "http://localhost:8000/api/admin/login" `
  -Method Post -Body '{"username":"admin","password":"admin123"}' `
  -ContentType "application/json" -UseBasicParsing
$token = ($resp.Content | ConvertFrom-Json).access_token

# Create session with auth cookies
$s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$s.Cookies.Add((New-Object System.Net.Cookie("admin_mode", "true", "/", "localhost")))
$s.Cookies.Add((New-Object System.Net.Cookie("admin_token", $token, "/", "localhost")))

# Check model status
$r = Invoke-WebRequest -Uri "http://localhost:8000/api/admin/ai-tagging/model-status" `
  -Method Get -WebSession $s -UseBasicParsing
$r.Content | ConvertFrom-Json | ConvertTo-Json -Depth 5
```

**Status meanings:**

| Field | Value | Meaning |
|-------|-------|---------|
| `enabled` | `true` | `AI_TAGGING_ENABLED=true` in .env |
| `enabled` | `false` | Feature disabled, add `AI_TAGGING_ENABLED=true` to .env |
| `available` | `true` | All Python dependencies installed |
| `available` | `false` | Missing `onnxruntime` or other dependency |
| `model_downloaded` | `true` | Model files cached locally |
| `model_downloaded` | `false` | Will download on first use (~450 MB) |
| `loaded` | `true` | Model loaded in memory, ready for inference |
| `loaded` | `false` | Model will load on first request |

### D. Single Image Dry-Run

**Finding a Media ID:**
- Browse the gallery, click any image
- The URL will be `http://localhost:8000/media/{id}`
- Or use the API: `GET /api/media?limit=5` → look at `items[].id`

**GUI:**
1. Go to Admin → Content → AI Auto Tagging
2. Enter the media ID (e.g., `1`)
3. Check **Dry Run** ✓
4. Click **Run AI Tagging**
5. Results appear below: tags predicted, confidence values, actions (confirmed/suggestion/ignored)
6. Database is NOT modified (dry run)

**API (PowerShell):**

```powershell
# (Using $s session from above)
$r = Invoke-WebRequest `
  -Uri "http://localhost:8000/api/admin/ai-tagging/media/1?dry_run=true" `
  -Method Post -WebSession $s -Body '{}' -ContentType "application/json" -UseBasicParsing
$data = $r.Content | ConvertFrom-Json
Write-Output "Tags that would be added: $($data.tags_added)"
Write-Output "Suggestions that would be added: $($data.suggestions_added)"
Write-Output "Ignored (low confidence): $($data.ignored_low_confidence)"
```

**Confirming no DB write:**
Check the media detail before and after:
```powershell
# Before dry-run
$before = (Invoke-WebRequest -Uri "http://localhost:8000/api/media/1" -WebSession $s -UseBasicParsing).Content | ConvertFrom-Json
$before.tags.Count  # Note this number

# After dry-run (should be same)
$after = (Invoke-WebRequest -Uri "http://localhost:8000/api/media/1" -WebSession $s -UseBasicParsing).Content | ConvertFrom-Json
$after.tags.Count  # Should be unchanged
```

### E. Single Image Real Write

**GUI:**
1. Uncheck **Dry Run**
2. Enter media ID
3. Click **Run AI Tagging**
4. Results show tags_added and suggestions_added
5. Navigate to the media detail page to see new tags

**API (PowerShell):**

```powershell
$r = Invoke-WebRequest `
  -Uri "http://localhost:8000/api/admin/ai-tagging/media/1?dry_run=false" `
  -Method Post -WebSession $s -Body '{}' -ContentType "application/json" -UseBasicParsing
$data = $r.Content | ConvertFrom-Json
Write-Output "Tags added: $($data.tags_added)"
Write-Output "Suggestions added: $($data.suggestions_added)"
```

**Verifying provenance:**

```powershell
$media = (Invoke-WebRequest -Uri "http://localhost:8000/api/media/1" -WebSession $s -UseBasicParsing).Content | ConvertFrom-Json

# Check tag_provenance for AI tags
$media.tag_provenance.PSObject.Properties | ForEach-Object {
    $p = $_.Value
    if ($p.source -eq "ai_wd") {
        Write-Output "Tag ID $($_.Name): source=$($p.source) confidence=$($p.confidence) suggestion=$($p.is_suggestion)"
    }
}
```

### F. Batch AI Tagging

**GUI:**
1. In AI Auto Tagging section, scroll to **Batch AI Tagging**
2. Optionally enter specific Media IDs (comma-separated) or leave empty for auto-selection
3. Set **Max Items**: `5` (start small!)
4. Check **Dry Run** ✓ (always test first!)
5. Click **Start Batch AI Tagging**
6. Review results in the table below

**API (PowerShell):**

```powershell
# Batch dry-run: auto-select 3 un-tagged images
$body = '{"max_items":3, "dry_run":true, "only_without_ai_tags":true}'
$r = Invoke-WebRequest -Uri "http://localhost:8000/api/admin/ai-tagging/batch" `
  -Method Post -WebSession $s -Body $body -ContentType "application/json" -UseBasicParsing
$data = $r.Content | ConvertFrom-Json
Write-Output "Processed: $($data.processed), Added: $($data.tags_added), Failed: $($data.failed)"
```

**Safety rules:**
- `max_items` is capped by `AI_TAGGING_BATCH_MAX_ITEMS` (default 10)
- Requesting more than the cap returns HTTP 400
- If `media_ids` is omitted, only selects images without existing AI tags
- **Per-item failure isolation**: single-item failures are rolled back and do not cascade — the `failed` count is recorded in the summary and the batch continues
- Thread-pool workers use independent DB sessions (not the request-scoped session)
- Always dry-run first when testing new settings

**Batch limit notes:**
- The "Max Items" field in the UI is not unlimited
- Backend enforces `AI_TAGGING_BATCH_MAX_ITEMS` (default 10)
- This prevents accidental full-library AI tagging
- Adjustable in `.env`: `AI_TAGGING_BATCH_MAX_ITEMS=50`
- Not recommended to remove the limit entirely
- Large batches should use background jobs with progress tracking and cancel

### G. Searching AI Tags

**Confirmed AI tags** participate in normal search. After tagging an image:

```
http://localhost:8000/?q=blue_theme
http://localhost:8000/?q=1girl+long_hair
http://localhost:8000/?q=monochrome
```

These work exactly like manually-added tags in search.

**Suggestion tags** do NOT participate in normal search. They are stored but invisible to the standard search engine. This is intentional — suggestions need manual review before they should affect search results. (Phase 2.2 will add a suggestion search syntax.)

### H. Suggestion Tags

**How they're created:**
- When AI confidence is between `AI_SUGGESTION_THRESHOLD` (0.20) and the category's confirm threshold (0.35/0.65/0.50)
- Example: a general tag with confidence 0.28 → stored as suggestion

**Where they're visible:**
- `GET /api/media/{id}` → `tag_provenance` dict shows `is_suggestion: true`
- They are stored in the database with full provenance

**Review UI (Phase 2.2):**
The AI Tag Review UI is now available in Admin Panel → Content → AI Tag Review:
- View all suggestions with confidence scores
- Confirm/reject individual suggestions
- Bulk confirm/reject selected suggestions
- Filter by confidence, tag name, media ID
- See [AI Tag Review documentation](ai-tag-review.md) for full details

---

## 6. Capability Boundaries

### Strong At (High Reliability)

General visual tags from Danbooru vocabulary:
- `1girl`, `solo`, `multiple_girls`
- `long_hair`, `short_hair`, `blonde_hair`, `blue_eyes`
- `smile`, `blush`, `open_mouth`, `looking_at_viewer`
- `school_uniform`, `dress`, `hat`, `thighhighs`
- `white_background`, `simple_background`, `outdoors`
- `monochrome`, `comic`, `chibi`, `sketch`
- Physical descriptors, clothing, background, style

### Partial (Variable Reliability)

- Popular character identification (top ~2000 Danbooru characters)
- Series-specific costumes/items
- Rating classification

### Unreliable or Unsupported

| Task | Status | Why |
|------|--------|-----|
| Obscure characters | Unreliable | Not in training data |
| Fan-art variations | Poor | Different from canonical appearance |
| Cropped images | Poor | Missing context |
| Low-resolution images | Degraded | Insufficient detail |
| Animation screenshots | Variable | Style differs from illustrations |
| Multi-character complex scenes | Variable | Confusion between characters |
| Non-typical outfits | Poor | Model relies on visual features |
| Identifying source URL | **Not supported** | Requires reverse image search |
| Identifying Pixiv/Twitter artist | **Not supported** | Requires external services |
| Reliably identifying all works | **Not supported** | No copyright tag output |
| Building character/work database | **Not supported** | Not a database tool |
| Tag alias/implication | **Not supported** | Phase 2.2+ feature |
| Reverse image search | **Not supported** | Completely different technology |
| Manual human review | **Not supported** | Phase 2.2 |

---

## 7. Should Imports Auto-Tag?

### Current State

AI tagging is **manually triggered only**. It does NOT run after local library scans. This is intentional.

### Why Not Auto-Tag Now

1. **iCloud Photos contains non-anime images** — auto-tagging would produce garbage tags on photos, screenshots, memes
2. **No anime detection** — cannot distinguish anime illustrations from photos
3. **No review UI** — incorrect auto-tags cannot be efficiently corrected
4. **CPU cost** — ~1-5 seconds per image × thousands of images = hours of CPU time
5. **Tag pollution risk** — incorrect tags degrade search quality

### When Auto-Tagging Should Be Added

Auto-tagging should be introduced **after Phase 2.2** (AI Tag Review UI), when:
- Users can quickly review and correct AI suggestions
- Bulk confirm/reject is available
- The system has been validated on real library content

### Recommended Architecture (Phase 2.3)

```
scan_job completes → creates ai_tagging_job (if enabled)
                     ↓
                     background thread
                     ↓
                     processes new imports only
                     ↓
                     respects max_items / dry-run / only_without_ai_tags
                     ↓
                     writes as suggestions (not confirmed)
                     ↓
                     user reviews in Phase 2.2 UI
```

Key design principles:
- Default OFF (`AI_AUTO_TAG_AFTER_IMPORT=false`)
- Non-blocking (separate background job from scan)
- Conservative (write as suggestions, not confirmed)
- Limited (max_items per batch, not entire library)
- Reversible (suggestions can be bulk-rejected)
- Configurable via Admin UI with clear risk warnings
- Has progress tracking and history
- Protects manual/locked tags

---

## 8. Future Roadmap

### Phase 2.2 — AI Tag Review UI ✅

Completed. Full review capabilities available:
- List, confirm, reject, lock, delete AI suggestions via API and Admin UI
- Bulk confirm/reject with multi-select
- Confidence and tag name filtering
- Provenance-aware display in media detail
- See [AI Tag Review](ai-tag-review.md) for documentation

### Phase 2.3 — Optional Auto Tagging After Import

- Scan job completion triggers optional AI tagging job
- Default OFF, configurable in Admin UI
- Does not block import process
- Respects max_items / dry-run / only_without_ai_tags
- Only processes newly imported or un-tagged images
- Writes as suggestions (requires Phase 2.2 review)
- Has dedicated progress and history tracking
- Clear Admin UI toggle with safety warnings

### Phase 3 — Anime Filtering & Source Identification

- Distinguish anime illustrations from photos
- Reverse image search integration (SauceNAO, IQDB)
- Source URL auto-fill from booru databases
- Artist identification from known databases

### Phase 4 — Watcher & Scheduled Scan

- Filesystem watcher for automatic import
- Scheduled scan intervals
- New-file-only incremental scanning

---

## 9. Troubleshooting

### "AI tagging is disabled"

Add `AI_TAGGING_ENABLED=true` to your `.env` file and restart the server.

### "AI tagger dependencies not available"

Missing Python packages. Run:
```powershell
.\venv\Scripts\pip.exe install onnxruntime numpy pandas pillow huggingface_hub
```

### Model download takes too long

First download is ~450 MB. On slow connections this can take 5-10 minutes. The progress is not shown in the API response — check your network usage.

### "File not found" error on tagging

The media file path stored in the database doesn't match a file on disk. This can happen if:
- Files were moved after import
- The storage directory was relocated
- The media was imported as a reference to an external path that no longer exists

### All tags are "ignored"

Your thresholds may be too high. Try:
```env
AI_GENERAL_THRESHOLD=0.25
AI_SUGGESTION_THRESHOLD=0.15
```

### Too many incorrect tags

Your thresholds may be too low. Try:
```env
AI_GENERAL_THRESHOLD=0.45
AI_CHARACTER_THRESHOLD=0.75
```

### Server won't start after enabling AI tagging

AI tagging is optional — the server should always start regardless of model availability. If it crashes, check for unrelated Python errors. The AI tagging feature uses late imports specifically to avoid startup failures.
