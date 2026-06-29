from __future__ import annotations

import inspect as py_inspect
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base, migrate_add_dynamic_library_sync_tables  # noqa: E402
from app.enums import FileTypeEnum, TagCategoryEnum  # noqa: E402
from app.models import DynamicSourceItem, DynamicSourceRoot, DynamicSyncRun, DynamicSyncRunItem, Media, Tag, TagTranslation  # noqa: E402
from app.routes.admin import dynamic_library_sync as dynamic_routes  # noqa: E402
from app.services import dynamic_library_sync_service as service  # noqa: E402
from app.utils.media_processor import calculate_file_hash  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def app_style_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(dynamic_routes.router, prefix="/api/admin")
    app.dependency_overrides[dynamic_routes.get_db] = lambda: db
    app.dependency_overrides[dynamic_routes.require_admin_mode] = lambda: object()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _seed_source_tree(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "new.jpg").write_bytes(b"new image")
    (root / "other.png").write_bytes(b"other image")
    (root / "empty.gif").write_bytes(b"")
    (root / "notes.txt").write_text("not media", encoding="utf-8")


def _write_png(path: Path, color: tuple[int, int, int] = (1, 2, 3)) -> None:
    from PIL import Image

    image = Image.new("RGB", (1, 1), color)
    image.save(path)


def test_dynamic_sync_migration_is_idempotent_and_indexed():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    try:
        migrate_add_dynamic_library_sync_tables(engine, inspect(engine))
        migrate_add_dynamic_library_sync_tables(engine, inspect(engine))
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "blombooru_dynamic_source_roots" in tables
        assert "blombooru_dynamic_source_items" in tables
        assert "blombooru_dynamic_sync_runs" in tables
        assert "blombooru_dynamic_sync_run_items" in tables
        indexes = {idx["name"] for idx in inspector.get_indexes("blombooru_dynamic_source_items")}
        assert "ix_dynamic_source_items_root_relhash" in indexes
        assert "ix_dynamic_source_items_import_status" in indexes
        assert "ix_dynamic_source_items_ai_tagging_status" in indexes
        assert "ix_dynamic_source_items_localization_status" in indexes
    finally:
        engine.dispose()


def test_update_check_persists_pending_counts_and_repeated_runs_are_idempotent(db, tmp_path, monkeypatch):
    monkeypatch.setenv("DYNAMIC_LIBRARY_SYNC_THRESHOLD", "100")
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)

    root = service.register_source_root(db, path=source_root, label="fixture")
    first = service.run_update_check(db, root_ids=[root.id])
    assert first["dry_run"] is True
    assert first["new_items"] == 2
    assert first["deferred_items"] == 2
    assert first["pending_summary"]["pending_new"] == 2
    assert first["pending_summary"]["pending_deferred"] == 2
    assert first["pending_summary"]["pending_deferred_scope"] == "historical_accumulated_active_source_roots"
    assert first["pending_summary"]["pending_deferred_includes_historical"] is True
    assert first["pending_summary"]["current_actionable_pending_import"] == 2
    assert first["pending_summary"]["threshold"] == 100
    assert first["pending_summary"]["threshold_reached"] is False
    assert db.query(DynamicSourceItem).count() == 4
    assert db.query(Media).count() == 0

    second = service.run_update_check(db, root_ids=[root.id])
    assert second["pending_summary"]["pending_new"] == 2
    assert second["pending_summary"]["pending_deferred"] == 2
    assert db.query(DynamicSourceItem).count() == 4
    assert db.query(DynamicSyncRun).count() == 2


def test_app_style_autoflush_false_flushes_observations_before_missing(app_style_db, tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "fresh.jpg").write_bytes(b"fresh")
    root = service.register_source_root(app_style_db, path=source_root, label="fixture")

    result = service.run_update_check(app_style_db, root_ids=[root.id])
    item = app_style_db.query(DynamicSourceItem).one()
    run_items = app_style_db.query(DynamicSyncRunItem).all()

    assert result["status"] == "completed"
    assert result["new_items"] == 1
    assert result["missing_items"] == 0
    assert item.source_status == "available"
    assert item.sync_state == "new"
    assert item.last_seen_run_id == result["id"]
    assert len(run_items) == 1
    assert len({(run_item.sync_run_id, run_item.source_item_id) for run_item in run_items}) == 1


def test_pending_snapshot_embeds_completed_current_run(app_style_db, tmp_path):
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)
    root = service.register_source_root(app_style_db, path=source_root, label="fixture")

    result = service.run_update_check(app_style_db, root_ids=[root.id])
    embedded = result["summary"]["pending_summary"]["last_sync_run"]

    assert result["status"] == "completed"
    assert embedded["id"] == result["id"]
    assert embedded["status"] == "completed"
    assert embedded["total_seen"] == result["total_seen"]
    assert embedded["new_items"] == result["new_items"]
    assert embedded["deferred_items"] == result["deferred_items"]
    assert not (embedded["status"] == "running" and embedded["total_seen"] == 0)


def test_dynamic_sync_dashboard_exposes_runtime_provenance(client):
    response = client.get("/api/admin/dynamic-library-sync")

    assert response.status_code == 200
    payload = response.json()
    runtime = payload["runtime_provenance"]
    assert "git_head" in runtime
    assert "git_branch" in runtime
    assert "profile_id" in runtime
    assert "app_port" in runtime
    assert "violet_env" in runtime
    assert "db_name" in runtime
    assert runtime["python_executable_name"]


