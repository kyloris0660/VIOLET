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
| Models | `backend/app/models.py` | `Media`, `Tag`, `TagAlias`, `TagImplication`, `Album`, `User`, `ApiKey`, `TagTranslation`, `ScanJob` (with iCloud stats) |
| Media routes | `backend/app/routes/media.py` | Upload, search, serve files, `process_and_save_media()` |
| Admin routes | `backend/app/routes/admin/` | `media.py` (scan-media, scan-local-library, preflight), `tags.py`, `settings.py`, `onboarding.py` |
| Local library scanner | `backend/app/utils/local_library_scanner.py` | Phase 1: external directory scan + import; Phase 2.4: preflight, iCloud detection, timeout, extended stats |
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
| Dev Tools API | `backend/app/routes/admin/dev_tools.py` | Config diagnostics (incl. server info, scan config), E2E reset |
| E2E Reset Service | `backend/app/services/e2e_reset_service.py` | Test data reset logic |
| Entity Alias Resolver | `backend/app/services/entity_alias_resolver.py` | Proper-noun alias resolution (character/copyright/artist) |
| Frontend | `frontend/templates/`, `frontend/static/` | Jinja2 HTML, CSS (Tailwind), JS |

### Phase plan approval rule

For every new major development phase or substantial feature scope, the agent must:

1. **Produce an implementation plan first** — covering scope, key design decisions, new files/tables, and testing approach.
2. **Wait for explicit user approval** before making substantial code changes.
3. Bug fixes, small review-comment fixes, and documentation updates may proceed without a separate plan.
4. Major stage-level design changes (new classifiers, new models, new DB schemas, evaluation frameworks) require user-approved plan.

This rule is permanent and applies to all future phases.

### Development workflow

See `docs/project-roadmap.md` § Development Standards. In short:

1. Branch from `main` → plan → implement → test → commit
2. Push → PR → squash merge → pull main
3. **Stop after merge.** Do not auto-start the next phase.

### GitHub PR / main protection

Agents may:
- Create feature branches, commit changes, push feature branches
- Create GitHub PRs, update existing PR branches
- Run tests, prepare local validation servers

Agents must NOT:
- Merge PRs (user manually reviews and merges on GitHub)
- Push directly to `main`
- Force-push `main`
- Delete `main`
- Treat local commits as PRs
- Claim a PR exists without a real GitHub PR URL (e.g. from `gh pr view` or `gh pr create`)
- Claim a PR is merged without GitHub or `origin/main` verification

Additional PR rules:
1. A local commit is not the same as a PR. A commit message containing `(#N)` is not proof that PR #N exists.
2. Before starting a new phase, verify: current branch, `git status`, `origin/main` latest commit, previous phase is actually merged into `origin/main`.
3. Do not mix multiple phases in one branch or one PR.
4. The final delivery report must include the real GitHub PR URL.

**Recommended**: Enable GitHub Branch Protection / Rulesets on `main` to enforce PR-based merges and prevent accidental direct pushes. See GitHub docs for setup.

### Real browser validation (mandatory)

For every feature phase, bug fix, or UI-affecting change, the agent must perform real browser validation before delivery. This applies to changes involving: Admin UI, gallery/media grid, media detail page, search behavior, tag localization, AI tagging/review UI, local library scan workflow, settings/developer tools, user-visible text, thumbnails/fallback images, routing/navigation, any frontend JavaScript behavior.

**Required standard:**

1. Prefer Playwright with system Edge on Windows.
2. Do not rely only on API tests or unit tests when UI behavior is affected.
3. Use a real running local server.
4. Use the actual app page, not only mocked DOM tests.
5. Verify the relevant user flow end-to-end.
6. If the feature touches local files, scan, thumbnails, or media display, validate with a real local test folder when safe.
7. If real browser validation cannot be run, the agent must explicitly explain why and provide the closest fallback validation.

The delivery report must include a dedicated section: **真实浏览器验收**, containing: 验收方式, 浏览器/Playwright project, URL tested, pages/flows validated, pass/fail result, skipped or not covered items, fallback explanation if real browser validation could not be completed. A phase is not considered complete without this section.

### Chinese reporting rule

Final user-facing stage summaries and delivery reports must be written in Chinese (zh-CN). This includes: 阶段性总结, 交付报告, 测试结果总结, 风险说明, 本地验收步骤, 已知限制, 下一步建议.

Keep technical identifiers in English: file paths, branch names, PR URLs, API routes, config keys, class/function names, commands, commit messages, PR titles. Code comments may remain English when appropriate.

### Test report accuracy

- Do not claim "all tests passed" if any test failed.
- If some tests are skipped, gated, unavailable, or unrelated, the report must say so clearly.
- The final delivery report must include exact commands and exact results.
- If a failing test is pre-existing or unrelated, the agent must either: (1) fix it; (2) gate/skip it intentionally with a clear reason; or (3) document it as non-blocking with evidence.

### Service / dev environment safety

- Never kill arbitrary Python or Node processes.
- Only stop clearly identified V.I.O.L.E.T. / AnimeLocalBooru dev server processes.
- Report PID, command line, and port before stopping.
- Prefer diagnostics-first UI.
- If adding stop/restart UI, restrict it to local debug mode only.
- Do not expose dangerous controls in production mode.

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
| Delivery reports / stage summaries | Chinese (zh-CN) |
| Optional user-facing Chinese docs | Separate supplements (e.g. `docs/tag-localization-zh.md`) |

### Safety rules

**Never commit:** `.env`, `venv/`, `data/`, `media/`, `storage/`, test images, cache files, tokens, passwords

**Never do without approval:**
- Delete/move files in external directories
- Full-scan iCloud Photos without dry-run
- Database migrations without review
- Multi-phase feature bundles in one PR
