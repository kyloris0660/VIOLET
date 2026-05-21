"""Tests for Phase 3.8c medium pilot preflight dry-run helpers."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "plan_phase38c_medium_pilot_preflight.py"

_spec = importlib.util.spec_from_file_location("plan_phase38c_medium_pilot_preflight", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)


@dataclass(frozen=True)
class _MutationSnapshot:
    media: int
    media_tags: int
    ai_jobs: int
    classification_jobs: int
    translation_jobs: int


def _write_image(path: Path, *, size: int = 2048, mtime: int = 1_700_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8" + b"\x00" * size)
    os.utime(path, (mtime, mtime))


def _write_previous_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "row_id",
        "source_path",
        "proposed_target_path",
        "extension",
        "size_bytes",
        "selection_reason",
        "duplicate_key",
        "exclusion_reason",
        "placeholder_flag",
        "stat_error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_temporal_stratified_selection_spans_all_time_buckets(tmp_path: Path):
    source = tmp_path / "source"
    previous = tmp_path / "previous.csv"
    target = tmp_path / "target"
    _write_previous_manifest(previous, [])
    for idx in range(30):
        _write_image(source / f"img_{idx:03d}.jpg", mtime=1_600_000_000 + idx * 86_400)

    rows, summary = _module.build_candidate_selection(
        source_root=source,
        previous_manifest=previous,
        target_root=target,
        planned_new_count=12,
        temporal_buckets=6,
        seed=38,
        timestamp_unknown_cap=0,
    )

    selected = [row for row in rows if row["selection_reason"] == "new_candidate"]
    selected_buckets = {row["temporal_bucket"] for row in selected}
    assert len(selected) == 12
    assert selected_buckets == {"b01", "b02", "b03", "b04", "b05", "b06"}
    assert summary["temporal_diversity_check"]["passed"] is True
    assert summary["strategy"] == "filesystem_mtime_quantile_stratified"


def test_candidate_selection_excludes_prior_manifest_paths_and_duplicate_keys(tmp_path: Path):
    source = tmp_path / "source"
    imported = source / "already.jpg"
    duplicate = source / "duplicate.jpg"
    fresh = source / "fresh.png"
    _write_image(imported, size=3000, mtime=1_600_000_000)
    _write_image(duplicate, size=4000, mtime=1_600_100_000)
    _write_image(fresh, size=5000, mtime=1_600_200_000)
    previous = tmp_path / "previous.csv"
    _write_previous_manifest(
        previous,
        [
            {
                "row_id": "1",
                "source_path": str(imported),
                "proposed_target_path": str(tmp_path / "old" / "already.jpg"),
                "extension": ".jpg",
                "size_bytes": "3002",
                "selection_reason": "new_candidate",
                "duplicate_key": "",
                "exclusion_reason": "",
                "placeholder_flag": "False",
                "stat_error": "False",
            },
            {
                "row_id": "2",
                "source_path": str(tmp_path / "elsewhere" / "duplicate.jpg"),
                "proposed_target_path": str(tmp_path / "old" / "duplicate.jpg"),
                "extension": ".jpg",
                "size_bytes": "4002",
                "selection_reason": "new_candidate",
                "duplicate_key": "",
                "exclusion_reason": "",
                "placeholder_flag": "False",
                "stat_error": "False",
            },
        ],
    )

    rows, summary = _module.build_candidate_selection(
        source_root=source,
        previous_manifest=previous,
        target_root=tmp_path / "target",
        planned_new_count=10,
        temporal_buckets=4,
        seed=1,
        timestamp_unknown_cap=0,
    )

    selected_sources = [Path(row["source_path"]).name for row in rows if row["selection_reason"] == "new_candidate"]
    assert selected_sources == ["fresh.png"]
    assert summary["exclusion_reason_counts"]["already_imported_prior_manifest"] == 1
    assert summary["exclusion_reason_counts"]["duplicate_prior_manifest_key"] == 1
    assert summary["selected_total"] == 1


def test_timestamp_unknown_candidates_are_capped_and_reported():
    candidates = [
        _module.CandidateEntry(
            path=Path(f"C:/src/known_{idx}.jpg"),
            relative_key=f"known_{idx}.jpg",
            filename=f"known_{idx}.jpg",
            extension=".jpg",
            size_bytes=2000,
            mtime_epoch=1_600_000_000 + idx,
            timestamp_source="filesystem_mtime",
        )
        for idx in range(8)
    ]
    candidates.extend(
        _module.CandidateEntry(
            path=Path(f"C:/src/unknown_{idx}.jpg"),
            relative_key=f"unknown_{idx}.jpg",
            filename=f"unknown_{idx}.jpg",
            extension=".jpg",
            size_bytes=2000,
            mtime_epoch=None,
            timestamp_source="timestamp_unknown",
        )
        for idx in range(5)
    )

    selected, summary = _module.select_temporal_stratified_candidates(
        candidates,
        planned_new_count=8,
        bucket_count=4,
        seed=2,
        timestamp_unknown_cap=2,
    )

    assert summary["timestamp_unknown_count"] == 5
    assert summary["timestamp_unknown_selected"] == 2
    assert sum(1 for entry in selected if entry.temporal_bucket == "timestamp_unknown") == 2
    assert summary["selected_count_by_bucket"]["timestamp_unknown"] == 2


def test_public_report_is_privacy_safe_and_local_manifest_keeps_paths(tmp_path: Path):
    source = tmp_path / "source"
    previous = tmp_path / "previous.csv"
    target = tmp_path / "target"
    manifest = tmp_path / "local_manifest.csv"
    _write_previous_manifest(previous, [])
    for idx in range(6):
        _write_image(source / f"img_{idx}.jpg", mtime=1_600_000_000 + idx)

    rows, summary = _module.build_candidate_selection(
        source_root=source,
        previous_manifest=previous,
        target_root=target,
        planned_new_count=3,
        temporal_buckets=3,
        seed=1,
        timestamp_unknown_cap=0,
    )
    _module.write_candidate_manifest(manifest, rows)

    content = manifest.read_text(encoding="utf-8")
    assert str(source) in content
    public = {"candidate_selection": summary, "local_artifacts": {"candidate_manifest": ".local_manifests/x.csv"}}
    safe = _module.sanitize_public_obj(public)
    assert _module.find_privacy_leaks(safe) == []
    assert str(source) not in json.dumps(safe)


def test_build_phase38c_report_fails_strict_selected_count_mismatch(tmp_path: Path):
    before = _module.TreeSnapshot(True, 10, 100, 0)
    db_snapshot = _MutationSnapshot(
        media=995,
        media_tags=1,
        ai_jobs=2,
        classification_jobs=3,
        translation_jobs=4,
    )
    workflow_report = {
        "started_at": "2026-05-20T00:00:00+00:00",
        "scope": {"source_label": "violet:custom-source"},
        "identity": {
            "repo": {"branch": "test"},
            "python": {"executable_label": "python.exe", "version": "3.12.0"},
            "database": {"violet_env": "development", "db_name": "blombooru"},
        },
        "counts": {
            "target_media_count": 995,
            "eligible_media_count": 969,
            "ineligible_media_count": 26,
            "content_class_distribution": {"anime": 948, "unknown": 21, "non_anime": 26},
        },
        "legacy_contamination": {"ineligible_ai_associations": 771},
        "workflow_order": ["candidate manifest / candidate selection"],
        "validation_contract": {"endpoint_sweeps": [], "smoke_checks": []},
        "warnings": [],
    }
    candidate_summary = {
        "selected_total": 2,
        "planned_new_count": 5,
        "candidate_total": 10,
        "excluded_total": 8,
        "approximate_byte_estimate": 4000,
        "temporal_diversity_check": {"passed": True},
        "extension_distribution": {".jpg": 2},
        "exclusion_reason_counts": {},
        "temporal_bucket_count": 2,
        "timestamp_unknown_count": 0,
        "timestamp_unknown_selected": 0,
        "bucket_details": [],
    }

    report = _module.build_phase38c_report(
        candidate_summary=candidate_summary,
        workflow_report=workflow_report,
        db_before=db_snapshot,
        db_after=db_snapshot,
        storage_before={"original": before, "thumbnail": before},
        storage_after={"original": before, "thumbnail": before},
        source_before=before,
        source_after=before,
        staging_before=before,
        staging_after=before,
        candidate_manifest=tmp_path / "manifest.csv",
        future_source_label="violet:medium1000:phase3.8d",
        expected_selected_count=3,
        strict=True,
    )

    assert report["success"] is False
    assert report["status"] == "failed_contract"
    assert report["scope"]["current_source_label"] == "violet:custom-source"
    assert report["scope"]["planned_new_candidate_count"] == 5
    assert report["scope"]["selected_new_candidate_count"] == 2
    assert any("expected_selected_count=3" in item for item in report["contract_failures"])


def test_execute_is_rejected_without_writing_reports(tmp_path: Path):
    out = StringIO()
    err = StringIO()
    report_json = tmp_path / "report.json"
    report_md = tmp_path / "report.md"
    manifest = tmp_path / "manifest.csv"

    code = _module.main(
        [
            "--execute",
            "--source-root",
            str(tmp_path),
            "--target-root",
            str(tmp_path / "target"),
            "--candidate-manifest",
            str(manifest),
            "--report-json",
            str(report_json),
            "--report-md",
            str(report_md),
        ],
        session_factory=lambda: None,
        settings_obj=object(),
        out=out,
        err=err,
    )

    assert code == 2
    assert _module.EXECUTE_REJECTION in err.getvalue()
    assert not report_json.exists()
    assert not report_md.exists()
    assert not manifest.exists()


def test_stage_validator_accepts_phase38c_exclusion_codes(tmp_path: Path):
    stage_path = Path(__file__).resolve().parent.parent / "scripts" / "stage_pilot_files.py"
    spec = importlib.util.spec_from_file_location("stage_pilot_files_for_phase38c", stage_path)
    stage = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(stage)

    target = tmp_path / "target"
    manifest = tmp_path / "manifest.csv"
    fieldnames = [
        "row_id",
        "source_path",
        "proposed_target_path",
        "extension",
        "size_bytes",
        "selection_reason",
        "duplicate_key",
        "exclusion_reason",
        "placeholder_flag",
        "stat_error",
    ]
    rows = [
        {
            "row_id": "1",
            "source_path": str(tmp_path / "old.jpg"),
            "proposed_target_path": "",
            "extension": ".jpg",
            "size_bytes": "2000",
            "selection_reason": "",
            "duplicate_key": "",
            "exclusion_reason": "not_selected_temporal_stratified",
            "placeholder_flag": "False",
            "stat_error": "False",
        },
        {
            "row_id": "2",
            "source_path": str(tmp_path / "dup.jpg"),
            "proposed_target_path": "",
            "extension": ".jpg",
            "size_bytes": "2000",
            "selection_reason": "",
            "duplicate_key": "duplicate_prior_manifest_key",
            "exclusion_reason": "duplicate_prior_manifest_key",
            "placeholder_flag": "False",
            "stat_error": "False",
        },
    ]
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    result = stage.validate_manifest(manifest, target)
    assert result["valid"] is True
    assert result["excluded_rows"] == 2
    assert result["invalid_exclusion_reasons"] == 0
