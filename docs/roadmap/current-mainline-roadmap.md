# Current Mainline Roadmap

Status: active during the initial cache-only SCV2-R2R run after PR #134 / R2
merged into `main` on 2026-07-10.

This is the durable short-term routing document. It records the accepted
mainline sequence and current stop boundary.

## Current Baseline

- PR #132 / R1R merged at
  `7224a61aeaea32cd86a07e8dfd6cf6a6d7fcc0ef` with 6,429 adjudicated pairs
  and a durable cache.
- PR #133 / A1R merged at
  `4a44d5809c9ec567bf59474cc3e20df62a0e97de` and approved SCV2-R2.
- PR #134 / R2 merged at
  `d553a7f51222f2c52c3fe5014e878faed7f7b5a1`. R2 established the accepted
  constraint-safe graph baseline but left recall/search closure incomplete.
- SCV2-R2R is the only active mainline phase. Its initial cache-only status is
  `blocked_llm_approval_required`; it authorizes no downstream route.

## Accepted Sequence

1. `R1R: Full SourceConcept Pipeline Replay / Remediation` - merged in PR #132.

2. `SCV2-A1R: Route Audit Rerun After R1R` - merged in PR #133.

3. `SCV2-R2: Constraint-Aware SourceConcept Graph Remediation` - merged in
   PR #134 with zero review-only union, direct/transitive cannot violation,
   unauthorized unknown-role materialization, or unexplained compatible-same
   regression.

4. `SCV2-R2R: Autonomous Recall and Search Closure` - current. It replaces
   human-review semantics with `must_link`, `cannot_link`, and
   `deferred_nonblocking`; separates materialized identities from retained
   evidence; and implements identity plus evidence-fallback source search.

5. `Pixiv/source metadata strategy polish` - later only if a separate route
   decision makes source coverage the dominant approved bottleneck. PX1-B is
   not authorized by R2R.

6. `S3B: Opt-in automated incremental sync` - later and opt-in only. It stays
   disabled by default and must bind to production runtime config.

## R2R Cache-Only Gate

- Fixed evidence: 15 upstream tables and 11 forbidden truth tables match the
  accepted R2 snapshot in the isolated R2R database.
- Candidate population: 3,319 unique all-eligible pairs. Current exact/stable
  compatible coverage is 0 / 1,035; 2,284 pairs are genuinely missing.
- Existing 6,429 cache rows classify globally as 0 exact-compatible, 2,080
  stable pair-identity, 4,349 semantic priors, and 0 invalidated.
- Initial machine dispositions are 200 `must_link`, 760 `cannot_link`, and 75
  `deferred_nonblocking`; 2,284 remain unaccounted until approved execution.
- Estimated first-pass input/completion is 639,658 / 182,720 tokens. Historical
  uncertainty projects 193 second-pass escalations and a total repo-model cost
  of `$1.912254`; proposed maximum budget is `$2.00`.
- Provider initialization/calls/errors are 0 / 0 / 0. No provider execution may
  begin without a separate explicit operator authorization.
- In-memory projection retains all 12,249 signals, previews 1,093 materialized
  concepts, and materializes 0 `needs_review` concepts. No human queue exists.
- The expanded automatic benchmark is generated with identity/fallback metrics
  separated. The current legacy 58-seed compatibility result still fails final
  search-improvement gates; target status is therefore not claimed before
  approved adjudication and final rebuild.

## What Is Intentionally Not Next

- Do not call the primary LLM until the operator approves the reported budget.
- Do not use a fallback LLM provider or reacquire gallery-dl/Pixiv/source data.
- Do not start PX1-B, Provider-2, scale-up, Entity bridge, confirmed assignment,
  production, full-library execution, or SourceConcept/Entity/`media_tags`
  truth promotion.
- Do not run import, classification, AI tagging, localization, schema migration,
  source/iCloud mutation, cleanup, delete, reset, drop, or truncate.

## Production / Development Separation

- R2R reads the preserved R2 isolated baseline and uses a separate test working
  database. Production profile, database, storage, source, and truth paths are
  forbidden.
- Public reports remain aggregate-only and path-redacted. Raw pair payloads,
  fixed fingerprints, signal names, caches, ledgers, and review packs remain
  local ignored artifacts.

## Choosing The Next Work

The only permitted next action is a separate operator decision on the proposed
`$2.00` maximum primary-provider budget for the 2,284 missing pairs. If approved,
continue in the same R2R PR; otherwise remain at
`blocked_llm_approval_required`. Do not start another phase.
