from dataclasses import replace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import scripts.run_phase44c1_validated_evidence_persistence as runner
from app.database import Base
from app.enums import (
    EntityCandidateGeneratorEnum,
    EntityCandidateStatusEnum,
    EntityEvidenceTypeEnum,
    EntityMetadataSourceEnum,
    EntityReviewStatusEnum,
    EntityTypeEnum,
    FileTypeEnum,
    MediaEntityRoleEnum,
)
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
    evidence_summary,
    persist_provider_evidence_plans,
    provider_cache_payload_ref,
)
from app.services.saucenao_evidence_mapper import map_saucenao_result_to_plan
from scripts.run_phase44c1_validated_evidence_persistence import (
    build_idempotency_verification,
    build_public_summary,
)


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


def _runner_metadata_row(
    media_id: int,
    *,
    result_id: int | None,
    host: str | None = "danbooru.donmai.us",
    index_name: str = "Index #9: Danbooru - provider-returned-file.jpg",
    source_url: str | None = None,
    provider_key: str | None = "saucenao",
) -> dict:
    metadata = {
        2687: {
            "artist": "yunkaiming",
            "works": ["honkai: star rail", "honkai (series)"],
            "characters": ["acheron (honkai: star rail)"],
        },
        2670: {
            "artist": "songchuan li",
            "works": ["blue archive"],
            "characters": ["kisaki (blue archive)"],
        },
    }[media_id]
    top_result = {
        "index_name": index_name,
        "result_id": result_id,
        "source_url_hosts": [host] if host else [],
        "source_url_present": bool(host or source_url),
        "creator": metadata["artist"],
        "material": metadata["works"],
        "characters": metadata["characters"],
        "general_tags": [],
    }
    if source_url is not None:
        top_result["source_url"] = source_url
    if provider_key is not None:
        top_result["provider_key"] = provider_key
    return {"media_id": media_id, "top_result": top_result}


def _runner_live_details(*items: dict) -> dict:
    return {"provider_results": list(items)}


def _runner_metadata_details(*rows: dict) -> dict:
    return {"provider_results": list(rows)}


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


def _invalid_approved_plan_missing_query_hash():
    live_item = _live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=9366672)
    live_item.pop("query_hash")
    return map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(2670),
        metadata_item=_metadata_item(
            2670,
            artist="songchuan li",
            works=["blue archive"],
            characters=["kisaki (blue archive)"],
            result_id=9366672,
        ),
    )


def _identity():
    return {
        "violet_env": "development",
        "configured_db": "blombooru",
        "current_database": "blombooru",
        "db_host": "localhost",
        "db_port": 5432,
        "db_user": "postgres",
        "db_auth_hidden": True,
        "storage_root_basename": "AnimeLocalBooru",
        "storage_root_is_test_storage": False,
        "test_database_url_set": False,
    }


def _state(
    *,
    provider_cache: int = 2,
    evidence: int = 2,
    candidates: int = 7,
    assignments: int = 0,
    entity_count: int = 0,
    tag_translation_count: int = 0,
    media_tags: int = 0,
    low_evidence: int = 0,
    low_candidates: int = 0,
    provider_cache_unrelated: int = 0,
    evidence_unrelated: int = 0,
    candidates_unrelated: int = 0,
) -> dict:
    return {
        "approved_media_present": 2,
        "provider_cache_approved": provider_cache,
        "provider_cache_unrelated_existing_ignored": provider_cache_unrelated,
        "entity_evidence_approved": evidence,
        "entity_evidence_unrelated_existing_ignored": evidence_unrelated,
        "media_entity_candidates_c1": candidates,
        "media_entity_candidates_unrelated_existing_ignored": candidates_unrelated,
        "media_entity_assignments_for_approved": assignments,
        "entity_count": entity_count,
        "tag_translation_count": tag_translation_count,
        "media_tags_for_approved": media_tags,
        "low_confidence_provider_cache": 0,
        "low_confidence_positive_evidence": low_evidence,
        "low_confidence_candidates": low_candidates,
    }


