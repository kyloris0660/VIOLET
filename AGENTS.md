# AGENTS.md

## Project context

This repository is **AnimeLocalBooru**, based on [Blombooru](https://github.com/mrblomblo/blombooru) — a self-hosted anime/illustration media tagging tool built with FastAPI + PostgreSQL + Jinja2/Tailwind.

The project goal is a personal, local anime image library with Danbooru-style tag-based retrieval. See `docs/project-roadmap.md` for the full phase plan and `docs/current-handoff.md` for the latest state.

### Architecture overview

- **Backend:** FastAPI (Python 3.10+), SQLAlchemy ORM, PostgreSQL 17
- **Frontend:** Jinja2 templates + Tailwind CSS + Vanilla JS (no SPA framework)
- **Entry point:** `python run.py --debug` (uses uvicorn with hot-reload)
- **Key config:** `.env` for DB credentials and `LOCAL_LIBRARY_PATHS`; `data/settings.json` for runtime settings (created after onboarding)

### Running the application (Windows)

```powershell
.\venv\Scripts\Activate.ps1
python run.py --debug          # listens on http://0.0.0.0:8000
```

First run shows onboarding page; subsequent runs load the gallery directly.

### Running the application (Linux / Cloud)

```bash
source venv/bin/activate       # or /workspace/venv/bin/activate
pg_ctlcluster 16 main start   # if PostgreSQL isn't running
python run.py --debug
```

### Authentication caveat

API endpoints that modify data require **both**:
- JWT token via `Authorization: Bearer <token>` header **or** `access_token` cookie
- `admin_mode=true` cookie (UX safety toggle, not a security gate)

Login endpoint: `POST /api/admin/login` with `{"username": "admin", "password": "admin123"}`.

### Database

- PostgreSQL database: `blombooru` on `localhost:5432`, user `postgres`
- No Alembic. Migrations are DIY `ALTER TABLE` functions in `backend/app/database.py` → `check_and_migrate_schema`.
- Before adding columns/tables, follow the existing pattern: write a `migrate_add_*` function that checks existence first.
- **Any database migration must be planned and reviewed before implementation.**

### Testing

No automated test suite. Verify changes manually:
- API: use curl / httpie / PowerShell `Invoke-RestMethod` against `http://localhost:8000/api/...`
- UI: open `http://localhost:8000` in browser
- Always test with a small directory first, never directly against iCloud Photos without dry-run

### Key code locations

| Module | Path | Notes |
|--------|------|-------|
| Models | `backend/app/models.py` | `Media`, `Tag`, `TagAlias`, `TagImplication`, `Album`, `User`, `ApiKey` |
| Media routes | `backend/app/routes/media.py` | Upload, search, serve files, `process_and_save_media()` |
| Admin routes | `backend/app/routes/admin/` | `media.py` (scan-media, scan-local-library), `tags.py`, `settings.py`, `onboarding.py` |
| Local library scanner | `backend/app/utils/local_library_scanner.py` | Phase 1: external directory scan + import |
| File scanner | `backend/app/utils/file_scanner.py` | Original Blombooru scan of `media/original` |
| Media processor | `backend/app/utils/media_processor.py` | Hash, MIME, dimensions, duration extraction |
| Thumbnail generator | `backend/app/utils/thumbnail_generator.py` | PIL image + OpenCV video thumbnails |
| Search parser | `backend/app/utils/search_parser.py` | Danbooru-style search syntax |
| Config | `backend/app/config.py` | Settings class, `.env` + `data/settings.json` loading |
| AI Tagger | `backend/app/services/wd_tagger.py` | WDv3 ONNX model (not yet integrated into scan flow) |
| Booru import | `backend/app/routes/booru_import.py` | Danbooru/Gelbooru URL import |
| Auth | `backend/app/auth.py` | JWT + admin_mode cookie |
| Frontend | `frontend/templates/`, `frontend/static/` | Jinja2 HTML, CSS (Tailwind), JS |

### Development workflow

See `docs/project-roadmap.md` § Development Standards. In short:

1. Branch from `main` → plan → implement → test → commit
2. Push → PR → squash merge → pull main
3. **Stop after merge.** Do not auto-start the next phase.

### Safety rules

**Never commit:** `.env`, `venv/`, `data/`, `media/`, `storage/`, test images, cache files, tokens, passwords

**Never do without approval:**
- Delete/move files in external directories
- Full-scan iCloud Photos without dry-run
- Database migrations without review
- Multi-phase feature bundles in one PR
