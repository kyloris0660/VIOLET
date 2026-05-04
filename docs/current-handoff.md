# Current Handoff — AnimeLocalBooru

> Last updated after Phase 2.1 AI Auto Tagging merge (2026-05-04).
> Read this file at the start of any new Cursor conversation to resume development.

## Repository State

| Item | Value |
|------|-------|
| **Repo** | `kyloris0660/AnimeLocalBooru` |
| **Branch** | `main` |
| **Upstream** | Based on [Blombooru](https://github.com/mrblomblo/blombooru) |
| **Stack** | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + Vanilla JS |
| **Python** | 3.12 (venv at `./venv`) |
| **DB** | `blombooru` on `localhost:5432`, user `postgres` |
| **Dev server** | `.\venv\Scripts\Activate.ps1` → `python run.py --debug` → `http://localhost:8000` |
| **Admin credentials** | `admin` / `admin123` |

## What Has Been Built

### Phase 0 — Upstream Import & Verification (PR #1)

Blombooru source code imported. All core features verified.

### Phase 1 — Local Library Scan MVP (PR #2)

`POST /api/admin/scan-local-library` — synchronous scan + import of external directories.

### Phase 1.5 — Scan Safety & UX (PR #4)

Added `dry_run`, `max_files`, and Admin UI for the synchronous scan endpoint.

### Phase 1.6 — Scan Job System / Progress / History (PR #5)

Upgraded Local Library Scan from synchronous to background job system:

- **Job API**: `POST /jobs` (create), `GET /jobs` (list 20 recent), `GET /jobs/{id}` (poll), `POST /jobs/{id}/cancel`
- **Database**: New `blombooru_scan_jobs` table for persistent job history
- **Background execution**: Python daemon threads with independent DB sessions
- **Single job limit**: Only one scan at a time (409 if duplicate)
- **Cancel support**: Graceful stop with already-imported files kept
- **max_files**: Counts only candidate images, stops immediately at limit
- **Progress polling**: Frontend polls every 1.5s, shows real-time stats
- **Stale recovery**: Startup marks leftover running jobs as interrupted
- **Path safety**: Refuses to scan project-internal directories
- **Admin UI**: Progress bar, cancel button, scan history table, auto-resume polling
- **Backward compatible**: Legacy synchronous `POST /api/admin/scan-local-library` still works

### Phase 2 — Tag Metadata Foundation

Extended the `blombooru_media_tags` junction table with provenance tracking:

- **New columns**: `source`, `confidence`, `is_locked`, `is_suggestion`, `created_at`, `updated_at`
- **Tag service**: `backend/app/services/tag_service.py` — centralized helpers for all tag association operations
- **Priority rule**: Manual/locked tags are never overwritten by AI
- **Suggestion support**: Low-confidence AI tags (future) stored as suggestions, excluded from search
- **Backward compatible**: All existing features (upload, search, tag edit, scan jobs, booru import) work unchanged
- **Migration**: Idempotent `migrate_add_media_tags_provenance` — existing tags backfilled as `manual/1.0/locked/confirmed`
- **Media detail API**: Now includes `tag_provenance` dict with per-tag metadata

**Key files:**

| File | Role |
|------|------|
| `backend/app/models.py` | Extended `blombooru_media_tags` with provenance columns |
| `backend/app/database.py` | `migrate_add_media_tags_provenance` migration |
| `backend/app/services/tag_service.py` | Tag provenance service (add/update/remove/query) |
| `backend/app/routes/media.py` | Uses tag service for upload/update, exposes provenance in detail |
| `backend/app/routes/booru_import.py` | Uses tag service for booru imports |
| `backend/app/utils/search_parser.py` | Excludes suggestions from search/counts |
| `docs/tag-metadata-foundation.md` | Full technical documentation |

### Phase 2.1 — WDv3 AI Auto Tagging

Manually triggered AI tagging using the WDv3 ONNX tagger:

- **AI Tagging Service**: `backend/app/services/ai_tagging_service.py` — orchestrates WDv3 predictions → tag provenance writes
- **Admin API**: `GET /api/admin/ai-tagging/model-status`, `POST /api/admin/ai-tagging/media/{id}`, `POST /api/admin/ai-tagging/batch`
- **Admin UI**: Model status, single-image tagging, batch tagging with dry-run, results summary
- **Dual thresholds**: confirmed (>= confirm_threshold), suggestion (>= suggestion_threshold), ignored (< suggestion_threshold)
- **Category-aware thresholds**: Character tags require higher confidence (0.65) than general tags (0.35)
- **Safety**: Batch capped by `AI_TAGGING_BATCH_MAX_ITEMS`, dry-run mode, manual trigger only
- **Manual/locked priority**: AI never overwrites `is_locked=true` or `source='manual'` tags
- **Graceful degradation**: App starts normally when model is unavailable; API returns clear errors

**Key files:**

| File | Role |
|------|------|
| `backend/app/services/ai_tagging_service.py` | AI tagging orchestration |
| `backend/app/routes/admin/ai_tagging.py` | Admin API endpoints |
| `backend/app/config.py` | AI threshold/model configuration |
| `frontend/templates/admin.html` | AI Tagging section in Content tab |
| `frontend/static/js/admin.js` | AI tagging UI logic |
| `docs/ai-auto-tagging.md` | Full technical documentation |

## What Has NOT Been Built

- No automatic AI tagging during scan (manual trigger only)
- No AI tag review UI for suggestions (Phase 2.2)
- No anime/photo filtering (Phase 3)
- No filesystem watcher or scheduled scan (Phase 4)
- No suggestion search syntax (e.g. `suggestion:tag_name`)
- No HEIC or video import support
- No WebSocket (uses polling)

## Phase 2.0.1 — Review Findings Hotfix

Fixed six reliability issues from Codex automated review:

1. **Provenance index creation on fresh databases** — migration now ensures indexes exist regardless of whether columns were created by `create_all()` or `ALTER TABLE`
2. **History API stale job pollution** — `mark_stale_jobs()` removed from `GET /jobs` endpoint; only runs at startup
3. **Cancel race for pending jobs** — `request_cancel()` pre-sets the cancel flag; worker checks DB status before starting
4. **max_files full directory traversal** — replaced `list(rglob)` with generator; stops immediately at limit across all directories
5. **Empty paths env fallback** — `{"paths": []}` now returns 400 instead of silently falling back to env
6. **Invalid root failed count** — non-existent/non-directory paths now increment `failed` counter

## Known Technical Debt

1. **`Media.source` reused for local path** — future `original_path` column may be needed
2. **Copy mode disk cost** — every imported image is duplicated on disk
3. **No scan progress percentage without max_files** — indeterminate progress only
4. **Polling-based progress** — 1.5s interval; WebSocket would be more efficient but adds complexity
5. **`Media.tags` relationship uses SQLAlchemy secondary** — tag reads via relationship don't filter suggestions; AI tagging now creates suggestions so Phase 2.2 should add search filtering
6. **First AI model download requires internet** — ~350-1200 MB from HuggingFace Hub; no offline fallback

## Recommended Next Phase: 2.2

**AI Tag Review UI** — add suggestion review capabilities:

1. Inline confirm/reject buttons for suggestion tags in media detail
2. Suggestion search syntax (e.g. `suggestion:tag_name`)
3. Bulk suggestion management
4. Tag alias support for AI tag resolution

## Test Directory

`C:\Users\kyloris\Pictures\AnimeLocalBooruTest` — 17 files (14 valid images already imported).

**Real target:** `C:\Users\kyloris\Pictures\iCloud Photos` — always use dry-run + max_files first.

## Key References

| Document | Purpose |
|----------|---------|
| `AGENTS.md` | Cursor agent instructions |
| `docs/project-roadmap.md` | Full phase plan |
| `docs/tag-metadata-foundation.md` | Phase 2 technical documentation |
| `docs/ai-auto-tagging.md` | Phase 2.1 AI tagging documentation |
| `docs/local-anime-library-devlog.md` | Per-phase technical log |
| `docs/local-library-scan.md` | Feature documentation and API usage |
| `example.env` | Available environment variables |
