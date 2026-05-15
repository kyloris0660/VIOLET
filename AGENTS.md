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
- Test database: `blombooru_test` (created via `scripts/setup_test_db.py`). `VIOLET_ENV=test` requires a test DB name or `TEST_DATABASE_URL`.
- No Alembic. Migrations are DIY `ALTER TABLE` functions in `backend/app/database.py` → `check_and_migrate_schema`.
- Before adding columns/tables, follow the existing pattern: write a `migrate_add_*` function that checks existence first.
- **Any database migration must be planned and reviewed before implementation.**

### Testing

The project has a three-tier test infrastructure. See `docs/test-workflow.md` for full details.

**Standardized test environment** — load via PowerShell:

```powershell
. "$env:USERPROFILE\.violet\test-env.ps1"
```

**Tier 1 — Unit tests** (no external dependencies):

```powershell
& "$PY" -m pytest tests/test_env_safety.py tests/test_destructive_gate.py tests/test_scanner_icloud.py tests/test_content_classification.py tests/test_smoke_validation.py tests/test_server_identity.py tests/test_unified_llm.py tests/test_python_env_preflight.py tests/test_media_processor_mime_magic_cache.py tests/test_config_precedence.py -v
```

| Test file | Coverage |
|-----------|----------|
| `tests/test_env_safety.py` | VIOLET_ENV, STORAGE_ROOT, test DB fail-closed, assert_test_db |
| `tests/test_destructive_gate.py` | Destructive gate conditions, storage path containment |
| `tests/test_scanner_icloud.py` | Scanner iCloud safety, preflight, skip mapping |
| `tests/test_content_classification.py` | CLIP + heuristic classifiers |
| `tests/test_smoke_validation.py` | Full pipeline smoke validation |
| `tests/test_server_identity.py` | Server identity endpoint fields, Python runtime identity, no secrets exposed |
| `tests/test_unified_llm.py` | `complete_chat`/`complete_json` success, failure, fallback paths |
| `tests/test_python_env_preflight.py` | Python/venv identity preflight: sys.executable match, JSON output, code-root check, no backend imports |
| `tests/test_media_processor_mime_magic_cache.py` | python-magic availability caching, thread-local detectors, fallback chain, concurrent init safety |
| `tests/test_config_precedence.py` | Config precedence: process env beats `.env`, `TEST_DATABASE_URL` override, translation flag overrides, code defaults |

**Tier 2 — Fixture validation** (requires `VIOLET_TEST_FIXTURE_PATH`):

```powershell
& "$PY" -m pytest tests/test_fixture_validation.py -v
```

**Tier 3 — Playwright E2E** (requires `VIOLET_RUN_REAL_E2E=1` + running server):

```powershell
npx playwright test tests/e2e/ --project=edge
```

| Test file | Coverage |
|-----------|----------|
| `tests/e2e/config-diagnostics-e2e.spec.ts` | Config diagnostics API sections |
| `tests/e2e/gallery-browse.spec.ts` | Gallery grid, media detail, thumbnails |
| `tests/e2e/fixture-import.spec.ts` | Preflight, dry-run, import, idempotency |
| `tests/e2e/entity-alias-resolver.spec.ts` | Entity resolver API, trust policy, admin UI |

For manual verification beyond test suites:
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
| Dev Tools API | `backend/app/routes/admin/dev_tools.py` | Config diagnostics (incl. server info, scan config), E2E reset, 9-condition destructive gate |
| Env Safety Tests | `tests/test_env_safety.py` | 14 unit tests for VIOLET_ENV, STORAGE_ROOT separation, test DB fail-closed, assert_test_db |
| Test DB Setup | `scripts/setup_test_db.py` | Idempotent creation of `blombooru_test` database |
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
3. Use a real running local server. **Agents must start a controlled test server themselves** (see "Agent-started test servers" section). Do not ask the user to start the server unless startup fails for a concrete reason.
4. Use the actual app page, not only mocked DOM tests.
5. Verify the relevant user flow end-to-end.
6. If the feature touches local files, scan, thumbnails, or media display, validate with a real local test folder when safe.
7. If real browser validation cannot be run despite best effort, the agent must explicitly explain why (exact error) and provide the closest fallback validation. "Server was not running" is not an acceptable excuse — the agent should have started one.

**Playwright base URL variable:** `VIOLET_BASE_URL` (read by `playwright.config.ts`). Do not use `PLAYWRIGHT_BASE_URL`.

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

