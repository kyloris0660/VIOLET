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
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.models import DynamicSourceItem, DynamicSyncRun, DynamicSyncRunItem, Media  # noqa: E402
from app.services import dynamic_library_sync_service as planner  # noqa: E402
from app.services import manual_sync_execute_service as execute_service  # noqa: E402
from app.services.dynamic_library_sync_service import S3A_M1_MANUAL_EXECUTE_CONFIRMATION_PREFIX  # noqa: E402
from app.services.manual_sync_execute_service import (  # noqa: E402
    ManualSyncExecuteError,
    create_manual_sync_execute_run,
    execute_manual_sync_run,
)


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


def test_s3a_m1_plan_integrity_is_public_safe_and_stable(db, tmp_path):
    source_root = tmp_path / "source"
    image_path = source_root / "private-name.png"
    _write_png(image_path)

    first = planner.plan_manual_sync_dry_run(db, source_path=source_root, max_files=5, stable_age_seconds=0)
    second = planner.plan_manual_sync_dry_run(db, source_path=source_root, max_files=5, stable_age_seconds=0)

    assert first["integrity"]["plan_hash"] == second["integrity"]["plan_hash"]
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


def test_s3a_m1_clip_cache_gate_does_not_enter_download_path(tmp_path, monkeypatch):
    calls = []
    fake_classifier = SimpleNamespace(
        _session=None,
        _text_embeddings=None,
        reset_failure=lambda: calls.append("reset_failure"),
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
        _write_png(source_root / f"missing-{index}.png")
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
    assert db.query(DynamicSyncRunItem).count() == 0
