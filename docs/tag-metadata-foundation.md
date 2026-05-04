# Tag Metadata Foundation

Phase 2 extends the media–tag relationship to support provenance tracking (source, confidence, lock status, suggestion state), laying the groundwork for AI auto tagging in Phase 2.1.

## Data Model

The `blombooru_media_tags` junction table now carries six additional columns:

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `source` | `VARCHAR(50)` | `'manual'` | Origin of the tag association |
| `confidence` | `FLOAT` | `NULL` | Model confidence score (0.0–1.0) |
| `is_locked` | `BOOLEAN` | `TRUE` | Prevents AI from overwriting this tag |
| `is_suggestion` | `BOOLEAN` | `FALSE` | Low-confidence tags shown as suggestions |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | `NOW()` | When the association was created |
| `updated_at` | `TIMESTAMP WITH TIME ZONE` | `NOW()` | Last modification time |

### Source Values

| Value | Meaning |
|-------|---------|
| `manual` | User added the tag via UI or API |
| `ai_wd` | WDv3 / SmilingWolf ONNX tagger |
| `booru_import` | Imported from Danbooru/Gelbooru via booru import |
| `reverse_search` | Future: from reverse image search (SauceNAO/IQDB) |
| `system` | Future: system-generated tags (media type, etc.) |

### Priority Rules

1. **Manual always wins.** If a media–tag pair has `is_locked=true` or `source='manual'`, AI tagging will **not** overwrite it.
2. **AI tags respect thresholds.** Tags with confidence below the confirmation threshold are stored as suggestions (`is_suggestion=true`).
3. **Suggestions don't affect search.** Tags with `is_suggestion=true` are excluded from normal tag search results and tag counts.
4. **Confirming a suggestion** upgrades it to `source='manual'`, `is_locked=true`, `is_suggestion=false`, `confidence=1.0`.

## Tag Service API

All tag association operations should use `backend/app/services/tag_service.py`:

| Function | Purpose |
|----------|---------|
| `add_manual_tag_to_media(db, media_id, tag_id)` | Add/upgrade a manual locked tag |
| `add_manual_tags_to_media(db, media_id, tag_ids)` | Bulk manual add |
| `add_booru_import_tag_to_media(db, media_id, tag_id)` | Add a booru-imported tag |
| `add_ai_tag_to_media(db, media_id, tag_id, confidence, ...)` | Add an AI tag with priority rules |
| `set_media_tags_manual(db, media_id, tag_ids)` | Replace all tags (like PATCH update) |
| `confirm_suggestion(db, media_id, tag_id)` | Accept a suggestion |
| `reject_suggestion(db, media_id, tag_id)` | Delete a suggestion |
| `update_tag_provenance(db, media_id, tag_id, ...)` | Update specific provenance fields |
| `remove_tag_from_media(db, media_id, tag_id)` | Delete a tag association |
| `get_media_tag_provenance(db, media_id)` | Get provenance data for all tags |

### AI Tag Behaviour

```python
from backend.app.services.tag_service import add_ai_tag_to_media

added = add_ai_tag_to_media(
    db,
    media_id=42,
    tag_id=100,
    confidence=0.85,
    source="ai_wd",
    confirm_threshold=0.35,
)
# added=True  → tag was inserted/updated
# added=False → existing manual/locked tag was preserved
```

- If `confidence >= confirm_threshold`: `is_suggestion=False` (confirmed)
- If `confidence < confirm_threshold`: `is_suggestion=True` (suggestion only)
- If an existing association has `is_locked=True` or `source='manual'`: no change, returns `False`

## Database Migration

Migration function: `migrate_add_media_tags_provenance` in `backend/app/database.py`.

**Behaviour:**
- Adds 6 columns to `blombooru_media_tags` if `source` column doesn't exist
- Backfills all existing rows with: `source='manual'`, `confidence=1.0`, `is_locked=true`, `is_suggestion=false`
- Creates indexes on `source` and `is_suggestion`
- Idempotent: safe to run multiple times

**Backup:** Before running, ensure a database backup exists (e.g. `pg_dump -Fc blombooru -f backups/blombooru_before_phase2.dump`).

## Search Behaviour

Normal tag search only considers **confirmed** tags (`is_suggestion=false`):
- Wildcard searches filter on `is_suggestion=false`
- Tag count searches (`tagcount:`, `gentags:`, etc.) exclude suggestions
- `update_tag_counts()` excludes suggestions from `post_count`
- Standard `Media.tags.contains(tag)` still works for exact tag searches — no suggestions are created in Phase 2, so this is safe

**Phase 2.2** will add explicit suggestion search support (e.g. `suggestion:tag_name`).

## API Changes

### Media Detail (`GET /api/media/{id}`)

Response now includes `tag_provenance` — a dict keyed by `tag_id`:

```json
{
  "id": 42,
  "tags": [{"id": 1, "name": "1girl", ...}],
  "tag_provenance": {
    "1": {
      "tag_id": 1,
      "source": "manual",
      "confidence": 1.0,
      "is_locked": true,
      "is_suggestion": false,
      "created_at": "2026-05-04T12:00:00+00:00",
      "updated_at": "2026-05-04T12:00:00+00:00"
    }
  }
}
```

Existing fields are unchanged. `tag_provenance` is additive.

## Future Integration Points

### Phase 2.1 — WDv3 AI Auto Tagging

The WDv3 tagger (`backend/app/services/wd_tagger.py`) will:
1. Run inference on each image → get `{tag_name: confidence}` dict
2. For each tag, call `get_or_create_tags()` to ensure the tag exists
3. Call `add_ai_tag_to_media()` with the confidence score
4. Tags above threshold become confirmed; below become suggestions
5. Manual/locked tags are never overwritten

### Phase 2.2 — Character & Copyright Enrichment

- Tag aliases will resolve before provenance lookup
- Tag implications will trigger chain additions with `source='system'`
- Character/copyright database will provide category hints

### Reverse Image Search

Future reverse search results will use `source='reverse_search'` when adding source/artist tags.

## Files Modified

| File | Change |
|------|--------|
| `backend/app/models.py` | Added columns to `blombooru_media_tags` Table |
| `backend/app/database.py` | Added `migrate_add_media_tags_provenance` |
| `backend/app/services/tag_service.py` | **New** — tag provenance helpers |
| `backend/app/routes/media.py` | Uses tag service for add/update/count |
| `backend/app/routes/booru_import.py` | Uses tag service for booru imports |
| `backend/app/utils/search_parser.py` | Excludes suggestions from search |
| `.gitignore` | Added `storage/`, `backups/` |
