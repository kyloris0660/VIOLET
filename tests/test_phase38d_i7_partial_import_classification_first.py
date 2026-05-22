import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_phase38d_i7_partial_import_classification_first.py"
_spec = importlib.util.spec_from_file_location("run_phase38d_i7_partial_import_classification_first", SCRIPT_PATH)
i7 = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["run_phase38d_i7_partial_import_classification_first"] = i7
_spec.loader.exec_module(i7)


FAILED_ROWS = {799, 839, 922, 970, 971, 972}


def _staged_ids() -> list[int]:
    ids = [row_id for row_id in range(1, 1001) if row_id not in FAILED_ROWS and row_id not in {98, 881}]
    ids.extend([1029, 1041])
    assert len(ids) == 994
    return ids


def _ledger_row(
    row_id: int,
    *,
    staged: bool,
    target_label: str | None = None,
    size: int = 3,
) -> dict:
    return {
        "row_id": row_id,
        "safe_label": f"source_row_{row_id:04d}.jpg",
        "bucket": "b01",
        "extension": ".jpg",
        "expected_size": size,
        "source_cloud_state_summary": {"recall_on_data_access": not staged},
        "target_safe_label": target_label or f"staged_{row_id:04d}.jpg",
        "status": "staged" if staged else "failed_cloud_hydration",
        "reason": None if staged else "cloud_network_unavailable",
        "bytes_copied": size if staged else 0,
        "staging_target_exists": staged,
        "eligible_for_db_import": staged,
    }


def _write_i6_ledger(tmp_path: Path, *, missing_row: int | None = None, duplicate_targets: bool = False) -> tuple[Path, Path, list[dict]]:
    target_root = tmp_path / "staging"
    target_root.mkdir()
    rows: list[dict] = []
    for index, row_id in enumerate(_staged_ids()):
        target_label = f"staged_{row_id:04d}.jpg"
        if duplicate_targets and index == 1:
            target_label = "staged_0001.jpg"
        row = _ledger_row(row_id, staged=True, target_label=target_label)
        rows.append(row)
        if row_id != missing_row:
            (target_root / target_label).write_bytes(b"abc")
    for row_id in sorted(FAILED_ROWS):
        rows.append(_ledger_row(row_id, staged=False))
    ledger_path = tmp_path / "i6-ledger.json"
    ledger_path.write_text(json.dumps({"phase": "3.8d-I6", "status": "completed_with_item_failures", "rows": rows}), encoding="utf-8")
    return ledger_path, target_root, rows


def test_validate_i6_ledger_selects_only_staged_success_rows(tmp_path: Path) -> None:
    ledger_path, target_root, _rows = _write_i6_ledger(tmp_path)

    candidates, summary = i7.validate_i6_item_ledger(ledger_path, target_root)

    candidate_ids = {item.row_id for item in candidates}
    assert len(candidates) == 994
    assert summary["total_rows"] == 1000
    assert summary["failed_rows"] == sorted(FAILED_ROWS)
    assert not (candidate_ids & FAILED_ROWS)
    assert not (candidate_ids & {98, 881})
    assert {1029, 1041}.issubset(candidate_ids)


def test_validate_i6_ledger_rejects_missing_staged_file(tmp_path: Path) -> None:
    ledger_path, target_root, _rows = _write_i6_ledger(tmp_path, missing_row=1)

    with pytest.raises(i7.PhaseI7Error, match="missing_staged_files"):
        i7.validate_i6_item_ledger(ledger_path, target_root)


def test_validate_i6_ledger_rejects_duplicate_target_path(tmp_path: Path) -> None:
    ledger_path, target_root, _rows = _write_i6_ledger(tmp_path, duplicate_targets=True)

    with pytest.raises(i7.PhaseI7Error, match="duplicate_target_path"):
        i7.validate_i6_item_ledger(ledger_path, target_root)


