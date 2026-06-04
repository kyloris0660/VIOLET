"""Focused tests for Phase 4.4-P2R-F6 source-layer media/search UI support."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base, get_db  # noqa: E402
from app.enums import FileTypeEnum, TagCategoryEnum  # noqa: E402
from app.models import (  # noqa: E402
    Entity,
    EntityAlias,
    EntityEvidence,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    SourceMetadataRecord,
    SourceSearchableNameAssertion,
    SourceTagObservation,
    Tag,
    TagTranslation,
    blombooru_media_tags,
)
from app.routes import search as search_route  # noqa: E402
from app.routes import source_assertions as source_assertions_route  # noqa: E402
from app.services.source_assertion_search_service import (  # noqa: E402
    apply_source_layer_filters,
    encode_source_assertion_filter,
    encode_source_tag_filter,
    list_media_source_layer,
    preview_source_assertion_promotion,
)
from app.utils.search_parser import apply_search_criteria, parse_search_query  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(source_assertions_route.router)
    app.include_router(search_route.router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def create_media(db, name: str, tags: list[tuple[str, TagCategoryEnum]] | None = None) -> Media:
    media = Media(
        filename=f"{name}.jpg",
        path=f"original/{name}.jpg",
        thumbnail_path=f"thumbs/{name}.jpg",
        hash=f"hash-{name}",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=100,
        width=100,
        height=100,
    )
    db.add(media)
    db.flush()

    for tag_name, category in tags or []:
        tag = db.query(Tag).filter(Tag.name == tag_name).one_or_none()
        if tag is None:
            tag = Tag(name=tag_name, category=category, post_count=1)
            db.add(tag)
            db.flush()
        media.tags.append(tag)
    db.commit()
    db.refresh(media)
    return media


def add_source_record(db, media: Media, provider: str, key: str) -> SourceMetadataRecord:
    row = SourceMetadataRecord(
        provider=provider,
        provider_record_key=key,
        media_id=media.id,
        title=f"title {key}",
        artist_name=f"artist {key}",
        metadata_kind="provider_metadata",
        data_type_label="fixture_or_mock",
        status="observed",
    )
    db.add(row)
    db.flush()
    return row


def add_assertion(
    db,
    record: SourceMetadataRecord,
    *,
    key: str,
    name: str,
    canonical: str,
    role: str = "character",
    status: str = "searchable_active",
) -> SourceSearchableNameAssertion:
    row = SourceSearchableNameAssertion(
        provider=record.provider,
        source_metadata_record_id=record.id,
        assertion_key=key,
        raw_input=name,
        normalized_input=name.lower(),
        canonical_name_key=canonical,
        asserted_name=name,
        asserted_role=role,
        status=status,
        confidence="high",
        confidence_score=0.91,
        structured_output_schema_version="source_searchable_name_assertion_v1",
        requires_review=status != "searchable_active",
    )
    db.add(row)
    db.flush()
    return row


def add_source_tag(
    db,
    record: SourceMetadataRecord,
    *,
    key: str,
    raw_tag: str,
    canonical: str,
) -> SourceTagObservation:
    row = SourceTagObservation(
        source_metadata_record_id=record.id,
        provider=record.provider,
        observation_key=key,
        raw_tag=raw_tag,
        normalized_tag=raw_tag.lower(),
        canonical_tag_key=canonical,
        source_tag_kind="provider_tag",
        status="observed",
    )
    db.add(row)
    db.flush()
    return row


def truth_counts(db) -> dict[str, int]:
    return {
        "Entity": db.query(Entity).count(),
        "EntityAlias": db.query(EntityAlias).count(),
        "EntityEvidence": db.query(EntityEvidence).count(),
        "MediaEntityCandidate": db.query(MediaEntityCandidate).count(),
        "MediaEntityAssignment": db.query(MediaEntityAssignment).count(),
        "TagTranslation": db.query(TagTranslation).count(),
        "media_tags": db.query(func.count()).select_from(blombooru_media_tags).scalar(),
    }


def test_media_source_layer_api_returns_unconfirmed_chips(client, db):
    media = create_media(db, "m1", [("old_character_tag", TagCategoryEnum.character)])
    record = add_source_record(db, media, "pixiv", "pixiv:m1")
    add_assertion(db, record, key="assert:ganyu", name="Ganyu", canonical="ganyu", role="character")
    add_assertion(
        db,
        record,
        key="assert:general",
        name="smile",
        canonical="smile",
        role="general_descriptor",
    )
    add_assertion(db, record, key="assert:bad", name="bad", canonical="bad", status="rejected")
    add_source_tag(db, record, key="tag:blue", raw_tag="blue hair", canonical="blue_hair")
    db.commit()

    response = client.get(f"/api/source-assertions/media/{media.id}")
    assert response.status_code == 200
    payload = response.json()

    assert payload["source_assertions"][0]["display_name"] == "Ganyu"
    assert payload["source_assertions"][0]["is_entity_truth"] is False
    assert payload["source_assertions"][0]["is_confirmed_entity"] is False
    assert payload["source_assertions"][0]["label_zh"] == "来源断言"
    assert payload["source_tags"][0]["display_name"] == "blue hair"
    assert payload["counts"]["hidden_assertions"]["general_descriptor"] == 1
    assert payload["manual_promotion"]["preview_only"] is True
    assert payload["manual_promotion"]["truth_writes_allowed"] is False


def test_mixed_normal_tag_and_source_assertion_search_is_and(db):
    m1 = create_media(db, "m1", [("solo", TagCategoryEnum.general), ("smile", TagCategoryEnum.general)])
    m2 = create_media(db, "m2", [("solo", TagCategoryEnum.general)])
    r1 = add_source_record(db, m1, "pixiv", "pixiv:m1")
    r2 = add_source_record(db, m2, "pixiv", "pixiv:m2")
    add_assertion(db, r1, key="assert:ganyu:m1", name="Ganyu", canonical="ganyu", role="character")
    add_assertion(db, r2, key="assert:ganyu:m2", name="Ganyu", canonical="ganyu", role="character")
    db.commit()

    filter_value = encode_source_assertion_filter(
        provider="pixiv",
        canonical_name_key="ganyu",
        asserted_role="character",
    )
    query = db.query(Media)
    query = apply_search_criteria(query, parse_search_query("solo smile"), db)
    query = apply_source_layer_filters(query, source_assertions=[filter_value])

    assert [row.id for row in query.all()] == [m1.id]


def test_multiple_source_assertions_are_intersection(db):
    m1 = create_media(db, "m1")
    m2 = create_media(db, "m2")
    r1 = add_source_record(db, m1, "pixiv", "pixiv:m1")
    r2 = add_source_record(db, m2, "pixiv", "pixiv:m2")
    add_assertion(db, r1, key="assert:ganyu:m1", name="Ganyu", canonical="ganyu", role="character")
    add_assertion(db, r1, key="assert:genshin:m1", name="Genshin", canonical="genshin", role="work_title")
    add_assertion(db, r2, key="assert:ganyu:m2", name="Ganyu", canonical="ganyu", role="character")
    db.commit()

    ganyu = encode_source_assertion_filter(provider="pixiv", canonical_name_key="ganyu", asserted_role="character")
    genshin = encode_source_assertion_filter(provider="pixiv", canonical_name_key="genshin", asserted_role="work_title")
    rows = apply_source_layer_filters(db.query(Media), source_assertions=[ganyu, genshin]).all()

    assert [row.id for row in rows] == [m1.id]


def test_multiple_source_tags_are_intersection(db):
    m1 = create_media(db, "m1")
    m2 = create_media(db, "m2")
    r1 = add_source_record(db, m1, "danbooru", "dan:m1")
    r2 = add_source_record(db, m2, "danbooru", "dan:m2")
    add_source_tag(db, r1, key="tag:blue:m1", raw_tag="blue hair", canonical="blue_hair")
    add_source_tag(db, r1, key="tag:smile:m1", raw_tag="smile", canonical="smile")
    add_source_tag(db, r2, key="tag:blue:m2", raw_tag="blue hair", canonical="blue_hair")
    db.commit()

    blue = encode_source_tag_filter(provider="danbooru", canonical_tag_key="blue_hair")
    smile = encode_source_tag_filter(provider="danbooru", canonical_tag_key="smile")
    rows = apply_source_layer_filters(db.query(Media), source_tags=[blue, smile]).all()

    assert [row.id for row in rows] == [m1.id]


def test_search_route_returns_source_filter_metadata(client, db):
    media = create_media(db, "m1", [("solo", TagCategoryEnum.general)])
    record = add_source_record(db, media, "pixiv", "pixiv:m1")
    add_assertion(db, record, key="assert:ganyu:m1", name="Ganyu", canonical="ganyu", role="character")
    db.commit()

    source_value = encode_source_assertion_filter(
        provider="pixiv",
        canonical_name_key="ganyu",
        asserted_role="character",
    )
    response = client.get(f"/api/search?q=solo&source_assertion={source_value}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == media.id
    assert payload["source_layer"] == "unconfirmed_source_assertion"
    assert payload["source_filters"]["source_assertions"][0]["display_name"] == "Ganyu"
    assert payload["source_filters"]["source_assertions"][0]["is_entity_truth"] is False


def test_promotion_preview_does_not_write_truth_path(db):
    media = create_media(db, "m1", [("solo", TagCategoryEnum.general)])
    record = add_source_record(db, media, "pixiv", "pixiv:m1")
    add_assertion(db, record, key="assert:ganyu:m1", name="Ganyu", canonical="ganyu", role="character")
    db.commit()

    source_value = encode_source_assertion_filter(
        provider="pixiv",
        canonical_name_key="ganyu",
        asserted_role="character",
    )
    before = truth_counts(db)
    preview = preview_source_assertion_promotion(db, source_value)
    after = truth_counts(db)

    assert preview["preview_only"] is True
    assert preview["disabled"] is True
    assert preview["truth_writes_allowed"] is False
    assert preview["affected_media_count"] == 1
    assert after == before


def test_list_media_source_layer_does_not_localize_or_mutate_translations(db):
    media = create_media(db, "m1")
    record = add_source_record(db, media, "pixiv", "pixiv:m1")
    add_assertion(db, record, key="assert:cn", name="甘雨", canonical="ganyu", role="character")
    db.commit()

    before = db.query(TagTranslation).count()
    payload = list_media_source_layer(db, media.id)
    after = db.query(TagTranslation).count()

    assert payload["source_assertions"][0]["display_name"] == "甘雨"
    assert after == before
