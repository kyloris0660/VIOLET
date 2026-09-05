# Current Handoff - V.I.O.L.E.T.

> Generated from `docs/state/current-phase.json`; this file is not the fact source.

## Current Facts

- Phase: `SCV2-PX3` - Pixiv Product Integration.
- Repository / PR: `kyloris0660/VIOLET` / PR #150.
- Branch: `codex/scv2-px3-restored-database-canary`.
- Status: `SCV2_PX3_RESTORED_DATABASE_CANARY_IN_PROGRESS`.
- Implementation evidence HEAD/tree: `476d5c0b5a97447cc2ee12365f7e0c37b005ce0e` / `0b82f0b42dc30c7548317bd95789dd6fb2ef4120`.
- `target_met=true`; `safe_to_merge=false`; `route_approved=false`.
- `px3_started=true`; `px3_owner_accepted=true`; `px3_merge_authorized=false`.

## PX2 Merge Projection

- Accepted PR #148 HEAD/tree: `bf8055af61c3a5d32155701ed7110db692047dba` / `507a223a9156ff2f9944524303419e85891812fa`.
- Merge commit/tree: `421e2989d274e2dc4492d5bccc10720dcfbbaa4f` / `507a223a9156ff2f9944524303419e85891812fa`.
- Merge parents: `5a8efdaf954ab95bd82f95464af31a7fd0873e5e,bf8055af61c3a5d32155701ed7110db692047dba`.
- Merge time: `2026-09-02T15:06:43Z`.
- `SCV2_PX2_MERGED`; accepted tree equals merge tree; no parallel main commit was present.

## Final Product Route

- PX1 repository-owned Pixiv aggregate/signal contract remains the input authority.
- PX2 existing SourceConcept resolver, graph policy, candidate dispositions, ambiguity ledger, and persistence seam are reused.
- PX3 adds product-owned run persistence, dry-run/apply/rollback, read APIs, and an operable admin UI.
- The real provider adapter is wired but real network, credentials, source, and user-data execution remain disabled.
- Historical phase-4.5-PX1 is historical compatibility evidence, not current authority.

## Executable Contract

- Contract: `scv2_px3_pixiv_product_integration_contract_v1`.
- Public schema: `violet.scv2-px3-pixiv-product-integration-result.v1`.
- Repository gap map completed: `true`.
- PX1/PX2 reused: `true`.
- Product persistence verified: `true`.
- Dry-run/apply/rollback verified: `true`.
- API/UI verified: `true`.
- Synthetic browser E2E verified: `true`.
- Controlled canary entrypoints verified: `true`.
- Hosted CI and owner authority remain separate and are not synthesized by local evidence.

## Current Gate And Authority

- Gate: `restored_database_canary_execution_in_progress`.
- Scope: Current SCV2-PX3 authorized PostgreSQL copy rehearsal
- Resolution: Complete fixed 1% work lifecycle on the isolated restored database; stop before original database writes.
- `repository_migration_code_authorized=true`; migrations may be tested only on task-owned temporary databases.
- `synthetic_local_server_browser_e2e_authorized=true`.
- STOP: normal startup executes Base.metadata.create_all() and schema migration. Back up and successfully restore before the first normal startup against any existing database.
- `real_pixiv_network_execution_authorized=false`; real gallery-dl execution is likewise forbidden.
- `existing_database_or_app_storage_mutation_authorized=false`.
- `real_source_or_icloud_access_authorized=false`; `provider_credentials_authorized=false`.
- `user_data_import_authorized=false`; LLM execution is forbidden; `production_authorized=false`; `full_library_import_authorized=false`.

## Completed Checkpoints

- PR #149 is already merged. Its expected-head merge permission is consumed.
- Original database read-only identification and private backup occurred; real database activity is nonzero. Original database/storage writes remain forbidden.
- Independent PostgreSQL restore, additive migration and fixed 1% work SourceConcept materialization are authorized only in the task copy. This is not media import.
- Copy metadata is frozen; runtime credentials must deny metadata refresh. No original media, thumbnails, source files, provider network or credentials may be accessed.
- Original first canary remains owner-gated: recheck target and backup/restore, authorize incremental schema startup separately, recompute actual-target dry-run and accept all three exact fingerprints before apply.