def test_manual_sync_dry_run_plan_reports_public_safe_item_states(db, tmp_path):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "a_new.png", (10, 20, 30))
    _write_png(source_root / "b_duplicate.png", (10, 20, 30))
    _write_png(source_root / "c_existing.png", (90, 80, 70))
    (source_root / "d_empty.jpg").write_bytes(b"")
    (source_root / "e_notes.txt").write_text("not media", encoding="utf-8")
    (source_root / "f_corrupt.jpg").write_bytes(b"not an image")
    (source_root / "g_cloud.icloud").write_bytes(b"placeholder")

    existing_hash = calculate_file_hash(source_root / "c_existing.png")
    db.add(
        Media(
            filename="existing-redacted.png",
            path="media/original/existing-redacted.png",
            hash=existing_hash,
            file_type=FileTypeEnum.image,
        )
    )
    db.commit()

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=20,
        stable_age_seconds=0,
    )
    counts = plan["counts"]["state_counts"]

    assert plan["job"]["mode"] == "dry_run"
    assert plan["job"]["production_execution_enabled"] is False
    assert plan["ledger"]["db_write_performed"] is False
    assert counts["import_planned"] == 1
    assert counts["skipped_duplicate"] == 1
    assert counts["skipped_existing_media"] == 1
    assert counts["skipped_zero_byte"] == 1
    assert counts["skipped_unsupported"] == 1
    assert counts["skipped_placeholder"] == 1
    assert counts["failed"] == 1
    assert plan["counts"]["estimated_classification_count"] == 1
    assert plan["counts"]["estimated_ai_tagging_count"] == 1
    assert plan["counts"]["estimated_localization_workload"] == 1
    assert all(item["bytes_copied"] == 0 for item in plan["ledger"]["per_file_public_records"])
    assert "private_details" not in plan
    assert "a_new.png" not in str(plan)


def test_manual_sync_dry_run_plan_defers_files_that_are_still_changing(db, tmp_path):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "fresh.png")

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=5,
        stable_age_seconds=3600,
    )

    assert plan["counts"]["state_counts"]["skipped_changing"] == 1
    assert plan["counts"]["estimated_import_count"] == 0
    assert plan["ledger"]["per_file_public_records"][0]["reason"] == "file_still_changing"


def test_manual_sync_dry_run_plan_allows_cloud_aware_hydration_policy(db, tmp_path, monkeypatch):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "cloud_backed.png")
    monkeypatch.setattr(service, "_is_cloud_only", lambda _path: True)

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        hydrated_only=False,
        stable_age_seconds=0,
    )

    assert plan["limits"]["hydrated_only"] is False
    assert plan["limits"]["hydration_policy"] == "cloud_aware_non_destructive_read"
    assert plan["limits"]["cloud_placeholders_detected_before_hydration"] == 1
    assert plan["counts"]["state_counts"]["import_planned"] == 1
    assert plan["ledger"]["per_file_public_records"][0]["cloud_placeholder_before_hydration"] is True


def test_manual_sync_dry_run_plan_route_is_read_only_and_public_safe(client, tmp_path):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "new.png")

    response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/plan",
        json={"source_path": str(source_root), "max_files": 5, "stable_age_seconds": 0},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["public_safe"] is True
    assert payload["ledger"]["db_write_performed"] is False
    assert payload["limits"]["hydrated_only"] is False
    assert payload["limits"]["hydration_policy"] == "cloud_aware_non_destructive_read"
    assert payload["counts"]["state_counts"]["import_planned"] == 1
    assert "private_details" not in payload
    assert "new.png" not in str(payload)


def test_manual_sync_plan_route_is_sync_worker_thread_endpoint() -> None:
    assert py_inspect.iscoroutinefunction(dynamic_routes.plan_manual_sync) is False


def test_manual_sync_execute_route_records_web_admin_gui_provenance(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("POSTGRES_DB", "blombooru_test")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "false")
    monkeypatch.setattr(dynamic_routes, "start_manual_sync_execute_run", lambda _run_id: None)

    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "fresh.png", (10, 20, 30))
    root = service.register_source_root(db, path=source_root, label="fixture")
    session_response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/gui-session",
        headers={"X-Violet-Gui-Client": "web-admin-manual-sync-v1"},
        json={"client_route": "/admin?tab=content#dynamic-library-sync-section"},
    )
    assert session_response.status_code == 200, session_response.text
    session = session_response.json()
    session_id = session["gui_validation_session_id"]
    session_token = session["gui_validation_session_token"]

    plan_response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/plan",
        json={
            "root_id": root.id,
            "max_files": 5,
            "hydrated_only": True,
            "stable_age_seconds": 0,
            "gui_validation_session_id": session_id,
            "gui_validation_session_token": session_token,
            "client_route": "/admin?tab=content#dynamic-library-sync-section",
        },
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["gui_provenance"]["request_source"] == "web_admin_gui"
    assert plan["gui_provenance"]["gui_validation_session_id"] == session_id
    assert plan["gui_provenance"]["gui_validation_session_signature_valid"] is True

    execute_response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/execute",
        json={
            "root_id": root.id,
            "max_files": 5,
            "hydrated_only": True,
            "stable_age_seconds": 0,
            "expected_plan_hash": plan["integrity"]["plan_hash"],
            "confirmation_phrase": plan["integrity"]["confirmation_phrase"],
            "plan_created_at": plan["job"]["created_at"],
            "production_acceptance_approved": False,
            "gui_validation_session_id": session_id,
            "gui_validation_session_token": session_token,
            "client_route": "/admin?tab=content#dynamic-library-sync-section",
        },
    )
    assert execute_response.status_code == 200, execute_response.text
    run = db.query(DynamicSyncRun).one()
    request = run.summary_json["manual_sync_execute"]["request"]
    assert request["request_source"] == "web_admin_gui"
    assert request["gui_validation_session_id"] == session_id
    assert request["gui_validation_session_signature_valid"] is True
    assert request["client_route"] == "/admin?tab=content#dynamic-library-sync-section"


