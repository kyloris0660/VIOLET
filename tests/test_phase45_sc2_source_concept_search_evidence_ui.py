"""Focused tests for Phase 4.5-SC2 SourceConcept search/evidence UI support."""

from __future__ import annotations

import json
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
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
    Tag,
    TagTranslation,
    blombooru_media_tags,
)
from app.routes import search as search_route  # noqa: E402
from app.routes import source_assertions as source_assertions_route  # noqa: E402
from app.routes import source_concepts as source_concepts_route  # noqa: E402
from app.services.source_concept_search_service import (  # noqa: E402
    list_media_source_concepts,
    preview_source_concept_promotion,
)
from app.services.source_metadata_registry_service import canonical_source_key  # noqa: E402


AYAKA_JA = "\u795e\u91cc\u7dbe\u83ef"


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
    app.include_router(search_route.router)
    app.include_router(source_assertions_route.router)
    app.include_router(source_concepts_route.router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def create_media(db, name: str, tags: list[tuple[str, TagCategoryEnum]] | None = None) -> Media:
    media = Media(
        filename=f"{name}.jpg",
        path=f"original/{name}.jpg",
        thumbnail_path=f"thumbs/{name}.jpg",
        hash=f"hash-sc2-{name}",
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


def add_source_concept(
    db,
    medias: list[Media],
    *,
    display_name: str = "Kamisato Ayaka",
    aliases: list[str] | None = None,
    status: str = "active",
    provider: str = "pixiv",
    evidence_strength: str = "strong",
    payload: dict | None = None,
) -> SourceConcept:
    aliases = aliases or [display_name, "kamisato_ayaka", AYAKA_JA]
    concept = SourceConcept(
        concept_key=f"character:{canonical_source_key(display_name)}:fixture",
        primary_display_name=display_name,
        concept_type_hint="character",
        status=status,
        confidence_score=0.91 if status == "active" else 0.42,
        evidence_score=0.87 if status == "active" else 0.35,
        media_count=len(medias),
        source_count=1,
        evidence_summary_json={
            "origin_counts": {"source_searchable_name_assertion": len(medias)},
            "max_trust_tier": evidence_strength,
        },
    )
    db.add(concept)
    db.flush()

    first_signal = None
    for idx, media in enumerate(medias, start=1):
        signal = SourceConceptSignal(
            signal_key=f"sc2:{canonical_source_key(display_name)}:{status}:{media.id}:{idx}",
            origin_type="source_searchable_name_assertion",
            origin_table="blombooru_source_searchable_name_assertions",
            origin_id=f"fixture:{media.id}:{idx}",
            provider=provider,
            media_id=media.id,
            source_metadata_record_id=None,
            source_record_id=f"{provider}:fixture:{media.id}",
            raw_value=display_name,
            display_value=display_name,
            normalized_key=canonical_source_key(display_name),
            canonical_key=canonical_source_key(display_name),
            role_hint="character",
            work_context_key="genshin_impact",
            source_kind="source_assertion",
            trust_tier=evidence_strength,
            confidence=0.91,
            status=status,
            evidence_payload=payload or {"safe": True},
        )
        db.add(signal)
        db.flush()
        first_signal = first_signal or signal
        db.add(
            SourceConceptEvidence(
                concept_id=concept.id,
                signal_id=signal.id,
                media_id=media.id,
                source_metadata_record_id=None,
                provider=provider,
                evidence_type="source_searchable_name_assertion",
                evidence_strength=evidence_strength,
                payload=payload or {"safe": True},
                run_id="sc2-test",
                status=status,
            )
        )
        db.add(
            SourceConceptSignalLink(
                signal_id=signal.id,
                concept_id=concept.id,
                link_status=status,
                confidence=0.91,
                resolution_reason_code="fixture_link",
                resolver_version="sc2-test",
                run_id="sc2-test",
                evidence_payload={"source_layer_only": True},
            )
        )

    seen_alias_keys = set()
    for alias_value in aliases:
        alias_key = canonical_source_key(alias_value)
        alias_role = "source_searchable_name_assertion"
        if (alias_key, alias_role) in seen_alias_keys:
            continue
        seen_alias_keys.add((alias_key, alias_role))
        db.add(
            SourceConceptAlias(
                concept_id=concept.id,
                alias_value=alias_value,
                alias_key=alias_key,
                display_name=alias_value,
                alias_role=alias_role,
                status=status,
                confidence=0.9,
                source_signal_id=first_signal.id if first_signal else None,
                evidence_payload={"source_layer_only": True},
            )
        )
        db.add(
            SourceConceptSearchIndex(
                concept_id=concept.id,
                search_key=alias_key,
                display_name=alias_value,
                alias_role=alias_role,
                weight=0.9,
                status=status,
                evidence_refs_json={"fixture": True},
                run_id="sc2-test",
            )
        )

    db.commit()
    db.refresh(concept)
    return concept


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


def result_ids(response) -> set[int]:
    assert response.status_code == 200
    return {item["id"] for item in response.json()["items"]}


def test_source_concept_alias_expands_search_and_reports_reason(client, db):
    linked_one = create_media(db, "ayaka-one")
    linked_two = create_media(db, "ayaka-two")
    add_source_concept(db, [linked_one, linked_two])

    response = client.get("/api/search", params={"q": AYAKA_JA})
    payload = response.json()

    assert result_ids(response) == {linked_one.id, linked_two.id}
    assert payload["source_concept_expansions"]
    expansion = payload["source_concept_expansions"][0]
    assert expansion["display_name"] == "Kamisato Ayaka"
    assert expansion["term"] == AYAKA_JA
    assert expansion["is_entity_truth"] is False
    assert expansion["truth_writes_allowed"] is False
    assert expansion["source_layer_label"] == "unconfirmed source-layer"
    assert {alias["search_key"] for alias in expansion["matched_aliases"]} == {canonical_source_key(AYAKA_JA)}


def test_normal_tag_results_are_preserved_when_alias_also_matches(client, db):
    normal_tag_media = create_media(db, "normal-tag", [("kamisato_ayaka", TagCategoryEnum.character)])
    source_media = create_media(db, "source-concept")
    add_source_concept(db, [source_media], aliases=["kamisato_ayaka", "Kamisato Ayaka"])

    response = client.get("/api/search", params={"q": "kamisato_ayaka"})

    assert result_ids(response) == {normal_tag_media.id, source_media.id}


def test_mixed_normal_tag_and_source_concept_query_preserves_and_semantics(client, db):
    both = create_media(db, "both", [("genshin_impact", TagCategoryEnum.copyright)])
    source_only = create_media(db, "source-only")
    tag_only = create_media(db, "tag-only", [("genshin_impact", TagCategoryEnum.copyright)])
    add_source_concept(db, [both, source_only])

    response = client.get("/api/search", params={"q": f"genshin_impact {AYAKA_JA}"})

    assert result_ids(response) == {both.id}
    assert tag_only.id not in result_ids(response)


def test_negative_and_quoted_query_boundaries(client, db):
    source_media = create_media(db, "quoted-source")
    keeper = create_media(db, "keeper", [("solo", TagCategoryEnum.general)])
    add_source_concept(db, [source_media], aliases=["Kamisato Ayaka"])

    quoted = client.get("/api/search", params={"q": '"Kamisato Ayaka"'})
    assert result_ids(quoted) == {source_media.id}

    negated = client.get("/api/search", params={"q": 'solo -"Kamisato Ayaka"'})
    assert result_ids(negated) == {keeper.id}


def test_needs_review_concept_requires_explicit_opt_in(client, db):
    review_media = create_media(db, "review-only")
    add_source_concept(
        db,
        [review_media],
        display_name="Review Only Character",
        aliases=["review_only_character"],
        status="needs_review",
        evidence_strength="weak",
    )

    default_response = client.get("/api/search", params={"q": "review_only_character"})
    default_payload = default_response.json()
    assert result_ids(default_response) == set()
    assert default_payload["source_concept_expansions"] == []
    assert default_payload["source_concept_review_hints"]

    opt_in = client.get(
        "/api/search",
        params={"q": "review_only_character", "include_source_needs_review": "1"},
    )
    assert result_ids(opt_in) == {review_media.id}
    assert opt_in.json()["source_concept_expansions"][0]["status"] == "needs_review"


def test_media_source_concept_grouping_and_detail_endpoint_are_redacted(client, db):
    media = create_media(db, "redaction-media")
    concept = add_source_concept(
        db,
        [media],
        display_name="Safe Character",
        aliases=["Safe Character", r"C:\Users\kyloris\Pictures\private.png"],
        payload={
            "local_path": r"C:\Users\kyloris\Pictures\private.png",
            "api_key": "secret-token",
        },
    )

    source_layer = client.get(f"/api/source-assertions/media/{media.id}").json()
    assert source_layer["source_concepts"]
    grouped = source_layer["source_concepts"][0]
    assert grouped["display_name"] == "Safe Character"
    assert grouped["is_entity_truth"] is False
    assert grouped["evidence_items"][0]["provider"] == "pixiv"

    detail = client.get(f"/api/source-concepts/{concept.id}")
    assert detail.status_code == 200
    text = json.dumps(detail.json(), ensure_ascii=False)
    assert r"C:\Users" not in text
    assert "private.png" not in text
    assert "secret-token" not in text
    assert "api_key" not in text
    assert "local_path" not in text


def test_source_concept_read_paths_do_not_write_truth_tables(client, db):
    media = create_media(db, "truth-boundary", [("solo", TagCategoryEnum.general)])
    concept = add_source_concept(db, [media])
    before = truth_counts(db)

    search_response = client.get("/api/search", params={"q": AYAKA_JA})
    detail_response = client.get(f"/api/source-concepts/{concept.id}")
    media_response = client.get(f"/api/source-concepts/media/{media.id}")
    preview = preview_source_concept_promotion(db, concept.id)
    grouped = list_media_source_concepts(db, media.id)

    after = truth_counts(db)
    assert search_response.status_code == 200
    assert detail_response.status_code == 200
    assert media_response.status_code == 200
    assert preview["disabled"] is True
    assert grouped[0]["manual_promotion"]["truth_writes_allowed"] is False
    assert after == before
