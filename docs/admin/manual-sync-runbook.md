# Manual Sync Runbook

Status: S3A-M2-R R1 operator runbook seed. This document describes the manual
operator model after PR #126 and the R0 health audit. It does not authorize a
production Execute run.

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
- The `75` deferred import candidates are visible in the next normal plan.
- Root 2 has `20` older app-media-backed/source-missing downstream-incomplete
  rows that need lifecycle cleanup; they are visible, but current planner
  wording does not classify them as follow-up.
- The next normal plan estimates `347` imports and `0` downstream follow-up.

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
4. Confirm the selected root is `icloud-photos-production`.
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

## Current Deferred Work

R2/R3 should implement:

- canonical lifecycle classifier;
- typed WorkItem plan output;
- `completed_with_retryable_failures` and continuation status vocabulary;
- retry/debt report;
- validator/report cleanup;
- preflight script bug fix;
- Chinese UI labels and heartbeat/progress display;
- table-driven lifecycle/liveness tests;
- browser validation on a controlled test server.
