import csv
import importlib.util
import json
import shutil
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_phase38d_i6_staging_copy_retry.py"
_spec = importlib.util.spec_from_file_location("run_phase38d_i6_staging_copy_retry", SCRIPT_PATH)
i6 = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["run_phase38d_i6_staging_copy_retry"] = i6
_spec.loader.exec_module(i6)


FIELDNAMES = [
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
    "temporal_bucket",
    "timestamp_source",
    "modified_time_utc",
]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture(tmp_path: Path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    repo_root = tmp_path / "repo"
    app_storage_root = tmp_path / "media"
    for root in (source_root, target_root, repo_root, app_storage_root):
        root.mkdir(parents=True)

    active_specs = [
        (1, "source_row_0001.jpg", ".jpg", "b01", b"a" * 10),
        (2, "source_row_0002.png", ".png", "b02", b"b" * 12),
        (1029, "replacement_row_1029.png", ".png", "b02", b"c" * 14),
        (1041, "replacement_row_1041.jpg", ".jpg", "b15", b"d" * 16),
    ]
    rows: list[dict[str, str]] = []
    for row_id, name, ext, bucket, content in active_specs:
        source = source_root / name
        source.write_bytes(content)
        rows.append(
            {
                "row_id": str(row_id),
                "source_path": str(source),
                "proposed_target_path": str(target_root / name),
                "extension": ext,
                "size_bytes": str(len(content)),
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
    for row_id, name, ext, bucket in (
        (98, "source_row_0098.jpg", ".jpg", "b02"),
        (881, "source_row_0881.png", ".png", "b15"),
    ):
        rows.append(
            {
                "row_id": str(row_id),
                "source_path": str(source_root / name),
                "proposed_target_path": "",
                "extension": ext,
                "size_bytes": "100",
                "selection_reason": "",
                "duplicate_key": "",
                "exclusion_reason": "not_selected_temporal_stratified",
                "placeholder_flag": "False",
                "stat_error": "False",
                "temporal_bucket": bucket,
                "timestamp_source": "filesystem_mtime",
                "modified_time_utc": "2026-01-01T00:00:00+00:00",
            }
        )

    manifest = tmp_path / "manifest.csv"
    ledger = tmp_path / "ledger.json"
    i5c_summary = tmp_path / "i5c.json"
    i3_details = tmp_path / "i3.json"
    _write_csv(manifest, rows)
    active = [row for row in rows if i6.is_selected_copy_row(row)]
    _write_json(ledger, {"rows": [{"row_id": 98}, {"row_id": 881}]})
    _write_json(
        i5c_summary,
        {
            "bucket_distribution_after": dict(
                sorted({bucket: sum(1 for row in active if row["temporal_bucket"] == bucket) for bucket in {"b01", "b02", "b15"}}.items())
            )
        },
    )
    _write_json(
        i3_details,
        {
            "cleanup": {
                "expected_staging_root": str(target_root),
                "protected_roots": [
                    {"label": "source_root", "path": str(source_root)},
                    {"label": "repo_root", "path": str(repo_root)},
                    {"label": "app_storage_root", "path": str(app_storage_root)},
                    {"label": "app_media_root", "path": str(app_storage_root)},
                ],
            }
        },
    )
    return {
        "manifest": manifest,
        "ledger": ledger,
        "i5c_summary": i5c_summary,
        "i3_details": i3_details,
        "target_root": target_root,
        "source_root": source_root,
        "rows": rows,
    }


def _fake_db_counts():
    return {
        "available": True,
        "media": 995,
        "media_tags": 53354,
        "ai_jobs": 46,
        "classification_jobs": 14,
        "translation_jobs": 15,
    }


def _run_fixture(
    monkeypatch,
    fixture: dict,
    *,
    execute: bool = False,
    allow_cloud_recall_copy: bool = False,
    confirm_cloud_aware_copy: str | None = None,
    failure_budget: dict | None = None,
):
    monkeypatch.setattr(i6, "collect_db_counts", _fake_db_counts)
    return i6.run_staging_copy_retry(
        manifest_path=fixture["manifest"],
        deferred_ledger_path=fixture["ledger"],
        i5c_summary_path=fixture["i5c_summary"],
        i3_local_details_path=fixture["i3_details"],
        target_root=None,
        expected_staging_root=None,
        protected_roots=[],
        expected_selected_total=4,
        execute=execute,
        confirm_copy_tier1000=execute,
        allow_cloud_recall_copy=allow_cloud_recall_copy,
        confirm_cloud_aware_copy=confirm_cloud_aware_copy,
        failure_budget=failure_budget,
    )


def _cloud_risk_dry_run_result() -> dict:
    return {
        "valid": False,
        "existing_tier500_rows": 0,
        "new_candidate_rows": 4,
        "source_files_missing": 0,
        "unsupported_extensions": 0,
        "target_root_escapes": 0,
        "target_filename_collisions": 0,
        "source_root_violations": 0,
        "target_existing_files": 0,
        "blank_source_paths": 0,
        "blank_target_paths": 0,
        "blank_extensions": 0,
        "invalid_selection_reasons": 0,
        "invalid_exclusion_reasons": 0,
        "extension_mismatches": 0,
        "suffix_missing": 0,
        "truncated_rows": 0,
        "cloud_risk_files": 1,
        "cloud_risk_by_reason": {"cloud_recall_on_data_access": 1},
        "total_copy_bytes": 52,
        "errors": ["cloud_availability_files_nonzero"],
    }


def test_successful_temp_copy_and_post_copy_audit(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)

    report, local, exit_code = _run_fixture(
        monkeypatch,
        fixture,
        execute=True,
        failure_budget={
            "max_item_failures": 20,
            "max_failure_rate": 1.0,
            "max_consecutive_failures": 10,
            "max_same_reason_failures": 20,
        },
    )

    assert exit_code == 0
    assert report["status"] == "staging_copy_passed"
    assert report["manifest_validation"]["selected_total"] == 4
    assert report["pre_copy_target_check"]["file_count"] == 0
    assert report["dry_run"]["status"] == "passed"
    assert report["actual_staging_copy"]["staged_success_count"] == 4
    assert report["actual_staging_copy"]["item_failure_count"] == 0
    assert report["actual_staging_copy"]["copied_files"] == 4
    assert report["actual_staging_copy"]["copied_bytes"] == 52
    assert report["post_copy_audit"]["status"] == "passed"
    assert report["post_copy_audit"]["expected_success_file_count"] == 4
    assert report["post_copy_audit"]["actual_file_count"] == 4
    assert report["db_import_eligibility"]["eligible_for_full_1000_import_planning"] is True
    assert report["db_no_mutation"]["unchanged"] is True
    assert len(local["item_ledger"]) == 4
    assert {item["status"] for item in local["item_ledger"]} == {"staged"}


def test_missing_target_root_blocks_preflight(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)
    fixture["target_root"].rmdir()

    report, _local, exit_code = _run_fixture(monkeypatch, fixture, execute=True)

    assert exit_code == 1
    assert report["status"] == "blocked_preflight_failed"
    assert "target_root_missing" in report["pre_copy_target_check"]["errors"]
    assert report["actual_staging_copy"]["attempted"] is False


def test_target_root_hazard_blocks_preflight(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(i6, "root_escape_hazards", lambda _path: ["target_root_reparse_point"])

    report, _local, exit_code = _run_fixture(monkeypatch, fixture, execute=True)

    assert exit_code == 1
    assert report["status"] == "blocked_preflight_failed"
    assert "target_root_reparse_point" in report["pre_copy_target_check"]["errors"]
    assert report["actual_staging_copy"]["attempted"] is False


def test_blocked_report_shape_renders_without_key_error(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)
    fixture["manifest"].unlink()

    report, _local, exit_code = _run_fixture(monkeypatch, fixture, execute=True)
    markdown = i6.render_markdown(report)

    assert exit_code == 1
    assert "blocked_missing_backfilled_manifest" in markdown


def test_staging_target_must_be_empty_before_copy(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)
    (fixture["target_root"] / "unexpected.txt").write_text("leftover", encoding="utf-8")

    report, _local, exit_code = _run_fixture(monkeypatch, fixture, execute=True)

    assert exit_code == 1
    assert report["status"] == "blocked_preflight_failed"
    assert "staging_target_not_empty" in report["pre_copy_target_check"]["errors"]
    assert report["actual_staging_copy"]["attempted"] is False


def test_default_recall_risk_blocks_copy(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(i6.stage_pilot, "validate_manifest", lambda *_args, **_kwargs: _cloud_risk_dry_run_result())

    report, _local, exit_code = _run_fixture(monkeypatch, fixture, execute=True)

    assert exit_code == 1
    assert report["status"] == "blocked_dry_run_failed"
    assert report["dry_run"]["cloud_risk_files"] == 1
    assert "cloud_availability_files_nonzero" in report["dry_run"]["errors"]
    assert report["actual_staging_copy"]["attempted"] is False


def test_explicit_cloud_aware_copy_allows_recall_risk_rows(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(i6.stage_pilot, "validate_manifest", lambda *_args, **_kwargs: _cloud_risk_dry_run_result())

    report, local, exit_code = _run_fixture(
        monkeypatch,
        fixture,
        execute=True,
        allow_cloud_recall_copy=True,
        confirm_cloud_aware_copy=i6.CLOUD_AWARE_CONFIRM_PHRASE,
    )

    assert exit_code == 0
    assert report["status"] == "staging_copy_passed"
    assert report["dry_run"]["status"] == "passed_with_item_level_risks"
    assert report["cloud_aware_copy_policy"]["confirmation_phrase_accepted"] is True
    assert report["actual_staging_copy"]["staged_success_count"] == 4
    assert {item["status"] for item in local["item_ledger"]} == {"staged"}


def test_per_item_failure_within_budget_does_not_abort_whole_run(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)
    original_copy2 = shutil.copy2

    def flaky_copy(src: str, dst: str):
        if src.endswith("source_row_0002.png"):
            raise OSError("simulated item read failure")
        return original_copy2(src, dst)

    monkeypatch.setattr(i6.shutil, "copy2", flaky_copy)

    report, local, exit_code = _run_fixture(
        monkeypatch,
        fixture,
        execute=True,
        failure_budget={
            "max_item_failures": 20,
            "max_failure_rate": 1.0,
            "max_consecutive_failures": 10,
            "max_same_reason_failures": 20,
        },
    )

    assert exit_code == 0
    assert report["status"] == "completed_with_item_failures"
    assert report["actual_staging_copy"]["staged_success_count"] == 3
    assert report["actual_staging_copy"]["item_failure_count"] == 1
    assert report["actual_staging_copy"]["budget_exceeded"] is False
    failed = [item for item in local["item_ledger"] if item["status"] != "staged"]
    assert len(failed) == 1
    assert failed[0]["eligible_for_db_import"] is False
    assert report["db_import_eligibility"]["eligible_for_full_1000_import_planning"] is False
    assert report["db_import_eligibility"]["eligible_for_partial_import_planning"] is True


def test_failure_budget_exceeded_aborts(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)

    def always_fail(_src: str, _dst: str):
        raise OSError("simulated repeated failure")

    monkeypatch.setattr(i6.shutil, "copy2", always_fail)

    report, local, exit_code = _run_fixture(
        monkeypatch,
        fixture,
        execute=True,
        failure_budget={
            "max_item_failures": 1,
            "max_failure_rate": 1.0,
            "max_consecutive_failures": 10,
            "max_same_reason_failures": 20,
        },
    )

    assert exit_code == 1
    assert report["status"] == "blocked_failure_budget_exceeded"
    assert report["actual_staging_copy"]["attempted_count"] == 2
    assert report["actual_staging_copy"]["item_failure_count"] == 2
    assert report["actual_staging_copy"]["budget_exceeded"] is True
    assert all(item["eligible_for_db_import"] is False for item in local["item_ledger"])


def test_failed_rows_cannot_remain_active(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)
    rows = fixture["rows"]
    for row in rows:
        if row["row_id"] == "98":
            row["selection_reason"] = "new_candidate"
            row["exclusion_reason"] = ""
            source = fixture["source_root"] / "source_row_0098.jpg"
            source.write_bytes(b"x" * 10)
            row["source_path"] = str(source)
            row["proposed_target_path"] = str(fixture["target_root"] / "source_row_0098.jpg")
    _write_csv(fixture["manifest"], rows)

    report, _local, exit_code = _run_fixture(monkeypatch, fixture, execute=False)

    assert exit_code == 1
    assert report["status"] == "blocked_preflight_failed"
    assert "failed_row_98_still_active_selected" in report["manifest_validation"]["errors"]


def test_duplicate_target_path_is_blocked(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)
    rows = fixture["rows"]
    rows[1]["proposed_target_path"] = rows[0]["proposed_target_path"]
    _write_csv(fixture["manifest"], rows)

    report, _local, exit_code = _run_fixture(monkeypatch, fixture, execute=False)

    assert exit_code == 1
    assert report["status"] == "blocked_preflight_failed"
    assert report["manifest_validation"]["duplicate_target_path_count"] == 1


def test_post_copy_audit_detects_unexpected_file(tmp_path: Path):
    fixture = _fixture(tmp_path)
    for row in i6.selected_rows(fixture["rows"]):
        target = Path(row["proposed_target_path"])
        target.write_bytes(Path(row["source_path"]).read_bytes())
    (fixture["target_root"] / "extra.jpg").write_bytes(b"extra")

    public, local = i6.audit_staged_files(
        fixture["rows"],
        target_root=fixture["target_root"],
        expected_copy_count=4,
    )

    assert public["status"] == "failed"
    assert public["unexpected_file_count"] == 1
    assert "post_copy_unexpected_files" in public["errors"]
    assert local["unexpected_files"] == ["extra.jpg"]


def test_post_copy_replacement_flags_derive_from_actual_staged_files(tmp_path: Path):
    fixture = _fixture(tmp_path)
    rows_by_id = {i6.row_id(row): row for row in i6.selected_rows(fixture["rows"])}
    for rid in (1, 2, 1029):
        row = rows_by_id[rid]
        Path(row["proposed_target_path"]).write_bytes(Path(row["source_path"]).read_bytes())
    item_ledger = [
        {
            "row_id": rid,
            "status": "staged" if rid in {1, 2, 1029} else "failed_cloud_hydration",
            "reason": None if rid in {1, 2, 1029} else "cloud_hydration_failed",
        }
        for rid in (1, 2, 1029, 1041)
    ]

    public, _local = i6.audit_staged_files(
        fixture["rows"],
        target_root=fixture["target_root"],
        expected_copy_count=4,
        item_ledger=item_ledger,
    )

    assert public["status"] == "passed"
    assert public["rows_1029_1041_staged"] == [1029]
    assert public["known_failed_item_count"] == 1


def test_public_report_is_privacy_safe(monkeypatch, tmp_path: Path):
    fixture = _fixture(tmp_path)

    report, _local, _exit_code = _run_fixture(monkeypatch, fixture, execute=False)

    text = json.dumps(report, ensure_ascii=False)
    assert report["privacy"]["passed"] is True
    assert str(tmp_path) not in text
    assert "source_path" not in text
    assert "file://" not in text