- `scv2_fl1_i2_pr146_merge_projection`: `accepted_head_is_merge_parent_and_accepted_tree_equals_merge_tree` - `8a825bcdd12f76d1c2c396b7039bd9e326cd63dc`.
- `scv2_px1_remote_sync_preflight`: `trusted_origin_main_exact_expected_merge_with_no_later_commits` - `9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71`.
- `scv2_px1_governance_entry`: `implementation_authorized_synthetic_offline_only_route_entered_with_all_data_plane_authorities_false`.
- `scv2_px1_vertical_slice_and_signal_projection`: `fourteen_deterministic_work_page_aggregates_and_forty_existing_sourceconcept_compatible_signals_replayed_with_zero_name_only_identity_anchors_or_cross_context_unions` - `c4bf9f62b2e1bec544342717659dea0b697d530a021496f9c8eefdaf3e3bc9f1`.
- `scv2_px1_same_head_receipt_and_contract`: `final_correction_local_operator_receipt_passed_433_focused_tests_on_exact_implementation_head_and_executable_contract_independently_rebuilds_fixture_aggregate_signal_px2_consumer_and_authority_facts` - `782360c04da475cac98f928038f34c5a337c814f`.
- `scv2_px1_full_non_e2e_compatibility`: `4238_passed_22_skipped_four_raw_failures_two_detached_validation_worktree_branch_identity_checks_one_docs_only_historical_phrase_assertion_closed_by_final_projection_and_one_missing_original_ai_execution_evidence_failure_reproduced_on_exact_origin_main`.
- `scv2_px1_final_exact_head_review_correction`: `four_accepted_metadata_input_and_compatibility_findings_closed_and_same_name_cross_work_artist_finding_disproved_by_exact_early_guard_regression_without_clustering_policy_change` - `782360c04da475cac98f928038f34c5a337c814f`.
- `scv2_px1_normal_pr_created`: `pr147_created_as_draft_for_final_exact_head_validation_then_single_ready_transition_without_merge`.
- `scv2_px1_pr147_expected_head_merge`: `owner_accepted_head_is_second_merge_parent_merge_tree_equals_accepted_tree_and_origin_main_has_no_parallel_commit` - `5a8efdaf954ab95bd82f95464af31a7fd0873e5e`.
- `scv2_px2_governance_entry`: `px2_synthetic_implementation_started_from_verified_pr147_merge_with_all_real_data_migration_model_ui_and_production_authorities_false` - `480d6a548e6276afeccf49ec75a73d7389b995fe`.
- `scv2_px2_deterministic_clustering_vertical_slice`: `fourteen_px1_bundles_and_forty_signals_resolved_to_twenty_concepts_with_all_fifty_nine_candidate_pairs_accounted_and_nonblocking_ambiguous_ledger_persisted` - `6cc88ac815fa364f93afb58befe2212e002f6f67bada6d42389e10955614c06a`.
- `scv2_px2_same_head_receipt_and_contract`: `five_hundred_seventy_six_canonical_focused_tests_passed_on_exact_implementation_head_and_contract_independently_rebuilt_repository_px1_fixture_consumer_resolver_candidate_ledger_persistence_and_authority_facts` - `9ee1d96004daa843544b977ed3ae607c51299f9b`.
- `scv2_px2_final_full_non_e2e_compatibility`: `4296_passed_22_skipped_three_raw_failures_two_pre_carry_forward_documentation_binding_checks_closed_by_final_projection_and_one_historical_missing_original_ai_execution_evidence_failure_15_warnings_without_px2_functional_regression`.
- `scv2_px2_final_review_correction`: `repository_owned_px1_evidence_rederived_operation_receipt_actual_path_counted_source_state_ledger_matches_effective_resolution_and_only_approved_or_stably_authoritative_aliases_can_union_five_threads_replied_once_and_resolved` - `9ee1d96004daa843544b977ed3ae607c51299f9b`.
- `scv2_px2_pr148_ready`: `normal_pr148_created_as_draft_at_exact_implementation_head_then_transitioned_to_ready_once_after_final_docs_projection_without_merge`.
- `scv2_px1_post_merge_late_review_adjudication`: `seven_threads_created_after_pr147_merge_once_adjudicated_five_real_path_findings_bound_to_px3_one_retained_database_binding_finding_rejected_by_independent_replay_one_stable_aggregate_key_finding_closed_at_px2_consumer_boundary_and_only_original_workspace_thread_remains_unresolved` - `5a8efdaf954ab95bd82f95464af31a7fd0873e5e`.
- `scv2_px2_pr148_expected_head_merge`: `owner_authorized_head_is_second_merge_parent_merge_tree_equals_accepted_tree_and_origin_main_has_no_parallel_commit` - `421e2989d274e2dc4492d5bccc10720dcfbbaa4f`.
- `scv2_px3_repository_gap_map_and_governance_entry`: `existing_ingestion_px2_resolver_sourceconcept_persistence_api_frontend_import_and_feature_flag_seams_mapped_and_final_phase_started_with_real_data_plane_authorities_false` - `507a223a9156ff2f9944524303419e85891812fa`.
- `scv2_px3_product_integration_vertical_slice`: `frozen_SCV2-PX1_and_SCV2-PX2_chain_reused_twenty_clusters_fifty_nine_candidate_dispositions_and_twenty_nine_ambiguity_records_projected_through_atomic_sourceconcept_owned_product_persistence_read_api_and_operable_admin_ui` - `f1ba10194cd72232b4b8bb2ee24e7fe421d0a18115ac3d4918dc1e8af72ce020`.
- `scv2_px3_same_head_receipt_and_contract`: `canonical_focused_validation_passed_on_exact_implementation_head_clean_before_after_and_contract_independently_rebuilt_px1_px2_product_persistence_api_and_authority_facts_with_zero_errors_or_warnings` - `389ab3994bb81ca772ce491dac88eb1d8b292d3d`.
- `scv2_px3_full_non_e2e_and_synthetic_browser_acceptance`: `4337_passed_22_skipped_three_raw_failures_two_pre_carry_forward_documentation_assertions_closed_by_final_projection_and_one_historical_missing_original_ai_execution_evidence_limitation_while_real_browser_dry_run_apply_detail_filter_rollback_reapply_completed_with_zero_final_console_errors`.
- `scv2_px3_pr149_ready`: `normal_pr149_created_as_draft_at_exact_implementation_head_then_transitioned_to_ready_once_after_final_governance_projection_without_merge`.
- `scv2_px3_final_local_media_product_closure`: `actual_gallery_search_media_detail_duplicate_support_row_id_neutrality_exact_plan_apply_replay_rollback_reapply_verified_by_independent_contract_and_real_edge_browser` - `ce5a11f75f13965652cb6f9179bbde45526c6e18`.
- `scv2_px3_pr149_expected_head_merge`: `accepted_head_is_second_parent_and_accepted_tree_equals_merge_tree_origin_main_verified_once_merge_authority_consumed` - `6db72c73397c17128bd2ce9be54f25233bc853f0`.
- `px3_original_readonly_backup_independent_restore`: `Original database read-only backup was independently restored to a new task-owned PostgreSQL database; source rows and foreign keys verified. Private data remains local; original writes zero.`.
- `px3_restored_postgresql_actual_product_lifecycle`: `Fixed 1% of 465 eligible works selected 5 works/pages/media, with 51 bindings and 43 clusters. Fresh-session accounting, actual SourceConcept-specific search, detail, replay, selection rejection, rollback and reapply passed. All 54 original table snapshots restored exactly after rollback; real Edge metadata-only browser passed with zero full-detail requests on admin initialization.`.

