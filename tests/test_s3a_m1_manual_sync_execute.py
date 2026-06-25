import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
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
from app.models import (  # noqa: E402
    AITagJob,
    ClassificationJob,
    ContentClassEnum,
    DynamicSourceItem,
    DynamicSyncRun,
    DynamicSyncRunItem,
    Media,
    Tag,
    TagCategoryEnum,
    blombooru_media_tags,
)
from app.routes.admin import dynamic_library_sync as dynamic_routes  # noqa: E402
from app.services import dynamic_library_sync_service as planner  # noqa: E402
from app.services import manual_sync_execute_service as execute_service  # noqa: E402
from app.services.dynamic_library_sync_service import S3A_M1_MANUAL_EXECUTE_CONFIRMATION_PREFIX  # noqa: E402
from app.services.manual_sync_execute_service import (  # noqa: E402
    ManualSyncExecuteError,
    create_manual_sync_execute_run,
    execute_manual_sync_run,
)
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


def _enable_manual_execute(monkeypatch) -> None:
    monkeypatch.setenv("DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED", "true")
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "false")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "false")


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
        hydrated_only=True,
        stable_age_seconds=0,
        expected_plan_hash=plan["integrity"]["plan_hash"],
        confirmation_phrase=plan["integrity"]["confirmation_phrase"],
        plan_created_at=plan["job"]["created_at"],
    )

    execute_payload = run.summary_json["manual_sync_execute"]
    assert plan["limits"]["max_files"] == execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES
    assert execute_payload["request"]["effective_max_files"] == execute_service.MANUAL_SYNC_EXECUTE_MAX_FILES
    assert execute_payload["plan"]["integrity"]["plan_hash"] == plan["integrity"]["plan_hash"]


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
    assert first["integrity"]["confirmation_phrase"].startswith(S3A_M1_MANUAL_EXECUTE_CONFIRMATION_PREFIX)
    assert first["integrity"]["hash_excludes_paths"] is True
    assert first["integrity"]["hash_includes_private_content_fingerprint"] is True
    assert "private-name.png" not in str(first)
    assert str(source_root) not in str(first)


def test_s3a_m1_execute_is_disabled_by_default(db, tmp_path):
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
    )

    first = create_manual_sync_execute_run(
        db,
        root_id=root.id,
        max_files=5,
        hydrated_only=True,
        stable_age_seconds=0,
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
    _write_png(source_file, (90, 80, 70))

    result = execute_manual_sync_run(db, run_id=run.id)

    assert result["status"] == "completed"
    assert result["manual_sync_execute"]["outcome_counts"]["content_changed_after_plan"] == 1
    assert db.get(DynamicSyncRun, run.id).status == "completed"
    assert db.query(Media).count() == 0
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.failure_reason == "content_changed_after_plan"


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
    assert db.query(Media).count() == 1
    assert source_file.read_bytes() == before
    assert db.query(DynamicSyncRunItem).count() == 1
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.import_status == "imported"
    assert source_item.classification_status == "skipped_classification_disabled"
    assert source_item.ai_tagging_status == "skipped_ai_tagging_disabled"
    assert source_item.localization_status == "blocked_llm_calls_forbidden"
    execute_summary = result["manual_sync_execute"]
    assert execute_summary["source_mutation_performed"] is False
    assert execute_summary["llm_calls_performed"] is False
    assert execute_summary["localization"]["scheduled"] is False
    assert "private_plan_items" not in str(result)


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
    assert db.query(Media).count() == 1
    source_item = db.query(DynamicSourceItem).one()
    assert source_item.classification_status == "skipped_classification_model_uncached"
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


def test_s3a_m1_heuristic_classifies_after_ai_tags_are_written(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_METHOD", "heuristic")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD", "1")
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD", "0.5")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "true")
    _patch_test_storage(monkeypatch, tmp_path)

    def fake_ai_tagging(db_arg, media_id):
        media = db_arg.get(Media, media_id)
        media.content_class_locked = False
        tag = Tag(name="unit_ai_style", category=TagCategoryEnum.general)
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
            "predictions": [{"name": "unit_ai_style", "confidence": 0.99, "action": "confirmed"}],
            "provenance": {"provider_backend": "unit"},
        }

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
    source_item = db.query(DynamicSourceItem).one()
    media = db.query(Media).one()
    assert source_item.ai_tagging_status == "ai_tagged"
    assert source_item.classification_status == "classified"
    assert media.content_class == ContentClassEnum.anime
    stage_rows = {row["name"]: row for row in result["manual_sync_execute"]["stage_rows"]}
    assert stage_rows["classification"]["method"] == "heuristic"
    assert stage_rows["classification"]["order"] == "ai_tagging_before_classification"
    run_item = db.query(DynamicSyncRunItem).one()
    assert run_item.current_metadata_json["ai_tagging"]["status"] == "ai_tagged"
    assert run_item.current_metadata_json["classification"]["content_class"] == "anime"


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

    assert result["status"] == "completed"
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
    assert result["manual_sync_execute"]["stopped_by"] == "stopped_by_failure_budget"
    assert result["manual_sync_execute"]["unprocessed_count"] == 1
    assert result["manual_sync_execute"]["unprocessed_import_planned_count"] == 1
    assert db.get(DynamicSyncRun, run.id).pending_import_items == 1
    assert db.query(DynamicSyncRunItem).count() == 2


def test_s3a_m1_execute_stops_on_duration_budget(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    monkeypatch.setattr(execute_service, "MANUAL_SYNC_EXECUTE_MAX_DURATION_SECONDS", -1)
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
    assert db.query(DynamicSyncRunItem).count() == 0
