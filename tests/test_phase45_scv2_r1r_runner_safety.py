"""Focused safety tests for the Phase 4.5 SCV2 R1R runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_phase45_scv2_r1r_full_source_concept_pipeline_replay as r1r_runner


def test_r1r_source_root_splitter_accepts_pipe_separator() -> None:
    assert r1r_runner.split_env_paths(r"C:\source_a|D:\source_b;E:\source_c") == [
        r"C:\source_a",
        r"D:\source_b",
        r"E:\source_c",
    ]


def test_r1r_storage_gate_reads_dotenv_side_effect_free(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dotenv_path = r1r_runner.ROOT / ".env"
    original = dotenv_path.read_text(encoding="utf-8") if dotenv_path.exists() else None
    safe_storage = tmp_path / "r1r-storage"
    source_root = tmp_path / "source-a"
    try:
        dotenv_path.write_text(
            f'VIOLET_STORAGE_ROOT="{safe_storage}"\nLOCAL_LIBRARY_PATHS="{source_root}|{tmp_path / "source-b"}"\n',
            encoding="utf-8",
        )
        monkeypatch.delenv("VIOLET_STORAGE_ROOT", raising=False)
        monkeypatch.delenv("LOCAL_LIBRARY_PATHS", raising=False)

        gate = r1r_runner.storage_root_pre_settings_import_gate()

        assert gate["dotenv_checked_before_settings_import"] is True
        assert gate["checked_before_settings_import"] is True
        assert gate["passed"] is True
        assert safe_storage.exists() is False
    finally:
        if original is None:
            dotenv_path.unlink(missing_ok=True)
        else:
            dotenv_path.write_text(original, encoding="utf-8")


def test_r1r_output_dir_gate_accepts_repo_local_private_artifact_root() -> None:
    output_dir = r1r_runner.ROOT / ".local_manifests" / "r1r-output-dir-test"

    gate = r1r_runner.validate_output_dir_safety(output_dir)

    assert gate["passed"] is True
    assert gate["checked_before_mkdir"] is True


@pytest.mark.parametrize(
    "output_dir",
    [
        r1r_runner.ROOT / "data" / "r1r-output-dir-test",
        r1r_runner.ROOT / "media" / "r1r-output-dir-test",
        Path(r"C:\Users\kyloris\iCloudDrive\r1r-output-dir-test"),
    ],
)
def test_r1r_output_dir_gate_rejects_protected_roots_without_creating(output_dir: Path) -> None:
    gate = r1r_runner.validate_output_dir_safety(output_dir)

    assert gate["passed"] is False
    assert gate["checked_before_mkdir"] is True
    assert output_dir.exists() is False


def test_r1r_redaction_failure_summary_does_not_publish_unsafe_payload() -> None:
    unsafe_summary = {
        "phase": "4.5-SCV2-R1R",
        "phase_slug": "phase-4.5-scv2-r1r-full-source-concept-pipeline-replay",
        "pipeline_contract": {
            "status": "smoke_only_not_route_evidence",
            "claims": {"target_met": False, "full_chain_complete": False},
        },
        "blocked_payload": {
            "source_path": r"C:\Users\kyloris\Pictures\secret.png",
            "api_key": "sk-test-secret",
        },
    }

    redaction = r1r_runner.public_redaction_check(
        unsafe_summary,
        r"blocked at C:\Users\kyloris\Pictures\secret.png with token sk-test-secret",
    )
    safe_summary = r1r_runner.redaction_failure_summary(unsafe_summary, redaction)
    safe_report = r1r_runner.redaction_failure_report(safe_summary)
    published = json.dumps(safe_summary, ensure_ascii=False) + safe_report

    assert redaction["passed"] is False
    assert redaction["findings"] == []
    assert safe_summary["pipeline_contract"]["status"] == "blocked_public_redaction_failed"
    assert safe_summary["pipeline_contract"]["claims"]["target_met"] is False
    assert safe_summary["redaction_failure"]["unsafe_payload_not_published"] is True
    assert r"C:\Users\kyloris" not in published
    assert "sk-test-secret" not in published
