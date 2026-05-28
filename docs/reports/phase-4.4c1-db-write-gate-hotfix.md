# Phase 4.4-C1-HF1 - DB Write Gate Hotfix

Date: 2026-05-28

## Summary

PR #81 was already merged before this hotfix. This must therefore be a new hotfix PR from latest `main`, not a push to the old merged PR branch.

This hotfix fixes one current-main DB write contract blocker:

- `EvidencePersistencePlan.db_write_allowed` is the authoritative plan-level write gate.
- C0 mapper output remains non-mutating with `db_write_allowed=false`.
- The durable persistence service now fails closed before any `ProviderCache`, `EntityEvidence`, or `MediaEntityCandidate` write unless the plan is explicitly writable.
- The C1 runner now promotes only approved, fully validated `2687` / `2670` plans to `db_write_allowed=true` after all C1 gates pass.

## Bug

PR #81 preserved the provider-neutral contract field, but the persistence service did not enforce it before writing. A raw C0 `EvidencePersistencePlan` with `db_write_allowed=false` could still be persisted if the rest of the C1 fields were valid.

That bypassed the provider-neutral contract and made caller discipline, rather than durable persistence code, the effective write gate.

## Fix

- `backend/app/services/provider_evidence_persistence_service.py`
  - `validate_persistence_ready_plan()` now raises `db_write_not_allowed_by_plan` when `plan.db_write_allowed is not True`.
  - The check runs during service validation before any DB write loop can insert provider cache, evidence, or candidates.

- `scripts/run_phase44c1_validated_evidence_persistence.py`
  - Added `promote_c1_plan_for_db_write()`.
  - C0 mapper output remains non-mutating by default.
  - C1 calls the promotion helper only after approved media/result identity, live/metadata identity, nested identity, provenance, request shape, source identifier, metadata, manual-validation, evidence-strength, localization, no-confirmed-assignment, no-Entity, and no-low-confidence-positive-write gates pass.

## Tests

Focused tests prove:

- `db_write_allowed=false` blocks service-level persistence before any DB row is written.
- Approved C1 plans are explicitly promoted to `db_write_allowed=true` and can persist in the mocked test DB.
- Low-confidence/discarded plans remain `db_write_allowed=false` and cannot persist.
- Reduced public-summary-style plans without provenance remain non-writable and cannot persist.
- No confirmed `MediaEntityAssignment` is created.
- No automatic `Entity` is created.
- Existing C1-equivalent rows remain idempotent and do not duplicate.
- Direct service callers cannot bypass the C1 runner and write non-writable plans.

## DB Impact

No development DB apply was run for this hotfix, and no development DB rows were inserted by this hotfix.

Existing accepted C1 rows for `2687` and `2670` are not rolled back or deleted by default. This hotfix only enforces the write gate for future execution and preserves idempotent behavior for existing equivalent rows.

## Deferred Items

The following reviewer/hardening items remain deferred because they are not required for this current DB write gate hotfix:

- Pre-existing candidate conflict dry-run detection.
- Non-suggested candidate decision preservation on rerun.
- Dry-run post-write count semantics.
- ProviderCache query-scoped payload redesign for duplicate images.
- Broader candidate lifecycle hardening.
- Future service caller transaction/savepoint policy.

## Safety Confirmation

- No provider call.
- No SauceNAO re-query.
- No image upload.
- No DB migration.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No localization execution.
- No Entity Resolver.
- No similarity/clustering.
- No D0 implementation.
- No broad sample expansion.
- No push to `main`.
- No merge.
