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
| Isolated ML2 DB | `blombooru_scv2_ml2_identity_closure_test_20260714` |
| SourceConcept / needs_review | `1677 / 0` |

R2R's 3,319 dispositions and 12,249 signals remain immutable. ML2 made no
provider, Pixiv, gallery-dl, LLM, production, Entity, or `media_tags` truth call
or write. Search-result union is not identity union, and
additional query terms use media-level AND intersection.

## ML2 Final Evidence

ML2 rebuilt creator identity candidates from the accepted ML1 snapshot using
`(provider, stable_creator_id, creator_role)` as the deterministic source-layer
anchor. It retained every non-empty canonical/account/historical name as an
alias signal, used star-topology candidate generation, and never expanded into
all-pairs alias comparisons.

- identity families: `606 = 12 already materialized + 594 new + 0 cannot-link + 0 deferred`;
- candidate pairs: `1214 = 1214 must_link + 0 cannot_link + 0 deferred`;
- candidate-generation gaps: `30 before / 0 unexplained after`;
- ML2 graph: `606` components, largest `4`, no multi-stable-ID, direct/transitive
  cannot-link, cross-role, unknown-role, or giant-component violation;
- creator-context benchmark: `93 / 93` evidence-supported cases succeeded; one
  evidence-absent case is `deferred_nonblocking_evidence_absent`;
- search-only families: `3642 before / 3642 after`, with zero regression,
  unsupported, rejected-only, superseded-only, AND leakage, or search mutation;
- second execution: zero mutations and identical component/disposition fingerprints.

Final contract: `target_met_multilingual_identity_candidate_closure`,
`target_met=true`, `safe_to_merge=true`, `route_approved=false`,
`active_blockers=[]`. The accepted ML1/R2R databases remained immutable; only
the separate ML2 dev/test clone received allowlisted SourceConcept/source-name
observation writes.

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

- [Current roadmap](roadmap/current-mainline-roadmap.md)
- [Phase contracts](phase-contracts.md)
- [Search semantics](source-concept-tag-search-semantics.md)
- [Autonomous resolution policy](source-concept-autonomous-resolution-policy.md)
- [ML2 report](reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure.md)
