# R1R-P0: Current Handoff and Roadmap Slim Refresh

## Status

R1R-P0 refreshes the durable current-state docs after PR #129 / S3A-M2-R PR-R2
merged. This is documentation and static validation only. It does not start R1R
implementation.

## Stale Docs Found

- `docs/current-handoff.md` still said it was last updated for S3A-M2-R R0/R1,
  listed the S3A-M2-R branch as active, and treated S3A-M2-R as current.
- `docs/roadmap/current-mainline-roadmap.md` still said the active status was
  after PR #126 / S3A-M2 merge and S3A-M2-R stabilization start.
- `docs/project-roadmap.md` still described S3A-M2-R as current and listed
  "complete S3A-M2-R stabilization" as the next near-term item.
- `docs/roadmap/post-s2-production-roadmap.md` still treated S3A-M1 as the
  current production-utility follow-up.
- `tests/test_pd1a_mainline_governance.py` still guarded the older PD1-A-R1
  route wording and failed before the refresh.
- `docs/phase-contracts.md` did not need a current-phase patch; its R1R and
  route-gate references were already aligned.

## Files Updated

- `docs/current-handoff.md`
- `docs/roadmap/current-mainline-roadmap.md`
- `docs/project-roadmap.md`
- `docs/roadmap/post-s2-production-roadmap.md`
- `tests/test_pd1a_mainline_governance.py`
- `tests/test_current_handoff_freshness.py`
- `docs/reports/r1r-p0-current-handoff-roadmap-refresh.md`
- `docs/reports/r1r-p0-current-handoff-roadmap-refresh-summary.json`

## What Was Slimmed

`docs/current-handoff.md` was reduced from a long historical ledger into an
operational handoff: current state, next phase, hard non-goals, R1R isolation,
and links to the durable reports. Historical PR lists were replaced with links
and short current-state bullets.

## Current Next Phase

The current next technical phase is R1R: full SourceConcept pipeline
replay/remediation under GOV3 contracts.

R1R is required because INC1 found old R1 was deterministic-only and did not
prove the full SC1 resolver chain with bounded LLM pair adjudication. A1R must
follow R1R before any route approval. R2, PX1-B, Provider-2, scale-up, Entity
bridge, and SourceConcept truth promotion remain blocked until R1R plus A1R
produce valid route evidence.

PR #130 tracks deferred S3A-M2-R PR-R2/manual-sync hardening debt. It does not
block R1R.

## Production Isolation Reminder

R1R must not use or write production DB, production storage, production source
roots, or production private ledgers. R1R execution must use dev/test/restored
snapshot DB only, with matching non-production storage and local ignored private
artifacts.

## Explicit Non-Goals

- No R1R implementation started.
- No SourceConcept resolver run.
- No LLM run.
- No provider, Pixiv, gallery-dl, SauceNAO, or Google run.
- No import, classification, AI tagging, or localization.
- No production DB write or production Execute.
- No source/iCloud/app-managed storage mutation.
- No Entity truth, confirmed assignment, SourceConcept truth promotion, or
  `media_tags` truth creation.
- No browser or Electron validation.
- No cleanup, delete, reset, drop, truncate, push main, or merge.

## Validation Results

- PR #129 / issue #130 metadata check: passed.
- Pre-refresh stale governance probe: failed as expected on the old
  `tests/test_pd1a_mainline_governance.py` route strings.
- Python identity check: passed with the canonical venv Python.
- `py_compile` for changed Python tests: passed.
- Focused docs/governance tests: passed, 5 tests.
- JSON parse for the new summary: passed.
- `public_redaction_contract_v1` for the new summary: initial run caught a
  sensitive public key shape; final rerun passed after renaming that key.
- `git diff --check`: passed.
- `git diff --cached --check`: passed after staging.
- Browser validation: not applicable; R1R-P0 is docs/static validation only.

## Can R1R Start After Merge?

Yes, after this PR is merged, R1R can be planned and started as a separate
approved implementation phase. R1R must still obey its own approval gate,
contract, and dev/test/restored-snapshot isolation. This PR does not itself
authorize R1R execution.
