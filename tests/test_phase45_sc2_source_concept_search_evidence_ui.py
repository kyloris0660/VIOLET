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
from app.services import source_concept_resolver_service as resolver_service  # noqa: E402
from app.services import source_concept_search_service as concept_search_service  # noqa: E402
from app.services.source_concept_resolver_service import (  # noqa: E402
    SourceConceptSignalDraft,
    persist_source_concept_resolution,
    resolve_source_concepts,
)
from app.services.source_concept_search_service import (  # noqa: E402
    list_media_source_concepts,
    preview_source_concept_promotion,
)
from app.utils.search_parser import parse_search_query  # noqa: E402
from app.services.source_metadata_registry_service import canonical_source_key  # noqa: E402
from app.utils import cache as cache_module  # noqa: E402
from app.utils.cache import invalidate_source_concept_search_cache  # noqa: E402
from scripts import seed_phase45_sc2_e2e_fixture as sc2_seed  # noqa: E402


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
    concept_key: str | None = None,
    aliases: list[str] | None = None,
    status: str = "active",
    provider: str = "pixiv",
    evidence_strength: str = "strong",
    payload: dict | None = None,
) -> SourceConcept:
    aliases = aliases or [display_name, "kamisato_ayaka", AYAKA_JA]
    concept = SourceConcept(
        concept_key=concept_key or f"character:{canonical_source_key(display_name)}:fixture",
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


class FakeRedisClient:
    def __init__(self, store: dict[str, object]):
        self.store = store

    def scan_iter(self, pattern: str, count: int = 100):
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        yield from [key for key in list(self.store) if key.startswith(prefix)]

    def delete(self, *keys: str):
        for key in keys:
            self.store.pop(key, None)


class FakeRedisCache:
    def __init__(self):
        self._enabled = True
        self.store: dict[str, object] = {}
        self.client = FakeRedisClient(self.store)

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: object, expire: int = 3600):
        self.store[key] = value


def resolver_signal(key: str, raw: str, *, media_id: int | None = None) -> SourceConceptSignalDraft:
    canonical = canonical_source_key(raw)
    return SourceConceptSignalDraft(
        signal_key=key,
        origin_type="source_searchable_name_assertion",
        origin_table="fixture",
        origin_id=key,
        provider="fixture",
        media_id=media_id,
        source_metadata_record_id=None,
        source_record_id=f"fixture:{key}",
        raw_value=raw,
        display_value=raw,
        normalized_key=canonical,
        canonical_key=canonical,
        role_hint="character",
        work_context_key=None,
        parenthetical_base=None,
        parenthetical_context=None,
        source_kind="source_assertion",
        trust_tier="strong",
        confidence=0.9,
        status="active",
        evidence_payload={"safe": True},
    )


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


def expansion_names(response) -> set[str]:
    assert response.status_code == 200
    return {item["display_name"] for item in response.json()["source_concept_expansions"]}


def payload_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


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


def test_source_concept_aliases_expand_to_same_concept_level_media_set(client, db):
    shared_one = create_media(db, "alias-shared-one")
    shared_two = create_media(db, "alias-shared-two")
    add_source_concept(
        db,
        [shared_one],
        display_name="Kamisato Ayaka",
        concept_key="character:alias_symmetry_ja:fixture",
        aliases=[AYAKA_JA, "Kamisato Ayaka", "kamisato_ayaka"],
    )
    add_source_concept(
        db,
        [shared_two],
        display_name="Kamisato Ayaka",
        concept_key="character:alias_symmetry_en:fixture",
        aliases=["Kamisato Ayaka", "kamisato_ayaka"],
    )

    responses = [
        client.get("/api/search", params={"q": AYAKA_JA}),
        client.get("/api/search", params={"q": '"Kamisato Ayaka"'}),
        client.get("/api/search", params={"q": "kamisato_ayaka"}),
    ]

    expected_ids = {shared_one.id, shared_two.id}
    for response in responses:
        assert result_ids(response) == expected_ids
        assert expansion_names(response) == {"Kamisato Ayaka"}
        expansion_text = payload_text(response.json()["source_concept_expansions"])
        assert "unconfirmed source-layer" in expansion_text
        assert "concept_key" not in expansion_text


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


def test_needs_review_concept_expands_on_explicit_alias_search(client, db):
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
    assert result_ids(default_response) == {review_media.id}
    assert default_payload["source_concept_review_hints"] == []
    expansion = default_payload["source_concept_expansions"][0]
    assert expansion["status"] == "needs_review"
    assert expansion["source_layer_label"] == "unconfirmed source-layer"
    assert expansion["is_entity_truth"] is False


