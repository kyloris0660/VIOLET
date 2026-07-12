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

The corrected ML1 re-audit in PR #136 currently stops at
`blocked_credential_rotation_confirmation_required`:

- `2,285` canonical candidate media/pages and `2,235` distinct works are fully
  accounted;
- `527` media / `519` works have complete matching metadata;
- `1,755` media have no durable attempt/result evidence, representing `1,713`
  exact deduplicated incremental work requests;
- `3` conflict media expose `9` field-token memberships across `3` distinct
  unresolved works; no concrete extracted work ID disappears from accounting;
- the real multilingual baseline separates `314` identity-eligible families
  from `3,625` search-only families; unsupported runtime results are `0`, while
  `303` identity candidate-generation gaps remain deferred;
- no provider or LLM call occurred, no fixed/forbidden evidence changed, and the
  ML1 contract passed for the honest blocked status.

The project owner has authorized the exact corrected Pixiv manifest in PR #136,
but execution remains forbidden until affected credentials are rotated/revoked,
new values remain only in the user-managed store, old-secret fingerprints scan
clean, and `VIOLET_CREDENTIAL_ROTATION_CONFIRMED=true` is present. The private
60-work owner sample remains optional deterministic diagnostic evidence; it is
not a runtime gate, does not require row-by-row owner adjudication, and is not a
human dependency in normal ingestion. The current environment does not contain
the credential confirmation or compromised-secret fingerprint input, so no
profile check, canary, or external request has run.

The isolated acquisition database is
`blombooru_scv2_ml1_acquisition_test_20260712`, cloned once from the accepted
R2R snapshot. Pre-credential preparation records 1,713 pending acquisition
works plus 3 separately governed conflicts. Deterministic creator backfill
accounts for 536 stable IDs, 545 display names, 459 accounts, and 536 raw or
derived profile identities; name/account search support is 1.0 and silent field
drop is zero. Current status remains
`blocked_credential_rotation_confirmation_required`.

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
