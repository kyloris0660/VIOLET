"""Phase 4.1 entity metadata foundation tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base, migrate_add_entity_metadata_tables  # noqa: E402
from app.enums import (  # noqa: E402
    ContentClassEnum,
    EntityCandidateGeneratorEnum,
    EntityCandidateStatusEnum,
    EntityEvidenceTypeEnum,
    EntityMetadataSourceEnum,
    EntityReviewStatusEnum,
    EntityTypeEnum,
    FileTypeEnum,
    MediaEntityRoleEnum,
    RatingEnum,
    TagCategoryEnum,
)
from app.models import (  # noqa: E402
    Entity,
    EntityAlias,
    ExternalSource,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    Tag,
    TagTranslation,
)
from app.services.entity_metadata_service import (  # noqa: E402
    EntityMetadataError,
    accept_candidate,
    add_alias,
    create_candidate,
    create_entity,
    create_or_update_assignment,
    is_external_lookup_allowed,
    list_entity_aliases,
    list_media_entities,
    record_evidence,
    reject_candidate,
)


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


def _media(db, media_id: int = 1, *, content_class=ContentClassEnum.anime):
    item = Media(
        id=media_id,
        filename=f"m{media_id}.jpg",
        path=f"media/original/m{media_id}.jpg",
        thumbnail_path=f"media/thumbnails/m{media_id}.jpg",
        hash=f"{media_id:064x}",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=123,
        rating=RatingEnum.safe,
        content_class=content_class,
    )
    db.add(item)
    db.commit()
    return item


def _tag(db, tag_id: int = 1):
    item = Tag(id=tag_id, name=f"tag_{tag_id}", category=TagCategoryEnum.character)
    db.add(item)
    db.commit()
    return item


def test_model_metadata_creates_entity_tables(db):
    tables = set(inspect(db.bind).get_table_names())
    expected = {
        "blombooru_entities",
        "blombooru_entity_aliases",
        "blombooru_entity_external_identities",
        "blombooru_entity_evidence",
        "blombooru_media_entity_candidates",
        "blombooru_media_entity_assignments",
        "blombooru_entity_translations",
        "blombooru_external_sources",
        "blombooru_provider_cache",
        "blombooru_negative_lookup_cache",
    }
    assert expected.issubset(tables)


def test_migration_creates_entity_tables_on_legacy_schema():
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE blombooru_media (id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE blombooru_tags (id INTEGER PRIMARY KEY)"))
            conn.commit()

        migrate_add_entity_metadata_tables(engine, inspect(engine))
        migrate_add_entity_metadata_tables(engine, inspect(engine))

        tables = set(inspect(engine).get_table_names())
        assert "blombooru_entities" in tables
        assert "blombooru_media_entity_assignments" in tables
        assert "blombooru_provider_cache" in tables
    finally:
        engine.dispose()


def test_create_entity_and_duplicate_alias_behavior(db):
    entity = create_entity(
        db,
        entity_type=EntityTypeEnum.character,
        canonical_name="Hatsune Miku",
    )
    alias = add_alias(
        db,
        entity_id=entity.id,
        alias="hatsune_miku",
        alias_type="original",
        source="manual",
        is_primary=True,
    )
    duplicate = add_alias(db, entity_id=entity.id, alias="Hatsune   Miku", source="tag")
    db.commit()

    assert entity.normalized_key == "hatsune_miku"
    assert duplicate.id == alias.id
    assert db.query(EntityAlias).count() == 1
    assert list_entity_aliases(db, entity.id)[0].normalized_alias == "hatsune_miku"


def test_entity_normalized_key_uniqueness(db):
    db.add(Entity(type=EntityTypeEnum.work, canonical_name="Example", normalized_key="example"))
    db.add(Entity(type=EntityTypeEnum.work, canonical_name="Example!", normalized_key="example"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_candidate_suggestion_does_not_create_assignment(db):
    media = _media(db)
    entity = create_entity(db, entity_type="character", canonical_name="Akari")
    evidence = record_evidence(
        db,
        evidence_type=EntityEvidenceTypeEnum.tag_signal,
        source_type="tag",
        media_id=media.id,
        entity_id=entity.id,
        score=0.8,
        summary="internal tag signal",
    )
    candidate = create_candidate(
        db,
        media_id=media.id,
        entity_id=entity.id,
        entity_type="character",
        candidate_name="akari",
        score=0.8,
        generator=EntityCandidateGeneratorEnum.ai_tag,
        evidence_id=evidence.id,
    )
    db.commit()

    assert candidate.status == EntityCandidateStatusEnum.suggested
    assert db.query(MediaEntityAssignment).count() == 0


def test_reject_candidate_does_not_create_assignment(db):
    media = _media(db)
    entity = create_entity(db, entity_type="character", canonical_name="Akari")
    candidate = create_candidate(
        db,
        media_id=media.id,
        entity_id=entity.id,
        entity_type="character",
        candidate_name="akari",
        generator="internal_tag",
    )
    reject_candidate(db, candidate_id=candidate.id, review_reason="ambiguous")
    db.commit()

    assert candidate.status == EntityCandidateStatusEnum.rejected
    assert db.query(MediaEntityAssignment).count() == 0


def test_confirmed_assignment_requires_manual_source_or_evidence(db):
    media = _media(db)
    entity = create_entity(db, entity_type="work", canonical_name="Example Work")

    with pytest.raises(EntityMetadataError, match="require evidence"):
        create_or_update_assignment(
            db,
            media_id=media.id,
            entity_id=entity.id,
            role=MediaEntityRoleEnum.work,
            review_status=EntityReviewStatusEnum.confirmed,
            source=EntityMetadataSourceEnum.trusted_external,
        )

    manual = create_or_update_assignment(
        db,
        media_id=media.id,
        entity_id=entity.id,
        role=MediaEntityRoleEnum.work,
        review_status=EntityReviewStatusEnum.confirmed,
        source=EntityMetadataSourceEnum.manual,
    )
    db.commit()

    assert manual.review_status == EntityReviewStatusEnum.confirmed
    assert list_media_entities(db, media.id)[0].source == EntityMetadataSourceEnum.manual


def test_accept_candidate_creates_confirmed_assignment_with_provenance(db):
    media = _media(db)
    tag = _tag(db)
    entity = create_entity(db, entity_type="character", canonical_name="Akari")
    evidence = record_evidence(
        db,
        evidence_type="tag_signal",
        source_type="tag",
        media_id=media.id,
        tag_id=tag.id,
        entity_id=entity.id,
        score=0.9,
        summary="tag-backed evidence",
    )
    candidate = create_candidate(
        db,
        media_id=media.id,
        entity_id=entity.id,
        entity_type="character",
        candidate_name="akari",
        generator="internal_tag",
        evidence_id=evidence.id,
        score=0.9,
    )
    assignment = accept_candidate(
        db,
        candidate_id=candidate.id,
        source=EntityMetadataSourceEnum.trusted_external,
    )
    db.commit()

    assert candidate.status == EntityCandidateStatusEnum.accepted
    assert assignment.review_status == EntityReviewStatusEnum.confirmed
    assert assignment.evidence_id == evidence.id


def test_entity_tables_do_not_conflict_with_tag_translation(db):
    tag = _tag(db)
    db.add(
        TagTranslation(
            tag_id=tag.id,
            canonical_name=tag.name,
            language="zh-CN",
            display_name="manual display",
            source="manual",
            status="reviewed",
        )
    )
    entity = create_entity(db, entity_type="character", canonical_name=tag.name)
    add_alias(db, entity_id=entity.id, alias=tag.name, source="tag")
    db.commit()

    assert db.query(TagTranslation).count() == 1
    assert db.query(Entity).count() == 1


def test_media_delete_cascades_candidates_and_assignments(db):
    media = _media(db)
    entity = create_entity(db, entity_type="character", canonical_name="Akari")
    candidate = create_candidate(
        db,
        media_id=media.id,
        entity_id=entity.id,
        entity_type="character",
        candidate_name="akari",
    )
    create_or_update_assignment(
        db,
        media_id=media.id,
        entity_id=entity.id,
        role="character",
        review_status="confirmed",
        source="manual",
        created_from_candidate_id=candidate.id,
    )
    db.commit()

    db.delete(media)
    db.commit()

    assert db.query(MediaEntityCandidate).count() == 0
    assert db.query(MediaEntityAssignment).count() == 0


def test_external_lookup_privacy_gate_defaults_closed(db):
    assert is_external_lookup_allowed(ContentClassEnum.anime) is False
    assert is_external_lookup_allowed(ContentClassEnum.unknown, provider_enabled=True) is False
    assert is_external_lookup_allowed(ContentClassEnum.non_anime, provider_enabled=True) is False
    assert is_external_lookup_allowed(ContentClassEnum.illustration, provider_enabled=True) is False
    assert is_external_lookup_allowed(ContentClassEnum.anime, provider_enabled=True) is True

    provider = ExternalSource(provider="danbooru", enabled=False, privacy_policy={"allow_unknown": True})
    assert is_external_lookup_allowed(ContentClassEnum.anime, external_source=provider) is False

    provider.enabled = True
    assert is_external_lookup_allowed(ContentClassEnum.anime, external_source=provider) is True
    assert is_external_lookup_allowed(ContentClassEnum.unknown, external_source=provider) is True