def test_media_source_concept_grouping_and_detail_endpoint_are_redacted(client, db):
    media = create_media(db, "redaction-media")
    concept = add_source_concept(
        db,
        [media],
        display_name="Safe Character",
        concept_key="character:c:_users_kyloris_pictures_private.png:fixture",
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
    assert "concept_key" not in grouped
    assert "concept_key_redacted" not in grouped

    detail = client.get(f"/api/source-concepts/{concept.id}")
    assert detail.status_code == 200
    text = json.dumps(detail.json(), ensure_ascii=False)
    assert r"C:\Users" not in text
    assert "private.png" not in text
    assert "private_png" not in text
    assert "c:_users_kyloris_pictures" not in text
    assert "c_users_kyloris_pictures" not in text
    assert "secret-token" not in text
    assert "api_key" not in text
    assert "local_path" not in text
    assert "concept_key" not in detail.json()

    search_payload = client.get("/api/search", params={"q": "Safe Character"}).json()
    search_text = json.dumps(search_payload, ensure_ascii=False)
    assert "concept_key" not in search_text
    assert "private.png" not in search_text
    assert "private_png" not in search_text


def test_canonicalized_filename_like_keys_are_omitted_from_public_payloads(client, db):
    media = create_media(db, "canonical-key-media")
    concept = add_source_concept(
        db,
        [media],
        display_name="Public Alias",
        concept_key="character:icloud_photos_original_private_png:fixture",
        aliases=["Public Alias", "icloud_photos_original_private_png"],
    )

    responses = [
        client.get(f"/api/source-concepts/{concept.id}").json(),
        client.get(f"/api/source-concepts/media/{media.id}").json(),
        client.get(f"/api/source-assertions/media/{media.id}").json(),
        client.get("/api/search", params={"q": "Public Alias"}).json(),
    ]
    for payload in responses:
        text = json.dumps(payload, ensure_ascii=False)
        assert "concept_key" not in text
        assert "icloud_photos_original_private_png" not in text
        assert "private_png" not in text


def test_canonicalized_filename_like_aliases_are_redacted_without_path_markers(client, db):
    media = create_media(db, "canonical-alias-media")
    unsafe_aliases = [
        "vacation_2024_jpg",
        "img_1234_jpeg",
        "private_png",
        "c_users_kyloris_pictures_private_png",
        "users_kyloris_pictures_private_png",
    ]
    safe_aliases = ["Safe Alias", "kamisato_ayaka", "Re:Zero", "Mona", AYAKA_JA]
    concept = add_source_concept(
        db,
        [media],
        display_name="Safe Alias",
        aliases=safe_aliases + unsafe_aliases,
    )

    responses = [
        client.get(f"/api/source-concepts/{concept.id}").json(),
        client.get(f"/api/source-concepts/media/{media.id}").json(),
        client.get(f"/api/source-assertions/media/{media.id}").json(),
        client.get("/api/search", params={"q": "Safe Alias"}).json(),
        client.get(f"/api/source-concepts/{concept.id}/promotion-preview").json(),
    ]
    for payload in responses:
        text = payload_text(payload)
        for unsafe in unsafe_aliases:
            assert unsafe not in text
        assert "kyloris" not in text
        assert "pictures_private_png" not in text
        assert "concept_key" not in text

    detail = responses[0]
    detail_text = payload_text(detail)
    for safe in safe_aliases:
        assert safe in detail_text
    assert detail["display_name"] == "Safe Alias"


def test_filename_like_primary_display_falls_back_to_opaque_concept_label(client, db):
    media = create_media(db, "filename-display-media")
    concept = add_source_concept(
        db,
        [media],
        display_name="vacation_2024_jpg",
        aliases=["vacation_2024_jpg"],
    )

    detail = client.get(f"/api/source-concepts/{concept.id}")
    assert detail.status_code == 200
    payload = detail.json()
    text = payload_text(payload)
    assert payload["concept_id"] == concept.id
    assert payload["display_name"] == f"SourceConcept {concept.id}"
    assert "vacation_2024_jpg" not in text


def test_search_cache_is_invalidated_after_source_concept_rows_change(client, db, monkeypatch):
    fake_cache = FakeRedisCache()
    monkeypatch.setattr(cache_module, "redis_cache", fake_cache)

    before = client.get("/api/search", params={"q": "late_alias"})
    assert before.status_code == 200
    assert before.json()["total"] == 0
    assert fake_cache.store

    media = create_media(db, "late-cache-media")
    add_source_concept(db, [media], display_name="Late Cache", aliases=["late_alias"])

    stale = client.get("/api/search", params={"q": "late_alias"})
    assert stale.json()["total"] == 0

    invalidate_source_concept_search_cache()
    updated = client.get("/api/search", params={"q": "late_alias"})
    updated_payload = updated.json()
    assert updated_payload["total"] == 1
    assert result_ids(updated) == {media.id}
    assert updated_payload["source_concept_expansions"]


def test_detail_and_promotion_preview_visibility_gate_by_status(client, db):
    active_media = create_media(db, "visible-active")
    review_media = create_media(db, "visible-review")
    active = add_source_concept(db, [active_media], display_name="Visible Active")
    review = add_source_concept(
        db,
        [review_media],
        display_name="Visible Review",
        aliases=["visible_review"],
        status="needs_review",
        evidence_strength="weak",
    )

    assert client.get(f"/api/source-concepts/{active.id}").status_code == 200
    assert client.get(f"/api/source-concepts/{review.id}").status_code == 200
    assert client.get(f"/api/source-concepts/{active.id}/promotion-preview").status_code == 200
    assert client.get(f"/api/source-concepts/{review.id}/promotion-preview").status_code == 200

    hidden_statuses = ["rejected", "ambiguous", "superseded"]
    for status in hidden_statuses:
        media = create_media(db, f"hidden-{status}")
        concept = add_source_concept(
            db,
            [media],
            display_name=f"Hidden {status}",
            aliases=[f"hidden_{status}"],
            status=status,
            evidence_strength="weak",
        )
        assert client.get(f"/api/source-concepts/{concept.id}").status_code == 404
        assert client.get(f"/api/source-concepts/{concept.id}/promotion-preview").status_code == 404
        assert preview_source_concept_promotion(db, concept.id) is None
        hidden_search = client.get("/api/search", params={"q": f"hidden_{status}"})
        assert result_ids(hidden_search) == set()
        assert hidden_search.json()["source_concept_expansions"] == []


def test_source_concept_search_urls_quote_parser_metacharacters(client, db):
    cases = [
        ("Re:Zero", '"Re:Zero"'),
        ("-name", '"-name"'),
        ("wild*alias", '"wild*alias"'),
        ("Kamisato Ayaka", '"Kamisato Ayaka"'),
        ("kamisato_ayaka", "kamisato_ayaka"),
    ]
    for idx, (alias, expected_token) in enumerate(cases):
        media = create_media(db, f"quote-{idx}")
        concept = add_source_concept(
            db,
            [media],
            display_name=alias,
            concept_key=f"character:quote_token_{idx}:fixture",
            aliases=[alias],
        )
        detail = client.get(f"/api/source-concepts/{concept.id}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["search_value"] == alias

        from urllib.parse import parse_qs, urlparse

        q_token = parse_qs(urlparse(payload["search_url"]).query)["q"][0]
        assert q_token == expected_token
        parsed = parse_search_query(q_token)
        assert parsed["tags"]["include"] == [alias]
        assert parsed["tags"]["exclude"] == []
        assert parsed["tags"]["wildcards"] == []

        response = client.get("/api/search", params={"q": q_token})
        ids = result_ids(response)
        assert media.id in ids
        if alias != "kamisato_ayaka":
            assert ids == {media.id}


def test_source_concept_write_paths_invalidate_search_cache(db, monkeypatch):
    resolver_calls = []
    seed_calls = []
    monkeypatch.setattr(
        resolver_service,
        "invalidate_source_concept_search_cache",
        lambda: resolver_calls.append("search"),
    )
    monkeypatch.setattr(
        sc2_seed,
        "invalidate_source_concept_search_cache",
        lambda: seed_calls.append("search"),
    )

    media = create_media(db, "resolver-cache")
    result = resolve_source_concepts(
        [resolver_signal("resolver-cache-signal", "Resolver Cache Alias", media_id=media.id)],
        run_id="resolver-cache-test",
    )
    persistence = persist_source_concept_resolution(db, result, apply=True)
    assert persistence["apply"] is True
    assert resolver_calls == ["search"]

    sc2_seed._delete_existing_fixture(db)
    assert seed_calls == ["search"]


def test_source_concept_filter_uses_all_matching_ids_beyond_display_cap(client, db):
    shared_alias = "shared_alias"
    total_active = concept_search_service.MAX_SEARCH_EXPANSIONS_PER_TERM * 3 + 5
    concept_media_ids = set()
    for idx in range(total_active):
        media = create_media(db, f"shared-{idx}", [("solo", TagCategoryEnum.general)])
        concept_media_ids.add(media.id)
        add_source_concept(
            db,
            [media],
            display_name=f"Shared Character {idx:02d}",
            aliases=[shared_alias, f"shared_character_{idx:02d}"],
        )
    review_media = create_media(db, "shared-review", [("solo", TagCategoryEnum.general)])
    add_source_concept(
        db,
        [review_media],
        display_name="Shared Review",
        aliases=[shared_alias, "shared_review"],
        status="needs_review",
        evidence_strength="weak",
    )
    keeper = create_media(db, "shared-keeper", [("solo", TagCategoryEnum.general)])

    positive = client.get("/api/search", params={"q": shared_alias, "limit": 100})
    positive_payload = positive.json()
    assert positive_payload["total"] == total_active + 1
    assert result_ids(positive) == concept_media_ids | {review_media.id}
    assert len(positive_payload["source_concept_expansions"]) == concept_search_service.MAX_SEARCH_EXPANSIONS_PER_TERM

    opt_in = client.get(
        "/api/search",
        params={"q": shared_alias, "limit": 100, "include_source_needs_review": "1"},
    )
    assert opt_in.json()["total"] == total_active + 1
    assert review_media.id in result_ids(opt_in)

    negated = client.get("/api/search", params={"q": f"solo -{shared_alias}", "limit": 100})
    assert result_ids(negated) == {keeper.id}

    negated_opt_in = client.get(
        "/api/search",
        params={"q": f"solo -{shared_alias}", "limit": 100, "include_source_needs_review": "1"},
    )
    assert result_ids(negated_opt_in) == {keeper.id}


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