### Agent-started test servers for E2E validation

For **non-destructive** UI/E2E validation, agents **MAY and SHOULD** start a controlled local test server themselves. Do not ask the user to start the server unless startup fails for a concrete reason the agent cannot fix.

**Required conditions (all must be met):**

1. Use `VIOLET_ENV=test`.
2. Use `POSTGRES_DB=blombooru_test`.
3. Use dedicated test storage (`VIOLET_STORAGE_ROOT`), never development storage.
4. Load the user's test env script first: `. "$env:USERPROFILE\.violet\test-env.ps1"`
5. Choose a free port dynamically — do NOT default to any fixed port (e.g. 8011). Probe candidate ports (8012–8024) for availability before starting. Use `APP_PORT` env var (not `--port` CLI flag).
6. Record the server PID.
7. Start the server from the PR branch/worktree being tested.
8. Only stop the exact PID the agent started — never kill unknown processes.
9. Do not run import, AI tagging, LLM translation, cleanup, reset, delete, truncate, drop, or bulk-update operations.
10. Do not touch iCloud paths or modify VioletTestFixture.
11. If server startup fails, diagnose and report the exact error — do not skip E2E.
12. **Mandatory identity preflight (hard gate):** After the server starts, run `scripts/check_test_server_identity.py` to verify `VIOLET_ENV`, `POSTGRES_DB`, `code_root`, `git_sha`, `storage_root`, and `python_executable` match the current worktree/branch. Include `--expected-python "$PY"` to verify the server is running the approved venv Python (not the system Python). Always pass `--expected-storage-root` when a specific storage root is required (e.g. medium pilot). **E2E tests MUST NOT run until identity verification passes.** If the identity check fails, stop the server, diagnose, and restart. Never skip E2E due to identity check failure.

> **Windows venv shim note:** On Windows, `wmic` / `tasklist` may display the system Python path for venv-launched processes — this is a known Windows reporting artifact. The venv `python.exe` is a launcher shim; Windows records the underlying base interpreter in its process table. The server identity endpoint uses `sys.executable`, which correctly reports the venv path. Always use the `/api/system/server-identity` endpoint (via `check_test_server_identity.py --expected-python`) for Python identity verification, not OS-level process listings.

**Singleton server policy:** Only one agent-started test server may be running at a time per session. Before starting a new server, verify no previous agent-started server is still running. If a port conflict is detected, diagnose the conflict (PID, command line) — do not silently pick another port.

**Stale server prevention:** A "stale server" is one serving code from a different commit, branch, or worktree. Stale servers produce false test results. On Windows, killed processes may leave TCP sockets in LISTENING state for up to 60 seconds — verify the port is free before restarting. Never mark stale-server-induced E2E failures as "pre-existing" or "non-blocking."

**Cannot skip E2E due to port conflicts:** If all candidate ports are occupied, the agent must diagnose which processes hold them and report. E2E cannot be skipped with "port unavailable" as the excuse.

**Final report must include:** working directory, branch, server command, PID, port, `VIOLET_BASE_URL`, environment confirmation (VIOLET_ENV, DB, storage root), identity check result, E2E command, stop/cleanup result.

**Clarification:** "Do not kill arbitrary processes" means only stop the exact server PID you started. It does **not** mean agents cannot start a test server.

### Python/venv identity preflight (hard gate)

**All agent workflows** — server start, test execution, script execution, dependency installation — **MUST use the approved project venv Python.** This is a hard gate, not a suggestion.

**Determining `$PY` (the approved venv Python):**

The rule is: "use the repo-local venv Python." The exact path depends on the platform:

| Environment | `$PY` |
|-------------|-------|
| Windows (user local dev) | `<repo>\venv\Scripts\python.exe` |
| Linux / macOS / cloud | `<repo>/venv/bin/python` or `<repo>/.venv/bin/python` |
| Git worktree (no local venv) | Use the main repo's venv explicitly |

For the current Windows local dev setup:

```powershell
$PY = "C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe"
```

`scripts/check_python_env.py` can auto-infer the venv Python from the repo root when `--expected-python` is omitted. It probes `venv/Scripts/python.exe`, `.venv/Scripts/python.exe`, `venv/bin/python`, `.venv/bin/python` in order. You can also set `VIOLET_EXPECTED_PYTHON` as an env var override.

**Rules:**

