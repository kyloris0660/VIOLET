"""Tests for executable phase contracts and phase gates."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_contracts import REQUIRED_CONTRACT_IDS, check_phase_contract, list_contracts, load_summary_file  # noqa: E402
from scripts.phase_contracts.contract_registry import SOURCE_CONCEPT_FULL_CHAIN_STAGES  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "phase_contracts"


def _source_concept_summary(**overrides: object) -> dict:
    summary = {
        "pipeline_contract": {
            "contract_id": "source_concept_full_chain_contract_v1",
            "status": "full_chain_completed",
            "claims": {"full_chain_complete": True},
        },
        "required_stages": list(SOURCE_CONCEPT_FULL_CHAIN_STAGES),
        "executed_stages": list(SOURCE_CONCEPT_FULL_CHAIN_STAGES),
        "missing_required_stages": [],
        "full_chain_fidelity_passed": True,
        "deterministic_stage_summary": {"resolver_version": "source_concept_resolver_core_v2_graph"},
        "llm_adjudication_plan": {
            "required": True,
            "status": "ready",
            "eligible_pair_count": 12,
            "selected_pair_count": 12,
            "max_calls": 300,
            "budget_usd": 50.0,
            "projected_budget_usd": 0.2,
        },
        "llm_adjudication_used": True,
        "llm_judgment_count": 12,
        "llm_max_calls": 300,
        "llm_budget_usd": 50.0,
        "llm_provider_mode": "primary_openai",
        "llm_cache_summary": {"cache_enabled": True, "cache_hits": 0, "cache_misses": 12},
        "mutation_proof": {"passed": True, "forbidden_changed_tables": [], "unexpected_changed_tables": []},
        "post_commit_verification": {"passed": True},
        "validation_pack": {"generated": True},
        "review_pack": {"generated": True},
        "conclusion": "full_chain_completed",
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _route_audit_summary(**overrides: object) -> dict:
    summary = {
        "final_route_decision_status": "blocked_pending_pipeline_fidelity_remediation",
        "transaction_readonly_proof": {
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
            "stable_snapshot": True,
            "snapshot_id_present": True,
        },
        "mutation_proof": {"passed": True, "changed_tables": []},
        "chatgpt_review_pack": _complete_review_pack_proof(),
        "pipeline_contract": {"contract_id": "route_audit_contract_v1"},
        "upstream_pipeline_contract": {"passed": False, "full_chain_fidelity_passed": False},
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _route_full_chain_upstream(**overrides: object) -> dict:
    upstream = {
        "contract_id": "source_concept_full_chain_contract_v1",
        "passed": True,
        "status": "full_chain_completed",
        "full_chain_fidelity_passed": True,
        "missing_required_stages": [],
    }
    upstream.update(overrides)
    return upstream


def _complete_review_pack_proof(**overrides: object) -> dict:
    pack = {
        "generated": True,
        "manifest_present": True,
        "checksums_present": True,
        "checksum_count": 3,
        "manifest_checksum_count": 3,
        "redaction_passed": True,
        "redaction_scan_covers_final_file_set": True,
        "public_report_copy_current": True,
        "zip_generated": True,
        "not_committed": True,
    }
    pack.update(overrides)
    return pack


def _review_pack_summary(**pack_overrides: object) -> dict:
    return {"review_pack": _complete_review_pack_proof(**pack_overrides)}


def _dynamic_sync_summary(**overrides: object) -> dict:
    summary = {
        "pipeline_contract": {
            "contract_id": "dynamic_library_sync_contract_v1",
            "status": "target_met",
            "claims": {"target_met": True},
        },
        "dynamic_sync": {
            "schema": {
                "tables": [
                    "blombooru_dynamic_source_roots",
                    "blombooru_dynamic_source_items",
                    "blombooru_dynamic_sync_runs",
                    "blombooru_dynamic_sync_run_items",
                ]
            },
            "identity": {"source_item_identity": "source_root_id + relative_path_hash"},
            "default_off_policy": {"auto_sync_enabled": False, "manual_sync_enabled": False},
            "threshold": {"default": 100},
            "pending_counts": {"visible": True},
            "dry_run_no_import": True,
            "source_root_safety": {"passed": True},
        },
        "ai_localization": {
            "chain_verified": True,
            "ai_tagging_auto_localization_default_enabled": True,
        },
        "proper_noun_safeguards": {
            "preserved": True,
            "worker_excludes_proper_nouns": True,
            "unreviewed_llm_aliases_excluded_from_search": True,
        },
        "validation": {
            "focused_tests_passed": True,
            "browser_validation": {"status": "passed"},
        },
        "safety": {
            "full_production_import": False,
            "production_db_import": False,
            "full_ai_tagging_run": False,
            "full_llm_localization_batch": False,
            "provider_calls": False,
            "sourceconcept_or_entity": False,
            "source_icloud_mutation": False,
            "destructive_cleanup": False,
        },
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _pd1a_governance_summary(**overrides: object) -> dict:
    summary = {
        "pipeline_contract": {
            "contract_id": "production_development_separation_contract_v1",
            "status": "target_met",
            "claims": {"target_met": True, "safe_to_merge": True},
        },
        "governance_lanes": {
            "production": {"explicit": True},
            "development": {"explicit": True},
        },
        "development_lane": {
            "allowed_data_sources": [
                "dev_or_test_db",
                "dev_or_test_storage",
                "fixtures_or_restored_snapshots",
            ],
            "production_db_as_fixture": False,
            "production_storage_as_fixture": False,
            "production_source_roots_as_fixture": False,
            "production_private_ledgers_as_fixture": False,
        },
        "production_promotion": {
            "required_for_production_writes": True,
            "enabled": False,
            "operator_confirmation_present": False,
        },
        "production_write_gates": {
            "import_classification_ai_localization_requires_promotion": True,
        },
        "production_source_root_write_gates": {
            "clean_identity_required": True,
            "backup_proof_required": True,
        },
        "schema_setup_gates": {
            "identity_gates_required": True,
            "no_schema_setup_when_identity_blocked": True,
            "schema_setup_requested": False,
            "schema_setup_ran": False,
        },
        "artifact_boundary": {
            "public_reports_aggregate_only": True,
            "public_reports_path_redacted": True,
            "public_redaction_contract_passed": True,
            "private_ledgers_local_ignored": True,
            "private_ledgers_committed": False,
        },
        "phase_boundaries": {
            "current_phase": "PD1-A",
            "next_recommended_phase": "S2G-1 GPU AI tagging capability probe and benchmark",
            "future_mentions_are_non_authorizing": True,
            "authorizes_s3": False,
            "authorizes_provider_calls": False,
            "authorizes_pixiv_gallery_dl_saucenao_google": False,
            "authorizes_sourceconcept_r1r_r2": False,
            "authorizes_entity_bridge": False,
            "authorizes_confirmed_assignments": False,
            "authorizes_automatic_production_sync": False,
            "authorizes_gpu_benchmark": False,
            "authorizes_desired_media_backfill": False,
        },
        "write_requests": {
            "production_import": False,
            "production_classification": False,
            "production_ai_tagging": False,
            "production_localization": False,
            "source_root_registration": False,
            "source_root_replacement": False,
            "schema_setup": False,
            "schema_migration": False,
        },
        "validation": {"focused_tests_passed": True},
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _s2g1x_summary(**overrides: object) -> dict:
    summary = {
        "pipeline_contract": {
            "contract_id": "s2g1x_probe_contract_v1",
            "status": "target_met",
            "claims": {"target_met": True, "safe_to_merge": True},
        },
        "capability_probe": {
            "completed": True,
            "safe_probe": {
                "no_db_connection": True,
                "no_production_db_writes": True,
                "no_media_tags_writes": True,
                "no_full_library_ai_tagging": True,
                "no_model_download": True,
                "local_files_only": True,
                "sample_count": 3,
            },
            "model_identity": {
                "model_name": "wd-swinv2-tagger-v3",
                "model_file_cached": True,
                "label_file_cached": True,
                "network_download_required": False,
            },
            "current_app_backend": {
                "forced_provider": "CPUExecutionProvider",
                "hardcoded_cpu_execution_provider": True,
            },
            "provider_matrix": {
                "cpu": {
                    "provider": "CPUExecutionProvider",
                    "available": True,
                    "practical": True,
                    "loaded": True,
                    "benchmark_status": "completed",
                    "throughput_items_per_second": 1.0,
                },
                "cuda": {
                    "provider": "CUDAExecutionProvider",
                    "available": False,
                    "practical": False,
                    "loaded": False,
                    "benchmark_status": "not_available",
                },
                "directml": {
                    "provider": "DmlExecutionProvider",
                    "available": False,
                    "practical": False,
                    "loaded": False,
                    "benchmark_status": "not_available",
                },
            },
            "thresholds": {
                "general_threshold": 0.35,
                "character_threshold": 0.65,
                "rating_threshold": 0.5,
                "suggestion_threshold": 0.2,
                "batch_max_items": 10,
            },
        },
        "load_control": {
            "recommended_config": {
                "batch_size": 2,
                "worker_count": 1,
                "max_concurrent_jobs": 1,
                "preprocess_workers": 2,
                "cpu_intra_op_threads": 4,
                "cpu_inter_op_threads": 1,
                "provider_preference": ["CPUExecutionProvider"],
            }
        },
        "s3a_dev_dry_run_plan": {
            "production_execution_enabled": False,
            "unattended_enabled": False,
            "dry_run_only": True,
            "stages": [
                {"name": "update_check", "writes_enabled": False},
                {"name": "ai_tagging_plan", "writes_enabled": False},
            ],
        },
        "s2g_s3a_decision": {
            "decision": "share_foundation_split_production_execution",
            "should_share_job_progress_throttle_ledger_architecture": True,
            "should_combine_current_production_execution": False,
            "gpu_load_control_before_s3a_production_execution": True,
            "production_s3a_execution_enabled": False,
            "unattended_s3b_enabled": False,
        },
        "public_redaction": {"passed": True},
        "safety": {
            "production_db_writes": False,
            "production_import": False,
            "production_classification": False,
            "production_ai_tagging": False,
            "production_localization": False,
            "production_s3a_execution_enabled": False,
            "unattended_auto_sync_enabled": False,
            "provider_pixiv_gallery_dl_saucenao_google_calls": False,
            "sourceconcept_or_entity": False,
            "confirmed_entity_assignments": False,
            "source_icloud_mutation": False,
            "cleanup_delete_reset_drop_truncate": False,
            "model_download": False,
        },
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _set_nested(payload: dict, path: str, value: object) -> None:
    cursor = payload
    parts = path.split(".")
    for part in parts[:-1]:
        cursor = cursor[part]
    cursor[parts[-1]] = value


def _phase47_s2_summary(**overrides: object) -> dict:
    summary = {
        "head_evidence": {
            "validated_run_head_sha": "validated-head",
            "report_generation_head_sha": "report-head",
            "current_pr_head_sha": "reported by PR handoff",
            "top_level_head_sha_omitted": True,
        },
        "pipeline_contract": {
            "contract_id": "phase47_s2_baseline_contract_v1",
            "status": "blocked_gate1",
            "claims": {"target_met": False, "safe_to_merge": False, "full_chain_complete": False},
        },
        "gate0": {
            "status": "blocked",
            "backup_recovery": {"proof_exists": False, "valid": False},
            "schema": {
                "ensure": {"status": "blocked_backup_required", "ran": False},
                "after": {"tables_missing": ["blombooru_dynamic_source_roots"]},
            },
            "input_root_registration": {"registered_count": 0},
        },
        "readiness": {
            "passed": False,
            "blockers": ["dynamic_sync_tables_missing"],
            "llm_localization": {"operator_approved": True},
            "python_env": {"check_python_env_passed": True},
            "app_settings_db_identity_matches_execution_db": True,
            "production_storage": {"explicitly_set": True},
            "backup_recovery": {"valid": False},
            "ai_model": {"model_downloaded": True},
            "automatic_production_sync": {"remains_opt_in": True},
            "proper_noun_safeguards": {"unreviewed_llm_aliases_excluded_from_search": True},
            "input_root_counts": {"valid_count": 0},
            "db_identity": {
                "db_resolution": {
                    "runner_matches_app_equivalent": True,
                    "password_value_recorded": False,
                }
            },
            "dynamic_schema": {"tables_missing": ["blombooru_dynamic_source_roots"]},
        },
        "dynamic_sync_dry_run": {"status": "not_run_gate1", "executed": False},
        "import_results": {"status": "not_run_gate1", "executed": False},
        "classification_results": {"status": "not_run_gate1", "executed": False},
        "ai_tagging_results": {"status": "not_run_gate1", "executed": False},
        "localization_results": {
            "status": "not_run_gate1",
            "executed": False,
            "llm_called": False,
            "proper_noun_unreviewed_aliases_trusted": False,
        },
        "proper_noun_review": {"unreviewed_llm_aliases_excluded_from_search": True},
        "browser_validation": {"status": "not_run_gate1"},
        "private_artifacts": {"private_artifacts_committed": False},
        "public_redaction": {"passed": True},
        "safety": {
            "no_source_icloud_mutation": True,
            "no_cleanup_delete_reset_drop_truncate": True,
            "no_db_import": True,
            "no_classification": True,
            "no_ai_tagging": True,
            "no_llm_call": True,
        },
        "artifact_lifecycle": {"artifacts": [{"path": "docs/reports/example.md"}]},
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _phase47_ready_readiness(**overrides: object) -> dict:
    readiness = {
        "passed": True,
        "blockers": [],
        "llm_localization": {"operator_approved": True},
        "python_env": {"check_python_env_passed": True},
        "app_settings_db_identity_matches_execution_db": True,
        "production_storage": {"explicitly_set": True},
        "backup_recovery": {"valid": True},
        "ai_model": {"model_downloaded": True},
        "automatic_production_sync": {"remains_opt_in": True},
        "proper_noun_safeguards": {"unreviewed_llm_aliases_excluded_from_search": True},
        "input_root_counts": {"valid_count": 1},
        "db_identity": {"db_resolution": {"runner_matches_app_equivalent": True, "password_value_recorded": False}},
        "dynamic_schema": {"tables_missing": [], "tables_missing_count": 0},
    }
    readiness.update(overrides)
    return readiness


def _phase47_full_execution_summary(**overrides: object) -> dict:
    summary = _phase47_s2_summary(
        pipeline_contract={
            "contract_id": "phase47_s2_baseline_contract_v1",
            "status": "target_met",
            "execute_confirmation_present": True,
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": True},
        },
        gate0={
            "status": "passed",
            "backup_recovery": {"proof_exists": True, "valid": True},
            "db_identity": {"matches_expected_database": True},
            "storage_identity": {"matches_expected": True},
            "schema": {
                "ensure": {"status": "not_needed", "ran": False},
                "after": {"tables_missing": []},
            },
            "input_root_registration": {"registered_count": 0},
        },
        readiness=_phase47_ready_readiness(),
        dynamic_sync_dry_run={
            "status": "completed",
            "executed": True,
            "source_scope_check": {"passed": True},
        },
        import_results={
            "status": "completed_with_item_failures_within_budget",
            "executed": True,
            "per_item_ledgers_written": True,
            "hydration_failure_budget": {"threshold_exceeded": False},
            "import_failure_budget": {"threshold_exceeded": False},
        },
        classification_results={
            "status": "completed",
            "executed": True,
            "failure_budget": {"threshold_exceeded": False},
        },
        ai_tagging_results={
            "status": "completed",
            "executed": True,
            "failure_budget": {"threshold_exceeded": False},
            "auto_translation_suppressed_during_ai_stage": True,
        },
        localization_results={
            "status": "completed_with_gap_visible",
            "executed": True,
            "llm_called": True,
            "gap_report_generated": True,
            "proper_noun_unreviewed_aliases_trusted": False,
            "failure_budget": {"threshold_exceeded": False},
        },
        llm_localization_audit={
            "provider_call_count_lower_bound": 1,
            "provider_calls_undercounted": False,
            "current_runner_suppresses_auto_translation_during_ai_stage": True,
            "background_provider_calls_ledgered": False,
        },
        browser_validation={"status": "passed"},
    )
    summary.update(overrides)
    return summary


def _error_codes(result) -> set[str]:
    return {error.code for error in result.errors}


def _serialized_result(result) -> str:
    return json.dumps(result.to_dict(), sort_keys=True)


def test_registry_contains_all_required_contracts() -> None:
    registered = {contract.contract_id for contract in list_contracts()}

    assert set(REQUIRED_CONTRACT_IDS).issubset(registered)
    assert len(registered) >= 15


def test_phase47_s2_contract_accepts_gate1_blocked_summary_without_completion_claim() -> None:
    result = check_phase_contract("phase47_s2_baseline_contract_v1", _phase47_s2_summary())

    assert result.passed is True


def test_phase47_s2_contract_rejects_completion_claim_with_gate1_blocker() -> None:
    summary = _phase47_s2_summary(
        pipeline_contract={
            "contract_id": "phase47_s2_baseline_contract_v1",
            "status": "target_met",
            "claims": {"target_met": True, "safe_to_merge": True},
        }
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_completion_claimed_with_gate1_blocker" in _error_codes(result)


def test_phase47_s2_contract_rejects_schema_ensure_without_backup() -> None:
    summary = _phase47_s2_summary(
        gate0={
            "status": "blocked",
            "backup_recovery": {"proof_exists": False, "valid": False},
            "schema": {
                "ensure": {"status": "completed", "ran": True},
                "after": {"tables_missing": []},
            },
            "input_root_registration": {"registered_count": 0},
        }
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_schema_ensure_without_valid_backup_proof" in _error_codes(result)


def test_phase47_s2_contract_rejects_missing_tables_after_schema_ensure() -> None:
    summary = _phase47_s2_summary(
        gate0={
            "status": "blocked",
            "backup_recovery": {"proof_exists": True, "valid": True},
            "schema": {
                "ensure": {"status": "failed", "ran": True},
                "after": {"tables_missing": ["blombooru_dynamic_source_roots"]},
            },
            "input_root_registration": {"registered_count": 0},
        }
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_schema_tables_missing_after_ensure" in _error_codes(result)


def test_phase47_s2_contract_accepts_readiness_passed_dry_run_complete_without_execute() -> None:
    summary = _phase47_s2_summary(
        pipeline_contract={
            "contract_id": "phase47_s2_baseline_contract_v1",
            "status": "dry_run_complete_execute_not_requested",
            "claims": {"target_met": False, "safe_to_merge": False, "full_chain_complete": False},
        },
        gate0={
            "status": "passed",
            "backup_recovery": {"proof_exists": True, "valid": True},
            "schema": {
                "ensure": {"status": "not_needed", "ran": False},
                "after": {"tables_missing": []},
            },
            "input_root_registration": {"registered_count": 1},
        },
        readiness=_phase47_ready_readiness(),
        dynamic_sync_dry_run={"status": "completed", "executed": True},
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert result.passed is True


def test_phase47_s2_contract_rejects_import_without_dry_run_or_ledgers() -> None:
    summary = _phase47_s2_summary(
        gate0={
            "status": "passed",
            "backup_recovery": {"proof_exists": True, "valid": True},
            "schema": {
                "ensure": {"status": "not_needed", "ran": False},
                "after": {"tables_missing": []},
            },
            "input_root_registration": {"registered_count": 1},
        },
        readiness=_phase47_ready_readiness(),
        dynamic_sync_dry_run={"status": "not_run", "executed": False},
        import_results={"status": "completed", "executed": True},
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)
    codes = _error_codes(result)

    assert "phase47_s2_import_claimed_without_fresh_dry_run" in codes
    assert "phase47_s2_import_missing_per_item_ledgers" in codes


def test_phase47_s2_contract_rejects_llm_call_without_operator_approval() -> None:
    summary = _phase47_s2_summary(
        readiness=_phase47_ready_readiness(
            passed=False,
            blockers=["llm_localization_operator_approval_missing"],
            llm_localization={"operator_approved": False},
        ),
        localization_results={
            "status": "completed",
            "llm_called": True,
            "proper_noun_unreviewed_aliases_trusted": False,
        },
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_llm_called_without_operator_approval" in _error_codes(result)


def test_phase47_s2_contract_rejects_missing_public_redaction() -> None:
    summary = _phase47_s2_summary(public_redaction={"passed": False})

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_public_redaction_absent_or_failed" in _error_codes(result)


def test_phase47_s2_contract_rejects_missing_no_execution_safety_flags() -> None:
    summary = _phase47_s2_summary(
        safety={
            "no_source_icloud_mutation": True,
            "no_cleanup_delete_reset_drop_truncate": True,
            "no_db_import": False,
            "no_classification": True,
            "no_ai_tagging": True,
            "no_llm_call": True,
        }
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_missing_no_execution_safety_flag" in _error_codes(result)


def test_phase47_s2_contract_rejects_private_artifacts_committed() -> None:
    summary = _phase47_s2_summary(private_artifacts={"private_artifacts_committed": True})

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_private_artifacts_committed_or_missing" in _error_codes(result)


def test_phase47_s2_contract_rejects_full_completion_without_executed_proofs() -> None:
    summary = _phase47_s2_summary(
        pipeline_contract={
            "contract_id": "phase47_s2_baseline_contract_v1",
            "status": "target_met",
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": True},
        },
        gate0={
            "status": "passed",
            "backup_recovery": {"proof_exists": True, "valid": True},
            "schema": {
                "ensure": {"status": "not_needed", "ran": False},
                "after": {"tables_missing": []},
            },
            "input_root_registration": {"registered_count": 1},
        },
        readiness=_phase47_ready_readiness(),
        dynamic_sync_dry_run={"status": "completed", "executed": True},
        import_results={"status": "completed", "executed": False, "per_item_ledgers_written": False},
        classification_results={"status": "completed", "executed": False},
        ai_tagging_results={"status": "completed", "executed": False},
        localization_results={"status": "completed", "executed": False, "llm_called": False},
        browser_validation={"status": "passed"},
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_full_completion_missing_executed_proof" in _error_codes(result)


def test_phase47_s2_contract_rejects_ambiguous_stale_head_sha() -> None:
    summary = _phase47_full_execution_summary(head_sha="stale-runtime-head")

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_ambiguous_top_level_head_sha_present" in _error_codes(result)


def test_phase47_s2_contract_rejects_execution_without_exact_confirmation() -> None:
    summary = _phase47_full_execution_summary(
        pipeline_contract={
            "contract_id": "phase47_s2_baseline_contract_v1",
            "status": "target_met",
            "execute_confirmation_present": False,
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": True},
        }
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_execution_claimed_without_exact_confirmation" in _error_codes(result)


def test_phase47_s2_contract_rejects_classification_failure_budget_missing_or_exceeded() -> None:
    missing = _phase47_full_execution_summary(
        classification_results={"status": "completed", "executed": True}
    )
    exceeded = _phase47_full_execution_summary(
        classification_results={
            "status": "completed_with_item_failures_within_budget",
            "executed": True,
            "failure_budget": {"threshold_exceeded": True},
        }
    )

    missing_result = check_phase_contract("phase47_s2_baseline_contract_v1", missing)
    exceeded_result = check_phase_contract("phase47_s2_baseline_contract_v1", exceeded)

    assert "phase47_s2_classification_failure_budget_missing" in _error_codes(missing_result)
    assert "phase47_s2_classification_failure_budget_exceeded" in _error_codes(exceeded_result)


def test_phase47_s2_contract_rejects_localization_failure_threshold_before_completion() -> None:
    summary = _phase47_full_execution_summary(
        localization_results={
            "status": "completed_with_gap_visible",
            "executed": True,
            "llm_called": True,
            "gap_report_generated": True,
            "proper_noun_unreviewed_aliases_trusted": False,
            "failure_budget": {"threshold_exceeded": True},
        }
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_localization_failure_budget_exceeded" in _error_codes(result)


def test_phase47_s2_contract_rejects_capped_localization_completion_claim() -> None:
    summary = _phase47_full_execution_summary(
        localization_results={
            "status": "partial_localization_max_tags_reached",
            "executed": True,
            "target_met": True,
            "llm_called": True,
            "gap_report_generated": True,
            "proper_noun_unreviewed_aliases_trusted": False,
            "failure_budget": {"threshold_exceeded": False},
            "stopped_by_rule": "localization_max_tags_reached",
        }
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_capped_localization_claimed_complete" in _error_codes(result)


def test_phase47_s2_contract_rejects_partial_dry_run_completion_claim() -> None:
    summary = _phase47_full_execution_summary(
        dynamic_sync_dry_run={
            "status": "completed",
            "executed": True,
            "source_scope_check": {"passed": False},
        }
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_full_completion_claimed_without_full_scope_dry_run" in _error_codes(result)


def test_phase47_s2_contract_rejects_llm_call_without_audit_or_with_undercount() -> None:
    missing = _phase47_full_execution_summary(llm_localization_audit={})
    undercount = _phase47_full_execution_summary(
        llm_localization_audit={"provider_call_count_lower_bound": 1, "provider_calls_undercounted": True}
    )

    missing_result = check_phase_contract("phase47_s2_baseline_contract_v1", missing)
    undercount_result = check_phase_contract("phase47_s2_baseline_contract_v1", undercount)

    assert "phase47_s2_llm_audit_missing" in _error_codes(missing_result)
    assert "phase47_s2_llm_provider_calls_undercounted" in _error_codes(undercount_result)


def test_phase47_s2_contract_rejects_llm_background_calls_without_suppression_or_ledger() -> None:
    summary = _phase47_full_execution_summary(
        ai_tagging_results={
            "status": "completed",
            "executed": True,
            "failure_budget": {"threshold_exceeded": False},
            "auto_translation_suppressed_during_ai_stage": False,
        },
        llm_localization_audit={
            "provider_call_count_lower_bound": 1,
            "provider_calls_undercounted": False,
            "current_runner_suppresses_auto_translation_during_ai_stage": False,
            "background_provider_calls_ledgered": False,
        },
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_unledgered_background_auto_translation_not_prevented" in _error_codes(result)


def test_phase47_s2_contract_rejects_source_root_write_without_clean_identity_or_backup() -> None:
    summary = _phase47_s2_summary(
        gate0={
            "status": "blocked",
            "backup_recovery": {"proof_exists": False, "valid": False},
            "db_identity": {"matches_expected_database": False},
            "storage_identity": {"matches_expected": True},
            "schema": {
                "ensure": {"status": "not_needed", "ran": False},
                "after": {"tables_missing": []},
            },
            "input_root_registration": {"registered_count": 1},
        }
    )

    result = check_phase_contract("phase47_s2_baseline_contract_v1", summary)

    assert "phase47_s2_source_root_write_without_clean_identity_or_backup" in _error_codes(result)


def test_dynamic_library_sync_contract_accepts_s1_foundation_summary() -> None:
    result = check_phase_contract("dynamic_library_sync_contract_v1", _dynamic_sync_summary())
    assert result.passed is True


def test_dynamic_library_sync_contract_rejects_auto_production_writes() -> None:
    summary = _dynamic_sync_summary(
        dynamic_sync={
            "schema": {
                "tables": [
                    "blombooru_dynamic_source_roots",
                    "blombooru_dynamic_source_items",
                    "blombooru_dynamic_sync_runs",
                    "blombooru_dynamic_sync_run_items",
                ]
            },
            "identity": {"source_item_identity": "source_root_id + relative_path_hash"},
            "default_off_policy": {"auto_sync_enabled": True, "manual_sync_enabled": False},
            "threshold": {"default": 100},
            "pending_counts": {"visible": True},
            "dry_run_no_import": True,
            "source_root_safety": {"passed": True},
        }
    )
    result = check_phase_contract("dynamic_library_sync_contract_v1", summary)
    assert "dynamic_sync_auto_writes_enabled" in _error_codes(result)


@pytest.mark.parametrize(
    "identity",
    [
        "media_id only",
        "uuid",
        "relative_path only",
        "source_root_id only",
    ],
)
def test_dynamic_library_sync_contract_rejects_missing_source_identity_components(identity: str) -> None:
    summary = _dynamic_sync_summary(
        dynamic_sync={
            "schema": {
                "tables": [
                    "blombooru_dynamic_source_roots",
                    "blombooru_dynamic_source_items",
                    "blombooru_dynamic_sync_runs",
                    "blombooru_dynamic_sync_run_items",
                ]
            },
            "identity": {"source_item_identity": identity},
            "default_off_policy": {"auto_sync_enabled": False, "manual_sync_enabled": False},
            "threshold": {"default": 100},
            "pending_counts": {"visible": True},
            "dry_run_no_import": True,
            "source_root_safety": {"passed": True},
        },
    )
    result = check_phase_contract("dynamic_library_sync_contract_v1", summary)
    assert "dynamic_sync_missing_source_identity_components" in _error_codes(result)


def test_dynamic_library_sync_contract_rejects_proper_noun_gap() -> None:
    summary = _dynamic_sync_summary(
        proper_noun_safeguards={
            "preserved": True,
            "worker_excludes_proper_nouns": False,
            "unreviewed_llm_aliases_excluded_from_search": True,
        },
    )
    result = check_phase_contract("dynamic_library_sync_contract_v1", summary)
    codes = _error_codes(result)
    assert "dynamic_sync_required_proof_failed" in codes


def test_dynamic_library_sync_contract_rejects_completion_without_browser_passed() -> None:
    summary = _dynamic_sync_summary(
        validation={
            "focused_tests_passed": True,
            "browser_validation": {"status": "not_run"},
        },
    )

    result = check_phase_contract("dynamic_library_sync_contract_v1", summary)

    assert "dynamic_sync_browser_validation_not_passed" in _error_codes(result)


def test_production_development_separation_contract_accepts_pd1a_summary() -> None:
    result = check_phase_contract("production_development_separation_contract_v1", _pd1a_governance_summary())

    assert result.passed is True


def test_production_development_separation_fixture_passes() -> None:
    summary = load_summary_file(FIXTURE_DIR / "mock_pd1a_governance_summary.json")
    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert result.passed is True


def test_production_development_separation_rejects_production_fixtures() -> None:
    summary = _pd1a_governance_summary()
    summary["development_lane"]["production_db_as_fixture"] = True

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_development_forbidden_fixture_or_artifact" in _error_codes(result)


def test_production_development_separation_rejects_production_write_without_promotion() -> None:
    summary = _pd1a_governance_summary()
    summary["write_requests"]["production_import"] = True

    result = check_phase_contract("production_development_separation_contract_v1", summary)
    codes = _error_codes(result)

    assert "production_write_without_promotion_mode" in codes
    assert "production_write_without_operator_confirmation" in codes


def test_production_development_separation_rejects_source_root_write_without_identity_backup() -> None:
    summary = _pd1a_governance_summary()
    summary["write_requests"]["source_root_registration"] = True
    summary["production_promotion"]["enabled"] = True
    summary["production_promotion"]["operator_confirmation_present"] = True
    summary["production_identity"] = {
        "db_clean": True,
        "storage_clean": False,
        "source_roots_clean": True,
    }
    summary["backup_proof"] = {"valid": False}

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_source_root_write_gate_missing" in _error_codes(result)


def test_production_development_separation_rejects_schema_setup_when_identity_blocked() -> None:
    summary = _pd1a_governance_summary()
    summary["write_requests"]["schema_setup"] = True
    summary["production_promotion"]["enabled"] = True
    summary["production_promotion"]["operator_confirmation_present"] = True
    summary["identity_gates"] = {
        "blocked": True,
        "env_clean": True,
        "db_clean": True,
        "storage_clean": True,
    }

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_schema_setup_identity_blocked" in _error_codes(result)


def test_production_development_separation_rejects_forbidden_current_phase_authorization() -> None:
    summary = _pd1a_governance_summary()
    summary["phase_boundaries"]["authorizes_provider_calls"] = True

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_development_forbidden_current_phase_authorization" in _error_codes(result)


def test_s2g1x_probe_contract_accepts_safe_probe_and_shared_decision() -> None:
    result = check_phase_contract("s2g1x_probe_contract_v1", _s2g1x_summary())

    assert result.passed is True


def test_s2g1x_probe_contract_accepts_current_canonical_probe_summary() -> None:
    summary = load_summary_file(ROOT / "docs/reports/s2g1x-gpu-ai-tagging-probe-summary.json")

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert result.passed is True


@pytest.mark.parametrize(
    ("path", "value", "expected_code"),
    [
        (
            "capability_probe.model_identity.model_file_cached",
            False,
            "s2g1x_completion_model_load_evidence_missing",
        ),
        (
            "capability_probe.model_identity.label_file_cached",
            False,
            "s2g1x_completion_model_load_evidence_missing",
        ),
        (
            "capability_probe.model_identity.network_download_required",
            True,
            "s2g1x_completion_requires_network_model_download",
        ),
        (
            "capability_probe.provider_matrix.cpu.loaded",
            False,
            "s2g1x_completion_model_load_evidence_missing",
        ),
        (
            "capability_probe.provider_matrix.cpu.practical",
            False,
            "s2g1x_completion_model_load_evidence_missing",
        ),
        (
            "capability_probe.provider_matrix.cpu.benchmark_status",
            "model_not_cached",
            "s2g1x_completion_cpu_benchmark_not_completed",
        ),
        (
            "capability_probe.provider_matrix.cpu.throughput_items_per_second",
            0,
            "s2g1x_completion_cpu_throughput_missing",
        ),
    ],
)
def test_s2g1x_probe_contract_requires_model_loaded_evidence_for_completion(
    path: str, value: object, expected_code: str
) -> None:
    summary = _s2g1x_summary()
    _set_nested(summary, path, value)

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert result.passed is False
    assert expected_code in _error_codes(result)


def test_s2g1x_probe_contract_allows_blocked_model_unavailable_without_completion_claim() -> None:
    summary = _s2g1x_summary()
    summary["pipeline_contract"] = {
        "contract_id": "s2g1x_probe_contract_v1",
        "status": "blocked_model_unavailable",
        "claims": {"target_met": False, "safe_to_merge": False},
    }
    _set_nested(summary, "capability_probe.model_identity.model_file_cached", False)
    _set_nested(summary, "capability_probe.model_identity.label_file_cached", False)
    _set_nested(summary, "capability_probe.model_identity.network_download_required", True)
    _set_nested(summary, "capability_probe.provider_matrix.cpu.loaded", False)
    _set_nested(summary, "capability_probe.provider_matrix.cpu.practical", False)
    _set_nested(summary, "capability_probe.provider_matrix.cpu.benchmark_status", "model_not_cached")
    _set_nested(summary, "capability_probe.provider_matrix.cpu.throughput_items_per_second", None)

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert result.passed is True

    blocked_completion = copy.deepcopy(summary)
    blocked_completion["pipeline_contract"]["claims"]["safe_to_merge"] = True

    blocked_result = check_phase_contract("s2g1x_probe_contract_v1", blocked_completion)

    assert blocked_result.passed is False
    assert "s2g1x_non_completion_status_claimed_completion" in _error_codes(blocked_result)


def test_s2g1x_probe_contract_rejects_model_download_and_production_ai() -> None:
    base = _s2g1x_summary()
    summary = _s2g1x_summary(
        capability_probe={
            **base["capability_probe"],
            "safe_probe": {
                **base["capability_probe"]["safe_probe"],
                "no_model_download": False,
            },
        },
        safety={**base["safety"], "production_ai_tagging": True, "model_download": True},
    )

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)
    codes = _error_codes(result)

    assert "s2g1x_required_probe_proof_missing" in codes
    assert "s2g1x_forbidden_execution_or_mutation" in codes


def test_s2g1x_probe_contract_rejects_s3a_execution_enabled() -> None:
    base = _s2g1x_summary()
    summary = _s2g1x_summary(
        s3a_dev_dry_run_plan={
            **base["s3a_dev_dry_run_plan"],
            "production_execution_enabled": True,
        },
        s2g_s3a_decision={
            **base["s2g_s3a_decision"],
            "production_s3a_execution_enabled": True,
        },
        safety={**base["safety"], "production_s3a_execution_enabled": True},
    )

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert "s2g1x_forbidden_execution_or_mutation" in _error_codes(result)


def test_source_concept_full_chain_fails_when_llm_required_but_missing() -> None:
    summary = _source_concept_summary(llm_adjudication_used=False)

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_required_missing" in _error_codes(result)


def test_source_concept_full_chain_fails_zero_judgments_when_completion_claimed() -> None:
    summary = _source_concept_summary(llm_judgment_count=0)

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_zero_llm_judgments_full_chain" in _error_codes(result)


def test_source_concept_full_chain_fails_required_false_with_eligible_pairs() -> None:
    summary = _source_concept_summary()
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 12,
        "selected_pair_count": 12,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_required_opt_out_with_eligible_pairs" in _error_codes(result)


def test_source_concept_deterministic_only_fails_safe_to_merge_claim() -> None:
    summary = _source_concept_summary(
        pipeline_contract={"contract_id": "source_concept_full_chain_contract_v1", "status": "deterministic_only"},
        safe_to_merge=True,
        full_chain_fidelity_passed=False,
        llm_adjudication_used=False,
        llm_judgment_count=0,
        llm_provider_mode="not_applicable_deterministic_only",
    )

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "deterministic_only_claimed_completion" in _error_codes(result)


def test_source_concept_blocked_or_inconclusive_fails_safe_to_merge_claim() -> None:
    for status in ("full_chain_blocked_llm_unavailable", "full_chain_inconclusive_missing_artifacts"):
        summary = _source_concept_summary(
            pipeline_contract={"contract_id": "source_concept_full_chain_contract_v1", "status": status},
            safe_to_merge=True,
            full_chain_fidelity_passed=False,
            llm_adjudication_used=False,
            llm_judgment_count=0,
        )

        result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

        assert result.passed is False
        assert "blocked_status_claimed_completion" in _error_codes(result)


def test_source_concept_full_chain_fails_required_false_with_eligible_omitted_without_zero_proof() -> None:
    summary = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    summary["llm_adjudication_plan"] = {
        "required": False,
        "status": "ready",
        "selected_pair_count": 0,
        "max_calls": 300,
        "budget_usd": 50.0,
        "projected_budget_usd": 0.0,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_required_opt_out_without_zero_eligible_proof" in _error_codes(result)
    assert "source_concept_llm_required_missing" in _error_codes(result)


def test_source_concept_full_chain_fails_required_false_zero_eligible_without_zero_proof() -> None:
    summary = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 0,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_required_opt_out_without_zero_eligible_proof" in _error_codes(result)
    assert "source_concept_zero_llm_judgments_full_chain" in _error_codes(result)


def test_source_concept_full_chain_allows_required_false_only_with_valid_zero_eligible_proof() -> None:
    summary = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 0,
        "zero_eligible_proof": True,
        "zero_eligible_reason": "ProviderCache adapter had no eligible LLM comparison pairs after deterministic resolution.",
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is True


def test_source_concept_full_chain_fails_eligible_pair_count_over_max_calls() -> None:
    summary = _source_concept_summary()
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "eligible_pair_count": 301,
        "selected_pair_count": 300,
        "max_calls": 300,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_call_cap_exceeded" in _error_codes(result)


def test_source_concept_full_chain_fails_selected_pair_count_over_max_calls() -> None:
    summary = _source_concept_summary()
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "eligible_pair_count": 12,
        "selected_pair_count": 301,
        "max_calls": 300,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_selected_call_cap_exceeded" in _error_codes(result)


def test_source_concept_full_chain_fails_judgment_count_over_zero_max_calls() -> None:
    summary = _source_concept_summary(llm_judgment_count=300, llm_max_calls=0)
    summary["llm_adjudication_plan"] = {
        **summary["llm_adjudication_plan"],
        "max_calls": 0,
    }

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_llm_judgment_call_cap_exceeded" in _error_codes(result)


def test_source_concept_full_chain_fails_judgment_count_over_max_calls_unless_approved() -> None:
    failing = _source_concept_summary(llm_judgment_count=301)
    failing["llm_adjudication_plan"] = {
        **failing["llm_adjudication_plan"],
        "max_calls": 300,
    }
    approved = _source_concept_summary(llm_judgment_count=301)
    approved["llm_adjudication_plan"] = {
        **approved["llm_adjudication_plan"],
        "max_calls": 300,
        "explicit_over_budget_or_call_cap_approval": True,
    }

    fail_result = check_phase_contract("source_concept_full_chain_contract_v1", failing)
    approved_result = check_phase_contract("source_concept_full_chain_contract_v1", approved)

    assert fail_result.passed is False
    assert "source_concept_llm_judgment_call_cap_exceeded" in _error_codes(fail_result)
    assert approved_result.passed is True


def test_source_concept_full_chain_rejects_partial_llm_pair_resolution() -> None:
    partial = _source_concept_summary(llm_judgment_count=1)
    complete = _source_concept_summary(llm_judgment_count=12)
    cached = _source_concept_summary(llm_judgment_count=10, llm_cache_summary={"cached_decision_count": 2})
    missing_cache = _source_concept_summary(llm_judgment_count=10)

    partial_result = check_phase_contract("source_concept_full_chain_contract_v1", partial)
    complete_result = check_phase_contract("source_concept_full_chain_contract_v1", complete)
    cached_result = check_phase_contract("source_concept_full_chain_contract_v1", cached)
    missing_cache_result = check_phase_contract("source_concept_full_chain_contract_v1", missing_cache)

    assert partial_result.passed is False
    assert "source_concept_llm_selected_pairs_not_resolved" in _error_codes(partial_result)
    assert complete_result.passed is True
    assert cached_result.passed is True
    assert missing_cache_result.passed is False
    assert "source_concept_llm_selected_pairs_not_resolved" in _error_codes(missing_cache_result)


def test_source_concept_full_chain_fails_missing_llm_counters() -> None:
    missing_eligible = _source_concept_summary()
    missing_eligible["llm_adjudication_plan"] = dict(missing_eligible["llm_adjudication_plan"])
    missing_eligible["llm_adjudication_plan"].pop("eligible_pair_count")
    missing_selected = _source_concept_summary()
    missing_selected["llm_adjudication_plan"] = dict(missing_selected["llm_adjudication_plan"])
    missing_selected["llm_adjudication_plan"].pop("selected_pair_count")
    missing_judgments = _source_concept_summary()
    missing_judgments.pop("llm_judgment_count")

    for summary in (missing_eligible, missing_selected, missing_judgments):
        result = check_phase_contract("source_concept_full_chain_contract_v1", summary)
        assert result.passed is False
        assert "source_concept_missing_llm_counter" in _error_codes(result)


def test_source_concept_zero_eligible_proof_requires_consistent_counters() -> None:
    selected_nonzero = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    selected_nonzero["llm_adjudication_plan"] = {
        **selected_nonzero["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 1,
        "zero_eligible_proof": True,
        "zero_eligible_reason": "No eligible pairs.",
    }
    judgments_nonzero = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=1)
    judgments_nonzero["llm_adjudication_plan"] = {
        **judgments_nonzero["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 0,
        "zero_eligible_proof": True,
        "zero_eligible_reason": "No eligible pairs.",
    }
    no_reason = _source_concept_summary(llm_adjudication_used=False, llm_judgment_count=0)
    no_reason["llm_adjudication_plan"] = {
        **no_reason["llm_adjudication_plan"],
        "required": False,
        "eligible_pair_count": 0,
        "selected_pair_count": 0,
        "zero_eligible_proof": True,
    }

    for summary in (selected_nonzero, judgments_nonzero, no_reason):
        result = check_phase_contract("source_concept_full_chain_contract_v1", summary)
        assert result.passed is False
        assert "source_concept_zero_eligible_proof_incomplete" in _error_codes(result)


def test_source_concept_full_chain_fails_missing_validation_pack() -> None:
    summary = _source_concept_summary()
    summary.pop("validation_pack")

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "source_concept_required_proof_missing" in _error_codes(result)


def test_source_concept_deterministic_only_allowed_only_without_completion_claim() -> None:
    executed = [
        stage
        for stage in SOURCE_CONCEPT_FULL_CHAIN_STAGES
        if not stage.startswith("llm_") and not stage.startswith("bounded_llm")
    ]
    summary = _source_concept_summary(
        pipeline_contract={"contract_id": "source_concept_full_chain_contract_v1", "status": "deterministic_only"},
        executed_stages=executed,
        missing_required_stages=[stage for stage in SOURCE_CONCEPT_FULL_CHAIN_STAGES if stage not in executed],
        full_chain_fidelity_passed=False,
        llm_adjudication_used=False,
        llm_judgment_count=0,
        llm_max_calls=0,
        llm_budget_usd=0.0,
        llm_provider_mode="not_applicable_deterministic_only",
        llm_cache_summary={"cache_enabled": False},
        conclusion="deterministic_only",
    )

    allowed = check_phase_contract("source_concept_full_chain_contract_v1", summary)
    assert allowed.passed is True
    assert len(allowed.warnings) >= 1

    summary["pipeline_contract"] = {
        "contract_id": "source_concept_full_chain_contract_v1",
        "status": "deterministic_only",
        "claims": {"full_chain_complete": True},
    }
    blocked = check_phase_contract("source_concept_full_chain_contract_v1", summary)
    assert blocked.passed is False
    assert "deterministic_only_claimed_completion" in _error_codes(blocked)


def test_source_concept_blocked_required_stage_does_not_count_as_completed() -> None:
    for stage_status in ("blocked_before_write", "skipped"):
        summary = _source_concept_summary(
            stages={"llm_provider_availability_check": {"status": stage_status}},
        )

        result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

        assert result.passed is False
        assert "source_concept_required_stage_missing" in _error_codes(result)


def test_source_concept_blocked_status_with_blocked_stage_cannot_claim_safe_to_merge() -> None:
    summary = _source_concept_summary(
        pipeline_contract={"contract_id": "source_concept_full_chain_contract_v1", "status": "full_chain_blocked_llm_unavailable"},
        safe_to_merge=True,
        stages={"llm_provider_availability_check": {"status": "blocked_before_write"}},
        full_chain_fidelity_passed=False,
        llm_adjudication_used=False,
        llm_judgment_count=0,
    )

    result = check_phase_contract("source_concept_full_chain_contract_v1", summary)

    assert result.passed is False
    assert "blocked_status_claimed_completion" in _error_codes(result)


def test_forbidden_stage_executed_true_fails_even_with_negative_status() -> None:
    provider = check_phase_contract(
        "source_concept_full_chain_contract_v1",
        {"stages": {"provider_enrichment_call": {"executed": True, "status": "skipped"}}},
    )
    upload = check_phase_contract(
        "source_concept_full_chain_contract_v1",
        {"stages": {"image_upload": {"executed": True, "status": "blocked"}}},
    )
    not_executed = check_phase_contract(
        "source_concept_full_chain_contract_v1",
        {"stages": {"image_upload": {"executed": False, "status": "skipped"}}},
    )

    assert provider.passed is False
    assert "forbidden_stage_executed" in _error_codes(provider)
    assert upload.passed is False
    assert "forbidden_stage_executed" in _error_codes(upload)
    assert "forbidden_stage_executed" not in _error_codes(not_executed)


def test_route_audit_blocks_route_approval_if_upstream_pipeline_incomplete() -> None:
    summary = _route_audit_summary(final_route_decision_status="route_approved", route_approved=True)

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert result.route_approved is True
    assert "route_approval_upstream_incomplete" in _error_codes(result)


def test_route_audit_route_status_takes_priority_over_pipeline_status() -> None:
    summary = _route_audit_summary(
        pipeline_contract={"contract_id": "route_audit_contract_v1", "status": "passed"},
        final_route_decision_status="route_approved",
        upstream_pipeline_contract=_route_full_chain_upstream(status="deterministic_only"),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.route_approved is True
    assert result.status == "route_approved"
    assert result.passed is False
    assert "route_approval_upstream_blocked_or_deterministic" in _error_codes(result)


def test_route_audit_route_status_approval_requires_review_pack_even_with_pipeline_passed() -> None:
    summary = _route_audit_summary(
        pipeline_contract={"contract_id": "route_audit_contract_v1", "status": "passed"},
        final_route_decision_status="route_approved",
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )
    summary.pop("chatgpt_review_pack")

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.route_approved is True
    assert result.passed is False
    assert "route_audit_route_approval_missing_review_pack" in _error_codes(result)


def test_route_audit_inconclusive_status_cannot_claim_route_approved() -> None:
    summary = _route_audit_summary(
        final_route_decision_status="inconclusive_missing_artifacts",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_approval_blocked_or_provisional_status" in _error_codes(result)


def test_route_audit_fails_blocked_status_with_route_approved_true() -> None:
    summary = _route_audit_summary(
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_approval_blocked_or_provisional_status" in _error_codes(result)


def test_route_audit_fails_mutation_proof_false() -> None:
    summary = _route_audit_summary(mutation_proof={"passed": False, "changed_tables": []})

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_audit_mutation_proof_failed" in _error_codes(result)


def test_route_audit_requires_positive_mutation_proof_for_blocked_routes() -> None:
    empty = check_phase_contract("route_audit_contract_v1", _route_audit_summary(mutation_proof={}))
    changed_only = check_phase_contract("route_audit_contract_v1", _route_audit_summary(mutation_proof={"changed_tables": []}))
    passed = check_phase_contract("route_audit_contract_v1", _route_audit_summary(mutation_proof={"passed": True, "changed_tables": []}))

    assert empty.passed is False
    assert "route_audit_mutation_proof_failed" in _error_codes(empty)
    assert changed_only.passed is False
    assert "route_audit_mutation_proof_failed" in _error_codes(changed_only)
    assert passed.passed is True


def test_route_audit_fails_forbidden_mutation_tables() -> None:
    summary = _route_audit_summary(
        mutation_proof={"passed": True, "forbidden_changed_tables": ["blombooru_media_tags"], "unexpected_changed_tables": []}
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_audit_mutation_forbidden_table_changed" in _error_codes(result)


def test_route_audit_fails_route_approved_without_review_pack() -> None:
    summary = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )
    summary.pop("chatgpt_review_pack")

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_audit_route_approval_missing_review_pack" in _error_codes(result)


def test_route_audit_route_approved_requires_complete_review_pack_proof() -> None:
    generated_only = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
        chatgpt_review_pack={"generated": True},
    )
    complete = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
        chatgpt_review_pack=_complete_review_pack_proof(),
    )

    generated_only_result = check_phase_contract("route_audit_contract_v1", generated_only)
    complete_result = check_phase_contract("route_audit_contract_v1", complete)

    assert generated_only_result.passed is False
    assert "route_audit_route_approval_incomplete_review_pack" in _error_codes(generated_only_result)
    assert complete_result.passed is True


def test_route_audit_fails_route_approved_with_deterministic_only_upstream() -> None:
    summary = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(status="deterministic_only"),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "route_approval_upstream_not_full_chain_completed" in _error_codes(result)
    assert "route_approval_upstream_blocked_or_deterministic" in _error_codes(result)


def test_route_audit_allows_route_approved_with_full_chain_upstream() -> None:
    summary = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(),
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is True
    assert result.route_approved is True


def test_route_audit_requires_upstream_contract_passed_and_missing_stages_list() -> None:
    passed_false = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract=_route_full_chain_upstream(passed=False, full_chain_fidelity_passed=True),
    )
    missing_absent = _route_audit_summary(
        final_route_decision_status="route_approved",
        route_approved=True,
        upstream_pipeline_contract={
            "contract_id": "source_concept_full_chain_contract_v1",
            "passed": True,
            "status": "full_chain_completed",
            "full_chain_fidelity_passed": True,
        },
    )

    passed_false_result = check_phase_contract("route_audit_contract_v1", passed_false)
    missing_absent_result = check_phase_contract("route_audit_contract_v1", missing_absent)

    assert passed_false_result.passed is False
    assert "route_approval_upstream_contract_not_passed" in _error_codes(passed_false_result)
    assert missing_absent_result.passed is False
    assert "route_approval_upstream_missing_required_stages_absent" in _error_codes(missing_absent_result)


def test_review_pack_contract_fails_missing_manifest_checksum_redaction_scan() -> None:
    result = check_phase_contract("review_pack_contract_v1", {"review_pack": {"generated": True}})

    assert result.passed is False
    assert "review_pack_required_flag_missing" in _error_codes(result)
    assert "review_pack_checksum_count_missing" in _error_codes(result)


def test_review_pack_contract_fails_missing_public_report_copy_proof() -> None:
    summary = _review_pack_summary(
        public_report_copy_current=False,
        public_report_copy_fresh=False,
        public_report_copy_rendered_from_current_summary=False,
        public_report_copy_generated_from_current_summary=False,
    )

    result = check_phase_contract("review_pack_contract_v1", summary)

    assert result.passed is False
    assert "review_pack_public_report_copy_missing" in _error_codes(result)


def test_review_pack_public_report_copy_must_be_current() -> None:
    present_only = _review_pack_summary(
        public_report_copy_present=True,
        public_report_copy_current=False,
        public_report_copy_fresh=False,
        public_report_copy_rendered_from_current_summary=False,
        public_report_copy_generated_from_current_summary=False,
    )
    current = _review_pack_summary(public_report_copy_present=True, public_report_copy_current=True)
    rendered = _review_pack_summary(
        public_report_copy_current=False,
        public_report_copy_rendered_from_current_summary=True,
    )

    present_only_result = check_phase_contract("review_pack_contract_v1", present_only)
    current_result = check_phase_contract("review_pack_contract_v1", current)
    rendered_result = check_phase_contract("review_pack_contract_v1", rendered)

    assert present_only_result.passed is False
    assert "review_pack_public_report_copy_missing" in _error_codes(present_only_result)
    assert current_result.passed is True
    assert rendered_result.passed is True


def test_review_pack_contract_fails_fixed_salt_hashes_or_raw_labels() -> None:
    summary = _review_pack_summary(review_samples=[{"raw_label": "private filename label"}], fixed_salt_hash="abc123")

    result = check_phase_contract("review_pack_contract_v1", summary)

    assert result.passed is False
    assert "review_pack_private_label_leak" in _error_codes(result)


def test_public_redaction_contract_catches_markdown_and_json_leaks() -> None:
    summary = {
        "public_markdown_text": r"Leaked local path C:\Users\example\Pictures\secret.png",
        "public_json_payload": {"source_path": "/Users/example/private/secret.png"},
    }

    result = check_phase_contract("public_redaction_contract_v1", summary)

    assert result.passed is False
    assert any(error.code.startswith("public_redaction_") for error in result.errors)


def test_public_redaction_contract_catches_bare_filenames_in_markdown() -> None:
    result = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "sample IMG_1234.JPG leaked"})

    assert result.passed is False
    assert "public_redaction_bare_filename" in _error_codes(result)


def test_public_redaction_contract_does_not_echo_sensitive_matches() -> None:
    leaks = [
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "github_pat_abcdefghijklmnopqrstuvwxyz123456",
        "xoxb-1234567890-abcdefghijk",
        r"C:\Users\name\secret.png",
        "/tmp/private/file.png",
        "IMG_1234.JPG",
    ]

    for leak in leaks:
        result = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": f"leak {leak}"})
        serialized = _serialized_result(result)
        assert result.passed is False, leak
        assert leak not in serialized
        assert "[redacted-match]" in serialized


def test_public_redaction_contract_sanitizes_sensitive_json_key_paths() -> None:
    payloads = [
        {r"C:\Users\name\secret.png": "value"},
        {"/tmp/private/file.png": "value"},
        {"IMG_1234.JPG": "value"},
        {"source_url": {"https://example.com/source/123": "value"}},
    ]
    leaks = [
        r"C:\Users\name\secret.png",
        "/tmp/private/file.png",
        "IMG_1234.JPG",
        "https://example.com/source/123",
    ]

    for payload, leak in zip(payloads, leaks):
        result = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": payload})
        serialized = _serialized_result(result)
        assert result.passed is False, leak
        assert leak not in serialized
        assert "[redacted-key]" in serialized or "[redacted-match]" in serialized


def test_public_redaction_contract_catches_sensitive_filename_json_keys() -> None:
    result = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"raw_filename": "IMG_1234.JPG"}})

    assert result.passed is False
    codes = _error_codes(result)
    assert "public_redaction_bare_filename" in codes
    assert "public_redaction_private_provenance_value_unredacted" in codes


def test_public_redaction_contract_catches_sensitive_public_urls_unless_redacted() -> None:
    leaked = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"source_url": "https://example.invalid/post/1"}})
    redacted = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"source_url": "[redacted]"}})

    assert leaked.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(leaked)
    assert redacted.passed is True


def test_public_redaction_contract_allows_public_api_route_text() -> None:
    api_route = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "GET /api/admin/media"})
    generic_route = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "/foo/bar"})
    provenance_route = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"source_path": "/foo/bar"}})
    tmp_path = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "/tmp/private/file.png"})
    workspace_path = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": "/workspace/VIOLET/.env"})

    assert api_route.passed is True
    assert generic_route.passed is True
    assert provenance_route.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(provenance_route)
    assert tmp_path.passed is False
    assert workspace_path.passed is False


def test_public_redaction_contract_scans_sensitive_non_string_values() -> None:
    api_key_number = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"api_key": 123456}})
    password_bool = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"password": True}})
    source_url_number = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"source_url": 123456}})
    redacted = check_phase_contract("public_redaction_contract_v1", {"public_json_payload": {"api_key": "[redacted]"}})

    assert api_key_number.passed is False
    assert "public_redaction_secret_key_name_with_unredacted_value" in _error_codes(api_key_number)
    assert "123456" not in _serialized_result(api_key_number)
    assert password_bool.passed is False
    assert "public_redaction_secret_key_name_with_unredacted_value" in _error_codes(password_bool)
    assert source_url_number.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(source_url_number)
    assert redacted.passed is True


def test_public_redaction_contract_propagates_private_provenance_context() -> None:
    leaked_url = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"source_url": {"value": "https://example.com/x"}}},
    )
    redacted_url = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"source_url": {"value": "[redacted]"}}},
    )
    leaked_filename = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"raw_filename": {"value": "IMG_1234.JPG"}}},
    )

    assert leaked_url.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(leaked_url)
    assert redacted_url.passed is True
    assert leaked_filename.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(leaked_filename)
    assert "public_redaction_bare_filename" in _error_codes(leaked_filename)


def test_public_redaction_contract_propagates_secret_parent_context() -> None:
    leaked = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"api_key": {"value": "internal-prod-key"}}},
    )
    redacted = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"api_key": {"value": "[redacted]"}}},
    )

    assert leaked.passed is False
    assert "public_redaction_secret_key_name_with_unredacted_value" in _error_codes(leaked)
    assert "internal-prod-key" not in _serialized_result(leaked)
    assert redacted.passed is True


def test_public_redaction_contract_catches_secret_key_names_and_token_formats() -> None:
    summary = {"public_json_payload": {"api_key": "sk-testsecret12345", "auth": "Authorization: Bearer abcdefghijk"}}

    result = check_phase_contract("public_redaction_contract_v1", summary)

    assert result.passed is False
    codes = _error_codes(result)
    assert "public_redaction_secret_key_name_with_unredacted_value" in codes
    assert "public_redaction_common_secret_or_token" in codes


def test_public_redaction_contract_catches_bare_token_formats() -> None:
    leaks = [
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "github_pat_abcdefghijklmnopqrstuvwxyz123456",
        "xoxb-1234567890-abcdefghijk",
    ]

    for leak in leaks:
        result = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": f"token {leak}"})
        assert result.passed is False, leak
        assert "public_redaction_common_secret_or_token" in _error_codes(result)


def test_public_redaction_contract_catches_private_path_shapes() -> None:
    leaks = [
        r"D:\library\private.png",
        r"\\nas-host\share\private.png",
        "file:///Users/example/private.png",
        "/Users/example/private.png",
        "/home/example/private.png",
        "/mnt/nas/private.png",
        "/Volumes/Archive/private.png",
        "/tmp/private.png",
        "/workspace/private.png",
        "/workspace/VIOLET/.env",
        "/opt/private.png",
        "/var/private.png",
        "/tmp/private/file.png",
    ]

    for leak in leaks:
        result = check_phase_contract("public_redaction_contract_v1", {"public_markdown_text": f"leak {leak}"})
        assert result.passed is False, leak


def test_mutation_safety_contract_fails_unexpected_forbidden_table_changes() -> None:
    summary = {
        "mutation_proof": {
            "passed": False,
            "forbidden_changed_tables": ["blombooru_media_tags"],
            "unexpected_changed_tables": [{"table": "blombooru_media", "allowed": False}],
        }
    }

    result = check_phase_contract("mutation_safety_contract_v1", summary)

    assert result.passed is False
    assert "mutation_forbidden_table_changed" in _error_codes(result)
    assert "mutation_unexpected_table_changed" in _error_codes(result)


def test_mutation_safety_contract_fails_false_passed_without_table_deltas() -> None:
    result = check_phase_contract("mutation_safety_contract_v1", {"mutation_proof": {"passed": False, "changed_tables": []}})

    assert result.passed is False
    assert "mutation_proof_failed" in _error_codes(result)


def test_mutation_safety_contract_requires_positive_passed_proof() -> None:
    empty = check_phase_contract("mutation_safety_contract_v1", {"mutation_proof": {}})
    missing = check_phase_contract("mutation_safety_contract_v1", {"mutation_proof": {"changed_tables": []}})
    passed = check_phase_contract("mutation_safety_contract_v1", {"mutation_proof": {"passed": True, "changed_tables": []}})

    assert empty.passed is False
    assert "mutation_proof_failed" in _error_codes(empty)
    assert missing.passed is False
    assert "mutation_proof_failed" in _error_codes(missing)
    assert passed.passed is True


def test_mutation_safety_contract_fails_non_list_table_violations() -> None:
    forbidden_string = check_phase_contract(
        "mutation_safety_contract_v1",
        {"mutation_proof": {"passed": True, "forbidden_changed_tables": "media_tags"}},
    )
    unexpected_dict = check_phase_contract(
        "mutation_safety_contract_v1",
        {"mutation_proof": {"passed": True, "unexpected_changed_tables": {"table": "media_tags"}}},
    )

    assert forbidden_string.passed is False
    assert "mutation_forbidden_table_changed" in _error_codes(forbidden_string)
    assert unexpected_dict.passed is False
    assert "mutation_unexpected_table_changed" in _error_codes(unexpected_dict)


def test_artifact_lifecycle_contract_distinguishes_public_and_private_artifacts() -> None:
    good = {
        "artifact_lifecycle": {
            "artifacts": [
                {"path": "docs/reports/gov3.md", "classification": "public report/handoff", "committed": True, "redacted": True},
                {"path": ".local_manifests/gov3", "classification": "one-off local/private", "committed": False},
            ]
        }
    }
    bad = {
        "artifact_lifecycle": {
            "artifacts": [
                {"path": ".local_manifests/private.zip", "classification": "one-off local/private", "committed": True}
            ]
        }
    }

    assert check_phase_contract("artifact_lifecycle_contract_v1", good).passed is True
    result = check_phase_contract("artifact_lifecycle_contract_v1", bad)
    assert result.passed is False
    assert "private_artifact_committed" in _error_codes(result)


def test_artifact_lifecycle_contract_requires_public_redaction_evidence() -> None:
    missing_redaction = {
        "artifact_lifecycle": {
            "artifacts": [{"path": "docs/reports/gov3.md", "classification": "public report/handoff", "committed": True}]
        }
    }
    redacted = {
        "artifact_lifecycle": {
            "artifacts": [
                {"path": "docs/reports/gov3.md", "classification": "public report/handoff", "committed": True, "redacted": True}
            ]
        }
    }

    missing_result = check_phase_contract("artifact_lifecycle_contract_v1", missing_redaction)
    redacted_result = check_phase_contract("artifact_lifecycle_contract_v1", redacted)

    assert missing_result.passed is False
    assert "public_artifact_redaction_evidence_missing" in _error_codes(missing_result)
    assert redacted_result.passed is True


def test_artifact_lifecycle_contract_normalizes_review_pack_classification() -> None:
    for classification in ("review pack", "review_pack", "review-pack"):
        result = check_phase_contract(
            "artifact_lifecycle_contract_v1",
            {"artifact_lifecycle": {"artifacts": [{"path": ".local_manifests/pack.zip", "classification": classification, "committed": True}]}},
        )
        assert result.passed is False
        assert "review_pack_committed" in _error_codes(result)


def test_artifact_lifecycle_contract_normalizes_public_classification() -> None:
    for classification in ("public_report", "public-report", "public handoff", "public_handoff", "public-handoff"):
        result = check_phase_contract(
            "artifact_lifecycle_contract_v1",
            {"artifact_lifecycle": {"artifacts": [{"path": "docs/reports/gov3.md", "classification": classification, "committed": True}]}},
        )
        assert result.passed is False
        assert "public_artifact_redaction_evidence_missing" in _error_codes(result)

    passed = check_phase_contract(
        "artifact_lifecycle_contract_v1",
        {"artifact_lifecycle": {"artifacts": [{"path": "docs/reports/gov3.md", "classification": "public report", "committed": True, "redacted": True}]}},
    )
    assert passed.passed is True


def test_required_summary_fields_do_not_accept_null_values() -> None:
    result = check_phase_contract(
        "python_env_contract_v1",
        {
            "python_env": {
                "expected_python_checked": None,
                "check_python_env_passed": True,
                "public_executable_name": "python.exe",
                "executable_path_redacted": True,
            }
        },
    )

    assert result.passed is False
    assert "missing_required_summary_field" in _error_codes(result)


def test_claimed_completion_requires_matching_contract_id() -> None:
    result = check_phase_contract(
        "route_audit_contract_v1",
        _route_audit_summary(
            pipeline_contract={"contract_id": "wrong_contract_v1"},
            final_route_decision_status="route_approved",
            route_approved=True,
            upstream_pipeline_contract=_route_full_chain_upstream(),
        ),
    )

    assert result.passed is False
    assert "claimed_completion_contract_id_mismatch" in _error_codes(result)


def test_public_redaction_contract_allows_auxiliary_check_on_claimed_phase_summary() -> None:
    result = check_phase_contract(
        "public_redaction_contract_v1",
        {
            "pipeline_contract": {
                "contract_id": "phase47_s2_baseline_contract_v1",
                "status": "target_met",
                "claims": {
                    "target_met": True,
                    "safe_to_merge": True,
                    "full_chain_complete": True,
                },
            },
            "public_json_payload": {
                "status": "target_met",
                "paths_redacted": True,
            },
        },
    )

    assert result.passed is True


def test_required_artifact_and_ledger_fields_must_be_non_empty() -> None:
    source_metadata_base = {
        "provider_policy": {"explicitly_approved": True},
        "provider_identity": {"no_secret_logging": True},
        "request_ledger": {"entry_count": 1},
        "failure_ledger": {"entry_count": 0, "zero_failure_reason": "No provider calls failed."},
        "cache_retry_rate_limit_accounting": {"passed": True},
        "source_metadata_write_allowlist": {"passed": True},
        "entity_truth_proof": {"no_entity_truth": True},
        "media_tags_mutation_proof": {"no_media_tags_mutation": True},
        "image_upload_policy": {"uploaded_images": False},
        "public_private_artifact_boundary": {"passed": True},
    }
    empty_request = {**source_metadata_base, "request_ledger": {}}
    empty_failure = {**source_metadata_base, "failure_ledger": {}}
    media_import_null = {
        "source_root_safety_proof": {"passed": True},
        "staging_root_safety_proof": {"passed": True},
        "import_ledger": None,
        "media_counts": {"before": 0, "after": 0},
        "duplicate_path_leak_proof": {"passed": True},
        "mutation_proof": {"passed": True},
        "rollback_recovery_notes": "No import run.",
    }
    media_import_empty = {**media_import_null, "import_ledger": {}}

    empty_request_result = check_phase_contract("source_metadata_contract_v1", empty_request)
    empty_failure_result = check_phase_contract("source_metadata_contract_v1", empty_failure)
    null_import_result = check_phase_contract("media_import_contract_v1", media_import_null)
    empty_import_result = check_phase_contract("media_import_contract_v1", media_import_empty)

    assert "empty_required_artifact_or_proof" in _error_codes(empty_request_result)
    assert "empty_required_artifact_or_proof" in _error_codes(empty_failure_result)
    assert "missing_required_summary_field" in _error_codes(null_import_result)
    assert "empty_required_artifact_or_proof" in _error_codes(empty_import_result)


def test_postgres_db_contract_rejects_nested_password_fields() -> None:
    summary = {
        "db_identity": {
            "db_resolution": {
                "runner_matches_app_equivalent": True,
                "password_value_recorded": False,
                "password": "secret-db-password",
            }
        }
    }

    result = check_phase_contract("postgres_db_contract_v1", summary)

    assert result.passed is False
    assert "db_secret_field_recorded" in _error_codes(result)


def test_postgres_db_contract_allows_password_presence_boolean_without_value() -> None:
    summary = {
        "db_identity": {
            "db_resolution": {
                "runner_matches_app_equivalent": True,
                "password_present": True,
                "password_value_recorded": False,
            }
        }
    }

    result = check_phase_contract("postgres_db_contract_v1", summary)

    assert result.passed is True


def test_destructive_operation_contract_fails_without_explicit_approval() -> None:
    summary = {
        "destructive_operation": {
            "dry_run_first": True,
            "backup_recovery_plan": True,
            "exact_target_set": True,
            "no_broad_wildcard_deletion": True,
            "post_run_verification": True,
        }
    }

    result = check_phase_contract("destructive_operation_contract_v1", summary)

    assert result.passed is False
    assert "destructive_operation_gate_missing" in _error_codes(result)


def test_entity_truth_bridge_contract_fails_without_preview_manual_audit_rollback_gates() -> None:
    result = check_phase_contract("entity_truth_bridge_contract_v1", {"entity_truth_bridge": {"route_approval": True}})

    assert result.passed is False
    assert "entity_truth_bridge_gate_missing" in _error_codes(result)


def test_existing_a1_and_inc1_summaries_remain_blocked_not_route_approved() -> None:
    a1 = json.loads((ROOT / "docs/reports/phase-4.5-scv2-a1-post-expansion-audit-route-decision-summary.json").read_text(encoding="utf-8"))
    inc1 = json.loads((ROOT / "docs/reports/phase-4.5-scv2-inc1-source-concept-pipeline-fidelity-summary.json").read_text(encoding="utf-8"))

    a1_result = check_phase_contract("route_audit_contract_v1", a1)
    inc1_redaction = check_phase_contract("public_redaction_contract_v1", inc1)

    assert a1_result.route_approved is False
    assert "blocked" in str(a1_result.status)
    assert inc1_redaction.route_approved is False
    assert inc1["llm_adjudication_fidelity"]["conclusion"] == "llm_stage_missing_incident"


def test_mock_future_r1r_summary_passes_only_with_all_stages_and_llm_proof() -> None:
    passing = load_summary_file(FIXTURE_DIR / "mock_source_concept_full_chain_pass.json")
    failing = dict(passing)
    failing["executed_stages"] = [stage for stage in passing["executed_stages"] if stage != "llm_cache_accounting"]
    failing["missing_required_stages"] = ["llm_cache_accounting"]

    pass_result = check_phase_contract("source_concept_full_chain_contract_v1", passing)
    fail_result = check_phase_contract("source_concept_full_chain_contract_v1", failing)

    assert pass_result.passed is True
    assert fail_result.passed is False
    assert "source_concept_required_stage_missing" in _error_codes(fail_result)
