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

### Phase 3.8d-I1 - iCloud / Windows Cloud Files Ingestion Reliability Incident & Hardening

**Goal:** Resolve a foundational ingestion reliability incident before Phase 3.8d execute can resume.

- Phase 3.8d staging copy failed at manifest `row_id=98`, bucket `b02`, with Windows error `388` (`The cloud sync provider failed to perform the operation due to network being unavailable`).
- The failed file showed Windows Cloud Files placeholder evidence (`Offline`, `ReparsePoint`, `SparseFile`, and later `RECALL_ON_DATA_ACCESS`).
- Impact was contained: no DB import, content classification, AI tagging, localization, Entity Resolver, similarity/clustering, app-managed storage mutation, cleanup, delete, reset, drop, or truncate ran.
- Partial staging remains preserved as evidence: `97` files, `340,159,586` bytes, safe label `phase_3_8d_partial_staging`.
- Metadata-only cloud availability audit found `1000` selected files, `97` already copied, `903` not yet copied, and `613` likely cloud placeholder / recall-risk files. Direct copy is blocked by `blocked_requires_hydration_policy`.
- Phase 2.4 solved scan-safety by avoiding unwanted mass downloads and hangs; Phase 3.8d-I1 adds ingestion-availability hardening for controlled copy of selected cloud-backed files.
- Added shared Windows Cloud Files metadata helper, privacy-safe manifest cloud availability audit, structured copy failure classification, opt-in read-probe hooks, dry-run same-bucket backfill planning, and dry-run-only cleanup/resume policy documentation.
- Phase 3.8d execute, Phase 4, and any larger import remain blocked until this incident is reviewed and a recovery path is explicitly approved.

### Phase 3.8d-I2 - Source Ingestion Gate Unification

**Goal:** Ensure cloud/iCloud handling is a shared project foundation rather than isolated script patches.

- Added a shared Source Ingestion Gate for source kinds: `path_source`, `upload_bytes`, `staging_file`, and `app_managed_file`.
- Path-based source workflows must inspect Cloud Files metadata through the gate before content reads/copies.
- Local library scan/preflight, legacy candidate manifest generation, Phase 3.8c candidate preflight, cloud availability audit, and staging copy validation/execute now route source availability checks through the shared gate.
- Staging-to-DB import is classified as `staging_file`: it does not repeat source/iCloud cloud checks, but it requires a passed staging audit artifact before import.
- Upload-bytes routes and app-managed storage reads are documented as outside the source cloud gate.
- Phase 3.8d execute remains blocked until cleanup/resume and controlled read-probe/hydration/backfill recovery are explicitly approved.

### Phase 3.8d-I3 - Recovery Cleanup Dry-run and Hydration Policy

**Goal:** Turn the preserved partial staging incident state into an explicit recovery plan before any Phase 3.8d retry.

- Added a Final Delivery Report Standard to project rules so implementation/review final reports must include PR URL, branch/head SHA, files changed, exact tests/results, validation/dry-run results, local artifacts, reviewer status, safety confirmation, blocked/ready status, and recommended next step in Chinese.
- Replaced the standard automatic reviewer-fix loop with a Reviewer Feedback Handling Policy: CodeX triggers reviewer and reports current-head feedback, but does not modify code from reviewer comments unless the user explicitly authorizes a specific auto-fix loop.
- Added `scripts/plan_phase38d_i3_recovery.py`, originally as a dry-run recovery planner. It inspects the preserved partial staging target, writes privacy-safe public reports, and writes full local details only to ignored `.local_manifests` artifacts.
- Partial staging cleanup dry-run confirms the dedicated target safe label `phase_3_8d_partial_staging` by manifest/filesystem proof: `97` files, `340,159,586` bytes, extension distribution `.jpg=68`, `.png=22`, `.jpeg=6`, `.gif=1`, no source/iCloud/repo/app-storage overlap, and no deletion performed. Staging logs are diagnostic only and are not used for cleanup authorization.
- Controlled read-probe/hydration policy remains opt-in, bounded, and approval-gated; metadata-only audit remains the default and direct `CfHydratePlaceholder` integration is deferred as a future enhancement unless explicitly approved.
- Same-bucket backfill is dry-run-only and may be applied only after bounded hydrate/read-probe failure. The failed row `98` has a same-bucket dry-run replacement candidate while preserving `selected_total=1000`; no manifest replacement is performed.
- Current recovery recommendation is cleanup plus rerun after explicit cleanup approval, because only `97` files were copied and no DB/import/classification/AI/localization downstream state exists.
- Phase 3.8d execute remains blocked until cleanup approval and controlled read-probe/hydration/backfill approval are handled in later stages.

