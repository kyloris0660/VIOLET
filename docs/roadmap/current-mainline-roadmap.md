# Current Mainline Roadmap

Status: active after PR #122 / PROD-LAUNCHER-UX1/PF1 merge and PD1-A-R1
roadmap reconciliation on 2026-06-23.

This is the durable short-term routing document for post-#122 work. It keeps
the accepted sequence visible for ChatGPT, CodeX, and operator sessions without
turning this phase into a broad planning essay.

## Current Baseline

- PR #113 / Phase 4.7-S2 is merged into `main`.
- Merge commit: `17ec3326bc00e4d025bbe4297fad1157b9cda2ff`.
- Final PR head before merge: `b8ef981f791ce3718610226a8e46871f8e2a03a3`.
- Public baseline summary:
  `docs/reports/phase-4.7-s2-baseline-full-import-ai-localization-summary.json`.
- PR #114 / PD1-A is merged into `main`.
- Merge commit: `3dcd201c9b8ece6204e088c7dc8af49bd3f4ad07`.
- PD1-A persisted the production/development executable gate foundation:
  `production_development_separation_contract_v1`.
- PR #122 / PROD-LAUNCHER-UX1/PF1 is merged into `main`.
- Merge commit: `aece424df2814ef0d840f9fe472a9d19478d2020`.
- PR #122 added the Electron-based V.I.O.L.E.T. production launcher, local
  ignored production profile/runtime config, root-level Windows launcher entry,
  and production/development runtime separation.
- The launcher is the accepted temporary Windows personal production entrypoint.
  It solves the current production entry problem but is not the long-term
  production configuration architecture.
- Long-term production/development config unification is deferred.
- V.I.O.L.E.T. has a real production baseline library. Development work must
  not casually use production DB, production storage, production source roots,
  or production private ledgers as fixtures.

## Accepted Sequence

1. `PD1-A-R1` - Post-#122 roadmap reconciliation and production/development
   gate foundation.
   - Immediate next phase after PR #122.
   - Reconcile persistent roadmap/handoff/report/contract state.
   - No production writes.
   - Does not start S2G, R1R, A1R, S3A, S3B, provider work, SourceConcept
     mutation, Entity truth writes, or DB mutation.

2. `S2G` - GPU / AI Tagging Execution Foundation.
   - Single consolidated phase, not two phases.
   - Includes GPU / AI tagging capability probe and benchmark.
   - Includes provider abstraction, provenance, batch/concurrency/throttle
     controls, and CPU fallback.
   - No production writes unless a later phase explicitly approves them.

3. `R1R` - SourceConcept route redo under GOV3 contracts.
   - Follows S2G unless the user explicitly reprioritizes.
   - Use executable contracts and a review pack.
   - Treat AI proper-noun tags as weak evidence only.
   - Do not create confirmed Entity assignments.
   - Do not run new provider or Pixiv live calls unless separately approved.

4. `A1R` - Route audit rerun after R1R.
   - Required before R2, PX1-B, Provider-2, scale-up, Entity bridge, or
     SourceConcept truth promotion can be considered.
   - Old R1/A1 evidence cannot approve R2 during the INC1 pipeline fidelity
     incident.

5. `Pixiv/source metadata strategy polish`.
   - Make the Pixiv/source metadata route reliable before adding providers.
   - Do not introduce new providers before Pixiv/source metadata is settled.

6. `S3A` - Controlled Incremental Sync Pipeline.
   - Later than S2G and the R1R/A1R/Pixiv route decision unless the user
     explicitly reprioritizes.
   - Must bind to production profile/runtime config, not development `.env`.
   - Production execution still waits for a separate approved phase.
   - Production execution still requires explicit confirmation and promotion
     gates.

7. `S3B` - Opt-in automated incremental sync.
   - Later.
   - Disabled by default.
   - Must bind to production profile/runtime config, not development `.env`.
   - Scheduled or unattended automation remains opt-in only until explicitly
     approved.

8. `S2F0` - Desired-media gap audit / support decision report.
   - Low priority.
   - Audit-only, not implementation.
   - Report unsupported desired-media ratio, extension breakdown, sampled
     relevance, and recommendation.
   - Do not implement HEIC/JFIF/MOV/MP4 support or backfill unless the audit
     proves it is worth doing for the anime-library use case.

## What Is Intentionally Not Next

- Do not enable production S3A/S3B execution from PD1-A-R1.
- Do not run S2G during PD1-A-R1; the next PR may start S2G after this
  reconciliation lands.
- Do not run provider, Pixiv, gallery-dl, SauceNAO, Google, or source-enrichment
  work.
- Do not run SourceConcept R1/R2, Entity bridge, or confirmed assignment
  creation.
- Do not implement desired-media support/backfill.
- Do not run production import, classification, AI tagging, localization, DB
  migration, source-root write, cleanup, delete, reset, drop, or truncate
  without a separate approved phase and promotion gate.

## Production / Development Separation

- The current Windows personal production launcher uses local ignored
  production profile/runtime config.
- Development `.env` must not be converted into production and must not be the
  production source of truth.
- Develop/feature branches use dev/test DB, dev/test storage, fixtures, or
  restored snapshots.
- Production DB/storage/source roots/private ledgers are not casual fixtures.
- Future production execution phases, including S3A/S3B, must bind to production
  profile/runtime config rather than development `.env`.
- Production import/classification/AI/localization/source-root/schema writes
  require explicit production/promotion mode, clean identity gates, backup proof
  where applicable, redacted public artifacts, and local ignored private ledgers.
- Public reports stay aggregate-only and path-redacted.
- Private ledgers remain local ignored artifacts.
- The executable foundation is
  `production_development_separation_contract_v1`.

## Choosing The Next Work

After PD1-A-R1 merges, the recommended next phase is:

`S2G: GPU / AI Tagging Execution Foundation`.

Only change that order if the operator explicitly reprioritizes. If a future
session is unsure, choose the first unmerged item in `Accepted Sequence` whose
prerequisites are met, and verify latest `main` plus any prerequisite PR merge
before branching.
