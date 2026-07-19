import os
import sys
import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.enums import FileTypeEnum  # noqa: E402
from app.models import (  # noqa: E402
    AITagJob,
    ClassificationJob,
    ContentClassEnum,
    DynamicSourceItem,
    DynamicSourceRoot,
    DynamicSyncRun,
    DynamicSyncRunItem,
    Entity,
    Media,
    MediaEntityAssignment,
    SourceConcept,
    Tag,
    TagCategoryEnum,
    TagTranslation,
    blombooru_media_tags,
)
from app.routes.admin import ai_tagging as ai_tagging_routes  # noqa: E402
from app.routes.admin import ai_tagging_jobs as ai_job_routes  # noqa: E402
from app.routes.admin import content_classification as classification_routes  # noqa: E402
from app.routes.admin import dynamic_library_sync as dynamic_routes  # noqa: E402
from app.services import dynamic_library_sync_service as planner  # noqa: E402
from app.services import manual_sync_execute_service as execute_service  # noqa: E402
from app.services.dynamic_library_sync_service import S3A_M2_MANUAL_EXECUTE_CONFIRMATION_PREFIX  # noqa: E402
from app.services.manual_sync_execute_service import (  # noqa: E402
    ManualSyncExecuteError,
    create_manual_sync_execute_run,
    execute_manual_sync_run,
)
from app.utils.media_processor import calculate_file_hash  # noqa: E402
from scripts import run_s3a_m1_manual_sync_execute as runner  # noqa: E402


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
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _write_png(path: Path, color: tuple[int, int, int] = (1, 2, 3)) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (2, 2), color)
    image.save(path)


def _patch_test_storage(monkeypatch, tmp_path: Path) -> Path:
    storage = tmp_path / "storage"
    original = storage / "media" / "original"
    thumbs = storage / "media" / "thumbnails"
    original.mkdir(parents=True)
    thumbs.mkdir(parents=True)
    monkeypatch.setattr(settings, "STORAGE_ROOT", storage)
    monkeypatch.setattr(settings, "MEDIA_DIR", storage / "media")
    monkeypatch.setattr(settings, "ORIGINAL_DIR", original)
    monkeypatch.setattr(settings, "THUMBNAIL_DIR", thumbs)
    return storage


def _write_app_media(storage: Path, stored_path: str, color: tuple[int, int, int] = (1, 2, 3)) -> Path:
    app_path = storage / stored_path
    _write_png(app_path, color)
    return app_path


def _replace_execute_private_plan_items(db, run: DynamicSyncRun, items: list[dict]) -> None:
    summary = dict(run.summary_json or {})
    execute_payload = dict(summary["manual_sync_execute"])
    execute_payload["private_plan_items"] = items
    summary["manual_sync_execute"] = execute_payload
    run.summary_json = summary
    db.commit()


def _source_item_core_snapshot(item: DynamicSourceItem) -> dict:
    return {
        "source_status": item.source_status,
        "sync_state": item.sync_state,
        "import_status": item.import_status,
        "classification_status": item.classification_status,
        "ai_tagging_status": item.ai_tagging_status,
        "localization_status": item.localization_status,
        "failure_reason": item.failure_reason,
        "deferred_reason": item.deferred_reason,
        "media_id": item.media_id,
        "content_hash": item.content_hash,
    }


def _enable_manual_execute(monkeypatch) -> None:
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED", "true")
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("POSTGRES_DB", "blombooru_test")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "false")


