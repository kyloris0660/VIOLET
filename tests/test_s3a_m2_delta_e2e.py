from __future__ import annotations

import json
import hashlib
import os
import sys
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

from app.database import Base
from app.enums import FileTypeEnum, TagCategoryEnum
from app.models import DynamicSourceItem, DynamicSourceRoot, DynamicSyncRun, DynamicSyncRunItem, Media, Tag, blombooru_media_tags
import scripts.run_s3a_m2_delta_e2e_with_telemetry as s3a_m2_runner
import scripts.audit_manual_sync_non_target_ai_localization as non_target_audit
import scripts.repair_s3a_m2_priority_backlog as priority_backlog_repair
from scripts.run_s3a_m2_delta_e2e_with_telemetry import (
    _placeholder_rows_from_plan,
    build_standard_pipeline_flow,
    build_ledger_pending_plan,
    build_source_delta_plan,
    refresh_completion_claims,
    ResourceTelemetryMonitor,
    S3AM2Blocked,
    StageTracker,
    s3a_m2_approval_phrase,
    summarize_telemetry,
)
from scripts.diagnose_s3a_m2_ai_tag_assignments import assignment_rows, load_run_ids_from_summary, repair_assignments
import scripts.validate_s3a_m2_gui_execute_acceptance as gui_validator


def test_s3a_m2_approval_phrase_is_plan_hash_bound() -> None:
    phrase = s3a_m2_approval_phrase("abcdef1234567890")

    assert phrase == "I APPROVE S3A-M2 PRODUCTION DELTA E2E abcdef123456"


def test_s3a_m2_non_target_audit_keeps_unknown_separate() -> None:
    assert non_target_audit._content_class_group("anime") == "target"
    assert non_target_audit._content_class_group("illustration") == "target"
    assert non_target_audit._content_class_group("non_anime") == "confirmed_non_target"
    assert non_target_audit._content_class_group("unknown") == "unknown_or_uncertain"
    assert non_target_audit._content_class_group(None) == "unknown_or_uncertain"


def test_s3a_m2_incident_diagnostic_requires_explicit_or_reported_run_ids(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="s3a_m2_summary_missing_run_ids"):
        load_run_ids_from_summary(tmp_path / "missing-summary.json")

    empty_summary = tmp_path / "summary.json"
    empty_summary.write_text(json.dumps({"execute": {}}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="s3a_m2_summary_missing_run_ids"):
        load_run_ids_from_summary(empty_summary)


def test_s3a_m2_runner_preserves_manual_llm_readiness_during_execute(monkeypatch) -> None:
    expected_hash = "abcdef123456"
    monkeypatch.setenv("TAG_TRANSLATION_LLM_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_AUTO_ENABLED", "true")
    monkeypatch.setenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "true")

    class FakeDb:
        def close(self) -> None:
            pass

    def fake_create(_db, *, args, plan, expected_hash):
        assert os.environ["TAG_TRANSLATION_LLM_ENABLED"] == "true"
        assert os.environ["TAG_TRANSLATION_AUTO_ENABLED"] == "false"
        assert os.environ["TAG_TRANSLATION_BACKGROUND_ENABLED"] == "false"
        return SimpleNamespace(id=42)

    def fake_execute(_db, *, run_id):
        assert run_id == 42
        assert os.environ["TAG_TRANSLATION_LLM_ENABLED"] == "true"
        return {"status": "completed"}

    from app.services import manual_sync_execute_service as execute_service

    monkeypatch.setattr(s3a_m2_runner, "open_db_session", lambda: FakeDb())
    monkeypatch.setattr(s3a_m2_runner, "_create_s3a_m2_execute_run_from_plan", fake_create)
    monkeypatch.setattr(execute_service, "execute_manual_sync_run", fake_execute)

    args = SimpleNamespace(
        expected_plan_hash=expected_hash,
        s3a_m2_approval_phrase=s3a_m2_approval_phrase(expected_hash),
        plan_source="source-delta",
    )
    plan = {"integrity": {"plan_hash": expected_hash, "confirmation_phrase": "ok", "production_confirmation_phrase": "ok"}}

    result = s3a_m2_runner.execute_manual_plan(args, plan, StageTracker())

    assert result["status"] == "completed"
    assert os.environ["TAG_TRANSLATION_LLM_ENABLED"] == "true"
    assert os.environ["TAG_TRANSLATION_AUTO_ENABLED"] == "true"
    assert os.environ["TAG_TRANSLATION_BACKGROUND_ENABLED"] == "true"


