# S3A-M2 Production Delta Manual Sync E2E + GPU Telemetry

## Identity

- Phase: `S3A-M2` / `Production Delta Manual Sync E2E + GPU Telemetry`.
- Status: `completed_with_followup_required`.
- Contract: `s3a_m2_production_delta_e2e_contract_v1`; target met: `False`.
- Standard pipeline flow: `backend_and_isolated_browser_e2e_validated; production_gui_acceptance_pending`.
- Branch: `codex/s3a-m2-production-delta-e2e-gpu-telemetry`.
- Head SHA at run #16 incident diagnosis base: `33441ae22bc44f1f74d9d3b7abbcee4308e00435`.
- API/runner production executes #7/#8 performed: `True`.
- User production GUI Execute acceptance performed: `attempted_but_failed_run_16_and_run_17`; acceptance pass: `False`.
- Authorized production source-ledger repair performed: `True` (`22698` stale priority rows terminalized).
- Source root public identity: `153684ac810c2191`.

## Current Evidence Update

- S3A-M2 remains **not safe to merge** and must not claim `target_met`.
- Correction retained: the earlier retry-readiness closeout was premature because the repeated local iCloud-copy incremental E2E had not been completed at that time. That validation has now been completed and is recorded in `docs/reports/s3a-m2-local-copy-incremental-e2e-summary.json`.
- The repeated local-copy E2E used an isolated source root, isolated test DB, and isolated app storage. It copied `850` locally available JPG/PNG files from the existing iCloud/photo source library without downloading additional cloud images and without mutating the original source/iCloud files.
- The repeated local-copy E2E passed `11` sequential cycles with pass criteria failures `[]`: baseline import, no-change no-op, small increment, medium increment, old-mtime increment, large stable root + cap-limited increment, duplicate/unsupported/hidden outcome, placeholder/cloud simulation, unknown-vs-non_anime gating, legacy-backlog-plus-new-files, and the new partial-import downstream recovery scenario reproducing the run #16/#17 failure shape.
- Plan expensive operations stayed `0/0/0/0` for content reads/hash/decode/hydration across all cycles.
- A real browser test against the isolated test server validated the normal operator flow: `Start manual sync` -> browser confirmation -> execute request -> stage UI completion, without using the Advanced exact audit phrase.
- The stale production priority backlog is no longer merely documented or sorted around. The bounded authorized production ledger repair executed and after-audit shows `legacy_pending_changed_rows=0` and `rows_that_need_repair_or_migration=0` for the audited stale condition.
- Architecture consolidation audit added: `docs/reports/s3a-m2-manual-sync-architecture-audit.md`. It inventories the full current manual-sync mechanism graph, defines canonical Plan/Execute ordering, records the conflict matrix, and documents which mechanisms were kept, refactored, merged, isolated, or deferred.
- Final production GUI Execute acceptance is still pending. The project owner may retry production GUI acceptance only after pulling the fixed head, running the new preflight script, and confirming the page shows the downstream follow-up batch; this is not a merge recommendation and not a normal-use safety claim.
- New production GUI evidence after the previous report: the user performed real Web Admin GUI Execute run `#16` on head `33441ae22bc44f1f74d9d3b7abbcee4308e00435`. The run imported media but failed before downstream stages, so it is not an acceptance pass.
- Bounded run #16 fix: retryable source read/hydration failures may stop further import attempts in the current run, but they no longer prevent classification / AI tagging / localization from running for media already imported in that run. Runs with downstream-complete imported media plus retryable source failures now end as `completed_with_failures` rather than plain `failed`.
- New production GUI evidence after that fix: the user performed real Web Admin GUI Execute run `#17`. It reached `completed_with_failures` and ran all visible stages, but it still did not complete run #16's `155` already-imported downstream-incomplete media. This is a current-scope acceptance blocker and is diagnosed below.

## Production GUI Execute Run #16 Incident

Evidence source:

- User-performed real production Web Admin GUI Execute run `#16`.
- Private/public-safe diagnosis artifacts under `.local_manifests/s3a_m2_delta_e2e/run16_incident/`.
- No production Execute, import, classification, AI tagging, or localization was run by CodeX during this incident follow-up.

Run #16 summary:

- Run type / mode: `manual_sync_execute` / `production_acceptance`.
- Request source: `web_admin_gui`.
- Source root: `2` / `icloud-photos-production` (`153684ac810c` public hash prefix).
- Runtime head recorded by the GUI request: `33441ae22bc44f1f74d9d3b7abbcee4308e00435`.
- Status before this fix: `failed`; stop reason: `stopped_by_failure_budget`; current stage: `summary`.
- Plan candidates / run items: `351`.
- Imported: `155`; skipped existing/duplicate: `6`; failed source reads: `9`; deferred unprocessed: `181`.
- Failure reasons: `read_error=5`, `read_timeout=4`.
- Failure-budget interpretation: the run had `155` imported + `6` stable skipped + `9` failed by the time the import budget tripped. The `9/170 = 5.29%` item failure rate exceeded the configured `5%` threshold, so further import attempts stopped.

Imported-media downstream status before this fix:

- Imported media rows present: `155/155`.
- App-managed storage files present: `155/155`.
- `classification_status`: `pending=155`.
- `ai_tagging_status`: `pending=155`.
- `localization_status`: `waiting_ai_tags=155`.
- `ai_wd` assignments: `0`.
- `Media.content_class`: `NULL=155`.
- Entity/SourceConcept truth pollution from run #16: `0`.

Diagnosis:

- Run #16 did not fail only because nine source files could not be read.
- The import-stage failure budget stopped the run and downstream stages were skipped for already imported media.
- This violated the S3A-M2 manual-sync invariant: any media already imported into DB/app storage must either finish downstream processing or remain in an explicit, visible downstream follow-up/deferred state.
- The read failures are item-level retryable source/iCloud/hydration-style failures. They may stop additional imports in the same batch when the failure budget trips, but they must not strand successfully imported media in `pending` / `waiting_ai_tags`.

Fix made in this continuation:

- Added explicit retryable source failure classification for `read_error`, `read_timeout`, `source_missing`, `permission_denied`, `cloud_hydration_failed`, `cloud_network_unavailable`, and `icloud_placeholder`.
- If import stops with `stopped_by_failure_budget` and all failures are retryable source failures, execute now clears the terminal stop for downstream processing and continues classification / AI tagging / localization for imported media.
- Budget accounting is reset for downstream stages after an allowed import-budget stop, so the prior source-read failures do not immediately re-trip the budget before classification/AI/localization can run.
- The final run status becomes `completed_with_failures` when imported media downstream completes but retryable source failures or unprocessed continuation items remain.
- The execute summary now records `import_stopped_by`, `downstream_continued_after_import_stop`, and `retryable_source_failure_count`.
- GUI acceptance validation now accepts `completed_with_failures` / `completed_with_followup_required` as terminal run statuses only if imported-media classification, AI tagging, localization, ledger, tag assignment semantics, redaction, GUI provenance, and Entity/SourceConcept truth checks also pass.

Run #16 aftermath / recovery:

- The existing run #16 imported items remain downstream-incomplete in production until the next operator run processes them.
- Read-only follow-up evidence shows `155` imported downstream-incomplete source items with app storage present and follow-up reasons `classification_required=155`, `ai_tagging_required=155`, `localization_required=155`.
- Current planner semantics mark imported rows with incomplete classification/AI/localization as `downstream_followup_planned`; they can be processed from app-managed media and do not require the original source file.
- The next normal manual sync should include those `155` items as downstream follow-up, not as new imports, while retryable source-read failures remain visible for retry.

Policy judgment:

- Read-error/read-timeout on unimported source items should not make the whole GUI run plain `completed`, because real retryable source failures remain.
- It also should not make the whole run plain `failed` if imported media complete downstream and the only remaining failures are retryable source-read/hydration items.
- Correct status for that shape is `completed_with_failures` / `completed_with_followup_required` according to existing project conventions.
- Production GUI acceptance is still not complete until the user retries on the fixed head and `scripts/validate_s3a_m2_gui_execute_acceptance.py` passes.

## Production GUI Execute Run #17 Follow-Up Recovery Incident

Evidence source:

- User-performed real production Web Admin GUI Execute run `#17`.
- Read-only diagnosis artifacts:
  - `.local_manifests/s3a_m2_delta_e2e/run16_run17_followup_incident/run16-run17-followup-diagnosis-public-20260701T173218Z.json`.
  - `.local_manifests/s3a_m2_delta_e2e/run16_run17_followup_incident/run16-followup-readonly-proof-public-20260701T173923Z.json`.