def test_write_import_manifest_excludes_failed_rows_and_uses_staged_paths(tmp_path: Path) -> None:
    ledger_path, target_root, _rows = _write_i6_ledger(tmp_path)
    candidates, _summary = i7.validate_i6_item_ledger(ledger_path, target_root)
    manifest_path = tmp_path / "i7-import.csv"

    manifest = i7.write_import_manifest(candidates, manifest_path)

    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    row_ids = {int(row["row_id"]) for row in rows}
    assert manifest["rows"] == 994
    assert len(rows) == 994
    assert not (row_ids & FAILED_ROWS)
    assert not (row_ids & {98, 881})
    assert {1029, 1041}.issubset(row_ids)
    assert all("source_row_" not in row["source_path"] for row in rows)
    assert all(Path(row["proposed_target_path"]).is_absolute() for row in rows)


def test_public_privacy_scan_flags_absolute_paths() -> None:
    leaks = i7.scan_privacy_leaks({"bad": "C:\\Users\\kyloris\\Pictures\\source.jpg"})

    assert "windows_absolute_path" in leaks


def test_write_public_outputs_fails_closed_on_summary_privacy_leak(tmp_path: Path) -> None:
    report_md = tmp_path / "report.md"
    summary_json = tmp_path / "summary.json"
    summary = {
        "status": "completed",
        "success": True,
        "source_label": i7.SOURCE_LABEL,
        "bad": "C:\\Users\\kyloris\\Pictures\\source.jpg",
    }

    i7.write_public_outputs(summary, report_md, summary_json)

    report_text = report_md.read_text(encoding="utf-8")
    summary_text = summary_json.read_text(encoding="utf-8")
    persisted = json.loads(summary_text)
    assert persisted["status"] == "blocked_public_report_privacy_leak"
    assert persisted["privacy_scan"]["summary_leak_count"] == 1
    assert "C:\\Users" not in report_text
    assert "C:\\Users" not in summary_text
    assert not i7.scan_privacy_leaks(persisted)
    assert not i7.scan_privacy_leaks(report_text)


def test_write_public_outputs_fails_closed_on_markdown_privacy_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_md = tmp_path / "report.md"
    summary_json = tmp_path / "summary.json"
    summary = {"status": "completed", "success": True, "source_label": i7.SOURCE_LABEL}

    monkeypatch.setattr(i7, "render_markdown_report", lambda _summary: "C:\\Users\\kyloris\\Pictures\\source.jpg\n")

    i7.write_public_outputs(summary, report_md, summary_json)

    report_text = report_md.read_text(encoding="utf-8")
    summary_text = summary_json.read_text(encoding="utf-8")
    persisted = json.loads(summary_text)
    assert persisted["status"] == "blocked_public_report_privacy_leak"
    assert persisted["privacy_scan"]["markdown_leak_count"] == 1
    assert "C:\\Users" not in report_text
    assert "C:\\Users" not in summary_text
    assert not i7.scan_privacy_leaks(persisted)
    assert not i7.scan_privacy_leaks(report_text)