def test_s3a_m2_runner_execute_run_payload_includes_plan_mode(monkeypatch) -> None:
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
    db = Session()
    try:
        root = DynamicSourceRoot(label="fixture", root_path="/safe/source", root_path_hash="root-hash", is_active=True)
        db.add(root)
        db.commit()

        from app.services import manual_sync_execute_service as execute_service

        monkeypatch.setenv("VIOLET_ENV", "test")
        monkeypatch.setattr(execute_service, "is_manual_sync_execute_active", lambda: False)
        monkeypatch.setattr(execute_service, "_recover_stale_manual_sync_execute_runs", lambda _db: [])
        monkeypatch.setattr(execute_service, "_find_active_manual_sync_execute_run", lambda _db: None)
        monkeypatch.setattr(execute_service, "manual_sync_execute_effective_max_files", lambda value: int(value))
        monkeypatch.setattr(execute_service, "_verify_execute_gates", lambda **_kwargs: None)
        monkeypatch.setattr(execute_service, "_budget_policy_payload", lambda: {"max_files": 5})
        monkeypatch.setattr(execute_service, "_localization_policy_payload", lambda _items: {"status": "not_started"})

        captured: dict[str, str] = {}

        def fake_public_request_payload(*, plan_mode: str, **kwargs):
            captured["plan_mode"] = plan_mode
            return {"plan_mode": plan_mode, **kwargs}

        monkeypatch.setattr(execute_service, "_public_request_payload", fake_public_request_payload)

        args = SimpleNamespace(
            root_id=root.id,
            delta_cap=5,
            hydrated_only=True,
            stable_age_seconds=0,
            plan_created_at="2026-07-01T00:00:00+00:00",
            plan_source="source-delta",
        )
        plan = {
            "source": {"plan_source": "source_delta"},
            "limits": {"plan_mode": "incremental"},
            "counts": {"total_seen": 0, "estimated_import_count": 0},
            "integrity": {
                "plan_hash": "abc123",
                "confirmation_phrase": "ok",
                "production_confirmation_phrase": "ok",
            },
            "private_details": {"items": []},
        }

        run = s3a_m2_runner._create_s3a_m2_execute_run_from_plan(
            db,
            args=args,
            plan=plan,
            expected_hash="abc123",
        )

        assert captured["plan_mode"] == "incremental"
        assert run.summary_json["manual_sync_execute"]["request"]["plan_mode"] == "incremental"
        assert run.summary_json["manual_sync_execute"]["request"]["plan_source"] == "source_delta"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_priority_backlog_repair_terminalizes_stale_existing_rows(tmp_path: Path, monkeypatch) -> None:
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
    db = Session()
    try:
        from app.config import settings

        storage = tmp_path / "storage"
        original = storage / "media" / "original"
        original.mkdir(parents=True)
        monkeypatch.setattr(settings, "STORAGE_ROOT", storage)

        source_root = tmp_path / "source"
        source_root.mkdir()
        stale_file = source_root / "stale.png"
        stale_file.write_bytes(b"stale-local-file")
        old_ns = 1_800_000_000_000_000_000
        new_ns = old_ns + 30 * 24 * 60 * 60 * 1_000_000_000
        os.utime(stale_file, ns=(old_ns, old_ns))
        (original / "existing.png").write_bytes(b"managed-media")

        root = DynamicSourceRoot(label="icloud-photos-production", root_path=str(source_root), root_path_hash="root-hash", is_active=True)
        db.add(root)
        db.flush()
        media = Media(filename="existing.png", path="media/original/existing.png", hash="hash-stale", file_type=FileTypeEnum.image)
        db.add(media)
        db.flush()
        db.add(
            DynamicSourceItem(
                source_root_id=root.id,
                relative_path="watermark.png",
                relative_path_hash="watermark-hash",
                file_size=1,
                mtime_ns=new_ns,
                content_hash="hash-watermark",
                source_status="available",
                sync_state="imported",
                import_status="imported",
                classification_status="classified",
                ai_tagging_status="ai_tagged",
                localization_status="localized",
                media_id=media.id,
            )
        )
        stale = DynamicSourceItem(
            source_root_id=root.id,
            relative_path="stale.png",
            relative_path_hash="stale-hash",
            file_size=stale_file.stat().st_size,
            mtime_ns=stale_file.stat().st_mtime_ns,
            content_hash="hash-stale",
            source_status="available",
            sync_state="changed",
            import_status="pending",
            classification_status="waiting_import",
            ai_tagging_status="waiting_import",
            localization_status="waiting_ai_tags",
            media_id=media.id,
        )
        db.add(stale)
        db.commit()

        before = priority_backlog_repair.select_repair_candidates(db, root_id=root.id, output_dir=tmp_path)
        assert before["candidate_count"] == 1

        repaired = priority_backlog_repair.execute_repair(db, candidate_ids=list(before["candidate_ids"]))
        after = priority_backlog_repair.select_repair_candidates(db, root_id=root.id, output_dir=tmp_path)

        assert repaired == 1
        assert after["candidate_count"] == 0
        db.refresh(stale)
        assert stale.sync_state == "skipped_existing_media"
        assert stale.import_status == "skipped"
        assert stale.deferred_reason == "existing_media_hash"
        assert stale.classification_status == "classified_reused"
        assert stale.ai_tagging_status == "tagged_reused"
        assert stale.localization_status == "localized"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_gui_validator_requires_web_admin_provenance() -> None:
    runner_run = SimpleNamespace(
        summary_json={
            "manual_sync_execute": {
                "request": {
                    "request_source": "api_or_runner",
                    "gui_validation_session_id": "gui-test-session",
                }
            }
        }
    )
    gui_run = SimpleNamespace(
        summary_json={
            "manual_sync_execute": {
                "request": {
                    "request_source": "web_admin_gui",
                    "gui_validation_session_id": "gui-test-session",
                    "gui_validation_session_signature_valid": True,
                    "gui_plan_hash_bound": True,
                    "gui_plan_flow_verified": True,
                    "gui_plan_request_id": "gui-plan-test",
                    "client_route": "/admin?tab=content#dynamic-library-sync-section",
                }
            }
        }
    )
    unsigned_gui_run = SimpleNamespace(
        summary_json={
            "manual_sync_execute": {
                "request": {
                    "request_source": "web_admin_gui",
                    "gui_validation_session_id": "gui-test-session",
                    "gui_validation_session_signature_valid": False,
                    "gui_plan_hash_bound": True,
                    "gui_plan_flow_verified": True,
                    "gui_plan_request_id": "gui-plan-test",
                    "client_route": "/admin?tab=content#dynamic-library-sync-section",
                }
            }
        }
    )

    assert gui_validator.gui_provenance_for_run(runner_run)["valid"] is False
    assert gui_validator.gui_provenance_for_run(unsigned_gui_run)["valid"] is False
    assert gui_validator.gui_provenance_for_run(gui_run, expected_session_id="wrong-session")["valid"] is False
    assert gui_validator.gui_provenance_for_run(gui_run, expected_session_id="gui-test-session")["valid"] is True

    unbound_gui_run = SimpleNamespace(
        summary_json={
            "manual_sync_execute": {
                "request": {
                    "request_source": "web_admin_gui",
                    "gui_validation_session_id": "gui-test-session",
                    "gui_validation_session_signature_valid": True,
                    "client_route": "/admin?tab=content#dynamic-library-sync-section",
                }
            }
        }
    )
    assert gui_validator.gui_provenance_for_run(unbound_gui_run)["valid"] is False


def test_s3a_m2_gui_validator_rejects_private_output_outside_manifest_tree(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="gui_acceptance_output_dir_outside_local_manifest_tree"):
        gui_validator.ensure_private_output_dir(tmp_path)


