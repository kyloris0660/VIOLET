# S3A-M2-R Post-Merge Manual Sync Health Audit

## Scope

- Mode: production read-only audit; no Execute, no DB writes, no source/iCloud mutation.
- Default evidence mode: DB-only/source-safe; no source walk/stat/open/hash/decode/hydration.
- Audited baseline main commit: `ff5972b0685def18bd658746e2ba1e3043c28d02`.
- Generator code head at run: `182059ebe95e7b06d792249eeac9c55a5d2d76d4` on branch `codex/s3a-m2-r-manual-sync-stabilization`.
- Report commit head: `not computed` (A committed report cannot truthfully contain the final self-referential commit SHA; use PR closeout/head metadata after commit.).
- Working tree dirty at generation: yes; tracked dirty files: docs/admin/manual-sync-runbook.md, docs/architecture/manual-sync-state-machine.md, docs/reports/s3a-m2-r-closeout.md, docs/reports/s3a-m2-r-post-merge-health-audit.md, docs/reports/s3a-m2-r-post-merge-health-summary.json, scripts/audit_s3a_m2_r_post_merge_health.py; untracked count: 82.
- Production source root audited: `audited-root` (label redacted: yes; root id redacted: yes).
- Side-effect safety: app config imported `no`, app storage mutation `no`.
- Raw private evidence root: `.local_manifests/s3a_m2_r/post_merge_health/`.

## R0 Findings

### Run #18 record vs current cohort health

| Field | Value |
|---|---|
| Run reconciliation scope | root_scoped_join_dynamic_source_item |
| Other-root run item count | 0 |
| Immutable stage snapshot available | no |
| Historical stage snapshot unavailable | yes |
| Current state used for downstream completion | yes |

PR #126 acceptance used DB truth observed after run #18. Future reruns of this audit may observe later current `DynamicSourceItem` / Media / app-storage health unless comparing against frozen private artifacts from the original audit time.

### 880 downstream follow-up rows

Run #18 recorded 880 downstream_followup_planned run items in the root-scoped run-item record. At run #18 plan time, those rows were genuinely actionable app-media-backed downstream work. At this audit time, current cohort health shows those same 880 rows are downstream complete and should no longer be treated as actionable follow-up. Future similar rows can still accumulate if historical downstream_followup_planned remains overloaded, so lifecycle cleanup is needed. A separate older 20-row media-backed/source-missing debt remains and needs canonical APP_MEDIA_FOLLOWUP/BROKEN_STATE handling.

| Question | Answer |
|---|---|
| Were all 880 genuinely actionable at run #18 plan time? | yes; they were app-media-backed downstream follow-up work. |
| Are those same 880 actionable at this audit time? | no; current cohort health shows 880/880 downstream complete. |
| Redundant/duplicate/no-op after run? | yes; classify as stable diagnostic unless downstream status regresses. |
| Did they merely pass through planned state without work? | no; statuses show downstream completion. |
| Can similar rows accumulate forever? | yes; if `downstream_followup_planned` is retained as a historical state. |
| Can they consume future caps? | not_proven_by_safe_default; safe default does not rerun the source-read-capable planner. |
| Expected to reappear next normal plan? | not_recomputed_by_safe_default; not recomputed in source-safe mode. |
| Recommended disposition | Mark through canonical lifecycle as STABLE_NOOP/HISTORICAL_DIAGNOSTIC unless downstream status regresses. |

### 120 planned imports

Run #18 reconciles as 120 planned import-family items: 34 imported, 11 retryable failures, 75 deferred continuation after `stopped_by_failure_budget`.

- `completed_with_failures` is a partial success for run #18: DB truth for processed imports/follow-up passed, but source-read retry/deferred continuation remains.
- Operator wording should become `completed_with_retryable_failures`; validator/report should not call it a clean completed run or a systemic failure.
- Run #18 deferred imports visible as root-scoped DB continuation rows: 75 / 75; planner ordering is not recomputed in safe default mode.

Safe-default breakdown of the earlier 347 next-plan import-candidate claim:

| Bucket | Count / status |
|---|---:|
| Exact 347 normal-plan import count | not computed |
| Exact count reason | requires opt-in source-read-capable planner path |
| Run #18 deferred continuation rows | 75 |
| Other continuation rows | 104 |
| Known DB continuation total | 179 |
| Known retryable source-read failures | 11 |
| Placeholder rows excluded from import | 4 |
| App-media follow-up candidates, not import | 20 |
| Root-scope stat-error reason rows | 0 |
| Root-scope unsupported reason rows | 4433 |
| Ledger-missing candidates | unknown_without_source_walk_stat |
| Mtime-new / safety-window / old-mtime candidates | unknown_without_source_walk_stat / unknown_without_source_walk_stat / unknown_without_source_walk_stat |

The first R0/R1 attempt reported `347` from the current source-read-capable planner. This safe-default report does not recompute that exact number because the planner path can walk/stat source entries. The DB-only evidence proves the 75 run #18 deferred rows remain visible as continuation and identifies the known DB debt buckets; ledger-missing and mtime-derived filesystem categories require explicit opt-in planner evidence.

### Remaining debt inventory

| Debt | Count | Interpretation |
|---|---:|---|
| Run #18 deferred import candidates | 75 | Continuation, not failed import. |
| All `not_processed_budget_stop` continuation rows | 179 | Historical + current continuation inventory. |
| Retryable source-read failures | 11 | Item-level retry/debt list needed. |
| Retryable/source-missing failure rows including old media-backed rows | 31 | Current status vocabulary mixes read retry and media-backed source-missing debt. |
| Placeholder / cloud hydration rows | 4 | Visible diagnostic/deferred debt. |
| Older app-media-backed downstream-incomplete rows | 20 | App storage exists, but current planner follow-up classifier catches 0; these need canonical APP_MEDIA_FOLLOWUP/BROKEN_STATE handling. |
| Source-missing media-backed downstream-incomplete rows | 20 | Visible as source-missing/retry style debt, not as follow-up. |
| Imported but downstream-incomplete under audited source root | 0 | Acceptance criterion currently satisfied. |
| Invisible app-media incomplete rows | 0 | None found. |
| Historical backlog that can consume normal cap | 0 | None found by current-priority classifier. |

### Next normal manual sync

| Check | Result |
|---|---|
| Evidence mode | db_only_source_safe |
| Source-read-capable planner invoked | no |
| Healthy/readable next plan | not computed; Safe default does not recompute executable planner health because that path may walk/stat source entries. |
| Estimated imports | not computed; not_computed_in_safe_default |
| Estimated downstream follow-up | not computed; not_computed_by_current_planner_in_safe_default |
| Retryable source-read failure debt | 11 |
| Retryable/source-missing rows including media-backed old debt | 31 |
| Placeholder debt | 4 |
| Continuation debt | 179 |
| Hidden by mtime/safety window | unknown_without_source_walk_stat |
| Hidden by stable no-op | no |
| App-media incomplete invisible | 0 |
| App-media incomplete caught as current planner follow-up | 0 |
| Plan stat/hash counts | stat `0`, hash `0` |

Important nuance: the current dry-run planner is not used by default in this R0 audit because it can walk/stat source entries. The R1 target model should make Plan metadata-only and reserve source reads/hash/decode for Execute.

## State-Machine Inventory

too_many_overlapping_state_fields.

- downstream_followup_planned is both historical run item state and a stable post-run source item state
- completed_with_failures mixes acceptable retryable item failures with operator-visible failure wording
- deferred_unprocessed/not_processed_budget_stop represents continuation, not terminal failure
- validator/report status can contradict DB-truth acceptance due field-name redaction false positives

Top current root state counts:

```json
{
  "deferred": 4436,
  "deferred_unprocessed": 179,
  "downstream_followup_planned": 880,
  "failed": 11,
  "imported": 102,
  "missing": 22,
  "skipped_existing_media": 22714,
  "unchanged": 11940
}
```

Report/status fields currently observed:

```json
{
  "gui_execute_acceptance.run_status": null,
  "gui_execute_acceptance.status": "db_truth_acceptance_passed_validator_tooling_deferred",
  "gui_execute_acceptance.validation_script_status": null,
  "launcher_web_admin_acceptance.status": "production_gui_run18_db_truth_acceptance_passed_validator_tooling_deferred",
  "summary.public_redaction.passed": true
}
```

## Design-Level Path Liveness Audit

This is report-level path analysis, not executable formal verification. Executable verification is deferred to PR-R1/R2: lifecycle table-driven tests, WorkItem boundary tests, root-scoped validator tests, report/status contradiction regression tests, and browser validation for UI/progress.

| Path | Terminates | Invisible risk | Cap forever | Blocks unrelated | Report risk | Source mutation | Missing test focus |
|---|---|---|---|---|---|---|---|
| A. New import succeeds fully | yes | no | no | no | no | no | none |
| B. New import succeeds but classification fails | yes | no | yes | no | yes | no | Add lifecycle table case for classification_failed_after_import. |
| C. New import succeeds but AI tagging fails | yes | no | yes | no | yes | no | Add explicit retry/debt visibility assertion. |
| D. New import succeeds but localization fails/deferred | yes | no | yes | no | yes | no | Assert operator status completed_with_followup_required. |
| E. Source read_error/read_timeout before import | yes | no | no | no | yes | no | Attempt-count/debt report tests are missing. |
| F. Source read_error/read_timeout after some imports | yes | no | no | no | yes | no | Failure-budget liveness should be table-driven. |
| G. Downstream follow-up succeeds | yes | no | no | no | no | no | none |
| H. Downstream follow-up source file missing | yes | no | no | no | no | no | none |
| I. Downstream follow-up app storage missing | no | yes | yes | yes | yes | no | Needs BROKEN_STATE classifier and validator coverage. |
| J. Existing-media duplicate with downstream terminal | yes | no | no | no | no | no | none |
| K. Existing-media duplicate with downstream incomplete | yes | no | yes | no | no | no | Move to canonical APP_MEDIA_FOLLOWUP tests. |
| L. Placeholder/cloud hydration failure | yes | no | no | no | yes | no | Needs clearer operator debt UI. |
| M. Failure budget stop | yes | no | no | no | yes | no | Rename/clarify completed_with_failures semantics. |
| N. Cap-limited continuation | yes | no | no | no | no | no | Assert continuation ordering after run-like state. |
| O. User cancellation | yes | no | no | no | no | no | Execute cancel debt visibility needs coverage. |
| P. Filesystem walk error | yes | no | no | no | no | no | none |
| Q. Validator/report update failure | yes | no | no | no | yes | no | Separate DB-truth acceptance from report-tool status. |
| R. Public redaction false positive | yes | no | no | no | yes | no | Field-name false positive regression needed. |
| S. Multiple roots / unrelated root pending work | yes | no | no | no | no | no | Root-scoped validator assertions needed. |
| T. Stale legacy backlog | yes | no | no | no | no | no | Canonical lifecycle should prevent future drift. |

## R0 Conclusion

- PR #126 acceptance was truthful for current-stage DB truth, but current operator wording and reports still blur partial success, retryable item failures, and continuation.
- The run #18 880 follow-up rows were real actionable follow-up at plan time and are terminal after run #18; this safe-default report does not prove future planner cap behavior for similar overloaded historical states.
- The 20 older media-backed/source-missing downstream-incomplete rows are visible but semantically misclassified for the desired model: FOLLOWUP should use app-managed media and not depend on source readability.
- The 75 run #18 deferred import candidates are visible as DB continuation rows. Exact normal-plan ordering/import count requires explicit opt-in source-read-capable planner evidence and is not part of default R0 validation.
- R2/R3/R4 are too large for a single low-risk PR if implemented all at once. This branch should stop at R0/R1 and split implementation into lifecycle/WorkItem core, UI/tooling cleanup, and docs/runbook follow-through.