- CodeX did not run production Execute/import/classification/AI/localization during this follow-up.

Run #17 summary:

- Run type / mode: `manual_sync_execute` / `production_acceptance`.
- Status: `completed_with_failures`.
- Seen / plan items: `348`.
- Imported: `3`.
- Failed source reads: `11` (`read_error=2`, `read_timeout=9`).
- Deferred unprocessed: `334`.
- Downstream stages for the three newly imported media completed: `classified=3`, `ai_tagged=3`, `localized=3`.
- Execute summary recorded `downstream_continued_after_import_stop=True` and `retryable_source_failure_count=11`.

What run #17 proved:

- The #16 import failure-budget fix worked for media imported in the same run: run #17's three newly imported media completed downstream despite retryable source-read failures.
- It did **not** recover the already-imported downstream-incomplete media from run #16. Run #17 wrote the run #16 source items as `deferred_unprocessed` again.

Read-only DB diagnosis for the run #16 leftovers after run #17:

- Run #16 imported media/source items inspected: `155`.
- Media rows present: `155/155`.
- App-managed storage files present: `155/155`.
- Current `DynamicSourceItem` state: `sync_state=deferred_unprocessed=155`, `import_status=imported=155`.
- Current downstream state: `classification_status=deferred=155`, `ai_tagging_status=deferred=155`, `localization_status=deferred=155`.
- Current deferred reason: `not_processed_budget_stop=155`.
- `media_id` and `content_hash` are present for all `155` rows.
- Current planner helper evaluation before the fix already said `requires_followup=True` and priority `10` for `155/155`, but run #17's persisted private plan ordered the `193` import candidates before the `155` downstream follow-up candidates. The failure budget tripped during import before the follow-up items were reached, and `_materialize_deferred_unprocessed_items()` marked the remaining follow-up items as `deferred_unprocessed`.

Root cause:

- The bug was not that the run #16 rows were unrecoverable.
- The bug was the combination of two gaps:
  1. downstream follow-up discovery was still tied to the source-ledger/file workset rather than being an explicit app-media-backed normal-plan pass; and
  2. execute trusted persisted private plan ordering, so import failures could stop the run before already-imported downstream follow-up items were processed.

Fix made for this incident:

- Normal manual-sync planning now has an explicit app-media-backed downstream follow-up pass. It selects `DynamicSourceItem` rows for the selected root where `media_id` exists, the item is an imported/media-backed representation, app-managed media evidence exists, and classification/AI/localization is incomplete.
- These records become `downstream_followup_planned`, count under `estimated_downstream_followup_count` / `db_followup_candidates`, preserve `source_item_id`, `media_id`, `content_hash`, and `relative_path_hash`, and do not require source file read/hash/decode/hydration.
- Follow-up priority now puts `imported` / `not_processed_budget_stop` recovery rows ahead of older `existing_media_hash` media-backed follow-up rows. This prevents cap-limited batches from spending the first batch on older existing-media follow-up before recovering run #16 leftovers.
- Execute now stably reorders private plan items by group before processing: `downstream_followup_planned` first, then `import_planned`, then other states. It preserves original order within each group. This protects production even if an older or stale persisted private plan had import items before follow-up items.
- Retryable source failures now write lightweight durable retry metadata under `DynamicSourceItem.metadata_json.manual_sync_retry` with `attempt_count`, `last_retry_at`, `last_failure_reason`, `retryable`, and `long_term_state` (`needs_diagnosis` after five attempts). No schema migration was added in this PR.

Read-only production proof after the fix:

- Source root: `2`.
- App-media-backed follow-up rows discoverable by current code: `880`.
- Run #16 source items discoverable as follow-up: `155/155`.
- With cap `500`, run #16 source items selected in the next batch: `155/155`.
- Priority distribution: `0=155` (`not_processed_budget_stop` / imported recovery), `5=725` (`existing_media_hash` media-backed follow-up).
- App-managed storage present for follow-up rows: `880/880`.
- Plan expensive operations for this proof: `content_reads=0`, `hashes=0`, `decodes=0`, `hydrations=0`.
- Source/iCloud mutation: `False`; production Execute run by CodeX: `False`.

Why previous tests missed this:

- Earlier repeated local-copy incremental E2E covered no-change, new files, old-mtime files, cap-limited batches, duplicates, unsupported files, placeholders, and unknown-vs-non_anime gating, but it did not reproduce the production shape of imported media being left downstream-incomplete after an import failure-budget stop and then requiring recovery in a second normal run.
- Existing execute tests covered downstream follow-up execution, including missing source files, but not the combination of a misordered persisted private plan plus retryable import failure budget before follow-up items.
- Existing planner tests covered source-file-present downstream follow-up and a missing-source execute path, but not an app-media-backed planner pass independent of the filesystem/source walk.
- The test environment therefore proved the backend pieces in isolation but did not assert next-run recovery of partially processed imported media using production-like `deferred_unprocessed/not_processed_budget_stop` `DynamicSourceItem` states.

## Manual GUI Acceptance One-Click Preparation

New operator scripts:

- PowerShell: `scripts/prepare_s3a_m2_manual_gui_acceptance.ps1`.
- Double-click wrapper: `scripts/prepare_s3a_m2_manual_gui_acceptance.cmd`.

How to run:

```powershell
.\scripts\prepare_s3a_m2_manual_gui_acceptance.ps1 -ExpectedHead <expected-head-sha>
```

or double-click / run:

```cmd
scripts\prepare_s3a_m2_manual_gui_acceptance.cmd -ExpectedHead <expected-head-sha>
```

What the script does:

- Verifies it is running inside the V.I.O.L.E.T. repo.
- Fetches origin, verifies/checks out `codex/s3a-m2-production-delta-e2e-gpu-telemetry`, optionally verifies the expected head, and fails closed on tracked dirty runtime changes unless explicitly overridden.
- Stops only the managed production server via `scripts/violet_production_control.py stop --json`, then runs `scripts/audit_active_violet_servers.py --ports 8000,8012-8024 --include-process-tree --json` and fails closed on ambiguous listeners.
- Validates repo venv identity with `scripts/check_python_env.py`.
- Validates the production profile points at `blombooru`, has manual sync/execute enabled, keeps automation flags OFF, and has manual E2E classification/AI/LLM readiness enabled.
- Read-only verifies source root `2` is active `icloud-photos-production`.
- Starts the launcher-managed production server, verifies `diagnostic-summary`, and opens `http://127.0.0.1:8012/admin?tab=content#dynamic-library-sync-section`.
- Writes a private local preflight artifact under `.local_manifests/s3a_m2_delta_e2e/manual_acceptance_preflight/`.
- Prints the post-run GUI validator and phase contract commands.

What the script refuses to do:

- It does not click `Start manual sync`.
- It does not click or call Execute.
- It does not call `/api/admin/dynamic-library-sync/manual-sync/execute`.
- It does not import, classify, AI-tag, localize, repair the DB, mutate source/iCloud files, or enable automatic/scheduled/startup/system-service sync.
- If it starts the server and then detects a failed preflight, it attempts to stop that managed server and prints `NOT SAFE TO RUN MANUAL ACCEPTANCE`.

## Priority Workset Backlog Root Cause And Repair

Audit and repair artifacts:

- Public audit summary: `docs/reports/s3a-m2-priority-backlog-audit-summary.json`.
- Public repair summary: `docs/reports/s3a-m2-priority-backlog-repair-summary.json`.
- Private row-level rollback snapshot: `.local_manifests/s3a_m2_delta_e2e/priority_backlog_repair/priority-backlog-pre-repair-root2-20260701T104528Z.jsonl`.
- Private DB backup before repair: `.local_manifests/s3a_m2_delta_e2e/priority_backlog_repair/priority-backlog-pre-repair-db-backup-20260701T104459Z.dump`.
- Source/iCloud mutation: `False`; media import/classification/AI/localization/provider calls during repair: `False`.

Before repair for root `2` / `icloud-photos-production`:

- `total_priority_workset_rows=22902`.
- `legacy_pending_changed_rows=22698`, all outside the mtime safety window.
- Dominant category: `stale_update_check_artifact_already_represented_by_existing_media_hash_storage_evidence`.
- The stale subset was file-visible, already represented by existing media/hash/storage evidence, had no retryable failure, and had no downstream follow-up requirement.

After repair:

- `total_priority_workset_rows=204`.
- `legacy_pending_changed_rows=0`.
- `legacy_pending_changed_outside_safety_window=0`.
- `rows_that_need_repair_or_migration=0`.
- `rows_that_should_be_actionable_now=204`.
- `rows_with_downstream_followup_needed=0`.
- `rows_with_retryable_failures=0`.