def _fake_ai_job(**overrides):
    payload = {
        "id": 7,
        "status": "pending",
        "trigger_source": "manual",
        "scan_job_id": None,
        "media_ids_json": None,
        "max_items": 1,
        "dry_run": False,
        "only_without_ai_tags": True,
        "force_suggestions": False,
        "processed": 0,
        "tags_added": 0,
        "suggestions_added": 0,
        "skipped_locked": 0,
        "ignored_low_confidence": 0,
        "failed": 0,
        "failed_items_json": None,
        "error_message": None,
        "localization_status": None,
        "created_at": None,
        "started_at": None,
        "finished_at": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _fake_classification_job(**overrides):
    payload = {
        "id": 8,
        "status": "pending",
        "trigger_source": "manual",
        "scan_job_id": None,
        "media_ids_json": None,
        "max_items": 1,
        "only_unclassified": True,
        "force_reclassify": False,
        "processed": 0,
        "classified_anime": 0,
        "classified_non_anime": 0,
        "classified_unknown": 0,
        "failed": 0,
        "failed_items_json": None,
        "error_message": None,
        "created_at": None,
        "started_at": None,
        "finished_at": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_s3a_m1_blank_max_files_api_plan_matches_execute_default(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    _write_png(source_root / "one.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")

    body = dynamic_routes.ManualSyncDryRunPlanRequest(root_id=root.id, stable_age_seconds=0)
    plan = dynamic_routes.plan_manual_sync(body, current_user=SimpleNamespace(id=1), db=db)
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=None,
        hydrated_only=False,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    execute_payload = run.summary_json["manual_sync_execute"]
    assert plan["limits"]["max_files"] == execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES
    assert execute_payload["request"]["effective_max_files"] == execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES
    assert execute_payload["plan"]["integrity"]["plan_hash"] == plan["integrity"]["plan_hash"]


def test_s3a_m1_update_check_default_is_not_manual_execute_cap(monkeypatch):
    observed = {}

    def fake_update_check(_db, *, root_ids, max_files, hydrated_only):
        observed.update({"root_ids": root_ids, "max_files": max_files, "hydrated_only": hydrated_only})
        return {"id": 123, "status": "completed"}

    monkeypatch.setattr(dynamic_routes, "run_update_check", fake_update_check)

    result = asyncio.run(
        dynamic_routes.run_dynamic_update_check(
            dynamic_routes.UpdateCheckRequest(),
            current_user=SimpleNamespace(id=1),
            db=SimpleNamespace(),
        )
    )

    assert result["id"] == 123
    assert observed["max_files"] is None
    assert observed["hydrated_only"] is True


def test_s3a_m2_manual_execute_cap_can_be_raised_explicitly(monkeypatch):
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_MAX_FILES", "300")

    assert execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES == 5
    assert execute_service.manual_sync_execute_max_files_cap() == 300
    assert execute_service.manual_sync_execute_effective_max_files(None) == 300
    assert execute_service.manual_sync_execute_effective_max_files(300) == 300

    with pytest.raises(ManualSyncExecuteError) as exc:
        execute_service.manual_sync_execute_effective_max_files(301)

    assert exc.value.code == "manual_sync_execute_max_files_exceeded"


def test_s3a_m1_manual_and_update_check_use_separate_frontend_limits():
    admin_js = (ROOT / "frontend" / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    admin_html = (ROOT / "frontend" / "templates" / "admin.html").read_text(encoding="utf-8")

    assert "dynamic-sync-execute-max-files" in admin_html
    assert "dynamic-sync-check-max-files" in admin_html
    assert "dynamic-sync-execute-cap" in admin_html
    assert "document.getElementById('dynamic-sync-execute-max-files')" in admin_js
    assert "document.getElementById('dynamic-sync-check-max-files')" in admin_js
    assert "manual_execute_max_files_cap" in admin_js
    assert "manual_sync_execute_enabled" in admin_js
    assert "_manualSyncExpectedConfirmationPhrase" in admin_js
    assert "production_acceptance_approved" in admin_js


def test_s3a_m1_plan_integrity_is_public_safe_and_stable(db, tmp_path):
    source_root = tmp_path / "source"
    image_path = source_root / "private-name.png"
    _write_png(image_path)

    plan_time = planner._utcnow()
    first = planner.plan_manual_sync_dry_run(db, source_path=source_root, max_files=5, stable_age_seconds=0, now=plan_time)
    second = planner.plan_manual_sync_dry_run(db, source_path=source_root, max_files=5, stable_age_seconds=0, now=plan_time)
    later = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=5,
        stable_age_seconds=0,
        now=plan_time + timedelta(seconds=1),
    )

    assert first["integrity"]["plan_hash"] == second["integrity"]["plan_hash"]
    assert first["integrity"]["plan_hash"] != later["integrity"]["plan_hash"]
    assert first["integrity"]["confirmation_phrase"].startswith(S3A_M2_MANUAL_EXECUTE_CONFIRMATION_PREFIX)
    assert first["integrity"]["hash_excludes_paths"] is True
    assert first["integrity"]["hash_includes_private_content_fingerprint"] is False
    assert "private-name.png" not in str(first)
    assert str(source_root) not in str(first)
    advanced = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        max_files=5,
        stable_age_seconds=0,
        now=plan_time,
        plan_mode="advanced_full_rescan",
    )
    assert advanced["integrity"]["hash_includes_private_content_fingerprint"] is True


def test_s3a_m1_plan_hash_is_stable_across_directory_walk_order(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    _write_png(source_root / "z_dir" / "z.png")
    _write_png(source_root / "a_dir" / "a.png")
    plan_time = planner._utcnow()
    orders = iter((["z_dir", "a_dir"], ["a_dir", "z_dir"], ["a_dir", "z_dir"]))

    def fake_walk(path, onerror=None):
        path = Path(path)
        if path == source_root:
            dirnames = list(next(orders))
            yield str(path), dirnames, []
            for dirname in dirnames:
                yield from fake_walk(path / dirname, onerror=onerror)
            return
        if path.name == "a_dir":
            yield str(path), [], ["a.png"]
            return
        if path.name == "z_dir":
            yield str(path), [], ["z.png"]
            return
        yield str(path), [], []

    monkeypatch.setattr(planner.os, "walk", fake_walk)

    first = planner.plan_manual_sync_dry_run(db, source_path=source_root, max_files=5, stable_age_seconds=0, now=plan_time)
    second = planner.plan_manual_sync_dry_run(db, source_path=source_root, max_files=5, stable_age_seconds=0, now=plan_time)

    assert first["integrity"]["plan_hash"] == second["integrity"]["plan_hash"]
    assert [item["relative_path_hash"] for item in first["ledger"]["per_file_public_records"]] == [
        item["relative_path_hash"] for item in second["ledger"]["per_file_public_records"]
    ]


def test_s3a_m1_execute_recheck_accepts_changed_directory_walk_order(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)
    source_root = tmp_path / "source"
    _write_png(source_root / "z_dir" / "z.png")
    _write_png(source_root / "a_dir" / "a.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    orders = iter((["z_dir", "a_dir"], ["a_dir", "z_dir"], ["z_dir", "a_dir"]))

    def fake_walk(path, onerror=None):
        path = Path(path)
        if path == source_root:
            dirnames = list(next(orders))
            yield str(path), dirnames, []
            for dirname in dirnames:
                yield from fake_walk(path / dirname, onerror=onerror)
            return
        if path.name == "a_dir":
            yield str(path), [], ["a.png"]
            return
        if path.name == "z_dir":
            yield str(path), [], ["z.png"]
            return
        yield str(path), [], []

    monkeypatch.setattr(planner.os, "walk", fake_walk)
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert db.get(DynamicSyncRun, run.id).status == "completed"
    db.refresh(root)
    assert root.last_checked_at is not None


def test_s3a_m1_execute_is_disabled_by_default(db, tmp_path):
    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "manual_sync_execute_disabled"
    assert db.query(Media).count() == 0


def test_s3a_m1_execute_rejects_stale_plan_hash(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    image_path = source_root / "new.png"
    _write_png(image_path, (1, 2, 3))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )
    _write_png(image_path, (90, 80, 70))

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "stale_or_mismatched_plan_hash"
    assert db.query(Media).count() == 0


def test_s3a_m1_execute_rejects_partial_scan_plan(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    plan = planner.plan_manual_sync_dry_run(db, source_path=source_root, max_files=5, stable_age_seconds=0)
    plan["counts"]["partial_scan"] = True

    with pytest.raises(ManualSyncExecuteError) as exc:
        execute_service._verify_execute_gates(
            db=db,
            plan=plan,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
            hydrated_only=True,
            production_acceptance_approved=False,
        )

    assert exc.value.code == "manual_sync_plan_partial_scan"


def test_manual_sync_execute_gate_allows_safe_cap_limited_batch(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    for index in range(2):
        _write_png(source_root / f"new_{index}.png", (10 + index, 20, 30))
    plan = planner.plan_manual_sync_dry_run(db, source_path=source_root, max_files=1, stable_age_seconds=0)

    assert plan["counts"]["partial_scan"] is True
    assert plan["counts"]["partial_scan_reason"] == "cap_limited_actionable_batch"
    assert plan["counts"]["batch_executable"] is True

    execute_service._verify_execute_gates(
        db=db,
        plan=plan,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
        hydrated_only=True,
        production_acceptance_approved=False,
    )

    plan["counts"]["unsafe_partial_scan"] = True
    plan["counts"]["partial_scan_reason"] = "no_progress_timeout"
    with pytest.raises(ManualSyncExecuteError) as exc:
        execute_service._verify_execute_gates(
            db=db,
            plan=plan,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
            hydrated_only=True,
            production_acceptance_approved=False,
        )
    assert exc.value.code == "manual_sync_plan_partial_scan"


def test_manual_sync_execute_rejects_advanced_full_rescan_retry_source_even_with_valid_confirmation(
    db,
    tmp_path,
    monkeypatch,
):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    timeout_file = source_root / "a_timeout.png"
    valid_file = source_root / "b_valid.png"
    _write_png(timeout_file)
    _write_png(valid_file)
    root = planner.register_source_root(db, path=source_root, label="fixture")

    def fake_hash(path: Path, _timeout_sec: int):
        if path.name == timeout_file.name:
            return None, "read_timeout"
        return calculate_file_hash(path), None

    monkeypatch.setattr(planner, "_verify_supported_image_file_with_timeout", lambda _path, _timeout_sec: None)
    monkeypatch.setattr(planner, "_calculate_manual_plan_file_hash", fake_hash)

    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )

    assert plan["counts"]["work_item_counts"]["RETRY_SOURCE"] == 1
    assert plan["counts"]["batch_executable"] is False
    assert plan["limits"]["advanced_full_rescan_retry_source_execution_not_validated"] is True

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            plan_mode="advanced_full_rescan",
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "advanced_full_rescan_retry_source_execute_not_validated"
    assert db.query(DynamicSyncRun).filter(DynamicSyncRun.run_type == "manual_sync_execute").count() == 0

    with pytest.raises(HTTPException) as api_exc:
        dynamic_routes.execute_manual_sync(
            dynamic_routes.ManualSyncExecuteRequest(
                root_id=root.id,
                max_files=5,
                hydrated_only=True,
                stable_age_seconds=0,
                plan_mode="advanced_full_rescan",
                expected_plan_hash=plan["integrity"]["plan_hash"],
                confirmation_phrase=plan["integrity"]["confirmation_phrase"],
                plan_created_at=plan["job"]["created_at"],
            ),
            current_user=SimpleNamespace(id=1),
            db=db,
        )

    assert api_exc.value.status_code == 409
    assert api_exc.value.detail["code"] == "advanced_full_rescan_retry_source_execute_not_validated"
    assert db.query(DynamicSyncRun).filter(DynamicSyncRun.run_type == "manual_sync_execute").count() == 0


def test_s3a_m1_execute_rejects_old_hash_with_forged_fresh_timestamp(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    old_time = planner._utcnow() - timedelta(seconds=30)
    old_plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        now=old_time,
    )
    forged_fresh_plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        now=planner._utcnow(),
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=old_plan["integrity"]["plan_hash"],
            confirmation_phrase=old_plan["integrity"]["confirmation_phrase"],
            plan_created_at=forged_fresh_plan["job"]["created_at"],
        )

    assert exc.value.code == "stale_or_mismatched_plan_hash"
    assert db.query(Media).count() == 0


def test_s3a_m1_execute_rejects_second_pending_run(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )

    first = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            plan_mode="advanced_full_rescan",
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "manual_sync_execute_already_active"
    assert first.status == "pending"
    assert db.query(DynamicSyncRun).filter(DynamicSyncRun.run_type == "manual_sync_execute").count() == 1


def test_s3a_m1_execute_records_content_change_as_item_failure(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "new.png"
    _write_png(source_file, (1, 2, 3))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    _write_png(source_file, (90, 80, 70))

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed_with_failures"
    assert result["manual_sync_execute"]["outcome_counts"]["content_changed_after_plan"] == 1
    assert db.get(DynamicSyncRun, run.id).status == "completed_with_failures"
    assert db.query(Media).count() == 0
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.failure_reason == "content_changed_after_plan"


def test_s3a_m1_execute_does_not_rehash_skipped_existing_noop_without_content_hash(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "existing.png"
    _write_png(source_file)
    existing_hash = calculate_file_hash(source_file)
    db.add(
        Media(
            filename="existing.png",
            path="media/original/existing.png",
            hash=existing_hash,
            file_type=FileTypeEnum.image,
        )
    )
    db.commit()
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    plan_item = plan["ledger"]["per_file_public_records"][0]
    assert plan_item["work_item_kind"] == "NOOP_DIAGNOSTIC"
    assert plan_item["can_execute"] is False
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    payload = json.loads(json.dumps(run.summary_json))
    payload["manual_sync_execute"]["private_plan_items"][0]["content_hash"] = None
    run.summary_json = payload
    db.commit()

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert result["manual_sync_execute"]["outcome_counts"]["skipped_existing_media"] == 1
    assert result["manual_sync_execute"]["outcome_counts"].get("plan_integrity_missing_content_hash", 0) == 0
    assert all(
        item.failure_reason != "plan_integrity_missing_content_hash"
        for item in db.query(DynamicSourceItem).all()
    )


def test_s3a_m1_dev_execute_imports_without_ai_or_llm_side_effects(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "new.png"
    _write_png(source_file)
    before = source_file.read_bytes()
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert db.query(Media).count() == 1
    assert source_file.read_bytes() == before
    assert db.query(DynamicSyncRunItem).count() == 1
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.import_status == "imported"
    assert source_item.classification_status == "skipped_classification_disabled"
    assert source_item.ai_tagging_status == "skipped_ai_tagging_disabled"
    assert source_item.localization_status == "blocked_ai_tagging_skipped"
    assert source_item.deferred_reason == "ai_tagging_disabled"
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["source_mutation_performed"] is False
    assert execute_summary["llm_calls_performed"] is False
    assert execute_summary["localization"]["status"] == "completed_noop_no_imports"
    assert execute_summary["localization"]["scheduled"] is False
    assert "private_plan_items" not in str(result)


def test_s3a_m1_import_preledger_exists_before_media_write(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)
    observed = {}

    def fake_process_and_save_media(*, db, file_path, unique_filename, **_kwargs):
        rows = db.query(DynamicSyncRunItem).all()
        observed["preledger_rows"] = [(row.item_state, row.action, row.media_id) for row in rows]
        assert observed["preledger_rows"] == [("import_in_progress", "import", None)]
        media = Media(
            filename=unique_filename,
            path=f"media/original/{unique_filename}",
            hash="unit-import-hash",
            file_type=FileTypeEnum.image,
            mime_type="image/png",
            file_size=Path(file_path).stat().st_size,
            width=2,
            height=2,
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        return media

    monkeypatch.setattr(execute_service, "process_and_save_media", fake_process_and_save_media)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    run_item = db.query(DynamicSyncRunItem).one()
    media = db.query(Media).one()
    assert observed["preledger_rows"] == [("import_in_progress", "import", None)]
    assert run_item.item_state == "imported_in_test"
    assert run_item.action == "import"
    assert run_item.media_id == media.id
    assert run_item.bytes_copied > 0


def test_s3a_m1_failed_import_keeps_preledger_without_public_path_leak(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)
    observed = {}

    def fake_process_and_save_media(**_kwargs):
        rows = db.query(DynamicSyncRunItem).all()
        observed["preledger_rows"] = [(row.item_state, row.action, row.media_id) for row in rows]
        assert observed["preledger_rows"] == [("import_in_progress", "import", None)]
        raise RuntimeError("simulated import failure for private_name.png")

    monkeypatch.setattr(execute_service, "process_and_save_media", fake_process_and_save_media)

    source_root = tmp_path / "source"
    _write_png(source_root / "private_name.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    private_hash = run.summary_json["manual_sync_execute"]["private_plan_items"][0]["content_hash"]

    result = execute_manual_sync_run(db, run_id=run.id)
    public_status = execute_service.serialize_manual_sync_execute_run(db.get(DynamicSyncRun, run.id))

    assert result["status"] == "completed_with_failures"
    assert observed["preledger_rows"] == [("import_in_progress", "import", None)]
    assert db.query(Media).count() == 0
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.item_state == "failed"
    assert run_item.action == "import"
    assert run_item.reason == "import_failed"
    assert run_item.media_id is None
    assert run_item.current_metadata_json["error_code"] == "RuntimeError"
    assert "private_name.png" not in str(public_status)
    assert "'content_hash':" not in str(public_status)
    assert private_hash not in str(public_status)


def test_s3a_m1_generic_sync_serializers_redact_private_plan_items(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    source_file = source_root / "private-name.png"
    _write_png(source_file)
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    private_items = run.summary_json["manual_sync_execute"]["private_plan_items"]
    private_hash = private_items[0]["content_hash"]
    assert private_items[0]["relative_path"] == "private-name.png"

    generic = planner.serialize_sync_run(run)
    dashboard = planner.get_dashboard_state(db)
    pending = planner.get_pending_summary(db)
    latest_job = execute_service.serialize_manual_sync_execute_run(run)

    for payload in (generic, dashboard, pending, latest_job):
        rendered = str(payload)
        assert "private_plan_items" not in rendered
        assert "'relative_path':" not in rendered
        assert "private-name.png" not in rendered
        assert private_hash not in rendered


def test_s3a_m1_execute_blocks_background_translation_llm_side_effects(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "true")

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "translation_llm_side_effects_enabled"
    assert db.query(Media).count() == 0


def test_s3a_m1_execute_blocks_auto_translation_llm_side_effects(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "true")

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "translation_llm_side_effects_enabled"
    assert db.query(Media).count() == 0


def test_s3a_m1_execute_blocks_live_translation_worker_with_flags_disabled(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setattr(
        execute_service,
        "_translation_worker_runtime_state",
        lambda: {"status": "idle", "thread_alive": True, "running": False},
    )

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "translation_llm_side_effects_enabled"
    assert "tag_translation_worker_idle" in execute_service._translation_side_effect_blockers()


def test_s3a_m1_execute_translation_gate_allows_stopped_worker_and_llm_disabled(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setattr(
        execute_service,
        "_translation_worker_runtime_state",
        lambda: {"status": "stopped", "thread_alive": False, "running": False},
    )

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    assert run.status == "pending"


def test_s3a_m1_execute_translation_gate_allows_llm_provider_when_auto_background_off(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "false")
    monkeypatch.setattr(
        execute_service,
        "_translation_worker_runtime_state",
        lambda: {"status": "stopped", "thread_alive": False, "running": False},
    )

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    assert run.status == "pending"
    assert execute_service._translation_side_effect_blockers() == []


def test_s3a_m1_execute_rejects_max_files_over_cap(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    _write_png(source_root / "one.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES,
        stable_age_seconds=0,
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=100000,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "manual_sync_execute_max_files_exceeded"


def test_s3a_m1_execute_requires_execute_enabled_flag(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED", "false")
    source_root = tmp_path / "source"
    _write_png(source_root / "one.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "manual_sync_execute_disabled"


def test_s3a_m1_execute_allows_max_files_within_cap_and_records_cap(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    source_root = tmp_path / "source"
    _write_png(source_root / "one.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES,
        stable_age_seconds=0,
    )

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    execute_payload = run.summary_json["manual_sync_execute"]
    assert execute_payload["request"]["effective_max_files"] == execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES
    assert execute_payload["request"]["execute_max_files_cap"] == execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES
    assert execute_payload["budgets"]["max_files"] == execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES


def test_s3a_m1_execute_blocks_active_ai_job(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setattr("app.services.ai_tagging_job_service.is_ai_job_active", lambda: True)
    monkeypatch.setattr("app.services.classification_job_service.is_classification_job_active", lambda: False)
    source_root = tmp_path / "source"
    _write_png(source_root / "one.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "ai_job_active_blocks_manual_sync_execute"


def test_s3a_m1_execute_blocks_active_classification_job(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setattr("app.services.ai_tagging_job_service.is_ai_job_active", lambda: False)
    monkeypatch.setattr("app.services.classification_job_service.is_classification_job_active", lambda: True)
    source_root = tmp_path / "source"
    _write_png(source_root / "one.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "classification_job_active_blocks_manual_sync_execute"


@pytest.mark.parametrize("status", ["pending", "running"])
def test_s3a_m1_execute_blocks_queued_ai_job(db, tmp_path, monkeypatch, status):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setattr("app.services.ai_tagging_job_service.is_ai_job_active", lambda: False)
    monkeypatch.setattr("app.services.classification_job_service.is_classification_job_active", lambda: False)
    source_root = tmp_path / "source"
    _write_png(source_root / "one.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )
    db.add(AITagJob(status=status, trigger_source="manual", max_items=1))
    db.commit()

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "ai_job_active_blocks_manual_sync_execute"


@pytest.mark.parametrize("status", ["pending", "running"])
def test_s3a_m1_execute_blocks_queued_classification_job(db, tmp_path, monkeypatch, status):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setattr("app.services.ai_tagging_job_service.is_ai_job_active", lambda: False)
    monkeypatch.setattr("app.services.classification_job_service.is_classification_job_active", lambda: False)
    source_root = tmp_path / "source"
    _write_png(source_root / "one.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )
    db.add(ClassificationJob(status=status, trigger_source="manual", max_items=1))
    db.commit()

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
        )

    assert exc.value.code == "classification_job_active_blocks_manual_sync_execute"


def test_s3a_m1_manual_execute_active_blocks_ai_job_start(db, monkeypatch):
    monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: True)
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    body = ai_job_routes.CreateAITagJobRequest(max_items=1)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(ai_job_routes.create_ai_tag_job(body=body, current_user=SimpleNamespace(id=1), db=db))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "manual_sync_execute_active_blocks_ai_job"


def test_s3a_m1_manual_execute_active_blocks_direct_ai_tagging(monkeypatch):
    monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: True)
    monkeypatch.setattr(ai_tagging_routes, "_get_session", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(ai_tagging_routes.tag_single_media(media_id=1, current_user=SimpleNamespace(id=1)))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "manual_sync_execute_active_blocks_ai_job"


def test_s3a_m1_manual_execute_active_blocks_classification_job_start(db, monkeypatch):
    monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: True)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    body = classification_routes.CreateClassificationJobRequest(max_items=1)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(classification_routes.create_classification_job(body=body, current_user=SimpleNamespace(id=1), db=db))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "manual_sync_execute_active_blocks_classification_job"


def test_s3a_m1_manual_execute_active_blocks_direct_classification(db, monkeypatch):
    monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: True)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            classification_routes.classify_single_media(
                media_id=1,
                current_user=SimpleNamespace(id=1),
                db=db,
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "manual_sync_execute_active_blocks_classification_job"


@pytest.mark.parametrize("status", ["pending", "running"])
def test_s3a_m1_manual_execute_db_row_blocks_ai_and_classification_job_start(db, monkeypatch, status):
    monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: False)
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    db.add(DynamicSyncRun(run_type="manual_sync_execute", mode="dev_test_execute", status=status, dry_run=False))
    db.commit()

    with pytest.raises(HTTPException) as ai_exc:
        asyncio.run(
            ai_job_routes.create_ai_tag_job(
                body=ai_job_routes.CreateAITagJobRequest(max_items=1),
                current_user=SimpleNamespace(id=1),
                db=db,
            )
        )
    with pytest.raises(HTTPException) as classification_exc:
        asyncio.run(
            classification_routes.create_classification_job(
                body=classification_routes.CreateClassificationJobRequest(max_items=1),
                current_user=SimpleNamespace(id=1),
                db=db,
            )
        )

    assert ai_exc.value.status_code == 409
    assert ai_exc.value.detail["code"] == "manual_sync_execute_active_blocks_ai_job"
    assert classification_exc.value.status_code == 409
    assert classification_exc.value.detail["code"] == "manual_sync_execute_active_blocks_classification_job"


def test_s3a_m1_pending_manual_execute_db_row_blocks_direct_ai_tagging(db, monkeypatch):
    class SessionProxy:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def close(self):
            return None

    monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: False)
    monkeypatch.setattr(ai_tagging_routes, "_get_session", lambda: SessionProxy(db))
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    db.add(DynamicSyncRun(run_type="manual_sync_execute", mode="dev_test_execute", status="pending", dry_run=False))
    db.commit()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(ai_tagging_routes.tag_single_media(media_id=1, current_user=SimpleNamespace(id=1)))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "manual_sync_execute_active_blocks_ai_job"


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_s3a_m1_finished_manual_execute_db_rows_do_not_block_job_start(db, monkeypatch, status):
    import app.services.ai_tagging_job_service as ai_job_service
    import app.services.classification_job_service as classification_job_service

    ai_started = []
    classification_started = []
    monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: False)
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("AI_TAGGING_BATCH_MAX_ITEMS", "10")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS", "10")
    monkeypatch.setattr(ai_job_service, "is_ai_job_active", lambda: False)
    monkeypatch.setattr(classification_job_service, "is_classification_job_active", lambda: False)
    monkeypatch.setattr(ai_job_service, "create_ai_tag_job", lambda *_args, **_kwargs: _fake_ai_job())
    monkeypatch.setattr(classification_job_service, "create_classification_job", lambda *_args, **_kwargs: _fake_classification_job())
    monkeypatch.setattr(ai_job_service, "start_ai_tag_job", lambda job_id: ai_started.append(job_id))
    monkeypatch.setattr(classification_job_service, "start_classification_job", lambda job_id: classification_started.append(job_id))
    db.add(DynamicSyncRun(run_type="manual_sync_execute", mode="dev_test_execute", status=status, dry_run=False))
    db.commit()

    ai_result = asyncio.run(
        ai_job_routes.create_ai_tag_job(
            body=ai_job_routes.CreateAITagJobRequest(max_items=1),
            current_user=SimpleNamespace(id=1),
            db=db,
        )
    )
    classification_result = asyncio.run(
        classification_routes.create_classification_job(
            body=classification_routes.CreateClassificationJobRequest(max_items=1),
            current_user=SimpleNamespace(id=1),
            db=db,
        )
    )

    assert ai_result["id"] == 7
    assert classification_result["id"] == 8
    assert ai_started == [7]
    assert classification_started == [8]


def test_s3a_m1_ai_job_start_still_works_when_manual_execute_inactive(db, monkeypatch):
    import app.services.ai_tagging_job_service as ai_job_service

    started = []
    monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: False)
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("AI_TAGGING_BATCH_MAX_ITEMS", "10")
    monkeypatch.setattr(ai_job_service, "is_ai_job_active", lambda: False)
    monkeypatch.setattr(ai_job_service, "create_ai_tag_job", lambda *_args, **_kwargs: _fake_ai_job())
    monkeypatch.setattr(ai_job_service, "start_ai_tag_job", lambda job_id: started.append(job_id))

    result = asyncio.run(
        ai_job_routes.create_ai_tag_job(
            body=ai_job_routes.CreateAITagJobRequest(max_items=1),
            current_user=SimpleNamespace(id=1),
            db=db,
        )
    )

    assert result["id"] == 7
    assert started == [7]


def test_s3a_m1_classification_job_start_still_works_when_manual_execute_inactive(db, monkeypatch):
    import app.services.classification_job_service as classification_job_service

    started = []
    monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: False)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS", "10")
    monkeypatch.setattr(classification_job_service, "is_classification_job_active", lambda: False)
    monkeypatch.setattr(classification_job_service, "create_classification_job", lambda *_args, **_kwargs: _fake_classification_job())
    monkeypatch.setattr(classification_job_service, "start_classification_job", lambda job_id: started.append(job_id))

    result = asyncio.run(
        classification_routes.create_classification_job(
            body=classification_routes.CreateClassificationJobRequest(max_items=1),
            current_user=SimpleNamespace(id=1),
            db=db,
        )
    )

    assert result["id"] == 8
    assert started == [8]


def test_s3a_m1_execute_skips_uncached_clip_without_download(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "clip")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    monkeypatch.setattr(execute_service, "_ensure_clip_model_cache_only", lambda: (False, "unit_uncached"))
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        include_private_details=True,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed_with_followup_required"
    assert db.query(Media).count() == 1
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.classification_status == "skipped_classification_model_uncached"
    assert source_item.ai_tagging_status == "blocked_classification_not_completed"
    assert source_item.localization_status == "blocked_classification_not_completed"
    assert result["manual_sync_execute"]["model_downloads_performed"] is False


def test_s3a_m1_clip_cache_gate_allows_cached_model_without_reset_failure(tmp_path, monkeypatch):
    calls = []
    cached_model = tmp_path / "vision_model.onnx"
    cached_model.write_bytes(b"cached")
    embeddings_file = tmp_path / "clip_text_embeddings.npz"
    embeddings_file.write_bytes(b"cached")
    fake_classifier = SimpleNamespace(
        _session=None,
        _text_embeddings=None,
        _load_session=lambda path: calls.append(("load_session", Path(path))),
        _load_text_embeddings=lambda: calls.append(("load_text_embeddings", None)),
    )
    fake_clip_module = SimpleNamespace(
        CLIP_REPO_ID="repo",
        CLIP_REVISION="rev",
        CLIP_VISION_FILE="model.onnx",
        EMBEDDINGS_FILE=embeddings_file,
        CLIPClassifier=lambda: fake_classifier,
    )
    fake_hf_module = SimpleNamespace(
        try_to_load_from_cache=lambda **_kwargs: calls.append(("cache_lookup", None)) or str(cached_model),
    )
    monkeypatch.setitem(sys.modules, "app.services.clip_classifier", fake_clip_module)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf_module)

    ready, reason = execute_service._ensure_clip_model_cache_only()

    assert ready is True
    assert reason is None
    assert ("cache_lookup", None) in calls
    assert ("load_session", cached_model) in calls
    assert ("load_text_embeddings", None) in calls


def test_s3a_m1_clip_cache_gate_does_not_enter_download_path(tmp_path, monkeypatch):
    calls = []
    fake_classifier = SimpleNamespace(
        _session=None,
        _text_embeddings=None,
        _load_session=lambda _path: calls.append("load_session"),
        _load_text_embeddings=lambda: calls.append("load_text_embeddings"),
    )
    fake_clip_module = SimpleNamespace(
        CLIP_REPO_ID="repo",
        CLIP_REVISION="rev",
        CLIP_VISION_FILE="model.onnx",
        EMBEDDINGS_FILE=tmp_path / "missing_embeddings.npz",
        CLIPClassifier=lambda: fake_classifier,
    )
    fake_hf_module = SimpleNamespace(
        try_to_load_from_cache=lambda **_kwargs: calls.append("cache_lookup") or None,
    )
    monkeypatch.setitem(sys.modules, "app.services.clip_classifier", fake_clip_module)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf_module)

    ready, reason = execute_service._ensure_clip_model_cache_only()

    assert ready is False
    assert reason == "classification_model_uncached"
    assert "cache_lookup" not in calls
    assert "load_session" not in calls


def test_s3a_m1_clip_cache_gate_uncached_model_skips_without_download(tmp_path, monkeypatch):
    calls = []
    embeddings_file = tmp_path / "clip_text_embeddings.npz"
    embeddings_file.write_bytes(b"cached")
    fake_classifier = SimpleNamespace(
        _session=None,
        _text_embeddings=None,
        _load_session=lambda _path: calls.append("load_session"),
        _load_text_embeddings=lambda: calls.append("load_text_embeddings"),
    )
    fake_clip_module = SimpleNamespace(
        CLIP_REPO_ID="repo",
        CLIP_REVISION="rev",
        CLIP_VISION_FILE="model.onnx",
        EMBEDDINGS_FILE=embeddings_file,
        CLIPClassifier=lambda: fake_classifier,
    )
    fake_hf_module = SimpleNamespace(
        try_to_load_from_cache=lambda **_kwargs: calls.append("cache_lookup") or None,
    )
    monkeypatch.setitem(sys.modules, "app.services.clip_classifier", fake_clip_module)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf_module)

    ready, reason = execute_service._ensure_clip_model_cache_only()

    assert ready is False
    assert reason == "classification_model_uncached"
    assert calls == ["cache_lookup"]


def test_s3a_m1_runner_defaults_to_local_manifest_reports(tmp_path):
    args = runner.build_parser().parse_args(["--source-path", str(tmp_path / "source")])

    assert args.max_files == execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES
    assert ".local_manifests" in args.report_json.parts
    assert ".local_manifests" in args.report_md.parts
    assert args.report_json.name == "manual-sync-runner-report.json"
    assert args.report_md.name == "manual-sync-runner-report.md"
    assert "docs" not in args.report_json.parts
    assert "docs" not in args.report_md.parts


def test_s3a_m1_runner_initializes_database_session_lazily(monkeypatch):
    calls = []

    class FakeSession:
        pass

    def fake_init_engine():
        calls.append("init_engine")
        runner.app_database.SessionLocal = lambda: FakeSession()

    monkeypatch.setattr(runner, "SessionLocal", None)
    monkeypatch.setattr(runner.app_database, "SessionLocal", None)
    monkeypatch.setattr(runner.app_database, "init_engine", fake_init_engine)

    session = runner._open_db_session()

    assert isinstance(session, FakeSession)
    assert calls == ["init_engine"]


def test_s3a_m1_runner_execute_report_uses_approved_plan_timestamp_and_hash(tmp_path, monkeypatch):
    approved_at = "2026-06-25T00:00:00+00:00"
    approved_plan = {
        "integrity": {"plan_hash": "approved-plan-hash"},
        "job": {"created_at": approved_at},
        "limits": {"max_files": execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES},
    }
    observed = {}
    source_root = tmp_path / "source"
    source_root.mkdir()

    class FakeDb:
        def get(self, _model, _id):
            return SimpleNamespace(id=123, root_path=str(source_root))

        def close(self):
            observed["closed"] = True

    def fake_plan(_db, **kwargs):
        observed["plan_now"] = kwargs.get("now")
        return {
            "integrity": {"plan_hash": "approved-plan-hash" if kwargs.get("now") else "fresh-plan-hash"},
            "job": {"created_at": kwargs.get("now").isoformat() if kwargs.get("now") else "fresh"},
            "limits": {"max_files": kwargs.get("max_files")},
        }

    def fake_create(_db, **kwargs):
        observed["create_kwargs"] = kwargs
        return SimpleNamespace(id=77, summary_json={"manual_sync_execute": {"plan": approved_plan}})

    written = {}
    monkeypatch.setattr(runner, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(runner, "plan_manual_sync_dry_run", fake_plan)
    monkeypatch.setattr(runner, "create_manual_sync_execute_run", fake_create)
    monkeypatch.setattr(runner, "execute_manual_sync_run", lambda _db, *, run_id: {"id": run_id, "status": "completed"})
    monkeypatch.setattr(runner, "_write_json", lambda _path, payload: written.setdefault("json", payload))
    monkeypatch.setattr(runner, "_write_markdown", lambda _path, payload: written.setdefault("md", payload))

    rc = runner.main(
        [
            "--root-id",
            "123",
            "--execute",
            "--expected-plan-hash",
            "approved-plan-hash",
            "--confirmation-phrase",
            "I APPROVE S3A-M1 MANUAL SYNC EXECUTE approved-plan-hash",
            "--plan-created-at",
            approved_at,
            "--report-json",
            str(tmp_path / "runner.json"),
            "--report-md",
            str(tmp_path / "runner.md"),
        ]
    )

    assert rc == 0
    assert observed["plan_now"].isoformat() == approved_at
    assert observed["create_kwargs"]["max_files"] == execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES
    assert written["json"]["plan"]["integrity"]["plan_hash"] == "approved-plan-hash"
    assert written["json"]["plan"] == approved_plan
    assert written["json"]["execution"]["status"] == "completed"


def test_s3a_m1_heuristic_manual_execute_does_not_bypass_classification_gate(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "heuristic")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD", "1")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD", "0.5")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    _patch_test_storage(monkeypatch, tmp_path)

    def fake_ai_tagging(_db_arg, _media_id):
        raise AssertionError("manual sync must not run WD tagging before classification has identified a target class")

    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed_with_followup_required"
    source_item = db.query(DynamicSourceItem).one()
    media = db.query(Media).one()
    assert source_item.ai_tagging_status == "blocked_classification_not_completed"
    assert source_item.localization_status == "blocked_classification_not_completed"
    assert source_item.deferred_reason == "classification_not_completed"
    assert source_item.classification_status == "classification_deferred_ai_tags_unavailable"
    assert media.content_class is None
    assert not db.execute(
        blombooru_media_tags.select().where(blombooru_media_tags.c.media_id == media.id)
    ).first()
    stage_rows = {row["name"]: row for row in result["manual_sync_execute"]["stage_rows"]}
    assert stage_rows["classification"]["method"] == "heuristic"
    assert stage_rows["classification"]["order"] == "classification_before_ai_tagging"
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.current_metadata_json["ai_tagging"]["status"] == "deferred"
    assert run_item.current_metadata_json["ai_tagging"]["reason"] == "classification_not_completed"


def test_manual_sync_normal_incremental_plan_runs_full_e2e_pipeline_with_stage_summary(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "clip")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setattr(execute_service, "_ensure_clip_model_cache_only", lambda: (True, None))
    _patch_test_storage(monkeypatch, tmp_path)

    calls: list[tuple[str, int]] = []

    def fake_copy(db_arg, source_file: Path):
        media = Media(
            filename=source_file.name,
            path=f"media/original/{source_file.name}",
            hash=calculate_file_hash(source_file),
            file_type=FileTypeEnum.image,
        )
        db_arg.add(media)
        db_arg.flush()
        calls.append(("import", media.id))
        return media.id, source_file.stat().st_size

    def fake_classify(db_arg, media_id: int):
        media = db_arg.get(Media, media_id)
        media.content_class = ContentClassEnum.anime
        calls.append(("classification", media_id))
        return {"media_id": media_id, "content_class": "anime", "method": "clip"}

    def fake_ai(db_arg, media_id: int):
        tag = Tag(name=f"unit_full_chain_{media_id}", category=TagCategoryEnum.general)
        db_arg.add(tag)
        db_arg.flush()
        db_arg.execute(
            blombooru_media_tags.insert().values(
                media_id=media_id,
                tag_id=tag.id,
                source="ai_wd",
                confidence=0.95,
                is_locked=False,
                is_suggestion=False,
            )
        )
        calls.append(("ai_tagging", media_id))
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0, "provenance": {"provider_backend": "unit"}}

    def fake_localization(db_arg, *, run, media_ids, source_item_ids=None, cancel_check=None):
        assert cancel_check is not None
        assert source_item_ids
        for source_item_id in source_item_ids or []:
            item = db_arg.get(DynamicSourceItem, int(source_item_id))
            item.localization_status = "localized"
        calls.append(("localization", len(media_ids)))
        return {
            "status": "completed",
            "failed": 0,
            "dynamic_source_items_updated": len(source_item_ids or []),
            "dynamic_source_items_target_status": "localized",
            "tags_requiring_localization_after_runner": 0,
            "llm_called": False,
            "provider_call_count": 0,
        }

    monkeypatch.setattr(execute_service, "_copy_and_import_media", fake_copy)
    monkeypatch.setattr(execute_service, "_classify_imported_media", fake_classify)
    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fake_localization)

    source_root = tmp_path / "source"
    _write_png(source_root / "one.png", (10, 20, 30))
    _write_png(source_root / "two.png", (30, 40, 50))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )
    assert plan["limits"]["plan_mode"] == "incremental"
    assert plan["limits"]["content_read_count"] == 0
    assert plan["limits"]["hash_required_count"] == 0
    assert plan["limits"]["image_decode_count"] == 0
    assert plan["counts"]["state_counts"]["import_planned"] == 2

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["outcome_counts"]["imported"] == 2
    assert execute_summary["outcome_counts"]["classified"] == 2
    assert execute_summary["outcome_counts"]["ai_tagged"] == 2
    assert execute_summary["outcome_counts"]["localized"] == 2
    assert execute_summary["last_heartbeat_at"]
    assert execute_summary["operator_status"] == "completed"
    assert execute_summary["operator_status_label_zh"].startswith("已完成")
    assert "IMPORT" in execute_summary["operator_labels"]["work_item_kinds"]
    assert execute_summary["work_item_counts"]["IMPORT"] == 2
    assert execute_summary["work_item_summary"]["import_work"] == 2
    assert execute_summary["work_item_summary"]["uses_work_item_kind_first_semantics"] is True
    stage_rows = {row["name"]: row for row in execute_summary["stage_rows"]}
    for stage in ("candidate_discovery", "import", "classification", "ai_tagging", "localization", "summary"):
        assert stage_rows[stage]["status"] == "completed"
        assert stage_rows[stage]["updated_at"]
    assert [name for name, _value in calls] == [
        "import",
        "import",
        "classification",
        "classification",
        "ai_tagging",
        "ai_tagging",
        "localization",
    ]
    assert {item.localization_status for item in db.query(DynamicSourceItem).all()} == {"localized"}


def test_manual_sync_execute_gates_ai_and_localization_by_content_class(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "clip")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_TARGET_CONTENT_CLASSES", "anime,illustration")
    monkeypatch.setattr(execute_service, "_ensure_clip_model_cache_only", lambda: (True, None))
    _patch_test_storage(monkeypatch, tmp_path)

    ai_calls: list[int] = []
    localization_media_ids: list[int] = []

    def fake_copy(db_arg, source_file: Path):
        media = Media(
            filename=source_file.name,
            path=f"media/original/{source_file.name}",
            hash=calculate_file_hash(source_file),
            file_type=FileTypeEnum.image,
        )
        db_arg.add(media)
        db_arg.flush()
        return media.id, source_file.stat().st_size

    def fake_classify(db_arg, media_id: int):
        media = db_arg.get(Media, media_id)
        if media.filename.startswith("anime"):
            media.content_class = ContentClassEnum.anime
            return {"media_id": media_id, "content_class": "anime", "method": "clip"}
        if media.filename.startswith("unknown"):
            media.content_class = ContentClassEnum.unknown
            return {"media_id": media_id, "content_class": "unknown", "method": "clip"}
        media.content_class = ContentClassEnum.non_anime
        return {"media_id": media_id, "content_class": "non_anime", "method": "clip"}

    def fake_ai(db_arg, media_id: int):
        media = db_arg.get(Media, media_id)
        assert media.content_class in {ContentClassEnum.anime, ContentClassEnum.unknown}
        ai_calls.append(media_id)
        tag = Tag(name=f"unit_target_gate_{media_id}", category=TagCategoryEnum.general)
        db_arg.add(tag)
        db_arg.flush()
        db_arg.execute(
            blombooru_media_tags.insert().values(
                media_id=media_id,
                tag_id=tag.id,
                source="ai_wd",
                confidence=0.95,
                is_locked=False,
                is_suggestion=False,
            )
        )
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0, "provenance": {"provider_backend": "unit"}}

    def fake_localization(db_arg, *, run, media_ids, source_item_ids=None, cancel_check=None):
        localization_media_ids.extend(media_ids)
        for source_item_id in source_item_ids or []:
            item = db_arg.get(DynamicSourceItem, int(source_item_id))
            item.localization_status = "localized"
        return {
            "status": "completed",
            "failed": 0,
            "dynamic_source_items_updated": len(source_item_ids or []),
            "dynamic_source_items_target_status": "localized",
            "tags_requiring_localization_after_runner": 0,
            "llm_called": False,
            "provider_call_count": 0,
        }

    monkeypatch.setattr(execute_service, "_copy_and_import_media", fake_copy)
    monkeypatch.setattr(execute_service, "_classify_imported_media", fake_classify)
    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fake_localization)

    source_root = tmp_path / "source"
    _write_png(source_root / "anime_target.png", (10, 20, 30))
    _write_png(source_root / "unknown_uncertain.png", (20, 30, 40))
    _write_png(source_root / "photo_non_target.png", (30, 40, 50))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        operator_confirmation_statement=plan["integrity"]["operator_confirmation_statement"],
        confirmation_phrase="",
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert len(ai_calls) == 2
    assert localization_media_ids == ai_calls
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["outcome_counts"]["imported"] == 3
    assert execute_summary["outcome_counts"]["classified"] == 3
    assert execute_summary["outcome_counts"]["ai_tagged"] == 2
    assert execute_summary["outcome_counts"]["ai_tagging_skipped_non_target"] == 1
    assert execute_summary["outcome_counts"]["localization_not_applicable_non_target"] == 1
    non_target_media = db.query(Media).filter(Media.filename == "photo_non_target.png").one()
    assert non_target_media.content_class == ContentClassEnum.non_anime
    assert not db.execute(
        blombooru_media_tags.select().where(blombooru_media_tags.c.media_id == non_target_media.id)
    ).first()
    non_target_item = (
        db.query(DynamicSourceItem)
        .filter(DynamicSourceItem.relative_path == "photo_non_target.png")
        .one()
    )
    assert non_target_item.ai_tagging_status == "ai_tagging_skipped_non_target"
    assert non_target_item.localization_status == "localization_not_applicable_non_target"
    unknown_media = db.query(Media).filter(Media.filename == "unknown_uncertain.png").one()
    assert unknown_media.content_class == ContentClassEnum.unknown
    assert db.execute(
        blombooru_media_tags.select().where(blombooru_media_tags.c.media_id == unknown_media.id)
    ).first()


def test_manual_sync_execute_enforces_source_policy_before_import(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    hidden_file = source_root / ".hidden.png"
    _write_png(hidden_file)
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
    )
    assert plan["counts"]["state_counts"]["import_planned"] == 1

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=False,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        operator_confirmation_statement=plan["integrity"]["operator_confirmation_statement"],
        confirmation_phrase="",
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["outcome_counts"]["skipped_unsupported"] == 1
    assert execute_summary["outcome_counts"]["hidden"] == 1
    assert execute_summary["outcome_counts"].get("imported", 0) == 0
    assert result["failed_items"] == 0
    assert db.query(Media).count() == 0
    item = db.query(DynamicSourceItem).one()
    assert item.sync_state == "skipped_unsupported"
    assert item.deferred_reason == "hidden"


def test_s3a_m1_manual_execute_ai_proper_nouns_follow_mature_media_tag_policy(db, tmp_path, monkeypatch):
    import app.services.ai_tagging_service as ai_tagging_service

    class FakeTagger:
        is_loaded = True
        current_model = "unit"

        def ensure_loaded(self, *_args, **_kwargs):
            return None

        def get_runtime_provenance(self, **kwargs):
            return {"provider": "unit", **kwargs}

        def predict_from_file(self, *_args, **_kwargs):
            return [
                {"name": "hakurei_reimu", "category": "character", "confidence": 0.99},
                {"name": "touhou", "category": "copyright", "confidence": 0.98},
                {"name": "zun", "category": "artist", "confidence": 0.97},
                {"name": "edge_character", "category": "character", "confidence": 0.25},
            ]

    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    _patch_test_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(ai_tagging_service, "_get_tagger", lambda: FakeTagger())

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    rows = {
        row.name: row
        for row in db.query(
            Tag.name,
            Tag.category,
            blombooru_media_tags.c.source,
            blombooru_media_tags.c.is_suggestion,
            blombooru_media_tags.c.is_locked,
        )
        .join(blombooru_media_tags, Tag.id == blombooru_media_tags.c.tag_id)
        .all()
    }
    assert rows["hakurei_reimu"].category == TagCategoryEnum.character
    assert rows["hakurei_reimu"].source == "ai_wd"
    assert rows["hakurei_reimu"].is_suggestion is False
    assert rows["hakurei_reimu"].is_locked is False
    assert rows["touhou"].category == TagCategoryEnum.copyright
    assert rows["touhou"].is_suggestion is False
    assert rows["zun"].category == TagCategoryEnum.artist
    assert rows["zun"].is_suggestion is False
    assert rows["edge_character"].category == TagCategoryEnum.character
    assert rows["edge_character"].is_suggestion is True
    assert db.query(Entity).count() == 0
    assert db.query(MediaEntityAssignment).count() == 0
    assert db.query(SourceConcept).count() == 0
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.current_metadata_json["ai_tagging"]["suggestions_added"] == 1
    assert run_item.current_metadata_json["ai_tagging"]["tags_added"] == 3


def test_s3a_m2_manual_execute_ai_policy_confirms_mature_categories_and_suggests_low_confidence(
    db,
    tmp_path,
    monkeypatch,
):
    import app.services.ai_tagging_service as ai_tagging_service

    class FakeTagger:
        is_loaded = True
        current_model = "unit"

        def ensure_loaded(self, *_args, **_kwargs):
            return None

        def get_runtime_provenance(self, **kwargs):
            return {"provider": "unit", **kwargs}

        def predict_from_file(self, *_args, **_kwargs):
            return [
                {"name": "long_hair", "category": "general", "confidence": 0.92},
                {"name": "hakurei_reimu", "category": "character", "confidence": 0.99},
                {"name": "touhou", "category": "copyright", "confidence": 0.99},
                {"name": "zun", "category": "artist", "confidence": 0.99},
                {"name": "blue_ribbon", "category": "general", "confidence": 0.25},
                {"name": "edge_character", "category": "character", "confidence": 0.25},
            ]

    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("AI_GENERAL_THRESHOLD", "0.35")
    monkeypatch.setenv("AI_CHARACTER_THRESHOLD", "0.85")
    monkeypatch.setenv("AI_SUGGESTION_THRESHOLD", "0.20")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(ai_tagging_service, "_get_tagger", lambda: FakeTagger())
    db.add_all(
        [
            TagTranslation(
                canonical_name="long_hair",
                language="zh-CN",
                display_name="长发",
                source="manual",
                status="reviewed",
                category="general",
            ),
            TagTranslation(
                canonical_name="blue_ribbon",
                language="zh-CN",
                display_name="蓝丝带",
                source="manual",
                status="reviewed",
                category="general",
            ),
        ]
    )
    db.commit()

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        include_private_details=True,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    rows = {
        row.name: row
        for row in db.query(
            Tag.name,
            blombooru_media_tags.c.is_suggestion,
            blombooru_media_tags.c.is_locked,
        )
        .join(blombooru_media_tags, Tag.id == blombooru_media_tags.c.tag_id)
        .all()
    }
    assert rows["long_hair"].is_suggestion is False
    assert rows["long_hair"].is_locked is False
    assert rows["hakurei_reimu"].is_suggestion is False
    assert rows["touhou"].is_suggestion is False
    assert rows["zun"].is_suggestion is False
    assert rows["blue_ribbon"].is_suggestion is True
    assert rows["edge_character"].is_suggestion is True
    assert db.query(Entity).count() == 0
    assert db.query(MediaEntityAssignment).count() == 0
    assert db.query(SourceConcept).count() == 0
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.current_metadata_json["ai_tagging"]["tags_added"] == 4
    assert run_item.current_metadata_json["ai_tagging"]["suggestions_added"] == 2
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.localization_status == "localized"
    localization = result["manual_sync_execute"]["localization"]
    assert localization["status"] == "completed_existing_coverage"
    assert localization["localizable_distinct_tags"] == 2
    assert localization["tags_requiring_localization_after_runner"] == 0
    assert localization["llm_called"] is False


def test_manual_sync_localization_save_failure_rolls_back_and_records_failure(db, tmp_path, monkeypatch):
    import app.services.llm_translation_provider as llm_provider_module
    import app.services.tag_localization_service as localization_service

    class FakeProvider:
        def is_available(self):
            return True

        async def translate_tags(self, inputs):
            return [
                SimpleNamespace(
                    canonical_name=inputs[0]["name"],
                    display_name_zh="单元测试标签",
                    aliases_zh=[],
                    confidence=0.9,
                    needs_review=False,
                )
            ]

        def get_provider_name(self):
            return "unit"

    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_API_KEY", "test-key")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_MODEL", "test-model")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    _patch_test_storage(monkeypatch, tmp_path)

    def fake_ai_tagging(db_arg, media_id):
        tag = Tag(name="unit_needs_localization", category=TagCategoryEnum.general)
        db_arg.add(tag)
        db_arg.flush()
        db_arg.execute(
            blombooru_media_tags.insert().values(
                media_id=media_id,
                tag_id=tag.id,
                source="ai_wd",
                confidence=0.99,
                is_locked=False,
                is_suggestion=False,
            )
        )
        db_arg.commit()
        return {
            "media_id": media_id,
            "tags_added": 1,
            "suggestions_added": 0,
            "predictions": [{"name": "unit_needs_localization", "confidence": 0.99, "action": "confirmed"}],
            "provenance": {"provider_backend": "unit"},
        }

    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)
    monkeypatch.setattr(llm_provider_module, "get_llm_provider", lambda: FakeProvider())

    def raise_upsert(*_args, **_kwargs):
        raise RuntimeError("simulated db write failure")

    monkeypatch.setattr(localization_service, "upsert_translation", raise_upsert)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    localization = result["manual_sync_execute"]["localization"]
    assert result["status"] == "completed_with_failures"
    assert localization["status"] == "blocked_localization_gap_remaining"
    assert localization["errors"] == ["translation_save_failed"]
    assert localization["failed"] == 1
    assert result["failed_items"] == 1
    assert result["manual_sync_execute"]["external_provider_calls_performed"] is True
    assert db.query(DynamicSourceItem).one().localization_status == "deferred"


def test_manual_sync_execute_processes_imported_downstream_followup(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "followup.png"
    _write_png(source_file)
    root = planner.register_source_root(db, path=source_root, label="fixture")
    media = Media(
        filename="followup.png",
        path="media/original/followup.png",
        hash=calculate_file_hash(source_file),
        file_type=FileTypeEnum.image,
        content_class="anime",
    )
    _write_png(settings.ORIGINAL_DIR / "followup.png")
    db.add(media)
    db.flush()
    source_stat = source_file.stat()
    db.add(
        DynamicSourceItem(
            source_root_id=root.id,
            relative_path="followup.png",
            relative_path_hash=planner._hash_text("followup.png"),
            file_size=source_stat.st_size,
            mtime_ns=source_stat.st_mtime_ns,
            content_hash=media.hash,
            source_status="available",
            sync_state="imported",
            import_status="imported",
            classification_status="classified",
            ai_tagging_status="failed_ai_tagger_model_uncached",
            localization_status="blocked_ai_tagging_failed",
            media_id=media.id,
        )
    )
    db.commit()

    ai_calls: list[int] = []
    localization_calls: list[list[int]] = []

    def fake_ai_tagging(_db_arg, media_id):
        ai_calls.append(media_id)
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0}

    def fake_localization(_db_arg, *, run, media_ids, source_item_ids=None, cancel_check=None):
        localization_calls.append(list(media_ids))
        assert source_item_ids
        assert cancel_check is not None
        item = db.get(DynamicSourceItem, int(source_item_ids[0]))
        item.localization_status = "localized"
        return {
            "status": "completed",
            "failed": 0,
            "dynamic_source_items_updated": len(media_ids),
            "dynamic_source_items_target_status": "localized",
            "tags_requiring_localization_after_runner": 0,
            "llm_called": False,
            "provider_call_count": 0,
        }

    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fake_localization)

    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        include_private_details=True,
    )
    assert plan["counts"]["state_counts"]["downstream_followup_planned"] == 1
    assert plan["counts"]["estimated_import_count"] == 0
    assert plan["counts"]["estimated_ai_tagging_count"] == 1
    followup_item = next(
        item for item in plan["private_details"]["items"] if item["state"] == "downstream_followup_planned"
    )
    assert followup_item["content_hash"] == media.hash

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["outcome_counts"]["downstream_followup_planned"] == 1
    assert execute_summary["outcome_counts"]["ai_tagged"] == 1
    assert execute_summary["imported_media_ids"] == []
    assert execute_summary["downstream_media_ids"] == [media.id]
    assert ai_calls == [media.id]
    assert localization_calls == [[media.id]]
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.item_state == "downstream_followup_planned"
    assert run_item.action == "downstream_followup"
    assert run_item.media_id == media.id
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.import_status == "imported"
    assert source_item.content_hash == media.hash
    assert source_item.ai_tagging_status == "ai_tagged"
    assert source_item.localization_status == "localized"


def test_manual_sync_execute_marks_deferred_localization_gap_as_followup_required(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    _patch_test_storage(monkeypatch, tmp_path)

    def fake_ai_tagging(db_arg, media_id):
        tag = Tag(name="unit_deferred_localization_tag", category=TagCategoryEnum.general)
        db_arg.add(tag)
        db_arg.flush()
        db_arg.execute(
            blombooru_media_tags.insert().values(
                media_id=media_id,
                tag_id=tag.id,
                source="ai_wd",
                confidence=0.95,
                is_locked=False,
                is_suggestion=False,
            )
        )
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0}

    def fake_localization(db_arg, *, run, media_ids, source_item_ids=None, cancel_check=None):
        for source_item_id in source_item_ids or []:
            item = db_arg.get(DynamicSourceItem, int(source_item_id))
            item.localization_status = "deferred"
            item.deferred_reason = "localization_backend_disabled"
        return {
            "status": "blocked_localization_gap_remaining",
            "blocked_reason": "localization_backend_disabled",
            "failed": 0,
            "dynamic_source_items_updated": len(source_item_ids or []),
            "dynamic_source_items_target_status": "deferred",
            "tags_requiring_localization_after_runner": 1,
            "llm_called": False,
            "provider_call_count": 0,
        }

    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fake_localization)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed_with_followup_required"
    assert result["failed_items"] == 0
    assert result["manual_sync_execute"]["outcome_counts"]["localization_deferred"] == 1
    assert result["manual_sync_execute"]["localization"]["status"] == "blocked_localization_gap_remaining"


def test_manual_sync_execute_allows_downstream_followup_when_source_file_missing(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "followup-missing-source.png"
    _write_png(source_file)
    root = planner.register_source_root(db, path=source_root, label="fixture")
    media = Media(
        filename="followup-missing-source.png",
        path="media/original/followup-missing-source.png",
        hash=calculate_file_hash(source_file),
        file_type=FileTypeEnum.image,
    )
    _write_png(settings.ORIGINAL_DIR / "followup-missing-source.png")
    db.add(media)
    db.flush()
    source_stat = source_file.stat()
    db.add(
        DynamicSourceItem(
            source_root_id=root.id,
            relative_path=source_file.name,
            relative_path_hash=planner._hash_text(source_file.name),
            file_size=source_stat.st_size,
            mtime_ns=source_stat.st_mtime_ns,
            content_hash=media.hash,
            source_status="available",
            sync_state="imported",
            import_status="imported",
            classification_status="classified",
            ai_tagging_status="failed_ai_tagger_model_uncached",
            localization_status="blocked_ai_tagging_failed",
            media_id=media.id,
        )
    )
    db.commit()
    source_file.unlink()

    ai_calls: list[int] = []

    def fake_ai_tagging(_db_arg, media_id):
        ai_calls.append(media_id)
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0}

    def fake_localization(_db_arg, *, run, media_ids, source_item_ids=None, cancel_check=None):
        for source_item_id in source_item_ids or []:
            item = db.get(DynamicSourceItem, int(source_item_id))
            item.localization_status = "localized"
        return {
            "status": "completed",
            "failed": 0,
            "dynamic_source_items_updated": len(source_item_ids or []),
            "dynamic_source_items_target_status": "localized",
            "tags_requiring_localization_after_runner": 0,
            "llm_called": False,
            "provider_call_count": 0,
        }

    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fake_localization)

    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    assert plan["counts"]["state_counts"]["downstream_followup_planned"] == 1

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        operator_confirmation_statement=plan["integrity"]["operator_confirmation_statement"],
        confirmation_phrase="",
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert ai_calls == [media.id]
    item = db.query(DynamicSourceItem).one()
    assert item.import_status == "imported"
    assert item.ai_tagging_status == "ai_tagged"
    assert item.localization_status == "localized"
    assert result["failed_items"] == 0


def test_manual_sync_execute_prioritizes_followup_before_import_failure_budget(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "clip")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setattr(execute_service, "MANUAL_SYNC_EXECUTE_MAX_ITEM_FAILURES", 20)
    monkeypatch.setattr(execute_service, "MANUAL_SYNC_EXECUTE_FAILURE_RATE_MIN_ITEMS", 1)
    monkeypatch.setattr(execute_service, "MANUAL_SYNC_EXECUTE_MAX_FAILURE_RATE", 0.20)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    import_path = source_root / "00-timeout.png"
    followup_path = source_root / "01-followup.png"
    _write_png(import_path, (1, 2, 3))
    _write_png(followup_path, (4, 5, 6))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    media = Media(
        filename="01-followup.png",
        path="media/original/01-followup.png",
        hash=calculate_file_hash(followup_path),
        file_type=FileTypeEnum.image,
        content_class="anime",
    )
    _write_png(settings.ORIGINAL_DIR / "01-followup.png", (4, 5, 6))
    db.add(media)
    db.flush()
    followup_stat = followup_path.stat()
    followup_item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path=followup_path.name,
        relative_path_hash=planner._hash_text(followup_path.name),
        file_size=followup_stat.st_size,
        mtime_ns=followup_stat.st_mtime_ns,
        content_hash=media.hash,
        source_status="available",
        sync_state="deferred_unprocessed",
        import_status="imported",
        classification_status="classified",
        ai_tagging_status="pending",
        localization_status="waiting_ai_tags",
        deferred_reason="not_processed_budget_stop",
        media_id=media.id,
    )
    db.add(followup_item)
    db.commit()
    followup_path.unlink()

    def fake_execute_hash(path: Path, _timeout_sec: int):
        if Path(path).name == "00-timeout.png":
            return None, "read_timeout"
        return calculate_file_hash(path), None

    ai_tagged: list[int] = []
    localized: list[int] = []

    def fake_ai_tagging(_db_arg, media_id):
        ai_tagged.append(media_id)
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0}

    def fake_localization(db_arg, *, run, media_ids, source_item_ids=None, cancel_check=None):
        assert source_item_ids == [followup_item.id]
        item = db_arg.get(DynamicSourceItem, followup_item.id)
        item.localization_status = "localized"
        localized.extend(media_ids)
        return {
            "status": "completed",
            "failed": 0,
            "dynamic_source_items_updated": len(source_item_ids or []),
            "dynamic_source_items_target_status": "localized",
            "tags_requiring_localization_after_runner": 0,
            "llm_called": False,
            "provider_call_count": 0,
        }

    monkeypatch.setattr(execute_service, "_calculate_manual_plan_file_hash", fake_execute_hash)
    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fake_localization)

    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    assert plan["counts"]["state_counts"]["downstream_followup_planned"] == 1
    assert plan["counts"]["state_counts"]["import_planned"] == 1
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    summary = dict(run.summary_json or {})
    execute_payload = dict(summary["manual_sync_execute"])
    private_items = list(execute_payload["private_plan_items"])
    execute_payload["private_plan_items"] = sorted(
        private_items,
        key=lambda item: 0 if item.get("state") == "import_planned" else 1,
    )
    summary["manual_sync_execute"] = execute_payload
    run.summary_json = summary
    db.commit()

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed_with_failures"
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["operator_status"] == "completed_with_retryable_failures"
    assert execute_summary["outcome_counts"]["downstream_followup_planned"] == 1
    assert execute_summary["outcome_counts"]["read_timeout"] == 1
    assert execute_summary["unprocessed_import_planned_count"] == 0
    assert ai_tagged == [media.id]
    assert localized == [media.id]
    db.refresh(followup_item)
    assert followup_item.import_status == "imported"
    assert followup_item.ai_tagging_status == "ai_tagged"
    assert followup_item.localization_status == "localized"
    run_items = {
        item.source_item_id: item
        for item in db.query(DynamicSyncRunItem).filter(DynamicSyncRunItem.sync_run_id == run.id).all()
    }
    assert run_items[followup_item.id].item_state == "downstream_followup_planned"
    assert run_items[followup_item.id].action == "downstream_followup"