def test_manual_sync_gui_provenance_rejects_unsigned_session_marker(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("POSTGRES_DB", "blombooru_test")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED", "true")

    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "fresh.png", (10, 20, 30))
    root = service.register_source_root(db, path=source_root, label="fixture")

    response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/plan",
        json={
            "root_id": root.id,
            "max_files": 5,
            "hydrated_only": True,
            "stable_age_seconds": 0,
            "gui_validation_session_id": "gui-test-session-unsigned",
            "client_route": "/admin?tab=content#dynamic-library-sync-section",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "gui_validation_session_invalid"


def test_manual_sync_execute_route_plain_api_does_not_record_gui_provenance(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("POSTGRES_DB", "blombooru_test")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "false")
    monkeypatch.setattr(dynamic_routes, "start_manual_sync_execute_run", lambda _run_id: None)

    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "fresh.png", (10, 20, 30))
    root = service.register_source_root(db, path=source_root, label="fixture")

    plan_response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/plan",
        json={
            "root_id": root.id,
            "max_files": 5,
            "hydrated_only": True,
            "stable_age_seconds": 0,
        },
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert "gui_provenance" not in plan

    execute_response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/execute",
        json={
            "root_id": root.id,
            "max_files": 5,
            "hydrated_only": True,
            "stable_age_seconds": 0,
            "expected_plan_hash": plan["integrity"]["plan_hash"],
            "confirmation_phrase": plan["integrity"]["confirmation_phrase"],
            "plan_created_at": plan["job"]["created_at"],
            "production_acceptance_approved": False,
        },
    )
    assert execute_response.status_code == 200, execute_response.text
    run = db.query(DynamicSyncRun).one()
    request = run.summary_json["manual_sync_execute"]["request"]
    assert request["request_source"] == "api_or_runner"
    assert request["gui_validation_session_signature_valid"] is False
    assert "gui_validation_session_id" not in request


def test_manual_sync_execute_route_requires_operator_entered_production_phrase(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    monkeypatch.setenv("POSTGRES_DB", "blombooru")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "false")
    monkeypatch.setattr(dynamic_routes, "start_manual_sync_execute_run", lambda _run_id: None)

    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "fresh.png", (10, 20, 30))
    root = service.register_source_root(db, path=source_root, label="fixture")
    session_response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/gui-session",
        headers={"X-Violet-Gui-Client": "web-admin-manual-sync-v1"},
        json={"client_route": "/admin?tab=content#dynamic-library-sync-section"},
    )
    assert session_response.status_code == 200, session_response.text
    session = session_response.json()

    plan_response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/plan",
        json={
            "root_id": root.id,
            "max_files": 5,
            "hydrated_only": True,
            "stable_age_seconds": 0,
            "gui_validation_session_id": session["gui_validation_session_id"],
            "gui_validation_session_token": session["gui_validation_session_token"],
            "client_route": "/admin?tab=content#dynamic-library-sync-section",
        },
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()

    execute_response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/execute",
        json={
            "root_id": root.id,
            "max_files": 5,
            "hydrated_only": True,
            "stable_age_seconds": 0,
            "expected_plan_hash": plan["integrity"]["plan_hash"],
            "confirmation_phrase": plan["integrity"]["confirmation_phrase"],
            "plan_created_at": plan["job"]["created_at"],
            "production_acceptance_approved": True,
            "gui_validation_session_id": session["gui_validation_session_id"],
            "gui_validation_session_token": session["gui_validation_session_token"],
            "client_route": "/admin?tab=content#dynamic-library-sync-section",
        },
    )

    assert execute_response.status_code == 409
    assert execute_response.json()["detail"]["code"] == "production_acceptance_approval_required"
    assert db.query(DynamicSyncRun).count() == 0


def test_manual_sync_dry_run_plan_route_defaults_to_cloud_aware_hydration(client, tmp_path):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "new.png")

    response = client.post(
        "/api/admin/dynamic-library-sync/manual-sync/plan",
        json={
            "source_path": str(source_root),
            "max_files": 5,
            "hydrated_only": False,
            "stable_age_seconds": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["limits"]["hydrated_only"] is False
    assert response.json()["limits"]["hydration_policy"] == "cloud_aware_non_destructive_read"


@pytest.mark.parametrize("scanner_reason", ["hidden", "too_large"])
def test_manual_sync_dry_run_preserves_policy_skip_reasons(db, tmp_path, monkeypatch, scanner_reason):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "policy_skip.png")

    monkeypatch.setattr(service, "_is_scannable_file", lambda _path, *, hydrated_only=True: scanner_reason)

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=5,
        stable_age_seconds=0,
    )
    item = plan["ledger"]["per_file_public_records"][0]

    assert item["state"] == "skipped_unsupported"
    assert item["reason"] == scanner_reason
    assert item["reason"] != "read_error"
    assert plan["counts"]["failure_reasons"][scanner_reason] == 1
    assert plan["counts"]["state_counts"]["failed"] == 0


def test_manual_sync_dry_run_maps_not_a_file_to_path_policy_skip(db, tmp_path, monkeypatch):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "policy_skip.png")

    monkeypatch.setattr(service, "_is_scannable_file", lambda _path, *, hydrated_only=True: "not_a_file")

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=5,
        stable_age_seconds=0,
    )
    item = plan["ledger"]["per_file_public_records"][0]

    assert item["state"] == "skipped_path_policy_error"
    assert item["reason"] == "not_a_file"
    assert item["reason"] != "read_error"
    assert plan["counts"]["state_counts"]["failed"] == 0


def test_manual_sync_dry_run_hash_timeout_is_item_level_failure(db, tmp_path, monkeypatch):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "a_timeout.png")
    _write_png(source_root / "b_valid.png")

    def fake_hash(path: Path, _timeout_sec: int):
        if path.name == "a_timeout.png":
            return None, "read_timeout"
        return calculate_file_hash(path), None

    monkeypatch.setattr(service, "_verify_supported_image_file_with_timeout", lambda _path, _timeout_sec: None)
    monkeypatch.setattr(service, "_calculate_manual_plan_file_hash", fake_hash)

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=10,
        stable_age_seconds=0,
    )

    assert plan["counts"]["state_counts"]["failed"] == 1
    assert plan["counts"]["state_counts"]["import_planned"] == 1
    assert plan["counts"]["failure_reasons"]["read_timeout"] == 1
    assert {item["reason"] for item in plan["ledger"]["per_file_public_records"]} == {"read_timeout", None}


