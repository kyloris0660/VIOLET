# Phase 4.6-FULLLIB-P0 Production Full-Library Import and AI Tagging Plan

## 1. Summary

This is a plan-only / contract-mapping phase for the production utility track: full-library inventory, safe production import, content classification, AI tagging, and reusable local search/browse value.

No full import, DB write, classification run, AI tagging run, provider call, LLM call, SourceConcept resolver run, Entity truth mutation, `media_tags` mutation, source metadata mutation, source/iCloud mutation, app-managed storage mutation, server start, or browser validation was performed by this P0 phase.

Recommended next implementation phase: `FULLLIB-E1`, after this P0 plan is approved. `FULLLIB-E1` must stay inside the production utility track and must not run provider/LLM/SourceConcept/Entity work.

## 2. Current repository baseline

- `main` includes GOV3: merge commit `6615053` merges PR #108.
- `docs/reports/phase-4.5-gov3-executable-pipeline-contracts-summary.json` exists and records the executable phase contract baseline.
- Issue #109 is open as GOV3.1 hardening debt. It is a valid follow-up, but it does not block this plan-only phase.
- Any later phase that uses route approval, `safe_to_merge`, or high-risk review-pack proof must account for #109 or close the relevant issue class first.
- Medium-scale DB baseline from prior accepted phases remains: `3750` total media, `3687` eligible media, and `3687/3687` eligible AI tag coverage.
- PR #102 / E1 proved that app-managed import plus classification plus AI tagging can produce real library value without Pixiv/provider/SourceConcept/Entity work.
- PR #108 / GOV3 makes executable phase contracts the baseline governance rule for future phases.

## 3. Why production utility track is separate from SourceConcept/provider track

The production utility track optimizes for a usable local image library: import, classification, AI tags, thumbnails, and tag search. Its allowed truth surface is `Media`, `Tag`, `media_tags` AI/manual provenance, content class fields, thumbnails, and app-managed originals.

The SourceConcept/provider/entity track optimizes for source metadata, source-layer concept evidence, bounded LLM adjudication, route decisions, provider policy, and future Entity bridge design. It remains blocked for route approval by the INC1 incident until R1R and A1R produce contract-shaped outputs.

Keeping the tracks separate protects both goals:

- Production utility can proceed without pretending that SourceConcept evidence is truth.
- SourceConcept remediation can happen later without blocking already useful import/search/AI tagging value.
- Provider privacy, budgets, source metadata, and Entity assignment policy stay out of FULLLIB-E1.
- AI proper-noun tags remain weak evidence/statistics/query seeds only, not Entity truth or confirmed assignment.

## 4. GOV3 contract applicability

GOV3 is usable as the baseline governance rule for future phases. FULLLIB-E1 must declare and pass executable contracts before claiming completion, target met, or safe handoff.

Required contract mapping for FULLLIB-E1:

| Contract | FULLLIB-E1 role |
|---|---|
| `python_env_contract_v1` | Verify the approved repo-local venv Python before runtime/script/test execution. Public summaries must not expose the full local executable path. |
| `postgres_db_contract_v1` | Prove the runner and app resolve the intended production DB, without recording passwords or full DB URLs. |
| `media_import_contract_v1` | Require source-root safety, staging/app-storage safety, import ledger proof, media before/after counts, duplicate/path leak proof, mutation proof, and rollback/recovery notes. |
| `classification_contract_v1` | Require a positive eligible denominator, job status accounting, content-class before/after distribution, and mutation proof. |
| `ai_tagging_contract_v1` | Require eligible media denominator, AI tag coverage before/after, model provenance, job accounting, manual/truth overwrite proof, mutation proof, and public redaction proof. |
| `mutation_safety_contract_v1` | Require table allowlist/denylist, before/after fingerprints, forbidden table stability, and fail-closed behavior. |
| `artifact_lifecycle_contract_v1` | Classify runner, private ledgers, public report, public summary, and optional review pack; prove private artifacts are not committed. |
| `public_redaction_contract_v1` | Scan public Markdown/JSON keys and values for local paths, filenames, source roots, URLs, secrets, and private provenance. |

Recommended / conditional:

- `review_pack_contract_v1` should be required if FULLLIB-E1 makes a full-library readiness or route-unlocking claim, or if the project owner wants independent sample audit of ledgers. If waived, the waiver must be explicit in the E1 summary.
- `destructive_operation_contract_v1` is not applicable because FULLLIB-E1 forbids cleanup/delete/reset/drop/truncate. If rollback ever requires deletion of production DB/app-storage rows or files, stop and create a separate destructive-operation plan.

Contract extension assessment:

- Existing GOV3 contracts are usable for FULLLIB-E1.
- No P0 contract extension is required.
- Before execution, the FULLLIB-E1 runner/summary must align its field names with the existing contract-required paths, especially `source_root_safety_proof`, `staging_root_safety_proof`, `import_ledger`, `media_counts`, `duplicate_path_leak_proof`, `eligible_denominator`, `eligible_media_denominator`, `manual_truth_overwrite_proof`, and `mutation_proof`.
- A future contract extension may make AI tag export/reuse proof first-class. This does not block FULLLIB-E1 if E1 records export/reuse ledgers as private artifacts and includes model/fingerprint provenance in the public summary.

## 5. Production DB vs development DB separation

Recommended separation:

| Purpose | Proposed DB | Environment | Storage root policy |
|---|---|---|---|
| Production utility library | `violet_library_prod` | `VIOLET_ENV=production` | Dedicated production `VIOLET_STORAGE_ROOT`; never repo root; never test storage. |
| Development / experimental work | current `blombooru` initially, optionally renamed later to `violet_dev` | `VIOLET_ENV=development` | Existing dev storage or explicit dev storage. |
| Automated tests | `blombooru_test` or test-specific DB names | `VIOLET_ENV=test` | Dedicated test storage from the test env script. |

Rationale:

- The current `blombooru` database already contains medium import data plus SourceConcept/provider experimental history. It should remain development/experimental until a separate migration/copy decision is approved.
- Production utility data should be isolated from SourceConcept/provider experiments so broad provider or Entity work cannot accidentally mutate production library truth.
- Production DB creation should be preceded by a backup/recovery plan and schema initialization/migration proof.

Required production profile:

- Explicit `VIOLET_ENV=production`.
- Explicit production DB name `violet_library_prod`.
- Explicit production `VIOLET_STORAGE_ROOT`.
- No `TEST_DATABASE_URL`.
- DB identity preflight must prove the connected database is `violet_library_prod`, host is expected, password value is not recorded, and app/runner DB URL resolution is equivalent.
- Storage identity preflight must prove app-managed original, thumbnail, cache, and data directories are under the production storage root and do not overlap any source root.

Preventing accidental experimental work against production:

- SourceConcept/provider/R1R/A1R/R2 runners should hard-block `VIOLET_ENV=production` and `POSTGRES_DB=violet_library_prod` unless a future approved production SourceConcept policy exists.
- FULLLIB-E1 should hard-block provider/LLM/SourceConcept/Entity stage flags in its summary and mutation proof.
- Provider credentials, gallery-dl config, reverse-search keys, and LLM config are not needed for FULLLIB-E1 and should be disabled/unset in the production utility execution environment.

## 6. Source root read-only policy

Source roots are protected inputs. FULLLIB-E1 may stat, inspect Cloud Files metadata, hash/read/copy only after gate approval, and record private source references in private ledgers. It must never write, move, delete, rename, hydrate broadly, tag, or otherwise mutate source roots.

Public artifacts must not expose source roots, local paths, filenames, `file://` URIs, source labels, or original image bytes.

Cloud recall-risk is a per-item state, not a permanent exclusion. Default behavior blocks recall-risk rows and records them for retry/backfill. Any cloud-aware copy/hydration policy must be explicit, bounded, and separately approved before it can read recall-risk content.

## 7. App-managed storage / thumbnail / cache policy

FULLLIB-E1 may write only new imported originals and thumbnails inside the dedicated production app-managed storage root. It may update DB paths to those app-managed files.

Allowed storage behavior:

- Create production storage directories if the production profile explicitly points there.
- Copy selected source files to a temp path inside app-managed originals, then atomically rename to final path.
- Generate thumbnails only for newly imported media.
- Remove only temp files and thumbnails/originals created by the current item when that item fails before DB commit.

Forbidden storage behavior:

- No regenerate-all-thumbnails.
- No orphan cleanup.
- No cache cleanup.
- No deletion of existing app-managed production originals or thumbnails.
- No writing under source roots, iCloud roots, test storage, or repo-local accidental storage.

