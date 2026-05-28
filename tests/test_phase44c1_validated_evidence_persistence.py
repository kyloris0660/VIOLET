import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.enums import FileTypeEnum
from app.models import (
    Entity,
    EntityEvidence,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    ProviderCache,
    TagTranslation,
)
from app.services.provider_evidence_persistence_service import (
    EvidencePersistenceError,
    EvidencePersistenceOptions,
    persist_provider_evidence_plans,
)
from app.services.saucenao_evidence_mapper import map_saucenao_result_to_plan


def _valid_query_hash(media_id: int) -> str:
    return f"{media_id:064x}"


def _live_item(
    media_id: int,
    *,
    result_class: str,
    score: float,
    minimum_similarity: float,
    result_id: int | None,
    host: str | None = "danbooru.donmai.us",
    source_url: str | None = None,
) -> dict:
    top_result = {
        "similarity": score,
        "index_id": 9,
        "index_name": "Index #9: Danbooru - provider-returned-file.jpg",
        "result_id": result_id,
        "source_url_host": host,
        "source_url_present": bool(host or source_url),
        "creator": "candidate artist",
        "title": "candidate work",
    }
    if source_url is not None:
        top_result["source_url"] = source_url
    return {
        "media_id": media_id,
        "query_hash": _valid_query_hash(media_id),
        "request_shape_redacted": {
            "phase": "4.4-B1",
            "provider_key": "saucenao",
            "provider_category": "saucenao_style_reverse_search",
            "query_type": "reverse_search_derived_image",
            "input_kind": "derived_resized_stripped_image",
            "media_ref": f"approved_media_id:{media_id}",
            "filename_included": False,
            "local_path_included": False,
            "source_label_included": False,
            "send_original": False,
            "send_thumbnail": False,
            "send_derived": True,
        },
        "provider_result": {
            "result_class": result_class,
            "score": score,
            "response_status": "ok",
            "normalized_payload": {
                "privacy_redacted": True,
                "saucenao_header": {
                    "status": 0,
                    "minimum_similarity": minimum_similarity,
                    "short_remaining": 1,
                    "long_remaining": 90,
                },
                "top_result": top_result,
            },
        },
    }


def _metadata_item(media_id: int, *, artist: str, works: list[str], characters: list[str], result_id: int) -> dict:
    return {
        "media_id": media_id,
        "provider_index_label": "Danbooru",
        "source_url_host": "danbooru.donmai.us",
        "result_id": result_id,
        "artist": artist,
        "work_or_copyright": works,
        "characters": characters,
        "general_tags": [],
        "metadata_extraction_status": "requery_performed",
    }


def _manual(media_id: int, *, action: str = "keep_as_strong_evidence", judgment: str = "correct") -> dict:
    return {
        "media_id": media_id,
        "recommended_action": action,
        "judgment": judgment,
        "metadata_useful": action != "discard",
    }


def _plan_2687():
    return map_saucenao_result_to_plan(
        live_item=_live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=7695035),
        manual_item=_manual(2687),
        metadata_item=_metadata_item(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )


def _plan_2670():
    return map_saucenao_result_to_plan(
        live_item=_live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=9366672),
        manual_item=_manual(2670),
        metadata_item=_metadata_item(
            2670,
            artist="songchuan li",
            works=["blue archive"],
            characters=["kisaki (blue archive)"],
            result_id=9366672,
        ),
    )


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    for media_id in (2670, 2687, 2690, 2654, 2647):
        session.add(
            Media(
                id=media_id,
                filename=f"m{media_id}.jpg",
                path=f"media/original/m{media_id}.jpg",
                hash=f"{media_id:064x}",
                file_type=FileTypeEnum.image,
            )
        )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_dry_run_generates_write_plan_for_2687_and_2670_only(db):
    result = persist_provider_evidence_plans(db, [_plan_2687(), _plan_2670()], apply=False)

    assert result["success"] is True
    assert result["counts"]["ProviderCache"]["planned"] == 2
    assert result["counts"]["EntityEvidence"]["planned"] == 2
    assert result["counts"]["MediaEntityCandidate"]["planned"] == 7
    assert result["counts"]["ProviderCache"]["inserted"] == 0
    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


@pytest.mark.parametrize("media_id", [2690, 2654, 2647])
def test_low_confidence_discarded_samples_do_not_generate_positive_writes(db, media_id):
    plan = map_saucenao_result_to_plan(
        live_item=_live_item(media_id, result_class="low_confidence_match", score=50.0, minimum_similarity=35.0, result_id=None),
        manual_item=_manual(media_id, action="discard", judgment="wrong_unrelated"),
        metadata_item={"media_id": media_id, "metadata_extraction_status": "parser_missing_discarded_low_confidence_not_requeried"},
    )

    result = persist_provider_evidence_plans(
        db,
        [plan],
        apply=False,
        options=EvidencePersistenceOptions(strict=False),
    )

    assert result["success"] is False
    assert result["items"][0]["status"] == "blocked"
    assert result["counts"]["EntityEvidence"]["planned"] == 0
    assert result["counts"]["MediaEntityCandidate"]["planned"] == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