def test_s3a_m2_gui_validator_scopes_remaining_inventory_to_validated_root() -> None:
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
    db = Session()
    try:
        root_gui = DynamicSourceRoot(label="gui", root_path="/safe/gui", root_path_hash="hash-gui", is_active=True)
        root_other = DynamicSourceRoot(label="other", root_path="/safe/other", root_path_hash="hash-other", is_active=True)
        db.add_all([root_gui, root_other])
        db.flush()
        run = DynamicSyncRun(id=9, run_type="manual_sync_execute", mode="production_acceptance", status="completed")
        db.add(run)
        db.flush()
        imported_item = DynamicSourceItem(
            source_root_id=root_gui.id,
            relative_path="imported.png",
            relative_path_hash="rel-imported",
            source_status="available",
            sync_state="imported",
            import_status="imported",
            classification_status="classified",
            ai_tagging_status="ai_tagged",
            localization_status="localized",
        )
        placeholder_item = DynamicSourceItem(
            source_root_id=root_gui.id,
            relative_path="placeholder.png",
            relative_path_hash="rel-placeholder",
            source_status="deferred",
            sync_state="skipped_placeholder",
            import_status="deferred",
            deferred_reason="cloud_placeholder",
        )
        other_root_pending = DynamicSourceItem(
            source_root_id=root_other.id,
            relative_path="other.png",
            relative_path_hash="rel-other",
            source_status="available",
            sync_state="new",
            import_status="pending",
        )
        db.add_all([imported_item, placeholder_item, other_root_pending])
        db.flush()
        db.add_all(
            [
                DynamicSyncRunItem(
                    sync_run_id=run.id,
                    source_item_id=imported_item.id,
                    item_state="imported",
                    action="import",
                    eligible_for_db_import=True,
                ),
                DynamicSyncRunItem(
                    sync_run_id=run.id,
                    source_item_id=placeholder_item.id,
                    item_state="skipped_placeholder",
                    reason="cloud_placeholder",
                    action="skip",
                    eligible_for_db_import=False,
                ),
            ]
        )
        db.commit()

        summary = gui_validator.run_items_summary(db, 9, root_id=root_gui.id)

        assert summary["remaining_importable_db_pending_count"] == 0
        assert summary["remaining_placeholder_db_count"] == 1
        assert summary["skipped_placeholder_run_item_count"] == 1
        assert summary["source_root_ids"] == [root_gui.id]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_gui_validator_applies_manual_e2e_profile_flags(tmp_path: Path, monkeypatch) -> None:
    profile_path = tmp_path / "production-profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "profile_id": "production-default",
                "repo_root": str(tmp_path),
                "python": sys.executable,
                "app_port": 8012,
                "storage_root": str(tmp_path / "storage"),
                "require_auth": True,
                "manual_sync_enabled": True,
                "manual_sync_execute_enabled": True,
                "manual_sync_execute_max_files": 1000,
                "db": {
                    "host": "localhost",
                    "port": 5432,
                    "name": "blombooru",
                    "user": "postgres",
                    "password": "",
                },
                "tag_translation_llm": {
                    "api_key": "test-key",
                    "model": "test-model",
                    "base_url": "http://127.0.0.1:1/v1",
                },
                "automation_flags": {
                    "dynamic_library_auto_sync": False,
                    "ai_auto_tag_after_import": False,
                    "content_classification_auto_after_import": False,
                    "tag_translation_auto": False,
                    "tag_translation_background": False,
                },
            }
        ),
        encoding="utf-8",
    )
    for key in (
        "AI_TAGGING_ENABLED",
        "CONTENT_CLASSIFICATION_ENABLED",
        "CONTENT_CLASSIFICATION_METHOD",
        "TAG_TRANSLATION_LLM_ENABLED",
        "TAG_TRANSLATION_LLM_API_KEY",
        "TAG_TRANSLATION_LLM_MODEL",
        "TAG_TRANSLATION_LLM_BASE_URL",
        "DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED",
        "TAG_TRANSLATION_BACKGROUND_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)

    public = gui_validator.apply_profile_env(profile_path)

    assert os.environ["AI_TAGGING_ENABLED"] == "true"
    assert os.environ["CONTENT_CLASSIFICATION_ENABLED"] == "true"
    assert os.environ["CONTENT_CLASSIFICATION_METHOD"] == "clip"
    assert os.environ["TAG_TRANSLATION_LLM_ENABLED"] == "true"
    assert os.environ["TAG_TRANSLATION_LLM_API_KEY"] == "test-key"
    assert os.environ["TAG_TRANSLATION_LLM_MODEL"] == "test-model"
    assert os.environ["TAG_TRANSLATION_LLM_BASE_URL"] == "http://127.0.0.1:1/v1"
    assert os.environ["DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED"] == "false"
    assert os.environ["TAG_TRANSLATION_BACKGROUND_ENABLED"] == "false"
    assert public["manual_e2e_components"]["ai_tagging_enabled"] is True
    assert public["manual_e2e_components"]["content_classification_method"] == "clip"
    assert public["manual_e2e_components"]["tag_translation_llm_enabled"] is True
    assert public["tag_translation_llm"]["api_key_present"] is True
    assert public["tag_translation_llm"]["model_configured"] is True
    assert public["tag_translation_llm"]["base_url_configured"] is True
    assert public["manual_e2e_components"]["auto_or_background_sync_enabled"] is False


def test_s3a_m2_gui_validator_migrates_legacy_profile_heuristic_to_clip(tmp_path: Path, monkeypatch) -> None:
    profile_path = tmp_path / "production-profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "profile_id": "production-default",
                "repo_root": str(tmp_path),
                "python": sys.executable,
                "app_port": 8012,
                "storage_root": str(tmp_path / "storage"),
                "require_auth": True,
                "manual_sync_enabled": True,
                "manual_sync_execute_enabled": True,
                "manual_e2e_components": {
                    "ai_tagging_enabled": True,
                    "content_classification_enabled": True,
                    "content_classification_method": "heuristic",
                    "tag_translation_llm_enabled": True,
                    "ai_tagging_auto_localization": False,
                },
                "db": {
                    "host": "localhost",
                    "port": 5432,
                    "name": "blombooru",
                    "user": "postgres",
                    "password": "",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("CONTENT_CLASSIFICATION_METHOD", raising=False)

    public = gui_validator.apply_profile_env(profile_path)

    manual = public["manual_e2e_components"]
    assert os.environ["CONTENT_CLASSIFICATION_METHOD"] == "clip"
    assert manual["content_classification_method"] == "clip"
    assert manual["content_classification_method_explicit"] is False
    assert manual["content_classification_method_migrated_from"] == "heuristic"


def test_s3a_m2_gui_validator_clears_profile_controlled_llm_env_when_absent(tmp_path: Path, monkeypatch) -> None:
    profile_path = tmp_path / "production-profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "profile_id": "production-default",
                "repo_root": str(tmp_path),
                "python": sys.executable,
                "app_port": 8012,
                "storage_root": str(tmp_path / "storage"),
                "require_auth": True,
                "manual_sync_enabled": "true",
                "manual_sync_execute_enabled": "true",
                "manual_e2e_components": {
                    "ai_tagging_enabled": "true",
                    "content_classification_enabled": "true",
                    "tag_translation_llm_enabled": "true",
                    "ai_tagging_auto_localization": "false",
                },
                "db": {
                    "host": "localhost",
                    "port": 5432,
                    "name": "blombooru",
                    "user": "postgres",
                    "password": "",
                },
                "tag_translation_llm": {
                    "provider": "openai_compatible",
                    "api_key": "",
                    "model": "",
                    "base_url": "",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TAG_TRANSLATION_LLM_API_KEY", "stale-shell-key")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_MODEL", "stale-shell-model")
    monkeypatch.setenv("TAG_TRANSLATION_LLM_BASE_URL", "http://stale.example/v1")

    public = gui_validator.apply_profile_env(profile_path)

    assert "TAG_TRANSLATION_LLM_API_KEY" not in os.environ
    assert "TAG_TRANSLATION_LLM_MODEL" not in os.environ
    assert "TAG_TRANSLATION_LLM_BASE_URL" not in os.environ
    assert public["manual_sync_enabled"] is True
    assert public["manual_sync_execute_enabled"] is True
    assert public["tag_translation_llm"]["api_key_present"] is False
    assert public["tag_translation_llm"]["model_configured"] is False
    assert public["tag_translation_llm"]["base_url_configured"] is False


def test_s3a_m2_gui_validator_fails_if_gui_run_imports_without_ai_tagging(tmp_path: Path, monkeypatch) -> None:
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
    db = Session()
    profile_path = tmp_path / "production-profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "profile_id": "production-default",
                "repo_root": str(tmp_path),
                "python": sys.executable,
                "app_port": 8012,
                "storage_root": str(tmp_path / "storage"),
                "require_auth": True,
                "manual_sync_enabled": True,
                "manual_sync_execute_enabled": True,
                "manual_sync_execute_max_files": 1000,
                "db": {"host": "localhost", "port": 5432, "name": "blombooru", "user": "postgres", "password": ""},
                "tag_translation_llm": {
                    "api_key": "test-key",
                    "model": "test-model",
                    "base_url": "http://127.0.0.1:1/v1",
                },
            }
        ),
        encoding="utf-8",
    )
    try:
        root = DynamicSourceRoot(
            label="gui",
            root_path=str(tmp_path / "source"),
            root_path_hash="guiroot",
            is_active=True,
        )
        media = Media(
            filename="gui-import.png",
            path="media/original/gui-import.png",
            hash="gui-import-hash",
            file_type=FileTypeEnum.image,
        )
        run = DynamicSyncRun(
            id=9,
            run_type="manual_sync_execute",
            mode="production_acceptance",
            status="completed",
            dry_run=False,
            total_seen=1,
            new_items=1,
            summary_json={
                "manual_sync_execute": {
                    "request": {
                        "request_source": "web_admin_gui",
                        "gui_validation_session_id": "gui-test-session",
                        "gui_validation_session_signature_valid": True,
                        "gui_plan_hash_bound": True,
                        "gui_plan_flow_verified": True,
                        "gui_plan_request_id": "gui-plan-test",
                        "runtime_git_head": "test-head",
                        "runtime_git_branch": "test-branch",
                        "client_route": "/admin?tab=content#dynamic-library-sync-section",
                    },
                    "outcome_counts": {"imported": 1},
                    "localization": {"status": "completed", "blocked_reason": None},
                }
            },
        )
        db.add_all([root, media, run])
        db.flush()
        item = DynamicSourceItem(
            source_root_id=root.id,
            relative_path="gui-import.png",
            relative_path_hash="gui-import-relhash",
            media_id=media.id,
            sync_state="imported",
            import_status="imported",
            classification_status="classified",
            ai_tagging_status="pending",
            localization_status="localized",
            last_sync_run_id=run.id,
        )
        db.add(item)
        db.flush()
        db.add(
            DynamicSyncRunItem(
                sync_run_id=run.id,
                source_item_id=item.id,
                item_state="imported",
                action="import",
                eligible_for_db_import=True,
                media_id=media.id,
            )
        )
        db.commit()
        monkeypatch.setattr(gui_validator, "open_db_session", lambda: Session())
        monkeypatch.setattr(
            gui_validator,
            "git_value",
            lambda *args: "test-head" if args == ("rev-parse", "HEAD") else "test-branch",
        )
        args = SimpleNamespace(
            profile_json=profile_path,
            min_run_id=8,
            run_id=None,
            gui_validation_session_id=None,
            allow_zero_import=False,
            allow_older_head=False,
        )

        public, _private = gui_validator.build_validation(args)

        assert public["validated"] is False
        assert "ai_tagging_incomplete_for_imported_items" in public["blockers"]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_ai_repair_commits_before_fresh_session_audit(tmp_path: Path) -> None:
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
    db = Session()
    try:
        media = Media(
            filename="repair-test.png",
            path="media/original/repair-test.png",
            hash="repair-test-hash",
            file_type=FileTypeEnum.image,
        )
        tag = Tag(name="repair_test_character", category=TagCategoryEnum.character, post_count=1)
        db.add_all([media, tag])
        db.flush()
        db.execute(
            blombooru_media_tags.insert().values(
                media_id=media.id,
                tag_id=tag.id,
                source="ai_wd",
                confidence=0.99,
                is_suggestion=True,
                is_locked=False,
            )
        )
        db.commit()
        media_id = int(media.id)
        rows = assignment_rows(db, [media_id])

        repair = repair_assignments(
            db,
            rows,
            run_ids=[7],
            reclassify=False,
            allow_clip_classification=False,
        )

        assert repair["db_commit_performed"] is True
        assert repair["assignments_converted_from_suggestion_to_normal"] == 1
        db.close()
        fresh = Session()
        try:
            post_rows = assignment_rows(fresh, [media_id])
            assert len(post_rows) == 1
            assert post_rows[0]["is_suggestion"] is False
        finally:
            fresh.close()
    finally:
        if db.is_active:
            db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_runner_rejects_cap_above_approved_ceiling_before_configure(monkeypatch) -> None:
    def fail_configure(_args):
        raise AssertionError("configure_phase_env must not run for over-ceiling cap")

    monkeypatch.setattr(s3a_m2_runner, "configure_phase_env", fail_configure)

    assert s3a_m2_runner.main(["--dry-run", "--delta-cap", "1001"]) == 2