def _successful_persistence() -> dict:
    return {
        "success": True,
        "counts": {
            "ProviderCache": {"planned": 2, "inserted": 2, "existing": 0, "skipped": 0},
            "EntityEvidence": {"planned": 2, "inserted": 2, "existing": 0, "skipped": 0},
            "MediaEntityCandidate": {"planned": 7, "inserted": 7, "existing": 0, "skipped": 0},
            "MediaEntityAssignment": {"inserted": 0},
            "Entity": {"inserted": 0},
        },
        "items": [],
        "candidate_deferred_schema_constraint": False,
    }


def _successful_idempotency() -> dict:
    return {
        "success": True,
        "counts": {
            "ProviderCache": {"planned": 2, "inserted": 0, "existing": 2, "skipped": 0},
            "EntityEvidence": {"planned": 2, "inserted": 0, "existing": 2, "skipped": 0},
            "MediaEntityCandidate": {"planned": 7, "inserted": 0, "existing": 7, "skipped": 0},
            "MediaEntityAssignment": {"inserted": 0},
            "Entity": {"inserted": 0},
        },
    }


class _FakeEngine:
    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


class _FakeSession:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class _FakeSessionLocal:
    def __init__(self):
        self.session = _FakeSession()

    def __call__(self):
        return self.session


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


def test_runner_plan_allows_approved_result_identities():
    plans = runner.build_phase44c1_plans(
        live_details=_runner_live_details(
            _live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=7695035),
            _live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=9366672),
        ),
        metadata_details=_runner_metadata_details(
            _runner_metadata_row(2687, result_id=7695035),
            _runner_metadata_row(2670, result_id=9366672),
        ),
        media_ids=[2687, 2670],
    )

    assert [plan.media_id for plan in plans] == [2687, 2670]
    assert [plan.source_match.provider_result_id for plan in plans] == ["7695035", "9366672"]


def test_runner_plan_blocks_approval_result_identity_mismatch():
    with pytest.raises(runner.PhaseC1Error, match="approval_result_identity_mismatch"):
        runner.build_phase44c1_plans(
            live_details=_runner_live_details(
                _live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=123),
                _live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=9366672),
            ),
            metadata_details=_runner_metadata_details(
                _runner_metadata_row(2687, result_id=123),
                _runner_metadata_row(2670, result_id=9366672),
            ),
            media_ids=[2687, 2670],
        )


def test_runner_plan_blocks_missing_approved_result_id():
    with pytest.raises(runner.PhaseC1Error, match="approved_result_id_missing"):
        runner.build_phase44c1_plans(
            live_details=_runner_live_details(
                _live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=7695035),
                _live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=None),
            ),
            metadata_details=_runner_metadata_details(
                _runner_metadata_row(2687, result_id=7695035),
                _runner_metadata_row(2670, result_id=None),
            ),
            media_ids=[2687, 2670],
        )


def test_runner_plan_blocks_source_identity_mismatch():
    with pytest.raises(runner.PhaseC1Error, match="source_identity_mismatch"):
        runner.build_phase44c1_plans(
            live_details=_runner_live_details(
                _live_item(
                    2687,
                    result_class="high_confidence_match",
                    score=96.2,
                    minimum_similarity=52.0,
                    result_id=7695035,
                    host="gelbooru.com",
                ),
                _live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=9366672),
            ),
            metadata_details=_runner_metadata_details(
                _runner_metadata_row(2687, result_id=7695035, host="gelbooru.com"),
                _runner_metadata_row(2670, result_id=9366672),
            ),
            media_ids=[2687, 2670],
        )


def test_runner_plan_blocks_live_metadata_result_identity_mismatch():
    with pytest.raises(runner.PhaseC1Error, match="live_metadata_identity_mismatch"):
        runner.build_phase44c1_plans(
            live_details=_runner_live_details(
                _live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=7695035),
                _live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=9366672),
            ),
            metadata_details=_runner_metadata_details(
                _runner_metadata_row(2687, result_id=9366672),
                _runner_metadata_row(2670, result_id=9366672),
            ),
            media_ids=[2687, 2670],
        )