Repair execution:

- Candidate count: `22698`.
- Rows repaired: `22698`.
- After candidate count: `0`.
- Target state: `sync_state=skipped_existing_media`, `import_status=skipped`, `deferred_reason=existing_media_hash`, `classification_status=classified_reused`, `ai_tagging_status=tagged_reused`, `localization_status=localized`.
- Uncertain rows skipped: `{}`.

Root cause: 22698 legacy pending/changed DynamicSourceItem rows were stale update-check/source-ledger artifacts from earlier broad update-check/import paths. They were visible files already represented by existing media/hash/storage evidence, had no retryable failure and no downstream follow-up, but were left in pending/changed states and therefore polluted the priority workset.

Why the previous full import did not clear it: Earlier execute/import paths reused or matched existing media without terminalizing these legacy source-ledger rows into stable no-op states; tests covered sorting/cap behavior before they covered production-shaped stale ledger cleanup.

Conclusion: this was both stale historical ledger state and a missing terminalization path. The tactical planner mitigation still matters, but the structural production data issue has now been repaired for the audited stale condition. Remaining `204` priority rows are current actionable/file-visible rows from the after-audit, not the `22698` legacy stale backlog.

## Repeated Local-Copy Incremental E2E

This is the required repeated incremental validation, not a one-time bulk import proof.

| Cycle | Added | Total files | Selected | Imported | Follow-up | Existing | Unsupported | Placeholder | Non-target | Unknown | Failed | Legacy seen/selected | Plan s | Total s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline import | 450 | 450 | 450 | 444 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.257 | 370.942 |
| no-change | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.207 | 0.207 |
| small increment | 10 | 460 | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.184 | 8.767 |
| medium increment | 80 | 540 | 80 | 80 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.220 | 63.009 |
| old-mtime increment | 80 | 620 | 80 | 80 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.265 | 62.429 |
| large stable + cap | 120 | 740 | 120 | 120 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0.292 | 95.044 |
| duplicate/unsupported/hidden | 12 | 752 | 8 | 6 | 0 | 1 | 3 | 1 | 0 | 0 | 0 | 0/0 | 0.299 | 6.514 |
| placeholder simulation | 1 | 753 | 0 | 0 | 0 | 0 | 3 | 2 | 0 | 0 | 0 | 0/0 | 0.283 | 0.283 |
| unknown/non_anime gate | 5 | 758 | 5 | 5 | 0 | 0 | 3 | 2 | 2 | 2 | 1 | 0/0 | 0.394 | 4.702 |
| legacy backlog + new files | 100 | 858 | 101 | 100 | 1 | 0 | 3 | 2 | 0 | 0 | 1 | 300/0 | 0.315 | 78.420 |
| partial-import downstream recovery | 0 | 858 | 4 | 0 | 4 | 0 | 3 | 2 | 0 | 0 | 1 | 300/0 | 0.411 | 1.857 |

Conclusions:

- No-change was a fast, explainable no-op.
- Repeated increments selected current ledger-missing work instead of stale backlog.
- Old-mtime copied files were discovered and imported.
- The simulated legacy backlog cycle saw `300` stale legacy rows and selected `0` of them, while importing the new batch and including only one legitimate downstream follow-up.
- The new partial-import downstream recovery cycle seeded `3` already-imported downstream-incomplete media, removed their copied source files, left app-managed media present, and proved the next normal plan selected `4` downstream follow-up items (`3` seeded recovery items plus one existing follow-up), completed all `3` seeded follow-up items, created `0` duplicate Media rows, and kept one retryable source failure visible.
- Outcome breakdown accounted for duplicate/existing, unsupported, placeholder-like, confirmed non-target, unknown, and classification-unavailable cases.
- `unknown` was not treated as `non_anime`; classifier-unavailable rows were deferred/blocked instead of non-target skipped.

## Isolated Real Browser Normal Flow