### Phase 3.8d-I4a - Controlled Partial Staging Cleanup Executor Support

**Goal:** Add reviewed cleanup execution support before the approved partial staging cleanup stage.

- Extends `scripts/plan_phase38d_i3_recovery.py` so `--execute-cleanup` is no longer an ad-hoc operation. It requires the exact confirmation phrase `DELETE_PHASE38D_PARTIAL_STAGING` and a fresh passing manifest/filesystem cleanup proof immediately before deleting anything.
- The executor deletes only expected manifest/filesystem-matched regular files under the verified target root. It fails closed for invalid protected roots, protected-root overlap, target/root identity mismatch, unexpected files, missing expected files, size mismatches, path traversal, and symlink/reparse/hard-link escape hazards.
- Staging logs remain diagnostic only and cannot authorize deletion.
- Parent directories are left in place. The executor does not touch source/iCloud files, repo files, app-managed storage, DB data, staging copy, read-probe/hydration, classification, AI tagging, localization, Entity Resolver, or similarity workflows.
- This phase adds support and tests only; real cleanup of the preserved Phase 3.8d partial staging target remains a separate approval stage.
- Phase 3.8d execute remains blocked until actual cleanup, controlled read-probe/hydration, and any approved same-bucket backfill are completed and verified.

### Phase 3.8d-I4b - Actual Partial Staging Cleanup

**Goal:** Execute the reviewed cleanup executor against only the verified Phase 3.8d partial staging target.

- Fresh manifest/filesystem proof passed immediately before deletion: `97` expected files, `340,159,586` bytes, no unexpected files, no missing expected files, no size mismatches, no duplicate/invalid manifest targets, no symlink/reparse/hard-link hazards, and no protected-root overlap.
- Actual cleanup deleted `97` files and `340,159,586` bytes from the dedicated partial staging target. The target directory remains empty with `0` files and `0` bytes.
- DB counts stayed unchanged: `media=995`, `media_tags=53,354`, `ai_jobs=46`, `classification_jobs=14`, `translation_jobs=15`.
- App-managed storage stayed unchanged at `2,557` files and `5,421,382,030` bytes.
- Source/iCloud files were not mutated. Staging copy, read-probe/hydration, DB import, classification, AI tagging, localization, Entity Resolver, and similarity did not run.
- Phase 3.8d execute remains blocked until controlled read-probe/hydration and any approved same-bucket backfill are completed and verified.

### Phase 3.8d-I5 - Controlled Read-probe / Hydration Audit

**Goal:** Determine whether selected iCloud / Windows Cloud Files recall-risk source files can be made readable through bounded source reads before retrying staging copy.

- Added `scripts/run_phase38d_i5_hydration_audit.py`, an operational audit tool with metadata-only baseline, explicit prefix read-probe, full-content read verification, sample-gated full recall verification, privacy-safe public reports, and local ignored per-file details.
- Metadata-only baseline over the selected Phase 3.8c/3.8d manifest found `1,000` selected files, `613` likely cloud placeholder / recall-risk files, and row `98` still in `recall_on_data_access` state.
- Sample gate included row `98` and covered risky temporal buckets: `46` attempted, `44` full-read successes, `2` failures, `121,073,270` bytes read, `114.109` seconds. Failed rows were `98` (`source_row_0098.jpg`, bucket `b02`) and `881` (`source_row_0881.png`, bucket `b15`), both with `read_timeout`.
- Because the sample gate failed, full recall verification for the remaining recall-risk set did not run. A post-sample metadata recheck found `569` remaining likely cloud placeholder / recall-risk files.
- Same-bucket backfill was dry-run-only and not applied: row `98` -> `replacement_row_1029.png`; row `881` -> `replacement_row_1041.jpg`.
- Source content was read for verification only; provider-side hydration/cache may have occurred. No staging copy, staging write, DB import, classification, AI tagging, localization, Entity Resolver, similarity, cleanup/delete, app-managed storage mutation, push main, or merge occurred.
- Phase 3.8d execute remains blocked. Next step is a targeted recovery/backfill decision for failed rows before any staging-copy retry.

