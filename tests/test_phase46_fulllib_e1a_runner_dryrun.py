from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_phase46_fulllib_e1_production_import_ai_tagging as e1a


def production_db_url() -> str:
    return "postgresql://postgres:super-secret@localhost:5432/violet_library_prod"


def make_args(tmp_path: Path, source_root: Path, storage_root: Path, *, output_dir: Path | None = None) -> list[str]:
    return [
        "--dry-run",
        "--source-root",
        str(source_root),
        "--production-db-url",
        production_db_url(),
        "--production-storage-root",
        str(storage_root),
        "--output-dir",
        str(output_dir or (tmp_path / "out")),
        "--max-files",
        "20",
        "--batch-size",
        "2",
        "--run-id",
        "test-run",
    ]


def seed_source_tree(source_root: Path) -> None:
    source_root.mkdir(parents=True)
    (source_root / "first.jpg").write_bytes(b"same-content")
    (source_root / "duplicate.jpg").write_bytes(b"same-content")
    (source_root / "unique.png").write_bytes(b"unique-content")
    (source_root / "empty.gif").write_bytes(b"")
    (source_root / "notes.txt").write_text("not media", encoding="utf-8")


def test_dry_run_writes_private_ledgers_and_public_report(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    monkeypatch.setattr(e1a, "PUBLIC_REPORT_MD", tmp_path / "public-report.md")
    monkeypatch.setattr(e1a, "PUBLIC_REPORT_JSON", tmp_path / "public-summary.json")
    source_root = tmp_path / "source"
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    seed_source_tree(source_root)

    code = e1a.main([*make_args(tmp_path, source_root, storage_root), "--write-public-report"])
    assert code == 0

    output_dir = tmp_path / "out"
    for name in [
        "inventory-candidates.jsonl",
        "duplicate-skipped.jsonl",
        "unsupported-or-deferred.jsonl",
        "batch-plan.jsonl",
        "run-summary-private.json",
        "public-redaction-check.json",
    ]:
        assert (output_dir / name).exists()

    public_summary = json.loads((tmp_path / "public-summary.json").read_text(encoding="utf-8"))
    assert public_summary["status"] == "dry_run_completed"
    assert public_summary["db_identity"]["password_value_recorded"] is False
    assert public_summary["db_identity"]["db_resolution"]["password_value_recorded"] is False
    assert public_summary["public_redaction"]["passed"] is True
    assert public_summary["duplicate_deferred_unsupported_summary"]["duplicate_count"] == 1
    assert public_summary["duplicate_deferred_unsupported_summary"]["unsupported_count"] == 1
    assert public_summary["duplicate_deferred_unsupported_summary"]["deferred_count"] == 1
    assert "first.jpg" not in (tmp_path / "public-report.md").read_text(encoding="utf-8")
    assert str(source_root) not in (tmp_path / "public-summary.json").read_text(encoding="utf-8")


def test_source_storage_overlap_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    source_root = tmp_path / "source"
    source_root.mkdir()
    storage_root = source_root / "storage"

    code = e1a.main(make_args(tmp_path, source_root, storage_root))
    assert code == 2


def test_output_dir_inside_source_root_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    source_root = tmp_path / "source"
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    seed_source_tree(source_root)

    code = e1a.main(make_args(tmp_path, source_root, storage_root, output_dir=source_root / "out"))
    assert code == 2


def test_execute_confirmation_string_is_required(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    source_root = tmp_path / "source"
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    seed_source_tree(source_root)

    with pytest.raises(SystemExit) as exc_info:
        e1a.main(
            [
                "--execute",
                "--source-root",
                str(source_root),
                "--production-db-url",
                production_db_url(),
                "--production-storage-root",
                str(storage_root),
            ]
        )
    assert exc_info.value.code == 2

    code = e1a.main(
        [
            "--execute",
            "--confirm-execution",
            e1a.CONFIRM_PHRASE,
            "--source-root",
            str(source_root),
            "--production-db-url",
            production_db_url(),
            "--production-storage-root",
            str(storage_root),
        ]
    )
    assert code == 2


def test_ledger_schema_validators_cover_required_rows():
    inventory_row = {field: "x" for field in e1a.INVENTORY_CANDIDATE_REQUIRED_FIELDS}
    duplicate_row = {field: "x" for field in e1a.DUPLICATE_REQUIRED_FIELDS}
    deferred_row = {field: "x" for field in e1a.UNSUPPORTED_OR_DEFERRED_REQUIRED_FIELDS}
    batch_row = {field: "x" for field in e1a.BATCH_PLAN_REQUIRED_FIELDS}

    assert e1a.validate_ledger_schema([inventory_row], e1a.INVENTORY_CANDIDATE_REQUIRED_FIELDS, "inventory")["passed"]
    assert e1a.validate_ledger_schema([duplicate_row], e1a.DUPLICATE_REQUIRED_FIELDS, "duplicate")["passed"]
    assert e1a.validate_ledger_schema([deferred_row], e1a.UNSUPPORTED_OR_DEFERRED_REQUIRED_FIELDS, "deferred")["passed"]
    assert e1a.validate_ledger_schema([batch_row], e1a.BATCH_PLAN_REQUIRED_FIELDS, "batch")["passed"]

    missing = dict(inventory_row)
    missing.pop("candidate_id")
    result = e1a.validate_ledger_schema([missing], e1a.INVENTORY_CANDIDATE_REQUIRED_FIELDS, "inventory")
    assert not result["passed"]
    assert result["missing_rows"][0]["missing"] == ["candidate_id"]


def test_public_redaction_rejects_raw_media_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    source_root = tmp_path / "source"
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    seed_source_tree(source_root)

    summary = e1a.run_dry_run(
        e1a.build_parser().parse_args(make_args(tmp_path, source_root, storage_root))
    )
    bad = dict(summary)
    bad["public_json_payload"] = {"raw_filename": "private_image.jpg"}
    redaction = e1a.run_redaction_check(bad, "clean report text")
    assert redaction["passed"] is False


def test_batch_plan_schema_and_batching(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    source_root = tmp_path / "source"
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    seed_source_tree(source_root)

    summary = e1a.run_dry_run(
        e1a.build_parser().parse_args(make_args(tmp_path, source_root, storage_root))
    )
    batch_rows = [
        json.loads(line)
        for line in (tmp_path / "out" / "batch-plan.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert summary["batch_plan"]["planned_unique_candidate_count"] == 2
    assert summary["batch_plan"]["planned_batch_count"] == 1
    assert set(e1a.BATCH_PLAN_REQUIRED_FIELDS) <= set(batch_rows[0])
    assert batch_rows[0]["requires_e1b_execute_approval"] is True
