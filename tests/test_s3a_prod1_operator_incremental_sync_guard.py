"""Focused tests for the S3A-PROD1 operator incremental sync guard."""

from __future__ import annotations

import importlib.util
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
        },
        "preflight": {
            "reported": True,
            "input_mode": "input_path",
            "discovered_supported_files": 1,
            "selected_count": 1,
            "over_cap_check": {"passed": True, "over_cap_count": 0, "max_items": 5},
            "model_cache": {"status": "cached", "local_files_only": True, "model_download_allowed": False},
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
            "status": "cached",
        },
        "import_write_preconditions": {
            "passed": True,
            "blockers": [],
            "write_requested": True,
            "input_path_mode": True,
            "local_files_only": True,
            "model_cache_cached": True,
            "model_download_not_allowed": True,
            "directml_available": True,
            "cpu_fallback_available": True,
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
        "public_redaction": {"passed": True, "finding_count": 0},
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _error_codes(result) -> set[str]:
    return {finding.code for finding in result.errors}


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
