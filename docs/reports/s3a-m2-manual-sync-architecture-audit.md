# S3A-M2 Manual Sync Architecture Consolidation Audit

## Scope And Status

- PR: `#126`.
- Branch: `codex/s3a-m2-production-delta-e2e-gpu-telemetry`.
- Scope: current S3A-M2 Web Admin manual sync path only.
- Public-safety: aggregate/status semantics only; no raw source paths, filenames, private IDs, prompts, credentials, or source hashes are included.
- Production mutation in this audit: none. The audit describes code/state behavior and read-only evidence. The separately authorized stale priority backlog repair is reported in the main S3A-M2 report.
- Acceptance status: this audit is a pre-acceptance consolidation gate. It does not itself make production GUI Execute accepted.

This is a full-system consistency audit of the current S3A-M2 manual sync architecture. It is not a compatibility check for only the newest app-media-backed follow-up patch. Older mechanisms and newer mechanisms are evaluated under the same current acceptance rule: a normal manual sync must discover current work quickly, process already-imported downstream-incomplete media, avoid duplicate imports, keep planning metadata-only, and report DB truth.

## Canonical Responsibility Model

### Normal Plan

Normal planning is metadata-only. It must not read image contents, compute content hashes, decode images, or trigger iCloud hydration/download. Its job is to build a bounded, truthful work batch:

1. App-media-backed downstream follow-up for already-imported media with incomplete classification / AI tagging / localization.
2. Retryable source failures from previous runs, visible as retry candidates or long-term diagnosis work.
3. Current source-ledger actionable rows that are not stable no-op and are not already owned by app-media follow-up.
4. Ledger-missing filesystem metadata candidates, including unknown files with old preserved mtimes.
5. Cloud/iCloud placeholder candidates as visible retry/deferred items, not silent disappearance.
6. Unsupported / hidden / zero-byte inventory as stable skipped or failure categories with public-safe reasons.
7. Stable existing-media / duplicate / historical deferred inventory as diagnostics/no-op, never as batch-cap consumers.

Cap semantics are actionable-batch semantics: cap applies to import candidates plus downstream follow-up candidates. Stable no-op inventory and unchanged existing media may be counted in diagnostics, but must not consume the actionable cap.

### Normal Execute

Normal execute owns all expensive and mutating app-side work. It must never mutate source/iCloud files. The canonical order is:

1. `downstream_followup_planned` first, from app-managed media.
2. `import_planned` next, with source file revalidation, hydration/read handling, hash/integrity, duplicate/existing detection, and DB/app-storage import.
3. Classification for all media imported or followed up in the run, unless classification is stably deferred/failed.
4. AI tagging only for target/unknown-eligible media after classification gates pass; confirmed non-target media are stable skipped.
5. Localization or stable localization reason after AI tagging.
6. Final summary/status based on DB truth, retryable source failures, and downstream completion.

Failure budget may stop further source imports when retryable read/hydration failures exceed policy, but it must not stop downstream stages for media already imported into DB/app storage. Cancellation remains different: cancellation stops downstream and must not call providers or write localization side effects after cancel.

### Validate

Validation checks DB truth, not only UI stage cards. A run can be accepted as `completed_with_failures` only when remaining failures are retryable source failures and imported media are downstream-complete or in explicit stable follow-up/deferred states. Validation must fail if imported media are stranded in pending/waiting downstream states.

## Canonical Plan Precedence

