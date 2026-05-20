"""Tests for the Phase 3.7 Tier-1000 classification/scope-gate runner."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

SCRIPT_PATH = ROOT / "scripts" / "run_phase37_tier1000_classification_scope_gate.py"
SPEC = importlib.util.spec_from_file_location("phase37_runner", SCRIPT_PATH)
phase37 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(phase37)

from app.database import Base  # noqa: E402
from app.enums import ContentClassEnum, FileTypeEnum, TagCategoryEnum  # noqa: E402
from app.models import (  # noqa: E402
    AITagJob,
    ClassificationJob,
    Media,
    Tag,
    TagTranslation,
    TagTranslationJob,
    blombooru_media_tags,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _settings(**overrides):
    values = {
        "VIOLET_ENV": "development",
        "IS_TEST_ENV": False,
        "DB_NAME": "blombooru",
        "DB_USER": "postgres",
        "DB_HOST": "localhost",
        "DB_PORT": 5432,
        "CONTENT_CLASSIFICATION_ENABLED": True,
        "CONTENT_CLASSIFICATION_METHOD": "clip",
        "CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS": 100,
        "CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT": False,
        "AI_AUTO_TAG_AFTER_IMPORT": False,
        "AI_TAGGING_AUTO_LOCALIZATION": False,
        "TAG_TRANSLATION_BG_ENABLED": False,
        "TAG_TRANSLATION_AUTO_ENABLED": False,
        "TAG_TRANSLATION_LLM_ENABLED": False,
        "ENTITY_ALIAS_RESOLVER_ENABLED": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _media(
    db,
    media_id: int,
    source: str,
    *,
    content_class=None,
):
    item = Media(
        id=media_id,
        filename=f"m{media_id}.jpg",
        path=f"media/original/m{media_id}.jpg",
        thumbnail_path=f"media/thumbnails/m{media_id}.jpg",
        hash=f"{media_id:064x}",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=123,
        source=source,
        content_class=content_class,
    )
    db.add(item)
    db.commit()
    return item


def _tag(db, tag_id: int, name: str, category=TagCategoryEnum.general):
    item = Tag(id=tag_id, name=name, category=category, post_count=1)
    db.add(item)
    db.commit()
    return item


def _ai_assoc(db, media_id: int, tag_id: int, *, suggestion: bool = False):
    db.execute(
        blombooru_media_tags.insert().values(
            media_id=media_id,
            tag_id=tag_id,
            source=phase37.AI_SOURCE,
            confidence=0.9,
            is_locked=False,
            is_suggestion=suggestion,
        )
    )
    db.commit()


def _backup(tmp_path: Path) -> Path:
    path = tmp_path / "phase37-before.dump"
    path.write_bytes(b"backup")
    return path


def _args(tmp_path: Path, **overrides):
    values = {
        "source_label": phase37.SOURCE_LABEL,
        "expected_media_count": 1,
        "confirm_phase37": phase37.CONFIRM_PHRASE,
        "db_backup_file": str(_backup(tmp_path)),
        "report_json": None,
        "chunk_size": 100,
        "limit": None,
        "force_reclassify": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_select_target_media_ids_scoped_to_phase35_source(db):
    _media(db, 1, phase37.SOURCE_LABEL)
    _media(db, 2, phase37.SOURCE_LABEL, content_class=ContentClassEnum.anime)
    _media(db, 3, "other-source")

    assert phase37.select_target_media_ids(db, phase37.SOURCE_LABEL) == [1]
    assert phase37.select_target_media_ids(
        db, phase37.SOURCE_LABEL, only_unclassified=False
    ) == [1, 2]


def test_write_gate_rejects_wrong_source_label_before_jobs(db, tmp_path):
    _media(db, 1, phase37.SOURCE_LABEL)
    args = _args(tmp_path, source_label="other-source")

    with pytest.raises(RuntimeError, match="locked to source label"):
        phase37.validate_common_write_gates(args, db, _settings())

    assert db.query(ClassificationJob).count() == 0


def test_write_gate_rejects_non_development_envs(db, tmp_path):
    _media(db, 1, phase37.SOURCE_LABEL)

    with pytest.raises(RuntimeError, match="VIOLET_ENV=development"):
        phase37.validate_common_write_gates(_args(tmp_path), db, _settings(VIOLET_ENV="production"))

    with pytest.raises(RuntimeError, match="VIOLET_ENV=development"):
        phase37.validate_common_write_gates(
            _args(tmp_path),
            db,
            _settings(VIOLET_ENV="test", IS_TEST_ENV=True),
        )

    assert db.query(ClassificationJob).count() == 0


def test_write_gate_rejects_missing_or_empty_backup(db, tmp_path):
    _media(db, 1, phase37.SOURCE_LABEL)
    missing = tmp_path / "missing.dump"
    with pytest.raises(RuntimeError, match="does not exist"):
        phase37.validate_common_write_gates(
            _args(tmp_path, db_backup_file=str(missing)),
            db,
            _settings(),
        )

    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")
    with pytest.raises(RuntimeError, match="non-empty"):
        phase37.validate_common_write_gates(
            _args(tmp_path, db_backup_file=str(empty)),
            db,
            _settings(),
        )


def test_write_gate_rejects_active_jobs(db, tmp_path):
    _media(db, 1, phase37.SOURCE_LABEL)
    db.add(ClassificationJob(status="pending", trigger_source="manual"))
    db.commit()

    with pytest.raises(RuntimeError, match="Active classification jobs"):
        phase37.validate_common_write_gates(_args(tmp_path), db, _settings())


def test_write_gate_rejects_ai_translation_and_entity_side_effect_flags(db, tmp_path):
    _media(db, 1, phase37.SOURCE_LABEL)
    unsafe_settings = _settings(
        AI_TAGGING_AUTO_LOCALIZATION=True,
        TAG_TRANSLATION_BG_ENABLED=True,
        ENTITY_ALIAS_RESOLVER_ENABLED=True,
    )
    with pytest.raises(RuntimeError):
        phase37.validate_common_write_gates(_args(tmp_path), db, unsafe_settings)


def test_classification_runner_uses_explicit_non_empty_chunks(db, tmp_path, monkeypatch):
    _media(db, 1, phase37.SOURCE_LABEL)
    _media(db, 2, phase37.SOURCE_LABEL)
    _media(db, 3, "other-source")
    report_json = tmp_path / "classification.json"
    settings = _settings(CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS=1)

    monkeypatch.setattr(
        phase37,
        "load_app_context",
        lambda: {"settings": settings, "database": SimpleNamespace(SessionLocal=lambda: db)},
    )

    import app.services.classification_job_service as service

    seen_chunks = []

    def fake_run(job_id: int):
        job = db.query(ClassificationJob).get(job_id)
        media_ids = json.loads(job.media_ids_json)
        assert media_ids
        seen_chunks.append(media_ids)
        media = db.query(Media).get(media_ids[0])
        media.content_class = ContentClassEnum.anime
        media.content_class_confidence = 0.9
        media.content_class_source = "test"
        media.content_class_model = "fake"
        job.status = "completed"
        job.processed = 1
        job.classified_anime = 1
        db.commit()

    monkeypatch.setattr(service, "run_classification_job", fake_run)

    payload = phase37.run_classification_controlled(
        _args(tmp_path, expected_media_count=2, report_json=str(report_json), chunk_size=100)
    )

    assert payload["success"] is True
    assert seen_chunks == [[1], [2]]
    assert db.query(Media).get(3).content_class is None
    assert report_json.exists()


def test_classification_failure_report_includes_failed_chunk(db, tmp_path, monkeypatch):
    _media(db, 1, phase37.SOURCE_LABEL)
    report_json = tmp_path / "classification-failed.json"
    settings = _settings(CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS=10)
    monkeypatch.setattr(
        phase37,
        "load_app_context",
        lambda: {"settings": settings, "database": SimpleNamespace(SessionLocal=lambda: db)},
    )

    import app.services.classification_job_service as service

    def fake_run(job_id: int):
        job = db.query(ClassificationJob).get(job_id)
        job.status = "completed"
        job.processed = 1
        job.failed = 1
        job.failed_items_json = json.dumps([{"media_id": 1, "error": "boom"}])
        db.commit()

    monkeypatch.setattr(service, "run_classification_job", fake_run)

    with pytest.raises(phase37.Phase37RunFailed):
        phase37.run_classification_controlled(
            _args(tmp_path, expected_media_count=1, report_json=str(report_json))
        )

    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert payload["success"] is False
    assert payload["status"] == "failed_classification_job"
    assert payload["totals"]["processed"] == 1
    assert payload["totals"]["failed"] == 1
    assert payload["jobs"][0]["failed_items"][0]["media_id"] == 1


def test_scope_audit_treats_only_anime_unknown_as_eligible(db):
    _media(db, 1, phase37.SOURCE_LABEL, content_class=ContentClassEnum.anime)
    _media(db, 2, phase37.SOURCE_LABEL, content_class=ContentClassEnum.unknown)
    _media(db, 3, phase37.SOURCE_LABEL, content_class=ContentClassEnum.non_anime)
    _media(db, 4, phase37.SOURCE_LABEL, content_class=ContentClassEnum.illustration)
    _media(db, 5, phase37.SOURCE_LABEL)
    _tag(db, 1, "eligible_tag")
    _tag(db, 2, "ineligible_tag")
    _ai_assoc(db, 1, 1)
    _ai_assoc(db, 3, 2)
    _ai_assoc(db, 4, 2)
    db.add(
        TagTranslation(
            tag_id=2,
            canonical_name="ineligible_tag",
            language="zh-CN",
            display_name="translated",
            category="general",
            source="llm",
            status="translated",
        )
    )
    db.commit()

    audit = phase37.audit_tag_scope(db, phase37.SOURCE_LABEL)

    assert audit["eligible_media_count"] == 2
    assert audit["ineligible_media_count"] == 3
    assert audit["ai_tags"]["ineligible_associations"] == 2
    assert audit["localization"]["translated_tag_names_attached_to_ineligible_media"] == 1
    assert audit["future_gate"]["ai_tagging_allowed_classes"] == ["anime", "unknown"]


def test_public_sanitizer_redacts_paths_and_secrets():
    text = (
        "C:\\Users\\person\\secret E:\\VioletPilotData_1000 "
        "/mnt/e/private /Volumes/private /workspace/project "
        "Bearer abc.def sk-secretsecret"
    )
    safe = phase37.sanitize_public_text(text)
    assert "C:\\" not in safe
    assert "E:\\" not in safe
    assert "/mnt/" not in safe
    assert "/Volumes/" not in safe
    assert "/workspace/" not in safe
    assert "abc.def" not in safe
    assert "secretsecret" not in safe


def test_cli_rejects_non_positive_limits():
    parser = phase37.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["classify", "--confirm-phase37", phase37.CONFIRM_PHRASE, "--db-backup-file", "x", "--limit", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["classify", "--confirm-phase37", phase37.CONFIRM_PHRASE, "--db-backup-file", "x", "--chunk-size", "-1"])
