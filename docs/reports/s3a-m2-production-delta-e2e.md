# S3A-M2 Production Delta Manual Sync E2E + GPU Telemetry

## Identity

- Phase: `S3A-M2` / `Production Delta Manual Sync E2E + GPU Telemetry`.
- Status: `completed_with_followup_required`.
- Contract: `s3a_m2_production_delta_e2e_contract_v1`; target met: `False`.
- Standard pipeline flow: `backend_and_isolated_browser_e2e_validated; production_gui_acceptance_pending`.
- Branch: `codex/s3a-m2-production-delta-e2e-gpu-telemetry`.
- Head SHA: `0e6ba221293644a13a9dde5038b3a61c3bac6e11`.
- Production acceptance performed: `True`.
- Source root: `153684ac810c2191`.

## Current Evidence Update

- S3A-M2 remains **not safe to merge** and must not claim `target_met`.
- Correction: the previous retry-readiness closeout was premature because the required repeated local iCloud-copy incremental E2E had not yet been completed. That validation is now complete and recorded in `docs/reports/s3a-m2-local-copy-incremental-e2e-summary.json`.
- The repeated local-copy E2E used an isolated source root, isolated test DB, and isolated app storage. It copied `965` locally available JPG/PNG files from the existing iCloud/photo source library without downloading additional cloud images and without mutating the original source/iCloud files.
- The repeated local-copy E2E passed `10` sequential cycles with pass criteria failures `[]`: baseline import, no-change no-op, small increment, medium increment, old-mtime increment, large stable root + cap-limited increment, duplicate/unsupported/hidden outcome, placeholder/cloud simulation, unknown-vs-non_anime gating, and legacy-backlog-plus-new-files.
- Plan expensive operations stayed `0/0/0/0` for content reads/hash/decode/hydration across all cycles.
- A real browser test against the isolated test server validated the normal operator flow: `Start manual sync` -> browser confirmation -> execute request -> stage UI completion, without using the Advanced exact audit phrase.
- Final production GUI Execute acceptance is still pending. The project owner may retry production GUI acceptance on this head or newer, but this is not a merge recommendation and not a normal-use safety claim.

## Priority Workset Backlog Root Cause

Read-only production audit artifacts:

- Public summary: `docs/reports/s3a-m2-priority-backlog-audit-summary.json`.
- Private raw artifact root: `.local_manifests/s3a_m2_delta_e2e/priority_backlog_audit/`.
- Production DB writes: `False`.
- Source/iCloud mutation: `False`.

For root `2` / `icloud-photos-production`:

- `total_dynamic_source_items=40096`.
- `total_priority_workset_rows=22902`.
- `legacy_pending_changed_rows=22698`, all outside the mtime safety window.
- `rows_with_media_id=35448`.
- `rows_matching_existing_media=35448`.
- `rows_matching_existing_media_hash=34723`.
- `rows_imported_but_still_pending_or_changed=22698`.
- `rows_with_downstream_followup_needed=0`.
- `rows_with_retryable_failures=0`.
- `rows_that_should_be_actionable_now=204`.
- `rows_that_should_be_stable_noop=34703`.
- `rows_that_need_repair_or_migration=22698`.

Filesystem/media reconciliation:

- `file_visible_and_media_exists=22698`.
- `metadata_matches_existing_import=22698`.
- `stale_update_check_artifact=22698`.
- `real_new_or_changed_candidate=204`.
- `file_visible_but_no_media=200`.
- `file_missing_and_no_media=4`.

Root cause: the 22902-row priority workset is not normal steady-state work. The dominant subset, `22698` rows, is stale historical update-check/source-ledger state in `blombooru_dynamic_source_items`: already-imported media/hash/storage evidence existed, but rows remained `sync_state=changed` and `import_status=pending`. Earlier full import/execute paths did not reconcile these equal-hash imported rows into terminal stable no-op states, and earlier tests did not model a near-full-import production ledger with tens of thousands of stale changed/pending rows.

Tactical mitigation implemented: normal planning excludes legacy pending/changed rows outside the safety window from current priority selection, so they do not consume actionable cap or hide new ledger-missing files.

Structural cleanup is proposed but not executed. Dry-run repair candidate count is `22698`; candidate condition is pending changed rows outside the safety window with media id or existing content-hash evidence and no downstream follow-up. Proposed target is stable `skipped_existing_media` / existing-hash no-op state. Production DB repair requires explicit project-owner approval, backup/export, private repair ledger, rerun of this backlog audit, and contract/redaction validation.

## Repeated Local-Copy Incremental E2E

This is the required repeated incremental validation, not a one-time bulk import proof.

| Cycle | Added | Total files | Selected | Imported | Existing | Unsupported | Placeholder | Non-target | Unknown | Failed | Legacy seen/selected | Plan s | Total s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| baseline import | 500 | 500 | 500 | 494 | 6 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.183 | 415.940 |
| no-change | 0 | 500 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.207 | 0.207 |
| small increment | 10 | 510 | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.318 | 9.229 |
| medium increment | 100 | 610 | 100 | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.207 | 79.266 |
| old-mtime increment | 100 | 710 | 100 | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.248 | 78.816 |
| large stable root + cap increment | 120 | 830 | 120 | 120 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.303 | 92.427 |
| duplicate/unsupported/hidden outcome | 17 | 847 | 13 | 11 | 1 | 3 | 1 | 0 | 0 | 0 | 0/0 | 0.305 | 10.137 |
| placeholder/cloud simulation | 1 | 848 | 0 | 0 | 0 | 3 | 2 | 0 | 0 | 0 | 0/0 | 0.312 | 0.312 |
| unknown vs non_anime/classifier unavailable | 5 | 853 | 5 | 5 | 0 | 3 | 2 | 2 | 2 | 1 | 0/0 | 0.299 | 4.992 |
| legacy backlog + new files | 120 | 973 | 127 | 120 | 6 | 3 | 2 | 0 | 0 | 1 | 494/0 | 0.331 | 103.458 |

Conclusions:

- No-change was a fast, explainable no-op.
- Repeated increments selected current ledger-missing work instead of stale backlog.
- Old-mtime copied files were discovered and imported.
- The simulated legacy backlog cycle saw `494` stale legacy rows and selected `0` of them, while importing the new batch.
- Outcome breakdown accounted for duplicate/existing, unsupported, placeholder-like, confirmed non-target, unknown, and classification-unavailable cases.
- `unknown` was not treated as `non_anime`; classifier-unavailable rows were deferred/blocked instead of non-target skipped.

## Isolated Real Browser Normal Flow

- Browser base URL: `http://127.0.0.1:8024`.
- Web Admin source root: `s3a-m2-local-copy-e2e`.
- Normal operator card visible: `True`.
- Advanced exact execute box hidden before the normal flow: `True`.
- Browser confirmation described the full pipeline in Chinese: import -> classification -> AI tagging -> localization -> report.
- Execute request observed: `True`.
- Latest isolated GUI-created job: `#10`, status `completed`, `request_source=web_admin_gui`.
- GUI plan hash binding: `True`; GUI plan flow verified: `True`.
- Plan candidate pool: `14` (`5` imports and `9` downstream follow-up items).
- Legacy backlog skipped in browser test: `494`.
- Plan expensive operations: `0/0/0/0`.

This proves the normal operator flow in an isolated real browser environment. It does not replace final user-performed production GUI Execute acceptance.

## Counts

- Cap used: `1000`; cap exceeded: `False`.
- Dry-run total/import: `173` / `49`.
- Execute total/imported: `173` / `49`.
- Classification count/failures: `49` / `0`.
- AI tagging count/failures: `49` / `0`.
- Localization translated/failures/skipped: `2` / `0` / `4`.
- Localization provider/calls/retries: `fallback(primary->fallback)` / `1` / `0`.
- Final imported/classified/AI-tagged/localized totals: `349` / `349` / `349` / `5`.
- Skipped/failed/deferred: `{'deferred': 0, 'failed': 0, 'skipped_duplicate': 0, 'skipped_existing_media': 20, 'skipped_placeholder': 0, 'skipped_unsupported': 104}`.

## Initial Run

- Dry-run total/import: `453` / `300`.
- Classified / AI-tagged / localized: `300` / `300` / `3`.
- Placeholder / unsupported / existing skips: `36` / `98` / `19`.

## Remaining Run

- Dry-run total/import: `173` / `49`.
- Classified / AI-tagged / localized: `49` / `49` / `2`.
- Placeholder / unsupported / existing skips: `0` / `104` / `20`.

## Localization Diagnosis

- Diagnosis: `benign_all_localizable_tags_already_localized_or_newly_localized`.
- AI tag assignments / distinct tags: `2328` / `714`.
- Localizable distinct / already localized / newly localized / remaining gap: `700` / `700` / `2` / `0`.
- Proper-noun entity-deferred/not-current-localization-category skipped: `4`.
- Not eligible: `{'category_not_in_general_or_meta': 0, 'proper_noun_entity_deferred_not_general_or_meta': 4, 'proper_noun_suggestion_review_only': 4}`.

## AI Tag Assignment Incident And Cohort Audit

- Incident status: `repaired`; affected runs: `[7, 8]`; affected media: `349`; assignments inspected: `17464`.
- Root cause: `manual_sync_execute_used_an_overbroad_suggestion_override; the first repair then retained an over-strict proper-noun suggestion-only policy instead of mature media-tag semantics.`.
- Repair converted suggestion->normal: `157`; kept suggestions: `4334`; duplicate rows created: `0`.
- After repair high-confidence non-proper incorrect suggestions: `0`; normal high-confidence non-proper tags: `12973`.
- Mature-policy proper-noun normal tags / incorrect suggestions: `157` / `0`.
- Proper-noun suggestions kept below threshold: `50`; Entity/SourceConcept truth violations: `0`.
- Cohort status: `passed_after_repair`; baseline method: `latest older non-S3A-M2 media with source='ai_wd' before affected cohort upload window`; affected/baseline media: `349` / `194`.
- S3A-M2 normal/suggestion tags per media avg: `37.622` / `12.418`.
- Baseline normal/suggestion tags per media avg: `39.526` / `12.021`.
- Classification unknown rate S3A-M2/baseline: `6.59` / `5.155`.
- Localization remaining gap after repair: `0`; blocker anomalies remaining: `0`.
- Post-repair UI validation: `passed`; samples: `8`; normal visible pass: `8`; mature proper normal visible pass: `3`; true suggestion visible pass: `8`.
- Computer Use result: `unavailable_in_current_tool_session; tool discovery did not expose computer-use controls after clean retry, fallback used Playwright/Edge`; fallback method: `playwright_msedge_against_launcher_started_production_server_after_second_repair`.

## Placeholder Hydration

- Status: `completed`.
- Passes represented: `3`.
- Before / attempted / succeeded / failed / remaining: `51` / `50` / `50` / `0` / `0`.
- Failure reasons: `{}`.
- Manual user action required: `False`.

## Final Inventory

- Current delta candidates/importable: `124` / `0`.
- Existing / placeholders remaining / unsupported / unreadable-zero-byte-damaged: `20` / `0` / `104` / `0`.
- Unsupported extension breakdown: `{'.heic': 28, '.mov': 75}`.
- Failure reason extension breakdown: `{'existing_media_hash': {'.jpg': 18, '.png': 2}, 'hidden': {'.ini': 1}, 'unsupported_extension': {'.heic': 28, '.mov': 75}}`.
- Scan cap stopped scan: `False`.

## Standard Pipeline Flow

- Version: `1`; future automation readiness: `manual_pipeline_backend_evidence_good_but_gui_execute_acceptance_still_required`.
- Aggregate basis: `{'final_inventory_delta_candidates': 124, 'hydration_passes_represented': 3, 'initial_execute_run_id': 7, 'remaining_execute_run_id': 8}`.
- capture_resource_gpu_telemetry: `completed`; completed: `True`.
- classify_imported_media: `completed`; completed: `True`.
- detect_cloud_placeholders: `completed`; completed: `True`.
- hydrate_placeholders_non_destructively: `completed`; completed: `True`.
- import_all_current_importable_items: `completed`; completed: `True`.
- produce_public_report_and_contract: `completed`; completed: `True`.
- record_ledger_for_every_planned_item: `completed`; completed: `True`.
- rescan_after_hydration: `completed`; completed: `True`.
- run_ai_tagging: `completed`; completed: `True`.
- run_localization_or_stable_reasons: `completed`; completed: `True`.
- scan_current_source_delta: `completed`; completed: `True`.
- validate_launcher_web_admin_workflow: `pending`; completed: `False`.
- validate_public_redaction: `completed`; completed: `True`.

