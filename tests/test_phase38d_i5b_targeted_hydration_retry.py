import csv
import importlib.util
import json
import sys
from pathlib import Path

from app.services.source_ingestion_gate import SourceIngestionGateResult
from app.utils.cloud_files import CloudFileState


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_phase38d_i5b_targeted_hydration_retry.py"
_spec = importlib.util.spec_from_file_location("run_phase38d_i5b_targeted_hydration_retry", SCRIPT_PATH)
i5b = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["run_phase38d_i5b_targeted_hydration_retry"] = i5b
_spec.loader.exec_module(i5b)


def _row(row_id: int, tmp_path: Path, *, bucket: str = "b01", selected: bool = True, ext: str = ".jpg") -> dict[str, str]:
    source = tmp_path / f"source_{row_id}{ext}"
    target = tmp_path / "target" / f"target_{row_id}{ext}"
    source.write_bytes((f"row-{row_id}").encode("ascii"))
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


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fake_gate(*, risky_ids: set[int] | None = None):
    risky_ids = risky_ids or set()

    def fake(path, safe_label=None, hydration_policy_enabled=False):
        row_id = int(str(safe_label).split("_row_", 1)[1].split(".", 1)[0])
        risky = row_id in risky_ids
        state = CloudFileState(
            path=str(path),
            supported_platform=True,
            exists=True,
            is_file=True,
            recall_on_data_access=risky,
            likely_cloud_placeholder=risky,
        )
        return SourceIngestionGateResult(
            allowed=not risky or hydration_policy_enabled,
            blocked=risky and not hydration_policy_enabled,
            source_kind="path_source",
            reason="cloud_recall_on_data_access" if risky else "path_source_available",
            required_policy="controlled_hydration_or_read_probe_or_backfill" if risky else None,
            cloud_state=state,
            safe_label=safe_label,
        )

    return fake


def _policy(**overrides):
    policy = {
        "prefix_bytes": 1,
        "prefix_timeout_seconds": 30,
        "prefix_retries": 2,
        "full_timeout_seconds": 180,
        "full_retries": 2,
        "retry_wait_seconds": 10,
        "full_chunk_size": 1024,
    }
    policy.update(overrides)
    return policy


def test_target_rows_selection_includes_only_98_and_881(tmp_path: Path):
    rows = [
        _row(98, tmp_path, bucket="b02"),
        _row(99, tmp_path, bucket="b02"),
        _row(881, tmp_path, bucket="b15", ext=".png"),
        _row(1029, tmp_path, bucket="b02", selected=False, ext=".png"),
    ]

    targets = i5b.select_target_rows(rows, [98, 881])

    assert [int(row["row_id"]) for row in targets] == [98, 881]


def test_full_read_rescue_runs_even_when_prefix_fails(monkeypatch, tmp_path: Path):
    rows = [_row(98, tmp_path, bucket="b02")]
    monkeypatch.setattr(i5b.SourceIngestionGate, "evaluate_path_source", _fake_gate(risky_ids={98}))
    monkeypatch.setattr(
        i5b,
        "read_probe_prefix",
        lambda *args, **kwargs: {"ok": False, "bytes_read": 0, "error_reason": "read_probe_timeout"},
    )
    monkeypatch.setattr(
        i5b,
        "read_verify_full_content",
        lambda *args, expected_size=None, **kwargs: {
            "ok": True,
            "bytes_read": expected_size,
            "bytes_read_total": expected_size,
            "duration_seconds": 0.1,
            "error_reason": None,
        },
    )

    report, _local = i5b.run_targeted_hydration_retry(
        rows,
        target_row_ids=[98],
        policy=_policy(prefix_retries=0, full_retries=0, retry_wait_seconds=0),
        sleeper=lambda _seconds: None,
    )

    row98 = report["row_98_result"]
    assert row98["prefix_read"]["ok"] is False
    assert row98["full_read"]["ok"] is True
    assert row98["full_read"]["ran_even_if_prefix_failed"] is True
    assert row98["staging_copy_ready"] is True
    assert report["status"] == "targeted_retry_succeeded"
    assert report["success"] is True


