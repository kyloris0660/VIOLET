# Post-S2 Production Roadmap

Status: accepted production routing reference after PR #129 / S3A-M2-R PR-R2
merged and closed as operator-ready.

Canonical short version: `docs/roadmap/current-mainline-roadmap.md`.
This file keeps the post-S2 production utility track aligned with the current
mainline route. It does not authorize S3B execution, provider calls,
SourceConcept reruns, Entity bridge writes, desired-media backfill, or automatic
production sync.

## Baseline

- PR #113 / Phase 4.7-S2 is merged; the project has a real production baseline
  library.
- PR #122 / PROD-LAUNCHER-UX1/PF1 is merged; the launcher is the accepted
  temporary Windows personal production entrypoint.
- PR #123 / PD1-A-R1 is merged; `production_development_separation_contract_v1`
  remains the executable production/development separation gate.
- PR #124 / S2G-M1 is merged.
- PR #125 / S3A-M1 is merged.
- PR #126 / S3A-M2 is merged.
- PR #129 / S3A-M2-R PR-R2 is merged and closed as operator-ready with visible
  non-clean debt. Issue #130 tracks non-blocking hardening debt.
- Production DB, storage, source roots, and private ledgers must stay separate
  from dev/test fixtures and feature-branch validation.

## Accepted Sequence

1. `S2G-M1: AI Tagging Execution and Manual Sync Foundation` - merged in
   PR #124. It added the AI/manual-sync foundation in dry-run/dev-test mode and
   did not authorize production writes.

2. `S3A-M1: Guarded Manual Sync Execute` - merged in PR #125. It added explicit
   manual execute wiring. Sync remains manual; no automatic, scheduled,
   startup, service, or unattended sync is authorized.

3. `S3A-M2: Production Delta Manual Sync E2E + GPU Telemetry` - merged in
   PR #126. Owner-run GUI Execute run #18 achieved current-stage DB-truth
   acceptance.

4. `S3A-M2-R: Manual Sync Stabilization and Operator Validation` - merged in
   PR #129. It closed the production utility stabilization path as
   operator-ready, not clean full-chain complete.

5. `R1R: Full SourceConcept Pipeline Replay / Remediation` - current next
   technical phase. It is required because INC1 found old R1 was
   deterministic-only and did not prove the full SC1 resolver chain with
   bounded LLM pair adjudication. R1R must use dev/test/restored-snapshot DB
   only and must not use production DB/storage/source roots.

6. `A1R: Route Audit Rerun After R1R` - required before R2, PX1-B, Provider-2,
   scale-up, Entity bridge, or SourceConcept truth promotion.

7. `Pixiv/source metadata strategy polish` - after A1R. Make the Pixiv/source
   metadata route reliable before adding providers.

8. `S3B: Opt-in automated incremental sync` - later, disabled by default, and
   opt-in only until explicitly approved. It must account for production
   profile/runtime config instead of development `.env`.

9. `S2F0: Desired-media gap audit / support decision report` - low priority,
   audit-only, and not implementation.

## Later Automated Sync

- S3B is not part of R1R.
- Scheduled or unattended sync remains disabled by default.
- Startup tasks, system services, and long-running daemons require a separate
  opt-in phase and are not authorized by S2G-M1, S3A-M1, S3A-M2, S3A-M2-R, or
  R1R.

## Production/Development Gate

- Develop branches must use dev/test DB, dev/test storage, fixtures, or
  restored snapshots.
- R1R must use dev/test/restored-snapshot DB only.
- Development `.env` must not be converted into production.
- Production execution phases must use explicit production profile/runtime
  config.
- Production source-root registration/replacement requires clean production
  identity gates and valid backup proof.
- Schema setup/migration paths must not run when env/storage/DB identity gates
  are blocked.
- Public reports are aggregate-only and path-redacted.
- Private ledgers remain local ignored artifacts.

## Current Non-Goals

R1R-P0 and the upcoming R1R route remediation do not authorize production
import/classification/AI/localization, S3B automation, provider/Pixiv/gallery-dl
/ SauceNAO / Google calls, SourceConcept truth promotion, Entity bridge,
confirmed assignments, desired-media backfill, cleanup, delete, reset, drop,
truncate, push main, or merge.