def test_s3a_m2_runner_rejects_non_positive_translation_batch_max_before_configure(monkeypatch) -> None:
    def fail_configure(_args):
        raise AssertionError("configure_phase_env must not run for invalid translation batch max")

    monkeypatch.setattr(s3a_m2_runner, "configure_phase_env", fail_configure)

    assert s3a_m2_runner.main(["--dry-run", "--translation-batch-max-items", "0"]) == 2


def test_s3a_m2_telemetry_summary_uses_public_safe_artifact_label(tmp_path: Path) -> None:
    samples_path = tmp_path / "resource-samples.jsonl"
    samples = [
        {
            "timestamp": "2026-06-26T00:00:00Z",
            "stage": "ai_tagging",
            "provider": {"actual_provider": "DmlExecutionProvider"},
            "psutil_available": True,
            "nvidia_smi_available": True,
            "system": {"ram_percent": 41.5},
            "process": {"rss_bytes": 123456},
            "gpu": [
                {
                    "name": "Test GPU",
                    "utilization_gpu_percent": 72.0,
                    "memory_used_mib": 2048.0,
                }
            ],
        }
    ]
    samples_path.write_text("\n".join(json.dumps(row) for row in samples) + "\n", encoding="utf-8")

    summary = summarize_telemetry(samples_path)

    assert summary["status"] == "collected"
    assert summary["actual_provider"] == "DmlExecutionProvider"
    assert summary["gpu_provider_used"] is True
    assert summary["max_gpu_memory_used_mib"] == 2048.0
    assert summary["peak_gpu_utilization_percent"] == 72.0
    assert summary["raw_samples_path_redacted"] is True
    assert str(tmp_path) not in json.dumps(summary, sort_keys=True)


