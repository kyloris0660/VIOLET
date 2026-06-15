# Phase 4.5-GOV3: Executable Pipeline Contracts and Phase Gates

## Summary

GOV3 adds a reusable executable phase-contract framework for V.I.O.L.E.T.
Future phases can no longer rely only on docs, prompt memory, or reviewer
attention when claiming `target_met`, `route_approved`,
`full_chain_completed`, or `safe_to_merge`.

The implementation adds:

- a registered contract model under `scripts/phase_contracts/`;
- a command-line checker at `scripts/check_phase_contract.py`;
- focused fail-closed tests in `tests/test_phase_contracts.py`;
- mock SourceConcept full-chain summaries for passing and failing checks;
- durable guidance updates in `AGENTS.md`, `docs/current-handoff.md`,
  `docs/project-roadmap.md`, and `docs/test-workflow.md`.

PR #108 follow-up tightened the contracts to fail closed for current-head
blockers: SourceConcept full-chain LLM opt-out, LLM call caps, validation-pack
proof, route-audit approval contradictions, mutation proof failures, public
bare filenames/URLs/tokens/paths, nested DB secrets, and public artifact
redaction evidence. The final bounded fix also requires valid zero-eligible
proof before skipping LLM evidence, checks `llm_judgment_count` against
`max_calls`, rejects deterministic-only upstream route approvals, requires
positive mutation proof, propagates private provenance context to nested JSON
values, requires public-report-copy proof in review packs, and blocks claimed
completion/approval summaries that do not declare the checked contract id.

GOV3 does not run R1R, A1R, R2, providers, LLM, import, classification, AI
tagging, localization, Entity bridge, DB writes, or storage/source mutation.

## Incident background

INC1 confirmed a pipeline fidelity incident:

- SC1 established and ran bounded LLM pair adjudication as part of the full
  SourceConcept resolver chain.
- SC1 LLM adjudication used=true, judgment_count=300, max_calls=300.
- R1 executed deterministic SourceConcept resolver stages and SourceConcept
  scoped persistence, but R1 did not request or run LLM pair adjudication.
- R1/A1 route evidence cannot approve R2.
- Required path: executable contract hardening, then R1R full-chain replay, then
  A1R route audit rerun.

## Why docs/prompt memory are insufficient

INC1 happened because a deterministic-only R1 run was reported honestly in its
own scope, but the later route process did not have an executable gate that
distinguished deterministic-only evidence from a full SC1-equivalent chain.
Docs and prompts can describe the distinction; they cannot fail a runner or a
summary. GOV3 moves the critical proof into code and tests.

## Executable phase contract framework

A phase contract is a machine-readable declaration of what a phase is allowed
and required to do. The framework records:

- `contract_id`
- `contract_version`
- `phase_kind`
- required inputs, stages, artifacts, summary fields, public sections, private
  artifacts, and validation commands
- DB, provider, LLM, mutation, redaction, review-pack, artifact-lifecycle,
  route-decision, and failure-behavior policies
- custom executable checks

The checker loads a summary JSON, runs generic required-field and forbidden
stage checks, runs contract-specific checks, prints JSON, and exits nonzero on
failure.

## Contract registry

GOV3 registers these contracts:

1. `python_env_contract_v1`
2. `postgres_db_contract_v1`
3. `media_import_contract_v1`
4. `classification_contract_v1`
5. `ai_tagging_contract_v1`
6. `localization_contract_v1`
7. `source_metadata_contract_v1`
8. `source_concept_full_chain_contract_v1`
9. `review_pack_contract_v1`
10. `route_audit_contract_v1`
11. `public_redaction_contract_v1`
12. `mutation_safety_contract_v1`
13. `artifact_lifecycle_contract_v1`
14. `destructive_operation_contract_v1`
15. `entity_truth_bridge_contract_v1`

## Required contracts implemented

The contracts are executable code, not prose. Initial validation is summary and
artifact-structure based so future runners can adopt them incrementally without
importing application runtime code. They are deliberately stdlib-only and do not
open DB connections, initialize providers, call LLMs, or write files.

## SourceConcept full-chain contract

`source_concept_full_chain_contract_v1` is the critical R1R gate. It requires the
full SC1 chain:

