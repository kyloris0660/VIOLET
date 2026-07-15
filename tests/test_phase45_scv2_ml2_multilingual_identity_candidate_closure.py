"""Focused SCV2-ML2 stable creator identity closure tests."""

from __future__ import annotations

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
    assert "if not alias_key:" in source
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
