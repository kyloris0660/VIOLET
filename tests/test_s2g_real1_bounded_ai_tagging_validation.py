"""Focused tests for the S2G-REAL1 bounded validation runner helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_s2g_real1_bounded_ai_tagging_validation import (  # noqa: E402
    CPU_PROVIDER_PREFERENCE,
    build_safety,
    build_write_prerequisites,
    derive_status,
    parse_media_ids,
    provider_list,
    validation_pass_succeeded,
    write_pass_succeeded,
)


def test_parse_media_ids_accepts_commas_spaces_and_semicolons() -> None:
    assert parse_media_ids("1, 2;3 4") == [1, 2, 3, 4]


def test_provider_list_drops_empty_values() -> None:
    assert provider_list("DmlExecutionProvider,, CPUExecutionProvider ") == [
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert provider_list(CPU_PROVIDER_PREFERENCE) == ["CPUExecutionProvider"]


def test_build_safety_marks_dry_run_media_tag_delta() -> None:
    safety = build_safety(
        write_requested=False,
        write_confirmed=False,
        write_executed=False,
        dry_run={"media_tags_count_delta": 1},
        write_prerequisites={"primary_dry_run_success": True, "all_passed": False},
    )

    assert safety["dry_run_media_tags_write"] is True
    assert safety["media_tags_write_executed"] is False
    assert safety["production_s3a_execution_enabled"] is False


def _provider_run(
    *,
    actual_provider: str = "DmlExecutionProvider",
    processed: int = 3,
    selected_media_count: int = 3,
    failed: int = 0,
    status: str = "completed",
    delta: int = 0,
) -> dict:
    return {
        "executed": True,
        "status": status,
        "provider_preference_requested": [actual_provider],
        "selected_media_count": selected_media_count,
        "processed": processed,
        "failed": failed,
        "rollback_error": False,
        "error_state": False,
        "media_tags_count_delta": delta,
        "provider": {"actual_provider": actual_provider},
    }


def _write_prerequisites(all_passed: bool = True) -> dict:
    return {
        "selected_media_count_within_cap": all_passed,
        "model_cache_available": all_passed,
        "primary_dry_run_success": all_passed,
        "primary_provider_evidence_present": all_passed,
        "cpu_fallback_success": all_passed,
        "public_private_scope_clean": all_passed,
        "exact_write_confirmation_present": all_passed,
        "write_executed_after_prerequisites_passed": all_passed,
        "all_passed": all_passed,
    }


def test_derive_status_blocks_missing_cpu_fallback() -> None:
    status = derive_status(
        selected_media={"count": 1},
        model_cache={"status": "cached"},
        dry_run=_provider_run(processed=1, selected_media_count=1),
        cpu_fallback={"executed": False},
        write_run={"executed": False},
        write_requested=False,
        write_confirmed=False,
        local_files_only=True,
        write_prerequisites=_write_prerequisites(False),
    )

    assert status == "blocked_cpu_fallback_not_validated"


def test_derive_status_accepts_dry_run_only_without_write_confirmation() -> None:
    status = derive_status(
        selected_media={"count": 3},
        model_cache={"status": "cached"},
        dry_run=_provider_run(),
        cpu_fallback=_provider_run(actual_provider=CPU_PROVIDER_PREFERENCE, processed=1, selected_media_count=1),
        write_run={"executed": False},
        write_requested=False,
        write_confirmed=False,
        local_files_only=True,
        write_prerequisites=_write_prerequisites(False),
    )

    assert status == "target_met_dry_run_only"


def test_derive_status_blocks_mistyped_write_request() -> None:
    status = derive_status(
        selected_media={"count": 3, "max_items": 3},
        model_cache={"status": "cached"},
        dry_run=_provider_run(),
        cpu_fallback=_provider_run(actual_provider=CPU_PROVIDER_PREFERENCE, processed=1, selected_media_count=1),
        write_run={"executed": False},
        write_requested=True,
        write_confirmed=False,
        local_files_only=True,
        write_prerequisites=_write_prerequisites(False),
    )

    assert status == "blocked_write_requested_without_exact_confirmation"


def test_derive_status_blocks_failed_bounded_write() -> None:
    status = derive_status(
        selected_media={"count": 3, "max_items": 3},
        model_cache={"status": "cached"},
        dry_run=_provider_run(),
        cpu_fallback=_provider_run(actual_provider=CPU_PROVIDER_PREFERENCE, processed=1, selected_media_count=1),
        write_run=_provider_run(status="completed_with_item_failures", failed=1, delta=0),
        write_requested=True,
        write_confirmed=True,
        local_files_only=True,
        write_prerequisites=_write_prerequisites(True),
    )

    assert status == "blocked_write_item_failures"


def test_derive_status_accepts_successful_bounded_write() -> None:
    status = derive_status(
        selected_media={"count": 3, "max_items": 3},
        model_cache={"status": "cached"},
        dry_run=_provider_run(),
        cpu_fallback=_provider_run(actual_provider=CPU_PROVIDER_PREFERENCE, processed=1, selected_media_count=1),
        write_run=_provider_run(delta=7),
        write_requested=True,
        write_confirmed=True,
        local_files_only=True,
        write_prerequisites=_write_prerequisites(True),
    )

    assert status == "target_met_with_bounded_write"


def test_derive_status_blocks_model_download_allowed() -> None:
    status = derive_status(
        selected_media={"count": 3},
        model_cache={"status": "cached"},
        dry_run=_provider_run(),
        cpu_fallback=_provider_run(actual_provider=CPU_PROVIDER_PREFERENCE, processed=1, selected_media_count=1),
        write_run={"executed": False},
        write_requested=False,
        write_confirmed=False,
        local_files_only=False,
        write_prerequisites=_write_prerequisites(False),
    )

    assert status == "blocked_model_download_allowed"


def test_write_prerequisites_require_cpu_fallback_before_write() -> None:
    prerequisites = build_write_prerequisites(
        selected_media={"count": 3, "max_items": 3, "no_full_library_fallback": True, "private_locator_values_recorded": False},
        model_cache={"status": "cached"},
        dry_run=_provider_run(),
        cpu_fallback={"executed": False},
        local_files_only=True,
        operator_confirmation_exact=True,
    )

    assert prerequisites["primary_dry_run_success"] is True
    assert prerequisites["cpu_fallback_success"] is False
    assert prerequisites["all_passed"] is False


def test_validation_pass_success_helpers_reject_error_states() -> None:
    run = _provider_run()
    assert validation_pass_succeeded(run, selected_count=3, require_provider=True, require_no_writes=True)
    assert write_pass_succeeded({**run, "media_tags_count_delta": 4}, selected_count=3)

    failed_write = {**run, "status": "completed_with_item_failures", "failed": 1}
    assert write_pass_succeeded(failed_write, selected_count=3) is False
