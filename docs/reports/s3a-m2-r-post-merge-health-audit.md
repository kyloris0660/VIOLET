# S3A-M2-R Post-Merge Manual Sync Health Audit

## Scope

- Mode: production read-only audit; no Execute, no DB writes, no source/iCloud mutation.
- Base merge commit verified by branch start: `ff5972b0685def18bd658746e2ba1e3043c28d02`.
- Audit head: `ff5972b0685def18bd658746e2ba1e3043c28d02` on branch `codex/s3a-m2-r-manual-sync-stabilization`.
- Production root audited: `2 / icloud-photos-production`.
- Raw private evidence root: `.local_manifests/s3a_m2_r/post_merge_health/`.

## R0 Findings

### 880 downstream follow-up rows

No. The 880 run #18 follow-up rows were actionable at run start as app-media-backed downstream work, but after run #18 they are terminal/stable: 880/880 are downstream complete and 0 of those rows remain current follow-up. A separate older 20-row media-backed/source-missing debt remains and needs canonical APP_MEDIA_FOLLOWUP/BROKEN_STATE handling.

| Question | Answer |
|---|---|
| Were all 880 genuinely actionable follow-up items? | no; they were actionable at run start, but 0 remain actionable after run #18. |
| Redundant/duplicate/no-op after run? | yes; classify as stable diagnostic unless downstream status regresses. |
| Did they merely pass through planned state without work? | no; statuses show downstream completion. |
| Can similar rows accumulate forever? | yes; yes if `downstream_followup_planned` is retained as a historical state. |
| Can they consume future caps? | no; current planner reports 0 downstream follow-up next plan. |
| Expected to reappear next normal plan? | no. |
| Recommended disposition | Mark through canonical lifecycle as STABLE_NOOP/HISTORICAL_DIAGNOSTIC unless downstream status regresses. |

### 120 planned imports

Run #18 reconciles as 120 planned import-family items: 34 imported, 11 retryable failures, 75 deferred continuation after `stopped_by_failure_budget`.

- `completed_with_failures` is a partial success for run #18: DB truth for processed imports/follow-up passed, but source-read retry/deferred continuation remains.
- Operator wording should become `completed_with_retryable_failures`; validator/report should not call it a clean completed run or a systemic failure.
- Run #18 deferred imports visible in next plan by public hash prefix: 75 / 75; first position `167`, last `241`.

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
| Imported but downstream-incomplete under root 2 | 0 | Acceptance criterion currently satisfied. |
| Invisible app-media incomplete rows | 0 | None found. |
| Historical backlog that can consume normal cap | 0 | None found by current-priority classifier. |

### Next normal manual sync

| Check | Result |
|---|---|
| Healthy/readable next plan | yes |
| Estimated imports | 347 |
| Estimated downstream follow-up | 0 |
| Retryable source-read failure debt | 11 |
| Retryable/source-missing rows including media-backed old debt | 31 |
| Placeholder debt | 4 |
| Continuation debt | 179 |
| Hidden by mtime/safety window | no |
| Hidden by stable no-op | no |
| App-media incomplete invisible | 0 |
| App-media incomplete caught as current planner follow-up | 0 |
| Plan stat/hash counts | stat `40260`, hash `0` |

Important nuance: the current dry-run planner is read-only, but not purely metadata-only in implementation terms because it stats source entries. The R1 target model should make Plan metadata-only and reserve source reads/hash/decode for Execute.

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

## Path Liveness Audit

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
- The 880 follow-up batch is no longer a hidden long-term blocker in the next normal plan, but `downstream_followup_planned` should stop serving as both historical source state and active work label.
- The 20 older media-backed/source-missing downstream-incomplete rows are visible but semantically misclassified for the desired model: FOLLOWUP should use app-managed media and not depend on source readability.
- The 75 run #18 deferred import candidates are visible in the next normal plan, but the next plan also sees additional import candidates; this needs WorkItem ordering and retry/debt UI rather than another giant execution PR.
- R2/R3/R4 are too large for a single low-risk PR if implemented all at once. This branch should stop at R0/R1 and split implementation into lifecycle/WorkItem core, UI/tooling cleanup, and docs/runbook follow-through.