def test_manual_sync_execute_scopes_downstream_followup_to_planned_source_item(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    planned_path = source_root / "followup.png"
    other_path = source_root / "other-copy.png"
    _write_png(planned_path, (30, 40, 50))
    _write_png(other_path, (30, 40, 50))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    media = Media(
        filename="followup.png",
        path="media/original/followup.png",
        hash=calculate_file_hash(planned_path),
        file_type=FileTypeEnum.image,
        content_class="anime",
    )
    _write_png(settings.ORIGINAL_DIR / "followup.png", (30, 40, 50))
    db.add(media)
    db.flush()
    planned_stat = planned_path.stat()
    other_stat = other_path.stat()
    planned_item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path="followup.png",
        relative_path_hash=planner._hash_text("followup.png"),
        file_size=planned_stat.st_size,
        mtime_ns=planned_stat.st_mtime_ns,
        content_hash=media.hash,
        source_status="available",
        sync_state="imported",
        import_status="imported",
        classification_status="classified",
        ai_tagging_status="failed_ai_tagger_model_uncached",
        localization_status="blocked_ai_tagging_failed",
        media_id=media.id,
    )
    other_item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path="other-copy.png",
        relative_path_hash=planner._hash_text("other-copy.png"),
        file_size=other_stat.st_size,
        mtime_ns=other_stat.st_mtime_ns,
        content_hash=media.hash,
        source_status="available",
        sync_state="imported",
        import_status="imported",
        classification_status="classified",
        ai_tagging_status="ai_tagged",
        localization_status="localized",
        media_id=media.id,
    )
    db.add_all([planned_item, other_item])
    db.commit()

    def fake_ai_tagging(_db_arg, media_id):
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0}

    def fake_localization(_db_arg, *, run, media_ids, source_item_ids=None, cancel_check=None):
        assert source_item_ids == [planned_item.id]
        item = db.get(DynamicSourceItem, planned_item.id)
        item.localization_status = "localized"
        return {
            "status": "completed",
            "failed": 0,
            "dynamic_source_items_updated": 1,
            "dynamic_source_items_target_status": "localized",
            "tags_requiring_localization_after_runner": 0,
            "llm_called": False,
            "provider_call_count": 0,
        }

    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fake_localization)

    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    assert plan["counts"]["state_counts"]["downstream_followup_planned"] == 1

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    db.refresh(planned_item)
    db.refresh(other_item)
    assert planned_item.ai_tagging_status == "ai_tagged"
    assert planned_item.localization_status == "localized"
    assert other_item.ai_tagging_status == "ai_tagged"
    assert other_item.localization_status == "localized"
    run_item = db.query(DynamicSyncRunItem).filter(DynamicSyncRunItem.source_item_id == planned_item.id).one()
    assert run_item.current_metadata_json["ai_tagging"]["status"] == "ai_tagged"
    assert result["manual_sync_execute"]["downstream_targets"] == [
        {"media_id": media.id, "source_item_id": planned_item.id}
    ]