| Priority | Candidate class | Owner | Cap treatment | Source readability required | Notes |
|---:|---|---|---|---|---|
| 0 | App-media-backed follow-up from `not_processed_budget_stop` / imported recovery | Plan follow-up DB pass | Counts as downstream follow-up | No | Fixes run #16/#17 recovery; cannot be hidden by filesystem, mtime, or stable skip. |
| 1 | Other app-media-backed downstream follow-up | Plan follow-up DB pass | Counts as downstream follow-up | No | Includes imported rows with incomplete downstream status and app-managed media evidence. |
| 2 | Retryable source failures from prior runs | Source ledger priority pass | Counts only when retried as current work | Yes for re-import, no for app-media follow-up | Must remain visible and retryable; may transition to needs-diagnosis after repeated attempts. |
| 3 | Current ledger-missing supported metadata candidates | Filesystem metadata fallback | Counts as import candidates | Yes at execute only | Includes old-mtime unknown files; plan must not silently skip them due watermark. |
| 4 | Current source-ledger pending/changed import candidates | Source ledger priority pass | Counts as import candidates | Yes at execute only | Stale media-backed pending rows should be repaired/terminalized, not normal priority work. |
| 5 | Cloud/iCloud placeholders | Filesystem/ledger metadata | Usually deferred/retry visible | Not for plan | Normal planning records state; execute handles safe read/hydration failure. |
| 6 | Unsupported / hidden / zero-byte inventory | Filesystem/ledger metadata | Stable skipped/failure diagnostics | No | Must explain non-imported items. |
| 7 | Stable existing media / duplicate / historical no-op | Ledger/media evidence | Does not consume actionable cap | No | Must not hide downstream-incomplete imported media. |
| 8 | Historical deferred inventory | Report/diagnostics | Does not consume actionable cap | No | Visible in advanced diagnostics; not normal current delta. |

If two mechanisms can select the same source item, the higher priority owner wins and lower-priority scans must de-duplicate. If two mechanisms can write the same `DynamicSourceItem` field, execute-stage state transitions win over planner diagnostics, and validator/report reads DB truth after commit.

## Mechanism Inventory And Decisions