def test_s3a_m2_telemetry_summary_reads_nested_provider_provenance(tmp_path: Path) -> None:
    samples_path = tmp_path / "resource-samples.jsonl"
    sample = {
        "timestamp": "2026-06-26T00:00:00Z",
        "stage": "localization",
        "provider": {
            "backend": "onnxruntime",
            "provider": {
                "actual_provider": "DmlExecutionProvider",
                "actual_onnx_provider_loaded": "DmlExecutionProvider",
            },
        },
        "nvidia_smi_available": True,
        "gpu": [{"name": "Test GPU", "utilization_gpu_percent": 55, "memory_used_mib": 1024}],
    }
    samples_path.write_text(json.dumps(sample) + "\n", encoding="utf-8")

    summary = summarize_telemetry(samples_path)

    assert summary["actual_provider"] == "DmlExecutionProvider"
    assert summary["gpu_provider_used"] is True
    assert summary["cpu_fallback_observed"] is False


def test_s3a_m2_telemetry_monitor_rejects_output_outside_approved_tree(tmp_path: Path) -> None:
    with pytest.raises(S3AM2Blocked, match="telemetry_dir_outside_approved_tree"):
        ResourceTelemetryMonitor(
            telemetry_dir=tmp_path,
            stage_tracker=StageTracker(),
            provider_getter=lambda: {},
        )


def test_s3a_m2_completion_claim_requires_launcher_validation() -> None:
    summary = {
        "status": "completed_with_followup_required",
        "pipeline_contract": {
            "contract_id": "s3a_m2_production_delta_e2e_contract_v1",
            "fresh_dry_run_completed": True,
            "claims": {"target_met": False},
        },
        "production_acceptance": {"performed": True},
        "controlled_delta": {"cap_exceeded": False},
        "dry_run": {"total_seen": 3, "partial_scan": False, "state_counts": {"skipped_placeholder": 0}},
        "readiness": {"passed": True},
        "ledger_consistency": {"passed": True},
        "public_redaction": {"passed": True, "finding_count": 0},
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-production-delta-e2e.md",
            "summary_json_path": "docs/reports/s3a-m2-production-delta-e2e-summary.json",
        },
        "safety": {"private_paths_or_hashes_in_public_report": False},
        "localization": {"status": "completed", "failed": 0},
        "localization_diagnosis": {
            "diagnosis": "benign_all_localizable_tags_already_localized_or_newly_localized",
            "tags_requiring_localization_after_runner": 0,
        },
        "placeholder_hydration": {
            "status": "completed",
            "remaining_placeholders_after_hydration": 0,
            "source_content_written": False,
            "source_deleted_moved_renamed": False,
        },
        "final_inventory": {
            "current_importable_hydrated_supported_items": 0,
            "placeholders_remaining": 0,
        },
        "execute": {
            "status": "completed",
            "imported": 3,
            "classified": 3,
            "ai_tagged": 3,
            "provider_provenance": {"provider": {"actual_provider": "DmlExecutionProvider"}},
        },
        "classification": {"reported": True, "count": 3, "failed": 0},
        "ai_tagging": {
            "reported": True,
            "count": 3,
            "failed": 0,
            "mature_media_tag_policy": True,
            "proper_nouns_suggestion_only": False,
            "no_sourceconcept_or_entity_truth_from_ai_only_tags": True,
        },
        "gpu_telemetry": {"status": "collected"},
        "launcher_web_admin_acceptance": {"validated": False, "status": "pending"},
        "ai_tag_assignment_incident": {
            "status": "repaired",
            "public_safe": True,
            "after": {
                "all_ai_assignments_are_suggestions": False,
                "high_conf_nonproper_expected_normal_count": 2,
                "high_conf_nonproper_incorrect_suggestion_count": 0,
                "high_conf_nonproper_normal_count": 2,
                "high_conf_proper_expected_normal_count": 1,
                "high_conf_proper_incorrect_suggestion_count": 0,
                "high_conf_proper_normal_count": 1,
                "proper_noun_non_suggestion_count": 1,
            },
            "entity_truth_violations_found": 0,
            "localization_remaining_gap": 0,
            "ui_verification": {
                "status": "passed",
                "public_safe": True,
                "sample_count": 2,
                "normal_visible_pass_count": 2,
                "proper_suggestion_visible_pass_count": 1,
            },
        },
        "cohort_self_audit": {
            "status": "passed_after_repair",
            "public_safe": True,
            "normal_ai_tag_semantics_consistent_with_policy": True,
            "blocker_anomaly_count": 0,
            "affected_media_count": 3,
            "baseline_media_count": 3,
        },
    }

    pending = refresh_completion_claims(json.loads(json.dumps(summary)))
    assert pending["status"] == "completed_with_followup_required"
    assert pending["pipeline_contract"]["claims"]["target_met"] is False

    summary["remaining_run"] = {"run_id": 8}
    summary["launcher_web_admin_acceptance"] = {
        "validated": True,
        "status": "passed_gui_execute_completed",
        "execute_clicked": True,
        "gui_execute_completed": True,
        "gui_execute_run_id": 9,
        "gui_provenance_valid": True,
        "request_source": "web_admin_gui",
        "gui_validation_session_id_present": True,
        "gui_validation_session_signature_valid": True,
    }
    completed = refresh_completion_claims(summary)

    assert completed["status"] == "target_met"
    assert completed["standard_pipeline_flow"]["status"] == "completed"
    assert completed["standard_pipeline_flow"]["future_automation_readiness"] == "manual_pipeline_standardized_no_automatic_sync_implemented"
    assert completed["gpu_telemetry"]["actual_provider"] == "DmlExecutionProvider"
    assert completed["gpu_telemetry"]["validation_status"] == "passed"
    assert completed["pipeline_contract"]["claims"]["target_met"] is True


