import json

import pytest

from app.services.provider_evidence_contract import (
    EvidencePersistencePlan,
    EvidenceStrength,
    ExtractedProviderMetadata,
    LocalizationStatus,
    ManualValidationStatus,
    PlannedEntityCandidate,
    ProviderQuery,
    SourceMatch,
    SourceMatchClass,
    assert_public_payload_safe,
)
from app.services.provider_evidence_schema_fit import audit_provider_evidence_contract_fit
from app.services.saucenao_evidence_mapper import map_saucenao_result_to_plan, map_saucenao_samples_to_plans


def _valid_query_hash(media_id: int) -> str:
    return f"{media_id:064x}"


def _live_item(
    media_id: int,
    *,
    result_class: str,
    score: float,
    minimum_similarity: float,
    result_id: int | None,
    host: str | None,
    creator: str | None,
    title: str | None,
) -> dict:
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
                "top_result": {
                    "similarity": score,
                    "index_id": 9,
                    "index_name": "Index #9: Danbooru - provider-returned-file.jpg",
                    "result_id": result_id,
                    "source_url_host": host,
                    "source_url_present": bool(host),
                    "creator": creator,
                    "title": title,
                },
            },
        },
    }


def _manual(media_id: int, *, action: str, judgment: str) -> dict:
    return {
        "media_id": media_id,
        "recommended_action": action,
        "judgment": judgment,
    }


def _metadata(
    media_id: int,
    *,
    artist: str,
    works: list[str],
    characters: list[str],
    result_id: int,
) -> dict:
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