| Mechanism / helper / module | File/function | Purpose | Introduced for which incident | Owner stage | Decision | Reason | Regression test coverage | Known risks |
|---|---|---|---|---|---|---|---|---|
| App-media-backed downstream follow-up discovery | `dynamic_library_sync_service._manual_plan_app_media_followup_source_items` | Find imported media with incomplete downstream processing without relying on source files. | Run #16/#17 stranded imported media. | Plan | Keep / canonicalize now | It is the canonical path for already-imported follow-up and must outrank source walks. | `test_manual_sync_dry_run_recovers_imported_downstream_incomplete_without_source_file`; local-copy cycle 11. | May surface older existing-media follow-up rows; priority buckets keep run-recovery rows first. |
| Media-backed follow-up predicate | `_manual_plan_media_backed_requires_followup` | Decide whether an imported/media-backed row needs classification/AI/localization. | Run #16/#17. | Plan | Keep | Prevents stable no-op and mtime filters from hiding downstream-incomplete media. | Dynamic sync follow-up tests. | Must stay aligned with validator terminal status rules. |
| Source-ledger priority workset | `_manual_plan_priority_source_files` and related candidate logic | Surface known ledger rows before filesystem fallback. | Earlier source-delta planner and stale backlog incidents. | Plan | Refactor / keep constrained | Still needed for retryable failures and true pending/changed rows, but cannot own app-media follow-up or stale no-op backlog. | Legacy backlog E2E, old pending tests, priority plus filesystem fallback tests. | Stale historical rows can distort planning if DB repair/health checks are absent. |
| Filesystem metadata walk fallback | normal plan filesystem traversal | Discover ledger-missing files, including old-mtime copied/iCloud files. | Old-mtime unseen file and `selected=0` production failure. | Plan | Keep | Required because mtime watermark alone can miss copied files preserving old timestamps. | Local-copy cycles 2-5 and old-mtime regression tests. | Needs continuation reporting for very large roots; must remain metadata-only. |
| Mtime watermark | source-root scan/import watermark | Fast path for recent candidates. | Plan performance redesign after 3727s plan failure. | Plan | Keep as optimization only | Useful for small current deltas but cannot be exclusionary for unknown files or app follow-up. | Old-mtime unseen tests, local-copy old-mtime cycle. | Wrong if treated as the only discovery boundary. |
| Safety lookback window | normal plan recent-window inclusion | Avoid missing files around watermark drift. | Incremental scanner redesign. | Plan | Keep | Good fast-path guard, but lower priority than app follow-up and ledger-missing discovery. | Safety-window tests. | Can produce extra candidates; execute handles duplicates. |
| Old-mtime backfill / ledger-missing discovery | filesystem metadata fallback for unknown old files | Catch copied/iCloud files whose mtime predates watermark. | Production job #15 selected zero after new files. | Plan | Keep | Required correctness mechanism. | Local-copy cycle 4 and cap/order tests. | Needs metadata cursor/continuation if full root is huge. |
| Stable no-op fast skip | `_manual_plan_existing_requires_followup`, stable reason sets | Avoid reprocessing already represented media. | 994 existing-media batch and stale backlog repair. | Plan | Refactor / keep guarded | Stable no-op must be evaluated after downstream follow-up, never before it. | Follow-up missing-source test; stable skip tests. | Misclassification can hide real follow-up if status sets drift. |
| Existing-media / duplicate handling | import/plan duplicate states and `existing_media_hash` | Prevent duplicate Media rows. | Existing-media dominated plans and job #13 outcome breakdown. | Plan / Execute | Keep | Correct as stable no-op for complete media; not a downstream terminal state by itself. | Duplicate/outcome E2E, execute tests. | Can become stale backlog if ledger rows stay pending/changed. |
| Pending / changed update-check state | `DynamicSourceItem.sync_state/import_status` historical rows | Represent source-ledger candidate state. | Original update-check/source-delta flow. | Plan / State | Merge conceptually | Canonical split is current actionable vs historical stable no-op vs retryable failure. Old pending/changed backlog was repaired; future code should not recreate it. | Priority backlog audit/repair report, legacy backlog E2E. | If future paths write pending/changed without terminalization, backlog can recur. |
| Retryable source failure handling | retry reason sets and `metadata_json.manual_sync_retry` | Keep read/hydration failures visible and retryable. | Run #16/#17 read failures and failure-budget incident. | Plan / Execute | Keep lightweight; defer schema UI | Current PR needs durable visibility without schema migration. | Retryable budget tests, local-copy cycle 11. | Metadata JSON is less queryable than a dedicated retry ledger. |
| Cloud/iCloud placeholder handling | cloud state checks and placeholder reasons | Safely record cloud-only or hydration/read failures. | iCloud production sync acceptance. | Plan / Execute | Keep | Per-item source availability failures are expected and must not block unrelated downstream. | Placeholder simulation E2E; scanner tests. | Real iCloud provider stalls remain UX/follow-up work. |
| Unsupported / hidden / zero-byte handling | extension/policy filters and failure reasons | Avoid importing invalid/policy-rejected files. | Outcome-breakdown and normal operator UX. | Plan / Execute | Keep | Needed for transparent non-imported counts. | Outcome breakdown E2E. | Must not be conflated with source-read failure or non-target classification. |
| Cap-limited actionable batch | max-files semantics and `more_batches_remain` | Bounded production batches. | Cap-limited batch P1. | Plan / Execute | Keep | Cap means actionable work, not raw visited files. | Cap 1001 tests, local-copy cap cycles. | UI must make continuation clear. |
| Continuation / more-batches semantics | plan limits/continuation fields | Tell operator whether more work remains. | Partial-scan/zero-import UX failures. | Plan / UI | Keep | Prevents dead disabled execute sections. | Browser/manual-flow tests, local-copy metrics. | Full cursor model still deferred beyond current acceptance. |
| Expensive operation counters | plan limits `content_reads/hashes/decodes/hydrations` | Prove plan stays metadata-only. | 3727s `checking_supported` plan incident. | Plan / Validate | Keep | Critical performance contract. | Local-copy E2E and contracts. | Counters must wrap all plan paths, including future diagnostics. |
| Import planned path | `manual_sync_execute_service` import branch | Validate/read/hash/hydrate/import new candidates. | Original manual execute capability. | Execute | Keep | Expensive work belongs here, not plan. | Manual execute tests, E2E. | Source failures must not strand existing imported media. |
| Downstream follow-up execute path | execute branch for `downstream_followup_planned` | Complete classification/AI/localization for existing media. | Run #16/#17. | Execute | Keep / canonicalize | Required to recover imported incomplete media from app-managed storage. | Execute follow-up tests, cycle 11. | Must update source item/run item identity, not bare media_id only. |
| Execute plan item ordering | `_order_manual_sync_execute_plan_items` | Process follow-up before new imports. | Run #17 persisted plan ordered imports before follow-up. | Execute | Keep | Defensive invariant: execution order cannot depend on stale private plan order. | `test_manual_sync_execute_prioritizes_followup_before_import_failure_budget`. | Could surprise if UI displays unsorted plan; summary should use grouped stages. |
| Source file revalidation | import execute path | Ensure file still exists/unchanged before import. | Stale plan/content safety. | Execute | Keep | Prevents stale content execution. | Execute tests. | Not used for app-media follow-up. |
| App-managed media fallback | follow-up execute path | Use stored media file when source file missing. | Run #16 recovery requirement. | Execute | Keep | Follow-up must not require source/iCloud readability. | Missing-source follow-up tests. | App storage missing must produce stable failure. |
| Failure budget | manual execute failure budget | Stop systemic source-read problems. | Cloud-aware ingestion safety. | Execute | Refactor / keep | Source-read budget stops further imports only; does not stop downstream for imported media. | Retryable budget continuation tests. | Catastrophic failures still need fail-closed handling. |
| Retryable source failure classification | retryable reason allowlist | Separate item-level source failures from systemic failures. | Run #16 status semantics. | Execute / Validate | Keep | Enables completed-with-failures status without hiding failures. | Retryable tests and validator checks. | New failure reasons must be classified deliberately. |
| Completed-with-failures semantics | run status / validator terminal rules | Truthful terminal status with retryable leftovers. | Run #16. | Execute / Validate / UI | Keep | Prevents plain failed when downstream completed, and prevents plain completed when source failures remain. | Validator tests, contract. | Must stay DB-truth based. |
| Cancellation semantics | cancel checks before downstream/localization | Prevent provider/LLM side effects after cancel. | Reviewer P1 cancellation incident. | Execute | Keep | Cancellation is not the same as retryable source failure. | Cancellation regression tests. | Needs periodic audit when adding downstream stages. |
| Localization finalizer | manual execute localization stage | Complete translations/stable localization reasons. | Manual E2E completeness. | Execute | Keep | Required for acceptance and validator. | Localization tests. | Provider failures must not be hidden as success. |
| Classification-before-AI order | content classification gate | Prevent WD/anime AI tags on confirmed non-target content. | Job #13 non-target contamination. | Execute | Keep | Business rule; unknown is not non-target. | Unknown-vs-non_anime tests. | Classifier unavailable must be blocker/deferred, not non-target skip. |
| AI tag assignment policy | media tag write semantics and validator checks | Mature high-confidence media tags, no Entity/SourceConcept truth. | AI tag assignment incident. | Execute / Validate | Keep | Current policy allows high-confidence media tags but forbids truth pollution. | Tag semantics tests/validator. | Future entity phases must not reinterpret these as truth. |
| GUI validator | `validate_s3a_m2_gui_execute_acceptance.py` | Verify real GUI run provenance and DB truth. | GUI acceptance proof. | Validate | Keep / align | Must fail if imported media stranded, accept retryable-only failures with downstream complete. | Validator tests. | Spoofable GUI provenance limitations remain documented unless browser/user evidence supplied. |
| S3A-M2 phase contract | `check_phase_contract.py` contract | Prevent reports from claiming target met incorrectly. | Executable contract rule. | Validate | Keep | Report truthfulness gate. | Phase contract tests. | Must be updated when summary schema changes. |
| Public redaction contract | `public_redaction_contract_v1` | Prevent private path/hash leakage. | Report safety. | Validate | Keep | Required for public reports. | Phase contract tests. | New reports must be scanned. |
| Local-copy incremental E2E | `run_s3a_m2_local_copy_incremental_e2e.py` | Repeated isolated sync cycles using copied local images. | Production incremental failures and process miss. | Validate | Extend now | Now includes partial-import + downstream recovery scenario. | Local-copy cycle 11. | Fast provider differs from production providers; production GUI remains final acceptance. |
| Browser normal-flow validation | Playwright/real browser flow | Prove normal UI flow, confirmation, stages. | GUI product-flow failures. | Validate / UI | Keep | API tests are insufficient for operator workflow. | Browser normal-flow evidence. | Computer Use remains tool-limited, not acceptance proof. |
| Manual acceptance prep script | `scripts/prepare_s3a_m2_manual_gui_acceptance.ps1` | Safe preflight/start/open helper for user retry. | Repeated manual preflight burden. | Operator tooling | Keep separate | Startup/preflight only; not sync business logic. | Script safety test. | Must fail closed on ambiguous server/profile state. |
| Public reports/private artifacts | docs reports + `.local_manifests` | Public-safe evidence and private diagnostics. | Incident/postmortem requirements. | Report | Keep | Required for truthful handoff. | Redaction contract. | Reports are not proof without executable checks. |