def test_manual_sync_execute_downstream_followup_uses_app_media_not_changed_source_file(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "followup.png"
    _write_png(source_file, (30, 40, 50))
    app_file = settings.ORIGINAL_DIR / "app-followup.png"
    _write_png(app_file, (30, 40, 50))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    media_hash = calculate_file_hash(app_file)
    media = Media(
        filename=app_file.name,
        path=f"media/original/{app_file.name}",
        hash=media_hash,
        file_type=FileTypeEnum.image,
        content_class="anime",
    )
    db.add(media)
    db.flush()
    source_stat = source_file.stat()
    source_item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path=source_file.name,
        relative_path_hash=planner._hash_text(source_file.name),
        file_size=source_stat.st_size,
        mtime_ns=source_stat.st_mtime_ns,
        content_hash=media_hash,
        source_status="available",
        sync_state="deferred_unprocessed",
        import_status="imported",
        classification_status="pending",
        ai_tagging_status="pending",
        localization_status="waiting_ai_tags",
        deferred_reason="not_processed_budget_stop",
        media_id=media.id,
    )
    db.add(source_item)
    db.commit()

    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    assert plan["counts"]["state_counts"]["downstream_followup_planned"] == 1

    _write_png(source_file, (99, 88, 77))

    def fail_if_source_metadata_is_read(*_args, **_kwargs):
        raise AssertionError("downstream follow-up must not stat/read the original source file")

    def fail_if_source_hash_is_read(*_args, **_kwargs):
        raise AssertionError("downstream follow-up must not hash the original source file")

    classified: list[int] = []
    ai_tagged: list[int] = []
    localized: list[int] = []

    def fake_classification(db_arg, media_id):
        media_row = db_arg.get(Media, media_id)
        media_row.content_class = "anime"
        classified.append(media_id)
        return {"media_id": media_id, "content_class": "anime", "method": "clip"}

    def fake_ai_tagging(_db_arg, media_id):
        ai_tagged.append(media_id)
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0}

    def fake_localization(db_arg, *, run, media_ids, source_item_ids=None, cancel_check=None):
        for item_id in source_item_ids or []:
            item = db_arg.get(DynamicSourceItem, int(item_id))
            item.localization_status = "localized"
            localized.append(int(item.media_id))
        return {
            "status": "completed",
            "failed": 0,
            "dynamic_source_items_updated": len(source_item_ids or []),
            "dynamic_source_items_target_status": "localized",
            "tags_requiring_localization_after_runner": 0,
            "llm_called": False,
            "provider_call_count": 0,
        }

    monkeypatch.setattr(execute_service, "_metadata_for_path", fail_if_source_metadata_is_read)
    monkeypatch.setattr(execute_service, "_calculate_manual_plan_file_hash", fail_if_source_hash_is_read)
    monkeypatch.setattr(execute_service, "_classify_imported_media", fake_classification)
    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fake_localization)

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert result["manual_sync_execute"]["operator_status"] == "completed"
    assert db.query(Media).count() == 1
    assert classified == [media.id]
    assert ai_tagged == [media.id]
    assert localized == [media.id]
    db.refresh(source_item)
    assert source_item.media_id == media.id
    assert source_item.content_hash == media_hash
    assert source_item.classification_status == "classified"
    assert source_item.ai_tagging_status == "ai_tagged"
    assert source_item.localization_status == "localized"
    run_item = db.query(DynamicSyncRunItem).filter(DynamicSyncRunItem.source_item_id == source_item.id).one()
    assert run_item.item_state == "downstream_followup_planned"
    assert run_item.current_metadata_json["source_file_required"] is False
    assert run_item.current_metadata_json["source_file_validation_skipped"] is True
    assert run_item.current_metadata_json["app_media_authoritative"] is True