def test_runner_plan_blocks_live_metadata_source_host_mismatch():
    with pytest.raises(runner.PhaseC1Error, match="live_metadata_identity_mismatch"):
        runner.build_phase44c1_plans(
            live_details=_runner_live_details(
                _live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=7695035),
                _live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=9366672),
            ),
            metadata_details=_runner_metadata_details(
                _runner_metadata_row(2687, result_id=7695035, host="gelbooru.com"),
                _runner_metadata_row(2670, result_id=9366672),
            ),
            media_ids=[2687, 2670],
        )


def test_runner_plan_blocks_metadata_identity_missing():
    with pytest.raises(runner.PhaseC1Error, match="metadata_identity_missing"):
        runner.build_phase44c1_plans(
            live_details=_runner_live_details(
                _live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=7695035),
                _live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=9366672),
            ),
            metadata_details=_runner_metadata_details(
                _runner_metadata_row(2687, result_id=None),
                _runner_metadata_row(2670, result_id=9366672),
            ),
            media_ids=[2687, 2670],
        )


def test_runner_plan_rejects_duplicate_media_ids_before_plan_build():
    with pytest.raises(runner.PhaseC1Error, match="duplicate_media_id"):
        runner.build_phase44c1_plans(
            live_details=_runner_live_details(
                _live_item(2687, result_class="high_confidence_match", score=96.2, minimum_similarity=52.0, result_id=7695035),
                _live_item(2670, result_class="high_confidence_match", score=91.96, minimum_similarity=37.66, result_id=9366672),
            ),
            metadata_details=_runner_metadata_details(
                _runner_metadata_row(2687, result_id=7695035),
                _runner_metadata_row(2670, result_id=9366672),
            ),
            media_ids=[2687, 2687, 2670],
        )


@pytest.mark.parametrize("nested_field", ["provider_query", "source_match"])
def test_nested_plan_media_id_mismatch_blocks_write(db, nested_field):
    plan = _plan_2687()
    mismatched_nested = replace(getattr(plan, nested_field), media_id=2670)
    mismatched_plan = replace(plan, **{nested_field: mismatched_nested})

    with pytest.raises(EvidencePersistenceError, match="nested_plan_identity_mismatch"):
        persist_provider_evidence_plans(db, [mismatched_plan], apply=True)

    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0
    assert db.query(MediaEntityAssignment).count() == 0
    assert db.query(Entity).count() == 0


def test_nested_plan_provider_key_mismatch_blocks_write(db):
    plan = _plan_2687()
    mismatched_source = replace(plan.source_match, provider_key="other_provider")
    mismatched_plan = replace(plan, source_match=mismatched_source)

    with pytest.raises(EvidencePersistenceError, match="nested_plan_identity_mismatch"):
        persist_provider_evidence_plans(db, [mismatched_plan], apply=True)

    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0
    assert db.query(MediaEntityAssignment).count() == 0
    assert db.query(Entity).count() == 0


def test_matching_nested_plan_identity_still_persists_without_entity_side_effects(db):
    result = persist_provider_evidence_plans(db, [_plan_2687()], apply=True)

    assert result["success"] is True
    assert result["counts"]["ProviderCache"]["inserted"] == 1
    assert result["counts"]["EntityEvidence"]["inserted"] == 1
    assert result["counts"]["MediaEntityCandidate"]["inserted"] == 4
    assert db.query(ProviderCache).count() == 1
    assert db.query(EntityEvidence).count() == 1
    assert db.query(MediaEntityCandidate).count() == 4
    assert db.query(MediaEntityAssignment).count() == 0
    assert db.query(Entity).count() == 0


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
    plan = _invalid_approved_plan_missing_query_hash()

    with pytest.raises(EvidencePersistenceError, match="persistence_plan_validation_failed"):
        persist_provider_evidence_plans(db, [plan], apply=True)

    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0