## Mechanism Conflict Matrix

Legend: `compatible`, `precedence-defined`, `conflict-fixed-now`, `conflict-deferred`, `not-applicable`.

| Mechanism pair | Interaction status | Current resolution |
|---|---|---|
| App-media follow-up vs source-ledger priority workset | `conflict-fixed-now` | App-media follow-up is a first-class DB pass and de-duplicates source-ledger/filesystem candidates by source item id. |
| App-media follow-up vs filesystem metadata walk | `precedence-defined` | Follow-up does not require source readability and is selected before filesystem candidates. |
| App-media follow-up vs mtime watermark/safety window | `conflict-fixed-now` | Mtime filters cannot hide media-backed downstream-incomplete rows. |
| App-media follow-up vs stable existing-media no-op | `conflict-fixed-now` | Downstream-incomplete imported rows are checked before stable no-op classification. |
| App-media follow-up vs existing/duplicate handling | `precedence-defined` | Existing/duplicate is no-op only if downstream is terminal; incomplete downstream remains follow-up. |
| App-media follow-up vs cap-limited batch | `precedence-defined` | Follow-up consumes actionable cap and has higher priority than imports for recovery rows. |
| App-media follow-up vs execute ordering | `conflict-fixed-now` | Execute stably processes follow-up before imports even if private plan order is stale/misordered. |
| App-media follow-up vs source file revalidation | `precedence-defined` | Follow-up validates app-managed media, not original source file. |
| App-media follow-up vs validator terminal rules | `compatible` | Validator must require downstream completion or stable follow-up for imported media. |
| Source-ledger priority vs filesystem metadata fallback | `conflict-fixed-now` | Priority workset can no longer stop before filesystem fallback when cap remains; ledger-missing new files must be discovered. |
| Source-ledger priority vs stale pending/changed backlog | `conflict-fixed-now` | Authorized production repair terminalized audited stale backlog; future stale rows are diagnostics/health issue, not normal priority. |
| Source-ledger priority vs retryable source failures | `precedence-defined` | Retryable failures remain visible, but do not block unrelated imports/follow-up. |
| Source-ledger priority vs downstream follow-up | `conflict-fixed-now` | App-media follow-up owns imported incomplete rows before source-ledger priority rows. |
| Filesystem metadata walk vs mtime watermark | `compatible` | Watermark is fast path; metadata fallback catches old-mtime unknown files. |
| Filesystem metadata walk vs cap | `precedence-defined` | Raw visited files do not consume actionable cap; selected candidates do. |
| Filesystem metadata walk vs plan expensive ops | `compatible` | Normal walk is stat/metadata only; counters must stay `0/0/0/0`. |
| Mtime watermark vs old-mtime backfill | `conflict-fixed-now` | Unknown old-mtime files are not permanently skipped. |
| Stable no-op vs retryable failures | `precedence-defined` | Retryable failure reasons are not stable no-op; they remain visible/retryable or long-term diagnosis. |
| Stable no-op vs downstream-incomplete imported media | `conflict-fixed-now` | No-op cannot hide downstream-incomplete media. |
| Existing/duplicate skip vs duplicate Media prevention | `compatible` | Duplicate detection prevents duplicate rows; complete duplicates become stable no-op. |
| Existing/duplicate skip vs downstream follow-up | `precedence-defined` | Existing-media hash rows with incomplete downstream can be follow-up, lower priority than run recovery rows. |
| Retryable failure metadata vs failure budget | `compatible` | Budget may stop imports; metadata keeps retry state visible for next plan. |
| Failure budget vs downstream continuation | `conflict-fixed-now` | Retryable source failure budget does not stop downstream for already imported media. |
| Failure budget vs cancellation | `precedence-defined` | Cancellation remains fail-closed; it does stop downstream and provider calls. |
| Execute import path vs downstream follow-up path | `conflict-fixed-now` | Execute ordering makes follow-up first; import failures cannot starve follow-up. |
| Downstream follow-up path vs duplicate Media rows | `compatible` | Follow-up uses existing `media_id` and app-managed media; it does not create Media rows. |
| Localization finalizer vs cancellation | `compatible` | Cancel check before finalizer prevents LLM/provider side effects after cancel. |
| Classification gate vs AI tagging | `compatible` | Confirmed non-target skips AI/localization; unknown is not non-target. |
| AI media tags vs Entity/SourceConcept truth | `compatible` | AI-only tags may be media tags under policy, but cannot create Entity/SourceConcept truth. |
| GUI plan summary vs validator/DB truth | `precedence-defined` | UI is advisory; validator/contract reads DB truth. |
| Local-copy E2E vs production behavior | `conflict-deferred` | Fast deterministic providers differ from production providers; production GUI Execute remains required. Non-blocking because E2E validates workflow/state mechanics, not provider quality. |
| Manual prep script vs sync business logic | `not-applicable` | Script only prepares environment and opens UI; it must not call plan/execute. |

