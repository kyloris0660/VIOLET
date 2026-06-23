# PD1-A-R1: Post-#122 Roadmap Reconciliation

## Purpose

PD1-A-R1 reconciles persistent roadmap, handoff, and executable contract state
after PR #122. PR #122 added the Electron V.I.O.L.E.T. production launcher,
local ignored production profile/runtime config, root-level Windows launcher
entry, and production/development runtime separation. This phase does not start
S2G, R1R, A1R, S3A, S3B, SourceConcept mutation, Entity truth writes, provider
calls, DB writes, or production sync.

## Source Files Inspected

- `docs/current-handoff.md`
- `docs/project-roadmap.md`
- `docs/roadmap/current-mainline-roadmap.md`
- `docs/roadmap/post-s2-production-roadmap.md`
- `docs/production-launcher.md`
- `docs/reports/prod-launcher-ux1-production-profile-summary.json`
- `docs/reports/prod-launcher-ux1-production-profile.md`
- `docs/reports/phase-4.5-scv2-inc1-source-concept-pipeline-fidelity-summary.json`
- `docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision-summary.json`
- `docs/reports/phase-4.7-s2-baseline-full-import-ai-localization-summary.json`
- `scripts/check_phase_contract.py`
- `scripts/phase_contracts/contract_checks.py`
- `scripts/phase_contracts/contract_registry.py`
- `tests/test_phase_contracts.py`
- `tests/fixtures/phase_contracts/mock_pd1a_governance_summary.json`

## #122 Merge Status And Launcher Role

PR #122 is merged. Merge commit recorded from GitHub and `origin/main`:
`aece424df2814ef0d840f9fe472a9d19478d2020`.

The #122 launcher is now the accepted temporary Windows personal production
entrypoint for current production library operation. It uses local ignored
production profile/runtime config and keeps development `.env` out of the
production source-of-truth path. Deferred launcher reviewer issues are not
current blockers for this Windows personal launcher path; they remain future
runtime/schema hardening debt.

This does not make #122 the long-term production configuration architecture.
Long-term production/development config unification is deferred.

## Updated Near-Term Roadmap

The accepted near-term route is now:

1. `PD1-A-R1`
2. `S2G`
3. `R1R`
4. `A1R`
5. `Pixiv/source metadata strategy polish`
6. `S3A`
7. `S3B`
8. `S2F0`

S3A and S3B remain later production phases. Future S3A/S3B execution must bind
to production profile/runtime config, not development `.env`. S3B stays disabled
by default and opt-in only.

## Why S2G Is Consolidated

The user accepted the overall roadmap with one route adjustment: do not split
S2G into S2G-1 and S2G-2/3. S2G is now one consolidated phase:

`S2G: GPU / AI Tagging Execution Foundation`

That single phase should cover GPU / AI tagging capability probe and benchmark,
provider abstraction, provenance, batch/concurrency/throttle controls, CPU
fallback, and no production writes unless a later phase explicitly approves
them. PD1-A-R1 only records that route; it does not start S2G.

## R1R And A1R Blockers

INC1 found that R1 was deterministic-only and did not prove the full SC1
resolver chain with bounded LLM pair adjudication. Old R1/A1 evidence therefore
cannot approve R2.

R1R remains required before R2. A1R remains required before route approval.
Provider-2, R2, PX1-B, Entity bridge, scale-up, and SourceConcept truth
promotion stay blocked until R1R and A1R produce valid route evidence.
SourceConcept remains source-layer evidence only; it is not Entity truth,
confirmed assignment truth, EntityAlias truth, or `media_tags` truth.

## Production / Development Separation After #122

The current operational policy is a temporary production/development split:

- development `.env` is not production source of truth;
- production execution phases must use explicit production profile/runtime
  config;
- develop branches use dev/test DB, dev/test storage, fixtures, or restored
  snapshots;
- production writes require explicit production/promotion mode plus the relevant
  execution contracts and safety gates;
- public artifacts stay aggregate-only and path-redacted;
- private ledgers and local production profiles remain ignored local artifacts.

## What Changed

- Updated `docs/current-handoff.md` for PR #122 merge state, daily launcher
  role, temporary production/development split, and consolidated near-term
  route.
- Updated `docs/project-roadmap.md` to add PR #122 to current accepted state and
  replace split S2G wording with the consolidated S2G phase.
- Updated `docs/roadmap/current-mainline-roadmap.md` with the current route
  after #122 and explicit production profile/runtime config requirements.
- Updated `docs/roadmap/post-s2-production-roadmap.md` to treat the #122
  launcher as the accepted current access path and keep S3A/S3B later.
- Updated `production_development_separation_contract_v1` so the contract
  verifies post-#122 launcher state, consolidated S2G routing, R1R/A1R blockers,
  provider/entity truth blocks, and no-mutation proof.
- Added this public report and the matching public-safe summary JSON.

## What Was Not Changed

- No launcher polishing.
- No production launcher start.
- No browser, Electron, or Computer Use validation.
- No production import, classification, AI tagging, tag localization, sync, DB
  mutation, SourceConcept mutation, Entity truth write, `media_tags` mutation,
  provider calls, LLM calls, source/iCloud mutation, or app-managed storage
  mutation.
- No S2G, R1R, A1R, S3A, S3B, Provider-2, R2, PX1-B, Entity bridge, or
  SourceConcept truth promotion started.

## Validation Results

Validation for this docs/contract reconciliation phase:

- Python identity check: passed.
- `production_development_separation_contract_v1`: passed for the PD1-A-R1
  summary JSON.
- `public_redaction_contract_v1`: passed for the PD1-A-R1 summary JSON.
- JSON formatting check: passed.
- Focused phase contract tests: passed.
- `git diff --check`: passed.

Runtime/browser validation was intentionally not run because this phase changes
docs, report JSON, and contract tests only. It does not change runtime UI,
Electron behavior, FastAPI routes, frontend JavaScript, or browser-visible app
flows.

## Next Recommended Phase

Next recommended phase:

`S2G: GPU / AI Tagging Execution Foundation`

Do not treat PD1-A-R1 as having started S2G or R1R.
