# Current Mainline Roadmap

Status: PR #135 is merged at `5bbbb8ff13b140ea77a839757603714bfdd87181`.
PR #136 has completed its project-lead-directed SCV2-ML1 closeout on its existing
branch and awaits the project lead's final merge decision.

## Accepted Mainline

1. R1R merged in PR #132.
2. SCV2-A1R merged in PR #133.
3. SCV2-R2 merged in PR #134 at `d553a7f51222f2c52c3fe5014e878faed7f7b5a1`.
4. SCV2-R2R merged in PR #135 at `5bbbb8ff13b140ea77a839757603714bfdd87181`.
   It accepted complete autonomous pair disposition, constraint-safe non-human
   materialization, immutable-evidence proof, and a disabled-by-default source
   evidence fallback. It did not authorize production or full-library work.

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

## Current Closeout

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
replay. Final merge adjudication remains with the project lead.

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

The remaining next-phase quality question is multilingual alias
candidate-generation recall (`30` known gaps) and creator SourceConcept closure,
not Pixiv acquisition replay and not whether one shared bare name returns media
from several correctly separated identities.

## Next Approved Phase

`SCV2-ML2: Multilingual Identity Candidate Closure` is the only approved next
route. Its accepted inputs are `606` identity-eligible families, `3,642`
search-only translation families, runtime equivalence `0.897363`, `30` remaining
candidate-generation gaps, complete creator name/account source preservation,
and creator + character/work intersection below acceptance. SourceConcept remains
source-layer only; no Entity or truth promotion is authorized.

ML2 priorities are:

1. deterministic stable-provider-identity materialization before LLM use;
2. close the 30 signal/candidate-generation gaps;
3. creator historical name/account alias closure;
4. character/work/copyright multilingual family closure;
5. creator + character/work AND-search recall;
6. bounded LLM only for evidence-insufficient ambiguous pairs under the existing
   finite-manifest primary-provider USD-10 policy.

This closeout creates no ML2 branch, implementation, provider run, or production
authorization.

Six reviewer hardening items are deliberately bounded debt, not ML1 fixes:
cross-pass spacing, manifest-scope outcome keys, conflict mismatch persistence,
and terminal/private classifier ordering are
`PRE-NEXT-PROVIDER-EXECUTION-HARDENING`; filename/path denominator treatment is
`CONTROLLED-SCALE-AUDIT-DEBT`; secret-token delimiter scanning is
`PRE-NONWAIVED-PROVIDER-CREDENTIAL-HARDENING`. Current evidence is unaffected:
main/conflict work overlap is 0, provider mismatch and systemic stop are 0,
pending/retryable/missing are 0, the mandatory population is filename/path
anchored, and neither this closeout nor ML2 authorizes provider calls.

## Provider-2 Deferral

`PX-REC1: Archived Source Metadata Recovery` remains deferred. The measured
authenticated terminal-unavailable population is `66 / 1,716` governed
acquisition works (about `3.846%`), not a pure deletion rate and not evidence for
implementing Provider-2 in PR #136. Any future archived-source recovery must
preserve Danbooru provenance and require an exact Pixiv source URL, exact hash,
strong perceptual evidence, or another independently verified correspondence;
it must never represent recovered Danbooru metadata as original Pixiv metadata.

## Stop Boundary

Do not start PX1-B broad acquisition, Provider-2, general scale-up, Entity bridge,
production, full-library execution, truth promotion, media import, AI tagging,
classification, localization, or another phase from ML1. PR #136's authorization
is limited to its exact corrected Pixiv manifest. New multilingual candidate
remediation, creator SourceConcept closure, and broad/full-library work remain a
separately approved next phase.
