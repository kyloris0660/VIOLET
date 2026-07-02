# S3A-M2-R R0/R1 Closeout

This is a stop-after-R0/R1 closeout, not final acceptance for the full
stabilization phase. The audit found that R2-R4 spans planner, execute,
validator, report, UI, tests, and durable docs, so implementation should be
split rather than shipped as another giant PR.

## Scope Completed

- Created branch `codex/s3a-m2-r-manual-sync-stabilization` from
  `origin/main` after PR #126 merge commit
  `ff5972b0685def18bd658746e2ba1e3043c28d02`.
- Added a read-only production health audit:
  `scripts/audit_s3a_m2_r_post_merge_health.py`.
- Generated public-safe reports:
  `docs/reports/s3a-m2-r-post-merge-health-audit.md` and
  `docs/reports/s3a-m2-r-post-merge-health-summary.json`.
- Added R1 design:
  `docs/architecture/manual-sync-state-machine.md`.
- Added operator runbook seed:
  `docs/admin/manual-sync-runbook.md`.

## R0 Findings

- Run #18 processed `1000` plan rows:
  `880` downstream follow-up, `34` imported, `11` retryable read failures, and
  `75` deferred continuation rows.
- The `880` follow-up rows were actionable at run start and completed; none of
  those rows remain actionable follow-up in the next normal plan.
- `completed_with_failures` should be treated as a legacy ambiguous value. For
  run #18 the correct operator meaning is
  `completed_with_retryable_failures`.
- The `75` run #18 deferred import candidates are visible in the next normal
  plan.
- Root 2 has `20` older app-media-backed/source-missing downstream-incomplete
  rows. They are visible, but current planner wording does not classify them as
  follow-up. The canonical model should classify them as `APP_MEDIA_FOLLOWUP`
  when app storage exists, or `BROKEN_STATE` if app storage is missing.
- Next normal plan is explainable:
  `347` estimated imports, `0` downstream follow-up, no unsafe partial scan,
  and dry-run-only import/classification/AI/localization work `0/0/0/0`.

## R1 Design

The next implementation PR should introduce:

- `backend/app/services/manual_sync_lifecycle.py`;
- `LifecycleDecision`;
- lifecycle kinds:
  `APP_MEDIA_FOLLOWUP`, `IMPORT_CANDIDATE`,
  `RETRYABLE_SOURCE_FAILURE`, `PLACEHOLDER_DEFERRED`, `STABLE_NOOP`,
  `HISTORICAL_DIAGNOSTIC`, `CONTINUATION`, `BROKEN_STATE`, and
  `FATAL_BLOCKER`;
- typed WorkItems:
  `FOLLOWUP`, `IMPORT`, `RETRY_SOURCE`, `PLACEHOLDER`,
  `NOOP_DIAGNOSTIC`, and `BROKEN_STATE`;
- unified statuses:
  `completed`, `completed_with_retryable_failures`,
  `completed_with_followup_required`, `completed_with_continuation`,
  `failed_systemic`, `cancelled`, and `blocked_preflight`.

## Proposed Split

PR-R0: health audit + lifecycle/WorkItem design + runbook seed.

PR-R1: lifecycle classifier / WorkItem refactor + unit and integration tests.

PR-R2: UI progress, Chinese labels, preflight/report/validator cleanup, browser
validation, and documentation persistence.

## Safety Confirmation

- No push to `main`.
- No merge.
- No production Execute.
- No DB writes from manual sync.
- No source/iCloud mutation.
- No cleanup/delete/reset/drop/truncate.
- No DB import.
- No classification, AI tagging, or localization execution.
- No Entity Resolver, similarity, SourceConcept, provider, Pixiv, gallery-dl,
  SauceNAO, Google, or source metadata expansion.

## Status

R0/R1 is ready for review as a bounded stabilization/design PR. The full
S3A-M2-R acceptance criteria are not claimed complete yet.
