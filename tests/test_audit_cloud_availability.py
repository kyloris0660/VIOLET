import importlib.util
from pathlib import Path

import pytest

from app.utils.cloud_files import CloudFileState


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "audit_cloud_availability.py"
_spec = importlib.util.spec_from_file_location("audit_cloud_availability", SCRIPT_PATH)
audit = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(audit)


def _row(row_id: int, tmp_path: Path, *, bucket: str, selected: bool = True, ext: str = ".jpg") -> dict[str, str]:
    source = tmp_path / f"src_{row_id}{ext}"
    target = tmp_path / "target" / f"dst_{row_id}{ext}"
    source.write_bytes(b"\xff\xd8" + b"\x00" * 2048)
    return {
        "row_id": str(row_id),
        "source_path": str(source),
        "proposed_target_path": str(target),
        "extension": ext,
        "size_bytes": str(source.stat().st_size),
        "selection_reason": "new_candidate" if selected else "",
        "duplicate_key": "",
        "exclusion_reason": "" if selected else "not_selected_temporal_stratified",
        "placeholder_flag": "False",
        "stat_error": "False",
        "temporal_bucket": bucket,
        "timestamp_source": "filesystem_mtime",
        "modified_time_utc": "2026-01-01T00:00:00+00:00",
    }


def test_metadata_only_audit_does_not_read_probe(monkeypatch, tmp_path: Path):
    rows = [_row(1, tmp_path, bucket="b01"), _row(2, tmp_path, bucket="b02", ext=".png")]

    def fake_state(path):
        return CloudFileState(
            path=str(path),
            supported_platform=True,
            exists=True,
            is_file=True,
            recall_on_data_access=str(path).endswith(".png"),
            likely_cloud_placeholder=str(path).endswith(".png"),
        )

    monkeypatch.setattr(audit, "classify_cloud_file_state", fake_state)
    monkeypatch.setattr(
        audit,
        "read_probe_prefix",
        lambda *args, **kwargs: pytest.fail("read_probe_prefix must not run by default"),
    )

    records, local_records = audit.selected_records(rows, read_probe=False)
    summary = audit.summarize_records(records)

    assert len(records) == 2
    assert len(local_records) == 2
    assert summary["selected_total"] == 2
    assert summary["likely_cloud_placeholder_count"] == 1
    assert summary["risky_count_by_bucket"] == {"b02": 1}
    assert summary["risky_count_by_extension"] == {".png": 1}
    assert summary["copy_gate"]["status"] == "blocked_requires_hydration_policy"
    assert all(record["read_probe"] is None for record in records)


def test_read_probe_is_opt_in(monkeypatch, tmp_path: Path):
    rows = [_row(1, tmp_path, bucket="b01")]
    monkeypatch.setattr(
        audit,
        "classify_cloud_file_state",
        lambda path: CloudFileState(path=str(path), supported_platform=True, exists=True, is_file=True),
    )
    calls = []

    def fake_probe(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True, "bytes_read": 1}

    monkeypatch.setattr(audit, "read_probe_prefix", fake_probe)

    records, _local = audit.selected_records(rows, read_probe=True, read_probe_limit=1)

    assert len(calls) == 1
    assert records[0]["read_probe"] == {"ok": True, "bytes_read": 1}


def test_public_report_is_privacy_safe(monkeypatch, tmp_path: Path):
    rows = [_row(1, tmp_path, bucket="b01")]
    monkeypatch.setattr(
        audit,
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
    records, _local = audit.selected_records(rows)
    summary = audit.summarize_records(records)
    report = audit.build_report(
        manifest_path=tmp_path / "manifest.csv",
        target_root=tmp_path / "target",
        local_details_path=Path(".local_manifests/details.json"),
        records=records,
        summary=summary,
        read_probe_enabled=False,
        backfill_plan=None,
        cleanup_plan=None,
    )

    text = str(report)
    assert str(tmp_path) not in text
    assert "src_1" not in text
    assert report["privacy"]["passed"] is True


def test_backfill_plan_uses_same_bucket_and_preserves_total(tmp_path: Path):
    rows = [
        _row(1, tmp_path, bucket="b01", selected=True),
        _row(2, tmp_path, bucket="b02", selected=True),
        _row(3, tmp_path, bucket="b02", selected=False),
        _row(4, tmp_path, bucket="b01", selected=False),
    ]

    plan = audit.plan_same_bucket_backfill(rows, [2])

    assert plan["mode"] == "dry_run_only"
    assert plan["selected_total_preserved"] is True
    assert plan["replacement_count"] == 1
    assert plan["replacements"][0]["failed_row_id"] == 2
    assert plan["replacements"][0]["replacement_row_id"] == 3
    assert plan["replacements"][0]["bucket"] == "b02"


def test_cleanup_plan_is_dry_run_and_refuses_unsafe_target(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"x")

    plan = audit.build_cleanup_dry_run_plan(
        target,
        protected_roots=[tmp_path],
        execute=True,
        confirm_phrase=audit.CLEANUP_CONFIRM_PHRASE,
    )

    assert plan["actual_delete_performed"] is False
    assert plan["execute_allowed"] is False
    assert plan["file_count"] == 1
    assert plan["unsafe_reasons"]
    assert (target / "copied.jpg").exists()


def test_cleanup_plan_never_allows_execution_in_incident_stage(tmp_path: Path):
    target = tmp_path / "target"
    protected = tmp_path / "protected"
    target.mkdir()
    protected.mkdir()
    (target / "copied.jpg").write_bytes(b"x")

    plan = audit.build_cleanup_dry_run_plan(
        target,
        protected_roots=[protected],
        execute=True,
        confirm_phrase=audit.CLEANUP_CONFIRM_PHRASE,
    )

    assert plan["actual_delete_performed"] is False
    assert plan["execute_allowed"] is False
    assert plan["would_be_eligible_after_separate_approval"] is True
    assert (target / "copied.jpg").exists()
