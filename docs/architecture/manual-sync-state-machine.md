# Manual Sync State Machine

Status: PR-R1 backend implementation for `S3A-M2-R`, based on the post-merge
read-only audit in `docs/reports/s3a-m2-r-post-merge-health-audit.md`.

This document defines the canonical lifecycle and WorkItem model for production
manual sync. It is not an approval to run production Execute, mutate source
files, start automation, or perform DB cleanup.

## Problem Statement

PR #126 proved that the Web Admin GUI production manual sync path can complete
real DB-truth work, but the state model is now too layered:

- `downstream_followup_planned` is both an active work state and a historical
  source-item state after the work is complete.
- `completed_with_failures` mixes acceptable retryable item failures with
  operator-facing failure wording.
- `deferred_unprocessed` / `not_processed_budget_stop` means continuation, not
  terminal failure.
- App-media-backed follow-up can be confused with source-file retry debt.
- Validator/report status can contradict DB truth when the report tooling has a
  redaction/status false positive.

The R0 audit found:

- Run #18's `880` follow-up rows are complete and should not reappear as
  actionable follow-up.
- Run #18's `75` deferred import candidates remain visible as root-scoped DB
  continuation rows.
- The audited production source root still has `20` older app-media-backed /
  source-missing downstream-incomplete rows; current planner priority sees
  them, but not as canonical app-media follow-up.
- The first R0/R1 attempt reported `347` next-plan imports through the current
  source-read-capable planner. The safe-default R0 audit no longer recomputes
  that exact number because the current planner may walk/stat source entries.
  DB-only evidence can explain known continuation/retry/placeholder/app-media
  buckets, while ledger-missing and mtime-derived filesystem candidates require
  explicit opt-in planner evidence.

## Canonical Lifecycle Classifier

PR-R1 introduces a single classifier in:

`backend/app/services/manual_sync_lifecycle.py`

Public API shape:

```python
class LifecycleKind(str, Enum):
    APP_MEDIA_FOLLOWUP = "APP_MEDIA_FOLLOWUP"
    IMPORT_CANDIDATE = "IMPORT_CANDIDATE"
    RETRYABLE_SOURCE_FAILURE = "RETRYABLE_SOURCE_FAILURE"
    PLACEHOLDER_DEFERRED = "PLACEHOLDER_DEFERRED"
    STABLE_NOOP = "STABLE_NOOP"
    HISTORICAL_DIAGNOSTIC = "HISTORICAL_DIAGNOSTIC"
    CONTINUATION = "CONTINUATION"
    BROKEN_STATE = "BROKEN_STATE"
    FATAL_BLOCKER = "FATAL_BLOCKER"


classify_source_item(
    item: DynamicSourceItem,
    *,
    media: Media | None = None,
    media_lookup_performed: bool = False,
    app_media_exists: bool | None = None,
    current_priority: bool = False,
    attempted_in_run: bool = False,
    run_item: DynamicSyncRunItem | None = None,
) -> LifecycleDecision
```

`LifecycleDecision` should include:

- `kind`
- `work_item_kind`
- `reason_code`
- `operator_label`
- `is_actionable`
- `is_visible_in_normal_ui`
- `consumes_actionable_cap`
- `can_execute`
- `allowed_source_reads`
- `allowed_db_writes`
- `terminal_after_execute`
- `requires_operator_attention`
- `validator_severity`
- `report_bucket`
- `evidence`

The classifier is now the canonical interpretation layer for PR-R1 planner
bucket classification, app-media follow-up discovery, duplicate/stable-noop
distinction, retryable source failure classification, continuation, placeholder,
operator-status mapping, and validator/report debt inventory. Some older local
predicates remain as compatibility adapters, but high-risk decisions are checked
against `LifecycleDecision` in tests.

## Lifecycle Kinds

`APP_MEDIA_FOLLOWUP`

