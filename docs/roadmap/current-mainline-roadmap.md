# Current Mainline Roadmap

Status: active after PR #114 / PD1-A merge and S2G-1X operator
reprioritization on 2026-06-20.

This is the durable short-term routing document for post-S2 work. It keeps the
accepted sequence visible for ChatGPT, CodeX, and operator sessions without
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
- V.I.O.L.E.T. now has a real production baseline library. Development work
  must not casually use production DB, production storage, production source
  roots, or production private ledgers as fixtures.

## Accepted Sequence

1. `PD1-A` - Post-S2 mainline roadmap persistence and production/development
   executable gate foundation.
   - Merged in PR #114.
   - Persisted the accepted short and long roadmap.
   - Added a narrow executable production/development separation contract.
   - No production writes.

2. `S2G-1X` - GPU AI tagging probe and S3A integration decision.
   - Current operator-reprioritized phase.
   - Evaluates whether S2G GPU/load-control work and S3A controlled
     incremental sync should share job/progress/throttle/ledger infrastructure.
   - May add safe probe/scaffold evidence only.
   - Production S3A execution remains disabled.

3. `S2G/S3A shared foundation` - combined foundation if S2G-1X evidence
   supports it.
   - Promote only the reviewed shared job/progress/throttle/ledger pieces.
   - Keep production import/classification/AI/localization/S3A execution
     disabled until a separate promotion phase.
   - Preserve manual operator trigger semantics; do not start S3B automation.

4. `S2G-2/3` - GPU/provider abstraction, provenance, and load control.
   - High priority.
   - Use S2G-1X capability evidence as the starting point.
   - Add execution-provider abstraction.
   - Record provider provenance, model identity, thresholds, batch size, and backend.
   - Add batch, concurrency, and throttle controls so AI tagging does not make
     the machine unusable.
   - Preserve CPU fallback.

5. `R1R` - SourceConcept route redo under GOV3 contracts.
   - Return to the main Phase 4 source/entity line after S2G.
   - Use executable contracts and a review pack.
   - Treat AI proper-noun tags as weak evidence only.
   - Do not create confirmed Entity assignments.
   - Do not run new provider or Pixiv live calls unless separately approved.
   - Decide whether the current Pixiv/source metadata strategy is viable or
     needs larger redesign.

6. `Pixiv/source metadata strategy polish`.
   - Long-term mainline after R1R.
   - Make the Pixiv/source metadata route reliable before adding providers.
   - Do not introduce new providers before Pixiv/source metadata is settled.

7. `S3A` - Controlled Incremental Sync Pipeline.
   - Important, and now explicitly evaluated for shared S2G infrastructure by
     S2G-1X.
   - Production execution still waits for a separate approved phase.
   - The operator manually triggers a run; the system performs update check,
     hydration/read, import/reuse, classification, AI tagging, localization,
     per-item ledgers, failure budgets, and summary.
   - Production execution still requires explicit confirmation and promotion gates.

8. `S3B` - Opt-in automated incremental sync.
   - Later.
   - Scheduled or unattended automation remains disabled by default until
     explicitly approved.

9. `S2F0` - Desired-media gap audit / support decision report.
   - Low priority.
   - Audit-only, not implementation.
   - Report unsupported desired-media ratio, extension breakdown, sampled
     relevance, and recommendation.
   - Do not implement HEIC/JFIF/MOV/MP4 support or backfill unless the audit
     proves it is worth doing for the anime-library use case.

## What Is Intentionally Not Next

- Do not enable production S3A/S3B execution from PD1-A or S2G-1X.
- Do not run provider, Pixiv, gallery-dl, SauceNAO, Google, or source-enrichment work.
- Do not run SourceConcept R1/R2, Entity bridge, or confirmed assignment creation.
- Do not implement desired-media support/backfill.
- Do not run production import, classification, AI tagging, localization, DB
  migration, source-root write, cleanup, delete, reset, drop, or truncate
  without a separate approved phase and promotion gate.
- Do not run the GPU benchmark during PD1-A; PD1-A only persists the route and
  adds the gate foundation.

## Production / development separation

- Develop/feature branches use dev/test DB, dev/test storage, fixtures, or
  restored snapshots.
- Production DB/storage/source roots/private ledgers are not casual fixtures.
- Production import/classification/AI/localization/source-root/schema writes
  require explicit production/promotion mode, clean identity gates, backup proof
  where applicable, redacted public artifacts, and local ignored private ledgers.
- Public reports stay aggregate-only and path-redacted.
- Private ledgers remain local ignored artifacts.
- The executable foundation is
  `production_development_separation_contract_v1`.

## Choosing The Next Work

After PD1-A merges, the operator-reprioritized immediate phase is
`S2G-1X GPU AI tagging probe and S3A integration decision`.

If S2G-1X evidence supports the shared-infrastructure route, the recommended
next phase is a combined S2G/S3A foundation phase that still keeps production
S3A execution disabled.

Only change that order if the operator explicitly reprioritizes. If a future
session is unsure, choose the first unmerged item in `Accepted Sequence` whose
prerequisites are met, and verify latest `main` plus any prerequisite PR merge
before branching.
