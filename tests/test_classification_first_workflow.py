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

    def test_partition_counts_treat_null_as_unknown(self):
        result = workflow.partition_content_class_counts(
            {
                "anime": 2,
                "unknown": 1,
                "non_anime": 3,
                "illustration": 4,
                "unclassified": 5,
            },
            null_policy=workflow.NULL_POLICY_TREAT_AS_UNKNOWN,
        )
        assert result == {"eligible": 8, "ineligible": 7, "null_content_class": 5}

    def test_partition_counts_unknown_content_class_buckets_as_ineligible(self):
        result = workflow.partition_content_class_counts(
            {
                "anime": 2,
                "unknown": 1,
                "cosplay": 3,
                "failed": 4,
                "unclassified": 5,
            }
        )
        assert result == {"eligible": 3, "ineligible": 12, "null_content_class": 5}

    def test_collect_scope_audit_preserves_extra_distribution_buckets(self, db, monkeypatch):
        monkeypatch.setattr(
            workflow,
            "_content_class_distribution",
            lambda _db, _source: {
                "anime": 2,
                "unknown": 1,
                "illustration": 0,
                "non_anime": 1,
                "unclassified": 0,
                "cosplay": 2,
            },
        )

        audit = workflow.collect_scope_audit(db, workflow.WorkflowScope(source_label=SOURCE_LABEL))

        assert audit.target_media_count == 6
        assert audit.eligible_media_count == 3
        assert audit.ineligible_media_count == 3
        assert audit.content_class_distribution["cosplay"] == 2


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

    def test_sql_and_python_null_policy_treat_as_unknown(self, db):
        _media(db, 1, content_class=ContentClassEnum.anime)
        _media(db, 2, content_class=ContentClassEnum.unknown)
        _media(db, 3, content_class=None)
        _media(db, 4, content_class=ContentClassEnum.non_anime)

        assert workflow.select_eligible_media_ids(db, SOURCE_LABEL) == [1, 2]
        assert workflow.select_eligible_media_ids(
            db,
            SOURCE_LABEL,
            null_policy=workflow.NULL_POLICY_TREAT_AS_UNKNOWN,
        ) == [1, 2, 3]

        with pytest.raises(workflow.WorkflowContractError):
            workflow.assert_ai_scope_media_ids_are_eligible(db, [1, 2, 3], source_label=SOURCE_LABEL)

        result = workflow.assert_ai_scope_media_ids_are_eligible(
            db,
            [1, 2, 3],
            source_label=SOURCE_LABEL,
            null_policy=workflow.NULL_POLICY_TREAT_AS_UNKNOWN,
        )
        assert result["eligible_ids"] == [1, 2, 3]


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

    def test_null_media_candidates_only_when_policy_treats_null_as_unknown(self, db, monkeypatch):
        _media(db, 1, content_class=ContentClassEnum.anime)
        _media(db, 2, content_class=None)
        _tag(db, 1, "anime_general", TagCategoryEnum.general)
        _tag(db, 2, "null_general", TagCategoryEnum.general)
        _assoc(db, 1, 1)
        _assoc(db, 2, 2)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())

        hard_fail_names = {
            item["canonical_name"]
            for item in workflow.select_eligible_localization_candidates(db, SOURCE_LABEL)
        }
        treat_names = {
            item["canonical_name"]
            for item in workflow.select_eligible_localization_candidates(
                db,
                SOURCE_LABEL,
                null_policy=workflow.NULL_POLICY_TREAT_AS_UNKNOWN,
            )
        }

        assert hard_fail_names == {"anime_general"}
        assert treat_names == {"anime_general", "null_general"}


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

    def test_treat_null_as_unknown_inverts_scope_counts(self, db, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())

        hard_fail = workflow.collect_scope_audit(db, workflow.WorkflowScope(source_label=SOURCE_LABEL))
        treat_as_unknown = workflow.collect_scope_audit(
            db,
            workflow.WorkflowScope(
                source_label=SOURCE_LABEL,
                null_content_class_policy=workflow.NULL_POLICY_TREAT_AS_UNKNOWN,
            ),
        )

        assert hard_fail.eligible_media_count == 2
        assert hard_fail.ineligible_media_count == 3
        assert hard_fail.null_content_class_count == 1
        assert treat_as_unknown.eligible_media_count == 3
        assert treat_as_unknown.ineligible_media_count == 2
        assert treat_as_unknown.null_content_class_count == 1


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
        assert report["public_report_text_policy"]["raw_arbitrary_error_warning_text_allowed"] is False
        unsafe_text_policy = report["public_report_text_policy"]["unsafe_raw_text_representation"]
        assert unsafe_text_policy == {
            "raw_text_redacted": True,
            "redaction_reason": workflow.REDACTION_REASON_LOCAL_PATH_OR_SECRET,
            "local_artifact_available": False,
            "local_artifact_label": None,
        }
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

    def test_non_strict_count_mismatch_warns_without_failure(self, db, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())
        scope = workflow.WorkflowScope(
            source_label=SOURCE_LABEL,
            expected_current_media_count=999,
            expected_eligible_count=999,
            expected_ineligible_count=999,
            strict=False,
        )

        report = workflow.build_dry_run_report(db, scope, repo_root=ROOT, settings=_settings())

        assert report["success"] is True
        assert report["status"] == "passed"
        assert report["contract_failures"] == []
        assert any("expected_current_media_count=999" in item for item in report["warnings"])

    def test_strict_expected_no_null_scope_fails_when_null_is_ineligible(self, db, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())
        scope = workflow.WorkflowScope(
            source_label=SOURCE_LABEL,
            expected_current_media_count=5,
            expected_eligible_count=2,
            expected_ineligible_count=2,
            strict=True,
        )

        report = workflow.build_dry_run_report(db, scope, repo_root=ROOT, settings=_settings())

        assert report["success"] is False
        assert report["counts"]["null_content_class_count"] == 1
        assert any("expected_ineligible_count=2" in item for item in report["contract_failures"])

    def test_mutation_delta_fails_even_when_not_strict(self, db, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())
        before = workflow.MutationSnapshot(
            media=0,
            media_tags=0,
            ai_jobs=0,
            classification_jobs=0,
            translation_jobs=0,
        )
        scope = workflow.WorkflowScope(source_label=SOURCE_LABEL, strict=False)

        report = workflow.build_dry_run_report(db, scope, repo_root=ROOT, settings=_settings(), before_snapshot=before)

        assert report["success"] is False
        assert report["status"] == "failed_contract"
        assert any("dry-run mutation detected" in item for item in report["contract_failures"])

    def test_privacy_leak_fails_even_when_not_strict(self, db, monkeypatch):
        _seed_scope_fixture(db)
        monkeypatch.setattr(workflow, "_static_translation_names", lambda: set())
        monkeypatch.setattr(workflow, "find_privacy_leaks", lambda _: ["absolute_path"])
        scope = workflow.WorkflowScope(source_label=SOURCE_LABEL, strict=False)

        report = workflow.build_dry_run_report(db, scope, repo_root=ROOT, settings=_settings())

        assert report["success"] is False
        assert report["status"] == "failed_privacy"
        assert report["privacy"]["leaks"] == ["absolute_path"]


