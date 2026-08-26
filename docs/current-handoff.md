# Current Handoff - V.I.O.L.E.T.

> Generated from `docs/state/current-phase.json`; this file is not the fact source.

## Current Facts

- Phase: `SCV2-PX1` - Pixiv Metadata Consolidation and Offline Vertical Slice.
- Repository / PR: `kyloris0660/VIOLET` / normal PR #147.
- Branch: `codex/scv2-px1-pixiv-metadata-consolidation`.
- Accepted mainline HEAD/tree: `8a825bcdd12f76d1c2c396b7039bd9e326cd63dc` / `9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71`.
- Implementation evidence HEAD/tree: `59349c76ecd086b535ad7cb4c5e14236b9fb241c` / `260bedf1fddfc0c5329f0defc5fdb14f1e0d195b`; status: `same_head_local_receipt_and_contract_passed_with_carry_forward_limited_to_final_governance_and_test_projection`.
- Status: `SCV2_PX1_PIXIV_METADATA_CONSOLIDATION_READY_FOR_OWNER_AUDIT`.
- `target_met=true`; `safe_to_merge=false`; `route_approved=false`.
- Manual acceptance: `pending_scv2_px1_exact_head_owner_audit`; `next_phase_started=false`.
- Contract: `scv2_px1_pixiv_metadata_consolidation_contract_v1`; public schema: `violet.scv2-px1-pixiv-metadata-summary.v1`.
- Synthetic vertical slice / deterministic replay verified: `true` / `true`.
- Contract evidence remains a local operator receipt; it is neither CI authority nor owner acceptance.

## PR #146 Merge Projection

- Accepted PR HEAD/tree: `914d746c3548241a99333393daa88caefd8b2337` / `9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71`.
- Merge commit/tree: `8a825bcdd12f76d1c2c396b7039bd9e326cd63dc` / `9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71`; merged: `true`.
- Final review `5031131564` covered `914d746c3548241a99333393daa88caefd8b2337` and recorded `10` unresolved, non-outdated findings.
- Merge does not silently close those findings; every finding remains attached to its exact future due gate below.

## PX1 Product Slice

- Synthetic Pixiv/gallery-dl metadata enters the existing canonical normalization and lifecycle authority.
- Existing SourceMetadataRecord, SourceNameObservation, SourceTagObservation, and provenance models remain the only source-layer persistence seam.
- A deterministic Pixiv work/page aggregate exposes stable creator ID as identity and names, title, and tags only as mutable observations.
- The aggregate projects into the existing SourceConcept signal semantics without cluster materialization or Entity promotion.
- The repository-owned runner uses exactly two task-owned temporary SQLite databases and performs no provider, source-root, app-storage, media, or production activity.
- Historical `phase-4.5-PX1` orchestration remains historical compatibility evidence; it is not the SCV2-PX1 authority.

## Current Gate And Authority Boundary

- Gate: `pending_scv2_px1_owner_audit_and_merge_decision` (SCV2-PX1 exact final PR HEAD/tree, executable contract, local receipt, and deferred due-gate mapping).
- Resolution: The project owner audits the exact normal PR head and either requests a bounded correction or separately authorizes merge; automation grants neither safe-to-merge nor merge authority.
- `owner_accepted=false`; `safe_to_merge=false`; `merge_authorized=false`; `px2_started=false`.
- `real_provider_authorized=false`; `real_source_authorized=false`; `full_import_authorized=false`; `production_authorized=false`.
- External data-plane network, existing DB, real source, media download, and production operation counts: `0/0/0/0/0`.

## Fixed Near-Term Route

- `SCV2-PX1` - Pixiv metadata consolidation and offline synthetic vertical slice; started: `true`.
- `SCV2-PX2` - deterministic Pixiv metadata clustering, identity, and candidate explanation; started: `false`.
- `SCV2-PX3` - persistence, API/UI integration, and bounded owner-acceptance canary; started: `false`.

## Completed Checkpoints