## Next Action

- Required checkpoint: `restored_database_canary_verification_then_original_database_apply_approval`.

## Durable Links

- [Current mainline roadmap](roadmap/current-mainline-roadmap.md)
- [Project roadmap entrypoint](project-roadmap.md)
- [Pixiv metadata ingestion and promotion policy](pixiv-metadata-ingestion-and-promotion-policy.md)
- [SourceConcept tag and search semantics](source-concept-tag-search-semantics.md)
- [Phase contracts](phase-contracts.md)
- [Detailed agent runbook](development/agent-runbook.md)
- [Test workflow](test-workflow.md)

## Deferred Debt And Exact Due Gates

- `FL1_I2_LISTED_MEMBER_VALIDATION_GATE` - due before `real source or iCloud enumeration or any I2 positive inventory authority`; PR #146 final review requires every listed member to be validated before suffix filtering; PX1 never enumerates a real source.
- `FL1_I2_EVENT_TIME_LOWER_BOUND_GATE` - due before `I2 target, safe, route, machine-verifiable, or validation-receipt reuse`; PR #146 final review found that evidence timestamps need a lower bound tied to run start; PX1 uses no I2 real-operation receipt.
- `FL1_I2_JPEG_CONTENT_AUTHORITY_GATE` - due before `any real-source JPEG content_verified claim`; Boundary-marker parsing does not by itself grant JPEG codec/content authority.
- `FL1_I2_VP8_CONTENT_AUTHORITY_GATE` - due before `any real-source VP8 content_verified claim`; Container checks do not by themselves grant VP8 payload/content authority.
- `FL1_I2_INITIAL_ENUMERATION_BUDGET_GATE` - due before `first real source directory listing`; Initial enumeration must obey the same finite budgets as later pages.
- `FL1_I2_OPERATION_ADMISSION_CAP_GATE` - due before `first real open, read, hash, structure validation, or retry`; Open, hash, validation, and retry caps must be enforced at admission rather than only after execution.
- `FL1_I2_EVIDENCE_PREPARSE_BUDGET_GATE` - due before `untrusted or real evidence ingestion, remote CI authority, or I2 positive authority reuse`; Evidence files must be size-bounded before JSON parsing to avoid a deterministic contract false positive or resource exhaustion.
- `FL1_I2_MAX_DEPTH_REDERIVATION_GATE` - due before `real enumeration or any I2 positive inventory authority`; Maximum traversal depth must be re-derived from member evidence rather than trusted as a caller total.
- `FL1_I2_NONNEGATIVE_BYTE_ACCOUNTING_GATE` - due before `I2 budget closure, positive authority, or validation-receipt reuse`; Negative byte totals must be rejected before they can reduce or satisfy a budget.
- `FL1_I2_FAILED_RECEIPT_COMPLETION_GATE` - due before `I2 evidence_complete, target, safe, route, or finalizer reuse`; A failed local receipt must never allow evidence_complete or phase-completion claims.
- `FL1_I2_DYNAMIC_LOADER_ENVIRONMENT_POLICY` - due before `first POSIX real-source execution, remote-CI positive authority, or hostile-local-environment resistance claim`; Owner-adjudicated PR #146 debt keeps dynamic-loader environment scrubbing outside the Windows local-operator threat model.
- `FL1_I2_VENV_FULL_PYTHON_SUPPLY_CHAIN_BINDING` - due before `machine-verifiable CI, reproducible-environment, tamper-resistant, or untrusted-venv claim`; Owner-adjudicated PR #146 debt defers whole-venv Python hashing outside the trusted owner-machine model.
- `FL1_I3_REAL_SOURCE_SCOPE_GATE` - due before `any real source or iCloud listing, stat, observation, open, read, hash, or validation`; No private real-source scope, protected-root registry, budgets, no-hydration policy, or canary authority exists.
- `PARENT_OBSERVED_CHILD_IDENTITY_CLAIM_BOUNDARY` - due before `any adversarial tamper-resistance claim`; Parent-observed child identity remains local provenance, not kernel, TPM, remote, CI, or tamper-resistant attestation.
- `VALIDATION_RECEIPT_GATE` - due before `any machine-verifiable CI, owner acceptance, or merge claim`; Local same-head receipts do not grant CI, owner, or merge authority.
- `OWNER_AUTHORITY_GATE` - due before `merge or any owner-accepted projection`; Automated tests and contracts cannot synthesize owner acceptance, safe-to-merge, or merge authority.
- `POSIX_LEDGER_DURABILITY_GATE` - due before `any cross-platform power-loss durability claim`; No unsupported host power-loss or POSIX durability claim is made by PX1.
- `STABLE_REPLAY_GATE` - due before `any real-data Stable Replay consumption or authority`; PX1 proves deterministic synthetic business replay only and does not authorize historical or user-data Stable Replay.
- `SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE` - due before `caller or user supplied workspace or evidence path, remote CI consumption of untrusted evidence, existing database or app-storage access, real-source canary, or production`; Dangling symlinks, component swaps, fixed-name evidence symlinks, SQLite URL-sensitive path characters, and hostile caller-controlled workspaces are outside the repository-owned temporary workspace threat model proven by synthetic PX1.
- `SCV2_PX3_MULTIWORKER_APPLY_GATE` - due before `multiple Uvicorn workers, multiple owners, or concurrent canary apply`; Default run.py uses one Uvicorn worker and the current scope is one owner; UI blocks repeated clicks and uniqueness conflicts return stable 409.
- `SCV2_PX3_METADATA_REFRESH_BINDING_GATE` - due before `Before metadata refresh or ongoing original-database use with active bindings`; Current active binding predicate does not fully revalidate corrected business metadata or invalidate cached search; copy inputs are frozen and refresh is denied.
- `SCV2_PX3_POLICY_VERSION_CAPTURE_GATE` - due before `Before the next PX2 policy version change`; Historical projection reconstruction uses current PX2 context and candidate policy constants.

Updated: `2026-09-05`.
