# Executable Phase Contracts

V.I.O.L.E.T. phase contracts are machine-readable gates for phase summaries.
They exist because governance, reports, prompts, and review comments cannot be
the only guard for pipeline-critical work.

## Command

```powershell
& "$PY" scripts/check_phase_contract.py --list-contracts
& "$PY" scripts/check_phase_contract.py --contract <contract_id> --summary <summary.json>
& "$PY" scripts/check_phase_contract.py --contract <contract_id> --summary <summary.json> --explain
& "$PY" scripts/check_phase_contract.py --contract scv2_fl1_isolated_full_library_dev_test_contract_v1 --summary <summary.json> --repo-root <trusted-repo> --expected-python "$PY" --runtime-ledger <private-ledger.json> --failure-budget-scenarios <private-failure-bundle.json> --reconciliation-scenarios <private-reconciliation-bundle.json>
& "$PY" scripts/check_documentation_state.py --check
& "$PY" scripts/check_documentation_state.py --check --implementation-evidence <trusted-squash-implementation-evidence.json>  # required only after squash removes PR ancestry
```

The checker prints JSON to stdout and exits nonzero on failure.

## Contract Rule

Any phase that claims `target_met`, `route_approved`, `full_chain_completed`,
or `safe_to_merge` must declare a registered contract and pass the contract
checker before making that claim. If no matching contract exists, create or
extend a contract first.

## Mandatory Manual Acceptance Rule

User manual acceptance is required after every two substantive behavioral/data
phases and before any full-library or production route. Media import, AI
tagging, provider acquisition, graph/resolver behavior, search behavior,
localization, runtime/UI behavior, and production workflows are substantive.
Docs-only, report-only, contract-only, and behavior-neutral repair phases do not
increment or reset the counter.

Acceptance is valid only for its exact Git HEAD, database identity, media
manifest fingerprint, acquired metadata package fingerprint, graph/search
fingerprints, and acceptance-case manifest fingerprint. Later runtime, data,
search, graph, or localization changes invalidate affected acceptance cases.
When acceptance is required and pending, the executable contract must enforce
`manual_acceptance_required=true`, `manual_acceptance_status=pending_user`,
`target_met=false`, `safe_to_merge=false`, and `route_approved=false`.

For SCV2-FL1-P1-R1, acceptance is additionally invalid unless it binds the
immutable implementation commit/tree/digest and the final reviewed
commit/tree. Any executable code, test, or contract drift after the
implementation boundary invalidates it. Only the exact governance-path
allowlist enforced by the executable contract may follow that boundary.
Implementation authorization, owner audit, owner acceptance, merge
authorization, and next-phase route authorization are separate evidence gates.

The FL1-P1 evidence checker has two strict Git modes. In `pr_audit`, the
reviewed final commit must be the repository's current HEAD, the implementation
commit must be its ancestor, and Git-derived post-implementation paths must all
be on the governance allowlist. In `squash_carry_forward`, the current squash
commit must have exactly the approved base as its parent and its tree must equal
the owner-reviewed final PR tree; branch-commit ancestry is intentionally not
required after squash. The documentation checker uses the same trusted
`ImplementationEvidence` repository proof and has no topology-only fallback.
The current contract has no automated positive owner/merge/route authority:
caller-supplied JSON always fails closed, while a human GitHub decision remains
outside the automated contract.

## Current Phase Boundary

<!-- CURRENT_PHASE: SCV2-FL1-I2 -->

PR #144 was owner-accepted and merge-commit merged at exact main
`8955b95e91630d4c5e18e1e2ca252b19754c81d5`. Its final HEAD/tree are
`2f8d5f8ce6cde9759c530de71d4ddd1893481656` /
`8930a21bdbac037702f92bcb75bd9b8a3632a073`; the frozen implementation
evidence/tree are `6992e7f1e5a45857111d15da1ad0274e49008a99` /
`6ff185defb150c3751c7433ef635c00a200c44bf`. The acceptance scope is only a
synthetic and newly created temporary-fixture foundation.