def test_manual_sync_dry_run_image_verify_timeout_is_item_level_failure(db, tmp_path, monkeypatch):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "a_timeout.png")
    _write_png(source_root / "b_valid.png")

    def fake_verify(path: Path, _timeout_sec: int):
        return "read_timeout" if path.name == "a_timeout.png" else None

    monkeypatch.setattr(service, "_verify_supported_image_file_with_timeout", fake_verify)
    monkeypatch.setattr(
        service,
        "_calculate_manual_plan_file_hash",
        lambda path, _timeout_sec: (calculate_file_hash(path), None),
    )

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=10,
        stable_age_seconds=0,
    )

    assert plan["counts"]["state_counts"]["failed"] == 1
    assert plan["counts"]["state_counts"]["import_planned"] == 1
    assert plan["counts"]["failure_reasons"]["read_timeout"] == 1


def test_manual_sync_dry_run_public_reasons_do_not_leak_oserror_paths(db, tmp_path, monkeypatch):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    _write_png(source_root / "private_filename.png")
    leaked_path = r"C:\Users\kyloris\Pictures\secret-fragment\private_filename.png"
    original_metadata = service._metadata_for_path

    def fake_metadata(path: Path, *, follow_symlinks: bool = True):
        if path.name == "private_filename.png":
            raise OSError(f"cannot stat {leaked_path}")
        return original_metadata(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(service, "_metadata_for_path", fake_metadata)

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=5,
        stable_age_seconds=0,
    )
    public_text = str(plan)

    assert plan["counts"]["failure_reasons"]["stat_error"] == 1
    assert plan["ledger"]["per_file_public_records"][0]["reason"] == "stat_error"
    assert leaked_path not in public_text
    assert "secret-fragment" not in public_text
    assert "private_filename.png" not in public_text


def test_manual_sync_dry_run_existing_media_uses_candidate_hash_lookup(db, tmp_path, monkeypatch):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    candidate_path = source_root / "existing.png"
    _write_png(candidate_path, (50, 60, 70))
    candidate_hash = calculate_file_hash(candidate_path)

    db.add(
        Media(
            filename="existing-redacted.png",
            path="media/original/existing-redacted.png",
            hash=candidate_hash,
            file_type=FileTypeEnum.image,
        )
    )
    for index in range(250):
        db.add(
            Media(
                filename=f"redacted-extra-{index}.png",
                path=f"media/original/redacted-extra-{index}.png",
                hash=f"{index:064x}",
                file_type=FileTypeEnum.image,
            )
        )
    db.commit()

    queried_hashes: list[set[str]] = []
    original_lookup = service._query_existing_media_by_hashes

    def spy_lookup(db_session, hashes):
        queried_hashes.append(set(hashes))
        return original_lookup(db_session, hashes)

    monkeypatch.setattr(service, "_query_existing_media_by_hashes", spy_lookup)

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=1,
        stable_age_seconds=0,
    )

    assert queried_hashes == [{candidate_hash}]
    assert plan["counts"]["state_counts"]["skipped_existing_media"] == 1
    assert plan["ledger"]["per_file_public_records"][0]["media_id"] is not None


def test_manual_sync_dry_run_cap_skips_unchanged_known_items_for_registered_root(db, tmp_path):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    known_path = source_root / "a_known.png"
    fresh_path = source_root / "b_fresh.png"
    _write_png(known_path, (20, 30, 40))
    _write_png(fresh_path, (60, 70, 80))
    known_stat = known_path.stat()

    root = DynamicSourceRoot(
        label="fixture",
        root_path=str(source_root),
        root_path_hash="registered-root-hash",
        is_active=True,
    )
    db.add(root)
    db.flush()
    media = Media(
        filename="a_known.png",
        path="media/original/a_known.png",
        hash=calculate_file_hash(known_path),
        file_type=FileTypeEnum.image,
    )
    db.add(media)
    db.flush()
    db.add(
        DynamicSourceItem(
            source_root_id=root.id,
            relative_path="a_known.png",
            relative_path_hash=service._hash_text("a_known.png"),
            file_size=known_stat.st_size,
            mtime_ns=known_stat.st_mtime_ns,
            content_hash=media.hash,
            source_status="available",
            sync_state="imported",
            import_status="imported",
            media_id=media.id,
        )
    )
    db.commit()

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=1,
        stable_age_seconds=0,
    )

    assert plan["counts"]["state_counts"]["import_planned"] == 1
    assert plan["counts"]["state_counts"]["skipped_existing_media"] == 0
    assert plan["counts"]["total_seen"] == 1
    assert plan["counts"]["partial_scan"] is False
    assert plan["limits"]["scanned_files"] == 2
    assert plan["limits"]["unchanged_known_files"] == 1
    assert plan["limits"]["max_files_scope"] == "manual_sync_delta_candidates"
    assert "a_known.png" not in str(plan)
    assert "b_fresh.png" not in str(plan)


def test_manual_sync_dry_run_reincludes_unresolved_known_items_for_registered_root(db, tmp_path):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    unresolved_path = source_root / "cloud_now_readable.png"
    _write_png(unresolved_path, (20, 30, 40))
    stat = unresolved_path.stat()

    root = DynamicSourceRoot(
        label="fixture",
        root_path=str(source_root),
        root_path_hash="registered-root-hash",
        is_active=True,
    )
    db.add(root)
    db.flush()
    db.add(
        DynamicSourceItem(
            source_root_id=root.id,
            relative_path="cloud_now_readable.png",
            relative_path_hash=service._hash_text("cloud_now_readable.png"),
            file_size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            source_status="available",
            sync_state="skipped_placeholder",
            import_status="deferred",
            deferred_reason="cloud_placeholder",
        )
    )
    db.commit()

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=1,
        stable_age_seconds=0,
    )

    assert plan["counts"]["state_counts"]["import_planned"] == 1
    assert plan["counts"]["total_seen"] == 1
    assert plan["limits"]["unchanged_known_files"] == 0
    assert "cloud_now_readable.png" not in str(plan)


