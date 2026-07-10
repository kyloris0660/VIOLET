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
| Final isolated R2 DB | `blombooru_scv2_r2_review4_test_20260710` |
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
- The final reviewer correction changed resolver execution semantics, so the
  downstream R2 pipeline was rerun from a fresh isolated clone. Resolver
  evidence code is `0e605ad95d20713b413340dc85e0fc80f38173dc`;
  the final report commit is report/docs-only relative to that evidence SHA.

## R2 Result

- Identity and review graphs are separated: 10,953 materialized identity edges
  and 36,137 review-overlay edges; review-only union count is 0.
- Direct LLM cannot, deterministic hard-conflict, and transitive cannot
  violations inside materialized components are all 0.
- Unknown-role deterministic/LLM candidates are 4,455 / 104. All 4,559 remain
  review-only; materialized and unauthorized materialized counts are both 0.
  Oversized-block partitioning prevented 3,443 hub edges; `26+` components
  fell from 25 to 4, and the largest component fell from 1,057 signals to 88.
- Of 6,429 cached R1R judgments: 0 exact-version reuse, 2,080 compatible stable
  pair-identity reuse, 4,349 semantic priors, and 0 invalidated. New calls: 0.
- R2 found 2,284 genuinely new/missing pairs. They materialize 0 identity edges
  and remain `blocked_llm_approval_required`; projected future cost is $0.73088.
- The judgment-derived benchmark accounts for all 2,072 existing R1R same
  decisions: 305 are compatible proof-grade pairs, 1,767 are semantic priors,
  and 0 are invalidated. The compatible equation is
  `305 = 22 retained + 283 intentionally constrained + 0 unexplained`; all
  283 splits have private blocker-ledger entries. Known-cannot avoidance is
  1,546 / 1,546.
- Concepts changed from 2,767 to 5,408 and `needs_review` from 1,703 to 4,315
  because uncertain/review evidence is no longer collapsed into identity
  components. Raw `needs_review` count is not a quality score.
- Aggregate gap signals increased from 4,443 to 9,363 under the stricter graph.
  Search remains 0 / 10 symmetric with 16 unmatched seeds, and average
  pairwise Jaccard fell from 0.3752 to 0.1552. Constraint target met is `true`;
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
