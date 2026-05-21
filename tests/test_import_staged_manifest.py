"""Tests for scripts/import_staged_manifest.py."""

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "import_staged_manifest.py"

_spec = importlib.util.spec_from_file_location("import_staged_manifest", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["import_staged_manifest"] = _module
_spec.loader.exec_module(_module)


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
]


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _write_audit(path: Path, expected: int, **overrides):
    payload = {
        "result": "PASS",
        "expected_copy_count": expected,
        "copy_rows": expected,
        "target_pass": expected,
        "copy_count_matches_expected": True,
        "total_verified_bytes": 0,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_image(path: Path, size=(32, 24), color=(128, 80, 160)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def _copy_row(row_id: str, source_path: Path, target_path: Path) -> dict[str, str]:
    return {
        "row_id": row_id,
        "source_path": str(source_path),
        "proposed_target_path": str(target_path),
        "extension": target_path.suffix.lower(),
        "size_bytes": str(target_path.stat().st_size),
        "selection_reason": "new_candidate",
        "duplicate_key": "",
        "exclusion_reason": "",
        "placeholder_flag": "",
        "stat_error": "",
    }


def _context(tmp_path: Path) -> object:
    storage_root = tmp_path / "storage"
    original_dir = storage_root / "media" / "original"
    thumbnail_dir = storage_root / "media" / "thumbnails"
    original_dir.mkdir(parents=True)
    thumbnail_dir.mkdir(parents=True)
    return _module.RuntimeContext(
        repo_root=tmp_path,
        violet_env="test",
        storage_root=storage_root.resolve(),
        original_dir=original_dir.resolve(),
        thumbnail_dir=thumbnail_dir.resolve(),
        database_url=make_url("sqlite:///:memory:"),
        safe_database_url="sqlite:///:memory:",
        db_name="blombooru_test",
        disabled_flags={name: True for name in _module.DISABLED_FLAG_DEFAULTS},
    )


def _engine(include_source: bool = True):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    source_column = ", source TEXT" if include_source else ""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE blombooru_media ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "filename TEXT NOT NULL, "
                "path TEXT UNIQUE NOT NULL, "
                "thumbnail_path TEXT, "
                "hash TEXT UNIQUE, "
                "file_type TEXT NOT NULL, "
                "mime_type TEXT, "
                "file_size INTEGER, "
                "width INTEGER, "
                "height INTEGER, "
                "duration FLOAT, "
                "rating TEXT, "
                "is_shared BOOLEAN, "
                "share_ai_metadata BOOLEAN, "
                "content_class_locked BOOLEAN, "
                "content_class_reviewed BOOLEAN"
                f"{source_column}"
                ")"
            )
        )
    return engine


def _prepare_candidates(tmp_path: Path, count: int = 1):
    target_root = tmp_path / "stage"
    source_root = tmp_path / "source"
    rows = []
    for index in range(count):
        staged = target_root / f"img-{index}.png"
        _make_image(staged, color=(index, 80, 160))
        rows.append(_copy_row(str(index + 1), source_root / f"secret-{index}.png", staged))
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest, rows)
    candidates, stats = _module.read_manifest(manifest)
    valid, invalid, _ = _module.validate_candidates(candidates, target_root)
    assert stats.copy_rows == count
    assert len(valid) == count
    assert not invalid
    return target_root, valid


def _clear_runtime_env(monkeypatch):
    for name in [
        "VIOLET_ENV",
        "VIOLET_STORAGE_ROOT",
        "TEST_DATABASE_URL",
        "POSTGRES_DB",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ]:
        monkeypatch.delenv(name, raising=False)
    for name in _module.DISABLED_FLAG_DEFAULTS:
        monkeypatch.delenv(name, raising=False)


def test_parse_requires_mode_and_execute_confirmation(tmp_path):
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"backup")
    common = [
        "--manifest",
        str(tmp_path / "manifest.csv"),
        "--target-root",
        str(tmp_path / "stage"),
        "--audit-summary",
        str(tmp_path / "audit.json"),
        "--expected-copy-count",
        "1",
        "--report-json",
        str(tmp_path / "report.json"),
        "--local-result-csv",
        str(tmp_path / "result.csv"),
    ]
    with pytest.raises(SystemExit):
        _module.parse_args(common)
    with pytest.raises(SystemExit):
        _module.parse_args(common + ["--execute", "--confirm-import-tier1000", "WRONG"])
    with pytest.raises(SystemExit):
        _module.parse_args(common + ["--execute", "--confirm-import-tier1000", _module.CONFIRM_PHRASE])
    args = _module.parse_args(
        common
        + [
            "--execute",
            "--confirm-import-tier1000",
            _module.CONFIRM_PHRASE,
            "--db-backup-file",
            str(backup),
        ]
    )
    assert args.execute is True


