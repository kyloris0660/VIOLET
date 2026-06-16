from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import run_phase46_fulllib_e1_production_import_ai_tagging as e1a


def production_db_url() -> str:
    return "postgresql://postgres:super-secret@localhost:5432/violet_library_prod"


def make_args(
    tmp_path: Path,
    source_root: Path,
    storage_root: Path,
    *,
    output_dir: Path | None = None,
    max_files: int = 20,
) -> list[str]:
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
        str(max_files),
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
    assert "head_sha" not in public_summary
    assert public_summary["report_generation_git_state"]["generated_from_worktree"] is True
    assert public_summary["report_generation_git_state"]["generated_before_commit"] is True
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


def test_streamed_traversal_honors_max_files_without_consuming_all_descendants(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    source_root = tmp_path / "source"
    storage_root = tmp_path / "storage"
    source_root.mkdir()
    storage_root.mkdir()
    first = source_root / "first.jpg"
    second = source_root / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    def bounded_entries(_source_root: Path):
        yield first
        yield second
        raise AssertionError("traversal consumed beyond max-files")

    monkeypatch.setattr(e1a, "iter_source_entries", bounded_entries)
    summary = e1a.run_dry_run(
        e1a.build_parser().parse_args(make_args(tmp_path, source_root, storage_root, max_files=2))
    )

    assert summary["inventory_results"]["max_files_reached"] is True
    assert summary["inventory_results"]["total_files_seen"] == 2
    assert summary["inventory_results"]["supported_candidates"] == 2
    ledger_rows = [
        json.loads(line)
        for line in (tmp_path / "out" / "inventory-candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(ledger_rows) == 2


def test_repo_python_preflight_accepts_documented_windows_and_posix_layouts(tmp_path):
    assert e1a.build_python_env(e1a.ROOT / "venv" / "Scripts" / "python.exe")["check_python_env_passed"] is True
    assert e1a.build_python_env(tmp_path / "venv" / "bin" / "python", root=tmp_path)["check_python_env_passed"] is True
    assert e1a.build_python_env(tmp_path / ".venv" / "Scripts" / "python.exe", root=tmp_path)["check_python_env_passed"] is True
    assert e1a.build_python_env(tmp_path / ".venv" / "bin" / "python", root=tmp_path)["check_python_env_passed"] is True
    assert e1a.build_python_env(tmp_path / "system" / "python.exe", root=tmp_path)["check_python_env_passed"] is False
    assert e1a.build_python_env(Path(sys.executable))["executable_path_redacted"] is True


def test_db_settings_mismatch_does_not_claim_app_equivalence(tmp_path):
    settings_path = tmp_path / "storage" / "data" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "database": {
                    "host": "localhost",
                    "port": 5432,
                    "name": "blombooru",
                    "user": "postgres",
                    "password": "different-secret",
                }
            }
        ),
        encoding="utf-8",
    )

    identity = e1a.resolve_production_db_identity(
        "postgresql://postgres:runner-secret@localhost:5432/violet_library_prod",
        dotenv={},
        environ={"VIOLET_ENV": "production"},
        settings_path=settings_path,
    )

    assert identity["db_connection_attempted"] is False
    assert identity["db_write_attempted"] is False
    assert identity["db_resolution"]["runner_matches_app_equivalent"] is False
    assert identity["db_resolution"]["urls_match"] is False
    assert identity["db_resolution"]["app_equivalence_status"] == "mismatch_e1b_blocker"
    dumped = json.dumps(identity, sort_keys=True)
    assert "runner-secret" not in dumped
    assert "different-secret" not in dumped


def test_db_settings_env_cli_alignment_can_prove_equivalence_with_reserved_password(tmp_path):
    password = "pa@ss:wo/rd#frag"
    settings_path = tmp_path / "storage" / "data" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(
            {
                "database": {
                    "host": "localhost",
                    "port": 5432,
                    "name": e1a.RECOMMENDED_PRODUCTION_DB,
                    "user": "postgres",
                    "password": password,
                }
            }
        ).encode("utf-8")
    )
    identity = e1a.resolve_production_db_identity(
        None,
        dotenv={},
        environ={
            "VIOLET_ENV": "production",
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": e1a.RECOMMENDED_PRODUCTION_DB,
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": password,
        },
        settings_path=settings_path,
    )

    assert identity["database"] == e1a.RECOMMENDED_PRODUCTION_DB
    assert identity["password_present"] is True
    assert identity["password_value_recorded"] is False
    assert identity["db_resolution"]["runner_matches_app_equivalent"] is True
    assert identity["db_resolution"]["urls_match"] is True
    assert password not in json.dumps(identity, sort_keys=True)


def test_metadata_access_failure_is_ledgered_as_deferred(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    source_root = tmp_path / "source"
    storage_root = tmp_path / "storage"
    source_root.mkdir()
    storage_root.mkdir()
    blocked = source_root / "blocked.jpg"
    blocked.write_bytes(b"blocked")

    def raising_is_file(path: Path) -> bool:
        if path == blocked:
            raise PermissionError(13, "denied")
        return path.is_file()

    monkeypatch.setattr(e1a, "is_file_entry", raising_is_file)
    summary = e1a.run_dry_run(
        e1a.build_parser().parse_args(make_args(tmp_path, source_root, storage_root))
    )

    deferred_rows = [
        json.loads(line)
        for line in (tmp_path / "out" / "unsupported-or-deferred.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(deferred_rows) == 1
    assert deferred_rows[0]["reason"] == "permission_denied"
    assert deferred_rows[0]["reason_category"] == "deferred"
    assert summary["inventory_results"]["total_files_seen"] == 1
    assert summary["duplicate_deferred_unsupported_summary"]["deferred_count"] == 1
    assert summary["duplicate_deferred_unsupported_summary"]["unsupported_or_deferred_ledger_rows"] == len(deferred_rows)
    assert summary["public_json_payload"]["inventory"]["deferred"] == len(deferred_rows)


def test_summary_provenance_object_replaces_misleading_head_sha(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    source_root = tmp_path / "source"
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    seed_source_tree(source_root)

    summary = e1a.run_dry_run(
        e1a.build_parser().parse_args(make_args(tmp_path, source_root, storage_root))
    )

    assert "head_sha" not in summary
    provenance = summary["report_generation_git_state"]
    assert provenance["generated_from_worktree"] is True
    assert provenance["generated_before_commit"] is True
    assert provenance["final_pr_head_sha_claimed"] is False
    assert "parent commit" not in summary["public_markdown_text"].lower()