## Conflict Answers By Mechanism Class

- Can a mechanism suppress another candidate set? Previously yes: source-ledger/filesystem ordering plus execute ordering suppressed run #16 downstream follow-up. Fixed by app-media follow-up precedence and execute sorting.
- Can a mechanism reclassify actionable work as stable no-op? Previously possible for imported media if stable existing-media logic ran before downstream checks. Fixed by media-backed follow-up predicates before no-op logic.
- Can ordering make a mechanism unreachable? Previously yes: import candidates before follow-up allowed failure budget to stop before follow-up. Fixed by execute group ordering.
- Can cap be consumed before higher-priority work? Previously yes for stale backlog/existing media. Fixed by actionable cap semantics, stale backlog repair, and follow-up priority.
- Can source-file readability block app-managed follow-up? Previously possible. Fixed: app-media follow-up does not require source file readability.
- Can state needed by another mechanism be overwritten? Risk existed for downstream follow-up `content_hash`/`media_id`; current code preserves source item identity, media id, relative path hash, and known content hash for follow-up.
- Can retryable work become permanent no-op too early? Retryable source reasons are now separated from stable no-op reasons. Long-term `needs_diagnosis` is metadata-only and still visible, not silent no-op.
- Can reports/validators pass while DB truth is incomplete? Validator/report semantics now require imported media downstream completion or stable follow-up/deferred states; stage cards alone are not proof.
- Can duplicate Media rows be created? Follow-up path uses existing media id and does not import. Import path still handles duplicate/existing content during execute.
- Can imported media be stranded? This was the run #16/#17 bug. The current model prevents recurrence with plan follow-up discovery plus execute follow-up-first ordering.
- Can production differ from E2E? Provider runtime can differ. Current E2E is scoped to planner/execute/state/UI workflow; production GUI Execute remains required for final acceptance.
- Can plan be metadata-only in tests but not production? Expensive operation counters are part of production plan output and E2E/contract evidence. Any future plan path must keep counters at zero or be Advanced repair mode only.