def test_manual_sync_execute_production_gate_rejects_unconfigured_localization_provider(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("VIOLET_ENV", "production")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_API_KEY", "")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_MODEL", "")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_BASE_URL", "")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_FALLBACK_API_KEY", "")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_FALLBACK_MODEL", "")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_FALLBACK_BASE_URL", "")
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["production_confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
            production_acceptance_approved=True,
        )

    assert exc.value.code == "manual_sync_localization_llm_provider_unconfigured"


def test_manual_sync_execute_production_gate_rejects_uncached_clip_before_import(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("VIOLET_ENV", "production")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "clip")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setattr(execute_service, "_ensure_clip_model_cache_only", lambda: (False, "classification_model_uncached"))
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["production_confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
            production_acceptance_approved=True,
        )

    assert exc.value.code == "classification_model_uncached"
    assert db.query(DynamicSyncRun).count() == 0
    assert db.query(Media).count() == 0


def test_manual_sync_execute_production_gate_rejects_uncached_wd_tagger_before_import(
    db, tmp_path, monkeypatch
):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("VIOLET_ENV", "production")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "clip")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_API_KEY", "test-key")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_MODEL", "test-model")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(execute_service, "_ensure_clip_model_cache_only", lambda: (True, None))
    monkeypatch.setattr(
        execute_service,
        "_ensure_wd_tagger_model_cache_only",
        lambda: (False, "manual_sync_ai_tagger_model_uncached"),
    )
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )

    with pytest.raises(ManualSyncExecuteError) as exc:
        create_manual_sync_execute_run(
            db,
            root_id=root.id,
            max_files=5,
            hydrated_only=True,
            stable_age_seconds=0,
            expected_plan_hash=plan["integrity"]["plan_hash"],
            confirmation_phrase=plan["integrity"]["production_confirmation_phrase"],
            plan_created_at=plan["job"]["created_at"],
            production_acceptance_approved=True,
        )

    assert exc.value.code == "manual_sync_ai_tagger_model_uncached"
    assert db.query(DynamicSyncRun).count() == 0
    assert db.query(Media).count() == 0
    assert list(settings.ORIGINAL_DIR.iterdir()) == []