## Telemetry

- GPU provider: `DmlExecutionProvider`; GPU validation: `passed`.
- GPU name: `NVIDIA GeForce RTX 4070 Ti`.
- Aggregate peak GPU memory MiB: `3752.0`; peak GPU util: `65.0`.
- Telemetry partial fields: `['process_rss', 'system_ram']`.
- Aggregate runtime seconds: `459.593`; stage durations: `{'dry_run_plan': 0.032, 'init': 2.405, 'localization': 9.641, 'manual_execute_import_classification_ai': 447.234, 'summary': 0.078}`.
- Remaining-run runtime seconds: `67.25`; stage durations: `{'dry_run_plan': 0.016, 'init': 1.218, 'localization': 4.625, 'manual_execute_import_classification_ai': 61.203, 'summary': 0.047}`.

## GUI Acceptance Debug

- Status: `zero_import_partial_plan_fixed_pending_user_retry`.
- Observed server: port `8012`, profile `production-default`, env `production`, DB `blombooru`.
- Endpoint clicked: `/api/admin/dynamic-library-sync/manual-sync/plan`; planner path: current S3A-M2 manual-sync plan endpoint, not the old update-check endpoint.
- Corrected root cause: the normal GUI plan had a hard roughly `600s` total elapsed timeout. The latest user run was still making progress through hashing when that timeout fired, so the plan was cancelled before Execute even though there was no proven stuck file.
- `file-00724` interpretation: last visible public-safe item label when the old global timeout fired; it is not evidence of a bad or stuck source file.
- Stuck jobs found/cleaned: `False` / `True`.
- Cap/UI mismatch: `backend execute cap was 1000 but frontend execute input kept stale default 5 until policy initialization; fixed to initialize from backend cap on first dashboard load`.
- Readiness/config mismatch: `Earlier UI readiness displayed background AI/LLM flags as blockers. A later manual GUI attempt correctly stopped because the launcher-managed production profile had AI tagging disabled for manual E2E. The profile/env mapping is now repaired so manual classification, AI tagging, and LLM localization provider readiness can be ON while automatic/background sync remains OFF.`.
- Latest user-path failure: on head `32cb03698f06746ae1f829b5b37fc565c7d59c4c`, the normal `Start manual sync` plan showed progress for about `597s`, reached `hashing` with `seen=724`, `unchanged ledger skips=404`, `batch candidates=319`, `importable=0`, `failed=0`, then the old global timeout requested cancellation.
- Post-failure cleanup audit: no V.I.O.L.E.T. listeners remained on ports `8000,8012-8024`; no active server/request was visible when Codex audited after the user aborted.
- Timeout policy changed: global elapsed timeout is removed as a normal failure mechanism; healthy progress may continue beyond `600s`. A `300s` no-progress watchdog can request cancellation, and stale `cancelling` progress becomes terminal `cancel_failed` after the watchdog window.
- Per-item timeout policy: supported-image verification and content hashing already use `file_read_timeout_seconds` and record stable per-item `read_timeout` reasons before continuing; the new watchdog only handles lack of global progress between service checkpoints.
- Batch/resume policy: GUI planning now reports `bounded_actionable_batch`; committed source-ledger terminal states may be reused only when source identity is unchanged, and Execute must revalidate source identity before writes.
- Plan progress/cancel added: `True`; normal UI now polls a `plan_request_id`, shows phase/current safe item/counts/recent events/elapsed time, exposes Cancel plan, rejects duplicate active plans per root/source, and releases active-plan locks on unexpected planner errors.
- UI label fix: `historical skipped` is now `unchanged ledger skips`; `planned` is now `batch candidates`.
- Threshold UI fix: historical threshold/deferred inventory is moved out of the normal operator path and labelled as diagnostic, not a current manual-execute blocker; current execute safety comes from the generated manual plan.
- AI/localization wording fix: `AI -> localization: OFF` is replaced by background-chaining wording and separate manual E2E localization readiness.
- Canonical routing fix: same-page `hashchange` now activates `#dynamic-library-sync-section`, so stale `admin_content_section` localStorage no longer hides the normal manual-sync section after `/admin?tab=content#dynamic-library-sync-section`.
- Real browser validation after latest fix: `passed_real_browser_gui_plan_flow_current_fix_only` on a controlled `VIOLET_ENV=test` server on port `8015` with Playwright Edge. It clicked the normal `Start manual sync` button, hit the real `/api/admin/dynamic-library-sync/manual-sync/plan` endpoint, confirmed the selected root summary followed the operator dropdown, confirmed GUI session/request/plan-hash binding, canonical URL, normal operator UI, priority-workset + filesystem-fallback diagnostics, fast source-ledger skip identity, actionable import/follow-up split, duplicate-click busy guard, no raw i18n/internal blocker constants, and operator-entered confirmation gating. It did **not** click Execute and does not satisfy final production GUI Execute acceptance.
- Acceptance blocker: `No GUI Execute button-triggered run newer than run #8 has completed and passed post-run validation.`.

### Latest Zero-Import Partial Plan Incident

