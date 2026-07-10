# Current Mainline Roadmap

Status: active during SCV2-R2 after PR #133 / A1R merged into `main` on
2026-07-10.

This is the durable short-term routing document. It records the accepted
mainline sequence and current stop boundary without turning the handoff into a
historical ledger.

## Current Baseline

- PR #113 / Phase 4.7-S2 established the real production baseline library.
- PR #122 / PROD-LAUNCHER-UX1/PF1 established the accepted temporary Windows
  personal production launcher.
- PR #123 / PD1-A-R1 established the executable production/development
  separation contract.
- PR #124 / S2G-M1, PR #125 / S3A-M1, PR #126 / S3A-M2, and PR #129 /
  S3A-M2-R are merged. Issue #130 tracks deferred manual-sync hardening.
- PR #132 / R1R is merged at
  `7224a61aeaea32cd86a07e8dfd6cf6a6d7fcc0ef`; its full-chain result is
  `target_met_full_chain` over restored dev/test evidence.
- PR #133 / A1R is merged at
  `4a44d5809c9ec567bf59474cc3e20df62a0e97de`; it approved exactly one next
  phase, SCV2-R2.
- SCV2-R2 is the current PR #134 review branch. Its isolated result is
  `target_met_constraint_aware_r2`, but it does not approve any downstream
  phase, provider call, production work, or truth promotion.

## Accepted Sequence

1. `S2G-M1: AI Tagging Execution and Manual Sync Foundation` - merged in
   PR #124; no production writes.

2. `S3A-M1: Guarded Manual Sync Execute` - merged in PR #125; sync remains
   manual/operator-triggered.

3. `S3A-M2: Production Delta Manual Sync E2E + GPU Telemetry` - merged in
   PR #126; owner-run GUI Execute run #18 met current-stage DB-truth acceptance.

4. `S3A-M2-R: Manual Sync Stabilization and Operator Validation` - merged in
   PR #129 and closed as operator-ready with visible non-clean debt.

5. `R1R: Full SourceConcept Pipeline Replay / Remediation` - merged in
   PR #132 with all 6,429 eligible pairs adjudicated and a durable cache.

6. `SCV2-A1R: Route Audit Rerun After R1R` - merged in PR #133. It was
   read-only and selected SCV2-R2 as the sole next phase.

7. `SCV2-R2: Constraint-Aware SourceConcept Graph Remediation` - current
   PR #134.
   It separates identity materialization from review evidence, enforces
   component-level cannot constraints, partitions oversized blocks, guards
   unknown roles, and reuses R1R judgments without new provider calls.

8. `Pixiv/source metadata strategy polish` - later only if a separate route
   decision makes source coverage the dominant approved bottleneck. PX1-B is
   not authorized by R2.

9. `S3B: Opt-in automated incremental sync` - later and opt-in only. It stays
   disabled by default and must bind to production runtime config, not the
   development `.env`.

10. `S2F0: Desired-media gap audit / support decision report` - low priority,
    audit-only, and not implementation.

## R2 Evidence And Remaining Debt

- Fixed upstream evidence: 15 tables, exact row-count and row-content match
  from R1R baseline to clone and before/after R2.
- Graph correctness: 0 review-only unions, 0 direct LLM cannot violations,
  0 deterministic hard conflicts, and 0 transitive cannot violations.
- Scale shape: largest component 1,057 -> 88 signals; `26+` component bucket
  25 -> 4; 3,443 oversized hub edges prevented.
- Judgment-derived same accounting: 2,072 existing same decisions consist of
  305 compatible proof-grade pairs, 1,767 semantic priors, and 0 invalidated.
  The compatible equation is `305 = 22 retained + 283 intentionally
  constrained + 0 unexplained`; every split has a private blocker-ledger
  entry. Known cannot avoided remains 1,546 / 1,546.
- Unknown-role diagnostics: 4,455 deterministic candidates and 104 LLM
  must-link candidates; materialized, unauthorized materialized, direct
  cannot, deterministic hard-conflict, and transitive cannot counts are all 0.
- LLM boundary: 2,284 new/missing pairs are review-only and remain blocked for
  separate approval; no provider was initialized or called.
- Visible debt: aggregate gaps 4,443 -> 9,363, search symmetry remains 0 / 10,
  unmatched seeds remain 16, and average pairwise search overlap fell from
  0.3752 to 0.1552. The stricter graph exposes rather than hides this debt.
- Quality interpretation: the narrow constraint-remediation target is met;
  search quality improved, gap quality improved, recall closure complete, and
  route quality ready for scale are all false. R2R closure remains required
  under separate approval and has not started.

## What Is Intentionally Not Next

- Do not start a downstream phase merely because the R2 contract passed.
- Do not call the LLM for new R2 pairs without separate operator approval.
- Do not start provider/Pixiv/gallery-dl/SauceNAO/Google acquisition work,
  PX1-B, Provider-2, scale-up, Entity bridge, confirmed assignments,
  `media_tags` truth, Entity truth, or SourceConcept truth promotion.
- Do not run production import, classification, AI tagging, localization,
  schema migration, source/iCloud write, cleanup, delete, reset, drop, or
  truncate without a separate approved phase and executable contract.
- Do not add automatic, scheduled, startup, service, or unattended production
  sync. Manual sync remains the default.

## Production / Development Separation

- Feature work uses dev/test DBs, dev/test storage, fixtures, or restored
  snapshots. The R1R evidence DB is read-only input; R2 writes only to a
  separate test clone.
- Development `.env` is not production truth. Production import or mutation
  requires explicit promotion mode, clean identity gates, backup proof where
  applicable, redacted public artifacts, and private ignored ledgers.
- Public reports remain aggregate-only and path-redacted. Fixed-input hashes,
  raw source labels, cache rows, review ledgers, and review packs remain local
  ignored artifacts.

## Choosing The Next Work

The current action is the final bounded review of SCV2-R2 PR #134, not another
phase.
After merge, any next route must be separately approved and should first decide
whether to evaluate the 2,284 blocked review pairs, improve search/gap semantics,
or gather new source evidence. R2 itself authorizes none of those routes.
