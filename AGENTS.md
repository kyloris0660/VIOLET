# AGENTS.md

## Project context

This repository is **V.I.O.L.E.T.** (Visual Image Organizer for Local Evaluation & Tagging), based on [Blombooru](https://github.com/mrblomblo/blombooru) — a self-hosted anime/illustration media tagging tool built with FastAPI + PostgreSQL + Jinja2/Tailwind.

The project goal is a personal, local anime image library with Danbooru-style tag-based retrieval and Chinese localization. See `docs/project-roadmap.md` for the full phase plan and `docs/current-handoff.md` for the latest state.

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
| Models | `backend/app/models.py` | `Media`, `Tag`, `TagAlias`, `TagImplication`, `Album`, `User`, `ApiKey`, `TagTranslation` |
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
| Tag Localization | `frontend/static/data/tag_translations_zh.json`, `frontend/static/js/tag-localization.js` | Chinese display names for tags |
| Tag Localization Service | `backend/app/services/tag_localization_service.py` | DB-backed translation management, seeding, batch/auto translate |
| LLM Translation Provider | `backend/app/services/llm_translation_provider.py` | Abstract LLM provider (OpenAI-compatible, disabled) |
| Tag Localization Admin | `backend/app/routes/admin/tag_localization.py` | Admin API for translation CRUD + batch LLM + worker control |
| Tag Translation Worker | `backend/app/services/tag_translation_worker.py` | Background continuous tag translation worker |
| AI Tagging Jobs | `backend/app/services/ai_tagging_job_service.py` | Background AI tagging job worker |
| AI Tagging Jobs API | `backend/app/routes/admin/ai_tagging_jobs.py` | AI job CRUD + cancel endpoints |
| Dev Tools API | `backend/app/routes/admin/dev_tools.py` | Config diagnostics, E2E reset |
| E2E Reset Service | `backend/app/services/e2e_reset_service.py` | Test data reset logic |
| Entity Alias Resolver | `backend/app/services/entity_alias_resolver.py` | Proper-noun alias resolution (character/copyright/artist) |
| Frontend | `frontend/templates/`, `frontend/static/` | Jinja2 HTML, CSS (Tailwind), JS |

### Development workflow

See `docs/project-roadmap.md` § Development Standards. In short:

1. Branch from `main` → plan → implement → test → commit
2. Push → PR → squash merge → pull main
3. **Stop after merge.** Do not auto-start the next phase.

### Git and PR verification rules

1. **Never claim a PR exists** unless you have an actual GitHub PR URL (e.g. from `gh pr view` or `gh pr create` output).
2. **Never claim a PR is merged** unless `origin/main` contains the merged commit AND `gh pr view` confirms the merged state.
3. A local commit is not the same as a PR. A commit message containing `(#N)` is not proof that PR #N exists.
4. Before starting a new phase, verify: current branch, `git status`, `origin/main` latest commit, previous phase is actually merged into `origin/main`.
5. Do not mix multiple phases in one branch or one PR.
6. The final delivery report must include the real GitHub PR URL.

### Windows local development notes

- **GitHub CLI** is installed at `C:\Program Files\GitHub CLI\gh.exe`. If `gh` is not found in Cursor's shell PATH, use the absolute path or run `$env:Path += ";C:\Program Files\GitHub CLI"` in the current session.
- Do not say "gh is unavailable" unless both `where gh` and `& "C:\Program Files\GitHub CLI\gh.exe" --version` fail.
- If `gh auth status` fails, stop and ask the user for manual login before proceeding.

### Language policy

| Layer | Language |
|-------|----------|
| User interface | zh-CN first (English fallback) |
| Internal code, API paths, config keys, canonical tags, DB fields | English |
| Core technical docs (AGENTS.md, handoff, roadmap, API docs) | English primary |
| Optional user-facing Chinese docs | Separate supplements (e.g. `docs/tag-localization-zh.md`) |

### Safety rules

**Never commit:** `.env`, `venv/`, `data/`, `media/`, `storage/`, test images, cache files, tokens, passwords

**Never do without approval:**
- Delete/move files in external directories
- Full-scan iCloud Photos without dry-run
- Database migrations without review
- Multi-phase feature bundles in one PR
