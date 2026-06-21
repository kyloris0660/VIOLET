"""Focused tests for S3A-PILOT1 runner and contract."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "run_s3a_pilot1_new_data_directml_chain.py"

_spec = importlib.util.spec_from_file_location("run_s3a_pilot1_new_data_directml_chain", SCRIPT_PATH)
assert _spec and _spec.loader
s3a_pilot1 = importlib.util.module_from_spec(_spec)
sys.modules["run_s3a_pilot1_new_data_directml_chain"] = s3a_pilot1
_spec.loader.exec_module(s3a_pilot1)

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


def _s3a_summary(**overrides: object) -> dict:
    load_control = {
        "effective_batch_size": 2,
        "batch_size": 2,
        "configured_batch_size": 2,
        "batch_cap_source": "configured",
        "cpu_intra_op_threads": 4,
        "cpu_inter_op_threads": 1,
        "preprocess_workers": 2,
        "max_concurrent_ai_jobs": 1,
        "execution_mode": "ORT_SEQUENTIAL",
        "appeared_bounded": True,
    }
    directml = {
        "reported": True,
        "executed": True,
        "status": "completed",
        "dry_run": True,
        "local_files_only": True,
        "provider_preference_requested": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "selected_media_count": 1,
        "processed": 1,
        "tags_added": 12,
        "suggestions_added": 3,
        "skipped_locked": 0,
        "ignored_low_confidence": 10,
        "failed": 0,
        "media_tags_count_before": 0,
        "media_tags_count_after": 0,
        "media_tags_count_delta": 0,
        "media_with_ai_tags_before": 0,
        "media_with_ai_tags_after": 0,
        "media_with_ai_tags_delta": 0,
        "first_time_media_tag_insertion_proven": False,
        "no_media_tags_writes": True,
        "provider": _provider(),
        "load_control": {
            "effective_batch_size": 2,
            "batch_size": 2,
            "configured_batch_size": 2,
            "batch_cap_source": "configured",
            "cpu_intra_op_threads": 4,
            "cpu_inter_op_threads": 1,
            "preprocess_workers": 2,
            "max_concurrent_jobs": 1,
            "execution_mode": "ORT_SEQUENTIAL",
        },
    }
    cpu = {
        **directml,
        "label": "cpu_fallback_dry_run",
        "provider_preference_requested": ["CPUExecutionProvider"],
        "provider": _provider("CPUExecutionProvider"),
        "selected_media_count": 1,
        "processed": 1,
    }
    summary = {
        "pipeline_contract": {
            "contract_id": "s3a_pilot1_new_data_directml_chain_contract_v1",
            "status": "target_met_dry_run_only",
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": False},
        },
        "run_configuration": {
            "input_mode": "media_ids",
            "max_items": 3,
            "local_files_only": True,
            "model_download_allowed": False,
            "import_write_requested": False,
            "import_confirmation_exact": False,
            "ai_tagging_write_requested": False,
            "ai_tagging_confirmation_exact": False,
            "s3a_production_execution_enabled": False,
            "unattended_s3b_enabled": False,
        },
        "scope": {
            "selected_count": 1,
            "max_items": 3,
            "over_cap_count": 0,
            "no_full_library_fallback": True,
            "private_locator_values_recorded": False,
        },
        "model_cache": {
            "local_files_only": True,
            "model_download_allowed": False,
            "status": "cached",
        },
        "import_write_preconditions": {
            "passed": False,
            "blockers": ["exact_confirmation_present"],
            "write_requested": False,
            "local_files_only": True,
            "model_cache_cached": True,
            "model_download_not_allowed": True,
            "scope_valid": True,
            "no_over_cap_input": True,
            "no_full_library_fallback": True,
            "exact_confirmation_present": False,
        },
        "import_reuse": {
            "reported": True,
            "executed": False,
            "imported_count": 0,
            "reused_count": 1,
            "would_import_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "downstream_media_count": 1,
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
            "reused_translations": 0,
            "new_translations": 0,
            "failed": 0,
            "llm_external_provider_used": False,
            "external_provider_used": False,
            "deferred_reason": "external_llm_provider_not_approved_for_s3a_pilot1",
        },
        "load_control_observations": load_control,
        "s3a_boundary": {
            "operator_triggered_pilot_only": True,
            "production_execution_enabled": False,
            "unattended_enabled": False,
            "scheduled_automation_enabled": False,
            "broad_production_sync_enabled": False,
        },
        "safety": {
            "max_items_lte_5": True,
            "selected_input_explicit_bounded": True,
            "no_full_library_run": True,
            "import_write_without_confirmation": False,
            "ai_tagging_write_without_confirmation": False,
            "production_s3a_execution_enabled": False,
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
            "private_locator_values_recorded": False,
            "external_llm_provider_used": False,
        },
        "public_reports": {
            "markdown_report_path": "docs/reports/s2g-real1-bounded-ai-tagging-validation.md",
        },
        "public_redaction": {"passed": True, "finding_count": 0},
    }
    for key, value in overrides.items():
        summary[key] = value
    return summary


def _error_codes(result) -> set[str]:
    return {finding.code for finding in result.errors}


def test_discover_input_candidates_is_bounded_and_redacted(tmp_path: Path) -> None:
    for index in range(7):
        (tmp_path / f"sample_{index}.png").write_bytes(b"not-a-real-image")
    (tmp_path / "ignored.txt").write_text("ignore me", encoding="utf-8")

    candidates, scope = s3a_pilot1.discover_input_candidates([str(tmp_path)], max_items=5)

    assert len(candidates) == 0
    assert scope["selected_count"] == 0
    assert scope["over_cap_count"] == 2
    assert scope["over_cap_blocked_before_selection"] is True
    assert scope["default_truncation_disabled"] is True
    assert scope["explicit_input_path_redacted"] is True
    assert "sample_0.png" not in str(scope)


def test_derive_status_blocks_unconfirmed_ai_write() -> None:
    summary = _s3a_summary()
    summary["run_configuration"]["ai_tagging_write_requested"] = True
    summary["run_configuration"]["ai_tagging_confirmation_exact"] = False

    assert s3a_pilot1.derive_status(summary) == "blocked_ai_tagging_requested_without_exact_confirmation"


def test_derive_status_blocks_missing_model_cache_before_target() -> None:
    summary = _s3a_summary()
    summary["model_cache"]["status"] = "blocked"

    assert s3a_pilot1.derive_status(summary) == "blocked_model_cache_missing"


def test_derive_status_blocks_over_cap_before_target() -> None:
    summary = _s3a_summary()
    summary["scope"]["selected_count"] = 0
    summary["scope"]["over_cap_count"] = 1

    assert s3a_pilot1.derive_status(summary) == "blocked_input_over_cap"


def test_derive_status_blocks_cpu_fallback_failure_before_target() -> None:
    summary = _s3a_summary()
    summary["cpu_fallback_validation"]["status"] = "completed_with_item_failures"
    summary["cpu_fallback_validation"]["failed"] = 1

    assert s3a_pilot1.derive_status(summary) == "blocked_cpu_fallback_not_validated"


def test_derive_status_reports_unproven_write_when_delta_zero() -> None:
    summary = _s3a_summary()
    summary["run_configuration"]["ai_tagging_write_requested"] = True
    summary["run_configuration"]["ai_tagging_confirmation_exact"] = True
    summary["directml_ai_tagging"]["dry_run"] = False
    summary["directml_ai_tagging"]["media_tags_count_delta"] = 0
    summary["directml_ai_tagging"]["first_time_media_tag_insertion_proven"] = False

    assert s3a_pilot1.derive_status(summary) == "write_executed_but_first_time_insertion_unproven"


def test_derive_status_accepts_bounded_write_with_first_time_delta() -> None:
    summary = _s3a_summary()
    summary["run_configuration"]["ai_tagging_write_requested"] = True
    summary["run_configuration"]["ai_tagging_confirmation_exact"] = True
    summary["directml_ai_tagging"]["dry_run"] = False
    summary["directml_ai_tagging"]["media_tags_count_after"] = 9
    summary["directml_ai_tagging"]["media_tags_count_delta"] = 9
    summary["directml_ai_tagging"]["first_time_media_tag_insertion_proven"] = True

    assert s3a_pilot1.derive_status(summary) == "target_met_with_bounded_write"


def test_s3a_pilot1_contract_accepts_bounded_dry_run() -> None:
    result = check_phase_contract("s3a_pilot1_new_data_directml_chain_contract_v1", _s3a_summary())

    assert result.passed, result.to_dict()


def test_s3a_pilot1_contract_rejects_unbounded_scope() -> None:
    summary = _s3a_summary()
    summary["run_configuration"]["max_items"] = 6
    summary["scope"]["selected_count"] = 6
    summary["scope"]["over_cap_count"] = 1
    summary["safety"]["max_items_lte_5"] = False

    result = check_phase_contract("s3a_pilot1_new_data_directml_chain_contract_v1", summary)

    codes = _error_codes(result)
    assert "s3a_pilot1_max_items_unbounded" in codes
    assert "s3a_pilot1_input_over_cap" in codes


def test_s3a_pilot1_contract_rejects_write_request_without_blocked_status() -> None:
    summary = _s3a_summary()
    summary["run_configuration"]["ai_tagging_write_requested"] = True
    summary["run_configuration"]["ai_tagging_confirmation_exact"] = False

    result = check_phase_contract("s3a_pilot1_new_data_directml_chain_contract_v1", summary)

    assert "s3a_pilot1_ai_requested_without_exact_confirmation_not_blocked" in _error_codes(result)


def test_s3a_pilot1_contract_rejects_write_target_without_first_time_insert() -> None:
    summary = _s3a_summary()
    summary["pipeline_contract"]["status"] = "target_met_with_bounded_write"
    summary["pipeline_contract"]["claims"] = {"target_met": True, "safe_to_merge": True, "full_chain_complete": False}
    summary["run_configuration"]["ai_tagging_write_requested"] = True
    summary["run_configuration"]["ai_tagging_confirmation_exact"] = True
    summary["directml_ai_tagging"]["dry_run"] = False

    result = check_phase_contract("s3a_pilot1_new_data_directml_chain_contract_v1", summary)

    assert "s3a_pilot1_write_target_without_first_time_media_tags" in _error_codes(result)


def test_s3a_pilot1_contract_rejects_target_when_cpu_fallback_failed() -> None:
    summary = _s3a_summary()
    summary["cpu_fallback_validation"]["status"] = "completed_with_item_failures"
    summary["cpu_fallback_validation"]["failed"] = 1

    result = check_phase_contract("s3a_pilot1_new_data_directml_chain_contract_v1", summary)

    codes = _error_codes(result)
    assert "s3a_pilot1_cpu_fallback_status_invalid" in codes
    assert "s3a_pilot1_cpu_fallback_failed" in codes


def test_write_reports_clears_completion_on_redaction_failure(tmp_path: Path, monkeypatch) -> None:
    summary = _s3a_summary()
    summary["private_leak_for_test"] = r"C:\Users\secret\sample.png"
    s3a_pilot1.apply_pipeline_status(summary, "target_met_dry_run_only")
    monkeypatch.setattr(s3a_pilot1, "SUMMARY_PATH", tmp_path / "summary.json")
    monkeypatch.setattr(s3a_pilot1, "MARKDOWN_PATH", tmp_path / "report.md")

    s3a_pilot1.write_reports(summary)

    payload = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert payload["pipeline_contract"]["status"] == "blocked_public_redaction_failed"
    assert payload["pipeline_contract"]["claims"]["target_met"] is False
    assert payload["pipeline_contract"]["claims"]["safe_to_merge"] is False
    assert payload["public_redaction"]["passed"] is False
