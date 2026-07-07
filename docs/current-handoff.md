# Current Handoff - V.I.O.L.E.T.

> Last updated for R1R-P0 after PR #129 / S3A-M2-R PR-R2 closeout.
> Active PR branch: `codex/r1r-p0-current-handoff-roadmap-refresh`.
> Read this file first for current state; use linked reports for history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `[canonical local checkout path]` |
| Current phase | `R1R-P0: Current Handoff and Roadmap Slim Refresh` |
| Next technical phase | `R1R: Full SourceConcept Pipeline Replay / Remediation` |
| Required follow-up | `A1R: Route audit rerun after R1R outputs exist` |
| Baseline main | PR #129 merge `285e76d3eaa76f02acaa9dccf2b7fc91761ca428` |
| Final PR #129 head | `ef9b4447e48221ece00924afed78101640ed56e9` |
| Stack | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + vanilla JavaScript + Electron launcher |
| Python | `.\venv\Scripts\python.exe` |

## Current State

- S2G-M1, S3A-M1, and S3A-M2 are merged.
- S3A-M2-R / PR #129 is merged and closed as operator-ready. It proved the
  manual-sync operator path can plan, confirm, monitor, and validate visible
  non-clean debt.
- PR #129 did not prove clean full-chain completion:
  `operator_ready=true`, `full_chain_complete=false`, and
  `full_s3a_m2_r_complete=false`.
- Issue #130 tracks deferred S3A-M2-R PR-R2/manual-sync hardening debt. It is
  non-blocking for the mainline return to R1R.
- The project is returning to the SourceConcept route remediation mainline
  before any route approval or truth promotion.

## Next Phase

R1R is required because INC1 found a SourceConcept pipeline fidelity incident:
old R1 persisted deterministic/source-layer outputs but did not prove the full
SC1 resolver chain with bounded LLM pair adjudication. Old R1/A1 evidence must
not approve R2.

R1R must:

- run as a separately planned and approved implementation phase;
- use `source_concept_full_chain_contract_v1` before any full-chain completion
  claim;
- use the durable SourceConcept LLM adjudication cache standard: cache-first,
  checkpoint successful pair judgments immediately, reuse exact-compatible
  judgments on rerun, and keep public reporting aggregate/redacted;
- keep SourceConcept output source-layer only;
- avoid confirmed Entity assignments, Entity truth writes, and `media_tags`
  truth writes;
- avoid new provider/Pixiv/gallery-dl/SauceNAO/Google live calls unless a later
  phase separately approves them.

A1R must follow R1R before any route approval. R2, PX1-B, Provider-2, scale-up,
Entity bridge, and SourceConcept truth promotion remain blocked until R1R plus
A1R produce valid route evidence.

## R1R Isolation Requirements

- R1R execution must not use or write production DB, production storage,
  production source roots, or production private ledgers.
- R1R must use dev/test/restored-snapshot DB only, with matching dev/test or
  restored-snapshot storage and local ignored private artifacts.
- No production Execute, production import, production classification,
  production AI tagging, production localization, source/iCloud mutation, or
  app-managed storage mutation is authorized for R1R.
- If R1R needs production-like evidence, plan the snapshot/restore path first
  and keep the live production roots out of the run.

## Hard Non-Goals For R1R-P0

- Do not start R1R implementation.
- Do not run the SourceConcept resolver, LLM adjudication, providers, Pixiv,
  gallery-dl, SauceNAO, Google, import, classification, AI tagging, localization,
  production Execute, browser validation, or Electron validation.
- Do not write production DB data, SourceConcept tables, Entity truth,
  confirmed assignments, `media_tags`, source/iCloud paths, staging roots, or
  app-managed storage.
- Do not clean up, delete, reset, drop, truncate, push `main`, or merge.

## Links And Validation Seeds

- Current mainline roadmap: `docs/roadmap/current-mainline-roadmap.md`.
- Full project roadmap: `docs/project-roadmap.md`.
- Post-S2 production roadmap: `docs/roadmap/post-s2-production-roadmap.md`.
- Phase contracts: `docs/phase-contracts.md`.
- SourceConcept LLM adjudication cache standard:
  `docs/source-concept-llm-adjudication-cache.md`.
- INC1 fidelity incident:
  `docs/reports/phase-4.5-scv2-inc1-source-concept-pipeline-fidelity.md`.
- Blocked A1 route decision:
  `docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision.md`.
- Post-#122 reconciliation:
  `docs/reports/pd1-a-r1-post-122-roadmap-reconciliation.md`.
- S3A-M2-R PR-R2 closeout:
  `docs/reports/s3a-m2-r-ui-operator-validation-closeout.md`.
- S3A-M2-R PR-R2 summary:
  `docs/reports/s3a-m2-r-ui-operator-validation-summary.json`.
- Follow-up hardening debt: GitHub issue #130.
- Python identity: `& "$PY" scripts/check_python_env.py --expected-python "$PY"`.
- Docs/static validation should use focused pytest coverage and public redaction
  checks; no browser validation is required for docs-only R1R-P0.
