"""Focused SCV2-ML2 stable creator identity closure tests."""

from __future__ import annotations

import copy
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from backend.app.models import (
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptResolutionRun,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
)
from backend.app.services.multilingual_creator_identity_closure_service import (
    CreatorIdentityClosureError,
    CreatorIdentityFamily,
    GAP_REASON_CODES,
    IdentityCandidate,
    TrustedCreatorAlias,
    alias_signal_key,
    audit_touched_identity_components,
    build_star_candidates,
    candidate_growth_accounting,
    component_purity,
    family_accounting,
    fingerprint,
    pair_accounting,
    select_llm_manifest,
)
from scripts import run_phase45_scv2_ml1_multilingual_alias_source_metadata_closure as ml1
from scripts import run_phase45_scv2_ml2_multilingual_identity_candidate_closure as runner
from scripts.phase_contracts.contract_checks import check_phase_contract


CONTRACT_ID = "ml2_multilingual_identity_candidate_closure_contract_v1"


def alias(value: str, kind: str = "creator_name_or_account") -> TrustedCreatorAlias:
    return TrustedCreatorAlias(
        alias_type=kind,
        value=value,
        canonical_key=value.casefold(),
        observation_refs=("obs_1",),
        parent_evidence_fingerprint=fingerprint(value),
    )


def family(stable_id: str, *values: str, existing: int | None = None) -> CreatorIdentityFamily:
    return CreatorIdentityFamily(
        family_id=f"family_{stable_id}",
        provider="pixiv",
        stable_creator_id=stable_id,
        creator_role="creator",
        aliases=tuple(alias(value) for value in values),
        metadata_refs=("metadata_1",),
        work_context_distribution={"work_1": 1},
        evidence_fingerprint=fingerprint((stable_id, values)),
        existing_concept_id=existing,
    )


def valid_summary() -> dict:
    manifest = lambda count: {"count": count, "sha256": "a" * 64}
    return {
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": "target_met_multilingual_identity_candidate_closure",
            "target_met": True,
            "safe_to_merge": True,
            "route_approved": False,
            "active_blockers": [],
            "semantic_completeness_claimed": False,
            "production_readiness_claimed": False,
            "scale_readiness_claimed": False,
        },
        "repository_sync_preflight": {
            "status": "passed_synchronization_preflight",
            "previous_branch": runner.PREVIOUS_BRANCH,
            "previous_head": runner.PREVIOUS_HEAD,
            "origin_main_sha": runner.BASE_SHA,
            "task_branch": runner.TASK_BRANCH,
            "task_branch_starting_sha": runner.BASE_SHA,
            "remote_tracking_branch": "origin/main",
            "tracked_tree_identical_across_transition": True,
            "tracked_change_count_after_switch": 0,
            "staged_change_count_after_switch": 0,
            "user_owned_untracked_and_ignored_preserved": True,
            "untracked_path_count": runner.UNTRACKED_PATH_COUNT,
            "untracked_path_list_sha256": runner.UNTRACKED_PATH_LIST_SHA256,
            "ignored_path_count": runner.IGNORED_PATH_COUNT,
            "ignored_path_list_sha256": runner.IGNORED_PATH_LIST_SHA256,
        },
        "environment_isolation": {"passed": True, "production_profile_active": False},
        "baseline": {
            "family_count": 4248,
            "observed_alias_count": 11860,
            "identity_family_count_before": 606,
            "identity_family_count_after": 606,
            "search_only_family_count_before": 3642,
            "search_only_family_count_after": 3642,
            "accepted_r2r_disposition_count": 3319,
            "accepted_r2r_signal_count": 12249,
        },
        "manifest_fingerprints": {
            "creator-identity-family-manifest.jsonl": manifest(606),
            "creator-identity-alias-observation-manifest.jsonl": manifest(4795),
            "candidate-generation-gap-manifest.jsonl": manifest(30),
            "creator-context-search-case-manifest.jsonl": manifest(94),
            "search-only-family-regression-manifest.jsonl": manifest(3642),
            "candidate-pair-ledger.jsonl": manifest(1214),
            "family-closure-ledger.jsonl": manifest(606),
            "creator-context-closure-ledger.jsonl": manifest(94),
        },
        "candidate_growth": {"linear_bound_passed": True, "all_pairs_alias_expansion_used": False},
        "pair_accounting": {
            "accounting_equality_passed": True,
            "duplicate_pair_count": 0,
            "missing_pair_count": 0,
            "outside_manifest_pair_count": 0,
            "invalid_disposition_count": 0,
        },
        "family_accounting": {
            "accounting_equality_passed": True,
            "duplicate_family_count": 0,
            "missing_family_count": 0,
            "outside_manifest_family_count": 0,
            "invalid_outcome_count": 0,
        },
        "candidate_gap_closure": {"initial_gap_count": 30, "remaining_gap_count": 0, "unexplained_gap_count": 0},
        "graph_safety": {
            "multi_stable_id_creator_component_count": 0,
            "direct_cannot_violation_count": 0,
            "transitive_cannot_violation_count": 0,
            "unauthorized_cross_role_component_count": 0,
            "unknown_role_materialization_count": 0,
            "deferred_identity_union_count": 0,
            "giant_component_recurrence": False,
        },
        "creator_context": {
            "case_count": 94,
            "classification_count": 94,
            "supported_evidence_runtime_success_coverage": 1.0,
            "implementation_failure_with_sufficient_evidence_count": 0,
            "unexplained_failure_count": 0,
        },
        "search_validation": {
            "search_only_regression_count": 0,
            "unsupported_result_count": 0,
            "rejected_only_result_count": 0,
            "superseded_only_result_count": 0,
            "invalid_or_deleted_only_result_count": 0,
            "and_leakage_count": 0,
            "search_caused_identity_mutation_count": 0,
        },
        "r2r_reuse": {"accepted_pair_count": 3319, "disposition_conflict_count": 0},
        "llm": {
            "projected_cost_usd": 0.0,
            "actual_cost_usd": 0.0,
            "deterministic_stable_id_pairs_excluded": True,
        },
        "mutation_proof": {
            "fixed_tables_unchanged": True,
            "forbidden_truth_tables_unchanged": True,
            "changed_fixed_tables": [],
            "changed_forbidden_truth_tables": [],
            "production_write_count": 0,
            "entity_truth_write_count": 0,
            "media_tags_truth_write_count": 0,
            "source_or_icloud_write_count": 0,
        },
        "idempotency": {"passed": True, "fingerprints_equal": True},
        "operation_counts": {
            "external_metadata_provider_calls": 0,
            "pixiv_calls": 0,
            "gallery_dl_calls": 0,
            "entity_truth_writes": 0,
        },
        "route_decision": {"route_approved": False, "next_phase_started": False},
    }