### Phase 3.8d-I5b - Targeted Hydration Retry

**Goal:** Retry only the two failed I5 sample rows before deciding whether to backfill or investigate lower-level provider hydration.

- Added `scripts/run_phase38d_i5b_targeted_hydration_retry.py`, a narrow operational audit tool for explicit target rows only. It does not run full recall verification and does not modify the manifest.
- Target rows were `98` (`source_row_0098.jpg`, bucket `b02`) and `881` (`source_row_0881.png`, bucket `b15`).
- Retry policy was more patient but bounded: prefix read `1` byte, prefix timeout `30` seconds, prefix retries `2`, full-read timeout `180` seconds, full-read retries `2`, and `10` seconds between retries. For I5b, full-read rescue still runs when prefix fails.
- Result: `2` attempted, `0` succeeded, `2` failed. Both rows remained `recall_on_data_access`, read `0` bytes, and are not staging-copy-ready. Failure reason distribution: `cloud_hydration_failed=2`.
- Same-bucket backfill remains dry-run-only and was not applied: row `98` -> `replacement_row_1029.png`; row `881` -> `replacement_row_1041.jpg`.
- Source content was read only for approved verification. Provider-side hydration/cache may have occurred, but no source/iCloud write mutation, staging copy, staging write, DB import, classification, AI tagging, localization, Entity Resolver, similarity, cleanup/delete, app-managed storage mutation, push main, or merge occurred.
- Phase 3.8d execute remains blocked. Next decision should be backfill approval, provider/network investigation, lower-level hydration API investigation, or another explicitly approved retry policy.

### Phase 3.8d-I5c - Validate and Apply Same-bucket Backfill

**Goal:** Validate approved same-bucket replacements for the two unrecovered cloud-backed rows and apply backfill only to a local selected-manifest artifact.

- Added `scripts/run_phase38d_i5c_backfill_application.py`, a narrow operational tool that validates replacement rows, writes privacy-safe public reports, writes ignored local details, and creates a local backfilled selected manifest only when all replacements pass full-read verification.
- Replacement validation targeted only row `1029` for failed row `98` and row `1041` for failed row `881`. Both replacements passed controlled full-read verification and are staging-copy-ready.
- Backfill was applied only to `.local_manifests/phase-3.8d-i5c-backfilled-selected-manifest.csv`: active selected total stayed `1000`, bucket distribution stayed unchanged, and rows `98`/`881` were removed from the active set while rows `1029`/`1041` were activated.
- Rows `98` and `881` were recorded in `.local_manifests/phase-3.8d-i5c-deferred-cloud-recovery-ledger.json` with structured reason `cloud_hydration_failed`; this is deferred recovery, not silent skipping.
- I5c records the project-level ingestion observability principle: every future production ingestion run/manifest/job must be able to report a final state for every source item, including success, failure reason, retry, backfill, deferred cloud recovery, DB import, ineligible exclusion, and unresolved status.
- This PR does not add a DB migration or full production ledger. A future Ingestion Run Ledger / Source Item State Ledger is required before full-library import so failed cloud-backed items cannot be mixed with successful imported items or hidden behind aggregate/global counts.
- Source content was read only for replacement validation. Provider-side hydration/cache may have occurred for the replacement reads, but no source/iCloud write mutation, staging copy, staging write, DB import, classification, AI tagging, localization, Entity Resolver, similarity, cleanup/delete, app-managed storage mutation, push main, or merge occurred.
- Phase 3.8d execute remains blocked until the I5c PR is reviewed/merged and a separate staging-copy retry plan is approved.

