import csv
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_phase38d_i5c_backfill_application.py"
_spec = importlib.util.spec_from_file_location("run_phase38d_i5c_backfill_application", SCRIPT_PATH)
i5c = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["run_phase38d_i5c_backfill_application"] = i5c
_spec.loader.exec_module(i5c)


def _manifest_rows(tmp_path: Path) -> list[dict[str, str]]:
    target_root = tmp_path / "staging"
    rows: list[dict[str, str]] = []
    for row_id in range(1, 1001):
        ext = ".png" if row_id == 881 else ".jpg"
        bucket = "b02" if row_id == 98 else ("b15" if row_id == 881 else f"b{row_id % 20:02d}")
        source = tmp_path / "source" / f"source_{row_id:04d}{ext}"
        target = target_root / source.name
        rows.append(
            {
                "row_id": str(row_id),
                "source_path": str(source),
                "proposed_target_path": str(target),
                "extension": ext,
                "size_bytes": str(row_id + 10),
                "selection_reason": "new_candidate",
                "duplicate_key": "",
                "exclusion_reason": "",
                "placeholder_flag": "False",
                "stat_error": "False",
                "temporal_bucket": bucket,
                "timestamp_source": "filesystem_mtime",
                "modified_time_utc": "2026-01-01T00:00:00+00:00",
            }
        )
    rows.extend(
        [
            {
                "row_id": "1029",
                "source_path": str(tmp_path / "source" / "replacement_row_1029.png"),
                "proposed_target_path": "",
                "extension": ".png",
                "size_bytes": "403770",
                "selection_reason": "",
                "duplicate_key": "",
                "exclusion_reason": "not_selected_temporal_stratified",
                "placeholder_flag": "False",
                "stat_error": "False",
                "temporal_bucket": "b02",
                "timestamp_source": "filesystem_mtime",
                "modified_time_utc": "2026-01-01T00:00:00+00:00",
            },
            {
                "row_id": "1041",
                "source_path": str(tmp_path / "source" / "replacement_row_1041.jpg"),
                "proposed_target_path": "",
                "extension": ".jpg",
                "size_bytes": "216819",
                "selection_reason": "",
                "duplicate_key": "",
                "exclusion_reason": "not_selected_temporal_stratified",
                "placeholder_flag": "False",
                "stat_error": "False",
                "temporal_bucket": "b15",
                "timestamp_source": "filesystem_mtime",
                "modified_time_utc": "2026-01-01T00:00:00+00:00",
            },
        ]
    )
    return rows


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _policy(**overrides):
    policy = {
        "prefix_bytes": 1,
        "prefix_timeout_seconds": 30,
        "prefix_retries": 1,
        "full_timeout_seconds": 180,
        "full_retries": 1,
        "retry_wait_seconds": 0,
        "full_chunk_size": 1024,
    }
    policy.update(overrides)
    return policy


def _fake_verify(ok_by_row: dict[int, bool]):
    def fake(record, *, policy, sleeper=None):
        row_id = int(record["row_id"])
        ok = ok_by_row.get(row_id, True)
        reason = None if ok else "cloud_hydration_failed"
        public = {
            "row_id": row_id,
            "source_safe_label": record["source_safe_label"],
            "bucket": record["bucket"],
            "extension": record["extension"],
            "expected_size": int(record["expected_size"]),
            "metadata_before": {"cloud_state": {"recall_on_data_access": not ok, "likely_cloud_placeholder": not ok}},
            "prefix_read": {
                "ok": ok,
                "attempted": True,
                "attempt_count": 1,
                "bytes_read": 1 if ok else 0,
                "bytes_read_total": 1 if ok else 0,
                "duration_seconds": 0.01,
                "error_reason": reason,
            },
            "full_read": {
                "ok": ok,
                "attempted": True,
                "attempt_count": 1,
                "bytes_read": int(record["expected_size"]) if ok else 0,
                "bytes_read_total": int(record["expected_size"]) if ok else 0,
                "duration_seconds": 0.02,
                "error_reason": reason,
                "ran_even_if_prefix_failed": False,
            },
            "metadata_after": {"cloud_state": {"recall_on_data_access": not ok, "likely_cloud_placeholder": not ok}},
            "still_recall_on_data_access": not ok,
            "staging_copy_ready": ok,
            "ok": ok,
            "failure_reason": reason,
            "audit_bytes_read": (1 + int(record["expected_size"])) if ok else 0,
            "duration_seconds": 0.03,
        }
        return {"public": public, "local": {**public, "source_path": record["source_path"]}}

    return fake