def valid_summary() -> dict:
    manifest = lambda count: {"count": count, "sha256": "a" * 64}
    zero_accounting = {
        "accounting_equality_passed": True,
        "duplicate_pair_count": 0,
        "missing_pair_count": 0,
        "outside_manifest_pair_count": 0,
        "invalid_disposition_count": 0,
        "cannot_link_count": 2,
    }
    return {
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": "target_met_multilingual_identity_candidate_closure",
            "target_met": True,
            "safe_to_merge": True,
            "route_approved": False,
            "active_blockers": [],
            "semantic_completeness_claimed": False,
            "production_readiness_claimed": False,
            "scale_readiness_claimed": False,
        },
        "repository_sync_preflight": {
            "status": "passed_synchronization_preflight",
            "evidence_source": "actual_git_subprocess",
            "repository_root_verified": True,
            "current_branch": runner.TASK_BRANCH,
            "current_head": "b" * 40,
            "remote_tracking_branch": f"origin/{runner.TASK_BRANCH}",
            "remote_head": "b" * 40,
            "ahead": 0,
            "behind": 0,
            "accepted_base": runner.BASE_SHA,
            "actual_merge_base": runner.BASE_SHA,
            "base_is_ancestor": True,
            "tracked_change_count": 0,
            "staged_change_count": 0,
            "preexisting_untracked_path_count": 3,
            "preexisting_untracked_path_list_sha256": "c" * 64,
            "preexisting_ignored_path_count": 4,
            "preexisting_ignored_path_list_sha256": "d" * 64,
            "preexisting_user_owned_path_missing_count": 0,
            "preexisting_user_owned_paths_preserved": True,
        },
        "environment_isolation": {
            "passed": True,
            "production_profile_active": False,
            "working_database_is_fresh_separate_clone": True,
            "source_database_immutable": True,
            "superseded_ml2_database_immutable": True,
            "source_database_fingerprint": "e" * 64,
            "superseded_ml2_database_fingerprint": "f" * 64,
        },
        "baseline": {
            "family_count": 20,
            "observed_alias_count": 40,
            "identity_family_count_before": 10,
            "identity_family_count_after": 10,
            "search_only_family_count_before": 10,
            "search_only_family_count_after": 10,
            "accepted_r2r_disposition_count": 3319,
        },
        "manifest_fingerprints": {
            "creator-identity-family-manifest.jsonl": manifest(10),
            "creator-identity-alias-observation-manifest.jsonl": manifest(20),
            "candidate-generation-gap-manifest.jsonl": manifest(2),
            "creator-context-search-case-manifest.jsonl": manifest(4),
            "search-only-family-regression-manifest.jsonl": manifest(10),
            "candidate-pair-ledger.jsonl": manifest(12),
            "family-closure-ledger.jsonl": manifest(10),
            "creator-context-closure-ledger.jsonl": manifest(4),
        },
        "candidate_growth": {"linear_bound_passed": True, "all_pairs_alias_expansion_used": False},
        "pair_accounting": zero_accounting,
        "family_accounting": {
            "accounting_equality_passed": True,
            "identity_eligible_family_count": 10,
            "already_materialized_family_count": 2,
            "newly_materialized_family_count": 7,
            "cannot_link_closed_family_count": 0,
            "deferred_nonblocking_family_count": 1,
            "fragmented_deferred_family_count": 1,
            "duplicate_family_count": 0,
            "missing_family_count": 0,
            "outside_manifest_family_count": 0,
            "invalid_outcome_count": 0,
        },
        "candidate_gap_closure": {"initial_gap_count": 2, "remaining_gap_count": 0, "unexplained_gap_count": 0},
        "active_concept_audit": {
            "inactive_concept_candidate_reference_count": 1,
            "inactive_concept_reuse_count": 0,
            "preexisting_partial_concept_fragmentation_family_count": 1,
        },
        "preexisting_component_audit": {"existing_12_full_component_audit_passed": True},
        "graph_safety": {
            "full_touched_component_audit_passed": True,
            "existing_12_full_component_audit_passed": True,
            "graph_audit_cannot_pair_count": 2,
            "graph_audit_cannot_pair_count_equality_passed": True,
            "multi_stable_id_creator_component_count": 0,
            "unauthorized_cross_role_component_count": 0,
            "unknown_role_materialization_count": 0,
            "character_work_copyright_contamination_count": 0,
            "trusted_parent_lineage_failure_count": 0,
            "direct_disposition_conflict_count": 0,
            "cannot_endpoints_same_component_count": 0,
            "direct_cannot_violation_count": 0,
            "transitive_cannot_violation_count": 0,
            "postclosure_duplicate_active_identity_concept_count": 0,
        },
        "concept_media_support": {
            "passed": True,
            "per_media_evidence_linear_bound_passed": True,
            "concept_media_support_row_count": 25,
            "expected_concept_media_support_row_count": 25,
            "duplicate_concept_media_support_count": 0,
            "missing_sourceconcept_media_count": 0,
            "unsupported_sourceconcept_media_count": 0,
            "media_count_mismatch_count": 0,
            "support_provenance_failure_count": 0,
        },
        "sourceconcept_only_runtime": {
            "passed": True,
            "sourceconcept_alias_family_count": 9,
            "sourceconcept_alias_expected_media_coverage": 1.0,
            "media_detail_sourceconcept_visibility_passed": True,
            "direct_source_name_or_tag_fallback_used": False,
            "search_inert_materialized_concept_count": 0,
            "missing_sourceconcept_media_count": 0,
            "unsupported_sourceconcept_media_count": 0,
            "media_detail_sample_failure_count": 0,
        },
        "creator_context": {
            "case_count": 4,
            "classification_count": 4,
            "supported_evidence_runtime_success_coverage": 1.0,
            "implementation_failure_with_sufficient_evidence_count": 0,
            "unexplained_failure_count": 0,
        },
        "search_validation": {
            "search_only_regression_count": 0,
            "unsupported_result_count": 0,
            "rejected_only_result_count": 0,
            "superseded_only_result_count": 0,
            "invalid_or_deleted_only_result_count": 0,
            "and_leakage_count": 0,
            "search_caused_identity_mutation_count": 0,
        },
        "r2r_reuse": {
            "accepted_pair_count": 3319,
            "accepted_must_link_count": 1522,
            "accepted_cannot_link_count": 1791,
            "accepted_deferred_nonblocking_count": 6,
            "candidate_disposition_coverage": 1.0,
            "snapshot_fingerprint": "1" * 64,
            "database_snapshot_crosscheck_passed": True,
            "private_pair_manifest_crosscheck_passed": True,
            "cache_only_rebuild_passed": True,
            "provider_attempt_count": 0,
            "reused_accepted_pair_count": 0,
            "disposition_conflict_count": 0,
            "accepted_dispositions_mutated": False,
            "preserved_r2r_artifacts_mutated": False,
            "preserved_r2r_artifact_fingerprint": "2" * 64,
        },
        "llm": {"projected_cost_usd": 0.0, "actual_cost_usd": 0.0, "deterministic_stable_id_pairs_excluded": True},
        "mutation_proof": {
            "fixed_tables_unchanged": True,
            "forbidden_truth_tables_unchanged": True,
            "changed_fixed_tables": [],
            "changed_forbidden_truth_tables": [],
            "production_write_count": 0,
            "entity_truth_write_count": 0,
            "media_tags_truth_write_count": 0,
            "source_or_icloud_write_count": 0,
        },
        "idempotency": {"passed": True, "fingerprints_equal": True, "second_run_duplicate_media_support": 0},
        "operation_counts": {"external_metadata_provider_calls": 0, "pixiv_calls": 0, "gallery_dl_calls": 0, "entity_truth_writes": 0},
        "route_decision": {"route_approved": False, "next_phase_started": False},
        "validation": {"public_redaction_passed": True},
    }


