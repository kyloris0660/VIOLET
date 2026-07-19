"""Focused tests for the S3A-PROD1 operator incremental sync guard."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "run_s3a_prod1_operator_incremental_sync_guard.py"

_spec = importlib.util.spec_from_file_location("run_s3a_prod1_operator_incremental_sync_guard", SCRIPT_PATH)
assert _spec and _spec.loader
s3a_prod1 = importlib.util.module_from_spec(_spec)
sys.modules["run_s3a_prod1_operator_incremental_sync_guard"] = s3a_prod1
_spec.loader.exec_module(s3a_prod1)

from backend.app.services import source_ingestion_gate  # noqa: E402
from backend.app.utils.cloud_files import CloudFileState  # noqa: E402
from scripts.phase_contracts import check_phase_contract  # noqa: E402


def _provider(actual: str = "DmlExecutionProvider") -> dict:
    return {
        "requested_provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "actual_provider": actual,
        "actual_onnx_provider_loaded": actual,
        "loaded_providers": [actual, "CPUExecutionProvider"] if actual != "CPUExecutionProvider" else ["CPUExecutionProvider"],
        "fallback_occurred": False,
        "fallback_reason": None,
        "provider_load_errors": [],
    }


def _ai_run(*, actual: str = "DmlExecutionProvider", dry_run: bool = False, delta: int = 14) -> dict:
    return {
        "reported": True,
        "executed": True,
        "status": "completed",
        "dry_run": dry_run,
        "local_files_only": True,
        "provider_preference_requested": ["DmlExecutionProvider", "CPUExecutionProvider"]
        if actual != "CPUExecutionProvider"
        else ["CPUExecutionProvider"],
        "selected_media_count": 1,
        "processed": 1,
        "tags_added": 10 if delta else 0,
        "suggestions_added": 4 if delta else 0,
        "skipped_locked": 0,
        "ignored_low_confidence": 10844,
        "failed": 0,
        "rollback_error": False,
        "error_state": False,
        "media_tags_count_before": 0,
        "media_tags_count_after": delta,
        "media_tags_count_delta": delta,
        "media_with_ai_tags_before": 0,
        "media_with_ai_tags_after": 1 if delta else 0,
        "media_with_ai_tags_delta": 1 if delta else 0,
        "first_time_media_tag_insertion_proven": bool(delta and not dry_run),
        "no_media_tags_writes": True if dry_run else None,
        "provider": _provider(actual),
        "load_control": {
            "effective_batch_size": 1,
            "batch_size": 1,
            "configured_batch_size": 2,
            "batch_cap_source": "AI_TAGGING_BATCH_MAX_ITEMS",
            "cpu_intra_op_threads": 4,
            "cpu_inter_op_threads": 1,
            "preprocess_workers": 2,
            "max_concurrent_jobs": 1,
            "execution_mode": "ORT_SEQUENTIAL",
        },
        "public_item_results": [
            {
                "status": "completed",
                "predictions": 10858,
                "tags_added": 10 if delta else 0,
                "suggestions_added": 4 if delta else 0,
                "skipped_locked": 0,
                "ignored_low_confidence": 10844,
            }
        ],
    }


def _prod_summary(**overrides: object) -> dict:
    directml = _ai_run()
    directml_probe = _ai_run(dry_run=True, delta=0)
    cpu = _ai_run(actual="CPUExecutionProvider", dry_run=True, delta=0)
    summary = {
        "pipeline_contract": {
            "contract_id": "s3a_prod1_operator_incremental_sync_contract_v1",
            "status": "target_met_with_bounded_write",
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": True},
        },
        "run_configuration": {
            "input_mode": "input_path",
            "max_items": 5,
            "provider_preference_requested": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "local_files_only": True,
            "model_download_allowed": False,
            "production_write_requested": True,
            "exact_production_sync_confirmation": True,
            "import_write_requested": True,
            "import_confirmation_exact": True,
            "ai_tagging_write_requested": True,
            "ai_tagging_confirmation_exact": True,
            "single_operator_triggered_run_only": True,
            "no_full_library_fallback": True,
            "production_automation_enabled": False,
            "scheduled_automation_enabled": False,
            "unattended_s3b_enabled": False,
        },
        "scope": {
            "input_mode": "input_path",
            "explicit_input_path_supplied": True,
            "explicit_input_path_redacted": True,
            "input_path_count": 1,
            "selected_count": 1,
            "supported_files": 1,
            "over_cap_count": 0,
            "no_full_library_fallback": True,
            "private_locator_values_recorded": False,
            "public_path_redaction": "paths_and_filenames_redacted",
            "protected_input_gate": {
                "reported": True,
                "passed": True,
                "blocked_count": 0,
                "protected_root_labels": ["[redacted]", "[redacted]"],
                "paths_redacted": True,
            },
            "source_safety_gate": {
                "reported": True,
                "passed": True,
                "blocked_count": 0,
                "cloud_placeholder_blocked_count": 0,
                "protected_path_blocked_count": 0,
                "local_readable_files": 1,
                "nonzero_files": 1,
                "supported_extension_files": 1,
                "read_probe_used": False,
                "hydration_policy_enabled": False,
                "cloud_detection": {
                    "method": "SourceIngestionGate.evaluate_path_source metadata_only",
                    "platform_specific_cloud_files_detection": True,
                    "conservative_fallback": False,
                },
                "stability_policy": {
                    "enabled": False,
                    "stable_enough": True,
                    "reason": "no_s3a_prod1_min_age_policy_configured",
                },
                "public_item_results": [],
            },
        },
        "preflight": {
            "reported": True,
            "input_mode": "input_path",
            "discovered_supported_files": 1,
            "selected_count": 1,
            "over_cap_check": {"passed": True, "over_cap_count": 0, "max_items": 5},
            "model_cache": {
                "status": "cached",
                "local_files_only": True,
                "model_download_allowed": False,
                "model_download_performed": False,
            },
            "provider_availability": {
                "reported": True,
                "requested_provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
                "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
                "directml_available": True,
                "cpu_fallback_available": True,
                "status": "available",
                "error_type": None,
            },
            "directml_available": True,
            "cpu_fallback_available": True,
            "protected_input_gate": {
                "reported": True,
                "passed": True,
                "blocked_count": 0,
                "protected_root_labels": ["[redacted]", "[redacted]"],
                "paths_redacted": True,
            },
            "source_safety_gate": {
                "reported": True,
                "passed": True,
                "blocked_count": 0,
                "read_probe_used": False,
                "hydration_policy_enabled": False,
            },
            "no_full_library_fallback": True,
            "public_private_path_redaction": {
                "public_path_redaction": "paths_and_filenames_redacted",
                "absolute_paths_redacted": True,
                "file_labels_redacted": True,
                "private_locator_values_recorded": False,
            },
        },
        "model_cache": {
            "local_files_only": True,
            "model_download_allowed": False,
            "model_download_performed": False,
            "status": "cached",
        },
        "db_session": {"reported": True, "available": True, "error_type": None},
        "import_write_preconditions": {
            "passed": True,
            "blockers": [],
            "write_requested": True,
            "input_path_mode": True,
            "local_files_only": True,
            "model_cache_cached": True,
            "model_download_not_allowed": True,
            "model_download_not_performed": True,
            "provider_preference_dml_then_cpu": True,
            "directml_available": True,
            "cpu_fallback_available": True,
            "protected_input_gate_passed": True,
            "source_safety_gate_passed": True,
            "scope_valid": True,
            "no_over_cap_input": True,
            "no_full_library_fallback": True,
            "exact_confirmation_present": True,
        },
        "import_reuse": {
            "reported": True,
            "input_mode": "input_path",
            "executed": True,
            "write_requested": True,
            "exact_confirmation_present": True,
            "imported_count": 1,
            "reused_count": 0,
            "would_import_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "downstream_media_count": 1,
            "source_icloud_mutation": False,
            "app_managed_storage_writes": 1,
        },
        "classification": {
            "reported": True,
            "executed": True,
            "dry_run": True,
            "classified_count": 1,
            "reused_classification_count": 0,
            "failed_count": 0,
            "content_class_distribution": {"unknown": 1},
        },
        "directml_ai_tagging": directml,
        "directml_provider_probe": directml_probe,
        "provider_write_gate": {
            "reported": True,
            "write_requested": True,
            "exact_confirmation_present": True,
            "requested_provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "requires_directml_provider": True,
            "provider_preference_includes_directml": True,
            "provider_preference_includes_cpu_fallback": True,
            "provider_preference_dml_then_cpu": True,
            "directml_available": True,
            "cpu_fallback_available": True,
            "probe_executed": True,
            "probe_status": "completed",
            "probe_failed": 0,
            "probe_rollback_error": False,
            "probe_error_state": False,
            "probe_actual_provider": "DmlExecutionProvider",
            "passed": True,
            "write_allowed": True,
            "no_cpu_only_write_path": True,
            "blockers": [],
        },
        "cpu_fallback_validation": cpu,
        "localization": {
            "reported": True,
            "attempted": True,
            "candidate_tags_count": 14,
            "reused_translations": 9,
            "new_translations": 0,
            "missing_or_deferred": 5,
            "failed": 0,
            "llm_external_provider_used": False,
            "external_provider_used": False,
            "deferred_reason": "external_llm_provider_not_approved_for_s3a_prod1",
        },
        "s3a_boundary": {
            "operator_triggered_production_sync_enabled": True,
            "single_operator_triggered_run_only": True,
            "production_automation_enabled": False,
            "scheduled_automation_enabled": False,
            "unattended_s3b_enabled": False,
            "broad_production_sync_enabled": False,
            "full_library_fallback_enabled": False,
        },
        "safety": {
            "max_items_lte_5": True,
            "explicit_input_required": True,
            "selected_input_explicit_bounded": True,
            "no_full_library_run": True,
            "no_full_library_fallback": True,
            "single_operator_triggered_run_only": True,
            "production_write_without_confirmation": False,
            "import_write_without_confirmation": False,
            "ai_tagging_write_without_confirmation": False,
            "operator_triggered_production_sync_enabled": True,
            "production_automation_enabled": False,
            "unattended_s3b_enabled": False,
            "scheduled_automation_enabled": False,
            "broad_production_sync_enabled": False,
            "provider_pixiv_gallery_dl_saucenao_google_calls": False,
            "sourceconcept_r1_r2_r1r": False,
            "entity_bridge": False,
            "confirmed_entity_assignments": False,
            "desired_media_backfill": False,
            "cleanup_delete_reset_drop_truncate": False,
            "source_icloud_mutation": False,
            "protected_app_storage_input": False,
            "source_safety_gate_passed": True,
            "cloud_hydration_or_recall_triggered": False,
            "model_download": False,
            "local_files_only": True,
            "public_redaction_passed": True,
            "private_locator_values_recorded": False,
            "external_llm_provider_used": False,
        },
        "public_reports": {
            "summary_json_path": "docs/reports/s3a-prod1-operator-incremental-sync-summary.json",
            "markdown_report_path": "tests/fixtures/phase_contracts/s3a_prod1_operator_incremental_sync_report.md",
        },
        "validation": {"production_write_completed": True, "preflight_completed": True},
        "public_redaction": {"passed": True, "finding_count": 0},
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _error_codes(result) -> set[str]:
    return {finding.code for finding in result.errors}


def _set_status(summary: dict, status: str) -> dict:
    s3a_prod1.apply_pipeline_status(summary, status)
    summary.setdefault("validation", {})["production_write_completed"] = s3a_prod1.production_write_completed(summary)
    summary["safety"] = s3a_prod1.build_safety(summary)
    return summary


def test_parser_requires_explicit_input_path() -> None:
    with pytest.raises(SystemExit):
        s3a_prod1.build_parser().parse_args([])


def test_discover_input_candidates_blocks_over_cap_without_truncation(tmp_path: Path) -> None:
    for index in range(6):
        (tmp_path / f"sample_{index}.png").write_bytes(b"not-a-real-image")

    candidates, scope = s3a_prod1.discover_input_candidates([str(tmp_path)], max_items=5)

    assert candidates == []
    assert scope["selected_count"] == 0
    assert scope["over_cap_count"] == 1
    assert scope["over_cap_blocked_before_selection"] is True
    assert scope["default_truncation_disabled"] is True
    assert "sample_0.png" not in str(scope)


def test_discover_input_candidates_blocks_protected_app_storage_before_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    protected = tmp_path / "media"
    protected.mkdir()
    source = protected / "sample.png"
    source.write_bytes(b"not-a-real-image")
    monkeypatch.setattr(s3a_prod1, "protected_input_roots", lambda: [("settings.MEDIA_DIR", protected)])

    candidates, scope = s3a_prod1.discover_input_candidates([str(source)], max_items=5)

    assert candidates == []
    assert scope["protected_input_gate"]["passed"] is False
    assert scope["protected_input_gate"]["blocked_count"] == 1
    assert scope["source_safety_gate"]["blocked_count"] == 1
    assert scope["source_safety_gate"]["public_item_results"][0]["reason"] == "protected_app_storage_input"
    assert "sample.png" not in str(scope)


def test_discover_input_candidates_blocks_cloud_placeholder_before_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "cloud.png"
    source.write_bytes(b"not-a-real-image")
    monkeypatch.setattr(s3a_prod1, "protected_input_roots", lambda: [])
    monkeypatch.setattr(
        source_ingestion_gate,
        "classify_cloud_file_state",
        lambda path: CloudFileState(
            path=str(path),
            supported_platform=True,
            exists=True,
            is_file=True,
            recall_on_data_access=True,
            likely_cloud_placeholder=True,
        ),
    )

    candidates, scope = s3a_prod1.discover_input_candidates([str(source)], max_items=5)

    assert candidates == []
    assert scope["source_safety_gate"]["passed"] is False
    assert scope["source_safety_gate"]["blocked_count"] == 1
    assert scope["source_safety_gate"]["cloud_placeholder_blocked_count"] == 1
    assert scope["source_safety_gate"]["read_probe_used"] is False
    assert scope["source_safety_gate"]["hydration_policy_enabled"] is False
    assert scope["source_safety_gate"]["public_item_results"][0]["reason"] == "cloud_recall_on_data_access"
    assert "cloud.png" not in str(scope)


def test_derive_status_preflight_without_confirmation() -> None:
    summary = _prod_summary()
    summary["run_configuration"]["production_write_requested"] = False
    summary["run_configuration"]["exact_production_sync_confirmation"] = False

    assert s3a_prod1.derive_status(summary) == "preflight_completed_write_confirmation_required"


def test_derive_status_blocks_wrong_confirmation() -> None:
    summary = _prod_summary()
    summary["run_configuration"]["production_write_requested"] = True
    summary["run_configuration"]["exact_production_sync_confirmation"] = False

    assert s3a_prod1.derive_status(summary) == "blocked_production_write_requested_without_exact_confirmation"


def test_derive_status_accepts_bounded_write() -> None:
    assert s3a_prod1.derive_status(_prod_summary()) == "target_met_with_bounded_write"


def test_s3a_prod1_contract_accepts_bounded_write() -> None:
    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", _prod_summary())

    assert result.passed, result.to_dict()


def test_s3a_prod1_contract_rejects_media_id_mode() -> None:
    summary = _prod_summary()
    summary["run_configuration"]["input_mode"] = "media_ids"

    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)

    assert "s3a_prod1_input_mode_invalid" in _error_codes(result)


def test_s3a_prod1_contract_rejects_target_without_exact_confirmation() -> None:
    summary = _prod_summary()
    summary["run_configuration"]["exact_production_sync_confirmation"] = False

    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)

    assert "s3a_prod1_target_without_exact_confirmation" in _error_codes(result)


def test_s3a_prod1_contract_rejects_cpu_primary_write_provider() -> None:
    summary = _prod_summary()
    summary["directml_ai_tagging"] = _ai_run(actual="CPUExecutionProvider", dry_run=False, delta=14)

    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)

    assert "s3a_prod1_actual_provider_not_directml" in _error_codes(result)


def test_cpu_provider_preference_blocks_before_media_tags_write() -> None:
    summary = _prod_summary()
    summary["run_configuration"]["provider_preference_requested"] = ["CPUExecutionProvider"]
    summary["import_write_preconditions"]["passed"] = False
    summary["import_write_preconditions"]["provider_preference_dml_then_cpu"] = False
    summary["import_write_preconditions"]["blockers"] = ["provider_preference_dml_then_cpu"]
    summary["import_reuse"].update({"executed": False, "imported_count": 0, "would_import_count": 1, "downstream_media_count": 0})
    summary["classification"] = s3a_prod1.empty_classification_result(status="not_run_import_write_preconditions_blocked")
    summary["directml_ai_tagging"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_primary",
        status="not_run_provider_write_gate_blocked",
        dry_run=True,
        provider_preference="CPUExecutionProvider",
        local_files_only=True,
        selected_media_count=1,
    )
    summary["directml_provider_probe"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_prewrite_probe",
        status="not_run_provider_write_gate_blocked",
        dry_run=True,
        provider_preference="CPUExecutionProvider",
        local_files_only=True,
        selected_media_count=1,
    )
    summary["provider_write_gate"] = s3a_prod1.build_provider_write_gate(
        summary["preflight"]["provider_availability"],
        "CPUExecutionProvider",
        summary["directml_provider_probe"],
        production_write_requested=True,
        production_confirmed=True,
    )
    _set_status(summary, "blocked_provider_preference_invalid")

    assert s3a_prod1.derive_status(summary) == "blocked_provider_preference_invalid"
    assert summary["provider_write_gate"]["blockers"] == ["provider_preference_missing_directml"]
    assert summary["safety"]["media_tags_write_executed"] is False
    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)
    assert result.passed, result.to_dict()


def test_dml_only_provider_preference_blocks_before_import_or_media_tags_write() -> None:
    summary = _prod_summary()
    summary["run_configuration"]["provider_preference_requested"] = ["DmlExecutionProvider"]
    summary["import_write_preconditions"]["passed"] = False
    summary["import_write_preconditions"]["provider_preference_dml_then_cpu"] = False
    summary["import_write_preconditions"]["blockers"] = ["provider_preference_dml_then_cpu"]
    summary["import_reuse"].update(
        {
            "executed": False,
            "imported_count": 0,
            "would_import_count": 1,
            "downstream_media_count": 0,
            "app_managed_storage_writes": 0,
        }
    )
    summary["classification"] = s3a_prod1.empty_classification_result(status="not_run_import_write_preconditions_blocked")
    summary["directml_provider_probe"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_prewrite_probe",
        status="not_run_provider_write_gate_blocked",
        dry_run=True,
        provider_preference="DmlExecutionProvider",
        local_files_only=True,
    )
    summary["directml_ai_tagging"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_primary",
        status="not_run_provider_write_gate_blocked",
        dry_run=True,
        provider_preference="DmlExecutionProvider",
        local_files_only=True,
    )
    summary["provider_write_gate"] = s3a_prod1.build_provider_write_gate(
        summary["preflight"]["provider_availability"],
        "DmlExecutionProvider",
        summary["directml_provider_probe"],
        production_write_requested=True,
        production_confirmed=True,
    )
    _set_status(summary, "blocked_provider_preference_invalid")

    assert s3a_prod1.derive_status(summary) == "blocked_provider_preference_invalid"
    assert summary["provider_write_gate"]["blockers"] == ["provider_preference_missing_cpu_fallback"]
    assert summary["safety"]["media_tags_write_executed"] is False
    assert summary["import_reuse"]["executed"] is False
    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)
    assert result.passed, result.to_dict()


def test_failed_directml_prewrite_probe_blocks_write() -> None:
    summary = _prod_summary()
    failed_probe = _ai_run(dry_run=True, delta=0)
    failed_probe["status"] = "completed_with_item_failures"
    failed_probe["failed"] = 1
    failed_probe["rollback_error"] = True
    failed_probe["error_state"] = True
    summary["directml_provider_probe"] = failed_probe
    summary["provider_write_gate"] = s3a_prod1.build_provider_write_gate(
        summary["preflight"]["provider_availability"],
        s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        failed_probe,
        production_write_requested=True,
        production_confirmed=True,
    )
    summary["directml_ai_tagging"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_primary",
        status="not_run_provider_write_gate_blocked",
        dry_run=True,
        provider_preference=s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        local_files_only=True,
        selected_media_count=1,
    )
    _set_status(summary, "blocked_directml_provider_not_validated")

    assert s3a_prod1.derive_status(summary) == "blocked_directml_provider_not_validated"
    assert "directml_probe_status_not_completed" in summary["provider_write_gate"]["blockers"]
    assert "directml_probe_failed" in summary["provider_write_gate"]["blockers"]
    assert "directml_probe_rollback_error" in summary["provider_write_gate"]["blockers"]
    assert "directml_probe_error_state" in summary["provider_write_gate"]["blockers"]
    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)
    assert result.passed, result.to_dict()


def test_contract_rejects_write_target_without_clean_directml_probe() -> None:
    summary = _prod_summary()
    summary["provider_write_gate"]["passed"] = False
    summary["provider_write_gate"]["write_allowed"] = False
    summary["provider_write_gate"]["probe_status"] = "completed_with_item_failures"
    summary["provider_write_gate"]["probe_failed"] = 1
    summary["provider_write_gate"]["probe_rollback_error"] = True
    summary["provider_write_gate"]["probe_error_state"] = True
    summary["provider_write_gate"]["blockers"] = ["directml_probe_failed"]

    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)
    codes = _error_codes(result)

    assert "s3a_prod1_ai_write_without_directml_gate" in codes
    assert "s3a_prod1_ai_write_without_clean_directml_probe" in codes


def test_main_cpu_provider_preference_never_calls_non_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    markdown_path = tmp_path / "summary.md"
    monkeypatch.setattr(s3a_prod1, "SUMMARY_PATH", summary_path)
    monkeypatch.setattr(s3a_prod1, "MARKDOWN_PATH", markdown_path)
    monkeypatch.setattr(
        s3a_prod1,
        "discover_input_candidates",
        lambda _paths, max_items: (
            [object()],
            {
                "input_mode": "input_path",
                "explicit_input_path_supplied": True,
                "explicit_input_path_redacted": True,
                "input_path_count": 1,
                "selected_count": 1,
                "supported_files": 1,
                "over_cap_count": 0,
                "max_items": max_items,
                "no_full_library_fallback": True,
                "private_locator_values_recorded": False,
                "public_path_redaction": "paths_and_filenames_redacted",
                "protected_input_gate": {"reported": True, "passed": True, "blocked_count": 0, "paths_redacted": True},
                "source_safety_gate": {
                    "reported": True,
                    "passed": True,
                    "blocked_count": 0,
                    "read_probe_used": False,
                    "hydration_policy_enabled": False,
                },
            },
        ),
    )
    monkeypatch.setattr(
        s3a_prod1.pilot,
        "check_model_cache",
        lambda local_files_only: {
            "status": "cached",
            "local_files_only": local_files_only,
            "model_download_allowed": False,
            "model_download_performed": False,
            "model_file_cached": True,
            "label_file_cached": True,
        },
    )
    monkeypatch.setattr(
        s3a_prod1,
        "check_provider_availability",
        lambda provider_preference: {
            "reported": True,
            "requested_provider_preference": s3a_prod1.provider_list(provider_preference),
            "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "directml_available": True,
            "cpu_fallback_available": True,
            "status": "available",
            "error_type": None,
        },
    )

    class FakeDb:
        def close(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    monkeypatch.setattr(s3a_prod1.pilot, "get_db_session", lambda: FakeDb())
    monkeypatch.setattr(
        s3a_prod1.pilot,
        "import_or_reuse_from_input",
        lambda *_args, **kwargs: (
            {
                "reported": True,
                "input_mode": "input_path",
                "executed": bool(kwargs.get("execute_import")),
                "status": "completed",
                "imported_count": 1 if kwargs.get("execute_import") else 0,
                "reused_count": 0,
                "would_import_count": 1 if not kwargs.get("execute_import") else 0,
                "skipped_count": 0,
                "failed_count": 0,
                "downstream_media_count": 1 if kwargs.get("execute_import") else 0,
                "source_icloud_mutation": False,
                "app_managed_storage_writes": 1 if kwargs.get("execute_import") else 0,
            },
            [101] if kwargs.get("execute_import") else [],
        ),
    )
    monkeypatch.setattr(
        s3a_prod1,
        "classify_media_scope",
        lambda _db, _media_ids: {
            "reported": True,
            "executed": True,
            "status": "completed",
            "classified_count": 1,
            "reused_classification_count": 0,
            "failed_count": 0,
            "content_class_distribution": {"unknown": 1},
        },
    )
    ai_calls: list[tuple[str, bool, str]] = []

    def fake_run_ai_tagging_pass(
        _db,
        *,
        label: str,
        media_ids: list[int],
        dry_run: bool,
        provider_preference: str,
        max_items: int,
        local_files_only: bool,
    ):
        del media_ids, max_items, local_files_only
        ai_calls.append((label, dry_run, provider_preference))
        if not dry_run:
            raise AssertionError("CPU-only provider preference must not reach non-dry-run AI tagging")
        return _ai_run(actual="CPUExecutionProvider", dry_run=True, delta=0), []

    monkeypatch.setattr(s3a_prod1.pilot, "run_ai_tagging_pass", fake_run_ai_tagging_pass)
    monkeypatch.setattr(
        s3a_prod1,
        "validate_localization_reuse",
        lambda _db, _tags: {
            "reported": True,
            "attempted": False,
            "status": "not_run_no_touched_tags",
            "candidate_tags_count": 0,
            "reused_translations": 0,
            "new_translations": 0,
            "missing_or_deferred": 0,
            "failed": 0,
            "llm_external_provider_used": False,
            "external_provider_used": False,
            "deferred_reason": "no_touched_tags",
        },
    )
    monkeypatch.setattr(s3a_prod1, "load_control_observations", lambda _ai, _cpu: {})

    exit_code = s3a_prod1.main(
        [
            "--input-path",
            "redacted-input",
            "--provider-preference",
            "CPUExecutionProvider",
            "--production-sync-confirmation",
            s3a_prod1.PRODUCTION_SYNC_CONFIRMATION,
        ]
    )

    report = json.loads(summary_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert ai_calls == []
    assert report["pipeline_contract"]["status"] == "blocked_provider_preference_invalid"
    assert report["import_reuse"]["executed"] is False
    assert report["provider_write_gate"]["blockers"] == ["provider_preference_missing_directml"]
    assert report["safety"]["media_tags_write_executed"] is False


def test_directml_unavailable_blocks_without_media_tags_write() -> None:
    summary = _prod_summary()
    summary["preflight"]["directml_available"] = False
    summary["preflight"]["provider_availability"]["directml_available"] = False
    summary["provider_availability"] = copy.deepcopy(summary["preflight"]["provider_availability"])
    summary["import_write_preconditions"]["passed"] = False
    summary["import_write_preconditions"]["directml_available"] = False
    summary["import_write_preconditions"]["blockers"] = ["directml_available"]
    summary["import_reuse"].update(
        {
            "executed": False,
            "imported_count": 0,
            "reused_count": 0,
            "skipped_count": 1,
            "downstream_media_count": 0,
            "app_managed_storage_writes": 0,
        }
    )
    summary["directml_ai_tagging"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_primary",
        status="not_run_directml_unavailable",
        dry_run=True,
        provider_preference=s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        local_files_only=True,
        selected_media_count=1,
    )
    summary["directml_provider_probe"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_prewrite_probe",
        status="not_run_provider_write_gate_blocked",
        dry_run=True,
        provider_preference=s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        local_files_only=True,
        selected_media_count=1,
    )
    summary["provider_write_gate"] = s3a_prod1.build_provider_write_gate(
        summary["provider_availability"],
        s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        summary["directml_provider_probe"],
        production_write_requested=True,
        production_confirmed=True,
    )
    _set_status(summary, "blocked_directml_unavailable")

    assert s3a_prod1.derive_status(summary) == "blocked_directml_unavailable"
    assert summary["safety"]["media_tags_write_executed"] is False
    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)
    assert result.passed, result.to_dict()


def test_contract_accepts_db_unavailable_blocked_report() -> None:
    summary = _prod_summary()
    summary["db_session"] = {"reported": True, "available": False, "error_type": "OperationalError"}
    summary["import_reuse"].update(
        {
            "executed": False,
            "status": "not_run_db_unavailable",
            "imported_count": 0,
            "reused_count": 0,
            "skipped_count": 1,
            "downstream_media_count": 0,
            "app_managed_storage_writes": 0,
        }
    )
    summary["classification"] = s3a_prod1.empty_classification_result(status="not_run_db_unavailable")
    summary["directml_ai_tagging"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_primary",
        status="not_run_db_unavailable",
        dry_run=True,
        provider_preference=s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        local_files_only=True,
    )
    summary["directml_provider_probe"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_prewrite_probe",
        status="not_run_db_unavailable",
        dry_run=True,
        provider_preference=s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        local_files_only=True,
    )
    summary["provider_write_gate"] = s3a_prod1.build_provider_write_gate(
        summary["preflight"]["provider_availability"],
        s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        summary["directml_provider_probe"],
        production_write_requested=True,
        production_confirmed=True,
    )
    summary["cpu_fallback_validation"] = s3a_prod1.empty_ai_tagging_result(
        label="cpu_fallback_dry_run",
        status="not_run_db_unavailable",
        dry_run=True,
        provider_preference=s3a_prod1.CPU_PROVIDER_PREFERENCE,
        local_files_only=True,
    )
    summary["localization"].update({"attempted": False, "status": "not_run_db_unavailable", "candidate_tags_count": 0})
    _set_status(summary, "blocked_db_unavailable")

    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)
    assert result.passed, result.to_dict()


def test_contract_accepts_db_unavailable_with_structural_source_blocker() -> None:
    summary = _prod_summary()
    summary["db_session"] = {"reported": True, "available": False, "error_type": "OperationalError"}
    summary["scope"]["selected_count"] = 0
    summary["scope"]["protected_input_gate"]["passed"] = False
    summary["scope"]["protected_input_gate"]["blocked_count"] = 1
    summary["scope"]["source_safety_gate"]["passed"] = False
    summary["scope"]["source_safety_gate"]["blocked_count"] = 1
    summary["scope"]["source_safety_gate"]["protected_path_blocked_count"] = 1
    summary["preflight"]["selected_count"] = 0
    summary["preflight"]["protected_input_gate"] = copy.deepcopy(summary["scope"]["protected_input_gate"])
    summary["preflight"]["source_safety_gate"] = copy.deepcopy(summary["scope"]["source_safety_gate"])
    summary["import_write_preconditions"]["passed"] = False
    summary["import_write_preconditions"]["protected_input_gate_passed"] = False
    summary["import_write_preconditions"]["source_safety_gate_passed"] = False
    summary["import_write_preconditions"]["blockers"] = ["protected_input_gate_passed", "source_safety_gate_passed"]
    summary["import_reuse"].update(
        {
            "executed": False,
            "imported_count": 0,
            "reused_count": 0,
            "would_import_count": 0,
            "skipped_count": 0,
            "downstream_media_count": 0,
            "app_managed_storage_writes": 0,
        }
    )
    summary["classification"] = s3a_prod1.empty_classification_result(status="not_run_db_unavailable")
    summary["directml_ai_tagging"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_primary",
        status="not_run_db_unavailable",
        dry_run=True,
        provider_preference=s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        local_files_only=True,
    )
    _set_status(summary, "blocked_protected_input_path")

    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)
    assert result.passed, result.to_dict()


def test_contract_accepts_input_scope_blocked_reports() -> None:
    over_cap_summary = _prod_summary()
    over_cap_summary["scope"].update({"selected_count": 0, "supported_files": 6, "over_cap_count": 1})
    over_cap_summary["preflight"].update(
        {"discovered_supported_files": 6, "selected_count": 0, "over_cap_check": {"passed": False, "over_cap_count": 1, "max_items": 5}}
    )
    over_cap_summary["import_reuse"].update(
        {"executed": False, "imported_count": 0, "reused_count": 0, "skipped_count": 0, "downstream_media_count": 0}
    )
    over_cap_summary["directml_ai_tagging"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_primary",
        status="not_run_input_scope_blocked",
        dry_run=True,
        provider_preference=s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        local_files_only=True,
    )
    _set_status(over_cap_summary, "blocked_input_over_cap")

    empty_summary = _prod_summary()
    empty_summary["scope"].update({"selected_count": 0, "supported_files": 0, "over_cap_count": 0})
    empty_summary["preflight"].update(
        {"discovered_supported_files": 0, "selected_count": 0, "over_cap_check": {"passed": True, "over_cap_count": 0, "max_items": 5}}
    )
    empty_summary["import_reuse"].update(
        {"executed": False, "imported_count": 0, "reused_count": 0, "skipped_count": 0, "downstream_media_count": 0}
    )
    empty_summary["directml_ai_tagging"] = s3a_prod1.empty_ai_tagging_result(
        label="directml_primary",
        status="not_run_input_scope_blocked",
        dry_run=True,
        provider_preference=s3a_prod1.DEFAULT_PROVIDER_PREFERENCE,
        local_files_only=True,
    )
    _set_status(empty_summary, "blocked_scope_invalid")

    over_cap_result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", over_cap_summary)
    empty_result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", empty_summary)
    assert over_cap_result.passed, over_cap_result.to_dict()
    assert empty_result.passed, empty_result.to_dict()


def test_contract_rejects_target_without_cached_model_proof() -> None:
    summary = _prod_summary()
    summary["model_cache"]["model_download_performed"] = True
    summary["preflight"]["model_cache"]["model_download_performed"] = True

    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)

    assert "s3a_prod1_target_without_cached_model_proof" in _error_codes(result)


def test_failed_write_does_not_mark_production_write_completed() -> None:
    summary = _prod_summary()
    summary["directml_ai_tagging"]["status"] = "completed_with_item_failures"
    summary["directml_ai_tagging"]["failed"] = 1
    _set_status(summary, "blocked_ai_tagging_item_failures")
    summary["validation"]["production_write_completed"] = True

    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)

    assert s3a_prod1.production_write_completed(summary) is False
    assert "s3a_prod1_production_write_completed_overstated" in _error_codes(result)


def test_preflight_stage_failure_blocks_success() -> None:
    summary = _prod_summary()
    summary["run_configuration"]["production_write_requested"] = False
    summary["run_configuration"]["exact_production_sync_confirmation"] = False
    summary["import_reuse"]["failed_count"] = 1
    summary["import_reuse"]["status"] = "completed_with_item_failures"
    _set_status(summary, "preflight_completed_write_confirmation_required")

    assert s3a_prod1.derive_status(summary) == "blocked_import_item_failures"
    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)
    assert "s3a_prod1_import_failures_not_blocked" in _error_codes(result)


def test_target_requires_real_media_tags_delta_or_first_time_proof() -> None:
    summary = _prod_summary()
    summary["directml_ai_tagging"] = _ai_run(dry_run=False, delta=0)
    summary["directml_ai_tagging"]["first_time_media_tag_insertion_proven"] = False
    _set_status(summary, "target_met_with_bounded_write")
    summary["validation"]["production_write_completed"] = True

    assert s3a_prod1.derive_status(summary) == "write_executed_but_first_time_insertion_unproven"
    result = check_phase_contract("s3a_prod1_operator_incremental_sync_contract_v1", summary)
    assert "s3a_prod1_write_target_without_media_tags_delta" in _error_codes(result)