def test_successful_backfill_preserves_selected_total_and_bucket_distribution(monkeypatch, tmp_path: Path):
    rows = _manifest_rows(tmp_path)
    monkeypatch.setattr(i5c, "verify_target_row", _fake_verify({1029: True, 1041: True}))

    report, local = i5c.run_backfill_application(
        rows,
        mapping={98: 1029, 881: 1041},
        policy=_policy(),
        i5b_summary={},
        sleeper=lambda _seconds: None,
    )

    assert report["status"] == "backfill_applied"
    assert report["success"] is True
    assert report["selected_total_before"] == 1000
    assert report["selected_total_after"] == 1000
    assert report["bucket_distribution_before"] == report["bucket_distribution_after"]
    assert report["backfill_application"]["active_backfilled_replacements"] == [1029, 1041]
    updated_by_id = {int(row["row_id"]): row for row in local["backfilled_rows"]}
    assert updated_by_id[98]["selection_reason"] == ""
    assert updated_by_id[98]["exclusion_reason"] == "not_selected_temporal_stratified"
    assert updated_by_id[1029]["selection_reason"] == "new_candidate"
    assert updated_by_id[1029]["exclusion_reason"] == ""
    assert updated_by_id[1029]["proposed_target_path"]


def test_failed_replacement_blocks_backfill_and_manifest_write(monkeypatch, tmp_path: Path):
    rows = _manifest_rows(tmp_path)
    manifest = tmp_path / "manifest.csv"
    report_json = tmp_path / "report.json"
    report_md = tmp_path / "report.md"
    details = tmp_path / "details.json"
    ledger = tmp_path / "ledger.json"
    output_manifest = tmp_path / "backfilled.csv"
    _write_manifest(manifest, rows)
    monkeypatch.setattr(i5c, "verify_target_row", _fake_verify({1029: True, 1041: False}))

    exit_code = i5c.main(
        [
            "--manifest",
            str(manifest),
            "--report-json",
            str(report_json),
            "--report-md",
            str(report_md),
            "--local-details-json",
            str(details),
            "--local-ledger-json",
            str(ledger),
            "--output-manifest",
            str(output_manifest),
            "--retry-wait",
            "0",
        ]
    )

    report = json.loads(report_json.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["status"] == "blocked_replacement_validation_failed"
    assert report["backfill_application"]["applied"] is False
    assert not output_manifest.exists()
    assert ledger.exists()


def test_same_bucket_replacement_required(monkeypatch, tmp_path: Path):
    rows = _manifest_rows(tmp_path)
    by_id = {int(row["row_id"]): row for row in rows}
    by_id[1029]["temporal_bucket"] = "b99"
    monkeypatch.setattr(i5c, "verify_target_row", _fake_verify({1029: True, 1041: True}))

    report, _local = i5c.run_backfill_application(
        rows,
        mapping={98: 1029, 881: 1041},
        policy=_policy(),
        i5b_summary={},
        sleeper=lambda _seconds: None,
    )

    assert report["status"] == "blocked_replacement_mapping_invalid"
    assert "replacement_bucket_mismatch_98_1029" in report["mapping_errors"]


def test_deferred_ledger_records_failed_rows_without_public_paths(monkeypatch, tmp_path: Path):
    rows = _manifest_rows(tmp_path)
    monkeypatch.setattr(i5c, "verify_target_row", _fake_verify({1029: True, 1041: True}))

    report, local = i5c.run_backfill_application(
        rows,
        mapping={98: 1029, 881: 1041},
        policy=_policy(),
        i5b_summary={
            "target_results": [
                {"row_id": 98, "failure_reason": "cloud_hydration_failed", "metadata_after": {"cloud_state": {"recall_on_data_access": True}}},
                {"row_id": 881, "failure_reason": "cloud_hydration_failed", "metadata_after": {"cloud_state": {"recall_on_data_access": True}}},
            ]
        },
        sleeper=lambda _seconds: None,
    )

    ledger = report["deferred_cloud_recovery_ledger"]
    assert ledger["unrecovered_original_rows"] == [98, 881]
    assert ledger["reason"] == "cloud_hydration_failed_after_I5_and_I5b_bounded_read_based_recovery_attempts"
    assert {row["deferred_reason"] for row in ledger["rows"]} == {"cloud_hydration_failed"}
    assert all(row["per_run_final_state"]["retried"] is True for row in ledger["rows"])
    assert all(row["per_run_final_state"]["backfilled"] is True for row in ledger["rows"])
    assert all(row["per_run_final_state"]["deferred_for_cloud_recovery"] is True for row in ledger["rows"])
    assert all(row["per_run_final_state"]["imported_into_db"] is False for row in ledger["rows"])
    public_text = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in public_text
    assert "source_path" not in public_text
    assert local["deferred_cloud_recovery_ledger"]["rows"][0]["source_path"]


def test_public_report_is_privacy_safe(monkeypatch, tmp_path: Path):
    rows = _manifest_rows(tmp_path)
    monkeypatch.setattr(i5c, "verify_target_row", _fake_verify({1029: True, 1041: True}))

    report, _local = i5c.run_backfill_application(
        rows,
        mapping={98: 1029, 881: 1041},
        policy=_policy(),
        i5b_summary={},
        sleeper=lambda _seconds: None,
    )

    assert report["privacy"]["passed"] is True
    assert report["ingestion_observability_principle"]["db_migration_in_this_pr"] is False
    assert report["ingestion_observability_principle"]["full_production_ledger_in_this_pr"] is False
    assert str(tmp_path) not in json.dumps(report, ensure_ascii=False)
    assert report["safety"]["staging_copy"] is False
    assert report["safety"]["db_import"] is False


def test_cli_writes_local_manifest_only_after_validation_passes(monkeypatch, tmp_path: Path):
    rows = _manifest_rows(tmp_path)
    manifest = tmp_path / "manifest.csv"
    report_json = tmp_path / "report.json"
    report_md = tmp_path / "report.md"
    details = tmp_path / "details.json"
    ledger = tmp_path / "ledger.json"
    output_manifest = tmp_path / "backfilled.csv"
    _write_manifest(manifest, rows)
    monkeypatch.setattr(i5c, "verify_target_row", _fake_verify({1029: True, 1041: True}))

    exit_code = i5c.main(
        [
            "--manifest",
            str(manifest),
            "--report-json",
            str(report_json),
            "--report-md",
            str(report_md),
            "--local-details-json",
            str(details),
            "--local-ledger-json",
            str(ledger),
            "--output-manifest",
            str(output_manifest),
            "--retry-wait",
            "0",
        ]
    )

    report = json.loads(report_json.read_text(encoding="utf-8"))
    written_rows = list(csv.DictReader(output_manifest.open(encoding="utf-8", newline="")))
    written_by_id = {int(row["row_id"]): row for row in written_rows}
    assert exit_code == 0
    assert report["status"] == "backfill_applied"
    assert report["backfill_application"]["local_manifest_written"] is True
    assert output_manifest.exists()
    assert details.exists()
    assert ledger.exists()
    assert written_by_id[98]["selection_reason"] == ""
    assert written_by_id[1029]["selection_reason"] == "new_candidate"
    assert report["safety"]["manifest_modified_in_repo"] is False