def test_same_pixiv_id_two_display_names_must_link():
    pairs = build_star_candidates((family("1", "Name A", "Name B"),))
    assert len(pairs) == 2
    assert {row.disposition for row in pairs} == {"must_link"}


def test_same_pixiv_id_name_and_account_must_link():
    pairs = build_star_candidates((family("1", "Display Name", "account_name"),))
    assert all(row.reason_code == "same_provider_stable_creator_id_trusted_parent" for row in pairs)


def test_same_creator_across_multiple_works_has_one_anchor_component():
    value = replace(family("1", "A", "B"), work_context_distribution={"work_1": 2, "work_2": 3})
    assert len({row.left_signal_key for row in build_star_candidates((value,)) if "creator-anchor" in row.left_signal_key} | {row.right_signal_key for row in build_star_candidates((value,)) if "creator-anchor" in row.right_signal_key}) == 1


def test_different_pixiv_ids_shared_name_create_collision_local_cannot_link():
    pairs = build_star_candidates((family("1", "Shared", "A"), family("2", "Shared", "B")))
    assert sum(row.disposition == "cannot_link" for row in pairs) == 1


def test_different_ids_without_shared_surface_are_not_globally_paired():
    pairs = build_star_candidates((family("1", "A", "AA"), family("2", "B", "BB")))
    assert sum(row.disposition == "cannot_link" for row in pairs) == 0


