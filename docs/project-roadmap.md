# V.I.O.L.E.T. — Project Roadmap

## Project Vision

Build a personal, local anime/illustration image library on top of [Blombooru](https://github.com/mrblomblo/blombooru). The core value is **Danbooru-style tag-based retrieval**, not generic file browsing.

The finished system should:

- Scan a continuously-updating local image directory (e.g. `C:\Users\kyloris\Pictures\iCloud Photos`)
- Reliably import anime/illustration images while skipping duplicates, corrupted files, undownloaded placeholders, and unsupported formats
- Automatically generate high-quality tags via AI (WDv3 / future models)
- Support searching and filtering by tag with full Danbooru syntax
- Cover character, copyright, artist, general, meta, and rating tag namespaces
- Record each tag's origin (AI, manual, booru import), confidence, and lock status
- Allow manual correction, deletion, and locking of tags — manual always wins over AI
- Eventually support tag aliases, tag implications, character/copyright databases, reverse image search, source completion, similar-image detection, and character clustering

---

## Completed Phases

### Phase 0 — Project Bootstrap

**PR:** #1 · **Commit:** `cd69b27`

- Imported Blombooru upstream into the V.I.O.L.E.T. repository
- Verified local dev environment (Python venv + PostgreSQL)
- Confirmed core functionality: upload, tag CRUD, search, thumbnails, scan-media, admin panel, onboarding

### Phase 1 — Local Library Scan MVP

**PR:** #2 · **Commit:** `46dca33`

- `POST /api/admin/scan-local-library` endpoint
- Recursive scan of external directories with copy-mode import
- Windows path + spaces support, `|`-separated multi-path in `.env`
- JSON body `{"paths": [...]}` override
- MD5 hash dedup, per-file error isolation
- Supports `.jpg/.jpeg/.png/.webp/.gif`; skips `.icloud`, zero-byte, symlinks
- Original files are never moved or deleted
- Original path stored in `Media.source` as `file://` URI
- Full documentation in `docs/local-library-scan.md`

### Phase 1.5 — Scan Safety & UX (PR #4)

**Commit:** `5d025aa`

- `dry_run` mode, `max_files` limit, Admin UI for scan
- Safe preview of large directories before real import

### Phase 1.6 — Scan Job System / Progress / History (PR #5)

**Commit:** `ec2a9a0`

- Background scan jobs with progress polling, cancel, history
- `blombooru_scan_jobs` table, stale recovery, path safety

### Phase 2 — Tag Metadata Foundation

Extended `blombooru_media_tags` with provenance tracking:

- Added `source`, `confidence`, `is_locked`, `is_suggestion`, `created_at`, `updated_at` columns
- Tag service (`backend/app/services/tag_service.py`) with helpers for manual, AI, and booru import tags
- Priority rule: manual/locked tags never overwritten by AI
- Suggestions excluded from search and tag counts
- Existing tags backfilled as `manual/1.0/locked/confirmed`
- Media detail API exposes `tag_provenance` dict
- Full documentation in `docs/tag-metadata-foundation.md`

### Phase 2.1 — WDv3 AI Auto Tagging MVP

**Goal:** Manually triggered AI tagging using the WDv3 ONNX tagger.

- AI Tagging Service orchestrates WDv3 predictions → tag provenance writes
- Admin API: model status, single-image tagging, batch tagging with dry-run
- Admin UI: model status display, single/batch tagging controls, results summary
- Dual thresholds: confirmed (≥ confirm), suggestion (≥ suggestion), ignored (< suggestion)
- Category-aware: character threshold (0.65) higher than general (0.35)
- Safety: batch capped, dry-run mode, manual trigger only, no auto-scan integration
- Graceful degradation: app starts when model unavailable

See [AI Tagging Usage Guide](ai-tagging-usage-guide.md) for complete usage instructions.

### Phase 2.1.1 — Documentation & Usage Guide

- Complete AI Tagging Usage Guide with GUI walkthrough and PowerShell examples
- README refresh for V.I.O.L.E.T. (replacing upstream Blombooru README)
- Capability boundaries documentation (what AI can/cannot do)
- Future auto-tagging architecture recommendation
- Manual GUI verification procedures

### Phase 2.2 — AI Tag Review UI

**Goal:** Add suggestion review UI for AI-generated tags.

- Review API: list, confirm, reject, lock, delete, bulk operations
- Admin UI: review panel with filters, table, single/bulk actions, pagination
- Media detail: provenance-aware tag rendering (confirmed vs suggestion)
- Confirm preserves AI source and confidence for provenance tracking
- Manual/locked tag protection in all review operations
- Tag counts updated on confirm (makes tags searchable)

See [AI Tag Review](ai-tag-review.md) for complete documentation.

### Phase 2.2.1 — V.I.O.L.E.T. Rebrand + zh-CN Localization Foundation

**Goal:** Formal project rebrand and zh-CN localization foundation.

- Project renamed to V.I.O.L.E.T. (Visual Image Organizer for Local Evaluation & Tagging)
- Admin UI fully localized to zh-CN: Local Library Scan, AI Auto Tagging, AI Tag Review
- Tag Chinese display: ~80 common Danbooru tags show Chinese names in UI
- Chinese tag search aliases: users can search with Chinese tag names
- Tag translation dictionary: static JSON file, extensible
- Logo integrated into navbar and README
- Added tag localization design document

### Phase 2.2.2 — Dynamic Tag Localization / LLM Translation Cache

**Goal:** Sustainable dynamic Chinese tag localization with DB-backed translations and optional LLM.

- New `blombooru_tag_translations` table for persistent translation storage
- Translation priority: manual/reviewed > static dict > LLM cache > canonical fallback
- Static dictionary seeded into DB on startup (79 tags)
- Optional LLM provider (OpenAI-compatible) for batch tag translation
- LLM disabled by default; API key from `.env` only
- Admin UI section: stats, manual edit, batch LLM, review panel
- Public batch API: `GET /api/tags/translations/batch`
- Search parser DB-backed alias cache with 5-minute refresh
- Frontend batch prefetch for efficient translation loading
- Admin operations immediately invalidate search cache

See [Tag Localization LLM](tag-localization-llm.md) and [Tag Localization zh-CN](tag-localization-zh.md) for documentation.

### Phase 2.2.2a — Auto Tag Localization + Priority Hotfix

**Goal:** Fix priority bug, add automatic translation on new tag creation, verify real LLM.

- Fixed `upsert_translation` to enforce strict source priority (Codex issue)
- Auto-translate new tags via background thread when LLM + auto are enabled
- Enhanced Admin UI: Test LLM, Refresh Stats, detailed LLM status
- Real LLM verified with OpenAI-compatible API
- Added `TAG_TRANSLATION_AUTO_ENABLED` and `TAG_TRANSLATION_AUTO_MAX_ITEMS` config
- Added `httpx` dependency for async LLM API calls

### Phase 2.3 — AI Tagging Jobs + Auto-Tag After Import

**Goal:** Background AI tagging job system with optional auto-tag after import.

- AI tagging job system with background execution, progress tracking, cancel, and history
- New `blombooru_ai_tag_jobs` table for persistent job state
- New `blombooru_scan_job_media` table records imported media IDs per scan job
- Auto-tag after import: scan completion optionally triggers AI tagging job (default OFF)
- `force_suggestions` mode: write all AI tags as suggestions for manual review
- Admin API: create/list/poll/cancel AI tagging jobs, auto-config endpoint
- Admin UI: AI Tagging Jobs section with config display, create job, progress, history
- Tag localization integration: AI jobs schedule auto-translate for newly created tags
- E2E validation: `scripts/e2e_validate_violet_workflow.py` + `docs/e2e-violet-test-100.md`
- Configuration: `AI_AUTO_TAG_AFTER_IMPORT_*` settings (all default OFF)

See [AI Tagging Jobs](ai-tagging-jobs.md) and [E2E Validation Guide](e2e-violet-test-100.md) for documentation.

### Phase 2.3a — Developer E2E Tools + Config Diagnostics

- Fixed `load_dotenv()` to use explicit project-root path with `override=True`
- Added config diagnostics API (`GET /api/admin/dev/config-diagnostics`)
- Added E2E test data reset API + CLI script
- Added Developer Tools UI in Admin Panel (System tab)

### Phase 2.3c — Full Real Browser E2E Acceptance

- Playwright E2E test suite: 26 smoke tests + 5 real workflow tests
- Real browser testing (Edge/Chrome), Chromium for CI
- Fixed 8+ backend/frontend bugs discovered during E2E
- Verified full pipeline: scan → AI tag → auto-localize → search

### Phase 2.3d — Continuous Background Tag Translation

**Goal:** Background worker that automatically and continuously translates all missing tags via LLM.

- New `tag_translation_worker.py` background daemon thread
- Periodic check for missing tags with configurable interval (default 300s)
- Batch LLM translation with daily limit, error limit, backoff
- New `blombooru_tag_translation_jobs` table for job history
- Admin API: worker/status, worker/run-now, worker/pause, worker/resume, worker/jobs
- Admin UI: Background Auto Translation panel with status, controls, job history
- AI job integration: completed AI jobs trigger run-now on the worker
- Config diagnostics updated with all background worker settings
- Playwright tests: 6 smoke + 3 real E2E for worker
- All 1844 tags translated to zh-CN with 0 failures

See [Tag Localization LLM](tag-localization-llm.md) for configuration details.

### Phase 2.3e — Proper Noun Alias Resolver Foundation

**Goal:** Separate proper-noun alias resolution from visual tag translation, with dedicated LLM prompt and trust policy.

- Background worker now skips character/copyright/artist tags (new `TAG_TRANSLATION_BG_CATEGORIES` setting)
- New entity alias resolver service with dedicated LLM prompt (forbids inventing names)
- Trust policy: unreviewed proper-noun LLM aliases excluded from Chinese search cache
- Admin API: entity/status, entity/pending, entity/resolve
- Admin UI: separate Entity Alias Resolver section with status, pending list, resolve controls
- 10 Playwright tests (7 smoke + 3 real E2E)

See [Entity Alias Resolver](entity-alias-resolver.md) for documentation.

---

## Upcoming Phases

### Phase 3 — Anime Filtering

**Goal:** Automatically detect and optionally skip non-anime images during import.

- Leverage WDv3 confidence as a proxy (very low confidence = likely not anime)
- Or introduce a dedicated anime/photo classifier
- Depends on Phase 2.1 (AI inference pipeline must exist first)

### Phase 4 — iCloud Photos Watcher / Scheduled Scan

**Goal:** Eliminate manual scan triggers.

- Filesystem watcher or periodic cron-style scan
- Requires Phase 1.5 safety controls to be in place
- Must handle iCloud sync edge cases (partial downloads, file locks, .icloud placeholders appearing/disappearing)

### Future Ideas (unscheduled)

- Reverse image search (SauceNAO / IQDB integration)
- Source completion (auto-fetch Pixiv/Twitter source URL)
- Similar image / near-duplicate detection (perceptual hashing)
- Character clustering (group images by character across different art styles)
- Batch tag editor in the UI
- Tag statistics dashboard

---

## Development Standards

### Branching & Delivery

Every phase follows this workflow:

1. Create a feature branch from `main`
2. Plan → implement → test locally
3. Verify: all new features work, no existing features broken, no sensitive files staged
4. Commit with conventional commit message (`feat:`, `fix:`, `docs:`, etc.)
5. Push branch, create PR with summary / scope / testing / limitations
6. Squash merge, delete branch
7. Checkout `main`, pull
8. **Stop.** Output delivery report. Do not auto-start the next phase.

### Safety Rules

**Never commit:**
`.env`, `venv/`, `data/`, `media/`, `storage/`, test images, cache files, tokens, passwords, API keys

**Never do without explicit approval:**
- Delete or move original files in external directories
- Full-scan a real iCloud Photos directory without a prior dry-run
- Implement AI tagging + anime filter + watcher + clustering in a single phase
- Large-scale refactors or frontend framework replacements
- Database migrations (must be planned and reviewed first)
