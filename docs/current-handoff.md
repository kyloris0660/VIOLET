# Current Handoff - V.I.O.L.E.T.

> PR #136 closes SCV2-ML1 as a project-lead-governed partial Pixiv metadata
> foundation. Final merge adjudication remains with the project lead.

## Canonical State

| Item | Value |
|------|-------|
| Accepted baseline | PR #135 / `5bbbb8ff13b140ea77a839757603714bfdd87181` |
| Current phase | `SCV2-ML1: Multilingual Alias and Source-Metadata Closure` |
| Accepted R2R DB | `blombooru_scv2_r2r_dryrun_test_20260710` (immutable) |
| Isolated ML1 DB | `blombooru_scv2_ml1_acquisition_test_20260712` |
| R2R dispositions | `3319 = 1522 must_link + 1791 cannot_link + 6 deferred_nonblocking` |
| SourceConcept / needs_review | `1083 / 0` |

R2R's 3,319 dispositions and 12,249 signals remain immutable.
Search-result union is not identity union; additional query terms use
media-level AND intersection.

## ML1 Final Evidence

PR #136 executed the exact 1,713-work main and 3-work conflict metadata-only
manifests under `operator_accepted_local_credential_risk_v1`. Historical
Pixiv/gallery-dl requests remain 1,817; no downloads/imports occurred.

- candidate media / works: `2,285 / 2,235`;
- complete media / works: `2,201 / 2,155`;
- terminal media / works: `66 / 66`;
- deferred absent-page rows / works: `18 / 14`;
- work equation: `2,235 = 2,155 complete + 66 terminal + 14 deferred`;
- pending, retryable, missing, normalization failure, provider mismatch, and
  blocking conflict: all `0`;
- closeout external-call delta: `0`.

Page-local governance completed 5 of the original 23 deferred rows because the
exact provider page was present; only 18 absent-page rows remain deferred. It
created no unsupported page link or conflict winner and preserved raw history.

Trusted-parent governance superseded 26 affected creator-name observations with
only an untrusted Pixiv parent, preserved 26 out-of-scope historical/manual-static
observations, and finished at 0 untrusted-parent observations. The persisted
`creator_account` audit reports 2,108 raw, retained, and trusted query-visible
values with 0 silent drops.

Final contract: `partial_ml1_pixiv_metadata_foundation_complete`,
`target_met=false`, `safe_to_merge=true`, `route_approved=true`,
`active_blockers=[]`. Keep the ignored manifests, checkpoints, ledgers,
owner-review artifacts, and review pack as required provenance.

## Next Boundary

Only separately governed `SCV2-ML2: Multilingual Identity Candidate Closure` is
route-approved: 606 identity-eligible families, 3,642 search-only families,
runtime equivalence 0.897363, and 30 candidate-generation gaps. SourceConcept
remains source-layer only; semantic completeness is not an ML1 requirement.

Future gates own the six deferred hardening items:
`PRE-NEXT-PROVIDER-EXECUTION-HARDENING` (spacing, scope keys, conflict mismatch,
terminal ordering), `CONTROLLED-SCALE-AUDIT-DEBT` (denominator), and
`PRE-NONWAIVED-PROVIDER-CREDENTIAL-HARDENING` (delimiter scanning). Current
overlap/provider mismatch/systemic stop are 0 and no provider call is authorized
in this closeout or ML2.

Further metadata acquisition, PX1-B, Provider-2/PX-REC1, scale/full-library,
Entity/truth, production, import, AI tagging, classification, and localization
remain unauthorized. The terminal category `66 / 1,716` is not a pure deletion
rate.

## Durable Links

- [Current roadmap](roadmap/current-mainline-roadmap.md)
- [Pixiv policy](pixiv-metadata-ingestion-and-promotion-policy.md)
- [Phase contracts](phase-contracts.md)
- [ML1 report](reports/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure.md)