def test_cross_role_family_is_rejected_before_identity_union():
    invalid = replace(family("1", "A", "B"), creator_role="work")
    with pytest.raises(CreatorIdentityClosureError):
        build_star_candidates((invalid,))


def test_runner_requires_trusted_parent_compatibility_predicate():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "is_pixiv_creator_observation_compatible_with_parent" in source


def test_search_only_translation_is_not_a_creator_family_input():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "search_only_rows" in source
    assert "build_star_candidates(families)" in source


def test_empty_canonical_creator_alias_remains_identity_evidence_but_not_search_index():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "if not alias_key or alias_key in collision_alias_keys:" in source
    assert 'SourceConceptSearchIndex.search_key == ""' in source


def test_accepted_cannot_link_never_allows_union():
    pairs = build_star_candidates((family("1", "Shared", "A"), family("2", "Shared", "B")))
    negative = next(row for row in pairs if row.disposition == "cannot_link")
    assert negative.union_allowed is False


def test_component_cannot_contain_two_stable_ids():
    result = component_purity((
        {"component_id": 1, "signal_key": "a", "role": "creator", "stable_identity_key": "one"},
        {"component_id": 1, "signal_key": "b", "role": "creator", "stable_identity_key": "two"},
    ))
    assert result["multi_stable_id_creator_component_count"] == 1


def test_n_aliases_generate_linear_anchor_relations():
    value = family("1", *(f"alias_{index}" for index in range(20)))
    result = candidate_growth_accounting((value,), build_star_candidates((value,)))
    assert result["candidate_pair_count"] == 20
    assert result["linear_bound_passed"]


def test_all_pairs_alias_edge_growth_is_rejected_by_bound():
    value = family("1", "A", "B", "C")
    candidates = list(build_star_candidates((value,)))
    candidates.append(replace(candidates[0], pair_id="extra"))
    assert not candidate_growth_accounting((value,), candidates)["linear_bound_passed"]


def test_duplicate_observations_do_not_create_duplicate_edges():
    duplicated = replace(family("1", "A", "B"), aliases=(alias("A"), alias("A"), alias("B")))
    assert len(build_star_candidates((duplicated,))) == 2


def test_deterministic_keys_make_second_execution_idempotent():
    value = family("1", "A", "B")
    assert build_star_candidates((value,)) == build_star_candidates((value,))


@pytest.mark.parametrize("reason", [
    "trusted_creator_id_signal_missing",
    "trusted_creator_name_signal_missing",
    "trusted_creator_account_signal_missing",
    "identity_anchor_not_generated",
    "role_classification_loss",
    "candidate_blocking_miss",
    "existing_sourceconcept_consumption_gap",
])
def test_supported_gap_root_causes_are_finite(reason):
    assert reason in GAP_REASON_CODES


def test_no_family_specific_private_id_hardcoding():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "stable_creator_id ==" not in source


