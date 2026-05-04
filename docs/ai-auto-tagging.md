# AI Auto Tagging (Phase 2.1)

Automatically tag imported images using the WDv3 (SmilingWolf) ONNX tagger. Tags are written with full provenance tracking (source, confidence, suggestion state) and respect manual/locked tag priority.

## Dependencies

| Package | Purpose |
|---------|---------|
| `onnxruntime` | ONNX model inference (CPU) |
| `numpy` | Array operations for image preprocessing |
| `pandas` | Reading tag label CSV |
| `Pillow` | Image loading and preprocessing |
| `huggingface_hub` | Model file download from HuggingFace Hub |

All dependencies are already in `requirements.txt`. No additional installation is needed.

## Model Files

The WDv3 tagger uses ONNX model files from SmilingWolf's HuggingFace repositories. Model files are **not** stored in git — they are downloaded automatically on first use to the HuggingFace cache directory (`~/.cache/huggingface/`).

### Available Models

| Model | Speed | Size |
|-------|-------|------|
| `wd-vit-tagger-v3` | Fastest | ~350 MB |
| `wd-convnext-tagger-v3` | Fast | ~350 MB |
| `wd-swinv2-tagger-v3` | Medium (default) | ~450 MB |
| `wd-eva02-large-tagger-v3` | Slow | ~850 MB |
| `wd-vit-large-tagger-v3` | Slowest | ~1.2 GB |

Default model: `wd-swinv2-tagger-v3` (good balance of quality and speed).

### Model Not Available

If the model is not downloaded or dependencies are missing:

- The application **starts normally** — AI tagging is an optional feature
- `GET /api/admin/ai-tagging/model-status` returns `available: false` with an error message
- AI tagging API endpoints return 503 with a clear error
- The Admin UI shows the model status and does not allow triggering tagging

## Configuration

Add to `.env`:

```env
AI_TAGGING_ENABLED=true
AI_GENERAL_THRESHOLD=0.35
AI_CHARACTER_THRESHOLD=0.65
AI_RATING_THRESHOLD=0.50
AI_SUGGESTION_THRESHOLD=0.20
AI_TAGGING_BATCH_MAX_ITEMS=10
AI_MODEL_NAME=wd-swinv2-tagger-v3
```

### Threshold Semantics

For each predicted tag:

| Confidence Range | Result | `is_suggestion` |
|-----------------|--------|-----------------|
| `>= confirm_threshold` | Confirmed tag | `false` |
| `>= suggestion_threshold` and `< confirm_threshold` | Suggestion tag | `true` |
| `< suggestion_threshold` | Ignored | N/A |

Where `confirm_threshold` depends on the tag category:
- **General tags**: `AI_GENERAL_THRESHOLD` (default 0.35)
- **Character tags**: `AI_CHARACTER_THRESHOLD` (default 0.65) — higher to reduce false character identifications
- **Rating tags**: `AI_RATING_THRESHOLD` (default 0.50)

The `AI_SUGGESTION_THRESHOLD` (default 0.20) is the floor below which tags are completely ignored.

### Manual / Locked Tag Priority

AI tags **never** overwrite manual or locked tags. If a media–tag association already exists with `is_locked=true` or `source='manual'`, the AI tag is silently skipped (counted as `skipped_locked` in the summary).

## API Endpoints

### Check Model Status

```
GET /api/admin/ai-tagging/model-status
```

Returns model availability, download status, current configuration, and thresholds.

### Tag Single Image

```
POST /api/admin/ai-tagging/media/{media_id}?dry_run=false
```

Runs WDv3 inference on one image. Returns per-tag predictions with confidence and action (confirmed/suggestion/ignored).

- `dry_run=true`: Returns predictions without writing to DB
- `dry_run=false`: Writes tags with provenance (source=ai_wd, confidence, is_suggestion)

### Batch Tag

