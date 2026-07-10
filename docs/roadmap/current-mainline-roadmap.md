# Current Mainline Roadmap

Status: active during SCV2-A1R after PR #132 / R1R merged into `main` on
2026-07-08.

This is the durable short-term routing document for post-#122 work. It keeps
the accepted sequence visible for ChatGPT, CodeX, and operator sessions without
turning the current handoff into a historical ledger.

## Current Baseline

- PR #113 / Phase 4.7-S2 is merged into `main`; V.I.O.L.E.T. has a real
  production baseline library.
- PR #122 / PROD-LAUNCHER-UX1/PF1 is merged into `main`; the Electron-based
  launcher is the accepted temporary Windows personal production entrypoint.
- PR #123 / PD1-A-R1 is merged into `main`; the
  `production_development_separation_contract_v1` gate remains the current
  executable production/development separation foundation.
- PR #124 / S2G-M1 is merged into `main`.
- PR #125 / S3A-M1 is merged into `main`.
- PR #126 / S3A-M2 is merged into `main`; owner-run Web Admin GUI Execute run
  #18 met current-stage DB-truth acceptance.
- PR #129 / S3A-M2-R PR-R2 is merged into `main`.
  - Merge commit: `285e76d3eaa76f02acaa9dccf2b7fc91761ca428`.
  - Status: closed as operator-ready with visible non-clean debt.
  - Truth boundary: `operator_ready=true`, `full_chain_complete=false`,
    `full_s3a_m2_r_complete=false`.
- PR #132 / R1R is merged into `main`.
  - Merge commit: `7224a61aeaea32cd86a07e8dfd6cf6a6d7fcc0ef`.
  - Status: `target_met_full_chain`.
  - R1R used restored dev/test DB `blombooru_r1r_restored_test_20260618`.
  - LLM accounting: 6429 / 6429 / 6429 eligible / selected / judged pairs;
    all eligible pairs adjudicated; latest evidence regeneration was
    cache-only with 6429 exact-compatible hits, 0 new provider calls, and
    0 failures / 0 remaining.
  - R1R did not authorize R2, PX1-B, Provider-2, scale-up, Entity bridge, or
    SourceConcept truth promotion; A1R remained required.
- Development work must not casually use production DB, production storage,
  production source roots, or production private ledgers as fixtures.

## Accepted Sequence

1. `S2G-M1: AI Tagging Execution and Manual Sync Foundation` - merged in
   PR #124. It added the AI tagging execution profile, provider
   fallback/load-control/provenance foundation, manual sync dry-run planner, and
   sync job/ledger foundation. No production writes.

2. `S3A-M1: Guarded Manual Sync Execute` - merged in PR #125. It added the
   explicit manual-sync execute path, Admin UI controls, launcher entry, CLI
   runner, and `s3a_m1_manual_sync_execute_contract_v1`. Sync remains manual.

3. `S3A-M2: Production Delta Manual Sync E2E + GPU Telemetry` - merged in
   PR #126. Owner-run GUI Execute run #18 achieved current-stage DB-truth
   acceptance.

4. `S3A-M2-R: Manual Sync Stabilization and Operator Validation` - merged in
   PR #129 and closed as operator-ready. Follow-up hardening lives in issue
   #130 and is not a mainline blocker.

5. `R1R: Full SourceConcept Pipeline Replay / Remediation` - merged in
   PR #132. R1R remediated the INC1 old-R1 deterministic-only incident by
   proving old-R1-scale full-chain SourceConcept replay with all-eligible LLM
   adjudication under a budget-driven, cache-first policy.

6. `SCV2-A1R: Route Audit Rerun After R1R` - current route audit PR. A1R is
   read-only and uses R1R public report/summary plus the restored R1R DB. A1R
   result is `route_partially_approved_for_one_next_phase` with exactly one
   recommended next phase: `SCV2-R2 targeted resolver / gap reduction`.

7. `SCV2-R2 targeted resolver / gap reduction` - recommended next technical
   phase if A1R is reviewed and merged. R2 must be separately approved and must
   define its own focused contract/dry-run/write gates. A1R does not start R2.

8. `Pixiv/source metadata strategy polish` - after source-layer resolver/gap
   quality is improved enough to make source metadata coverage the dominant
   bottleneck. PX1-B is not the immediate next route from A1R.

9. `S3B: Opt-in automated incremental sync` - later and opt-in only. It remains
   disabled by default and must bind to production profile/runtime config, not
   development `.env`.

10. `S2F0: Desired-media gap audit / support decision report` - low priority,
    audit-only, and not implementation.

## What Is Intentionally Not Next

- Do not start S3B, automatic sync, scheduled sync, startup sync, service sync,
  or unattended production sync.
- Do not treat S2G-M1, S3A-M1, S3A-M2, S3A-M2-R, R1R, or A1R evidence as
  approval for broad future production imports.
- Do not run provider, Pixiv, gallery-dl, SauceNAO, Google, or source-enrichment
  work before an approved later phase.
- Do not run PX1-B, Provider-2, scale-up, Entity bridge, confirmed assignment
  creation, `media_tags` truth, Entity truth, or SourceConcept truth promotion
  before a separate approved phase and contract.
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
- R1R/A1R evidence came from restored dev/test DB only; live production
  DB/storage/source roots were not mutated.
- Production import/classification/AI/localization/source-root/schema writes
  require explicit production/promotion mode, clean identity gates, backup proof
  where applicable, redacted public artifacts, and local ignored private ledgers.
- Public reports stay aggregate-only and path-redacted.
- Private ledgers remain local ignored artifacts.
- SourceConcept LLM pair adjudication cache records remain private ignored
  artifacts; public reports may cite only aggregate cache counts and labels.

## Choosing The Next Work

If A1R is merged, the recommended next technical phase is
`SCV2-R2 targeted resolver / gap reduction`.

R2 should focus on the remaining source-layer quality blockers identified by
A1R: high cannot ratio, meaningful uncertain residue, 4443 gap signals,
10 asymmetric search-seed groups, 16 unmatched seeds, and 1703 `needs_review`
concepts. R2 must not authorize production/truth-path work by default.