def test_manual_sync_dry_run_skips_unchanged_stable_unsupported_without_consuming_cap(db, tmp_path):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    stable_path = source_root / "a_old_video.mov"
    fresh_path = source_root / "b_fresh.png"
    stable_path.write_bytes(b"not imported video")
    _write_png(fresh_path, (60, 70, 80))
    stable_stat = stable_path.stat()

    root = DynamicSourceRoot(
        label="fixture",
        root_path=str(source_root),
        root_path_hash="registered-root-hash",
        is_active=True,
    )
    db.add(root)
    db.flush()
    db.add(
        DynamicSourceItem(
            source_root_id=root.id,
            relative_path="a_old_video.mov",
            relative_path_hash=service._hash_text("a_old_video.mov"),
            file_size=stable_stat.st_size,
            mtime_ns=stable_stat.st_mtime_ns,
            source_status="deferred",
            sync_state="skipped_unsupported",
            import_status="deferred",
            deferred_reason="unsupported_extension",
        )
    )
    db.commit()

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=1,
        stable_age_seconds=0,
    )

    assert plan["counts"]["state_counts"]["import_planned"] == 1
    assert plan["counts"]["state_counts"]["skipped_unsupported"] == 0
    assert plan["counts"]["total_seen"] == 1
    assert plan["counts"]["partial_scan"] is False
    assert plan["limits"]["scanned_files"] == 2
    assert plan["limits"]["unchanged_known_files"] == 1
    assert "a_old_video.mov" not in str(plan)


def test_manual_sync_dry_run_retries_unchanged_historical_read_error(db, tmp_path):
    source_root = tmp_path / "manual_source"
    source_root.mkdir()
    recovered_path = source_root / "a_recovered.png"
    _write_png(recovered_path, (60, 70, 80))
    stat = recovered_path.stat()

    root = DynamicSourceRoot(
        label="fixture",
        root_path=str(source_root),
        root_path_hash="registered-root-hash",
        is_active=True,
    )
    db.add(root)
    db.flush()
    db.add(
        DynamicSourceItem(
            source_root_id=root.id,
            relative_path="a_recovered.png",
            relative_path_hash=service._hash_text("a_recovered.png"),
            file_size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            source_status="failed",
            sync_state="failed",
            import_status="failed",
            failure_reason="read_error",
        )
    )
    db.commit()

    plan = service.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=1,
        stable_age_seconds=0,
    )

    assert plan["counts"]["state_counts"]["import_planned"] == 1
    assert plan["counts"]["total_seen"] == 1
    assert plan["limits"]["unchanged_known_files"] == 0


def test_update_check_detects_changed_and_missing_items(db, tmp_path):
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)
    root = service.register_source_root(db, path=source_root, label="fixture")
    service.run_update_check(db, root_ids=[root.id])

    (source_root / "new.jpg").write_bytes(b"changed image content")
    (source_root / "other.png").unlink()
    result = service.run_update_check(db, root_ids=[root.id])

    assert result["changed_items"] == 1
    assert result["missing_items"] == 1
    summary = service.get_pending_summary(db)
    assert summary["pending_changed"] == 1
    assert summary["pending_deferred"] >= 3


def test_pending_summary_defaults_to_active_roots_and_reports_inactive_historical(db, tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "active.jpg").write_bytes(b"active")
    active_root = service.register_source_root(db, path=source_root, label="active")
    service.run_update_check(db, root_ids=[active_root.id])

    inactive_root = DynamicSourceRoot(
        label="inactive",
        root_path=str(tmp_path / "inactive"),
        root_path_hash="inactive-hash",
        is_active=False,
        auto_sync_enabled=False,
    )
    db.add(inactive_root)
    db.flush()
    db.add(
        DynamicSourceItem(
            source_root_id=inactive_root.id,
            relative_path="historical.jpg",
            relative_path_hash="historical-hash",
            sync_state="new",
            import_status="pending",
            classification_status="waiting_import",
            ai_tagging_status="waiting_import",
            localization_status="waiting_ai_tags",
        )
    )
    db.commit()

    active_summary = service.get_pending_summary(db)
    all_summary = service.get_pending_summary(db, include_inactive=True)

    assert active_summary["scope"] == "active_source_roots"
    assert active_summary["pending_new"] == 1
    assert active_summary["inactive_historical"]["pending_import"] == 1
    assert all_summary["scope"] == "all_source_roots"
    assert all_summary["pending_new"] == 2