### Phase 3.8d-I6 - Staging Copy Retry with Backfilled Manifest

**Goal:** Retry staging copy using the I5c backfilled local selected manifest, without DB import or downstream jobs.

- Added `scripts/run_phase38d_i6_staging_copy_retry.py`, a narrow operational runner that validates the I5c backfilled local manifest, verifies the staging target is empty and disjoint from protected roots, runs the existing `stage_pilot_files.py` dry-run gate, and executes copy only after explicit approval.
- The runner now supports an explicit cloud-aware copy policy for production-like iCloud ingestion: recall-risk rows remain blocked by default, but `--allow-cloud-recall-copy` plus `--confirm-cloud-aware-copy COPY_PHASE38D_BACKFILLED_STAGING_WITH_CLOUD_RECALL` lets provider-side hydration/cache happen through source reads while item-level failures are classified and recorded.
- Structural safety failures still block the whole run. Per-item source failures are recorded in an ignored item ledger and excluded from DB import eligibility. The I6 medium pilot failure budget is `max_item_failures=20`, `max_failure_rate=0.05`, `max_consecutive_failures=10`, and `max_same_reason_failures=20`.
- I6 manifest validation passed: active selected total `1000`, rows `98`/`881` absent, replacement rows `1029`/`1041` present, bucket distribution unchanged, duplicate source/target counts `0`, expected bytes `3,109,318,484`.
- Pre-copy staging target check passed: the target label is empty (`0` files / `0` bytes), protected roots are valid/disjoint, and no symlink/reparse/hard-link hazards were found.
- Staging copy dry-run reported `566` metadata-level `cloud_recall_on_data_access` rows; these were treated as cloud-backed recall-risk rows, not proven failures, after explicit cloud-aware copy approval.
- Actual staging copy completed with item-level failures within budget: attempted `1000`, staged `994`, failed `6`, copied `3,063,523,992` bytes, failure rate `0.006`, max consecutive failures `3`, budget exceeded `False`.
- Failed item rows were `799`, `839`, `922`, `970`, `971`, and `972`, all with `cloud_network_unavailable`. They are not eligible for DB import and must be handled by later backfill, targeted retry, or an explicitly approved partial-import strategy.
- Post-copy audit passed for the staged subset: `994` files / `3,063,523,992` bytes, no unexpected files, no missing staged files, no size mismatches, no hazards, rows `1029`/`1041` staged, rows `98`/`881` not staged.
- Full `1000` DB import remains blocked. Any later partial-import plan must consume the I6 item ledger / staged-success set as the source of truth and must not blindly import the full `1000` manifest.
- DB counts stayed unchanged: `media=995`, `media_tags=53,354`, `ai_jobs=46`, `classification_jobs=14`, `translation_jobs=15`.
- No DB import, DB mutation, classification, AI tagging, localization, Entity Resolver, similarity, cleanup/delete, source/iCloud write mutation, app-managed storage mutation, push main, or merge occurred.
- Reports: `docs/reports/phase-3.8d-i6-staging-copy-retry.md` and `docs/reports/phase-3.8d-i6-staging-copy-retry-summary.json`.
- Next decision: approve same-bucket backfill for the 6 failed rows, perform a targeted provider/network retry, or explicitly approve partial-import planning for the `994` staged rows from the item ledger. Full `1000` DB import remains blocked until a complete staged set is produced and separately approved.

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

### Future prerequisite - Ingestion Run Ledger / Source Item State Ledger

**Goal:** Before any full-library import, implement a production-grade per-run ledger that records final state for every source item in each ingestion run, manifest, or job.