def test_dry_run_with_invalid_approved_plan_reports_blocked_without_writes(db):
    result = persist_provider_evidence_plans(
        db,
        [_plan_2687(), _invalid_approved_plan_missing_query_hash()],
        apply=False,
        options=EvidencePersistenceOptions(strict=False),
    )

    assert result["success"] is False
    assert any(item["media_id"] == 2670 and item["status"] == "blocked" for item in result["items"])
    assert any(item["media_id"] == 2687 and item["status"] == "planned" for item in result["items"])
    assert result["counts"]["ProviderCache"]["inserted"] == 0
    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0


def test_apply_with_one_invalid_approved_plan_fails_closed_even_without_strict(db):
    with pytest.raises(EvidencePersistenceError, match="persistence_plan_validation_failed"):
        persist_provider_evidence_plans(
            db,
            [_plan_2687(), _invalid_approved_plan_missing_query_hash()],
            apply=True,
            options=EvidencePersistenceOptions(strict=False),
        )

    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


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


def test_confirmed_assignment_detected_fails_closed_and_rolls_back_c1_writes(db):
    entity = Entity(type=EntityTypeEnum.character, canonical_name="existing", normalized_key="existing")
    db.add(entity)
    db.flush()
    db.add(
        MediaEntityAssignment(
            media_id=2687,
            entity_id=entity.id,
            role=MediaEntityRoleEnum.character,
            review_status=EntityReviewStatusEnum.confirmed,
            source=EntityMetadataSourceEnum.manual,
        )
    )
    db.commit()

    with pytest.raises(EvidencePersistenceError, match="confirmed_assignment_detected"):
        persist_provider_evidence_plans(db, [_plan_2687(), _plan_2670()], apply=True)

    assert db.query(MediaEntityAssignment).count() == 1
    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


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


def test_collect_db_state_counts_only_exact_c1_evidence_rows(db):
    plan = _plan_2687()
    unrelated = EntityEvidence(
        provider="saucenao",
        source_type="external",
        evidence_type=EntityEvidenceTypeEnum.reverse_search,
        media_id=2687,
        query_hash=plan.provider_query.query_hash,
        payload_ref="external:historical:unrelated",
        score=plan.source_match.score_value,
        summary="Historical reverse-search evidence outside C1.",
        privacy_redacted=True,
    )
    db.add(unrelated)
    db.flush()
    db.add(
        MediaEntityCandidate(
            media_id=2687,
            entity_id=None,
            entity_type=EntityTypeEnum.character,
            label="historical",
            candidate_name="historical unrelated candidate",
            score=plan.source_match.score_value,
            status=EntityCandidateStatusEnum.suggested,
            generator=EntityCandidateGeneratorEnum.external,
            evidence_id=unrelated.id,
        )
    )
    db.flush()

    state_before_c1 = runner.collect_db_state(db, plans=[plan], low_confidence_query_hashes=[])

    assert state_before_c1["entity_evidence_approved"] == 0
    assert state_before_c1["entity_evidence_unrelated_existing_ignored"] == 1
    assert state_before_c1["media_entity_candidates_c1"] == 0
    assert state_before_c1["media_entity_candidates_unrelated_existing_ignored"] == 1

    persist_provider_evidence_plans(db, [plan], apply=True)
    state_after_c1 = runner.collect_db_state(db, plans=[plan], low_confidence_query_hashes=[])

    assert state_after_c1["provider_cache_approved"] == 1
    assert state_after_c1["entity_evidence_approved"] == 1
    assert state_after_c1["entity_evidence_unrelated_existing_ignored"] == 1
    assert state_after_c1["media_entity_candidates_c1"] == 4
    assert state_after_c1["media_entity_candidates_unrelated_existing_ignored"] == 1