def test_backfill_remains_dry_run_when_failures_remain(monkeypatch, tmp_path: Path):
    rows = [
        _row(98, tmp_path, bucket="b02"),
        _row(881, tmp_path, bucket="b15", ext=".png"),
        _row(1029, tmp_path, bucket="b02", selected=False, ext=".png"),
        _row(1041, tmp_path, bucket="b15", selected=False),
    ]
    monkeypatch.setattr(i5b.SourceIngestionGate, "evaluate_path_source", _fake_gate(risky_ids={98, 881}))
    monkeypatch.setattr(i5b, "read_probe_prefix", lambda *args, **kwargs: {"ok": True, "bytes_read": 1})
    monkeypatch.setattr(
        i5b,
        "read_verify_full_content",
        lambda *args, **kwargs: {
            "ok": False,
            "bytes_read": 0,
            "bytes_read_total": 0,
            "duration_seconds": 0.1,
            "error_reason": "read_timeout",
        },
    )

    report, _local = i5b.run_targeted_hydration_retry(
        rows,
        target_row_ids=[98, 881],
        policy=_policy(prefix_retries=0, full_retries=0, retry_wait_seconds=0),
        sleeper=lambda _seconds: None,
    )

    assert report["status"] == "targeted_retry_failed"
    assert report["success"] is False
    assert report["backfill_dry_run"]["mode"] == "dry_run_only"
    assert report["backfill_dry_run"]["replacement_count"] == 2
    assert report["safety"]["manifest_modified"] is False
    assert report["safety"]["backfill_applied"] is False


def test_report_is_privacy_safe_and_local_details_keep_paths(monkeypatch, tmp_path: Path):
    rows = [_row(98, tmp_path, bucket="b02")]
    monkeypatch.setattr(i5b.SourceIngestionGate, "evaluate_path_source", _fake_gate(risky_ids={98}))
    monkeypatch.setattr(i5b, "read_probe_prefix", lambda *args, **kwargs: {"ok": True, "bytes_read": 1})
    monkeypatch.setattr(
        i5b,
        "read_verify_full_content",
        lambda *args, expected_size=None, **kwargs: {
            "ok": True,
            "bytes_read": expected_size,
            "bytes_read_total": expected_size,
            "duration_seconds": 0.1,
            "error_reason": None,
        },
    )

    report, local = i5b.run_targeted_hydration_retry(
        rows,
        target_row_ids=[98],
        policy=_policy(prefix_retries=0, full_retries=0, retry_wait_seconds=0),
        sleeper=lambda _seconds: None,
    )

    assert report["privacy"]["passed"] is True
    assert str(tmp_path) not in json.dumps(report)
    assert Path(local["target_results"][0]["source_path"]).is_relative_to(tmp_path)


def test_cli_does_not_mutate_manifest(monkeypatch, tmp_path: Path):
    rows = [_row(98, tmp_path, bucket="b02")]
    manifest = tmp_path / "manifest.csv"
    report_json = tmp_path / "report.json"
    report_md = tmp_path / "report.md"
    details_json = tmp_path / "details.json"
    _write_manifest(manifest, rows)
    before = manifest.read_bytes()
    monkeypatch.setattr(i5b.SourceIngestionGate, "evaluate_path_source", _fake_gate(risky_ids={98}))
    monkeypatch.setattr(i5b, "read_probe_prefix", lambda *args, **kwargs: {"ok": True, "bytes_read": 1})
    monkeypatch.setattr(
        i5b,
        "read_verify_full_content",
        lambda *args, expected_size=None, **kwargs: {
            "ok": True,
            "bytes_read": expected_size,
            "bytes_read_total": expected_size,
            "duration_seconds": 0.1,
            "error_reason": None,
        },
    )

    exit_code = i5b.main(
        [
            "--manifest",
            str(manifest),
            "--report-json",
            str(report_json),
            "--report-md",
            str(report_md),
            "--local-details-json",
            str(details_json),
            "--target-row-id",
            "98",
            "--prefix-retries",
            "0",
            "--full-retries",
            "0",
            "--retry-wait",
            "0",
        ]
    )

    assert exit_code == 0
    assert manifest.read_bytes() == before
    assert json.loads(report_json.read_text(encoding="utf-8"))["safety"]["staging_copy"] is False