- Observed head: `f1040fb21a1c04489b3ed31d295321f5eddc44ac`.
- Observed plan hash prefix: `b0cce6557c8866b14eb58b66`; request id: `gui-plan-2c981cd1-33d1-4c86-adaf-ab2678dcc776`.
- Observed root scope: same registered active source root used by this S3A-M2 phase; raw root id, label, source path, and full identifier are kept out of public artifacts.
- Observed plan source/cap/hydration: `source_delta` / `1000` / `cloud_aware_non_destructive_read`.
- Observed counts: scanned/seen `1000`; import `0`; partial scan `True`; states `skipped_existing_media=994`, `failed=6`.
- Execute disabled reason: the plan was partial and had no importable hydrated items. The confirmation phrase was not the deciding condition; Execute was correctly blocked by backend/frontend safety policy.
- Current plan reuse: `not_reusable`. The plan was produced by the previous selection/cap semantics, had no writeable import items, and expired under the existing plan-staleness rule. It is diagnostic evidence only; it should not be executed.
- Root cause: the planner selected source-root/ledger candidates, then only discovered `existing_media_hash` after those candidates had already consumed the visible batch/cap. Source-delta rows with existing media were therefore allowed to dominate the first batch before the planner reached the current actionable new items.
- Scanner/cursor finding: the old user-facing plan did not expose a durable operator cursor or continuation point. Registered roots reused `DynamicSourceItem` metadata for skip decisions, but unchanged/existing media could still consume cap before existing-media hash classification.
- Cap semantics before fix: effectively `files/candidates visited before final existing-media classification`.
- Cap semantics after fix: `unique_actionable_import_or_downstream_followup_candidates_not_stable_existing_or_duplicate_media`. Stable existing media, duplicate hashes, and unchanged ledger rows are counted in diagnostics but do not consume the actionable execute cap.
- Source-delta workset after fix: registered roots now prioritize current actionable `DynamicSourceItem` rows such as pending new/changed items without `media_id`, cloud placeholders, retryable read/cloud failures, and imported rows with downstream follow-up. After that priority workset is exhausted or below cap, the planner continues into the filesystem walk so files not yet in `DynamicSourceItem` cannot be hidden behind old ledger rows.
- Continuation/resume model after fix: committed source-ledger states are reusable only when source identity and metadata still match. Unexecuted private plan candidates are not trusted for Execute; Execute re-plans and revalidates size/mtime/hash/source identity before DB writes. A new plan is required after code/profile changes or plan expiry.
- Failure reason visibility: the old GUI plan did not persist public-safe per-failure reason details for the six failed rows. Future plans now surface aggregate reason and extension breakdowns in the plan/report path without exposing private filenames or paths.
- Confirmation UX fix: plans with zero importable items no longer show a production confirmation phrase or confirmation controls. Writeable plans now use an S3A-M2 human-readable operator phrase; the old `S3A-M1` phrase label is no longer shown for new plans.
- Real browser validation for this fix: `passed` on controlled Playwright Edge against a `VIOLET_ENV=test` server. It clicked the normal `Start manual sync` plan flow, verified the normal operator path, selected-root summary, canonical URL, workset/fallback diagnostics, fast-skip identity, actionable import/follow-up split, visible duplicate-click guard, hidden raw diagnostics, GUI plan-flow binding, and operator-entered confirmation gating. Execute was not clicked.

### Incremental Scanner / Workset Model

- Does manual sync start from root every time: `False` in the effective model for registered roots with source-ledger state. Registered roots start with a priority `DynamicSourceItem` source-delta workset, then fall through to a metadata filesystem walk for files not yet in the ledger. There is still no single global filesystem watermark; this PR uses the durable source-item ledger as the bounded incremental checkpoint model.
- Where the scan head lives: in `DynamicSourceItem` source identity/state, not in raw public paths. The durable fast identity is `source_root_id + relative_path_hash + file_size + mtime_ns`, plus import/classification/AI/localization status and known `media_id/content_hash` when available.
- When hashing happens: for new or metadata-changed supported files, duplicate/existing verification when cached identity is insufficient, downstream follow-up source integrity checks, and Execute-time revalidation before any DB writes.
- When hashing is skipped: known stable source-ledger rows with unchanged root/relative identity/size/mtime and no downstream follow-up can be skipped cheaply and shown as `Unchanged ledger skips`; they do not consume the actionable cap.
- What invalidates reuse: changed source root, relative identity/path hash, size, `mtime_ns`, content-hash mismatch on revalidation, source missing/deleted/moved, hydration mode/profile/cap/manual-E2E setting change for an unexecuted plan.
- Moved/deleted/modified handling: deleted/missing files become stable `source_missing/path_missing` reasons; moved files leave the old ledger row stale/missing and are rediscovered by filesystem fallback under the new relative identity; modified files force metadata/hash revalidation and become changed/importable or duplicate/existing as appropriate.
- Cap semantics: cap now means actionable import or downstream-follow-up candidates. Stable existing/duplicate/unchanged rows remain visible in diagnostics but cannot crowd out new files.
- Downstream follow-up: already-imported media that still need classification, AI tagging, or localization are planned as `downstream_followup_planned` and executed through downstream stages; they are not hidden as `skipped_existing_media`.
- User-visible proof fields: normal operator plan now shows `Root last checked`, `Scan model`, `Start basis`, `Actionable cap`, `Cap means`, `Workset`, `Priority items`, `Filesystem fallback`, `Filesystem complete`, `Incremental ledger`, `Fast skip identity`, `Actionable (import/follow-up)`, `Unchanged ledger skips`, `Fast-skipped`, `Stat checked`, `Hash checked`, and `More batches`.

### P0 Normal Manual Sync Flow Redesign