The documentation checker pins all four identities through a fixed trusted Git
executable and explicit repository root after case-insensitively scrubbing
inherited `GIT_*` controls, disabling replace objects, hooks, fsmonitor, and
caller-injected configuration. It rejects a current state that asserts
`machine_verifiable_ci=true`, nonzero `github_checks`, or CI authority. PR #144
exposed zero checks, so it has no machine-verifiable CI PASS claim. Its direct
owner decision cannot be synthesized by a contract.

Current synthetic implementation projection:

- `status=fl1_i2_synthetic_implementation_in_progress`
- `planning_authorized=true`
- `planning_completed=true`
- `planning_approved=true`
- `approved_planning_head=acb12c1db258fdef1d4f063b053d422e0d887abf`
- `approved_planning_tree=fc573c7646ad5edf10c32c7712de7f27ab058a2a`
- `merge_authorized=false`
- `implementation_authorized=true`
- `implementation_started=true`
- `target_met=false`
- `safe_to_merge=false`
- `route_approved=false`
- `real_inventory_started=false`
- `real_source_inventory_authorized=false`
- blocker: `pending_fl1_i2_synthetic_implementation_and_contract_closure`

Terminal review `4897012517` at the exact final HEAD produced 17 historical
findings (13 P1, 4 P2). The complete use-before projection is:

| # | Finding | Contract projection |
|---:|---|---|
| 1 | Scrub Git control variables before trusted invocations | Must close during I2 before I2 completion/merge or I3 across runtime, receipt, and contract paths. The checker-only Git scrub does not close that overall delivery. |
| 2 | Validate the parent-observed child identity | Claim boundary: local provenance only, not tamper-resistant or OS/kernel/TPM/remote/CI attestation. |
| 3 | Recheck recall attributes before final resolution | Must close during I2 before I2 completion/merge or I3: final open/no-recall decision binds the same object and refreshed Cloud attributes. |
| 4 | Allow interrupted attempts before corrupt-media closure | Must close during I2 before I2 completion/merge or I3: interruption and corrupt-media accounting remain distinct and reconcile exactly. |
| 5 | Enforce the deadline around blocking file operations | Must close during I2 before I2 completion/merge or I3: open/read/hash/structure work runs in a parent-terminable worker. |
| 6 | Bind the receipt to one unchanged HEAD | Must close during I2 before I2 completion/merge or I3: repository HEAD is identical before and after validation. |
| 7 | Re-derive the adapter policy during contract validation | Must close during I2 before I2 completion/merge or I3: policy derives from trusted configuration, not caller claims. |
| 8 | Stop at the configured failure maximum | Must close during I2 before I2 completion/merge or I3: maximum failure is fail-closed with no off-by-one attempt. |
| 9 | Pin the frozen remediation commit and tree | Closed by the current documentation-governance checker. |
| 10 | Reject CI authority in documentation state | Closed by the current documentation-governance checker. |
| 11 | Include a change identity in file signatures | Must close during I2 before I2 completion/merge or I3: Windows file identity plus change identity. |
| 12 | Reject hard-linked files that alias protected data | Must close during I2 before I2 completion/merge or I3: explicit hard-link/reparse/symlink/path-alias policy. |
| 13 | Confine private artifact reads as well as writes | Must close during I2 before I2 completion/merge or I3: no-follow confined reads and writes. |
| 14 | Enumerate directories through a verified no-follow handle | Must close during I2 before I2 completion/merge or I3: enumerate members from the same verified, no-follow, identity-bound directory handle. Identity-before/after is supplemental drift evidence only; path-based `os.scandir()` plus post-check is insufficient. Windows must implement a safe same-handle primitive or fail closed. |
| 15 | Reconcile intents from ended failed invocations | Must close during I2 before I2 completion/merge or I3: residual INTENT closure after failed process termination. |
| 16 | Validate media structure beyond boundary markers | Must close during I2 before I2 completion/merge or I3: bounded structural parsing beyond boundary markers. |
| 17 | Handle runtime-context failures in scanner CLI | Must close during I2 before I2 completion/merge or I3: stable privacy-safe JSON error envelope. |