- `scv2_fl1_i2_pr146_merge_projection`: `accepted_head_is_merge_parent_and_accepted_tree_equals_merge_tree` - `8a825bcdd12f76d1c2c396b7039bd9e326cd63dc`.
- `scv2_px1_remote_sync_preflight`: `trusted_origin_main_exact_expected_merge_with_no_later_commits` - `9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71`.
- `scv2_px1_governance_entry`: `implementation_authorized_synthetic_offline_only_route_entered_with_all_data_plane_authorities_false`.
- `scv2_px1_vertical_slice_and_signal_projection`: `nine_deterministic_work_page_aggregates_and_fifteen_existing_sourceconcept_compatible_signals_replayed_with_zero_name_only_identity_anchors_or_cross_context_unions` - `7220f9f57fd577a0acc683cfaf8b7b74817fcdfa5cfb499a1d780c34d38bb077`.
- `scv2_px1_same_head_receipt_and_contract`: `local_operator_receipt_passed_295_focused_tests_and_executable_contract_independently_rebuilt_fixture_aggregate_signal_and_authority_facts` - `59349c76ecd086b535ad7cb4c5e14236b9fb241c`.
- `scv2_px1_full_non_e2e_compatibility`: `4100_passed_22_skipped_two_stale_current_route_assertions_corrected_and_targeted_2_passed_one_missing_original_ai_execution_evidence_failure_reproduced_on_exact_origin_main`.
- `scv2_px1_normal_pr_created`: `pr147_created_as_draft_for_final_exact_head_validation_then_single_ready_transition_without_merge`.

## Allowed / Forbidden

- Allowed: read repository files and trusted Git or GitHub control-plane state; implement and test the PX1 durable backend projection in the isolated worktree; create new repository-owned synthetic fixtures and task-owned temporary SQLite databases; commit and normally push the feature branch and create one normal pull request; update current governance state and public-safe documentation.
- Forbidden: merge or direct main push, force-push, rebase, reset, stash, clean, or overwrite; real source or iCloud access, inventory, listing, stat, open, read, hash, or mutation; existing database or app-storage access or write; real Pixiv or gallery-dl provider execution, credentials, network, media, or thumbnail download; import, classification or tagging on user data, SourceConcept materialization, Entity truth promotion, or full-library work; LLM or external model, server, browser, E2E, PX2, PX3, or production execution.

## Next Action

- Required checkpoint: `project owner audits the exact Ready PR #147 head and either requests one bounded correction or separately authorizes merge; no merge or PX2 authority is implied`.

## Durable Links

- [Current mainline roadmap](roadmap/current-mainline-roadmap.md)
- [Project roadmap entrypoint](project-roadmap.md)
- [Pixiv metadata ingestion and promotion policy](pixiv-metadata-ingestion-and-promotion-policy.md)
- [SourceConcept tag and search semantics](source-concept-tag-search-semantics.md)
- [Phase contracts](phase-contracts.md)
- [Detailed agent runbook](development/agent-runbook.md)
- [Test workflow](test-workflow.md)

## Deferred Debt And Exact Due Gates