- source signal adapter inventory;
- media_tags adapter;
- SourceMetadataRecord structured-field adapter;
- SourceTagObservation adapter;
- SourceNameObservation adapter;
- SourceSearchableNameAssertion adapter;
- SourceNameCandidate / F7a adapter;
- ProviderCache adapter, or explicit zero-eligible proof;
- deterministic blocking key generation;
- deterministic edge graph generation;
- context compatibility;
- alias component / context equivalence;
- union/component resolution;
- bounded LLM pair adjudication planning;
- LLM pair selection;
- LLM provider availability check;
- LLM judgment execution or explicit blocked-before-write status;
- LLM judgment count;
- LLM cache accounting;
- LLM decisions recorded as source-layer evidence only;
- persistence to allowed SourceConcept tables;
- mutation proof;
- post-commit verification;
- validation pack / review pack.

The contract distinguishes:

- `deterministic_only`
- `full_chain_completed`
- `full_chain_blocked_llm_unavailable`
- `full_chain_blocked_budget`
- `full_chain_inconclusive_missing_artifacts`

It fails if a phase claims full-chain completion while LLM adjudication is
required but missing, while `llm_judgment_count=0` without zero-eligible proof,
or while required stages are absent. It also fails if a full-chain summary sets
`llm_adjudication_plan.required=false` while eligible LLM pairs exist, if
eligible or selected pair counts exceed `max_calls` without explicit
over-budget/call-cap approval, if `full_chain_fidelity_passed` is not true, or
if validation-pack evidence is missing.
`required=false` alone never skips LLM checks for `full_chain_completed`; the
only zero-judgment path requires `zero_eligible_proof=true`, explicit
`eligible_pair_count=0`, and a recorded reason. `llm_judgment_count` must also
stay within `max_calls` unless explicit over-budget/call-cap approval is
recorded.

## Public redaction contract

`public_redaction_contract_v1` scans public JSON and Markdown keys and values.
It fails on common tokens, secret-looking unredacted key names, Windows paths,
UNC paths, file-scheme URIs, private POSIX filesystem roots, and source/private
provenance values such as raw filenames or source paths. It also fails on bare
filename values in public Markdown/JSON, sensitive URL/path keys such as `source_url` or
`thumbnail_url` unless explicitly redacted, bare `ghp_`, `github_pat_`, `xoxb-`,
`sk-`, `Bearer`, or `Authorization` tokens, and local/private POSIX filesystem
locations. Nested public JSON values inherit private provenance context from
ancestor keys such as `source_url` or `raw_filename`; descendant scalar values
must be explicitly redacted.

## Review pack contract

`review_pack_contract_v1` requires manifest/checksum/redaction proof, checksum
count consistency, final-file-set redaction coverage, current public report
copy proof, zip generation, and not-committed status. Public-report-copy proof
is required as executable evidence, not a prose note. It fails on reversible
fixed-salt hash markers and raw/private labels.

## Route audit contract

`route_audit_contract_v1` requires read-only stable snapshot proof and blocks
route approval when an upstream pipeline contract is failed, deterministic-only,
or incomplete. It fails if `route_approved=true` appears with a blocked or
provisional final status, if `mutation_proof.passed=false`, if forbidden or
unexpected mutation tables are recorded, or if a route-approved summary lacks a
review pack without a contract-approved waiver. Existing A1 remains blocked,
not route-approved. Route-approved summaries must use upstream
`source_concept_full_chain_contract_v1` evidence with status
`full_chain_completed`, `full_chain_fidelity_passed=true`, and no missing
required stages; deterministic-only, blocked, or inconclusive upstream evidence
cannot approve a route.

## Mutation safety contract

`mutation_safety_contract_v1` checks allowlist/denylist proof, forbidden table
changes, unexpected table changes, and destructive-operation gating. It is a
summary-level gate for future write phases; GOV3 itself performs no writes.
`mutation_proof.passed` must be explicitly true. Empty objects, missing
`passed`, or `passed=false` fail even when no table deltas are listed.

## Artifact lifecycle contract

`artifact_lifecycle_contract_v1` distinguishes production, durable validation,
reusable validation, phase-scoped, one-off local/private, and public
report/handoff artifacts. It fails if private artifacts or review packs are
committed, or if public report artifacts are not redacted. Public
report/handoff artifacts must now provide explicit `redacted=true` evidence;
missing redaction evidence fails instead of defaulting to pass.
Review-pack classifications are normalized across `review pack`, `review_pack`,
and `review-pack`.

## Final fail-closed closure sweep