def test_execute_rejects_limit(tmp_path, capsys):
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"backup")
    with pytest.raises(SystemExit):
        _module.parse_args(
            [
                "--manifest",
                str(tmp_path / "manifest.csv"),
                "--target-root",
                str(tmp_path / "stage"),
                "--audit-summary",
                str(tmp_path / "audit.json"),
                "--expected-copy-count",
                "1",
                "--execute",
                "--confirm-import-tier1000",
                _module.CONFIRM_PHRASE,
                "--db-backup-file",
                str(backup),
                "--report-json",
                str(tmp_path / "report.json"),
                "--local-result-csv",
                str(tmp_path / "result.csv"),
                "--limit",
                "1",
            ]
        )
    assert "--limit is not allowed with --execute" in capsys.readouterr().err


def test_dry_run_allows_limit(tmp_path):
    args = _module.parse_args(
        [
            "--manifest",
            str(tmp_path / "manifest.csv"),
            "--target-root",
            str(tmp_path / "stage"),
            "--audit-summary",
            str(tmp_path / "audit.json"),
            "--expected-copy-count",
            "1",
            "--dry-run",
            "--report-json",
            str(tmp_path / "report.json"),
            "--local-result-csv",
            str(tmp_path / "result.csv"),
            "--limit",
            "1",
        ]
    )
    assert args.dry_run is True
    assert args.limit == 1


def test_default_db_host_is_localhost(tmp_path, monkeypatch):
    _clear_runtime_env(monkeypatch)

    context = _module.build_runtime_context(repo_root=tmp_path)

    assert context.database_url.host == "localhost"


def test_postgres_host_env_wins_without_settings(tmp_path, monkeypatch):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_HOST", "pg.example.local")

    context = _module.build_runtime_context(repo_root=tmp_path)

    assert context.database_url.host == "pg.example.local"


def test_data_settings_host_wins_over_postgres_host(tmp_path, monkeypatch):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_HOST", "env-host")
    settings_dir = tmp_path / "data"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps({"database": {"host": "settings-host", "name": "blombooru"}}),
        encoding="utf-8",
    )

    context = _module.build_runtime_context(repo_root=tmp_path)

    assert context.database_url.host == "settings-host"


def test_execute_backup_file_gate(tmp_path):
    missing = tmp_path / "missing.dump"
    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")
    valid = tmp_path / "valid.dump"
    valid.write_bytes(b"backup")

    with pytest.raises(_module.ImportGateError, match="requires --db-backup-file"):
        _module.validate_db_backup_file(None)
    with pytest.raises(_module.ImportGateError, match="does not exist"):
        _module.validate_db_backup_file(missing)
    with pytest.raises(_module.ImportGateError, match="empty"):
        _module.validate_db_backup_file(empty)

    info = _module.validate_db_backup_file(valid)
    assert info == {
        "required_for_execute": True,
        "file_name": "valid.dump",
        "bytes": 6,
        "path_redacted": True,
    }


def test_audit_summary_mismatch_fails(tmp_path):
    audit = tmp_path / "audit.json"
    _write_audit(audit, 3, target_pass=2)
    with pytest.raises(_module.ImportGateError, match="target_pass"):
        _module.validate_audit_summary(audit, 3)


def test_target_outside_root_is_invalid(tmp_path):
    target_root = tmp_path / "stage"
    target_root.mkdir()
    outside = tmp_path / "outside.png"
    _make_image(outside)
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest, [_copy_row("1", tmp_path / "source.png", outside)])
    candidates, _ = _module.read_manifest(manifest)

    valid, invalid, _ = _module.validate_candidates(candidates, target_root)

    assert not valid
    assert len(invalid) == 1
    assert "outside allowed root" in invalid[0].invalid_reason


def test_dry_run_plan_detects_duplicate_without_storage_mutation(tmp_path):
    context = _context(tmp_path)
    engine = _engine()
    _, valid = _prepare_candidates(tmp_path, count=2)
    duplicate_hash = valid[0].file_hash
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO blombooru_media "
                "(filename, path, hash, file_type, rating, source) "
                "VALUES ('already.png', 'media/original/already.png', :hash, 'image', 'safe', 'manual')"
            ),
            {"hash": duplicate_hash},
        )

    before_original = _module.directory_stats(context.original_dir)
    existing = _module.get_existing_media_by_hash(engine, [c.file_hash for c in valid])
    items = _module.build_import_items(valid, [], existing)

    assert [item.status for item in items].count("duplicate_by_hash") == 1
    assert [item.status for item in items].count("would_create") == 1
    assert _module.directory_stats(context.original_dir) == before_original