def test_s3a_m2_standard_pipeline_records_runner_fallback_without_gui_execute_claim() -> None:
    summary = {
        "pipeline_contract": {
            "contract_id": "s3a_m2_production_delta_e2e_contract_v1",
            "fresh_dry_run_completed": True,
        },
        "controlled_delta": {"cap": 1000},
        "dry_run": {"total_seen": 12, "partial_scan": False, "state_counts": {"skipped_placeholder": 0}},
        "execute": {"status": "completed", "imported": 5},
        "classification": {"reported": True, "count": 5, "failed": 0},
        "ai_tagging": {
            "reported": True,
            "count": 5,
            "failed": 0,
            "mature_media_tag_policy": True,
            "proper_nouns_suggestion_only": False,
            "no_sourceconcept_or_entity_truth_from_ai_only_tags": True,
        },
        "localization": {"status": "completed", "failed": 0},
        "localization_diagnosis": {
            "diagnosis": "benign_all_localizable_tags_already_localized_or_newly_localized",
            "tags_requiring_localization_after_runner": 0,
        },
        "placeholder_hydration": {
            "status": "completed",
            "remaining_placeholders_after_hydration": 0,
            "source_content_written": False,
            "source_deleted_moved_renamed": False,
        },
        "final_inventory": {
            "current_delta_candidates": 12,
            "current_importable_hydrated_supported_items": 0,
            "placeholders_remaining": 0,
            "scan_cap_stopped_scan": False,
        },
        "ledger_consistency": {"passed": True, "expected_plan_items": 12, "run_item_count": 12},
        "gpu_telemetry": {"status": "collected", "validation_status": "passed", "actual_provider": "DmlExecutionProvider"},
        "public_redaction": {"passed": True, "finding_count": 0},
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-production-delta-e2e.md",
            "summary_json_path": "docs/reports/s3a-m2-production-delta-e2e-summary.json",
        },
        "safety": {"private_paths_or_hashes_in_public_report": False},
        "launcher_web_admin_acceptance": {
            "validated": True,
            "status": "passed_gui_execute_not_safe_runner_execute_used",
            "execute_clicked": False,
            "fallback_reason": "GUI cannot execute source-delta private plan under telemetry wrapper.",
            "computer_use_result": "policy_stop_then_browser_validation",
        },
    }

    flow = build_standard_pipeline_flow(summary)

    assert flow["status"] == "incomplete"
    step = flow["steps"]["validate_launcher_web_admin_workflow"]
    assert step["status"] == "gui_execute_pending_fallback_documented"
    assert step["completed"] is False
    assert step["evidence"]["execute_clicked"] is False