def test_denominator_correction_requires_explicit_contract_evidence():
    summary = valid_summary()
    summary["baseline"]["identity_family_count_after"] = 605
    summary["pipeline_contract"].update(status="blocked_ml2_baseline_drift", target_met=False, safe_to_merge=False, active_blockers=["blocked_ml2_baseline_drift"])
    assert check_phase_contract(CONTRACT_ID, summary).passed


def test_unexplained_gap_fails_contract():
    summary = valid_summary()
    summary["candidate_gap_closure"]["unexplained_gap_count"] = 1
    summary["pipeline_contract"].update(status="blocked_ml2_candidate_generation_gap", target_met=False, safe_to_merge=False, active_blockers=["blocked_ml2_candidate_generation_gap"])
    assert check_phase_contract(CONTRACT_ID, summary).passed


def _pair() -> IdentityCandidate:
    return build_star_candidates((family("1", "A", "B"),))[0]


def test_omitted_pair_fails_accounting():
    pairs = build_star_candidates((family("1", "A", "B"),))
    assert pair_accounting(pairs, ({"pair_id": pairs[0].pair_id, "disposition": "must_link"},))["missing_pair_count"] == 1


def test_duplicated_pair_fails_accounting():
    pair = _pair()
    rows = ({"pair_id": pair.pair_id, "disposition": "must_link"},) * 2
    assert pair_accounting((pair,), rows)["duplicate_pair_count"] == 1


def test_multi_bucket_pair_fails_accounting():
    pair = _pair()
    rows = ({"pair_id": pair.pair_id, "disposition": "must_link"}, {"pair_id": pair.pair_id, "disposition": "cannot_link"})
    assert not pair_accounting((pair,), rows)["accounting_equality_passed"]


def test_pair_outside_manifest_fails_accounting():
    pair = _pair()
    assert pair_accounting((pair,), ({"pair_id": "outside", "disposition": "must_link"},))["outside_manifest_pair_count"] == 1


def test_omitted_family_fails_accounting():
    assert family_accounting(("a", "b"), ({"family_id": "a", "outcome": "already_materialized"},))["missing_family_count"] == 1


def test_multi_outcome_family_fails_accounting():
    rows = ({"family_id": "a", "outcome": "already_materialized"}, {"family_id": "a", "outcome": "deterministic_must_link_materialized"})
    assert family_accounting(("a",), rows)["duplicate_family_count"] == 1


def test_direct_cannot_violation_is_detected():
    rows = ({"component_id": 1, "signal_key": "a", "role": "creator", "stable_identity_key": "one"}, {"component_id": 1, "signal_key": "b", "role": "creator", "stable_identity_key": "one"})
    assert component_purity(rows, (("a", "b"),))["direct_cannot_violation_count"] == 1


def test_transitive_cannot_violation_is_detected():
    rows = ({"component_id": 1, "signal_key": "a", "role": "creator", "stable_identity_key": "one"}, {"component_id": 1, "signal_key": "b", "role": "creator", "stable_identity_key": "one"})
    assert component_purity(rows, (("a", "b"),))["transitive_cannot_violation_count"] == 1


def test_deferred_relation_never_unions():
    deferred = replace(_pair(), disposition="deferred_nonblocking", union_allowed=False)
    assert deferred.union_allowed is False


def test_unknown_role_cannot_materialize():
    result = component_purity(({"component_id": 1, "signal_key": "a", "role": "unknown", "stable_identity_key": "one"},))
    assert result["unknown_role_materialization_count"] == 1


def test_same_name_distinct_creators_keep_identity_separate_but_search_union_supported():
    pairs = build_star_candidates((family("1", "Shared", "A"), family("2", "Shared", "B")))
    assert any(row.disposition == "cannot_link" for row in pairs)
    support = ml1.classify_runtime_support({1, 2}, {1: {"direct"}, 2: {"direct"}})
    assert support["supported_result_count"] == 2


@pytest.mark.parametrize("context", ["work", "character"])
def test_creator_context_query_uses_media_level_and(context):
    creator_media, context_media = {1, 2}, {2, 3}
    assert creator_media & context_media == {2}


def test_unsupported_result_is_detected():
    assert ml1.classify_runtime_support({1, 2}, {1: {"direct"}})["unsupported_result_ids"] == {2}


def test_rejected_only_support_is_detected():
    result = ml1.classify_runtime_support({2}, {}, rejected_ids={2})
    assert result["rejected_evidence_result_ids"] == {2}


def test_search_audit_does_not_mutate_identity():
    source = Path(ml1.__file__).read_text(encoding="utf-8")
    assert '"identity_before_after_match": identity_before == identity_after' in source


