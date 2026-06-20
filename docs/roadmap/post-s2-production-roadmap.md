# Post-S2 Production Roadmap

Status: accepted routing reference after PR #113 / Phase 4.7-S2.

Canonical short version: `docs/roadmap/current-mainline-roadmap.md`.
This file keeps the post-S2 production utility track aligned with that current
mainline route. It does not authorize S3, provider calls, SourceConcept reruns,
Entity bridge writes, desired-media backfill, or automatic production sync.

## Baseline

- PR #113 / Phase 4.7-S2 is merged.
- Merge commit: `17ec3326bc00e4d025bbe4297fad1157b9cda2ff`.
- Final PR head before merge: `b8ef981f791ce3718610226a8e46871f8e2a03a3`.
- The project now has a real production baseline library.
- Production DB, storage, source roots, and private ledgers must stay separate
  from develop/test fixtures and feature-branch validation.

## Accepted Sequence

1. `PD1-A` - Post-S2 mainline roadmap persistence and production/development
   executable gate foundation.
   - Current phase.
   - Sync main after PR #113, persist the accepted roadmap, and add the
     `production_development_separation_contract_v1` foundation.
   - No production writes.

2. `S2G-1` - GPU AI tagging capability probe and benchmark.
   - Recommended immediate next phase after PD1-A: `S2G-1`.
   - Probe CUDA, DirectML, and CPU fallback.
   - Benchmark local WD tagging throughput using dev/test DB/storage, fixtures,
     or restored snapshots.
   - Produce provider capability and load-control design evidence.

3. `S2G-2/3` - GPU/provider abstraction, provenance, and load control.
   - Add execution-provider abstraction.
   - Record provider provenance, model identity, thresholds, batch size, and backend.
   - Add batch/concurrency/throttle controls.
   - Preserve CPU fallback.

4. `R1R` - SourceConcept route redo under GOV3 contracts.
   - Return to the main Phase 4 line after S2G.
   - Use executable contracts and a review pack.
   - AI proper-noun tags are weak evidence only.
   - Do not create confirmed Entity assignments.
   - Do not run new provider/Pixiv live calls unless separately approved.
   - Decide whether the current Pixiv/source metadata strategy is viable or
     needs larger redesign.

5. `Pixiv/source metadata strategy polish`.
   - Long-term mainline after R1R.
   - Focus on making the Pixiv/source metadata route reliable.
   - Do not introduce new providers before Pixiv/source metadata is settled.

6. `S3A` - Controlled Incremental Sync Pipeline.
   - Important, but after S2G and the R1R/Pixiv route decision unless the
     operator explicitly reprioritizes.
   - This is not one-by-one manual import.
   - The operator manually triggers a run, then the system automatically
     performs update check, hydration/read, import/reuse, classification,
     AI tagging, localization, per-item ledgers, failure budgets, and summary.
   - Production execution still requires explicit confirmation and promotion gates.

7. `S3B` - Opt-in automated incremental sync.
   - Later.
   - Scheduled/unattended automation remains disabled by default until
     explicitly approved.

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
- Production execution requires explicit promotion mode.
- Production source-root registration/replacement requires clean production
  identity gates and valid backup proof.
- Schema setup/migration paths must not run when env/storage/DB identity gates
  are blocked.
- Public reports are aggregate-only and path-redacted.
- Private ledgers remain local ignored artifacts.

## Non-Goals For PD1-A

PD1-A must not run production import/classification/AI/localization, S3,
provider/Pixiv/gallery-dl/SauceNAO/Google calls, SourceConcept R1/R2, Entity
bridge, confirmed assignments, GPU benchmark, desired-media backfill, cleanup,
delete, reset, drop, truncate, push main, or merge.
