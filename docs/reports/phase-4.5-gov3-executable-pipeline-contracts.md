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
redaction evidence.

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

## Public redaction contract

`public_redaction_contract_v1` scans public JSON and Markdown keys and values.
It fails on common tokens, secret-looking unredacted key names, Windows paths,
UNC paths, `file://` URIs, private POSIX roots, and source/private provenance
values such as raw filenames or source paths. It also fails on bare filename
values in public Markdown/JSON, sensitive URL/path keys such as `source_url` or
`thumbnail_url` unless explicitly redacted, bare `ghp_`, `github_pat_`, `xoxb-`,
`sk-`, `Bearer`, or `Authorization` tokens, and local/private POSIX paths such
as `/workspace`, `/tmp`, `/opt`, `/var`, `/home`, `/Users`, `/mnt`, and
`/Volumes`.

## Review pack contract

`review_pack_contract_v1` requires manifest/checksum/redaction proof, checksum
count consistency, final-file-set redaction coverage, current public report
copy proof, zip generation, and not-committed status. It fails on reversible
fixed-salt hash markers and raw/private labels.

## Route audit contract

`route_audit_contract_v1` requires read-only stable snapshot proof and blocks
route approval when an upstream pipeline contract is failed, deterministic-only,
or incomplete. It fails if `route_approved=true` appears with a blocked or
provisional final status, if `mutation_proof.passed=false`, if forbidden or
unexpected mutation tables are recorded, or if a route-approved summary lacks a
review pack without a contract-approved waiver. Existing A1 remains blocked,
not route-approved.

## Mutation safety contract

`mutation_safety_contract_v1` checks allowlist/denylist proof, forbidden table
changes, unexpected table changes, and destructive-operation gating. It is a
summary-level gate for future write phases; GOV3 itself performs no writes.
`mutation_proof.passed=false` fails immediately even when no table deltas are
listed.

## Artifact lifecycle contract

`artifact_lifecycle_contract_v1` distinguishes production, durable validation,
reusable validation, phase-scoped, one-off local/private, and public
report/handoff artifacts. It fails if private artifacts or review packs are
committed, or if public report artifacts are not redacted. Public
report/handoff artifacts must now provide explicit `redacted=true` evidence;
missing redaction evidence fails instead of defaulting to pass.

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
  - result: 37 passed
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
