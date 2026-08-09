"""Tests for executable phase contracts and phase gates."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_contracts import REQUIRED_CONTRACT_IDS, check_phase_contract, get_contract, list_contracts, load_summary_file  # noqa: E402
from scripts.phase_contracts import contract_checks as contract_checks_module  # noqa: E402
from scripts.phase_contracts.contract_registry import (  # noqa: E402
    R1R_FULL_SOURCE_CONCEPT_PIPELINE_STAGES,
    SOURCE_CONCEPT_FULL_CHAIN_STAGES,
)


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "phase_contracts"


def test_fl1_p1_registry_requires_all_late_review_safety_stages() -> None:
    contract = get_contract("scv2_fl1_isolated_full_library_dev_test_contract_v1")

    assert contract.required_stages == (
        "environment_isolation_preflight",
        "mutation_default_deny",
        "stable_inventory_identity",
        "restartable_item_ledger",
        "interrupted_mutation_reconciliation",
        "failure_budget_and_manual_stop",
        "forbidden_operation_evidence",
    )
    assert "stage_evidence" in contract.required_summary_fields
    assert "implementation_evidence" in contract.required_summary_fields


def test_public_payload_redaction_rejects_content_hash_keys() -> None:
    findings = contract_checks_module.scan_public_payload(
        {"public": {"content_hash": "[redacted]", "nested": {"sha256": "[redacted]"}}}
    )

    assert {finding["code"] for finding in findings} >= {"private_content_hash_key_present"}


def _current_test_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "test-head"


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


def _r1r_stage_manifest(*, omit: set[str] | None = None, executed_without_label: str | None = None) -> list[dict]:
    omitted = omit or set()
    rows = []
    for stage in R1R_FULL_SOURCE_CONCEPT_PIPELINE_STAGES:
        if stage in omitted:
            continue
        row = {
            "stage_name": stage,
            "required": True,
            "requested": True,
            "configured": True,
            "executed": True,
            "skipped": False,
            "skip_reason": None,
            "input_count": 1,
            "output_count": 1,
            "evidence_artifact_label": f"r1r-private-{stage}",
            "public_safe_summary_fields": ["count"],
            "private_artifact_label": f"r1r-private-{stage}",
            "contract_check_name": stage,
            "status": "verified",
        }
        if stage == "provider_cache_adapter_or_zero_eligible_proof":
            row["input_count"] = 0
            row["output_count"] = 0
            row["zero_eligible_proof"] = True
            row["status"] = "skipped_not_applicable"
            row["executed"] = False
            row["skipped"] = True
            row["skip_reason"] = "not_in_input_scope_zero_eligible"
        if stage == executed_without_label:
            row["evidence_artifact_label"] = ""
        rows.append(row)
    return rows


def _r1r_input_scope_rows(overrides: dict[str, int] | None = None) -> list[dict]:
    expected_values = {
        "total_media": 3750,
        "eligible_media": 3687,
        "source_metadata_records_total": 671,
        "px1_source_metadata_records": 471,
        "source_tag_observations": 3727,
        "source_name_observations": 918,
        "source_searchable_name_assertions": 918,
        "source_metadata_evidence": 3727,
        "resolver_input_signals": 12249,
        "deterministic_edge_count": 42751,
        "source_concept_replay_total": 2887,
        "source_concept_replay_active": 1078,
        "source_concept_replay_needs_review": 1809,
        "source_concept_total": 6094,
        "source_concept_active": 1078,
        "source_concept_needs_review": 1809,
        "source_concept_superseded": 3207,
        "llm_eligible_pair_count": 12,
        "llm_selected_pair_count": 12,
    }
    actual_values = dict(expected_values)
    if overrides:
        actual_values.update(overrides)
    baseline_only = {
        "source_concept_total",
        "source_concept_active",
        "source_concept_needs_review",
        "source_concept_superseded",
    }
    rows = []
    for metric, expected in expected_values.items():
        actual = int(actual_values[metric])
        ratio = round(actual / expected, 4) if expected else None
        rows.append(
            {
                "metric": metric,
                "category": "old_r1_persisted_baseline_scale"
                if metric in baseline_only
                else (
                    "current_r1r_replay_output_scale"
                    if metric.startswith("source_concept_replay_")
                    else ("llm_selected_accounting" if metric.startswith("llm_") else "input_data_scale")
                ),
                "required_for_route_evidence": metric not in baseline_only,
                "old_r1_expected": expected,
                "current_r1r_actual": actual,
                "ratio": ratio,
                "status": "matched" if ratio is not None and ratio >= 0.8 else "insufficient",
            }
        )
    return rows


def _r1r_summary(**overrides: object) -> dict:
    summary = {
        "pipeline_contract": {
            "contract_id": "r1r_full_source_concept_pipeline_contract_v1",
            "status": "target_met_full_chain",
            "claims": {"target_met": True, "full_chain_complete": True, "safe_to_merge": False},
        },
        "environment_isolation": {
            "passed": True,
            "blockers": [],
            "violet_env": "test",
            "violet_env_is_production": False,
            "production_profile_active": False,
            "db_name": "blombooru_test",
            "db_target_is_production": False,
            "dev_test_restored_snapshot_db_used": True,
            "storage_root_is_production": False,
            "storage_root_pre_settings_import": {
                "checked_before_settings_import": True,
                "passed": True,
                "blocked_count": 0,
                "no_directories_created_before_gate": True,
            },
            "output_dir_safety": {
                "checked_before_mkdir": True,
                "passed": True,
                "blocked_reasons": [],
                "repo_local_ignored_artifact_root": True,
            },
            "exact_db_identity_from_actual_connection": {
                "checked_from_actual_connection": True,
                "db_name": "blombooru_test",
                "db_target_is_production": False,
                "dev_test_restored_snapshot_db_used": True,
                "passed": True,
                "blockers": [],
            },
            "source_icloud_app_storage_write_target": False,
            "dynamic_production_launcher_used": False,
            "production_db_storage_source_roots_private_ledgers_used_as_fixtures": False,
            "production_write_attempted": False,
        },
        "input_scope_fidelity": {
            "required_for_route_evidence": True,
            "passed": True,
            "status": "matched_old_r1_scope",
            "minimum_ratio": 0.8,
            "failed_metrics": [],
            "route_evidence_allowed": True,
            "current_run_classification": "route_evidence_candidate",
            "comparison_table": _r1r_input_scope_rows(),
        },
        "snapshot_input_scope_recovery": {
            "status": "old_r1_scope_available",
            "searched": ["postgres-database-list-label", "repo-local-backup-dump-snapshot-restore-labels"],
            "old_post_px1_pre_r1_snapshot_found": False,
            "existing_dump_restored": True,
            "restored_db_created_this_run": True,
            "restored_db_label": "blombooru_r1r_restored_test_20260618",
            "source_artifact_label": "r1r-private-existing-dump-20260618-blombooru",
            "dev_test_clone_created_from_live_production": False,
            "live_production_clone_needed": False,
            "operator_approval_needed_for_live_clone": False,
            "operator_approval_needed_for_restore": False,
            "current_production_contains_old_r1_equivalent_inputs": True,
            "restored_source_scope_passed": True,
            "deterministic_rerun_scope_passed": True,
            "r1r_can_continue": True,
        },
        "sc1_required_stage_manifest": _r1r_stage_manifest(),
        "sc1_full_chain_proof": {
            "complete_sc1_pipeline_executed": True,
            "deterministic_pipeline_executed": True,
            "llm_pair_adjudication_requested": True,
            "llm_pair_adjudication_executed": True,
            "llm_eligible_pair_count": 12,
            "llm_selected_pair_count": 12,
            "llm_judgment_count": 12,
            "all_eligible_llm_pairs_adjudicated": True,
            "budget_cap_usd": 15.0,
            "projected_full_eligible_cost_usd": 0.2,
            "llm_same_count": 3,
            "llm_cannot_count": 2,
            "llm_uncertain_count": 7,
            "all_required_stage_statuses_verified": True,
            "missing_required_stages": [],
            "skipped_required_stages": ["provider_cache_adapter_or_zero_eligible_proof"],
            "stage_manifest_artifact": "r1r-private-stage-manifest",
            "review_pack_includes_stage_manifest": True,
            "deterministic_only_output_used_as_full_chain_route_approval_evidence": False,
        },
        "sc1_r1_r1r_fidelity_table": [
            {
                "pipeline_step": "bounded_llm_pair_adjudication",
                "sc1_expected": "required",
                "sc1_actual_evidence": "300 judgments",
                "old_r1_actual_evidence": "disabled",
                "r1r_actual_evidence": "12 judgments",
                "r1r_status": "verified",
                "impact_if_missing": "route blocked",
                "contract_guard": "r1r_full_source_concept_pipeline_contract_v1",
            }
        ],
        "llm_adjudication_plan": {
            "required": True,
            "eligible_pair_count": 12,
            "selected_pair_count": 12,
            "selection_policy": "budget_driven_all_eligible",
            "all_eligible_pair_count": 12,
            "all_eligible_pairs_selected": True,
            "all_eligible_pairs_adjudicated": True,
            "budget_limit_is_primary": True,
            "fixed_call_cap_primary_limiter": False,
            "emergency_call_ceiling": 20000,
            "max_calls": 20000,
            "budget_usd": 15.0,
            "budget_cap_usd": 15.0,
            "projected_budget_usd": 0.2,
            "projected_full_eligible_cost_usd": 0.2,
            "skipped_pair_count": 0,
            "unselected_pair_count": 0,
            "eligible_pair_accounting_total": 12,
            "provider_required_for_missing_pairs": True,
            "provider_not_required_for_fully_cached_pairs": False,
        },
        "llm_readiness": {
            "passed": True,
            "operator_approved": True,
            "provider_available": True,
            "provider_required_for_missing_pairs": True,
            "provider_not_required_for_fully_cached_pairs": False,
            "provider_mode": "primary_openai",
            "provider_model": "gpt-test",
            "uses_fallback_provider": False,
            "cache_ready": True,
            "budget_ready": True,
        },
        "llm_provider_execution": {
            "provider_mode": "primary_openai",
            "provider_label": "primary_openai",
            "provider_name": "openai_compatible(primary_openai)",
            "model_name": "gpt-test",
            "uses_fallback_provider": False,
            "fallback_provider_used": False,
            "primary_openai_compatible_used": True,
        },
        "llm_judgment_summary": {
            "judgment_count": 12,
            "ledger_row_count": 12,
            "error_count": 0,
            "selected_pair_count": 12,
            "cache_hits": 0,
            "cache_misses": 12,
            "compatible_cache_hit_count": 0,
            "exact_compatible_cache_hit_count": 0,
            "imported_previous_judgment_count": 0,
            "new_provider_call_count": 12,
            "new_provider_success_count": 12,
            "failed_provider_call_count": 0,
            "remaining_missing_pair_count": 0,
            "cost_avoided_by_cache_reuse_usd": 0.0,
            "selected_pair_accounting": {
                "selected_pair_count": 12,
                "resolved_provider_judgment_count": 12,
                "valid_cached_judgment_count": 0,
                "explicit_skipped_pair_count": 0,
                "provider_error_pair_count": 0,
                "successful_accounted_pair_count": 12,
                "all_selected_pairs_successfully_accounted": True,
            },
            "llm_same_count": 3,
            "llm_cannot_count": 2,
            "llm_uncertain_count": 7,
        },
        "llm_cache_policy": {
            "policy_version": "source_concept_llm_adjudication_cache_v1",
            "decision_schema_version": "source_concept_pair_decision_schema_v1",
            "adjudication_policy_version": "source_concept_budget_driven_adjudication_v1",
            "durable_cache_root_label": "source-concept-llm-adjudication-cache",
            "private_ignored_cache_root": True,
            "cache_writes_atomic": True,
            "raw_private_paths_redacted": True,
            "exact_compatible_reuse_counts_as_valid_judgment": True,
            "semantic_prior_reuse_counts_as_valid_judgment": False,
            "compatible_cache_hit_count": 0,
            "exact_compatible_cache_hit_count": 0,
            "imported_previous_judgment_count": 0,
            "semantic_prior_judgment_count": 0,
            "new_provider_call_count": 12,
            "new_provider_success_count": 12,
            "failed_provider_call_count": 0,
            "remaining_missing_pair_count": 0,
            "provider_required_for_missing_pairs": True,
            "provider_not_required_for_fully_cached_pairs": False,
            "cost_spent_this_run_usd": 0.2,
            "cost_avoided_by_cache_reuse_usd": 0.0,
            "projected_new_call_cost_usd": 0.2,
            "projected_full_eligible_cost_usd": 0.2,
            "budget_cap_usd": 15.0,
        },
        "mutation_proof": {"passed": True, "forbidden_changed_tables": [], "unexpected_changed_tables": []},
        "post_commit_verification": {"passed": True},
        "old_r1_contamination_handling": {
            "baseline_snapshot_recorded": True,
            "baseline_artifact_label": "old-r1-sourceconcept-baseline",
            "old_r1_used_as_baseline_only": True,
            "old_r1_isolated_before_r1r_persistence": True,
            "source_concept_owned_tables_cleared_or_rebuilt_in_dev_test": True,
            "production_source_concept_tables_overwritten": False,
            "dev_test_restored_snapshot_scope_only": True,
            "contamination_handling_method": "dev_test_sourceconcept_owned_delete_rebuild",
        },
        "review_pack": {"generated": True, "includes_stage_manifest": True},
        "public_redaction": {
            "passed": True,
            "finding_count": 0,
            "scanned_artifacts": {"final_json_summary": True, "final_markdown_report": True},
            "clean_before_public_write": True,
            "unsafe_public_report_written": False,
        },
        "route_authorization": {
            "r2_authorized": False,
            "px1_b_authorized": False,
            "provider_2_authorized": False,
            "scale_up_authorized": False,
            "entity_bridge_authorized": False,
            "source_concept_truth_promotion_authorized": False,
            "route_approval_authorized": False,
            "a1r_still_required": True,
        },
        "forbidden_writes": {
            "entity_truth": False,
            "entity_alias_truth": False,
            "confirmed_assignments": False,
            "media_tags": False,
            "source_metadata": False,
            "provider_cache": False,
            "source_icloud_app_storage": False,
        },
        "source_concept_write_scope": {
            "allowed_tables": [
                "blombooru_source_concept_resolution_runs",
                "blombooru_source_concept_signals",
                "blombooru_source_concepts",
                "blombooru_source_concept_aliases",
                "blombooru_source_concept_evidence",
                "blombooru_source_concept_signal_links",
                "blombooru_source_concept_search_index",
            ],
            "changed_tables": ["blombooru_source_concept_signals"],
        },
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
        "phase": "PD1-A-R1",
        "post_122_launcher_merged": True,
        "production_launcher_entry_documented": True,
        "production_profile_runtime_config_documented": True,
        "development_dotenv_not_production_source": True,
        "production_execution_requires_profile_or_runtime_config": True,
        "s2g_consolidated_route": True,
        "r1r_required_before_r2": True,
        "a1r_required_before_route_approval": True,
        "provider_entity_truth_blocked": True,
        "no_production_writes": True,
        "no_db_mutation": True,
        "no_source_icloud_mutation": True,
        "no_provider_calls": True,
        "safety": {"no_llm_calls": True, "no_media_tags_mutation": True},
        "no_sourceconcept_mutation": True,
        "no_entity_truth_write": True,
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
            "current_phase": "PD1-A-R1",
            "next_recommended_phase": "S2G: GPU / AI Tagging Execution Foundation",
            "future_mentions_are_non_authorizing": True,
            "authorizes_s3": False,
            "authorizes_provider_calls": False,
            "authorizes_pixiv_gallery_dl_saucenao_google": False,
            "authorizes_sourceconcept_r1r_r2": False,
            "authorizes_entity_bridge": False,
            "authorizes_confirmed_assignments": False,
            "authorizes_automatic_production_sync": False,
            "authorizes_s2g_execution": False,
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


def _prod_launcher_mvp_summary(**overrides: object) -> dict:
    summary = {
        "pipeline_contract": {
            "contract_id": "prod_launcher_mvp_contract_v1",
            "status": "target_met",
            "claims": {"target_met": True, "safe_to_merge": True},
        },
        "mainline_sync": {
            "latest_main_after_pr120_included": True,
            "merge_base_origin_main_is_ancestor": True,
            "pr119_contract_preserved": True,
            "pr120_contract_preserved": True,
            "prod_launcher_contract_preserved": True,
        },
        "launcher_code": {
            "control_exists": True,
            "cli_control_exists": True,
            "visual_launcher_exists": True,
            "cmd_entry_exists": True,
        },
        "start_command": {
            "production_mode": True,
            "no_debug": True,
            "debug": False,
            "command": ["python.exe", "run.py"],
        },
        "preflight_gates": {
            "env": True,
            "debug_disabled": True,
            "storage_root": True,
            "db": True,
            "port": True,
            "venv": True,
            "worktree_dev_refusal": True,
            "destructive_e2e_disabled": True,
            "dangerous_dev_test_flags_disabled": True,
            "malformed_app_port_failure": True,
            "malformed_db_port_failure": True,
        },
        "startup_write_policy": {
            "normal_startup_maintenance_documented": True,
            "launcher_safe_startup_mode_enabled": True,
            "schema_migration_allowed": False,
            "schema_migration_blocked_by_launcher_safe_mode": True,
            "destructive_cleanup_allowed": False,
            "import_tagging_sync_jobs_allowed": False,
            "operator_intent_required_for_startup_maintenance": True,
        },
        "stop_safety": {
            "refuses_unknown_process": True,
            "managed_identity_required": True,
            "refuses_unverified_stale_pid": True,
            "verifies_process_create_time": True,
            "platform_aware_create_time": True,
            "verifies_python_executable": True,
            "verifies_port_owner_when_available": True,
            "force_kill_same_verified_only": True,
        },
        "start_safety": {
            "serialized": True,
            "start_already_in_progress_status": True,
            "atomic_state_writes": True,
            "stale_lock_reclaim": True,
        },
        "state_file": {
            "local_ignored": True,
            "path": ".local_manifests/production_launcher/violet-production-launcher-state.json",
        },
        "public_json_safety": {
            "log_tail_in_public_json": False,
            "log_tail_redacted": True,
        },
        "health_status": {
            "auth_exempt_for_launcher": True,
            "public_safe": True,
            "no_paths": True,
            "safe_fields_only": True,
            "schema_compatible_check": True,
            "read_only_schema_check": True,
            "status_example": {
                "ok": True,
                "app_name": "V.I.O.L.E.T.",
                "version": "1.41.0",
                "env": "production",
                "db_reachable": True,
                "schema_compatible": True,
                "schema_status": "compatible",
                "storage_configured": True,
                "debug": False,
            },
        },
        "diagnostics": {
            "status_json_example": {
                "running": True,
                "managed_by_launcher": True,
                "port": 8000,
                "url": "http://127.0.0.1:8000",
                "env": "production",
                "debug": False,
                "db_reachable": True,
                "schema_compatible": True,
                "schema_status": "compatible",
                "health_ok": True,
            }
        },
        "reports": {"redacted": True},
        "tests": {
            "preflight_failure": True,
            "port_occupied": True,
            "stale_pid": True,
            "managed_stop": True,
            "unknown_process_refusal": True,
            "health_auth_exempt": True,
            "startup_write_policy": True,
            "destructive_e2e_denial": True,
            "unverified_pid_refusal": True,
            "start_serialization": True,
            "malformed_app_port": True,
            "malformed_db_port": True,
            "log_tail_public_json": True,
            "stale_lock_reclaim": True,
            "posix_process_verification": True,
            "health_schema_compatibility": True,
        },
        "validation": {"focused_tests_passed": True, "contract_passed": True},
        "safety": {
            "no_import_tagging_localization_sync_jobs": True,
            "no_provider_calls": True,
            "no_sourceconcept_or_entity": True,
            "no_db_migrations": True,
            "no_destructive_operations": True,
            "no_source_icloud_mutation": True,
            "destructive_e2e_allowed": False,
        },
        "forbidden_operations": {
            "import_jobs": False,
            "tagging_jobs": False,
            "localization_jobs": False,
            "sync_jobs": False,
            "provider_calls": False,
            "sourceconcept": False,
            "entity_bridge": False,
            "db_migrations": False,
            "destructive_operations": False,
            "source_icloud_mutation": False,
        },
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _prod_launcher_ux1_summary(**overrides: object) -> dict:
    summary = {
        "pipeline_contract": {
            "contract_id": "prod_launcher_ux1_production_profile_contract_v1",
            "status": "implementation_complete_pending_manual_acceptance",
            "claims": {"target_met": False, "safe_to_merge": False},
        },
        "mainline_sync": {
            "latest_main_used": True,
            "pr119_merge_commit_included": True,
            "pr120_merge_commit_included": True,
            "pr121_merge_commit_included": True,
        },
        "production_profile": {
            "path": ".local_manifests/production_launcher/production-profile.json",
            "local_ignored_path": True,
            "separate_from_development_dotenv": True,
            "development_dotenv_modified": False,
            "child_env_from_profile": True,
            "child_env_skips_dotenv": True,
            "clean_allowlisted_process_environment": True,
            "profile_mismatch_fails_closed": True,
            "repair_resets_invariants": True,
            "local_bootstrap_supported": True,
            "auth_policy_explicit": True,
            "child_env_sets_require_auth": True,
            "create_repair_persists_inferred_values": True,
            "partial_update_bootstraps_inferred_values": True,
            "profile_overrides_development_dotenv_for_child": True,
            "storage_root_not_invented": True,
            "incomplete_profile_state_explicit": True,
        },
        "electron_launcher": {
            "exists": True,
            "primary_documented_entrypoint": True,
            "windows_executable_packaging": True,
            "zh_cn_primary_visible_ui": True,
            "inferred_values_not_marked_saved_without_profile": True,
            "copy_diagnostics_preserves_state": True,
            "open_browser_preserves_state": True,
            "db_access_value_clear_explicit": True,
            "calls_python_control_plane": True,
            "raw_json_hidden_from_main_screen": True,
            "advanced_diagnostics_collapsed_by_default": True,
            "checklist_groups": [
                "Production Profile",
                "Environment",
                "Storage",
                "Database",
                "Schema",
                "Port",
                "Safety Flags",
                "Startup Policy",
            ],
        },
        "npm_proxy_setup": {
            "local_ignored_npmrc": True,
            "reset_supported": True,
        },
        "preflight_mapping": {
            "violet_env_production": True,
            "storage_root_explicit": True,
            "production_storage_root_shape": True,
            "db_readonly_reachable": True,
            "no_startup_mutation_automation": True,
        },
        "health_status": {
            "auth_exempt_for_launcher": True,
            "public_safe": True,
            "schema_required_columns_check": True,
            "status_example": {
                "ok": True,
                "app_name": "V.I.O.L.E.T.",
                "env": "production",
                "db_reachable": True,
                "schema_compatible": True,
                "schema_status": "compatible",
                "storage_configured": True,
                "debug": False,
            },
        },
        "start_safety": {
            "launched_pid_verified": True,
            "health_identity_verified": True,
            "managed_unhealthy_is_unhealthy": True,
            "existing_managed_unhealthy_blocks_start": True,
            "failed_start_verification_cleanup": True,
            "failed_start_state_cleared": True,
            "failed_start_pid_reverified_before_signal": True,
            "failed_start_child_reaped": True,
            "profile_identity_updates_blocked_while_running": True,
        },
        "stop_safety": {
            "refuses_unknown_process": True,
            "posix_unknown_port_owner_fails_closed": True,
        },
        "shutdown_safety": {
            "safe_startup_skips_background_tasks": True,
            "tracked_background_tasks_cancelled": True,
        },
        "public_json_safety": {
            "log_tail_in_public_json": False,
            "profile_paths_redacted": True,
            "production_profile_suffix_redacted": True,
            "forward_slash_windows_paths_redacted": True,
            "unc_paths_redacted": True,
        },
        "reviewer_ledger": {"completed": True},
        "state_machine_audit": {"completed": True},
        "same_class_sweep": {"completed": True},
        "manual_acceptance_required_before_merge": True,
        "manual_acceptance_completed": False,
        "merge_allowed": False,
        "validation": {
            "python_tests_status": "passed",
            "electron_tests_status": "passed",
        },
        "safety": {
            "no_import_tagging_localization_sync_jobs": True,
            "no_provider_calls": True,
            "no_sourceconcept_or_entity": True,
            "no_db_migrations": True,
            "no_destructive_operations": True,
            "no_source_icloud_mutation": True,
        },
        "forbidden_operations": {
            "import_jobs": False,
            "tagging_jobs": False,
            "localization_jobs": False,
            "sync_jobs": False,
            "provider_calls": False,
            "sourceconcept": False,
            "entity_bridge": False,
            "db_migrations": False,
            "destructive_operations": False,
            "source_icloud_mutation": False,
        },
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _s2g1x_summary(**overrides: object) -> dict:
    head = _current_test_head()
    summary = {
        "head_evidence": {
            "probe_run_head_sha": head,
            "report_generation_head_sha": head,
            "current_pr_head_sha": "represented_by_pr_metadata_after_commit",
            "top_level_head_sha_omitted": True,
        },
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
        "public_reports": {
            "summary_json_path": "docs/reports/s2g1x-gpu-ai-tagging-probe-summary.json",
            "markdown_report_path": "docs/reports/s2g1x-gpu-ai-tagging-probe.md",
            "path_style": "repo_relative_public_artifacts",
        },
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


def _s2g_s3a_f1_summary(**overrides: object) -> dict:
    summary = {
        "pipeline_contract": {
            "contract_id": "s2g_s3a_f1_foundation_contract_v1",
            "status": "target_met",
            "claims": {"target_met": True, "safe_to_merge": True},
        },
        "wd_tagger": {
            "provider_abstraction": {
                "implemented": True,
                "hardcoded_cpu_provider_removed": True,
                "supported_provider_preference": [
                    "CUDAExecutionProvider",
                    "DmlExecutionProvider",
                    "CPUExecutionProvider",
                ],
                "requested_provider_preference": [
                    "CUDAExecutionProvider",
                    "DmlExecutionProvider",
                    "CPUExecutionProvider",
                ],
                "available_onnx_providers": [
                    "AzureExecutionProvider",
                    "CPUExecutionProvider",
                ],
                "actual_provider": "CPUExecutionProvider",
                "loaded_providers": ["CPUExecutionProvider"],
                "fallback_occurred": True,
                "fallback_reason": "unavailable_requested_providers=CUDAExecutionProvider,DmlExecutionProvider",
            },
            "load_control": {
                "configured_batch_size": 20,
                "effective_batch_size": 2,
                "batch_size": 2,
                "batch": {
                    "configured_batch_size": 20,
                    "load_control_effective_batch_size": 10,
                    "effective_batch_size": 2,
                    "batch_cap_source": "model_optimal_batch_size",
                    "batch_max_items": 10,
                    "phase_max_batch_size": 16,
                    "model_optimal_batch_size": 2,
                },
                "cpu_intra_op_threads": 4,
                "cpu_inter_op_threads": 1,
                "preprocess_workers": 2,
                "execution_mode": "ORT_SEQUENTIAL",
                "max_concurrent_jobs": 1,
                "process_priority": "below_normal",
            },
            "provenance": {
                "fields_available": [
                    "model_name",
                    "model_repo_id",
                    "thresholds",
                    "requested_provider_preference",
                    "actual_provider",
                    "fallback_reason",
                    "batch_size",
                    "effective_batch_size",
                    "configured_batch_size",
                    "batch_cap_source",
                    "cpu_thread_settings",
                    "preprocess_workers",
                    "execution_mode",
                    "tagger_version_source",
                ]
            },
            "model": {
                "model_download_allowed": False,
                "model_download_performed": False,
            },
        },
        "gpu_directml_enablement": {
            "attempted": True,
            "package_install": {
                "performed": False,
                "scope": "project_venv",
                "packages": [
                    {
                        "package": "onnxruntime-directml",
                        "install_performed": False,
                        "installed_after_attempt": False,
                        "version": None,
                    },
                    {
                        "package": "onnxruntime-gpu",
                        "install_performed": False,
                        "installed_after_attempt": False,
                        "version": None,
                    },
                ],
                "global_or_system_python_modified": False,
            },
            "available_onnx_providers_after_attempt": [
                "AzureExecutionProvider",
                "CPUExecutionProvider",
            ],
            "success": False,
            "actual_gpu_provider_loaded": None,
            "blocker": "package_missing",
            "benchmarks": [
                {
                    "provider": "DmlExecutionProvider",
                    "status": "provider_unavailable",
                    "blocker": "package_missing",
                },
                {
                    "provider": "CUDAExecutionProvider",
                    "status": "provider_unavailable",
                    "blocker": "package_missing",
                },
            ],
        },
        "benchmarks": {
            "sample_source": "synthetic_zero_arrays",
            "sample_count": 2,
            "cpu": {
                "status": "completed",
                "actual_provider": "CPUExecutionProvider",
                "sample_count": 2,
                "throughput_items_per_second": 1.0,
            },
            "gpu_or_directml": [],
        },
        "shared_foundation": {
            "module": "backend/app/services/job_control.py",
            "concepts": [
                "LoadControlConfig",
                "ProviderCapability",
                "JobRun",
                "StageRun",
                "ProgressSnapshot",
                "ProviderProvenance",
            ],
        },
        "s3a_dry_run_plan": {
            "production_execution_enabled": False,
            "unattended_enabled": False,
            "stages": [
                {"name": "update_check", "writes_enabled": False},
                {"name": "hydration_read", "writes_enabled": False},
                {"name": "import_reuse", "writes_enabled": False},
                {"name": "classification", "writes_enabled": False},
                {"name": "ai_tagging", "writes_enabled": False},
                {"name": "localization", "writes_enabled": False},
                {"name": "summary", "writes_enabled": False},
            ],
        },
        "public_reports": {
            "summary_json_path": "docs/reports/s2g-s3a-f1-provider-load-control-foundation-summary.json",
            "markdown_report_path": "docs/reports/s2g1x-gpu-ai-tagging-probe.md",
            "path_style": "repo_relative_public_artifacts",
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
            "db_schema_change": False,
        },
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _s2g_real1_summary(**overrides: object) -> dict:
    provider = {
        "requested_provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "actual_provider": "DmlExecutionProvider",
        "actual_onnx_provider_loaded": "DmlExecutionProvider",
        "loaded_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "fallback_occurred": False,
        "fallback_reason": None,
        "provider_load_errors": [],
    }
    load_control = {
        "batch_size": 2,
        "configured_batch_size": 2,
        "effective_batch_size": 2,
        "batch_cap_source": "configured",
        "cpu_intra_op_threads": 4,
        "cpu_inter_op_threads": 1,
        "preprocess_workers": 2,
        "max_concurrent_jobs": 1,
        "execution_mode": "ORT_SEQUENTIAL",
        "process_priority": "below_normal",
    }
    dry_run = {
        "executed": True,
        "status": "completed",
        "dry_run": True,
        "local_files_only": True,
        "provider_preference_requested": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "selected_media_count": 3,
        "processed": 3,
        "tags_added": 12,
        "suggestions_added": 2,
        "skipped_locked": 0,
        "ignored_low_confidence": 9,
        "failed": 0,
        "rollback_error": False,
        "error_state": False,
        "predicted_tag_count": 23,
        "media_tags_count_before": 10,
        "media_tags_count_after": 10,
        "media_tags_count_delta": 0,
        "no_media_tags_writes": True,
        "tag_source_values_used": ["ai_wd"],
        "job_record_created": False,
        "provider": provider,
        "load_control": load_control,
        "runtime_provenance": {
            "model_name": "wd-swinv2-tagger-v3",
            "model_repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
            "provider": provider,
            "load_control": load_control,
        },
    }
    cpu_provider = {
        "requested_provider_preference": ["CPUExecutionProvider"],
        "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "actual_provider": "CPUExecutionProvider",
        "actual_onnx_provider_loaded": "CPUExecutionProvider",
        "loaded_providers": ["CPUExecutionProvider"],
        "fallback_occurred": False,
        "fallback_reason": None,
        "provider_load_errors": [],
    }
    cpu_fallback = {
        **dry_run,
        "label": "cpu_fallback_dry_run",
        "provider_preference_requested": ["CPUExecutionProvider"],
        "selected_media_count": 1,
        "processed": 1,
        "provider": cpu_provider,
        "runtime_provenance": {
            "model_name": "wd-swinv2-tagger-v3",
            "model_repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
            "provider": cpu_provider,
            "load_control": load_control,
        },
    }
    summary = {
        "phase": "S2G-REAL1",
        "pipeline_contract": {
            "contract_id": "s2g_real1_bounded_ai_tagging_validation_contract_v1",
            "status": "target_met_dry_run_only",
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": False},
        },
        "run_configuration": {
            "mode": "dry_run",
            "write_requested": False,
            "operator_confirmation_exact": False,
            "max_items": 3,
            "max_items_cap": 5,
            "candidate_scan_limit": 25,
            "provider_preference_requested": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "cpu_fallback_provider_preference": ["CPUExecutionProvider"],
            "local_files_only": True,
            "model_download_allowed": False,
            "s3a_execution_enabled": False,
            "unattended_enabled": False,
        },
        "selected_media": {
            "selection_mode": "content_class_filter",
            "content_class_filter": ["anime"],
            "explicit_media_ids_supplied": False,
            "explicit_media_ids_publicly_recorded": False,
            "candidate_scan_limit": 25,
            "candidate_rows_reviewed": 3,
            "skipped_missing_local_file_count": 0,
            "count": 3,
            "id_count": 3,
            "max_items": 3,
            "small_explicit_sample": True,
            "no_full_library_fallback": True,
            "private_locator_values_recorded": False,
        },
        "model_cache": {
            "model_name": "wd-swinv2-tagger-v3",
            "model_repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
            "local_files_only": True,
            "model_download_allowed": False,
            "model_download_performed": False,
            "model_file_cached": True,
            "label_file_cached": True,
            "status": "cached",
            "blocker": None,
        },
        "dry_run": dry_run,
        "write_run": {
            "executed": False,
            "status": "not_run_not_requested",
            "required_confirmation_present": False,
            "selected_media_count": 0,
            "processed": 0,
            "media_tags_count_delta": 0,
            "tags_added": 0,
            "suggestions_added": 0,
            "skipped_locked": 0,
            "ignored_low_confidence": 0,
            "failed": 0,
            "rollback_error": False,
            "error_state": False,
            "tag_source_values_used": ["ai_wd"],
        },
        "write_prerequisites": {
            "selected_media_count_within_cap": True,
            "model_cache_available": True,
            "primary_dry_run_success": True,
            "primary_provider_evidence_present": True,
            "cpu_fallback_success": True,
            "public_private_scope_clean": True,
            "exact_write_confirmation_present": False,
            "write_executed_after_prerequisites_passed": True,
            "all_passed": False,
        },
        "primary_provider_validation": dry_run,
        "cpu_fallback_validation": cpu_fallback,
        "load_control_observations": {
            **load_control,
            "max_concurrent_ai_jobs": 1,
            "actual_provider": "DmlExecutionProvider",
            "appeared_bounded": True,
            "warnings": [],
        },
        "s3a_boundary": {
            "production_execution_enabled": False,
            "unattended_enabled": False,
            "dry_run_only": True,
            "stages": [
                {"name": "ai_tagging", "writes_enabled": False},
                {"name": "summary", "writes_enabled": False},
            ],
        },
        "safety": {
            "max_items_lte_5": True,
            "no_full_library_run": True,
            "dry_run_before_write": True,
            "ai_tagging_write_without_confirmation": False,
            "media_tags_write_executed": False,
            "write_requested_without_exact_confirmation": False,
            "write_executed_after_prerequisites_passed": True,
            "dry_run_media_tags_write": False,
            "production_s3a_execution_enabled": False,
            "unattended_s3b_enabled": False,
            "provider_pixiv_gallery_dl_saucenao_google_calls": False,
            "provider_pixiv_r1r_entity_operations": False,
            "sourceconcept_r1r_r2": False,
            "entity_bridge": False,
            "confirmed_entity_assignments": False,
            "source_icloud_mutation": False,
            "cleanup_delete_reset_drop_truncate": False,
            "db_import": False,
            "production_import": False,
            "production_classification": False,
            "production_localization": False,
            "model_download": False,
            "local_files_only": True,
            "private_locator_values_recorded": False,
        },
        "public_reports": {
            "summary_json_path": "docs/reports/s2g-real1-bounded-ai-tagging-validation-summary.json",
            "markdown_report_path": "docs/reports/s2g-real1-bounded-ai-tagging-validation.md",
            "path_style": "repo_relative_public_artifacts",
        },
        "public_redaction": {"passed": True, "finding_count": 0},
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _s2g_m1_summary(**overrides: object) -> dict:
    current_head = _current_test_head()
    summary = {
        "phase": "S2G-M1",
        "pipeline_contract": {
            "contract_id": "s2g_manual_sync_foundation_contract_v1",
            "status": "target_met",
            "phase_identity": "S2G-M1",
            "post_123_route_respected": True,
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": False},
        },
        "head_evidence": {
            "report_generation_head_sha": current_head,
            "validated_implementation_sha": current_head,
            "validated_implementation_is_ancestor_of_head": True,
            "validated_implementation_is_not_base_main": True,
            "post_validation_changes_report_only": True,
            "head_evidence_valid": True,
            "origin_main_sha": "4724530d83767a62b6525a58bb1a1d04e973d48e",
            "pr123_merge_commit": "4724530d83767a62b6525a58bb1a1d04e973d48e",
            "pr123_merge_is_ancestor_of_origin_main": True,
            "latest_main_after_pr123": True,
        },
        "ai_execution_profile": {
            "profile_id": "ai_tagging_execution_profile_v1",
            "provider_backend": "onnxruntime",
            "model_name": "wd-swinv2-tagger-v3",
            "model_repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
            "model_source": "huggingface_local_cache",
            "thresholds": {"general": 0.35, "character": 0.65, "rating": 0.5},
            "provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "batch_size": 2,
            "concurrency": 1,
            "preprocess_workers": 2,
            "per_image_timeout_seconds": 60,
            "job_timeout_seconds": 600,
            "allow_provider_fallback": True,
            "execution_scope": "dry_run_or_dev_test",
            "dry_run": True,
            "dev_test": True,
            "production_capable": True,
            "production_writes_enabled": False,
            "local_files_only": True,
            "provider_network_calls_enabled": False,
            "llm_calls_enabled": False,
            "provenance_fields": ["source", "model_name", "provider_backend", "confidence", "thresholds", "job_id"],
        },
        "capability_probe": {
            "attempted": True,
            "bounded": True,
            "synthetic_input_only": True,
            "sample_count": 2,
            "local_files_only": True,
            "provider_matrix": {
                "directml": {
                    "provider": "DmlExecutionProvider",
                    "available": True,
                    "loaded": True,
                    "practical": True,
                    "status": "completed",
                    "seconds_per_item": 0.25,
                },
                "cpu": {
                    "provider": "CPUExecutionProvider",
                    "available": True,
                    "loaded": True,
                    "practical": True,
                    "status": "completed",
                    "seconds_per_item": 1.5,
                },
            },
            "provider_selection": {
                "requested_provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
                "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
                "selected_provider": "DmlExecutionProvider",
                "fallback_occurred": False,
                "fallback_reason": None,
            },
            "provider_fallback_decision_recorded": True,
            "cpu_fallback_available": True,
            "cpu_fallback_completed": True,
            "recommended_provider": "DmlExecutionProvider",
            "recommended_batch_size": 2,
            "recommended_concurrency": 1,
            "recommended_seconds_per_item": 0.25,
            "estimated_runtime_seconds_for_25_item_manual_batch": 6.25,
            "status": "completed",
            "blocker": None,
        },
        "provider_fallback": {
            "selection": {
                "requested_provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
                "selected_provider": "DmlExecutionProvider",
                "fallback_occurred": False,
            },
            "decision_recorded": True,
            "gpu_requested_when_available": True,
            "cpu_fallback_available": True,
            "cpu_fallback_completed": True,
            "actual_recommended_provider": "DmlExecutionProvider",
        },
        "load_control_policy": {
            "present": True,
            "max_batch_size": 2,
            "max_concurrency": 1,
            "per_image_timeout_seconds": 60,
            "job_timeout_seconds": 600,
            "single_active_ai_execution_guard": True,
            "cancelability_or_safe_stop": "item_boundary",
            "failure_isolation_per_image": True,
            "no_unbounded_production_loop": True,
        },
        "provenance_policy": {
            "present": True,
            "source": "ai_wd",
            "model_name": "wd-swinv2-tagger-v3",
            "model_repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
            "provider_backend": "onnxruntime",
            "confidence_recorded": True,
            "thresholds_recorded": True,
            "job_id_recorded_for_job_runs": True,
            "dry_run_vs_write_mode_recorded": True,
            "manual_locked_tags_not_overwritten": True,
            "suggestions_vs_confirmed_recorded": True,
            "production_writes_enabled": False,
            "provenance_fields": ["source", "model_name", "provider_backend", "confidence", "thresholds", "job_id"],
        },
        "manual_sync": {
            "dry_run_planner": {
                "implemented": True,
                "public_safe": True,
                "db_write_performed": False,
                "source_mutation_performed": False,
                "app_storage_mutation_performed": False,
                "state_counts": {
                    "candidate": 0,
                    "skipped_unsupported": 1,
                    "skipped_placeholder": 1,
                    "skipped_zero_byte": 1,
                    "skipped_changing": 0,
                    "skipped_path_policy_error": 0,
                    "skipped_duplicate": 1,
                    "skipped_existing_media": 1,
                    "import_planned": 1,
                    "imported_in_test": 0,
                    "classified_in_test": 0,
                    "ai_tagged_in_test": 0,
                    "localization_scheduled_in_test": 0,
                    "failed": 1,
                },
                "failure_reasons": {"corrupted_image": 1},
                "estimated_import_count": 1,
                "estimated_classification_count": 1,
                "estimated_ai_tagging_count": 1,
                "estimated_localization_workload": 1,
            },
            "job_ledger_foundation": {
                "implemented": True,
                "job_id_present": True,
                "mode": "dry_run",
                "state": "planned",
                "trigger_type": "manual_operator",
                "per_file_state_records_present": True,
                "persistent_tables_available": [
                    "blombooru_dynamic_sync_runs",
                    "blombooru_dynamic_sync_run_items",
                    "blombooru_dynamic_source_items",
                ],
                "ledger_mode": "public_summary_plus_optional_private_local_details",
            },
            "controlled_pipeline": {
                "implemented": True,
                "status": "planned_dry_run_only",
                "dry_run_only_this_phase": True,
                "production_execute_enabled": False,
                "estimated_runtime_seconds": 4.0,
                "stages": [
                    {"name": "candidate_discovery", "writes_enabled": False, "production_execution_enabled": False},
                    {"name": "import", "writes_enabled": False, "production_execution_enabled": False},
                    {"name": "classification", "writes_enabled": False, "production_execution_enabled": False},
                    {"name": "ai_tagging", "writes_enabled": False, "production_execution_enabled": False},
                    {"name": "localization", "writes_enabled": False, "production_execution_enabled": False},
                    {"name": "summary", "writes_enabled": False, "production_execution_enabled": False},
                ],
            },
        },
        "api_surface": {
            "manual_plan_endpoint": "POST /api/admin/dynamic-library-sync/manual-sync/plan",
            "manual_status_endpoint": "GET /api/admin/dynamic-library-sync/manual-sync/status",
            "auth_required": "require_admin_mode",
            "production_write_endpoint_enabled": False,
            "automatic_execution_endpoint_added": False,
        },
        "final_button_recommendation": {
            "placement": "both_launcher_and_web_admin",
            "primary_call": "POST /api/admin/dynamic-library-sync/manual-sync/plan",
            "launcher_pending_check_on_startup": "lightweight_count_only_ok",
            "launcher_intrusive_prompt": False,
            "safe_default_max_files": 25,
            "safe_default_max_duration_seconds": 600,
            "safe_default_ai_batch_size": 2,
            "safe_default_concurrency": 1,
            "partial_failure_behavior": "complete_successful_items_keep_failed_visible",
            "first_real_acceptance_batch_size": 5,
            "rollback_supersede_diagnostic_plan": "ledger_driven_retry",
        },
        "validation": {
            "focused_tests_passed": True,
            "runner_completed": True,
            "browser_validation_required": False,
            "browser_validation_reason": "backend route/service only",
        },
        "public_reports": {
            "summary_json_path": "docs/reports/s2g-m1-ai-manual-sync-foundation-summary.json",
            "markdown_report_path": "docs/reports/s2g-real1-bounded-ai-tagging-validation.md",
            "path_style": "repo_relative_public_artifacts",
        },
        "public_redaction": {"passed": True, "finding_count": 0},
        "safety": {
            "production_db_mutation": False,
            "production_import": False,
            "production_classification": False,
            "production_ai_tagging_writes": False,
            "production_localization_writes": False,
            "source_icloud_mutation": False,
            "app_managed_production_storage_mutation": False,
            "external_provider_calls": False,
            "gallery_dl_pixiv_saucenao_google_calls": False,
            "sourceconcept_mutation": False,
            "entity_truth_writes": False,
            "confirmed_assignment_writes": False,
            "production_media_tags_mutation": False,
            "llm_calls": False,
            "automatic_sync_enabled": False,
            "scheduled_sync_enabled": False,
            "system_service_enabled": False,
            "startup_task_enabled": False,
            "long_running_daemon_enabled": False,
            "final_production_acceptance_completed": False,
        },
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _s3a_m1_summary(**overrides: object) -> dict:
    summary = {
        "phase": "S3A-M1",
        "target_met": True,
        "pipeline_contract": {
            "contract_id": "s3a_m1_manual_sync_execute_contract_v1",
            "status": "target_met_dev_test_ready",
            "phase_identity": "S3A-M1",
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": False},
        },
        "manual_sync": {
            "plan_endpoint": True,
            "execute_endpoint": True,
            "status_endpoint": True,
            "job_status_endpoint": True,
            "cancel_endpoint": True,
            "plan_hash_required": True,
            "exact_confirmation_required": True,
            "plan_freshness_required": True,
            "registered_root_required_for_execute": True,
            "hydrated_only_required": True,
            "default_execute_disabled": True,
            "stale_replan_rejected": True,
            "confirmation_phrase_prefix": "I APPROVE S3A-M1 MANUAL SYNC EXECUTE",
            "production_confirmation_phrase_prefix": "I APPROVE S3A-M1 PRODUCTION MANUAL SYNC EXECUTE",
            "limits": {
                "safe_default_max_files": 25,
                "execute_max_files": 5,
                "manual_execute_default_max_files": 5,
                "dry_run_execute_default_max_files_aligned": True,
                "normal_update_check_not_forced_to_execute_cap": True,
                "separate_update_check_and_execute_limits": True,
                "execute_max_files_exceeded_rejected": True,
                "max_duration_seconds": 600,
            },
            "active_job_gates": {
                "ai_job_active_blocked": True,
                "classification_job_active_blocked": True,
                "queued_ai_job_blocked": True,
                "queued_classification_job_blocked": True,
                "queued_manual_sync_execute_blocks_ai_job": True,
                "queued_manual_sync_execute_blocks_classification_job": True,
                "manual_sync_execute_active_blocks_ai_job": True,
                "manual_sync_execute_active_blocks_classification_job": True,
            },
            "runner_outputs": {
                "default_report_json_gitignored": True,
                "default_report_md_gitignored": True,
                "docs_reports_require_explicit_flags": True,
                "execute_report_uses_approved_plan": True,
                "standalone_db_session_initialized": True,
            },
            "translation_side_effect_gates": {
                "background_llm_blocked": True,
                "auto_llm_blocked": True,
                "llm_enabled_blocked": True,
                "live_worker_state_blocked": True,
                "schedule_localization_false_not_sufficient": True,
            },
            "classification": {
                "local_only": True,
                "clip_cache_only_required": True,
                "uncached_clip_skips": True,
                "model_downloads_allowed": False,
                "method_and_order_reported": True,
                "heuristic_ai_tags_before_classification": True,
                "heuristic_deferred_when_ai_tags_unavailable": True,
                "heuristic_ai_failure_does_not_write_unknown": True,
                "ai_tags_unavailable_reason": "classification_deferred_ai_tags_unavailable",
                "ai_tagging_failed_reason": "classification_skipped_ai_tagging_failed",
                "clip_cached_path_preserved": True,
            },
            "ai_tagging": {
                "item_exception_containment": True,
                "model_uncached_reason": "ai_tagger_model_uncached",
                "file_missing_reason": "ai_tagger_file_missing",
                "inference_failure_reason": "ai_tagger_inference_failed",
                "returned_error_sanitized": True,
                "raw_error_details_public": False,
                "proper_nouns_suggestion_only": True,
                "no_sourceconcept_or_entity_truth_from_ai_proper_nouns": True,
                "single_item_failure_does_not_fail_whole_run": True,
            },
            "active_run_recovery": {
                "stale_pending_running_finalized": True,
                "stale_cancelling_finalized": True,
                "timeout_seconds": 1800,
            },
            "plan_replay_protection": {
                "plan_hash_binds_created_at": True,
                "directory_walk_order_deterministic": True,
                "unchanged_tree_not_rejected_by_directory_order": True,
                "forged_fresh_timestamp_rejected": True,
                "source_change_still_rejected": True,
            },
            "per_item_failures": {
                "source_missing_recorded": True,
                "read_error_recorded": True,
                "read_timeout_recorded": True,
                "continue_within_failure_budget": True,
            },
            "failure_budget": {
                "max_item_failures": 20,
                "max_failure_rate": 0.05,
                "max_consecutive_failures": 10,
                "max_duration_seconds": 600,
                "stopped_by_failure_budget_recorded": True,
                "stopped_by_duration_budget_recorded": True,
                "unprocessed_count_reported": True,
                "pending_import_preserved_on_early_stop": True,
            },
            "localization": {
                "scheduled_in_execute": False,
                "blocked_current_phase": True,
                "blocked_reason": "manual_sync_execute_forbids_llm_localization_current_phase",
            },
            "public_serialization": {
                "generic_sync_run_redacts_private_plan": True,
                "dashboard_state_redacts_private_plan": True,
                "pending_summary_redacts_private_plan": True,
                "job_serializers_redact_private_plan": True,
            },
            "ledger": {
                "per_file_records_present": True,
                "dynamic_sync_run_used": True,
                "import_preledger_committed_before_media_write": True,
                "import_preledger_success_failure_updated": True,
                "run_item_deduplicated_per_source_item": True,
                "deferred_unprocessed_rows_materialized": True,
                "deferred_unprocessed_without_source_read_or_hash": True,
                "public_safe": True,
            },
            "pipeline": {
                "dev_test_execute_supported": True,
                "production_acceptance_pending": True,
                "stages": [
                    {"name": "plan"},
                    {"name": "import"},
                    {"name": "classification"},
                    {"name": "ai_tagging"},
                    {"name": "localization"},
                    {"name": "summary"},
                ],
            },
            "dev_test_execute_validation": {
                "completed": True,
                "source_mutation_absent": True,
                "llm_calls_absent": True,
            },
        },
        "ai_execution_profile": {
            "provider_backend": "onnxruntime",
            "provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "batch_size": 2,
            "concurrency": 1,
            "local_files_only": True,
            "llm_calls_enabled": False,
        },
        "api_surface": {
            "manual_execute_endpoint_added": True,
            "automatic_execution_endpoint_added": False,
        },
        "ui": {
            "web_admin_manual_execute_panel": True,
            "web_admin_plan_confirmation_flow": True,
            "web_admin_default_max_files_visible": True,
            "web_admin_separate_update_check_limit": True,
            "launcher_manual_sync_entry": True,
            "launcher_manual_sync_forces_content_tab": True,
        },
        "validation": {
            "focused_tests_passed": True,
            "launcher_tests_passed": True,
            "browser_validation_performed": True,
            "contract_check_passed": True,
        },
        "public_reports": {
            "summary_json_path": "docs/reports/s3a-m1-manual-sync-execute-summary.json",
            "markdown_report_path": "docs/reports/s3a-m1-manual-sync-execute.md",
            "path_style": "repo_relative_public_artifacts",
        },
        "public_redaction": {"passed": True, "finding_count": 0},
        "safety": {
            "production_execute_performed": False,
            "production_import": False,
            "production_classification": False,
            "production_ai_tagging_writes": False,
            "production_localization_writes": False,
            "source_icloud_mutation": False,
            "app_managed_production_storage_mutation": False,
            "external_provider_calls": False,
            "model_downloads": False,
            "llm_calls": False,
            "automatic_sync_enabled": False,
            "scheduled_sync_enabled": False,
            "system_service_enabled": False,
            "startup_task_enabled": False,
            "production_acceptance_completed": False,
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


def _s3a_m2_summary(**overrides: object) -> dict:
    summary = {
        "phase": "S3A-M2",
        "head_sha": "head-abc123",
        "pipeline_contract": {
            "contract_id": "s3a_m2_production_delta_e2e_contract_v1",
            "status": "target_met",
            "phase_identity": "S3A-M2",
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": True},
            "fresh_dry_run_completed": True,
            "execute_after_approval": True,
            "exact_operator_approval_present": True,
        },
        "source": {
            "root_id": 1,
            "public_source_identity": "source-abc123",
            "paths_redacted": True,
        },
        "registered_roots_public": {
            "registered_root_count": 1,
            "in_scope_root_id": 1,
            "paths_redacted": True,
            "roots": [
                {
                    "id": 1,
                    "public_source_identity": "source-abc123",
                    "is_active": True,
                    "auto_sync_enabled": False,
                    "in_scope": True,
                    "path_redacted": True,
                }
            ],
        },
        "controlled_delta": {
            "cap": 300,
            "cap_exceeded": False,
            "expected_delta_range": "100-300",
            "silently_truncated": False,
            "hydrated_only": True,
        },
        "dry_run": {
            "plan_hash": "abcdef1234567890",
            "total_seen": 120,
            "estimated_import_count": 100,
            "estimated_classification_count": 100,
            "estimated_ai_tagging_count": 100,
            "approval_phrase": "I APPROVE S3A-M2 PRODUCTION DELTA E2E abcdef123456",
            "partial_scan": False,
        },
        "execute": {
            "run_id": 9,
            "status": "completed",
            "imported": 100,
            "classified": 100,
            "ai_tagged": 100,
            "failed": 0,
            "deferred": 0,
        },
        "classification": {"reported": True, "count": 100, "failed": 0, "skipped": 0},
        "ai_tagging": {
            "reported": True,
            "count": 100,
            "failed": 0,
            "skipped": 0,
            "mature_media_tag_policy": True,
            "proper_nouns_suggestion_only": False,
            "no_sourceconcept_or_entity_truth_from_ai_only_tags": True,
            "no_sourceconcept_or_entity_truth_from_ai_proper_nouns": True,
        },
        "localization": {
            "status": "completed",
            "translated": 80,
            "failed": 0,
            "skipped": 20,
            "llm_called": True,
            "provider_call_count": 2,
            "requested_max_tags": 1500,
            "candidate_count": 80,
            "candidate_overflow": False,
            "localization_limit_status": "under_limit",
            "dynamic_source_items_target_status": "localized",
            "dynamic_source_items_deferred_reason": None,
        },
        "localization_diagnosis": {
            "status": "completed",
            "diagnosis": "benign_all_localizable_tags_already_localized_or_newly_localized",
            "ai_wd_assignment_count": 1000,
            "distinct_ai_wd_tag_count": 200,
            "localizable_distinct_tags": 180,
            "localizable_already_localized_or_static": 100,
            "newly_localized_tags": 80,
            "tags_requiring_localization_after_runner": 0,
            "proper_noun_suggestion_review_only_tags_skipped": 20,
            "not_eligible_for_localization": {"proper_noun_suggestion_review_only": 20},
            "public_safe": True,
        },
        "gpu_telemetry": {
            "status": "collected",
            "validation_status": "passed",
            "actual_provider": "DmlExecutionProvider",
            "max_gpu_memory_used_mib": 2048.0,
            "peak_gpu_utilization_percent": 72.0,
        },
        "runtime": {
            "total_seconds": 300.0,
            "stage_durations_seconds": {
                "import": 30.0,
                "classification": 60.0,
                "ai_tagging": 180.0,
                "localization": 30.0,
            },
        },
        "ledger_consistency": {"status": "passed", "passed": True},
        "public_redaction": {"passed": True, "finding_count": 0},
        "production_acceptance": {
            "performed": True,
            "approval_phrase_type": "s3a_m2_plan_hash_bound",
            "approval_phrase_recorded": False,
            "exact_statement": "production acceptance performed",
        },
        "readiness": {
            "passed": True,
            "production_settings": {
                "violet_env": "production",
                "db_name": "blombooru",
            },
        },
        "api_runner_acceptance": {
            "dry_run_plan_generated": True,
            "execute_ran": True,
            "status_polled_or_serialized": True,
        },
        "initial_run_validation": {
            "passed": True,
            "blockers": [],
            "status": "passed",
            "public_safe": True,
        },
        "placeholder_hydration": {
            "status": "completed",
            "placeholder_count_before_hydration": 2,
            "hydration_attempted_count": 2,
            "hydration_succeeded_count": 2,
            "hydration_failed_count": 0,
            "remaining_placeholders_after_hydration": 0,
            "failure_reasons": {},
            "manual_user_action_required": False,
            "source_content_written": False,
            "source_deleted_moved_renamed": False,
            "public_safe": True,
        },
        "final_inventory": {
            "current_delta_candidates": 20,
            "current_importable_hydrated_supported_items": 0,
            "existing_media": 3,
            "placeholders_remaining": 0,
            "unsupported_items": 17,
            "unreadable_zero_byte_damaged": 0,
            "unsupported_extension_breakdown": {".txt": 17},
            "scan_cap_stopped_scan": False,
            "public_safe": True,
        },
        "final_totals": {
            "imported": 100,
            "classified": 100,
            "ai_tagged": 100,
            "localized": 80,
        },
        "launcher_web_admin_acceptance": {
            "validated": True,
            "status": "passed_gui_execute_completed",
            "target_path": "/admin?tab=content#dynamic-library-sync-section",
            "execute_clicked": True,
            "gui_execute_completed": True,
            "gui_execute_run_id": 9,
            "previous_execute_run_id": 8,
            "production_execute_run_id_seen": 9,
            "gui_provenance_valid": True,
            "request_source": "web_admin_gui",
            "gui_validation_session_id_present": True,
            "gui_validation_session_id_hash": "session-hash",
            "gui_validation_session_signature_valid": True,
            "gui_plan_hash_bound": True,
            "gui_plan_flow_verified": True,
            "gui_plan_request_id_present": True,
            "runtime_head_matches_current": True,
            "validated_head_sha": "head-abc123",
            "public_source_identity": "source-abc123",
        },
        "scanner_incremental_model": {
            "model": "hybrid_source_ledger_metadata_fast_path_with_filesystem_fallback",
            "durable_state_tables": [
                "blombooru_dynamic_source_items",
                "blombooru_dynamic_sync_runs",
                "blombooru_dynamic_sync_run_items",
            ],
            "durable_global_filesystem_cursor": False,
            "starts_from_root_each_run": "metadata_fallback_only_after_source_ledger_priority_workset",
            "stable_known_files_fast_skipped_without_hash": True,
            "hash_only_when": [
                "new_source_path",
                "metadata_changed",
                "downstream_followup",
                "ambiguous_existing_media",
                "execute_integrity_revalidation",
            ],
            "cap_semantics": "actionable candidates only; unchanged existing media excluded",
            "next_batch_continuation": "after execute committed DynamicSourceItem states are reused by the next plan",
            "invalidation_policy": [
                "source_root_id",
                "relative_path_hash",
                "file_size",
                "mtime_ns",
                "content_hash_mismatch",
                "hydration_policy",
                "profile_or_pipeline_settings",
            ],
            "public_safe": True,
        },
        "priority_backlog_root_cause": {
            "table": "blombooru_dynamic_source_items",
            "root_public_ref": "registered-root-2-public",
            "total_priority_workset_rows": 22902,
            "legacy_pending_changed_rows": 22698,
            "legacy_pending_changed_outside_safety_window": 22698,
            "rows_matching_existing_media": 35448,
            "rows_imported_but_still_pending_or_changed": 22698,
            "rows_that_should_be_actionable_now": 204,
            "rows_that_need_repair_or_migration": 22698,
            "root_cause": "historical update-check backlog left imported existing-media rows in changed/pending source-ledger state",
            "production_db_repair_executed": False,
            "repair_migration_plan": {
                "candidate_count": 22698,
                "candidate_condition": "pending changed rows outside safety window with media_id or existing content hash and no downstream follow-up",
                "requires_owner_approval": True,
                "would_modify_db": False,
                "validation_after_repair": [
                    "rerun priority backlog audit",
                    "verify current actionable rows preserved",
                    "verify public redaction",
                ],
            },
            "public_safe": True,
        },
        "local_copy_repeated_incremental_e2e": {
            "status": "completed",
            "bulk_run_alone_sufficient": False,
            "completed": True,
            "scenario_count": 10,
            "pass_criteria_failures": [],
            "plan_expensive_ops_zero_all_cycles": True,
            "browser_normal_flow_passed": True,
            "source_originals_mutated": False,
            "production_db_used": False,
            "user_retry_recommended": True,
            "public_safe": True,
        },
        "standard_pipeline_flow": {
            "version": 1,
            "status": "completed",
            "future_automation_readiness": "manual_pipeline_standardized_no_automatic_sync_implemented",
            "automatic_sync_implemented": False,
            "public_safe": True,
            "aggregate_basis": {
                "initial_execute_run_id": 7,
                "remaining_execute_run_id": 8,
                "hydration_passes_represented": 2,
                "final_inventory_delta_candidates": 20,
            },
            "steps": {
                name: {"completed": True, "status": "completed", "evidence": {}}
                for name in (
                    "scan_current_source_delta",
                    "detect_cloud_placeholders",
                    "hydrate_placeholders_non_destructively",
                    "rescan_after_hydration",
                    "import_all_current_importable_items",
                    "classify_imported_media",
                    "run_ai_tagging",
                    "run_localization_or_stable_reasons",
                    "record_ledger_for_every_planned_item",
                    "capture_resource_gpu_telemetry",
                    "validate_public_redaction",
                    "validate_launcher_web_admin_workflow",
                    "produce_public_report_and_contract",
                )
            },
        },
        "ai_tag_assignment_incident": {
            "status": "repaired",
            "discovered_by": "manual_production_ui_validation",
            "affected_run_ids": [7, 8],
            "affected_media_count": 100,
            "assignments_inspected": 1000,
            "root_cause": "manual_sync_execute_forced_force_suggestions_true_for_all_ai_tags",
            "before": {
                "all_ai_assignments_are_suggestions": True,
                "high_conf_nonproper_expected_normal_count": 600,
                "high_conf_nonproper_incorrect_suggestion_count": 600,
                "high_conf_proper_expected_normal_count": 25,
                "high_conf_proper_incorrect_suggestion_count": 25,
                "high_conf_proper_normal_count": 0,
                "proper_noun_non_suggestion_count": 0,
                "proper_noun_suggestion_count": 25,
            },
            "repair": {
                "assignments_converted_from_suggestion_to_normal": 625,
                "assignments_converted_from_normal_to_suggestion": 0,
                "assignments_kept_suggestion": 375,
                "proper_noun_suggestions_inspected": 25,
                "proper_noun_suggestions_converted_to_normal": 25,
                "proper_noun_suggestions_kept_suggestion": 0,
                "proper_noun_suggestions_kept_reason": "below_confirm_threshold_suggestion",
                "assignments_deleted_or_replaced": 0,
                "duplicate_rows_created": 0,
            },
            "after": {
                "all_ai_assignments_are_suggestions": False,
                "high_conf_nonproper_expected_normal_count": 600,
                "high_conf_nonproper_incorrect_suggestion_count": 0,
                "high_conf_nonproper_normal_count": 600,
                "high_conf_proper_expected_normal_count": 25,
                "high_conf_proper_incorrect_suggestion_count": 0,
                "high_conf_proper_normal_count": 25,
                "low_conf_proper_suggestion_count": 3,
                "proper_noun_non_suggestion_count": 25,
                "proper_noun_suggestion_count": 3,
            },
            "entity_truth_violations_found": 0,
            "localization_remaining_gap": 0,
            "cohort_blocker_anomaly_count": 0,
            "ui_verification": {
                "status": "passed",
                "method": "in_app_browser_playwright_against_launcher_started_production_server",
                "computer_use_attempted": True,
                "computer_use_result": "unavailable_in_current_tool_session",
                "sample_count": 8,
                "normal_visible_pass_count": 8,
                "proper_suggestion_visible_pass_count": 8,
                "samples_expect_proper_suggestions": 3,
                "raw_screenshots_committed": False,
                "raw_ids_private": True,
                "public_safe": True,
            },
            "public_safe": True,
        },
        "post_repair_ui_validation": {
            "status": "passed",
            "sample_count": 8,
            "normal_visible_pass_count": 8,
            "proper_suggestion_visible_pass_count": 8,
            "public_safe": True,
        },
        "cohort_self_audit": {
            "status": "passed_after_repair",
            "baseline_selection": {
                "method": "latest older non-S3A-M2 media with source='ai_wd' before affected cohort upload window",
                "limit": 500,
                "media_count": 50,
            },
            "affected_media_count": 100,
            "baseline_media_count": 50,
            "blocker_anomaly_count": 0,
            "normal_ai_tag_semantics_consistent_with_policy": True,
            "public_safe": True,
        },
        "failure_timeline": [
            {
                "event": "initial_production_delta_run",
                "what_happened": "bounded production delta execute ran",
                "detected_by": "runner ledger and report",
                "why_earlier_evidence_missed": "no GUI execute evidence was required yet",
                "production_impact": "production import/classification/AI/localization rows were written",
                "repair_or_prevention": "aggregate postmortem and GUI execute contract gates added",
            }
        ],
        "deferred_failed_inventory": {
            "source_field": "pending_summary.pending_deferred",
            "query_scope": "active_source_roots",
            "total": 0,
            "reason_counts": {},
            "extension_counts": {},
            "pipeline_status_counts": {},
            "current_actionable_importable_pending": 0,
            "current_placeholder_reason_count": 0,
            "ui_recommendation": "distinguish historical inventory from current actionable blockers",
            "public_safe": True,
        },
        "gui_hang_root_cause": {
            "status": "not_observed_in_passing_fixture",
            "endpoint_called": "/api/admin/dynamic-library-sync/manual-sync/plan",
            "root_cause": "no hang in passing fixture",
            "backend_request_sent": True,
            "backend_kept_scanning": False,
            "cleanup_performed": True,
            "watchdog_timeout_added": True,
            "public_safe": True,
        },
        "api_vs_gui_divergence": {
            "runner_gui_planner_diverged": False,
            "api_runner_proved_backend_only": False,
            "prevention_added": ["gui_execute_run_id_newer_than_runner_required"],
            "public_safe": True,
        },
        "branch_profile_provenance": {
            "branch": "codex/s3a-m2-production-delta-e2e-gpu-telemetry",
            "head_sha": "head-abc123",
            "profile_id": "production-default",
            "db_name": "blombooru",
            "violet_env": "production",
            "stale_process_cleanup_status": "clean",
            "public_safe": True,
        },
        "unsupported_inventory": {
            "extension_counts": {".txt": 17},
            "current_scope_unsupported_items": 17,
            "current_image_pipeline_importable_under_existing_rules": 0,
            "public_safe": True,
        },
        "manual_sync_safety_judgement": {
            "status": "manual_sync_safe_with_operator_checks",
            "evidence_based": True,
            "gui_execute_validated": True,
            "api_runner_e2e_reliable": True,
            "future_automatic_sync_ready": False,
            "public_safe": True,
        },
        "remaining_blockers": [],
        "private_artifacts": {
            "root": ".local_manifests/s3a_m2_delta_e2e",
            "telemetry_root": ".local_manifests/s3a_m2_delta_e2e/telemetry",
            "private_artifacts_committed": False,
        },
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-production-delta-e2e.md",
            "summary_json_path": "docs/reports/s3a-m2-production-delta-e2e-summary.json",
        },
        "safety": {
            "automatic_sync_enabled": False,
            "scheduled_sync_enabled": False,
            "startup_sync_enabled": False,
            "system_service_enabled": False,
            "source_icloud_mutation": False,
            "source_mutation_attempted": False,
            "provider_pixiv_gallery_dl_saucenao_google_calls": False,
            "sourceconcept_entity_bridge": False,
            "cleanup_delete_reset_drop_truncate": False,
            "full_library_reimport": False,
            "private_paths_or_hashes_in_public_report": False,
        },
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


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


def _s3a_m2_r_lifecycle_summary(**overrides: object) -> dict:
    summary = {
        "phase": "S3A-M2-R PR-R1",
        "pipeline_contract": {
            "contract_id": "s3a_m2_r_lifecycle_workitem_contract_v1",
            "status": "pr_r1_core_complete",
            "phase_identity": "S3A-M2-R PR-R1",
            "claims": {
                "pr_r1_core_complete": True,
                "full_s3a_m2_r_complete": False,
                "target_met": False,
                "safe_to_merge": False,
                "full_chain_complete": False,
            },
        },
        "lifecycle_classifier": {
            "implemented": True,
            "module": "backend/app/services/manual_sync_lifecycle.py",
            "lifecycle_kinds": [
                "APP_MEDIA_FOLLOWUP",
                "IMPORT_CANDIDATE",
                "RETRYABLE_SOURCE_FAILURE",
                "PLACEHOLDER_DEFERRED",
                "STABLE_NOOP",
                "HISTORICAL_DIAGNOSTIC",
                "CONTINUATION",
                "BROKEN_STATE",
                "FATAL_BLOCKER",
            ],
            "work_item_kinds": [
                "FOLLOWUP",
                "IMPORT",
                "RETRY_SOURCE",
                "PLACEHOLDER",
                "NOOP_DIAGNOSTIC",
                "BROKEN_STATE",
            ],
        },
        "work_item_source_read_boundaries": {
            "FOLLOWUP": {"allowed_source_reads": False, "can_execute": True, "consumes_actionable_cap": True},
            "IMPORT": {"allowed_source_reads": True, "can_execute": True, "consumes_actionable_cap": True},
            "RETRY_SOURCE": {"allowed_source_reads": True, "can_execute": True, "consumes_actionable_cap": True},
            "PLACEHOLDER": {"allowed_source_reads": False, "can_execute": False, "consumes_actionable_cap": False},
            "NOOP_DIAGNOSTIC": {"allowed_source_reads": False, "can_execute": False, "consumes_actionable_cap": False},
            "BROKEN_STATE": {"allowed_source_reads": False, "can_execute": False, "consumes_actionable_cap": False},
        },
        "operator_status_mapping": {
            "implemented": True,
            "legacy_completed_with_failures_mapped": True,
        },
        "debt_model": {
            "older_app_media_source_missing_downstream_incomplete": {"count": 20, "kind": "APP_MEDIA_FOLLOWUP_or_BROKEN_STATE"},
            "run18_deferred_continuation": {"count": 75, "kind": "CONTINUATION"},
            "run18_retryable_source_failures": {"count": 11, "kind": "RETRYABLE_SOURCE_FAILURE"},
        },
        "validation": {
            "table_driven_lifecycle_scenarios": {"count": 27, "passed": True},
            "source_read_boundary_tests": {"passed": True},
            "app_media_missing_broken_state_covered": True,
            "attempted_vs_completed_separation_covered": True,
            "root_scoped_validator_report_coverage": True,
            "phase_contract_tests_passed": True,
        },
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-r-lifecycle-workitem-closeout.md",
            "summary_json_path": "docs/reports/s3a-m2-r-lifecycle-workitem-summary.json",
        },
        "public_redaction": {"passed": True},
        "safety": {
            "no_production_execute": True,
            "production_execute_ran": False,
            "no_source_icloud_mutation": True,
            "source_icloud_mutation": False,
            "no_app_storage_repair_or_mutation": True,
            "app_storage_repair_or_mutation": False,
            "no_db_import": True,
            "db_import": False,
            "no_production_classification_ai_localization": True,
            "production_classification_ai_localization": False,
            "no_provider_or_source_metadata_calls": True,
            "provider_or_source_metadata_calls": False,
            "no_sourceconcept_entity_media_tags_truth_writes": True,
            "sourceconcept_entity_media_tags_truth_writes": False,
        },
        "scope": {
            "full_s3a_m2_r_completion_claimed": False,
            "ui_progress_browser_validation_in_scope": False,
        },
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def test_s3a_m2_r_lifecycle_workitem_contract_accepts_core_completion_summary() -> None:
    result = check_phase_contract("s3a_m2_r_lifecycle_workitem_contract_v1", _s3a_m2_r_lifecycle_summary())

    assert result.passed is True


def test_s3a_m2_r_lifecycle_workitem_contract_rejects_overclaim_and_mutation() -> None:
    summary = _s3a_m2_r_lifecycle_summary()
    _set_nested(summary, "pipeline_contract.claims.full_s3a_m2_r_complete", True)
    _set_nested(summary, "scope.full_s3a_m2_r_completion_claimed", True)
    _set_nested(summary, "safety.production_execute_ran", True)

    result = check_phase_contract("s3a_m2_r_lifecycle_workitem_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_r_lifecycle_overclaimed_full_completion" in codes
    assert "s3a_m2_r_lifecycle_forbidden_scope_or_mutation" in codes


def test_s3a_m2_r_lifecycle_workitem_contract_requires_source_boundaries_and_scenarios() -> None:
    summary = _s3a_m2_r_lifecycle_summary()
    _set_nested(summary, "work_item_source_read_boundaries.FOLLOWUP.allowed_source_reads", True)
    _set_nested(summary, "validation.table_driven_lifecycle_scenarios.count", 10)

    result = check_phase_contract("s3a_m2_r_lifecycle_workitem_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_r_source_boundary_invalid" in codes
    assert "s3a_m2_r_lifecycle_scenario_count_too_low" in codes


def test_s3a_m2_r_lifecycle_workitem_contract_validates_placeholder_boundary() -> None:
    summary = _s3a_m2_r_lifecycle_summary()
    _set_nested(summary, "work_item_source_read_boundaries.PLACEHOLDER.allowed_source_reads", True)

    result = check_phase_contract("s3a_m2_r_lifecycle_workitem_contract_v1", summary)

    assert "s3a_m2_r_source_boundary_invalid" in _error_codes(result)
    assert any(
        error.path == "work_item_source_read_boundaries.PLACEHOLDER.allowed_source_reads"
        for error in result.errors
    )


def _s3a_m2_r_operator_validation_summary(**overrides: object) -> dict:
    summary = {
        "phase": "S3A-M2-R PR-R2",
        "pipeline_contract": {
            "contract_id": "s3a_m2_r_operator_validation_contract_v1",
            "status": "operator_ready",
            "phase_identity": "S3A-M2-R PR-R2",
            "claims": {
                "operator_ready": True,
                "full_s3a_m2_r_complete": False,
                "target_met": True,
                "safe_to_merge": True,
                "full_chain_complete": False,
                "target_met_scope": "operator_ready_visible_non_clean_debt",
                "safe_to_merge_scope": "operator_ready_visible_non_clean_debt",
            },
        },
        "ui_progress": {
            "plan_progress_visible": True,
            "execute_transition_visible_before_run_id": True,
            "execute_duplicate_submit_disabled": True,
            "stage_heartbeat_visible": True,
            "error_state_visible": True,
        },
        "operator_labels": {
            "operator_statuses": {
                "completed": "已完成",
                "completed_with_retryable_failures": "已完成但有可重试债务",
                "completed_with_followup_required": "已完成但需后续补处理",
                "completed_with_continuation": "已完成当前批次，仍需续跑",
                "completed_with_retryable_failures_plus_continuation": "已完成当前批次，同时有重试债务和续跑",
                "failed_systemic": "系统性失败",
                "blocked_preflight": "预检阻断",
                "cancelled": "已取消",
            },
            "work_item_kinds": {
                "IMPORT": "导入新媒体",
                "FOLLOWUP": "应用媒体后续补处理",
                "RETRY_SOURCE": "重试源文件读取",
                "BROKEN_STATE": "状态异常诊断",
                "PLACEHOLDER": "云占位/暂缓项目",
                "NOOP_DIAGNOSTIC": "无需执行的诊断项",
            },
            "lifecycle_kinds": {
                "APP_MEDIA_FOLLOWUP": "应用内媒体需要补处理",
                "IMPORT_CANDIDATE": "可导入候选",
                "RETRYABLE_SOURCE_FAILURE": "源文件读取可重试失败",
                "PLACEHOLDER_DEFERRED": "云占位暂缓",
                "STABLE_NOOP": "稳定无操作",
                "HISTORICAL_DIAGNOSTIC": "历史诊断记录",
                "CONTINUATION": "批次续跑",
                "BROKEN_STATE": "状态异常",
                "FATAL_BLOCKER": "致命阻断",
            },
        },
        "work_item_kind_first": {
            "import_counts_from_work_item_kind": True,
            "retry_counts_from_work_item_kind": True,
            "legacy_state_does_not_override_work_item_kind": True,
            "noop_and_placeholder_non_executable": True,
            "broken_diagnostics_visible_non_actionable": True,
            "successful_retry_creates_visible_pending_import": True,
            "source_missing_retry_debt_visible": True,
            "missing_media_or_app_file_visible": True,
        },
        "browser_validation": {"status": "passed", "execute_transition_checked": True},
        "local_gui_acceptance": {"status": "passed", "gui_plan_passed": True, "gui_execute_passed": True},
        "production_plan_only": {
            "status": "passed",
            "gui_path_used": True,
            "execute_not_run": True,
            "no_unsafe_execute_implied": True,
            "selected_plan_items": 343,
            "plan_items": 343,
            "work_item_counts": {"FOLLOWUP": 20, "IMPORT": 312, "RETRY_SOURCE": 11},
            "lifecycle_counts": {
                "APP_MEDIA_FOLLOWUP": 20,
                "CONTINUATION": 179,
                "IMPORT_CANDIDATE": 133,
                "RETRYABLE_SOURCE_FAILURE": 11,
            },
            "state_counts": {
                "skipped_unsupported": 10,
                "downstream_followup_planned": 20,
                "import_planned": 312,
                "retry_source_planned": 11,
                "failed": 4,
            },
            "state_counts_total": 357,
            "state_counts_scope": "broader planner state rows including skipped/failed diagnostics outside selected plan items",
        },
        "advanced_full_rescan_policy": {"retry_source_not_executable_until_validated": True},
        "public_redaction": {"passed": True, "finding_count": 0},
        "s3b_disabled": {"enabled": False},
        "production_execute": {"ran": False, "owner_approved": False, "approval_reference": None},
        "safety": {
            "no_production_execute_without_owner_approval": True,
            "production_execute_ran": False,
            "no_source_icloud_mutation": True,
            "source_icloud_mutation": False,
            "no_app_storage_repair_or_mutation": True,
            "app_storage_repair_or_mutation": False,
            "no_destructive_cleanup": True,
            "destructive_cleanup": False,
            "no_provider_pixiv_sourceconcept_entity_work": True,
            "provider_pixiv_sourceconcept_entity_work": False,
        },
        "scope": {
            "s3b_started": False,
            "pixiv_provider_sourceconcept_entity_started": False,
        },
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "docs/reports/s3a-m2-r-ui-operator-validation-closeout.md",
                    "classification": "Public report / handoff / roadmap update",
                    "redacted": True,
                    "committed": True,
                },
                {
                    "path": "docs/reports/s3a-m2-r-ui-operator-validation-summary.json",
                    "classification": "Public report / handoff / roadmap update",
                    "redacted": True,
                    "committed": True,
                },
            ],
        },
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-r-ui-operator-validation-closeout.md",
            "summary_json_path": "docs/reports/s3a-m2-r-ui-operator-validation-summary.json",
        },
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def test_s3a_m2_r_operator_validation_contract_accepts_operator_ready_summary() -> None:
    result = check_phase_contract("s3a_m2_r_operator_validation_contract_v1", _s3a_m2_r_operator_validation_summary())

    assert result.passed is True


def test_s3a_m2_r_operator_validation_contract_rejects_production_execute_flag_mismatch() -> None:
    summary = _s3a_m2_r_operator_validation_summary()
    _set_nested(summary, "production_execute.ran", True)
    _set_nested(summary, "safety.production_execute_ran", False)

    result = check_phase_contract("s3a_m2_r_operator_validation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_r_production_execute_flags_disagree" in codes
    assert "s3a_m2_r_production_execute_without_owner_approval" in codes
    assert "s3a_m2_r_production_execute_approval_reference_missing" in codes


def test_s3a_m2_r_operator_validation_contract_rejects_production_plan_count_mismatch() -> None:
    summary = _s3a_m2_r_operator_validation_summary()
    _set_nested(summary, "production_plan_only.work_item_counts.IMPORT", 311)
    _set_nested(summary, "production_plan_only.state_counts_scope", "")

    result = check_phase_contract("s3a_m2_r_operator_validation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_r_production_plan_only_work_item_count_mismatch" in codes
    assert "s3a_m2_r_production_plan_only_state_count_scope_missing" in codes


def test_s3a_m2_r_operator_validation_contract_rejects_non_clean_full_chain_claims() -> None:
    summary = _s3a_m2_r_operator_validation_summary()
    _set_nested(summary, "pipeline_contract.claims.full_chain_complete", True)
    _set_nested(summary, "pipeline_contract.claims.full_s3a_m2_r_complete", True)
    _set_nested(
        summary,
        "work_item_kind_first.local_final_db_truth",
        {
            "import_status": {"imported": 2, "failed": 1},
            "localization_status": {"localized": 2, "deferred": 1},
        },
    )

    result = check_phase_contract("s3a_m2_r_operator_validation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_r_operator_full_chain_overclaimed_with_non_clean_evidence" in codes
    assert "s3a_m2_r_operator_full_s3a_m2_r_overclaimed_with_non_clean_evidence" in codes


def test_s3a_m2_r_operator_validation_contract_allows_operator_ready_with_visible_non_clean_debt() -> None:
    summary = _s3a_m2_r_operator_validation_summary()
    _set_nested(
        summary,
        "work_item_kind_first.local_final_db_truth",
        {
            "import_status": {"imported": 2, "failed": 1},
            "localization_status": {"localized": 2, "deferred": 1},
        },
    )

    result = check_phase_contract("s3a_m2_r_operator_validation_contract_v1", summary)

    assert result.passed is True


def test_s3a_m2_r_operator_validation_contract_rejects_placeholder_chinese_label() -> None:
    summary = _s3a_m2_r_operator_validation_summary()
    _set_nested(summary, "operator_labels.operator_statuses.completed_with_continuation", "????????")

    result = check_phase_contract("s3a_m2_r_operator_validation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_r_operator_status_labels_missing_placeholder_or_empty" in codes


def test_public_redaction_contract_rejects_production_source_root_label_leak() -> None:
    result = check_phase_contract(
        "public_redaction_contract_v1",
        {"selected_root_label_public": "2: icloud-photos-production (153684ac)"},
    )
    codes = _error_codes(result)

    assert "public_redaction_private_provenance_value_unredacted" in codes


def test_s3a_m2_r_operator_validation_contract_rejects_missing_gui_and_plan_only_gates() -> None:
    summary = _s3a_m2_r_operator_validation_summary()
    _set_nested(summary, "local_gui_acceptance.gui_execute_passed", False)
    _set_nested(summary, "production_plan_only.status", "blocked")
    _set_nested(summary, "pipeline_contract.claims.full_s3a_m2_r_complete", True)

    result = check_phase_contract("s3a_m2_r_operator_validation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_r_operator_required_proof_missing" in codes
    assert "s3a_m2_r_production_plan_only_not_passed" in codes
    assert "s3a_m2_r_operator_full_completion_overclaimed" in codes


def test_s3a_m2_r_operator_validation_contract_rejects_s3b_and_provider_scope() -> None:
    summary = _s3a_m2_r_operator_validation_summary()
    _set_nested(summary, "s3b_disabled.enabled", True)
    _set_nested(summary, "safety.provider_pixiv_sourceconcept_entity_work", True)

    result = check_phase_contract("s3a_m2_r_operator_validation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_r_s3b_enabled" in codes
    assert "s3a_m2_r_operator_forbidden_scope_or_mutation" in codes


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


@pytest.mark.parametrize(
    "write_request",
    [
        "production_import",
        "production_classification",
        "production_ai_tagging",
        "production_localization",
        "source_root_registration",
        "source_root_replacement",
        "schema_setup",
        "schema_migration",
    ],
)
def test_production_development_separation_rejects_production_write_requests_outright(write_request: str) -> None:
    summary = _pd1a_governance_summary()
    summary["write_requests"][write_request] = True
    summary["production_promotion"]["enabled"] = True
    summary["production_promotion"]["operator_confirmation_present"] = True

    result = check_phase_contract("production_development_separation_contract_v1", summary)
    codes = _error_codes(result)

    assert "production_development_write_request_forbidden" in codes
    assert "production_write_without_promotion_mode" not in codes
    assert "production_write_without_operator_confirmation" not in codes


def test_production_development_separation_rejects_source_root_write_request_before_identity_gates() -> None:
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

    assert "production_development_write_request_forbidden" in _error_codes(result)


def test_production_development_separation_rejects_schema_write_request_before_identity_gates() -> None:
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

    assert "production_development_write_request_forbidden" in _error_codes(result)


def test_production_development_separation_rejects_forbidden_current_phase_authorization() -> None:
    summary = _pd1a_governance_summary()
    summary["phase_boundaries"]["authorizes_provider_calls"] = True

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_development_forbidden_current_phase_authorization" in _error_codes(result)


def test_production_development_separation_rejects_split_s2g_route() -> None:
    summary = _pd1a_governance_summary()
    summary["phase_boundaries"]["next_recommended_phase"] = "S2G-1 GPU AI tagging capability probe and benchmark"

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_development_next_phase_not_consolidated_s2g" in _error_codes(result)


@pytest.mark.parametrize(
    "next_phase",
    [
        "Provider-2 before S2G",
        "A1R then S2G",
        "R1R before S2G",
        "S2G-2/3",
    ],
)
def test_production_development_separation_requires_s2g_as_immediate_exact_next_phase(next_phase: str) -> None:
    summary = _pd1a_governance_summary()
    summary["phase_boundaries"]["next_recommended_phase"] = next_phase

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_development_next_phase_not_consolidated_s2g" in _error_codes(result)


@pytest.mark.parametrize(
    "stage",
    [
        "S2G",
        "s2g",
        "S2G_EXECUTION",
        "s2g-execution",
        "s2g execution",
        "gpu_benchmark",
        "AI_TAGGING_EXECUTION",
        "LLM_CALL",
        "llm-call",
        "llm call",
        "openai_call",
    ],
)
def test_production_development_separation_rejects_normalized_forbidden_execution_stages(stage: str) -> None:
    summary = _pd1a_governance_summary(executed_stages=[stage])

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "forbidden_stage_executed" in _error_codes(result)


@pytest.mark.parametrize("stage", ["A1R", "route_audit", "route_decision"])
def test_production_development_separation_rejects_a1r_route_audit_execution(stage: str) -> None:
    summary = _pd1a_governance_summary(executed_stages=[stage])

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "forbidden_stage_executed" in _error_codes(result)


def test_production_development_separation_rejects_no_llm_calls_false() -> None:
    summary = _pd1a_governance_summary()
    summary["safety"]["no_llm_calls"] = False

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_development_llm_calls_not_forbidden" in _error_codes(result)


def test_production_development_separation_rejects_no_media_tags_mutation_false() -> None:
    summary = _pd1a_governance_summary()
    summary["safety"]["no_media_tags_mutation"] = False

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_development_media_tags_mutation_not_forbidden" in _error_codes(result)


def test_production_development_separation_requires_no_media_tags_mutation_proof() -> None:
    summary = _pd1a_governance_summary()
    del summary["safety"]["no_media_tags_mutation"]

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "missing_required_summary_field" in _error_codes(result)


def test_production_development_separation_rejects_media_tags_mutation_stage() -> None:
    summary = _pd1a_governance_summary(executed_stages=["media_tags_mutation"])

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "forbidden_stage_executed" in _error_codes(result)


def test_production_development_separation_rejects_stale_top_level_phase() -> None:
    summary = _pd1a_governance_summary()
    summary["phase"] = "PD1-A"

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_development_phase_mismatch" in _error_codes(result)


def test_production_development_separation_rejects_stale_current_phase() -> None:
    summary = _pd1a_governance_summary()
    summary["phase_boundaries"]["current_phase"] = "PD1-A"

    result = check_phase_contract("production_development_separation_contract_v1", summary)

    assert "production_development_current_phase_mismatch" in _error_codes(result)


def test_prod_launcher_mvp_contract_accepts_safe_launcher_summary() -> None:
    result = check_phase_contract("prod_launcher_mvp_contract_v1", _prod_launcher_mvp_summary())

    assert result.passed is True


def test_prod_launcher_mvp_contract_rejects_debug_start_command() -> None:
    summary = _prod_launcher_mvp_summary(
        start_command={
            "production_mode": True,
            "no_debug": False,
            "debug": True,
            "command": ["python.exe", "run.py", "--debug"],
        }
    )

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)
    codes = _error_codes(result)

    assert "prod_launcher_required_proof_failed" in codes
    assert "prod_launcher_start_command_debug_enabled" in codes


def test_prod_launcher_mvp_contract_rejects_nonignored_state_file() -> None:
    summary = _prod_launcher_mvp_summary(state_file={"local_ignored": True, "path": "data/runtime/state.json"})

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_state_file_not_local_ignored" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_public_status_path_leak() -> None:
    summary = _prod_launcher_mvp_summary()
    summary["health_status"]["status_example"]["storage_root"] = r"C:\Users\name\Pictures\private"

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_public_status_not_safe" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_forbidden_operation_enabled() -> None:
    summary = _prod_launcher_mvp_summary()
    summary["forbidden_operations"]["provider_calls"] = True

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_forbidden_operation_enabled" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_health_auth_not_exempt() -> None:
    summary = _prod_launcher_mvp_summary()
    summary["health_status"]["auth_exempt_for_launcher"] = False

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_required_proof_failed" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_schema_migration_allowed() -> None:
    summary = _prod_launcher_mvp_summary()
    summary["startup_write_policy"]["schema_migration_allowed"] = True

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_forbidden_operation_enabled" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_missing_start_serialization() -> None:
    summary = _prod_launcher_mvp_summary()
    summary["start_safety"]["serialized"] = False

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_required_proof_failed" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_public_log_tail_key() -> None:
    summary = _prod_launcher_mvp_summary()
    summary["diagnostics"]["status_json_example"]["recent_log_tail"] = ["safe looking line"]

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_log_tail_public_json" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_health_ok_without_schema_compatibility() -> None:
    summary = _prod_launcher_mvp_summary()
    del summary["health_status"]["status_example"]["schema_compatible"]

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_health_ok_without_schema_compatible" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_missing_latest_main_proof() -> None:
    summary = _prod_launcher_mvp_summary()
    summary["mainline_sync"]["latest_main_after_pr120_included"] = False

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_required_proof_failed" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_missing_malformed_db_port_proof() -> None:
    summary = _prod_launcher_mvp_summary()
    summary["preflight_gates"]["malformed_db_port_failure"] = False

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_required_proof_failed" in _error_codes(result)


def test_prod_launcher_mvp_contract_rejects_log_tail_public_json_enabled() -> None:
    summary = _prod_launcher_mvp_summary()
    summary["public_json_safety"]["log_tail_in_public_json"] = True

    result = check_phase_contract("prod_launcher_mvp_contract_v1", summary)

    assert "prod_launcher_forbidden_operation_enabled" in _error_codes(result)


def test_prod_launcher_ux1_contract_accepts_pending_manual_acceptance_summary() -> None:
    result = check_phase_contract("prod_launcher_ux1_production_profile_contract_v1", _prod_launcher_ux1_summary())

    assert result.passed is True


def test_prod_launcher_ux1_contract_rejects_development_dotenv_mutation() -> None:
    summary = _prod_launcher_ux1_summary()
    summary["production_profile"]["development_dotenv_modified"] = True

    result = check_phase_contract("prod_launcher_ux1_production_profile_contract_v1", summary)

    assert "prod_launcher_ux1_development_dotenv_modified" in _error_codes(result)


def test_prod_launcher_ux1_contract_rejects_merge_allowed_before_manual_acceptance() -> None:
    summary = _prod_launcher_ux1_summary(merge_allowed=True)

    result = check_phase_contract("prod_launcher_ux1_production_profile_contract_v1", summary)

    assert "prod_launcher_ux1_merge_allowed_before_manual_acceptance" in _error_codes(result)


def test_prod_launcher_ux1_contract_rejects_raw_log_tail_public_json() -> None:
    summary = _prod_launcher_ux1_summary()
    summary["public_json_payload"] = {"recent_log_tail": ["line"]}

    result = check_phase_contract("prod_launcher_ux1_production_profile_contract_v1", summary)

    assert "prod_launcher_ux1_log_tail_public_json" in _error_codes(result)


def test_prod_launcher_ux1_contract_rejects_missing_checklist_group() -> None:
    summary = _prod_launcher_ux1_summary()
    summary["electron_launcher"]["checklist_groups"] = ["Production Profile"]

    result = check_phase_contract("prod_launcher_ux1_production_profile_contract_v1", summary)

    assert "prod_launcher_ux1_missing_checklist_groups" in _error_codes(result)


def test_prod_launcher_ux1_contract_rejects_pending_python_validation() -> None:
    summary = _prod_launcher_ux1_summary()
    summary["validation"]["python_tests_status"] = "pending"

    result = check_phase_contract("prod_launcher_ux1_production_profile_contract_v1", summary)

    assert "prod_launcher_ux1_python_validation_not_passed" in _error_codes(result)


def test_prod_launcher_ux1_contract_rejects_failed_electron_validation() -> None:
    summary = _prod_launcher_ux1_summary()
    summary["validation"]["electron_tests_status"] = "failed"

    result = check_phase_contract("prod_launcher_ux1_production_profile_contract_v1", summary)

    assert "prod_launcher_ux1_electron_validation_not_passed" in _error_codes(result)


def test_prod_launcher_ux1_contract_rejects_missing_skip_dotenv_proof() -> None:
    summary = _prod_launcher_ux1_summary()
    summary["production_profile"]["child_env_skips_dotenv"] = False

    result = check_phase_contract("prod_launcher_ux1_production_profile_contract_v1", summary)

    assert "prod_launcher_ux1_required_proof_failed" in _error_codes(result)


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


def test_s2g1x_probe_contract_requires_gpu_provider_checks_for_completion() -> None:
    summary = _s2g1x_summary()
    _set_nested(summary, "capability_probe.provider_matrix.cuda.benchmark_status", "not_requested")
    _set_nested(summary, "capability_probe.provider_matrix.directml.benchmark_status", "not_requested")

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert result.passed is False
    assert "s2g1x_completion_provider_not_checked" in _error_codes(result)


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


def test_s2g1x_probe_contract_allows_blocked_probe_unavailable_without_cpu_provider() -> None:
    summary = _s2g1x_summary()
    summary["pipeline_contract"] = {
        "contract_id": "s2g1x_probe_contract_v1",
        "status": "blocked_probe_unavailable",
        "claims": {"target_met": False, "safe_to_merge": False},
    }
    _set_nested(summary, "capability_probe.provider_matrix.cpu.available", False)
    _set_nested(summary, "capability_probe.provider_matrix.cpu.loaded", False)
    _set_nested(summary, "capability_probe.provider_matrix.cpu.practical", False)
    _set_nested(summary, "capability_probe.provider_matrix.cpu.benchmark_status", "not_available")
    _set_nested(summary, "capability_probe.provider_matrix.cpu.throughput_items_per_second", None)

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert result.passed is True


def test_s2g1x_probe_contract_independently_scans_public_payload() -> None:
    summary = _s2g1x_summary()
    summary["public_redaction"] = {"passed": True}
    summary["capability_probe"]["leaked_example"] = r"C:\Users\example\private.png"

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert result.passed is False
    assert "s2g1x_public_payload_redaction_failed" in _error_codes(result)
    assert "C:\\Users\\example\\private.png" not in _serialized_result(result)


def test_s2g1x_probe_contract_scans_markdown_report(monkeypatch) -> None:
    monkeypatch.setattr(
        contract_checks_module,
        "_read_s2g1x_markdown_report",
        lambda _summary, _result: r"leaked C:\Users\example\private.png",
    )

    result = check_phase_contract("s2g1x_probe_contract_v1", _s2g1x_summary())

    assert result.passed is False
    assert "s2g1x_public_payload_redaction_failed" in _error_codes(result)
    assert "C:\\Users\\example\\private.png" not in _serialized_result(result)


def test_s2g1x_probe_contract_rejects_stale_head_evidence_after_probe_code_changes(monkeypatch) -> None:
    summary = _s2g1x_summary()
    summary["head_evidence"]["report_generation_head_sha"] = "old-head"
    monkeypatch.setattr(contract_checks_module, "_current_git_head", lambda: "new-head")
    monkeypatch.setattr(
        contract_checks_module,
        "_changed_paths_between",
        lambda _old, _new, _paths: ["scripts/run_s2g1_ai_tagging_capability_probe.py"],
    )

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert result.passed is False
    assert "s2g1x_probe_evidence_stale_for_current_code" in _error_codes(result)


def test_s2g1x_probe_contract_requires_explicit_false_safety_flags() -> None:
    summary = _s2g1x_summary()
    del summary["safety"]["provider_pixiv_gallery_dl_saucenao_google_calls"]

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert result.passed is False
    assert "s2g1x_required_safety_false_missing_or_true" in _error_codes(result)


def test_s2g1x_probe_contract_accepts_explicit_false_safety_flags() -> None:
    result = check_phase_contract("s2g1x_probe_contract_v1", _s2g1x_summary())

    assert result.passed is True


def test_s2g1x_probe_contract_requires_s3a_dry_run_only() -> None:
    base = _s2g1x_summary()
    summary = _s2g1x_summary(
        s3a_dev_dry_run_plan={
            **base["s3a_dev_dry_run_plan"],
            "dry_run_only": False,
        }
    )

    result = check_phase_contract("s2g1x_probe_contract_v1", summary)

    assert result.passed is False
    assert "s2g1x_required_probe_proof_missing" in _error_codes(result)


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
    assert "s2g1x_required_safety_false_missing_or_true" in codes


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


def test_s2g_s3a_f1_contract_accepts_provider_load_control_foundation() -> None:
    result = check_phase_contract("s2g_s3a_f1_foundation_contract_v1", _s2g_s3a_f1_summary())

    assert result.passed is True


def test_s2g_s3a_f1_contract_requires_fallback_reason_when_fallback_occurs() -> None:
    summary = copy.deepcopy(_s2g_s3a_f1_summary())
    summary["wd_tagger"]["provider_abstraction"]["fallback_reason"] = ""

    result = check_phase_contract("s2g_s3a_f1_foundation_contract_v1", summary)

    assert "s2g_s3a_f1_fallback_reason_missing" in _error_codes(result)


def test_s2g_s3a_f1_contract_rejects_fallback_reason_without_fallback() -> None:
    summary = copy.deepcopy(_s2g_s3a_f1_summary())
    summary["wd_tagger"]["provider_abstraction"]["fallback_occurred"] = False
    summary["wd_tagger"]["provider_abstraction"]["fallback_reason"] = "unavailable_requested_providers=CUDAExecutionProvider"

    result = check_phase_contract("s2g_s3a_f1_foundation_contract_v1", summary)

    assert "s2g_s3a_f1_fallback_reason_present_without_fallback" in _error_codes(result)


def test_s2g_s3a_f1_contract_rejects_unbounded_cpu_controls() -> None:
    summary = copy.deepcopy(_s2g_s3a_f1_summary())
    summary["wd_tagger"]["load_control"]["batch_size"] = 32
    summary["wd_tagger"]["load_control"]["effective_batch_size"] = 32
    summary["wd_tagger"]["load_control"]["cpu_intra_op_threads"] = 16
    summary["wd_tagger"]["load_control"]["cpu_inter_op_threads"] = 4
    summary["wd_tagger"]["load_control"]["preprocess_workers"] = 8
    summary["wd_tagger"]["load_control"]["execution_mode"] = "ORT_PARALLEL"

    result = check_phase_contract("s2g_s3a_f1_foundation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s2g_s3a_f1_batch_size_unbounded" in codes
    assert "s2g_s3a_f1_effective_batch_size_unbounded" in codes
    assert "s2g_s3a_f1_cpu_intra_threads_unbounded" in codes
    assert "s2g_s3a_f1_cpu_inter_threads_unbounded" in codes
    assert "s2g_s3a_f1_preprocess_workers_unbounded" in codes
    assert "s2g_s3a_f1_parallel_execution_enabled" in codes


def test_s2g_s3a_f1_contract_requires_effective_batch_size_and_cap_source() -> None:
    summary = copy.deepcopy(_s2g_s3a_f1_summary())
    summary["wd_tagger"]["load_control"]["effective_batch_size"] = 3
    summary["wd_tagger"]["load_control"]["batch"]["batch_cap_source"] = ""

    result = check_phase_contract("s2g_s3a_f1_foundation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s2g_s3a_f1_effective_batch_size_mismatch" in codes
    assert "s2g_s3a_f1_batch_cap_source_missing" in codes


def test_s2g_s3a_f1_contract_rejects_gpu_success_without_actual_loaded_provider() -> None:
    summary = copy.deepcopy(_s2g_s3a_f1_summary())
    summary["gpu_directml_enablement"]["success"] = True
    summary["gpu_directml_enablement"]["actual_gpu_provider_loaded"] = None
    summary["gpu_directml_enablement"]["blocker"] = None

    result = check_phase_contract("s2g_s3a_f1_foundation_contract_v1", summary)

    assert "s2g_s3a_f1_gpu_success_without_gpu_provider" in _error_codes(result)


def test_s2g_s3a_f1_contract_requires_gpu_blocker_when_unavailable() -> None:
    summary = copy.deepcopy(_s2g_s3a_f1_summary())
    summary["gpu_directml_enablement"]["success"] = False
    summary["gpu_directml_enablement"]["blocker"] = ""

    result = check_phase_contract("s2g_s3a_f1_foundation_contract_v1", summary)

    assert "s2g_s3a_f1_gpu_unavailable_blocker_missing" in _error_codes(result)


def test_s2g_s3a_f1_contract_rejects_model_download_allowed() -> None:
    summary = copy.deepcopy(_s2g_s3a_f1_summary())
    summary["wd_tagger"]["model"]["model_download_allowed"] = True

    result = check_phase_contract("s2g_s3a_f1_foundation_contract_v1", summary)

    assert "s2g_s3a_f1_model_download_allowed" in _error_codes(result)


def test_s2g_s3a_f1_contract_rejects_s3a_or_s3b_execution_enabled() -> None:
    summary = copy.deepcopy(_s2g_s3a_f1_summary())
    summary["s3a_dry_run_plan"]["production_execution_enabled"] = True
    summary["s3a_dry_run_plan"]["unattended_enabled"] = True
    summary["safety"]["production_s3a_execution_enabled"] = True
    summary["safety"]["unattended_auto_sync_enabled"] = True

    result = check_phase_contract("s2g_s3a_f1_foundation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s2g_s3a_f1_forbidden_execution_enabled" in codes
    assert "s2g_s3a_f1_required_safety_false_missing_or_true" in codes


def test_s2g_real1_contract_accepts_bounded_dry_run_without_write() -> None:
    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        _s2g_real1_summary(),
    )

    assert result.passed is True


def test_s2g_real1_contract_rejects_max_items_above_cap() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["run_configuration"]["max_items"] = 6
    summary["selected_media"]["count"] = 6
    summary["selected_media"]["id_count"] = 6
    summary["selected_media"]["max_items"] = 6
    summary["dry_run"]["selected_media_count"] = 6
    summary["safety"]["max_items_lte_5"] = False

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )
    codes = _error_codes(result)

    assert "s2g_real1_max_items_unbounded" in codes
    assert "s2g_real1_selected_media_not_small" in codes
    assert "s2g_real1_required_proof_missing" in codes


def test_s2g_real1_contract_rejects_write_without_exact_confirmation() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["run_configuration"]["write_requested"] = True
    summary["write_run"]["executed"] = True
    summary["write_run"]["selected_media_count"] = 3
    summary["write_run"]["processed"] = 3
    summary["safety"]["media_tags_write_executed"] = True
    summary["safety"]["write_requested_without_exact_confirmation"] = True

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )

    assert "s2g_real1_write_without_exact_confirmation" in _error_codes(result)


def test_s2g_real1_contract_rejects_execute_without_confirmation_as_dry_run_target() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["run_configuration"]["write_requested"] = True
    summary["write_run"]["status"] = "not_run_missing_exact_operator_confirmation"
    summary["safety"]["write_requested_without_exact_confirmation"] = True

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )
    codes = _error_codes(result)

    assert "s2g_real1_write_requested_without_exact_confirmation_not_blocked" in codes
    assert "s2g_real1_dry_run_target_with_write_requested" in codes


def test_s2g_real1_contract_accepts_successful_bounded_write() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["pipeline_contract"]["status"] = "target_met_with_bounded_write"
    summary["pipeline_contract"]["claims"]["target_met"] = True
    summary["pipeline_contract"]["claims"]["safe_to_merge"] = True
    summary["run_configuration"]["mode"] = "execute"
    summary["run_configuration"]["write_requested"] = True
    summary["run_configuration"]["operator_confirmation_exact"] = True
    summary["write_run"] = {
        "executed": True,
        "status": "completed",
        "required_confirmation_present": True,
        "selected_media_count": 3,
        "processed": 3,
        "media_tags_count_before": 10,
        "media_tags_count_after": 15,
        "media_tags_count_delta": 5,
        "tags_added": 5,
        "suggestions_added": 0,
        "skipped_locked": 0,
        "ignored_low_confidence": 9,
        "failed": 0,
        "rollback_error": False,
        "error_state": False,
        "tag_source_values_used": ["ai_wd"],
    }
    summary["write_prerequisites"]["exact_write_confirmation_present"] = True
    summary["write_prerequisites"]["all_passed"] = True
    summary["safety"]["media_tags_write_executed"] = True

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )

    assert result.passed is True


def test_s2g_real1_contract_rejects_failed_bounded_write_target() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["pipeline_contract"]["status"] = "target_met_with_bounded_write"
    summary["run_configuration"]["mode"] = "execute"
    summary["run_configuration"]["write_requested"] = True
    summary["run_configuration"]["operator_confirmation_exact"] = True
    summary["write_run"].update(
        {
            "executed": True,
            "status": "completed_with_item_failures",
            "required_confirmation_present": True,
            "selected_media_count": 3,
            "processed": 3,
            "failed": 1,
        }
    )
    summary["write_prerequisites"]["exact_write_confirmation_present"] = True
    summary["write_prerequisites"]["all_passed"] = True
    summary["safety"]["media_tags_write_executed"] = True

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )
    codes = _error_codes(result)

    assert "s2g_real1_write_run_failed_not_blocked" in codes
    assert "s2g_real1_write_run_failed_target" in codes


def test_s2g_real1_contract_rejects_write_before_cpu_fallback_prerequisite() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["pipeline_contract"]["status"] = "target_met_with_bounded_write"
    summary["run_configuration"]["mode"] = "execute"
    summary["run_configuration"]["write_requested"] = True
    summary["run_configuration"]["operator_confirmation_exact"] = True
    summary["write_run"].update(
        {
            "executed": True,
            "status": "completed",
            "required_confirmation_present": True,
            "selected_media_count": 3,
            "processed": 3,
            "media_tags_count_delta": 2,
        }
    )
    summary["write_prerequisites"]["exact_write_confirmation_present"] = True
    summary["write_prerequisites"]["cpu_fallback_success"] = False
    summary["write_prerequisites"]["write_executed_after_prerequisites_passed"] = False
    summary["write_prerequisites"]["all_passed"] = False
    summary["safety"]["media_tags_write_executed"] = True
    summary["safety"]["write_executed_after_prerequisites_passed"] = False

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )
    codes = _error_codes(result)

    assert "s2g_real1_write_before_prerequisites" in codes
    assert "s2g_real1_write_target_without_prerequisites" in codes
    assert "s2g_real1_write_target_missing_prerequisite" in codes


def test_s2g_real1_contract_rejects_model_download_target() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["pipeline_contract"]["status"] = "target_met_dry_run_only"
    summary["run_configuration"]["local_files_only"] = False
    summary["run_configuration"]["model_download_allowed"] = True
    summary["model_cache"]["local_files_only"] = False
    summary["model_cache"]["model_download_allowed"] = True
    summary["safety"]["model_download"] = True

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )
    codes = _error_codes(result)

    assert "s2g_real1_model_download_allowed_claimed_target" in codes
    assert "s2g_real1_model_download_allowed_not_blocked" in codes


def test_s2g_real1_contract_rejects_missing_primary_provider() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["primary_provider_validation"]["provider"]["actual_provider"] = None

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )

    assert "s2g_real1_actual_provider_missing" in _error_codes(result)


def test_s2g_real1_contract_rejects_directml_absent_without_blocker() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["primary_provider_validation"]["provider"]["actual_provider"] = "CPUExecutionProvider"
    summary["primary_provider_validation"]["provider"]["actual_onnx_provider_loaded"] = "CPUExecutionProvider"
    summary["primary_provider_validation"]["provider"]["fallback_occurred"] = False
    summary["primary_provider_validation"]["provider"]["fallback_reason"] = None
    summary["primary_provider_validation"]["provider"]["provider_load_errors"] = []

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )

    assert "s2g_real1_directml_missing_without_blocker" in _error_codes(result)


def test_s2g_real1_contract_rejects_missing_cpu_fallback() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["cpu_fallback_validation"]["executed"] = False
    summary["cpu_fallback_validation"]["provider"]["actual_provider"] = None

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )
    codes = _error_codes(result)

    assert "s2g_real1_required_proof_missing" in codes
    assert "s2g_real1_cpu_fallback_actual_provider_invalid" in codes


def test_s2g_real1_contract_rejects_dry_run_media_tag_delta() -> None:
    summary = copy.deepcopy(_s2g_real1_summary())
    summary["dry_run"]["media_tags_count_delta"] = 1
    summary["dry_run"]["no_media_tags_writes"] = False
    summary["safety"]["dry_run_media_tags_write"] = True

    result = check_phase_contract(
        "s2g_real1_bounded_ai_tagging_validation_contract_v1",
        summary,
    )
    codes = _error_codes(result)

    assert "s2g_real1_dry_run_media_tags_delta" in codes
    assert "s2g_real1_required_proof_missing" in codes
    assert "s2g_real1_forbidden_safety_flag" in codes


def test_s2g_m1_contract_accepts_ai_and_manual_sync_foundation() -> None:
    result = check_phase_contract(
        "s2g_manual_sync_foundation_contract_v1",
        _s2g_m1_summary(),
    )

    assert result.passed is True


def test_s2g_m1_contract_rejects_base_main_only_head_evidence() -> None:
    summary = copy.deepcopy(_s2g_m1_summary())
    base_main = summary["head_evidence"]["pr123_merge_commit"]
    summary["head_evidence"]["validated_implementation_sha"] = base_main
    summary["head_evidence"]["validated_implementation_is_not_base_main"] = False
    summary["head_evidence"]["head_evidence_valid"] = False

    result = check_phase_contract("s2g_manual_sync_foundation_contract_v1", summary)
    codes = _error_codes(result)

    assert "s2g_m1_stale_base_main_head_evidence" in codes


def test_s2g_m1_contract_accepts_blocked_stale_head_evidence_without_completion_claim() -> None:
    summary = copy.deepcopy(_s2g_m1_summary())
    base_main = summary["head_evidence"]["pr123_merge_commit"]
    summary["pipeline_contract"]["status"] = "blocked_stale_head_evidence"
    summary["pipeline_contract"]["claims"] = {"target_met": False, "safe_to_merge": False, "full_chain_complete": False}
    summary["head_evidence"]["validated_implementation_sha"] = base_main
    summary["head_evidence"]["validated_implementation_is_not_base_main"] = False
    summary["head_evidence"]["head_evidence_valid"] = False

    result = check_phase_contract("s2g_manual_sync_foundation_contract_v1", summary)

    assert result.passed is True


def test_s2g_m1_contract_rejects_target_without_focused_tests() -> None:
    summary = copy.deepcopy(_s2g_m1_summary())
    summary["validation"]["focused_tests_passed"] = False

    result = check_phase_contract("s2g_manual_sync_foundation_contract_v1", summary)

    assert "s2g_m1_target_without_focused_tests" in _error_codes(result)


def test_s2g_m1_contract_rejects_production_writes_and_db_mutation() -> None:
    summary = copy.deepcopy(_s2g_m1_summary())
    summary["ai_execution_profile"]["production_writes_enabled"] = True
    summary["manual_sync"]["dry_run_planner"]["db_write_performed"] = True
    summary["safety"]["production_db_mutation"] = True
    summary["safety"]["production_media_tags_mutation"] = True

    result = check_phase_contract("s2g_manual_sync_foundation_contract_v1", summary)

    assert "s2g_m1_forbidden_execution_or_mutation" in _error_codes(result)


def test_s2g_m1_contract_rejects_automatic_sync_and_final_acceptance() -> None:
    summary = copy.deepcopy(_s2g_m1_summary())
    summary["safety"]["automatic_sync_enabled"] = True
    summary["safety"]["scheduled_sync_enabled"] = True
    summary["safety"]["final_production_acceptance_completed"] = True

    result = check_phase_contract("s2g_manual_sync_foundation_contract_v1", summary)

    assert "s2g_m1_forbidden_execution_or_mutation" in _error_codes(result)


def test_s2g_m1_contract_rejects_write_enabled_pipeline_stage() -> None:
    summary = copy.deepcopy(_s2g_m1_summary())
    summary["manual_sync"]["controlled_pipeline"]["stages"][1]["writes_enabled"] = True

    result = check_phase_contract("s2g_manual_sync_foundation_contract_v1", summary)

    assert "s2g_m1_pipeline_stage_write_enabled" in _error_codes(result)


def test_s2g_m1_contract_rejects_public_redaction_leak(monkeypatch) -> None:
    monkeypatch.setattr(
        contract_checks_module,
        "_read_s2g_m1_markdown_report",
        lambda _summary, _result: r"C:\Users\private\Pictures\example.png",
    )

    result = check_phase_contract("s2g_manual_sync_foundation_contract_v1", _s2g_m1_summary())

    assert "s2g_m1_public_payload_redaction_failed" in _error_codes(result)


def test_s3a_m1_contract_accepts_manual_sync_execute_ui() -> None:
    result = check_phase_contract(
        "s3a_m1_manual_sync_execute_contract_v1",
        _s3a_m1_summary(),
    )

    assert result.passed is True


def test_s3a_m1_contract_rejects_missing_confirmation_gate() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["exact_confirmation_required"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_production_execute_completion() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["pipeline"]["production_acceptance_pending"] = False
    summary["safety"]["production_execute_performed"] = True
    summary["safety"]["production_import"] = True
    summary["safety"]["production_acceptance_completed"] = True

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)
    assert "s3a_m1_forbidden_execution_or_mutation" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_launcher_or_browser_validation() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["ui"]["launcher_manual_sync_entry"] = False
    summary["validation"]["browser_validation_performed"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_translation_side_effect_gate() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["translation_side_effect_gates"]["live_worker_state_blocked"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_clip_model_download_allowed() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["classification"]["model_downloads_allowed"] = True
    summary["safety"]["model_downloads"] = True

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_forbidden_execution_or_mutation" in _error_codes(result)
    assert "s3a_m1_forbidden_model_or_localization_side_effect" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_per_item_failure_budget() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["per_item_failures"]["source_missing_recorded"] = False
    summary["manual_sync"]["failure_budget"]["max_item_failures"] = 0

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)
    assert "s3a_m1_failure_budget_count_invalid" in _error_codes(result)


def test_s3a_m1_contract_rejects_private_plan_serializer_gap() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["public_serialization"]["generic_sync_run_redacts_private_plan"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_heuristic_pre_ai_order() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["classification"]["heuristic_ai_tags_before_classification"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_ai_exception_whole_run_failure() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["ai_tagging"]["single_item_failure_does_not_fail_whole_run"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_ai_proper_noun_confirmation() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["ai_tagging"]["proper_nouns_suggestion_only"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_stale_plan_replay_gap() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["plan_replay_protection"]["forged_fresh_timestamp_rejected"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_nondeterministic_directory_walk_hash() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["plan_replay_protection"]["directory_walk_order_deterministic"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_early_stop_pending_loss() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["failure_budget"]["pending_import_preserved_on_early_stop"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_deferred_unprocessed_ledger_rows() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["ledger"]["deferred_unprocessed_rows_materialized"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_import_preledger() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["ledger"]["import_preledger_committed_before_media_write"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_execute_max_files_cap() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["limits"]["execute_max_files"] = 100000

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_execute_max_files_unbounded" in _error_codes(result)


def test_s3a_m1_contract_rejects_unaligned_dry_run_execute_defaults() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["limits"]["dry_run_execute_default_max_files_aligned"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_update_check_forced_to_execute_cap() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["limits"]["normal_update_check_not_forced_to_execute_cap"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_active_job_gate() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["active_job_gates"]["ai_job_active_blocked"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_queued_job_gate() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["active_job_gates"]["queued_ai_job_blocked"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_reciprocal_manual_execute_job_gate() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["active_job_gates"]["manual_sync_execute_active_blocks_ai_job"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_missing_queued_manual_execute_reciprocal_gate() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["active_job_gates"]["queued_manual_sync_execute_blocks_ai_job"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_runner_default_docs_report_output() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["runner_outputs"]["default_report_json_gitignored"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_runner_execute_report_plan_drift() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["runner_outputs"]["execute_report_uses_approved_plan"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_runner_without_db_session_init() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["runner_outputs"]["standalone_db_session_initialized"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_raw_ai_error_public_details() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["ai_tagging"]["returned_error_sanitized"] = False
    summary["manual_sync"]["ai_tagging"]["raw_error_details_public"] = True

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)
    assert "s3a_m1_ai_raw_error_public" in _error_codes(result)


def test_s3a_m1_contract_rejects_launcher_without_content_tab_target() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["ui"]["launcher_manual_sync_forces_content_tab"] = False

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_required_proof_missing" in _error_codes(result)


def test_s3a_m1_contract_rejects_heuristic_classification_without_ai_tag_defer() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["classification"]["heuristic_deferred_when_ai_tags_unavailable"] = False
    summary["manual_sync"]["classification"]["ai_tags_unavailable_reason"] = "raw missing tag text"

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    codes = _error_codes(result)
    assert "s3a_m1_required_proof_missing" in codes
    assert "s3a_m1_heuristic_ai_tags_unavailable_reason_invalid" in codes


def test_s3a_m1_contract_rejects_localization_scheduled_in_execute() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["localization"]["scheduled_in_execute"] = True

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", summary)

    assert "s3a_m1_forbidden_model_or_localization_side_effect" in _error_codes(result)


def test_s3a_m1_contract_rejects_public_redaction_leak(monkeypatch) -> None:
    monkeypatch.setattr(
        contract_checks_module,
        "_read_s3a_m1_markdown_report",
        lambda _summary, _result: r"C:\Users\private\Pictures\example.png",
    )

    result = check_phase_contract("s3a_m1_manual_sync_execute_contract_v1", _s3a_m1_summary())

    assert "s3a_m1_public_payload_redaction_failed" in _error_codes(result)


def test_s3a_m2_contract_accepts_dry_run_pending_approval() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    _set_nested(summary, "pipeline_contract.status", "dry_run_complete_pending_approval")
    _set_nested(summary, "pipeline_contract.claims.target_met", False)
    _set_nested(summary, "pipeline_contract.claims.safe_to_merge", False)
    _set_nested(summary, "pipeline_contract.claims.full_chain_complete", False)
    _set_nested(summary, "pipeline_contract.execute_after_approval", False)
    _set_nested(summary, "pipeline_contract.exact_operator_approval_present", False)
    _set_nested(summary, "production_acceptance.performed", False)
    _set_nested(summary, "production_acceptance.exact_statement", "production acceptance not performed")
    _set_nested(summary, "api_runner_acceptance.execute_ran", False)
    _set_nested(summary, "execute.status", "not_run")
    _set_nested(summary, "execute.imported", 0)
    _set_nested(summary, "classification.count", 0)
    _set_nested(summary, "ai_tagging.count", 0)
    _set_nested(summary, "localization.status", "not_run")
    _set_nested(summary, "localization.translated", 0)
    _set_nested(summary, "localization.llm_called", False)
    _set_nested(summary, "localization.provider_call_count", 0)
    _set_nested(summary, "gpu_telemetry.status", "not_run")
    _set_nested(summary, "gpu_telemetry.validation_status", "partial_provider_unknown")
    _set_nested(summary, "gpu_telemetry.actual_provider", "not_loaded_before_execute")
    _set_nested(summary, "gpu_telemetry.max_gpu_memory_used_mib", 0.0)
    _set_nested(summary, "gpu_telemetry.peak_gpu_utilization_percent", 0.0)
    _set_nested(summary, "ledger_consistency.status", "not_run")
    _set_nested(summary, "ledger_consistency.passed", False)
    _set_nested(summary, "launcher_web_admin_acceptance.validated", False)
    _set_nested(summary, "launcher_web_admin_acceptance.status", "pending_after_dry_run")

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert result.passed is True


def test_s3a_m2_contract_accepts_explicit_cap_1000() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["controlled_delta"]["cap"] = 1000
    summary["dry_run"]["total_seen"] = 900

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert result.passed is True


def test_s3a_m2_contract_rejects_cap_above_1000() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["controlled_delta"]["cap"] = 1001

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_delta_cap_out_of_bounds" in _error_codes(result)


def test_s3a_m2_contract_accepts_target_met() -> None:
    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", _s3a_m2_summary())

    assert result.passed is True


def test_s3a_m2_contract_rejects_cap_exceeded_completion() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["controlled_delta"]["cap_exceeded"] = True
    summary["dry_run"]["total_seen"] = 301

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_cap_exceeded_wrong_status" in codes
    assert "s3a_m2_cap_exceeded_claimed_completion" in codes


def test_s3a_m2_contract_rejects_localization_when_execute_not_completed() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["execute"]["status"] = "failed"
    summary["localization"]["executed"] = True
    summary["localization"]["llm_called"] = True

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_localization_ran_without_completed_execute" in _error_codes(result)


def test_s3a_m2_contract_rejects_cpu_fallback_as_gpu_success() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["gpu_telemetry"]["actual_provider"] = "CPUExecutionProvider"
    summary["gpu_telemetry"]["validation_status"] = "passed"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_gpu_pass_without_gpu_provider" in codes
    assert "s3a_m2_cpu_fallback_claimed_target" in codes


def test_s3a_m2_contract_rejects_missing_launcher_validation_for_target() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["validated"] = False
    summary["launcher_web_admin_acceptance"]["status"] = "pending_browser_validation_after_runner"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_target_proof_missing" in codes
    assert "s3a_m2_launcher_validation_not_passed" in codes


def test_s3a_m2_contract_rejects_remaining_placeholder_completion() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["placeholder_hydration"]["remaining_placeholders_after_hydration"] = 1
    summary["final_inventory"]["placeholders_remaining"] = 1

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_placeholders_remaining_after_hydration" in codes
    assert "s3a_m2_final_placeholders_remaining" in codes


def test_s3a_m2_contract_rejects_remaining_importable_delta_completion() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["final_inventory"]["current_importable_hydrated_supported_items"] = 2

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_importable_items_remaining" in _error_codes(result)


def test_s3a_m2_contract_rejects_unexplained_localization_gap() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["localization_diagnosis"]["diagnosis"] = "localization_gap_remaining"
    summary["localization_diagnosis"]["tags_requiring_localization_after_runner"] = 4

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_localization_diagnosis_not_benign" in codes
    assert "s3a_m2_localization_gap_remaining" in codes


def test_s3a_m2_contract_rejects_localization_partial_without_overflow() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["localization"]["status"] = "partial_localization_max_tags_reached"
    summary["localization"]["candidate_count"] = summary["localization"]["requested_max_tags"]
    summary["localization"]["candidate_overflow"] = False
    summary["localization"]["localization_limit_status"] = "exact_limit_no_overflow"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_localization_partial_without_overflow" in _error_codes(result)


def test_s3a_m2_contract_rejects_localization_overflow_marked_localized() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["localization"]["candidate_overflow"] = True
    summary["localization"]["status"] = "completed"
    summary["localization"]["dynamic_source_items_target_status"] = "localized"
    summary["localization"]["dynamic_source_items_deferred_reason"] = None

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_localization_overflow_claimed_complete" in codes
    assert "s3a_m2_localization_overflow_source_items_not_deferred" in codes
    assert "s3a_m2_localization_overflow_missing_deferred_reason" in codes


def test_s3a_m2_contract_rejects_incomplete_standard_pipeline_step() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["standard_pipeline_flow"]["status"] = "incomplete"
    summary["standard_pipeline_flow"]["steps"]["hydrate_placeholders_non_destructively"]["completed"] = False

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_standard_pipeline_flow_incomplete" in codes
    assert "s3a_m2_standard_pipeline_step_incomplete" in codes


def test_s3a_m2_contract_rejects_gui_execute_completion_without_click() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["status"] = "passed_gui_execute_completed"
    summary["launcher_web_admin_acceptance"]["execute_clicked"] = False

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_gui_execute_claim_without_click" in _error_codes(result)


def test_s3a_m2_contract_rejects_gui_execute_completion_without_newer_run() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["gui_execute_run_id"] = 8
    summary["launcher_web_admin_acceptance"]["production_execute_run_id_seen"] = 8
    summary["execute"]["run_id"] = 8

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_gui_execute_claim_without_newer_run" in codes


def test_s3a_m2_contract_rejects_gui_execute_completion_without_gui_provenance() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["gui_provenance_valid"] = False
    summary["launcher_web_admin_acceptance"]["request_source"] = "standalone_runner"
    summary["launcher_web_admin_acceptance"]["gui_validation_session_id_present"] = False
    summary["launcher_web_admin_acceptance"]["gui_validation_session_signature_valid"] = False

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_gui_execute_claim_without_gui_provenance" in _error_codes(result)


def test_s3a_m2_contract_rejects_gui_execute_completion_without_signed_session() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["gui_validation_session_signature_valid"] = False

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_gui_execute_claim_without_gui_provenance" in _error_codes(result)


def test_s3a_m2_contract_rejects_gui_execute_completion_without_bound_plan_flow() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["gui_plan_hash_bound"] = False
    summary["launcher_web_admin_acceptance"]["gui_plan_flow_verified"] = False
    summary["launcher_web_admin_acceptance"]["gui_plan_request_id_present"] = False

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_gui_execute_claim_without_bound_plan_flow" in _error_codes(result)


def test_s3a_m2_contract_rejects_gui_execute_completion_without_current_head_runtime() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["runtime_head_matches_current"] = False

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_gui_execute_claim_without_current_head_runtime" in _error_codes(result)


def test_s3a_m2_contract_rejects_missing_deferred_failed_inventory_postmortem() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary.pop("deferred_failed_inventory")

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_postmortem_section_missing" in _error_codes(result)


def test_s3a_m2_contract_rejects_missing_manual_sync_safety_judgement() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["manual_sync_safety_judgement"]["status"] = "not_assessed"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_manual_sync_safety_judgement_missing" in _error_codes(result)


def test_s3a_m2_contract_rejects_missing_scanner_incremental_model() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary.pop("scanner_incremental_model")

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_postmortem_section_missing" in _error_codes(result)


def test_s3a_m2_contract_rejects_missing_priority_backlog_root_cause() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary.pop("priority_backlog_root_cause")

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_postmortem_section_missing" in _error_codes(result)


def test_s3a_m2_contract_rejects_incomplete_priority_backlog_repair_plan() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["priority_backlog_root_cause"]["repair_migration_plan"].pop("candidate_condition")

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_priority_backlog_repair_plan_incomplete" in _error_codes(result)


def test_s3a_m2_contract_rejects_missing_local_copy_incremental_e2e() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary.pop("local_copy_repeated_incremental_e2e")

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_postmortem_section_missing" in _error_codes(result)


def test_s3a_m2_contract_rejects_retry_recommendation_from_bulk_only_evidence() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    local_copy = summary["local_copy_repeated_incremental_e2e"]
    local_copy["completed"] = False
    local_copy["bulk_run_alone_sufficient"] = True
    local_copy["scenario_count"] = 1
    local_copy["browser_normal_flow_passed"] = False
    local_copy["pass_criteria_failures"] = ["bulk_only_not_incremental"]

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_retry_recommended_without_local_copy_e2e" in codes
    assert "s3a_m2_retry_recommended_with_local_copy_failures" in codes
    assert "s3a_m2_retry_recommended_without_required_incremental_cycles" in codes
    assert "s3a_m2_retry_recommended_without_browser_normal_flow" in codes
    assert "s3a_m2_retry_recommended_from_bulk_only_evidence" in codes


def test_s3a_m2_contract_rejects_scanner_without_metadata_fast_skip() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["scanner_incremental_model"]["stable_known_files_fast_skipped_without_hash"] = False
    summary["scanner_incremental_model"]["cap_semantics"] = "raw files visited"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_scanner_does_not_fast_skip_stable_known_files" in codes
    assert "s3a_m2_scanner_cap_semantics_not_actionable" in codes


def test_s3a_m2_contract_rejects_safe_judgement_without_gui_execute() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["gui_execute_completed"] = False

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_manual_sync_safe_without_gui_execute" in _error_codes(result)


def test_s3a_m2_contract_rejects_stale_branch_profile_provenance() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["branch_profile_provenance"]["head_sha"] = "old-head"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_branch_profile_head_mismatch" in _error_codes(result)


def test_s3a_m2_contract_rejects_runner_fallback_without_reason() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["status"] = "passed_gui_execute_not_safe_runner_execute_used"
    summary["launcher_web_admin_acceptance"]["execute_clicked"] = False
    summary["launcher_web_admin_acceptance"].pop("fallback_reason", None)

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_runner_fallback_missing_reason" in _error_codes(result)


def test_s3a_m2_contract_rejects_test_db_production_acceptance() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["readiness"]["production_settings"]["db_name"] = "blombooru_test"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_production_acceptance_on_test_db" in _error_codes(result)


def test_s3a_m2_contract_rejects_initial_run_validation_failure() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["initial_run_validation"]["passed"] = False
    summary["initial_run_validation"]["blockers"] = ["initial_ledger_consistency_not_passed"]

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_initial_run_validation_not_passed" in _error_codes(result)


def test_s3a_m2_contract_rejects_stale_launcher_validation_artifact() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["launcher_web_admin_acceptance"]["production_execute_run_id_seen"] = 7
    summary["launcher_web_admin_acceptance"]["validated_head_sha"] = "old-head"
    summary["launcher_web_admin_acceptance"]["public_source_identity"] = "source-old"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_launcher_validation_run_id_mismatch" in codes
    assert "s3a_m2_launcher_validation_head_sha_mismatch" in codes
    assert "s3a_m2_launcher_validation_source_mismatch" in codes


def test_s3a_m2_contract_rejects_all_ai_suggestions_with_high_conf_nonproper_tags() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["ai_tag_assignment_incident"]["status"] = "blocked"
    summary["ai_tag_assignment_incident"]["after"]["all_ai_assignments_are_suggestions"] = True
    summary["ai_tag_assignment_incident"]["after"]["high_conf_nonproper_expected_normal_count"] = 12
    summary["ai_tag_assignment_incident"]["after"]["high_conf_nonproper_incorrect_suggestion_count"] = 12
    summary["ai_tag_assignment_incident"]["after"]["high_conf_nonproper_normal_count"] = 0

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_ai_tag_assignment_incident_not_resolved" in codes
    assert "s3a_m2_all_ai_tags_suggestions_with_mature_policy_expected" in codes
    assert "s3a_m2_high_conf_nonproper_ai_tags_still_suggestions" in codes
    assert "s3a_m2_high_conf_nonproper_ai_tags_not_normalized" in codes


def test_s3a_m2_contract_rejects_high_conf_proper_ai_tags_left_as_suggestions() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["ai_tag_assignment_incident"]["after"]["high_conf_proper_expected_normal_count"] = 7
    summary["ai_tag_assignment_incident"]["after"]["high_conf_proper_incorrect_suggestion_count"] = 7
    summary["ai_tag_assignment_incident"]["after"]["high_conf_proper_normal_count"] = 0

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    codes = _error_codes(result)
    assert "s3a_m2_high_conf_proper_ai_tags_still_suggestions" in codes
    assert "s3a_m2_high_conf_proper_ai_tags_not_normalized" in codes


def test_s3a_m2_contract_rejects_abnormal_cohort_distribution() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["cohort_self_audit"]["status"] = "blocked_anomalies_remaining"
    summary["cohort_self_audit"]["blocker_anomaly_count"] = 1
    summary["cohort_self_audit"]["normal_ai_tag_semantics_consistent_with_policy"] = False

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_m2_cohort_self_audit_not_passed" in codes
    assert "s3a_m2_cohort_blocker_anomalies_remaining" in codes
    assert "s3a_m2_cohort_ai_tag_semantics_abnormal" in codes


def test_s3a_m2_contract_rejects_failed_post_repair_ui_validation() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["ai_tag_assignment_incident"]["ui_verification"]["status"] = "failed"
    summary["ai_tag_assignment_incident"]["ui_verification"]["normal_visible_pass_count"] = 7
    summary["post_repair_ui_validation"]["status"] = "failed"
    summary["post_repair_ui_validation"]["normal_visible_pass_count"] = 7

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_post_repair_ui_validation_not_passed" in _error_codes(result)


def test_s3a_m2_contract_rejects_telemetry_outside_local_manifest_tree() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["private_artifacts"]["telemetry_root"] = "telemetry/s3a_m2"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_telemetry_artifact_outside_approved_tree" in _error_codes(result)


def test_s3a_m2_contract_rejects_public_content_hash_leak() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["public_example"] = {"content_hash": "a" * 64}

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_public_payload_not_safe" in _error_codes(result)


def test_s3a_m2_contract_rejects_public_redaction_leak() -> None:
    summary = copy.deepcopy(_s3a_m2_summary())
    summary["source"]["public_source_identity"] = r"C:\Users\private\Pictures\example.png"

    result = check_phase_contract("s3a_m2_production_delta_e2e_contract_v1", summary)

    assert "s3a_m2_public_payload_not_safe" in _error_codes(result)


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


def test_r1r_contract_accepts_target_met_full_chain_with_stage_manifest() -> None:
    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", _r1r_summary())

    assert result.passed is True


def test_r1r_contract_accepts_truthful_no_llm_approval_block() -> None:
    summary = _r1r_summary()
    summary["pipeline_contract"] = {
        "contract_id": "r1r_full_source_concept_pipeline_contract_v1",
        "status": "blocked_llm_approval_required",
        "claims": {"target_met": False, "full_chain_complete": False, "safe_to_merge": False},
    }
    summary["sc1_full_chain_proof"]["complete_sc1_pipeline_executed"] = False
    summary["sc1_full_chain_proof"]["llm_pair_adjudication_executed"] = False
    summary["sc1_full_chain_proof"]["llm_judgment_count"] = 0
    summary["llm_readiness"]["operator_approved"] = False

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert result.passed is True


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("environment_isolation.production_profile_active", "r1r_environment_isolation_failed"),
        ("environment_isolation.violet_env_is_production", "r1r_environment_isolation_failed"),
        ("environment_isolation.db_target_is_production", "r1r_environment_isolation_failed"),
        ("environment_isolation.production_write_attempted", "r1r_environment_isolation_failed"),
    ],
)
def test_r1r_contract_rejects_production_isolation_failures(path: str, code: str) -> None:
    summary = _r1r_summary()
    _set_nested(summary, path, True)

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert code in _error_codes(result)


def test_r1r_contract_rejects_production_db_name() -> None:
    summary = _r1r_summary()
    summary["environment_isolation"]["db_name"] = "blombooru"

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_production_db_name_rejected" in _error_codes(result)


def test_r1r_contract_rejects_actual_connection_production_db_even_when_env_label_is_safe() -> None:
    summary = _r1r_summary()
    summary["environment_isolation"]["db_name"] = "blombooru_test"
    summary["environment_isolation"]["exact_db_identity_from_actual_connection"] = {
        "checked_from_actual_connection": True,
        "db_name": "blombooru",
        "db_target_is_production": True,
        "dev_test_restored_snapshot_db_used": False,
        "passed": False,
        "blockers": ["actual_connection_production_like_db"],
    }

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_actual_connection_production_db_rejected" in codes
    assert "r1r_actual_connection_dev_test_snapshot_required" in codes


def test_r1r_contract_rejects_protected_storage_pre_settings_gate_failure() -> None:
    summary = _r1r_summary()
    summary["environment_isolation"]["storage_root_pre_settings_import"] = {
        "checked_before_settings_import": True,
        "passed": False,
        "blocked_count": 1,
        "blockers": [{"reason": "storage_root_overlaps_protected_root"}],
    }

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_storage_pre_settings_gate_failed" in _error_codes(result)


@pytest.mark.parametrize(
    ("path", "expected_code"),
    [
        ("environment_isolation.passed", "r1r_environment_isolation_aggregate_failed_for_target"),
        ("environment_isolation.storage_root_pre_settings_import.passed", "r1r_storage_pre_settings_gate_required_for_target"),
        ("environment_isolation.output_dir_safety.passed", "r1r_output_dir_safety_gate_required_for_target"),
        ("environment_isolation.exact_db_identity_from_actual_connection.passed", "r1r_actual_db_identity_gate_failed_for_target"),
    ],
)
def test_r1r_contract_rejects_target_when_environment_aggregate_or_subgate_fails(
    path: str,
    expected_code: str,
) -> None:
    summary = _r1r_summary()
    _set_nested(summary, path, False)
    if path == "environment_isolation.passed":
        summary["environment_isolation"]["blockers"] = ["fixture_blocker"]

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert expected_code in codes
    if path == "environment_isolation.passed":
        assert "r1r_environment_isolation_blockers_present_for_target" in codes


def test_r1r_contract_rejects_target_met_with_insufficient_input_scope() -> None:
    summary = _r1r_summary()
    summary["input_scope_fidelity"] = {
        "required_for_route_evidence": True,
        "passed": False,
        "status": "insufficient_input_scope",
        "route_evidence_allowed": False,
        "failed_metrics": ["resolver_input_signals", "deterministic_edge_count"],
        "comparison_table": [
            {
                "metric": "resolver_input_signals",
                "old_r1_expected": 12249,
                "current_r1r_actual": 99,
                "ratio": 0.0081,
                "status": "insufficient",
            }
        ],
    }

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_target_met_with_insufficient_input_scope" in codes
    assert "r1r_input_scope_failure_not_blocked" in codes


def test_r1r_contract_recomputes_input_scope_from_rows_not_booleans() -> None:
    summary = _r1r_summary()
    summary["input_scope_fidelity"]["passed"] = True
    summary["input_scope_fidelity"]["route_evidence_allowed"] = True
    summary["input_scope_fidelity"]["failed_metrics"] = []
    summary["input_scope_fidelity"]["comparison_table"] = _r1r_input_scope_rows(
        {"resolver_input_signals": 99, "deterministic_edge_count": 170}
    )

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_input_scope_claim_not_supported_by_rows" in codes
    assert "r1r_target_met_with_insufficient_input_scope" in codes


def test_r1r_contract_rejects_stale_sourceconcept_rows_without_current_replay_output() -> None:
    summary = _r1r_summary()
    summary["input_scope_fidelity"]["comparison_table"] = _r1r_input_scope_rows(
        {
            "source_concept_replay_total": 58,
            "source_concept_replay_active": 20,
            "source_concept_replay_needs_review": 10,
            "source_concept_total": 6094,
            "source_concept_active": 1078,
            "source_concept_needs_review": 1809,
            "source_concept_superseded": 3207,
        }
    )

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_input_scope_claim_not_supported_by_rows" in codes
    assert "r1r_target_met_with_insufficient_input_scope" in codes


def test_r1r_contract_accepts_smoke_only_insufficient_input_scope_without_completion_claims() -> None:
    summary = _r1r_summary()
    summary["pipeline_contract"] = {
        "contract_id": "r1r_full_source_concept_pipeline_contract_v1",
        "status": "smoke_only_not_route_evidence",
        "claims": {"target_met": False, "full_chain_complete": False, "safe_to_merge": False},
    }
    summary["sc1_full_chain_proof"]["complete_sc1_pipeline_executed"] = False
    summary["sc1_full_chain_proof"]["all_required_stage_statuses_verified"] = False
    summary["input_scope_fidelity"]["passed"] = False
    summary["input_scope_fidelity"]["status"] = "insufficient_input_scope"
    summary["input_scope_fidelity"]["route_evidence_allowed"] = False
    summary["input_scope_fidelity"]["failed_metrics"] = ["resolver_input_signals"]
    summary["input_scope_fidelity"]["comparison_table"][0]["current_r1r_actual"] = 99
    summary["input_scope_fidelity"]["comparison_table"][0]["ratio"] = 0.0081
    summary["input_scope_fidelity"]["comparison_table"][0]["status"] = "insufficient"

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert result.passed is True


def test_r1r_contract_accepts_ready_for_old_r1_scope_rerun_without_llm_claims() -> None:
    summary = _r1r_summary()
    summary["pipeline_contract"] = {
        "contract_id": "r1r_full_source_concept_pipeline_contract_v1",
        "status": "ready_for_old_r1_scope_rerun",
        "claims": {"target_met": False, "full_chain_complete": False, "safe_to_merge": False},
    }
    summary["sc1_full_chain_proof"]["complete_sc1_pipeline_executed"] = False
    summary["sc1_full_chain_proof"]["llm_pair_adjudication_executed"] = False
    summary["sc1_full_chain_proof"]["llm_judgment_count"] = 0
    summary["sc1_full_chain_proof"]["all_required_stage_statuses_verified"] = False
    summary["llm_readiness"]["operator_approved"] = False
    summary["llm_readiness"]["provider_available"] = False
    summary["llm_readiness"]["budget_ready"] = False
    summary["snapshot_input_scope_recovery"]["status"] = "ready_for_old_r1_scope_rerun"

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert result.passed is True


def test_r1r_contract_rejects_missing_snapshot_input_scope_recovery() -> None:
    summary = _r1r_summary()
    summary.pop("snapshot_input_scope_recovery")

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "missing_required_summary_field" in _error_codes(result)


def test_r1r_contract_rejects_target_met_with_llm_disabled_or_zero_judgments() -> None:
    disabled = _r1r_summary()
    disabled["sc1_full_chain_proof"]["llm_pair_adjudication_executed"] = False
    disabled["llm_readiness"]["operator_approved"] = False
    zero = _r1r_summary()
    zero["sc1_full_chain_proof"]["llm_judgment_count"] = 0

    disabled_result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", disabled)
    zero_result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", zero)

    assert "r1r_llm_used_false_with_target_met_full_chain" in _error_codes(disabled_result)
    assert "r1r_llm_judgment_count_zero_for_eligible_pairs" in _error_codes(zero_result)


def test_r1r_contract_rejects_partial_selected_llm_pair_accounting() -> None:
    summary = _r1r_summary()
    summary["sc1_full_chain_proof"]["llm_selected_pair_count"] = 33
    summary["sc1_full_chain_proof"]["llm_judgment_count"] = 1
    summary["llm_adjudication_plan"]["eligible_pair_count"] = 33
    summary["llm_adjudication_plan"]["selected_pair_count"] = 33
    summary["llm_adjudication_plan"]["eligible_pair_accounting_total"] = 33
    summary["llm_judgment_summary"]["selected_pair_count"] = 33
    summary["llm_judgment_summary"]["judgment_count"] = 1
    summary["llm_judgment_summary"]["selected_pair_accounting"] = {
        "selected_pair_count": 33,
        "resolved_provider_judgment_count": 1,
        "valid_cached_judgment_count": 0,
        "explicit_skipped_pair_count": 0,
        "provider_error_pair_count": 0,
        "successful_accounted_pair_count": 1,
        "all_selected_pairs_successfully_accounted": False,
    }

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_selected_llm_pairs_not_fully_accounted" in _error_codes(result)


def test_r1r_contract_rejects_disconnected_llm_proof_and_ledger_counts() -> None:
    summary = _r1r_summary()
    summary["sc1_full_chain_proof"]["llm_judgment_count"] = 12
    summary["llm_judgment_summary"]["judgment_count"] = 0
    summary["llm_judgment_summary"]["ledger_row_count"] = 0
    summary["llm_judgment_summary"]["selected_pair_accounting"] = {
        "selected_pair_count": 12,
        "resolved_provider_judgment_count": 0,
        "valid_cached_judgment_count": 0,
        "explicit_skipped_pair_count": 0,
        "provider_error_pair_count": 0,
        "successful_accounted_pair_count": 0,
        "all_selected_pairs_successfully_accounted": False,
    }

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_llm_judgment_count_mismatch" in codes
    assert "r1r_selected_llm_pairs_not_fully_accounted" in codes


def test_r1r_contract_rejects_provider_error_rows_as_successful_judgments() -> None:
    summary = _r1r_summary()
    summary["llm_judgment_summary"]["error_count"] = 1
    summary["llm_judgment_summary"]["selected_pair_accounting"]["provider_error_pair_count"] = 1

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_llm_error_count_nonzero_for_target" in codes
    assert "r1r_provider_error_pairs_not_successful_judgments" in codes


def test_r1r_contract_rejects_eligible_pair_accounting_gap() -> None:
    summary = _r1r_summary()
    summary["llm_adjudication_plan"]["eligible_pair_count"] = 35
    summary["llm_adjudication_plan"]["selected_pair_count"] = 33
    summary["llm_adjudication_plan"]["skipped_pair_count"] = 0
    summary["llm_adjudication_plan"]["unselected_pair_count"] = 0
    summary["llm_adjudication_plan"]["eligible_pair_accounting_total"] = 33
    summary["sc1_full_chain_proof"]["llm_eligible_pair_count"] = 35
    summary["sc1_full_chain_proof"]["llm_selected_pair_count"] = 33
    summary["sc1_full_chain_proof"]["llm_judgment_count"] = 33
    summary["llm_judgment_summary"]["selected_pair_count"] = 33
    summary["llm_judgment_summary"]["judgment_count"] = 33
    summary["llm_judgment_summary"]["selected_pair_accounting"] = {
        "selected_pair_count": 33,
        "resolved_provider_judgment_count": 33,
        "valid_cached_judgment_count": 0,
        "explicit_skipped_pair_count": 0,
        "provider_error_pair_count": 0,
        "successful_accounted_pair_count": 33,
        "all_selected_pairs_successfully_accounted": True,
    }

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_eligible_pair_accounting_mismatch" in _error_codes(result)


def test_r1r_contract_rejects_fixed_cap_subset_as_target_met() -> None:
    summary = _r1r_summary()
    summary["llm_adjudication_plan"].update(
        {
            "selection_policy": "ranked",
            "eligible_pair_count": 6429,
            "selected_pair_count": 300,
            "all_eligible_pair_count": 6429,
            "all_eligible_pairs_selected": False,
            "all_eligible_pairs_adjudicated": False,
            "fixed_call_cap_primary_limiter": True,
            "max_calls": 300,
            "emergency_call_ceiling": 300,
            "skipped_pair_count": 6129,
            "unselected_pair_count": 0,
            "eligible_pair_accounting_total": 6429,
            "projected_full_eligible_cost_usd": 2.1,
            "budget_cap_usd": 15.0,
        }
    )
    summary["sc1_full_chain_proof"].update(
        {
            "llm_eligible_pair_count": 6429,
            "llm_selected_pair_count": 300,
            "llm_judgment_count": 300,
            "all_eligible_llm_pairs_adjudicated": False,
        }
    )
    summary["llm_judgment_summary"].update(
        {
            "judgment_count": 300,
            "ledger_row_count": 300,
            "selected_pair_count": 300,
        }
    )
    summary["llm_judgment_summary"]["selected_pair_accounting"] = {
        "selected_pair_count": 300,
        "resolved_provider_judgment_count": 300,
        "valid_cached_judgment_count": 0,
        "explicit_skipped_pair_count": 0,
        "provider_error_pair_count": 0,
        "successful_accounted_pair_count": 300,
        "all_selected_pairs_successfully_accounted": True,
    }

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_target_requires_all_eligible_llm_pairs_selected" in codes
    assert "r1r_target_requires_all_eligible_llm_pairs_judged" in codes
    assert "r1r_all_eligible_llm_pairs_adjudicated_missing" in codes
    assert "r1r_target_requires_budget_driven_llm_selection_policy" in codes
    assert "r1r_fixed_call_cap_cannot_be_primary_target_limiter" in codes


def test_r1r_contract_rejects_missing_llm_cache_policy_for_target() -> None:
    summary = _r1r_summary()
    summary.pop("llm_cache_policy")

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_llm_cache_policy_missing" in _error_codes(result)


def test_r1r_contract_rejects_raw_cache_path_in_public_policy() -> None:
    summary = _r1r_summary()
    summary["llm_cache_policy"]["durable_cache_root_label"] = r"C:\Users\kyloris\private-cache"

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_llm_cache_root_label_leaks_path" in _error_codes(result)


def test_r1r_contract_accepts_blocked_budget_without_target_claim() -> None:
    summary = _r1r_summary()
    summary["pipeline_contract"] = {
        "contract_id": "r1r_full_source_concept_pipeline_contract_v1",
        "status": "blocked_budget",
        "claims": {"target_met": False, "full_chain_complete": False, "safe_to_merge": False},
    }
    summary["sc1_full_chain_proof"].update(
        {
            "complete_sc1_pipeline_executed": False,
            "llm_pair_adjudication_executed": False,
            "llm_eligible_pair_count": 6429,
            "llm_selected_pair_count": 0,
            "llm_judgment_count": 0,
            "all_required_stage_statuses_verified": False,
            "all_eligible_llm_pairs_adjudicated": False,
        }
    )
    summary["llm_adjudication_plan"].update(
        {
            "selection_policy": "budget_driven_all_eligible",
            "eligible_pair_count": 6429,
            "selected_pair_count": 0,
            "all_eligible_pair_count": 6429,
            "all_eligible_pairs_selected": False,
            "all_eligible_pairs_adjudicated": False,
            "skipped_pair_count": 6429,
            "unselected_pair_count": 0,
            "eligible_pair_accounting_total": 6429,
            "projected_full_eligible_cost_usd": 20.0,
            "budget_cap_usd": 15.0,
        }
    )
    summary["llm_readiness"]["budget_ready"] = False
    summary["llm_readiness"]["passed"] = False
    summary["llm_judgment_summary"].update(
        {
            "judgment_count": 0,
            "ledger_row_count": 0,
            "selected_pair_count": 0,
        }
    )
    summary["llm_judgment_summary"]["selected_pair_accounting"] = {
        "selected_pair_count": 0,
        "resolved_provider_judgment_count": 0,
        "valid_cached_judgment_count": 0,
        "explicit_skipped_pair_count": 0,
        "provider_error_pair_count": 0,
        "successful_accounted_pair_count": 0,
        "all_selected_pairs_successfully_accounted": True,
    }
    summary["llm_cache_policy"].update(
        {
            "compatible_cache_hit_count": 0,
            "exact_compatible_cache_hit_count": 0,
            "new_provider_call_count": 0,
            "new_provider_success_count": 0,
            "failed_provider_call_count": 0,
            "remaining_missing_pair_count": 0,
            "cost_spent_this_run_usd": 0.0,
            "cost_avoided_by_cache_reuse_usd": 0.0,
            "projected_new_call_cost_usd": 0.0,
            "projected_full_eligible_cost_usd": 0.0,
        }
    )

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert result.passed is True


def test_r1r_contract_rejects_target_met_with_provider_errors_or_fallback() -> None:
    provider_error = _r1r_summary()
    provider_error["llm_judgment_summary"]["error_count"] = 1
    fallback = _r1r_summary()
    fallback["llm_provider_execution"]["uses_fallback_provider"] = True
    fallback["llm_provider_execution"]["fallback_provider_used"] = True
    missing_model = _r1r_summary()
    missing_model["llm_provider_execution"]["model_name"] = ""

    provider_error_result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", provider_error)
    fallback_result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", fallback)
    missing_model_result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", missing_model)

    assert "r1r_llm_error_count_nonzero_for_target" in _error_codes(provider_error_result)
    assert "r1r_fallback_provider_used" in _error_codes(fallback_result)
    assert "r1r_llm_model_identity_missing" in _error_codes(missing_model_result)


def test_r1r_contract_rejects_missing_stage_manifest_or_executed_stage_without_label() -> None:
    missing = _r1r_summary(sc1_required_stage_manifest=_r1r_stage_manifest(omit={"bounded_llm_judgment_execution"}))
    unlabeled = _r1r_summary(
        sc1_required_stage_manifest=_r1r_stage_manifest(executed_without_label="bounded_llm_judgment_execution")
    )

    missing_result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", missing)
    unlabeled_result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", unlabeled)

    assert "r1r_required_stage_manifest_row_missing" in _error_codes(missing_result)
    assert "r1r_stage_executed_without_evidence_label" in _error_codes(unlabeled_result)


def test_r1r_contract_rejects_required_stage_manifest_row_opt_out() -> None:
    manifest = _r1r_stage_manifest()
    for row in manifest:
        if row["stage_name"] == "bounded_llm_judgment_execution":
            row["required"] = False
            row["status"] = "blocked"
            row["executed"] = False
            row["evidence_artifact_label"] = ""

    result = check_phase_contract(
        "r1r_full_source_concept_pipeline_contract_v1",
        _r1r_summary(sc1_required_stage_manifest=manifest),
    )
    codes = _error_codes(result)

    assert "r1r_required_stage_row_cannot_opt_out" in codes
    assert "r1r_required_stage_not_verified_for_target" in codes


def test_r1r_contract_rejects_provider_cache_skip_without_proof() -> None:
    manifest = _r1r_stage_manifest()
    for row in manifest:
        if row["stage_name"] == "provider_cache_adapter_or_zero_eligible_proof":
            row["zero_eligible_proof"] = False
            row["not_in_input_scope_proof"] = False
            row["input_count"] = 5
            row["skip_reason"] = "not checked"
    result = check_phase_contract(
        "r1r_full_source_concept_pipeline_contract_v1",
        _r1r_summary(sc1_required_stage_manifest=manifest),
    )

    assert "r1r_provider_cache_adapter_skip_without_zero_scope_proof" in _error_codes(result)


@pytest.mark.parametrize(
    "write_key",
    [
        "entity_truth",
        "media_tags",
        "source_metadata",
        "source_icloud_app_storage",
    ],
)
def test_r1r_contract_rejects_forbidden_write_claims(write_key: str) -> None:
    summary = _r1r_summary()
    summary["forbidden_writes"][write_key] = True

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_forbidden_write_claimed" in _error_codes(result)


def test_r1r_contract_rejects_mutation_outside_source_concept_tables() -> None:
    summary = _r1r_summary()
    summary["source_concept_write_scope"]["changed_tables"] = ["blombooru_media_tags"]
    summary["mutation_proof"]["forbidden_changed_tables"] = ["blombooru_media_tags"]

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_forbidden_table_changed" in codes
    assert "r1r_source_concept_write_outside_allowlist" in codes


def test_r1r_contract_rejects_same_row_count_forbidden_content_change() -> None:
    summary = _r1r_summary()
    summary["mutation_proof"]["changed_tables"] = [
        {
            "table": "blombooru_media_tags",
            "before_count": 10,
            "after_count": 10,
            "delta": 0,
            "changed": True,
            "content_signature_changed": True,
            "allowed": False,
            "prompt_forbidden": True,
        }
    ]
    summary["mutation_proof"]["forbidden_changed_tables"] = []

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_forbidden_table_changed" in _error_codes(result)


def test_r1r_contract_rejects_target_without_old_r1_output_isolation() -> None:
    summary = _r1r_summary()
    summary["old_r1_contamination_handling"]["old_r1_isolated_before_r1r_persistence"] = False
    summary["old_r1_contamination_handling"]["source_concept_owned_tables_cleared_or_rebuilt_in_dev_test"] = False

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_old_r1_output_not_isolated" in codes
    assert "r1r_old_r1_sourceconcept_rebuild_missing" in codes


def test_r1r_contract_uses_fixed_write_allowlist_not_summary_supplied_allowlist() -> None:
    summary = _r1r_summary()
    summary["source_concept_write_scope"]["allowed_tables"] = ["blombooru_media_tags"]
    summary["source_concept_write_scope"]["changed_tables"] = ["blombooru_media_tags"]
    summary["mutation_proof"]["changed_tables"] = [
        {"table": "blombooru_media_tags", "changed": True, "allowed": True, "prompt_forbidden": False}
    ]

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    codes = _error_codes(result)
    assert "r1r_source_concept_write_outside_allowlist" in codes
    assert "r1r_mutation_changed_table_outside_fixed_allowlist" in codes


def test_r1r_contract_requires_source_concept_write_scope_for_persistence_claim() -> None:
    summary = _r1r_summary()
    summary.pop("source_concept_write_scope")
    summary["mutation_proof"]["changed_tables"] = [
        {"table": "blombooru_source_concepts", "changed": True, "allowed": True}
    ]

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_source_concept_write_scope_missing" in _error_codes(result)


def test_r1r_contract_rejects_llm_readiness_status_mismatches() -> None:
    no_approval = _r1r_summary()
    no_approval["pipeline_contract"]["status"] = "dry_run_complete_execute_not_requested"
    no_approval["pipeline_contract"]["claims"] = {"target_met": False, "full_chain_complete": False}
    no_approval["sc1_full_chain_proof"]["complete_sc1_pipeline_executed"] = False
    no_approval["llm_readiness"]["operator_approved"] = False
    provider = _r1r_summary()
    provider["pipeline_contract"]["status"] = "dry_run_complete_execute_not_requested"
    provider["pipeline_contract"]["claims"] = {"target_met": False, "full_chain_complete": False}
    provider["sc1_full_chain_proof"]["complete_sc1_pipeline_executed"] = False
    provider["llm_readiness"]["provider_available"] = False
    budget = _r1r_summary()
    budget["pipeline_contract"]["status"] = "dry_run_complete_execute_not_requested"
    budget["pipeline_contract"]["claims"] = {"target_met": False, "full_chain_complete": False}
    budget["sc1_full_chain_proof"]["complete_sc1_pipeline_executed"] = False
    budget["llm_readiness"]["budget_ready"] = False

    assert "r1r_llm_approval_required_status_missing" in _error_codes(
        check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", no_approval)
    )
    assert "r1r_provider_unavailable_not_blocked" in _error_codes(
        check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", provider)
    )
    assert "r1r_budget_unready_not_blocked" in _error_codes(
        check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", budget)
    )


def test_r1r_contract_accepts_cache_only_target_when_provider_unavailable() -> None:
    summary = _r1r_summary()
    summary["llm_readiness"].update(
        {
            "passed": True,
            "provider_available": False,
            "provider_required_for_missing_pairs": False,
            "provider_not_required_for_fully_cached_pairs": True,
        }
    )
    summary["llm_provider_execution"].update(
        {
            "provider_name": "cache_only",
            "primary_openai_compatible_used": False,
            "primary_openai_compatible_policy_used_for_cache_compatibility": True,
            "provider_not_required_for_fully_cached_pairs": True,
        }
    )
    summary["llm_adjudication_plan"].update(
        {
            "already_cached_compatible_judgments": 12,
            "new_provider_call_count": 0,
            "failed_provider_call_count": 0,
            "remaining_missing_pair_count": 0,
            "provider_required_for_missing_pairs": False,
            "provider_not_required_for_fully_cached_pairs": True,
        }
    )
    summary["llm_judgment_summary"].update(
        {
            "cache_hits": 12,
            "cache_misses": 0,
            "compatible_cache_hit_count": 12,
            "exact_compatible_cache_hit_count": 12,
            "new_provider_call_count": 0,
            "new_provider_success_count": 0,
            "estimated_cost_usd": 0.0,
            "selected_pair_accounting": {
                "selected_pair_count": 12,
                "resolved_provider_judgment_count": 0,
                "valid_cached_judgment_count": 12,
                "explicit_skipped_pair_count": 0,
                "provider_error_pair_count": 0,
                "successful_accounted_pair_count": 12,
                "all_selected_pairs_successfully_accounted": True,
            },
        }
    )
    summary["llm_cache_policy"].update(
        {
            "compatible_cache_hit_count": 12,
            "exact_compatible_cache_hit_count": 12,
            "new_provider_call_count": 0,
            "new_provider_success_count": 0,
            "remaining_missing_pair_count": 0,
            "cost_spent_this_run_usd": 0.0,
            "cost_avoided_by_cache_reuse_usd": 0.2,
            "projected_new_call_cost_usd": 0.0,
            "provider_required_for_missing_pairs": False,
            "provider_not_required_for_fully_cached_pairs": True,
        }
    )

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert result.passed, _error_codes(result)


def test_r1r_contract_rejects_provider_unavailable_when_cache_misses_remain() -> None:
    summary = _r1r_summary()
    summary["llm_readiness"]["provider_available"] = False
    summary["llm_cache_policy"]["remaining_missing_pair_count"] = 1

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)
    codes = _error_codes(result)

    assert "r1r_provider_unavailable_not_blocked" in codes
    assert "r1r_llm_readiness_missing_for_target" in codes


def test_r1r_contract_rejects_review_pack_without_manifest_and_redaction_failure() -> None:
    review = _r1r_summary()
    review["review_pack"]["includes_stage_manifest"] = False
    redaction = _r1r_summary()
    redaction["public_redaction"] = {"passed": False, "finding_count": 1}

    assert "r1r_review_pack_omits_stage_manifest" in _error_codes(
        check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", review)
    )
    assert "r1r_public_redaction_missing_or_failed" in _error_codes(
        check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", redaction)
    )


def test_r1r_contract_rejects_redaction_without_final_artifact_scans() -> None:
    summary = _r1r_summary()
    summary["public_redaction"] = {"passed": True, "finding_count": 0}

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_public_redaction_final_artifact_scan_missing" in _error_codes(result)


def test_r1r_contract_rejects_missing_llm_judgment_summary() -> None:
    summary = _r1r_summary()
    summary.pop("llm_judgment_summary")

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "missing_required_summary_field" in _error_codes(result)


def test_r1r_contract_accepts_zero_eligible_full_chain_with_explicit_proof() -> None:
    summary = _r1r_summary()
    summary["sc1_full_chain_proof"]["llm_eligible_pair_count"] = 0
    summary["sc1_full_chain_proof"]["llm_selected_pair_count"] = 0
    summary["sc1_full_chain_proof"]["llm_judgment_count"] = 0
    summary["sc1_full_chain_proof"]["llm_pair_adjudication_executed"] = False
    summary["sc1_full_chain_proof"]["zero_eligible_pair_proof"] = True
    summary["llm_adjudication_plan"]["eligible_pair_count"] = 0
    summary["llm_adjudication_plan"]["selected_pair_count"] = 0
    summary["llm_adjudication_plan"]["eligible_pair_accounting_total"] = 0
    summary["llm_readiness"] = {
        "passed": False,
        "operator_approved": False,
        "provider_available": False,
        "provider_mode": None,
        "provider_model": None,
        "uses_fallback_provider": False,
        "cache_ready": False,
        "budget_ready": False,
    }
    summary["llm_provider_execution"] = {
        "provider_mode": None,
        "provider_label": None,
        "provider_name": None,
        "model_name": None,
        "uses_fallback_provider": False,
        "fallback_provider_used": False,
        "primary_openai_compatible_used": False,
    }
    summary["llm_judgment_summary"]["judgment_count"] = 0
    summary["llm_judgment_summary"]["ledger_row_count"] = 0
    summary["llm_judgment_summary"]["selected_pair_count"] = 0
    summary["llm_judgment_summary"]["selected_pair_accounting"] = {
        "selected_pair_count": 0,
        "resolved_provider_judgment_count": 0,
        "valid_cached_judgment_count": 0,
        "explicit_skipped_pair_count": 0,
        "provider_error_pair_count": 0,
        "successful_accounted_pair_count": 0,
        "all_selected_pairs_successfully_accounted": True,
    }
    summary["llm_cache_policy"].update(
        {
            "compatible_cache_hit_count": 0,
            "exact_compatible_cache_hit_count": 0,
            "new_provider_call_count": 0,
            "new_provider_success_count": 0,
            "failed_provider_call_count": 0,
            "remaining_missing_pair_count": 0,
            "cost_spent_this_run_usd": 0.0,
            "cost_avoided_by_cache_reuse_usd": 0.0,
            "projected_new_call_cost_usd": 0.0,
            "projected_full_eligible_cost_usd": 0.0,
        }
    )

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert result.passed is True


@pytest.mark.parametrize(
    "route_key",
    [
        "r2_authorized",
        "px1_b_authorized",
        "provider_2_authorized",
        "scale_up_authorized",
        "entity_bridge_authorized",
        "source_concept_truth_promotion_authorized",
        "route_approval_authorized",
    ],
)
def test_r1r_contract_rejects_downstream_route_authorization(route_key: str) -> None:
    summary = _r1r_summary()
    summary["route_authorization"][route_key] = True

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_forbidden_route_authorization" in _error_codes(result)


@pytest.mark.parametrize(
    "claim_path",
    [
        "pipeline_contract.claims.route_approved",
        "pipeline_contract.claims.safe_to_merge",
        "pipeline_contract.claims.r2_authorized",
        "pipeline_contract.claims.px1_b_authorized",
        "pipeline_contract.claims.provider_2_authorized",
        "pipeline_contract.claims.scale_up_authorized",
        "pipeline_contract.claims.entity_bridge_authorized",
        "claims.route_approved",
        "claims.safe_to_merge",
        "route_approved",
        "safe_to_merge",
    ],
)
def test_r1r_contract_rejects_route_approval_claims_outside_route_authorization(claim_path: str) -> None:
    summary = _r1r_summary()
    if claim_path.startswith("claims."):
        summary.setdefault("claims", {})[claim_path.split(".", 1)[1]] = True
    elif "." in claim_path:
        _set_nested(summary, claim_path, True)
    else:
        summary[claim_path] = True

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_forbidden_route_claim" in _error_codes(result)


def test_r1r_contract_requires_a1r_after_r1r() -> None:
    summary = _r1r_summary()
    summary["route_authorization"]["a1r_still_required"] = False

    result = check_phase_contract("r1r_full_source_concept_pipeline_contract_v1", summary)

    assert "r1r_a1r_still_required_missing" in _error_codes(result)


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


def _a1r_route_audit_summary(**overrides: object) -> dict:
    options = [
        {
            "candidate": "SCV2-R2 targeted resolver / gap reduction",
            "recommended": True,
            "allowed_by_A1R": True,
        },
        {
            "candidate": "Entity bridge preview",
            "recommended": False,
            "allowed_by_A1R": False,
        },
    ]
    summary = _route_audit_summary(
        phase="4.5-SCV2-A1R",
        phase_slug="phase-4.5-scv2-a1r-route-audit-after-r1r",
        final_route_decision_status="route_partially_approved_for_one_next_phase",
        recommended_next_phase="SCV2-R2 targeted resolver / gap reduction",
        required_contract_for_next_phase="focused SCV2-R2 resolver/gap contract",
        r1r_evidence_intake={"passed": True},
        upstream_pipeline_contract=_route_full_chain_upstream(),
        public_redaction={"passed": True},
        chatgpt_review_pack=_complete_review_pack_proof(integrity_passed=True),
        route_decision_matrix={"options": options},
        route_authorization={
            "recommended_next_phase": "SCV2-R2 targeted resolver / gap reduction",
            "still_blocked_routes": ["Entity bridge preview"],
            "r2_started": False,
            "px1_b_started": False,
            "provider_2_started": False,
            "scale_up_started": False,
            "entity_bridge_started": False,
            "source_concept_truth_promotion_authorized": False,
            "entity_truth_authorized": False,
            "media_tags_truth_authorized": False,
            "production_write_authorized": False,
        },
        safety={
            "db_write_attempted": False,
            "provider_calls_attempted": False,
            "llm_provider_calls_attempted": False,
            "media_import_attempted": False,
            "classification_ai_localization_attempted": False,
            "source_concept_resolver_persistence_attempted": False,
            "entity_or_media_tags_truth_mutation_attempted": False,
            "source_icloud_app_storage_mutation_attempted": False,
            "cleanup_delete_reset_drop_truncate_attempted": False,
            "r2_started": False,
            "px1_b_started": False,
            "provider_2_started": False,
            "scale_up_started": False,
            "entity_bridge_started": False,
            "source_concept_truth_promotion_attempted": False,
        },
    )
    for key, value in overrides.items():
        summary[key] = value
    return summary


def test_a1r_route_audit_contract_accepts_one_next_phase_recommendation() -> None:
    result = check_phase_contract("route_audit_contract_v1", _a1r_route_audit_summary())

    assert result.passed is True


def test_a1r_route_audit_contract_rejects_multiple_recommended_next_phases() -> None:
    summary = _a1r_route_audit_summary()
    summary["route_decision_matrix"]["options"].append(
        {
            "candidate": "PX1-B additional Pixiv/source metadata extraction",
            "recommended": True,
            "allowed_by_A1R": True,
        }
    )

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "a1r_multiple_recommended_next_phases" in _error_codes(result)


def test_a1r_route_audit_contract_requires_explicit_false_downstream_flags() -> None:
    summary = _a1r_route_audit_summary()
    summary["route_authorization"].pop("entity_bridge_started")
    summary["safety"]["provider_calls_attempted"] = True

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    codes = _error_codes(result)
    assert "a1r_route_authorization_flag_missing" in codes
    assert "a1r_forbidden_work_attempted" in codes


def test_a1r_route_audit_contract_requires_r1r_intake_for_recommendation() -> None:
    summary = _a1r_route_audit_summary(r1r_evidence_intake={"passed": False})

    result = check_phase_contract("route_audit_contract_v1", summary)

    assert result.passed is False
    assert "a1r_r1r_evidence_not_passed" in _error_codes(result)


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


def test_public_redaction_contract_allows_only_safe_source_root_public_marker() -> None:
    safe_marker = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"source_root_public_marker": "audited-root"}},
    )
    leaked_marker = check_phase_contract(
        "public_redaction_contract_v1",
        {"public_json_payload": {"source_root_public_marker": "icloud-photos-production"}},
    )

    assert safe_marker.passed is True
    assert leaked_marker.passed is False
    assert "public_redaction_private_provenance_value_unredacted" in _error_codes(leaked_marker)


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


def _sv1_contract_summary() -> dict[str, object]:
    executed_stages = [
        "global_non_e2e_baseline",
        "read_only_source_inventory",
        "deterministic_scale_manifest",
        "controlled_media_import",
        "ai_tag_provenance_completion",
        "stable_key_evidence_export_import",
        "controlled_scale_denominator_audit",
        "graph_search_rebuild_benchmark",
        "accepted_source_evidence_actual_rebuild",
        "true_new_media_search_benchmark",
            "connected_component_graph_audit_v2",
            "promotion_rollback_commit_idempotency",
            "immutable_artifact_drift_proof",
            "current_head_repair_validation",
            "prewrite_root_containment",
            "canonical_orchestration_completeness",
            "public_redaction_review_pack",
    ]
    return {
        "pipeline_contract": {
            "contract_id": "sv1_controlled_scale_promotion_readiness_contract_v1",
            "status": "partial_sv1_media_ai_scale_and_stable_key_promotion_complete",
            "target_met": False,
            "safe_to_merge": True,
            "route_approved": False,
            "semantic_completeness_claimed": False,
            "full_library_readiness_claimed": False,
            "production_readiness_claimed": False,
            "provider_readiness_claimed": False,
            "entity_readiness_claimed": False,
            "full_pipeline_completion_claimed": False,
            "active_blockers": [],
            "executed_stages": executed_stages,
        },
        "repository_sync_preflight": {
            "passed": True,
            "local_main_equals_origin_main_before_branch": True,
            "accepted_ml2_merge_is_ancestor": True,
            "accepted_ml2_merge_sha": "7fca41151cc9e1d5b48cfe243279e66296346bae",
            "task_branch_start_sha": "7fca41151cc9e1d5b48cfe243279e66296346bae",
            "tracked_change_count_before_sync": 0,
            "staged_change_count_before_sync": 0,
            "user_owned_artifacts_preserved": True,
        },
        "global_test_baseline": {
            "final_unexpected_failure_count": 0,
            "unexplained_skip_count": 0,
            "environment_specific_profiles_passed": True,
            "sv1_regression_count": 0,
        },
        "environment_isolation": {
            "passed": True,
            "violet_env": "test",
            "production_profile_active": False,
            "scale_database_clean_schema": True,
            "promotion_database_independent": True,
            "source_routes_read_only": True,
            "predecessor_databases_immutable": True,
            "production_database_selected": False,
            "production_storage_selected": False,
            "scale_database_identity": "blombooru_custom_test_scale",
            "promotion_database_identity": "blombooru_custom_test_promotion",
            "rebuild_database_identity": "blombooru_custom_test_rebuild",
        },
        "source_inventory": {"safely_usable_real_media_count": 12000},
        "scale_manifest": {
            "selected_eligible_media_count": 12000,
            "deterministic_selection": True,
            "accepted_current_available_media_included": True,
            "accounting_equality_passed": True,
            "synthetic_or_cloned_media_count": 0,
            "preselection_outcome_counts": {"eligible_unique": 12000},
            "final_outcome_counts": {"selected": 12000},
            "preselection_membership_fingerprint": "preselection-fingerprint",
            "final_membership_fingerprint": "final-fingerprint",
        },
        "media_import": {
            "all_selected_accounted": True,
            "blocking_failed": 0,
            "unexplained_outcome_count": 0,
            "out_of_manifest_import_count": 0,
            "source_mutation_count": 0,
            "eligible_media_after": 12000,
            "original_execution": {
                "imported_media_count": 12000, "storage_write_count": 12000,
                "runtime_seconds": None, "runtime_evidence_available": False,
            },
            "current_invocation": {
                "new_import_count": 0, "storage_write_count": 0,
                "runtime_seconds": 0.0, "resumed_exact_checkpoint": True,
            },
            "cumulative_checkpoint_state": {
                "imported_media_count": 12000, "storage_object_count": 12000,
            },
        },
        "ai_tag_provenance": {
            "coverage": 1.0,
            "missing_provenance_count": 0,
            "fingerprint_mismatch_reuse_count": 0,
            "external_provider_calls": 0,
            "model_download_count": 0,
            "ai_coverage_ledger_count": 12000,
            "ai_coverage_ledger_fingerprint": "ai-ledger-fingerprint",
            "original_accepted_execution": {
                "reused_media_count": 3420, "newly_inferred_media_count": 8580,
                "eligible_media_count": 12000, "ai_inference_executed": True,
            },
            "current_repair_invocation": {
                "checkpoint_existing_covered_media_count": 12000,
                "newly_inferred_media_count": 0, "ai_inference_rerun": False,
            },
        },
        "evidence_export": {
            "passed": True,
            "development_row_id_dependency_count": 0,
            "package_checksum_manifest_passed": True,
            "table_counts": {
                "source_metadata_records": 4421,
                "source_tag_observations": 18112,
                "source_name_observations": 7388,
                "source_concept_evidence": 12027,
                "source_concept_fallback_search_index": 6596,
            },
        },
        "evidence_import": {
            "blocking_failed": 0,
            "unexplained_item_count": 0,
            "accepted_evidence_silently_dropped": 0,
            "development_row_id_dependency_count": 0,
            "exact_stable_key_membership_passed": True,
            "all_table_equations_balanced": True,
            "atomic_import_contract_enforced": True,
            "success_ledger_written_only_after_commit": True,
            "current_reaudit_write_count": 0,
            "extra_materialized_count": 0,
            "fallback_search_target_missing_count": 260,
            "per_table_accounting": {
                "source_metadata_records": {
                    "exported": 4421, "inserted": 0, "compatible_existing": 4421,
                    "deferred_target_missing": 0, "rejected_incompatible": 0,
                    "blocking_failed": 0, "target_missing_reference_count": 298,
                    "equation_balanced": True,
                },
                "source_tag_observations": {
                    "exported": 18112, "inserted": 0, "compatible_existing": 18112,
                    "deferred_target_missing": 0, "rejected_incompatible": 0,
                    "blocking_failed": 0, "target_missing_reference_count": 298,
                    "equation_balanced": True,
                },
                "source_name_observations": {
                    "exported": 7388, "inserted": 0, "compatible_existing": 7388,
                    "deferred_target_missing": 0, "rejected_incompatible": 0,
                    "blocking_failed": 0, "target_missing_reference_count": 298,
                    "equation_balanced": True,
                },
                "source_concept_evidence": {
                    "exported": 12027, "inserted": 0, "compatible_existing": 12027,
                    "deferred_target_missing": 0, "rejected_incompatible": 0,
                    "blocking_failed": 0, "target_missing_reference_count": 298,
                    "equation_balanced": True,
                },
                "source_concept_fallback_search_index": {
                    "exported": 6596, "inserted": 0, "compatible_existing": 6336,
                    "deferred_target_missing": 260, "rejected_incompatible": 0,
                    "blocking_failed": 0, "target_missing_reference_count": 260,
                    "equation_balanced": True,
                },
            },
        },
        "denominator_audit": {
            "accounting_equality_passed": True,
            "mandatory_and_supplemental_distinguished": True,
            "unclassified_count": 0,
            "unexplained_count": 0,
            "canonical_runtime_denominator_changed": False,
            "independent_stored_path_parser_executed": True,
            "stored_path_population_derived_independently": True,
            "selected_media_classification_coverage": 1.0,
            "denominator_classification_fingerprint": "denominator-fingerprint",
            "database_identity": "blombooru_custom_test_scale",
            "manifest_content_key_count": 12000,
            "database_content_key_count": 12000,
            "duplicate_manifest_content_key_count": 0,
            "missing_in_database_count": 0,
            "extra_in_database_count": 0,
            "manifest_membership_fingerprint": "manifest-membership",
            "database_membership_fingerprint": "database-membership",
            "missing_membership_fingerprint": "empty-missing",
            "extra_membership_fingerprint": "empty-extra",
            "exact_membership_equality": True,
            "safe_to_publish_denominator": True,
        },
        "r2r_reuse": {
            "exact_pair_membership_passed": True,
            "fingerprint_compatible": True,
            "accepted_pair_count": 3319,
            "must_link_count": 1522,
            "cannot_link_count": 1791,
            "deferred_nonblocking_count": 6,
            "coverage": 1.0,
        },
        "identity_traceability": {
            "accepted_606_family_traceability_passed": True,
            "accepted_family_count": 606,
            "human_review_queue_count": 0,
            "needs_review_normal_pipeline_count": 0,
        },
        "pair_accounting": {
            "candidate_equation_passed": True,
            "all_pairs_creator_alias_expansion_used": False,
        },
        "graph_safety": {
            "graph_audit_algorithm_version": "active_bipartite_connected_components_v2",
            "component_membership_fingerprint": "component-fingerprint",
            "pair_membership_fingerprint": "pair-fingerprint",
            "giant_component_recurrence": False,
            "multi_stable_id_creator_component_count": 0,
            "direct_cannot_link_violation_count": 0,
            "transitive_cannot_link_violation_count": 0,
            "unauthorized_cross_role_component_count": 0,
            "unknown_role_materialization_count": 0,
            "deferred_identity_union_count": 0,
            "duplicate_active_stable_identity_count": 0,
        },
        "independent_graph_metrics": {
            name: {
                "database_identity": database,
                "graph_audit_algorithm_version": "active_bipartite_connected_components_v2",
                "component_membership_fingerprint": f"{name}-component-fingerprint",
                "pair_membership_fingerprint": f"{name}-pair-fingerprint",
                "giant_component_recurrence": False,
                "multi_stable_id_creator_component_count": 0,
                "direct_cannot_link_violation_count": 0,
                "transitive_cannot_link_violation_count": 0,
                "unauthorized_cross_role_component_count": 0,
                "unknown_role_materialization_count": 0,
                "deferred_identity_union_count": 0,
                "duplicate_active_stable_identity_count": 0,
            }
            for name, database in (
                ("scale", "blombooru_custom_test_scale"),
                ("promotion", "blombooru_custom_test_promotion"),
                ("rebuild", "blombooru_custom_test_rebuild"),
            )
        },
        "actual_rebuild_verification": {
            "derived_row_import_count": 0,
            "accepted_r2r_disposition_compatibility": 1.0,
            "accepted_creator_family_traceability": 1.0,
            "blocking_creator_gap_count": 0,
            "actual_r2r_ml2_derivation_replayed": True,
            "ledger_fingerprint": "rebuild-ledger-fingerprint",
            "ledger_algorithm_version": "actual-r2r-ml2-v2",
            "derivation_algorithm_identity": "source-signal+r2r+ml2",
            "logical_subset_comparison": {
                "graph_logical_mismatch_count": 0, "search_logical_mismatch_count": 0,
                "numeric_row_id_equality_claimed": False,
            },
        },
        "media_count_equality": {
            "passed": True, "manifest_count": 12000, "database_count": 12000,
            "import_ledger_count": 12000, "ai_ledger_count": 12000,
        },
        "true_new_media_search_benchmark": {
            "case_count": 40, "scale_unsupported_result_count": 0,
            "promotion_unsupported_result_count": 0, "rebuild_unsupported_result_count": 0,
            "leakage_count": 0, "deterministic_selection_fingerprint": "new-media-fingerprint",
        },
        "python_identity": {
            "python_version": "3.12.0", "architecture": "64bit",
            "interpreter_class": "repo_local_venv", "code_root_fingerprint": "root-fingerprint",
        },
        "search_benchmark": {
            "unsupported_result_count": 0,
            "rejected_only_result_count": 0,
            "superseded_only_result_count": 0,
            "invalid_or_deleted_only_result_count": 0,
            "and_leakage_count": 0,
            "search_caused_identity_mutation_count": 0,
            "performance_gate_passed": True,
            "scale_p95_ms": 100.0,
            "allowed_scale_p95_ms": 750.0,
            "scale_max_ms": 200.0,
            "promotion_max_ms": 200.0,
        },
        "promotion_rehearsal": {
            "rollback_fingerprint_restoration": True,
            "second_import_mutation_count": 0,
            "logical_cross_database_mismatch_count": 0,
        },
        "mutation_proof": {
            "predecessor_databases_unchanged": True,
            "media_media_tags_unchanged_during_promotion": True,
            "protected_forbidden_tables_unchanged": True,
            "immutable_heavy_artifact_proof_passed": True,
        },
        "immutable_artifact_proof": {
            "passed": True,
            "accepted_manifest_import_ai_package_unchanged": True,
            "storage_object_membership_unchanged": True,
            "scale_protected_tables_unchanged": True,
            "promotion_protected_tables_unchanged": True,
            "accepted_predecessor_databases_unchanged": True,
            "proof_fingerprint": "immutable-proof-fingerprint",
        },
        "operation_counts": {
            "provider_calls": 0,
            "pixiv_calls": 0,
            "gallery_dl_calls": 0,
            "external_llm_calls": 0,
            "production_operations": 0,
            "entity_operations": 0,
            "confirmed_assignment_operations": 0,
            "truth_promotion_operations": 0,
            "source_mutations": 0,
            "localization_operations": 0,
        },
        "public_redaction": {
            "passed": True, "negative_control_passed": True,
            "exact_final_bytes_scanned": True, "absolute_path_finding_count": 0,
        },
        "review_pack": {
            "integrity_passed": True, "member_checksum_equality_passed": True,
            "canonical_final_pack": True, "pack_fingerprint_recorded_privately": True,
            "pack_id": "sv1-finalization-safety-canonical-pack-v2",
        },
        "validation": {
            "current_candidate_validation_passed": True,
            "head_sha_matches_current": True, "changed_file_fingerprint_matches": True,
            "python_identity_fingerprint_matches": True,
            "validation_ledger_fingerprint_verified": True,
            "py_compile_passed": True, "focused_tests_passed": True,
            "documentation_contract_tests_passed": True, "full_non_e2e_passed": True,
        },
        "prewrite_root_containment": {
            "passed": True,
            "validation_order": "resolved_and_validated_before_mkdir_or_artifact_write",
        },
        "canonical_orchestration": {
            "stage": "all", "complete": True,
            "stages": [
                "prepare", "import", "ai", "evidence", "promotion", "benchmark", "rebuild",
                "connected-graph-audits", "repair-benchmark", "finalization-accounting",
                "validation", "repair-finalize",
            ],
        },
        "route_decision": {
            "route_approved": False,
            "recommended_next_phase": "SCV2-SV1B",
            "next_phase_started": False,
        },
    }


def test_sv1_contract_accepts_only_the_complete_bounded_claim() -> None:
    result = check_phase_contract("sv1_controlled_scale_promotion_readiness_contract_v1", _sv1_contract_summary())

    assert result.passed is True
    assert result.target_met_claimed is False
    assert result.safe_to_merge_claimed is True
    assert result.route_approved is False


def test_sv1_contract_independently_derives_blockers_and_rejects_overclaim() -> None:
    summary = _sv1_contract_summary()
    summary["media_import"]["blocking_failed"] = 1  # type: ignore[index]

    result = check_phase_contract("sv1_controlled_scale_promotion_readiness_contract_v1", summary)

    assert result.passed is False
    assert "sv1_active_blockers_incomplete" in _error_codes(result)
    assert "sv1_target_overclaimed" in _error_codes(result)


def test_sv1_contract_fails_closed_when_required_stage_is_missing() -> None:
    summary = _sv1_contract_summary()
    summary["pipeline_contract"]["executed_stages"].remove("promotion_rollback_commit_idempotency")  # type: ignore[index,union-attr]

    result = check_phase_contract("sv1_controlled_scale_promotion_readiness_contract_v1", summary)

    assert result.passed is False
    assert "sv1_required_stage_missing" in _error_codes(result)
    assert "sv1_active_blockers_incomplete" in _error_codes(result)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("actual_rebuild_verification.blocking_creator_gap_count", 1),
        ("actual_rebuild_verification.actual_r2r_ml2_derivation_replayed", False),
        ("immutable_artifact_proof.storage_object_membership_unchanged", False),
        ("validation.current_candidate_validation_passed", False),
        ("public_redaction.absolute_path_finding_count", 1),
        ("prewrite_root_containment.passed", False),
        ("denominator_audit.database_identity", "blombooru_default_test_wrong"),
        ("canonical_orchestration.complete", False),
        ("media_import.current_invocation.storage_write_count", 1),
        ("ai_tag_provenance.current_repair_invocation.ai_inference_rerun", True),
    ],
)
def test_sv1_finalization_safety_gates_fail_closed(path: str, value: object) -> None:
    summary = _sv1_contract_summary()
    _set_nested(summary, path, value)
    result = check_phase_contract("sv1_controlled_scale_promotion_readiness_contract_v1", summary)
    assert result.passed is False
    assert "sv1_active_blockers_incomplete" in _error_codes(result)
    assert "sv1_target_overclaimed" in _error_codes(result)


def test_sv1_contract_rejects_stale_inventory_and_ambiguous_resume_fields() -> None:
    summary = _sv1_contract_summary()
    summary["scale_manifest"]["inventory_outcome_counts"] = {"eligible_unique": 12000}  # type: ignore[index]
    summary["media_import"]["app_managed_storage_write_count"] = 12000  # type: ignore[index]
    summary["media_import"]["copy_import_runtime_seconds"] = 3604.0  # type: ignore[index]
    result = check_phase_contract("sv1_controlled_scale_promotion_readiness_contract_v1", summary)
    assert result.passed is False
    assert "sv1_active_blockers_incomplete" in _error_codes(result)


@pytest.mark.parametrize("database", ["promotion", "rebuild"])
def test_sv1_contract_blocks_when_only_non_scale_graph_has_violation(database: str) -> None:
    summary = _sv1_contract_summary()
    summary["independent_graph_metrics"][database]["transitive_cannot_link_violation_count"] = 1  # type: ignore[index]
    result = check_phase_contract("sv1_controlled_scale_promotion_readiness_contract_v1", summary)
    assert result.passed is False
    active_error = next(error for error in result.errors if error.code == "sv1_active_blockers_incomplete")
    assert "blocked_sv1_graph_safety" in active_error.expected
    assert "sv1_target_overclaimed" in _error_codes(result)


def _sv1b_contract_summary() -> dict[str, object]:
    stages = [
        "accepted_baseline_checkpoint",
        "superseded_retry1_forensic_classification",
        "primary_phase_delta_checkpoint",
        "provider_pre_execution_hardening",
        "credential_redaction_preflight",
        "canonical_candidate_manifest",
        "finite_metadata_acquisition",
        "metadata_normalization_retention",
        "localization_closure",
        "source_graph_rebuild",
        "accepted_baseline_preservation",
        "connected_component_graph_audit",
        "search_lifecycle_and_and_validation",
        "clean_replay_verification",
        "manual_acceptance_harness",
        "full_validation_and_immutable_proof",
    ]
    graph = {
        "multi_stable_id_creator_component_count": 0,
        "direct_cannot_link_violation_count": 0,
        "transitive_cannot_link_violation_count": 0,
        "deferred_identity_union_count": 0,
        "unauthorized_cross_role_component_count": 0,
        "unknown_role_materialization_count": 0,
        "duplicate_active_stable_identity_count": 0,
        "giant_component_recurrence": False,
        "concept_signal_link_membership_fingerprint": "graph-fingerprint",
        "pair_membership_fingerprint": "pair-fingerprint",
    }
    page_closed_outcomes = {
        "metadata_complete": 7661,
        "terminal_remote_unavailable": 80,
        "deferred_nonblocking_source_page_mismatch": 16,
        "unattempted": 0,
        "pending": 0,
        "retryable": 0,
        "authentication_failure": 0,
        "rate_limit_failure": 0,
        "network_failure": 0,
        "generic_provider_failure": 0,
        "parser_failure": 0,
        "normalization_failure": 0,
        "unresolved_identity_conflict": 0,
        "unexplained_outcome": 0,
        "blocking_failure": 0,
    }
    work_closed_outcomes = dict(page_closed_outcomes)
    work_closed_outcomes["metadata_complete"] = 6932
    return {
        "pipeline_contract": {
            "contract_id": "sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1",
            "status": "automated_sv1b_candidate_ready_manual_acceptance_pending",
            "target_met": False,
            "safe_to_merge": False,
            "route_approved": False,
            "manual_acceptance_required": True,
            "manual_acceptance_status": "pending_user",
            "active_blockers": [],
            "executed_stages": stages,
        },
        "repository_sync_preflight": {
            "passed": True,
            "accepted_merge_sha": "46861489fa0b3b05ae917a99a3932897efd70365",
            "accepted_evidence_head": "af073ca0ad2a9df9418cf072dc381d7b2c10216a",
            "branch_start_sha": "46861489fa0b3b05ae917a99a3932897efd70365",
            "local_main_equals_origin_main_before_branch": True,
            "tracked_change_count_before_sync": 0,
            "staged_change_count_before_sync": 0,
            "user_owned_artifacts_preserved": True,
        },
        "environment_isolation": {
            "passed": True,
            "violet_env": "test",
            "primary_database_identity": "blombooru_custom_sv1b_primary_test",
            "replay_database_identity": "blombooru_custom_sv1b_replay_test",
            "accepted_storage_read_only": True,
            "production_selected": False,
        },
        "immutable_input_proof": {
            "passed": True,
            "manifest_fingerprint": "5f7ccaec155db688db72ed4a762cbd7d2977382e80344c385e3d40fcf6bd610f",
            "all_before_after_fingerprints_equal": True,
            "accepted_database_mutation_count": 0,
            "accepted_storage_mutation_count": 0,
        },
        "accepted_baseline_checkpoint": {
            "checkpoint": "A_ACCEPTED_BASELINE",
            "passed": True,
            "manifest_fingerprint": "5f7ccaec155db688db72ed4a762cbd7d2977382e80344c385e3d40fcf6bd610f",
            "accepted_r2r_snapshot_fingerprint": "25090761abff2c2ae9f7ef8d9ea04904c47a9f3a43ce03ab660a39502ae792fc",
            "provider_tooling_executed_before_checkpoint": False,
            "checkpoint_fingerprint": "checkpoint-a-fingerprint",
            "primary": {
                "accepted_stable_key_reconciliation": {
                    "missing_accepted_stable_keys": 0,
                    "extra_nonderived_stable_keys": 0,
                    "accepted_payload_drift": 0,
                },
                "derived_graph_row_count": 0,
                "phase_owned_delta_row_count": 0,
                "phase_owned_provider_execution_row_count": 0,
            },
            "replay": {
                "accepted_stable_key_reconciliation": {
                    "missing_accepted_stable_keys": 0,
                    "extra_nonderived_stable_keys": 0,
                    "accepted_payload_drift": 0,
                },
                "derived_graph_row_count": 0,
                "phase_owned_delta_row_count": 0,
                "phase_owned_provider_execution_row_count": 0,
            },
        },
        "retry1_forensics": {
            "passed": True,
            "read_only": True,
            "retry1_provider_execution_authorized": False,
            "payload_drift_row_count": 489,
            "accepted_provider_fact_mutation_count": 0,
            "stable_identity_change_count": 0,
        },
        "primary_phase_delta_checkpoint": {
            "checkpoint": "B_PRIMARY_PHASE_DELTA",
            "passed": True,
            "accepted_rows_missing": 0,
            "accepted_stable_identities_changed": 0,
            "accepted_provider_facts_changed": 0,
            "phase_delta_envelope_failure_count": 0,
            "accepted_baseline_plus_phase_delta_equation_passed": True,
            "retry1_deterministic_transformation_reproduced": True,
            "phase_delta_fingerprint": "phase-delta-fingerprint",
        },
        "provider_hardening": {
            "persistent_cross_process_spacing_passed": True,
            "spacing_survives_restart_and_resume": True,
            "manifest_scoped_outcome_keys_passed": True,
            "conflict_mismatch_persistence_passed": True,
            "terminal_classifier_precedence_passed": True,
            "finite_manifest_passed": True,
            "no_concurrent_duplicate_execution": True,
            "metadata_only_command_passed": True,
            "subprocess_arguments_redacted": True,
            "subprocess_environment_redacted": True,
            "minimum_spacing_seconds": 2.0,
            "maximum_attempts_per_work": 3,
            "fallback_provider_used": False,
            "media_download_enabled": False,
        },
        "credential_preflight": {
            "approved_local_route_available": True,
            "operator_confirmation_policy_passed": True,
            "delimiter_aware_fingerprint_scan_passed": True,
            "redacted_authentication_preflight_passed": True,
            "secret_value_exposed": False,
            "raw_configuration_output_exposed": False,
        },
        "candidate_accounting": {
            "manifest_media_count": 12000,
            "canonical_candidate_media_count": 6496,
            "explicit_non_candidate_media_count": 5504,
            "accounting_equality_passed": True,
            "independently_reproduced": True,
            "change_from_sv1a_fully_accounted": True,
            "unclassified_count": 0,
            "unexplained_count": 0,
            "page_media_manifest_fingerprint": "page-fingerprint",
            "distinct_work_manifest_fingerprint": "work-fingerprint",
            "page_media_manifest_row_count": 7757,
            "distinct_work_manifest_row_count": 7028,
        },
        "acquisition_accounting": {
            "requested_page_count": 7757,
            "distinct_work_count": 7028,
            "page_outcome_counts": dict(page_closed_outcomes),
            "work_outcome_counts": dict(work_closed_outcomes),
            "page_equation_passed": True,
            "work_equation_passed": True,
            "checkpoint_after_every_attempt": True,
            "out_of_manifest_attempt_count": 0,
            "concurrent_duplicate_attempt_count": 0,
        },
        "metadata_retention": {
            "raw_and_normalized_package_retained": True,
            "creator_identity_fields_retained": True,
            "work_title_and_provider_tags_retained": True,
            "trusted_parent_policy_passed": True,
            "entity_truth_write_count": 0,
            "media_tags_truth_write_count": 0,
        },
        "localization_closure": {
            "eligible_ai_tag_missing_count": 0,
            "silently_missing_eligible_count": 0,
            "localization_ambiguity_count": 0,
            "final_untranslated_echo_count": 1,
            "final_missing_result_count": 0,
            "final_invalid_display_count": 0,
            "final_invalid_aliases_count": 0,
            "final_unexpected_result_count": 0,
            "final_duplicate_result_count": 0,
            "item_validation_policy_version": "sv1b_localization_item_validation_v1",
            "display_preserve_policy_version": "sv1b_localization_display_preserve_v1",
            "targeted_adjudication_prompt_version": "sv1b_localization_targeted_item_prompt_v1",
            "manual_review_policy_version": "sv1b_manual_localization_review_pending_v1",
            "manual_review_pending_threshold": 8,
            "initial_eligible_count": 1788,
            "accepted_new_translation_count": 1787,
            "explicit_proper_noun_exclusion_count": 454,
            "explicit_display_preserved_count": 0,
            "manual_localization_review_pending_count": 1,
            "manual_localization_override_count": 0,
            "missing_disposition_count": 0,
            "duplicate_disposition_count": 0,
            "standard_batch_call_count": 72,
            "item_adjudication_call_count": 0,
            "external_llm_call_count": 72,
            "primary_replay_translation_fingerprint_equal": True,
            "localization_equations": {
                "initial_missing_balanced": True,
                "eligible_outcomes_balanced": True,
                "translation_count_balanced": True,
                "terminal_membership_exact": True,
                "primary_replay_equal": True,
                "silently_missing_zero": True,
                "duplicate_disposition_zero": True,
            },
            "localization_accounting_closed": True,
            "localization_translation_complete": False,
            "downstream_progression_allowed": True,
            "transport_logging": {
                "minimum_log_level": "WARNING",
                "root_handler_filters_added": 0,
                "process_log_record_factory_redaction_enabled": False,
                "unrelated_loggers_modified": False,
                "non_sensitive_url_context_preserved": True,
                "exception_context_preserved": True,
                "request_response_body_logging_enabled": False,
            },
            "provider_tags_written_to_media_tags_count": 0,
            "original_provider_text_preserved": True,
            "projected_and_actual_llm_cost_usd": 0.0,
            "fallback_provider_used": False,
            "image_upload_count": 0,
        },
        "r2r_replay_accounting": {
            "accepted_snapshot_fingerprint": "25090761abff2c2ae9f7ef8d9ea04904c47a9f3a43ce03ab660a39502ae792fc",
            "exact_endpoint_and_disposition_membership_passed": True,
            "accepted_pair_count": 3319,
            "comparable_count": 3319,
            "genuine_target_missing_count": 0,
            "ambiguous_remap_count": 0,
            "conflicting_remap_count": 0,
            "compatibility_derived_from_verified_pairs": True,
        },
        "baseline_preservation": {
            "accepted_family_count": 606,
            "accepted_family_traceable_count": 606,
            "accepted_stable_identity_disappeared_count": 0,
            "cannot_link_became_identity_union_count": 0,
            "search_only_became_identity_count": 0,
            "every_changed_family_has_governed_reason": True,
        },
        "primary_graph_safety": dict(graph),
        "replay_graph_safety": dict(graph),
        "primary_replay_comparison": {
            "checkpoint_membership_gate_passed": True,
            "unexplained_logical_mismatch_count": 0,
            "numeric_row_id_equality_claimed": False,
        },
        "search_validation": {
            "counters_derived_from_returned_rows": True,
            "independent_expected_membership_used": True,
            "blombooru_tags_protected": True,
            "unsupported_result_count": 0,
            "rejected_only_result_count": 0,
            "superseded_only_result_count": 0,
            "invalid_deleted_only_result_count": 0,
            "and_leakage_count": 0,
            "search_caused_identity_mutation_count": 0,
            "lifecycle_status_violation_count": 0,
            "supported_query_missing_result_count": 0,
            "p95_latency_ms": 10.0,
        },
        "validation": {
            "failed_test_count": 0,
            "unexplained_skip_count": 0,
            "exact_approved_skip_membership_passed": True,
            "full_default_non_e2e_passed": True,
            "environment_specific_profiles_passed": True,
            "json_parse_passed": True,
            "public_redaction_passed": True,
            "git_diff_check_passed": True,
            "real_browser_validation_passed": True,
        },
        "manual_acceptance": {
            "required": True,
            "status": "pending_user",
            "case_count": 40,
            "category_case_counts": {
                "pixiv_metadata": 12,
                "creator_clustering": 8,
                "shared_name_cannot_link": 6,
                "ai_tag_localization": 8,
                "search_and_negative": 6,
            },
            "actual_backend_services_used": True,
            "result_private_and_uncommitted": True,
            "absolute_paths_exposed": False,
            "acceptance_case_manifest_fingerprint": "acceptance-fingerprint",
            "localhost_url": "http://127.0.0.1:8012/",
        },
        "operation_counts": {
            "media_downloads": 0,
            "media_imports": 0,
            "ai_tagging_runs": 0,
            "classification_runs": 0,
            "production_operations": 0,
            "full_library_operations": 0,
            "entity_operations": 0,
            "confirmed_assignment_operations": 0,
            "media_tags_truth_writes": 0,
            "source_icloud_mutations": 0,
            "fallback_provider_calls": 0,
            "hidden_daemon_starts": 0,
            "fl1_operations": 0,
        },
        "route_decision": {
            "route_approved": False,
            "recommended_next_phase": "SCV2-FL1",
            "next_phase_started": False,
        },
    }


def test_sv1b_contract_accepts_only_pending_user_automated_candidate() -> None:
    result = check_phase_contract(
        "sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1",
        _sv1b_contract_summary(),
    )
    assert result.passed is True
    assert result.target_met_claimed is False
    assert result.safe_to_merge_claimed is False
    assert result.route_approved is False


def test_sv1b_contract_accepts_work_level_mixed_closed_when_pages_are_terminal() -> None:
    summary = _sv1b_contract_summary()
    work_outcomes = summary["acquisition_accounting"]["work_outcome_counts"]
    work_outcomes["metadata_complete"] -= 10
    work_outcomes["mixed_closed"] = 10

    result = check_phase_contract(
        "sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1",
        summary,
    )

    assert result.passed is True


def test_sv1b_contract_allows_one_pending_but_blocks_systemic_quality_above_eight() -> None:
    accepted = check_phase_contract(
        "sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1",
        _sv1b_contract_summary(),
    )
    assert accepted.passed is True

    summary = _sv1b_contract_summary()
    localization = summary["localization_closure"]
    localization["accepted_new_translation_count"] = 1779
    localization["manual_localization_review_pending_count"] = 9
    localization["final_untranslated_echo_count"] = 9
    localization["downstream_progression_allowed"] = False
    pipeline = summary["pipeline_contract"]
    pipeline["status"] = "blocked_sv1b_systemic_localization_quality"
    pipeline["manual_acceptance_status"] = "not_started_blocked"
    pipeline["active_blockers"] = [
        "blocked_sv1b_systemic_localization_quality"
    ]
    blocked = check_phase_contract(
        "sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1",
        summary,
    )
    assert blocked.passed is True
    assert blocked.target_met_claimed is False
    assert blocked.safe_to_merge_claimed is False


def test_sv1b_contract_accepts_only_the_exact_phase_scoped_credential_waiver() -> None:
    summary = _sv1b_contract_summary()
    summary["credential_preflight"] = {
        "approved_local_route_available": True,
        "operator_confirmation_policy_passed": True,
        "delimiter_aware_fingerprint_scan_passed": False,
        "credential_risk_waiver_accepted": True,
        "credential_risk_waiver_policy": "operator_accepted_existing_local_pixiv_credential_risk_sv1b_v1",
        "credential_rotation_performed": False,
        "known_compromised_secret_fingerprint_scan_performed": False,
        "generic_delimiter_aware_secret_scan_passed": True,
        "raw_credential_exposure_count": 0,
        "raw_config_exposure_count": 0,
        "credential_like_value_finding_count": 0,
        "redacted_authentication_preflight_passed": True,
        "secret_value_exposed": False,
        "raw_configuration_output_exposed": False,
    }
    result = check_phase_contract(
        "sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1",
        summary,
    )
    assert result.passed is True
    summary["credential_preflight"]["credential_risk_waiver_policy"] = "operator_accepted_wrong_scope_v1"
    result = check_phase_contract(
        "sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1",
        summary,
    )
    assert result.passed is False
    assert "blocked_sv1b_provider_authentication" in next(
        item.expected for item in result.errors if item.code == "sv1b_active_blockers_incomplete"
    )


@pytest.mark.parametrize(
    ("path", "value", "blocker"),
    [
        ("accepted_baseline_checkpoint.primary.phase_owned_delta_row_count", 1, "blocked_sv1b_accepted_baseline_checkpoint"),
        ("retry1_forensics.accepted_provider_fact_mutation_count", 1, "blocked_sv1b_accepted_provider_fact_mutation"),
        ("primary_phase_delta_checkpoint.accepted_provider_facts_changed", 1, "blocked_sv1b_primary_phase_delta_checkpoint"),
        ("provider_hardening.spacing_survives_restart_and_resume", False, "blocked_sv1b_provider_hardening"),
        ("credential_preflight.redacted_authentication_preflight_passed", False, "blocked_sv1b_provider_authentication"),
        ("acquisition_accounting.page_outcome_counts.retryable", 1, "blocked_sv1b_acquisition_incomplete"),
        ("localization_closure.eligible_ai_tag_missing_count", 1, "blocked_sv1b_normalization_or_localization"),
        ("localization_closure.localization_ambiguity_count", 1, "blocked_sv1b_normalization_or_localization"),
        ("localization_closure.final_untranslated_echo_count", 2, "blocked_sv1b_normalization_or_localization"),
        ("localization_closure.display_preserve_policy_version", "wrong-policy", "blocked_sv1b_normalization_or_localization"),
        ("localization_closure.external_llm_call_count", 71, "blocked_sv1b_normalization_or_localization"),
        ("localization_closure.localization_equations.eligible_outcomes_balanced", False, "blocked_sv1b_normalization_or_localization"),
        ("localization_closure.transport_logging.minimum_log_level", "INFO", "blocked_sv1b_normalization_or_localization"),
        ("localization_closure.transport_logging.process_log_record_factory_redaction_enabled", True, "blocked_sv1b_normalization_or_localization"),
        ("r2r_replay_accounting.ambiguous_remap_count", 1, "blocked_sv1b_r2r_replay"),
        ("primary_graph_safety.transitive_cannot_link_violation_count", 1, "blocked_sv1b_graph_safety"),
        ("search_validation.and_leakage_count", 1, "blocked_sv1b_search_safety"),
        ("manual_acceptance.case_count", 39, "blocked_sv1b_manual_acceptance_harness"),
    ],
)
def test_sv1b_contract_independently_derives_fail_closed_blockers(
    path: str, value: object, blocker: str
) -> None:
    summary = _sv1b_contract_summary()
    _set_nested(summary, path, value)
    result = check_phase_contract(
        "sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1",
        summary,
    )
    assert result.passed is False
    finding = next(item for item in result.errors if item.code == "sv1b_active_blockers_incomplete")
    assert blocker in finding.expected
    assert "sv1b_completion_overclaimed" in _error_codes(result)


def test_sv1b_contract_never_allows_manual_acceptance_or_merge_claim_in_automation() -> None:
    summary = _sv1b_contract_summary()
    _set_nested(summary, "pipeline_contract.manual_acceptance_status", "accepted")
    _set_nested(summary, "pipeline_contract.target_met", True)
    _set_nested(summary, "pipeline_contract.safe_to_merge", True)
    result = check_phase_contract(
        "sv1b_controlled_pixiv_metadata_localization_source_graph_closure_contract_v1",
        summary,
    )
    assert result.passed is False
    assert "sv1b_pending_claim_incomplete" in _error_codes(result)


def _sv1b_owner_closeout_summary() -> dict[str, object]:
    return {
        "pipeline_contract": {
            "contract_id": "sv1b_owner_acceptance_closeout_contract_v1",
            "status": "sv1b_accepted_with_known_nonblocking_limitations",
            "target_met": False,
            "safe_to_merge": True,
            "route_approved": True,
            "manual_acceptance_required": True,
            "manual_acceptance_status": "accepted_with_known_nonblocking_limitations",
            "active_blockers": [],
        },
        "composite_acceptance": {
            "passed": True,
            "manual_acceptance_status": "accepted_with_known_nonblocking_limitations",
            "case_count": 40,
            "pass_count": 37,
            "owner_waived_nonblocking_known_limitation_count": 3,
            "pending_count": 0,
            "unwaived_fail_count": 0,
            "owner_waived_case_ids": ["B01", "B04", "B08"],
            "owner_waiver_identity": "owner_accepted_sv1b_placeholder_creator_identity_limitations_v1_20260807",
            "underlying_mismatch_preserved": True,
            "waiver_scope": "SCV2-SV1B_only",
            "file_sha256": "composite-file-sha",
            "composite_fingerprint": "composite-fingerprint",
            "binding_fingerprint": "4992ed754539ef1f14500825d0fd78fc448e26846780cd4c64bacc5c2c6c3f81",
            "case_manifest_sha256": "b37eb60dc90418959a6b3a7be188dedc29eb29ebf8c85c5303dd8665bdfdad5c",
            "delta_audit_sha256": "fe3455b9b9fd2cfcb13d242f01208a378ef69342896905044c789523aaaadbb1",
            "old_result_sha256": "6ad0d4d78815de0984a4e563490be91e985e9f109facb462c8528896867ae2b9",
        },
        "behavior_neutral_carry_forward": {
            "passed": True,
            "accepted_implementation_head": "e7ada8e83593cbb639f0c1fd4442f76e47537e8d",
            "closeout_head": "f" * 40,
            "file_sha256": "carry-file-sha",
            "proof_fingerprint": "carry-fingerprint",
            "runtime_data_search_graph_localization_semantics_changed": False,
            "changed_files": ["docs/state/current-phase.json"],
        },
        "operation_counts": {
            "database_access": 0,
            "database_write": 0,
            "provider_request": 0,
            "llm_request": 0,
            "media_download": 0,
            "production_access": 0,
            "entity_truth_write": 0,
            "provider_derived_media_tags_write": 0,
        },
        "route_decision": {
            "route_approved": True,
            "route_scope": "SCV2-FL1_planning_only_no_execution",
            "fl1_data_execution_authorized": False,
            "production_authorized": False,
            "next_phase_started": False,
        },
    }


def test_sv1b_owner_closeout_contract_accepts_scoped_owner_waivers() -> None:
    result = check_phase_contract(
        "sv1b_owner_acceptance_closeout_contract_v1",
        _sv1b_owner_closeout_summary(),
    )

    assert result.passed is True
    assert result.target_met_claimed is False
    assert result.safe_to_merge_claimed is True
    assert result.route_approved is True


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        ("composite_acceptance.pass_count", 40, "sv1b_closeout_composite_invalid"),
        ("composite_acceptance.owner_waived_case_ids", [], "sv1b_closeout_composite_invalid"),
        ("composite_acceptance.waiver_scope", "SCV2-FL1", "sv1b_closeout_composite_invalid"),
        ("behavior_neutral_carry_forward.runtime_data_search_graph_localization_semantics_changed", True, "sv1b_closeout_carry_forward_invalid"),
        ("operation_counts.database_access", 1, "sv1b_closeout_forbidden_activity"),
        ("route_decision.fl1_data_execution_authorized", True, "sv1b_closeout_route_scope_invalid"),
        ("route_decision.route_scope", "production", "sv1b_closeout_route_scope_invalid"),
    ],
)
def test_sv1b_owner_closeout_contract_fails_closed(
    path: str, value: object, code: str
) -> None:
    summary = _sv1b_owner_closeout_summary()
    _set_nested(summary, path, value)

    result = check_phase_contract(
        "sv1b_owner_acceptance_closeout_contract_v1", summary
    )

    assert result.passed is False
    assert code in _error_codes(result)
