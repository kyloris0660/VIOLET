# Current Mainline Roadmap

Status: PR #136 is merged in `origin/main` at
`f6cae3483f4cf75974746a4cc82222f28e399b96`. SCV2-ML2 has completed its
separately governed isolated execution and awaits project-lead PR adjudication.

## Accepted Mainline

1. R1R merged in PR #132.
2. SCV2-A1R merged in PR #133.
3. SCV2-R2 merged in PR #134 at `d553a7f51222f2c52c3fe5014e878faed7f7b5a1`.
4. SCV2-R2R merged in PR #135 at `5bbbb8ff13b140ea77a839757603714bfdd87181`.
   It accepted complete autonomous pair disposition, constraint-safe non-human
   materialization, immutable-evidence proof, and a disabled-by-default source
   evidence fallback. It did not authorize production or full-library work.
5. SCV2-ML1 merged in PR #136 at
   `f6cae3483f4cf75974746a4cc82222f28e399b96`. Its accepted isolated database is
   an immutable input to ML2; ML2 did not replay acquisition.

## Corrected Route Semantics

The former `SCV2-SR1: Context-Aware Disambiguated Source Search` recommendation
is superseded. It incorrectly treated a legitimate shared bare-name result union
as generic contamination.

- Identity union and search-result union are separate.
- `cannot_link` blocks identity union and unsupported alias propagation, not direct
  retrieval of independently supported same-name media.
- Bare names return the union of legitimate matches across concepts/roles/works.
- Additional terms use media-level AND intersection for disambiguation.
- R2R broad-union/cannot-contamination counts remain historical diagnostics and
  are not product-failure gates under the corrected policy.

## Accepted ML1 Foundation

`SCV2-ML1: Multilingual Alias and Source-Metadata Closure`

PR #136 executed the exact `1,713`-work main and 3-work conflict metadata-only
manifests in the isolated ML1 database under the project-owner local-risk waiver.
Historical execution totaled `1,817` attributable Pixiv/gallery-dl metadata
requests, with no media download or import. The final zero-network closeout
accounts `2,285` candidate media/pages and `2,235` distinct works as `2,201`
complete media / `2,155` complete works, `66` authenticated terminal, and `18`
deferred media rows / `14` works in
`deferred_nonblocking_source_page_mismatch`; there are no pending, retryable,
missing, normalization-failed, provider-mismatch, or unresolved blocking-conflict
works.

The final page-local audit corrected 5 of the formerly deferred 23 queue rows
because their exact provider page was already present; only the 18 absent-page
rows remain deferred. The trusted-lineage audit superseded 26 affected
query-visible creator-name observations with only an untrusted Pixiv parent,
preserved manual/static and independently trusted evidence, and finished with 0
untrusted-parent creator observations. The actual persisted `creator_account`
audit reports `2,108` raw, normalized, and trusted query-visible values with 0
silent drops.

The final contract status is
`partial_ml1_pixiv_metadata_foundation_complete`, with `target_met=false`,
`safe_to_merge=true`, `route_approved=true`, and `active_blockers=[]`. Route
approval is only for a separately governed `SCV2-ML2` phase; it does not authorize
production, scale, Provider-2, Entity/truth promotion, or another acquisition
replay. PR #136 is merged and its accepted evidence is now immutable input.

ML1 owns:

- canonical Pixiv filename-candidate completeness accounting over actual data;
- existing Pixiv/gallery-dl creator metadata preservation and source-layer search;
- a real fixed-evidence multilingual alias benchmark;
- candidate-generation recall and representative-edge/fresh-schema debt;
- actual application-runtime shared-name union and AND-intersection validation;
- continuous canonical Pixiv-on-import queue and batch-closure enforcement;
- exact current-stock Pixiv outcome governance without inventing unsupported page
  links or conflict winners;
- production evidence promotion policy and a bounded USD-10 LLM policy.

