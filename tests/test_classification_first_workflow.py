"""Tests for Phase 3.8b classification-first workflow helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

SCRIPT_PATH = ROOT / "scripts" / "plan_classification_first_e2e.py"
SPEC = importlib.util.spec_from_file_location("plan_classification_first_e2e", SCRIPT_PATH)
cli = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(cli)

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
from app.services import classification_first_workflow as workflow  # noqa: E402


SOURCE_LABEL = workflow.DEFAULT_SOURCE_LABEL


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
        "DB_NAME": "blombooru",
        "DB_USER": "postgres",
        "DB_HOST": "localhost",
        "DB_PORT": 5432,
        "STORAGE_ROOT_EXPLICITLY_SET": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _media(db, media_id: int, *, content_class=None, source: str = SOURCE_LABEL):
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


def _tag(db, tag_id: int, name: str, category=TagCategoryEnum.general, post_count: int = 1):
    item = Tag(id=tag_id, name=name, category=category, post_count=post_count)
    db.add(item)
    db.commit()
    return item


def _assoc(db, media_id: int, tag_id: int, *, source: str = workflow.AI_SOURCE, suggestion: bool = False):
    db.execute(
        blombooru_media_tags.insert().values(
            media_id=media_id,
            tag_id=tag_id,
            source=source,
            confidence=0.9,
            is_locked=False,
            is_suggestion=suggestion,
        )
    )
    db.commit()


def _seed_scope_fixture(db):
    _media(db, 1, content_class=ContentClassEnum.anime)
    _media(db, 2, content_class=ContentClassEnum.unknown)
    _media(db, 3, content_class=ContentClassEnum.non_anime)
    _media(db, 4, content_class=ContentClassEnum.illustration)
    _media(db, 5, content_class=None)
    _media(db, 6, content_class=ContentClassEnum.anime, source="other-source")

    _tag(db, 1, "eligible_general", TagCategoryEnum.general, post_count=10)
    _tag(db, 2, "eligible_meta", TagCategoryEnum.meta, post_count=8)
    _tag(db, 3, "eligible_character", TagCategoryEnum.character, post_count=7)
    _tag(db, 4, "ineligible_only", TagCategoryEnum.general, post_count=6)
    _tag(db, 5, "already_translated", TagCategoryEnum.general, post_count=5)

    _assoc(db, 1, 1)
    _assoc(db, 1, 2, suggestion=True)
    _assoc(db, 2, 3)
    _assoc(db, 3, 4)
    _assoc(db, 4, 4)
    _assoc(db, 1, 5)

    db.add(
        TagTranslation(
            tag_id=5,
            canonical_name="already_translated",
            language="zh-CN",
            display_name="translated",
            category="general",
            source="manual",
            status="translated",
        )
    )
    db.commit()


class TestEligibleScopeHelper:
    def test_content_class_policy(self):
        assert workflow.is_eligible_content_class(ContentClassEnum.anime)
        assert workflow.is_eligible_content_class(ContentClassEnum.unknown)
        assert not workflow.is_eligible_content_class(ContentClassEnum.non_anime)
        assert not workflow.is_eligible_content_class(ContentClassEnum.illustration)
        assert not workflow.is_eligible_content_class("unclassified")
        assert not workflow.is_eligible_content_class("failed")
        assert not workflow.is_eligible_content_class(None)
        assert workflow.is_eligible_content_class(None, null_policy=workflow.NULL_POLICY_TREAT_AS_UNKNOWN)

    def test_partition_counts_null_is_explicit(self):
        result = workflow.partition_content_class_counts(
            {
                "anime": 2,
                "unknown": 1,
                "non_anime": 3,
                "illustration": 4,
                "unclassified": 5,
            }
        )
        assert result == {"eligible": 3, "ineligible": 12, "null_content_class": 5}


class TestAITaggingScopePolicy:
    def test_refuses_ineligible_media_ids(self, db):
        _media(db, 1, content_class=ContentClassEnum.anime)
        _media(db, 2, content_class=ContentClassEnum.unknown)
        _media(db, 3, content_class=ContentClassEnum.non_anime)
        _media(db, 4)

        with pytest.raises(workflow.WorkflowContractError) as exc:
            workflow.assert_ai_scope_media_ids_are_eligible(db, [1, 2, 3, 4], source_label=SOURCE_LABEL)

        message = str(exc.value)
        assert "non_anime" in message
        assert "unclassified" in message
        assert "C:\\" not in message

    def test_accepts_only_eligible_media_ids(self, db):
        _media(db, 1, content_class=ContentClassEnum.anime)
        _media(db, 2, content_class=ContentClassEnum.unknown)

        result = workflow.assert_ai_scope_media_ids_are_eligible(db, [1, 2], source_label=SOURCE_LABEL)

        assert result["checked"] == 2
        assert result["eligible_ids"] == [1, 2]
        assert result["ineligible_ids"] == []


class TestLocalizationScopePolicy:
    def test_candidates_only_from_eligible_media_and_general_meta(self, db, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())

        candidates = workflow.select_eligible_localization_candidates(db, SOURCE_LABEL)

        assert [item["canonical_name"] for item in candidates] == [
            "eligible_general",
            "eligible_meta",
        ]
        assert all(item["category"] in {"general", "meta"} for item in candidates)
        assert "ineligible_only" not in {item["canonical_name"] for item in candidates}
        assert "eligible_character" not in {item["canonical_name"] for item in candidates}
        assert "already_translated" not in {item["canonical_name"] for item in candidates}


class TestLegacyContaminationAudit:
    def test_ineligible_ai_associations_are_counted_not_cleaned(self, db, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())
        before = workflow.collect_mutation_snapshot(db)

        audit = workflow.collect_scope_audit(db, workflow.WorkflowScope(source_label=SOURCE_LABEL))
        after = workflow.collect_mutation_snapshot(db)

        assert audit.eligible_media_count == 2
        assert audit.ineligible_media_count == 3
        assert audit.legacy_contamination["ineligible_media_with_ai_tags"] == 2
        assert audit.legacy_contamination["ineligible_ai_associations"] == 2
        assert audit.legacy_contamination["cleanup_performed"] is False
        assert before == after


class TestDryRunReport:
    def test_report_schema_stage_contracts_no_mutation_and_privacy(self, db, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())
        before = workflow.collect_mutation_snapshot(db)
        scope = workflow.WorkflowScope(
            source_label=SOURCE_LABEL,
            expected_current_media_count=5,
            expected_eligible_count=2,
            expected_ineligible_count=3,
            strict=True,
        )

        report = workflow.build_dry_run_report(db, scope, repo_root=ROOT, settings=_settings(), before_snapshot=before)
        after = workflow.collect_mutation_snapshot(db)

        assert report["success"] is True
        assert len(report["stage_contracts"]) == 11
        assert report["counts"]["eligible_media_count"] == 2
        assert report["counts"]["ineligible_media_count"] == 3
        assert report["legacy_contamination"]["status"] == "legacy_validation_artifact"
        assert report["mutation_safety"]["passed"] is True
        assert before == after
        assert workflow.find_privacy_leaks(report) == []

    def test_strict_count_mismatch_fails(self, db, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())
        scope = workflow.WorkflowScope(
            source_label=SOURCE_LABEL,
            expected_current_media_count=999,
            expected_eligible_count=2,
            expected_ineligible_count=3,
            strict=True,
        )

        report = workflow.build_dry_run_report(db, scope, repo_root=ROOT, settings=_settings())

        assert report["success"] is False
        assert any("expected_current_media_count=999" in item for item in report["contract_failures"])


class TestPrivacyHelpers:
    def test_sanitizer_redacts_paths_and_secrets(self):
        payload = {
            "path": "C:\\Users\\someone\\AnimeLocalBooru E:\\VioletPilotData_1000 /workspace/project",
            "token": "Bearer abc.def sk-secretsecret",
            "url": "postgresql://postgres:password@localhost:5432/blombooru",
        }
        safe = workflow.sanitize_public_obj(payload)
        text = json.dumps(safe)
        assert "C:\\" not in text
        assert "E:\\" not in text
        assert "/workspace/" not in text
        assert "abc.def" not in text
        assert "secretsecret" not in text
        assert "password@" not in text
        assert workflow.find_privacy_leaks(safe) == []


class TestDryRunCli:
    def test_cli_dry_run_writes_reports(self, db, tmp_path, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())
        out = StringIO()
        err = StringIO()
        report_json = tmp_path / "summary.json"
        report_md = tmp_path / "summary.md"

        code = cli.main(
            [
                "--dry-run",
                "--source-label",
                SOURCE_LABEL,
                "--expected-current-media-count",
                "5",
                "--expected-eligible-count",
                "2",
                "--expected-ineligible-count",
                "3",
                "--report-json",
                str(report_json),
                "--report-md",
                str(report_md),
                "--strict",
            ],
            session_factory=lambda: db,
            settings_obj=_settings(),
            repo_root=ROOT,
            out=out,
            err=err,
        )

        assert code == 0
        assert report_json.exists()
        assert report_md.exists()
        payload = json.loads(report_json.read_text(encoding="utf-8"))
        assert payload["counts"]["eligible_media_count"] == 2
        assert payload["mutation_safety"]["passed"] is True
        assert "target=5 eligible=2 ineligible=3" in out.getvalue()
        assert err.getvalue() == ""

    def test_cli_execute_is_rejected(self, tmp_path):
        out = StringIO()
        err = StringIO()

        code = cli.main(
            ["--execute", "--report-json", str(tmp_path / "x.json"), "--report-md", str(tmp_path / "x.md")],
            session_factory=lambda: None,
            settings_obj=_settings(),
            repo_root=ROOT,
            out=out,
            err=err,
        )

        assert code == 2
        assert cli.EXECUTE_REJECTION in err.getvalue()
        assert not (tmp_path / "x.json").exists()

    def test_cli_strict_count_mismatch_fails(self, db, tmp_path, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())
        report_json = tmp_path / "summary.json"
        report_md = tmp_path / "summary.md"

        code = cli.main(
            [
                "--dry-run",
                "--source-label",
                SOURCE_LABEL,
                "--expected-current-media-count",
                "999",
                "--expected-eligible-count",
                "2",
                "--expected-ineligible-count",
                "3",
                "--report-json",
                str(report_json),
                "--report-md",
                str(report_md),
                "--strict",
            ],
            session_factory=lambda: db,
            settings_obj=_settings(),
            repo_root=ROOT,
            out=StringIO(),
            err=StringIO(),
        )

        assert code == 1
        payload = json.loads(report_json.read_text(encoding="utf-8"))
        assert any("expected_current_media_count=999" in item for item in payload["contract_failures"])