- Triggering incident: real Web Admin normal `Start manual sync` plan request `gui-plan-dfd8cb35-53df-42f5-a37e-26b2bdb25380` on head `cccef9ddbd6312ae9ef2909d194fe47e2e9062db` was cancelled by the user after about `3727s`. It had only reached `seen=263`, `batch_candidates=262`, `importable=0`, and was still in `checking_supported`.
- Root cause: the old normal Plan path was doing import-time validation work. The `checking_supported` stage belonged to the deep planner and could open/read/decode source files or trigger slow iCloud/Windows Cloud Files behavior before any Execute stage began. That made Plan responsible for the wrong job and produced minutes-to-hours user waits.
- Why previous tests missed it: earlier tests proved public-safe plan shape, cap gates, cancellation, and backend execute semantics, but did not assert operation boundaries such as `content_reads=0`, `hashes=0`, `decodes=0`, `hydrations=0` for the normal Plan, nor did they test the normal UI as one Plan -> Import -> Classification -> AI tagging -> Localization -> Complete pipeline.
- Adopted mature sync/import pattern: source-ledger + mtime watermark + safety lookback + metadata fast path + bounded actionable batches. This follows the same broad pattern documented by rsync/rclone/Syncthing style sync systems: use metadata fast checks first, then hash/read only when needed for changed/new/ambiguous items or execute-time verification.
- New normal Plan boundary: candidate discovery only. It may query `DynamicSourceItem`, stat metadata, compare `source_root_id + relative_path_hash + file_size + mtime_ns`, apply a cheap extension filter, and select bounded action candidates. It does **not** compute content hashes, open/decode images, hydrate iCloud placeholders, read full file content, or decide corrupt-image/duplicate-by-content outcomes.
- New Execute boundary: import-time validation happens during Execute. Execute revalidates source identity with size/mtime before writes, then performs hash/integrity, cloud-placeholder hydration, duplicate/existing detection, unsupported/corrupt item recording, import, classification, AI tagging, localization or stable localization reasons, ledger update, and final summary.
- Watermark / safety window: the normal Plan derives `source_mtime_watermark_ns` from stable imported/source-ledger rows and uses `DYNAMIC_LIBRARY_MANUAL_SYNC_SAFETY_LOOKBACK_SECONDS` (default `604800`, seven days) to include recent files before the watermark. Files with old unchanged mtimes outside the window are normal-path fast skips; Advanced/Diagnostics full rescan remains the repair path for stale-mtime edge cases.
- Old stable files: unchanged known rows are fast-skipped from ledger metadata and do not consume the actionable cap. They are visible as diagnostics (`unchanged_known_files`, `fast_skipped_from_ledger`) but are not treated as current work.
- Unsupported/duplicate/corrupt/cloud-placeholder candidates: normal Plan may cheaply filter unsupported extensions, but actual content validation, duplicate/existing hash checks, corrupt image failures, and cloud-placeholder hydration are Execute responsibilities with item-level stable reasons.
- UI redesign: the normal operator path is now one staged workflow with a stage strip: `Plan -> Import -> Classification -> AI tagging -> Localization -> Complete`. Deep counters, plan hash, exact audit phrase, raw cap/workset diagnostics, and update-check controls remain in Advanced/Diagnostics.
- Confirmation UX: the normal flow uses one explicit browser confirmation before writes, with a human-readable operator statement describing import/follow-up counts and the full pipeline. The exact audit phrase remains advanced-only and is not auto-filled. Zero-import plans do not show execute confirmation controls.
- Performance target: normal no-change/small-delta Plan should be milliseconds-to-seconds scale; under `1000` metadata candidates, over `30s` is a blocker unless an external OS/iCloud stall is proven. Any normal Plan averaging more than `100ms` per metadata candidate is suspicious. Normal Plan counters for content reads, hashes, image decodes, and hydrations should remain `0`.
- Measured code-level performance proof: `test_manual_sync_incremental_plan_fast_skips_large_stable_ledger_without_expensive_reads` created `1200` stable ledger rows plus `5` new files and proved the normal Plan selected the `5` new imports while expensive operation counters stayed at `0`. The focused suite passed `142` tests.
- Full-chain proof in isolated test DB/storage: `test_manual_sync_normal_incremental_plan_runs_full_e2e_pipeline_with_stage_summary` exercised normal incremental Plan -> Import -> Classification -> AI tagging -> Localization -> Summary with stubbed local stages, confirmed stage ordering, counts, and no Plan hash/read/decode/hydration work.
- Real browser proof: Playwright Edge against a controlled `VIOLET_ENV=test`, `blombooru_test`, isolated-storage server on port `8013` validated the Dynamic Library Sync normal operator UI, stage strip, hidden advanced execute controls, localized Advanced/Diagnostics separation, and no replacement of this with production/API runner evidence. Execute was not clicked.
- Current acceptance status after this redesign: final production GUI Execute acceptance is still not complete. The user should not retry until this PR head is pushed, reports/PR body are refreshed, contracts/redaction pass, and current-head reviewer feedback is requested/checked.

### Latest Web Admin Smoke Test On Head 69fab773

- Evidence source: real user-performed production Web Admin manual-sync smoke test on head `69fab773d52ddf67b736d06485c485ea626be2c7`.
- Result: `partial_success_with_blockers`.
- Job: `#13`; final status: `completed`; observed elapsed time: about `129s`.
- Plan limit / observed plan hash prefix: `100` / `44beb57e2770`.
- Visible stage UI: `Plan -> Import -> Classification -> AI tagging -> Localization -> Complete`; all stages reached completed state.
- Plan expensive-operation counters: content reads / hashes / decodes / hydrations = `0 / 0 / 0 / 0`.
- Counts: `seen=100`, `imported=65`, `failed=0`.
- Outcome breakdown: `imported=65`, `skipped_existing_media=35`, duplicate `0`, unsupported `0`, placeholder/hydration skipped `0`, failed `0`.
- Explanation for `seen=100` but `imported=65`: the 35 non-imported rows were represented as stable `skipped_existing_media` with `existing_media_hash`; no candidate was silently lost and no failure was recorded.
- Acceptance limitation: the normal browser confirmation popup appeared, but the normal flow did not start Execute. The user still had to use the lower Advanced/Diagnostics exact-phrase execute control. This is not full normal-flow GUI acceptance.
- Fix made after the smoke test: normal `Start manual sync` now submits a human-readable operator confirmation statement to `executeManualSyncPlan()` after the browser confirmation; Advanced exact phrase remains collapsed/advanced-only and is no longer required for normal incremental flow.
- Real browser regression for the fix: Playwright Edge against an isolated `VIOLET_ENV=test` server clicked normal `Start manual sync`, accepted the browser confirmation, and verified `/api/admin/dynamic-library-sync/manual-sync/execute` started without touching the Advanced exact-phrase control.

### Non-Target AI / Localization Audit

