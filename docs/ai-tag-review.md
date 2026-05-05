# AI Tag Review (Phase 2.2)

Provides a UI and API for reviewing AI-generated tag suggestions — confirming, rejecting, locking, and deleting tags. This is the critical step that makes the AI tagging pipeline usable: without review, suggestions are invisible to search and cannot be trusted.

## Core Concepts

### Tag States

| State | `is_suggestion` | `is_locked` | Searchable | Meaning |
|-------|-----------------|-------------|------------|---------|
| Manual/Locked | `false` | `true` | Yes | User-added or confirmed tag |
| AI Confirmed | `false` | `false` | Yes | AI tag above confirm threshold |
| AI Suggestion | `true` | `false` | **No** | AI tag below confirm threshold, pending review |
| Reviewed (Confirmed) | `false` | `true` | Yes | Suggestion confirmed by user |

### Review Actions

| Action | Behavior | Result |
|--------|----------|--------|
| **Confirm** | `is_suggestion=false`, `is_locked=true`, source/confidence preserved | Tag becomes searchable |
| **Reject** | Association deleted from DB | Tag disappears entirely |
| **Lock** | `is_locked=true`, `is_suggestion=false` | Tag protected from AI overwrite |
| **Delete** | Association removed | Tag gone (refuses manual+locked unless force=true) |

### Important Semantics

- **Confirm preserves AI provenance**: When confirming a suggestion, `source` stays `ai_wd` and `confidence` is preserved. This allows tracking which tags came from AI even after human review.
- **Reject does NOT persist**: Current MVP simply deletes the suggestion. If AI tagging runs again, the same tag may be re-suggested. A future `rejected_decisions` table could solve this.
- **Manual/locked protection**: Bulk delete and single delete refuse to remove manual+locked tags unless `force=true` is explicitly set.
- **Confirm makes searchable**: After confirmation, the tag participates in normal Danbooru-style search.

## API Endpoints

All endpoints require admin authentication (JWT + admin_mode cookie).

### List Suggestions

```
GET /api/admin/ai-tags/review
```

Query parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Max items (1–200) |
| `offset` | int | 0 | Pagination offset |
| `min_confidence` | float | — | Floor confidence filter |
| `max_confidence` | float | — | Ceiling confidence filter |
| `tag_name` | string | — | Substring match on tag name |
| `media_id` | int | — | Filter to specific media |
| `source` | string | — | Filter by source (e.g. `ai_wd`) |
| `order` | string | `confidence_desc` | Sort: `confidence_desc`, `confidence_asc`, `created_desc` |

Response:

```json
{
  "items": [
    {
      "media_id": 1,
      "tag_id": 42,
      "tag_name": "blue_eyes",
      "tag_category": "general",
      "source": "ai_wd",
      "confidence": 0.2834,
      "is_locked": false,
      "is_suggestion": true,
      "created_at": "2026-05-04T12:00:00+00:00",
      "updated_at": "2026-05-04T12:00:00+00:00",
      "thumbnail_url": "/api/media/1/thumbnail",
      "media_filename": "image_001.jpg"
    }
  ],
  "total": 150,
  "limit": 50,
  "offset": 0
}
```

### Confirm Suggestion

```
POST /api/admin/ai-tags/{media_id}/{tag_id}/confirm
```

Sets `is_suggestion=false`, `is_locked=true`. Preserves `source=ai_wd` and original confidence. Updates `post_count`.

### Reject Suggestion

```
POST /api/admin/ai-tags/{media_id}/{tag_id}/reject
```

Deletes the suggestion association. Only works on `is_suggestion=true` tags. Does NOT affect manual/locked tags.

**Limitation**: Rejection is not persistent. Re-running AI tagging may regenerate the same suggestion. Future improvement: `rejected_tag_decisions` table.

### Lock Tag

```
POST /api/admin/ai-tags/{media_id}/{tag_id}/lock
```

Sets `is_locked=true`, `is_suggestion=false`. Protects from future AI overwrite.

### Delete Tag

```
DELETE /api/admin/ai-tags/{media_id}/{tag_id}?force=false
```

Removes the tag association. Refuses to delete manual+locked tags unless `force=true`.

