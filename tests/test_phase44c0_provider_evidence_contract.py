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
from app.services.saucenao_evidence_mapper import map_saucenao_result_to_plan


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
        "query_hash": f"query-hash-{media_id}",
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
        assert_public_payload_safe({"note": "C:\\Users\\kyloris\\private.jpg"})
    with pytest.raises(ValueError):
        assert_public_payload_safe({"raw_image_bytes": "abcd"})


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
        query_hash="example-query-hash",
        request_shape_redacted={"media_ref": "approved_media_id:999", "local_path_included": False},
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