- Audit mode: read-only, aggregate only; raw private artifact: `.local_manifests/s3a_m2_delta_e2e/non_target_ai_audit/manual-sync-non-target-ai-audit-runs-7-8-13.json`.
- Runs audited: `[7, 8, 13]`; DB writes performed: `False`.
- Media by content class: `anime=379`, `non_anime=12`, `unknown=23`.
- Run #13 content class: `anime=65`; no confirmed non-target media were imported by the latest smoke test.
- Historical #7/#8 confirmed non-target media with `ai_wd` assignments: `12` (`non_anime` only).
- Confirmed non-target `ai_wd` assignment count: `429`; distinct confirmed non-target AI tags: `238`; distinct confirmed non-target AI tags with zh-CN translation coverage: `237`.
- Historical #7/#8 unknown/uncertain media with `ai_wd` assignments: `23`; unknown/uncertain `ai_wd` assignment count: `1216`.
- Diagnosis: #7/#8 historical S3A-M2 runs let confirmed `non_anime` media pass into WD anime tagging and localization. Unknown is not non-target; those 23 unknown media are reported separately and are not destructive repair candidates by default.
- Future-run fix: manual execute now runs classification before AI tagging. Confirmed `non_anime` skips AI/localization with stable statuses `ai_tagging_skipped_non_target` and `localization_not_applicable_non_target`; classified `unknown` remains downstream-eligible and classification-unavailable/unclassified rows are deferred with a classification blocker instead of being labeled non-target.
- Production DB repair status: `not_executed_requires_project_owner_approval`.
- Proposed deterministic repair: privately back up/export affected row identities, remove or invalidate only `source='ai_wd'` assignments on confirmed `non_anime` #7/#8 media after project-owner approval, preserve manual tags and target/anime/unknown media tags, mark confirmed non-target source items non-applicable where appropriate, and verify Entity/SourceConcept truth violations remain `0`.
- Merge blocker: unresolved until the project owner explicitly approves and the repair is executed/validated, or explicitly accepts the historical contamination as deferred debt.

### Latest Correctness Fixes

- Normal confirmation flow: fixed; normal operator confirmation now starts the full pipeline without the Advanced exact phrase.
- Classification gate: fixed; manual E2E production readiness requires `CONTENT_CLASSIFICATION_METHOD=clip`; confirmed non-target media are skipped, classified unknown remains eligible, and classifier-unavailable rows are deferred as classification blockers.
- Unknown/non-target semantic correction: fixed after review of head `46156d1a9b147574958483176120eab4fa358a45`; `unknown`, `unclassified`, and null content class are no longer collapsed into confirmed `non_anime`. Classification unavailable now blocks/deferred downstream stages with `classification_not_completed`; classified `unknown` stays eligible for AI/localization under the current project-owner ruling.
- Localization truthfulness: fixed; deferred localization gaps now produce `completed_with_followup_required` instead of a plain completed job, successful translation saves recompute the remaining gap, and cancellation after localization side effects preserves LLM/provider/DB-write counters.
- Production launcher readiness: fixed; the production manual E2E profile default classification method is `clip`, existing unmarked legacy `heuristic` profile values are normalized to `clip`, explicit non-clip operator overrides fail closed before launch/execute, and automatic/background sync flags remain off.
- Old-mtime unseen files: fixed; files absent from `DynamicSourceItem` are not skipped solely because preserved mtime is older than watermark minus safety lookback. Known stable old files may still fast-skip by ledger identity.
- Job #15 discovery/cap ordering failure: fixed; historical `pending/changed` source-ledger backlog outside the mtime safety window no longer sits ahead of filesystem ledger-missing discovery or consumes the normal actionable cap. The planner now collects metadata-only candidates, prioritizes current ledger-missing files, and keeps old-mtime ledger-missing backfill as a lower-priority candidate class.
- Duplicate/existing visibility: fixed; latest job summary now shows imported, stable skipped/not-applicable, failed, and per-reason breakdown.
- Governance follow-up: future non-trivial feature work should start plan-only, include mature prior-art/design references for common engineering domains, and use realistic product-path/performance validation. Reports remain supporting evidence, not proof.

### Production Profile CLIP Runtime Blocker

- User-observed blocker: real Web Admin manual acceptance stopped with `Manual E2E requires classification-before-AI gating; set CONTENT_CLASSIFICATION_METHOD=clip for production acceptance.`
- Root cause: the private existing production profile already persisted `manual_e2e_components.content_classification_method="heuristic"`. The earlier fix changed new/default profile values to `clip`, but did not migrate or fail closed for existing legacy production profiles before the user reached Web Admin.
- Actual runtime source: the launcher-managed child process uses the private production profile under `.local_manifests/production_launcher/production-profile.json` with `VIOLET_SKIP_DOTENV=1`; therefore `.env` defaults did not repair this runtime.
- Fix: profile load/coercion now treats missing or unmarked legacy `heuristic` manual-E2E classification method as a legacy default and normalizes it to `clip` with `content_classification_method_migrated_from="heuristic"`.
- Explicit override behavior: if an operator explicitly updates `manual_e2e_components.content_classification_method` to non-`clip`, `profile-status`, `preflight`, and `start` fail closed with `manual_e2e_classification_method_clip` instead of letting the GUI discover the blocker after `Start manual sync`.
- Readiness alignment: `profile-status --json`, `diagnostic-summary --json`, launcher child env, Web Admin readiness, and backend execute gate now agree on `CONTENT_CLASSIFICATION_METHOD=clip` for the repaired legacy profile path.
- Current local read-only proof: `profile-status` reported method `clip`, `runtime_env_content_classification_method=clip`, `migrated_from=heuristic`; a child Python process importing `backend.app.config.settings` under the production profile env also saw `CONTENT_CLASSIFICATION_METHOD=clip`, `VIOLET_ENV=production`, DB `blombooru`, and `DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED=false`.
- Persisted repair path: `scripts/violet_production_control.py profile-repair --json` rewrites the private profile with the normalized `clip` value. Without running repair, restart from the fixed code still normalizes the legacy value at load time; repair makes the private file explicit for future runs.
- Production data/source impact: this fix is profile/config-only. It does not mutate source/iCloud files, app-managed media, or production DB content.

### Job #15 Manual Sync Discovery Failure