The I1 contract `scv2_fl1_i1_read_only_inventory_contract_v1` remains accepted
only for its narrow synthetic foundation. I2 currently has no completed or
registered executable contract. The separately authorized synthetic I2
implementation must converge canonical Cloud/file-identity primitives,
`SourceIngestionGate` policy, operation-gateway evidence, and CLI/runtime
consumers; close all 14 gates with synthetic/adversarial temporary fixtures;
and register a contract before any readiness claim. These 14 gates are
classified
`must_close_during_i2_before_i2_completion_merge_or_i3`: exact-plan owner
approval and separate implementation authorization come first, and the gates
then close during synthetic-only I2 before `implementation_completed`,
`target_met`, `safe_to_merge`, merge, I3, or any real-source operation.

The future contract must preserve these gates:

- `REAL_OPERATION_GATEWAY_GATE`: no I3 or real listing/stat/attribute/read/hash
  until complete exact source scope and protected roots are separately
  authorized; that I3 scope is not a condition for starting authorized
  synthetic-only I2 implementation.
- `VALIDATION_RECEIPT_GATE`: local evidence remains
  `trust_level=local_operator_receipt` and `machine_verifiable_ci=false`.
- `OWNER_AUTHORITY_GATE`: direct owner approval remains external to automation.
- `POSIX_LEDGER_DURABILITY_GATE`: no unsupported host power-loss durability
  claim.
- `STABLE_REPLAY_GATE`: no replay evidence is consumed or authorized.

The fixed order is plan owner approval, separate I2 implementation
authorization, synthetic-only closure of all 14 gates, I2 owner audit and
merge, separate `FL1_I3_REAL_SOURCE_SCOPE_GATE`, then bounded canary. I3
requires an exact private source identity, protected-root registry, budgets,
Cloud/no-hydration policy, and stop conditions.

The owner accepted exact planning HEAD/tree
`acb12c1db258fdef1d4f063b053d422e0d887abf` /
`fc573c7646ad5edf10c32c7712de7f27ab058a2a` under review `4907783329`.
P1 thread `PRRT_kwDOSTBMB86YRuq7` is closed in the owner-acceptance projection
contract: trusted Git re-derives the accepted tree, proves that the accepted
commit is an ancestor of either the projection HEAD or its later merge commit,
and rejects every post-plan path outside the explicit governance allowlist.
This binding cannot be replaced by caller JSON, environment variables, or a
CLI authority flag. PR #145 merged at
`1913bd27517efc1a6007a202fc9650de4f20fab4`; G0 closes its five accepted
post-merge governance-entry findings. Synthetic implementation authority is
true, while real-source, safe-to-merge, and merge authority remain false.
I4 full inventory, E1 isolated import, E2 local classification/tagging, and V1
product validation are later independent routes. No current contract authorizes
real source/iCloud access, DB/app-storage, import, classification/tagging,
provider/LLM/media or external data-plane network, Stable Replay, UI/server, or
production execution. Authorized Git/GitHub governance control-plane operations
are allowed and have occurred; they are not provider/data execution.

## Registered GOV3 Contracts