The recurring failure class was permissive helper behavior: ambiguous route
status, generic completion claims, missing counters, blocked stage states,
empty proof artifacts, raw redaction matches, and non-list mutation violations
could be interpreted as sufficient proof. GOV3 now enforces these code-level
invariants:

- All completion/approval claims are equivalent for blocking purposes:
  `target_met`, `route_approved`, `full_chain_complete`,
  `full_chain_completed`, and `safe_to_merge` cannot appear on deterministic,
  blocked, provisional, or inconclusive evidence. Tests:
  `test_source_concept_deterministic_only_fails_safe_to_merge_claim`,
  `test_source_concept_blocked_or_inconclusive_fails_safe_to_merge_claim`,
  `test_route_audit_inconclusive_status_cannot_claim_route_approved`.
- Route status is route-derived first, and route approval requires full
  SourceConcept upstream contract proof with `passed=true`,
  `full_chain_completed`, fidelity passed, and explicit
  `missing_required_stages=[]`. Tests:
  `test_route_audit_route_status_takes_priority_over_pipeline_status`,
  `test_route_audit_requires_upstream_contract_passed_and_missing_stages_list`,
  `test_route_audit_allows_route_approved_with_full_chain_upstream`.
- Required SourceConcept stages count only positive completion states; blocked,
  skipped, missing, failed, inconclusive, or not-run stages do not satisfy
  full-chain completion. Tests:
  `test_source_concept_blocked_required_stage_does_not_count_as_completed`,
  `test_source_concept_blocked_status_with_blocked_stage_cannot_claim_safe_to_merge`.
- Public redaction findings never echo raw matches in result details, failure
  `actual`, or stdout JSON; nested secret/private-provenance parent keys carry
  to descendant scalar values. Tests:
  `test_public_redaction_contract_does_not_echo_sensitive_matches`,
  `test_public_redaction_contract_propagates_secret_parent_context`,
  `test_public_redaction_contract_propagates_private_provenance_context`.
- Full-chain LLM counters are explicit positive proof. Missing
  `eligible_pair_count`, `selected_pair_count`, or `llm_judgment_count` fails;
  zero-eligible proof must be internally consistent. Tests:
  `test_source_concept_full_chain_fails_missing_llm_counters`,
  `test_source_concept_zero_eligible_proof_requires_consistent_counters`.
- Mutation table violations fail whether encoded as strings, dicts, or lists,
  and mutation safety requires `mutation_proof.passed=true`. Tests:
  `test_mutation_safety_contract_fails_non_list_table_violations`,
  `test_mutation_safety_contract_requires_positive_passed_proof`.
- Artifact lifecycle and required proof fields are normalized and positive:
  review-pack/public-report variants are recognized, public artifacts require
  `redacted=true`, and required ledgers/proofs cannot be null or empty. Tests:
  `test_artifact_lifecycle_contract_normalizes_public_classification`,
  `test_required_artifact_and_ledger_fields_must_be_non_empty`.

No P2/P3 findings are intentionally deferred in this closure sweep. Remaining
limitations are adoption work for future runners; they do not allow R1R/A1R
route approval bypass because route approval now requires executable upstream
contract proof.

## Latest reviewer closure

The latest reviewer pass against head `3cac039ad0` found remaining permissive
edges in diagnostics and proof handling. This closure fixes the same
fail-closed model consistently:

- Public redaction diagnostics now sanitize sensitive JSON keys before they
  appear in result paths or stdout JSON. Tests:
  `test_public_redaction_contract_sanitizes_sensitive_json_key_paths`,
  `test_public_redaction_contract_does_not_echo_sensitive_matches`.
- Public redaction still catches local/private filesystem paths, tokens,
  filenames, and private provenance values, but no longer treats ordinary
  public API route text as a local path by default. Tests:
  `test_public_redaction_contract_allows_public_api_route_text`,
  `test_public_redaction_contract_catches_private_path_shapes`.
- Secret and private-provenance context now applies to non-string scalar
  values. Tests:
  `test_public_redaction_contract_scans_sensitive_non_string_values`,
  `test_public_redaction_contract_propagates_secret_parent_context`.
- Route audits now require `mutation_proof.passed=true` for all route audits,
  including blocked/not-approved audits. Tests:
  `test_route_audit_requires_positive_mutation_proof_for_blocked_routes`,
  `test_route_audit_fails_mutation_proof_false`.