- User-observed blocker: after a no-change cap `200` smoke run, the project owner added `110+` new iCloud/source-root photos, then started normal Web Admin manual sync with cap `500`. The GUI-created run `#15` completed with `seen=500`, `imported=0`, `failed=0`, and all stages visually completed.
- Request / route evidence: request id `gui-plan-f5b3646a-e365-4fdc-baec-1b1f59817e8d`, endpoint `/api/admin/dynamic-library-sync/manual-sync/plan`, root `icloud-photos-production`, root id `2`, cap `500`, hydration `cloud-aware`.
- Preserved read-only artifacts: `.local_manifests/s3a_m2_delta_e2e/gui_acceptance_debug/job15-source-ledger-audit-public-safe-20260701T131014.json` and `.local_manifests/s3a_m2_delta_e2e/gui_acceptance_debug/job15-post-fix-plan-recheck-public-safe-20260701T132045.json` public-safe summaries; raw/private item evidence remains under the same ignored local artifact tree.
- Source visibility finding: the source root was visible to the process. A metadata-only audit saw `40205` visible files, `40071` ledger-known files, `134` ledger-missing files, and `133` ledger-missing supported files. Of those supported missing files, `120` were within/after the watermark safety window and `13` preserved old mtime below the cutoff.
- Job `#15` DB finding: the completed execute run did not import because its private plan contained `500` `import_planned` items that all became stable `skipped_existing_media` with reason `existing_media_hash` during execute-time hash/import validation. It did not prove there was no new delta.
- First/no-op attempt finding: the preserved log shows multiple GUI plan requests before #15, including `gui-plan-d1ce0ffa-dc36-43e1-915b-bc4f322bb5b7`, and #15 later created a verified GUI execute run. The key durable DB side effect from these attempts was materializing additional `skipped_existing_media` source-ledger rows; no source/iCloud mutation or DB import was performed by CodeX during diagnosis.
- Root cause: the normal incremental planner put a very large historical `DynamicSourceItem` priority workset ahead of filesystem ledger-missing discovery. In production this priority workset had `22902` rows, including `22698` old `sync_state=changed` / `import_status=pending` rows from historical update-check state. The cap was therefore consumed by historical/legacy candidates before the planner reached the newly added ledger-missing files.
- Why the previous old-mtime fix was incomplete: it proved a single ledger-missing old-mtime file could be selected when reached, but it did not prove the planner would reach ledger-missing files when a historical priority workset existed first.
- Algorithm fix: normal priority workset now excludes legacy `pending/changed` rows outside the mtime safety window, while preserving real downstream follow-up, cloud-placeholder, and retryable read/cloud failures. The normal planner then performs metadata-only filesystem fallback, collects candidates, sorts them by currentness (`downstream_followup`, ledger-missing mtime-new, ledger-missing safety-window, known pending/window, ledger-missing old-mtime backfill), and only then applies the actionable cap.
- Cap semantics after this fix: stable known rows and historical legacy pending backlog do not consume the import/actionable cap. Unknown ledger-missing supported files are not skipped solely because their mtime is old; old-mtime ledger-missing files become lower-priority backfill candidates after current/recent candidates.
- Post-fix read-only production recheck: same root and cap `500` produced a metadata-only plan in `21.515s`: `metadata_entries_seen=40217`, `candidate_pool_count=341`, `estimated_import_count=341`, `partial_scan=false`, `mtime_new_candidates=119`, `safety_window_candidates=19`, `ledger_missing_candidates=141`, `ledger_missing_recent_candidates=128`, `ledger_missing_old_mtime_candidates=13`, `legacy_pending_outside_window_skips=22698`, and plan reads/hash/decode/hydrate `0/0/0/0`.
- Interpretation: the newly added files were visible; the miss was caused by priority-workset/cap ordering, not by wrong root, unsupported extensions, iCloud invisibility, or a source mutation requirement.
- Safety impact: no production execute/import/classification/AI/localization was run by CodeX for this fix; no source/iCloud files were mutated. The user should restart/pull the fixed head before retry so the live 8012 server uses this planner.

## Pre-User Manual Acceptance Safety Fixes

- Status: `fixed_pending_codex_re_review_after_ebed72c_followup_commit`.
- Reviewer scope: current-scope P1/P2 on reviewed head `ebed72c3794d088e8813d7b7390412ef825ee72a`, including executable cap-limited batches, manual E2E LLM readiness preservation, GUI plan-flow provenance binding, current-head GUI validator proof, downstream source-item scoping, cancellation before localization finalizer, and the source-ledger incremental scanner model.
- Operator-entered production confirmation required: `True`.
- Signed GUI provenance required: `True`; ordinary API can satisfy GUI acceptance: `False`; GUI Execute acceptance now also requires the same GUI session to submit the bound `plan_request_id` and plan hash, and the validator rejects old-head GUI runs unless explicitly run in diagnostic mode.
- Validator scope: `validated_gui_run_source_root_only`; skipped placeholders included: `True`.
- Localization failure reporting: `manual_sync_execute.localization_failed_items plus localization summary failed/gap fields`.
- Cancellation before localization guard: `True`; LLM calls prevented: `True`; localization DB writes prevented: `True`.
- Historical read errors retryable in current delta planning: `True`.
- Manual E2E readiness/backend gates aligned: `True`.
- Priority workset falls through to filesystem walk: `True`.
- Existing/duplicate stable rows consume actionable cap: `False`.
- Downstream follow-up rows executable: `True`.
- Backend fails closed when manual E2E localization requires an unconfigured LLM provider: `True`.
- GUI validator clears stale profile env when profile values are absent: `True`.
- Existing production profile CLIP migration: `fixed`; old unmarked `heuristic` manual-E2E profiles normalize to `clip`, while explicit non-clip updates are blocked before launch/execute.
- Cap-limited batch execute gate: `fixed`; a safe plan containing the first N actionable candidates under cap can execute with `more_batches_remain=True`, while timeout/cancel/walk-error partial scans remain blocked.
- User manual GUI acceptance package status: `do_not_ask_user_to_retry_until_new_head_reviewed_or_explicitly_accepted_by_project_owner`.

## Validation

