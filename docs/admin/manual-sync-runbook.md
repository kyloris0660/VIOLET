# Manual Sync Runbook

Status: S3A-M2-R PR-R1 operator runbook update. This document describes the
manual operator model after PR #126, the R0 health audit, and the PR-R1 backend
lifecycle/WorkItem implementation. It does not authorize a production Execute
run.

## Current Operator Posture

- Production manual sync remains manual only.
- No scheduled, startup, service, automatic, or unattended sync is approved.
- Do not run production Execute unless the owner explicitly authorizes a manual
  validation run.
- Do not mutate source/iCloud files.
- Do not run provider/Pixiv/gallery-dl/SauceNAO/Google/source metadata work.
- Do not run SourceConcept or Entity bridge work from this manual sync track.

## Current Health Snapshot

The S3A-M2-R R0 read-only audit found:

- Run #18 completed as legacy `completed_with_failures`.
- The operator-facing meaning should be
  `completed_with_retryable_failures`.
- Run #18 processed `1000` plan rows:
  `880` downstream follow-up, `34` imported, `11` retryable read failures, and
  `75` deferred continuation rows.
- The `880` follow-up rows completed downstream work and should not reappear as
  actionable follow-up in the next normal plan.
- The `75` deferred import candidates remain visible as root-scoped DB
  continuation rows.
- The audited production source root has `20` older app-media-backed /
  source-missing downstream-incomplete rows that need lifecycle cleanup; they
  are visible, but current planner wording does not classify them as follow-up.
- The first R0/R1 attempt reported `347` next-plan import candidates via the
  current source-read-capable planner. The safe-default audit does not recompute
  that exact number because the current planner may walk/stat source entries.
  DB-only evidence accounts for `75` run #18 continuation rows, `104` other
  continuation rows, `11` retryable source-read failures, `4` placeholder rows,
  and `20` app-media follow-up candidates that are not import work.

## How To Read Terminal Statuses

`completed`

The batch completed cleanly.

`completed_with_retryable_failures`

The batch did useful work and ended with item-level source/cloud/read failures.
This is partial success. The UI must show retry/debt counts and the next
action.

`completed_with_followup_required`

Imported media or existing media still needs classification, AI tagging, or
localization. The next normal plan must show follow-up work.

`completed_with_continuation`

The batch stopped before all planned import candidates were processed. Deferred
items must remain visible in the next plan.

`failed_systemic`

Do not continue. Inspect preflight/server/DB/storage/report privacy blockers.

`cancelled`

The operator cancelled. Already committed work remains real and must be
audited; unprocessed items are continuation.

`blocked_preflight`

Nothing should have executed. Fix readiness or identity blockers first.

Legacy `completed_with_failures`

Treat as ambiguous. For run #18 it meant partial success with retryable
source-read failures, not a clean success and not a systemic failure.

## Normal Manual Flow

1. Start production through the approved GUI launcher only when an owner has
   requested production validation.
2. Open Web Admin manual sync.
3. Run Plan.
4. Confirm the selected source root through the private operator UI; public
   reports must use the source-root marker/hash, not the raw label or id.
5. Read counts by WorkItem kind:
   follow-up, import, retry source, placeholder, continuation, no-op diagnostic,
   broken state.
6. Confirm no automatic/background sync flags are enabled.
7. Execute only if the owner has explicitly authorized the manual run.
8. Wait for a terminal status.
9. Run a read-only DB truth audit after execution.

## What The Operator Should See

The UI should show:

- classified `N/M`;
- AI tagged `N/M`;
- localized `N/M`;
- retryable failures;
- deferred continuation;
- placeholder/debt counts;
- whether follow-up uses app-managed media;
- whether import/retry will read source;
- clear terminal status text in Chinese.

Do not leave the operator staring at a blank stage during classification, AI
tagging, localization, or summary/report generation.

## PR-R1 Backend Semantics

`APP_MEDIA_FOLLOWUP` now means app-managed media exists and downstream work is
currently incomplete. It is executed as `FOLLOWUP`, does not read the original
source file, and may remain actionable even when the source file is missing.

`IMPORT_CANDIDATE` is source-read-capable import work. Execute may revalidate,
hash, copy, classify, AI-tag, and localize under the existing manual sync
guards.

`RETRYABLE_SOURCE_FAILURE` is item-level source/cloud/read debt. It does not
block app-media follow-up, and it maps legacy run wording toward
`completed_with_retryable_failures` when processed work completed.

`CONTINUATION` is unprocessed work left by cap, budget stop, or cancellation. It
must stay visible in the next plan and must not be treated as terminal failure.

`STABLE_NOOP` and `HISTORICAL_DIAGNOSTIC` do not consume actionable cap.
Stable media-backed no-op requires app-managed storage presence and cannot hide
downstream-incomplete media.

`BROKEN_STATE` is visible and non-executable by default. Missing app-managed
media for a media-backed item is broken even when downstream statuses appear
terminal.

## Current Deferred Work

PR-R2 should implement:

- UI progress and heartbeat;
- Chinese operator-facing labels and status text;
- retry/debt report polish;
- validator/report cleanup and Markdown/public-redaction hardening where needed;
- preflight script bug fix;
- browser validation on a controlled test server;
- final GUI-path acceptance when production operator behavior is in scope.
