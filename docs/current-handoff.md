# Current Handoff - V.I.O.L.E.T.

> SCV2-ML1 starts from merged PR #135 on the corrected tag-search semantics.

## Canonical State

| Item | Value |
|------|-------|
| Accepted baseline | PR #135 merged at `5bbbb8ff13b140ea77a839757603714bfdd87181` |
| Accepted phase | `SCV2-R2R: Autonomous Recall and Search Closure` |
| Accepted status | `partial_autonomous_closure` |
| Current phase | `SCV2-ML1: Multilingual Alias and Source-Metadata Closure` |
| Source DB | `blombooru_scv2_r2_review4_test_20260710` (immutable input) |
| Working DB | `blombooru_scv2_r2r_dryrun_test_20260710` (immutable accepted R2R evidence) |
| Candidate accounting | `3319 = 1522 must_link + 1791 cannot_link + 6 deferred_nonblocking` |
| Materialized SourceConcept / needs_review | `1083 / 0` |

R2R autonomous pair disposition, materialization, graph safety, and zero-provider
closeout are accepted. All 12,249 signals remain retained and no human review
queue exists. The accepted pair cache and materialization evidence must be reused;
ML1 must not repeat the 3,319 adjudications or mutate the accepted R2R database.

## Corrected Search Semantics

- Search-result union is not identity union.
- `cannot_link` prevents identity materialization and unsupported alias propagation;
  it does not suppress direct retrieval of media that independently carry the same
  observed name or tag.
- A shared bare name legitimately returns the union of every directly or validly
  alias-supported media result across distinct concepts, roles, and works.
- Additional query terms are AND constraints and intersect at media-result level.
- Search is invalid only for unsupported/rejected results, ignored constraints, or
  identity mutation—not merely because several identities share one surface name.

The R2R `false_broad_union_indicator_count` and
`cannot_linked_search_contamination_count` fields are retained as historical
diagnostics measured under the superseded one-name/one-family interpretation.
They are not generic proof of product-search failure.

## Current Boundary

ML1 executed the exact Pixiv metadata-only route in the isolated
`blombooru_scv2_ml1_acquisition_test_20260712` database under the project-owner
`operator_accepted_local_credential_risk_v1` waiver. Credential rotation and
old-secret fingerprint scanning were explicitly waived, not claimed complete.
The default non-waived provider gate remains unchanged. gallery-dl 1.32.1 used
the user-managed profile, `--dump-json --no-download`, argument-safe subprocess
execution, checkpointing, and at least two-second spacing.

The final actual-data audit in PR #136 stops at
`blocked_pixiv_acquisition_execution_incomplete`:

- all `2,285` candidate media / `2,235` works are accounted;
- `2,196` media / `2,155` works are metadata complete;
- `66` media / works have authenticated terminal-unavailable evidence;
- pending, retryable, unexplained missing, auth, rate, and network final counts
  are zero;
- `11` normal-manifest works and all `3` conflict works remain exact
  `provider_metadata_missing_attempted_local_page` cases: Pixiv returned the
  correct work's p0 metadata while the local queue requires p1, so ML1 did not
  invent a page link or winner;
- `1,817` provider/gallery-dl requests are attributable to the exact governed
  manifests, diagnostics, and corrected replay cycles; every attempted work has
  exactly one final outcome and no systemic stop occurred;
- main/conflict executable fingerprints are
  `b7d5ba037ecd174cb727e1fc9a03a80d2f903301c2ad5f0eb2407725c2082516` and
  `9c8a038b4e07930a6d75fc52dd33f2764630be275d1d2af81f2913df8b3bd17c`;
- the real multilingual baseline is now `606` identity-eligible and `3,642`
  search-only families, runtime equivalence `0.897363`, with `30` remaining ML2
  candidate-generation gaps;
- fixed/forbidden evidence remained unchanged, the executable ML1 contract
  passed with zero errors/warnings, and the clean review pack has exact member
  and checksum equality.

The optional owner sample remains historical stage evidence, not a runtime or
merge gate. Current claims are `target_met=false`, `safe_to_merge=false`, and
`route_approved=false`. Project-lead adjudication must decide how to correct or
explicitly govern the 14 local p1/provider p0 mismatches before merge or ML2.

Potential future phase `PX-REC1: Archived Source Metadata Recovery` remains
deferred until authenticated Pixiv acquisition measures the actual terminal
deleted/private/unavailable population. Future evidence may use an exact Pixiv
work ID preserved in a Danbooru source URL, exact cryptographic hash,
high-confidence perceptual hash, or another independently verified image
correspondence. Recovered metadata must retain Danbooru provenance and must not
be represented as original Pixiv metadata. No fixed trigger threshold is set
before terminal-rate evidence exists.

PX1-B broad acquisition, Provider-2, scale-up, Entity bridge, production,
full-library execution, broad metadata acquisition, truth promotion, AI
tagging, classification, and localization remain unauthorized. If existing data
cannot close normal Pixiv gaps, stop at the bounded retry/operator gate. New-pair
candidate remediation and creator SourceConcept closure remain the next phase.

## Durable Links

- Tag-search policy: `docs/source-concept-tag-search-semantics.md`.
- Autonomous resolution policy: `docs/source-concept-autonomous-resolution-policy.md`.
- Evidence reuse policy: `docs/source-evidence-snapshot-reuse-policy.md`.
- Pixiv ingestion/promotion policy: `docs/pixiv-metadata-ingestion-and-promotion-policy.md`.
- Contract catalog: `docs/phase-contracts.md`.
- Current roadmap: `docs/roadmap/current-mainline-roadmap.md`.
- R2R report: `docs/reports/phase-4.5-scv2-r2r-autonomous-recall-search-closure.md`.