- Ledger consistency: `passed`; represented items: `173` / `173`.
- DB count delta: media `349`, source items `391`.
- Public redaction: `True`; findings: `0`.
- Launcher/Web Admin: `blocked_pending_reviewer_recheck_and_user_manual_gui_execute_after_p0_normal_flow_redesign`; browser: `msedge`; normal operator UI and staged flow validated in controlled test browser; production Execute clicked: `False`.
- Launcher dry-run request/timeout/server-stop: `True` / `True` / `True`; latest timeout observed: `597s` before Execute.
- Plan progress endpoints/tests: `added`; cancellation endpoint/tests: `added`; duplicate active plan guard/tests: `added`.
- Real browser validation for latest UI fix: `passed_p0_normal_operator_ui_stage_flow`; method `Playwright Edge`, URL `http://127.0.0.1:8013/admin?tab=content#dynamic-library-sync-section`, environment `test`, DB `blombooru_test`, normal staged workflow visible `True`, Advanced/Diagnostics separated `True`, Execute clicked `False`.
- Real browser validation for latest normal-confirmation fix: `passed`; method `Playwright Edge`, URL `http://127.0.0.1:8013/admin?tab=content#dynamic-library-sync-section`, environment `test`, DB `blombooru_test`, normal Start manual sync clicked, browser confirmation accepted, `/manual-sync/execute` observed, Advanced exact phrase not used.
- Launcher fallback reason: `Computer Use stopped before page validation because it could not independently verify the Chrome URL; Playwright/browser evidence is not being used as a substitute for Computer Use acceptance.`.
- Latest job observed by real user smoke test: run `13`, status `completed`, imported `65`; full normal-flow GUI acceptance remains `False` because Advanced execute was still required before the fix.
- Latest user-path discovery failure: run `15`, status `completed`, imported `0`; read-only audit found `133` ledger-missing supported files visible under the registered root, so this was a planner selection/cap-ordering bug rather than proof of no new work.
- Post-fix read-only production plan recheck: cap `500`, `estimated_import_count=341`, `ledger_missing_candidates=141`, `legacy_pending_outside_window_skips=22698`, `partial_scan=false`, and plan reads/hash/decode/hydrate `0/0/0/0`.
- Latest focused validation after Job #15 fix: `tests/test_dynamic_library_sync.py` `66 passed`; `tests/test_s3a_m1_manual_sync_execute.py` `79 passed`; `tests/test_s3a_m2_delta_e2e.py` `26 passed`; `tests/test_phase_contracts.py -k s3a_m2` `37 passed`; `s3a_m2_production_delta_e2e_contract_v1` `passed` with `target_met_claimed=false`; `public_redaction_contract_v1` `passed`.

## Current Merge / Retry Status

- Manual sync safety judgement: `manual_sync_not_yet_safe_gui_execute_unvalidated`.
- Remaining blockers:
  - `confirmed_non_target_ai_localization_contamination_unrepaired`: #7/#8 confirmed `non_anime` `ai_wd` assignments require approved deterministic repair or explicit acceptance as deferred debt. Unknown #7/#8 AI tags are not included in default destructive repair.
  - `real_production_gui_execute_acceptance_pending_after_local_copy_e2e`: isolated repeated incremental E2E and isolated browser normal flow have passed, but production GUI Execute acceptance still needs a user-created run on this head or newer.
  - `gui_validator_after_normal_flow_run_pending`: the GUI acceptance validator must pass on a normal-flow GUI execute run after the fix.
- The user may retry production GUI acceptance on this head or newer, but must not treat S3A-M2 as complete or safe to merge until the production GUI run and validator pass.
- Priority backlog repair/migration: proposed as a dry-run plan and not executed. It is not required before the next acceptance retry because normal planning now excludes those stale rows from actionable cap and isolated backlog-cycle/browser evidence passed, but it remains recommended follow-up before long-term steady-state cleanup.

## Safety

- Source/iCloud mutation attempted: `False`.
- Automatic/scheduled/startup/system-service sync enabled: `False` / `False` / `False` / `False`.
- Provider/source expansion run: `False`.
- Private paths or hashes in public report: `False`.

## Not Completed

- Automatic/scheduled/startup/system-service sync was not implemented; it remains out of scope.
- Pixiv/provider/gallery-dl/SauceNAO/Google/source metadata expansion was not run.
- SourceConcept/Entity bridge work was not run.
- Actual launcher/Web Admin GUI Execute acceptance is not completed after the one-hour GUI dry-run hang and the later AI-tagging-disabled readiness blocker; that blocker is now fixed, but a new GUI-created run newer than run #8 must still be validated before merge.
- Actual launcher/Web Admin GUI Execute acceptance is still not completed after the later user-path zero-import partial plan (`seen=1000`, `import=0`, `partial_scan=yes`). This patch changes the scanner/workset model so priority ledger rows cannot hide new filesystem files, stable existing/duplicate rows cannot consume actionable cap, and downstream follow-up rows are executable; it still needs Codex re-review and then a real GUI Execute run before merge.
- Actual launcher/Web Admin GUI Execute acceptance is also not completed after the P0 normal Plan architecture failure (`gui-plan-dfd8cb35-53df-42f5-a37e-26b2bdb25380`, about `3727s`, still `checking_supported`). The normal Plan/Execute boundary and operator UI have now been redesigned and test/browser validated in isolation, but production GUI Execute must not be retried until this head is reviewed and accepted for retry.
- Actual launcher/Web Admin GUI Execute acceptance is not completed after the later job `#15` discovery failure on head `e7bda5bbb10e17d882af9ad84d5b4a3d2a877f11`: the planner selected 500 historical/legacy candidates that all resolved to `existing_media_hash`, while 133 ledger-missing supported filesystem files were visible outside the selected priority workset. The planner now excludes legacy pending backlog from normal priority, performs filesystem metadata fallback, prioritizes current ledger-missing candidates, and needs a user retry on the fixed head.
- Required repeated local-copy incremental E2E is now completed and passed. This evidence allows a production GUI acceptance retry, but it is not a substitute for the final production GUI Execute acceptance.
- The historical deferred/failed inventory still contains unsupported/out-of-scope and stale rows; it is documented separately from current actionable GUI delta work and should be improved in UI wording after GUI Execute acceptance.
- Final user-performed launcher/Web Admin GUI Execute acceptance run newer than run #8 has not completed yet.

No source paths, filenames, content hashes, API keys, prompts, source URLs, or original image bytes are included in this public report.
