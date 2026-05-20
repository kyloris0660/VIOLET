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

### Phase 2.4 — iCloud Large Library Readiness / Safe Ingestion

**Goal:** Make the scan pipeline safe for large iCloud-synced directories on Windows — never trigger mass downloads, never hang on a single file.

- Preflight scan endpoint (`POST /scan-local-library/preflight`): stat-only, no `open()`, completes in seconds on 100K files
- Hydrated-only mode (default ON): skips cloud-only files detected via Windows `GetFileAttributesW` (`FILE_ATTRIBUTE_OFFLINE`, `RECALL_ON_DATA_ACCESS`, `RECALL_ON_OPEN`)
- Per-file timeout via ThreadPoolExecutor: wraps `calculate_file_hash` with configurable timeout (default 30s)
- Extended skip-reason counters: `skipped_cloud_placeholder`, `skipped_zero_byte`, `skipped_timeout`, `skipped_unreadable`, `skipped_hidden`, `skipped_too_large`
- Max file size limit: configurable via `SCAN_MAX_FILE_SIZE_MB` (default 200 MB)
- Config diagnostics extended with server info (PID, Python version, app version, platform) and scan config
- Admin UI: preflight button, hydrated-only checkbox, iCloud safety note, 6 extended stat cards, preflight results display
- 22 unit tests + 7 Playwright E2E tests

See [iCloud Safe Ingestion](icloud-safe-ingestion.md) for documentation.

### Phase 3 — Content Classification Foundation + Evaluation Harness

**Goal:** Build content classification infrastructure (schema, job system, admin UI, search filters) and an evaluation harness to measure classifier accuracy. The heuristic classifier serves as a baseline placeholder — **not a production-ready filter**.

- `ContentClassEnum`: anime, illustration, non_anime, unknown
- 6 new Media columns: content_class, confidence, source, model, locked, reviewed
- Heuristic classifier: counts AI tags above confidence threshold to determine content type
- Classification job system: background jobs with progress, cancel, history
- Inline classification: AI tagging jobs classify after tagging (when enabled)
- Auto-classify after scan: scan completion optionally triggers classification
- Admin UI: stats, config panel, create job, progress, job history (with limitation warning)
- Search filter: `class:anime`, `class:non_anime`, `class:illustration`, `class:unknown`, `class:none`
- Media detail: content class info row with localized labels (zh-CN / en)
- Evaluation harness: `scripts/evaluate_content_classification.py` — imports, tags, classifies, measures accuracy
- Disabled by default; all `CONTENT_CLASSIFICATION_*` settings default OFF

**Evaluation results (heuristic classifier):**

| Dataset | Ground Truth | Total | Result | Metric |
|---------|-------------|-------|--------|--------|
| VioletTest100 | mixed | 145 | 100% anime | distribution only |
| VioletTest100_2 | anime | 81 | 100% anime recall | PASS (≥ 80%) |
| VioletPhase3Eval | non_anime | 39 | 97.4% FP rate | FAIL (≤ 15%) |

**Conclusion:** The heuristic tag-count approach has a 97.4% non-anime false positive rate. The WD tagger generates many tags with high confidence for ANY image type, making simple tag-count thresholds ineffective for non-anime rejection. A model-backed classifier (Phase 3.1) is required before this feature can be used for production filtering or iCloud import gating.

### Phase 3.1 — CLIP Zero-Shot Content Classifier (PR #25)

**Goal:** Replace the heuristic tag-count classifier with a model-backed approach that achieves non-anime FP rate ≤ 10-15%.

- CLIP ViT-B/32 zero-shot classifier via ONNX Runtime (MIT license, Xenova/clip-vit-base-patch32)
- Cosine similarity between image embeddings and pre-computed text prompt centroids
- Margin-based unknown threshold: `CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN=0.005`
- Dual-method support: `CONTENT_CLASSIFICATION_METHOD` switches between `clip` (default) and `heuristic` (legacy)
- Standalone evaluation script: `scripts/evaluate_clip_content_classifier.py` (no DB, no server)
- Pre-computed text embeddings generated offline by `scripts/generate_clip_text_embeddings.py`
- Thread-safe singleton with inference lock; ~350 MB model auto-downloaded from HuggingFace Hub