PR #136 is merged. Its ML1 database and accepted R2R evidence are immutable ML2
inputs; identity closure does not authorize acquisition replay.

## Current ML2 Closeout

`SCV2-ML2: Multilingual Identity Candidate Closure` consumed `606`
identity-eligible families, `3,642` search-only translation families, and `30`
candidate-generation gaps from the immutable ML1 snapshot. It materialized the
creator families in the fresh review-fix clone
`blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715` through
deterministic stable-provider anchors, without provider acquisition or
Entity/truth promotion. The first ML2 database remains immutable, preserved,
and superseded rather than patched in place.

ML2 completed:

1. `606 = 12 already materialized + 594 new + 0 cannot-link + 0 deferred + 0 fragmented deferred`
   creator identity families;
2. `1213 = 1213 must_link + 0 cannot_link + 0 deferred` candidate pairs under
   linear star-topology generation;
3. exact immutable R2R reuse evidence passed as
   `3319 = 1522 must_link + 1791 cannot_link + 6 deferred_nonblocking`, with
   zero reused-pair conflicts and no accepted-disposition mutation;
4. active-only concept lookup found zero inactive reuse and zero fragmented
   active families; 310 partial historical references were diagnostics, not reuse;
5. all 12 existing concepts and every touched active component passed full
   historical-link purity and graph-safety audit;
6. `2065 / 2065` exact concept-media support rows bind every materialized
   identity to its trusted distinct media set without alias-by-media expansion;
7. SourceConcept-only retrieval passed for `606` families and `1213` aliases at
   `1.0` expected-media coverage, with zero inert, missing, or unsupported media;
8. all 30 candidate-generation gaps closed with zero unexplained remainder;
9. creator historical display/account aliases retained under stable provider IDs;
10. `93 / 93` evidence-supported creator + character/work runtime cases passed,
   with one evidence-absent case explicitly deferred nonblocking;
11. 3,642 search-only families preserved with zero regression or AND leakage;
12. public redaction, contract, idempotency, fixed/forbidden comparison, and
    review-pack integrity passed; provider, Pixiv, gallery-dl, and LLM calls were zero.

The executable contract reports
`target_met_multilingual_identity_candidate_closure`, `target_met=true`,
`safe_to_merge=true`, `route_approved=false`, and `active_blockers=[]`. This
closeout creates no production/full-library/provider authorization and starts no
next phase.

Six reviewer hardening items are deliberately bounded debt, not ML1 fixes:
cross-pass spacing, manifest-scope outcome keys, conflict mismatch persistence,
and terminal/private classifier ordering are
`PRE-NEXT-PROVIDER-EXECUTION-HARDENING`; filename/path denominator treatment is
`CONTROLLED-SCALE-AUDIT-DEBT`; secret-token delimiter scanning is
`PRE-NONWAIVED-PROVIDER-CREDENTIAL-HARDENING`. Current evidence is unaffected:
main/conflict work overlap is 0, provider mismatch and systemic stop are 0,
pending/retryable/missing are 0, the mandatory population is filename/path
anchored. ML2 made no provider calls, so the provider-execution gates remain
deferred.

## Provider-2 Deferral

`PX-REC1: Archived Source Metadata Recovery` remains deferred. The measured
authenticated terminal-unavailable population is `66 / 1,716` governed
acquisition works (about `3.846%`), not a pure deletion rate and not evidence for
implementing Provider-2 in PR #136. Any future archived-source recovery must
preserve Danbooru provenance and require an exact Pixiv source URL, exact hash,
strong perceptual evidence, or another independently verified correspondence;
it must never represent recovered Danbooru metadata as original Pixiv metadata.

## Stop Boundary

Do not start PX1-B broad acquisition, Provider-2, Controlled Scale Validation,
general scale-up, Entity bridge, production, full-library execution, truth
promotion, media import, AI tagging, classification, localization, or another
phase from ML2. The recommended 10k-15k Controlled Scale Validation remains a
project-lead decision and requires a separate approved phase.
