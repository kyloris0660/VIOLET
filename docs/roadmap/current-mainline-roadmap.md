# Current Mainline Roadmap

Status: active after PR #129 / S3A-M2-R PR-R2 merged and closed as
operator-ready on 2026-07-06.

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
  - Final PR head before merge: `ef9b4447e48221ece00924afed78101640ed56e9`.
  - Status: closed as operator-ready with visible non-clean debt.
  - Truth boundary: `operator_ready=true`, `full_chain_complete=false`,
    `full_s3a_m2_r_complete=false`.
- Issue #130 tracks deferred S3A-M2-R PR-R2/manual-sync hardening debt and does
  not block R1R.
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

5. `R1R: Full SourceConcept Pipeline Replay / Remediation` - current next
   technical phase. R1R is required because INC1 found old R1 was
   deterministic-only and did not prove bounded LLM pair adjudication from the
   full SC1 resolver chain. R1R must use dev/test/restored-snapshot DB only and
   must not use production DB/storage/source roots. R1R and future full-library
   SourceConcept replays must use budget-driven, cache-first LLM adjudication:
   exact-compatible cached judgments are reused, successful new judgments are
   checkpointed immediately, and fixed small caps such as 300 pairs cannot stand
   in for all eligible pairs when the approved budget covers the full set.

6. `A1R: Route Audit Rerun After R1R` - required before R2, PX1-B, Provider-2,
   scale-up, Entity bridge, or SourceConcept truth promotion can be considered.
   Old R1/A1 evidence cannot approve R2 during the INC1 pipeline fidelity
   incident.

7. `Pixiv/source metadata strategy polish` - after A1R. Make the Pixiv/source
   metadata route reliable before adding providers.

8. `S3B: Opt-in automated incremental sync` - later and opt-in only. It remains
   disabled by default and must bind to production profile/runtime config, not
   development `.env`.

9. `S2F0: Desired-media gap audit / support decision report` - low priority,
   audit-only, and not implementation.

## What Is Intentionally Not Next

- Do not start S3B, automatic sync, scheduled sync, startup sync, service sync,
  or unattended production sync.
- Do not treat S2G-M1, S3A-M1, S3A-M2, or S3A-M2-R evidence as approval for
  broad future production imports.
- Do not run provider, Pixiv, gallery-dl, SauceNAO, Google, or source-enrichment
  work before an approved later phase.
- Do not run SourceConcept R2, PX1-B, Provider-2, scale-up, Entity bridge,
  confirmed assignment creation, or SourceConcept truth promotion before R1R
  plus A1R.
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
- R1R must use dev/test/restored-snapshot DB only; live production DB/storage
  and production source roots are out of scope for R1R execution.
- Production import/classification/AI/localization/source-root/schema writes
  require explicit production/promotion mode, clean identity gates, backup proof
  where applicable, redacted public artifacts, and local ignored private ledgers.
- Public reports stay aggregate-only and path-redacted.
- Private ledgers remain local ignored artifacts.
- SourceConcept LLM pair adjudication cache records remain private ignored
  artifacts; public reports may cite only aggregate cache counts and labels.

## Choosing The Next Work

After PR #129, the recommended next technical phase is R1R. R1R should be
planned separately, then executed only under its approved contract and
development/restored-snapshot isolation. A1R follows R1R before any route
approval.