- Required states include: succeeded, failed, failure reason, retried, backfilled, deferred for later cloud recovery, imported into DB, excluded as ineligible, and unresolved.
- Required per-item fields include: row id or safe label, source state, staging status, failure reason, bytes copied, `eligible_for_db_import`, deferred/backfilled/unresolved state, and imported media ID if later imported.
- Reports must be scoped to the current run/manifest/job, not only global library totals.
- Failed cloud-backed items must remain visible and must not be mixed with successfully imported items or hidden behind aggregate counts.
- Full-library import must not run until this production ledger exists and can prove which staged-success rows are eligible for DB import.
- This is a future design/implementation phase and is intentionally not implemented by Phase 3.8d-I5c or I6.

### Future prerequisite - Over-selection Buffer for Large Imports

**Goal:** Large imports should select enough candidates to reach the desired successful import size while preserving item-level failure visibility.

- Use `desired_success_count=N` and `candidate_count=N * buffer_ratio`.
- The buffer must account for cloud failures, duplicate targets/sources, unsupported files, non-anime or otherwise ineligible classification results, and user exclusions.
- Buffering is not silent skipping: every failed, excluded, deferred, backfilled, unresolved, and imported item must remain scoped to the current run/manifest/job in the production ledger.
- The buffer design belongs in a future ingestion planning phase before full-library import. It must not bypass staging audit, item-ledger, or DB import approval gates.

### Phase 4 — iCloud Photos Watcher / Scheduled Scan

**Goal:** Eliminate manual scan triggers.

**Blocked by Phase 3.8d-I1/I2/I3/I4/I5/I5b/I5c/I6 and DB-import readiness:** Do not start Phase 4 until cloud availability/hydration/backfill handling for ingestion/staging/copy is reviewed, merged, and the Phase 3.8d import path is explicitly approved. Watcher work must inherit the Source Ingestion Gate; manual mass hydration is not a formal workflow.

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

### Cloud Availability Gate for Ingestion/Staging/Copy

Any workflow that can read or copy from iCloud / Windows Cloud Files source paths must pass the Source Ingestion Gate before content reads:

- `stat()`, `exists()`, file size, and `is_file()` are insufficient for cloud-backed files.
- Windows cloud attributes must be inspected before copy/import.
- Phase 2.4 solved scan safety; staging copy also requires ingestion availability.
- Cloud recall-risk is a risk signal, not a permanent exclusion.
- Default behavior blocks recall-risk rows, but an explicit cloud-aware copy policy may allow recall-risk rows into controlled copy with bounded reporting.
- Manual hydrate is an emergency workaround only, not the formal V.I.O.L.E.T. workflow.
- Structured cloud failure reasons are required.
- DB import must not run after failed or incomplete staging copy.
- Upload-bytes routes and app-managed storage reads do not require the source cloud gate.

Safety gates should make workflows controlled, observable, and recoverable rather than infinite blockers for expected iCloud / Cloud Files states. Structural blockers stop the whole run, including server/DB identity mismatch, unsafe staging target, target escape, protected-root overlap, invalid manifest schema, duplicate target paths, report generation failure, privacy leak, DB/app-storage/source-root confusion, and unexpected DB/app-storage/source mutation.

Per-item failures are recorded, excluded from DB import eligibility, and handled through retry/backfill/deferred recovery when they stay within the approved failure budget. Examples include `cloud_hydration_failed`, `cloud_network_unavailable`, `read_timeout`, `source_missing`, `permission_denied`, `unsupported_extension`, `size_mismatch`, and `unreadable_source`. The current medium pilot failure budget is `max_item_failures=20`, `max_failure_rate=0.05`, `max_consecutive_failures=10`, and `max_same_reason_failures=20`; exceeding it indicates possible systemic provider/network/workflow failure and should stop the run.

### Destructive DB Operation Safety (post-incident, 2026-05-10)

All destructive API endpoints (`reset-e2e-test-data`, `missing-media-cleanup`) are protected by:
1. `VIOLET_ALLOW_DESTRUCTIVE_E2E=1` env flag (HTTP 403 without it)
2. Unique `confirm_phrase` per endpoint
3. `dry_run=true` default
4. `logger.warning(...)` audit log before execution

E2E tests that call destructive endpoints must be gated by `VIOLET_ALLOW_DESTRUCTIVE_E2E=1`. Never run a dev server from a git worktree against the shared production DB for destructive E2E tests.