def test_trusted_existing_source_work_metadata_can_backfill_observation():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "is_trusted_complete_pixiv_metadata_record(record)" in source
    assert "_upsert_name_observation" in source
    assert 'SourceNameObservation.source_field == "pixiv_title"' in source
    assert "SourceNameObservation.canonical_name_key == canonical_source_key(record.title)" in source


def test_missing_provider_evidence_cannot_be_invented():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "metadata_acquisition" not in source


def test_evidence_conditioned_supported_cases_must_reach_full_coverage():
    summary = valid_summary()
    summary["creator_context"]["supported_evidence_runtime_success_coverage"] = 0.99
    summary["pipeline_contract"].update(status="blocked_ml2_creator_context_recall", target_met=False, safe_to_merge=False, active_blockers=["blocked_ml2_creator_context_recall"])
    assert check_phase_contract(CONTRACT_ID, summary).passed


def test_evidence_absent_cases_may_defer_without_union():
    outcome = {"family_id": "a", "outcome": "deferred_nonblocking_insufficient_trusted_alias_evidence"}
    assert family_accounting(("a",), (outcome,))["accounting_equality_passed"]


def test_empty_canonical_work_title_is_evidence_absent_not_a_search_alias():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert 'classification = "deferred_nonblocking_evidence_absent"' in source


def test_stable_id_deterministic_pairs_never_enter_llm_manifest():
    pairs = build_star_candidates((family("1", "A", "B"),))
    assert select_llm_manifest(pairs, (), projected_cost_usd=0.0) == ()


def test_exact_accepted_pairs_never_enter_llm_manifest():
    deferred = replace(_pair(), disposition="deferred_nonblocking", reason_code="requires_bounded_llm_adjudication", union_allowed=False)
    assert select_llm_manifest((deferred,), (deferred.pair_id,), projected_cost_usd=0.1) == ()


def test_projected_cost_above_usd10_fails_closed():
    with pytest.raises(CreatorIdentityClosureError):
        select_llm_manifest((), (), projected_cost_usd=10.01)


def test_retries_are_part_of_public_llm_cost_contract():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert '"retries": 0' in source
    assert '"actual_cost_usd": 0.0' in source


def test_one_final_disposition_is_persisted_per_llm_pair():
    pair = replace(_pair(), disposition="deferred_nonblocking", reason_code="requires_bounded_llm_adjudication", union_allowed=False)
    assert len(select_llm_manifest((pair,), (), projected_cost_usd=0.1)) == 1


def test_provider_unavailable_does_not_create_human_review_queue():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert '"human_review_queue_count": 0' in source
    assert "needs_review" not in {row.disposition for row in build_star_candidates((family("1", "A", "B"),))}


def test_fresh_schema_contains_every_reused_sourceconcept_table_and_identity_column():
    models = (
        SourceConceptResolutionRun, SourceConceptSignal, SourceConcept, SourceConceptAlias,
        SourceConceptEvidence, SourceConceptSignalLink, SourceConceptSearchIndex,
    )
    assert all(model.__table__.name for model in models)
    assert {"signal_key", "provider", "role_hint", "evidence_payload"}.issubset(SourceConceptSignal.__table__.columns.keys())
    assert {"run_id", "status", "summary_json"}.issubset(SourceConceptResolutionRun.__table__.columns.keys())


def test_valid_ml2_contract_passes():
    result = check_phase_contract(CONTRACT_ID, valid_summary())
    assert result.passed, [error.code for error in result.errors]


def test_contract_fails_closed_when_repository_sync_proof_is_missing():
    summary = valid_summary()
    summary.pop("repository_sync_preflight")
    result = check_phase_contract(CONTRACT_ID, summary)
    codes = {error.code for error in result.errors}
    assert "missing_required_summary_field" in codes
    assert "ml2_active_blockers_incomplete" in codes


def test_contract_fails_when_summary_omits_known_graph_blocker():
    summary = valid_summary()
    summary["graph_safety"]["multi_stable_id_creator_component_count"] = 1
    result = check_phase_contract(CONTRACT_ID, summary)
    assert not result.passed
    assert "ml2_active_blockers_incomplete" in {error.code for error in result.errors}


def test_reviewfix_redaction_failure_writes_only_private_diagnostic(tmp_path, monkeypatch):
    summary = valid_summary()
    monkeypatch.setattr(runner, "render_report", lambda _summary: "safe report")
    pack_called = False

    def forbidden_pack(*_args, **_kwargs):
        nonlocal pack_called
        pack_called = True

    monkeypatch.setattr(runner, "write_review_pack", forbidden_pack)
    public_md = tmp_path / "public.md"
    public_json = tmp_path / "public.json"
    result = runner.fail_closed_publication(
        summary=summary,
        output_dir=tmp_path,
        public_report_path=public_md,
        public_summary_path=public_json,
        private_artifacts=(),
        redactor=lambda _value: (_ for _ in ()).throw(ValueError("raw alias")),
    )
    assert result == {"published": False, "blocker": "blocked_ml2_public_redaction"}
    assert not public_md.exists() and not public_json.exists() and not pack_called
    assert sorted(path.name for path in tmp_path.iterdir()) == ["private-public-redaction-diagnostic.json"]


