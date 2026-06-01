"""Focused tests for the Phase 4.4-P2R-F3 Pixiv metadata normalization pilot."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base  # noqa: E402
from app.enums import EntityMetadataSourceEnum, EntityStatusEnum, EntityTypeEnum, TagCategoryEnum  # noqa: E402
from app.models import Entity, EntityAlias, Tag  # noqa: E402
from scripts import run_phase44p2r_f3_pixiv_metadata_normalization_pilot as pilot  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _entrypoint(mode: str = "explicit_operator_command_mode") -> pilot.GalleryDlEntrypoint:
    return pilot.GalleryDlEntrypoint(
        mode=mode,
        command=("py", "-m", "gallery_dl"),
        version="1.32.1",
        available=True,
        reproducibility_status="conditional_explicit_operator_command",
    )


def _completed(args, returncode=0, stdout="1.32.1\n", stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def _record(**overrides):
    values = {
        "work_id": "100000001",
        "page_index": 0,
        "page_count": 1,
        "title": "Private title",
        "artist_name": "Private artist",
        "artist_id": "200",
        "tags": ("　女の子　", "R-18", "Blue Archive", "mystery_tag"),
        "metadata_richness": "rich_structured_metadata",
        "local_match_status": "metadata_matches_eligible_anime_local_prior",
        "local_match_content_class": "anime",
        "local_match_content_class_approved": True,
        "local_media_id_private": 1,
        "eligible_for_future_local_source_hint": True,
        "eligible_for_future_entity_candidate": True,
    }
    values.update(overrides)
    return pilot.f2.PixivGalleryDlAdapterRecord(**values)


def test_raw_pixiv_tags_and_unicode_are_preserved():
    record = _record(tags=("　女の子　", "ＡＢＣ", "謎タグ"))
    normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())

    assert normalized[0].raw_pixiv_tags == ("　女の子　", "ＡＢＣ", "謎タグ")
    assert normalized[0].normalized_unicode_tags == ("女の子", "ABC", "謎タグ")
    assert any(row.raw_tag == "　女の子　" and row.normalized_tag == "女の子" for row in rows)
    assert all(row.db_write_allowed is False for row in rows)


def test_pixiv_user_metadata_becomes_high_confidence_artist_candidate():
    record = _record(artist_name="Artist Name", artist_id="12345", tags=())
    normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())

    artist_rows = [row for row in rows if row.reason == "pixiv_user_metadata"]
    assert len(artist_rows) == 1
    assert artist_rows[0].candidate_namespace == "artist"
    assert artist_rows[0].confidence >= 0.9
    assert artist_rows[0].future_entity_candidate_eligible is True
    assert normalized[0].entity_candidates[0]["reason"] == "pixiv_user_metadata"


def test_raw_tags_are_not_forced_into_entity_candidates():
    record = _record(tags=("mystery_tag", "another_unknown"), artist_name=None, artist_id=None)
    _normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())

    assert rows
    assert all(row.candidate_kind != "entity_candidate" for row in rows)
    assert {row.candidate_kind for row in rows} == {"unknown_or_unresolved_pixiv_tag"}


def test_original_no_series_work_does_not_force_copyright():
    record = _record(tags=("オリジナル", "女の子"), artist_name=None, artist_id=None)
    normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())

    assert normalized[0].original_work_status == "original_work_context_claimed_by_pixiv_tag"
    assert not any(row.candidate_namespace == "copyright" for row in rows)
    assert any(row.candidate_kind == "original_work_context" for row in rows)


def test_existing_entity_and_alias_match_are_read_only_entity_candidates(db):
    entity = Entity(
        type=EntityTypeEnum.work,
        canonical_name="Blue Archive",
        normalized_key="blue_archive",
        status=EntityStatusEnum.active,
    )
    db.add(entity)
    db.flush()
    db.add(
        EntityAlias(
            entity_id=entity.id,
            alias="ブルーアーカイブ",
            normalized_alias="ブルーアーカイブ",
            source=EntityMetadataSourceEnum.manual,
            needs_review=False,
        )
    )
    db.commit()
    index = pilot.build_local_classification_index(db)
    record = _record(tags=("Blue Archive", "ブルーアーカイブ"), artist_name=None, artist_id=None)

    _normalized, rows = pilot.normalize_metadata_candidates([record], index)

    entity_rows = [row for row in rows if row.candidate_kind == "entity_candidate"]
    assert len(entity_rows) == 2
    assert {row.candidate_namespace for row in entity_rows} == {"copyright"}
    assert any(row.existing_alias_match for row in entity_rows)
    alias_groups = [row for row in rows if row.candidate_kind == "candidate_alias_group"]
    assert len(alias_groups) == 1
    assert alias_groups[0].db_write_allowed is False


def test_existing_tag_category_classifies_without_entity_creation(db):
    db.add(Tag(name="1girl", category=TagCategoryEnum.general))
    db.add(Tag(name="sample_character", category=TagCategoryEnum.character))
    db.commit()
    index = pilot.build_local_classification_index(db)
    record = _record(tags=("1girl", "sample_character"), artist_name=None, artist_id=None)

    _normalized, rows = pilot.normalize_metadata_candidates([record], index)

    general = next(row for row in rows if row.normalized_tag == "1girl")
    character = next(row for row in rows if row.normalized_tag == "sample_character")
    assert general.candidate_kind == "tag_candidate"
    assert general.candidate_namespace == "general"
    assert character.candidate_kind == "entity_candidate"
    assert character.candidate_namespace == "character"
    assert character.existing_entity_match is False


def test_fallback_classifies_ambiguous_general_sensitive_and_unknown():
    record = _record(tags=("Blue Archive", "女の子", "R-18", "mystery_tag"), artist_name=None, artist_id=None)
    _normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())
    by_tag = {row.normalized_tag: row for row in rows}

    assert by_tag["Blue Archive"].candidate_kind == "ambiguous_proper_noun_candidate"
    assert by_tag["女の子"].candidate_namespace == "general"
    assert by_tag["R-18"].candidate_kind == "sensitive_or_meta_tag_candidate"
    assert by_tag["mystery_tag"].candidate_kind == "unknown_or_unresolved_pixiv_tag"
    assert all(row.reason for row in rows)
    assert all(row.confidence > 0 for row in rows)


def test_sample_and_record_caps_fail_closed():
    pilot.enforce_sample_size(50)
    pilot.enforce_record_count(200, 200)
    with pytest.raises(pilot.SampleGateError, match="sample_or_record_cap_exceeded"):
        pilot.enforce_sample_size(51)
    with pytest.raises(pilot.SampleGateError, match="sample_or_record_cap_exceeded"):
        pilot.enforce_record_count(201, 200)
    with pytest.raises(pilot.SampleGateError, match="sample_or_record_cap_exceeded"):
        pilot.enforce_record_count(0, 201)


def test_record_cap_blocks_before_accepted_raw_write():
    raw_dir = pilot.PHASE_OUTPUT_DIR / "raw-unit-test-cap-block"
    raw_root = ROOT / raw_dir
    shutil.rmtree(raw_root, ignore_errors=True)
    sample = pilot.SelectedSample(
        work_id="100000025",
        page_indexes=(0,),
        content_classes=("anime",),
        local_media_ids_private=(25,),
        local_basenames_private=("100000025_p0.jpg",),
        has_p0_page=True,
        has_non_p0_page=False,
        duplicate_or_ambiguous=False,
    )

    def fake_run(args, **kwargs):
        return _completed(
            args,
            stdout="\n".join(
                [
                    json.dumps([3, {"id": 100000025, "num": 0, "filename": "100000025_p0"}]),
                    json.dumps([3, {"id": 100000025, "num": 1, "filename": "100000025_p1"}]),
                ]
            ),
        )

    try:
        results, parse_result = pilot.run_metadata_commands([sample], _entrypoint(), raw_dir, max_records=1, runner=fake_run)
        assert results[0].success is False
        assert results[0].blocked_over_limit is True
        assert results[0].error_class == "sample_or_record_cap_exceeded"
        assert len(parse_result.media_records) == 0
        assert not (raw_root / "metadata-01.jsonl").exists()
    finally:
        shutil.rmtree(raw_root, ignore_errors=True)


def test_bearer_authorization_redaction_redacts_token():
    redacted = pilot.redact_text(
        "Authorization: Bearer abcdefghijklmnop.1234567890\ncookie=session-secret",
        private_markers=["session-secret"],
    )

    assert "abcdefghijklmnop" not in redacted
    assert "1234567890" not in redacted
    assert "session-secret" not in redacted
    assert "Bearer [REDACTED]" in redacted


def test_public_report_redacts_exact_ids_paths_and_private_markers(tmp_path):
    record = _record(work_id="100000003", artist_name="Private Artist", artist_id="100", tags=("mystery_tag",))
    normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())
    media_summaries = pilot.build_media_summaries(normalized, rows)
    parse_result = pilot.f1.ParseResult(records=[], files=[])
    summary = pilot.build_public_summary(
        generated_at="2026-06-01T00:00:00+00:00",
        pr_context={"state": "MERGED", "url": "https://github.com/kyloris0660/VIOLET/pull/89"},
        git_context={"branch": "test"},
        entrypoint=_entrypoint(),
        db_identity={"configured_db_host": "localhost", "db_sensitive_value_included": False},
        sample_public={"selected_count": 1, "exact_work_ids_public": False},
        parse_result=parse_result,
        records=[record],
        normalized_records=normalized,
        candidate_rows=rows,
        media_summaries=media_summaries,
        join_summary={"status_counts": {}, "page_index_status_counts": {}},
        command_public={"metadata_command_count": 1},
        raw_scope={"raw_input_scope": "current_run_only", "current_run_raw_file_count": 1, "stale_raw_files_ignored_count": 0},
        local_index_summary=pilot.LocalClassificationIndex().public_summary(),
        containment={"output_path_violation": False},
        manual_review_guide=pilot.PRIVATE_MANUAL_REVIEW_GUIDE,
    )
    report = pilot.build_markdown_report(summary, private_markers=["100000003", str(tmp_path)])

    assert "100000003" not in json.dumps(summary, ensure_ascii=False)
    assert "100000003" not in report
    assert str(tmp_path) not in report


def test_private_artifact_paths_must_stay_under_local_manifests(tmp_path):
    containment = pilot.output_containment_summary(
        pilot.PHASE_OUTPUT_DIR,
        private_paths=[
            pilot.PRIVATE_DETAILS_JSON,
            pilot.PRIVATE_MEDIA_SUMMARY_CSV,
            pilot.PRIVATE_TAG_CANDIDATES_CSV,
            pilot.PRIVATE_ENTITY_CANDIDATES_CSV,
            pilot.PRIVATE_MANUAL_REVIEW_GUIDE,
            pilot.PRIVATE_RAW_DIR,
        ],
    )
    assert containment["private_artifacts_under_phase_root"] is True
    with pytest.raises(pilot.OutputPathError, match="f3_output_path_violation"):
        pilot.output_containment_summary(pilot.PHASE_OUTPUT_DIR, private_paths=[tmp_path / "outside.json"])