### Bulk Operations

```
POST /api/admin/ai-tags/bulk
Content-Type: application/json

{
  "action": "confirm",
  "items": [
    {"media_id": 1, "tag_id": 42},
    {"media_id": 1, "tag_id": 43}
  ]
}
```

Supported actions: `confirm`, `reject`, `lock`, `delete`.

Constraints:
- Max 100 items per request
- Individual failures do not abort the batch
- Returns summary with success/failed counts and error details
- Bulk delete skips manual+locked tags (counted as failed)

Response:

```json
{
  "action": "confirm",
  "total": 10,
  "success": 8,
  "failed": 2,
  "errors": [
    {"media_id": 1, "tag_id": 99, "error": "not a suggestion or not found"}
  ]
}
```

## Admin UI

The Admin Panel → Content tab includes an **AI Tag Review** section below AI Auto Tagging:

1. **Filters** — Min/max confidence, tag name search, media ID filter
2. **Review Table** — Thumbnail, media ID, tag name, category, confidence, source, action buttons
3. **Single Actions** — ✓ Confirm, ✗ Reject, 🔒 Lock per row
4. **Multi-select** — Checkbox selection with Select All
5. **Bulk Actions** — Bulk Confirm Selected, Bulk Reject Selected
6. **Pagination** — Previous/Next with page info
7. **Auto-refresh** — List refreshes after every action

## Media Detail Provenance Display

The media detail page now visually distinguishes tags:

- **Confirmed/manual tags**: Normal display with optional 🔒 icon if locked
- **AI tags**: Show source and confidence in tooltip
- **Suggestions**: Displayed in a separate "Suggestions (AI)" section with dashed border, reduced opacity, and confidence percentage

## Search Behavior

- Normal tag search only returns media with **confirmed** tags (`is_suggestion=false`)
- Suggestions do NOT appear in search results or tag autocomplete
- After confirming a suggestion, the tag immediately participates in search
- Suggestion search syntax (`suggestion:tag_name`) is deferred to a future phase

## Manual/Locked Tag Protection

The review system enforces these safety rules:

1. Manual+locked tags cannot be deleted via review API (unless `force=true`)
2. Bulk delete skips manual+locked tags automatically
3. Confirm only targets `is_suggestion=true` — cannot "re-confirm" already confirmed tags
4. Reject only targets `is_suggestion=true` — cannot accidentally reject confirmed tags
5. Lock preserves source and confidence (only changes is_locked and is_suggestion)

## Known Limitations

1. **Reject is not persistent** — No `rejected_decisions` table. Re-running AI tagging may regenerate rejected suggestions.
2. **No suggestion search syntax** — Cannot search `suggestion:tag_name`. Planned for future.
3. **No undo** — Rejecting a suggestion deletes it permanently.
4. **Auto-tag after import** — Available since Phase 2.3 (disabled by default). See [AI Tagging Jobs](ai-tagging-jobs.md).
5. **Media detail review buttons** — Suggestions are shown visually but confirm/reject must be done in Admin panel or API.

## Auto-Tagging After Import (Phase 2.3)

Phase 2.3 added optional auto-tagging after import:

1. Review UI (Phase 2.2) is available for correcting incorrect auto-tags
2. Auto-tags are written as suggestions by default (`force_suggestions`), requiring review
3. Disabled by default; enable via `AI_AUTO_TAG_AFTER_IMPORT=true` in `.env`
4. See [AI Tagging Jobs](ai-tagging-jobs.md) for full documentation

## Files Added/Modified

| File | Change |
|------|--------|
| `backend/app/routes/admin/ai_tag_review.py` | **New** — Review API endpoints |
| `backend/app/routes/admin/__init__.py` | Register review router |
| `backend/app/services/tag_service.py` | `confirm_suggestion` gains `preserve_source` option |
| `frontend/templates/admin.html` | AI Tag Review section in Content tab |
| `frontend/static/js/admin.js` | Review UI logic (load, filter, actions, bulk, pagination) |
| `frontend/static/js/media-viewer-base.js` | Provenance-aware tag rendering |
| `docs/ai-tag-review.md` | **New** — This document |
