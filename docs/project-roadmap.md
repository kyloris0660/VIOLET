# V.I.O.L.E.T. - Project Roadmap

## Project Vision

Build a personal, local anime/illustration image library on top of [Blombooru](https://github.com/mrblomblo/blombooru). The core value is **Danbooru-style tag-based retrieval**, not generic file browsing.

Canonical GitHub repository: [kyloris0660/VIOLET](https://github.com/kyloris0660/VIOLET). The historical repository name was `AnimeLocalBooru`; local worktrees may still use `C:\Users\kyloris\Documents\AnimeLocalBooru`, and old historical GitHub links may redirect.

The finished system should:

- Scan a continuously-updating local image directory (e.g. `C:\Users\kyloris\Pictures\iCloud Photos`)
- Reliably import anime/illustration images while skipping duplicates, corrupted files, undownloaded placeholders, and unsupported formats
- Automatically generate high-quality tags via AI (WDv3 / future models)
- Support searching and filtering by tag with full Danbooru syntax
- Cover character, copyright, artist, general, meta, and rating tag namespaces
- Record each tag's origin (AI, manual, booru import), confidence, and lock status
- Allow manual correction, deletion, and locking of tags — manual always wins over AI
- Eventually support tag aliases, tag implications, source-first character/copyright/artist enrichment, reverse image search under explicit privacy policy, source completion, similar-image recall, and character clustering

---

## Current Active Roadmap

The active route is now the post-S2 mainline route. The canonical current
sequence lives in `docs/roadmap/current-mainline-roadmap.md`. PR #113 /
Phase 4.7-S2 is merged, and V.I.O.L.E.T. has a real production baseline
library, so development work must keep production DB/storage/source roots and
private ledgers separate from dev/test fixtures.

The accepted short-term sequence is:

1. **PD1-A:** Persist the post-S2 roadmap and add the production/development executable gate foundation.
2. **S2G-1:** GPU AI tagging capability probe and benchmark.
3. **S2G-2/3:** GPU/provider abstraction, provenance, and load control.
4. **R1R:** SourceConcept route redo under GOV3 contracts.
5. **Pixiv/source metadata strategy polish:** settle Pixiv/source metadata reliability before adding providers.
6. **S3A:** Controlled incremental sync pipeline, after S2G and the R1R/Pixiv route decision unless explicitly reprioritized.
7. **S3B:** Opt-in automated incremental sync, disabled by default until approved.
8. **S2F0:** Low-priority desired-media gap audit/support decision report.

SourceConcept/provider/entity work remains a separate track. Provider expansion,
SourceConcept editing, Entity truth work, R2, PX1-B, and Provider-2 remain
blocked while the pipeline fidelity incident is unresolved. This does not block
the production utility route as long as that route does not promote
SourceConcept/Entity truth.

Current accepted state:

- Phase 4.5-SC1 is merged: SourceConcept resolver core, aliases, evidence, links, search-preview rows, run ledger, readiness checks, and no-truth-write validation.
- Phase 4.5-SC2 is merged: read-only SourceConcept search expansion, media-detail chips/grouping, evidence preview, `needs_review` source-layer search behavior, and disabled/no-op promotion preview.
- Phase 4.5-DOC1-R1 is merged: README/handoff/roadmap/test workflow restructuring and guard-debt classification.
- Phase 4.5-SCV1 is merged: read-only current-DB coverage audit, search symmetry check, alias-gap analysis, `needs_review` cluster analysis, redaction proof, and decision matrix.
- Phase 4.5-SCV2-P0 is merged: read-only current-DB media/Pixiv-like/source metadata inventory, AI tag continuity policy, medium expansion target/buffer, E1/PX1/R1/A1 split, ledger schemas, safety gates, and public/private artifact boundary.
- Phase 4.5-SCV2-E1 / PR #102 is merged: medium import plus eligible AI tag completion, ending at 3750 media and 3687/3687 eligible AI tag coverage.
- Phase 4.5-PX1 / PR #103 is merged: bounded Pixiv/gallery-dl metadata extraction selected 500, succeeded 470, recorded 30 unavailable/private/deleted failures, wrote source-layer metadata/observations/assertions only, and found 0 exact duplicate dry-run groups.
- Phase 4.5-SCV2-R1 / PR #104 is merged: PX1 evidence was consumed by SourceConcept triage, execute transactions were explicitly committed and post-commit verified on a fresh connection, mutation proof/public redaction passed, and only allowed SourceConcept resolver tables changed.
- Phase 4.5-SCV2-A1 / PR #105 is merged: read-only post-expansion audit, route-decision evidence, public report/summary, and a generated privacy-safe ChatGPT review pack for independent audit. Its route approval is blocked by INC1 pending R1R full-chain remediation and A1R rerun.
- Phase 4.5-SCV2-INC1 is available on `main` via PR #107: it confirmed `llm_stage_missing_incident`, SC1 used bounded LLM adjudication, and R1 did not.
- PR #108 / Phase 4.5-GOV3 is merged: executable phase contracts and phase gates are now the baseline governance rule before completion, route approval, or safe handoff claims.
- Issue #109 tracks GOV3.1 hardening debt. It does not block plan-only production utility planning, but later route approval, `safe_to_merge`, or high-risk review-pack proof must account for the relevant issue class.
- PR #110 / Phase 4.6-FULLLIB-P0 is merged: plan-only contract mapping for a production utility full-library import, classification, AI tagging, and AI tag reuse track.
- PR #111 / Phase 4.6-FULLLIB-E1a is merged: production full-library runner dry-run proof, no DB write/import, no source/app-storage mutation, no provider/LLM/SourceConcept/Entity execution.
- PR #113 / Phase 4.7-S2 is merged: the production baseline library exists, with controlled baseline import, classification, AI tagging, tag localization, public redaction, and real browser validation already completed.
- PD1-A is current: persist the post-S2 roadmap and add the `production_development_separation_contract_v1` foundation without production writes.
- SourceConcept is source-layer evidence only. It is not Entity truth, not `EntityAlias` truth, not confirmed assignment, and not `media_tags` truth.

Current post-PX1 result:

- Current DB baseline is 3750 total media, 3687 eligible media, and 3687/3687 eligible AI tag coverage.
- DB-derived Pixiv-like media candidates are 2287; PX1 selected a bounded 500 for metadata extraction and persisted 470 successes.
- PX1 source searchable assertions are intentionally `needs_review` with `requires_review=true`; they are not `searchable_active`.
- R1's trusted transition moved SourceConcept counts 4214 -> 6094 total, 355 -> 1078 active, 760 -> 1809 `needs_review`, with 1692 concepts influenced by PX1 evidence. The final current-head execute rerun was idempotent over the committed R1 state and verified 6094 total / 1078 active / 1809 `needs_review` after commit.
- R1 improved source assertion/name/tag connection gaps while increasing total gap signals by 626; A1 should interpret these deltas before any editing or truth bridge.
- Current route is split into two tracks:
  1. Production utility: PD1-A, then S2G-1 and S2G-2/3 before any S3A/S3B production sync automation.
  2. SourceConcept/provider/entity: R1R full SourceConcept pipeline replay/remediation, Pixiv/source metadata strategy polish, then any later R2/Provider-2/Entity bridge decision after refreshed route evidence.
- A1 final route approval remains blocked pending R1R and A1R. The durable review-pack policy lives in `docs/chatgpt-review-pack-policy.md`, but uploading the A1 pack does not approve R2 during this incident.
- Old R1/A1 evidence must not approve R2. R1/A1 should not be destructively rolled back; R1R/A1R should supersede/remediate with contract-shaped outputs.
- PX1-B, DEDUP1, SourceConcept editing, and Entity bridge are not part of the production utility FULLLIB-E1 track.

## Near-Term Route

1. Complete `PD1-A` with roadmap persistence and `production_development_separation_contract_v1` focused tests. No production writes.
2. Start `S2G-1 GPU AI tagging capability probe and benchmark` next unless the operator explicitly reprioritizes.
3. Follow with `S2G-2/3` for provider abstraction, provenance, batch/concurrency/throttle controls, and CPU fallback.
4. Resume `R1R` under `source_concept_full_chain_contract_v1` after S2G. AI proper-noun tags remain weak evidence; no confirmed Entity assignments.
5. Polish Pixiv/source metadata strategy after R1R before introducing new providers.
6. Start `S3A` controlled incremental sync only after S2G and the R1R/Pixiv route decision unless explicitly reprioritized.
7. Keep `S3B` automated sync opt-in and disabled by default until separately approved.
8. Keep `S2F0` desired-media gap audit low priority and audit-only until evidence shows support/backfill is worth implementing.
9. Keep DEDUP1 deferred because PX1 exact duplicate dry-run groups were 0.
10. Keep Entity bridge blocked until SourceConcept gaps/needs_review triage are acceptable and a separate preview/manual-confirmation/audit/rollback design is approved.

Explicit ordering:

- Controlled medium import/AI continuity, PX1, and R1 are complete for this route; A1 is the current audit/decision step.
- Alias resolver improvement still comes before Entity bridge or promotion.
- SourceConcept management/editing is a later source-layer phase, not SCV1 by default.
- Entity bridge must have preview, manual confirmation, audit trail, rollback/supersede behavior, and write guards before any truth-path write.
- Provider/gallery-dl/Pixiv/SauceNAO/Google/source-enrichment runs and any non-4.7 product utility scale-up require separate policy, budget, ledger, and approval.
- Dynamic sync automatic production writes are disabled by default. Manual check-for-updates and pending counts are product behavior; threshold-triggered or unattended production writes require explicit user opt-in and visible config.
- AI tagging and tag localization are one S2 execution chain: baseline import -> AI tagging job -> new tags collected -> `_schedule_localization` -> background worker / auto translate -> `blombooru_tag_translations` -> frontend Chinese display and trusted search aliases.
- General/meta localization may use the background translation worker. Character/copyright/artist proper nouns require manual/static trusted aliases or reviewed Entity Alias Resolver output; unreviewed LLM aliases must not pollute Chinese search.
- Review-pack-required route phases normally use `provisional_pending_chatgpt_pack_audit` until the generated pack is independently audited, but pipeline fidelity incidents override that with `blocked_pending_pipeline_fidelity_remediation` until remediation and rerun evidence exist.

## Current Governance / Development Standards

- Agents may create/update branches and PRs, but must not push `main` or merge PRs.
- Use the established PR body sections for reviewable phase PRs: summary, scope, constraints, implementation, validation, test plan, reviewer status, safety confirmation, and next step.
- GOV-2 is active: use focused executable guards, tests, DB constraints, validation runners, and runtime assertions where practical; avoid repeating long hard rules in every doc.
- GOV3 requires every completion claim to pass an executable phase contract. Contracts are checked with `scripts/check_phase_contract.py --contract <contract_id> --summary <summary.json>`. Documentation alone cannot claim `target_met`, `route_approved`, `full_chain_completed`, or `safe_to_merge`.
- Docs-only stages use `git diff --check`, JSON validation, Python identity if Python is used, and focused doc consistency tests when present. They do not need pytest/E2E/browser/server validation unless they touch code, runtime, or UI.
- UI/runtime changes require real browser validation with a controlled test server and identity preflight.
- Broad provider/source/full-library work must wait for run-ledger discipline and explicit approval.
- Route-decision and large-data audit phases require a privacy-safe ChatGPT review pack unless explicitly waived; see `docs/chatgpt-review-pack-policy.md`.

Detailed historical standards remain below under development standards; older phase reports remain archival.

---

## Phase Archive / Historical Traceability

This section preserves historical context. For the current route, read the active roadmap above and `docs/current-handoff.md` first.

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
- `force_suggestions` mode: write all AI tags as suggestions for targeted correction/review workflows; this must not imply exhaustive manual processing of every suggestion.
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

Note: Phase 3.8d-I1 through I5c entries below preserve historical stage-state snapshots, including temporary "execute blocked" wording from those stages. The current accepted state is superseded by I6, I7, G1, G2, and G3.

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

### Phase 3.8d-I7 - Partial Import and Classification-first Pipeline for 994 Staged Rows

**Goal:** Import only the I6 staged-success rows, then run the classification-first downstream pipeline without importing failed or unstaged rows.

- Added `scripts/run_phase38d_i7_partial_import_classification_first.py`, a ledger-scoped runner that validates the I6 item ledger, generates an ignored 994-row staged-success import manifest, gates DB identity/backup/dry-run, imports only staged-success rows, and records downstream state in a local ignored item ledger.
- I6 item ledger validation passed: `1000` rows total, `994` staged-success import candidates, failed rows `799`, `839`, `922`, `970`, `971`, and `972` excluded, rows `98`/`881` not active candidates, replacement rows `1029`/`1041` present.
- DB import executed under source label `violet:phase3.8d:i7:staged-success`: `994` media rows imported, `0` duplicate-by-hash, `0` import failures. A later resume run detected the already completed import and did not duplicate app-managed writes.
- Classification-first policy completed: `994` processed, `0` failed; distribution `anime=934`, `unknown=33`, `non_anime=27`, `illustration=0`, `unclassified=0`.
- AI tagging ran only for anime + unknown rows: `967` eligible processed, `0` failed, `40,287` confirmed tag associations and `12,058` suggestions added; `27` non-anime rows were excluded from AI tagging.
- Controlled localization ran only for eligible-derived general/meta tags. The initial bounded batch translated `200` with `0` failures, then the PR #64 closeout continuation translated the remaining `370` eligible general/meta candidates with `0` failures; final remaining eligible general/meta missing translations are `0`, while `101` proper-noun candidates remain intentionally skipped.
- PR #64 closeout hardening makes public report privacy leaks fail closed, rebuilds resume media IDs from current DB `Media.source='violet:phase3.8d:i7:staged-success'` rows instead of local validation details, and requires classification resume identity proof for the same imported media ID set.
- Post-import DB/storage validation is now authoritative for I7 completion: missing app-managed originals/thumbnails, non-app-relative DB paths, storage-root escapes, source-label mismatches, DB row mismatches, privacy leaks, or storage probe failures block success.
- Localization continuation is partial-import compatible: it scopes to the actual current DB source-label media set, blocks only when that set is empty, continues to skip proper-noun categories, and marks `TagTranslationJob` failed on translation persistence/accounting exceptions where possible.
- Downstream scope is DB SOURCE_LABEL authoritative after import/resume: classification, AI scope, localization scope, DB/storage validation, and item-ledger downstream status must use current `Media.source='violet:phase3.8d:i7:staged-success'` rows after count/hash coverage is proven against staged-success import candidates. External `duplicate_by_hash` media outside the source label must block coverage rather than silently reducing downstream processing.
- API/admin/browser smoke passed on a controlled local server: media list, content-class filter, media detail, original/thumbnail endpoints, content-class stats, AI tag review, localization stats, gallery load, and media detail page all validated with no 500/traceback/console errors.
- Full `1000` DB import remains blocked. Future work must not import the 6 failed I6 rows unless a separate retry/backfill/deferred recovery decision produces a staged-success set for them.
- Manual validation passed after PR #64 merge. The medium pilot pipeline is accepted at a practical level: `994` staged-success rows imported, `6` I6 failed rows excluded/deferred, classification-first pipeline completed, AI tagging limited to anime/unknown, and eligible general/meta localization completed. A few odd translations are non-blocking and belong to a later proper-noun/entity/character localization strategy.

### Phase 3.8d-G1 - Governance and Manual Validation Workflow Hardening

**Goal:** Persist governance and manual validation workflow so future agents do not depend on chat context.

- Documents artifact and operational script lifecycle policy: production reusable code, reusable validation/safety tools, phase-scoped operational runners, one-off local artifacts, and public reports/handoff/roadmap must be distinguished explicitly.
- Documents reviewer feedback lifecycle handling: fix current phase correctness/safety/truthfulness issues, defer future-reuse/generalization/polish for phase-scoped or one-off code when they do not affect current decisions.
- Strengthens the required `Engineering judgment / operator notes` section so final reports must assess phase boundaries, risks, reviewer findings, artifact lifecycle, prompt quality, and next-step recommendations.
- Adds durable development/blombooru manual validation workflow in `docs/manual-validation.md`. At the time of G1, manual startup required `PYTHONPATH=<repo>\backend` because `run.py` loads `backend.app.main:app` while one backend runtime module still used an `app.*` import; this was superseded by Phase 3.8d-G2.
- This is docs/governance only. It does not add runtime behavior, DB migration, DB import, classification, AI tagging, localization, staging copy, Entity Resolver, similarity/clustering, or Phase 4 work.

### Phase 3.8d-G2 - Startup / Import Path Consistency Hardening

**Goal:** Make development manual validation startup work from repo root with the approved venv Python and `run.py --debug`, without requiring `PYTHONPATH=<repo>\backend`.

- Fixed the startup-critical mixed import in `backend/app/services/source_ingestion_gate.py` by replacing a top-level `app.*` import with a package-relative import.
- Added `tests/test_server_startup_imports.py`, a subprocess import smoke that removes `<repo>\backend` from `PYTHONPATH` and imports both `backend.app.services.source_ingestion_gate` and `backend.app.main`.
- Updated `docs/manual-validation.md`, `docs/test-workflow.md`, and `docs/current-handoff.md` so manual validation no longer documents the backend `PYTHONPATH` workaround as required.
- The broader script/test ecosystem still contains intentional `app.*` imports that rely on explicit script path setup or pytest configuration; those are deferred because they are not in the `run.py` server startup path.
- This hardening does not add DB migration, DB import, classification, AI tagging, localization, staging copy, Entity Resolver, similarity/clustering, or Phase 4 work.

### Phase 3.8d-G3 - Final Handoff, Docs, and Repo Hygiene

**Goal:** Close Phase 3.8d with a small docs/handoff hygiene pass before any Phase 4 planning or Phase 3.9 ingestion-ledger work.

- Current handoff now records the accepted Phase 3.8d medium pilot state: `994` I7 imported/resumed media under `violet:phase3.8d:i7:staged-success`, six I6 failed rows deferred, classification-first pipeline completed, AI tagging limited to anime/unknown, and eligible general/meta localization completed.
- Roadmap now reflects that G1 governance/manual validation workflow and G2 startup/import path consistency are complete.
- README points future agents to `docs/current-handoff.md` as the starting point for current state instead of inferring from old reports.
- Tracked Phase 3.8d scripts were audited as phase-scoped historical runners or safety/validation tools; they are not production orchestrators and must not be rerun for DB import/staging without explicit approval.
- This is docs/handoff hygiene only. It does not add runtime behavior, DB migration, DB import, classification, AI tagging, localization, staging copy, Entity Resolver, similarity/clustering, production ingestion ledger, admin UI rewrite, or Phase 4 work.

### Phase 4.1 - Entity Metadata Foundation

**Goal:** Add the local DB/model/service foundation for entity metadata before manual correction UI, internal candidate generation, external provider pilots, proper-noun localization automation, or similarity/clustering.

- Adds additive entity metadata tables for canonical entities, aliases, external identities, evidence/provenance, media entity candidates, media entity assignments, entity translations, inactive external source policy, provider cache, and negative lookup cache.
- Keeps entity metadata separate from `TagTranslation`; character/work/artist names remain proper nouns and do not enter the general/meta tag localization workflow.
- Keeps entity assignments separate from `media_tags`; tags can become future signals, but confirmed entity assignments require review/provenance and are stored separately.
- Adds a local-only entity metadata service skeleton for normalization, entity/alias/identity/evidence/candidate/assignment operations, listing helpers, and future external-eligibility policy checks.
- Provider/cache tables are placeholders only. External providers default disabled, and Phase 4.1 performs no external network calls, reverse image search, crawler work, or provider API calls.
- Privacy policy is fail-closed by default: `unknown`, `non_anime`, and `illustration` are blocked from future external lookup unless a later explicitly reviewed policy changes that. `anime` is eligible only when an enabled provider policy explicitly allows it.
- No DB import, classification, AI tagging, localization, staging copy, source/iCloud mutation, app-managed storage mutation, Entity Resolver execution, similarity/clustering, or automatic confirmed entity assignment is performed in this phase.
- Current handoff PR link traceability is restored for known recent PRs so future phase entries should use clickable GitHub PR links when PR numbers are known.

### Phase 4.2 - Manual Entity Correction and Review Foundation

**Goal:** Add the smallest useful admin-only foundation for sparse, targeted entity correction and confirmation on top of the Phase 4.1 schema.

- Manual interaction is correction-oriented, not exhaustive review. V.I.O.L.E.T. must not rely on the operator processing thousands of AI/entity suggestions one by one.
- The operator should be able to find/create/correct entities, add aliases, assign existing entities to a media item, correct wrong assignments, and accept/reject a small number of targeted candidates.
- Manual changes become durable signals for future automation: confirmed assignments, rejected candidates with reasons, aliases, translations when explicitly added, and provenance/evidence records.
- Candidate handling remains targeted. Do not build broad queues, bulk auto-confirm, automatic candidate generation, or automatic confirmed writes in this phase.
- No external provider calls, reverse image search, crawlers, DB import, classification, AI tagging, localization, staging copy, source/iCloud mutation, app-managed storage mutation, Entity Resolver execution, or similarity/clustering are in scope.

### Phase 4.3-A - Proper-noun Signal Provenance Audit and Trust Policy

**Goal:** Audit current proper-noun/entity-like tag signals before any candidate generation.

- Read-only audit inspected `1989` media, `105699` media-tag rows, `2131` proper-noun/entity-like rows, and `287` distinct proper-noun/entity-like tags.
- All `287` distinct proper-noun/entity-like tags were sourced only from AI; trusted anchors were `0`.
- Default `T0/T1/T2` candidate-source simulation produced `0` rows. Including `T3` AI confirmed proper-noun tags would expose `1806` rows, and including `T4` AI suggestions would expose `325` rows.
- Durable policy: existing AI-generated character/copyright/artist/proper-noun tags are weak identity evidence only. They may be statistics or future query seeds, but they must not automatically create trusted entities or confirmed assignments.
- General/meta visual tags can be useful visual descriptors, but they are not identity signals.
- No candidate generation, external calls, DB writes, classification, AI tagging, localization, Entity Resolver, similarity/clustering, source/iCloud mutation, or app-managed storage mutation occurred.

### Phase 4.3-B - Source-first Entity Enrichment Policy and Pilot Design

**Goal:** Correct the entity enrichment strategy after Phase 4.3-A and design the first safe provider-backed pilot without implementing providers.

- Source-first / provenance-first entity enrichment supersedes broad internal candidate generation from current AI proper-noun tags.
- Future reliable evidence should prioritize known source URLs, exact external post IDs, imported source metadata, source-backed provider lookups, and only then source-backed candidates.
- AI proper-noun tags remain weak query seeds/statistics. Image/tag similarity and local clustering remain supplementary recall tools, not authoritative identity.
- External provider eligibility is fail-closed: `unknown`, `non_anime`, and unapproved `illustration` are blocked by default; `anime` requires explicit provider policy and run approval.
- Local paths, iCloud paths, filenames, source labels, directory structure, original image bytes, and privacy-sensitive content must not be sent externally by default or appear in public reports.
- Future provider calls require opt-in `ExternalSource` policy, per-run budget, rate limits, retry/backoff, circuit breakers, cache-first behavior, negative cache, redacted audit logs, and aggregate public reports.
- Phase 4.4-A supersedes the exact-source inventory next step for the current library because the accepted working assumption is no usable source URL, no reliable external post ID, and no imported source metadata suitable for exact-source lookup.
- Exact booru/source lookup remains a second-step verifier after no-source source discovery yields a source/post candidate. First pilot writes should remain cache/evidence/candidate-only; no automatic confirmed assignments.
- Phase 3.9 must precede broad provider enrichment, 5k/10k scale, full-library request scheduling, or large cache population.
- No provider API calls, reverse image search, scraping, DB import, classification, AI tagging, localization, staging copy, entity writes, Entity Resolver execution, similarity/clustering, source/iCloud mutation, app-managed storage mutation, production ingestion ledger implementation, or admin UI rewrite occurred.

### Phase R1 - GitHub Repository Rename Sync

**Goal:** Align active repository documentation and local git `origin` with the GitHub repository rename to canonical `kyloris0660/VIOLET`.

- Canonical remote repository is now `https://github.com/kyloris0660/VIOLET`.
- Historical old repository name was `AnimeLocalBooru`; do not recreate that old GitHub repo name because redirects can break.
- Local working directory remains `C:\Users\kyloris\Documents\AnimeLocalBooru`; docs must not infer local folder names from the remote repository name.
- Active clone, issue, and repository references should use the VIOLET URL.
- Historical phase reports and old PR links may remain on `github.com/kyloris0660/AnimeLocalBooru` when rewriting them would be noisy; GitHub rename redirects are expected to handle those archival links.
- No runtime behavior, DB, classification, AI tagging, localization, staging copy, source/iCloud files, app-managed storage, Entity Resolver, similarity/clustering, Phase 4.4 implementation, or provider calls are changed by this infrastructure/docs stage.

### Phase 4.4-A - No-source Source Discovery Pilot Design

**Goal:** Design a safe first source-discovery pilot for the current no-source anime library without executing providers or uploading images.

- The current library is treated as no-source: no usable traceable source URLs, no reliable external post IDs, and no imported source metadata suitable for exact-source lookup.
- Exact-source inventory is skipped as the primary next step. A future implementation may include only a minor sanity count so it does not miss trivial source-shaped fields.
- Recommended first provider category for Phase 4.4-B is one SauceNAO-style reverse image search provider for anime illustration source discovery, contingent on current official API/TOS/rate-limit verification and explicit derived-image input approval.
- `anime` is eligible only under explicit approved provider policy and a small sample. `unknown`, `non_anime`, and unapproved `illustration` are blocked by default.
- Original image upload is blocked by default. A resized/stripped derivative or app thumbnail may be sent only after explicit provider-specific approval.
- Local paths, filenames, source labels, iCloud metadata, directory names, secrets, and raw provider payloads must not be sent or appear in public reports.
- Phase 4.4-B should start with a dry-run planner, cache lookup, privacy eligibility report, deterministic derived-input policy, one provider only, `max_items=25`, `max_requests=25` or `50`, conservative sequential rate limiting, failure budgets, and circuit breakers.
- Future approved writes may include `ExternalSource`, `ProviderCache`, `NegativeLookupCache`, `EntityEvidence`, and optionally `MediaEntityCandidate`. They must not include confirmed `MediaEntityAssignment`, automatic trusted entities, `media_tags`, `TagTranslation`, source/iCloud files, or app storage writes.
- Phase 3.9 is not required before a tightly bounded 25-item pilot, but it is required before larger provider pilots, broad enrichment, repeated source-discovery runs, 5k/10k scale, large cache population, or full-library scheduling.
- Manual seeds and local retrieval remain supplementary validation/recall tools. Whole-image embedding/clustering risks multi-character/style/pose/clothing false positives and must not create automatic confirmed assignments.
- No provider API calls, authenticated calls, scraping, reverse search execution, image/thumbnail upload, DB import, classification, AI tagging, localization, staging copy, entity writes, Entity Resolver execution, similarity/clustering, source/iCloud mutation, app-managed storage mutation, production ingestion ledger implementation, or admin UI rewrite occurred.

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

### Phase 3.1.2c - Server Identity + Unified LLM Fallback + Entity Resolver Hardening (PR #33, merged)

- `GET /api/system/server-identity` endpoint for dev server validation
- `scripts/check_test_server_identity.py` verification script
- Unified LLM architecture: `complete_chat()` / `complete_json()` two-layer API
- Structured error hierarchy: `LLMProviderError` → transport, HTTP status, format, all-failed, batch errors
- Fallback policy: transport + HTTP 408/429/5xx → fallback; HTTP 4xx + invalid JSON → no fallback
- Entity alias resolver uses unified provider path (no direct httpx)
- Entity resolver concurrency protection (asyncio.Lock, HTTP 409)
- Frontend entity resolve UX lifecycle (loading → success/error)

### Phase S1 - Server Lifecycle Guard and Stale Server Prevention

**Goal:** Harden local validation server lifecycle after the 8012 stale test server incident, before returning to Phase 4.4-B0.

- Incident facts: port `8012` stayed `LISTENING` after validation work; identity confirmed a V.I.O.L.E.T. test server (`VIOLET_ENV=test`, `DB=blombooru_test`, `storage_root=C:\Users\kyloris\VioletStorage\test`, identity PID `10292`, listener/reloader PID `39504` invisible in the process table).
- Adds `scripts/audit_active_violet_servers.py` as a reusable read-only safety tool for no-active-server preflight, process-tree inspection, identity reporting, stale classification, and non-zero gates.
- The tool does not stop/kill processes, start servers, write DB data, or call mutation APIs. Stale cleanup remains user-approved manual action.
- Governance now requires recording parent/reloader PID, worker/identity PID, process tree, env, DB, storage root, code root, git SHA, branch, and venv Python for agent-started servers.
- Cleanup must verify the port is no longer `LISTENING`; `run.py --debug` / uvicorn reload requires explicit reloader/worker child handling.
- Phase 4.4-B0 may proceed only after stale server cleanup/no-active-server preflight is clean and the user provides approved sample media IDs.

### Phase 4.4-B0 - Sample-gated Reverse-search Preflight

**Goal:** Implement a user-approved sample-gated reverse-search preflight scaffold without executing providers or uploading images.

- Approved sample media IDs are fixed to `2690`, `2687`, `2670`, `2654`, and `2647`; the runner fails closed if no IDs are provided or any ID outside this set is requested.
- The preflight reads only development/blombooru rows for those IDs, verifies `content_class=anime`, and blocks missing, unknown, non_anime, unapproved illustration, unsafe, or unavailable app-managed media.
- The runner generates a redacted request plan for `provider=saucenao`, `provider_category=saucenao_style_reverse_search`, and `input_kind=derived_resized_image_plan`.
- B0 does not generate derived image files by default; it records local derived-input readiness from app-managed thumbnail availability and keeps original/thumbnail/derived upload flags false.
- Public reports record approved sample IDs, no-active-server preflight, DB/storage identity proof, sample counts, content-class distribution, input policy, redaction proof, request budget, provider policy stub, future write mapping, and explicit no-call/no-upload/no-write safety facts.
- Future live-pilot write mapping is plan-only: `ProviderCache`, `NegativeLookupCache`, `EntityEvidence`, and optional `MediaEntityCandidate`; confirmed `MediaEntityAssignment` remains blocked for the first live pilot.
- B0 proves a live pilot can be considered for this sample only; it does not approve provider execution. Live reverse search remains blocked until provider policy, official API/TOS/rate-limit review, derived-input generation/upload approval, and run approval are explicit.
- Phase 3.9 remains required before larger provider pilots, broad enrichment, repeated source-discovery runs, 5k/10k scale, large cache population, or full-library scheduling.
- No provider API calls, authenticated calls, scraping, reverse search execution, image/thumbnail/derived upload, DB import, classification, AI tagging, localization, staging copy, entity writes, Entity Resolver execution, similarity/clustering, source/iCloud mutation, app-managed storage mutation, production ingestion ledger implementation, or admin UI rewrite occurred.

### Phase 4.4-B1 - One-provider Live Reverse-search Pilot

**Goal:** Implement and run a tiny one-provider reverse-search pilot for the five user-approved anime samples, using derived/resized/stripped image inputs only when all live gates pass.

- Provider selected: `saucenao`, because it best matches no-source anime illustration reverse-search needs among evaluated official/public provider categories. Exact booru APIs remain second-step verifiers after a source/post candidate exists; trace.moe remains screenshot-oriented; IQDB automation was not selected because no stable official API was verified for this workflow.
- Approved media IDs remain fixed to `2690`, `2687`, `2670`, `2654`, and `2647`; the runner fails closed if the explicit requested set differs or includes anything outside this set.
- The runner reuses the B0 sample and privacy gates, requires clean no-active-server preflight, verifies development/blombooru identity, blocks `unknown`, `non_anime`, unapproved `illustration`, missing app-managed media, local paths, filenames, source labels, originals, thumbnail uploads, scraping, browser-session use, and multi-provider expansion.
- Derived input generation is deterministic and metadata-stripped, uses safe generated filenames under `.local_manifests/phase-4.4b1-derived`, and happens only after live gates pass. The first actual B1 run stopped before derived generation because no credential was configured.
- Current result: `credential_required`. `SAUCENAO_API_KEY` was absent locally, so B1 made `0` live requests, generated `0` derived files, uploaded `0` images, wrote `0` DB rows, created `0` evidence/candidate rows, and created `0` confirmed assignments.
- Closeout hardening preserves partial live-run attempted/request/derived counts when a credentialed rerun later stops mid-run, fixes the credential-required rerun command to include no-active-server proof arguments, and defers `--write-db-records` until first live provider behavior is reviewed.
- Public reports: `docs/reports/phase-4.4b1-one-provider-live-reverse-search-pilot.md` and `docs/reports/phase-4.4b1-one-provider-live-reverse-search-pilot-summary.json`. Local details remain ignored under `.local_manifests/phase-4.4b1-live-details.json`.
- A later rerun requires local `SAUCENAO_API_KEY`, explicit operator verification of current SauceNAO API/account limits, the existing five-ID sample gate, and the same derived-only upload approval. The first credentialed behavior-validation rerun should still avoid DB writes. Broad provider scaling still requires Phase 3.9 ledger discipline.

### Phase 4.4-B1 Live Rerun - SauceNAO Results

**Goal:** Execute the approved five-sample SauceNAO live rerun using only derived/resized/stripped inputs and no DB writes.

- PR #76 was merged before execution. `.env` is gitignored and contained `SAUCENAO_API_KEY`; the key was not committed or included in public/local artifacts.
- Fresh no-active-server audit was clean before provider work: `occupied_count=0`, `confirmed_violet_count=0`, `suspected_violet_count=0`.
- Approved media IDs remained fixed to `2690`, `2687`, `2670`, `2654`, and `2647`; all five were found, eligible, and `content_class=anime`.
- The rerun generated five safe derived images from app-managed originals, resized/metadata-stripped them under ignored `.local_manifests/phase-4.4b1-live-rerun-derived`, and uploaded only those derived files.
- SauceNAO accepted all five requests: `requests_attempted=5`, `requests_skipped=0`, `header.status=[0,0,0,0,0]`.
- Quota observations: `short_remaining=[3,2,1,1,1]`, `long_remaining=[99,98,97,96,95]`, `minimum_similarity=[35.63,52.0,37.66,52.0,51.7]`; no short-window quota exhaustion, no daily quota exhaustion, no out-of-searches condition, and provider availability was `available`.
- Result classes: `2690=low_confidence_match`, `2687=high_confidence_match`, `2670=high_confidence_match`, `2654=low_confidence_match`, `2647=low_confidence_match`; aggregate `high_confidence_match=2`, `low_confidence_match=3`.
- No DB writes occurred: no ProviderCache, NegativeLookupCache, EntityEvidence, MediaEntityCandidate, MediaEntityAssignment, Entity, media_tags, TagTranslation, DB import, classification, AI tagging, localization, Entity Resolver, similarity/clustering, source/iCloud mutation, or app-managed storage mutation.
- Public reports: `docs/reports/phase-4.4b1-live-rerun-saucenao-results.md` and `docs/reports/phase-4.4b1-live-rerun-saucenao-results-summary.json`. Local details remain ignored under `.local_manifests/phase-4.4b1-live-rerun-details.json`.
- Subscription is not recommended for the completed five-sample run because quota did not block execution. If scaling is later approved, subscription would likely help quota/throughput rather than improve match quality.

### Phase 4.4-B1V - Manual Validation and SauceNAO Metadata Extraction Audit

**Goal:** Persist user manual validation of the B1 live rerun, audit SauceNAO metadata preservation, and plan a bounded B2 expansion without executing a broad provider run.

- User manual validation accepted both high-confidence SauceNAO results as correct source matches: `2687` and `2670`.
- User manual validation discarded all three low-confidence results as unrelated: `2690`, `2654`, and `2647`; low-confidence SauceNAO matches should be discarded by default in this workflow.
- The PR #77 public report and local normalized details did not show character names, but that was a parser/report preservation gap, not proof that SauceNAO API lacks character metadata.
- A bounded metadata-preservation re-query was performed only for the two manually validated high-confidence IDs (`2687`, `2670`) using the same derived-image privacy rules and no DB writes. Both returned Danbooru `data.characters`, `data.material`, `creator`, source/post IDs, and source URL fields.
- SauceNAO high-confidence exact/near-exact results are promising source-backed evidence candidates, but still cannot create automatic confirmed `MediaEntityAssignment`, automatic character assignment, or trusted `Entity` rows.
- External provider metadata should be preserved in canonical provider form first. It should later flow through the existing localization, tag translation, and entity translation pipelines; do not add a separate SauceNAO translation path.
- B2 planning target is `20-30` anime-only user-approved samples, one provider only, quota-aware sequential scheduling, no originals, no full-library selection, and no DB writes unless a separate persistence design approves them.
- Subscription is not recommended solely for quality; it may be reconsidered for quota/throughput only if B2 confirms high-confidence usefulness and quota becomes the bottleneck.
- Public reports: `docs/reports/phase-4.4b1-manual-validation-and-saucenao-metadata-audit.md` and `docs/reports/phase-4.4b1-manual-validation-and-saucenao-metadata-audit-summary.json`. Local metadata audit artifacts remain ignored under `.local_manifests/phase-4.4b1-metadata-extraction-audit-*`.

### Phase 4.4-C0 - Provider-neutral Evidence Contract

**Goal:** Define a provider-neutral reverse-search evidence/candidate contract and map existing SauceNAO B1/B1V facts into it without mutation.

- Adds internal contract DTOs: `ProviderQuery`, `ProviderRunOutcome`, `SourceMatch`, `ExtractedProviderMetadata`, `EvidencePersistencePlan`, and `PlannedEntityCandidate`.
- Adds a SauceNAO-to-contract mapper that maps validated high-confidence samples `2687` and `2670` to `match_class=exact_or_near_exact`, `evidence_strength=strong`, raw provider artist/work/character metadata, `localization_status=pending`, and C1 evidence/candidate plans when concrete source identifiers are present.
- Maps manually invalid low-confidence samples `2690`, `2654`, and `2647` to `match_class=discarded`, `evidence_strength=discard`, no positive evidence/candidate plan, and optional future negative-cache persistence.
- Adds a non-mutating schema-fit audit: Phase 4.1 tables are `sufficient_with_json_payload` for narrow C1 persistence using `ProviderCache`, `EntityEvidence`, nullable-entity `MediaEntityCandidate`, and optionally `NegativeLookupCache`; first-class match/manual-validation/localization columns remain follow-up design.
- Establishes multi-provider rule: every future provider must map to the same contract and must not introduce a provider-specific DB write path. Provider scores are not directly comparable; normalized `match_class` and `evidence_strength` drive downstream logic.
- Closeout hardening rejects missing/fabricated/placeholder/malformed query hashes, accepts only existing 64-hex sha256 query hashes or `sha256:<64 hex>` as `present_valid`, blocks all positive persistence planning unless provider provenance is ready, requires public-safe source identifier fields before persistence planning, normalizes secret-like and forbidden privacy key variants before public serialization, rejects non-finite numbers before public JSON/report emission, marks non-JSON request shapes invalid instead of crashing, decodes URL-encoded strings before privacy scanning, and makes discard/wrong/unrelated/metadata-not-useful manual signals dominate conflicting keep/correct fields.
- Reduced public summaries may still expose `non_persistable_source_match=true` when the source match itself is useful, but they cannot plan ProviderCache, EntityEvidence, or MediaEntityCandidate rows.
- C1 should consume only persistence-ready plans from local details/raw provider artifacts with valid query hashes, public-safe JSON redacted request shapes, and public-safe source identifiers; reduced public summaries are not enough for provider provenance readiness.
- Localization remains pending: raw provider metadata should later feed existing localization/tag translation/entity translation paths with provenance and overrides; no translation is performed inside the mapper.
- No provider API call, upload, DB write, DB migration, ProviderCache/NegativeLookupCache/EntityEvidence/MediaEntityCandidate/MediaEntityAssignment write, automatic Entity creation, confirmed assignment, media_tags mutation, TagTranslation mutation, localization execution, Entity Resolver, similarity/clustering, source/iCloud mutation, or app-managed storage mutation occurs in C0.
- Public reports: `docs/reports/phase-4.4c0-provider-neutral-evidence-contract.md` and `docs/reports/phase-4.4c0-provider-neutral-evidence-contract-summary.json`.

### Phase 4.4-C1 - Validated High-confidence Evidence Persistence

**Goal:** Persist the first narrow provider-neutral evidence path for the two manually validated high-confidence SauceNAO results only.

- Uses local ignored B1/B1V detail artifacts as the persistence source of truth, not reduced public summaries.
- Writes `ProviderCache` rows for `2687` and `2670` with public-safe redacted provider-neutral payloads, valid query hashes, request shapes, source identifiers, scores, source hosts/post URLs, manual validation status, evidence strength, and raw provider metadata.
- Writes `EntityEvidence` reverse-search rows for `2687` and `2670`, with deterministic `payload_ref` values pointing to the provider cache natural key.
- Writes suggestion-only `MediaEntityCandidate` rows with `entity_id=NULL`, `generator=external`, and `status=suggested` for raw artist/work/character metadata: `7` candidate rows total.
- Creates no trusted `Entity`, no confirmed `MediaEntityAssignment`, no `media_tags`, no `TagTranslation`, no localization execution, no Entity Resolver run, and no similarity/clustering.
- Excludes low-confidence discarded samples `2690`, `2654`, and `2647` from positive persistence; C1 does not write negative cache rows.
- Creates a local ignored `pg_dump -Fc` backup before DB writes and records rollback SQL plus local ignored details.
- Public reports: `docs/reports/phase-4.4c1-validated-evidence-persistence.md` and `docs/reports/phase-4.4c1-validated-evidence-persistence-summary.json`.

### Phase 4.4-C1-HF1 - DB Write Gate Hotfix

**Goal:** Enforce the provider-neutral plan-level DB write gate after PR #81 was merged.

- Enforces `EvidencePersistencePlan.db_write_allowed` in durable provider evidence persistence before any `ProviderCache`, `EntityEvidence`, or `MediaEntityCandidate` write.
- Keeps C0 mapper output non-mutating by default and makes the C1 runner explicitly promote only approved validated `2687` / `2670` plans to `db_write_allowed=True` after all C1 identity, provenance, privacy, no-assignment, and no-Entity gates pass.
- Does not delete or rewrite existing accepted C1 rows by default; expected hotfix DB impact is zero new rows.
- Public reports: `docs/reports/phase-4.4c1-db-write-gate-hotfix.md` and `docs/reports/phase-4.4c1-db-write-gate-hotfix-summary.json`.

### Phase 4.4-D0/D1 - Second Provider Scouting and Conditional Tiny Pilot

**Goal:** Scout a second provider after SauceNAO and run a same-stage tiny five-sample live pilot only if a provider is task-appropriate and passes policy/privacy/API/quota gates.

- Evaluated trace.moe, Google Cloud Vision Web Detection, Danbooru API, Gelbooru API/DAPI, AniList API, IQDB-style services, ASCII2D, TinEye API, and Pixiv-related options.
- Corrected selection logic so trace.moe is not selected merely because it has an easy public upload API; it is classified as an anime screenshot/scene provider, not a current booru-style illustration source-discovery provider.
- Best low-cost official pilot candidate is Google Cloud Vision Web Detection: official REST/base64 `WEB_DETECTION`, web entities, full/partial matching images, pages with matching images, first 1000 units/month free, and Web Detection $3.50/1000 units after the free tier; it still requires Google Cloud credentials/setup and explicit derived-upload approval before any tiny pilot.
- Best dedicated reverse-image API candidate is TinEye API, but it requires a paid search bundle and `x-api-key` before any tiny pilot. IQDB-style services have high conceptual fit but lack confirmed official API/automation policy.
- Prepared local ignored derived files during the aborted pre-correction trace.moe readiness path, but made `0` search/upload requests and uploaded `0` images.
- Did not run a live pilot because no provider was both task-appropriate for illustration/source-backed metadata discovery and currently pilotable under the hard rules.
- Danbooru/Gelbooru remain better future metadata lookup candidates after a known post/source ID exists, not no-source reverse-image providers.
- No DB write, DB migration, ProviderCache/EntityEvidence/MediaEntityCandidate write, confirmed assignment, automatic Entity creation, media_tags mutation, TagTranslation mutation, localization execution, Entity Resolver, similarity/clustering, source/iCloud mutation, app-managed storage mutation, SauceNAO call, original or derived upload, scraping, cookies, browser automation, push to `main`, or merge occurred.
- Public reports: `docs/reports/phase-4.4d0d1-second-provider-scouting-and-tiny-pilot.md` and `docs/reports/phase-4.4d0d1-second-provider-scouting-and-tiny-pilot-summary.json`.

### Phase 4.4-D1G - Google Vision Tiny Pilot and Pixiv Source-Prior Audit

**Goal:** Run the approved five-sample Google Vision Web Detection tiny pilot and separately audit Pixiv-like filename source priors from local DB/app-managed metadata only.

- Google Cloud setup was available, but the current CodeX PowerShell PATH was stale. The phase recovered by discovering Cloud SDK `gcloud.cmd` through a common absolute install path, without permanently modifying PATH, installing software, printing tokens, or printing credential contents.
- Google preflight passed: project/quota project `image-project-497811`, Vision API enabled, ADC token available and redacted, and `GOOGLE_APPLICATION_CREDENTIALS` unset.
- Approved media IDs remained fixed to `2690`, `2687`, `2670`, `2654`, and `2647`; all five passed `content_class=anime` and app-managed media availability gates.
- The live Google Vision pilot generated five safe derived/resized/metadata-stripped JPEG inputs under ignored `.local_manifests`, uploaded only those five derived images, and made five `WEB_DETECTION` requests.
- Google Vision classified 4 of 5 samples as `exact_source_candidate` and 1 of 5 as `visually_similar_only`; it returned useful web/source-like references, but its artist/work/character clues are indirect web entity/page signals rather than structured booru metadata comparable to SauceNAO.
- Pixiv filename source-prior audit scanned only DB/app-managed metadata strings and did not scan source roots, touch iCloud, hydrate cloud files, or read original source files. It found Pixiv-like filename tokens in `555` of `1989` media records (`27.9%`) and `551` distinct candidate work IDs.
- The five approved Google samples had `0` Pixiv-prior hits and are not representative for Pixiv-prior coverage. Broader DB/app-managed metadata, not the five-sample set, is the right basis for judging the Pixiv-prior route.
- Current DB records preserve filename and app-managed basenames well enough to detect many Pixiv-style priors, but there is no dedicated `original_basename` or source-prior ledger field; absence of a token remains a metadata retention limitation.
- No DB write, DB migration, ProviderCache/EntityEvidence/MediaEntityCandidate write, confirmed assignment, automatic Entity creation, media_tags mutation, TagTranslation mutation, localization execution, Entity Resolver, similarity/clustering, source/iCloud mutation, app-managed storage mutation, SauceNAO/TinEye/Pixiv/Danbooru/Gelbooru call, original upload, unapproved sample upload, scraping, cookies, browser automation, push to `main`, or merge occurred.
- Public reports: `docs/reports/phase-4.4d1g-google-vision-pixiv-source-prior.md` and `docs/reports/phase-4.4d1g-google-vision-pixiv-source-prior-summary.json`. Exact Pixiv IDs/page mappings, raw provider details, derived images, and the manual validation sheet remain ignored local artifacts.

### Phase 4.4-P0 - Pixiv Filename Source-Prior Auto-Verification Design

**Goal:** Correct the Pixiv filename route from manual per-image validation toward an automated pre-persistence correspondence gate, without DB writes or Pixiv/provider calls.

- Defined `LocalSourceHint` / `SourcePrior` as a local deterministic hint concept, not `ProviderCache`, not confirmed `EntityEvidence`, not a confirmed assignment, and not an automatic `Entity`.
- Parser policy remains strict: `(?<!\d)(?P<pixiv_work_id>[1-9]\d{5,11})_p(?P<page_index>\d+)(?!\d)`, lowercase `_p`, 6-12 digit positive work IDs, and uppercase variants detected only as possible variants.
- Re-ran read-only extraction over development DB/app-managed metadata only. It confirmed Pixiv-like tokens in `555` of `1989` media records (`27.9%`) and `551` distinct candidate work IDs, with the approved five Google samples still at `0` Pixiv-prior hits.
- Selected a private 30-item feasibility sample covering simple, suffix/timestamp, prefix, non-`p0`, and duplicate-work-ID cases; exact sample mappings remain only in ignored `.local_manifests` artifacts.
- Safe Pixiv reference lookup was `reference_lookup_policy_blocked`: no official, documented, unauthenticated Pixiv metadata/preview route was accepted. The runner made `0` Pixiv/provider requests and downloaded no reference images.
- Designed and unit-tested local pairwise similarity helpers for a future gate: orientation normalization, aspect ratio delta, average hash distance, difference hash distance, and average color distance. Thresholds are design-only until safe reference samples exist.
- Future P1 should persist only `auto_verified_high_confidence` hints after separate DB-write approval and an accepted correspondence route; filename-token-only rows remain untrusted.
- No DB write, DB migration, ProviderCache/EntityEvidence/MediaEntityCandidate write, confirmed assignment, automatic Entity creation, media_tags mutation, TagTranslation mutation, localization execution, Entity Resolver, broad similarity/clustering, source/iCloud mutation, app-managed storage mutation, Pixiv scraping, browser automation, cookies/login, provider call, reference-image download, push to `main`, or merge occurred.
- Public reports: `docs/reports/phase-4.4p0-pixiv-filename-source-prior-auto-verification.md` and `docs/reports/phase-4.4p0-pixiv-filename-source-prior-auto-verification-summary.json`.

### Phase 4.4-P2R - Pixiv Authenticated Metadata Route Scouting and Adapter Design

**Goal:** Stop polishing the unauthenticated public-page / preview route and choose a reliable future Pixiv metadata route based on mature implementations.

- PR #86 proved public Pixiv pages/previews can be reached in a tiny non-mutating pilot, but metadata stayed `preview_only` and the route remained brittle around HTML/previews, redirects, preview hosts, page-index/crop mismatch, and report-truthfulness edge cases.
- Bounded prior-art review inspected gallery-dl, pixivpy, PixivUtil2, Pixiv terms/policies, and robots.txt. No official public artwork metadata API was found in this bounded pass; this is not a legal/TOS conclusion.
- Mature Pixiv metadata implementations use authenticated or tool-mediated routes: gallery-dl supports Pixiv refresh-token auth, `{id}_p{num}` page convention, multi-page metadata, JSON output, request sleep, archive behavior, and no-download/metadata modes; pixivpy exposes structured `illust_detail`; PixivUtil2 confirms AJAX field and page handling but is downloader/cookie/Referer oriented.
- Recommended route: first run a manual gallery-dl JSON metadata import pilot with no Pixiv network inside V.I.O.L.E.T.; if that succeeds, design an external gallery-dl adapter. Do not implement a first-party Pixiv authenticated adapter or pixivpy-style adapter unless the gallery-dl boundary proves insufficient.
- PR #86 should not be merged as the durable Pixiv metadata foundation. It may remain as diagnostic evidence or be closed as superseded after the P2R route design is accepted.
- No DB write, DB migration, LocalSourceHint, ProviderCache, EntityEvidence, MediaEntityCandidate, NegativeLookupCache, confirmed assignment, automatic Entity creation, media_tags mutation, TagTranslation mutation, localization execution, Entity Resolver, similarity/clustering, Pixiv login/cookie/refresh-token use, authenticated request, gallery-dl credentialed run, image download, source/iCloud mutation, app-managed storage mutation, push to `main`, or merge occurred.
- Public reports: `docs/reports/phase-4.4p2r-pixiv-authenticated-metadata-route-design.md` and `docs/reports/phase-4.4p2r-pixiv-authenticated-metadata-route-design-summary.json`.

#### Phase 4.4-P2R-F5 / F6 / F7a / 4.5-SC update

- PR #92 / F5 added the provider-neutral source metadata, source tag, source name, source name registry, alias candidate, evidence, and `SourceSearchableNameAssertion` foundation. This is a source-search layer, not Entity truth.
- PR #93 fixed a local debug startup self-lock before F6. It did not implement source-search UI.
- PR #94 / F6 made F5 source-layer data usable in the normal media workflow: media detail source chips, visual multi-select search, mixed ordinary tag + source-layer search, and a clearer admin Content layout. Existing ordinary tags and existing CHARACTER tag display/search remain intact.
- PR #95 / F7a added the primary-provider-backed source-name candidate extraction path and final validation pack. F7a candidates are source-layer evidence only; they are not Entity truth and are not the full SourceConcept scope.
- Phase 4.5-SC1 / PR #96 is complete. It added the missing source-layer soft linker between raw source signals and future Entity promotion. It aggregates F7a candidates, ordinary media tags, AI/model character tags, `SourceSearchableNameAssertion`, `SourceNameObservation`, `SourceTagObservation`, `SourceNameAliasCandidate`, provider structured fields/cache context, and future provider/manual signals into unconfirmed `SourceConcept` rows with aliases, evidence, links, run ledger, and search-preview rows.
- Phase 4.5-SC1 did not run providers, gallery-dl, Pixiv/SauceNAO/Google enrichment, tag localization batches, background translation, source enrichment, broad scans, imports, image uploads, full search/UI integration, or Entity promotion. Bounded text-only LLM pair adjudication was allowed only when explicitly enabled for resolver validation, capped by call/budget settings, cache-backed, primary-provider-only, and recorded in the validation pack.
- Phase 4.5-SC1 did not create or mutate `Entity`, `EntityAlias`, `EntityEvidence`, `MediaEntityCandidate`, `MediaEntityAssignment`, `LocalSourceHint`, `TagTranslation`, confirmed assignments, or `media_tags`. Manual promotion remains preview/design/disabled until a later explicit Entity bridge phase.
- Phase 4.5-SC2 / PR #98 is complete. It added read-only SourceConcept alias expansion through `SourceConceptSearchIndex`, media-detail SourceConcept grouping/evidence UI, search expansion explanation, `needs_review` source-layer search behavior, and disabled/no-op manual-promotion preview. It preserves normal tag search, preserves F6 user-facing chip behavior through global `q=` search, keeps scoped source filters advanced/debug-only, and remains read-only over Entity truth paths.
- Phase 4.5-DOC1 is the post-SC2 documentation consolidation and executable-guard audit. It should keep long-term docs concise, link to reports instead of copying long rules, and identify hard safety rules that are enforced by code/tests versus still documented-only.
- Phase 4.5-SCV1 / PR #100 is complete. It audited current SourceConcept coverage, alias gaps, search symmetry, and `needs_review` clusters before any Entity bridge.
- Phase 4.5-SCV2-P0 / PR #101 is complete. It established the E1/PX1/R1/A1 route and safety split.
- Phase 4.5-SCV2-E1 / PR #102 is complete. It expanded to 3750 media and restored eligible AI tag coverage to 3687/3687 without provider or SourceConcept resolver work.
- Phase 4.5-PX1 / PR #103 is complete. It produced a bounded Pixiv source metadata batch for R1: 470 metadata successes, 3727 tag observations, 918 name observations/assertions, 3727 metadata evidence rows, all new PX1 assertions review-scoped, and 0 exact duplicate dry-run groups.
- Phase 4.5-SCV2-R1 generated `docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage.md` and `docs/reports/phase-4.5-scv2-r1-post-px1-source-concept-triage-summary.json`: dry-run and execute passed, execute transaction commit/post-commit verification passed, PX1 evidence influenced 1692 SourceConcepts, public redaction passed, and only allowed SourceConcept tables changed.
- Phase 4.5-SCV2-A1 adds `docs/chatgpt-review-pack-policy.md` and generates a read-only post-expansion audit/report/review pack. Its route recommendation is provisional until the user uploads the generated pack to ChatGPT for independent audit.
- Phase 4.5-SCV2-INC1 confirmed a pipeline fidelity incident: SC1 used bounded LLM pair adjudication, R1 did not, and old R1/A1 route evidence cannot approve R2.
- Phase 4.5-GOV3 adds executable pipeline contracts and a command-line checker before R1R/A1R/R2 can proceed.

### Phase GOV-2 - Documentation Alignment and Workflow Weight Reduction

**Goal:** Align active governance docs with the project-level decision that reliability remains high while workflow weight decreases.

- Durable core architecture stays strict: DB schema/migrations, provider-neutral contracts, entity/evidence/candidate/assignment lifecycle, provider-cache/evidence/candidate write semantics, provider upload privacy/budget gates, confirmed assignment policy, source/iCloud/app-managed storage safety, broad/repeated provider run ledgers, and in-scope E2E pass requirements.
- Phase-scoped and one-off tooling stays lightweight: safe and truthful for the current phase, but not polished into generic production frameworks unless explicitly promoted.
- Reviewer closeout is bounded by lifecycle and current-stage impact. Default closeout is 1-2 fix rounds; P1/P2 severity is a signal, not an automatic blocker.
- Findings that only matter for the next DB-writing or broad-scaling phase move into that phase's acceptance criteria instead of keeping non-mutating design PRs open indefinitely.
- Small docs/process updates should be batched unless they remove major contradictions or unblock current work.
- Public reports: `docs/reports/governance-documentation-alignment-and-workflow-weight-reduction.md` and `docs/reports/governance-documentation-alignment-and-workflow-weight-reduction-summary.json`.

## Future Backlog Reference

This reference preserves older backlog items. The active near-term route is the `Near-Term Route` section at the top of this file.

1. `Phase 4.5-SCV1: Expanded SourceConcept validation and coverage audit` should run before any Entity bridge. It should inventory current DB coverage, validate larger current-data samples, identify cross-language alias gaps, check search-expansion symmetry, and decide whether broader import/source-data expansion is needed.
2. The current Nahida / `纳西妲` observation belongs to SCV1-style coverage analysis: `nahida_(genshin_impact)` links to Nahida, while `纳西妲` currently appears as separate Pixiv/source evidence and is not yet linked.
3. A later Entity bridge phase must explicitly design preview, user confirmation, audit trail, rollback/supersede, and write guards before creating `Entity`, aliases, candidates, assignments, `LocalSourceHint`, or `media_tags` relations.
4. Do not merge PR #86 as the durable Pixiv metadata route. Public-page preview probing is diagnostic/fallback only, not the source of reliable Pixiv tags/artist/page metadata.
5. Any new provider/gallery-dl/Pixiv/SauceNAO/Google/source-enrichment/localization run after SC1 requires a separate approved run policy and must not be smuggled into resolver validation. Future broad LLM extraction/classification or localization runs also need separate approval; SC1's approved LLM scope is limited to bounded text-only pair adjudication.
6. Phase 3.9: production Ingestion Run Ledger / Source Item State Ledger, over-selection buffer, and provider/source run ledger discipline before `100+`, repeated, broad, 5k/10k scale, large cache population, or full-library provider scheduling.
7. Exact booru/source lookup only after reverse search, Pixiv source-prior validation, or another approved source-discovery path yields a source/post candidate.
8. Repeat or expand B0-style preflight only with new explicit sample approval; do not auto-select replacements or broaden beyond approved IDs.
9. Six failed rows recovery/backfill decision for I6 rows `799`, `839`, `922`, `970`, `971`, and `972`.
10. Proper noun / entity / character localization strategy after source-backed entity correction and alias foundations are usable.
11. Seed-based local retrieval or clustering only as supplementary recall after source-discovery/source-backed evidence exists; no automatic confirmed assignments.
12. Admin stats/settings UI rewrite remains separate from source concept search/UI work.
13. SourceConcept management/editing remains a later source-layer phase. Do not start it inside DOC1 or SCV1 unless explicitly approved.

### Future prerequisite - Ingestion Run Ledger / Source Item State Ledger

**Goal:** Before any full-library import, implement a production-grade per-run ledger that records final state for every source item in each ingestion run, manifest, or job.

- Required states include: succeeded, failed, failure reason, retried, backfilled, deferred for later cloud recovery, imported into DB, excluded as ineligible, and unresolved.
- Required per-item fields include: row id or safe label, source state, staging status, failure reason, bytes copied, `eligible_for_db_import`, deferred/backfilled/unresolved state, and imported media ID if later imported.
- Reports must be scoped to the current run/manifest/job, not only global library totals.
- Failed cloud-backed items must remain visible and must not be mixed with successfully imported items or hidden behind aggregate counts.
- DB import must consume staged-success / eligible items from the ledger, not a raw selected manifest.
- Full-library import must not run until this production ledger exists and can prove which staged-success rows are eligible for DB import.
- Broad provider enrichment, 5k/10k scale, full-library request scheduling, and large cache population should also wait for this ledger discipline so provider outcomes have per-item final state, failure reason, retry/defer state, and public/private artifact separation.
- This is a future design/implementation phase and is intentionally not implemented by Phase 3.8d-I5c, I6, I7, G1-G3, 4.3-A, or 4.3-B.

### Future prerequisite - Over-selection Buffer for Large Imports

**Goal:** Large imports should select enough candidates to reach the desired successful import size while preserving item-level failure visibility.

- Use `desired_success_count=N` and `candidate_count=N * buffer_ratio`.
- The buffer must account for cloud failures, duplicate targets/sources, unsupported files, non-anime or otherwise ineligible classification results, and user exclusions.
- Buffering is not silent skipping: every failed, excluded, deferred, backfilled, unresolved, and imported item must remain scoped to the current run/manifest/job in the production ledger.
- The buffer design belongs in a future ingestion planning phase before full-library import. It must not bypass staging audit, item-ledger, or DB import approval gates.

### Completed hardening - Import / Startup Path Consistency

**Status:** Completed by Phase 3.8d-G2 before Phase 3.9 / Phase 4 work.

- Development manual validation no longer uses `PYTHONPATH=<repo>\backend`; run from repo root with the approved venv Python and `run.py --debug`.
- Startup-critical backend runtime imports should stay package-relative or otherwise importable through `backend.app.*`.
- Remaining script/test-only `app.*` imports are not part of the server startup path and should only be changed in a separate, reviewed import cleanup phase if future evidence shows value.
- `docs/manual-validation.md` remains the source of truth for development/blombooru manual validation startup.

### Future hardening - Proper Noun / Entity / Character Localization Strategy

**Priority:** Medium after Phase 3.8d acceptance and before broad proper-noun localization.

- A few odd LLM translations from the medium pilot are accepted as non-blocking for Phase 3.8d.
- Character, copyright, artist, and other proper-noun handling should be designed through entity/proper-noun strategy rather than broad visual tag translation.
- Character/entity metadata integration remains separate from Phase 3.8d governance and manual validation closeout.

### Future UI/IA Debt

**Priority:** Lower than ingestion ledger, over-selection buffer, entity metadata/source strategy, and proper-noun/entity localization strategy.

- Admin stats page redesign.
- Admin settings page redesign.
- Admin information architecture cleanup.
- Management panel UX rewrite.

### Explicit Deferrals After Phase 3.8d

- Similarity graph / clustering stays deferred until an entity metadata foundation exists and the library has a larger validated scale, likely around a 5k/10k import decision point.
- Admin UI rewrite stays deferred because it does not block core ingestion safety, entity architecture planning, or production ledger work.
- Rare LLM translation oddities from the medium pilot stay deferred to the proper noun / entity / character localization strategy.

### Phase 4 — iCloud Photos Watcher / Scheduled Scan

**Goal:** Eliminate manual scan triggers.

**Status after Phase 4.1:** Do not implement watcher behavior yet. Phase 4.1 is an entity metadata foundation, not watcher work. Any future watcher/scheduled scan work must inherit the Source Ingestion Gate and should wait for a separate ingestion-ledger/source-item-state decision. Manual mass hydration is not a formal workflow.

- Filesystem watcher or periodic cron-style scan
- Requires Phase 1.5 safety controls to be in place
- Must handle iCloud sync edge cases (partial downloads, file locks, .icloud placeholders appearing/disappearing)

### Future Ideas (unscheduled)

- Reverse image search (SauceNAO / IQDB integration) only after explicit provider policy, privacy approval, image/thumbnail/hash upload approval, cache/audit/rate-limit design, and small opt-in batch approval
- Source completion (for example Pixiv/source URLs) from exact source metadata first; no login/cookie/private APIs or scraping without separate policy review
- Similar image / near-duplicate detection (perceptual hashing) as supplementary recall, not identity truth
- Character clustering (group images by character across different art styles) remains deferred; no automatic confirmed assignments from clustering
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

### Workflow Weight and Phase Granularity (GOV-2)

Reliability remains high, but process weight should stay proportional to the artifact lifecycle and current-stage risk.

- Prefer executable guards, assertions, DB constraints, transaction boundaries, enum states, allowlists/denylists, and focused tests over long prompt-only constraints, repeated docs-only gate additions, or generic frameworks for one-off scripts.
- Classify new scripts/tools/reports/artifacts as durable production code, reusable validation/safety tool, phase-scoped operational runner, one-off local artifact/ignored output, or public report/handoff/roadmap update.
- Durable production code, DB/migration work, provider-neutral contracts, provider-cache/evidence/candidate write semantics, and confirmed-assignment policy remain strict.
- Reusable validation/safety tools are reviewed strictly for their safety contract, but should not be expanded into broad frameworks without evidence of cross-phase need.
- Phase-scoped runners must be safe, privacy-preserving, data-integrity-preserving, and truthful for the current phase; they do not need arbitrary future parameter support unless promoted.
- Default reviewer closeout is 1-2 bounded fix rounds per PR. Continue beyond that only for current-stage data corruption, DB writes executed by the PR, privacy/provider-upload safety, current report truthfulness, entity/media_tags truth pollution, core contract/schema correctness consumed by the PR, or irreversible operation safety.
- Severity labels are signals, not automatic decisions. Lifecycle plus current-stage impact decides whether to fix now or defer.
- Do not split phases unless the split reduces real risk or improves delivery clarity. Small docs-only updates should usually be batched unless they remove major contradictions or unblock current work.

### GitHub PR / Main Protection

Agents may create branches, commit, push, create PRs, and run tests. Agents must NOT merge PRs, push to `main`, force-push `main`, or delete `main`. The user reviews and merges on GitHub.

Default PR lifecycle is a normal open PR. Create a draft PR only when the user/ChatGPT explicitly requests draft, or when the stage is clearly a design draft / not ready for review. Docs-only does not imply draft, and a reviewable plan/design PR may be opened normally. Draft PRs must not become the default way to avoid reviewer or human judgment. Final reports must state whether the PR is draft and why.

**Recommended**: Enable GitHub Branch Protection / Rulesets on `main` to enforce PR-based merges.

### Real Browser Validation (Mandatory)

Every feature phase or UI-affecting change requires real browser validation before delivery (Playwright with system Edge preferred). The delivery report must include a dedicated real-browser validation section with method, browser/Playwright project, URL tested, pages/flows validated, pass/fail result, and skipped items. Docs-only and non-UI governance changes do not require starting a server solely for E2E.

### Chinese Reporting

Final delivery reports and stage summaries must be written in Chinese (zh-CN). Technical identifiers (file paths, branch names, PR URLs, API routes, config keys, commands) remain English.

### Test Report Accuracy

Do not claim "all tests passed" if any test failed. Report exact commands and results. Pre-existing or unrelated failures must be documented with evidence.

### Service / Dev Environment Safety

Never kill arbitrary processes. Only stop identified V.I.O.L.E.T. dev server process trees started by the current task (report PID/port first). Use `scripts/audit_active_violet_servers.py` for no-active-server preflight on `8000,8012-8024`, stale-server diagnosis, and port-free verification. Do not silently choose another port around a stale server. Restrict stop/restart UI to local debug mode only.

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