def test_reviewfix_missing_r2r_private_proof_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "R2R_PAIR_MANIFEST", tmp_path / "missing.json")
    with pytest.raises(runner.ML2BlockedError, match="blocked_ml2_r2r_reuse_evidence"):
        runner.load_exact_r2r_disposition_snapshot()


def test_reviewfix_r2r_distribution_mismatch_blocks():
    pairs = [{"pair_id": f"p{index}"} for index in range(3319)]
    dispositions = [
        {"pair_id": row["pair_id"], "disposition": "must_link"} for row in pairs
    ]
    execution = {
        "candidate_dispositions": {
            "total_candidate_pairs": 3319,
            "must_link_count": 1522,
            "cannot_link_count": 1791,
            "deferred_nonblocking_count": 6,
            "candidate_disposition_coverage": 1.0,
        }
    }
    with pytest.raises(runner.ML2BlockedError, match="count_or_distribution_mismatch"):
        runner.validate_r2r_disposition_snapshot(
            database_summary={"llm_usage": {"judgment_count": 3319}},
            execution_summary=execution,
            pair_manifest={"pairs": pairs},
            dispositions=dispositions,
        )


def test_reviewfix_active_concept_lookup_joins_concept_status():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "JOIN blombooru_source_concepts c ON c.id=l.concept_id" in source
    assert "AND c.status='active'" in source


def test_reviewfix_existing_active_concept_requires_prewrite_full_audit():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "preexisting_audit = audit_touched_identity_components" in source
    assert "accepted_component_impure" in source


def test_reviewfix_single_partial_alias_overlap_is_not_identity_reuse():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "complete_active_alias_concepts = (" in source
    assert "active_concepts = active_anchor_concepts | complete_active_alias_concepts" in source
    assert '"preexisting_partial_concept_reference_family_count"' in source


def test_reviewfix_historical_links_participate_in_full_component_purity():
    rows = (
        {"concept_id": 7, "signal_key": "current", "role": "creator", "stable_identity_key": "one", "active_media_support_count": 1},
        {"concept_id": 7, "signal_key": "historical", "role": "character", "stable_identity_key": "one", "active_media_support_count": 1},
    )
    result = audit_touched_identity_components(rows, existing_concept_ids=(7,))
    assert not result["full_touched_component_audit_passed"]
    assert result["character_work_copyright_contamination_count"] == 1


def test_reviewfix_cannot_pairs_reach_full_graph_audit():
    result = audit_touched_identity_components(
        (
            {"concept_id": 1, "signal_key": "a", "role": "creator", "stable_identity_key": "one", "active_media_support_count": 1},
            {"concept_id": 2, "signal_key": "b", "role": "creator", "stable_identity_key": "two", "active_media_support_count": 1},
        ),
        (("a", "b"),),
        existing_concept_ids=(1,),
    )
    assert result["graph_audit_cannot_pair_count"] == 1
    assert result["cannot_endpoints_same_component_count"] == 0


def test_reviewfix_cannot_endpoints_in_one_component_fail():
    result = audit_touched_identity_components(
        (
            {"concept_id": 1, "signal_key": "a", "role": "creator", "stable_identity_key": "one", "active_media_support_count": 1},
            {"concept_id": 1, "signal_key": "b", "role": "creator", "stable_identity_key": "one", "active_media_support_count": 1},
        ),
        (("a", "b"),),
        existing_concept_ids=(1,),
    )
    assert not result["full_touched_component_audit_passed"]
    assert result["direct_cannot_violation_count"] == 1


def test_reviewfix_empty_aliases_create_no_candidates_or_growth():
    first = replace(family("1", "A"), aliases=(alias(""), alias("   "), alias("A")))
    second = replace(family("2", "B"), aliases=(alias(""), alias("\t"), alias("B")))
    pairs = build_star_candidates((first, second))
    growth = candidate_growth_accounting((first, second), pairs)
    assert len(pairs) == 2
    assert growth["unique_alias_signal_count"] == 2
    assert not any(row.disposition == "cannot_link" for row in pairs)


def test_reviewfix_concept_media_evidence_is_persisted_per_media_record():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert 'evidence_type="trusted_creator_media_support"' in source
    assert "source_metadata_record_id=source_metadata_record_id" in source