- `python_env_contract_v1`
- `postgres_db_contract_v1`
- `media_import_contract_v1`
- `classification_contract_v1`
- `ai_tagging_contract_v1`
- `localization_contract_v1`
- `source_metadata_contract_v1`
- `source_concept_full_chain_contract_v1`
- `r1r_full_source_concept_pipeline_contract_v1`
- `r2_source_concept_graph_remediation_contract_v1`
- `r2r_autonomous_recall_search_closure_contract_v1`
- `ml1_multilingual_alias_source_metadata_closure_contract_v1`
- `ml2_multilingual_identity_candidate_closure_contract_v1`
- `sv1_controlled_scale_promotion_readiness_contract_v1`
- `sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1`
- `sv1b_owner_acceptance_closeout_contract_v1`
- `scv2_fl1_isolated_full_library_dev_test_contract_v1`
- `review_pack_contract_v1`
- `route_audit_contract_v1`
- `public_redaction_contract_v1`
- `mutation_safety_contract_v1`
- `artifact_lifecycle_contract_v1`
- `destructive_operation_contract_v1`
- `entity_truth_bridge_contract_v1`
- `dynamic_library_sync_contract_v1`
- `phase47_s2_baseline_contract_v1`
- `production_development_separation_contract_v1`
- `prod_launcher_mvp_contract_v1`
- `s2g1x_probe_contract_v1`
- `s2g_s3a_f1_foundation_contract_v1`
- `s2g_real1_bounded_ai_tagging_validation_contract_v1`
- `s3a_pilot1_new_data_directml_chain_contract_v1`
- `s3a_prod1_operator_incremental_sync_contract_v1`
- `s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1`

## R1R Gate

`source_concept_full_chain_contract_v1` is mandatory before any R1R completion
claim. It distinguishes deterministic-only output from full-chain completion
and fails if a phase silently skips required bounded LLM pair adjudication while
claiming full-chain completion.

`r1r_full_source_concept_pipeline_contract_v1` is the focused SCV2-R1R replay
contract. It requires development/test/restored-snapshot isolation, a
stage-level SC1 required-stage manifest, LLM pair planning/selection/judgment
truthfulness, SourceConcept-owned write scope, mutation proof, review-pack
manifest inclusion, public redaction, and explicit downstream route
non-authorization. `target_met_full_chain` also requires primary
OpenAI-compatible provider/model identity, no fallback provider use, and zero
provider/judgment errors. It also requires the standard SourceConcept LLM
adjudication cache policy: a private ignored durable cache root label, atomic
cache-write proof, exact-compatible cache accounting, provider-failure
exclusion, projected/actual cost fields, and redacted public aggregate cache
reporting. A fixed call cap such as 300 pairs is not sufficient route evidence
when all eligible pairs fit within the approved budget. It allows truthful
blocked statuses, but only `target_met_full_chain` may claim full-chain
completion.

See `docs/source-concept-llm-adjudication-cache.md` for the durable cache and
checkpoint/reuse standard shared by R1R and future full-library SourceConcept
phases.

## R2 Constraint-Aware Graph Gate

`r2_source_concept_graph_remediation_contract_v1` is the focused SCV2-R2 gate.
It requires an immutable fixed-input manifest with content fingerprints, a
separate dev/test working DB, SourceConcept-only output writes, complete
accounting for all 6,429 R1R judgments, and an explicit approval boundary for
genuinely new pairs. Initial R2 must not initialize or call an LLM provider for
those pairs.

Target completion requires zero review-only unions, zero applicable direct or
transitive cannot violations, compatible known-same preservation with a private
reason ledger for intentional splits, recomputed baseline/post metrics, public
redaction, and an integrity-checked private review pack. It forbids provider,
gallery-dl, AI/import, upstream evidence, production, Entity, and truth-path
work and cannot authorize a downstream phase.

The target gate also requires every production-isolation and downstream
authorization flag to be present as an exact boolean, an explicit integer-zero
forbidden truth-table persistence delta, empty forbidden/unexpected table
lists, and `truncate_drop_reset_used=false`. Missing false-valued proofs do not
count as evidence. User-controlled run IDs are bounded to a safe filename
format, and exact final Markdown/JSON redaction failure blocks public writes.

