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

1. S2F desired-media support and backfill
   - Add or validate support/backfill for `.heic`, `.heif`, `.jfif`, `.mov`, `.mp4`, and `.pic`.
   - Keep sidecar/metadata files such as `.AAE` separate from desired user media.
   - Backfill through source-item state ledgers, content hashes, and retryable failure accounting.

2. S2G GPU AI tagging, provider benchmark, and load control
   - Benchmark local WD tagging throughput and CPU/GPU options.
   - Add operator-visible throttles for long production jobs.
   - Preserve model identity, threshold, and provenance compatibility for reuse.

## Incremental Library Sync

1. S3A manual incremental import
   - Manual check first.
   - Operator-reviewed pending counts and thresholds.
   - Explicit execute confirmation for production writes.
   - Resume/retry support for cloud hydration, import, classification, AI tagging, and localization.

2. S3B opt-in automated incremental sync
   - Automatic production writes stay disabled by default until S3B approval.
   - Auto-sync requires bounded batch size, failure budgets, safe scheduling, clear UI state, and pause/disable controls.

## Source / Entity Work Later

1. R1R SourceConcept route redo under GOV3 contracts
   - Re-enter only after S2 closeout is stable.
   - Keep AI proper-noun tags as weak evidence only.
   - Do not create confirmed Entity assignments from AI tags.

2. Later provider / Pixiv / source metadata / entity bridge
   - Provider/Pixiv/gallery-dl/SauceNAO/Google remain out of S2.
   - Future provider work must be cache-first, budgeted, privacy-gated, and separately approved.
   - Entity bridge writes require explicit phase approval, provenance rules, and executable contracts.