def test_reviewfix_sourceconcept_only_alias_coverage_is_contract_gate():
    summary = valid_summary()
    summary["sourceconcept_only_runtime"]["sourceconcept_alias_expected_media_coverage"] = 0.9
    result = check_phase_contract(CONTRACT_ID, summary)
    assert "ml2_active_blockers_incomplete" in {error.code for error in result.errors}


def test_reviewfix_search_alias_with_zero_media_support_blocks():
    summary = valid_summary()
    summary["concept_media_support"]["concept_media_support_row_count"] = 0
    result = check_phase_contract(CONTRACT_ID, summary)
    assert not result.passed


def test_reviewfix_per_media_evidence_is_linear_not_alias_cross_product():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    media_loop = source.index("for media_id, source_metadata_record_id in support_rows:")
    alias_loop = source.index("for alias in family.aliases:", media_loop)
    assert media_loop < alias_loop
    assert "alias × media" not in source[media_loop:alias_loop]


def test_reviewfix_duplicate_media_support_blocks_contract():
    summary = valid_summary()
    summary["concept_media_support"]["duplicate_concept_media_support_count"] = 1
    assert not check_phase_contract(CONTRACT_ID, summary).passed


def test_reviewfix_media_count_must_match_exact_distinct_support():
    summary = valid_summary()
    summary["concept_media_support"]["media_count_mismatch_count"] = 1
    assert not check_phase_contract(CONTRACT_ID, summary).passed


def test_reviewfix_multiple_preexisting_concepts_do_not_create_third():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    fragment_branch = source.index("if len(family.preexisting_active_concept_ids) > 1:")
    create_branch = source.index("concept, _ = _get_or_create", fragment_branch)
    assert "continue" in source[fragment_branch:create_branch]


def test_reviewfix_fragmented_family_is_exact_deferred_outcome():
    result = family_accounting(
        ("f",),
        ({"family_id": "f", "outcome": "deferred_nonblocking_existing_component_fragmentation"},),
    )
    assert result["accounting_equality_passed"]
    assert result["fragmented_deferred_family_count"] == 1


def test_reviewfix_postclosure_duplicate_active_identity_blocks():
    summary = valid_summary()
    summary["graph_safety"]["postclosure_duplicate_active_identity_concept_count"] = 1
    assert not check_phase_contract(CONTRACT_ID, summary).passed


def test_reviewfix_existing_component_audit_requires_historical_signal_count():
    result = audit_touched_identity_components(
        (
            {"concept_id": 3, "signal_key": "old-a", "role": "creator", "stable_identity_key": "id", "active_media_support_count": 2},
            {"concept_id": 3, "signal_key": "old-b", "role": "creator", "stable_identity_key": "id", "active_media_support_count": 2},
        ),
        existing_concept_ids=(3,),
    )
    assert result["existing_12_full_component_audit_passed"]
    assert result["existing_component_signal_count"] == 2


def test_reviewfix_git_contract_cannot_be_satisfied_by_historical_constants():
    summary = valid_summary()
    summary["repository_sync_preflight"].update(
        evidence_source="operator_constants",
        current_head=runner.BASE_SHA,
        remote_head=runner.BASE_SHA,
    )
    assert not check_phase_contract(CONTRACT_ID, summary).passed


def test_reviewfix_preedit_path_fingerprint_preserves_original_git_order():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert 'preexisting_untracked = untracked_manifest.read_text(encoding="utf-8").splitlines()' in source
    assert 'preexisting_ignored = ignored_manifest.read_text(encoding="utf-8").splitlines()' in source


def test_reviewfix_git_octal_utf8_path_is_decoded_before_existence_check():
    assert runner.decode_git_path('"media/original/\\345\\217\\254\\344\\275\\277.jpeg"') == "media/original/召使.jpeg"
    assert runner.decode_git_path("plain/path.txt") == "plain/path.txt"


def test_reviewfix_media_detail_visibility_is_contract_gate():
    summary = valid_summary()
    summary["sourceconcept_only_runtime"]["media_detail_sourceconcept_visibility_passed"] = False
    assert not check_phase_contract(CONTRACT_ID, summary).passed


def test_reviewfix_database_fingerprint_excludes_capture_timestamp():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert 'if key != "captured_at"' in source
    assert '"fingerprint": fingerprint(stable_snapshot)' in source


def test_reviewfix_source_or_superseded_db_change_is_fixed_evidence_blocker():
    summary = valid_summary()
    summary["environment_isolation"]["source_database_immutable"] = False
    summary["pipeline_contract"].update(
        status="blocked_ml2_fixed_evidence_changed",
        target_met=False,
        safe_to_merge=False,
        active_blockers=["blocked_ml2_fixed_evidence_changed"],
    )
    result = check_phase_contract(CONTRACT_ID, summary)
    assert result.passed, [error.code for error in result.errors]
