# Current Mainline Roadmap

Status: active after PR #126 / S3A-M2 merge and S3A-M2-R stabilization start
on 2026-07-02.

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
- PR #123 / PD1-A-R1 is merged into `main`.
- Merge commit: `4724530d83767a62b6525a58bb1a1d04e973d48e`.
- PD1-A-R1 reconciled the post-#122 route and kept the
  `production_development_separation_contract_v1` gate current.
- PR #124 / S2G-M1 is merged into `main`.
- PR #125 / S3A-M1 is merged into `main`.
- PR #126 / S3A-M2 is merged into `main`.
- Merge commit: `ff5972b0685def18bd658746e2ba1e3043c28d02`.
- PR #126 owner-run Web Admin GUI Execute run #18 met current-stage DB-truth
  acceptance, while deferring stabilization debt around lifecycle vocabulary,
  retry/continuation visibility, report/validator cleanup, and operator UI.
- V.I.O.L.E.T. has a real production baseline library. Development work must
  not casually use production DB, production storage, production source roots,
  or production private ledgers as fixtures.

## Accepted Sequence

1. `S2G-M1: AI Tagging Execution and Manual Sync Foundation` - merged in PR #124.
   - Single consolidated phase, not two phases.
   - Includes GPU / AI tagging capability probe and benchmark.
   - Includes provider abstraction, provenance, batch/concurrency/throttle
     controls, and CPU fallback.
   - Adds manual sync dry-run planning, sync job/ledger foundation, and the
     controlled import -> classification -> AI tagging -> localization pipeline
     foundation for dry-run/dev-test mode.
   - No production writes.

2. `S3A-M1` - Explicit manual-sync execute path plus final UI / launcher entry.
   - Merged in PR #125.
   - Must remain manual; no automatic, scheduled, startup, service, or
     unattended sync.

3. `S3A-M2` - Production Delta Manual Sync E2E + GPU Telemetry.
   - Merged in PR #126.
   - Owner-run GUI Execute run #18 achieved current-stage DB-truth acceptance.

4. `S3A-M2-R` - Manual Sync Stabilization and State-Machine Cleanup.
   - Current phase.
   - R0/R1 first: post-merge health audit and canonical lifecycle/WorkItem
     design.
   - If implementation is broad, split into lifecycle/WorkItem refactor,
     UI/tooling/report cleanup, and docs/runbook follow-through.
   - Do not start S3B, provider/source metadata expansion, SourceConcept,
     Entity bridge, or large production import.

5. `R1R` - SourceConcept route redo under GOV3 contracts.
   - Follows the production utility acceptance unless the user explicitly
     reprioritizes.
   - Use executable contracts and a review pack.
   - Treat AI proper-noun tags as weak evidence only.
   - Do not create confirmed Entity assignments.
   - Do not run new provider or Pixiv live calls unless separately approved.

6. `A1R` - Route audit rerun after R1R.
   - Required before R2, PX1-B, Provider-2, scale-up, Entity bridge, or
     SourceConcept truth promotion can be considered.
   - Old R1/A1 evidence cannot approve R2 during the INC1 pipeline fidelity
     incident.

7. `Pixiv/source metadata strategy polish`.
   - Make the Pixiv/source metadata route reliable before adding providers.
   - Do not introduce new providers before Pixiv/source metadata is settled.

8. `S3B` - Opt-in automated incremental sync.
   - Later.
   - Disabled by default.
   - Must bind to production profile/runtime config, not development `.env`.
   - Scheduled or unattended automation remains opt-in only until explicitly
     approved.

9. `S2F0` - Desired-media gap audit / support decision report.
   - Low priority.
   - Audit-only, not implementation.
   - Report unsupported desired-media ratio, extension breakdown, sampled
     relevance, and recommendation.
   - Do not implement HEIC/JFIF/MOV/MP4 support or backfill unless the audit
     proves it is worth doing for the anime-library use case.

## What Is Intentionally Not Next

- Do not enable automatic, scheduled, startup, service, or unattended sync from
  S2G-M1, S3A-M1, S3A-M2, or S3A-M2-R.
- Do not treat S2G-M1 capability probe, dry-run planner output, or S3A-M1
  dev/test execute validation as production acceptance. PR #126 production
  acceptance was current-stage DB-truth acceptance, not approval for unattended
  sync or broad future imports.
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

After PR #126, the recommended next step is S3A-M2-R stabilization. Do not run
another production Execute unless the owner explicitly requests a manual
validation run after a fresh plan and readiness check.

Only change that order if the operator explicitly reprioritizes. If a future
session is unsure, choose the first unmerged item in `Accepted Sequence` whose
prerequisites are met, and verify latest `main` plus any prerequisite PR merge
before branching.