- Browser base URL: `http://127.0.0.1:8024`.
- Web Admin source root: `s3a-m2-local-copy-e2e`.
- Normal operator card visible: `True`.
- Advanced exact execute box hidden before the normal flow: `True`.
- Browser confirmation shown: `True`.
- Execute request observed: `True`.
- Latest isolated GUI-created job: `#10`, status `completed`, `request_source=web_admin_gui`.
- GUI plan hash binding: `True`; GUI plan flow verified: `True`.
- Runtime head recorded in the GUI-created test run: `5a177c857020aa30b8c42554b7d8e8d3bee5bdbe`.
- Plan candidate pool: `1` (`0` imports and `1` downstream follow-up item).
- Legacy backlog skipped in browser test: `300`; legacy backlog selected/candidates: `0`.
- Plan elapsed: `0.297` seconds; metadata entries seen: `859`.
- Plan expensive operations: `{'content_reads': 0, 'hashes': 0, 'decodes': 0, 'hydrations': 0}`.
- Private evidence artifact: `.local_manifests/s3a_m2_delta_e2e/local_copy_incremental_e2e/browser_flow_current_llm_ready/browser-flow-evidence.json`.
- Note: this was an isolated `VIOLET_ENV=test` full-chain browser validation; it enabled test LLM localization readiness and performed test-environment localization provider calls. It did not run production Execute and did not mutate production DB or source/iCloud files.

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

- Status: `fixed_pending_codex_re_review_after_push`.
- Current runtime-code validation basis: `5a177c857020aa30b8c42554b7d8e8d3bee5bdbe` plus the current working-tree changes in this continuation. The final pushed commit SHA is recorded in the PR body and CodeX closeout because a committed report cannot embed its own final SHA without changing that SHA.
- Current reviewer status at start of continuation: Codex reviewed exact head `85e9993f8772151e4e9d42ad0a1825e99a59c74e` and returned comments; this continuation addressed the current owner/reviewer items that affect production Execute safety and acceptance proof.
- S3A-M2 runner execute payload bug: fixed by passing normalized `plan_mode`/`plan_source` into `_public_request_payload(...)`; regression coverage is in `tests/test_s3a_m2_delta_e2e.py`.
- AI identity/media-tag policy: explicit mature media-tag semantics are retained. High-confidence WD `general/meta/rating/character/copyright/artist` media tags may be normal `media_tags`; low-confidence/edge predictions remain suggestions. AI-only tags still must not create SourceConcept truth, Entity truth, or confirmed entity assignments.
- Stale priority backlog: repaired in production for the audited `22698` stale rows. This was not a source/iCloud or media import operation.
- Old pending ledger-row starvation: fixed. Real old pending rows no longer disappear solely due to mtime, while media-backed stale pending rows are terminalized or fast-skipped as stable no-op.
- Downstream follow-up content hash preservation: fixed. Known `content_hash` evidence is preserved and execute no longer overwrites existing source-ledger fingerprints with null.
- Cap semantics: actionable import/downstream-follow-up cap; stable existing/duplicate/unchanged rows do not consume the user-visible batch cap.
- GUI provenance: normal browser flow binds GUI session, `plan_request_id`, plan hash, and runtime head in the run summary. The final validator still requires a user-created production GUI run newer than #8.
- Confirmation UX: normal Start manual sync uses one human-readable browser confirmation before the full chain; Advanced exact phrase remains advanced-only and was not used in the isolated browser normal-flow test.
- Historical confirmed non-target AI contamination: not repaired in this continuation. The code path is fixed for future runs, but the historical #7/#8 confirmed `non_anime` AI WD assignment cleanup remains pending owner repair/deferral decision before merge.

## Validation

