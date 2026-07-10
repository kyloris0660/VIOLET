# Current Handoff - V.I.O.L.E.T.

> Last updated for SCV2-R2 after PR #133 / A1R merged.
> Active branch: `codex/scv2-r2-constraint-aware-source-concept-graph-remediation`.
> Read this file first for current state; use linked reports for detailed evidence.

## Canonical Context

| Item | Value |
|------|-------|
| Repository | `kyloris0660/VIOLET` |
| Current phase | `SCV2-R2: Constraint-Aware SourceConcept Graph Remediation` |
| Current branch | `codex/scv2-r2-constraint-aware-source-concept-graph-remediation` |
| Current PR | PR #134 |
| Baseline main | PR #133 merge `4a44d5809c9ec567bf59474cc3e20df62a0e97de` |
| R1R evidence DB | `blombooru_r1r_restored_test_20260618` (preserved) |
| Final isolated R2 DB | `blombooru_scv2_r2_test_20260710b` |
| Current result | `target_met_constraint_aware_r2`; no downstream route approved |
| Python | `.\venv\Scripts\python.exe` |

## Current State

- PR #132 / R1R and PR #133 / A1R are merged into `main`.
- R2 reused one immutable 15-table R1R snapshot. Row counts and content
  fingerprints matched baseline-to-clone and before-to-after; upstream
  observations and provider cache were not mutated.
- Only the seven SourceConcept-owned output tables were rebuilt in the isolated
  R2 DB. Production, source/iCloud, import, AI, provider, Entity, and truth
  paths were not touched.
- The focused R2 contract and public redaction contract pass. The private
  review pack is checksummed, ZIP-valid, ignored, and uncommitted.
- PR #134 final closeout changes only contract/report safety and containment.
  Resolver evidence remains commit `4b7b57c0d66299620322e9c653524788e376c0fe`;
  resolver code and the R2 databases were not rerun or changed.

## R2 Result

- Identity and review graphs are separated: 10,971 materialized identity edges
  and 36,119 review-overlay edges; review-only union count is 0.
- Direct LLM cannot, deterministic hard-conflict, and transitive cannot
  violations inside materialized components are all 0.
- Unknown-role bridge candidates/materialized are 4,455 / 0. Oversized-block
  partitioning prevented 3,443 hub edges; `26+` components fell from 25 to 4,
  and the largest component fell from 1,057 signals to 88.
- Of 6,429 cached R1R judgments: 0 exact-version reuse, 2,080 compatible stable
  pair-identity reuse, 4,349 semantic priors, and 0 invalidated. New calls: 0.
- R2 found 2,284 genuinely new/missing pairs. They materialize 0 identity edges
  and remain `blocked_llm_approval_required`; projected future cost is $0.73088.
- Compatible known-same retention is 37 / 37. Three additional same labels are
  intentionally split by cached transitive cannot constraints with private
  reasons. Known-cannot avoidance is 1,546 / 1,546.
- Concepts changed from 2,767 to 5,396 and `needs_review` from 1,703 to 4,304
  because uncertain/review evidence is no longer collapsed into identity
  components. Raw `needs_review` count is not a quality score.
- Aggregate gap signals increased from 4,443 to 9,344 under the stricter graph.
  Search remains 0 / 10 symmetric with 16 unmatched seeds, and average
  pairwise Jaccard fell from 0.3752 to 0.1539. Constraint target met is `true`;
  search quality improved, gap quality improved, and recall closure complete
  are all `false`.

## Current Stop Condition

- Complete the final bounded review of PR #134 before any merge decision.
- Do not call an LLM for the 2,284 new pairs without separate operator approval.
- Do not start PX1-B, Provider-2, scale-up, Entity bridge, production work,
  full-library execution, or SourceConcept/Entity/`media_tags` truth promotion.
- No downstream phase is selected by R2. A later route decision must account
  for the larger truthful gap surface and unchanged search symmetry.
- R2R recall/search closure is still required, but R2R was not started or
  authorized by this closeout.

## Durable Policies And Links

- Evidence snapshot reuse policy:
  `docs/source-evidence-snapshot-reuse-policy.md`.
- SourceConcept LLM cache policy:
  `docs/source-concept-llm-adjudication-cache.md`.
- Current mainline roadmap: `docs/roadmap/current-mainline-roadmap.md`.
- Phase contracts: `docs/phase-contracts.md`.
- R2 report:
  `docs/reports/phase-4.5-scv2-r2-constraint-aware-graph-remediation.md`.
- R2 summary:
  `docs/reports/phase-4.5-scv2-r2-constraint-aware-graph-remediation-summary.json`.