def test_duplicate_c1_evidence_rows_are_detected_by_approved_verification(db):
    plans = [_plan_2687(), _plan_2670()]
    persist_provider_evidence_plans(db, plans, apply=True)
    duplicate = EntityEvidence(
        provider="saucenao",
        source_type="external",
        evidence_type=EntityEvidenceTypeEnum.reverse_search,
        media_id=2687,
        entity_id=None,
        tag_id=None,
        query_hash=plans[0].provider_query.query_hash,
        payload_ref=provider_cache_payload_ref(plans[0]),
        score=plans[0].source_match.score_value,
        summary=evidence_summary(plans[0]),
        privacy_redacted=True,
    )
    db.add(duplicate)
    db.flush()

    before = _state()
    after = runner.collect_db_state(db, plans=plans, low_confidence_query_hashes=[])
    verification = runner.build_post_write_verification(before, after, plans)

    assert after["entity_evidence_approved"] == 3
    assert verification["success"] is False
    assert "entity_evidence_count_matches_expected" in verification["failure_codes"]


def test_low_confidence_preexisting_rows_do_not_count_as_c1_writes():
    before = _state(low_evidence=2, low_candidates=3)
    after = _state(low_evidence=2, low_candidates=3)

    verification = runner.build_post_write_verification(before, after, [_plan_2687(), _plan_2670()])

    assert verification["success"] is True
    assert verification["low_confidence_positive_evidence_inserted_by_c1"] == 0
    assert verification["low_confidence_candidates_inserted_by_c1"] == 0


def test_low_confidence_positive_delta_blocks_c1_verification():
    before = _state(low_evidence=2, low_candidates=3)
    after = _state(low_evidence=3, low_candidates=4)

    verification = runner.build_post_write_verification(before, after, [_plan_2687(), _plan_2670()])

    assert verification["success"] is False
    assert "low_confidence_positive_evidence_inserted_by_c1_zero" in verification["failure_codes"]
    assert "low_confidence_candidates_inserted_by_c1_zero" in verification["failure_codes"]


def test_apply_pre_commit_verification_failure_rolls_back_new_rows(db, monkeypatch):
    plans = [_plan_2687(), _plan_2670()]
    before = runner.collect_db_state(db, plans=plans, low_confidence_query_hashes=[])

    def fail_verification(*_args, **_kwargs):
        return {"success": False, "failure_codes": ["entity_evidence_count_matches_expected"]}

    monkeypatch.setattr(runner, "build_post_write_verification", fail_verification)

    with pytest.raises(runner.PhaseC1Error, match="post_write_verification_failed"):
        runner.apply_plans_with_pre_commit_gates(
            db,
            plans=plans,
            db_before=before,
            low_confidence_query_hashes=[],
        )
    db.rollback()

    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


def test_apply_pre_commit_verification_success_can_commit(db):
    plans = [_plan_2687(), _plan_2670()]
    before = runner.collect_db_state(db, plans=plans, low_confidence_query_hashes=[])

    result = runner.apply_plans_with_pre_commit_gates(
        db,
        plans=plans,
        db_before=before,
        low_confidence_query_hashes=[],
    )
    db.commit()

    assert result["post_write_verification"]["success"] is True
    assert result["idempotency_summary"]["counts"]["ProviderCache"]["inserted"] == 0
    assert db.query(ProviderCache).count() == 2
    assert db.query(EntityEvidence).count() == 2
    assert db.query(MediaEntityCandidate).count() == 7


def test_apply_pre_commit_idempotency_failure_rolls_back_new_rows(db, monkeypatch):
    plans = [_plan_2687(), _plan_2670()]
    before = runner.collect_db_state(db, plans=plans, low_confidence_query_hashes=[])

    def fail_idempotency(*_args, **_kwargs):
        return {"success": False, "failure_codes": ["ProviderCache_would_insert"]}

    monkeypatch.setattr(runner, "build_idempotency_verification", fail_idempotency)

    with pytest.raises(runner.PhaseC1Error, match="idempotency_verification_failed"):
        runner.apply_plans_with_pre_commit_gates(
            db,
            plans=plans,
            db_before=before,
            low_confidence_query_hashes=[],
        )
    db.rollback()

    assert db.query(ProviderCache).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