def test_s3a_m2_completion_claim_rejects_stale_gui_run_id() -> None:
    summary = {
        "pipeline_contract": {"contract_id": "s3a_m2_production_delta_e2e_contract_v1", "fresh_dry_run_completed": True},
        "production_acceptance": {"performed": True},
        "controlled_delta": {"cap_exceeded": False},
        "dry_run": {"total_seen": 3, "partial_scan": False, "state_counts": {"skipped_placeholder": 0}},
        "readiness": {"passed": True},
        "ledger_consistency": {"passed": True},
        "public_redaction": {"passed": True, "finding_count": 0},
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-production-delta-e2e.md",
            "summary_json_path": "docs/reports/s3a-m2-production-delta-e2e-summary.json",
        },
        "safety": {"private_paths_or_hashes_in_public_report": False},
        "localization": {"status": "completed", "failed": 0},
        "localization_diagnosis": {
            "diagnosis": "benign_all_localizable_tags_already_localized_or_newly_localized",
            "tags_requiring_localization_after_runner": 0,
        },
        "placeholder_hydration": {
            "status": "completed",
            "remaining_placeholders_after_hydration": 0,
            "source_content_written": False,
            "source_deleted_moved_renamed": False,
        },
        "final_inventory": {"current_importable_hydrated_supported_items": 0, "placeholders_remaining": 0},
        "execute": {"status": "completed", "imported": 3, "classified": 3, "ai_tagged": 3},
        "remaining_run": {"run_id": 8},
        "classification": {"reported": True, "count": 3, "failed": 0},
        "ai_tagging": {
            "reported": True,
            "count": 3,
            "failed": 0,
            "mature_media_tag_policy": True,
            "proper_nouns_suggestion_only": False,
            "no_sourceconcept_or_entity_truth_from_ai_only_tags": True,
        },
        "gpu_telemetry": {"status": "collected", "validation_status": "passed", "actual_provider": "DmlExecutionProvider"},
        "launcher_web_admin_acceptance": {
            "validated": True,
            "status": "passed_gui_execute_completed",
            "execute_clicked": True,
            "gui_execute_completed": True,
            "gui_execute_run_id": 8,
        },
        "ai_tag_assignment_incident": {
            "status": "repaired",
            "public_safe": True,
            "after": {
                "all_ai_assignments_are_suggestions": False,
                "high_conf_nonproper_expected_normal_count": 2,
                "high_conf_nonproper_incorrect_suggestion_count": 0,
                "high_conf_nonproper_normal_count": 2,
                "high_conf_proper_expected_normal_count": 1,
                "high_conf_proper_incorrect_suggestion_count": 0,
                "high_conf_proper_normal_count": 1,
                "proper_noun_non_suggestion_count": 1,
            },
            "entity_truth_violations_found": 0,
            "localization_remaining_gap": 0,
            "ui_verification": {"status": "passed", "public_safe": True, "sample_count": 1, "normal_visible_pass_count": 1},
        },
        "cohort_self_audit": {
            "status": "passed_after_repair",
            "public_safe": True,
            "normal_ai_tag_semantics_consistent_with_policy": True,
            "blocker_anomaly_count": 0,
            "affected_media_count": 3,
            "baseline_media_count": 3,
        },
    }

    refreshed = refresh_completion_claims(summary)

    assert refreshed["pipeline_contract"]["claims"]["target_met"] is False
    assert refreshed["standard_pipeline_flow"]["steps"]["validate_launcher_web_admin_workflow"]["completed"] is False