R2 quality is multidimensional: `constraint_safety_target_met=true` may coexist
with `search_quality_improved=false`, `gap_quality_improved=false`,
`recall_closure_complete=false`, and `route_quality_ready_for_scale=false`.
Those acknowledged route debts do not invalidate the narrow constraint target,
but they require R2R recall/search closure under a separate future approval.

See `docs/source-evidence-snapshot-reuse-policy.md` for the acquisition versus
rebuild boundary shared by R2 and future full-library work.

## R2R Autonomous Recall/Search Gate

`r2r_autonomous_recall_search_closure_contract_v1` is the focused SCV2-R2R
gate. It requires immutable R1R/R2 evidence, a separate dev/test working DB,
cache-first pair planning, complete machine-disposition accounting, an
autonomous first/second-pass ladder, non-materialized deferred evidence,
constraint-safe rebuild, dual-path source search, an expanded reproducible
benchmark, cache-only final regeneration, public redaction, and an
integrity-checked private review pack.

Normal completion cannot depend on a human queue. Target status requires:

```text
total_candidate_pairs
= must_link_count + cannot_link_count + deferred_nonblocking_count
```

It also requires `manual_review_required_count=0`,
`operator_blocking_review_count=0`, `manual_review_queue_generated=false`,
candidate disposition coverage `1.0`, and zero materialized SourceConcept
`needs_review` rows. Deferred relations preserve evidence but never union.

The initial cache-only run must use `blocked_llm_approval_required` whenever
compatible cache coverage does not account for all eligible pairs. That status
requires zero provider initialization/calls and cannot claim target completion.
After separate approval, provider errors stay unaccounted until retried; they
never count as successful judgments. Final target evidence must regenerate
cache-only with zero provider calls.

The contract also fails on fixed-evidence mutation, production profile/DB use,
truth writes, acquisition/import/AI/localization calls, review/deferred identity
union, cannot-link or unknown-role identity regression, fallback-provider use,
missing checkpoints, public redaction failure, or downstream authorization.

R2R's historical `false_broad_union_indicator_count` and
`cannot_linked_search_contamination_count` were measured under an overly
restrictive one-name/one-family interpretation. Their numeric values remain
preserved diagnostics, but they are not generic product-search failure gates.
The corrected rule is defined in
`docs/source-concept-tag-search-semantics.md`: shared supported bare-name results
are valid, while unsupported/rejected results, AND leakage, and identity mutation
are failures.

For the honest `partial_autonomous_closure` foundation, the contract also
requires zero-provider closeout proof, complete overlay lifecycle proof, the
fallback-index table in the R2R mutation set, deterministic double-rebuild
fingerprints, persisted-runtime benchmark equivalence, and
`experimental_fallback_enabled_by_default=false`. Historical missing usage is
retained honestly and does not become fabricated actual cost.

See `docs/source-concept-autonomous-resolution-policy.md` for the durable
no-human-review and evidence-fallback policy.

## ML1 Multilingual Alias And Source-Metadata Gate

`ml1_multilingual_alias_source_metadata_closure_contract_v1` is the focused
SCV2-ML1 gate. Audit execution is read-only over the immutable accepted R2R
evidence database. It requires corrected durable search
semantics, complete canonical Pixiv filename-candidate accounting at media/page
and distinct-work levels, creator-field retention, a real fixed-evidence
multilingual-family benchmark, candidate-generation recall accounting, actual
runtime AND-search proof, public redaction, and an integrity-checked review pack.

The contract does not require one bare name to resolve to one identity and does
not use the historical R2R broad-union/cannot-contamination counts as target
gates. It fails on unsupported or rejected results, AND leakage, search-caused
identity mutation, silently lost aliases/creator fields, unexplained candidate
misses, human-review dependency, fixed/forbidden evidence mutation, unauthorized
provider/LLM/gallery-dl calls outside the exact authorization, redaction gaps,
or downstream authorization.