def test_public_summary_success_requires_post_write_verification_success():
    summary = build_public_summary(
        mode="apply",
        identity=_identity(),
        plans=[_plan_2687(), _plan_2670()],
        persistence=_successful_persistence(),
        db_before=_state(tag_translation_count=0),
        db_after=_state(tag_translation_count=1),
        backup=None,
        idempotency_summary=_successful_idempotency(),
    )

    assert summary["success"] is False
    assert summary["status"] == "blocked"
    assert "tag_translation_count_unchanged" in summary["blocked_reasons"]
    assert summary["safety_confirmation"]["tag_translation_mutated"] is True


def test_public_summary_blocks_confirmed_assignment_and_media_tag_deltas():
    summary = build_public_summary(
        mode="apply",
        identity=_identity(),
        plans=[_plan_2687(), _plan_2670()],
        persistence=_successful_persistence(),
        db_before=_state(media_tags=92),
        db_after=_state(assignments=1, media_tags=93),
        backup=None,
        idempotency_summary=_successful_idempotency(),
    )

    assert summary["success"] is False
    assert "confirmed_assignment_count_is_zero" in summary["blocked_reasons"]
    assert "media_tags_for_approved_unchanged" in summary["blocked_reasons"]
    assert summary["safety_confirmation"]["confirmed_assignment_created"] is True
    assert summary["safety_confirmation"]["media_tags_mutated"] is True


def test_public_summary_success_requires_idempotency_verification_success():
    bad_idempotency = _successful_idempotency()
    bad_idempotency["counts"]["ProviderCache"]["inserted"] = 1
    bad_idempotency["counts"]["ProviderCache"]["existing"] = 1

    summary = build_public_summary(
        mode="apply",
        identity=_identity(),
        plans=[_plan_2687(), _plan_2670()],
        persistence=_successful_persistence(),
        db_before=_state(),
        db_after=_state(),
        backup=None,
        idempotency_summary=bad_idempotency,
    )

    assert summary["success"] is False
    assert "ProviderCache_would_insert" in summary["blocked_reasons"]
    assert "ProviderCache_existing_count_mismatch" in summary["blocked_reasons"]


def test_idempotency_verification_accepts_zero_insert_existing_rows():
    result = build_idempotency_verification(_successful_idempotency(), [_plan_2687(), _plan_2670()])

    assert result["success"] is True
    assert result["idempotency_check_ran"] is True
    assert result["idempotency_success"] is True
    assert result["failure_codes"] == []
    assert result["would_insert_provider_cache"] == 0
    assert result["would_insert_entity_evidence"] == 0
    assert result["would_insert_media_entity_candidate"] == 0
    assert result["existing_provider_cache"] == 2
    assert result["existing_entity_evidence"] == 2
    assert result["existing_media_entity_candidate"] == 7


def test_public_summary_success_when_all_safety_checks_pass():
    summary = build_public_summary(
        mode="apply",
        identity=_identity(),
        plans=[_plan_2687(), _plan_2670()],
        persistence=_successful_persistence(),
        db_before=_state(),
        db_after=_state(),
        backup=None,
        idempotency_summary=_successful_idempotency(),
    )

    assert summary["success"] is True
    assert summary["post_write_verification"]["success"] is True
    assert summary["idempotency_verification"]["success"] is True


