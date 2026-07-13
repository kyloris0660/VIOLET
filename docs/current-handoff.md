# Current Handoff - V.I.O.L.E.T.

> PR #136 closes SCV2-ML1 as a project-lead-governed partial Pixiv metadata
> foundation. Final merge adjudication remains with the project lead.

## Canonical State

| Item | Value |
|------|-------|
| Accepted baseline | PR #135 merged at `5bbbb8ff13b140ea77a839757603714bfdd87181` |
| Accepted phase/status | `SCV2-R2R` / `partial_autonomous_closure` |
| Current phase | `SCV2-ML1: Multilingual Alias and Source-Metadata Closure` (final closeout) |
| Accepted R2R DB | `blombooru_scv2_r2r_dryrun_test_20260710` (immutable) |
| Isolated ML1 DB | `blombooru_scv2_ml1_acquisition_test_20260712` |
| R2R candidate accounting | `3319 = 1522 must_link + 1791 cannot_link + 6 deferred_nonblocking` |
| Materialized SourceConcept / needs_review | `1083 / 0` |

R2R's 3,319 autonomous pair dispositions and 12,249 signals remain accepted and
immutable. ML1 reused them and did not recreate a human review queue.

## Search Semantics

Search-result union is not identity union. `cannot_link` prevents identity union
and unsupported alias propagation; it does not hide independently supported
same-name media. Additional query terms use media-level AND intersection.

## ML1 Final Evidence

PR #136 executed the exact `1,713`-work main and 3-work conflict metadata-only
manifests in `blombooru_scv2_ml1_acquisition_test_20260712` under the project-owner
`operator_accepted_local_credential_risk_v1` waiver. Historical execution used
`1,817` attributable Pixiv/gallery-dl metadata requests, no downloads/imports,
and left the accepted R2R DB unchanged.

- candidate media / works: `2,285 / 2,235`;
- complete media / works: `2,196 / 2,155`;
- authenticated terminal media / works: `66 / 66`;
- governed deferred page-mismatch queue rows / works: `23 / 14`;
- exhaustive work equation: `2,235 = 2,155 complete + 66 terminal + 14 deferred`;
- pending, retryable, missing, normalization failure, provider mismatch, and
  unresolved blocking conflict works: all `0`;
- closeout external-call delta: `0`;
- main/conflict manifest fingerprints:
  `b7d5ba037ecd174cb727e1fc9a03a80d2f903301c2ad5f0eb2407725c2082516` /
  `9c8a038b4e07930a6d75fc52dd33f2764630be275d1d2af81f2913df8b3bd17c`.

The 14 local-p1/provider-p0 cases are
`deferred_nonblocking_source_page_mismatch` under
`source_page_mismatch_deferred_nonblocking_v1`. Raw evidence remains preserved;
no local-page link, p0 substitution, or conflict winner was invented. Generic
restart, requeue, cache reuse, or unchanged provider metadata cannot reopen the
state. Reopening requires separately governed materially stronger exact-page or
source evidence, a corrected filename/page identity, or an explicit operator
identity correction.

Final status is `partial_ml1_pixiv_metadata_foundation_complete` with
`target_met=false`, `safe_to_merge=true`, `route_approved=true`, and
`active_blockers=[]`. The private manifests, checkpoints, outcome/governance
ledgers, owner-review artifacts, and final review pack are required provenance;
keep them ignored and back them up to owner-controlled local/NAS storage.

## Next Boundary

Route approval is limited to separately governed `SCV2-ML2: Multilingual
Identity Candidate Closure`. Its inputs are `606` identity-eligible families,
`3,642` search-only families, runtime equivalence `0.897363`, and `30`
candidate-generation gaps. SourceConcept remains source-layer only.

PX1-B broad metadata acquisition, Provider-2/PX-REC1, scale/full-library work,
Entity/truth promotion, production, media import, AI tagging, classification,
and localization remain unauthorized. The measured terminal category is
`66 / 1,716` governed acquisition works (about `3.846%`), not a pure deletion
rate. PX-REC1 remains deferred and any future recovery must preserve non-Pixiv
provenance.

## Durable Links

- Search policy: `docs/source-concept-tag-search-semantics.md`.
- Pixiv ingestion/promotion policy: `docs/pixiv-metadata-ingestion-and-promotion-policy.md`.
- Contract catalog: `docs/phase-contracts.md`.
- Current roadmap: `docs/roadmap/current-mainline-roadmap.md`.
- ML1 report: `docs/reports/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure.md`.