def test_capped_update_check_skips_missing_reconciliation(db, tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    for name in ["a.jpg", "b.jpg", "c.jpg"]:
        (source_root / name).write_bytes(name.encode("utf-8"))

    root = service.register_source_root(db, path=source_root, label="fixture")
    service.run_update_check(db, root_ids=[root.id])

    capped = service.run_update_check(db, root_ids=[root.id], max_files=1)
    root_summary = capped["summary"]["root_summaries"][0]

    assert capped["missing_items"] == 0
    assert root_summary["partial_scan"] is True
    assert root_summary["missing_reconciliation_skipped"] is True
    assert root_summary["missing_reconciliation_reason"] == "max_files_cap"
    assert root_summary["counts"]["total_seen"] == 1
    assert capped["pending_summary"]["pending_new"] == 3
    assert capped["pending_summary"]["pending_deferred"] == 0

    items = db.query(DynamicSourceItem).all()
    assert len(items) == 3
    assert {item.source_status for item in items} == {"available"}
    assert {item.import_status for item in items} == {"pending"}
    assert "missing" not in {item.sync_state for item in items}


def test_walk_error_skips_missing_reconciliation_for_unseen_descendants(db, tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    subtree = source_root / "subtree"
    subtree.mkdir(parents=True)
    (source_root / "root.jpg").write_bytes(b"root")
    (subtree / "tracked.jpg").write_bytes(b"tracked")
    root = service.register_source_root(db, path=source_root, label="fixture")
    service.run_update_check(db, root_ids=[root.id])

    def fake_walk(path, onerror=None):
        yield str(path), ["subtree"], ["root.jpg"]
        if onerror is not None:
            onerror(OSError("permission denied"))

    monkeypatch.setattr(service.os, "walk", fake_walk)

    result = service.run_update_check(db, root_ids=[root.id])
    root_summary = result["summary"]["root_summaries"][0]
    tracked = (
        db.query(DynamicSourceItem)
        .filter(DynamicSourceItem.relative_path == "subtree/tracked.jpg")
        .one()
    )

    assert result["missing_items"] == 0
    assert root_summary["partial_scan"] is True
    assert root_summary["missing_reconciliation_skipped"] is True
    assert root_summary["missing_reconciliation_reason"] == "source_walk_error"
    assert root_summary["source_walk_error_count"] == 1
    assert "subtree" not in str(root_summary)
    assert tracked.source_status == "available"
    assert tracked.import_status == "pending"
    assert tracked.sync_state == "new"
    assert result["pending_summary"]["pending_new"] == 2
    assert result["pending_summary"]["pending_deferred"] == 0


def test_case_preserving_relative_path_hash_disambiguates_case_variants(db, tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "upper_fixture.jpg").write_bytes(b"upper")
    (source_root / "lower_fixture.jpg").write_bytes(b"lower")
    root = service.register_source_root(db, path=source_root, label="fixture")
    original_identity = service._relative_identity_and_preflight_reason

    def fake_identity(root_path, file_path):
        if file_path.name == "upper_fixture.jpg":
            return "A.jpg", None
        if file_path.name == "lower_fixture.jpg":
            return "a.jpg", None
        return original_identity(root_path, file_path)

    monkeypatch.setattr(service, "_relative_identity_and_preflight_reason", fake_identity)

    first = service.run_update_check(db, root_ids=[root.id])
    items = db.query(DynamicSourceItem).order_by(DynamicSourceItem.relative_path.asc()).all()
    first_hashes = {item.relative_path: item.relative_path_hash for item in items}
    first_run_items = (
        db.query(DynamicSyncRunItem)
        .filter(DynamicSyncRunItem.sync_run_id == first["id"])
        .all()
    )

    assert first["new_items"] == 2
    assert {item.relative_path for item in items} == {"A.jpg", "a.jpg"}
    assert len({item.relative_path_hash for item in items}) == 2
    assert len(first_run_items) == 2
    assert len({item.source_item_id for item in first_run_items}) == 2

    second = service.run_update_check(db, root_ids=[root.id])
    assert db.query(DynamicSourceItem).count() == 2
    assert {
        item.relative_path: item.relative_path_hash
        for item in db.query(DynamicSourceItem).all()
    } == first_hashes
    second_run_items = (
        db.query(DynamicSyncRunItem)
        .filter(DynamicSyncRunItem.sync_run_id == second["id"])
        .all()
    )
    assert len(second_run_items) == 2
    assert len({item.source_item_id for item in second_run_items}) == 2


def test_max_files_is_aggregate_across_selected_roots(db, tmp_path):
    root_a_path = tmp_path / "source_a"
    root_b_path = tmp_path / "source_b"
    root_a_path.mkdir()
    root_b_path.mkdir()
    for name in ["a1.jpg", "a2.jpg"]:
        (root_a_path / name).write_bytes(name.encode("utf-8"))
    for name in ["b1.jpg", "b2.jpg"]:
        (root_b_path / name).write_bytes(name.encode("utf-8"))

    root_a = service.register_source_root(db, path=root_a_path, label="a")
    root_b = service.register_source_root(db, path=root_b_path, label="b")
    result = service.run_update_check(db, root_ids=[root_a.id, root_b.id], max_files=2)
    root_summaries = result["summary"]["root_summaries"]

    assert result["total_seen"] == 2
    assert db.query(DynamicSourceItem).count() == 2
    assert root_summaries[0]["counts"]["total_seen"] == 2
    assert root_summaries[1]["counts"]["total_seen"] == 0
    assert root_summaries[1]["partial_scan"] is True
    assert root_summaries[1]["missing_reconciliation_skipped"] is True
    assert root_summaries[1]["missing_reconciliation_reason"] == "max_files_cap"
    assert result["pending_summary"]["pending_new"] == 2
    assert result["pending_summary"]["pending_deferred"] == 0


def test_root_id_selection_distinguishes_empty_none_and_specific(db, tmp_path):
    root_a_path = tmp_path / "source_a"
    root_b_path = tmp_path / "source_b"
    root_a_path.mkdir()
    root_b_path.mkdir()
    (root_a_path / "a.jpg").write_bytes(b"a")
    (root_b_path / "b.jpg").write_bytes(b"b")
    root_a = service.register_source_root(db, path=root_a_path, label="a")
    root_b = service.register_source_root(db, path=root_b_path, label="b")

    with pytest.raises(ValueError, match="root_ids must not be empty"):
        service.run_update_check(db, root_ids=[])
    assert db.query(DynamicSyncRun).count() == 0
    assert db.query(DynamicSourceItem).count() == 0

    specific = service.run_update_check(db, root_ids=[root_a.id])
    assert specific["total_seen"] == 1
    assert [summary["root_id"] for summary in specific["summary"]["root_summaries"]] == [root_a.id]
    assert db.query(DynamicSourceItem).count() == 1

    all_active = service.run_update_check(db, root_ids=None)
    assert all_active["total_seen"] == 2
    assert [summary["root_id"] for summary in all_active["summary"]["root_summaries"]] == [root_a.id, root_b.id]
    assert db.query(DynamicSourceItem).count() == 2


def test_update_check_rolls_back_before_marking_failed_run(db, tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "broken.jpg").write_bytes(b"broken")
    root = service.register_source_root(db, path=source_root, label="fixture")
    rollback_calls = []
    original_rollback = db.rollback

    def tracked_rollback():
        rollback_calls.append(True)
        original_rollback()

    def fail_record(*args, **kwargs):
        raise RuntimeError("simulated run item write failure")

    monkeypatch.setattr(db, "rollback", tracked_rollback)
    monkeypatch.setattr(service, "_record_file_observation", fail_record)

    with pytest.raises(RuntimeError, match="simulated run item write failure"):
        service.run_update_check(db, root_ids=[root.id])

    run = db.query(DynamicSyncRun).one()
    assert rollback_calls
    assert run.status == "failed"
    assert "simulated run item write failure" in run.error_message


def test_deferred_item_requeues_when_it_becomes_eligible_with_same_metadata(db, tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "retry.jpg").write_bytes(b"same bytes")
    root = service.register_source_root(db, path=source_root, label="fixture")

    calls = {"count": 0}

    def fake_scannable(_file_path, *, hydrated_only=True):
        calls["count"] += 1
        return "cloud_placeholder" if calls["count"] == 1 else None

    monkeypatch.setattr(service, "_is_scannable_file", fake_scannable)

    first = service.run_update_check(db, root_ids=[root.id])
    assert first["deferred_items"] == 1
    item = db.query(DynamicSourceItem).one()
    assert item.import_status == "deferred"
    first_size = item.file_size
    first_mtime_ns = item.mtime_ns

    second = service.run_update_check(db, root_ids=[root.id])
    db.refresh(item)

    assert second["new_items"] == 1
    assert item.file_size == first_size
    assert item.mtime_ns == first_mtime_ns
    assert item.source_status == "available"
    assert item.import_status == "pending"
    assert item.classification_status == "waiting_import"
    assert item.ai_tagging_status == "waiting_import"
    assert item.localization_status == "waiting_ai_tags"
    assert item.deferred_reason is None
    assert second["pending_summary"]["pending_new"] == 1
    assert second["pending_summary"]["pending_deferred"] == 0


def test_path_escape_is_item_level_deferred_without_missing_corruption(db, tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "normal.jpg").write_bytes(b"normal")
    (source_root / "escape.jpg").write_bytes(b"escape")
    root = service.register_source_root(db, path=source_root, label="fixture")
    original_identity = service._relative_identity_and_preflight_reason

    def fake_identity(root_path, file_path):
        if file_path.name == "escape.jpg":
            return "escape.jpg", "path_escape"
        return original_identity(root_path, file_path)

    monkeypatch.setattr(service, "_relative_identity_and_preflight_reason", fake_identity)

    result = service.run_update_check(db, root_ids=[root.id])
    items = {item.relative_path: item for item in db.query(DynamicSourceItem).all()}

    assert result["failed_items"] == 0
    assert result["missing_items"] == 0
    assert result["new_items"] == 1
    assert result["deferred_items"] == 1
    assert items["normal.jpg"].import_status == "pending"
    assert items["escape.jpg"].import_status == "deferred"
    assert items["escape.jpg"].deferred_reason == "path_escape"
    assert items["escape.jpg"].source_status == "deferred"


def test_threshold_default_100_and_override(db, tmp_path, monkeypatch):
    monkeypatch.setenv("DYNAMIC_LIBRARY_SYNC_THRESHOLD", "100")
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)
    root = service.register_source_root(db, path=source_root)
    service.run_update_check(db, root_ids=[root.id])
    assert service.get_pending_summary(db)["threshold_reached"] is False

    monkeypatch.setenv("DYNAMIC_LIBRARY_SYNC_THRESHOLD", "3")
    assert service.get_pending_summary(db)["threshold_reached"] is True


def test_source_root_safety_blocks_project_and_storage_paths(db, tmp_path):
    with pytest.raises(ValueError):
        service.register_source_root(db, path=ROOT, label="repo")


def test_source_root_identity_preserves_case_when_platform_does(monkeypatch):
    class FakePath:
        def __init__(self, value):
            self.value = value

        def resolve(self):
            return self

        def __str__(self):
            return self.value

    monkeypatch.setattr(service.os.path, "normcase", lambda value: value)

    upper = service._normalized_path_identity(FakePath("/data/CaseRoot"))
    lower = service._normalized_path_identity(FakePath("/data/caseroot"))

    assert upper != lower
    assert service._hash_text(upper) != service._hash_text(lower)


def test_source_root_identity_uses_platform_normcase_for_case_insensitive_paths(monkeypatch):
    class FakePath:
        def __init__(self, value):
            self.value = value

        def resolve(self):
            return self

        def __str__(self):
            return self.value

    monkeypatch.setattr(service.os.path, "normcase", lambda value: value.lower())

    upper = service._normalized_path_identity(FakePath("C:/Data/CaseRoot/"))
    lower = service._normalized_path_identity(FakePath("C:/Data/caseroot"))

    assert upper == lower


def test_localization_gap_and_proper_noun_safeguards(db, monkeypatch):
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_CATEGORIES", "general,meta")
    db.add_all([
        Tag(name="phase47_missing_general_alpha", category=TagCategoryEnum.general, post_count=10),
        Tag(name="phase47_missing_meta_beta", category=TagCategoryEnum.meta, post_count=8),
        Tag(name="phase47_missing_character_gamma", category=TagCategoryEnum.character, post_count=5),
        Tag(name="phase47_missing_copyright_delta", category=TagCategoryEnum.copyright, post_count=3),
    ])
    db.commit()

    gap = service.get_localization_gap_summary(db)
    assert gap["general_meta_missing"] >= 2
    assert gap["proper_noun_missing"] >= 2
    assert gap["worker_excludes_proper_nouns"] is True
    assert gap["proper_noun_policy"]["proper_noun_llm_requires_review"] is True
    assert gap["proper_noun_policy"]["search_alias_trust_sources"] == ["manual", "static", "operator_reviewed"]


def test_readiness_reports_ai_to_localization_chain_without_running_llm(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("AI_TAGGING_AUTO_LOCALIZATION", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_CATEGORIES", "general,meta")
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)
    service.register_source_root(db, path=source_root)

    readiness = service.get_production_readiness(db)
    chain = readiness["ai_localization_readiness"]["integration_chain"]
    assert "_schedule_localization" in chain
    assert "background worker / auto translate" in chain
    assert readiness["ai_localization_readiness"]["tag_localization"]["ai_to_localization_chain_ready"] is True
    assert readiness["ai_localization_readiness"]["tag_localization"]["gap_summary"]["worker_excludes_proper_nouns"] is True
    assert readiness["production_settings"]["auto_sync_enabled"] is False
    assert readiness["production_settings"]["manual_sync_enabled"] is False


def test_readiness_blocks_unreviewed_proper_noun_llm_aliases(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("AI_TAGGING_AUTO_LOCALIZATION", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_CATEGORIES", "general,meta")
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)
    service.register_source_root(db, path=source_root)
    db.add(
        TagTranslation(
            canonical_name="phase47_unreviewed_character_alias",
            language="zh-CN",
            display_name="unreviewed character",
            category="character",
            source="llm",
            status="translated",
            needs_review=True,
        )
    )
    db.commit()

    readiness = service.get_production_readiness(db)

    assert "unreviewed_proper_noun_llm_aliases_present" in readiness["blockers_before_s2"]
    assert readiness["s2_ready"] is False


def test_readiness_blocks_unreviewed_proper_noun_llm_alias_even_if_needs_review_false(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("AI_TAGGING_AUTO_LOCALIZATION", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_CATEGORIES", "general,meta")
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)
    service.register_source_root(db, path=source_root)
    db.add(
        TagTranslation(
            canonical_name="phase47_unreviewed_character_alias_needs_review_false",
            language="zh-CN",
            display_name="unreviewed character false",
            category="character",
            source="llm",
            status="translated",
            needs_review=False,
        )
    )
    db.commit()

    readiness = service.get_production_readiness(db)

    assert "unreviewed_proper_noun_llm_aliases_present" in readiness["blockers_before_s2"]
    assert readiness["s2_ready"] is False


def test_operator_readiness_separates_manual_and_background_warnings(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_AUTO_LOCALIZATION", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "false")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED", "true")
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)
    service.register_source_root(db, path=source_root)

    readiness = service.get_production_readiness(db)
    operator = readiness["manual_sync_operator_readiness"]

    assert operator["manual_execute_ready"] is False
    blocker_codes = [item["code"] for item in operator["manual_execute_blockers"]]
    assert "AI_TAGGING_ENABLED_false" in blocker_codes
    assert "TAG_TRANSLATION_LLM_ENABLED_false" in blocker_codes
    background_codes = {item["code"] for item in operator["background_warnings"]}
    assert "tag_translation_auto_and_background_disabled" in background_codes
    assert all("_false" not in item["label"] for item in operator["manual_execute_blockers"])
    assert operator["normal_operator_plan_endpoint"] == "POST /api/admin/dynamic-library-sync/manual-sync/plan"
    assert operator["legacy_update_check_endpoint"] == "POST /api/admin/dynamic-library-sync/check"


def test_operator_readiness_ready_for_manual_e2e_with_background_sync_off(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "heuristic")
    monkeypatch.setenv("AI_TAGGING_AUTO_LOCALIZATION", "false")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_API_KEY", "test-key")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_MODEL", "test-model")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "false")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED", "false")
    monkeypatch.setenv("S3B_UNATTENDED_SYNC_ENABLED", "false")
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)
    service.register_source_root(db, path=source_root)

    readiness = service.get_production_readiness(db)
    operator = readiness["manual_sync_operator_readiness"]

    assert operator["manual_execute_ready"] is True
    assert operator["manual_execute_blockers"] == []
    assert operator["cloud_placeholder_hydration_policy"] == "cloud_aware_non_destructive_read"
    assert readiness["production_settings"]["classification_enabled"] is True
    assert readiness["production_settings"]["ai_tagging_enabled"] is True
    assert readiness["production_settings"]["tag_translation_llm_enabled"] is True
    assert readiness["production_settings"]["tag_translation_llm_provider_configured"] is True
    background_codes = {item["code"] for item in operator["background_warnings"]}
    assert "AI_TAGGING_AUTO_LOCALIZATION_false" in background_codes
    assert "tag_translation_auto_and_background_disabled" in background_codes


def test_admin_api_register_check_pending_and_fail_closed_sync(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DYNAMIC_LIBRARY_SYNC_THRESHOLD", "100")
    source_root = tmp_path / "source"
    _seed_source_tree(source_root)

    register = client.post(
        "/api/admin/dynamic-library-sync/source-roots",
        json={"path": str(source_root), "label": "fixture"},
    )
    assert register.status_code == 200
    root_id = register.json()["id"]

    check = client.post(
        "/api/admin/dynamic-library-sync/check",
        json={"root_ids": [root_id], "hydrated_only": True},
    )
    assert check.status_code == 200
    assert check.json()["dry_run"] is True

    summary = client.get("/api/admin/dynamic-library-sync/pending-summary")
    assert summary.status_code == 200
    assert summary.json()["pending_new"] == 2

    readiness = client.get("/api/admin/dynamic-library-sync/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["manual_sync_execution_ready"] is False

    sync = client.post("/api/admin/dynamic-library-sync/sync-pending")
    assert sync.status_code == 409
    assert "disabled by default" in sync.json()["detail"]
