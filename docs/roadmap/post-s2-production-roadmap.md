# Post-S2 Production Roadmap

Status: accepted production routing reference after PR #123 / PD1-A-R1 and
S2G-M1 foundation work.

Canonical short version: `docs/roadmap/current-mainline-roadmap.md`.
This file keeps the post-S2 production utility track aligned with that current
mainline route. It does not authorize S3A/S3B execution, provider calls,
SourceConcept reruns, Entity bridge writes, desired-media backfill, or automatic
production sync.

## Baseline

- PR #113 / Phase 4.7-S2 is merged.
- Merge commit: `17ec3326bc00e4d025bbe4297fad1157b9cda2ff`.
- Final PR head before merge: `b8ef981f791ce3718610226a8e46871f8e2a03a3`.
- PR #114 / PD1-A is merged.
- Merge commit: `3dcd201c9b8ece6204e088c7dc8af49bd3f4ad07`.
- The production/development separation foundation is persisted by
  `production_development_separation_contract_v1`.
- PR #122 / PROD-LAUNCHER-UX1/PF1 is merged.
- Merge commit: `aece424df2814ef0d840f9fe472a9d19478d2020`.
- The #122 production launcher is now the accepted access path for current
  production library operation on the Windows personal checkout.
- The launcher uses local ignored production profile/runtime config. It solves
  the current production entry problem, but the long-term production
  configuration architecture remains deferred.
- The project has a real production baseline library.
- PR #123 / PD1-A-R1 is merged.
- Merge commit: `4724530d83767a62b6525a58bb1a1d04e973d48e`.
- S2G-M1 is the current production-utility foundation phase for AI execution
  and manual controlled sync.
- Production DB, storage, source roots, and private ledgers must stay separate
  from develop/test fixtures and feature-branch validation.

## Accepted Sequence

1. `S2G-M1: AI Tagging Execution and Manual Sync Foundation`.
   - The production utility foundation after PD1-A-R1.
   - Single consolidated phase, not split into S2G-1 and S2G-2/3.
   - Covers GPU / AI tagging capability probe and benchmark, provider
     abstraction, provenance, batch/concurrency/throttle controls, and CPU
     fallback.
   - Adds manual sync dry-run planner, job/ledger foundation, and controlled
     pipeline foundation in dry-run/dev-test mode.
   - Does not authorize production writes.

2. `S3A-M1` - Final manual-sync UI / production acceptance.
   - Small follow-up phase after S2G-M1.
   - Wires the final visible manual-sync button and runs the first small
     production acceptance batch only after explicit operator approval.
   - Must remain manual; no automatic, scheduled, startup, service, or
     unattended sync.

3. `R1R` - SourceConcept route redo under GOV3 contracts.
   - Follows the production utility manual-sync acceptance unless the user
     explicitly reprioritizes.
   - Required because INC1 found old R1 was deterministic-only.
   - Do not create confirmed Entity assignments.
   - Do not run new provider/Pixiv live calls unless separately approved.

4. `A1R` - Route audit rerun after R1R.
   - Required before R2, PX1-B, Provider-2, scale-up, Entity bridge, or
     SourceConcept truth promotion.

5. `Pixiv/source metadata strategy polish`.
   - Focus on making the Pixiv/source metadata route reliable.
   - Do not introduce new providers before Pixiv/source metadata is settled.

6. `S3B` - Opt-in automated incremental sync.
   - Later phase.
   - Disabled by default and opt-in only until explicitly approved.
   - Must account for production profile/runtime config instead of development
     `.env`.

7. `S2F0` - Desired-media gap audit / support decision report.
   - Low priority.
   - Audit-only, not implementation.
   - Report unsupported desired-media ratio, extension breakdown, sampled
     relevance, and recommendation.
   - Do not implement HEIC/JFIF/MOV/MP4 support or backfill unless the audit
     proves it is worth doing for the anime-library use case.

## S3A-M1 Acceptance Shape

- The visible manual-sync button should be exposed in both Web Admin and the
  launcher, but the launcher should only perform a lightweight pending check on
  startup.
- The launcher should not show an intrusive automatic prompt by default.
- The backend should call the S2G-M1 dry-run plan endpoint first:
  `POST /api/admin/dynamic-library-sync/manual-sync/plan`.
- Production execution should require a separate S3A-M1 execute endpoint or
  runner with explicit operator approval, production identity proof, and a small
  first batch.
- Safe defaults from S2G-M1 are max files `25`, max duration `600` seconds,
  AI batch size `2`, and concurrency `1`.
- Partial failure should complete successful items, keep failed/deferred items
  visible in the ledger, and stop only on hard safety gates or failure budget
  breach.

## Later Automated Sync

- S3B is not the same as S3A-M1.
- Scheduled or unattended sync remains disabled by default.
- Startup tasks, system services, and long-running daemons require a separate
  opt-in phase and are not authorized by S2G-M1 or S3A-M1.

## Historical Controlled Sync Shape

- This is not one-by-one manual import.
- The operator manually triggers a run, then the system automatically
  performs update check, hydration/read, import/reuse, classification,
  AI tagging, localization, per-item ledgers, failure budgets, and summary.
- Production execution still requires explicit confirmation and promotion
  gates.

## Production/Development Gate

- Develop branches must use dev/test DB, dev/test storage, fixtures, or restored
  snapshots.
- Development `.env` must not be converted into production.
- Production execution phases must use explicit production profile/runtime
  config.
- Production source-root registration/replacement requires clean production
  identity gates and valid backup proof.
- Schema setup/migration paths must not run when env/storage/DB identity gates
  are blocked.
- Public reports are aggregate-only and path-redacted.
- Private ledgers remain local ignored artifacts.

## Current Non-Goals

S2G-M1 does not authorize production import/classification/AI/localization,
S3A-M1 production acceptance completion, S3B automation,
provider/Pixiv/gallery-dl/SauceNAO/Google calls, SourceConcept R1/R2, Entity
bridge, confirmed assignments, desired-media backfill, cleanup, delete, reset,
drop, truncate, push main, or merge.
