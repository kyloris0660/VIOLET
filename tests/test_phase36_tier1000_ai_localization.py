"""Tests for the Phase 3.6 Tier-1000 controlled AI/localization runner.

These tests use an in-memory SQLite database and do not run the AI model, LLM,
classification, Entity Resolver, or any real import/copy path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

SCRIPT_PATH = ROOT / "scripts" / "run_phase36_tier1000_ai_localization.py"
SPEC = importlib.util.spec_from_file_location("phase36_runner", SCRIPT_PATH)
phase36 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(phase36)

from app.database import Base  # noqa: E402
from app.enums import FileTypeEnum, TagCategoryEnum  # noqa: E402
from app.models import AITagJob, Media, Tag, TagTranslation, TagTranslationJob, blombooru_media_tags  # noqa: E402


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


def _media(db, media_id: int, source: str):
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
    )
    db.add(item)
    db.commit()
    return item


def _tag(db, tag_id: int, name: str, category: TagCategoryEnum, post_count: int = 1):
    item = Tag(id=tag_id, name=name, category=category, post_count=post_count)
    db.add(item)
    db.commit()
    return item


def _ai_assoc(db, media_id: int, tag_id: int, *, suggestion: bool = False):
    db.execute(
        blombooru_media_tags.insert().values(
            media_id=media_id,
            tag_id=tag_id,
            source=phase36.AI_SOURCE,
            confidence=0.9,
            is_locked=False,
            is_suggestion=suggestion,
        )
    )
    db.commit()


def _phase36_settings(**overrides):
    values = {
        "DB_NAME": "blombooru",
        "IS_TEST_ENV": False,
        "CONTENT_CLASSIFICATION_ENABLED": False,
        "CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT": False,
        "ENTITY_ALIAS_RESOLVER_ENABLED": False,
        "TAG_TRANSLATION_BG_ENABLED": False,
        "TAG_TRANSLATION_AUTO_ENABLED": False,
        "AI_TAGGING_AUTO_LOCALIZATION": False,
        "VIOLET_ENV": "development",
        "DB_USER": "postgres",
        "DB_HOST": "localhost",
        "DB_PORT": 5432,
        "AI_TAGGING_ENABLED": True,
        "TAG_TRANSLATION_LLM_ENABLED": True,
        "AI_TAGGING_BATCH_MAX_ITEMS": 10,
        "AI_MODEL_NAME": "wd-swinv2-tagger-v3",
        "AI_GENERAL_THRESHOLD": 0.35,
        "AI_CHARACTER_THRESHOLD": 0.65,
        "AI_RATING_THRESHOLD": 0.5,
        "AI_SUGGESTION_THRESHOLD": 0.2,
        "TAG_TRANSLATION_BATCH_MAX_ITEMS": 2,
        "TAG_TRANSLATION_LLM_PROVIDER": "openai_compatible",
        "TAG_TRANSLATION_LLM_BASE_URL": "https://llm.example.invalid/v1",
        "TAG_TRANSLATION_LLM_MODEL": "model-a",
        "TAG_TRANSLATION_LLM_API_KEY": "configured",
        "TAG_TRANSLATION_LLM_FALLBACK_BASE_URL": "",
        "TAG_TRANSLATION_LLM_FALLBACK_MODEL": "",
        "TAG_TRANSLATION_LLM_FALLBACK_API_KEY": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_select_target_media_ids_scoped_to_phase35_source(db):
    _media(db, 1, phase36.SOURCE_LABEL)
    _media(db, 2, phase36.SOURCE_LABEL)
    _media(db, 3, "other-source")
    _tag(db, 1, "already_ai_tagged", TagCategoryEnum.general)
    _ai_assoc(db, 1, 1)

    assert phase36.select_target_media_ids(db, phase36.SOURCE_LABEL) == [2]
    assert phase36.select_target_media_ids(
        db, phase36.SOURCE_LABEL, only_without_ai_tags=False
    ) == [1, 2]


def test_localization_candidates_are_visual_only_and_skip_proper_nouns(db):
    _media(db, 1, phase36.SOURCE_LABEL)
    _tag(db, 1, "phase36_general_tag", TagCategoryEnum.general, post_count=5)
    _tag(db, 2, "phase36_meta_tag", TagCategoryEnum.meta, post_count=4)
    _tag(db, 3, "phase36_character_name", TagCategoryEnum.character, post_count=3)
    _tag(db, 4, "phase36_copyright_name", TagCategoryEnum.copyright, post_count=2)
    _tag(db, 5, "phase36_already_translated", TagCategoryEnum.general, post_count=1)
    for tag_id in [1, 2, 3, 4, 5]:
        _ai_assoc(db, 1, tag_id)
    db.add(
        TagTranslation(
            tag_id=5,
            canonical_name="phase36_already_translated",
            language="zh-CN",
            display_name="已翻译",
            category="general",
            source="manual",
            status="translated",
        )
    )
    db.commit()

    visual = phase36.select_localization_candidates(
        db, phase36.SOURCE_LABEL, phase36.LOCALIZABLE_CATEGORIES, limit=None
    )
    proper = phase36.select_localization_candidates(
        db, phase36.SOURCE_LABEL, phase36.PROPER_NOUN_CATEGORIES, limit=None
    )

    assert [item["canonical_name"] for item in visual] == [
        "phase36_general_tag",
        "phase36_meta_tag",
    ]
    assert {item["canonical_name"] for item in proper} == {
        "phase36_character_name",
        "phase36_copyright_name",
    }


def test_effective_localization_limit_uses_configured_batch_cap():
    assert phase36.effective_localization_limit(10, 2) == 2
    assert phase36.effective_localization_limit(2, 10) == 2
    with pytest.raises(RuntimeError, match="--max-items"):
        phase36.effective_localization_limit(0, 10)
    with pytest.raises(RuntimeError, match="TAG_TRANSLATION_BATCH_MAX_ITEMS"):
        phase36.effective_localization_limit(10, 0)


def test_localization_provider_receives_no_more_than_batch_cap(db, tmp_path, monkeypatch):
    _media(db, 1, phase36.SOURCE_LABEL)
    for tag_id in range(1, 5):
        _tag(db, tag_id, f"phase36_batch_cap_tag_{tag_id}", TagCategoryEnum.general, post_count=10 - tag_id)
        _ai_assoc(db, 1, tag_id)

    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"not-empty")
    report_json = tmp_path / "localization-report.json"
    settings = _phase36_settings(TAG_TRANSLATION_BATCH_MAX_ITEMS=2)

    class FakeProvider:
        def __init__(self):
            self.received = []

        def is_available(self):
            return True

        def get_provider_name(self):
            return "fake"

        async def translate_tags(self, tag_inputs):
            self.received = list(tag_inputs)
            return [
                SimpleNamespace(
                    canonical_name=item["name"],
                    display_name_zh=f"translated {item['name']}",
                    aliases_zh=[],
                    confidence=1.0,
                    needs_review=False,
                )
                for item in tag_inputs
            ]

    provider = FakeProvider()
    monkeypatch.setattr(
        phase36,
        "load_app_context",
        lambda: {"settings": settings, "database": SimpleNamespace(SessionLocal=lambda: db)},
    )
    import app.services.llm_translation_provider as provider_mod

    monkeypatch.setattr(provider_mod, "get_llm_provider", lambda: provider)

    payload = phase36.run_controlled_localization(
        SimpleNamespace(
            source_label=phase36.SOURCE_LABEL,
            expected_media_count=1,
            confirm_phase36=phase36.CONFIRM_PHRASE,
            db_backup_file=str(backup),
            report_json=str(report_json),
            lang="zh-CN",
            max_items=10,
        )
    )

    assert len(provider.received) == 2
    assert payload["requested_max_items"] == 10
    assert payload["effective_max_items"] == 2
    assert payload["configured_batch_max"] == 2
    assert payload["candidates_selected"] == 2
    assert json.loads(report_json.read_text(encoding="utf-8"))["candidates_selected"] == 2


def test_localization_respects_requested_limit_when_below_batch_cap(db, tmp_path, monkeypatch):
    _media(db, 1, phase36.SOURCE_LABEL)
    for tag_id in range(1, 4):
        _tag(db, tag_id, f"phase36_requested_limit_tag_{tag_id}", TagCategoryEnum.general)
        _ai_assoc(db, 1, tag_id)

    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"not-empty")
    settings = _phase36_settings(TAG_TRANSLATION_BATCH_MAX_ITEMS=10)

    class FakeProvider:
        def __init__(self):
            self.received = []

        def is_available(self):
            return True

        def get_provider_name(self):
            return "fake"

        async def translate_tags(self, tag_inputs):
            self.received = list(tag_inputs)
            return [
                SimpleNamespace(
                    canonical_name=item["name"],
                    display_name_zh=f"translated {item['name']}",
                    aliases_zh=[],
                    confidence=1.0,
                    needs_review=False,
                )
                for item in tag_inputs
            ]

    provider = FakeProvider()
    monkeypatch.setattr(
        phase36,
        "load_app_context",
        lambda: {"settings": settings, "database": SimpleNamespace(SessionLocal=lambda: db)},
    )
    import app.services.llm_translation_provider as provider_mod

    monkeypatch.setattr(provider_mod, "get_llm_provider", lambda: provider)

    payload = phase36.run_controlled_localization(
        SimpleNamespace(
            source_label=phase36.SOURCE_LABEL,
            expected_media_count=1,
            confirm_phase36=phase36.CONFIRM_PHRASE,
            db_backup_file=str(backup),
            report_json=str(tmp_path / "report.json"),
            lang="zh-CN",
            max_items=1,
        )
    )

    assert len(provider.received) == 1
    assert payload["requested_max_items"] == 1
    assert payload["effective_max_items"] == 1
    assert payload["configured_batch_max"] == 10
    assert payload["candidates_selected"] == 1


def test_metric_delta_reports_distinct_ai_and_translation_changes(db):
    before = {
        "target_media_with_ai_tags": 10,
        "target_ai_confirmed_associations": 20,
        "target_ai_suggestion_associations": 5,
        "target_ai_associations": 25,
        "total_tag_count": 100,
        "translation_count": 40,
        "classification_jobs": 0,
        "translation_jobs": 1,
    }
    after = {
        "target_media_with_ai_tags": 12,
        "target_ai_confirmed_associations": 23,
        "target_ai_suggestion_associations": 7,
        "target_ai_associations": 30,
        "total_tag_count": 104,
        "translation_count": 43,
        "classification_jobs": 0,
        "translation_jobs": 2,
    }

    assert phase36.metric_delta(before, after) == {
        "target_media_with_ai_tags": 2,
        "target_ai_confirmed_associations": 3,
        "target_ai_suggestion_associations": 2,
        "target_ai_associations": 5,
        "total_tag_count": 4,
        "translation_count": 3,
        "classification_jobs": 0,
        "translation_jobs": 1,
    }


def test_public_sanitizer_redacts_paths_and_secrets():
    text = (
        "C:\\Users\\person\\repo\\file.jpg E:\\VioletPilotData_1000\\x.jpg "
        "/mnt/e/VioletPilotData_1000/x.jpg /Volumes/Data/x.jpg "
        "/workspace/project/file.jpg Bearer abc.def.ghi sk-secret123456"
    )
    sanitized = phase36.sanitize_public_text(text)

    assert "C:\\" not in sanitized
    assert "E:\\" not in sanitized
    assert "/mnt/" not in sanitized
    assert "/Volumes/" not in sanitized
    assert "/workspace/" not in sanitized
    assert "abc.def.ghi" not in sanitized
    assert "sk-secret123456" not in sanitized


def test_llm_config_summary_reports_presence_not_values(monkeypatch):
    settings = SimpleNamespace(
        TAG_TRANSLATION_LLM_ENABLED=True,
        TAG_TRANSLATION_LLM_PROVIDER="openai_compatible",
        TAG_TRANSLATION_LLM_BASE_URL="https://llm.example.invalid/v1",
        TAG_TRANSLATION_LLM_MODEL="model-a",
        TAG_TRANSLATION_LLM_API_KEY="sk-verysecretvalue",
        TAG_TRANSLATION_LLM_FALLBACK_BASE_URL="https://fallback.example.invalid/v1",
        TAG_TRANSLATION_LLM_FALLBACK_MODEL="model-b",
        TAG_TRANSLATION_LLM_FALLBACK_API_KEY="key-anothersecret",
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid:8080")

    summary = phase36.llm_config_summary(settings)
    encoded = str(summary)

    assert summary["api_key_configured"] is True
    assert summary["fallback_api_key_configured"] is True
    assert summary["base_url_host"] == "llm.example.invalid"
    assert "sk-verysecretvalue" not in encoded
    assert "key-anothersecret" not in encoded


def test_write_gates_require_backup_and_disable_side_effect_systems(db, tmp_path):
    _media(db, 1, phase36.SOURCE_LABEL)
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"not-empty")
    safe_settings = SimpleNamespace(
        DB_NAME="blombooru",
        IS_TEST_ENV=False,
        CONTENT_CLASSIFICATION_ENABLED=False,
        CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT=False,
        ENTITY_ALIAS_RESOLVER_ENABLED=False,
        TAG_TRANSLATION_BG_ENABLED=False,
        TAG_TRANSLATION_AUTO_ENABLED=False,
        AI_TAGGING_AUTO_LOCALIZATION=False,
        VIOLET_ENV="development",
        DB_USER="postgres",
        DB_HOST="localhost",
        DB_PORT=5432,
    )

    gates = phase36.validate_common_write_gates(
        db=db,
        settings=safe_settings,
        source_label=phase36.SOURCE_LABEL,
        expected_media_count=1,
        confirm_phrase=phase36.CONFIRM_PHRASE,
        backup_file=backup,
    )
    assert gates["backup"]["file_name"] == "backup.dump"

    unsafe_settings = SimpleNamespace(**{**safe_settings.__dict__, "CONTENT_CLASSIFICATION_ENABLED": True})
    with pytest.raises(RuntimeError, match="Content classification"):
        phase36.validate_common_write_gates(
            db=db,
            settings=unsafe_settings,
            source_label=phase36.SOURCE_LABEL,
            expected_media_count=1,
            confirm_phrase=phase36.CONFIRM_PHRASE,
            backup_file=backup,
        )


def test_ai_gate_requires_llm_disabled_during_ai_tagging():
    settings = SimpleNamespace(
        AI_TAGGING_ENABLED=True,
        TAG_TRANSLATION_LLM_ENABLED=True,
        AI_TAGGING_BATCH_MAX_ITEMS=10,
        AI_MODEL_NAME="wd-swinv2-tagger-v3",
        AI_GENERAL_THRESHOLD=0.35,
        AI_CHARACTER_THRESHOLD=0.65,
        AI_RATING_THRESHOLD=0.5,
        AI_SUGGESTION_THRESHOLD=0.2,
    )

    with pytest.raises(RuntimeError, match="TAG_TRANSLATION_LLM_ENABLED"):
        phase36.validate_ai_gates(settings)


def test_db_backed_active_ai_jobs_block_new_ai_chunks(db):
    blocking = [
        AITagJob(status="pending", trigger_source="manual"),
        AITagJob(status="running", trigger_source="phase3.6"),
        AITagJob(status="cancelling", trigger_source="manual"),
    ]
    historical = [
        AITagJob(status="completed", trigger_source="phase3.6"),
        AITagJob(status="failed", trigger_source="phase3.6"),
        AITagJob(status="cancelled", trigger_source="phase3.6"),
    ]
    db.add_all(blocking + historical)
    db.commit()

    active = phase36.find_active_ai_jobs(db)
    assert [job["status"] for job in active] == ["pending", "running", "cancelling"]
    with pytest.raises(RuntimeError, match="Active AI tagging jobs"):
        phase36.ensure_no_db_active_ai_jobs(db)

    for job in blocking:
        job.status = "completed"
    db.commit()
    assert phase36.find_active_ai_jobs(db) == []
    phase36.ensure_no_db_active_ai_jobs(db)


def test_db_active_ai_job_summary_is_sanitized(db):
    db.add(AITagJob(status="pending", trigger_source="C:\\Users\\person\\secret"))
    db.commit()

    with pytest.raises(RuntimeError) as exc_info:
        phase36.ensure_no_db_active_ai_jobs(db)

    message = str(exc_info.value)
    assert "C:\\" not in message
    assert "secret" not in message


def test_ai_chunk_failure_writes_report_before_abort(db, tmp_path, monkeypatch):
    _media(db, 1, phase36.SOURCE_LABEL)
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"not-empty")
    report_json = tmp_path / "ai-failure-report.json"
    settings = _phase36_settings(
        TAG_TRANSLATION_LLM_ENABLED=False,
        AI_TAGGING_BATCH_MAX_ITEMS=5,
        TAG_TRANSLATION_BATCH_MAX_ITEMS=5,
    )

    monkeypatch.setattr(
        phase36,
        "load_app_context",
        lambda: {"settings": settings, "database": SimpleNamespace(SessionLocal=lambda: db)},
    )

    import app.services.ai_tagging_job_service as job_service
    import app.services.ai_tagging_service as ai_service

    def fake_create_ai_tag_job(db_arg, **_kwargs):
        job = AITagJob(
            status="failed",
            trigger_source="phase3.6",
            processed=1,
            failed=1,
            tags_added=2,
            suggestions_added=3,
            skipped_locked=0,
            ignored_low_confidence=4,
            error_message="model failure at C:\\Users\\person\\secret\\model.onnx",
        )
        db_arg.add(job)
        db_arg.commit()
        db_arg.refresh(job)
        return job

    monkeypatch.setattr(job_service, "create_ai_tag_job", fake_create_ai_tag_job)
    monkeypatch.setattr(job_service, "is_ai_job_active", lambda: False)
    monkeypatch.setattr(job_service, "run_ai_tag_job", lambda _job_id: None)
    monkeypatch.setattr(
        ai_service,
        "check_model_status",
        lambda: {"enabled": True, "available": True, "error": None},
    )

    with pytest.raises(phase36.Phase36RunFailed):
        phase36.run_ai_tagging_controlled(
            SimpleNamespace(
                source_label=phase36.SOURCE_LABEL,
                expected_media_count=1,
                confirm_phase36=phase36.CONFIRM_PHRASE,
                db_backup_file=str(backup),
                report_json=str(report_json),
                lang="zh-CN",
                limit=None,
                chunk_size=5,
            )
        )

    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert payload["success"] is False
    assert payload["status"] == "failed_ai_chunk"
    assert payload["ai_tagging"]["failed_job"]["status"] == "failed"
    assert payload["ai_tagging"]["failed_job"]["processed"] == 1
    assert payload["ai_tagging"]["failed_job"]["failed"] == 1
    assert payload["ai_tagging"]["failed_job"]["tags_added"] == 2
    assert payload["ai_tagging"]["failed_job"]["suggestions_added"] == 3
    assert payload["ai_tagging"]["failed_job"]["chunk_index"] == 1
    assert payload["ai_tagging"]["failed_job"]["requested_media_count"] == 1
    assert "C:\\" not in json.dumps(payload, ensure_ascii=False)


def test_forbidden_side_effect_job_delta_fails_phase_isolation():
    payload = {
        "safety": {
            "content_classification_jobs_delta": 1,
            "translation_jobs_delta_during_ai": 0,
        },
        "errors": [],
    }

    with pytest.raises(phase36.Phase36RunFailed):
        phase36.assert_no_forbidden_ai_side_effects(payload)

    assert payload["success"] is False
    assert payload["status"] == "failed_phase_isolation_violation"
    assert payload["safety"]["phase_isolation_passed"] is False


def test_translation_side_effect_job_delta_fails_phase_isolation():
    payload = {
        "safety": {
            "content_classification_jobs_delta": 0,
            "translation_jobs_delta_during_ai": 1,
        },
        "errors": [],
    }

    with pytest.raises(phase36.Phase36RunFailed):
        phase36.assert_no_forbidden_ai_side_effects(payload)


def test_zero_side_effect_job_delta_passes_phase_isolation():
    payload = {
        "safety": {
            "content_classification_jobs_delta": 0,
            "translation_jobs_delta_during_ai": 0,
        },
        "errors": [],
    }

    phase36.assert_no_forbidden_ai_side_effects(payload)
    assert payload["safety"].get("phase_isolation_passed") is not False


def test_main_exits_nonzero_for_failed_localization_payload(monkeypatch, capsys):
    def fake_localize(_args):
        return {
            "mode": "controlled_localization_execute",
            "success": False,
            "status": "failed_provider_unavailable",
            "candidates": 3,
            "failed": 3,
            "errors": ["LLM provider not available or not configured"],
        }

    monkeypatch.setattr(phase36, "run_controlled_localization", fake_localize)

    code = phase36.main(
        [
            "localize",
            "--confirm-phase36",
            phase36.CONFIRM_PHRASE,
            "--db-backup-file",
            "backup.dump",
            "--report-json",
            "report.json",
        ]
    )

    assert code == 1
    assert "failed_provider_unavailable" in capsys.readouterr().out


def test_main_exits_nonzero_for_failed_localization_candidates(monkeypatch):
    def fake_localize(_args):
        return {
            "mode": "controlled_localization_execute",
            "success": False,
            "status": "failed_partial",
            "candidates": 3,
            "translated": 2,
            "failed": 1,
            "errors": ["one candidate failed"],
        }

    monkeypatch.setattr(phase36, "run_controlled_localization", fake_localize)

    assert (
        phase36.main(
            [
                "localize",
                "--confirm-phase36",
                phase36.CONFIRM_PHRASE,
                "--db-backup-file",
                "backup.dump",
                "--report-json",
                "report.json",
            ]
        )
        == 1
    )


def test_main_allows_localization_noop_success(monkeypatch):
    def fake_localize(_args):
        return {
            "mode": "controlled_localization_execute",
            "success": True,
            "status": "noop_no_candidates",
            "candidates": 0,
            "failed": 0,
            "errors": [],
        }

    monkeypatch.setattr(phase36, "run_controlled_localization", fake_localize)

    assert (
        phase36.main(
            [
                "localize",
                "--confirm-phase36",
                phase36.CONFIRM_PHRASE,
                "--db-backup-file",
                "backup.dump",
                "--report-json",
                "report.json",
            ]
        )
        == 0
    )


def test_translation_job_marked_failed_after_save_or_finalize_error(db):
    job = TagTranslationJob(
        status="running",
        source="phase3.6",
        language="zh-CN",
        category="general,meta",
        processed=0,
        translated=0,
        failed=0,
        skipped=0,
        remaining_before=2,
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    failed = phase36.mark_translation_job_failed(
        db,
        job,
        error="save failed at C:\\Users\\person\\secret\\file.jpg",
        total_candidates=2,
        processed=1,
    )

    assert failed.status == "failed"
    assert failed.processed == 1
    assert failed.failed == 1
    assert failed.finished_at is not None
    assert "C:\\" not in (failed.last_error or "")


def test_parser_rejects_non_positive_localize_max_items():
    parser = phase36.build_parser()
    base = [
        "localize",
        "--confirm-phase36",
        phase36.CONFIRM_PHRASE,
        "--db-backup-file",
        "backup.dump",
        "--report-json",
        "report.json",
    ]

    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--max-items", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--max-items", "-1"])
    assert parser.parse_args([*base, "--max-items", "1"]).max_items == 1


def test_parser_rejects_non_positive_ai_limit():
    parser = phase36.build_parser()
    base = [
        "ai-tag",
        "--confirm-phase36",
        phase36.CONFIRM_PHRASE,
        "--db-backup-file",
        "backup.dump",
        "--report-json",
        "report.json",
    ]

    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--limit", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--limit", "-1"])
    assert parser.parse_args([*base, "--limit", "1"]).limit == 1


def test_chunked_never_emits_empty_chunks():
    assert phase36.chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert phase36.chunked([], 2) == []
    with pytest.raises(ValueError, match="chunk_size"):
        phase36.chunked([1], 0)