def _plan_2687():
    return map_saucenao_result_to_plan(
        live_item=_live_item(
            2687,
            result_class="high_confidence_match",
            score=96.2,
            minimum_similarity=52.0,
            result_id=7695035,
            host="danbooru.donmai.us",
            creator="yunkaiming",
            title="honkai: star rail, honkai (series)",
        ),
        manual_item=_manual(2687, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item=_metadata(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )


def _plan_2670():
    return map_saucenao_result_to_plan(
        live_item=_live_item(
            2670,
            result_class="high_confidence_match",
            score=91.96,
            minimum_similarity=37.66,
            result_id=9366672,
            host="danbooru.donmai.us",
            creator="songchuan li",
            title="blue archive",
        ),
        manual_item=_manual(2670, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item=_metadata(
            2670,
            artist="songchuan li",
            works=["blue archive"],
            characters=["kisaki (blue archive)"],
            result_id=9366672,
        ),
    )


@pytest.mark.parametrize("plan_factory", [_plan_2687, _plan_2670])
def test_saucenao_high_confidence_validated_result_maps_to_strong_source_match(plan_factory):
    plan = plan_factory()

    assert plan.source_match.match_class == SourceMatchClass.exact_or_near_exact
    assert plan.source_match.evidence_strength == EvidenceStrength.strong
    assert plan.source_match.manual_validation_status == ManualValidationStatus.validated_correct
    assert plan.entity_evidence_planned is True
    assert plan.media_entity_candidate_planned is True
    assert plan.provider_cache_planned is True
    assert plan.provider_cache_persistence_allowed is True
    assert plan.provider_query.query_hash_status == "present_valid"
    assert plan.provider_query.request_shape_status == "present"
    assert plan.provider_provenance_status == "ready"
    assert plan.non_persistable_source_match is False
    assert plan.source_match.source_identifier_status == "present"


def test_2687_preserves_character_work_artist_metadata():
    plan = _plan_2687()

    assert plan.extracted_metadata.artist_raw == ("yunkaiming",)
    assert plan.extracted_metadata.work_raw == ("honkai: star rail", "honkai (series)")
    assert plan.extracted_metadata.character_raw == ("acheron (honkai: star rail)",)
    assert {row.entity_type for row in plan.planned_entity_candidates} == {"artist", "work", "character"}


def test_2670_preserves_character_work_artist_metadata():
    plan = _plan_2670()

    assert plan.extracted_metadata.artist_raw == ("songchuan li",)
    assert plan.extracted_metadata.work_raw == ("blue archive",)
    assert plan.extracted_metadata.character_raw == ("kisaki (blue archive)",)
    assert plan.source_match.post_url == "https://danbooru.donmai.us/posts/9366672"
    assert plan.source_match.provider_result_id == "9366672"


@pytest.mark.parametrize(
    ("media_id", "score", "minimum_similarity", "judgment"),
    [
        (2690, 34.63, 35.63, "invalid_completely_unrelated"),
        (2654, 52.3, 52.0, "wrong_unrelated"),
        (2647, 50.7, 51.7, "wrong_unrelated"),
    ],
)
def test_low_confidence_manually_invalid_samples_map_to_discard(media_id, score, minimum_similarity, judgment):
    plan = map_saucenao_result_to_plan(
        live_item=_live_item(
            media_id,
            result_class="low_confidence_match",
            score=score,
            minimum_similarity=minimum_similarity,
            result_id=None,
            host="www.pixiv.net",
            creator="discarded artist",
            title="discarded title",
        ),
        manual_item=_manual(media_id, action="discard", judgment=judgment),
        metadata_item={"media_id": media_id, "metadata_extraction_status": "parser_missing_discarded_low_confidence_not_requeried"},
    )

    assert plan.source_match.match_class == SourceMatchClass.discarded
    assert plan.source_match.evidence_strength == EvidenceStrength.discard
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False
    assert plan.negative_lookup_cache_planned is True
    assert plan.planned_entity_candidates == ()


def test_minimum_similarity_or_high_score_alone_does_not_cause_acceptance():
    plan = map_saucenao_result_to_plan(
        live_item=_live_item(
            3000,
            result_class="high_confidence_match",
            score=98.0,
            minimum_similarity=98.0,
            result_id=123,
            host="danbooru.donmai.us",
            creator="candidate artist",
            title="candidate work",
        ),
        manual_item=_manual(3000, action="", judgment=""),
        metadata_item={"media_id": 3000, "artist": "candidate artist", "work_or_copyright": ["candidate work"]},
    )

    assert plan.source_match.evidence_strength == EvidenceStrength.weak
    assert plan.source_match.match_class == SourceMatchClass.low_confidence
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False


def test_public_report_row_without_query_hash_does_not_fabricate_cache_key():
    live_item = _live_item(
        2687,
        result_class="high_confidence_match",
        score=96.2,
        minimum_similarity=52.0,
        result_id=7695035,
        host="danbooru.donmai.us",
        creator="yunkaiming",
        title="honkai: star rail, honkai (series)",
    )
    live_item.pop("query_hash")
    live_item.pop("request_shape_redacted")

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(2687, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item=_metadata(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )
    payload_text = json.dumps(plan.to_public_dict(), ensure_ascii=False, sort_keys=True)

    assert plan.provider_query.query_hash is None
    assert plan.provider_query.query_hash_status == "missing"
    assert plan.provider_query.request_shape_status == "missing"
    assert plan.provider_cache_planned is False
    assert plan.provider_cache_persistence_allowed is False
    assert plan.provider_provenance_status == "blocked"
    assert plan.persistence_blocked_reason == "missing_query_hash"
    assert "missing_request_shape" in plan.persistence_blocked_reasons
    assert "missing_provider_provenance" in plan.persistence_blocked_reasons
    assert "missing-query-hash" not in payload_text
    assert plan.source_match.evidence_strength == EvidenceStrength.strong
    assert plan.non_persistable_source_match is True
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False
    assert plan.planned_entity_candidates == ()


def test_local_details_row_with_real_query_hash_allows_provider_cache_plan():
    plan = _plan_2687()

    assert plan.provider_query.query_hash == _valid_query_hash(2687)
    assert plan.provider_query.query_hash_status == "present_valid"
    assert plan.provider_query.request_shape_status == "present"
    assert plan.provider_provenance_status == "ready"
    assert plan.provider_cache_planned is True
    assert plan.provider_cache_persistence_allowed is True
    assert plan.persistence_blocked_reason is None


def test_request_shape_missing_blocks_provider_cache_plan_even_with_query_hash():
    live_item = _live_item(
        2687,
        result_class="high_confidence_match",
        score=96.2,
        minimum_similarity=52.0,
        result_id=7695035,
        host="danbooru.donmai.us",
        creator="yunkaiming",
        title="honkai: star rail, honkai (series)",
    )
    live_item.pop("request_shape_redacted")

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(2687, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item=_metadata(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )

    assert plan.provider_query.query_hash_status == "present_valid"
    assert plan.provider_query.request_shape_status == "missing"
    assert plan.provider_cache_planned is False
    assert plan.provider_cache_persistence_allowed is False
    assert plan.provider_provenance_status == "blocked"
    assert plan.persistence_blocked_reason == "missing_request_shape"
    assert "missing_provider_provenance" in plan.persistence_blocked_reasons
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False


def test_unsafe_request_shape_blocks_positive_persistence_plan_and_public_output():
    live_item = _live_item(
        2687,
        result_class="high_confidence_match",
        score=96.2,
        minimum_similarity=52.0,
        result_id=7695035,
        host="danbooru.donmai.us",
        creator="yunkaiming",
        title="honkai: star rail, honkai (series)",
    )
    live_item["request_shape_redacted"]["originalFilename"] = "private.jpg"

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(2687, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item=_metadata(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )

    assert plan.provider_query.request_shape_status == "invalid"
    assert plan.provider_query.request_shape_redacted == {}
    assert plan.provider_cache_planned is False
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False
    assert plan.provider_provenance_status == "blocked"
    assert "invalid_request_shape" in plan.persistence_blocked_reasons
    assert_public_payload_safe(plan.to_public_dict())


def test_non_json_request_shape_blocks_persistence_without_crashing():
    live_item = _live_item(
        2687,
        result_class="high_confidence_match",
        score=96.2,
        minimum_similarity=52.0,
        result_id=7695035,
        host="danbooru.donmai.us",
        creator="yunkaiming",
        title="honkai: star rail, honkai (series)",
    )
    live_item["request_shape_redacted"]["derived_bytes"] = b"private-image-bytes"

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(2687, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item=_metadata(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )

    assert plan.provider_query.request_shape_status == "invalid"
    assert plan.provider_query.request_shape_redacted == {}
    assert plan.provider_cache_planned is False
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False
    assert "invalid_request_shape" in plan.persistence_blocked_reasons
    assert_public_payload_safe(plan.to_public_dict())


def test_public_summary_row_without_local_details_has_no_positive_persistence_plan():
    live_item = _live_item(
        2687,
        result_class="high_confidence_match",
        score=96.2,
        minimum_similarity=52.0,
        result_id=7695035,
        host="danbooru.donmai.us",
        creator="yunkaiming",
        title="honkai: star rail, honkai (series)",
    )
    live_item.pop("query_hash")
    live_item.pop("request_shape_redacted")

    plans = map_saucenao_samples_to_plans(
        live_details={"provider_result_items": [live_item]},
        manual_validation_summary={
            "manual_validation": {
                "items": [_manual(2687, action="keep_as_strong_evidence", judgment="correct")],
            },
            "metadata_extraction_audit": {
                "items": [
                    _metadata(
                        2687,
                        artist="yunkaiming",
                        works=["honkai: star rail", "honkai (series)"],
                        characters=["acheron (honkai: star rail)"],
                        result_id=7695035,
                    )
                ],
            },
        },
    )

    plan = plans[0]
    assert plan.source_match.evidence_strength == EvidenceStrength.strong
    assert plan.non_persistable_source_match is True
    assert plan.provider_cache_planned is False
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False
    assert plan.provider_provenance_status == "blocked"


@pytest.mark.parametrize(
    "unsafe_source_url",
    [
        "https://example.invalid/post?src=C:\\Users\\kyloris\\private.jpg",
        "file://C:/Users/kyloris/private.jpg",
    ],
)
def test_unsafe_source_url_blocks_positive_persistence_plan(unsafe_source_url):
    live_item = _live_item(
        4300,
        result_class="high_confidence_match",
        score=96.0,
        minimum_similarity=50.0,
        result_id=None,
        host=None,
        creator="candidate artist",
        title="candidate work",
    )
    top_result = live_item["provider_result"]["normalized_payload"]["top_result"]
    top_result["source_url"] = unsafe_source_url

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(4300, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item={"media_id": 4300, "artist": "candidate artist", "work_or_copyright": ["candidate work"]},
    )

    assert plan.source_match.source_identifier_status == "not_public_safe"
    assert plan.source_match.evidence_strength != EvidenceStrength.strong
    assert plan.non_persistable_source_match is True
    assert plan.provider_cache_planned is False
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False
    assert "source_identifier_not_public_safe" in plan.persistence_blocked_reasons
    assert_public_payload_safe(plan.to_public_dict())


def test_secret_like_source_url_blocks_positive_persistence_plan():
    live_item = _live_item(
        4301,
        result_class="high_confidence_match",
        score=96.0,
        minimum_similarity=50.0,
        result_id=None,
        host=None,
        creator="candidate artist",
        title="candidate work",
    )
    top_result = live_item["provider_result"]["normalized_payload"]["top_result"]
    top_result["source_url"] = "https://example.invalid/post?api_key=secret"

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(4301, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item={"media_id": 4301, "artist": "candidate artist", "work_or_copyright": ["candidate work"]},
    )

    assert plan.source_match.source_identifier_status == "not_public_safe"
    assert plan.non_persistable_source_match is True
    assert plan.provider_cache_planned is False
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False
    assert "source_identifier_not_public_safe" in plan.persistence_blocked_reasons
    assert_public_payload_safe(plan.to_public_dict())


def test_valid_sha256_prefixed_query_hash_allows_provider_cache_plan():
    live_item = _live_item(
        2687,
        result_class="high_confidence_match",
        score=96.2,
        minimum_similarity=52.0,
        result_id=7695035,
        host="danbooru.donmai.us",
        creator="yunkaiming",
        title="honkai: star rail, honkai (series)",
    )
    live_item["query_hash"] = "sha256:" + ("a" * 64)

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(2687, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item=_metadata(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )

    assert plan.provider_query.query_hash == "sha256:" + ("a" * 64)
    assert plan.provider_query.query_hash_status == "present_valid"
    assert plan.provider_cache_planned is True


@pytest.mark.parametrize(
    ("query_hash", "status"),
    [
        ("missing-query-hash-2687", "placeholder"),
        ("placeholder", "placeholder"),
        ("unknown", "placeholder"),
        ("1234", "invalid"),
        ("a" * 63, "invalid"),
    ],
)
def test_invalid_or_placeholder_query_hash_blocks_positive_persistence_plan(query_hash, status):
    live_item = _live_item(
        2687,
        result_class="high_confidence_match",
        score=96.2,
        minimum_similarity=52.0,
        result_id=7695035,
        host="danbooru.donmai.us",
        creator="yunkaiming",
        title="honkai: star rail, honkai (series)",
    )
    live_item["query_hash"] = query_hash

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(2687, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item=_metadata(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )

    assert plan.provider_query.query_hash is None
    assert plan.provider_query.query_hash_status == status
    assert plan.provider_cache_planned is False
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False
    assert plan.provider_provenance_status == "blocked"
    assert plan.persistence_blocked_reason == "invalid_query_hash"
    assert "missing_provider_provenance" in plan.persistence_blocked_reasons


def test_high_confidence_validated_without_source_identifier_is_not_strong():
    plan = map_saucenao_result_to_plan(
        live_item=_live_item(
            4000,
            result_class="high_confidence_match",
            score=96.0,
            minimum_similarity=50.0,
            result_id=None,
            host=None,
            creator="candidate artist",
            title="candidate work",
        ),
        manual_item=_manual(4000, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item={"media_id": 4000, "artist": "candidate artist", "work_or_copyright": ["candidate work"]},
    )

    assert plan.source_match.source_identifier_status == "missing"
    assert plan.source_match.evidence_strength != EvidenceStrength.strong
    assert plan.source_match.match_class == SourceMatchClass.low_confidence
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False
    assert plan.persistence_blocked_reason == "missing_source_identifier"


def test_low_confidence_with_source_identifier_still_discards_by_policy():
    plan = map_saucenao_result_to_plan(
        live_item=_live_item(
            2690,
            result_class="low_confidence_match",
            score=34.63,
            minimum_similarity=35.63,
            result_id=12345,
            host="deviantart.com",
            creator="discarded artist",
            title="discarded title",
        ),
        manual_item=_manual(2690, action="discard", judgment="invalid_completely_unrelated"),
        metadata_item={"media_id": 2690, "result_id": 12345, "source_url_host": "deviantart.com"},
    )

    assert plan.source_match.source_identifier_status == "present"
    assert plan.source_match.match_class == SourceMatchClass.discarded
    assert plan.source_match.evidence_strength == EvidenceStrength.discard
    assert plan.entity_evidence_planned is False


def test_strong_evidence_plan_includes_source_identifier():
    plan = _plan_2687()

    assert plan.source_match.source_identifier_status == "present"
    assert plan.source_match.provider_result_id == "7695035"
    assert plan.source_match.source_host == "danbooru.donmai.us"
    assert plan.source_match.post_url == "https://danbooru.donmai.us/posts/7695035"


def test_provider_external_id_can_anchor_traceable_strong_evidence():
    live_item = _live_item(
        4100,
        result_class="high_confidence_match",
        score=94.0,
        minimum_similarity=50.0,
        result_id=None,
        host="example.invalid",
        creator="candidate artist",
        title="candidate work",
    )
    live_item["provider_result"]["normalized_payload"]["top_result"]["provider_external_id"] = "external-4100"

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(4100, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item={"media_id": 4100, "artist": "candidate artist", "work_or_copyright": ["candidate work"]},
    )

    assert plan.source_match.source_identifier_status == "present"
    assert plan.source_match.provider_result_id == "external-4100"
    assert plan.source_match.evidence_strength == EvidenceStrength.strong
    assert plan.entity_evidence_planned is True


def test_discard_action_dominates_conflicting_correct_judgment():
    plan = map_saucenao_result_to_plan(
        live_item=_live_item(
            4200,
            result_class="high_confidence_match",
            score=96.0,
            minimum_similarity=50.0,
            result_id=123456,
            host="danbooru.donmai.us",
            creator="candidate artist",
            title="candidate work",
        ),
        manual_item=_manual(4200, action="discard", judgment="correct"),
        metadata_item={"media_id": 4200, "artist": "candidate artist", "work_or_copyright": ["candidate work"]},
    )

    assert plan.source_match.manual_validation_status == ManualValidationStatus.validated_wrong
    assert plan.source_match.evidence_strength == EvidenceStrength.discard
    assert plan.source_match.match_class == SourceMatchClass.discarded
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False


def test_metadata_not_useful_dominates_correct_judgment():
    manual = _manual(4201, action="keep_as_strong_evidence", judgment="correct")
    manual["metadata_useful"] = "no"

    plan = map_saucenao_result_to_plan(
        live_item=_live_item(
            4201,
            result_class="high_confidence_match",
            score=96.0,
            minimum_similarity=50.0,
            result_id=123457,
            host="danbooru.donmai.us",
            creator="candidate artist",
            title="candidate work",
        ),
        manual_item=manual,
        metadata_item={"media_id": 4201, "artist": "candidate artist", "work_or_copyright": ["candidate work"]},
    )

    assert plan.source_match.manual_validation_status == ManualValidationStatus.validated_wrong
    assert plan.source_match.evidence_strength != EvidenceStrength.strong
    assert plan.entity_evidence_planned is False
    assert plan.media_entity_candidate_planned is False


def test_explicit_keep_without_negative_signals_allows_positive_mapping():
    plan = map_saucenao_result_to_plan(
        live_item=_live_item(
            4202,
            result_class="high_confidence_match",
            score=96.0,
            minimum_similarity=50.0,
            result_id=123458,
            host="danbooru.donmai.us",
            creator="candidate artist",
            title="candidate work",
        ),
        manual_item=_manual(4202, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item={"media_id": 4202, "artist": "candidate artist", "work_or_copyright": ["candidate work"]},
    )

    assert plan.source_match.manual_validation_status == ManualValidationStatus.validated_correct
    assert plan.source_match.evidence_strength == EvidenceStrength.strong
    assert plan.provider_provenance_status == "ready"
    assert plan.entity_evidence_planned is True
    assert plan.media_entity_candidate_planned is True


def test_confirmed_assignment_and_entity_auto_creation_are_blocked():
    plan = _plan_2687()

    assert plan.confirmed_assignment_allowed is False
    assert plan.entity_auto_create_allowed is False
    assert all(candidate.entity_id is None for candidate in plan.planned_entity_candidates)


def test_localization_status_pending_for_extracted_metadata():
    plan = _plan_2670()

    assert plan.extracted_metadata.localization_status == LocalizationStatus.pending
    assert plan.localization_pending is True


def test_provider_score_and_policy_version_are_preserved():
    plan = _plan_2687()

    assert plan.source_match.score_value == 96.2
    assert plan.source_match.score_kind == "saucenao_similarity_percent"
    assert plan.source_match.provider_minimum_similarity == 52.0
    assert plan.source_match.acceptance_policy_version == "phase44c0-manual-validated-source-evidence-v1"
    assert plan.provider_query.provider_policy_version == "phase44b1-derived-saucenao-policy-v1"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_public_payload_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError):
        assert_public_payload_safe({"score_value": value})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"nested": [{"score_value": value}]})


def test_mapper_sanitizes_non_finite_provider_scores():
    live_item = _live_item(
        2687,
        result_class="high_confidence_match",
        score=float("nan"),
        minimum_similarity=52.0,
        result_id=7695035,
        host="danbooru.donmai.us",
        creator="yunkaiming",
        title="honkai: star rail, honkai (series)",
    )

    plan = map_saucenao_result_to_plan(
        live_item=live_item,
        manual_item=_manual(2687, action="keep_as_strong_evidence", judgment="correct"),
        metadata_item=_metadata(
            2687,
            artist="yunkaiming",
            works=["honkai: star rail", "honkai (series)"],
            characters=["acheron (honkai: star rail)"],
            result_id=7695035,
        ),
    )

    assert plan.source_match.score_value is None
    assert_public_payload_safe(plan.to_public_dict())


def test_public_serialization_excludes_private_provider_request_material():
    plan = _plan_2687()
    public_payload = plan.to_public_dict()
    payload_text = json.dumps(public_payload, ensure_ascii=False, sort_keys=True)

    assert "api_key" not in payload_text.lower()
    assert "C:\\" not in payload_text
    assert "\\\\192.168.71.230\\Storage" not in payload_text
    assert "original_filename" not in payload_text.lower()
    assert "raw_image_bytes" not in payload_text.lower()
    assert "provider-returned-file.jpg" not in payload_text

    with pytest.raises(ValueError):
        assert_public_payload_safe({"api_key": "secret"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"saucenao_api_key": "short"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"apiKey": "short"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"api-key": "short"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"password": "short"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"token": "short"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"access_token": "short"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"nested": [{"credential": "short"}]})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"originalFilename": "private.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"original_filename": "private.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"original-filename": "private.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"safeFilename": "private.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"sourceLabel": "icloud"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"source_label": "icloud"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"imageBytes": "abcd"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"image_bytes": "abcd"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"localPath": "C:\\Users\\kyloris\\private.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"filePath": "C:\\Users\\kyloris\\private.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"absolutePath": "C:\\Users\\kyloris\\private.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"rawImageBytes": "abcd"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"nested": [{"safeFilename": "private.jpg"}]})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"url": "https://example.invalid/post?src=C%3A%5CUsers%5Ckyloris%5Cprivate.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"url": "https://example.invalid/post?src=%252Fhome%252Fkyloris%252Fprivate.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"url": "https://example.invalid/post?api%5Fkey%3Dsecret"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"url": "https://example.invalid/post?token%3Dsecret"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"url": "https://example.invalid/post?auth=bearer%20abcd1234efgh5678"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"note": "C:\\Users\\kyloris\\private.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"raw_image_bytes": "abcd"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"payload": b"not-json"})

    assert_public_payload_safe({"url": "https://danbooru.donmai.us/posts/7695035"})

    assert_public_payload_safe(
        {
            "artist": "yunkaiming",
            "character": "acheron (honkai: star rail)",
            "copyright": "honkai: star rail",
            "work": "honkai: star rail",
            "source_host": "danbooru.donmai.us",
            "result_id": "7695035",
            "post_id": "7695035",
        }
    )


def test_schema_fit_audit_is_static_non_mutating():
    audit = audit_provider_evidence_contract_fit()

    assert audit["schema_fit_status"] == "sufficient_with_json_payload"
    assert audit["non_mutating"] is True
    assert audit["db_write_allowed"] is False
    assert all(table_plan["c0_write"] is False for table_plan in audit["per_table_mapping_plan"].values())
    audit["schema_fit_status"] = "mutated"
    assert audit_provider_evidence_contract_fit()["schema_fit_status"] == "sufficient_with_json_payload"


def test_second_provider_placeholder_uses_same_contract_without_db_specific_code():
    query = ProviderQuery(
        provider_key="example_second_provider",
        provider_category="reverse_search",
        media_id=999,
        input_kind="derived_resized_stripped_image",
        query_hash="f" * 64,
        query_hash_status="present_valid",
        request_shape_redacted={"media_ref": "approved_media_id:999", "local_path_included": False},
        request_shape_status="present",
        live_request=False,
        uploaded_input_kind=None,
        provider_policy_version="example-provider-policy-v1",
    )
    source_match = SourceMatch(
        media_id=999,
        provider_key="example_second_provider",
        provider_result_id="post-123",
        provider_index="ExampleIndex",
        source_host="example.invalid",
        source_url=None,
        post_url="https://example.invalid/post-123",
        source_identifier_status="present",
        rank=1,
        score_value=0.87,
        score_kind="provider_native_score",
        provider_minimum_similarity=None,
        match_class=SourceMatchClass.exact_or_near_exact,
        evidence_strength=EvidenceStrength.strong,
        manual_validation_status=ManualValidationStatus.validated_correct,
        acceptance_policy_version="example-acceptance-policy-v1",
    )
    metadata = ExtractedProviderMetadata(
        artist_raw=("artist",),
        work_raw=("work",),
        character_raw=("character",),
        localization_status=LocalizationStatus.pending,
        raw_metadata_available=True,
        parser_status="fixture",
    )
    plan = EvidencePersistencePlan(
        media_id=999,
        provider_query=query,
        source_match=source_match,
        extracted_metadata=metadata,
        provider_provenance_status="ready",
        provider_cache_persistence_allowed=True,
        provider_cache_planned=True,
        entity_evidence_planned=True,
        media_entity_candidate_planned=True,
        planned_entity_candidates=(
            PlannedEntityCandidate("character", "character", "character_raw", EvidenceStrength.strong),
        ),
        db_write_allowed=False,
    )

    public_payload = plan.to_public_dict()
    assert public_payload["provider_query"]["provider_key"] == "example_second_provider"
    assert "ProviderCache" not in json.dumps(public_payload)
    assert public_payload["db_write_allowed"] is False