**Evaluation results (CLIP zero-shot):**

| Dataset | Ground Truth | Key Metric | Result |
|---------|-------------|------------|--------|
| VioletTest100_2 | anime | Anime recall >= 80% | PASS |
| VioletPhase3Eval | non_anime | Non-anime FP rate <= 15% | PASS |

**Key files:** `backend/app/services/clip_classifier.py`, `backend/app/assets/content_classification/clip_prompts.json`, `backend/app/assets/content_classification/clip_text_embeddings.npz`, `scripts/evaluate_clip_content_classifier.py`, `scripts/generate_clip_text_embeddings.py`

See [Content Classification](content-classification.md) for full documentation.

### Phase 3.7 - Tier-1000 Classification Scope Gate

**Goal:** Validate content classification on the imported Tier-1000 set and define the scope gate for future tag-derived workflows.

- Target source label: `violet:tier1000:phase3.5`
- Real validation: 995 media processed, 0 failed
- Distribution: `anime=948`, `unknown=21`, `non_anime=26`, `illustration=0`
- Future tag-derived workflows must include only `anime` and `unknown` media.
- `illustration`, `non_anime`, and `unclassified` media are excluded from future AI tagging candidate selection, tag localization candidate selection, tag statistics, and tag-driven similarity/clustering signals.
- Existing Phase 3.6 AI tag associations on newly classified ineligible media are retained as audit evidence; no cleanup is performed without a later approved cleanup plan.
- Phase 4, similarity/clustering, Entity Resolver, and tag cleanup remain out of scope.

### Phase 3.8b - Classification-First E2E Workflow Foundation

**Goal:** Formalize the classification-first medium E2E workflow as reusable dry-run contracts before any new execute pilot.

- Reusable workflow service helpers define scope, stage contracts, eligible/ineligible content-class policy, localization candidate policy, mutation snapshots, legacy contamination audit, and privacy-safe reporting.
- Thin CLI wrapper `scripts/plan_classification_first_e2e.py` is dry-run only; `--execute` is rejected until a later approved phase.
- Formal order is encoded as: candidate manifest / candidate selection -> staging copy -> pre-import audit -> DB import -> content classification -> eligible media selection (`anime` + `unknown`) -> AI tagging only eligible media -> localization only eligible-derived `general`/`meta` tags -> post-run validation -> browser/API smoke -> report.
- `NULL content_class` is not silently eligible. Dry-run reports NULL counts; future execute must fail closed unless an approved earlier step explicitly converts NULL to `unknown`.
- Current Phase 3.5 source-label dry-run baseline: target `995`, eligible `969`, ineligible `26`, legacy ineligible AI associations `771`, and no mutation deltas.
- Public dry-run reports: `docs/reports/phase-3.8b-classification-first-e2e-dry-run.md` and `docs/reports/phase-3.8b-classification-first-e2e-dry-run-summary.json`.
- Real import/copy/classification/AI/localization execution, full 5k+ run, legacy tag cleanup, Entity Resolver, and similarity/clustering remain deferred.

### Phase 3.8c - Medium Pilot Preflight + Classification-First E2E Dry-run

**Goal:** Prepare the next +1000 medium pilot with temporal-diverse candidate selection and formal dry-run/no-mutation proof before any guarded execute stage.