def test_missing_query_hash_blocks_write(db):
    live_item = _live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=7695035)
    live_item.pop("query_hash")
    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(2687),
        metadata_item=_metadata_item(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )

    with pytest.raises(EvidencePersistenceError, match="persistence_plan_validation_failed"):
        persist_provider_evidence_plans(db, [plan], apply=True)

    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0


def test_unsafe_source_url_blocks_write(db):
    plan = map_saucenao_result_to_plan(
        live_item=_live_item(
            2687,
            result_class="high_confidence_match",
            score=96.2,
            minimum_similarity=52.0,
            result_id=7695035,
            host="danbooru.donmai.us",
            source_url="https://danbooru.donmai.us/posts/7695035?src=C%3A%5CUsers%5Cprivate.jpg",
        ),
        manual_item=_manual(2687),
        metadata_item=_metadata_item(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )

    with pytest.raises(EvidencePersistenceError, match="persistence_plan_validation_failed"):
        persist_provider_evidence_plans(db, [plan], apply=True)

    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0


def test_unsafe_metadata_blocks_write(db):
    plan = map_saucenao_result_to_plan(
        live_item=_live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=7695035),
        manual_item=_manual(2687),
        metadata_item=_metadata_item(
            2687,
            artist="C:\\Users\\private\\artist",
            works=["honkai: star rail"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )

    with pytest.raises(ValueError, match="local path"):
        persist_provider_evidence_plans(db, [plan], apply=True)

    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0


def test_apply_creates_no_confirmed_assignment_entity_or_translation(db):
    result = persist_provider_evidence_plans(db, [_plan_2687(), _plan_2670()], apply=True)

    assert result["counts"]["ProviderCache"]["inserted"] == 2
    assert result["counts"]["EntityEvidence"]["inserted"] == 2
    assert result["counts"]["MediaEntityCandidate"]["inserted"] == 7
    assert db.query(ProviderCache).count() == 2
    assert db.query(EntityEvidence).count() == 2
    assert db.query(MediaEntityCandidate).count() == 7
    assert db.query(MediaEntityAssignment).count() == 0
    assert db.query(Entity).count() == 0
    assert db.query(TagTranslation).count() == 0
    assert all(candidate.entity_id is None for candidate in db.query(MediaEntityCandidate).all())
    assert all(
        row.response_json_redacted["extracted_metadata"]["localization_status"] == "pending"
        for row in db.query(ProviderCache).all()
    )


def test_idempotent_rerun_does_not_duplicate_rows(db):
    first = persist_provider_evidence_plans(db, [_plan_2687(), _plan_2670()], apply=True)
    second = persist_provider_evidence_plans(db, [_plan_2687(), _plan_2670()], apply=True)

    assert first["counts"]["ProviderCache"]["inserted"] == 2
    assert second["counts"]["ProviderCache"]["inserted"] == 0
    assert second["counts"]["ProviderCache"]["existing"] == 2
    assert second["counts"]["EntityEvidence"]["existing"] == 2
    assert second["counts"]["MediaEntityCandidate"]["existing"] == 7
    assert db.query(ProviderCache).count() == 2
    assert db.query(EntityEvidence).count() == 2
    assert db.query(MediaEntityCandidate).count() == 7


def test_candidate_write_can_be_deferred_without_creating_candidates(db):
    result = persist_provider_evidence_plans(
        db,
        [_plan_2687(), _plan_2670()],
        apply=True,
        options=EvidencePersistenceOptions(write_candidates=False),
    )

    assert result["candidate_deferred_schema_constraint"] is True
    assert result["counts"]["ProviderCache"]["inserted"] == 2
    assert result["counts"]["EntityEvidence"]["inserted"] == 2
    assert result["counts"]["MediaEntityCandidate"]["skipped"] == 7
    assert db.query(MediaEntityCandidate).count() == 0


def test_transaction_rolls_back_when_later_plan_conflicts(db):
    conflicting = ProviderCache(
        provider="saucenao",
        query_hash=_valid_query_hash(2670),
        query_type="reverse_search_derived_image",
        request_shape_redacted={"different": True},
        response_status="ok",
        response_json_redacted={"different": True},
    )
    db.add(conflicting)
    db.commit()

    with pytest.raises(EvidencePersistenceError, match="conflict_existing_provider_cache"):
        persist_provider_evidence_plans(db, [_plan_2687(), _plan_2670()], apply=True)

    assert db.query(ProviderCache).filter(ProviderCache.query_hash == _valid_query_hash(2687)).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


def test_runner_style_pre_read_then_commit_persists_writes(db):
    assert db.query(Media).count() == 5

    persist_provider_evidence_plans(db, [_plan_2687(), _plan_2670()], apply=True)
    db.commit()

    assert db.query(ProviderCache).count() == 2
    assert db.query(EntityEvidence).count() == 2
    assert db.query(MediaEntityCandidate).count() == 7