def test_s3a_m2_ledger_pending_plan_uses_pending_delta_and_redacts_private_fields(tmp_path: Path) -> None:
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
    db = Session()
    try:
        source_root = tmp_path / "library"
        source_root.mkdir()
        root = DynamicSourceRoot(
            label="test",
            root_path=str(source_root),
            root_path_hash="abc1234567890defabc1234567890def",
            is_active=True,
        )
        db.add(root)
        db.flush()
        db.add(
            Media(
                filename="existing.png",
                path="media/original/existing.png",
                hash="hash-existing",
                file_type=FileTypeEnum.image,
            )
        )
        db.flush()
        db.add_all(
            [
                DynamicSourceItem(
                    source_root_id=root.id,
                    relative_path="new/image-1.png",
                    relative_path_hash="relhash-one",
                    file_size=123,
                    mtime_ns=1,
                    content_hash="hash-new",
                    source_status="available",
                    sync_state="new",
                    import_status="pending",
                ),
                DynamicSourceItem(
                    source_root_id=root.id,
                    relative_path="new/image-2.png",
                    relative_path_hash="relhash-two",
                    file_size=456,
                    mtime_ns=2,
                    content_hash="hash-existing",
                    source_status="available",
                    sync_state="new",
                    import_status="pending",
                ),
                DynamicSourceItem(
                    source_root_id=root.id,
                    relative_path="deferred/video.mov",
                    relative_path_hash="relhash-three",
                    source_status="deferred",
                    sync_state="skipped_unsupported",
                    import_status="deferred",
                    deferred_reason="unsupported_extension",
                ),
            ]
        )
        db.commit()

        args = SimpleNamespace(
            delta_cap=300,
            hydrated_only=True,
            stable_age_seconds=0.0,
            execute=False,
            plan_created_at="",
        )
        plan = build_ledger_pending_plan(db, args, root, include_private_details=False)

        counts = plan["counts"]
        assert counts["total_seen"] == 2
        assert counts["estimated_import_count"] == 1
        assert counts["state_counts"]["import_planned"] == 1
        assert counts["state_counts"]["skipped_existing_media"] == 1
        assert counts["source_ledger_aggregate_counts"]["sync_state_counts"]["skipped_unsupported"] == 1
        assert "private_details" not in plan
        public_json = json.dumps(plan, sort_keys=True)
        assert "new/image-1.png" not in public_json
        assert "hash-new" not in public_json
        assert "hash-existing" not in public_json
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_source_delta_plan_caps_delta_not_unchanged_known_files(tmp_path: Path) -> None:
    from PIL import Image
    from app.services.dynamic_library_sync_service import _hash_text

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
    db = Session()
    try:
        source_root = tmp_path / "library"
        source_root.mkdir()
        unchanged = source_root / "known.png"
        new_file = source_root / "fresh.png"
        Image.new("RGB", (2, 2), (1, 2, 3)).save(unchanged)
        Image.new("RGB", (2, 2), (4, 5, 6)).save(new_file)
        unchanged_stat = unchanged.stat()
        root = DynamicSourceRoot(
            label="test",
            root_path=str(source_root),
            root_path_hash="abc1234567890defabc1234567890def",
            is_active=True,
        )
        db.add(root)
        db.flush()
        media = Media(
            filename="known.png",
            path="media/original/known.png",
            hash="known-hash",
            file_type=FileTypeEnum.image,
        )
        db.add(media)
        db.flush()
        db.add(
            DynamicSourceItem(
                source_root_id=root.id,
                relative_path="known.png",
                relative_path_hash=_hash_text("known.png"),
                file_size=unchanged_stat.st_size,
                mtime_ns=unchanged_stat.st_mtime_ns,
                content_hash="known-hash",
                source_status="available",
                sync_state="new",
                import_status="imported",
                media_id=media.id,
            )
        )
        db.commit()

        args = SimpleNamespace(
            delta_cap=300,
            hydrated_only=True,
            stable_age_seconds=0.0,
            execute=False,
            plan_created_at="",
        )
        plan = build_source_delta_plan(db, args, root, include_private_details=False)

        counts = plan["counts"]
        assert counts["total_seen"] == 1
        assert counts["estimated_import_count"] == 1
        assert counts["state_counts"]["import_planned"] == 1
        assert plan["limits"]["scanned_files"] == 2
        assert plan["limits"]["unchanged_known_files"] == 1
        public_json = json.dumps(plan, sort_keys=True)
        assert "fresh.png" not in public_json
        assert "known.png" not in public_json
        assert "known-hash" not in public_json
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_source_delta_plan_skips_stable_unsupported_before_cap(tmp_path: Path) -> None:
    from PIL import Image
    from app.services.dynamic_library_sync_service import _hash_text

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
    db = Session()
    try:
        source_root = tmp_path / "library"
        source_root.mkdir()
        stable_unsupported = source_root / "old-video.mov"
        new_file = source_root / "fresh.png"
        stable_unsupported.write_bytes(b"not current image pipeline")
        Image.new("RGB", (2, 2), (4, 5, 6)).save(new_file)
        stable_stat = stable_unsupported.stat()
        root = DynamicSourceRoot(
            label="test",
            root_path=str(source_root),
            root_path_hash="abc1234567890defabc1234567890def",
            is_active=True,
        )
        db.add(root)
        db.flush()
        db.add(
            DynamicSourceItem(
                source_root_id=root.id,
                relative_path="old-video.mov",
                relative_path_hash=_hash_text("old-video.mov"),
                file_size=stable_stat.st_size,
                mtime_ns=stable_stat.st_mtime_ns,
                source_status="deferred",
                sync_state="skipped_unsupported",
                import_status="deferred",
                deferred_reason="unsupported_extension",
            )
        )
        db.commit()

        args = SimpleNamespace(
            delta_cap=1,
            hydrated_only=True,
            stable_age_seconds=0.0,
            execute=False,
            plan_created_at="",
        )
        plan = build_source_delta_plan(db, args, root, include_private_details=False)

        counts = plan["counts"]
        assert counts["total_seen"] == 1
        assert counts["estimated_import_count"] == 1
        assert counts["state_counts"]["import_planned"] == 1
        assert counts["state_counts"]["skipped_unsupported"] == 0
        assert counts["partial_scan"] is False
        assert plan["limits"]["scanned_files"] == 2
        assert plan["limits"]["unchanged_known_files"] == 1
        public_json = json.dumps(plan, sort_keys=True)
        assert "old-video.mov" not in public_json
        assert "fresh.png" not in public_json
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_source_delta_reincludes_imported_item_with_missing_media_link(tmp_path: Path) -> None:
    from PIL import Image
    from app.services.dynamic_library_sync_service import _hash_text

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
    db = Session()
    try:
        source_root = tmp_path / "library"
        source_root.mkdir()
        source_file = source_root / "orphaned-import.png"
        Image.new("RGB", (2, 2), (8, 8, 8)).save(source_file)
        stat = source_file.stat()
        rel_hash = _hash_text("orphaned-import.png")
        root = DynamicSourceRoot(
            label="test",
            root_path=str(source_root),
            root_path_hash="abc1234567890defabc1234567890def",
            is_active=True,
        )
        db.add(root)
        db.flush()
        db.add(
            DynamicSourceItem(
                source_root_id=root.id,
                relative_path="orphaned-import.png",
                relative_path_hash=rel_hash,
                file_size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                content_hash="previous-hash",
                source_status="available",
                sync_state="imported",
                import_status="imported",
                media_id=None,
            )
        )
        db.commit()

        args = SimpleNamespace(
            delta_cap=300,
            hydrated_only=True,
            stable_age_seconds=0.0,
            execute=False,
            plan_created_at="",
        )
        plan = build_source_delta_plan(db, args, root, include_private_details=False)

        counts = plan["counts"]
        assert counts["total_seen"] == 1
        assert counts["estimated_import_count"] == 1
        assert counts["state_counts"]["import_planned"] == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_source_delta_reincludes_unresolved_placeholder_from_run(tmp_path: Path) -> None:
    from PIL import Image

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
    db = Session()
    try:
        source_root = tmp_path / "library"
        source_root.mkdir()
        placeholder_now_hydrated = source_root / "hydrated.png"
        Image.new("RGB", (2, 2), (7, 8, 9)).save(placeholder_now_hydrated)
        stat = placeholder_now_hydrated.stat()
        root = DynamicSourceRoot(
            label="test",
            root_path=str(source_root),
            root_path_hash="abc1234567890defabc1234567890def",
            is_active=True,
        )
        db.add(root)
        db.flush()
        db.add(DynamicSyncRun(id=7, run_type="manual_sync_execute", mode="production_acceptance", status="completed"))
        db.add(
            DynamicSourceItem(
                source_root_id=root.id,
                relative_path="hydrated.png",
                relative_path_hash=hashlib.sha256("hydrated.png".encode("utf-8")).hexdigest(),
                file_size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                source_status="available",
                sync_state="skipped_placeholder",
                import_status="deferred",
                deferred_reason="cloud_placeholder",
                last_sync_run_id=7,
            )
        )
        db.commit()

        args = SimpleNamespace(
            delta_cap=300,
            hydrated_only=True,
            stable_age_seconds=0.0,
            execute=False,
            plan_created_at="",
            include_unresolved_run_id=7,
        )
        plan = build_source_delta_plan(db, args, root, include_private_details=False)

        counts = plan["counts"]
        assert counts["total_seen"] == 1
        assert counts["state_counts"]["import_planned"] == 1
        assert counts["estimated_import_count"] == 1
        assert plan["limits"]["unchanged_known_files"] == 0
        assert "hydrated.png" not in json.dumps(plan, sort_keys=True)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_placeholder_rows_from_private_plan_selects_only_placeholders() -> None:
    plan = {
        "private_details": {
            "items": [
                {"safe_label": "delta-1", "relative_path": "a.png", "relative_path_hash": "hash-a", "state": "skipped_placeholder", "reason": "cloud_placeholder"},
                {"safe_label": "delta-2", "relative_path": "b.png", "relative_path_hash": "hash-b", "state": "import_planned", "reason": None},
            ],
            "not_for_public_reports": True,
        }
    }

    rows = _placeholder_rows_from_plan(plan)

    assert len(rows) == 1
    assert rows[0]["safe_label"] == "delta-1"
    assert rows[0]["relative_path"] == "a.png"