## State Model

### `DynamicSourceItem.sync_state`

- Terminal success: `imported`, `skipped_existing_media`, `skipped_duplicate`.
- Terminal stable no-op: `skipped_unsupported`, `skipped_hidden`, `skipped_zero_byte`, `skipped_existing_media`, `skipped_duplicate`, `skipped_placeholder` only when the reason is explicitly stable and no downstream follow-up is needed.
- Retryable: `failed`, `deferred_unprocessed` when paired with retryable source reasons such as `read_error`, `read_timeout`, `source_missing`, `permission_denied`, `cloud_hydration_failed`, `cloud_network_unavailable`, `icloud_placeholder`.
- Downstream follow-up: `imported`, `deferred_unprocessed`, or media-backed existing states when `media_id` exists and classification/AI/localization is incomplete.
- Should not remain after a completed run: imported media with downstream `pending` / `waiting_*` and no stable deferred/failure reason or follow-up visibility.

### `DynamicSourceItem.import_status`

- Terminal success: `imported`.
- Stable no-op: `skipped`, `existing_media_hash`, `duplicate_hash` or equivalent no-op reasons when downstream is terminal.
- Retryable: `failed`, `deferred`, `pending` with retryable source reasons.
- Downstream follow-up: `imported` or media-backed representation with incomplete downstream states.
- Should not remain after completed import: `import_in_progress` or `pending` for media that was already imported unless it is explicitly retry/follow-up classified.

