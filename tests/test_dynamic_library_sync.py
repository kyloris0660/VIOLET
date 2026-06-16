from __future__ import annotations

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
from app.enums import TagCategoryEnum  # noqa: E402
from app.models import DynamicSourceItem, DynamicSyncRun, Media, Tag, TagTranslation  # noqa: E402
from app.routes.admin import dynamic_library_sync as dynamic_routes  # noqa: E402
from app.services import dynamic_library_sync_service as service  # noqa: E402


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
    assert first["pending_summary"]["threshold"] == 100
    assert first["pending_summary"]["threshold_reached"] is False
    assert db.query(DynamicSourceItem).count() == 4
    assert db.query(Media).count() == 0

    second = service.run_update_check(db, root_ids=[root.id])
    assert second["pending_summary"]["pending_new"] == 2
    assert second["pending_summary"]["pending_deferred"] == 2
    assert db.query(DynamicSourceItem).count() == 4
    assert db.query(DynamicSyncRun).count() == 2


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
    assert gap["proper_noun_policy"]["search_alias_trust_sources"] == ["manual", "static"]


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
            display_name="未审核角色",
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
