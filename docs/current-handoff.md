# Current Handoff - V.I.O.L.E.T.

> Last updated during Phase 3.8d-I4a - Controlled cleanup executor support (2026-05-21).
> Read this file at the start of any new conversation to resume development.

## Repository State

| Item | Value |
|------|-------|
| **Repo** | `kyloris0660/AnimeLocalBooru` (project name: V.I.O.L.E.T.) |
| **Branch** | `phase3.8d-i3-recovery-cleanup-hydration-plan` (recovery cleanup dry-run and hydration/backfill policy; Phase 3.8d execute remains blocked) |
| **Upstream** | Based on [Blombooru](https://github.com/mrblomblo/blombooru) |
| **Stack** | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + Vanilla JS |
| **Python** | 3.12 (venv at `./venv`) |
| **DB (dev)** | `blombooru` on `localhost:5432`, user `postgres` |
| **DB (test)** | `blombooru_test` on `localhost:5432` — created via `scripts/setup_test_db.py` |
| **Dev server** | `.\venv\Scripts\Activate.ps1` → `python run.py --debug` → `http://localhost:8000` |
| **Test server** | `. "$env:USERPROFILE\.violet\test-env.ps1"` → `& "$PY" run.py --debug` → `http://localhost:<APP_PORT>` |
| **Admin credentials** | `admin` / `admin123` |
| **Phase 3.1 status** | PR [#25](https://github.com/kyloris0660/AnimeLocalBooru/pull/25) merged |
| **Phase 3.1.1a status** | PR [#26](https://github.com/kyloris0660/AnimeLocalBooru/pull/26) merged |
| **Phase 3.1.1b status** | PR [#28](https://github.com/kyloris0660/AnimeLocalBooru/pull/28) merged |
| **Phase 3.1.1c (PR #29)** | PR [#29](https://github.com/kyloris0660/AnimeLocalBooru/pull/29) merged — full pipeline smoke validation |
| **Unicode fix (PR #30)** | PR [#30](https://github.com/kyloris0660/AnimeLocalBooru/pull/30) merged — harden Unicode scan import failure handling |
| **Phase 3.1.2a (PR #31)** | PR [#31](https://github.com/kyloris0660/AnimeLocalBooru/pull/31) merged — Admin UI closeout |
| **Phase 3.1.2b (PR #32)** | PR [#32](https://github.com/kyloris0660/AnimeLocalBooru/pull/32) merged — Gallery content-class filter |
| **Phase 3.1.2c (PR #33)** | PR [#33](https://github.com/kyloris0660/AnimeLocalBooru/pull/33) merged — Server identity + unified LLM fallback + entity resolver hardening |
| **Phase 3.2b status** | PR [#34](https://github.com/kyloris0660/AnimeLocalBooru/pull/34) merged — Pilot hardening, configuration audit, medium-scale readiness |
| **Phase 3.2c status** | PR [#35](https://github.com/kyloris0660/AnimeLocalBooru/pull/35) merged — Medium-scale pilot preparation, env docs, LLM gate unification |
| **Python env hardening** | PR pending — Python/venv identity preflight hard gate (`scripts/check_python_env.py`), server runtime Python identity (`/api/system/server-identity` + `check_test_server_identity.py --expected-python`) |
| **Phase 3.3a.1 (PR #45)** | PR [#45](https://github.com/kyloris0660/AnimeLocalBooru/pull/45) merged — iCloud candidate manifest generation (5,326 rows: 522 existing + 478 new + 4,326 excluded) |
| **Phase 3.3b (PR #46)** | PR [#46](https://github.com/kyloris0660/AnimeLocalBooru/pull/46) merged — Tier-1000 staging copy executor (1,000 files, 2.98 GB to privacy-safe `tier1000_staging` label) |
| **Phase 3.4 (PR #48)** | PR [#48](https://github.com/kyloris0660/AnimeLocalBooru/pull/48) merged - Tier-1000 pre-import audit, 1,000/1,000 PASS |
| **Phase 3.5 (PR #49)** | PR [#49](https://github.com/kyloris0660/AnimeLocalBooru/pull/49) merged - Tier-1000 DB import tooling executed: 995 imported, 5 duplicate hashes skipped, post-import audit PASS |
| **Phase 3.6 (PR #50)** | PR [#50](https://github.com/kyloris0660/AnimeLocalBooru/pull/50) merged - controlled AI tagging + visual tag localization executed for the Phase 3.5 source label |
| **Phase 3.7 (PR #51)** | PR [#51](https://github.com/kyloris0660/AnimeLocalBooru/pull/51) merged - Tier-1000 content classification validation and tag-derived workflow scope gate |
| **Phase 3.8b (PR #53)** | PR [#53](https://github.com/kyloris0660/AnimeLocalBooru/pull/53) merged - reusable classification-first workflow helpers and dry-run CLI; execute workflow remains deferred |
| **Phase 3.8c (PR #54)** | PR #54 merged - medium +1000 candidate preflight with temporal stratified selection; dry-run only |
| **Phase 3.8d-I1 (PR #55)** | PR #55 merged - iCloud / Windows Cloud Files ingestion reliability incident hardening; Phase 3.8d execute blocked |
| **Phase 3.8d-I2 (PR #56)** | PR #56 merged - source ingestion gate unification; no execute/resume yet |
| **Phase 3.8d-I3 (PR #57)** | PR #57 merged - partial staging cleanup dry-run plus controlled hydration/read-probe and same-bucket backfill policy; no execute/resume yet |
| **Phase 3.8d-I4a (branch)** | `phase3.8d-i4a-cleanup-executor-support` - controlled partial staging cleanup executor support and tests; no real cleanup performed |

## Mandatory Workflow Rules

These rules are permanent and apply to all future phases. See `CLAUDE.md` and `AGENTS.md` for full details.

1. **GitHub PR / main protection** — Agents may NOT merge PRs, push to `main`, force-push `main`, or delete `main`. The user manually reviews and merges on GitHub.
2. **Real browser validation** — Every feature phase or UI-affecting change requires real browser validation (Playwright with system Edge preferred). Delivery reports must include a **真实浏览器验收** section.
3. **Chinese reporting** — Final delivery reports and stage summaries must be written in Chinese (zh-CN). Technical identifiers remain English.
4. **Test report accuracy** — Never claim "all tests passed" if any test failed. Report exact commands, exact results, and document any skipped/pre-existing failures.
5. **Service / dev environment safety** — Never kill arbitrary processes. Only stop clearly identified V.I.O.L.E.T. dev server processes with PID/port reported first.
6. **Branch protection recommendation** — Consider enabling GitHub Branch Protection / Rulesets on `main` to enforce PR-based merges.
7. **Phase plan approval** — For every new major development phase, the agent must first produce an implementation plan and wait for explicit user approval before making substantial code changes.
8. **Destructive DB operation safety** — All destructive API endpoints require `VIOLET_ALLOW_DESTRUCTIVE_E2E=1` env flag, unique `confirm_phrase`, `dry_run=true` default, and `logger.warning(...)` audit log. E2E tests calling destructive endpoints must be gated by the env flag. Never run a dev server from a worktree against the shared production DB for destructive E2E tests. See incident log below.
9. **Python/venv identity preflight (hard gate)** — All agent workflows (server start, test run, script execution, dependency install) MUST use the approved project venv Python (`$PY`). On Windows: `$PY = "<repo>\venv\Scripts\python.exe"`; on POSIX: `$PY = "<repo>/venv/bin/python"`. The script `scripts/check_python_env.py` auto-infers the venv when `--expected-python` is omitted (probes `venv/` and `.venv/`), or you can set `VIOLET_EXPECTED_PYTHON` env var. Run it as a mandatory preflight before any operation. For running servers, also use `scripts/check_test_server_identity.py --expected-python "$PY"` to verify the server process reports `sys.executable` matching the venv Python (not the system Python that Windows process tables may display). Never use the global/system Python. See `AGENTS.md` § Python/venv identity preflight.
10. **AI-only phase isolation** — When running AI-only phases (e.g. AI tagging without localization), all four translation env vars must be set: `AI_TAGGING_AUTO_LOCALIZATION=false`, `TAG_TRANSLATION_BACKGROUND_ENABLED=false`, `TAG_TRANSLATION_AUTO_ENABLED=false`, `TAG_TRANSLATION_LLM_ENABLED=false`. After server startup, verify the worker is stopped via `GET /api/admin/tag-localization/worker/status`. See `AGENTS.md` § AI-only phase isolation.
11. **Admin auth mutation rule** — Agent-initiated admin password resets (e.g. via `psql UPDATE`) require explicit user consent in the chat before execution. Silent resets are prohibited. Document any auth mutation in the delivery report.
12. **Reporting accuracy for tag deltas** — Reports involving AI tagging phases must separately state: `tags_added` (new Tag rows), `suggestions_added` (new AI suggestion rows), `media_tags` row delta (net change in media↔tag associations), `tag row delta` (net change in Tag table rows), and `media_with_ai_tags delta` (net change in media items that have at least one AI-generated tag). If `media_tags` delta equals `tags_added + suggestions_added`, state so explicitly. Do not conflate these metrics.

13. **Cloud availability gate for ingestion/staging/copy** - Any workflow that can read or copy from iCloud / Windows Cloud Files source paths must inspect cloud attributes through the Source Ingestion Gate before content reads. `stat()`, `exists()`, size, and `is_file()` are insufficient. High cloud-risk selected sets must not proceed directly to copy; manual hydrate is an emergency workaround only, structured cloud failure reasons are required, and DB import must not run after failed/incomplete staging. Upload-bytes and app-managed storage reads do not require the source cloud gate.

## Incident Log — 2026-05-10: Worktree/DB Mismatch Data Loss

**What happened:** `ux-hygiene-fix.spec.ts` test #10 called `POST /api/admin/dev/missing-media-cleanup` with `dry_run: false` during a normal Playwright E2E run. The dev server was running from a git worktree, so `settings.BASE_DIR` resolved to the worktree path (not the main repo). All 284 media files live in the main repo's `media/original/` — they don't exist at the worktree path. The cleanup endpoint checked `settings.BASE_DIR / m.path` for each media item, found them all "missing," and deleted all 284 `blombooru_media` rows. CASCADE propagated to `blombooru_media_tags` (0 rows) and `blombooru_scan_job_media` (0 rows).

**Evidence:** `blombooru_media` = 0, `blombooru_tags` = 1966 (survived, not cascade-dependent), `blombooru_scan_jobs` = 141 (survived), 284 original files + 283 thumbnails intact on disk.

**Root cause:** No env-flag gate or confirm-phrase on destructive endpoints; E2E test ran destructive cleanup in the default test suite without any guard.

**Remediation (all committed on `phase3.1-clip-anime-classifier`):**
- `backend/app/routes/admin/dev_tools.py`: Added `VIOLET_ALLOW_DESTRUCTIVE_E2E=1` env flag gate, unique `confirm_phrase` per endpoint, `logger.warning(...)` audit logging
- `tests/e2e/ux-hygiene-fix.spec.ts`: Gated test #10 with `VIOLET_ALLOW_DESTRUCTIVE_E2E` skip + added `confirm_phrase`
- `tests/e2e/violet-test100-real.spec.ts`: Gated reset test with `VIOLET_ALLOW_DESTRUCTIVE_E2E` skip + added `confirm_phrase`
- `frontend/static/js/admin.js`: Frontend destructive calls include `confirm_phrase`
- Safety policy added to `AGENTS.md`, `CLAUDE.md`, `docs/project-roadmap.md`, `docs/current-handoff.md`

## Language Policy

| Layer | Language |
|-------|----------|
| User interface | zh-CN first (English fallback) |
| Internal code / API / config / canonical tags | English |
| Core technical docs | English primary |
| Delivery reports / stage summaries | Chinese (zh-CN) |
| Optional user-facing Chinese docs | Separate supplements |

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

### Phase 2.1.2 — AI Tagging Session / Rollback Hotfix

Fixed two reliability issues in the AI tagging code:

1. **Independent DB sessions**: API endpoints no longer pass the request-scoped SQLAlchemy session into `run_in_threadpool`. Worker functions create, use, and close their own sessions via `SessionLocal`.
2. **Per-item batch rollback**: If a single `run_ai_tagging` call raises during batch processing, the session is rolled back before proceeding to the next item, preventing `PendingRollbackError` cascades.

### Phase 2.2 — AI Tag Review UI

Added suggestion review capabilities:

- **Review API**: `GET /api/admin/ai-tags/review`, `POST .../confirm`, `POST .../reject`, `POST .../lock`, `DELETE .../`, `POST .../bulk`
- **Admin UI**: Review panel with filters, table, single-action buttons, multi-select, bulk confirm/reject, pagination
- **Media Detail**: Provenance-aware tag rendering — distinguishes confirmed, locked, and suggestion tags visually
- **Confirm preserves AI provenance**: source=ai_wd and confidence retained after confirmation
- **Manual/locked protection**: Bulk/single delete refuses to remove manual+locked tags without force=true
- **Tag counts updated**: Confirming a suggestion updates `post_count` so it appears in search

**Key files:**

| File | Role |
|------|------|
| `backend/app/routes/admin/ai_tag_review.py` | Review API endpoints |
| `backend/app/services/tag_service.py` | Updated `confirm_suggestion` with `preserve_source` |
| `frontend/templates/admin.html` | AI Tag Review section |
| `frontend/static/js/admin.js` | Review UI logic |
| `frontend/static/js/media-viewer-base.js` | Provenance-aware tag display |
| `docs/ai-tag-review.md` | Full documentation |

### Phase 2.2.2 — Dynamic Tag Localization / LLM Translation Cache

Dynamic tag localization system with database-backed translations:

- **Translation table**: `blombooru_tag_translations` — persistent storage for all tag translations with source, status, confidence, and review tracking
- **Priority system**: manual/reviewed DB > static dictionary > LLM cache > canonical fallback
- **Static seeding**: App startup imports ~79 static JSON translations into DB
- **LLM integration**: Optional OpenAI-compatible LLM provider for batch translation (disabled by default)
- **Admin UI**: Tag localization section with stats, manual edit, batch LLM translate, review panel
- **Public API**: `GET /api/tags/translations/batch?names=...` for efficient frontend batching
- **Search enhancement**: DB-backed Chinese alias resolution with 5-minute cache, priority-based conflict resolution
- **Frontend batching**: `tag-localization.js` pre-fetches translations via batch API, falls back to static JSON

**Key files:**

| File | Role |
|------|------|
| `backend/app/models.py` | `TagTranslation` model |
| `backend/app/database.py` | `migrate_add_tag_translations_table` migration |
| `backend/app/services/tag_localization_service.py` | Core translation service |
| `backend/app/services/llm_translation_provider.py` | LLM provider abstraction |
| `backend/app/routes/admin/tag_localization.py` | Admin API endpoints |
| `backend/app/routes/tags.py` | Public batch translations endpoint |
| `backend/app/utils/search_parser.py` | DB-backed alias resolution |
| `frontend/static/js/tag-localization.js` | Frontend batch API integration |
| `backend/app/config.py` | `TAG_TRANSLATION_LLM_*` settings |
| `docs/tag-localization-llm.md` | Full LLM integration documentation |

### Phase 2.2.2a — Auto Tag Localization + Priority Hotfix

Fixed priority bug and added automatic translation:

- **Priority fix**: `upsert_translation` now enforces strict source priority — lower-priority sources (e.g., `llm`) cannot overwrite higher-priority sources (e.g., `static`, `manual`)
- **Auto-translate**: New tags automatically translated via LLM when `TAG_TRANSLATION_AUTO_ENABLED=true` (non-blocking background thread)
- **Enhanced LLM status**: API key configured (yes/no), auto-translate status, test LLM button
- **Admin UI**: Test LLM Translation button, Refresh Stats button, detailed LLM status display
- **Real LLM verified**: Successfully translated tags via OpenAI-compatible API
- **httpx dependency**: Added for async LLM API calls

### Phase 2.3 — AI Tagging Jobs + Auto-Tag After Import

Background AI tagging job system with optional auto-tag after import:

- **AI Tagging Job system**: Background jobs with progress tracking, cancel, and history — persisted in new `blombooru_ai_tag_jobs` table
- **Scan job media tracking**: New `blombooru_scan_job_media` table records which media IDs were imported per scan job
- **Auto-tag after import**: When `AI_AUTO_TAG_AFTER_IMPORT=true`, scan job completion automatically creates an AI tagging job for newly imported media (disabled by default)
- **Admin API**: `POST /api/admin/ai-tagging/jobs` (create), `GET /api/admin/ai-tagging/jobs` (list), `GET /api/admin/ai-tagging/jobs/{id}` (poll), `POST /api/admin/ai-tagging/jobs/{id}/cancel`, `GET /api/admin/ai-tagging/auto-config` (auto-tag config)
- **Admin UI**: AI Tagging Jobs section — create job, progress polling, cancel, job history, auto-tag config display
- **force_suggestions**: Option to write all AI tags as suggestions for manual review (regardless of confidence)
- **Tag localization integration**: AI tagging jobs schedule auto-translate for newly created tags
- **E2E validation**: `scripts/e2e_validate_violet_workflow.py` script + `docs/e2e-violet-test-100.md` guide for VioletTest100 workflow testing
- **Non-blocking**: Auto-tag job runs independently after scan; scan job is never delayed
- **Configuration**: `AI_AUTO_TAG_AFTER_IMPORT`, `AI_AUTO_TAG_AFTER_IMPORT_THRESHOLD`, `AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS`, `AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS` (all default OFF)

**Key files:**

| File | Role |
|------|------|
| `backend/app/services/ai_tagging_job_service.py` | AI tagging job background worker |
| `backend/app/routes/admin/ai_tagging_jobs.py` | AI tagging jobs API endpoints |
| `backend/app/models.py` | `AITagJob` and `ScanJobMedia` models |
| `backend/app/database.py` | `migrate_add_ai_tag_jobs_table`, `migrate_add_scan_job_media_table` |
| `frontend/templates/admin.html` | AI Tagging Jobs section in Content tab |
| `frontend/static/js/admin.js` | AI tagging jobs UI logic |
| `scripts/e2e_validate_violet_workflow.py` | E2E validation script |
| `docs/ai-tagging-jobs.md` | AI tagging jobs documentation |
| `docs/e2e-violet-test-100.md` | E2E validation guide |

### Phase 2.3a — Developer E2E Tools + Config Diagnostics

Config fix and developer tooling for E2E validation:

- **Config fix**: `load_dotenv()` now uses explicit project-root path with `override=False` (changed from `override=True` in Phase 3.2g.5)
- **Config diagnostics API**: `GET /api/admin/dev/config-diagnostics` returns all runtime config values (no secrets)
- **E2E reset API**: `POST /api/admin/dev/reset-e2e-test-data` with dry-run support
- **Developer Tools UI**: Admin → System → "Developer / E2E Tools" section
- **Reset CLI**: `scripts/reset_e2e_test_data.py`
- **Runtime override**: Not implemented — use `.env` + restart instead

#### Key files changed

| File | Change |
|------|--------|
| `backend/app/config.py` | `load_dotenv(override=False)` with explicit path (changed from `override=True` in Phase 3.2g.5) |
| `backend/app/routes/admin/dev_tools.py` | Config diagnostics, reset, recommended config APIs |
| `backend/app/services/e2e_reset_service.py` | Reset logic (summary + execute) |
| `frontend/templates/admin.html` | Developer Tools UI section |
| `frontend/static/js/admin.js` | Developer Tools frontend logic |
| `scripts/reset_e2e_test_data.py` | CLI reset script |

### Phase 2.3c — Full Real Browser E2E Acceptance

Full Playwright E2E test suite with real browser testing:

- 26 smoke tests + 5 real workflow tests covering scan → AI tag → localize → search
- Real browser testing via system Edge/Chrome (Chromium for CI)
- Fixed 8+ backend/frontend bugs found during E2E (API paths, auth, path validation, LIKE escaping)
- Dangerous path rejection verified for drive roots, `data/`, `media/original/`, and project root

### Phase 2.3d — Continuous Background Tag Translation

Background worker that automatically translates all missing tags via LLM:

- **Background worker**: `tag_translation_worker.py` — daemon thread, periodic check, batch LLM
- **Job history**: `blombooru_tag_translation_jobs` table — tracks every translation run
- **Admin API**: `worker/status`, `worker/run-now`, `worker/pause`, `worker/resume`, `worker/jobs`
- **Admin UI**: Background Auto Translation panel with status, controls, cost warning, job history
- **AI job integration**: Completed AI jobs trigger run-now on the worker
- **Stale recovery**: Startup marks leftover running jobs as interrupted
- **Configuration**: `TAG_TRANSLATION_BACKGROUND_*` settings (enabled, interval, batch_size, max_per_run, daily_limit, error_limit, priority)
- **Verified**: All 1844 tags translated to zh-CN (missing=0, failed=0)

**Key files:**

| File | Role |
|------|------|
| `backend/app/services/tag_translation_worker.py` | Background translation worker |
| `backend/app/models.py` | `TagTranslationJob` model |
| `backend/app/database.py` | `migrate_add_tag_translation_jobs_table` |
| `backend/app/routes/admin/tag_localization.py` | Worker API endpoints |
| `frontend/templates/admin.html` | Worker status panel |
| `frontend/static/js/admin.js` | Worker UI logic |
| `tests/e2e/tag-translation-worker.spec.ts` | Worker Playwright tests |

### Phase 2.3e — Proper Noun Alias Resolver Foundation

Separated proper-noun tag handling from visual tag translation:

- **Category policy**: Background worker now skips character/copyright/artist tags (`TAG_TRANSLATION_BG_CATEGORIES=general,meta`)
- **Entity alias resolver**: Dedicated LLM prompt for resolving established Chinese names (not inventing translations)
- **Trust policy**: Untrusted proper-noun LLM aliases (`needs_review=true`) excluded from Chinese search cache
- **Admin API**: `entity/status`, `entity/pending`, `entity/resolve` endpoints
- **Admin UI**: Separate "Entity Alias Resolver" section with status, pending list, resolve button
- **10 Playwright tests**: 7 smoke + 3 real E2E for entity resolution workflow

**Key files:**

| File | Role |
|------|------|
| `backend/app/services/entity_alias_resolver.py` | Entity alias resolver service + LLM prompt |
| `backend/app/routes/admin/tag_localization.py` | Entity API endpoints (status/pending/resolve) |
| `backend/app/utils/search_parser.py` | Trust policy for proper-noun aliases |
| `backend/app/services/tag_translation_worker.py` | Category filtering (skip proper-nouns) |
| `frontend/templates/admin.html` | Entity Alias Resolver UI section |
| `frontend/static/js/admin.js` | Entity resolver UI logic |
| `tests/e2e/entity-alias-resolver.spec.ts` | Playwright E2E tests |
| `docs/entity-alias-resolver.md` | Full documentation |

### Phase 2.4 — iCloud Large Library Readiness / Safe Ingestion

Made the scan pipeline safe for large iCloud-synced directories:

- **Preflight scan**: `POST /scan-local-library/preflight` — stat-only analysis, no `open()` calls, returns file counts, extension breakdown, estimated size, largest file
- **Hydrated-only mode**: Default ON, skips cloud-only files detected via Windows `GetFileAttributesW` (FILE_ATTRIBUTE_OFFLINE, RECALL_ON_DATA_ACCESS, RECALL_ON_OPEN)
- **Per-file timeout**: ThreadPoolExecutor wraps `calculate_file_hash` with configurable timeout (default 30s), prevents hanging on cloud files
- **Extended skip counters**: 6 new per-reason stat columns replace blanket `skipped_unsupported` — cloud_placeholder, zero_byte, timeout, unreadable, hidden, too_large
- **Max file size**: Configurable `SCAN_MAX_FILE_SIZE_MB` (default 200 MB)
- **Config diagnostics extended**: Server info (PID, Python version, app version, platform) and scan settings
- **Admin UI**: Preflight button, hydrated-only checkbox, iCloud safety note, 6 extended stat cards, preflight results (estimated size, largest file, extension breakdown)
- **22 unit tests + 7 Playwright E2E tests**

**Key files:**

| File | Role |
|------|------|
| `backend/app/config.py` | `SCAN_HYDRATED_ONLY_DEFAULT`, `SCAN_FILE_OPEN_TIMEOUT_SECONDS`, `SCAN_MAX_FILE_SIZE_MB` |
| `backend/app/models.py` | 8 new `ScanJob` columns (6 skip counters + hydrated_only + is_preflight) |
| `backend/app/database.py` | `migrate_add_scan_job_icloud_stats` migration |
| `backend/app/utils/local_library_scanner.py` | `_is_cloud_only`, `_is_hidden`, enhanced `_is_scannable_file`, `preflight_analyze`, timeout wrapping |
| `backend/app/routes/admin/media.py` | Preflight endpoint, hydrated_only param, extended serializer |
| `backend/app/routes/admin/dev_tools.py` | Server diagnostics section |
| `frontend/templates/admin.html` | Preflight UI, hydrated-only, extended stats, safety note |
| `frontend/static/js/admin.js` | `startPreflightJob()`, extended progress, server info rendering |
| `tests/test_scanner_icloud.py` | 22 unit tests (scanner, preflight, skip mapping) |
| `tests/e2e/test_icloud_scan.spec.ts` | 7 Playwright E2E tests |
| `docs/icloud-safe-ingestion.md` | Full documentation |

### Phase 3 — Content Classification Foundation + Evaluation Harness

> **⚠️ Scope clarification:** Phase 3 delivers the content classification **infrastructure and evaluation harness** only. The heuristic classifier has a 97.4% non-anime false positive rate and is **not suitable for production filtering or iCloud import gating**. A model-backed classifier (Phase 3.1) is required. All classification features default to OFF.

Content classification infrastructure using existing WD tagger output:

- **Content type schema**: `ContentClassEnum` with values: `anime`, `illustration`, `non_anime`, `unknown`
- **Media metadata**: 6 new columns — `content_class`, `content_class_confidence`, `content_class_source`, `content_class_model`, `content_class_locked`, `content_class_reviewed`
- **Classifier service**: Heuristic approach counting AI tags above confidence threshold — if count ≥ `ANIME_TAG_THRESHOLD` → anime, elif count > 0 → non_anime, else → unknown
- **Inline classification**: AI tagging jobs automatically classify media after tagging (when enabled)
- **Classification job system**: Background jobs with progress, cancel, history — reuses AI tagging job patterns
- **Auto-classify after scan**: Scan completion optionally triggers classification job (when enabled)
- **Admin UI**: Content Classification section — stats grid, breakdown, config panel, create job, progress, history
- **Search filter**: `class:anime`, `class:non_anime`, `class:illustration`, `class:unknown`, `class:none` (and negation `-class:...`)
- **Media detail**: Content class info row with localized label in media viewer
- **i18n**: Chinese and English localization for all content class labels
- **Startup recovery**: Stale classification jobs marked as interrupted on startup
- **Disabled by default**: All `CONTENT_CLASSIFICATION_*` settings default to off

**Key files:**

| File | Role |
|------|------|
| `backend/app/enums.py` | `ContentClassEnum` |
| `backend/app/models.py` | 6 `content_class_*` columns on `Media`, `ClassificationJob` model |
| `backend/app/database.py` | `migrate_add_content_classification` migration |
| `backend/app/config.py` | `CONTENT_CLASSIFICATION_*` settings (6 config properties) |
| `backend/app/schemas.py` | `content_class` in `MediaUpdate` and `MediaResponse` |
| `backend/app/services/content_classifier.py` | Heuristic classifier (`classify_media`, `classify_from_predictions`) |
| `backend/app/services/classification_job_service.py` | Classification job lifecycle |
| `backend/app/services/ai_tagging_job_service.py` | Inline classification after AI tagging |
| `backend/app/routes/admin/content_classification.py` | Admin API endpoints |
| `backend/app/utils/search_parser.py` | `class:` / `content_class:` meta filter |
| `backend/app/utils/local_library_scanner.py` | Auto-classify after scan hook |
| `backend/app/main.py` | Stale classification job recovery at startup |
| `frontend/templates/admin.html` | Content Classification admin section |
| `frontend/static/js/admin.js` | Classification UI logic |
| `frontend/static/js/media-viewer-base.js` | Content class info row + label helper |
| `frontend/static/locales/zh-cn.json` | Chinese content class labels |
| `frontend/static/locales/en.json` | English content class labels |
| `example.env` | Phase 3 configuration variables |

### Phase 3 — Evaluation Results

Real dataset evaluation using `scripts/evaluate_content_classification.py`:

| Dataset | Ground Truth | Total | Result | Metric |
|---------|-------------|-------|--------|--------|
| VioletTest100 | mixed | 145 | 100% anime | distribution only |
| VioletTest100_2 | anime | 81 | 100% anime recall | **PASS** (threshold ≥ 80%) |
| VioletPhase3Eval | non_anime | 39 | 97.4% FP rate | **FAIL** (threshold ≤ 15%) |

**Root cause of high FP rate**: The WD tagger generates many tags with confidence ≥ 0.5 for ANY image type (photos, screenshots, etc.), so the tag-count threshold of 5 is trivially exceeded. The heuristic classifier (count confirmed AI tags above threshold) cannot distinguish anime from non-anime effectively.

**Conclusion**: PR #23 delivers the **content classification foundation + evaluation harness** — the infrastructure (schema, job system, admin UI, search filters, inline classification) is solid, but the heuristic classifier needs a model-backed approach in Phase 3.1 to achieve acceptable non-anime rejection.

**Bug fixes included in PR #23**:
- Codex Issue A: Thread-safety — classification job `_active` flag race condition (commit `06e60f5`)
- Codex Issue B: Suggestion-policy — `classify_from_predictions` now filters `is_suggestion=False` (commit `06e60f5`)
- Auth cookie fix: evaluation script now uses `admin_token` cookie (not `access_token`)
- Test isolation fix: pytest config tests properly mock `dotenv.load_dotenv` to avoid `.env` pollution

### Phase 3.1 — CLIP Zero-Shot Anime/Non-Anime Classifier (PR #25)

Production-ready content classifier using CLIP ViT-B/32 zero-shot classification:

- **CLIP classifier**: `clip_classifier.py` — singleton ONNX inference, cosine similarity to pre-computed text centroids, margin-based unknown detection
- **Zero-shot approach**: Classifies images directly (no WD tags needed) by comparing CLIP image embeddings to text prompt centroids for anime, illustration, non_anime categories
- **Pre-computed text embeddings**: Generated offline by `scripts/generate_clip_text_embeddings.py`, stored in `backend/app/assets/content_classification/clip_text_embeddings.npz`
- **Prompt design**: Category prompts defined in `backend/app/assets/content_classification/clip_prompts.json`
- **Dual-method support**: `CONTENT_CLASSIFICATION_METHOD` switches between `clip` (default) and `heuristic` (legacy)
- **Standalone evaluation**: `scripts/evaluate_clip_content_classifier.py` (no database, no server required)
- **Gate criteria met**: Anime recall >= 80%, non-anime FP rate <= 15%
- **Optimal threshold**: `CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN=0.005` (tuned via threshold sweep)
- **Model**: Xenova/clip-vit-base-patch32 (MIT license), ~350 MB, auto-downloaded from HuggingFace Hub on first use
- **Thread-safe**: Singleton pattern with inference lock, safe for concurrent classification jobs

**Key files:**

| File | Role |
|------|------|
| `backend/app/services/clip_classifier.py` | CLIP ViT-B/32 ONNX zero-shot classifier |
| `backend/app/services/content_classifier.py` | Classifier dispatcher (CLIP + heuristic) |
| `backend/app/assets/content_classification/clip_prompts.json` | Text prompt definitions |
| `backend/app/assets/content_classification/clip_text_embeddings.npz` | Pre-computed text centroids |
| `backend/app/config.py` | `CONTENT_CLASSIFICATION_METHOD`, `CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN` |
| `scripts/evaluate_clip_content_classifier.py` | Standalone CLIP evaluation script |
| `scripts/generate_clip_text_embeddings.py` | Text centroid generator |
| `tests/test_content_classification.py` | Unit tests for CLIP + heuristic classifiers |

### Phase 3.1.2a — Admin UI Closeout (PR #31)

- Documentation closeout (6 files updated with PR #27-#30 info)
- Full admin UI audit (all sections, HTML→JS→backend tracing)
- AI tagging UI consolidation (old direct section moved to Developer Tools as legacy)
- Admin navigation/hierarchy improvements (section quick nav, collapsible groups)
- i18n fixes (~60 new locale keys, replace all hardcoded strings)
- Locale key consistency (en.json, zh-cn.json, ru.json, sv.json)
- Dark Violet theme, logo/favicon integration
- Real browser Edge E2E validation

### Phase 3.1.2b — Gallery Content-Class Filter (PR #32)

- Gallery sidebar content-class filter (5 modes: 全部, 动漫+未分类, 仅动漫, 仅非动漫, 仅未分类)
- Backend `content_class` param on `GET /api/media/`

### Phase 3.1.2c — Server Identity + Unified LLM Fallback + Entity Resolver Hardening (PR #33)

- `GET /api/system/server-identity` endpoint for dev server validation (git SHA, branch, PID, env, DB, Python runtime identity: `python_executable`, `python_version`, `python_prefix`, `python_base_prefix`, `is_venv`)
- `scripts/check_test_server_identity.py` verification script with port-owner diagnostics and `--expected-python` for venv Python path verification (with Windows path normalization)
- Unified LLM architecture: `BaseLLMProvider` with `complete_chat()` / `complete_json()` two-layer API
- Structured error hierarchy: `LLMProviderError` → `LLMTransportError`, `LLMHTTPStatusError`, `LLMResponseFormatError`, `LLMAllProvidersFailed`, `LLMBatchAggregateError`
- Fallback policy: transport errors + HTTP 408/429/5xx → fallback; HTTP 400/401/403/404 + invalid JSON → no fallback
- Entity alias resolver uses unified `provider.complete_json()` instead of direct `httpx.post`
- Entity alias resolver concurrency protection (`asyncio.Lock`, HTTP 409 on concurrent resolve)
- Structured error responses at route level (no raw traceback to client)
- Frontend entity resolve UX lifecycle: loading (yellow) → completed (green) / failed (red)

**Key files:**

| File | Role |
|------|------|
| `backend/app/routes/system.py` | Server identity endpoint |
| `scripts/check_test_server_identity.py` | Server identity verification script |
| `backend/app/services/llm_translation_provider.py` | Unified LLM provider with `complete_chat`/`complete_json`, error hierarchy, fallback |
| `backend/app/services/entity_alias_resolver.py` | Entity resolver using unified LLM path |
| `backend/app/routes/admin/tag_localization.py` | Structured error handling for entity resolve |
| `frontend/static/js/admin.js` | Entity resolve UX lifecycle |

### Phase 3.2b — Pilot Hardening, Configuration Audit, Medium-Scale Readiness

- E2E test hardening: replaced hardcoded `.toBe(200)` assertions with `expectPositiveInteger()` helper in `tag-localization.spec.ts` and `ai-tagging-jobs.spec.ts`. The helper validates typeof === 'number', Number.isFinite(), Number.isInteger(), >= 1 — no artificial upper bound. Intermediate `[1, 10000]` range was removed per Codex P2 review (backend config.py has no such cap).
- Fallback provider unit tests: `TestShouldFallbackDecision` + `TestFallbackProviderHTTPCodes` covering typed error classes, parametrized HTTP code coverage ({408,429,500,502,503,504} fallback, {400,401,403,404,405,422} no-fallback)
- Repository-wide configuration audit: `docs/config-audit-phase3.2b.md` with actionable classifications (keep_safety_gate, document_default, make_test_dynamic, leave_as_fixture, naming_inconsistency)
- Content classification attribution analysis: 4 trigger paths documented, inline classification design intent clarified
- Report path mismatch root cause: confirmed "batch_a/b/c" labels never existed in codebase — originated from external notes
- `.env.example` + `.env.production.example` expanded with all Phase 3.x config keys (AI tagging, LLM translation, fallback, entity alias, content classification)
- Env flag naming inconsistency documented: `VIOLET_RUN_REAL_LLM_TESTS` vs `VIOLET_RUN_REAL_LLM_E2E` — standardize to `_E2E` suffix in Phase 3.3+
- **Server lifecycle hardening (post stale-server incident):**
  - Mandatory identity preflight via `scripts/check_test_server_identity.py` — hard gate before any E2E
  - No default port (removed hardcoded 8011) — agents must probe 8012–8024 for availability
  - Singleton server policy — one agent-started server per session
  - Stale server prevention rules — cannot mark stale-server failures as non-blocking
  - `APP_PORT` env var (not `--port` CLI flag) documented across CLAUDE.md, AGENTS.md, docs/test-workflow.md
  - Root cause analysis in `docs/config-audit-phase3.2b.md` § 5

**Key files:**

| File | Role |
|------|------|
| `tests/e2e/tag-localization.spec.ts` | Fixed dynamic assertions |
| `tests/e2e/ai-tagging-jobs.spec.ts` | Fixed dynamic assertions |
| `tests/test_llm_translation_provider.py` | Fallback smoke unit tests |
| `docs/config-audit-phase3.2b.md` | Configuration audit report |
| `.env.example` | Updated config documentation |
| `.env.production.example` | Updated config documentation |

**Test results:**

| Suite | Result |
|-------|--------|
| Backend pytest | 442 passed, 10 skipped, 0 failures |
| Playwright E2E (Edge) | 126 passed, 18 skipped, 0 failures |

All gallery-content-filter tests (14/14) pass. Initial failures were caused by running E2E against a stale server on port 8011 — resolved by using a fresh server on port 8023 with identity verification via `scripts/check_test_server_identity.py`.

### Phase 3.2c — Medium-Scale Pilot Preparation

Closed deferred items from Phase 3.2b and designed the medium-scale pilot workflow:

- **`.env.example` coverage gap closed**: Added 18 missing Phase 3.x config keys to `.env.example`, `.env.production.example`, and `.env.test.example` — covering AI auto-tag extensions, background translation worker, content classification thresholds, scan parameters, storage paths, and E2E gating variables
- **`TEST_DATABASE_URL` documented**: Added to `.env.test.example` with safety warnings explaining forbidden DB name validation and override behavior
- **LLM E2E gate naming unified**: `tag-localization.spec.ts` now accepts both `VIOLET_RUN_REAL_LLM_E2E` (canonical) and `VIOLET_RUN_REAL_LLM_TESTS` (deprecated alias) via OR logic. Config audit doc §1.7 updated to "fixed"
- **`docs/test-workflow.md` updated**: Added `tag-localization.spec.ts` to Tier 3 table, documented LLM E2E gate variable and deprecated alias
- **Medium-scale pilot workflow designed**: `docs/medium-pilot-workflow.md` — 500→1000→2000 tier design with isolated `blombooru_test_medium` DB, dry-run-first policy, recommended processing limits, 12-item preflight checklist, rigorous pass/fail criteria (separate failure definitions for unsupported/duplicate/hidden vs errors, immediate-fail conditions for data loss/corruption/crash/security), rollback strategy with explicit destructive operation warnings, LLM cost control, and per-tier report template

**Key files:**

| File | Role |
|------|------|
| `.env.example` | Added 18 missing Phase 3.x config keys |
| `.env.production.example` | Synced with `.env.example` additions |
| `.env.test.example` | Added `TEST_DATABASE_URL` docs, E2E gating variables |
| `tests/e2e/tag-localization.spec.ts` | Unified LLM gate variable (OR logic) |
| `docs/medium-pilot-workflow.md` | Medium-scale pilot workflow design |
| `docs/config-audit-phase3.2b.md` | §1.7 naming inconsistency → fixed |
| `docs/test-workflow.md` | Added tag-localization spec, LLM gate docs |

### Phase 3.2f — Model / Proxy Runtime Hardening

Hardened CLIP model loading, proxy bypass, and preflight tooling:

- **Localhost proxy bypass**: `scripts/check_test_server_identity.py` now sets `session.trust_env = False` to prevent `HTTP_PROXY`/`HTTPS_PROXY` env vars from routing localhost identity checks through an external proxy. Unit tests added in `tests/test_check_server_identity_script.py`.
- **`HF_HUB_OFFLINE=1` documentation**: Documented across `docs/medium-pilot-workflow.md`, `docs/test-workflow.md`, and this file. Required for all test/pilot runs where the CLIP model is already cached locally.
- **CLIP preflight script**: `scripts/check_clip_model_ready.py` — standalone preflight check that verifies the CLIP model is cached locally and loadable. Returns structured JSON result (`ready`, `model_info`, `error`, `elapsed_ms`, `hf_hub_offline`, `cache_only`). Resets singleton failure state before load attempt. **Cache-only by default** — forces `HF_HUB_OFFLINE=1` internally before loading the model and restores the previous value afterward; never triggers network downloads. 24 unit tests in `tests/test_check_clip_model_ready.py`.
- **CLIP early-fail in classification jobs**: `classification_job_service.py` now performs a CLIP readiness pre-check before the per-item processing loop. Without this, a missing/corrupted CLIP model causes every single item to fail individually — the first failure triggers `CLIPClassifier._LOAD_COOLDOWN_SECONDS` (300 seconds), and all remaining items silently fail with the cooldown error. The early-fail check prevents this cascade by testing CLIP once and failing the entire job with a clear error message and diagnostic hints.
- **Video-only jobs skip CLIP precheck**: The precheck now queries actual candidate `Media` objects and uses `requires_clip_inference()` (from `content_classifier.py`) to determine if any candidate needs CLIP. Video-only jobs (`FileTypeEnum.video`) bypass the CLIP readiness check entirely, so they succeed even when CLIP is unavailable. Mixed jobs (video + image) still fail early if CLIP cannot load. 7 regression tests in `tests/test_classification_job_clip_precheck.py`.
- **CLIPClassifier cooldown behavior**: `clip_classifier.py` has a 300-second cooldown after `ensure_loaded()` fails. During cooldown, all subsequent `ensure_loaded()` calls return `False` immediately without retrying. This protects against repeated expensive load attempts but causes silent cascading failures in batch jobs. The new pre-check avoids this by failing the job before entering the loop.

**Key files:**

| File | Role |
|------|------|
| `scripts/check_test_server_identity.py` | Localhost proxy bypass (`trust_env=False`) |
| `scripts/check_clip_model_ready.py` | CLIP model preflight check (cache-only default) |
| `tests/test_check_clip_model_ready.py` | 24 unit tests for CLIP preflight (cache-only, HF_HUB_OFFLINE, exit codes) |
| `tests/test_check_server_identity_script.py` | Tests for identity script proxy bypass |
| `tests/test_classification_job_clip_precheck.py` | 7 regression tests: video-only skip, early fail, `requires_clip_inference` |
| `backend/app/services/classification_job_service.py` | Conditional CLIP pre-check (video-only skip via `requires_clip_inference`) |
| `backend/app/services/content_classifier.py` | `requires_clip_inference()` helper (public API) |
| `docs/medium-pilot-workflow.md` | Updated preflight checklist (CLIP cache-only, video-only note) |

### Known Observation: Stale Job Recovery at Startup

All four job types (scan, AI tag, translation, classification) have stale recovery at application startup (`main.py` lines 68–84). On startup, any jobs left in `pending`/`running`/`cancelling` status from an unclean shutdown are marked as `interrupted` with an error message. This is a document-only observation — no cleanup or mutation is needed.

**Phase 3.2g awareness:** If the `blombooru_test_medium` database contains stale `interrupted` AI tagging jobs from prior pilot runs, these are expected artifacts of the startup recovery mechanism. They do not indicate data corruption and should NOT be cleaned up or reset. Future phases that re-run pilot tiers should expect to see these jobs in the history. The `only_without_ai_tags` filter on new AI tagging jobs ensures already-tagged media from interrupted jobs is not re-processed unnecessarily.

## What Has NOT Been Built

- No filesystem watcher or scheduled scan (Phase 4)
- No suggestion search syntax (e.g. `suggestion:tag_name`)
- No persistent rejected decision tracking (reject = delete)
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

### Phase 2.2.1 — V.I.O.L.E.T. Rebrand + zh-CN Localization Foundation

Formal project rebrand from AnimeLocalBooru to V.I.O.L.E.T. (Visual Image Organizer for Local Evaluation & Tagging).

- Unified project name to V.I.O.L.E.T. across UI, docs, and config
- Admin UI fully localized to zh-CN (Local Library Scan, AI Tagging, AI Tag Review)
- Tag Chinese display: ~80 common Danbooru tags show Chinese names in UI, canonical tag in tooltip
- Chinese tag search aliases: search "蓝眼睛" → `blue_eyes`, "长发" → `long_hair`
- Tag translation dictionary: `frontend/static/data/tag_translations_zh.json`
- Logo integrated into navbar and README
- Documentation updated with V.I.O.L.E.T. naming
- Added `docs/tag-localization-zh.md` for tag localization design

## Phase History (recent)

**Phase 3.1.2 — complete:**
- Phase 3.1.2a — Admin UI closeout (PR [#31](https://github.com/kyloris0660/AnimeLocalBooru/pull/31) merged)
- Phase 3.1.2b — Gallery content-class filter (PR [#32](https://github.com/kyloris0660/AnimeLocalBooru/pull/32) merged)
- Phase 3.1.2c — Server identity + unified LLM fallback + entity resolver hardening (PR [#33](https://github.com/kyloris0660/AnimeLocalBooru/pull/33) merged)

**Phase 3.2b — completed (PR [#34](https://github.com/kyloris0660/AnimeLocalBooru/pull/34) merged):**
- Pilot hardening, configuration audit, medium-scale readiness

**Phase 3.2c — completed (PR [#35](https://github.com/kyloris0660/AnimeLocalBooru/pull/35) merged):**
- Medium-scale pilot preparation, env documentation, LLM gate unification
- Medium-scale pilot workflow designed in `docs/medium-pilot-workflow.md` (execution is a separate phase)

**Phase 3.2f — completed (PR [#38](https://github.com/kyloris0660/AnimeLocalBooru/pull/38) merged):**
- Model / proxy runtime hardening: localhost proxy bypass, CLIP preflight (cache-only default), CLIP early-fail in classification jobs, video-only CLIP skip via `requires_clip_inference()`, 24+7 unit/regression tests

**Phase 3.2g — completed (manual, no PR):**
- Limited AI tagging on `blombooru_test_medium` (medium pilot DB, 522 media)
- WDv3 tagger: 3 jobs total — #1 interrupted (46), #2 dry-run (25), #3 completed (50/50, 2237 tags)
- Post-job-#3 DB: 1090 tags, 5513 media_tags, 97 media with AI tags
- **Critical incident**: System auto-triggered tag localization after AI tagging (localization_status=`queued_767_tags_worker_running`), created 306 LLM translations + 4 translation jobs despite phase policy prohibiting LLM usage. This motivated Phase 3.2g.1.

**Phase 3.2g.1 — AI Tagging Scope & Localization Side-Effect Hardening (PR [#39](https://github.com/kyloris0660/AnimeLocalBooru/pull/39) merged):**
- Added `AI_TAGGING_AUTO_LOCALIZATION` config flag (default: `true`) to gate the automatic localization trigger after AI tagging jobs
- When set to `false`, `_schedule_localization()` skips with `localization_status=skipped_auto_localization_disabled`
- Added `content_class_filter` parameter to `CreateAITagJobRequest` for safe AI tagging scope targeting (e.g., `["anime"]` only)
- Pre-filters media IDs at route level, cannot be combined with explicit `media_ids`
- 4 regression tests for localization gate, 7 tests for content_class_filter model

**Phase 3.2g.2 — AI-only run (manual, no PR):**
- WDv3 AI tagging on `blombooru_test_medium`: 2 jobs — #4 dry-run (25 items), #5 completed (100/100, anime+illustration only)
- `AI_TAGGING_AUTO_LOCALIZATION=false` set, but background translation worker was NOT disabled → 182 LLM translations leaked
- Server ran with `VIOLET_STORAGE_ROOT=test` instead of `medium` — storage root mismatch not caught (identity script lacked `--expected-storage-root`)
- Admin password silently reset via psql without user consent
- These incidents motivated Phase 3.2g.2a

**Phase 3.2g.2a — AI-only Run Isolation & Storage Identity Hardening (PR [#40](https://github.com/kyloris0660/AnimeLocalBooru/pull/40)):**
- Server identity endpoint: added `original_dir`, `thumbnail_dir`, `storage_root_explicitly_set` fields
- Identity check script: added `--expected-storage-root` arg with `normalize_path()` comparison
- `media_processor.py`: thread-safe python-magic availability probe (one-time, lock-protected) with per-thread `Magic(mime=True)` detectors via `threading.local()`; MIME fallback chain (python-magic → PIL → mimetypes → octet-stream) works without libmagic
- Unit tests: 4 new identity endpoint tests, 3 normalize_path tests, 20+ MIME magic cache/thread-safety tests (`test_media_processor_mime_magic_cache.py`)
- Docs: AI-only isolation section, admin auth mutation rule, reporting accuracy rule, storage identity hard gate

**Phase 3.2j — Manual Tag Translation Correction (PR pending):**
- PATCH endpoint: `PATCH /api/admin/tag-localization/translations/{id}` for manual correction of display_name, aliases, needs_review
- Sets `source='manual'` and `status='reviewed'` to protect from future LLM overwrites (source priority system: manual > static > llm > imported)
- Input validation: at least one field required, empty display_name rejected, alias normalization (dedup, trim, remove empty, remove alias==display_name)
- Returns old/new diff in response for audit trail
- Admin UI: edit button on review table rows enters PATCH mode (canonical name locked, save→update, cancel button), `_patchTagTranslation()` and `_cancelEditMode()` methods
- i18n: 4 new keys in en.json and zh-cn.json (update_translation, cancel_edit, translation_saved, translation_updated)
- Tests: 11 unit tests covering 10 required cases (valid update, aliases, needs_review, empty body 422, empty display_name 422, 404, auth dependency, alias normalization, source priority protection, cache invalidation)

**Phase 3.3a.1 — iCloud Candidate Manifest (PR [#45](https://github.com/kyloris0660/AnimeLocalBooru/pull/45) merged):**
- `scripts/generate_candidate_manifest.py`: generates frozen CSV manifest from iCloud source directories
- 5,326 total rows: 522 existing (already in medium pilot), 478 new candidates, 4,326 excluded
- Copy-safety boundaries: combined total cap, extension allowlist, path escape detection, size sanity checks
- `scripts/stage_pilot_files.py`: staging executor with `--dry-run` default, `--manifest` and `--target-root` args
- Tests: 38 manifest tests + 55 staging tests

**Phase 3.3b — Tier-1000 Staging Copy (PR [#46](https://github.com/kyloris0660/AnimeLocalBooru/pull/46) merged):**
- Executed controlled copy of 1,000 files (2.98 GB) to privacy-safe `tier1000_staging` label
- 522 existing files (from medium pilot) + 478 new files from iCloud source
- Copy-safety: dry-run verification before real execution, file count/size limits enforced

**Phase 3.4 - Tier-1000 Pre-import Audit (PR [#48](https://github.com/kyloris0660/AnimeLocalBooru/pull/48) merged):**
- `scripts/audit_tier1000.py`: self-contained manifest-vs-disk verification (no cross-script imports)
- Verifies: target exists, size matches, extension matches, no path escapes, no unexpected files
- Real audit: 1,000/1,000 files PASS, 3,204,263,387 bytes verified, zero discrepancies
- `tests/test_audit_tier1000.py`: 110 tests after Phase 3.4 hardening
- Full non-E2E suite at Phase 3.4 closeout: 855 passed, 10 skipped

**Phase 3.5 - Tier-1000 Database Import (branch `phase3.5-tier1000-db-import`):**
- `scripts/import_staged_manifest.py`: manifest-driven copy-mode importer with dry-run, execute confirmation, privacy-safe JSON report, and local full-path CSV under `.local_manifests/`
- Real import: 995 media rows created, 5 same-hash duplicates skipped, 0 failures
- DB/storage: `blombooru`, app-managed storage recorded in public reports as `app_storage`, `Media.source='violet:tier1000:phase3.5'`
- Post-import audit: 995 DB rows, 995 originals, 995 thumbnails, 0 missing, source label mismatches 0
- Real post-import dev app smoke: `/api/media`, media detail, original file, thumbnail, gallery page, and Playwright Edge smoke all PASS against the actual imported Tier-1000 rows
- Controlled test-env app/browser smoke: `VIOLET_ENV=test`, `POSTGRES_DB=blombooru_test`, dedicated test storage, server identity PASS, gallery/API/Playwright Edge smoke PASS
- Closeout hardening: execute rejects NULL-thumbnail imports, post-import audit counts `thumbnail_path=NULL`, public report sanitizer redacts Windows/POSIX absolute paths, and idempotency dry-run reports `estimated_bytes_to_copy=0`
- Background side effects: 0 AI jobs, 0 classification jobs, 0 translation jobs/translations since import start

**Phase 3.6 - Tier-1000 AI Tagging + Localization (PR [#50](https://github.com/kyloris0660/AnimeLocalBooru/pull/50) merged):**
- `scripts/run_phase36_tier1000_ai_localization.py`: narrow source-label runner with explicit media ID AI jobs and controlled visual/general localization
- Backup before writes: `phase-3.6-tier1000-before-20260519-203857.dump` (`223388` bytes), path redacted in public docs
- AI tagging target: 995 media where `Media.source='violet:tier1000:phase3.5'`
- AI tagging result: processed 995, failed 0, confirmed associations 41416, suggestions 11938, media_tags delta 53354, tag row delta 1301, media_with_ai_tags delta 995
- AI isolation: classification job delta 0, translation job delta during AI 0, auto-localization skipped by `AI_TAGGING_AUTO_LOCALIZATION=false`
- Localization result: 1196 visual/general/meta candidates translated, failed 0, remaining target visual missing translations 0, proper-noun candidates skipped 102, translation job ID 15
- Validation: real dev DB API + Playwright Edge smoke PASS; controlled `VIOLET_ENV=test` server identity + API/browser smoke PASS
- Closeout hardening: Phase 3.6 runner now locks write modes to `Media.source='violet:tier1000:phase3.5'`, requires `VIOLET_ENV=development`, blocks DB-backed active AI jobs (`pending`/`running`/`cancelling`), hard-fails forbidden classification/translation side-effect job deltas during AI, exits nonzero on localization provider/candidate failures including unsaved `upsert_translation()` results, marks `TagTranslationJob` failed on save/finalization exceptions, caps localization candidates to `min(--max-items, TAG_TRANSLATION_BATCH_MAX_ITEMS)`, writes partial AI failure reports before bad-chunk aborts with failed chunk stats included in top-level totals, and rejects non-positive `ai-tag --limit` / `localize --max-items`
- Manual inspection notes: Admin AI tagging currently lacks an aggregate dashboard by design; read-only DB validation found 106 pending translations are all `character` tags, target visual/general/meta pending is 0, and content classification remains intentionally unrun (`995` unclassified)
- Phase 4 not started; Entity Resolver not run during Phase 3.6; content classification not run during Phase 3.6

**Phase 3.7 - Tier-1000 Content Classification Validation + Tag Scope Gate (branch `phase3.7-tier1000-classification-scope-gate`):**
- `scripts/run_phase37_tier1000_classification_scope_gate.py`: source-label-locked classification runner and read-only tag scope audit
- Backup before writes: `phase-3.7-tier1000-before-20260520-125024.dump` (`1392536` bytes), path redacted in public docs
- Target: 995 media where `Media.source='violet:tier1000:phase3.5'`
- Classification result: processed 995, failed 0, classified 995, unclassified 0
- Distribution: `anime=948`, `unknown=21`, `non_anime=26`, `illustration=0`
- Side effects: classification jobs +10; AI jobs delta 0; translation jobs delta 0; tag rows delta 0; target AI association delta 0
- Tag scope gate: future tag-derived workflows must include only `anime` and `unknown`; `illustration`, `non_anime`, and `unclassified` are excluded from future AI tagging candidate selection, localization candidate selection, tag statistics, and tag-driven similarity
- Scope audit found 969 eligible media and 26 ineligible media. Existing Phase 3.6 AI associations on ineligible media are audit evidence only; no tag/media cleanup was performed.
- Closeout fix: metadata extraction now recursively sanitizes PIL/EXIF/XMP values before JSON response, preventing `IFDRational`/`jsonable_encoder` 500s on `/api/media/{id}/metadata`.
- Closeout validation: full read-only sweep passed for 995 metadata endpoints, 995 media detail endpoints, 995 thumbnails, and a 65-item original-file sample; content-class filters, canonical/localized search, AI review/tag APIs, browser smoke, and server-log scan all passed with 0 failures.
- Phase 4 not started; similarity/clustering not started; Entity Resolver not run

**Phase 3.8b - Classification-First E2E Workflow Foundation + Dry-run Orchestrator (branch `phase3.8b-classification-first-e2e-foundation`):**
- Adds reusable service-level helpers in `backend/app/services/classification_first_workflow.py` for workflow scope, eligible/ineligible content-class policy, localization candidate scope, legacy contamination audit, mutation snapshots, privacy-safe reports, and stage contracts.
- Adds dry-run-only CLI `scripts/plan_classification_first_e2e.py`; `--execute` is explicitly rejected in Phase 3.8b.
- Encodes the formal workflow order: candidate manifest, staging copy, pre-import audit, DB import, content classification, eligible selection (`anime` + `unknown`), AI tagging only eligible media, localization only eligible-derived `general`/`meta` tags, post-run validation, browser/API smoke, report.
- Dry-run against the current Phase 3.5 source label confirmed target `995`, eligible `969`, ineligible `26`, `NULL content_class=0`, and legacy ineligible AI associations `771`.
- Dry-run mutation check confirmed `media`, `media_tags`, `ai_jobs`, `classification_jobs`, and `translation_jobs` before/after deltas are all `0`.
- Public reports: `docs/reports/phase-3.8b-classification-first-e2e-dry-run.md` and `docs/reports/phase-3.8b-classification-first-e2e-dry-run-summary.json`.
- Phase 3.8b does not execute import/copy/classification/AI/localization, does not mutate DB/storage/source/staging, and does not start Phase 4, Entity Resolver, or similarity/clustering.

**Phase 3.8c - Medium Pilot Preflight + Classification-First E2E Dry-run (branch `phase3.8c-medium-pilot-preflight-dryrun`):**
- Adds `scripts/plan_phase38c_medium_pilot_preflight.py`, a dry-run-only planner for the next +1000 medium pilot. `--execute` is explicitly rejected; Phase 3.8d remains the first possible execute phase after approval.
- Candidate discovery scans the approved source read-only, uses filesystem modified time as the time signal, and samples across 16 quantile temporal buckets instead of directory order, newest-only, oldest-only, or one contiguous time window.
- Candidate result: source inventory `38,356`, eligible not-yet-selected candidate pool `33,032`, selected `1,000`, excluded/not-selected `37,356`, timestamp_unknown `0`, approximate future copy size `3,112,402,513` bytes.
- Selected bucket distribution: `b01-b08=63` each and `b09-b16=62` each. Extension distribution: `.jpg=816`, `.png=173`, `.jpeg=10`, `.gif=1`.
- Planned future execute scale: current DB media `995`; expected post-execute media count around `1,995` before duplicate/import failures.
- No-mutation proof passed for DB (`media`, `media_tags`, `ai_jobs`, `classification_jobs`, `translation_jobs` deltas all `0`), app storage originals/thumbnails, source tree, and planned staging target.
- Public reports: `docs/reports/phase-3.8c-medium-pilot-preflight.md` and `docs/reports/phase-3.8c-medium-pilot-preflight-summary.json`.
- Local full-path manifest: `.local_manifests/phase-3.8c-medium-candidate-manifest.csv`; it is gitignored and must not be committed.
- Phase 3.8c does not execute real import, staging copy, DB import, content classification, AI tagging, localization, Entity Resolver, similarity/clustering, cleanup, delete, reset, drop, or truncate.

**Phase 3.8d-I1 - iCloud / Windows Cloud Files Ingestion Reliability Incident & Hardening (branch `phase3.8d-icloud-ingestion-hardening`):**
- Phase 3.8d guarded execute remains blocked after real staging copy failed at manifest `row_id=98`, bucket `b02`, with WinError `388` (`The cloud sync provider failed to perform the operation due to network being unavailable`).
- Impact was contained: DB import did not run; classification, AI tagging, localization, Entity Resolver, similarity/clustering, app-managed storage mutation, cleanup, delete, reset, drop, and truncate did not run.
- Partial staging is evidence only: `97` copied files, `340,159,586` bytes, safe label `phase_3_8d_partial_staging`; do not auto-clean it.
- The Phase 3.8d-I1 metadata-only cloud availability audit found selected_total `1000`, already copied `97`, not yet copied `903`, `RECALL_ON_DATA_ACCESS=613`, likely cloud placeholder / recall-risk `613`, and copy gate `blocked_requires_hydration_policy`.
- Phase 2.4 solved scan-safety by skipping placeholders in hydrated-only scan mode; Phase 3.8d requires ingestion-availability, where selected real cloud-backed files need controlled hydrate/read-probe/backfill/resume policy before copy.
- New shared helper `backend/app/utils/cloud_files.py` provides Windows Cloud Files attribute metadata and structured error classification without reading file content in metadata-only mode.
- New audit script `scripts/audit_cloud_availability.py` produces privacy-safe public reports plus local full-path details; `--read-probe` is opt-in and must not be run without explicit approval.
- Reports: `docs/reports/phase-3.8d-icloud-ingestion-incident.md`, `docs/reports/phase-3.8d-cloud-availability-audit.md`, and `docs/reports/phase-3.8d-cloud-availability-audit-summary.json`.

**Phase 3.8d-I2 - Source Ingestion Gate Unification (branch `phase3.8d-i2-source-ingestion-gate`):**
- Adds `backend/app/services/source_ingestion_gate.py` as the shared policy point for path-based source availability.
- Path-based source workflows must use `path_source` gate checks before content reads/copies.
- Upload-bytes and app-managed storage reads are explicitly outside the source cloud gate; their normal validation/storage checks still apply.
- Staging-to-DB import uses `staging_file` semantics and requires a passed staging audit artifact before DB import.
- Report: `docs/reports/phase-3.8d-i2-source-ingestion-gate.md`.

**Phase 3.8d-I3 - Recovery Cleanup Dry-run and Hydration Policy (branch `phase3.8d-i3-recovery-cleanup-hydration-plan`):**
- Adds a Final Delivery Report Standard to `AGENTS.md`, `CLAUDE.md`, and `docs/test-workflow.md`; final implementation/review reports must be Chinese and include PR URL, branch/head SHA, files changed, exact tests/results, local artifacts, reviewer status, safety confirmation, blocked/ready status, and recommended next step.
- Replaces the standard automatic reviewer-fix loop with a Reviewer Feedback Handling Policy: CodeX triggers reviewer and reports current-head feedback, but does not modify code from reviewer comments unless the user explicitly authorizes a specific auto-fix loop.
- Adds `scripts/plan_phase38d_i3_recovery.py`, originally as a dry-run recovery planner for the preserved partial staging target. It generates privacy-safe cleanup and recovery policy reports plus an ignored local details artifact.
- Partial staging cleanup dry-run result: target safe label `phase_3_8d_partial_staging`, exists, dedicated Phase 3.8d target by manifest/filesystem proof, `97` files, `340,159,586` bytes, extension distribution `.jpg=68`, `.png=22`, `.jpeg=6`, `.gif=1`, no protected-root overlap, no deletion performed. Staging logs are diagnostic only and are not used for cleanup authorization.
- Controlled read-probe/hydration remains opt-in only, with bounded prefix read policy; direct `CfHydratePlaceholder` integration remains future work unless explicitly approved.
- Same-bucket backfill dry-run for failed row `98` finds one same-bucket replacement candidate and preserves `selected_total=1000`; no manifest replacement is performed.
- Recommendation: cleanup plus rerun is preferred over resume because only `97` files were copied and no DB/downstream state exists, but actual cleanup and any read-probe/hydration both require explicit later approval.
- Reports: `docs/reports/phase-3.8d-i3-recovery-plan.md`, `docs/reports/phase-3.8d-i3-partial-staging-cleanup-dry-run.md`, and `docs/reports/phase-3.8d-i3-partial-staging-cleanup-dry-run-summary.json`.

**Phase 3.8d-I4a - Controlled Partial Staging Cleanup Executor Support (branch `phase3.8d-i4a-cleanup-executor-support`):**
- Adds reviewed cleanup execution support to `scripts/plan_phase38d_i3_recovery.py`, but this phase does not run it against the real partial staging target.
- Execute mode requires `--execute-cleanup`, exact confirmation phrase `DELETE_PHASE38D_PARTIAL_STAGING`, a fresh passing cleanup dry-run proof, valid protected roots, manifest/filesystem identity proof, no unexpected/missing/size-mismatched files, and no symlink/reparse escape hazard under the target.
- Deletion scope is limited to expected manifest/filesystem-matched regular files under the verified target. Parent directories are left in place; source/iCloud, repo files, app-managed storage, DB data, staging copy, read-probe/hydration, classification, AI tagging, localization, Entity Resolver, and similarity remain untouched.
- Tests cover default no-delete behavior, wrong confirmation phrase, identity-proof failures, valid temp-directory cleanup, path traversal blocking, and symlink/reparse hazard blocking.
- Report: `docs/reports/phase-3.8d-i4a-cleanup-executor-support.md`.

## Recommended Next Step: Resolve Phase 3.8d Cloud Recovery Before Any Execute

Do not resume Phase 3.8d execute, start Phase 4, or run any larger import until the cloud ingestion reliability incident is reviewed and an explicit recovery path is approved:

1. Review and merge Phase 3.8d-I4a cleanup executor support.
2. Start Phase 3.8d-I4b only after approval to run the reviewed executor against the dedicated partial staging target.
3. Explicitly approve any controlled read-probe/hydration attempt separately. Metadata-only audit remains the default.
4. If bounded hydrate/read-probe fails for specific files, approve same-bucket backfill planning to preserve `selected_total=1000` and temporal diversity.
5. Only after cleanup/recovery and staging copy are complete and verified may Phase 3.8d DB import be reconsidered.

## Previous Recommended Step: Manual Validation Before Scaling

Do not start Phase 4 or a larger-scale pilot until a human pass reviews the Phase 3.6/3.7 output:

1. Randomly inspect 30-50 imported media across gallery and media detail.
2. Check AI tag quality, obvious false positives, and suggestion volume.
3. Check Chinese localized tag display and search for common visual tags.
4. Inspect the 26 `non_anime` classified media before any future cleanup proposal.
5. During media-detail browsing, verify sampled `/api/media/<id>/metadata` responses are HTTP 200 and browser Network shows no `/metadata` 500.
6. Check AI Tag Review usability and whether bulk-review workflow is practical.
7. Record recurring tag/localization/classification/API browsing problems before Phase 3.8.

After manual validation, the next engineering phase should prioritize background task reliability/failure isolation and explicit tag-derived workflow gates before larger pilots.

---

### Previously planned: Phase 3.2g.3 — Medium Pilot Tier 1

> The following section is retained for reference. Phase 3.6+ stability and scale validation take priority.

**Phase 3.2g.3 — Medium Pilot Tier 1: Complete AI Tagging + Tag Translation**

Resume medium-pilot AI tagging on `blombooru_test_medium` to tag remaining anime media, then run controlled tag translation.

### Known Medium State (last known from Phase 3.2g.2 report — re-audit before execution)

| Metric | Count |
|--------|-------|
| Total media | 522 |
| anime | 500 |
| non_anime | 14 |
| unknown | 8 |
| media_with_ai_tags | 197 |
| Remaining anime without AI tags | ~306 |
| non_anime AI-tagged | 0 |
| unknown AI-tagged | 3 |

> These counts are from the Phase 3.2g.2 report. Re-audit `blombooru_test_medium` before starting any new processing — counts may have drifted if interim operations were performed.

### Prerequisites (all must pass before execution)

1. PR #40 (Phase 3.2g.2a) merged and code on `main`
2. Full preflight checklist from `docs/medium-pilot-workflow.md` § 7 completed
3. Python/venv identity preflight passed (`scripts/check_python_env.py`)
4. Server identity check passed with all `--expected-*` args including a dedicated medium-pilot storage root
5. All 4 AI-only isolation env vars set for AI tagging phase (see `docs/medium-pilot-workflow.md` § 6.3)
6. Translation worker confirmed stopped (`GET /api/admin/tag-localization/worker/status` → `running: false`)
7. If any active/running translation jobs exist from prior phases, stop and report them before starting new AI tagging
8. Database backup taken (`pg_dump -Fc -f backup_before_3.2g.3.dump blombooru_test_medium`)
9. Re-audit medium DB state: verify actual media counts, `media_with_ai_tags`, tag counts against known state above
10. CLIP model readiness verified (`scripts/check_clip_model_ready.py` exits 0)
11. `HF_HUB_OFFLINE=1` set
12. Dry-run AI tagging job completed and reviewed before real run
13. Plan approved by user before execution begins

### Next-Phase Requirement: Service Control UI

The next development phase should include a developer/service control panel:

- Show current local dev/background services status (running, stopped, port, PID)
- Show server PID and listening port
- Allow safe stop/restart of V.I.O.L.E.T. dev server (without killing unrelated Python processes)
- Show background workers status (tag translation worker, entity alias resolver)
- One-click restart after config changes (optional)
- Diagnostics for port conflicts and stale server processes (optional)

## Test Environment

### Standardized Test Env Loading

```powershell
. "$env:USERPROFILE\.violet\test-env.ps1"
```

This sets core test variables: `VIOLET_ENV=test`, `POSTGRES_DB=blombooru_test`, dedicated `VIOLET_STORAGE_ROOT`, dedicated `VIOLET_TEST_FIXTURE_PATH`, and `APP_PORT=8001`. For E2E runs, agents override `VIOLET_BASE_URL` and `VIOLET_RUN_REAL_E2E` in the current session.

**HuggingFace Hub Offline Mode:** Set `HF_HUB_OFFLINE=1` when the CLIP model is already cached locally. This prevents HuggingFace Hub metadata network requests from failing in proxy environments. Required for medium-pilot tiers. See `docs/medium-pilot-workflow.md` § 3.1.

### Test Server

```powershell
. "$env:USERPROFILE\.violet\test-env.ps1"
& "$PY" run.py --debug  # APP_PORT set by test-env.ps1 or overridden manually
```

### Test Tiers

| Tier | Command | Requirements |
|------|---------|-------------|
| 1 — Unit tests | `& "$PY" -m pytest tests/test_env_safety.py tests/test_destructive_gate.py tests/test_scanner_icloud.py -v` | None (mocks only) |
| 2 — Fixture validation | `& "$PY" -m pytest tests/test_fixture_validation.py -v` | `VIOLET_TEST_FIXTURE_PATH` |
| 3 — Playwright E2E | `npx playwright test tests/e2e/*.spec.ts --project=edge` | Running test server + `VIOLET_RUN_REAL_E2E=1` |

### VioletTestFixture

Location: dedicated `VioletTestFixture` path — contains curated images for E2E testing (anime, non_anime, mixed subfolders). Read-only; never modified by tests.

## Test Directory

Dedicated `AnimeLocalBooruTest` path — 17 files (14 valid images already imported).

**Real target:** privacy-sensitive iCloud Photos path — always use dry-run + max_files first.

## Key References

| Document | Purpose |
|----------|---------|
| `README.md` | Project overview and quick start |
| `AGENTS.md` | Cursor agent instructions |
| `docs/ai-tagging-usage-guide.md` | Complete AI tagging usage guide with GUI walkthrough |
| `docs/ai-tag-review.md` | Phase 2.2 review UI and API documentation |
| `docs/ai-auto-tagging.md` | Phase 2.1 AI tagging technical reference |
| `docs/ai-tagging-jobs.md` | Phase 2.3 AI tagging jobs documentation |
| `docs/e2e-violet-test-100.md` | E2E validation guide (VioletTest100) |
| `docs/project-roadmap.md` | Full phase plan |
| `docs/tag-metadata-foundation.md` | Phase 2 technical documentation |
| `docs/local-anime-library-devlog.md` | Per-phase technical log |
| `docs/local-library-scan.md` | Feature documentation and API usage |
| `docs/tag-localization-llm.md` | Phase 2.2.2 LLM translation documentation |
| `docs/entity-alias-resolver.md` | Phase 2.3e entity alias resolver documentation |
| `docs/icloud-safe-ingestion.md` | Phase 2.4 iCloud safe ingestion documentation |
| `docs/content-classification.md` | Phase 3 + 3.1 content classification documentation |
| `scripts/evaluate_content_classification.py` | Phase 3 server-based evaluation harness |
| `scripts/evaluate_clip_content_classifier.py` | Phase 3.1 standalone CLIP evaluation (no DB) |
| `docs/test-workflow.md` | Test workflow documentation and policy |
| `docs/medium-pilot-workflow.md` | Medium-scale pilot (500/1000/2000) workflow design |
| `docs/config-audit-phase3.2b.md` | Phase 3.2b configuration audit report |

### Phase 3.1.1a — Environment / DB / Storage Safety Foundation

Hardened the environment, database, and storage separation to prevent worktree/DB mismatch incidents:

- **VIOLET_ENV**: New `development|test|production` environment variable with `IS_TEST_ENV` and `IS_PRODUCTION_ENV` computed properties. Invalid values raise at access time.
- **CODE_ROOT / STORAGE_ROOT separation**: `CODE_ROOT` = project root (replaces ambiguous `BASE_DIR`). `STORAGE_ROOT` optionally set via `VIOLET_STORAGE_ROOT` env var, defaults to `CODE_ROOT`. All storage paths (`MEDIA_DIR`, `ORIGINAL_DIR`, `THUMBNAIL_DIR`, `DATA_DIR`) derive from `STORAGE_ROOT`.
- **Test DB fail-closed**: `VIOLET_ENV=test` requires `POSTGRES_DB` to contain `_test` suffix or `TEST_DATABASE_URL` to be set. Using the production DB name in test mode raises RuntimeError.
- **`assert_test_db()` helper**: `database.py` function that raises if `VIOLET_ENV != test` or DB name is `blombooru`. Used as a guard in test fixtures.
- **9-condition destructive gate**: `_require_destructive_gate()` in `dev_tools.py` replaces the old flag+confirm checks. Production hard-refuses (condition 0). Remaining 8 conditions: test env, test DB, STORAGE_ROOT explicitly set, STORAGE_ROOT != CODE_ROOT, STORAGE_ROOT != main repo, STORAGE_ROOT under recommended prefix, `VIOLET_ALLOW_DESTRUCTIVE_E2E=1`, confirm + phrase.
- **`_resolve_stored_media_path()`**: Now uses `settings.STORAGE_ROOT` instead of `settings.BASE_DIR` for media file resolution.
- **Startup logging**: `run.py` prints `VIOLET_ENV`, `APP_VERSION`, `CODE_ROOT`, `STORAGE_ROOT`, `DB_NAME` at boot.
- **14 unit tests**: `tests/test_env_safety.py` covering VIOLET_ENV, STORAGE_ROOT separation, test DB fail-closed, assert_test_db.
- **5 env templates**: `.env.example`, `.env.test.example`, `.env.production.example`, `.env.worktree.debug.example`, `.env.worktree.test.example`.
- **Test DB setup script**: `scripts/setup_test_db.py` — idempotent creation of `blombooru_test` database.

**Key files:**

| File | Role |
|------|------|
| `backend/app/config.py` | `VIOLET_ENV`, `CODE_ROOT`, `STORAGE_ROOT`, `IS_TEST_ENV`, `IS_PRODUCTION_ENV`, `DB_NAME` fail-closed |
| `backend/app/database.py` | `assert_test_db()` helper |
| `backend/app/routes/admin/dev_tools.py` | `_require_destructive_gate()`, `_compute_gate_diagnostic()`, `_resolve_stored_media_path()` uses STORAGE_ROOT |
| `run.py` | Startup env/storage/DB logging |
| `tests/test_env_safety.py` | 14 unit tests for env/DB/storage safety |
| `scripts/setup_test_db.py` | Idempotent test DB creation |
| `.env.*.example` | 5 env template files |

### Phase 3.1.1b — Fixture-Based Test Workflow Foundation

Established a reproducible, fixture-based E2E test workflow using isolated test DB + test storage:

- **`setup_test_db.py --migrate`**: Enhanced script with `--migrate` flag that runs the app's full schema initialization (`Base.metadata.create_all()` + `check_and_migrate_schema()`) against the test database. Includes forbidden DB name safeguard and `VIOLET_ENV=test` enforcement.
- **`inspect_test_fixture.py`**: Read-only fixture validation script. Counts supported images per subfolder (anime/non_anime/mixed), reports unsupported files, CLI with `--json` output.
- **`tests/conftest.py`**: Shared pytest fixtures — `reload_settings`, `fixture_path` (gated by `VIOLET_TEST_FIXTURE_PATH`), `fixture_counts`.
- **`tests/test_fixture_validation.py`**: 10 tests verifying VioletTestFixture directory structure and file counts (read-only, never modifies fixture files).
- **`tests/test_destructive_gate.py`**: 12 tests covering destructive operation gate conditions (production refusal, test env pass, missing E2E flag, forbidden DB names) and storage path containment (traversal, absolute, UNC, empty rejection).
- **`tests/e2e/fixture-import.spec.ts`**: 5 Playwright E2E tests — config diagnostics, preflight scan, dry-run scan, real import (max_files=5), duplicate idempotency. Gated by `VIOLET_RUN_REAL_E2E=1` + `VIOLET_TEST_FIXTURE_PATH`.
- **`tests/e2e/gallery-browse.spec.ts`**: 4 Playwright E2E tests — gallery page load, API media listing, media detail page, thumbnail endpoint. Gated by `VIOLET_RUN_REAL_E2E=1`.
- **`tests/e2e/config-diagnostics-e2e.spec.ts`**: 6 Playwright E2E tests — environment/database/storage/destructive-ops/secrets/server-info sections. Gated by `VIOLET_RUN_REAL_E2E=1`.

**Key files:**

| File | Role |
|------|------|
| `scripts/setup_test_db.py` | Idempotent test DB creation + schema migration |
| `scripts/inspect_test_fixture.py` | Read-only fixture validation |
| `tests/conftest.py` | Shared pytest fixtures |
| `tests/test_fixture_validation.py` | Fixture structure validation (10 tests) |
| `tests/test_destructive_gate.py` | Destructive gate + storage containment (12 tests) |
| `tests/e2e/fixture-import.spec.ts` | Fixture import E2E (5 tests) |
| `tests/e2e/gallery-browse.spec.ts` | Gallery browse E2E (4 tests) |
| `tests/e2e/config-diagnostics-e2e.spec.ts` | Config diagnostics E2E (6 tests) |
| `docs/test-workflow.md` | Test workflow documentation |

### Fix: Storage Root Containment with Path Semantics (PR #27)

Hardened storage path containment to use proper `PurePosixPath` / `PureWindowsPath` semantics:

- Replaced string-prefix `startswith()` checks with `PurePath.is_relative_to()` for traversal prevention
- Storage root containment now correctly handles edge cases: `../` traversal, absolute paths, UNC paths, empty paths
- Tests in `tests/test_destructive_gate.py` verify all containment scenarios

### Phase 3.1.1c — Full Pipeline Smoke Validation (PR #29)

Local full pipeline smoke validation helper for verifying the complete import → tag → classify → search workflow:

- **`scripts/smoke_validate_pipeline.py`**: End-to-end validation script covering preflight, import, AI tagging, content classification, gallery browse, and search
- Verified entire pipeline runs cleanly against isolated test environment
- All existing unit tests (Tier 1) and fixture validation tests (Tier 2) pass

### Fix: Harden Unicode Scan Import Failure Handling (PR #30)

Fixed crash during scan import when files with certain Unicode characters in their paths failed to hash:

- Hardened `calculate_file_hash` and scan pipeline to handle Unicode path edge cases gracefully
- Files that fail to hash are now counted as `failed` rather than crashing the entire scan job

Post-Phase 2.4 infrastructure fix for LLM connectivity issues behind GFW:

- **Root cause**: OpenAI API (`api.openai.com`) unreachable through local proxy (SSL EOF) — blocked by GFW
- **DeepSeek fallback provider**: New `FallbackProvider` class wraps primary + fallback `OpenAICompatibleProvider`. Transport errors (ConnectError, timeout, etc.) on the primary automatically retry through the fallback. Non-transport errors (HTTP 4xx, bad JSON) are raised immediately.
- **Improved error diagnostics**: Error messages now include structured context: `[ExceptionClass] provider=label host=hostname model=name proxy=host:port`. API keys and Authorization headers are never included.
- **Proxy detection**: `_detect_proxy()` reads `HTTPS_PROXY`/`HTTP_PROXY` env vars and includes proxy info in error messages for debugging.
- **Configuration**: 3 new env vars: `TAG_TRANSLATION_LLM_FALLBACK_API_KEY`, `TAG_TRANSLATION_LLM_FALLBACK_MODEL`, `TAG_TRANSLATION_LLM_FALLBACK_BASE_URL`

**Key files:**

| File | Change |
|------|--------|
| `backend/app/services/llm_translation_provider.py` | `FallbackProvider`, error formatting, proxy detection, transport error classification |
| `backend/app/config.py` | 3 fallback provider config properties |
| `example.env` | Fallback LLM configuration section |
| `docs/tag-localization-llm.md` | Fallback Provider, Proxy Configuration, Error Diagnostics docs |
| `docs/tag-localization-zh.md` | Tag localization design (zh-CN) |
| `example.env` | Available environment variables |

### Phase 3.2g.5 — Config Precedence Hardening

**Status:** In progress
**Branch:** `phase3.2g-config-precedence-hardening` (from `main` @ `ff7da9a`)

**Root cause:** `backend/app/config.py` called `load_dotenv(override=True)`, forcing `.env` values to overwrite explicit session/process environment variables. This caused repeated incidents during medium pilot phases where `POSTGRES_DB=blombooru` from `.env` overwrote `POSTGRES_DB=blombooru_test_medium` set in the shell, and `TAG_TRANSLATION_*=true` flags from `.env` overwrote `false` values set for AI-only isolation.

**Fix:** Changed `load_dotenv(override=True)` → `load_dotenv(override=False)` in `backend/app/config.py` line 12. With `override=False`, `load_dotenv` only sets variables that are not already present in the environment, preserving the standard precedence: process env → `.env` defaults → code defaults.

**Regression tests:** `tests/test_config_precedence.py` — 6 test classes covering:
- `POSTGRES_DB` session override wins over `.env`
- `TEST_DATABASE_URL` still works with `override=False`
- `TAG_TRANSLATION_BACKGROUND_ENABLED` session `false` overrides `.env` `true`
- `TAG_TRANSLATION_AUTO_ENABLED` session `false` overrides `.env` `true`
- `TAG_TRANSLATION_LLM_ENABLED` session `false` overrides `.env` `true`
- Code defaults used when env var unset; source code assertion for `override=False`

**Files changed:**

| File | Change |
|------|--------|
| `backend/app/config.py` | `load_dotenv(override=True)` → `override=False` |
| `tests/test_config_precedence.py` | New: 6 regression test classes |
| `docs/test-workflow.md` | Added `test_config_precedence.py` to Tier 1 table and command |
| `AGENTS.md` | Added `test_config_precedence.py` to Tier 1 table and command |
| `docs/medium-pilot-workflow.md` | Added config precedence fix note in § 6.3 |
| `docs/current-handoff.md` | Updated `override=True` → `override=False` references; added this section |
