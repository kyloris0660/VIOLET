# Current Handoff — AnimeLocalBooru

> Last updated after Phase 1 merge (2026-05-03).
> Read this file at the start of any new Cursor conversation to resume development.

## Repository State

| Item | Value |
|------|-------|
| **Repo** | `kyloris0660/AnimeLocalBooru` |
| **Branch** | `main` |
| **Latest commit** | `46dca33 feat: add local library scan MVP (#2)` |
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

**Key files added/modified:**

| File | Role |
|------|------|
| `backend/app/utils/local_library_scanner.py` | Core scan + import logic |
| `backend/app/routes/admin/media.py` | `scan-local-library` endpoint |
| `backend/app/config.py` | `LOCAL_LIBRARY_PATHS` property |
| `docs/local-library-scan.md` | Feature documentation |

## What Has NOT Been Built

- No dry-run mode or max_files limit
- No Admin UI for scan (API-only)
- No AI tagging integration
- No anime/photo filtering
- No tag provenance (source, confidence, lock)
- No filesystem watcher or scheduled scan
- No HEIC or video import support
- No database migrations beyond upstream Blombooru schema

## Known Technical Debt

1. **`Media.source` reused for local path** — `file://` URI in the `source` field works but is not a dedicated column. A future `original_path` column + migration may be needed for sync/audit workflows.
2. **Synchronous scan** — `scan_and_import` runs in a threadpool but is blocking within that thread. Large directories (10k+ files) will produce a long HTTP response time. Future: background task with progress polling.
3. **No progress reporting** — The caller gets no feedback until the scan finishes.
4. **Copy mode disk cost** — Every imported image is duplicated on disk.

## Recommended Next Phase: 1.5

**Scan Safety & UX** — before pointing at the real iCloud Photos directory:

1. `dry_run` parameter: scan and report without importing
2. `max_files` parameter: cap how many files to process
3. Admin UI button to trigger scan and display results
4. Show `failed_files` details in the admin panel

See `docs/project-roadmap.md` for the full phase plan.

## Test Directory

A test directory exists at `C:\Users\kyloris\Pictures\AnimeLocalBooruTest` with 17 files (14 valid images + 1 duplicate + 2 unsupported + 1 zero-byte). This was used for Phase 1 verification. The 14 images are already imported into the local database.

The real target directory is `C:\Users\kyloris\Pictures\iCloud Photos` — do **not** scan it without dry-run support.

## Key References

| Document | Purpose |
|----------|---------|
| `AGENTS.md` | Cursor agent instructions (architecture, running, auth, DB, code map) |
| `docs/project-roadmap.md` | Full phase plan and development standards |
| `docs/local-anime-library-devlog.md` | Detailed per-phase technical log |
| `docs/local-library-scan.md` | Phase 1 feature documentation and API usage |
| `example.env` | All available environment variables |