Semantic completeness, universal recall, perfect creator/character/work
intersection, and universal multilingual identity materialization are not ML1
gates. Under-recall and candidate-generation gaps remain explicit evidence for
ML2. ML1 does require every runtime result to have direct or accepted support,
zero rejected-only or superseded-only results, media-level AND semantics, and
zero search-caused identity mutation.

PR #136 executed only the exact deduplicated current-stock Pixiv metadata
manifests under the project-owner `operator_accepted_local_credential_risk_v1`
waiver. Execution was metadata-only, used at least two-second request spacing,
bounded retry/backoff, per-work checkpoints, one isolated ML1 dev/test database,
and no fallback provider or media download. The final closeout retains the
historical `1,817` calls but requires a zero external-call delta. The accepted
R2R database remains immutable.

New imports use the canonical `phase44p0_pixiv_filename_prior_v1` parser and a
durable source-metadata queue record. Normal complete/terminal closure remains
the preferred rule. The separately governed
`deferred_nonblocking_source_page_mismatch` state is closed but neither complete
nor terminal: it requires positive exact-work/page attempt evidence, preserved
raw history, no unsupported page link or conflict winner, and explicit
`source_page_mismatch_deferred_nonblocking_v1` policy evidence. Generic retry or
resume cannot reopen it. The final PR #136 contract may therefore prove the
exhaustive equation `candidate = complete + terminal + deferred`, zero open or
blocking-conflict works, status
`partial_ml1_pixiv_metadata_foundation_complete`, with its historical target,
merge-safety, route, and blocker checks satisfied. That historical route
approval was limited to separately governed SCV2-ML2 work and did not authorize
production, scale, another provider, Entity/truth writes, or acquisition replay.

The final gate additionally requires page-local disposition and trusted creator
lineage: `deferred_returned_page_row_count_after=0` and
`untrusted_parent_query_visible_creator_observation_count=0`. A returned exact
page must be complete even when another page for the work is absent. Creator
backfill, creator-field audit, trusted mismatch precedence, and runtime mismatch
detection must use the same canonical complete Pixiv-parent predicate. The
creator audit must inspect `creator_account` before compatible historical fields
and report non-overlapping work, media/page, metadata-record, payload-queue,
terminal-evidence, and deferred-row metrics.

Deferred execution hardening is gated by named future milestones:
`PRE-NEXT-PROVIDER-EXECUTION-HARDENING` for cross-pass spacing, manifest-scope
outcome keys, conflict mismatch persistence, and terminal/private classifier
ordering; `CONTROLLED-SCALE-AUDIT-DEBT` for denominator treatment; and
`PRE-NONWAIVED-PROVIDER-CREDENTIAL-HARDENING` for secret-token delimiter
scanning. None authorizes work in ML1 or ML2.

The default primary-provider LLM policy pre-authorizes only one finite,
reproducible, cache-first execution whose projected aggregate cost including
retries is at most USD 10.00, with no fallback, image upload, production/truth
write, or semantic scope expansion. No ML1 LLM call is expected while candidate
remediation is deferred.

## ML2 Multilingual Identity Candidate Closure Gate

`ml2_multilingual_identity_candidate_closure_contract_v1` is the focused
SCV2-ML2 gate. It requires a fresh isolated ML2 dev/test clone, immutable
accepted ML1/R2R inputs, exact creator-family and alias-observation manifests,
stable `(provider, stable_creator_id, creator_role)` anchors, and complete
candidate-pair and family accounting. Candidate growth must remain linear by
using one stable anchor per family rather than all-pairs alias expansion.

Target completion requires every identity-eligible creator family to have
exactly one terminal outcome and every candidate pair to be `must_link`,
`cannot_link`, or `deferred_nonblocking`. A family with multiple active
pre-existing components must be safely deferred as
`deferred_nonblocking_existing_component_fragmentation`; the runner may not
create a third component or silently merge accepted history. All ML1
candidate-generation gaps must be explained and closed. Existing concepts may
be reused only when active and after auditing the complete historical component.
The contract requires zero inactive reuse and zero duplicate active identity
concepts among materialized families.