- `py_compile`: passed for `backend/app/services/manual_sync_execute_service.py`, `scripts/validate_s3a_m2_gui_execute_acceptance.py`, `tests/test_s3a_m1_manual_sync_execute.py`, and `tests/test_s3a_m2_delta_e2e.py`.
- `pytest tests/test_dynamic_library_sync.py -q`: `71 passed in 16.35s`.
- `pytest tests/test_s3a_m1_manual_sync_execute.py -q`: `81 passed in 97.79s`.
- `pytest tests/test_s3a_m2_delta_e2e.py -q`: `29 passed in 7.75s`.
- `pytest tests/test_phase_contracts.py -k "s3a_m2 or public_redaction" -q`: `59 passed, 220 deselected in 0.90s`.
- `scripts/check_phase_contract.py --contract s3a_m2_production_delta_e2e_contract_v1 --summary docs/reports/s3a-m2-production-delta-e2e-summary.json --explain`: passed; `target_met_claimed=false`.
- `scripts/check_phase_contract.py --contract public_redaction_contract_v1 --summary docs/reports/s3a-m2-production-delta-e2e-summary.json --explain`: passed; findings `0`.
- `json.tool` for `docs/reports/s3a-m2-production-delta-e2e-summary.json`: passed.
- `git diff --check`: passed; only Windows CRLF normalization warnings were printed.
- `git diff --cached --check`: passed.
- Run #16 focused coverage added: retryable import failure-budget stop continues downstream for already imported media; GUI validator accepts `completed_with_failures` only when imported downstream stages and tag/Entity/SourceConcept semantics pass.
- Authorized priority backlog repair: pre-audit candidate `22698`; DB backup created; row-level snapshot created; repair executed; after-audit candidate `0`, `legacy_pending_changed_rows=0`, `rows_that_need_repair_or_migration=0`.
- Repeated local-copy incremental E2E: passed; `850` copied local JPG/PNG files; `11` sequential cycles; isolated test DB/storage/source root; no production DB or source/iCloud mutation; plan expensive ops `0/0/0/0` across cycles; partial-import downstream recovery cycle passed with `3/3` seeded follow-up items completed and `0` duplicate Media rows.
- Real browser normal-flow validation: passed against isolated test server on port `8024`; normal Start manual sync clicked; browser confirmation accepted; `/manual-sync/execute` observed; isolated GUI-created test job completed; Advanced exact phrase was not used.
- Production GUI Execute validation: not performed by CodeX in this continuation. The user-performed production GUI Execute run `#16` failed on the previous head before this bounded fix; a new user retry and validator pass are still required.

## Current Merge / Retry Status

- Manual sync safety judgement: `manual_sync_not_yet_safe_gui_execute_unvalidated`.
- Remaining blockers:
  - `real_production_gui_execute_acceptance_retry_required_after_run16_failure`: final production GUI Execute must be retried by the user on the fixed head, then `scripts/validate_s3a_m2_gui_execute_acceptance.py` must pass.
  - `historical_confirmed_non_target_ai_wd_assignments_need_owner_repair_or_explicit_deferral_acceptance_before_merge`: #7/#8 confirmed `non_anime` AI WD assignment cleanup is still pending; `unknown` is not part of default destructive repair.
  - `latest_head_codex_re_review_pending_after_this_local_commit_push`: after this commit is pushed, reviewer should re-check the new exact head.
- After this bounded fix passes validation and is pushed, the user may retry production GUI acceptance after pulling the fixed head and restarting the production launcher/server. This is a **retry permission**, not a merge-ready or normal-use-safe claim.
- PR #126 is not merge-ready until a post-fix production GUI run completes with acceptable terminal status and the GUI validator passes, and until the historical confirmed non-target repair is either executed or explicitly accepted as deferred debt.

## Safety

- Source/iCloud mutation attempted: `False`.
- Automatic/scheduled/startup/system-service sync enabled: `False` / `False` / `False` / `False`.
- Provider/Pixiv/gallery-dl/SauceNAO/Google/source expansion run: `False`.
- SourceConcept/Entity bridge work: `False`.
- Production DB mutation in this continuation: `True`, limited to authorized `DynamicSourceItem` source-ledger terminalization for `22698` audited stale priority backlog rows.
- Production media import/classification/AI/localization in this continuation: `False` / `False` / `False` / `False`.
- Cleanup/delete/reset/drop/truncate: `False`.
- Private paths or hashes in public report: `False`.
- Private artifacts committed: `False`.

## Not Completed

- Automatic/scheduled/startup/system-service sync was not implemented; it remains out of scope.
- Pixiv/provider/gallery-dl/SauceNAO/Google/source metadata expansion was not run.
- SourceConcept/Entity bridge work was not run.
- Final user-performed launcher/Web Admin production GUI Execute acceptance run newer than run #8 has not completed yet.
- GUI acceptance validator has not passed for a production GUI-created run on this repaired head.
- Historical confirmed `non_anime` AI WD assignment repair remains pending owner repair/deferral decision.
- The historical deferred/failed inventory still contains unsupported/out-of-scope rows; after the authorized repair it is no longer dominated by the `22698` stale pending/changed priority backlog.

No source paths, filenames, content hashes, API keys, prompts, source URLs, or original image bytes are included in this public report.