class TestPrivacyHelpers:
    def test_sanitizer_redacts_paths_and_secrets(self):
        payload = {
            "path": "C:\\Users\\someone\\AnimeLocalBooru E:\\VioletPilotData_1000 /workspace/project",
            "token": "Bearer abc.def sk-secretsecret key-secretsecret",
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

    def test_windows_paths_with_spaces_are_fully_redacted(self):
        raw = (
            r'failed at "C:\Users\name\iCloud Photos\foo.jpg", '
            r"then C:/Users/name/iCloud Photos/foo.jpg."
        )

        safe = workflow.sanitize_public_text(raw)

        assert "C:\\Users" not in safe
        assert "C:/Users" not in safe
        assert "iCloud Photos" not in safe
        assert r"Photos\foo.jpg" not in safe
        assert "Photos/foo.jpg" not in safe
        assert workflow.find_privacy_leaks(raw) == ["absolute_path"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_unc_paths_with_spaces_are_redacted(self):
        raw = r'copy failed from "\\nas\Shared Folder\iCloud Photos\foo.jpg".'

        safe = workflow.sanitize_public_text(raw)

        assert "\\\\nas" not in safe
        assert "Shared Folder" not in safe
        assert "iCloud Photos" not in safe
        assert workflow.find_privacy_leaks(raw) == ["absolute_path"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_file_uris_are_redacted_as_local_paths(self):
        samples = [
            "file:///Users/alice/Pictures/foo.jpg",
            "file://localhost/C:/Users/alice/iCloud Photos/foo.jpg",
            "file:///C:/Users/alice/iCloud Photos/foo.jpg",
        ]
        raw = " ".join(samples)

        safe = workflow.sanitize_public_text(raw)

        assert "file://" not in safe
        assert "/Users/alice" not in safe
        assert "C:/Users/alice" not in safe
        assert "iCloud Photos" not in safe
        assert "Pictures/foo.jpg" not in safe
        for sample in samples:
            assert workflow.find_privacy_leaks(sample) == ["absolute_path"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_windows_paths_with_apostrophes_are_fully_redacted(self):
        raw = (
            r"failed at C:\Users\O'Connor\Pictures\foo.jpg, "
            r"quoted 'C:\Users\O'Connor\Pictures\bar.jpg'"
        )

        safe = workflow.sanitize_public_text(raw)

        assert "C:\\Users" not in safe
        assert r"Connor\Pictures" not in safe
        assert r"Pictures\foo.jpg" not in safe
        assert r"Pictures\bar.jpg" not in safe
        assert workflow.find_privacy_leaks(raw) == ["absolute_path"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_posix_paths_with_apostrophes_are_fully_redacted(self):
        raw = "failed at /home/o'connor/Pictures/foo.jpg and '/Users/o'connor/Pictures/bar.jpg'"

        safe = workflow.sanitize_public_text(raw)

        assert "/home/o" not in safe
        assert "/Users/o" not in safe
        assert "connor/Pictures" not in safe
        assert "Pictures/foo.jpg" not in safe
        assert "Pictures/bar.jpg" not in safe
        assert workflow.find_privacy_leaks(raw) == ["absolute_path"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_non_ascii_posix_paths_are_fully_redacted(self):
        path = "/\u7528\u6237/\u56fe\u7247/foo.jpg"

        safe = workflow.sanitize_public_text(f"error at {path}")

        assert path not in safe
        assert "\u7528\u6237" not in safe
        assert "\u56fe\u7247" not in safe
        assert workflow.find_privacy_leaks(path) == ["absolute_path"]
        assert workflow.find_privacy_leaks(safe) == []

    @pytest.mark.parametrize(
        "path",
        [
            "/root/foo/bar.png",
            "/etc/app/config.yml",
            "/usr/local/bin/tool",
            "/private/var/folders/abc/file",
            "/Volumes/Disk With Space/foo.jpg",
            "/Users/name/Pictures/foo.jpg",
        ],
    )
    def test_generic_posix_absolute_paths_are_redacted(self, path):
        safe = workflow.sanitize_public_text(f"error at {path}")

        assert path not in safe
        assert workflow.PUBLIC_PATH_REDACTION in safe
        assert workflow.find_privacy_leaks(path) == ["absolute_path"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_urls_are_not_redacted_as_posix_paths(self):
        raw = "GET https://example.com/a/b?x=1 then http://example.com/a/b"

        safe = workflow.sanitize_public_text(raw)

        assert "https://example.com/a/b?x=1" in safe
        assert "http://example.com/a/b" in safe
        assert workflow.find_privacy_leaks(safe) == []

    @pytest.mark.parametrize(
        "raw,fragments",
        [
            (
                "https://host/?origin=/Users/alice/Pictures/foo.jpg",
                ["/Users", "alice", "Pictures/foo.jpg"],
            ),
            (
                "https://host/?src=C:/Users/alice/iCloud Photos/foo.jpg",
                ["C:/Users", "alice", "iCloud Photos", "Photos/foo.jpg"],
            ),
            (
                "https://host/path?file=file:///Users/alice/Pictures/foo.jpg",
                ["file://", "/Users", "alice", "Pictures/foo.jpg"],
            ),
            (
                "https://example.com/callback?path=C%3A%2FUsers%2Falice%2Fsecret.jpg",
                ["C%3A%2FUsers", "alice", "secret.jpg"],
            ),
            (
                "https://host/?origin=%2FUsers%2Falice%2FPictures%2Ffoo.jpg",
                ["%2FUsers", "alice", "Pictures"],
            ),
            (
                "https://host/?origin=%252FUsers%252Falice%252FPictures%252Ffoo.jpg",
                ["%252FUsers", "alice", "Pictures"],
            ),
            (
                "https://host/?origin=%25252FUsers%25252Falice%25252FPictures%25252Ffoo.jpg",
                ["%25252FUsers", "alice", "Pictures"],
            ),
            (
                "https://host/?src=C%253A%252FUsers%252Falice%252Fsecret.jpg",
                ["C%253A%252FUsers", "alice", "secret.jpg"],
            ),
            (
                "https://host/#/Users/alice/Pictures/foo.jpg",
                ["/Users", "alice", "Pictures/foo.jpg"],
            ),
            (
                "https://host/path/Users/alice/Pictures/foo.jpg",
                ["/Users", "alice", "Pictures/foo.jpg"],
            ),
        ],
    )
    def test_urls_with_embedded_local_paths_are_redacted(self, raw, fragments):
        safe = workflow.sanitize_public_text(raw)

        assert workflow.find_privacy_leaks(raw) == ["absolute_path"]
        assert workflow.find_privacy_leaks(safe) == []
        for fragment in fragments:
            assert fragment not in safe

    def test_slash_separated_prose_is_not_redacted_as_posix_path(self):
        raw = "candidate manifest / candidate selection"

        safe = workflow.sanitize_public_text(raw)

        assert safe == raw
        assert workflow.find_privacy_leaks(raw) == []

    @pytest.mark.parametrize(
        "raw,fragments",
        [
            ("origin=%252Fhome%252Falice%252Fsecret.png", ["%252Fhome", "alice", "secret.png"]),
            ("origin=%25252Fhome%25252Falice%25252Fsecret.png", ["%25252Fhome", "alice", "secret.png"]),
            ("path=%252F\u7528\u6237%252F\u56fe\u7247%252Ffoo.jpg", ["%252F", "\u7528\u6237", "\u56fe\u7247"]),
        ],
    )
    def test_double_encoded_local_path_tokens_are_redacted(self, raw, fragments):
        safe = workflow.sanitize_public_text(raw)

        assert workflow.find_privacy_leaks(raw) == ["absolute_path"]
        assert workflow.find_privacy_leaks(safe) == []
        for fragment in fragments:
            assert fragment not in safe

    def test_bearer_token_padding_is_redacted(self):
        raw = "Authorization: Bearer abc.def=="

        safe = workflow.sanitize_public_text(raw)

        assert f"Bearer {workflow.SECRET_REDACTION}" in safe
        assert "abc.def" not in safe
        assert "==" not in safe
        assert workflow.find_privacy_leaks(raw) == ["secret_token"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_bearer_token_scheme_is_case_insensitive(self):
        raw = "authorization: bearer abc.def=="

        safe = workflow.sanitize_public_text(raw)

        assert f"Bearer {workflow.SECRET_REDACTION}" in safe
        assert "abc.def" not in safe
        assert "==" not in safe
        assert workflow.find_privacy_leaks(raw) == ["secret_token"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_bearer_token68_tail_is_fully_redacted(self):
        raw = "Authorization: Bearer abc~def=="

        safe = workflow.sanitize_public_text(raw)

        assert f"Bearer {workflow.SECRET_REDACTION}" in safe
        assert "abc~def" not in safe
        assert "~def" not in safe
        assert "==" not in safe
        assert workflow.find_privacy_leaks(raw) == ["secret_token"]
        assert workflow.find_privacy_leaks(safe) == []

    @pytest.mark.parametrize(
        "raw",
        [
            "Bearer%20abc.def==",
            "bearer%20abc~def==",
            "Bearer+abc+/def==",
            "Authorization%3A%20Bearer%20abc.def==",
            "Bearer%20abc%2Fdef%2Bghi%3D%3D",
            "bearer%20abc%2Fdef%2Bghi%3D%3D",
            "Authorization%3A%20Bearer%20abc%2Fdef%2Bghi%3D%3D",
            "Bearer%2520abc.def%253D%253D",
            "authorization%253A%2520Bearer%2520abc.def%253D%253D",
            "Bearer%252520abc.def%25253D%25253D",
        ],
    )
    def test_url_encoded_bearer_tokens_are_redacted(self, raw):
        safe = workflow.sanitize_public_text(raw)

        assert "abc" not in safe
        assert "~def" not in safe
        assert "/def" not in safe
        assert "%2Fdef" not in safe
        assert "%2Bghi" not in safe
        assert "%3D%3D" not in safe
        assert "%253D%253D" not in safe
        assert "%25253D%25253D" not in safe
        assert "==" not in safe
        assert workflow.find_privacy_leaks(raw) == ["secret_token"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_normal_url_encoded_strings_are_not_over_redacted(self):
        raw = "https://example.com/callback?message=hello%20world&state=ok"

        safe = workflow.sanitize_public_text(raw)

        assert raw in safe
        assert workflow.find_privacy_leaks(raw) == []
        assert workflow.find_privacy_leaks(safe) == []

    @pytest.mark.parametrize(
        "raw,fragments",
        [
            ("sk%2Dabcdef1234567890", ["abcdef1234567890"]),
            ("sk%2dabcdef1234567890", ["abcdef1234567890"]),
            ("key%2Dabcdef1234567890", ["abcdef1234567890"]),
            ("https://host/?api_key=sk%2Dabcdef1234567890", ["abcdef1234567890"]),
            ("sk%252Dabcdef1234567890", ["abcdef1234567890"]),
            ("key%252Dabcdef1234567890", ["abcdef1234567890"]),
            ("sk%25252Dabcdef1234567890", ["abcdef1234567890"]),
            ("sk_live_abcdef1234567890", ["abcdef1234567890", "live"]),
            ("key_live_abcdef1234567890", ["abcdef1234567890", "live"]),
            ("sk_test_abcdef1234567890", ["abcdef1234567890", "test"]),
            ("key_test_abcdef1234567890", ["abcdef1234567890", "test"]),
            ("https://host/?api_key=sk_live_abcdef1234567890", ["abcdef1234567890", "live"]),
        ],
    )
    def test_percent_encoded_api_key_prefixes_are_redacted(self, raw, fragments):
        safe = workflow.sanitize_public_text(raw)

        for fragment in fragments:
            assert fragment not in safe
        assert workflow.find_privacy_leaks(raw) == ["secret_token"]
        assert workflow.find_privacy_leaks(safe) == []

    def test_privacy_scan_fails_before_redaction_and_passes_after(self):
        payload = {
            "windows": r"C:\Users\name\iCloud Photos\foo.jpg",
            "posix": "/private/var/folders/abc/file",
            "file_uri": "file:///Users/alice/Pictures/foo.jpg",
            "unicode_posix": "/\u7528\u6237/\u56fe\u7247/foo.jpg",
            "embedded_url_path": "https://host/?origin=/Users/alice/Pictures/foo.jpg",
            "encoded_url_path": "https://host/?path=C%3A%2FUsers%2Falice%2Fsecret.jpg",
            "double_encoded_url_path": "https://host/?path=C%253A%252FUsers%252Falice%252Fsecret.jpg",
            "apostrophe": r"C:\Users\O'Connor\Pictures\foo.jpg",
            "token": "bearer%2520abc~def%253D%253D",
            "api_key": "sk_live_abcdef1234567890",
        }

        assert workflow.find_privacy_leaks(payload) == ["absolute_path", "secret_token"]
        safe = workflow.sanitize_public_obj(payload)

        text = json.dumps(safe)
        assert "iCloud Photos" not in text
        assert "/private/var" not in text
        assert "file://" not in text
        assert "\u7528\u6237" not in text
        assert "\u56fe\u7247" not in text
        assert "C%3A%2FUsers" not in text
        assert "C%253A%252FUsers" not in text
        assert "alice" not in text
        assert "Connor" not in text
        assert "~def" not in text
        assert "abcdef1234567890" not in text
        assert "sk_live" not in text
        assert "==" not in text
        assert workflow.find_privacy_leaks(safe) == []


class TestDryRunCli:
    @pytest.mark.parametrize(
        "arg",
        [
            "--expected-current-media-count",
            "--expected-eligible-count",
            "--expected-ineligible-count",
        ],
    )
    def test_expected_count_args_accept_zero(self, arg):
        parsed = cli.build_parser().parse_args([arg, "0"])
        attr = arg.removeprefix("--").replace("-", "_")
        assert getattr(parsed, attr) == 0

    @pytest.mark.parametrize(
        "arg",
        [
            "--expected-current-media-count",
            "--expected-eligible-count",
            "--expected-ineligible-count",
        ],
    )
    @pytest.mark.parametrize("value", ["-1", "not-an-int"])
    def test_expected_count_args_reject_negative_and_non_integer(self, arg, value):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([arg, value])

    def test_cli_strict_zero_expected_counts_succeeds_on_empty_fixture(self, db, tmp_path):
        out = StringIO()
        report_json = tmp_path / "summary.json"
        report_md = tmp_path / "summary.md"

        code = cli.main(
            [
                "--dry-run",
                "--source-label",
                SOURCE_LABEL,
                "--expected-current-media-count",
                "0",
                "--expected-eligible-count",
                "0",
                "--expected-ineligible-count",
                "0",
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
            err=StringIO(),
        )

        assert code == 0
        payload = json.loads(report_json.read_text(encoding="utf-8"))
        assert payload["counts"]["target_media_count"] == 0
        assert payload["counts"]["eligible_media_count"] == 0
        assert payload["counts"]["ineligible_media_count"] == 0
        assert "target=0 eligible=0 ineligible=0" in out.getvalue()

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