R2R reuse proof is exact and fail-closed: the immutable database snapshot and a
compatible private manifest, when present, must agree on
`3319 = 1522 must_link + 1791 cannot_link + 6 deferred_nonblocking`, the
snapshot fingerprint, actual reused-pair count, conflict count, and immutable
accepted dispositions. Missing or disagreeing proof activates
`blocked_ml2_r2r_reuse_evidence`; no fixed-count fallback is permitted.

Every touched active concept is audited with all active/materialized historical
links and signals, not only the current run. The graph audit consumes the exact
final-ledger cannot-link pairs and distinguishes direct disposition conflicts,
cannot endpoints in one component, and transitive violations. Both
`full_touched_component_audit_passed` and
`existing_12_full_component_audit_passed` are required. Empty canonical alias
keys cannot become candidates, collision keys, aliases, signals, or search rows.

Runtime membership requires exact trusted concept-media evidence, one active
row per distinct concept/media/source-metadata-record tuple, linear in media
rather than alias-by-media. `SourceConcept.media_count` must equal the distinct
supported media set. The SourceConcept-only audit disables direct source-name
and tag fallback, requires all materialized families and non-empty aliases to
retrieve exactly their trusted media, rejects search-inert concepts and
missing/unsupported media, and samples media-detail SourceConcept visibility.
The second execution must be mutation-free and create no duplicate support.

Search completion is evidence-conditioned rather than denominator-tuned. Every
creator-context case with sufficient trusted evidence must succeed through the
real runtime path; evidence-absent cases may only be explicitly
`deferred_nonblocking_evidence_absent`. Search-only families must remain
unchanged, and unsupported, rejected-only, superseded-only, AND-leaking, or
search-caused identity mutations fail the contract.

The ML2 write allowlist is limited to source-name observations and
SourceConcept-owned concepts, signals, links, aliases, evidence, search-index,
and run-ledger rows in the isolated clone. Provider/Pixiv/gallery-dl calls,
production writes, Entity or `media_tags` truth writes, import, AI tagging,
classification, localization, source/iCloud mutation, and downstream route
authorization are forbidden. LLM adjudication is allowed only for a finite
evidence-insufficient manifest under the existing USD-10 policy; a zero-item
manifest must initialize no provider and make zero calls.

The target claim additionally requires fixed/forbidden table fingerprints,
actual argument-safe Git synchronization evidence, preservation of every
pre-edit user-owned path, exact JSON parsing, and independent contract blocker
derivation. Public publication is fail-closed: the complete proposed public
payload must pass redaction before any public report, summary, handoff, roadmap,
or review pack is written. A failure activates
`blocked_ml2_public_redaction` and permits only a private safe-code diagnostic.
The contract also derives `blocked_ml2_runtime_media_binding`,
`blocked_ml2_existing_component_fragmentation`,
`blocked_ml2_environment_isolation`, and `blocked_ml2_graph_safety` from their
independent evidence. Its terminal status is
`target_met_multilingual_identity_candidate_closure` with `target_met=true`,
`safe_to_merge=true`, `route_approved=false`, and no active blockers.

## Route Gate

`route_audit_contract_v1` is mandatory for route-decision phases. A route cannot
be approved while an upstream pipeline contract is failed, deterministic-only,
or incomplete. For SCV2-A1R summaries, it also enforces the explicit A1R status
vocabulary, at most one recommended next phase, explicit downstream/truth/
production non-authorization flags, public redaction, review-pack integrity, and
the required next contract when a single next phase is recommended.

## Public Artifact Gate

`public_redaction_contract_v1` and `review_pack_contract_v1` make public
artifact and review-pack checks executable. They scan both keys and values, and
they fail on local paths, tokens, unredacted secret-looking fields, raw private
labels, stale or incomplete review-pack manifests, and final-file-set redaction
gaps.
