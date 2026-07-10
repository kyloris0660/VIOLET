# Current Handoff - V.I.O.L.E.T.

> Last updated for SCV2-A1R after PR #132 / R1R merge.
> Active PR branch: `codex/scv2-a1r-route-audit-after-r1r`.
> Read this file first for current state; use linked reports for history.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Local path | `[canonical local checkout path]` |
| Current phase | `SCV2-A1R: Route Audit Rerun After R1R` |
| Current PR branch | `codex/scv2-a1r-route-audit-after-r1r` |
| Recommended next phase if A1R merges | `SCV2-R2 targeted resolver / gap reduction` |
| Baseline main | PR #132 merge `7224a61aeaea32cd86a07e8dfd6cf6a6d7fcc0ef` |
| R1R restored evidence DB | `blombooru_r1r_restored_test_20260618` |
| Stack | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + vanilla JavaScript + Electron launcher |
| Python | `.\venv\Scripts\python.exe` |

## Current State

- S2G-M1, S3A-M1, S3A-M2, and S3A-M2-R are merged.
- PR #132 / R1R is merged into `main` with merge commit
  `7224a61aeaea32cd86a07e8dfd6cf6a6d7fcc0ef`.
- R1R remediated the INC1 fidelity incident for old-R1-scale replay:
  `target_met_full_chain`, 6429 / 6429 / 6429 eligible / selected / judged
  LLM pairs, all eligible pairs adjudicated, 6429 exact-compatible durable
  cache hits, 0 new provider calls in the latest evidence regeneration, and
  0 failures / 0 remaining.
- R1R did not authorize R2, PX1-B, Provider-2, scale-up, Entity bridge,
  production writes, Entity truth, `media_tags` truth, or SourceConcept truth
  promotion. A1R remained required after R1R.
- A1R is a read-only route audit PR. It recomputed route metrics from the
  restored R1R DB with `transaction_read_only=on` and a repeatable-read stable
  snapshot; no DB writes, providers, import, AI/classification/localization, or
  truth-path mutation occurred.

## A1R Result

A1R status is `route_partially_approved_for_one_next_phase`.

The single recommended next phase is:

- `SCV2-R2 targeted resolver / gap reduction`

Why R2 is recommended:

- R1R gives valid full-chain route evidence, but it does not clear source-layer
  quality blockers.
- Post-R1R SourceConcept state is still 2767 concepts: 1064 active and 1703
  `needs_review`.
- LLM decisions are 2072 same / 3815 cannot / 542 uncertain. The high cannot
  ratio is evidence that candidate generation / weak-edge selection remains
  broad; uncertain residue is still meaningful.
- Gap audit rerun still has 4443 gap signals. Improved from old A1 by 179, but
  CJK/romaji sibling gaps, source tags without aliases, split aliases, and
  needs_review clusters remain route-blocking.
- Search seed symmetry remains poor: 10 groups tested, 0 symmetric groups,
  10 asymmetric groups, and 16 unmatched seeds.
- Source metadata coverage remains low at 531 / 3687 eligible media (14.4%),
  but resolver/search/needs_review blockers dominate before PX1-B.

## Still Blocked

A1R does not start or authorize:

- R2 implementation before this A1R PR is reviewed/merged and separately
  approved;
- PX1-B;
- Provider-2;
- scale-up / full-library expansion;
- Entity bridge;
- SourceConcept truth promotion;
- Entity truth;
- `media_tags` truth;
- production writes;
- import, classification, AI tagging, localization, provider calls, or LLM
  provider calls.

## Durable Policies

- SourceConcept full-chain work remains cache-first and budget-driven. Fixed
  small caps such as 300 pairs are not sufficient route evidence when the
  approved budget covers all eligible pairs.
- Public reports stay aggregate-only and path-redacted.
- Private ledgers, review packs, and cache records remain local ignored
  artifacts.
- Development work must not casually use production DB, production storage,
  production source roots, or production private ledgers as fixtures.

## Links And Validation Seeds

- Current mainline roadmap: `docs/roadmap/current-mainline-roadmap.md`.
- Full project roadmap: `docs/project-roadmap.md`.
- Phase contracts: `docs/phase-contracts.md`.
- SourceConcept LLM adjudication cache standard:
  `docs/source-concept-llm-adjudication-cache.md`.
- R1R full-chain replay report:
  `docs/reports/phase-4.5-scv2-r1r-full-source-concept-pipeline-replay.md`.
- R1R summary:
  `docs/reports/phase-4.5-scv2-r1r-full-source-concept-pipeline-replay-summary.json`.
- A1R route audit report:
  `docs/reports/phase-4.5-scv2-a1r-route-audit-after-r1r.md`.
- A1R summary:
  `docs/reports/phase-4.5-scv2-a1r-route-audit-after-r1r-summary.json`.
- INC1 fidelity incident:
  `docs/reports/phase-4.5-scv2-inc1-source-concept-pipeline-fidelity.md`.
- Old blocked A1 route decision:
  `docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision.md`.