def test_s3a_m1_heuristic_classification_defers_when_ai_tagging_failed(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "heuristic")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    _patch_test_storage(monkeypatch, tmp_path)

    monkeypatch.setattr(
        execute_service,
        "_ai_tag_imported_media",
        lambda _db_arg, _media_id: pytest.fail("heuristic manual E2E must not AI tag before classification gate"),
    )

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed_with_followup_required"
    media = db.query(Media).one()
    source_item = db.query(DynamicSourceItem).one()
    run_item = db.query(DynamicSyncRunItem).one()
    assert media.content_class is None
    assert source_item.ai_tagging_status == "blocked_classification_not_completed"
    assert source_item.localization_status == "blocked_classification_not_completed"
    assert source_item.deferred_reason == "classification_not_completed"
    assert source_item.classification_status == "classification_deferred_ai_tags_unavailable"
    assert run_item.current_metadata_json["classification"]["reason"] == "classification_deferred_ai_tags_unavailable"
    assert run_item.current_metadata_json["ai_tagging"]["reason"] == "classification_not_completed"
    assert "private-name.png" not in str(result)


def test_s3a_m1_heuristic_classification_defers_when_ai_tagging_disabled(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "heuristic")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed_with_followup_required"
    media = db.query(Media).one()
    source_item = db.query(DynamicSourceItem).one()
    run_item = db.query(DynamicSyncRunItem).one()
    assert media.content_class is None
    assert source_item.ai_tagging_status == "blocked_classification_not_completed"
    assert source_item.localization_status == "blocked_classification_not_completed"
    assert source_item.deferred_reason == "classification_not_completed"
    assert source_item.classification_status == "classification_deferred_ai_tags_unavailable"
    assert run_item.current_metadata_json["classification"]["reason"] == "classification_deferred_ai_tags_unavailable"
    assert run_item.current_metadata_json["ai_tagging"]["reason"] == "classification_not_completed"


def test_s3a_m1_ai_tagger_model_failure_is_item_level_not_whole_run(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    _patch_test_storage(monkeypatch, tmp_path)

    def fake_ai_tagging(_db_arg, _media_id):
        raise FileNotFoundError("model file not found in local cache")

    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert result["manual_sync_execute"]["outcome_counts"]["ai_tagger_model_uncached"] == 1
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.import_status == "imported"
    assert source_item.ai_tagging_status == "failed_ai_tagger_model_uncached"
    assert source_item.failure_reason == "ai_tagger_model_uncached"
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.current_metadata_json["ai_tagging"]["reason"] == "ai_tagger_model_uncached"
    assert db.get(DynamicSyncRun, run.id).status == "completed"


def test_s3a_m1_ai_tagger_returned_error_is_sanitized_in_public_status(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    _patch_test_storage(monkeypatch, tmp_path)
    secret_name = "secret_filename.png"

    def fake_ai_tagging(_db_arg, media_id):
        return {"media_id": media_id, "error": f"File not found: {secret_name}"}

    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)
    public_status = execute_service.serialize_manual_sync_execute_run(db.get(DynamicSyncRun, run.id))

    assert result["status"] == "completed"
    assert result["manual_sync_execute"]["outcome_counts"]["ai_tagger_file_missing"] == 1
    assert secret_name not in str(result)
    assert secret_name not in str(public_status)
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.ai_tagging_status == "failed_ai_tagger_file_missing"
    assert source_item.failure_reason == "ai_tagger_file_missing"
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.current_metadata_json["ai_tagging"]["reason"] == "ai_tagger_file_missing"


def test_s3a_m1_execute_recovers_stale_active_runs(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    old_started = planner._utcnow() - timedelta(
        seconds=execute_service.MANUAL_SYNC_EXECUTE_ACTIVE_TIMEOUT_SECONDS + 5
    )
    stale_running = DynamicSyncRun(
        run_type="manual_sync_execute",
        mode="dev_test_execute",
        status="running",
        dry_run=False,
        started_at=old_started,
        summary_json={"manual_sync_execute": {"status": "running"}},
    )
    stale_cancelling = DynamicSyncRun(
        run_type="manual_sync_execute",
        mode="dev_test_execute",
        status="cancelling",
        dry_run=False,
        started_at=old_started,
        summary_json={"manual_sync_execute": {"status": "cancelling"}},
    )
    db.add_all([stale_running, stale_cancelling])
    db.commit()

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )

    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    assert run.status == "pending"
    assert db.get(DynamicSyncRun, stale_running.id).status == "failed"
    assert db.get(DynamicSyncRun, stale_cancelling.id).status == "cancelled"


def test_s3a_m1_execute_records_missing_file_and_continues(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    missing_file = source_root / "a-missing.png"
    kept_file = source_root / "b-kept.png"
    _write_png(missing_file, (1, 2, 3))
    _write_png(kept_file, (7, 8, 9))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    missing_file.unlink()

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed_with_failures"
    assert result["manual_sync_execute"]["operator_status"] == "completed_with_retryable_failures"
    assert result["manual_sync_execute"]["outcome_counts"]["source_missing"] == 1
    assert db.query(Media).count() == 1
    assert db.query(DynamicSyncRunItem).count() == 2
    assert {item.failure_reason for item in db.query(DynamicSourceItem).all()} == {"source_missing", None}


def test_s3a_m1_execute_stops_on_failure_budget(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setattr(execute_service, "MANUAL_SYNC_EXECUTE_MAX_ITEM_FAILURES", 1)
    monkeypatch.setattr(execute_service, "MANUAL_SYNC_EXECUTE_FAILURE_RATE_MIN_ITEMS", 100)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    for index in range(3):
        _write_png(source_root / f"missing-{index}.png", (index + 1, index + 2, index + 3))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    for path in source_root.glob("*.png"):
        path.unlink()

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "failed"
    assert result["manual_sync_execute"]["status"] == "stopped_by_failure_budget"
    assert result["manual_sync_execute"]["operator_status"] == "completed_with_retryable_failures_plus_continuation"
    assert result["manual_sync_execute"]["stopped_by"] == "stopped_by_failure_budget"
    assert result["manual_sync_execute"]["unprocessed_count"] == 1
    assert result["manual_sync_execute"]["unprocessed_import_planned_count"] == 1
    assert db.get(DynamicSyncRun, run.id).pending_import_items == 1
    run_items = db.query(DynamicSyncRunItem).all()
    assert len(run_items) == 3
    deferred = [item for item in run_items if item.item_state == "deferred_unprocessed"]
    assert len(deferred) == 1
    assert deferred[0].action == "defer"
    assert deferred[0].reason == "not_processed_budget_stop"
    public_status = execute_service.serialize_manual_sync_execute_run(db.get(DynamicSyncRun, run.id))
    assert "missing-2.png" not in str(public_status)
    assert "content_hash" not in str(deferred[0].current_metadata_json)


def test_s3a_m1_retryable_import_budget_stop_continues_downstream_for_imported_media(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "clip")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setattr(execute_service, "MANUAL_SYNC_EXECUTE_MAX_ITEM_FAILURES", 20)
    monkeypatch.setattr(execute_service, "MANUAL_SYNC_EXECUTE_FAILURE_RATE_MIN_ITEMS", 1)
    monkeypatch.setattr(execute_service, "MANUAL_SYNC_EXECUTE_MAX_FAILURE_RATE", 0.20)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    _write_png(source_root / "00-ok.png", (1, 2, 3))
    _write_png(source_root / "01-ok.png", (4, 5, 6))
    _write_png(source_root / "02-timeout.png", (7, 8, 9))
    _write_png(source_root / "03-deferred.png", (10, 11, 12))
    base_mtime_ns = 1_700_000_000_000_000_000
    for index, name in enumerate(("00-ok.png", "01-ok.png", "02-timeout.png", "03-deferred.png")):
        ordered_mtime_ns = base_mtime_ns + (10 - index) * 1_000_000
        os.utime(source_root / name, ns=(ordered_mtime_ns, ordered_mtime_ns))
    root = planner.register_source_root(db, path=source_root, label="fixture")

    def fake_execute_hash(path: Path, _timeout_sec: int):
        if Path(path).name == "02-timeout.png":
            return None, "read_timeout"
        return calculate_file_hash(path), None

    classified: list[int] = []
    ai_tagged: list[int] = []
    localized: list[int] = []

    def fake_classification(db_arg, media_id):
        media = db_arg.get(Media, media_id)
        media.content_class = "anime"
        classified.append(media_id)
        return {"media_id": media_id, "content_class": "anime", "method": "clip"}

    def fake_ai_tagging(_db_arg, media_id):
        ai_tagged.append(media_id)
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0}

    def fake_localization(db_arg, *, run, media_ids, source_item_ids=None, cancel_check=None):
        assert cancel_check is not None
        for source_item_id in source_item_ids or []:
            item = db_arg.get(DynamicSourceItem, int(source_item_id))
            item.localization_status = "localized"
            localized.append(int(item.media_id))
        return {
            "status": "completed",
            "failed": 0,
            "dynamic_source_items_updated": len(source_item_ids or []),
            "dynamic_source_items_target_status": "localized",
            "tags_requiring_localization_after_runner": 0,
            "llm_called": False,
            "provider_call_count": 0,
        }

    monkeypatch.setattr(execute_service, "_calculate_manual_plan_file_hash", fake_execute_hash)
    monkeypatch.setattr(execute_service, "_classify_imported_media", fake_classification)
    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fake_localization)

    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed_with_failures"
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["operator_status"] == "completed_with_retryable_failures_plus_continuation"
    assert execute_summary["import_stopped_by"] == "stopped_by_failure_budget"
    assert execute_summary["downstream_continued_after_import_stop"] is True
    assert execute_summary["retryable_source_failure_count"] == 1
    assert execute_summary["stopped_by"] is None
    assert execute_summary["outcome_counts"]["imported"] == 2
    assert execute_summary["outcome_counts"]["read_timeout"] == 1
    assert execute_summary["unprocessed_import_planned_count"] == 1
    assert result["failed_items"] == 1
    assert len(classified) == 2
    assert ai_tagged == classified
    assert sorted(localized) == sorted(classified)
    stage_rows = {row["name"]: row for row in execute_summary["stage_rows"]}
    assert stage_rows["import"]["status"] == "stopped_by_failure_budget"
    assert stage_rows["classification"]["status"] == "completed"
    assert stage_rows["ai_tagging"]["status"] == "completed"
    assert stage_rows["localization"]["status"] == "completed"

    source_items = {item.relative_path: item for item in db.query(DynamicSourceItem).all()}
    assert source_items["00-ok.png"].classification_status == "classified"
    assert source_items["00-ok.png"].ai_tagging_status == "ai_tagged"
    assert source_items["00-ok.png"].localization_status == "localized"
    assert source_items["01-ok.png"].classification_status == "classified"
    assert source_items["01-ok.png"].ai_tagging_status == "ai_tagged"
    assert source_items["01-ok.png"].localization_status == "localized"
    assert source_items["02-timeout.png"].failure_reason == "read_timeout"
    assert source_items["02-timeout.png"].import_status == "failed"
    retry_metadata = (source_items["02-timeout.png"].metadata_json or {}).get("manual_sync_retry") or {}
    assert retry_metadata["attempt_count"] == 1
    assert retry_metadata["last_failure_reason"] == "read_timeout"
    assert retry_metadata["retryable"] is True
    assert retry_metadata["long_term_state"] == "retryable"
    assert source_items["03-deferred.png"].deferred_reason == "not_processed_budget_stop"
    assert source_items["03-deferred.png"].import_status == "deferred"


def test_manual_sync_execute_retry_source_read_timeout_does_not_import_or_copy(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "retry-timeout.png"
    _write_png(source_file, (1, 2, 3))
    stat = source_file.stat()
    root = planner.register_source_root(db, path=source_root, label="fixture")
    db.add(
        DynamicSourceItem(
            source_root_id=root.id,
            relative_path=source_file.name,
            relative_path_hash=planner._hash_text(source_file.name),
            file_size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            source_status="failed",
            sync_state="failed",
            import_status="failed",
            failure_reason="read_timeout",
        )
    )
    db.commit()

    monkeypatch.setattr(execute_service, "_calculate_manual_plan_file_hash", lambda _path, _timeout_sec: (None, "read_timeout"))

    def fail_copy(_db_arg, _source_file):
        raise AssertionError("RETRY_SOURCE must not call import/copy")

    monkeypatch.setattr(execute_service, "_copy_and_import_media", fail_copy)
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        include_private_details=True,
    )
    assert plan["private_details"]["items"][0]["work_item_kind"] == "RETRY_SOURCE"
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    pending_payload = execute_service.serialize_manual_sync_execute_run(run)
    pending_execute = pending_payload["manual_sync_execute"]
    assert pending_execute["run_created_at"]
    assert pending_execute["last_heartbeat_at"] is None
    assert pending_execute["current_stage"] == "queued"
    assert pending_execute["current_stage_status"] == "pending"
    pending_stage_rows = {row["name"]: row for row in pending_execute["stage_rows"]}
    for stage in ("candidate_discovery", "import", "classification", "ai_tagging", "localization", "summary"):
        assert pending_stage_rows[stage]["status"] == "pending"
        assert pending_stage_rows[stage].get("updated_at") is None

    result = execute_manual_sync_run(db, run_id=run.id)

    assert db.query(Media).count() == 0
    assert list(settings.ORIGINAL_DIR.iterdir()) == []
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["outcome_counts"]["failed"] == 1
    assert execute_summary["outcome_counts"]["read_timeout"] == 1
    item = db.query(DynamicSourceItem).one()
    assert item.import_status == "failed"
    assert item.failure_reason == "read_timeout"
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.action == "retry_source"


def test_manual_sync_execute_retry_source_cloud_hydration_success_does_not_import(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "hydrated-now.png"
    _write_png(source_file, (4, 5, 6))
    stat = source_file.stat()
    root = planner.register_source_root(db, path=source_root, label="fixture")
    item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path=source_file.name,
        relative_path_hash=planner._hash_text(source_file.name),
        file_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        source_status="failed",
        sync_state="failed",
        import_status="failed",
        failure_reason="cloud_hydration_failed",
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        execute_service,
        "_calculate_manual_plan_file_hash",
        lambda path, _timeout_sec: (calculate_file_hash(path), None),
    )

    def fail_copy(_db_arg, _source_file):
        raise AssertionError("successful RETRY_SOURCE must re-plan as IMPORT, not import immediately")

    monkeypatch.setattr(execute_service, "_copy_and_import_media", fail_copy)
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        include_private_details=True,
    )
    assert plan["private_details"]["items"][0]["work_item_kind"] == "RETRY_SOURCE"
    assert plan["private_details"]["items"][0]["reason"] == "cloud_hydration_failed"
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert db.query(Media).count() == 0
    assert list(settings.ORIGINAL_DIR.iterdir()) == []
    execute_summary = result["manual_sync_execute"]
    assert result["status"] == "completed_with_followup_required"
    assert execute_summary["status"] == "completed_with_continuation"
    assert execute_summary["operator_status"] == "completed_with_continuation"
    assert "重试恢复后的导入需要继续计划" in execute_summary["operator_status_label_zh"]
    assert execute_summary["outcome_counts"]["retry_source_ready_for_import"] == 1
    db.refresh(item)
    assert item.sync_state == "new"
    assert item.import_status == "pending"
    assert item.failure_reason is None
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.item_state == "retry_source_ready_for_import"
    assert run_item.action == "retry_source"