Already has a `media_id` and app-managed media exists, but classification,
AI tagging, or localization is incomplete. It must use app-managed media and
must not depend on original source-file readability. The 20 older
source-missing/media-backed rows from R0 belong here unless app storage is
missing. Public reports must identify the source root by marker/hash rather
than raw label or id.

`IMPORT_CANDIDATE`

No imported media is available for the source item. Execute may revalidate the
source file, hash, copy/import, classify, AI-tag, and localize. Plan should
discover it without content reads.

`RETRYABLE_SOURCE_FAILURE`

A source read/stat/hydration failure prevented import. It is item-level debt.
It may be retried by a retry-source work item, but failure must not block
app-media follow-up or unrelated imports within the approved failure budget.

`PLACEHOLDER_DEFERRED`

Cloud placeholder or hydration-risk item. It is visible diagnostic debt and may
be retried only under an explicit cloud-aware policy. It must not mutate
iCloud/source.

`STABLE_NOOP`

Terminal success or stable skip. It is visible in diagnostics but does not
consume actionable cap and does not execute.

`HISTORICAL_DIAGNOSTIC`

Old ledger/report state retained for provenance. It is not normal actionable
work. It should not crowd out imports or follow-up.

`CONTINUATION`

A planned item was not processed because of cap, cancellation, or budget stop.
It remains visible in the next normal plan and should preserve ordering
semantics. Run #18's 75 deferred import candidates are continuation.

`BROKEN_STATE`

A contradiction requiring operator or repair-phase attention, such as
downstream follow-up with missing app storage, media row missing for a stored
`media_id`, duplicate identity conflict, or impossible status combination.

`FATAL_BLOCKER`

A systemic problem that blocks the whole run: server/DB identity mismatch,
unsafe storage target, invalid manifest, report generation failure, privacy
leak, source/app-storage/DB confusion, or unexpected mutation.

## WorkItem Model

Typed WorkItems should be generated from lifecycle decisions.

| WorkItem kind | Required fields | Source reads | DB writes | Consumes cap | Executes | Terminal state |
|---|---|---|---|---|---|---|
| `FOLLOWUP` | `source_item_id`, `media_id`, stage statuses, app-storage evidence | none | classification/AI/localization status and tag writes only | yes | yes | `STABLE_NOOP` or `BROKEN_STATE` |
| `IMPORT` | `source_item_id` or source identity, root id, relative path hash, size/mtime evidence | source revalidation/hash/copy | source item, media, stage statuses, run item | yes | yes | `STABLE_NOOP`, `RETRYABLE_SOURCE_FAILURE`, or `CONTINUATION` |
| `RETRY_SOURCE` | `source_item_id`, failure reason, attempt metadata | source stat/read only as approved | retry metadata, failure status | yes, but after follow-up | yes | `IMPORT`, `RETRYABLE_SOURCE_FAILURE`, or `PLACEHOLDER_DEFERRED` |
| `PLACEHOLDER` | `source_item_id`, placeholder reason, cloud/source state | none by default | diagnostic/deferred metadata only | no by default | no by default | `PLACEHOLDER_DEFERRED` |
| `NOOP_DIAGNOSTIC` | stable reason, source/media identity evidence | none | none | no | no | `STABLE_NOOP` / `HISTORICAL_DIAGNOSTIC` |
| `BROKEN_STATE` | contradiction evidence, severity, recommendation | none by default | none by default | no | no | blocks validator until explained or repaired |

Critical rules:

- `FOLLOWUP` uses app-managed media. It must not require source readability.
- `IMPORT` uses source file revalidation/hash/copy.
- `RETRY_SOURCE` may touch source, but failure remains item-level.
- `NOOP_DIAGNOSTIC` never consumes actionable cap.
- `BROKEN_STATE` is visible and does not silently disappear behind cap or mtime
  windows.

## Operator Status Vocabulary

Replace vague or overloaded public status with this vocabulary:

| Status | Meaning | Merge/acceptance interpretation |
|---|---|---|
| `completed` | All executable work in the approved batch finished and no retry/debt remains in scope. | Clean success. |
| `completed_with_retryable_failures` | Executable work finished where possible; remaining failures are item-level source/cloud/read failures. | Partial success; acceptable only when validator proves DB truth for processed work and debt is visible. |
| `completed_with_followup_required` | Import or source work finished, but app-media downstream stages remain. | Not clean; next plan must show follow-up. |
| `completed_with_continuation` | Batch ended due cap/budget/cancel before all planned import items ran, but continuation is visible. | Partial success; must not be described as fully complete. |
| `failed_systemic` | A structural blocker invalidates the run. | Blocks acceptance. |
| `cancelled` | Operator cancelled. Side effects already committed must remain truthful. | Not success; continuation/debt must be visible. |
| `blocked_preflight` | Nothing executed because identity/readiness/safety preflight failed. | Safe block. |

`completed_with_failures` should be treated as a legacy compatibility value.
For run #18 it meant partial success with retryable source-read failures, not a
systemic failure and not clean completion.

## Plan / Execute / Validate Boundaries

Plan:

- discovers lifecycle decisions and WorkItems;
- is metadata-only in the target model;
- performs no content hash, decode, import, hydration, provider call, LLM call,
  or DB mutation;
- reports counts by WorkItem kind and lifecycle reason;
- exposes continuation, retry, placeholder, no-op diagnostic, and broken-state
  counts separately.

Execute:

- consumes WorkItems generated from a fresh, bound plan;
- executes `FOLLOWUP` before `IMPORT`, then `RETRY_SOURCE`;
- reads source only for `IMPORT` / `RETRY_SOURCE`;
- never mutates source/iCloud;
- records item-level outcomes before and after side effects;
- writes continuation rather than failure when a cap/budget stop leaves planned
  imports unprocessed.

Validate:

- reads DB truth, not stale report-only proof;
- checks lifecycle decisions after execution;
- distinguishes DB-truth acceptance from report-tool failures;
- fails on privacy leaks and real contradictions;
- reports remaining retry/debt/continuation explicitly.

## PR-R1 Migration Slice

PR-R1 intentionally avoids rewriting the entire manual sync stack in one pass.

Implemented slice:

1. Implemented `manual_sync_lifecycle.py` with dataclasses/enums and table-driven
   tests.
2. Used the classifier in planner output buckets for:
   `APP_MEDIA_FOLLOWUP`, `IMPORT_CANDIDATE`, `RETRYABLE_SOURCE_FAILURE`,
   `PLACEHOLDER_DEFERRED`, `STABLE_NOOP`, `CONTINUATION`, and `BROKEN_STATE`.
3. Used the classifier in validator/report debt inventory and root-scoped
   reconciliation helpers.
4. Kept execute's current core path, while adding canonical `operator_status`
   interpretation and preserving legacy `run.status` compatibility.
5. Deferred UI/progress/browser validation and richer Chinese operator labels
   to PR-R2.

## Required Tests

PR-R1 table-driven tests cover:

- fully successful import;
- import plus classification failure;
- import plus AI tagging failure;
- import plus localization deferral;
- read error/read timeout before import;
- failure-budget stop with continuation;
- follow-up with missing source file;
- follow-up with missing app storage;
- existing-media duplicate terminal vs incomplete;
- placeholder/hydration deferred;
- stale legacy no-op;
- source-missing media-backed downstream incomplete;
- `completed_with_failures` legacy mapping to
  `completed_with_retryable_failures`.
- missing media row and missing app-managed media as `BROKEN_STATE`;
- attempted follow-up separated from current downstream completion health;
- root-scoped inventory excluding other roots;
- `NOOP_DIAGNOSTIC` not consuming actionable cap;
- continuation staying visible without becoming terminal failure.
