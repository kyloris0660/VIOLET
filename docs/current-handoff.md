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

ML1 owns a read-only, zero-network audit of canonical Pixiv filename candidates,
existing metadata and creator-field retention, real multilingual alias families,
candidate-generation recall, and the application runtime's AND-search semantics.
The primary unresolved quality question is multilingual alias coverage and
candidate-generation recall.

PX1-B broad acquisition, Provider-2, scale-up, Entity bridge, production,
full-library execution, metadata acquisition, truth promotion, media import, AI
tagging, classification, and localization remain unauthorized. If existing data
cannot close normal Pixiv gaps or new pairs need LLM adjudication, stop at the
separate approval gate stated by the ML1 contract.

## Durable Links

- Tag-search policy: `docs/source-concept-tag-search-semantics.md`.
- Autonomous resolution policy: `docs/source-concept-autonomous-resolution-policy.md`.
- Evidence reuse policy: `docs/source-evidence-snapshot-reuse-policy.md`.
- Contract catalog: `docs/phase-contracts.md`.
- Current roadmap: `docs/roadmap/current-mainline-roadmap.md`.
- R2R report: `docs/reports/phase-4.5-scv2-r2r-autonomous-recall-search-closure.md`.