## 8. Full-library inventory dry-run plan

FULLLIB-E1 must start with a read-only inventory dry-run before any production DB or app-storage write.

Inventory dry-run steps:

1. Verify Python identity and production DB/storage identity.
2. Enumerate configured source roots with stat-only metadata first.
3. For supported extensions, assign stable private candidate IDs and public safe labels.
4. Evaluate `SourceIngestionGate.evaluate_path_source()` with hydration disabled.
5. Record Cloud Files state and defer recall-risk rows by structured reason.
6. Record unsupported extensions, zero-byte files, too-large files, path escapes, stat failures, and source-missing rows.
7. For gate-allowed candidates, run bounded hash reads with timeout.
8. Build duplicate indexes from the production DB using existing `Media.hash` and optional private filename/size heuristics.
9. Produce private inventory and duplicate ledgers plus public aggregate counts.
10. Stop for review if structural blockers or failure-budget signals appear.

Dry-run must not write DB, app storage, source roots, provider caches, SourceConcept tables, `media_tags`, or thumbnails.

## 9. Batch import plan

FULLLIB-E1 should import in bounded batches, not as one uninterruptible full-library operation.

Recommended execution shape:

- Use a new FULLLIB runner based on the E1 runner pattern, not the medium E1 runner unchanged.
- Dry-run first with the same candidate selection and duplicate logic.
- Use a run ID and batch IDs.
- Import only gate-allowed, deduplicated, supported, readable candidates.
- Copy temp-first inside production app-managed storage.
- Verify copied hash before DB insert.
- Generate thumbnail for the copied file.
- Insert `Media` with app-managed relative path and safe production source label, not `file://<source path>`.
- Commit per item or small transaction batch only after file and DB state are coherent.
- Record per-item final state in private ledger.
- Stop if structural blockers or failure budget are exceeded.

The current medium E1 runner directly inserted `Media` and wrote private ledgers. That pattern is useful, but FULLLIB-E1 must replace hard-coded phase branch, target counts, DB name, output names, and medium-only assumptions.

## 10. Classification plan

Classification should run only after a batch imports successfully and only for newly imported media, unless an approved production backfill explicitly expands scope.

Policy:

- Use existing classification job service.
- Use offline/cached model preflight when CLIP is required.
- Record `ClassificationJob` rows and per-media classification ledger.
- Treat `anime` and `unknown` as eligible for AI tagging; treat `non_anime` and unsupported classes as ineligible for AI tagging.
- Do not mutate source or app storage during classification.
- Do not override locked/reviewed classifications unless a separate reclassification policy is approved.

## 11. AI tagging plan

AI tagging should run only for classification-eligible media and should prefer reuse before expensive inference when a compatible fingerprint export exists.

Policy:

- Use the local WD tagger only.
- No network model download during production execution; model availability must be preflighted offline.
- Disable localization side effects: `AI_TAGGING_AUTO_LOCALIZATION=false`, `TAG_TRANSLATION_BACKGROUND_ENABLED=false`, `TAG_TRANSLATION_AUTO_ENABLED=false`, and `TAG_TRANSLATION_LLM_ENABLED=false`.
- Use existing AI tagging job service in bounded chunks.
- Use `only_without_ai_tags=true`.
- Preserve manual/locked tag priority via `add_ai_tag_to_media`.
- Record model name, thresholds, tagger/code version, job IDs, failures, and coverage.
- AI tags remain tag provenance/signal and search utility, not Entity truth or confirmed identity assignments.

## 12. AI tag reuse/export plan

AI tag reuse must be keyed by stable media fingerprints, not database `media_id`.

Recommended private export format: JSONL, optionally compressed, local ignored artifact.

Per media record:

- `content_sha256` as primary key.
- Existing `md5` / `Media.hash` for compatibility.
- `file_size`.
- `width`, `height`, `duration`, `mime_type`, `file_type`.
- `model_name`.
- model repo/version or cache identity when available.
- tagger implementation/version and git SHA.
- thresholds: general, character, rating, suggestion, effective confirm threshold, `force_suggestions`.
- `source="ai_wd"`.
- `generated_at`, run ID, job ID.
- tag array: canonical tag name, WD category, confidence, action/confirmed-or-suggestion.

Reuse strategy:

- Before inference, compute fingerprint for imported app-managed media.
- If a compatible export row exists with matching `content_sha256`, model identity, and thresholds, import/replay tag rows through the same tag-service semantics instead of running inference.
- Respect manual/locked rows and do not overwrite manual truth.
- Record reused vs newly inferred vs failed in the AI ledger.
- If policy mismatch exists, do not reuse; run inference or defer.

This avoids redoing expensive AI tagging across dev/prod databases while keeping provenance explicit.

## 13. Media fingerprint / stable key design

Stable key priority:

1. `content_sha256` of file bytes.
2. Existing `Media.hash` / MD5 as compatibility key for current DB rows.
3. `file_size`.
4. `width`, `height`, `duration`.
5. Optional perceptual hash later for near-duplicate recall, not for automatic truth.

FULLLIB-E1 can compute SHA-256 in private ledgers/export artifacts without a DB migration. A future DB column for SHA-256 may be useful, but it is not required to execute E1 if the import ledger and AI export preserve it.

## 14. Allowed DB writes for FULLLIB-E1

Default allowed tables:

- `blombooru_media`
- `blombooru_tags`
- `blombooru_media_tags`
- `blombooru_classification_jobs`
- `blombooru_ai_tag_jobs`

Optional only if FULLLIB-E1 deliberately routes through scan-job APIs and updates the mutation allowlist:

- `blombooru_scan_jobs`
- `blombooru_scan_job_media`

The preferred FULLLIB runner should avoid scan-job tables unless they add clear operational value, because the private run ledger is the source of execution truth.

## 15. Forbidden DB writes for FULLLIB-E1

Forbidden table families:

- Entity truth: `blombooru_entities`, `blombooru_entity_aliases`, `blombooru_entity_external_identities`, `blombooru_entity_evidence`, `blombooru_media_entity_candidates`, `blombooru_media_entity_assignments`, `blombooru_entity_translations`.
- Provider/cache: `blombooru_external_sources`, `blombooru_provider_cache`, `blombooru_negative_lookup_cache`.
- Source metadata: `blombooru_source_metadata_records`, `blombooru_source_tag_observations`, `blombooru_source_tag_registry`, `blombooru_source_name_observations`, `blombooru_source_name_registry`, `blombooru_source_name_alias_candidates`, `blombooru_source_metadata_evidence`, `blombooru_source_searchable_name_assertions`, `blombooru_source_name_candidate_extraction_runs`, `blombooru_source_name_candidate_record_verdicts`, `blombooru_source_name_candidates`.
- SourceConcept: `blombooru_source_concept_resolution_runs`, `blombooru_source_concept_signals`, `blombooru_source_concepts`, `blombooru_source_concept_aliases`, `blombooru_source_concept_evidence`, `blombooru_source_concept_signal_links`, `blombooru_source_concept_search_index`.
- Localization: `blombooru_tag_translations`, `blombooru_tag_translation_jobs`.
- Pixiv/source taxonomy KB: `blombooru_external_tag_category_lookup_cache`, `blombooru_pixiv_tag_taxonomy_kb`, `blombooru_pixiv_tag_alias_kb`.
- Users, API keys, albums, settings, or unrelated operational tables unless explicitly added to a later approved allowlist.

## 16. Forbidden operations

FULLLIB-E1 must not:

- Run provider/Pixiv/gallery-dl/SauceNAO/Google/reverse search.
- Run LLM or localization.
- Run SourceConcept resolver, R1R, A1R, R2, Entity resolver, similarity, or Entity bridge.
- Mutate Entity truth, confirmed assignments, source metadata, SourceConcept tables, or provider caches.
- Mutate source roots, iCloud roots, staging roots, existing app-managed originals/thumbnails/cache, or external directories.
- Cleanup/delete/reset/drop/truncate.
- Push `main` or merge.
- Expose local paths, filenames, source roots, secrets, originals, thumbnails, or source labels in public artifacts.

## 17. Ledger design

Required private ledgers:

- `inventory-candidates.jsonl`: every supported-extension candidate and every deferred candidate with safe label, private source ref, source root label, size, extension, cloud state, source gate decision, candidate state, and fingerprint fields when computed.
- `duplicate-skipped.jsonl`: duplicate/hash/manifest/source-key decisions, duplicate reason, matched media ID if private, and public safe label.
- `import-item-ledger.jsonl`: per item imported/deferred/failed, bytes copied, app-managed path private ref, media ID, DB import eligibility, failure reason, retry state.
- `classification-ledger.jsonl`: media ID, fingerprint, content class, confidence, model/method, job ID, eligible for AI tagging.
- `ai-tagging-ledger.jsonl`: media ID, fingerprint, job ID, reused/exported/inferred status, output tag count, coverage status, failure reason.
- `ai-tag-export.jsonl`: reusable fingerprint-keyed AI tag export with model/threshold provenance.
- `batch-summary.jsonl`: one row per batch with candidates, attempted, imported, classified, AI tagged, deferred, failed, budget status.
- `run-summary.json`: aggregate run state, contract fields, mutation proof, redaction proof, resume cursor.

Required public artifacts:

- Aggregate Markdown report.
- Aggregate summary JSON.
- Optional review pack if required or requested.

Public artifacts must contain aggregate counts and safe labels only.

## 18. Failure budget and per-item failure policy

Structural blockers stop the whole run:

- DB identity mismatch.
- Source root / storage root confusion.
- Unsafe app-managed storage.
- Target path escape.
- Unexpected DB mutation.
- Source/iCloud mutation.
- Public report redaction failure.
- Ledger schema failure.
- Production storage missing or overlapping source roots.
- Active background AI/classification/localization jobs before execution.
- Offline model preflight failure when required.

Per-item failures are recorded and excluded from success counts when within budget:

- unsupported extension.
- corrupt file.
- unreadable file.
- duplicate.
- cloud unavailable.
- timeout.
- source missing.
- permission denied.
- size mismatch.
- AI tagging failed for one item.

Default batch failure budget:

- `max_item_failures=20`
- `max_failure_rate=0.05`
- `max_consecutive_failures=10`
- `max_same_reason_failures=20`

For a true full-library run, these should apply per batch, with run-level aggregate reporting. Candidate discovery cloud deferrals should be ledgered separately from import/AI failures, because they are not DB import attempts.

## 19. Resume/retry/backfill policy

Resume must be ledger-driven:

- A run can resume from the last completed batch only if DB identity, storage identity, code SHA, source roots, model policy, and contract version match.
- Imported rows are idempotent by fingerprint/hash and app-managed path.
- Duplicates remain skipped, not retried as failures.
- Cloud-unavailable rows remain deferred for later backfill.
- Failed item retries must record retry count and previous failure reason.
- Backfill must only consume previously deferred/failed rows and must not silently replace the full candidate set.
- AI reuse export/import must record whether tags were reused, newly inferred, or deferred due to policy mismatch.

## 20. Validation plan

P0 validation:

- `git diff --check`
- `git diff --cached --check`
- Python identity check with the repo-local venv Python.
- JSON syntax validation for this summary JSON.
- `tests/test_phase45_doc1_documentation_state.py`

FULLLIB-E1 validation before any execute:

- Python identity contract.
- Production DB identity contract.
- Production storage identity proof.
- Source root and source/app-storage overlap proof.
- Inventory dry-run ledger schema validation.
- Duplicate/hash dry-run validation.
- Offline model availability preflight.
- Public redaction dry-run.
- Contract checks for import, classification, AI tagging, mutation safety, artifact lifecycle, and public redaction summary fields.
- Backup/recovery proof for production DB and production storage before DB/app-storage writes.

FULLLIB-E1 validation after execute:

- Before/after table fingerprints.
- Post-commit DB counts on a fresh connection.
- Per-batch ledger validation.
- AI tag coverage denominator and coverage proof.
- Manual/locked overwrite proof.
- Public report/summary redaction scan.
- Optional review pack contract if E1 makes a full-library readiness claim.

No browser validation is required for P0. FULLLIB-E1 needs browser validation only if it changes UI/runtime behavior or if the execution prompt explicitly asks for gallery search/browse manual validation after import.

## 21. Whether existing tools are sufficient

Existing tools are sufficient as components, not as an unchanged full-library production runner.

Reusable:

- `SourceIngestionGate` and Cloud Files helpers.
- `process_media_file`, `get_unique_filename`, and thumbnail generation.
- Existing `Media`, `Tag`, `media_tags`, `ClassificationJob`, and `AITagJob` models.
- Classification and AI tagging job services.
- E1 runner patterns for private ledgers, public redaction, failure budgets, DB identity, storage identity, and mutation proof.
- GOV3 contract checker and contracts.