### `classification_status`

- Terminal success: `classified`, `classified_reused`.
- Retryable/deferred: `deferred`, `failed`, `blocked`, provider/model unavailable reasons.
- Downstream follow-up: missing, `pending`, `waiting_import`, `deferred`, or `failed` for media-backed rows.
- Should not remain invisible after completed run: `pending` for imported media without follow-up selection.

### `ai_tagging_status`

- Terminal success: `ai_tagged`, `tagged`, `tagged_reused`.
- Stable no-op: `ai_tagging_skipped_non_target`, `skipped_non_target` for confirmed non-target only.
- Retryable/deferred: `deferred`, `failed`, provider errors, classification-not-ready blockers.
- Downstream follow-up: missing, `pending`, `waiting_import`, `waiting_ai_tags`, `deferred`, `failed` for media-backed rows.
- Should not remain invisible after completed run: `pending` for imported media without follow-up selection.

### `localization_status`

- Terminal success: `localized`, `completed`.
- Stable no-op: `skipped_no_localizable_tags`, `skipped_no_new_tags`, `skipped_static_coverage`, `localization_not_applicable_non_target`.
- Retryable/deferred: `deferred`, `failed`, provider/translation unavailable reasons.
- Downstream follow-up: `waiting_ai_tags`, `waiting_localization`, `deferred`, `failed` for media-backed rows.
- Should not remain invisible after completed run: `waiting_ai_tags` for imported media without follow-up selection.

### Reasons And Identity Fields

- `failure_reason` records retryable or fatal item failures. Retryable source reasons remain visible for future manual sync.
- `deferred_reason` records why an item is not processed now; `not_processed_budget_stop` is follow-up/retry visible, not stable no-op.
- `media_id` is the app-managed media anchor for downstream follow-up.
- `content_hash` and `relative_path_hash` are source-ledger identity evidence; downstream follow-up must preserve them and never overwrite them with null.
- `last_sync_run_id` / `last_seen_run_id` are provenance pointers; they must not be used alone to decide terminal state.
- `metadata_json.manual_sync_retry` is the current lightweight retry ledger with `attempt_count`, `last_retry_at`, `last_failure_reason`, `retryable`, and `long_term_state`. A dedicated schema/UI remains deferred.

