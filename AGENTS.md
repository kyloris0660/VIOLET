# AGENTS.md

## Cursor Cloud specific instructions

This repository is **AnimeLocalBooru**, based on [Blombooru](https://github.com/mrblomblo/blombooru) — a self-hosted media tagging tool built with FastAPI + PostgreSQL + Jinja2/Tailwind.

### Architecture overview

- **Backend:** FastAPI (Python 3.10+), SQLAlchemy ORM, PostgreSQL 16/17
- **Frontend:** Jinja2 templates + Tailwind CSS + Vanilla JS (no SPA framework)
- **Entry point:** `python run.py --debug` (uses uvicorn with hot-reload)
- **Key config:** `.env` for DB credentials; `data/settings.json` for runtime settings (created after onboarding)

### Running the application

1. Ensure PostgreSQL is running: `pg_ctlcluster 16 main start`
2. Activate venv: `source /workspace/venv/bin/activate`
3. Start dev server: `python run.py --debug` (listens on `0.0.0.0:8000`)
4. First run shows onboarding page; subsequent runs load the gallery directly

### Authentication caveat

API endpoints that modify data require **both**:
- JWT token via `Authorization: Bearer <token>` header **or** `access_token` cookie
- `admin_mode=true` cookie (UX safety toggle, not a security gate)

Login endpoint: `POST /api/admin/login` with `{"username": "admin", "password": "admin123"}`.

### Database

- PostgreSQL database: `blombooru` on `localhost:5432`, user `postgres`, password `devpassword`
- No Alembic. Migrations are DIY `ALTER TABLE` functions in `backend/app/database.py` → `check_and_migrate_schema`.
- Before adding columns/tables, follow the existing pattern: write a `migrate_add_*` function that checks existence first.

### Testing

No automated test suite exists in the upstream project. Verify changes manually:
- API: use curl or httpie against `http://localhost:8000/api/...`
- UI: open `http://localhost:8000` in browser

### Key code locations

See `docs/local-anime-library-devlog.md` for the full code map. Quick reference:
- Models: `backend/app/models.py`
- Routes: `backend/app/routes/` (media, tags, search, admin/*, ai_tagger, booru_import)
- Services: `backend/app/services/` (wd_tagger, booru clients)
- Config: `backend/app/config.py`
- Search parser: `backend/app/utils/search_parser.py`
- File scanner: `backend/app/utils/file_scanner.py`
- Thumbnail generator: `backend/app/utils/thumbnail_generator.py`