def test_dry_run_plan_detects_internal_manifest_hash_duplicate(tmp_path):
    _, valid = _prepare_candidates(tmp_path, count=2)
    valid[1].file_hash = valid[0].file_hash

    items = _module.build_import_items(valid, [], {})
    counts = _module._item_counts(items, dry_run=True)

    assert [item.status for item in items] == ["would_create", "duplicate_by_hash"]
    assert "duplicate hash within manifest" in items[1].message
    assert counts["duplicates_by_hash"] == 1
    assert counts["manifest_internal_hash_duplicates"] == 1
    assert _module.estimate_bytes_to_copy(items) == valid[0].size_bytes


def test_estimated_bytes_excludes_all_duplicate_rows(tmp_path):
    _, valid = _prepare_candidates(tmp_path, count=2)
    existing = {
        candidate.file_hash: {"id": index, "path": f"media/original/{index}.png"}
        for index, candidate in enumerate(valid, start=1)
    }

    items = _module.build_import_items(valid, [], existing)

    assert [item.status for item in items] == ["duplicate_by_hash", "duplicate_by_hash"]
    assert _module.estimate_bytes_to_copy(items) == 0


def test_estimated_bytes_counts_only_new_rows(tmp_path):
    _, valid = _prepare_candidates(tmp_path, count=2)
    existing = {valid[0].file_hash: {"id": 1, "path": "media/original/already.png"}}

    items = _module.build_import_items(valid, [], existing)

    assert [item.status for item in items] == ["duplicate_by_hash", "would_create"]
    assert _module.estimate_bytes_to_copy(items) == valid[1].size_bytes


def test_execute_import_uses_relative_managed_paths_and_generates_thumbnail(tmp_path):
    context = _context(tmp_path)
    engine = _engine()
    _, valid = _prepare_candidates(tmp_path, count=1)
    items = _module.build_import_items(valid, [], {})

    executed = _module.execute_import_items(items, context, engine)

    assert executed[0].status == "imported"
    assert executed[0].media_id == 1
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT path, thumbnail_path, source, is_shared, share_ai_metadata, "
                "content_class_locked, content_class_reviewed FROM blombooru_media"
            )
        ).mappings().one()
    assert row["source"] == _module.IMPORT_SOURCE_LABEL
    assert row["is_shared"] in (False, 0)
    assert row["share_ai_metadata"] in (False, 0)
    assert row["content_class_locked"] in (False, 0)
    assert row["content_class_reviewed"] in (False, 0)
    assert row["path"].startswith("media/original/")
    assert row["thumbnail_path"].startswith("media/thumbnails/")
    assert str(tmp_path) not in row["path"]
    assert (context.storage_root / row["path"]).exists()
    assert (context.storage_root / row["thumbnail_path"]).exists()


def test_execute_thumbnail_failure_prevents_insert_and_cleans_up(tmp_path, monkeypatch):
    context = _context(tmp_path)
    engine = _engine()
    _, valid = _prepare_candidates(tmp_path, count=1)
    items = _module.build_import_items(valid, [], {})
    monkeypatch.setattr(_module, "generate_thumbnail", lambda *_args, **_kwargs: False)

    executed = _module.execute_import_items(items, context, engine)

    assert executed[0].status == "failed"
    assert "Thumbnail generation failed" in executed[0].message
    assert _module.get_media_count(engine) == 0
    assert _module.directory_stats(context.original_dir)["file_count"] == 0
    assert _module.directory_stats(context.thumbnail_dir)["file_count"] == 0


def test_execute_rerun_is_duplicate_idempotent(tmp_path):
    context = _context(tmp_path)
    engine = _engine()
    _, valid = _prepare_candidates(tmp_path, count=1)
    first_items = _module.build_import_items(valid, [], {})
    _module.execute_import_items(first_items, context, engine)

    existing = _module.get_existing_media_by_hash(engine, [valid[0].file_hash])
    second_items = _module.build_import_items(valid, [], existing)
    _module.execute_import_items(second_items, context, engine)

    assert second_items[0].status == "duplicate_by_hash"
    assert _module.get_media_count(engine) == 1