def test_manual_sync_execute_successful_retry_replans_as_explicit_import(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "retry-ready.png"
    _write_png(source_file, (7, 8, 9))
    stat = source_file.stat()
    root = planner.register_source_root(db, path=source_root, label="fixture")
    item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path=source_file.name,
        relative_path_hash=planner._hash_text(source_file.name),
        file_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        source_status="failed",
        sync_state="failed",
        import_status="failed",
        failure_reason="read_timeout",
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        execute_service,
        "_calculate_manual_plan_file_hash",
        lambda path, _timeout_sec: (calculate_file_hash(path), None),
    )
    monkeypatch.setattr(
        execute_service,
        "_copy_and_import_media",
        lambda _db_arg, _source_file: (_ for _ in ()).throw(AssertionError("RETRY_SOURCE imported unexpectedly")),
    )
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        include_private_details=True,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    execute_manual_sync_run(db, run_id=run.id)
    next_plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        include_private_details=True,
    )

    assert db.query(Media).count() == 0
    next_item = next_plan["private_details"]["items"][0]
    assert next_item["lifecycle_kind"] == "IMPORT_CANDIDATE"
    assert next_item["work_item_kind"] == "IMPORT"
    assert next_item["can_execute"] is True


def test_manual_sync_execute_successful_retry_existing_media_replans_as_followup(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    app_storage = _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "retry-existing-media.png"
    _write_png(source_file, (17, 18, 19))
    stat = source_file.stat()
    root = planner.register_source_root(db, path=source_root, label="fixture")
    media = Media(
        filename="retry-existing-media.png",
        path="media/original/retry-existing-media.png",
        hash=calculate_file_hash(source_file),
        file_type=FileTypeEnum.image,
    )
    _write_app_media(app_storage, media.path, (17, 18, 19))
    db.add(media)
    db.flush()
    item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path=source_file.name,
        relative_path_hash=planner._hash_text(source_file.name),
        file_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        content_hash=media.hash,
        source_status="failed",
        sync_state="failed",
        import_status="failed",
        classification_status="classified",
        ai_tagging_status="failed_ai_tagger_model_uncached",
        localization_status="blocked_ai_tagging_failed",
        failure_reason="read_timeout",
        media_id=media.id,
    )
    db.add(item)
    db.commit()

    monkeypatch.setattr(
        execute_service,
        "_calculate_manual_plan_file_hash",
        lambda path, _timeout_sec: (calculate_file_hash(path), None),
    )
    monkeypatch.setattr(
        execute_service,
        "_copy_and_import_media",
        lambda _db_arg, _source_file: (_ for _ in ()).throw(AssertionError("RETRY_SOURCE imported unexpectedly")),
    )
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        include_private_details=True,
    )
    assert plan["private_details"]["items"][0]["work_item_kind"] == "RETRY_SOURCE"
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert db.query(Media).count() == 1
    assert result["manual_sync_execute"]["outcome_counts"]["retry_source_ready_for_import"] == 1
    db.refresh(item)
    assert item.media_id == media.id
    assert item.sync_state == "imported"
    assert item.import_status == "imported"
    assert item.failure_reason is None
    assert item.classification_status == "classified"
    assert item.ai_tagging_status == "failed_ai_tagger_model_uncached"

    next_plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        include_private_details=True,
    )
    next_item = next_plan["private_details"]["items"][0]
    assert next_item["lifecycle_kind"] == "APP_MEDIA_FOLLOWUP"
    assert next_item["work_item_kind"] == "FOLLOWUP"
    assert next_item["can_execute"] is True
    assert next_item["allowed_source_reads"] is False


def test_manual_sync_execute_broken_state_does_not_mutate_source_item(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "broken.png"
    _write_png(source_file, (10, 20, 30))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path=source_file.name,
        relative_path_hash=planner._hash_text(source_file.name),
        source_status="available",
        sync_state="imported",
        import_status="imported",
        classification_status="pending",
        ai_tagging_status="pending",
        localization_status="waiting_ai_tags",
        failure_reason=None,
        deferred_reason=None,
    )
    db.add(item)
    db.commit()
    before = _source_item_core_snapshot(item)
    plan = planner.plan_manual_sync_dry_run(db, source_path=source_root, source_record_id=root.id, max_files=5, stable_age_seconds=0)
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    _replace_execute_private_plan_items(
        db,
        run,
        [
            {
                "safe_label": "diagnostic-00001",
                "relative_path": source_file.name,
                "source_item_id": item.id,
                "state": "broken_state",
                "reason": "app_media_missing",
                "lifecycle_reason_code": "app_media_missing",
                "lifecycle_kind": "BROKEN_STATE",
                "work_item_kind": "BROKEN_STATE",
                "can_execute": False,
                "allowed_source_reads": False,
            }
        ],
    )

    execute_manual_sync_run(db, run_id=run.id)

    db.refresh(item)
    assert _source_item_core_snapshot(item) == before
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.action == "diagnostic"
    assert run_item.reason == "app_media_missing"


def test_manual_sync_execute_noop_diagnostic_does_not_mutate_source_item(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "noop.png"
    _write_png(source_file, (11, 21, 31))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path=source_file.name,
        relative_path_hash=planner._hash_text(source_file.name),
        source_status="available",
        sync_state="imported",
        import_status="imported",
        classification_status="classified",
        ai_tagging_status="ai_tagged",
        localization_status="localized",
        failure_reason=None,
        deferred_reason=None,
    )
    db.add(item)
    db.commit()
    before = _source_item_core_snapshot(item)
    plan = planner.plan_manual_sync_dry_run(db, source_path=source_root, source_record_id=root.id, max_files=5, stable_age_seconds=0)
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    _replace_execute_private_plan_items(
        db,
        run,
        [
            {
                "safe_label": "diagnostic-00001",
                "relative_path": source_file.name,
                "source_item_id": item.id,
                "state": "unchanged",
                "reason": "downstream_complete",
                "lifecycle_reason_code": "downstream_complete",
                "lifecycle_kind": "STABLE_NOOP",
                "work_item_kind": "NOOP_DIAGNOSTIC",
                "can_execute": False,
                "allowed_source_reads": False,
            }
        ],
    )

    execute_manual_sync_run(db, run_id=run.id)

    db.refresh(item)
    assert _source_item_core_snapshot(item) == before
    assert db.query(DynamicSyncRunItem).one().action == "diagnostic"


def test_manual_sync_execute_placeholder_does_not_mutate_imported_media_backed_state(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_file = source_root / "placeholder.png"
    _write_png(source_file, (12, 22, 32))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    media = Media(
        filename="placeholder.png",
        path="media/original/placeholder.png",
        hash=calculate_file_hash(source_file),
        file_type=FileTypeEnum.image,
    )
    db.add(media)
    db.flush()
    item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path=source_file.name,
        relative_path_hash=planner._hash_text(source_file.name),
        source_status="available",
        sync_state="imported",
        import_status="imported",
        classification_status="classified",
        ai_tagging_status="ai_tagged",
        localization_status="localized",
        media_id=media.id,
        content_hash=media.hash,
        failure_reason=None,
        deferred_reason=None,
    )
    db.add(item)
    db.commit()
    before = _source_item_core_snapshot(item)
    plan = planner.plan_manual_sync_dry_run(db, source_path=source_root, source_record_id=root.id, max_files=5, stable_age_seconds=0)
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    _replace_execute_private_plan_items(
        db,
        run,
        [
            {
                "safe_label": "diagnostic-00001",
                "relative_path": source_file.name,
                "source_item_id": item.id,
                "state": "skipped_placeholder",
                "reason": "cloud_placeholder",
                "lifecycle_reason_code": "cloud_placeholder",
                "lifecycle_kind": "PLACEHOLDER_DEFERRED",
                "work_item_kind": "PLACEHOLDER",
                "can_execute": False,
                "allowed_source_reads": False,
                "media_id": media.id,
            }
        ],
    )

    execute_manual_sync_run(db, run_id=run.id)

    db.refresh(item)
    assert _source_item_core_snapshot(item) == before
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.action == "diagnostic"
    assert run_item.reason == "cloud_placeholder"


