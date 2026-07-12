"""Gate-0 contract and durable-semantics guards for SCV2-ML1."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.database import Base, migrate_add_source_concept_fallback_search_index
from app.models import TagTranslation
from app.utils.search_parser import parse_search_query
from scripts.phase_contracts.contract_checks import check_phase_contract
from scripts.phase_contracts.contract_registry import get_contract
from scripts import run_phase45_scv2_r2r_autonomous_recall_search_closure as r2r_runner
from scripts import run_phase45_scv2_ml1_multilingual_alias_source_metadata_closure as ml1_runner


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ID = "ml1_multilingual_alias_source_metadata_closure_contract_v1"


def _summary(*, status: str = "target_met_multilingual_alias_source_metadata_closure") -> dict:
    target = status == "target_met_multilingual_alias_source_metadata_closure"
    summary = {
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "claims": {"target_met": target, "route_approved": False, "safe_to_merge": False},
            "active_blockers": [status] if status.startswith("blocked_") else [],
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
        "credential_safety": {
            "rotation_confirmation_present": status != "blocked_credential_rotation_confirmation_required",
            "external_call_attempted": False,
        },
        "owner_sample_validation": {
            "sample_generated": True,
            "sample_size": 60,
            "conflict_cases_exported": 0,
            "sample_manifest_fingerprint": "a" * 64,
            "validation_confirmed": False,
            "confirmation_env": None,
            "optional_stage_evidence": True,
            "runtime_gate_required": False,
            "normal_pipeline_human_dependency": False,
        },
        "pixiv_accounting": {
            "canonical_complete_statuses": ["accepted", "active", "metadata_complete", "observed"],
            "candidate_media_count": 3,
            "accounted_media_count": 3,
            "metadata_present_complete_media_count": 2,
            "metadata_pending_media_count": 0,
            "terminal_remote_unavailable_media_count": 1,
            "retryable_failure_media_count": 0,
            "parse_or_identity_failure_media_count": 0,
            "not_attempted_media_count": 0,
            "unexplained_missing_media_count": 0,
            "candidate_distinct_work_count": 2,
            "accounted_distinct_work_count": 2,
            "candidate_media_accounting_coverage": 1.0,
            "candidate_work_accounting_coverage": 1.0,
            "metadata_present_complete_work_count": 1,
            "terminal_remote_unavailable_work_count": 1,
            "pending_work_count": 0,
            "retryable_work_count": 0,
            "normalization_failed_work_count": 0,
            "missing_work_count": 0,
            "conflict_unresolved_work_count": 0,
            "no_durable_attempt_or_result_evidence_media_count": 0,
            "work_accounting_equality_holds": True,
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
            "actual_runtime_search_used": True,
            "synthetic_alias_media_propagation_used": False,
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
            "superseded_evidence_result_count": 0,
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
                "media_downloads",
                "llm_provider_calls", "fallback_provider_calls", "accepted_r2r_pair_readjudications",
                "fixed_evidence_mutations", "truth_path_writes", "production_writes", "media_imports",
                "ai_tagging_calls", "classification_calls", "localization_calls", "entity_writes",
            )
        },
        "acquisition_execution": {
            "acquisition_route_active": False,
            "acquisition_manifest_distinct_work_count": 0,
            "acquisition_manifest_fingerprint": "",
            "max_attempts_per_work": 3,
            "unique_work_ids_attempted_count": 0,
            "provider_request_attempt_count": 0,
            "gallery_dl_call_count": 0,
            "successful_work_count": 0,
            "terminal_work_count": 0,
            "retryable_work_count": 0,
            "skipped_complete_work_count": 0,
            "resumed_work_count": 0,
            "duplicate_unexpected_work_attempt_count": 0,
            "out_of_manifest_work_attempt_count": 0,
            "complete_work_reacquisition_count": 0,
            "max_observed_attempts_for_one_work": 0,
            "retry_attempts_attributable_to_manifest_work": True,
            "resume_only_remaining_open_works": True,
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
            "pixiv_acquisition_authorized": True, "llm_execution_authorized": False,
            "provider_2_authorized": False, "scale_up_authorized": False,
            "entity_bridge_authorized": False, "production_authorized": False,
            "full_library_execution_authorized": False, "truth_promotion_authorized": False,
        },
        "pixiv_metadata_foundation": {
            "current_stock_closed": True,
            "continuous_ingestion_gate_implemented": True,
            "complete_or_terminal_coverage": 1.0,
        },
        "llm_budget_policy": {"preauthorized": True},
    }
    return summary


def test_ml1_contract_registered_and_target_proof_passes() -> None:
    assert get_contract(CONTRACT_ID).contract_id == CONTRACT_ID
    result = check_phase_contract(CONTRACT_ID, _summary())
    assert result.passed, [finding.to_dict() for finding in result.errors]


def _configure_acquisition(summary: dict, *, manifest: int, attempts: int, unique: int, max_attempts: int = 3) -> None:
    summary["acquisition_execution"].update(
        acquisition_route_active=True,
        acquisition_manifest_distinct_work_count=manifest,
        acquisition_manifest_fingerprint="b" * 64,
        max_attempts_per_work=max_attempts,
        unique_work_ids_attempted_count=unique,
        provider_request_attempt_count=attempts,
        gallery_dl_call_count=attempts,
        max_observed_attempts_for_one_work=min(attempts, max_attempts),
    )
    for key in ("gallery_dl_calls", "pixiv_provider_calls", "provider_metadata_acquisition_calls"):
        summary["operation_counts"][key] = attempts
    summary["environment_isolation"]["network_disabled"] = attempts == 0


def test_provider_call_exact_upper_bound_passes() -> None:
    summary = _summary()
    _configure_acquisition(summary, manifest=2, attempts=6, unique=2)
    summary["acquisition_execution"]["max_observed_attempts_for_one_work"] = 3
    assert check_phase_contract(CONTRACT_ID, summary).passed


def test_provider_call_one_over_bound_fails() -> None:
    summary = _summary()
    _configure_acquisition(summary, manifest=2, attempts=7, unique=2)
    summary["acquisition_execution"]["max_observed_attempts_for_one_work"] = 3
    assert "ml1_acquisition_request_bound_exceeded" in {item.code for item in check_phase_contract(CONTRACT_ID, summary).errors}


def test_duplicate_retry_within_allowance_passes_but_beyond_fails() -> None:
    within = _summary()
    _configure_acquisition(within, manifest=1, attempts=3, unique=1)
    assert check_phase_contract(CONTRACT_ID, within).passed
    beyond = deepcopy(within)
    beyond["acquisition_execution"]["provider_request_attempt_count"] = 4
    beyond["acquisition_execution"]["gallery_dl_call_count"] = 4
    for key in ("gallery_dl_calls", "pixiv_provider_calls", "provider_metadata_acquisition_calls"):
        beyond["operation_counts"][key] = 4
    assert "ml1_acquisition_request_bound_exceeded" in {item.code for item in check_phase_contract(CONTRACT_ID, beyond).errors}


@pytest.mark.parametrize("key", ["out_of_manifest_work_attempt_count", "complete_work_reacquisition_count", "duplicate_unexpected_work_attempt_count"])
def test_provider_scope_violation_fails(key: str) -> None:
    summary = _summary()
    _configure_acquisition(summary, manifest=1, attempts=1, unique=1)
    summary["acquisition_execution"][key] = 1
    assert "ml1_acquisition_scope_violation" in {item.code for item in check_phase_contract(CONTRACT_ID, summary).errors}


def test_interrupted_checkpoint_resume_requires_remaining_open_proof() -> None:
    summary = _summary()
    _configure_acquisition(summary, manifest=3, attempts=1, unique=1)
    summary["acquisition_execution"]["resumed_work_count"] = 1
    summary["acquisition_execution"]["resume_only_remaining_open_works"] = False
    assert "ml1_acquisition_resume_or_retry_proof_missing" in {item.code for item in check_phase_contract(CONTRACT_ID, summary).errors}


def test_owner_sample_is_optional_and_not_a_runtime_blocker() -> None:
    summary = _summary(status="blocked_credential_rotation_confirmation_required")
    summary["pixiv_accounting"].update(incremental_acquisition_required=True, missing_work_count=1, candidate_distinct_work_count=3, accounted_distinct_work_count=3)
    summary["acquisition_execution"].update(acquisition_manifest_distinct_work_count=1, acquisition_manifest_fingerprint="c" * 64)
    result = check_phase_contract(CONTRACT_ID, summary)
    assert result.passed, [item.to_dict() for item in result.errors]


def test_blocked_fixed_evidence_requires_positive_change_proof() -> None:
    changed = _summary(status="blocked_fixed_evidence_changed")
    changed["fixed_evidence_proof"]["before_after_match"] = False
    changed["fixed_evidence_proof"]["changed_fixed_tables"] = ["blombooru_media"]
    assert check_phase_contract(CONTRACT_ID, changed).passed

    unchanged = _summary(status="blocked_fixed_evidence_changed")
    result = check_phase_contract(CONTRACT_ID, unchanged)
    assert "ml1_fixed_evidence_block_unproven" in {item.code for item in result.errors}

    missing = _summary(status="blocked_fixed_evidence_changed")
    missing["fixed_evidence_proof"].pop("before_after_match")
    result = check_phase_contract(CONTRACT_ID, missing)
    assert "ml1_fixed_evidence_block_unproven" in {item.code for item in result.errors}

    forbidden = _summary(status="blocked_fixed_evidence_changed")
    forbidden["fixed_evidence_proof"]["before_after_match"] = False
    forbidden["fixed_evidence_proof"]["changed_forbidden_truth_tables"] = ["blombooru_entities"]
    assert check_phase_contract(CONTRACT_ID, forbidden).passed


def test_metadata_complete_is_canonical_complete_and_audit_is_idempotent() -> None:
    media = [
        {"id": 1, "filename": "123456789_p0.jpg", "path": "media/123456789_p0.jpg", "thumbnail_path": None, "source": None, "uploaded_at": "2026-01-01T00:00:00+00:00"},
        {"id": 2, "filename": "123456789_p1.jpg", "path": "media/123456789_p1.jpg", "thumbnail_path": None, "source": None, "uploaded_at": "2026-01-02T00:00:00+00:00"},
    ]
    metadata = [
        {"id": 10, "provider": "pixiv", "media_id": 1, "source_work_id": "123456789", "source_page_index": 0, "status": "metadata_complete"},
        {"id": 11, "provider": "pixiv", "media_id": 2, "source_work_id": "123456789", "source_page_index": 1, "status": "metadata_complete"},
    ]
    first = ml1_runner.build_pixiv_accounting(media, metadata)
    second = ml1_runner.build_pixiv_accounting(media, metadata)
    assert first == second
    public, candidates, work_rows = first
    assert public["metadata_present_complete_media_count"] == 2
    assert public["metadata_present_complete_work_count"] == 1
    assert public["filename_identity_conflict_media_count"] == 0
    assert all(item.status == "metadata_present_complete" for item in candidates)
    assert work_rows[0]["page_indexes"] == [0, 1]


def test_wrong_page_metadata_complete_remains_mismatch_and_terminal_retryable_distinct() -> None:
    media = [
        {"id": 1, "filename": "123456789_p0.jpg", "path": "media/123456789_p0.jpg", "thumbnail_path": None, "source": None, "uploaded_at": None},
        {"id": 2, "filename": "223456789_p0.jpg", "path": "media/223456789_p0.jpg", "thumbnail_path": None, "source": None, "uploaded_at": None},
        {"id": 3, "filename": "323456789_p0.jpg", "path": "media/323456789_p0.jpg", "thumbnail_path": None, "source": None, "uploaded_at": None},
    ]
    metadata = [
        {"id": 10, "provider": "pixiv", "media_id": 1, "source_work_id": "123456789", "source_page_index": 1, "status": "metadata_complete"},
        {"id": 11, "provider": "pixiv", "media_id": 2, "source_work_id": "223456789", "source_page_index": 0, "status": "terminal_remote_unavailable"},
        {"id": 12, "provider": "pixiv", "media_id": 3, "source_work_id": "323456789", "source_page_index": 0, "status": "metadata_retryable"},
    ]
    public, candidates, _ = ml1_runner.build_pixiv_accounting(media, metadata)
    assert candidates[0].status == "filename_identity_conflict"
    assert public["terminal_remote_unavailable_media_count"] == 1
    assert public["retryable_failure_media_count"] == 1


@pytest.mark.parametrize(
    ("record_status", "candidate_status", "work_status", "in_manifest"),
    [
        ("metadata_pending", "metadata_pending", "pending", True),
        ("metadata_retryable", "retryable_provider_failure", "retryable", True),
        ("metadata_complete", "metadata_present_complete", "complete", False),
        ("terminal_remote_unavailable", "terminal_remote_unavailable", "terminal", False),
        ("normalization_failed", "metadata_parse_or_normalization_failure", "normalization_failed", False),
    ],
)
def test_exact_lifecycle_status_drives_manifest_without_false_conflict(
    record_status: str, candidate_status: str, work_status: str, in_manifest: bool
) -> None:
    media = [{"id": 1, "filename": "123456789_p0.jpg", "path": "media/123456789_p0.jpg", "thumbnail_path": None, "source": None, "uploaded_at": None}]
    metadata = [{"id": 10, "provider": "pixiv", "media_id": 1, "source_work_id": "123456789", "source_page_index": 0, "status": record_status}]
    public, candidates, work_rows = ml1_runner.build_pixiv_accounting(media, metadata)
    assert candidates[0].status == candidate_status
    assert candidates[0].status != "filename_identity_conflict"
    assert work_rows[0]["status"] == work_status
    assert bool(public["projected_gallery_dl_request_count"]) is in_manifest


def test_pending_to_complete_post_acquisition_audit_transition() -> None:
    media = [{"id": 1, "filename": "123456789_p0.jpg", "path": "media/123456789_p0.jpg", "thumbnail_path": None, "source": None, "uploaded_at": None}]
    row = {"id": 10, "provider": "pixiv", "media_id": 1, "source_work_id": "123456789", "source_page_index": 0, "status": "metadata_pending"}
    before = ml1_runner.build_pixiv_accounting(media, [row])
    assert before[2][0]["status"] == "pending"
    row["status"] = "metadata_complete"
    after = ml1_runner.build_pixiv_accounting(media, [row])
    assert after[2][0]["status"] == "complete"
    assert after[0]["projected_gallery_dl_request_count"] == 0


def test_matching_pending_queue_does_not_hide_historical_work_page_mismatch() -> None:
    media = [{"id": 1, "filename": "123456789_p0.jpg", "path": "media/123456789_p0.jpg", "thumbnail_path": None, "source": None, "uploaded_at": None}]
    metadata = [
        {"id": 10, "provider": "pixiv", "media_id": 1, "source_work_id": "123456789", "source_page_index": 0, "status": "metadata_pending"},
        {"id": 11, "provider": "pixiv", "media_id": 1, "source_work_id": "999999999", "source_page_index": 1, "status": "observed"},
    ]
    public, candidates, work_rows = ml1_runner.build_pixiv_accounting(media, metadata)
    assert candidates[0].status == "filename_identity_conflict"
    assert work_rows[0]["status"] == "unresolved_conflict"
    assert public["projected_gallery_dl_request_count"] == 0


def test_owner_review_sample_is_deterministic_exact_unique_private_and_conflicts_exported(tmp_path: Path) -> None:
    media = []
    for index in range(80):
        work_id = str(600000 + index)
        basename = f"prefix-{work_id}_p0-suffix.jpg" if index % 3 == 0 else f"{work_id}_p0.jpg"
        media.append({
            "id": index + 1, "filename": (f"C:\\private\\root\\{basename}" if index == 0 else basename), "path": f"private/root/{basename}",
            "thumbnail_path": None, "source": None,
            "uploaded_at": f"2026-{(index % 12) + 1:02d}-{(index % 27) + 1:02d}T00:00:00+00:00",
        })
        if index < 40:
            page_one = f"{work_id}_p1.jpg"
            media.append({
                "id": 300 + index, "filename": page_one, "path": f"private/root/{page_one}",
                "thumbnail_path": None, "source": None,
                "uploaded_at": f"2026-{(index % 12) + 1:02d}-{(index % 27) + 1:02d}T00:00:00+00:00",
            })
    for offset in range(3):
        left, right = 700000 + offset * 2, 700001 + offset * 2
        basename = f"{left}_p0__{right}_p1.jpg"
        media.append({"id": 100 + offset, "filename": basename, "path": f"private/root/{basename}", "thumbnail_path": None, "source": None, "uploaded_at": None})
    _, candidates, work_rows = ml1_runner.build_pixiv_accounting(media, [])
    first, paths = ml1_runner.build_owner_review_artifacts(tmp_path / "first", candidates, work_rows)
    second, _ = ml1_runner.build_owner_review_artifacts(tmp_path / "second", candidates, work_rows)
    assert first == second
    assert first["owner_sample_validation"]["sample_size"] == 60
    assert first["owner_sample_validation"]["conflict_cases_exported"] == 3
    for category in (
        "oldest_local_import", "newest_local_import", "low_work_id_quantile",
        "high_work_id_quantile", "multi_page_or_multiple_local_media", "nontrivial_filename_layout",
    ):
        assert first["selection"]["category_counts"][category] == 10
    full_rows = list(__import__("csv").DictReader(paths[0].open(encoding="utf-8-sig")))
    assert all("private/root" not in row["local_basenames"] and "private\\root" not in row["local_basenames"] for row in full_rows)
    sample_rows = list(__import__("csv").DictReader(paths[1].open(encoding="utf-8-sig")))
    assert len(sample_rows) == 60
    assert len({row["pixiv_work_id"] for row in sample_rows}) == 60
    assert all("private/root" not in row["local_basenames"] and "private\\root" not in row["local_basenames"] for row in sample_rows)
    assert all(row["exact_compatible_metadata_row_count"] == "0" for row in sample_rows)
    conflict_rows = list(__import__("csv").DictReader(paths[3].open(encoding="utf-8-sig")))
    assert len(conflict_rows) == 3
    assert all(row["automatically_selected_winner"] == "" for row in conflict_rows)
    assert all(path.is_file() for path in paths)


@pytest.mark.parametrize(
    ("section", "key", "value", "code"),
    [
        ("document_semantics", "identity_union_is_search_result_union", True, "ml1_document_semantics_incomplete"),
        ("pixiv_accounting", "accounted_media_count", 2, "ml1_pixiv_media_accounting_incomplete"),
        ("creator_metadata", "silently_dropped_creator_field_count", 1, "ml1_creator_target_failed"),
        ("multilingual_benchmark", "candidate_not_generated_count", 1, "ml1_multilingual_target_failed"),
        ("search_semantics", "and_constraint_leakage_count", 1, "ml1_search_semantics_target_failed"),
        ("operation_counts", "media_downloads", 1, "ml1_forbidden_operation_nonzero_or_missing"),
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
        candidate_distinct_work_count=3,
        accounted_distinct_work_count=3,
        retryable_work_count=1,
    )
    summary["route_authorization"]["pixiv_acquisition_authorized"] = False
    result = check_phase_contract(CONTRACT_ID, summary)
    assert result.passed, [finding.to_dict() for finding in result.errors]
    assert result.target_met_claimed is False


@pytest.mark.parametrize(
    "status",
    [
        "blocked_document_semantics_not_corrected",
        "blocked_environment_isolation",
        "blocked_credential_rotation_confirmation_required",
        "blocked_fixed_evidence_changed",
        "blocked_pixiv_metadata_audit_incomplete",
        "blocked_pixiv_incremental_acquisition_approval_required",
        "blocked_pixiv_acquisition_execution_incomplete",
        "blocked_creator_metadata_loss",
        "blocked_multilingual_benchmark_incomplete",
        "blocked_candidate_generation_gap",
        "blocked_llm_approval_required",
        "blocked_and_search_semantics",
    ],
)
def test_each_blocked_status_requires_its_own_evidence(status: str) -> None:
    summary = _summary(status=status)
    # The default fixture is deliberately healthy, so the selected blocker is
    # unsupported and must fail even though its string is in active_blockers.
    result = check_phase_contract(CONTRACT_ID, summary)
    assert "ml1_status_evidence_missing" in {finding.code for finding in result.errors}


def test_partial_pixiv_foundation_status_requires_closed_stock_and_continuous_gate() -> None:
    summary = _summary(status="partial_ml1_pixiv_metadata_closure_complete")
    result = check_phase_contract(CONTRACT_ID, summary)
    assert result.passed, [finding.to_dict() for finding in result.errors]
    summary["pixiv_metadata_foundation"]["current_stock_closed"] = False
    result = check_phase_contract(CONTRACT_ID, summary)
    assert "ml1_status_evidence_missing" in {finding.code for finding in result.errors}


def test_partial_pixiv_foundation_may_carry_deferred_creator_and_candidate_gaps() -> None:
    summary = _summary(status="partial_ml1_pixiv_metadata_closure_complete")
    summary["creator_metadata"]["silently_dropped_creator_field_count"] = 1
    summary["multilingual_benchmark"]["candidate_not_generated_count"] = 1
    summary["candidate_generation"]["unresolved_candidate_generation_count"] = 1
    summary["pipeline_contract"]["active_blockers"] = [
        "blocked_creator_metadata_loss",
        "blocked_candidate_generation_gap",
    ]
    result = check_phase_contract(CONTRACT_ID, summary)
    assert result.passed, [finding.to_dict() for finding in result.errors]


def test_active_blockers_cannot_hide_later_known_gaps() -> None:
    summary = _summary(status="blocked_credential_rotation_confirmation_required")
    summary["pixiv_accounting"].update(
        incremental_acquisition_required=True,
        projected_gallery_dl_request_count=1,
        metadata_present_complete_media_count=1,
        retryable_failure_media_count=1,
        metadata_present_complete_work_count=0,
        retryable_work_count=1,
    )
    summary["creator_metadata"]["silently_dropped_creator_field_count"] = 1
    summary["multilingual_benchmark"]["candidate_not_generated_count"] = 1
    summary["candidate_generation"]["unresolved_candidate_generation_count"] = 1
    result = check_phase_contract(CONTRACT_ID, summary)
    assert "ml1_active_blockers_incomplete" in {finding.code for finding in result.errors}


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


def test_parse_search_query_uses_explicit_snapshot_session_for_aliases() -> None:
    engine_a = create_engine("sqlite:///:memory:")
    engine_b = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine_a, tables=[TagTranslation.__table__])
    Base.metadata.create_all(engine_b, tables=[TagTranslation.__table__])
    session_a = sessionmaker(bind=engine_a)()
    session_b = sessionmaker(bind=engine_b)()
    try:
        session_a.add(
            TagTranslation(
                canonical_name="canonical_a",
                language="zh-CN",
                display_name="snapshot_specific_alias",
                source="static",
                status="translated",
                needs_review=False,
            )
        )
        session_b.add(
            TagTranslation(
                canonical_name="canonical_b",
                language="zh-CN",
                display_name="snapshot_specific_alias",
                source="static",
                status="translated",
                needs_review=False,
            )
        )
        session_a.commit()
        session_b.commit()
        assert parse_search_query("snapshot_specific_alias", db=session_a)["tags"]["include"] == ["canonical_a"]
        assert parse_search_query("snapshot_specific_alias", db=session_b)["tags"]["include"] == ["canonical_b"]
        assert "search_parser_translation_alias_map_v1" in session_a.info
        assert "search_parser_translation_alias_map_v1" in session_b.info
    finally:
        session_a.close()
        session_b.close()
        engine_a.dispose()
        engine_b.dispose()


def test_old_r2_schema_clone_adds_fallback_index_before_dry_run(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old-r2-clone.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE blombooru_media (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE blombooru_source_concept_signals "
                "(id INTEGER PRIMARY KEY, media_id INTEGER)"
            )
        )

    assert "blombooru_source_concept_fallback_search_index" not in inspect(engine).get_table_names()
    migrate_add_source_concept_fallback_search_index(engine, inspect(engine))
    assert "blombooru_source_concept_fallback_search_index" in inspect(engine).get_table_names()

    source = __import__("inspect").getsource(r2r_runner.run_cache_only_dry_run)
    assert source.index("migrate_add_source_concept_fallback_search_index") < source.index("SessionLocal = sessionmaker")
    engine.dispose()


def test_pixiv_accounting_handles_prefix_suffix_multipage_and_exact_linkage() -> None:
    media_rows = [
        {"id": 1, "filename": "prefix-123456789_p0-copy.jpg", "path": "", "thumbnail_path": "", "source": ""},
        {"id": 2, "filename": "123456789_p1.jpg", "path": "", "thumbnail_path": "", "source": ""},
        {"id": 3, "filename": "987654321_p0.jpg", "path": "", "thumbnail_path": "", "source": ""},
    ]
    metadata_rows = [
        {"id": 10, "provider": "pixiv", "media_id": 1, "source_work_id": "123456789", "source_page_index": 0, "status": "observed"},
        {"id": 11, "provider": "pixiv", "media_id": 2, "source_work_id": "123456789", "source_page_index": 1, "status": "observed"},
    ]

    public, candidates, works = ml1_runner.build_pixiv_accounting(media_rows, metadata_rows)

    assert public["candidate_media_count"] == 3
    assert public["candidate_distinct_work_count"] == 2
    assert public["metadata_present_complete_media_count"] == 2
    assert public["metadata_present_complete_work_count"] == 1
    assert public["no_durable_attempt_or_result_evidence_media_count"] == 1
    assert public["projected_gallery_dl_request_count"] == 1
    assert [item.page_index for item in candidates[:2]] == [0, 1]
    assert {item["media_count"] for item in works} == {1, 2}


def test_pixiv_conflict_keeps_every_work_and_origin_in_accounting() -> None:
    media_rows = [
        {
            "id": 1,
            "filename": "123456789_p0__987654321_p2.jpg",
            "path": "media/original/123456789_p0__987654321_p2.jpg",
            "thumbnail_path": "thumb/123456789_p0.jpg",
            "source": "file://source/987654321_p2.jpg",
        }
    ]
    public, candidates, works = ml1_runner.build_pixiv_accounting(media_rows, [])

    assert public["candidate_distinct_work_count"] == 2
    assert public["accounted_distinct_work_count"] == 2
    assert public["work_accounting_equality_holds"] is True
    assert public["filename_identity_conflict_media_count"] == 1
    assert public["filename_identity_conflict_distinct_work_count"] == 2
    assert public["conflict_unresolved_work_count"] == 2
    assert {item["work_id"] for item in works} == {"123456789", "987654321"}
    assert all(item["status"] == "unresolved_conflict" for item in works)
    assert public["origin_breakdown"]["filename_origin"]["candidate_media_count"] == 1
    assert public["origin_breakdown"]["source_field_origin"]["distinct_work_count"] == 1
    assert candidates[0].match_origins


def test_runtime_support_accounting_detects_unsupported_rejected_and_superseded() -> None:
    result = ml1_runner.classify_runtime_support(
        {1, 2, 3, 4},
        {1: {"direct_media_tag"}, 2: {"accepted_search_only_alias_relation"}},
        rejected_ids={3},
        superseded_ids={4},
    )
    assert result["runtime_result_count"] == 4
    assert result["supported_result_count"] == 2
    assert result["unsupported_result_ids"] == {3, 4}
    assert result["rejected_evidence_result_ids"] == {3}
    assert result["superseded_evidence_result_ids"] == {4}
    assert result["support_coverage"] == 0.5


def test_exact_text_support_key_preserves_unicode_independently_of_canonical_key() -> None:
    value = "蓝发"
    key = ml1_runner.exact_support_key(value)
    assert key.startswith("__exact_text__:")
    support = {key: {7: {"direct_media_tag_exact_text"}}}
    assert support[key][7] == {"direct_media_tag_exact_text"}


def test_translation_support_inherits_exact_and_parenthetical_direct_tags() -> None:
    support = {
        ml1_runner.canonical_source_key("blue_hair"): {
            1: {"direct_media_tag"},
            2: {"direct_media_tag_parenthetical_variant"},
            3: {"accepted_materialized_sourceconcept_alias"},
        }
    }
    ml1_runner.apply_translation_support_relations(
        support,
        [
            {
                "canonical_name": "blue_hair",
                "display_name": "blue hair translated",
                "aliases_json": [],
                "source": "static",
                "status": "active",
            }
        ],
    )
    inherited = support[ml1_runner.canonical_source_key("blue hair translated")]
    assert set(inherited) == {1, 2, 3}
    assert all("accepted_search_only_translation_relation" in types for types in inherited.values())


def test_review_pack_equivalence_fails_when_packed_evidence_differs(tmp_path: Path) -> None:
    pack_dir = tmp_path / "review-pack"
    pack_dir.mkdir()
    evidence = {"phase": "test", "value": 1}
    (pack_dir / "evidence-summary.json").write_text(json.dumps(evidence), encoding="utf-8")
    (pack_dir / "public-report-copy.md").write_text("report", encoding="utf-8")
    (pack_dir / "contract-evidence.json").write_text("{}", encoding="utf-8")
    public = {
        "evidence_summary": evidence,
        "review_pack_attestation": {
            "packed_evidence_summary_sha256": ml1_runner.sha256_file(pack_dir / "evidence-summary.json"),
            "packed_report_sha256": ml1_runner.sha256_file(pack_dir / "public-report-copy.md"),
            "packed_contract_evidence_sha256": ml1_runner.sha256_file(pack_dir / "contract-evidence.json"),
            "self_referential_hash_claimed": False,
            "placeholder_field_count": 0,
        },
    }
    ml1_runner.verify_review_pack_equivalence(public, pack_dir)
    (pack_dir / "evidence-summary.json").write_text(json.dumps({"phase": "test", "value": 2}), encoding="utf-8")
    with pytest.raises(ml1_runner.ML1BlockedError, match="review_pack_evidence_summary_mismatch"):
        ml1_runner.verify_review_pack_equivalence(public, pack_dir)


def test_public_markdown_uses_current_blocker_and_no_durable_semantics() -> None:
    wrapper = json.loads(
        (ROOT / "docs/reports/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure-summary.json").read_text(
            encoding="utf-8"
        )
    )
    evidence = wrapper.get("evidence_summary", wrapper)
    report = ml1_runner.render_report(evidence)
    assert "credential rotation confirmed" in report
    assert "no-durable-result" in report
    assert "approval required: `True`" not in report
    assert "Identity-eligible / search-only families" in report
    assert "Unsupported / rejected / superseded results" in report


def test_creator_audit_detects_account_loss_without_destroying_stable_id() -> None:
    metadata = [
        {
            "id": 1,
            "provider": "pixiv",
            "artist_id": "42",
            "artist_name": "Display",
            "raw_metadata_json": {"user": {"id": 42, "name": "Display", "account": "handle"}},
        }
    ]
    observations = [
        {
            "source_metadata_record_id": 1,
            "raw_name": "Display",
            "name_role": "artist",
            "source_field": "pixiv_user_metadata",
        }
    ]

    public, private, aliases = ml1_runner.build_creator_audit(metadata, observations)

    assert public["stable_creator_id_preservation_coverage"] == 1.0
    assert public["observed_creator_name_search_support_coverage"] == 1.0
    assert public["observed_creator_account_search_support_coverage"] == 0.0
    assert public["silently_dropped_creator_field_count"] == 1
    assert private[0]["dropped_fields"] == ["creator_account"]
    assert aliases["42"] == {"Display", "handle"}


def test_public_redaction_rejects_paths_secrets_and_private_name_keys() -> None:
    ml1_runner.assert_public_safe({"aggregate_count": 1})
    with pytest.raises(ml1_runner.ML1BlockedError):
        ml1_runner.assert_public_safe({"value": "C:\\Users\\person\\private.jpg"})
    with pytest.raises(ml1_runner.ML1BlockedError):
        ml1_runner.assert_public_safe({"creator_name": "private"})


def test_missing_forbidden_table_fingerprint_is_stable() -> None:
    missing = {
        "table": "table_that_is_absent",
        "status": "missing",
        "count": None,
        "row_content_sha256": hashlib.sha256(b"missing_table").hexdigest(),
        "columns": [],
    }
    before = {"tables": {"table_that_is_absent": missing}}
    after = {"tables": {"table_that_is_absent": dict(missing)}}
    comparison = __import__(
        "scripts.run_phase45_scv2_r2_constraint_aware_graph_remediation",
        fromlist=["compare_fingerprints"],
    ).compare_fingerprints(before, after)
    assert comparison["passed"] is True
    assert comparison["changed_tables"] == []