def test_execute_failure_rolls_back_and_removes_created_files(tmp_path):
    context = _context(tmp_path)
    engine = _engine(include_source=False)
    _, valid = _prepare_candidates(tmp_path, count=1)
    items = _module.build_import_items(valid, [], {})

    executed = _module.execute_import_items(items, context, engine)

    assert executed[0].status == "failed"
    assert _module.directory_stats(context.original_dir)["file_count"] == 0
    assert _module.directory_stats(context.thumbnail_dir)["file_count"] == 0
    assert _module.get_media_count(engine) == 0


def test_post_import_audit_counts_null_thumbnail_as_missing(tmp_path):
    context = _context(tmp_path)
    engine = _engine()
    original = context.original_dir / "orphan.png"
    _make_image(original)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO blombooru_media "
                "(filename, path, thumbnail_path, hash, file_type, rating, source) "
                "VALUES ('orphan.png', 'media/original/orphan.png', NULL, 'hash-1', 'image', 'safe', :source)"
            ),
            {"source": _module.IMPORT_SOURCE_LABEL},
        )
    item = _module.ImportItem(
        candidate=_module.ManifestCandidate(
            row_number=2,
            row_id="1",
            source_path="source.png",
            proposed_target_path="stage.png",
            extension=".png",
            size_bytes=1,
            selection_reason="new_candidate",
        ),
        status="imported",
        media_id=1,
    )

    audit = _module.post_import_audit([item], context, engine)

    assert audit["original_files_found"] == 1
    assert audit["thumbnails_found"] == 0
    assert audit["missing_count"] == 1
    assert audit["missing"] == [{"id": 1, "kind": "thumbnail_null"}]


def test_report_omits_per_file_private_paths(tmp_path):
    context = _context(tmp_path)
    target_root, valid = _prepare_candidates(tmp_path, count=1)
    audit = {
        "result": "PASS",
        "expected_copy_count": 1,
        "copy_rows": 1,
        "target_pass": 1,
        "copy_count_matches_expected": True,
        "total_verified_bytes": valid[0].size_bytes,
    }
    report = _module.prepare_report(
        "dry_run",
        1,
        audit,
        _module.ManifestStats(total_rows=1, copy_rows=1),
        context,
        target_root,
    )
    gate = report.gates["source_ingestion_gate"]
    assert gate["source_kind"] == "staging_file"
    assert gate["source_cloud_gate_required"] is False
    assert gate["staging_audit_required"] is True
    assert gate["result"]["allowed"] is True
    assert gate["result"]["reason"] == "staging_audit_passed"
    report.counts = {"would_create": 1}
    report.errors = [
        r"Staged target path is outside allowed root: C:\Users\kyloris\secret.png",
        r"missing staged file: E:\VioletPilotData_1000\secret.png",
        f"user home path: {Path.home()}",
        "linux staging path: /mnt/e/VioletPilotData_1000/private.png",
        "mac staging path: /Volumes/VioletPilotData_1000/private.png",
        "workspace path: /workspace/AnimeLocalBooru/storage/private.png",
    ]
    report_path = tmp_path / "report.json"
    _module.write_report(report_path, report)

    payload = report_path.read_text(encoding="utf-8")
    assert valid[0].source_path not in payload
    assert valid[0].proposed_target_path not in payload
    assert "C:\\" not in payload
    assert "E:\\" not in payload
    assert str(Path.home()) not in payload
    assert "/mnt/" not in payload
    assert "/Volumes/" not in payload
    assert "/workspace/" not in payload
    assert "source_path" not in payload
    assert "proposed_target_path" not in payload
    assert "staged_path" not in payload
    assert "storage_root_label" in payload
    assert "target_root_label" in payload
    assert "paths_redacted" in payload


def test_local_result_csv_may_contain_full_paths(tmp_path):
    candidate = _module.ManifestCandidate(
        row_number=2,
        row_id="1",
        source_path=r"C:\Users\kyloris\private-source.png",
        proposed_target_path=r"E:\VioletPilotData_1000\private-source.png",
        extension=".png",
        size_bytes=1,
        selection_reason="new_candidate",
        staged_path=Path(r"E:\VioletPilotData_1000\private-source.png"),
        file_hash="abc",
    )
    item = _module.ImportItem(candidate=candidate, status="would_create")
    csv_path = tmp_path / "local-results.csv"

    _module.write_local_result_csv(csv_path, "run", [item])

    payload = csv_path.read_text(encoding="utf-8")
    assert r"C:\Users\kyloris\private-source.png" in payload
    assert r"E:\VioletPilotData_1000\private-source.png" in payload