- Route approval now requires complete review-pack proof; `generated=true`
  alone is insufficient, and public report copies must be current/fresh or
  rendered/generated from the current summary. Tests:
  `test_route_audit_route_approved_requires_complete_review_pack_proof`,
  `test_review_pack_public_report_copy_must_be_current`.
- SourceConcept full-chain completion now rejects partial LLM pair resolution:
  selected pairs must be covered by judgments, cached decisions, or explicit
  skipped-pair accounting. Test:
  `test_source_concept_full_chain_rejects_partial_llm_pair_resolution`.
- Forbidden stages with `executed=true` fail even when their status text is
  `skipped` or `blocked`. Test:
  `test_forbidden_stage_executed_true_fails_even_with_negative_status`.

No P2/P3 items are intentionally deferred in this latest closure. Remaining
future work is adoption inside R1R/A1R runners after GOV3 merges; the current
contracts do not allow deterministic-only upstream evidence, missing mutation
proof, weak review-pack proof, or unsafe public redaction diagnostics to approve
R1R/A1R route decisions.

## R1R prerequisites

R1R must not start until GOV3 is merged. After GOV3:

1. Verify existing PX1/source metadata/tag/name/assertion inputs under
   `source_metadata_contract_v1`.
2. Verify no provider rerun is required unless inputs are invalid or
   unverifiable.
3. Run R1R full SourceConcept pipeline replay/remediation under
   `source_concept_full_chain_contract_v1`.
4. Include deterministic resolver plus bounded LLM pair adjudication.
5. If the LLM plan exceeds the approved budget or call cap, stop and request
   approval.
6. Generate an R1R review pack.

## A1R prerequisites

A1R must run only after R1R outputs exist. It must use
`route_audit_contract_v1`, consume the R1R pipeline-contract result, and keep
route approval blocked if the upstream R1R contract is failed, blocked,
deterministic-only, or inconclusive.

## Remaining limitations

GOV3 creates reusable contract gates, but existing phase runners do not all
write contract-shaped summaries yet. Future phases must adopt the summary
fields and run the checker as part of their validation stack. GOV3 also does
not retrofit DB-level constraints or rewrite historical runners.

## Validation

GOV3 validation passed:

- `git diff --check`
- `git diff --cached --check`
- `scripts/check_python_env.py --expected-python "$PY"`
- `py_compile` for the checker and tests
- `pytest tests/test_phase_contracts.py tests/test_phase45_doc1_documentation_state.py -v`
  - result: 65 passed
- `json.tool` for this GOV3 summary
- contract checker examples:
  - A1 summary with `route_audit_contract_v1`: exit 0, passed=true,
    route_approved=false, status=`blocked_pending_pipeline_fidelity_remediation`
  - INC1 summary with `public_redaction_contract_v1`: exit 0, passed=true
  - mock passing SourceConcept full-chain fixture: exit 0, passed=true
  - mock deterministic-only fixture: exit 1 as expected, passed=false
  - temporary P1 negative fixtures for LLM opt-out, selected-pair call cap,
    missing validation pack, blocked route approval, failed mutation proof,
    bare filename redaction, nested DB password, and missing public redaction
    evidence: exit 1 as expected
  - final negative fixtures for missing/invalid zero-eligible proof,
    `llm_judgment_count > max_calls`, deterministic-only upstream route
    approval, empty mutation proof, and nested private-provenance values:
    exit 1 as expected
  - closure sweep negative fixtures for `safe_to_merge` claims, route status
    precedence, blocked/skipped stages, sanitized redaction output, missing LLM
    counters, inconsistent zero-eligible proof, non-list mutation violations,
    normalized artifact classifications, and empty required ledgers/proofs:
    exit 1 as expected

No browser validation is required because GOV3 does not change UI/runtime files.

## What must not happen yet

- Do not run R1R.
- Do not run A1R.
- Do not run R2.
- Do not run provider/Pixiv/gallery-dl/SauceNAO/Google.
- Do not run LLM.
- Do not write DB.
- Do not run media import, classification, AI tagging, localization, or Entity
  bridge.
- Do not mutate Entity truth, `media_tags`, source metadata, SourceConcept
  tables, source roots, iCloud/source storage, originals, or thumbnails.
- Do not cleanup/delete/reset/drop/truncate anything.

## Next step

After GOV3 is reviewed and merged, start remediation from the
source/tag/source-metadata boundary. Do not rerun media import, classification,
AI tagging, or localization unless a contract audit finds those upstream outputs
invalid or unverifiable. Old R1/A1 route evidence cannot approve R2.
