"""Focused tests for S3A-PROD2/S3B-D1 runner, policy, and contract."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "run_s3a_prod2_bounded_operator_sync_scaleup.py"

_spec = importlib.util.spec_from_file_location("run_s3a_prod2_bounded_operator_sync_scaleup", SCRIPT_PATH)
assert _spec and _spec.loader
s3a_prod2 = importlib.util.module_from_spec(_spec)
sys.modules["run_s3a_prod2_bounded_operator_sync_scaleup"] = s3a_prod2
_spec.loader.exec_module(s3a_prod2)

from backend.app.services.s3b_unattended_sync_policy import (  # noqa: E402
    build_disabled_scaffold,
    evaluate_source_file,
)
from backend.app.services.job_control import (  # noqa: E402
    build_ai_tagging_load_control_config,
    select_onnx_provider,
)
from scripts.phase_contracts import check_phase_contract  # noqa: E402
from scripts.phase_contracts import contract_checks  # noqa: E402


class FakeS3BSettings:
    S3B_UNATTENDED_SYNC_ENABLED = False
    S3B_SCHEDULED_SYNC_ENABLED = False
    S3B_SYNC_MAX_ITEMS = 0
    S3B_SYNC_SOURCE_ROOTS: list[Path] = []
    S3B_REQUIRE_OPERATOR_CONFIRMATION = True
    S3B_DRY_RUN_ONLY = True
    S3B_SYNC_MIN_STABLE_AGE_SECONDS = 60
    S3B_SYNC_STABILITY_WAIT_SECONDS = 0.25


def _provider(actual: str = "DmlExecutionProvider", requested: list[str] | None = None) -> dict:
    requested = requested or ["DmlExecutionProvider"]
    return {
        "requested_provider_preference": requested,
        "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "actual_provider": actual,
        "actual_onnx_provider_loaded": actual,
        "loaded_providers": [actual, "CPUExecutionProvider"] if actual != "CPUExecutionProvider" else ["CPUExecutionProvider"],
        "fallback_occurred": False,
        "fallback_reason": None,
        "provider_load_errors": [],
    }


def _base_ai(
    *,
    dry_run: bool = True,
    actual: str = "DmlExecutionProvider",
    requested: list[str] | None = None,
) -> dict:
    requested = requested or ["DmlExecutionProvider"]
    delta = 0 if dry_run else 12
    return {
        "reported": True,
        "executed": True,
        "status": "completed",
        "dry_run": dry_run,
        "local_files_only": True,
        "provider_preference_requested": requested,
        "selected_media_count": 3,
        "processed": 3,
        "tags_added": 12,
        "suggestions_added": 3,
        "skipped_locked": 0,
        "ignored_low_confidence": 10,
        "failed": 0,
        "media_tags_count_before": 0,
        "media_tags_count_after": delta,
        "media_tags_count_delta": delta,
        "media_with_ai_tags_before": 0,
        "media_with_ai_tags_after": 3 if not dry_run else 0,
        "media_with_ai_tags_delta": 3 if not dry_run else 0,
        "first_time_media_tag_insertion_proven": not dry_run,
        "first_time_media_tag_insertion_count": 3 if not dry_run else 0,
        "no_media_tags_writes": dry_run,
        "provider": _provider(actual, requested),
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


def _s3a_prod2_summary(*, write: bool = False) -> dict:
    directml = _base_ai(dry_run=not write)
    probe = {
        **_base_ai(dry_run=True, requested=["DmlExecutionProvider", "CPUExecutionProvider"]),
        "label": "directml_prewrite_probe",
        "executed": write,
        "status": "completed" if write else "not_required_preflight_only",
        "selected_media_count": 1 if write else 0,
        "processed": 1 if write else 0,
        "tags_added": 0,
        "suggestions_added": 0,
        "first_time_media_tag_insertion_count": 0,
        "first_time_media_tag_insertion_proven": False,
    }
    provider_gate = {
        "reported": True,
        "write_requested": write,
        "exact_confirmation_present": write,
        "write_preconditions_passed": write,
        "requested_provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "requires_directml_provider": True,
        "provider_preference_includes_directml": True,
        "provider_preference_includes_cpu_fallback": True,
        "provider_preference_dml_then_cpu": True,
        "directml_available": True,
        "cpu_fallback_available": True,
        "probe_executed": write,
        "probe_status": "completed" if write else "not_required_preflight_only",
        "probe_failed": 0,
        "probe_rollback_error": False,
        "probe_error_state": False,
        "probe_actual_provider": "DmlExecutionProvider" if write else None,
        "passed": write,
        "write_allowed": write,
        "blockers": [] if write else ["write_not_requested"],
    }
    cpu = {
        **_base_ai(dry_run=True, actual="CPUExecutionProvider"),
        "label": "cpu_fallback_dry_run",
        "provider_preference_requested": ["CPUExecutionProvider"],
        "provider": _provider("CPUExecutionProvider", ["CPUExecutionProvider"]),
        "selected_media_count": 1,
        "processed": 1,
    }
    import_reuse = {
        "reported": True,
        "executed": write,
        "write_requested": write,
        "exact_confirmation_present": write,
        "write_preconditions_passed": write,
        "write_blockers": [],
        "status": "completed",
        "files_discovered": 3,
        "files_supported": 3,
        "imported_count": 3 if write else 0,
        "reused_count": 0 if write else 3,
        "would_import_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "downstream_media_count": 3,
        "no_full_library_fallback": True,
        "source_icloud_mutation": False,
        "app_managed_storage_writes": 3 if write else 0,
        "private_locator_values_recorded": False,
        "public_path_redaction": "paths_and_filenames_redacted",
    }
    summary = {
        "phase": "S3A-PROD2/S3B-D1",
        "pipeline_contract": {
            "contract_id": "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1",
            "status": "target_met_with_bounded_write" if write else "target_met_dry_run_only",
            "claims": {"target_met": True, "safe_to_merge": True, "full_chain_complete": True},
        },
        "run_configuration": {
            "input_mode": "input_path",
            "max_items": 5,
            "local_files_only": True,
            "model_download_allowed": False,
            "write_requested": write,
            "operator_confirmation_exact": write,
            "provider_preference_requested": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "actual_write_provider_preference": ["DmlExecutionProvider"],
            "cpu_fallback_write_allowed": False,
            "provider_fallback_disabled_for_actual_write": True,
            "s3a_production_automation_enabled": False,
            "unattended_s3b_enabled": False,
            "scheduled_s3b_enabled": False,
            "no_full_library_fallback": True,
            "min_stable_age_seconds": 60,
            "stability_wait_seconds": 0.25,
        },
        "scope": {
            "selected_count": 3,
            "max_items": 5,
            "over_cap_count": 0,
            "missing_input_count": 0,
            "explicit_input_path_supplied": True,
            "no_full_library_fallback": True,
            "protected_input_gate": {
                "reported": True,
                "passed": True,
                "blocked_count": 0,
                "paths_redacted": True,
            },
            "private_locator_values_recorded": False,
        },
        "protected_input_gate": {
            "reported": True,
            "passed": True,
            "blocked_count": 0,
            "paths_redacted": True,
        },
        "source_file_preflight": {
            "reported": True,
            "eligible_count": 3,
            "skipped_count": 0,
            "failed_count": 0,
            "cloud_placeholder_skipped": 0,
            "paths_redacted": True,
        },
        "model_cache": {"local_files_only": True, "model_download_allowed": False, "status": "cached"},
        "provider_availability": {
            "reported": True,
            "directml_available": True,
            "cpu_available": True,
            "cpu_fallback_available": True,
        },
        "job_concurrency": {
            "reported": True,
            "no_concurrent_import_or_tagging_jobs": True,
            "background_job_started_by_runner": False,
        },
        "write_preconditions": {
            "reported": True,
            "passed": write,
            "blockers": [] if write else ["write_not_requested"],
            "exact_confirmation_present": write,
            "local_files_only": True,
            "model_cache_cached": True,
            "directml_provider_available": True,
            "provider_preference_dml_then_cpu": True,
            "protected_input_gate_passed": True,
            "protected_input_blocked_count": 0,
            "cpu_fallback_available": True,
            "s3b_disabled_state_passed": True,
            "no_concurrent_import_or_tagging_jobs": True,
        },
        "write_provider_policy": {
            "reported": True,
            "prewrite_probe_provider_preference": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "actual_write_provider_preference": ["DmlExecutionProvider"],
            "actual_write_requires_directml_only": True,
            "provider_fallback_disabled_for_actual_write": True,
            "cpu_fallback_write_allowed": False,
            "cpu_fallback_validation_dry_run_only": True,
            "no_cpu_fallback_write_path": True,
        },
        "write_window_protection": {
            "reported": True,
            "mode": "immediate_recheck_no_durable_lock",
            "durable_lock_held": False,
            "durable_lock_deferred": True,
            "write_requested": write,
            "exact_confirmation_present": write,
            "write_preconditions_passed": write,
            "write_window_rechecked": write,
            "no_concurrent_import_or_tagging_jobs": write,
            "import_recheck": {
                "reported": True,
                "stage": "before_import_write",
                "executed": write,
                "status": "passed" if write else "not_run",
                "passed": write,
                "no_concurrent_import_or_tagging_jobs": write,
            },
            "ai_write_recheck": {
                "reported": True,
                "stage": "before_ai_write",
                "executed": write,
                "status": "passed" if write else "not_run",
                "passed": write,
                "no_concurrent_import_or_tagging_jobs": write,
            },
            "blockers": [],
        },
        "import_reuse": import_reuse,
        "classification": {
            "reported": True,
            "executed": True,
            "dry_run": True,
            "classified_count": 3,
            "reused_classification_count": 0,
            "failed_count": 0,
            "content_class_distribution": {"unknown": 3},
        },
        "directml_provider_probe": probe,
        "provider_write_gate": provider_gate,
        "directml_ai_tagging": directml,
        "cpu_fallback_validation": cpu,
        "localization": {
            "reported": True,
            "attempted": True,
            "reused_translations": 2,
            "new_translations": 0,
            "missing_or_deferred": 1,
            "failed": 0,
            "llm_external_provider_used": False,
            "external_provider_used": False,
        },
        "failure_budget": {
            "passed": True,
            "import_failed_count": 0,
            "classification_failed_count": 0,
            "ai_tagging_failed": 0,
            "cpu_fallback_failed": 0,
            "public_redaction_finding_count": 0,
        },
        "load_control_observations": {
            "effective_batch_size": 2,
            "cpu_intra_op_threads": 4,
            "cpu_inter_op_threads": 1,
            "preprocess_workers": 2,
            "max_concurrent_ai_jobs": 1,
        },
        "s3a_boundary": {
            "operator_triggered_only": True,
            "production_execution_enabled": False,
            "unattended_enabled": False,
            "scheduled_automation_enabled": False,
            "broad_production_sync_enabled": False,
        },
        "s3b_disabled_scaffold": build_disabled_scaffold(FakeS3BSettings),
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-prod2-test.md",
        },
        "public_redaction": {"passed": True, "finding_count": 0},
    }
    summary["safety"] = s3a_prod2.build_safety(summary)
    return summary


def _write_public_report(monkeypatch, tmp_path: Path, summary: dict) -> None:
    report = tmp_path / "docs" / "reports" / "s3a-prod2-test.md"
    report.parent.mkdir(parents=True)
    report.write_text("# S3A PROD2 test report\n\nPublic aggregate report.\n", encoding="utf-8")
    monkeypatch.setattr(contract_checks, "CONTRACT_ROOT", tmp_path)
    summary["public_reports"]["markdown_report_path"] = "docs/reports/s3a-prod2-test.md"


def _error_codes(result) -> set[str]:
    return {finding.code for finding in result.errors}


def test_s3b_disabled_scaffold_defaults_are_off() -> None:
    scaffold = build_disabled_scaffold(FakeS3BSettings)

    assert scaffold["status"] == "disabled_scaffold_ready"
    assert scaffold["policy"]["unattended_enabled"] is False
    assert scaffold["policy"]["scheduled_enabled"] is False
    assert scaffold["scheduler_started"] is False
    assert scaffold["automatic_writes_started"] is False


def test_evaluate_source_file_skips_zero_byte(tmp_path: Path) -> None:
    source = tmp_path / "empty.png"
    source.write_bytes(b"")

    decision = evaluate_source_file(
        source,
        safe_label="item_001",
        min_stable_age_seconds=0,
        stability_wait_seconds=0,
    )

    assert decision.eligible is False
    assert decision.reason == "zero_byte_file"
    assert str(tmp_path) not in str(decision.to_public_dict())


def test_discover_input_candidates_blocks_over_cap_without_truncation(tmp_path: Path) -> None:
    for index in range(21):
        (tmp_path / f"sample_{index}.png").write_bytes(b"x")

    result = s3a_prod2.discover_input_candidates([str(tmp_path)], max_items=20)

    assert result.candidates == []
    assert result.scope["selected_count"] == 0
    assert result.scope["over_cap_count"] == 1
    assert result.scope["over_cap_blocked_before_selection"] is True
    assert "sample_0.png" not in str(result.scope)


def test_discover_input_candidates_reports_missing_input_as_failure(tmp_path: Path) -> None:
    valid = tmp_path / "valid.png"
    valid.write_bytes(b"x")
    missing = tmp_path / "missing.png"

    result = s3a_prod2.discover_input_candidates(
        [str(valid), str(missing)],
        max_items=5,
        min_stable_age_seconds=0,
        stability_wait_seconds=0,
    )

    assert result.scope["missing_input_count"] == 1
    assert result.source_file_preflight["failed_count"] == 1
    assert result.source_file_preflight["reason_counts"]["source_missing"] == 1
    assert "missing.png" not in str(result.source_file_preflight)


def test_discover_input_candidates_uses_preflight_size_after_stat_race(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "race.png"
    source.write_bytes(b"x")

    def fake_evaluate(path, **kwargs):
        Path(path).unlink()
        return s3a_prod2.SourceFileDecision(
            safe_label=kwargs["safe_label"],
            eligible=True,
            reason="local_readable_stable_supported_file",
            source_state="available",
            size_bytes=7,
            supported_extension=True,
            stable_size_mtime=True,
        )

    monkeypatch.setattr(s3a_prod2, "evaluate_source_file", fake_evaluate)

    result = s3a_prod2.discover_input_candidates(
        [str(source)],
        max_items=5,
        min_stable_age_seconds=0,
        stability_wait_seconds=0,
    )

    assert result.candidates[0].size_bytes == 7


def test_discover_input_candidates_blocks_original_dir_before_source_preflight(monkeypatch, tmp_path: Path) -> None:
    original_dir = tmp_path / "storage" / "media" / "original"
    original_dir.mkdir(parents=True)
    source = original_dir / "protected.png"
    source.write_bytes(b"x")

    monkeypatch.setattr(
        s3a_prod2,
        "protected_input_roots",
        lambda: [("settings.ORIGINAL_DIR", original_dir)],
    )

    def fail_if_called(*args, **kwargs):
        pytest.fail("protected input must block before source preflight")

    monkeypatch.setattr(s3a_prod2, "evaluate_source_file", fail_if_called)

    result = s3a_prod2.discover_input_candidates(
        [str(source)],
        max_items=5,
        min_stable_age_seconds=0,
        stability_wait_seconds=0,
    )

    assert result.candidates == []
    assert result.scope["protected_input_gate"]["passed"] is False
    assert result.scope["protected_input_gate"]["blocked_count"] == 1
    assert result.source_file_preflight["evaluated_count"] == 0
    assert "protected.png" not in str(result.scope)


def test_discover_input_candidates_blocks_storage_root_before_import(monkeypatch, tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    source = storage_root / "protected.jpg"
    source.write_bytes(b"x")

    monkeypatch.setattr(
        s3a_prod2,
        "protected_input_roots",
        lambda: [("settings.STORAGE_ROOT", storage_root)],
    )

    result = s3a_prod2.discover_input_candidates(
        [str(source)],
        max_items=5,
        min_stable_age_seconds=0,
        stability_wait_seconds=0,
    )

    assert result.candidates == []
    assert result.scope["protected_input_gate"]["passed"] is False
    assert result.scope["protected_input_gate"]["paths_redacted"] is True
    assert result.scope["selected_count"] == 0
    assert "protected.jpg" not in str(result.scope)


def test_discover_input_candidates_blocks_repo_data_and_media_without_path_leak() -> None:
    blocked_inputs = [
        str(ROOT / "data" / "synthetic_private_input.png"),
        str(ROOT / "media" / "synthetic_private_input.png"),
    ]

    result = s3a_prod2.discover_input_candidates(
        blocked_inputs,
        max_items=5,
        min_stable_age_seconds=0,
        stability_wait_seconds=0,
    )

    gate = result.scope["protected_input_gate"]
    assert result.candidates == []
    assert gate["reported"] is True
    assert gate["passed"] is False
    assert gate["blocked_count"] == 2
    assert gate["paths_redacted"] is True
    assert "synthetic_private_input.png" not in str(result.scope)


def test_derive_status_blocks_unconfirmed_write() -> None:
    summary = _s3a_prod2_summary(write=False)
    summary["run_configuration"]["write_requested"] = True
    summary["run_configuration"]["operator_confirmation_exact"] = False

    assert s3a_prod2.derive_status(summary) == "blocked_write_requested_without_exact_confirmation"


def test_derive_status_blocks_cpu_only_provider_before_write() -> None:
    summary = _s3a_prod2_summary(write=True)
    summary["run_configuration"]["provider_preference_requested"] = ["CPUExecutionProvider"]
    summary["write_preconditions"]["provider_preference_dml_then_cpu"] = False

    assert s3a_prod2.derive_status(summary) == "blocked_provider_preference_invalid"


def test_provider_selection_can_disable_cpu_fallback_for_actual_write() -> None:
    selection = select_onnx_provider(
        ["DmlExecutionProvider"],
        ["CPUExecutionProvider"],
        allow_fallback=False,
    )

    assert selection.selected_provider is None
    assert selection.candidate_provider_order == ()
    assert selection.fallback_occurred is True


def test_load_control_config_honors_provider_fallback_disable() -> None:
    settings = SimpleNamespace(
        AI_TAGGING_BATCH_SIZE=2,
        AI_TAGGING_BATCH_MAX_ITEMS=5,
        AI_TAGGING_MAX_CONCURRENT_JOBS=1,
        AI_TAGGING_PREPROCESS_WORKERS=2,
        AI_TAGGING_PROVIDER_PREFERENCE=("DmlExecutionProvider",),
        AI_TAGGING_ALLOW_PROVIDER_FALLBACK=False,
        AI_TAGGING_CPU_INTRA_OP_THREADS=4,
        AI_TAGGING_CPU_INTER_OP_THREADS=1,
        AI_TAGGING_EXECUTION_MODE="ORT_SEQUENTIAL",
        AI_TAGGING_PROCESS_PRIORITY="below_normal",
    )

    config = build_ai_tagging_load_control_config(settings)

    assert config.provider_preference == ("DmlExecutionProvider",)
    assert config.allow_provider_fallback is False


def test_derive_status_blocks_failed_ai_write_window_recheck() -> None:
    summary = _s3a_prod2_summary(write=True)
    summary["write_window_protection"]["ai_write_recheck"]["passed"] = False
    summary["write_window_protection"]["ai_write_recheck"]["status"] = "blocked_concurrent_job_active"
    summary["write_window_protection"]["no_concurrent_import_or_tagging_jobs"] = False
    summary["write_window_protection"]["blockers"] = ["ai_write_concurrency_recheck_not_passed"]

    assert s3a_prod2.derive_status(summary) == "blocked_write_window_concurrency"


def test_contract_accepts_bounded_write_summary(monkeypatch, tmp_path: Path) -> None:
    summary = _s3a_prod2_summary(write=True)
    _write_public_report(monkeypatch, tmp_path, summary)

    result = check_phase_contract(
        "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1",
        summary,
    )

    assert result.passed, result.to_dict()


def test_contract_rejects_cpu_only_write_provider(monkeypatch, tmp_path: Path) -> None:
    summary = _s3a_prod2_summary(write=True)
    summary["directml_ai_tagging"]["provider_preference_requested"] = ["CPUExecutionProvider"]
    summary["directml_ai_tagging"]["provider"] = _provider("CPUExecutionProvider", ["CPUExecutionProvider"])
    summary["write_provider_policy"]["actual_write_provider_preference"] = ["CPUExecutionProvider"]
    summary["write_provider_policy"]["cpu_fallback_write_allowed"] = True
    summary["write_provider_policy"]["provider_fallback_disabled_for_actual_write"] = False
    _write_public_report(monkeypatch, tmp_path, summary)

    result = check_phase_contract(
        "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1",
        summary,
    )

    codes = _error_codes(result)
    assert "s3a_prod2_ai_write_provider_preference_not_dml_only" in codes
    assert "s3a_prod2_fallback_write_provider_not_allowed" in codes
    assert "s3a_prod2_cpu_fallback_write_path_allowed" in codes


def test_contract_rejects_dml_cpu_write_fallback_and_requires_zero_delta_on_block(monkeypatch, tmp_path: Path) -> None:
    summary = _s3a_prod2_summary(write=True)
    summary["directml_ai_tagging"]["provider_preference_requested"] = [
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ]
    summary["directml_ai_tagging"]["provider"] = _provider(
        "CPUExecutionProvider",
        ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    summary["directml_ai_tagging"]["media_tags_count_before"] = 0
    summary["directml_ai_tagging"]["media_tags_count_after"] = 0
    summary["directml_ai_tagging"]["media_tags_count_delta"] = 0
    summary["directml_ai_tagging"]["first_time_media_tag_insertion_count"] = 0
    _write_public_report(monkeypatch, tmp_path, summary)

    result = check_phase_contract(
        "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1",
        summary,
    )

    codes = _error_codes(result)
    assert summary["directml_ai_tagging"]["media_tags_count_delta"] == 0
    assert "s3a_prod2_ai_write_provider_preference_not_dml_only" in codes
    assert "s3a_prod2_fallback_write_provider_not_allowed" in codes


def test_contract_rejects_inconsistent_media_tags_delta(monkeypatch, tmp_path: Path) -> None:
    summary = _s3a_prod2_summary(write=True)
    summary["directml_ai_tagging"]["media_tags_count_before"] = 0
    summary["directml_ai_tagging"]["media_tags_count_after"] = 0
    summary["directml_ai_tagging"]["media_tags_count_delta"] = 9
    _write_public_report(monkeypatch, tmp_path, summary)

    result = check_phase_contract(
        "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1",
        summary,
    )

    assert "s3a_prod2_media_tags_delta_inconsistent" in _error_codes(result)


def test_contract_rejects_missing_write_window_recheck(monkeypatch, tmp_path: Path) -> None:
    summary = _s3a_prod2_summary(write=True)
    summary["write_window_protection"]["write_window_rechecked"] = False
    summary["write_window_protection"]["ai_write_recheck"]["passed"] = False
    summary["write_window_protection"]["no_concurrent_import_or_tagging_jobs"] = False
    _write_public_report(monkeypatch, tmp_path, summary)

    result = check_phase_contract(
        "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1",
        summary,
    )

    codes = _error_codes(result)
    assert "s3a_prod2_write_window_not_protected" in codes
    assert "s3a_prod2_write_without_window_recheck" in codes


def test_contract_rejects_ai_write_when_write_preconditions_failed(monkeypatch, tmp_path: Path) -> None:
    summary = _s3a_prod2_summary(write=True)
    summary["write_preconditions"]["passed"] = False
    summary["write_preconditions"]["blockers"] = ["provider_preference_not_dml_then_cpu"]
    summary["import_reuse"]["executed"] = False
    summary["import_reuse"]["reused_count"] = 3
    summary["directml_ai_tagging"]["dry_run"] = False
    summary["directml_ai_tagging"]["executed"] = True
    _write_public_report(monkeypatch, tmp_path, summary)

    result = check_phase_contract(
        "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1",
        summary,
    )

    assert "s3a_prod2_ai_write_without_write_preconditions" in _error_codes(result)


def test_contract_rejects_write_target_without_clean_directml_probe(monkeypatch, tmp_path: Path) -> None:
    summary = _s3a_prod2_summary(write=True)
    summary["provider_write_gate"]["probe_status"] = "failed"
    summary["provider_write_gate"]["passed"] = False
    _write_public_report(monkeypatch, tmp_path, summary)

    result = check_phase_contract(
        "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1",
        summary,
    )

    assert "s3a_prod2_write_target_without_provider_write_gate" in _error_codes(result)
    assert "s3a_prod2_write_target_without_clean_directml_probe" in _error_codes(result)


def test_contract_rejects_enabled_s3b(monkeypatch, tmp_path: Path) -> None:
    summary = _s3a_prod2_summary(write=True)
    summary["s3b_disabled_scaffold"]["policy"]["unattended_enabled"] = True
    summary["s3b_disabled_scaffold"]["status"] = "blocked_policy_enabled"
    summary["safety"] = s3a_prod2.build_safety(summary)
    _write_public_report(monkeypatch, tmp_path, summary)

    result = check_phase_contract(
        "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1",
        summary,
    )

    codes = _error_codes(result)
    assert "s3a_prod2_forbidden_safety_flag" in codes
    assert "s3a_prod2_s3b_scaffold_status_invalid" in codes
