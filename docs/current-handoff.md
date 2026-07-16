# Current Handoff - V.I.O.L.E.T.

> PR #136 is merged in `origin/main` at
> `f6cae3483f4cf75974746a4cc82222f28e399b96`. SCV2-ML2 has completed its
> isolated SourceConcept-only execution; final PR adjudication remains with the
> project lead.

## Canonical State

| Item | Value |
|------|-------|
| Merged baseline | PR #136 / `f6cae3483f4cf75974746a4cc82222f28e399b96` |
| Current phase | `SCV2-ML2: Multilingual Identity Candidate Closure` |
| Accepted R2R DB | `blombooru_scv2_r2r_dryrun_test_20260710` (immutable) |
| Accepted SCV2-ML1 DB | `blombooru_scv2_ml1_acquisition_test_20260712` (immutable) |
| Fresh review-fix ML2 DB | `blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715` |
| Superseded ML2 DB | `blombooru_scv2_ml2_identity_closure_test_20260714` (preserved, immutable) |
| SourceConcept / needs_review | `1677 / 0` |

Accepted predecessor chain: PR #133 / A1R, `SCV2-R2` (`target_met_constraint_aware_r2`),
PR #135 / R2R, and `SCV2-ML1: Multilingual Alias and Source-Metadata Closure`. Provider
metadata acquisition ended in ML1; production, Entity bridge, and downstream routes
remain unauthorized. See [R2R evidence reuse policy](source-evidence-snapshot-reuse-policy.md).

R2R's 3,319 dispositions and 12,249 signals remain immutable. ML2 made no
provider, Pixiv, gallery-dl, LLM, production, Entity, or `media_tags` truth call
or write. Search-result union is not identity union, and
additional query terms use media-level AND intersection.

The superseded first ML2 execution reported `1214` candidate pairs. That count
is preserved only as historical evidence and is not current execution truth;
the fresh review-fix execution rebuilt the ledger from the new database and
produced the actual `1213` pairs reported below.

## ML2 Final Evidence

ML2 rebuilt creator identity candidates from the accepted ML1 snapshot using
`(provider, stable_creator_id, creator_role)` as the deterministic source-layer
anchor. It retained every non-empty canonical/account/historical name as an
alias signal, used star-topology candidate generation, and never expanded into
all-pairs alias comparisons.

- identity families: `606 = 12 already materialized + 594 new + 0 cannot-link + 0 deferred + 0 fragmented deferred`;
- candidate pairs: `1213 = 1213 must_link + 0 cannot_link + 0 deferred`;
- accepted R2R accounting: `3319 = 1522 must_link + 1791 cannot_link + 6 deferred_nonblocking`, reconstructed cache-only from the immutable database and compatible private evidence with zero reuse conflict;
- active-concept reuse: `0` inactive references/reuses, `310` partial historical references retained only as private diagnostics, and `0` pre-existing active-component fragmentations;
- concept-media membership: `2065 / 2065` exact trusted support rows and distinct expected media, with zero duplicate, missing, unsupported, or `media_count` mismatch;
- SourceConcept-only runtime: `606` families / `1213` alias cases at `1.0` expected-media coverage, zero fallback, zero search-inert concepts, and `25 / 25` media-detail visibility samples passed;
- candidate-generation gaps: `30 before / 0 unexplained after`;
- ML2 touched graph: `606` complete active components, largest `8`; all 12 accepted historical components were audited with their old links, with no multi-stable-ID, direct/transitive cannot-link, contamination, cross-role, unknown-role, or duplicate-active-identity violation;
- creator-context benchmark: `93 / 93` evidence-supported cases succeeded; one
  evidence-absent case is `deferred_nonblocking_evidence_absent`;
- search-only families: `3642 before / 3642 after`, with zero regression,
  unsupported, rejected-only, superseded-only, AND leakage, or search mutation;
- second execution: zero mutations, no duplicate media support, and identical component/disposition fingerprints;
- public redaction and review-pack integrity passed fail-closed publication gates.

Final contract: `target_met_multilingual_identity_candidate_closure`,
`target_met=true`, `safe_to_merge=true`, `route_approved=false`,
`active_blockers=[]`. The accepted ML1/R2R databases remained immutable; only
the fresh review-fix ML2 dev/test clone received allowlisted SourceConcept/source-name
observation writes. The first ML2 database remains preserved as superseded evidence.

## Next Boundary

The evidence recommends project-lead review before any separately governed
Controlled Scale Validation over roughly 10k-15k media. This PR does not start
that phase and does not authorize a provider run, production/full-library run,
PX1-B, Provider-2/PX-REC1, Entity bridge, truth promotion, media import, AI
tagging, classification, localization, or source/iCloud mutation.

The six ML1 hardening milestones remain unchanged:
`PRE-NEXT-PROVIDER-EXECUTION-HARDENING`, `CONTROLLED-SCALE-AUDIT-DEBT`, and
`PRE-NONWAIVED-PROVIDER-CREDENTIAL-HARDENING`. None is exercised by ML2 because
ML2 performs no provider execution.

## Durable Links

Canonical roadmap path: `docs/roadmap/current-mainline-roadmap.md`.

- [Current roadmap](roadmap/current-mainline-roadmap.md)
- [Phase contracts](phase-contracts.md)
- [Search semantics](source-concept-tag-search-semantics.md)
- [Autonomous resolution policy](source-concept-autonomous-resolution-policy.md)
- [ML2 report](reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure.md)
