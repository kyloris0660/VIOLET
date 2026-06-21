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
    derive_status,
    parse_media_ids,
    provider_list,
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
    safety = build_safety(False, {"media_tags_count_delta": 1})

    assert safety["dry_run_media_tags_write"] is True
    assert safety["media_tags_write_executed"] is False
    assert safety["production_s3a_execution_enabled"] is False


def test_derive_status_blocks_missing_cpu_fallback() -> None:
    status = derive_status(
        selected_media={"count": 1},
        model_cache={"status": "cached"},
        dry_run={"executed": True, "failed": 0},
        cpu_fallback={"executed": False},
        write_executed=False,
        write_confirmed=False,
    )

    assert status == "blocked_cpu_fallback_not_validated"


def test_derive_status_accepts_dry_run_only_without_write_confirmation() -> None:
    status = derive_status(
        selected_media={"count": 3},
        model_cache={"status": "cached"},
        dry_run={"executed": True, "failed": 0},
        cpu_fallback={"executed": True, "failed": 0},
        write_executed=False,
        write_confirmed=False,
    )

    assert status == "target_met_dry_run_only"
