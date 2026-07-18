# Current Handoff - V.I.O.L.E.T.

> PR #137 is merged and accepted in `origin/main` at
> `7fca41151cc9e1d5b48cfe243279e66296346bae`; the accepted ML2 evidence-code
> commit is `00398a0b5b1a46d010e82c2b6f72796dbdb47918`. The separately governed
> `SCV2-SV1: Controlled Scale Replay and Promotion-Readiness Validation` run has
> reached its bounded target on the feature branch; its PR remains unmerged.

## Canonical State

| Item | Value |
|------|-------|
| Merged baseline | PR #137 / `7fca41151cc9e1d5b48cfe243279e66296346bae` |
| Current work item | `SCV2-SV1: Controlled Scale Replay and Promotion-Readiness Validation` |
| Accepted R2R DB | `blombooru_scv2_r2r_dryrun_test_20260710` (immutable) |
| Accepted SCV2-ML1 DB | `blombooru_scv2_ml1_acquisition_test_20260712` (immutable) |
| Accepted ML2 DB | `blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715` (immutable) |
| SV1 scale DB | `blombooru_scv2_sv1_controlled_scale_test_20260718` |
| Successful promotion DB | `blombooru_scv2_sv1_promotion_rehearsal_test_20260718_retry1` |
| SV1 contract | `target_met_controlled_scale_promotion_readiness`; `route_approved=false` |

Accepted predecessor chain includes PR #133 / A1R, `SCV2-R2`
(`target_met_constraint_aware_r2`), PR #135 / R2R,
`SCV2-ML1: Multilingual Alias and Source-Metadata Closure`, and PR #137 / ML2
(`target_met_multilingual_identity_candidate_closure`, `safe_to_merge=true`).
Provider metadata acquisition ended in ML1; provider and production routes
remain unauthorized.
R2R's 3,319 dispositions and ML2's logical evidence remain immutable. ML2
identity accounting remains
`606 = 12 already materialized + 594 new + 0 cannot-link + 0 deferred` and
`1213 = 1213 must_link + 0 cannot_link + 0 deferred`.
The superseded first ML2 run's `1214` count remains historical evidence only;
the fresh accepted execution truth is `1213`.
Search-result union is not identity union; additional query terms use
media-level AND intersection.

## SV1 Bounded Result

- Read-only inventory found `20,702` candidates and `20,160` safely usable real
  media; deterministic manifest selected exactly `12,000` non-synthetic items.
- Controlled import accounted `12,000 / 12,000`, with zero blocking failure,
  out-of-manifest import, unexplained outcome, or source mutation.
- AI provenance reached `12,000 / 12,000`: `3,420` compatible rows reused and
  `8,580` inferred through the cached local `wd-swinv2-tagger-v3` model; model
  downloads and external calls were zero.
- Stable-key export contains `108,442` logical items with zero development row-ID
  dependencies. Scale import committed `108,182`; `298` missing-target metadata
  references remained explicitly deferred and zero accepted evidence was lost.
- Denominator accounting is exact: filename/path mandatory `6,496`, source
  supplemental target population `3,452`, supplemental-only `1,080`, explicit
  non-candidate `4,424`, unclassified/unexplained `0`.
- Accepted R2R reuse remained `3319 = 1522 must_link + 1791 cannot_link + 6
  deferred_nonblocking`; all `606` ML2 families remained traceable.
- Graph audit found `1,677` active components, largest `88`, `14,068` signals,
  `8,124` aliases, and `2,065` concept-media support rows. All cannot-link,
  cross-role, unknown-role, duplicate-active-identity, deferred-union, and
  multi-stable-ID violation counts are zero.
- The 240-query benchmark returned `471` supported results, zero unsupported
  result and zero AND leakage or search mutation. Scale P50/P95/max was
  `3.9 / 8.602 / 40.645 ms`; the 750 ms P95 gate passed.
- Promotion rehearsal proved rollback restoration, committed `108,182` logical
  items, second-import mutation `0`, cross-database logical mismatch `0`, and
  media/media_tags plus protected/forbidden mutation `0`.
- The first promotion attempt is preserved, not cleaned: it exposed PostgreSQL
  NULL uniqueness behavior for 2,065 media-support rows. The runner now performs
  full logical-key deduplication; the fresh `retry1` database supplied the
  accepted proof.
- Public redaction, negative control, review-pack integrity, predecessor
  immutability, environment isolation, and the executable phase contract passed.

## Next Boundary

SV1 may recommend `SCV2-FL1: Full-Library Dev/Test Replay`, but it does not
approve or start FL1. Production, provider/Pixiv/gallery-dl/external LLM,
Entity bridge / EntityAlias, confirmed assignment, SourceConcept-to-`media_tags` truth,
and source/iCloud mutation remain forbidden. No later phase begins until this
normal PR is reviewed and the project owner makes a separate decision.

## Durable Links

- Canonical roadmap path: `docs/roadmap/current-mainline-roadmap.md`.
- [Current roadmap](roadmap/current-mainline-roadmap.md)
- [Phase contracts](phase-contracts.md)
- [Evidence snapshot reuse](source-evidence-snapshot-reuse-policy.md)
- [SV1 report](reports/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness.md)
- [SV1 public summary](reports/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness-summary.json)
- [ML2 report](reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure.md)
- [Search semantics](source-concept-tag-search-semantics.md)