def test_runner_apply_aborts_before_backup_when_dry_run_blocks(monkeypatch):
    fake_engine = _FakeEngine()
    fake_session_local = _FakeSessionLocal()
    calls = []

    def fake_persist(_db, _plans, *, apply, options):
        calls.append({"apply": apply, "strict": options.strict})
        assert apply is False
        return {
            **_successful_persistence(),
            "success": False,
            "items": [{"media_id": 2670, "status": "blocked", "blocked_reason": "missing_query_hash"}],
            "counts": {
                "ProviderCache": {"planned": 1, "inserted": 0, "existing": 0, "skipped": 0},
                "EntityEvidence": {"planned": 1, "inserted": 0, "existing": 0, "skipped": 0},
                "MediaEntityCandidate": {"planned": 4, "inserted": 0, "existing": 0, "skipped": 0},
                "MediaEntityAssignment": {"inserted": 0},
                "Entity": {"inserted": 0},
            },
        }

    monkeypatch.setattr(runner, "load_json", lambda _path: {})
    monkeypatch.setattr(runner, "validate_local_artifact_flags", lambda *_args: None)
    monkeypatch.setattr(runner, "build_phase44c1_plans", lambda **_kwargs: [_plan_2687(), _invalid_approved_plan_missing_query_hash()])
    monkeypatch.setattr(runner, "load_settings_and_engine", lambda: (object(), fake_engine, _identity()))
    monkeypatch.setattr(runner, "sessionmaker", lambda bind: fake_session_local)
    monkeypatch.setattr(runner, "low_confidence_query_hashes", lambda _details: [])
    monkeypatch.setattr(runner, "collect_db_state", lambda *_args, **_kwargs: _state(provider_cache=0, evidence=0, candidates=0))
    monkeypatch.setattr(runner, "ensure_media_rows_present", lambda *_args: None)
    monkeypatch.setattr(runner, "persist_provider_evidence_plans", fake_persist)
    monkeypatch.setattr(runner, "create_pg_dump_backup", lambda *_args: pytest.fail("backup must not run before valid dry-run"))
    monkeypatch.setattr(runner, "write_json", lambda *_args: None)
    monkeypatch.setattr(runner, "write_text", lambda *_args: None)

    exit_code = runner.main(["--apply"])

    assert exit_code == 2
    assert calls == [{"apply": False, "strict": False}]
    assert fake_session_local.session.committed == 0


def test_runner_apply_runs_post_apply_idempotency_check(monkeypatch):
    fake_engine = _FakeEngine()
    fake_session_local = _FakeSessionLocal()
    calls = []

    def fake_persist(_db, _plans, *, apply, options):
        calls.append({"apply": apply, "strict": options.strict})
        if apply:
            return _successful_persistence()
        if len(calls) == 1:
            return _successful_persistence()
        return _successful_idempotency()

    monkeypatch.setattr(runner, "load_json", lambda _path: {})
    monkeypatch.setattr(runner, "validate_local_artifact_flags", lambda *_args: None)
    monkeypatch.setattr(runner, "build_phase44c1_plans", lambda **_kwargs: [_plan_2687(), _plan_2670()])
    monkeypatch.setattr(runner, "load_settings_and_engine", lambda: (object(), fake_engine, _identity()))
    monkeypatch.setattr(runner, "sessionmaker", lambda bind: fake_session_local)
    monkeypatch.setattr(runner, "low_confidence_query_hashes", lambda _details: [])
    monkeypatch.setattr(runner, "collect_db_state", lambda *_args, **_kwargs: _state())
    monkeypatch.setattr(runner, "ensure_media_rows_present", lambda *_args: None)
    monkeypatch.setattr(runner, "persist_provider_evidence_plans", fake_persist)
    monkeypatch.setattr(runner, "create_pg_dump_backup", lambda *_args: {"basename": "backup.dump", "bytes": 1, "format": "pg_dump -Fc", "toc_verified": True})
    monkeypatch.setattr(runner, "write_json", lambda *_args: None)
    monkeypatch.setattr(runner, "write_text", lambda *_args: None)

    exit_code = runner.main(["--apply"])

    assert exit_code == 0
    assert calls == [
        {"apply": False, "strict": False},
        {"apply": True, "strict": True},
        {"apply": False, "strict": True},
    ]
    assert fake_session_local.session.committed == 1


