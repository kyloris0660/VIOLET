"""Phase 4.2 admin entity correction API tests."""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base  # noqa: E402
from app.enums import (  # noqa: E402
    ContentClassEnum,
    EntityCandidateStatusEnum,
    EntityReviewStatusEnum,
    EntityTypeEnum,
    FileTypeEnum,
    RatingEnum,
    TagCategoryEnum,
)
from app.models import (  # noqa: E402
    Entity,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    Tag,
    blombooru_media_tags,
)
from app.routes.admin import entities as entity_routes  # noqa: E402
from app.services.entity_metadata_service import create_candidate, create_entity  # noqa: E402


@pytest.fixture()
def db():
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
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(entity_routes.router, prefix="/api/admin")
    app.dependency_overrides[entity_routes.get_db] = lambda: db
    app.dependency_overrides[entity_routes.require_admin_mode] = lambda: object()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _media(db, media_id: int = 1):
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
        content_class=ContentClassEnum.anime,
    )
    db.add(item)
    db.commit()
    return item


def _tagged_media(db, media_id: int = 1):
    media = _media(db, media_id)
    tag = Tag(id=media_id, name=f"tag_{media_id}", category=TagCategoryEnum.character, post_count=1)
    db.add(tag)
    db.commit()
    db.execute(
        blombooru_media_tags.insert().values(
            media_id=media.id,
            tag_id=tag.id,
            source="manual",
            confidence=1.0,
            is_locked=True,
            is_suggestion=False,
        )
    )
    db.commit()
    return media, tag


def test_create_list_search_entity(client):
    resp = client.post(
        "/api/admin/entities",
        json={"entity_type": "character", "canonical_name": "Hatsune Miku"},
    )
    assert resp.status_code == 200
    assert resp.json()["entity"]["normalized_key"] == "hatsune_miku"

    list_resp = client.get("/api/admin/entities?search=Miku&entity_type=character")
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["canonical_name"] == "Hatsune Miku"
    assert payload["review_model"] == "targeted_correction"
    assert payload["exhaustive_review_required"] is False


def test_add_alias_and_find_entity_by_alias(client):
    entity = client.post(
        "/api/admin/entities",
        json={"entity_type": "character", "canonical_name": "Hatsune Miku"},
    ).json()["entity"]

    alias_resp = client.post(
        f"/api/admin/entities/{entity['id']}/aliases",
        json={"alias": "miku hatsune", "alias_type": "common", "language": "en"},
    )
    assert alias_resp.status_code == 200
    assert alias_resp.json()["alias"]["normalized_alias"] == "miku_hatsune"

    search_resp = client.get("/api/admin/entities?search=miku_hatsune")
    assert search_resp.status_code == 200
    assert search_resp.json()["items"][0]["id"] == entity["id"]


def test_manual_assign_entity_to_media_does_not_modify_media_tags(client, db):
    media, _tag = _tagged_media(db)
    entity = create_entity(db, entity_type=EntityTypeEnum.character, canonical_name="Akari")
    db.commit()
    before = db.execute(blombooru_media_tags.select()).all()

    resp = client.post(
        f"/api/admin/media/{media.id}/entity-assignments",
        json={"entity_id": entity.id, "role": "character", "locked": True},
    )
    assert resp.status_code == 200
    assignment = resp.json()["assignment"]
    assert assignment["source"] == "manual"
    assert assignment["review_status"] == "confirmed"
    assert assignment["locked"] is True

    after = db.execute(blombooru_media_tags.select()).all()
    assert after == before

    list_resp = client.get(f"/api/admin/media/{media.id}/entity-assignments")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["items"]) == 1


def test_locked_assignment_requires_explicit_update(client, db):
    media = _media(db)
    entity = create_entity(db, entity_type="character", canonical_name="Akari")
    db.commit()

    ok = client.post(
        f"/api/admin/media/{media.id}/entity-assignments",
        json={"entity_id": entity.id, "role": "character", "locked": True},
    )
    assert ok.status_code == 200

    blocked = client.post(
        f"/api/admin/media/{media.id}/entity-assignments",
        json={"entity_id": entity.id, "role": "character", "locked": False},
    )
    assert blocked.status_code == 409

    explicit = client.post(
        f"/api/admin/media/{media.id}/entity-assignments",
        json={
            "entity_id": entity.id,
            "role": "character",
            "locked": False,
            "allow_locked_update": True,
        },
    )
    assert explicit.status_code == 200
    assert explicit.json()["assignment"]["locked"] is False