def test_write_public_outputs_writes_normal_safe_report(tmp_path: Path) -> None:
    report_md = tmp_path / "report.md"
    summary_json = tmp_path / "summary.json"
    summary = {"status": "completed", "success": True, "source_label": i7.SOURCE_LABEL}

    i7.write_public_outputs(summary, report_md, summary_json)

    persisted = json.loads(summary_json.read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"
    assert persisted["privacy_scan"]["public_report_leaks"] == []
    assert "Phase 3.8d-I7" in report_md.read_text(encoding="utf-8")


def test_resume_prior_import_items_maps_integer_row_ids(tmp_path: Path) -> None:
    ledger_path, target_root, _rows = _write_i6_ledger(tmp_path)
    candidates, _summary = i7.validate_i6_item_ledger(ledger_path, target_root)
    details_path = tmp_path / "validation-details.json"
    details_path.write_text(
        json.dumps(
            {
                "import_items": [
                    {
                        "row_id": item.row_id,
                        "status": "imported",
                        "media_id": 10000 + index,
                        "managed_path": f"media/original/{item.target_safe_label}",
                        "thumbnail_path": f"media/thumbnails/{item.target_safe_label}",
                    }
                    for index, item in enumerate(candidates)
                ]
            }
        ),
        encoding="utf-8",
    )

    items = i7.load_prior_import_items(details_path, candidates)

    assert len(items) == 994
    assert items[0].status == "imported"
    assert items[0].media_id == 10000


def _dry_run_item(row_id: int, file_hash: str) -> object:
    candidate = i7.staged_import.ManifestCandidate(
        row_number=row_id,
        row_id=str(row_id),
        source_path=f"staged_{row_id:04d}.jpg",
        proposed_target_path=f"staged_{row_id:04d}.jpg",
        extension=".jpg",
        size_bytes=3,
        selection_reason="staged_success_i6",
    )
    candidate.file_hash = file_hash
    return i7.staged_import.ImportItem(candidate=candidate, status="duplicate_by_hash")


def test_resume_import_items_uses_db_source_label_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    dry_run_items = [_dry_run_item(1, "hash-a"), _dry_run_item(2, "hash-b")]

    monkeypatch.setattr(
        i7,
        "media_rows_for_source",
        lambda _engine, _source: [
            {"id": 201, "hash": "hash-a", "path": "media/original/a.jpg", "thumbnail_path": "media/thumbnails/a.jpg"},
            {"id": 202, "hash": "hash-b", "path": "media/original/b.jpg", "thumbnail_path": "media/thumbnails/b.jpg"},
        ],
    )

    items = i7.resume_import_items_from_db_source(object(), dry_run_items, expected_count=2)

    assert [item.media_id for item in items] == [201, 202]
    assert all(item.message == "resumed from current DB source-label media row" for item in items)


def test_resume_import_items_blocks_db_source_label_count_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    dry_run_items = [_dry_run_item(1, "hash-a"), _dry_run_item(2, "hash-b")]
    monkeypatch.setattr(
        i7,
        "media_rows_for_source",
        lambda _engine, _source: [
            {"id": 201, "hash": "hash-a", "path": "media/original/a.jpg", "thumbnail_path": "media/thumbnails/a.jpg"},
        ],
    )

    with pytest.raises(i7.PhaseI7Error, match="resume_db_source_label_count_mismatch"):
        i7.resume_import_items_from_db_source(object(), dry_run_items, expected_count=2)


def test_prior_classification_requires_media_id_identity_proof() -> None:
    imported_ids = [1, 2, 3]
    prior = {"status": "completed", "processed": 3}

    assert not i7.prior_classification_matches_media_ids(prior, imported_ids)

    prior["identity_proof"] = i7.media_ids_identity_proof(imported_ids)
    assert i7.prior_classification_matches_media_ids(prior, imported_ids)


def test_classification_resume_rejects_same_count_different_media_ids() -> None:
    records = [{"id": 10, "content_class": "anime"}, {"id": 11, "content_class": "unknown"}]

    with pytest.raises(i7.PhaseI7Error, match="classification_resume_media_id_set_mismatch"):
        i7.build_classification_resume_from_records([1, 2], records, identity_source="unit_test")


def test_classification_resume_accepts_exact_media_id_set() -> None:
    records = [{"id": 2, "content_class": "unknown"}, {"id": 1, "content_class": "anime"}]

    classification = i7.build_classification_resume_from_records([1, 2], records, identity_source="unit_test")

    assert classification["status"] == "completed"
    assert classification["processed"] == 2
    assert classification["failed"] == 0
    assert classification["distribution"] == {
        "anime": 1,
        "unknown": 1,
        "non_anime": 0,
        "illustration": 0,
        "failed_or_unclassified": 0,
    }
    assert classification["identity_proof"]["media_ids_count"] == 2