- Added `scripts/plan_phase38c_medium_pilot_preflight.py`, a dry-run-only preflight planner. `--execute` is rejected; real execution is deferred to Phase 3.8d after approval.
- Candidate discovery is source read-only and uses filesystem modified time as the time signal. Known-time candidates are split into 16 quantile temporal buckets and sampled across all buckets.
- Result: source inventory `38,356`, eligible not-yet-selected candidate pool `33,032`, selected `1,000`, excluded/not-selected `37,356`, timestamp_unknown `0`.
- Temporal distribution: `b01-b08=63` each and `b09-b16=62` each; this avoids directory-order, newest-only, oldest-only, and contiguous-window selection.
- Selected extension distribution: `.jpg=816`, `.png=173`, `.jpeg=10`, `.gif=1`; approximate future copy size `3,112,402,513` bytes.
- Planned scale: current DB media count `995`; expected post-execute count around `1,995` before duplicate/import failures.
- No-mutation proof passed for DB, app storage originals/thumbnails, source tree, and planned staging target. Public reports are privacy-safe; the full-path manifest remains local and gitignored at `.local_manifests/phase-3.8c-medium-candidate-manifest.csv`.
- Real import/copy/classification/AI/localization execution, Entity Resolver, similarity/clustering, cleanup, delete, reset, drop, and truncate remain forbidden in Phase 3.8c.

### Phase 3.1.1a — Environment / DB / Storage Safety Foundation

**Goal:** Harden environment, database, and storage separation to prevent worktree/DB mismatch incidents like the 2026-05-10 data loss.

- `VIOLET_ENV` environment variable: `development|test|production` with fail-closed validation
- `CODE_ROOT` / `STORAGE_ROOT` separation: storage paths derive from `STORAGE_ROOT` (configurable via `VIOLET_STORAGE_ROOT`), not project root
- Test DB fail-closed: `VIOLET_ENV=test` requires explicit test DB name or `TEST_DATABASE_URL`
- `assert_test_db()` database helper for test fixture guards
- 9-condition destructive operation gate with production hard refusal
- `_resolve_stored_media_path()` uses `STORAGE_ROOT` instead of `BASE_DIR`
- Startup logging: env, version, code root, storage root, DB name
- 14 unit tests in `tests/test_env_safety.py`
- 5 env template files + `scripts/setup_test_db.py`

### Phase 3.1.1b — Fixture-Based Test Workflow Foundation

**Goal:** Establish a reproducible, fixture-based E2E test workflow with isolated test database and test storage, enabling safe end-to-end validation without touching the production database.

- Enhanced `setup_test_db.py` with `--migrate` flag for full schema initialization on test DB
- Read-only `inspect_test_fixture.py` for fixture validation and file counting
- Shared pytest fixtures (`conftest.py`): `reload_settings`, `fixture_path`, `fixture_counts`
- 10 fixture validation tests (read-only, never modifies fixture files)
- 12 destructive gate + storage containment tests (non-destructive verification)
- 5 fixture import Playwright E2E tests (preflight, dry-run, real import, idempotency)
- 4 gallery browse Playwright E2E tests (grid, API, detail, thumbnail)
- 6 config diagnostics Playwright E2E tests (env, DB, storage, gate, secrets, server)
- All Playwright E2E tests gated by `VIOLET_RUN_REAL_E2E=1`; fixture tests gated by `VIOLET_TEST_FIXTURE_PATH`
- Test workflow documentation in `docs/test-workflow.md`

See [Test Workflow](test-workflow.md) for documentation.

### Fix: Storage Root Containment with Path Semantics (PR #27)

Hardened storage path containment to use `PurePath.is_relative_to()` instead of string `startswith()`, preventing edge cases with `../` traversal, absolute paths, UNC paths, and empty paths.

### Phase 3.1.1c — Full Pipeline Smoke Validation (PR #29)

Local full pipeline smoke validation helper (`scripts/smoke_validate_pipeline.py`) for verifying the complete import → tag → classify → search workflow against the isolated test environment.

### Fix: Harden Unicode Scan Import Failure Handling (PR #30)

Fixed crash during scan import when files with certain Unicode characters in their paths failed to hash. Files that fail to hash are now counted as `failed` rather than crashing the scan job.

### Phase 3.1.2a — Admin UI Closeout (PR #31, merged)