```
POST /api/admin/ai-tagging/batch
Content-Type: application/json

{
  "media_ids": [1, 2, 3],     // optional — omit to auto-select
  "max_items": 5,              // default 10, capped by AI_TAGGING_BATCH_MAX_ITEMS
  "dry_run": true,             // default false
  "only_without_ai_tags": true // default true
}
```

**Safety controls:**
- `max_items` is clamped to `AI_TAGGING_BATCH_MAX_ITEMS` (default 10)
- If `media_ids` is omitted, selects only images without existing AI tags
- `dry_run=true` returns predictions without writing
- Single-item failures do not stop the batch

## Admin UI

The Admin Panel → Content tab includes an **AI Auto Tagging** section:

1. **Model Status** — Shows model availability, download status, and configuration
2. **Single Image Tagging** — Enter a media ID, optionally dry-run, and run
3. **Batch AI Tagging** — Optionally specify IDs and max items, dry-run, and run
4. **Results** — Summary (processed, added, suggestions, locked, ignored, failed) and per-image detail table

## Dry Run

When `dry_run=true`:
- Model inference runs normally (tags are predicted)
- No database writes occur
- Response includes all predictions with their computed action (confirmed/suggestion/ignored)
- Use to preview what would happen before committing

## Why Default Is Not Full Library

AI tagging is **manually triggered only** — it does NOT run automatically during local library scan. Reasons:

1. iCloud Photos directories may contain thousands of non-anime images
2. Model inference is CPU-intensive (~1-5 seconds per image)
3. Accidental full-library tagging could create thousands of incorrect tags
4. Manual control lets you verify results before scaling up

### Recommended Testing Workflow

1. Check model status in Admin UI
2. Run dry-run on 1 image → verify predictions look correct
3. Run real tagging on 1 image → verify tags appear in media detail
4. Run dry-run batch of 5 → verify predictions
5. Run real batch of 5 → verify results
6. Gradually increase batch size as confidence grows

## Data Model

AI tags use the existing `blombooru_media_tags` provenance system from Phase 2:

| Column | AI Tag Value |
|--------|-------------|
| `source` | `ai_wd` |
| `confidence` | Model confidence (0.0–1.0) |
| `is_locked` | `false` |
| `is_suggestion` | `true` if below confirm threshold |
| `created_at` | When the tag was written |

Tags are visible in:
- `GET /api/media/{id}` → `tag_provenance` dict shows source/confidence/is_suggestion per tag
- Media detail page (existing UI shows tags; provenance data accessible via API)

Suggestion tags are **excluded from normal search** (consistent with Phase 2 design).

## Files Modified / Added

| File | Change |
|------|--------|
| `backend/app/services/ai_tagging_service.py` | **New** — AI tagging orchestration service |
| `backend/app/routes/admin/ai_tagging.py` | **New** — Admin API endpoints |
| `backend/app/routes/admin/__init__.py` | Register AI tagging router |
| `backend/app/config.py` | AI tagging configuration properties |
| `example.env` | AI tagging configuration examples |
| `frontend/templates/admin.html` | AI Tagging section in Content tab |
| `frontend/static/js/admin.js` | AI tagging UI logic |
| `.gitignore` | Exclude *.onnx, models/, .cache/ |
| `docs/ai-auto-tagging.md` | **New** — this document |

## Known Limitations

- No scheduled or automatic AI tagging — manual trigger only
- No tag review UI for suggestions (Phase 2.2)
- No anime/photo filtering (Phase 3)
- Character identification depends on model training data; novel characters will not be recognized
- Rating tags from WDv3 use names like `general`, `sensitive`, `questionable`, `explicit` which map to the `meta` category, not the media `rating` field
- First run requires internet access to download model files (~350-1200 MB)

## Next Steps (Phase 2.2)

- **AI Tag Review UI**: Inline suggestion review (confirm/reject) in media detail
- **Suggestion search syntax**: e.g. `suggestion:tag_name`
- **Tag alias support**: Resolve aliases before AI tag lookup
- **Tag implication chains**: Auto-apply implications when AI tags are confirmed