def test_runner_apply_writes_audit_artifacts_before_commit(monkeypatch):
    fake_engine = _FakeEngine()
    fake_session_local = _FakeSessionLocal()
    calls = []
    events = []

    original_commit = fake_session_local.session.commit

    def record_commit():
        events.append("commit")
        original_commit()

    def fake_persist(_db, _plans, *, apply, options):
        calls.append(apply)
        if apply:
            return _successful_persistence()
        if len(calls) == 1:
            return _successful_persistence()
        return _successful_idempotency()

    def fake_write_json(*_args):
        assert fake_session_local.session.committed == 0
        events.append("write_json")

    def fake_write_text(*_args):
        assert fake_session_local.session.committed == 0
        events.append("write_text")

    fake_session_local.session.commit = record_commit
    monkeypatch.setattr(runner, "load_json", lambda _path: {})
    monkeypatch.setattr(runner, "validate_local_artifact_flags", lambda *_args: None)
    monkeypatch.setattr(runner, "build_phase44c1_plans", lambda **_kwargs: [_plan_2687(), _plan_2670()])
    monkeypatch.setattr(runner, "load_settings_and_engine", lambda: (object(), fake_engine, _identity()))
    monkeypatch.setattr(runner, "sessionmaker", lambda bind: fake_session_local)
    monkeypatch.setattr(runner, "low_confidence_query_hashes", lambda _details: [])
    monkeypatch.setattr(runner, "collect_db_state", lambda *_args, **_kwargs: _state())
    monkeypatch.setattr(runner, "ensure_media_rows_present", lambda *_args: None)
    monkeypatch.setattr(runner, "persist_provider_evidence_plans", fake_persist)
    monkeypatch.setattr(
        runner,
        "create_pg_dump_backup",
        lambda *_args: {"basename": "backup.dump", "bytes": 1, "format": "pg_dump -Fc", "toc_verified": True},
    )
    monkeypatch.setattr(runner, "write_json", fake_write_json)
    monkeypatch.setattr(runner, "write_text", fake_write_text)

    exit_code = runner.main(["--apply"])

    assert exit_code == 0
    assert events == ["write_json", "write_text", "write_json", "commit"]
    assert fake_session_local.session.committed == 1


def test_runner_apply_audit_write_failure_rolls_back_before_commit(monkeypatch):
    fake_engine = _FakeEngine()
    fake_session_local = _FakeSessionLocal()

    def fake_persist(_db, _plans, *, apply, options):
        if apply:
            return _successful_persistence()
        return _successful_idempotency()

    def fail_write_json(*_args):
        raise OSError("audit output unavailable")

    monkeypatch.setattr(runner, "load_json", lambda _path: {})
    monkeypatch.setattr(runner, "validate_local_artifact_flags", lambda *_args: None)
    monkeypatch.setattr(runner, "build_phase44c1_plans", lambda **_kwargs: [_plan_2687(), _plan_2670()])
    monkeypatch.setattr(runner, "load_settings_and_engine", lambda: (object(), fake_engine, _identity()))
    monkeypatch.setattr(runner, "sessionmaker", lambda bind: fake_session_local)
    monkeypatch.setattr(runner, "low_confidence_query_hashes", lambda _details: [])
    monkeypatch.setattr(runner, "collect_db_state", lambda *_args, **_kwargs: _state())
    monkeypatch.setattr(runner, "ensure_media_rows_present", lambda *_args: None)
    monkeypatch.setattr(runner, "persist_provider_evidence_plans", fake_persist)
    monkeypatch.setattr(
        runner,
        "create_pg_dump_backup",
        lambda *_args: {"basename": "backup.dump", "bytes": 1, "format": "pg_dump -Fc", "toc_verified": True},
    )
    monkeypatch.setattr(runner, "write_json", fail_write_json)
    monkeypatch.setattr(runner, "write_text", lambda *_args: None)

    exit_code = runner.main(["--apply"])

    assert exit_code == 2
    assert fake_session_local.session.committed == 0
    assert fake_session_local.session.rolled_back == 1
