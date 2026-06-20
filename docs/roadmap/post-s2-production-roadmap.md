# Post-S2 Production Roadmap

Status: planning reference after PR #113 / Phase 4.7-S2.

This roadmap keeps production baseline closeout separate from later source/entity/provider work. It does not authorize S3, provider calls, SourceConcept reruns, entity bridge writes, or automatic production sync.

## Immediate Closeout

1. PR #113 closeout
   - Finish browser/manual validation evidence.
   - Keep public report head evidence split into validated run head, report-generation head, and PR-handoff head.
   - Keep unsupported desired-media counts visible instead of folding them into S2 completion.
   - Keep source-item localization status aligned with tag-localization completion semantics.

2. Production governance persistence
   - Keep production and development lanes separate.
   - Use dev/test DB and storage for feature branches.
   - Promote to production only through PR review, contracts, backup proof, production dry-run, browser validation, and redaction checks.

## Short-Term Follow-Ups

1. S2F0 desired-media gap audit / support decision report
   - Report unsupported desired-media ratio, extension breakdown, sampled relevance, and recommendation.
   - Keep sidecar/metadata files such as `.AAE` separate from desired user media.
   - Do not commit to HEIC/JFIF/MOV/MP4 support or backfill unless the audit proves it is worth doing for the anime-library use case.

2. S2G GPU AI tagging, provider benchmark, and load control (high priority)
   - Benchmark CUDA, DirectML, and CPU fallback paths for local WD tagging.
   - Record provider provenance, model identity, thresholds, and CPU/GPU drift audit results.
   - Add batch/concurrency controls and operator-visible throttling for long production jobs.
   - Preserve provenance compatibility for reuse.

3. PD1 Production/Development Separation Executable Gates
   - Convert the governance document into tests/contracts that prevent develop branches from casually writing production DB, storage, or source-root state.
   - Require explicit promotion mode for production execution.

## Incremental Library Sync

1. S3A Controlled Incremental Sync Pipeline
   - Operator manually triggers a production run.
   - The system automatically performs update check, hydration/read, import/reuse, classification, AI tagging, localization, per-item ledgers, failure budgets, and summary.
   - Require operator-reviewed pending counts, thresholds, and explicit execute confirmation for production writes.
   - Preserve resume/retry support for cloud hydration, import, classification, AI tagging, and localization.

2. S3B opt-in automated incremental sync
   - Automatic production writes stay disabled by default until S3B approval.
   - Auto-sync requires bounded batch size, failure budgets, safe scheduling, clear UI state, and pause/disable controls.

## Source / Entity Work Later

1. R1R SourceConcept route redo under GOV3 contracts
   - Re-enter only after S2 closeout is stable.
   - Keep AI proper-noun tags as weak evidence only.
   - Do not create confirmed Entity assignments from AI tags.

2. Pixiv / source metadata strategy polish after R1R
   - Use R1R results to decide whether the current Pixiv/source metadata strategy is viable or needs a larger redesign.
   - Do not introduce new providers before Pixiv/source metadata is polished.
   - Provider/Pixiv/gallery-dl/SauceNAO/Google remain out of S2 and require separate approval.
   - Future provider work must be cache-first, budgeted, privacy-gated, and separately approved.
   - Entity bridge writes require explicit phase approval, provenance rules, and executable contracts.