def test_reject_wrong_assignment_hides_it_from_confirmed_list(client, db):
    media = _media(db)
    entity = create_entity(db, entity_type="work", canonical_name="Wrong Work")
    db.commit()
    assignment_id = client.post(
        f"/api/admin/media/{media.id}/entity-assignments",
        json={"entity_id": entity.id, "role": "work"},
    ).json()["assignment"]["id"]

    reject_resp = client.post(
        f"/api/admin/media/{media.id}/entity-assignments/{assignment_id}/reject",
        json={"review_reason": "wrong work"},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["assignment"]["review_status"] == "rejected"

    confirmed = client.get(f"/api/admin/media/{media.id}/entity-assignments")
    assert confirmed.status_code == 200
    assert confirmed.json()["items"] == []

    all_rows = client.get(f"/api/admin/media/{media.id}/entity-assignments?include_all=true")
    assert len(all_rows.json()["items"]) == 1


def test_candidate_accept_creates_assignment_and_marks_accepted(client, db):
    media = _media(db)
    entity = create_entity(db, entity_type="character", canonical_name="Akari")
    candidate = create_candidate(
        db,
        media_id=media.id,
        entity_id=entity.id,
        entity_type="character",
        candidate_name="akari",
        score=0.91,
    )
    db.commit()

    resp = client.post(f"/api/admin/entity-candidates/{candidate.id}/accept", json={"role": "character"})
    assert resp.status_code == 200
    assert resp.json()["assignment"]["created_from_candidate_id"] == candidate.id

    db.expire_all()
    assert db.get(MediaEntityCandidate, candidate.id).status == EntityCandidateStatusEnum.accepted
    assert db.query(MediaEntityAssignment).count() == 1


def test_candidate_reject_does_not_create_assignment(client, db):
    media = _media(db)
    entity = create_entity(db, entity_type="character", canonical_name="Akari")
    candidate = create_candidate(
        db,
        media_id=media.id,
        entity_id=entity.id,
        entity_type="character",
        candidate_name="akari",
    )
    db.commit()

    resp = client.post(
        f"/api/admin/entity-candidates/{candidate.id}/reject",
        json={"review_reason": "ambiguous"},
    )
    assert resp.status_code == 200

    db.expire_all()
    assert db.get(MediaEntityCandidate, candidate.id).status == EntityCandidateStatusEnum.rejected
    assert db.query(MediaEntityAssignment).count() == 0


def test_candidate_accept_failure_does_not_mark_accepted(client, db):
    media = _media(db)
    candidate = create_candidate(
        db,
        media_id=media.id,
        entity_id=None,
        entity_type="character",
        candidate_name="unlinked",
    )
    db.commit()

    resp = client.post(f"/api/admin/entity-candidates/{candidate.id}/accept", json={})
    assert resp.status_code == 400

    db.expire_all()
    assert db.get(MediaEntityCandidate, candidate.id).status == EntityCandidateStatusEnum.suggested
    assert db.query(MediaEntityAssignment).count() == 0


def test_entity_routes_require_admin_auth(db):
    app = FastAPI()
    app.include_router(entity_routes.router, prefix="/api/admin")
    app.dependency_overrides[entity_routes.get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.get("/api/admin/entities")
        assert resp.status_code in {401, 403}
    finally:
        app.dependency_overrides.clear()


def test_api_path_does_not_make_external_network_calls(client):
    with patch.object(socket, "create_connection", side_effect=AssertionError("external network call")):
        resp = client.post(
            "/api/admin/entities",
            json={"entity_type": "artist", "canonical_name": "Local Artist"},
        )
    assert resp.status_code == 200


def test_candidate_list_is_targeted_not_exhaustive_queue(client, db):
    media = _media(db)
    create_candidate(db, media_id=media.id, entity_type="character", candidate_name="akari")
    db.commit()

    resp = client.get("/api/admin/entity-candidates?status=suggested")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["review_model"] == "targeted_correction"
    assert payload["exhaustive_review_required"] is False
