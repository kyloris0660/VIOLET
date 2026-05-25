"""Phase 4.3-A proper-noun signal trust audit tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.enums import ContentClassEnum, FileTypeEnum, RatingEnum, TagCategoryEnum
from app.models import Media, MediaEntityAssignment, MediaEntityCandidate, Tag, blombooru_media_tags

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "audit_phase43a_proper_noun_signal_trust.py"
spec = importlib.util.spec_from_file_location("phase43a_signal_trust_audit", SCRIPT_PATH)
assert spec and spec.loader
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")

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


def _media(db, media_id: int, *, content_class=ContentClassEnum.anime) -> Media:
    item = Media(
        id=media_id,
        filename=f"m{media_id}.jpg",
        path=f"media/original/m{media_id}.jpg",
        thumbnail_path=f"media/thumbnails/m{media_id}.jpg",
        hash=f"{media_id:064x}",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=100,
        rating=RatingEnum.safe,
        content_class=content_class,
    )
    db.add(item)
    db.commit()
    return item


def _tag(db, tag_id: int, name: str, category: TagCategoryEnum) -> Tag:
    item = Tag(id=tag_id, name=name, category=category)
    db.add(item)
    db.commit()
    return item


def _attach(
    db,
    *,
    media_id: int,
    tag_id: int,
    source: str,
    confidence: float | None,
    is_locked: bool,
    is_suggestion: bool,
) -> None:
    db.execute(
        blombooru_media_tags.insert().values(
            media_id=media_id,
            tag_id=tag_id,
            source=source,
            confidence=confidence,
            is_locked=is_locked,
            is_suggestion=is_suggestion,
        )
    )
    db.commit()


def test_ai_proper_noun_tag_is_weak_evidence_not_candidate_source(db):
    media = _media(db, 1, content_class=ContentClassEnum.anime)
    tag = _tag(db, 1, "hatsune_miku", TagCategoryEnum.character)
    _attach(
        db,
        media_id=media.id,
        tag_id=tag.id,
        source="ai_wd",
        confidence=0.91,
        is_locked=False,
        is_suggestion=False,
    )

    report = audit.audit_database(db)

    assert report["trust_tier_distribution"]["T3"] == 1
    simulation = report["candidate_generation_simulation"]["estimated_candidate_signal_rows_by_policy_group"]
    assert simulation["T0_T1_T2_default_candidate_sources"] == 0
    assert simulation["T3_ai_confirmed_if_included"] == 1
    assert report["recommendation"]["t3_ai_confirmed"].startswith("weak_evidence")


def test_general_meta_visual_tag_is_not_identity_signal(db):
    media = _media(db, 1)
    tag = _tag(db, 1, "blue_eyes", TagCategoryEnum.general)
    _attach(
        db,
        media_id=media.id,
        tag_id=tag.id,
        source="ai_wd",
        confidence=0.94,
        is_locked=False,
        is_suggestion=False,
    )

    report = audit.audit_database(db)

    assert report["media_tag_signal_counts"]["proper_noun_or_entity_like_rows"] == 0
    assert report["trust_tier_distribution"]["T5"] == 1
    simulation = report["candidate_generation_simulation"]["estimated_candidate_signal_rows_by_policy_group"]
    assert simulation["T5_visual_context_blocked"] == 1


def test_manual_locked_signal_is_higher_trust_than_ai_signal(db):
    _media(db, 1)
    manual_tag = _tag(db, 1, "manual_character", TagCategoryEnum.character)
    ai_tag = _tag(db, 2, "ai_character", TagCategoryEnum.character)
    _attach(
        db,
        media_id=1,
        tag_id=manual_tag.id,
        source="manual",
        confidence=1.0,
        is_locked=True,
        is_suggestion=False,
    )
    _attach(
        db,
        media_id=1,
        tag_id=ai_tag.id,
        source="ai_wd",
        confidence=0.88,
        is_locked=False,
        is_suggestion=False,
    )

    report = audit.audit_database(db)

    assert report["trust_tier_distribution"]["T2"] == 1
    assert report["trust_tier_distribution"]["T3"] == 1
    simulation = report["candidate_generation_simulation"]["estimated_candidate_signal_rows_by_policy_group"]
    assert simulation["T0_T1_T2_default_candidate_sources"] == 1
    assert simulation["T3_ai_confirmed_if_included"] == 1


def test_unknown_and_non_anime_identity_signals_are_counted_conservatively(db):
    _media(db, 1, content_class=ContentClassEnum.unknown)
    _media(db, 2, content_class=ContentClassEnum.non_anime)
    tag = _tag(db, 1, "possibly_wrong_character", TagCategoryEnum.character)
    _attach(
        db,
        media_id=1,
        tag_id=tag.id,
        source="ai_wd",
        confidence=0.77,
        is_locked=False,
        is_suggestion=False,
    )
    _attach(
        db,
        media_id=2,
        tag_id=tag.id,
        source="ai_wd",
        confidence=0.77,
        is_locked=False,
        is_suggestion=False,
    )

    report = audit.audit_database(db)

    counts = report["proper_noun_content_class_distribution"]
    assert counts["unknown"] == 1
    assert counts["non_anime"] == 1
    assert report["media_tag_signal_counts"]["proper_noun_non_anime_unknown_or_unclassified_rows"] == 2
    assert report["candidate_generation_simulation"]["estimated_candidate_signal_rows_by_policy_group"][
        "T0_T1_T2_default_candidate_sources"
    ] == 0


def test_audit_simulation_does_not_commit_or_insert_candidates(db, monkeypatch):
    _media(db, 1)
    tag = _tag(db, 1, "ai_character", TagCategoryEnum.character)
    _attach(
        db,
        media_id=1,
        tag_id=tag.id,
        source="ai_wd",
        confidence=0.86,
        is_locked=False,
        is_suggestion=False,
    )
    before_candidates = db.query(MediaEntityCandidate).count()
    before_assignments = db.query(MediaEntityAssignment).count()

    def fail_commit():
        raise AssertionError("audit_database must not call commit")

    monkeypatch.setattr(db, "commit", fail_commit)
    report = audit.audit_database(db)

    assert report["candidate_generation_simulation"]["no_writes_performed"] is True
    assert db.query(MediaEntityCandidate).count() == before_candidates
    assert db.query(MediaEntityAssignment).count() == before_assignments


def test_public_report_privacy_safety(db):
    _media(db, 1)
    tag = _tag(db, 1, "blue_eyes", TagCategoryEnum.general)
    _attach(
        db,
        media_id=1,
        tag_id=tag.id,
        source="ai_wd",
        confidence=0.94,
        is_locked=False,
        is_suggestion=False,
    )
    report = audit.audit_database(db)
    public_md = audit.render_public_markdown(report)

    audit.assert_public_text_safe(public_md)
    with pytest.raises(ValueError, match="local path"):
        audit.assert_public_text_safe("leak C:\\Users\\name\\secret.jpg")
    with pytest.raises(ValueError, match="secret"):
        audit.assert_public_text_safe("Authorization: Bearer abcdefghijklmnop")


def test_missing_entity_foundation_tables_are_reported_as_zero():
    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE blombooru_media (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE blombooru_tags (id INTEGER PRIMARY KEY)"))
        conn.commit()

    session = Session()
    try:
        counts = audit._entity_foundation_counts(session)
        anchors = audit._trusted_anchor_counts(
            session,
            proper_noun_manual_locked_rows=0,
            imported_rows=0,
        )
    finally:
        session.close()
        engine.dispose()

    assert counts["blombooru_entities"] == 0
    assert counts["blombooru_media_entity_candidates"] == 0
    assert counts["missing_entity_foundation_tables"] == 10
    assert anchors["confirmed_entity_assignments"] == 0
    assert anchors["manual_entity_aliases"] == 0
