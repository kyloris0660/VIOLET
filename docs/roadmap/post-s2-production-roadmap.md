# Post-S2 Production Roadmap

Status: accepted production routing reference after PR #122 /
PROD-LAUNCHER-UX1/PF1 and PD1-A-R1 reconciliation.

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
- Production DB, storage, source roots, and private ledgers must stay separate
  from develop/test fixtures and feature-branch validation.

## Accepted Sequence

1. `PD1-A-R1` - Post-#122 roadmap reconciliation and production/development
   gate foundation.
   - Immediate reconciliation phase after #122.
   - Updates roadmap, handoff, public report, and executable separation
     contract.
   - No production writes.

2. `S2G: GPU / AI Tagging Execution Foundation`.
   - The next production utility foundation after PD1-A-R1.
   - Single consolidated phase, not split into S2G-1 and S2G-2/3.
   - Covers GPU / AI tagging capability probe and benchmark, provider
     abstraction, provenance, batch/concurrency/throttle controls, and CPU
     fallback.
   - Does not authorize production writes unless a later phase explicitly
     approves them.

3. `R1R` - SourceConcept route redo under GOV3 contracts.
   - Follows S2G unless the user explicitly reprioritizes.
   - Required because INC1 found old R1 was deterministic-only.
   - Do not create confirmed Entity assignments.
   - Do not run new provider/Pixiv live calls unless separately approved.

4. `A1R` - Route audit rerun after R1R.
   - Required before R2, PX1-B, Provider-2, scale-up, Entity bridge, or
     SourceConcept truth promotion.

5. `Pixiv/source metadata strategy polish`.
   - Focus on making the Pixiv/source metadata route reliable.
   - Do not introduce new providers before Pixiv/source metadata is settled.

6. `S3A` - Controlled Incremental Sync Pipeline.
   - Later phase.
   - Must account for production profile/runtime config instead of development
     `.env`.
   - Production execution still waits for a separate approved phase.
   - This is not one-by-one manual import.
   - The operator manually triggers a run, then the system automatically
     performs update check, hydration/read, import/reuse, classification,
     AI tagging, localization, per-item ledgers, failure budgets, and summary.
   - Production execution still requires explicit confirmation and promotion
     gates.

7. `S3B` - Opt-in automated incremental sync.
   - Later phase.
   - Disabled by default and opt-in only until explicitly approved.
   - Must account for production profile/runtime config instead of development
     `.env`.

8. `S2F0` - Desired-media gap audit / support decision report.
   - Low priority.
   - Audit-only, not implementation.
   - Report unsupported desired-media ratio, extension breakdown, sampled
     relevance, and recommendation.
   - Do not implement HEIC/JFIF/MOV/MP4 support or backfill unless the audit
     proves it is worth doing for the anime-library use case.

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

PD1-A-R1 does not authorize production import/classification/AI/localization,
S2G execution, S3A/S3B production execution, S3B automation,
provider/Pixiv/gallery-dl/SauceNAO/Google calls, SourceConcept R1/R2, Entity
bridge, confirmed assignments, desired-media backfill, cleanup, delete, reset,
drop, truncate, push main, or merge.