## Validation And Report Mechanisms

- GUI validator: production GUI acceptance proof; checks GUI provenance, current head, DB run status, imported-media downstream completion, tag assignment semantics, no Entity/SourceConcept truth pollution, remaining importable/follow-up state, and public redaction.
- S3A-M2 contract: prevents `target_met=true` until real GUI Execute acceptance passes.
- Public redaction contract: scans public summaries/reports for private path/hash leakage.
- Local-copy incremental E2E: isolated test DB/storage/source, copied local images only, repeated cycles, metadata-only plan proof, now extended with partial-import downstream recovery.
- Browser normal-flow validation: proves normal operator flow and confirmation behavior, distinct from production acceptance.
- Manual acceptance preparation script: operator preflight/start/open only; not an acceptance runner.
- Public reports: explain incidents and evidence; private `.local_manifests` hold raw evidence and are not committed.

## Cleanup Decisions

### Refactored Now

- App-media-backed downstream follow-up is canonical and independent of source-file readability.
- Stable no-op classification now yields to downstream follow-up for imported/media-backed incomplete rows.
- Execute now processes downstream follow-up before imports, preventing retryable source failures from starving follow-up.
- Retryable source failure reasons are separated from stable no-op reasons and write lightweight retry metadata.
- Validator/report semantics distinguish retryable source failures from true pipeline failure.

### Kept As-Is

- Metadata-only normal planning and expensive-operation counters.
- Import-time validation/hash/hydration/duplicate handling.
- Classification-before-AI and confirmed non-target skip policy.
- AI-only tag truth barrier for Entity/SourceConcept.
- Public/private artifact split and redaction contract.

### Merged Conceptually

- `priority workset`, `pending/changed`, and `historical backlog` are no longer treated as one undifferentiated current-delta concept. The canonical categories are current actionable import, app-media downstream follow-up, retryable source failure, stable no-op, and historical diagnostics.
- `retryable failure` and `deferred_unprocessed` are not synonyms. `deferred_unprocessed` must carry a reason that determines whether it is follow-up, retryable, stable no-op, or diagnostic.

### Isolated Or Deferred

- Durable retry attempt schema/table and long-term failure UI are deferred. Current PR stores retry metadata in `metadata_json.manual_sync_retry` and reports it.
- A full persistent filesystem cursor/checkpoint remains deferred because current correctness is covered by source ledger + metadata fallback + actionable cap + local-copy E2E. Future scale work can improve performance further.
- Broad cloud hydration UX and automatic retry scheduler are deferred to later phases.
- Computer Use validation remains tool-limited on this Windows environment and is not used as acceptance proof.

## Why This Prevents Run #16 / #17 Recurrence

Run #16 stranded 155 already-imported media because retryable import failures stopped downstream. Run #17 proved that same-run downstream continuation worked, but old imported incomplete rows were still starved because imports were ordered before follow-up and the follow-up discovery was not canonical.

The current architecture blocks both failure modes:

1. Plan discovers app-media-backed downstream follow-up before source walks, mtime windows, stable no-op, or import candidates.
2. Execute processes downstream follow-up before imports regardless of persisted private plan order.
3. Retryable source failure budget stops further source imports only; it cannot prevent downstream processing for imported media.
4. Validator fails if imported media remain downstream-incomplete without stable follow-up/deferred state.
5. Local-copy E2E now includes the partial-import recovery shape that earlier tests missed.

## Remaining Non-Blocking Risks

- Production provider runtime and real iCloud behavior can still expose retryable read/hydration failures; final production GUI Execute remains required.
- The retry metadata is lightweight JSON, not a dedicated query-optimized retry table.
- Older existing-media follow-up rows are now visible and may require additional batches; this is acceptable because they do not hide run #16 recovery rows and are processed from app-managed media.
- Future phases should add a health check that warns if abnormal priority backlog or invisible imported-downstream-incomplete rows reappear.