- `FL1_I2_LISTED_MEMBER_VALIDATION_GATE` - owner: future real-source inventory owner; due before: `real source or iCloud enumeration or any I2 positive inventory authority`; PR #146 final review requires every listed member to be validated before suffix filtering; PX1 never enumerates a real source. Requirements: validate and account for every listed member before eligibility filtering; preserve unsupported and rejected member dispositions.
- `FL1_I2_EVENT_TIME_LOWER_BOUND_GATE` - owner: future I2 receipt-reuse owner; due before: `I2 target, safe, route, machine-verifiable, or validation-receipt reuse`; PR #146 final review found that evidence timestamps need a lower bound tied to run start; PX1 uses no I2 real-operation receipt. Requirements: bind all event timestamps to a validated run interval; reject pre-run events before positive projection.
- `FL1_I2_JPEG_CONTENT_AUTHORITY_GATE` - owner: future real-source content-validation owner; due before: `any real-source JPEG content_verified claim`; Boundary-marker parsing does not by itself grant JPEG codec/content authority. Requirements: add bounded codec-level JPEG validation; otherwise retain an explicit unsupported or uncertain disposition.
- `FL1_I2_VP8_CONTENT_AUTHORITY_GATE` - owner: future real-source content-validation owner; due before: `any real-source VP8 content_verified claim`; Container checks do not by themselves grant VP8 payload/content authority. Requirements: add bounded VP8 payload validation; otherwise retain an explicit unsupported or uncertain disposition.
- `FL1_I2_INITIAL_ENUMERATION_BUDGET_GATE` - owner: future real-source inventory owner; due before: `first real source directory listing`; Initial enumeration must obey the same finite budgets as later pages. Requirements: apply entry, page, byte, depth, and deadline budgets from the first listing call; fail closed before an unbounded initial result.
- `FL1_I2_OPERATION_ADMISSION_CAP_GATE` - owner: future real-source operation-gateway owner; due before: `first real open, read, hash, structure validation, or retry`; Open, hash, validation, and retry caps must be enforced at admission rather than only after execution. Requirements: reserve every finite operation budget before dispatch; reject equality and overflow without recording a started operation.
- `FL1_I2_EVIDENCE_PREPARSE_BUDGET_GATE` - owner: future untrusted-evidence or CI owner; due before: `untrusted or real evidence ingestion, remote CI authority, or I2 positive authority reuse`; Evidence files must be size-bounded before JSON parsing to avoid a deterministic contract false positive or resource exhaustion. Requirements: perform no-follow regular-file and byte-budget checks before decode; bind the exact bounded bytes to the reconstructed projection.
- `FL1_I2_MAX_DEPTH_REDERIVATION_GATE` - owner: future real-source inventory contract owner; due before: `real enumeration or any I2 positive inventory authority`; Maximum traversal depth must be re-derived from member evidence rather than trusted as a caller total. Requirements: recompute maximum depth from bound member chains; reject caller-reported depth mismatches.
- `FL1_I2_NONNEGATIVE_BYTE_ACCOUNTING_GATE` - owner: future I2 budget-closure owner; due before: `I2 budget closure, positive authority, or validation-receipt reuse`; Negative byte totals must be rejected before they can reduce or satisfy a budget. Requirements: enforce exact nonnegative integer byte counters; recompute aggregate byte use from evidence.
- `FL1_I2_FAILED_RECEIPT_COMPLETION_GATE` - owner: future I2 receipt/finalizer owner; due before: `I2 evidence_complete, target, safe, route, or finalizer reuse`; A failed local receipt must never allow evidence_complete or phase-completion claims. Requirements: derive completion only from a positive same-head receipt; make every failed or missing receipt force the public completion projection false.
- `FL1_I2_DYNAMIC_LOADER_ENVIRONMENT_POLICY` - owner: future POSIX, remote-CI, or hostile-local-environment execution owner; due before: `first POSIX real-source execution, remote-CI positive authority, or hostile-local-environment resistance claim`; Owner-adjudicated PR #146 debt keeps dynamic-loader environment scrubbing outside the Windows local-operator threat model. Requirements: define and test the target-platform dynamic-loader allowlist; retain local-operator-only receipt claims until then.
- `FL1_I2_VENV_FULL_PYTHON_SUPPLY_CHAIN_BINDING` - owner: future CI or tamper-resistant environment-evidence owner; due before: `machine-verifiable CI, reproducible-environment, tamper-resistant, or untrusted-venv claim`; Owner-adjudicated PR #146 debt defers whole-venv Python hashing outside the trusted owner-machine model. Requirements: bind full Python package and source provenance; do not upgrade the current local receipt to supply-chain attestation.
- `FL1_I3_REAL_SOURCE_SCOPE_GATE` - owner: future separately authorized real-source canary owner; due before: `any real source or iCloud listing, stat, observation, open, read, hash, or validation`; No private real-source scope, protected-root registry, budgets, no-hydration policy, or canary authority exists. Requirements: bind exact private source identity and finite scope; approve protected roots, budgets, no-hydration policy, and stop conditions.
- `PARENT_OBSERVED_CHILD_IDENTITY_CLAIM_BOUNDARY` - owner: future hostile-local threat-model owner; due before: `any adversarial tamper-resistance claim`; Parent-observed child identity remains local provenance, not kernel, TPM, remote, CI, or tamper-resistant attestation. Requirements: keep claims limited to local operator provenance; design separate attestation only if the product requires it.
- `VALIDATION_RECEIPT_GATE` - owner: PX1 and future phase owner; due before: `any machine-verifiable CI, owner acceptance, or merge claim`; Local same-head receipts do not grant CI, owner, or merge authority. Requirements: bind the exact current HEAD/tree and approved Python; keep machine-verifiable CI and owner authority false.
- `OWNER_AUTHORITY_GATE` - owner: project owner; due before: `merge or any owner-accepted projection`; Automated tests and contracts cannot synthesize owner acceptance, safe-to-merge, or merge authority. Requirements: audit the exact normal PR head; issue any merge decision explicitly and separately.
- `POSIX_LEDGER_DURABILITY_GATE` - owner: future POSIX durability owner; due before: `any cross-platform power-loss durability claim`; No unsupported host power-loss or POSIX durability claim is made by PX1. Requirements: define the supported filesystem and flush boundary; test crash recovery on the claimed platform.
- `STABLE_REPLAY_GATE` - owner: future real-data replay owner; due before: `any real-data Stable Replay consumption or authority`; PX1 proves deterministic synthetic business replay only and does not authorize historical or user-data Stable Replay. Requirements: bind exact real-data scope and immutable inputs; obtain separate execution authority.

Updated: `2026-08-26`.