Not sufficient unchanged:

- The medium E1 runner hard-codes phase slug, branch, target counts, development DB assumptions, and medium-batch limits.
- Existing admin scan flow stores raw `file://` source paths in `Media.source`, which is not the desired public-safe production import record.
- Current AI tag outputs are keyed by DB media IDs in job rows; cross-DB reuse needs fingerprint-keyed export/import.

## 22. Gaps before FULLLIB-E1

Must close before execute:

1. Approve this P0 plan.
2. Create or adapt a FULLLIB runner from the E1 runner pattern with production DB/storage profile gates.
3. Add/verify contract-shaped summary fields for the GOV3 contracts listed above.
4. Add ledger schema validation for inventory, import, classification, AI tagging, export/reuse, batch, and run summary.
5. Add production DB backup/recovery and app-storage recovery expectations.
6. Add AI tag fingerprint export/reuse design to the runner or explicitly defer reuse for the first production run.
7. Add production safety tests for DB/storage/source separation and forbidden-table mutation proof.
8. Decide whether `review_pack_contract_v1` is required or explicitly waived for FULLLIB-E1.

Can defer:

- DB migration for persistent SHA-256 column, if private ledgers/export contain SHA-256.
- Production SourceConcept/provider/entity track.
- R1R/A1R/R2.
- UI changes.

## 23. FULLLIB-E1 recommended prompt outline

Recommended next prompt:

```text
Phase 4.6-FULLLIB-E1: Production Utility Full-Library Import, Classification, and AI Tagging Execution

Start from latest main. Create branch codex/phase46-fulllib-e1-production-import-ai-tagging.
Do not run provider/LLM/SourceConcept/Entity/R1R/A1R/R2/localization/similarity.
Use VIOLET_ENV=production, POSTGRES_DB=violet_library_prod, and the approved production VIOLET_STORAGE_ROOT.
First implement/adapt the FULLLIB runner and focused tests from the E1 runner pattern.
Run Python identity, DB identity, storage identity, source-root safety, inventory dry-run, duplicate dry-run, offline model preflight, contract checks, and redaction checks.
Stop before execute unless this prompt includes explicit execute approval and the exact confirmation phrase.
If execute is approved, import in bounded batches, classify imported media, reuse AI tags by fingerprint when compatible, infer remaining eligible media locally, record private ledgers, generate public report/summary, run all contract checks, push branch, open a normal PR, comment exactly @codex review, and stop.
```

## 24. Relationship to R1R/A1R

FULLLIB-E1 does not remediate the SourceConcept pipeline fidelity incident. It must not approve R2, Provider-2, PX1-B, SourceConcept truth promotion, or Entity bridge.

R1R remains the future full SourceConcept pipeline replay/remediation under `source_concept_full_chain_contract_v1`. A1R remains the future route audit rerun under `route_audit_contract_v1`. They should follow production utility planning unless the project owner explicitly reprioritizes SourceConcept.

## 25. Why old R1/A1 should not be rolled back

Do not roll back R1/A1 now.

- R1 remains historical deterministic source-layer output, not truth.
- A1 remains read-only audit evidence, invalid for route approval.
- Old R1/A1 must not approve R2.
- R1R/A1R should supersede/remediate with new contract-shaped outputs, not destructive rollback.
- Destructive rollback would add more risk than value because it would mutate historical source-layer evidence without creating the missing full-chain proof.

## 26. Remaining risks

- Production/dev separation depends on strict DB/storage identity preflight because current config allows file settings and environment settings to interact.
- Full-library source roots may contain many cloud placeholders; failure budgets must distinguish deferral from failed import.
- Existing `Media.hash` is MD5; private SHA-256 fingerprinting is needed for robust AI tag reuse.
- AI tag reuse can create stale results if model name, model revision, thresholds, or tagger code differ.
- Production storage backup/restore expectations must be explicit before execute.
- #109 remains GOV3.1 debt and must be considered before any route approval or high-risk review-pack proof.

## 27. Next step

Approve or revise this P0 plan. If approved, start `FULLLIB-E1` as the next implementation/execution phase for the production utility track. Keep R1R/A1R important but separate, and do not start them unless the project owner explicitly reprioritizes the SourceConcept/provider track.