1. **Never use the global/system Python** (`C:\Python313\python.exe`, `python.exe` from PATH, or any interpreter outside the project venv). This includes `pip install` — never install packages into the global Python.
2. **Preflight is mandatory.** Before any server start, test run, or script execution, run:

```powershell
& "$PY" scripts/check_python_env.py --expected-python "$PY"
```

The script must exit 0. If it exits 1, stop and diagnose — do not proceed.

3. **Worktrees do not have their own venv.** Always use the main repo venv explicitly:

```powershell
$PY = "C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe"
& "$PY" run.py --debug
```

4. **Test execution** must also use `$PY`:

```powershell
& "$PY" -m pytest tests/ -v
```

5. **Include `sys.executable` in all delivery reports** to prove the correct Python was used.

The server must run the PR branch/worktree code (i.e. CWD = worktree path).

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

### Destructive DB operation safety (post-incident policy, 2026-05-10)

**Context:** A worktree/database mismatch caused the `missing-media-cleanup` E2E test to wipe all 284 media records from the shared PostgreSQL database. The worktree's `BASE_DIR` did not contain the actual media files, so every record appeared "missing" and was deleted.

**Mandatory guardrails for all destructive endpoints:**

1. **Env flag gate:** Non-dry-run destructive operations (`reset-e2e-test-data`, `missing-media-cleanup`) require `VIOLET_ALLOW_DESTRUCTIVE_E2E=1` in the server environment. Without it, the API returns HTTP 403.
2. **Confirm phrase:** Each destructive endpoint requires a unique `confirm_phrase` string (e.g., `RESET_E2E_DATA`, `DELETE_ALL_MISSING_MEDIA`). This prevents accidental triggering by generic `confirm: true`.
3. **Dry-run default:** All cleanup/reset request models default to `dry_run=true`.
4. **Audit logging:** All destructive operations log `logger.warning(...)` with: operation name, item count, `BASE_DIR`, username.
5. **E2E test gating:** Any E2E test that calls a destructive endpoint with `dry_run: false` must be gated by `VIOLET_ALLOW_DESTRUCTIVE_E2E=1` via `test.skip()`.
6. **Worktree awareness:** Never run a dev server from a worktree against the shared DB for E2E tests — worktree `BASE_DIR` differs from main repo, making all media appear "missing."

**Agents must NOT:**
- Run destructive E2E tests without setting `VIOLET_ALLOW_DESTRUCTIVE_E2E=1`
- Add new destructive endpoints without all 4 guardrails (env flag, confirm phrase, dry-run default, audit log)
- Run the dev server from a worktree path when E2E tests will touch the shared DB

### AI-only phase isolation (post-incident policy, 2026-05-13)

**Context:** During Phase 3.2g.2, `AI_TAGGING_AUTO_LOCALIZATION=false` was set to prevent localization during AI-only tagging. However, the server-level background translation worker (`tag_translation_worker.py`) was not disabled and ran independently, adding 182 translations. The setting only gates `_schedule_localization()` inside AI tagging jobs — it does NOT disable the background worker.

**Required env vars for AI-only phases** (AI tagging without any localization/translation side effects):

```
AI_TAGGING_AUTO_LOCALIZATION=false        # disable AI-job-triggered localization
TAG_TRANSLATION_BACKGROUND_ENABLED=false  # disable background translation worker
TAG_TRANSLATION_AUTO_ENABLED=false        # disable auto-translate on tag creation
TAG_TRANSLATION_LLM_ENABLED=false         # disable LLM translation provider entirely
```

All four must be set. After server startup, verify the worker is stopped via `GET /api/admin/tag-localization/worker/status` — the response should show `running: false`. If any active or running translation jobs exist from prior phases, stop and report them before starting new AI tagging.

**Admin auth mutation rule:** Agent-initiated admin password resets (e.g. via `psql UPDATE`) require explicit user consent in the chat before execution. Silent resets are prohibited. Document any auth mutation in the delivery report.

**Reporting accuracy for tag deltas:** Reports involving AI tagging phases must separately state: `tags_added` (new Tag rows), `suggestions_added` (new AI suggestion rows), `media_tags` row delta (net change in media↔tag associations), `tag row delta` (net change in Tag table rows), and `media_with_ai_tags delta` (net change in media items with at least one AI-generated tag). If `media_tags` delta equals `tags_added + suggestions_added`, state so explicitly. Do not conflate these metrics or attribute the total `media_tags` delta solely to tag creation.