- Documentation closeout for PR #27-#30
- Full admin UI audit (all sections, HTML→JS→backend)
- AI tagging UI consolidation (old direct section → Developer Tools as legacy)
- Admin navigation improvements (section quick nav, collapsible groups)
- i18n fixes (~60 new locale keys)
- Locale key consistency across all 4 locale files
- Dark Violet theme, logo/favicon

### Phase 3.1.2b — Gallery Content-Class Filter (PR #32, merged)

- Gallery sidebar content-class filter (5 modes)
- Backend `content_class` param on `GET /api/media/`

### Phase 3.1.2c — Server Identity + Unified LLM Fallback + Entity Resolver Hardening (in progress)

- `GET /api/system/server-identity` endpoint for dev server validation
- `scripts/check_test_server_identity.py` verification script
- Unified LLM architecture: `complete_chat()` / `complete_json()` two-layer API
- Structured error hierarchy: `LLMProviderError` → transport, HTTP status, format, all-failed, batch errors
- Fallback policy: transport + HTTP 408/429/5xx → fallback; HTTP 4xx + invalid JSON → no fallback
- Entity alias resolver uses unified provider path (no direct httpx)
- Entity resolver concurrency protection (asyncio.Lock, HTTP 409)
- Frontend entity resolve UX lifecycle (loading → success/error)

## Upcoming Phases

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
6. **User manually reviews and merges** (squash merge, delete branch)
7. Checkout `main`, pull
8. **Stop.** Output delivery report. Do not auto-start the next phase.

### GitHub PR / Main Protection

Agents may create branches, commit, push, create PRs, and run tests. Agents must NOT merge PRs, push to `main`, force-push `main`, or delete `main`. The user reviews and merges on GitHub.

**Recommended**: Enable GitHub Branch Protection / Rulesets on `main` to enforce PR-based merges.

### Real Browser Validation (Mandatory)

Every feature phase or UI-affecting change requires real browser validation before delivery (Playwright with system Edge preferred). The delivery report must include a **真实浏览器验收** section with: 验收方式, browser/Playwright project, URL tested, pages/flows validated, pass/fail, skipped items.

### Chinese Reporting

Final delivery reports and stage summaries must be written in Chinese (zh-CN). Technical identifiers (file paths, branch names, PR URLs, API routes, config keys, commands) remain English.

### Test Report Accuracy

Do not claim "all tests passed" if any test failed. Report exact commands and results. Pre-existing or unrelated failures must be documented with evidence.

### Service / Dev Environment Safety

Never kill arbitrary processes. Only stop identified V.I.O.L.E.T. dev server processes (report PID/port first). Restrict stop/restart UI to local debug mode only.

### Phase Plan Approval

For every new major development phase or substantial feature scope, the agent must:

1. **Produce an implementation plan first** — covering scope, key design decisions, new files/tables, and testing approach.
2. **Wait for explicit user approval** before making substantial code changes.
3. Bug fixes and small review-comment fixes may proceed without a separate plan.
4. Major stage-level design changes (new classifiers, new models, new DB schemas, evaluation frameworks) require user-approved plan.

### Safety Rules

**Never commit:**
`.env`, `venv/`, `data/`, `media/`, `storage/`, test images, cache files, tokens, passwords, API keys

**Never do without explicit approval:**
- Delete or move original files in external directories
- Full-scan a real iCloud Photos directory without a prior dry-run
- Implement AI tagging + anime filter + watcher + clustering in a single phase
- Large-scale refactors or frontend framework replacements
- Database migrations (must be planned and reviewed first)

### Destructive DB Operation Safety (post-incident, 2026-05-10)

All destructive API endpoints (`reset-e2e-test-data`, `missing-media-cleanup`) are protected by:
1. `VIOLET_ALLOW_DESTRUCTIVE_E2E=1` env flag (HTTP 403 without it)
2. Unique `confirm_phrase` per endpoint
3. `dry_run=true` default
4. `logger.warning(...)` audit log before execution

E2E tests that call destructive endpoints must be gated by `VIOLET_ALLOW_DESTRUCTIVE_E2E=1`. Never run a dev server from a git worktree against the shared production DB for destructive E2E tests.
