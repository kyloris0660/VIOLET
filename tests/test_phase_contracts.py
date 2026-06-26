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

from scripts.phase_contracts import REQUIRED_CONTRACT_IDS, check_phase_contract, list_contracts, load_summary_file  # noqa: E402
from scripts.phase_contracts import contract_checks as contract_checks_module  # noqa: E402
from scripts.phase_contracts.contract_registry import SOURCE_CONCEPT_FULL_CHAIN_STAGES  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "phase_contracts"


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
                "manual_sync_execute_active_blocks_ai_job": True,
                "manual_sync_execute_active_blocks_classification_job": True,
            },
            "runner_outputs": {
                "default_report_json_gitignored": True,
                "default_report_md_gitignored": True,
                "docs_reports_require_explicit_flags": True,
                "execute_report_uses_approved_plan": True,
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
                "single_item_failure_does_not_fail_whole_run": True,
            },
            "active_run_recovery": {
                "stale_pending_running_finalized": True,
                "stale_cancelling_finalized": True,
                "timeout_seconds": 1800,
            },
            "plan_replay_protection": {
                "plan_hash_binds_created_at": True,
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


def test_s3a_m1_contract_rejects_stale_plan_replay_gap() -> None:
    summary = copy.deepcopy(_s3a_m1_summary())
    summary["manual_sync"]["plan_replay_protection"]["forged_fresh_timestamp_rejected"] = False

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
