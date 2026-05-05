# Current Handoff — V.I.O.L.E.T.

> Last updated after Phase 2.3 Auto Tagging Jobs + E2E Validation (2026-05-05).
> Read this file at the start of any new Cursor conversation to resume development.

## Repository State

| Item | Value |
|------|-------|
| **Repo** | `kyloris0660/AnimeLocalBooru` (project name: V.I.O.L.E.T.) |
| **Branch** | `main` |
| **Upstream** | Based on [Blombooru](https://github.com/mrblomblo/blombooru) |
| **Stack** | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + Vanilla JS |
| **Python** | 3.12 (venv at `./venv`) |
| **DB** | `blombooru` on `localhost:5432`, user `postgres` |
| **Dev server** | `.\venv\Scripts\Activate.ps1` → `python run.py --debug` → `http://localhost:8000` |
| **Admin credentials** | `admin` / `admin123` |

## Language Policy

| Layer | Language |
|-------|----------|
| User interface | zh-CN first (English fallback) |
| Internal code / API / config / canonical tags | English |
| Core technical docs | English primary |
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

## What Has NOT Been Built

- No anime/photo filtering (Phase 3)
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

## Recommended Next Phase: 3

**Anime Filtering** — automatically detect and optionally skip non-anime images during import:

1. Leverage WDv3 confidence as a proxy (very low confidence = likely not anime)
2. Or introduce a dedicated anime/photo classifier
3. AI tagging infrastructure from Phase 2.3 provides the execution pipeline

## Test Directory

`C:\Users\kyloris\Pictures\AnimeLocalBooruTest` — 17 files (14 valid images already imported).

**Real target:** `C:\Users\kyloris\Pictures\iCloud Photos` — always use dry-run + max_files first.

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
| `docs/tag-localization-zh.md` | Tag localization design (zh-CN) |
| `example.env` | Available environment variables |
