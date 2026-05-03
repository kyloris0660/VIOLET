# Current Handoff — AnimeLocalBooru

> Last updated after Phase 1.5 merge (2026-05-03).
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

Blombooru source code imported. All core features verified: upload, tags, search, thumbnails, scan-media, admin panel, onboarding.

### Phase 1 — Local Library Scan MVP (PR #2)

New endpoint `POST /api/admin/scan-local-library` that:

- Recursively scans external directories for `.jpg/.jpeg/.png/.webp/.gif`
- Copies files into `media/original/` (originals never touched)
- Deduplicates by MD5 hash
- Records original path in `Media.source` as `file://` URI
- Isolates per-file errors; returns detailed statistics
- Configured via `LOCAL_LIBRARY_PATHS` env var (pipe-separated) or JSON body

### Phase 1.5 — Scan Safety & UX

Enhanced `POST /api/admin/scan-local-library` with:

- **dry_run mode**: scan and report without copying files or writing to DB
- **max_files limit**: cap the number of candidate files processed per scan
- **Admin UI**: Local Library Scan section in Admin → Content tab with path input, dry-run toggle, max_files limit, scan results summary, and failed files table
- **Backward compatible**: existing API calls without `dry_run`/`max_files` behave identically to Phase 1

**Key files modified:**

| File | Role |
|------|------|
| `backend/app/utils/local_library_scanner.py` | dry_run + max_files in scan logic |
| `backend/app/routes/admin/media.py` | API accepts dry_run, max_files params |
| `frontend/templates/admin.html` | Local Library Scan UI section |
| `frontend/static/js/admin.js` | scanLocalLibrary() JS handler |
| `docs/local-library-scan.md` | Updated feature documentation |

## What Has NOT Been Built

- No AI tagging integration
- No anime/photo filtering
- No tag provenance (source, confidence, lock)
- No filesystem watcher or scheduled scan
- No HEIC or video import support
- No scan progress bar / real-time feedback
- No scan history / audit log
- No database migrations beyond upstream Blombooru schema

## Known Technical Debt

1. **`Media.source` reused for local path** — `file://` URI in the `source` field works but is not a dedicated column. A future `original_path` column + migration may be needed for sync/audit workflows.
2. **Synchronous scan** — `scan_and_import` runs in a threadpool but is blocking within that thread. Large directories (10k+ files) will produce a long HTTP response time. Future: background task with progress polling.
3. **No progress reporting** — The caller gets no feedback until the scan finishes.
4. **Copy mode disk cost** — Every imported image is duplicated on disk.

## Recommended Next Phase: 2

**Tag Metadata Foundation** — extend the tag–media relationship to support AI-generated tags with provenance:

1. Extend `blombooru_media_tags` junction table with `source` (enum: `manual`, `ai_wd`, `booru_import`), `confidence` (float 0–1), `is_locked` (bool)
2. Database migration function following existing DIY pattern
3. Priority rule: `is_locked = true` or `source = manual` → AI never overwrites
4. Low-confidence tags stored as suggestions, not confirmed

**Important:** This is a database migration. Must be planned, reviewed, and tested on a copy of the DB before merging.

After Phase 2, proceed to Phase 2.1 (AI Auto Tagging using existing WDv3/SmilingWolf ONNX tagger).

See `docs/project-roadmap.md` for the full phase plan.

## Test Directory

A test directory exists at `C:\Users\kyloris\Pictures\AnimeLocalBooruTest` with 17 files (14 valid images + 1 duplicate + 2 unsupported + 1 zero-byte). The 14 images are already imported into the local database.

The real target directory is `C:\Users\kyloris\Pictures\iCloud Photos` — use dry-run mode before any real import.

## Key References

| Document | Purpose |
|----------|---------|
| `AGENTS.md` | Cursor agent instructions (architecture, running, auth, DB, code map) |
| `docs/project-roadmap.md` | Full phase plan and development standards |
| `docs/local-anime-library-devlog.md` | Detailed per-phase technical log |
| `docs/local-library-scan.md` | Phase 1 + 1.5 feature documentation and API usage |
| `example.env` | All available environment variables |