def test_s3a_m1_execute_stops_on_duration_budget(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setattr(execute_service, "manual_sync_execute_max_duration_seconds", lambda: -1)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    _write_png(source_root / "new.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "failed"
    assert result["manual_sync_execute"]["status"] == "stopped_by_duration_budget"
    assert result["manual_sync_execute"]["stopped_by"] == "stopped_by_duration_budget"
    assert result["manual_sync_execute"]["unprocessed_count"] == 1
    assert result["manual_sync_execute"]["unprocessed_import_planned_count"] == 1
    assert db.get(DynamicSyncRun, run.id).pending_import_items == 1
    run_items = db.query(DynamicSyncRunItem).all()
    assert len(run_items) == 1
    assert run_items[0].item_state == "deferred_unprocessed"
    assert run_items[0].action == "defer"
    assert run_items[0].reason == "not_processed_budget_stop"


def test_s3a_m1_execute_does_not_hash_non_import_skip_items(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    source_root.mkdir(parents=True)
    unsupported = source_root / "notes.txt"
    unsupported.write_text("not media", encoding="utf-8")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    assert plan["counts"]["state_counts"]["skipped_unsupported"] == 1
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    def fail_if_hashing_skip_item(path, _timeout):
        raise AssertionError(f"execute must not hash non-import skip item: {path.name}")

    monkeypatch.setattr(execute_service, "_calculate_manual_plan_file_hash", fail_if_hashing_skip_item)

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert result["manual_sync_execute"]["outcome_counts"]["skipped_unsupported"] == 1
    assert db.query(Media).count() == 0
    assert db.query(DynamicSyncRunItem).count() == 0
    assert db.query(DynamicSourceItem).count() == 0


def test_s3a_m1_execute_does_not_rehash_existing_media_noop_under_workitem_boundary(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    planned_existing = source_root / "existing.png"
    _write_png(planned_existing, (10, 20, 30))
    existing_hash = calculate_file_hash(planned_existing)
    db.add(
        Media(
            filename="existing-redacted.png",
            path="media/original/existing-redacted.png",
            hash=existing_hash,
            file_type=FileTypeEnum.image,
        )
    )
    db.commit()
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
    )
    assert plan["counts"]["state_counts"]["skipped_existing_media"] == 1
    assert plan["ledger"]["per_file_public_records"][0]["work_item_kind"] == "NOOP_DIAGNOSTIC"
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        plan_mode="advanced_full_rescan",
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    def fail_if_hashing_noop_item(path, _timeout):
        raise AssertionError(f"execute must not hash existing-media NOOP item: {path.name}")

    monkeypatch.setattr(execute_service, "_calculate_manual_plan_file_hash", fail_if_hashing_noop_item)
    _write_png(planned_existing, (90, 80, 70))
    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert result["failed_items"] == 0
    assert result["manual_sync_execute"]["outcome_counts"]["skipped_existing_media"] == 1
    assert result["manual_sync_execute"]["outcome_counts"].get("failed", 0) == 0
    assert db.query(DynamicSyncRunItem).count() == 0
    assert db.query(Media).count() == 1


def test_s3a_m1_execute_materializes_unprocessed_items_on_cancel(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    monkeypatch.setattr(execute_service, "_is_cancel_requested", lambda _run_id: True)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    for index in range(2):
        _write_png(source_root / f"cancel-{index}.png", (index + 1, index + 2, index + 3))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "cancelled"
    assert result["manual_sync_execute"]["unprocessed_count"] == 2
    assert result["manual_sync_execute"]["unprocessed_import_planned_count"] == 2
    assert db.get(DynamicSyncRun, run.id).pending_import_items == 2
    run_items = db.query(DynamicSyncRunItem).all()
    assert len(run_items) == 2
    assert {item.item_state for item in run_items} == {"deferred_unprocessed"}
    assert {item.reason for item in run_items} == {"not_processed_cancelled"}
    public_status = execute_service.serialize_manual_sync_execute_run(db.get(DynamicSyncRun, run.id))
    assert "cancel-0.png" not in str(public_status)
    assert "cancel-1.png" not in str(public_status)


def test_manual_sync_execute_does_not_materialize_tail_diagnostics_as_deferred(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    monkeypatch.setattr(execute_service, "_is_cancel_requested", lambda _run_id: True)
    _patch_test_storage(monkeypatch, tmp_path)

    source_root = tmp_path / "source"
    import_file = source_root / "cancel-actionable.png"
    _write_png(import_file, (21, 22, 23))
    root = planner.register_source_root(db, path=source_root, label="fixture")
    retry_file = source_root / "retry-tail.png"
    _write_png(retry_file, (31, 32, 33))
    retry_stat = retry_file.stat()
    retry = DynamicSourceItem(
        source_root_id=root.id,
        relative_path=retry_file.name,
        relative_path_hash=planner._hash_text(retry_file.name),
        file_size=retry_stat.st_size,
        mtime_ns=retry_stat.st_mtime_ns,
        source_status="failed",
        sync_state="failed",
        import_status="failed",
        classification_status="deferred",
        ai_tagging_status="deferred",
        localization_status="blocked_import_failed",
        failure_reason="read_timeout",
        deferred_reason=None,
    )
    broken = DynamicSourceItem(
        source_root_id=root.id,
        relative_path="broken-diagnostic.png",
        relative_path_hash=planner._hash_text("broken-diagnostic.png"),
        source_status="available",
        sync_state="imported",
        import_status="imported",
        classification_status="pending",
        ai_tagging_status="pending",
        localization_status="waiting_ai_tags",
        failure_reason=None,
        deferred_reason=None,
    )
    db.add_all([retry, broken])
    db.commit()
    before_retry = _source_item_core_snapshot(retry)
    before = _source_item_core_snapshot(broken)
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=1,
        stable_age_seconds=0,
        include_private_details=True,
    )
    actionable_item = dict(plan["private_details"]["items"][0])
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=1,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )
    _replace_execute_private_plan_items(
        db,
        run,
        [
            actionable_item,
            {
                "safe_label": "retry-00001",
                "relative_path": retry.relative_path,
                "source_item_id": retry.id,
                "state": "retry_source_planned",
                "reason": "read_timeout",
                "lifecycle_reason_code": "read_timeout",
                "lifecycle_kind": "RETRYABLE_SOURCE_FAILURE",
                "work_item_kind": "RETRY_SOURCE",
                "can_execute": True,
                "is_actionable": True,
                "consumes_actionable_cap": True,
                "allowed_source_reads": True,
            },
            {
                "safe_label": "diagnostic-00001",
                "relative_path": broken.relative_path,
                "source_item_id": broken.id,
                "state": "broken_state",
                "reason": "app_media_missing",
                "lifecycle_reason_code": "app_media_missing",
                "lifecycle_kind": "BROKEN_STATE",
                "work_item_kind": "BROKEN_STATE",
                "can_execute": False,
                "is_actionable": False,
                "consumes_actionable_cap": False,
                "allowed_source_reads": False,
            },
        ],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    execute_summary = result["manual_sync_execute"]
    assert execute_summary["unprocessed_count"] == 1
    assert execute_summary["unprocessed_actionable_count"] == 2
    assert execute_summary["unprocessed_retry_source_count"] == 1
    assert execute_summary["skipped_or_recorded_diagnostic_count"] == 1
    assert execute_summary["outcome_counts"]["deferred_unprocessed"] == 1
    assert execute_summary["outcome_counts"]["retry_source_not_deferred"] == 1
    assert execute_summary["outcome_counts"]["diagnostic_not_deferred"] == 1
    db.refresh(retry)
    assert _source_item_core_snapshot(retry) == before_retry
    db.refresh(broken)
    assert _source_item_core_snapshot(broken) == before
    run_items = db.query(DynamicSyncRunItem).all()
    assert len(run_items) == 1
    assert run_items[0].item_state == "deferred_unprocessed"
    assert run_items[0].source_item_id != broken.id


def test_s3a_m1_execute_cancel_after_ai_tagging_skips_localization_finalizer(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "heuristic")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    _patch_test_storage(monkeypatch, tmp_path)
    finalizer_called = False

    def fake_ai_tagging(_db_arg, media_id):
        execute_service.request_manual_sync_execute_cancel(run.id)
        return {"media_id": media_id, "tags_added": 1, "suggestions_added": 0}

    def fail_if_finalizer_runs(*_args, **_kwargs):
        nonlocal finalizer_called
        finalizer_called = True
        raise AssertionError("localization finalizer must not run after cancellation")

    monkeypatch.setattr(execute_service, "_ai_tag_imported_media", fake_ai_tagging)
    monkeypatch.setattr(execute_service, "_manual_sync_finalize_localization", fail_if_finalizer_runs)

    source_root = tmp_path / "source"
    _write_png(source_root / "cancel-after-ai.png")
    root = planner.register_source_root(db, path=source_root, label="fixture")
    plan = planner.plan_manual_sync_dry_run(
        db,
        source_path=source_root,
        source_record_id=root.id,
        max_files=5,
        stable_age_seconds=0,
    )
    run = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    result = execute_manual_sync_run(db, run_id=run.id)

    assert finalizer_called is False
    assert result["status"] == "cancelled"
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["status"] == "cancelled"
    assert execute_summary["llm_calls_performed"] is False
    assert execute_summary["localization"]["status"] == "skipped_cancelled_run"
    assert execute_summary["localization"]["blocked_reason"] == "manual_sync_cancelled_before_localization"
    assert execute_summary["localization"]["llm_called"] is False
    assert execute_summary["localization"]["provider_call_count"] == 0
    assert execute_summary["localization"]["dynamic_source_items_updated"] == 0
    assert execute_summary["localization"]["dynamic_source_items_target_status"] == "unchanged"
    assert execute_summary["localization"]["localization_finalizer_called"] is False
    assert execute_summary["localization"]["localization_db_writes_performed"] is False
    stage_rows = {row["name"]: row for row in execute_summary["stage_rows"]}
    assert stage_rows["localization"]["status"] == "skipped_cancelled_run"
    assert stage_rows["summary"]["status"] == "cancelled"
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.import_status == "imported"
    assert source_item.ai_tagging_status == "ai_tagged"
    assert source_item.localization_status == "waiting_localization"
    assert source_item.deferred_reason is None


def test_manual_sync_localization_finalizer_honors_late_cancel_before_provider_call(db, monkeypatch):
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    root = DynamicSourceRoot(
        label="fixture",
        root_path="/redacted/source",
        root_path_hash="root-hash",
        is_active=True,
    )
    media = Media(
        filename="localized.png",
        path="media/original/localized.png",
        hash="localized-hash",
        file_type=FileTypeEnum.image,
    )
    tag = Tag(name="new_general_tag_for_cancel", category=TagCategoryEnum.general)
    run = DynamicSyncRun(run_type="manual_sync_execute", mode="dev_test_execute", status="running", dry_run=False)
    db.add_all([root, media, tag, run])
    db.flush()
    source_item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path="localized.png",
        relative_path_hash="localized-relhash",
        media_id=media.id,
        source_status="available",
        sync_state="imported",
        import_status="imported",
        classification_status="classified",
        ai_tagging_status="ai_tagged",
        localization_status="waiting_localization",
    )
    db.add(source_item)
    db.flush()
    db.execute(
        blombooru_media_tags.insert().values(
            media_id=media.id,
            tag_id=tag.id,
            source="ai_wd",
            confidence=0.9,
            is_suggestion=False,
        )
    )
    db.commit()

    cancel_checks = {"count": 0}

    def late_cancel() -> bool:
        cancel_checks["count"] += 1
        return cancel_checks["count"] >= 2

    def fail_provider_call():
        raise AssertionError("LLM provider must not be initialized after late cancel")

    monkeypatch.setattr(execute_service, "_covered_translation_names", lambda _db, *, lang: set())
    monkeypatch.setattr("app.services.llm_translation_provider.get_llm_provider", fail_provider_call)

    result = execute_service._manual_sync_finalize_localization(
        db,
        run=run,
        media_ids=[media.id],
        source_item_ids=[source_item.id],
        cancel_check=late_cancel,
    )

    assert result["status"] == "skipped_cancelled_run"
    assert result["llm_called"] is False
    assert result["provider_call_count"] == 0
    assert result["localization_db_writes_performed"] is False
    assert db.get(DynamicSourceItem, source_item.id).localization_status == "waiting_localization"


def test_manual_sync_localization_finalizer_preserves_side_effect_counts_on_late_cancel(db, monkeypatch):
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BATCH_MAX_ITEMS", "10")
    root = DynamicSourceRoot(
        label="fixture",
        root_path="/redacted/source",
        root_path_hash="root-hash",
        is_active=True,
    )
    media = Media(
        filename="localized-late-cancel.png",
        path="media/original/localized-late-cancel.png",
        hash="late-cancel-hash",
        file_type=FileTypeEnum.image,
    )
    tag_one = Tag(name="unit_cancel_localization_one", category=TagCategoryEnum.general)
    tag_two = Tag(name="unit_cancel_localization_two", category=TagCategoryEnum.general)
    run = DynamicSyncRun(run_type="manual_sync_execute", mode="dev_test_execute", status="running", dry_run=False)
    db.add_all([root, media, tag_one, tag_two, run])
    db.flush()
    source_item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path="localized-late-cancel.png",
        relative_path_hash="localized-late-cancel-relhash",
        media_id=media.id,
        source_status="available",
        sync_state="imported",
        import_status="imported",
        classification_status="classified",
        ai_tagging_status="ai_tagged",
        localization_status="waiting_localization",
    )
    db.add(source_item)
    db.flush()
    for tag in (tag_one, tag_two):
        db.execute(
            blombooru_media_tags.insert().values(
                media_id=media.id,
                tag_id=tag.id,
                source="ai_wd",
                confidence=0.9,
                is_suggestion=False,
            )
        )
    db.commit()

    class FakeProvider:
        def is_available(self):
            return True

        def get_provider_name(self):
            return "unit"

        async def translate_tags(self, inputs):
            return [
                SimpleNamespace(
                    canonical_name=item["name"],
                    display_name_zh=f"zh-{index}",
                    aliases_zh=[],
                    confidence=0.9,
                    needs_review=False,
                )
                for index, item in enumerate(inputs)
            ]

    cancel_checks = {"count": 0}

    def late_cancel() -> bool:
        cancel_checks["count"] += 1
        return cancel_checks["count"] >= 5

    monkeypatch.setattr(execute_service, "_covered_translation_names", lambda _db, *, lang: set())
    monkeypatch.setattr("app.services.llm_translation_provider.get_llm_provider", lambda: FakeProvider())

    result = execute_service._manual_sync_finalize_localization(
        db,
        run=run,
        media_ids=[media.id],
        source_item_ids=[source_item.id],
        cancel_check=late_cancel,
    )

    assert result["status"] == "skipped_cancelled_run"
    assert result["llm_called"] is True
    assert result["provider_call_count"] == 1
    assert result["translated"] == 1
    assert result["localization_db_writes_performed"] is True
    assert db.query(TagTranslation).count() == 1
    assert db.get(DynamicSourceItem, source_item.id).localization_status == "waiting_localization"


def test_manual_sync_localization_recomputes_missing_tags_after_successful_save(db, monkeypatch):
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BATCH_MAX_ITEMS", "10")
    root = DynamicSourceRoot(
        label="fixture",
        root_path="/redacted/source",
        root_path_hash="root-hash",
        is_active=True,
    )
    media = Media(
        filename="localized-success.png",
        path="media/original/localized-success.png",
        hash="localized-success-hash",
        file_type=FileTypeEnum.image,
    )
    tag_one = Tag(name="unit_success_localization_one", category=TagCategoryEnum.general)
    tag_two = Tag(name="unit_success_localization_two", category=TagCategoryEnum.meta)
    run = DynamicSyncRun(run_type="manual_sync_execute", mode="dev_test_execute", status="running", dry_run=False)
    db.add_all([root, media, tag_one, tag_two, run])
    db.flush()
    source_item = DynamicSourceItem(
        source_root_id=root.id,
        relative_path="localized-success.png",
        relative_path_hash="localized-success-relhash",
        media_id=media.id,
        source_status="available",
        sync_state="imported",
        import_status="imported",
        classification_status="classified",
        ai_tagging_status="ai_tagged",
        localization_status="waiting_localization",
    )
    db.add(source_item)
    db.flush()
    for tag in (tag_one, tag_two):
        db.execute(
            blombooru_media_tags.insert().values(
                media_id=media.id,
                tag_id=tag.id,
                source="ai_wd",
                confidence=0.9,
                is_suggestion=False,
            )
        )
    db.commit()

    class FakeProvider:
        def is_available(self):
            return True

        def get_provider_name(self):
            return "unit"

        async def translate_tags(self, inputs):
            return [
                SimpleNamespace(
                    canonical_name=item["name"],
                    display_name_zh=f"zh-{index}",
                    aliases_zh=[],
                    confidence=0.9,
                    needs_review=False,
                )
                for index, item in enumerate(inputs)
            ]

    monkeypatch.setattr("app.services.llm_translation_provider.get_llm_provider", lambda: FakeProvider())

    result = execute_service._manual_sync_finalize_localization(
        db,
        run=run,
        media_ids=[media.id],
        source_item_ids=[source_item.id],
        cancel_check=lambda: False,
    )

    assert result["status"] == "completed"
    assert result["translated"] == 2
    assert result["tags_requiring_localization_after_runner"] == 0
    assert result["dynamic_source_items_target_status"] == "localized"
    assert db.query(TagTranslation).count() == 2
    assert db.get(DynamicSourceItem, source_item.id).localization_status == "localized"
