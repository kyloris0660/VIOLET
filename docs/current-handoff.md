# Current Handoff - V.I.O.L.E.T.

> PR #137 is merged and accepted in `origin/main` at
> `7fca41151cc9e1d5b48cfe243279e66296346bae`; the accepted ML2 evidence-code
> commit is `00398a0b5b1a46d010e82c2b6f72796dbdb47918`. The separately governed
> `SCV2-SV1: Controlled Scale Replay and Promotion-Readiness Validation` run has
> reached its merge-safe partial SV1-A boundary on the feature branch; its PR remains unmerged.

## Canonical State

| Item | Value |
|------|-------|
| Merged baseline | PR #137 / `7fca41151cc9e1d5b48cfe243279e66296346bae` |
| Current work item | `SCV2-SV1: Controlled Scale Replay and Promotion-Readiness Validation` |
| Accepted R2R DB | `blombooru_scv2_r2r_dryrun_test_20260710` (immutable) |
| Accepted SCV2-ML1: Multilingual Alias and Source-Metadata Closure DB | `blombooru_scv2_ml1_acquisition_test_20260712` (immutable) |
| Accepted ML2 DB | `blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715` (immutable) |
| SV1 scale DB | `blombooru_scv2_sv1_controlled_scale_test_20260718` |
| Successful promotion DB | `blombooru_scv2_sv1_promotion_rehearsal_test_20260718_retry1` |
| Rebuild verification DB | `blombooru_scv2_sv1_rebuild_verification_test_20260718` |
| SV1 contract | `partial_sv1_media_ai_scale_and_stable_key_promotion_complete`; `target_met=false`; `safe_to_merge=true`; `route_approved=false` |

PR #133 / `SCV2-R2` remains `target_met_constraint_aware_r2`; PR #135 / R2R precedes ML2, which remains `target_met_multilingual_identity_candidate_closure`. Provider routes remain forbidden; production routes remain unauthorized.

PR #135 R2R's 3,319 dispositions and ML2's logical evidence remain immutable. ML2
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
- Denominator accounting independently parses filename and stored path:
  canonical Pixiv candidates `6,496`, accepted metadata support `2,372`, not
  acquired in SV1-A `4,124`, explicit non-candidate `5,504`, conflicts and
  unclassified/unexplained `0`. The `298` unavailable accepted media remain explicit.
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
- A fresh rebuild DB imported zero derived SourceConcept rows and replayed the
  accepted R2R/ML2 service paths with `606 / 606` creator-family traceability.
  Forty cases from the actual `8,548` new-media population passed
  scale/promotion/rebuild local-tag search with zero unsupported result or leakage.

## Next Boundary

The immediate recommended phase is `SCV2-SV1B: Controlled Pixiv Metadata,
Localization, and Source-Graph Closure`, but SV1-A does not approve or start
SV1B or FL1. Production, provider metadata acquisition/Pixiv/gallery-dl/external LLM,
Entity bridge / EntityAlias, confirmed assignment, SourceConcept-to-`media_tags` truth,
and source/iCloud mutation remain unauthorized. No later phase begins until this
normal PR is reviewed and the project owner makes a separate decision.

## Durable Links

- Canonical roadmap: `docs/roadmap/current-mainline-roadmap.md` ([link](roadmap/current-mainline-roadmap.md))
- [Phase contracts](phase-contracts.md)
- [Evidence snapshot reuse](source-evidence-snapshot-reuse-policy.md)
- [SV1 report](reports/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness.md)
- [SV1 public summary](reports/phase-4.5-scv2-sv1-controlled-scale-promotion-readiness-summary.json)
- [ML2 report](reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure.md)
- [Search semantics](source-concept-tag-search-semantics.md)
