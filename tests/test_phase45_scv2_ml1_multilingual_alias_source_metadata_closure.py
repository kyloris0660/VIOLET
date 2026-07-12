"""Gate-0 contract and durable-semantics guards for SCV2-ML1."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.phase_contracts.contract_checks import check_phase_contract
from scripts.phase_contracts.contract_registry import get_contract


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ID = "ml1_multilingual_alias_source_metadata_closure_contract_v1"


def _summary(*, status: str = "target_met_multilingual_alias_source_metadata_closure") -> dict:
    target = status == "target_met_multilingual_alias_source_metadata_closure"
    return {
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "claims": {"target_met": target, "route_approved": False, "safe_to_merge": False},
        },
        "document_semantics": {
            "passed": True,
            "durable_policy_created": True,
            "r2r_interpretation_erratum_present": True,
            "old_one_name_one_family_interpretation_superseded": True,
            "identity_union_is_search_result_union": False,
            "shared_bare_name_results_are_legitimate_when_supported": True,
            "cannot_link_globally_suppresses_direct_matches": False,
            "and_search_is_media_level_intersection": True,
            "current_phase_is_ml1": True,
            "contradictory_statement_count": 0,
        },
        "environment_isolation": {
            "passed": True,
            "violet_env_test": True,
            "accepted_r2r_database_immutable": True,
            "source_database_immutable": True,
            "production_profile_active": False,
            "production_write_attempted": False,
            "network_disabled": True,
        },
        "pixiv_accounting": {
            "candidate_media_count": 3,
            "accounted_media_count": 3,
            "metadata_present_complete_media_count": 2,
            "terminal_remote_unavailable_media_count": 1,
            "retryable_failure_media_count": 0,
            "parse_or_identity_failure_media_count": 0,
            "not_attempted_media_count": 0,
            "unexplained_missing_media_count": 0,
            "candidate_distinct_work_count": 2,
            "accounted_distinct_work_count": 2,
            "candidate_media_accounting_coverage": 1.0,
            "candidate_work_accounting_coverage": 1.0,
            "normal_retrievable_missing_media_count": 0,
            "work_id_mismatch_media_count": 0,
            "incremental_acquisition_required": False,
        },
        "creator_metadata": {
            "available_creator_fields_accounting_coverage": 1.0,
            "stable_creator_id_preservation_coverage": 1.0,
            "observed_creator_search_support_coverage": 1.0,
            "silently_dropped_creator_field_count": 0,
            "creator_role_misclassification_count": 0,
            "creator_search_passed": True,
            "creator_and_character_work_intersection_passed": True,
        },
        "multilingual_benchmark": {
            "observed_alias_accounting_coverage": 1.0,
            "signal_generation_coverage": 1.0,
            "candidate_family_connectivity_coverage": 1.0,
            "adjudication_coverage": 1.0,
            "search_equivalence_coverage": 1.0,
            "and_work_equivalence_coverage": 1.0,
            "unexplained_multilingual_split_count": 0,
            "candidate_not_generated_count": 0,
            "role_or_context_loss_count": 0,
            "human_review_queue_generated": False,
        },
        "candidate_generation": {
            "all_misses_classified": True,
            "unresolved_candidate_generation_count": 0,
            "representative_edge_semantic_ranking_passed": True,
            "fresh_old_schema_migration_passed": True,
            "new_pair_manifest_count": 0,
            "llm_approval_required": False,
        },
        "search_semantics": {
            "runtime_application_path_used": True,
            "shared_name_union_passed": True,
            "unsupported_result_media_count": 0,
            "rejected_evidence_result_count": 0,
            "identity_union_from_search_count": 0,
            "and_constraint_leakage_count": 0,
            "direct_or_accepted_alias_support_coverage": 1.0,
            "multilingual_and_work_equivalence_coverage": 1.0,
            "creator_and_character_work_accuracy": 1.0,
        },
        "fixed_evidence_proof": {
            "present": True,
            "before_after_match": True,
            "accepted_r2r_dispositions_reused": True,
            "accepted_r2r_disposition_count": 3319,
            "forbidden_truth_content_unchanged": True,
            "changed_fixed_tables": [],
            "changed_forbidden_truth_tables": [],
        },
        "operation_counts": {
            key: 0
            for key in (
                "gallery_dl_calls", "pixiv_provider_calls", "provider_metadata_acquisition_calls",
                "llm_provider_calls", "fallback_provider_calls", "accepted_r2r_pair_readjudications",
                "fixed_evidence_mutations", "truth_path_writes", "production_writes", "media_imports",
                "ai_tagging_calls", "classification_calls", "localization_calls", "entity_writes",
            )
        },
        "graph_invariants": {
            "review_or_deferred_identity_union_count": 0,
            "direct_cannot_violation_count": 0,
            "transitive_cannot_violation_count": 0,
            "unauthorized_unknown_role_materialization_count": 0,
            "identity_changes_caused_by_search_count": 0,
        },
        "public_redaction": {"passed": True},
        "review_pack": {
            "generated": True, "manifest_present": True, "checksums_present": True,
            "integrity_passed": True, "not_committed": True,
        },
        "route_authorization": {
            "pixiv_acquisition_authorized": False, "llm_execution_authorized": False,
            "provider_2_authorized": False, "scale_up_authorized": False,
            "entity_bridge_authorized": False, "production_authorized": False,
            "full_library_execution_authorized": False, "truth_promotion_authorized": False,
        },
    }


def test_ml1_contract_registered_and_target_proof_passes() -> None:
    assert get_contract(CONTRACT_ID).contract_id == CONTRACT_ID
    result = check_phase_contract(CONTRACT_ID, _summary())
    assert result.passed, [finding.to_dict() for finding in result.errors]


@pytest.mark.parametrize(
    ("section", "key", "value", "code"),
    [
        ("document_semantics", "identity_union_is_search_result_union", True, "ml1_document_semantics_incomplete"),
        ("pixiv_accounting", "accounted_media_count", 2, "ml1_pixiv_media_accounting_incomplete"),
        ("creator_metadata", "silently_dropped_creator_field_count", 1, "ml1_creator_target_failed"),
        ("multilingual_benchmark", "candidate_not_generated_count", 1, "ml1_multilingual_target_failed"),
        ("search_semantics", "and_constraint_leakage_count", 1, "ml1_search_semantics_target_failed"),
        ("operation_counts", "gallery_dl_calls", 1, "ml1_forbidden_operation_nonzero_or_missing"),
    ],
)
def test_ml1_contract_fails_closed(section: str, key: str, value: object, code: str) -> None:
    summary = deepcopy(_summary())
    summary[section][key] = value
    result = check_phase_contract(CONTRACT_ID, summary)
    assert not result.passed
    assert code in {finding.code for finding in result.errors}


def test_ml1_pixiv_acquisition_block_requires_exact_projection() -> None:
    summary = _summary(status="blocked_pixiv_incremental_acquisition_approval_required")
    summary["pixiv_accounting"].update(
        retryable_failure_media_count=1,
        metadata_present_complete_media_count=1,
        incremental_acquisition_required=True,
        projected_gallery_dl_request_count=1,
        authentication_requirements_present=True,
        rate_limit_plan_present=True,
        checkpoint_resume_plan_present=True,
    )
    result = check_phase_contract(CONTRACT_ID, summary)
    assert result.passed, [finding.to_dict() for finding in result.errors]
    assert result.target_met_claimed is False


def test_durable_documents_encode_corrected_search_semantics() -> None:
    policy = (ROOT / "docs/source-concept-tag-search-semantics.md").read_text(encoding="utf-8")
    handoff = (ROOT / "docs/current-handoff.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "docs/roadmap/current-mainline-roadmap.md").read_text(encoding="utf-8")
    report = (ROOT / "docs/reports/phase-4.5-scv2-r2r-autonomous-recall-search-closure.md").read_text(encoding="utf-8")
    summary = json.loads((ROOT / "docs/reports/phase-4.5-scv2-r2r-autonomous-recall-search-closure-summary.json").read_text(encoding="utf-8"))

    combined = "\n".join((policy, handoff, roadmap))
    assert "Search-result union is not identity union" in combined
    assert "media-level AND intersection" in combined
    assert "SCV2-ML1" in handoff and "SCV2-ML1" in roadmap
    assert "Interpretation erratum" in report
    erratum = summary["search_semantics_interpretation_erratum"]
    assert erratum["old_interpretation_superseded"] is True
    assert erratum["historical_numeric_fields_preserved"] is True
    assert summary["search_benchmark"]["false_broad_union_indicator_count"] == 9186
    assert summary["search_benchmark"]["cannot_linked_search_contamination_count"] == 8768
