# Current Handoff — AnimeLocalBooru

> Last updated after Phase 1.6 merge (2026-05-03).
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

### Phase 1.6 — Scan Job System / Progress / History

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

**Key files:**

| File | Role |
|------|------|
| `backend/app/models.py` | `ScanJob` model |
| `backend/app/database.py` | `migrate_add_scan_jobs_table` migration |
| `backend/app/utils/local_library_scanner.py` | Scanner with job runner, cancel, progress |
| `backend/app/routes/admin/media.py` | Job API endpoints + path safety |
| `backend/app/main.py` | Stale job recovery at startup |
| `frontend/templates/admin.html` | Job-based scan UI |
| `frontend/static/js/admin.js` | Polling, cancel, history JS |

## What Has NOT Been Built

- No AI tagging integration
- No anime/photo filtering
- No tag provenance (source, confidence, lock)
- No filesystem watcher or scheduled scan
- No HEIC or video import support
- No WebSocket (uses polling)
- No database migrations to existing tables

## Known Technical Debt

1. **`Media.source` reused for local path** — future `original_path` column may be needed
2. **Copy mode disk cost** — every imported image is duplicated on disk
3. **No scan progress percentage without max_files** — indeterminate progress only
4. **Polling-based progress** — 1.5s interval; WebSocket would be more efficient but adds complexity

## Recommended Next Phase: 2

**Tag Metadata Foundation** — extend the tag–media relationship to support AI-generated tags with provenance:

1. Extend `blombooru_media_tags` junction table with `source` (enum: `manual`, `ai_wd`, `booru_import`), `confidence` (float 0–1), `is_locked` (bool)
2. Database migration function following existing DIY pattern
3. Priority rule: `is_locked = true` or `source = manual` → AI never overwrites
4. Low-confidence tags stored as suggestions, not confirmed

**Important:** This is a database migration. Must be planned and reviewed first.

After Phase 2, proceed to Phase 2.1 (AI Auto Tagging using existing WDv3/SmilingWolf ONNX tagger).

## Test Directory

`C:\Users\kyloris\Pictures\AnimeLocalBooruTest` — 17 files (14 valid images already imported).

**Real target:** `C:\Users\kyloris\Pictures\iCloud Photos` — always use dry-run + max_files first.

## Key References

| Document | Purpose |
|----------|---------|
| `AGENTS.md` | Cursor agent instructions |
| `docs/project-roadmap.md` | Full phase plan |
| `docs/local-anime-library-devlog.md` | Per-phase technical log |
| `docs/local-library-scan.md` | Feature documentation and API usage |
| `example.env` | Available environment variables |
