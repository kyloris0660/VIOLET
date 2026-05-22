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
