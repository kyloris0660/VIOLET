"""Map SauceNAO pilot outputs into the provider-neutral evidence contract."""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .provider_evidence_contract import (
    EvidencePersistencePlan,
    EvidenceStrength,
    ExtractedProviderMetadata,
    LocalizationStatus,
    ManualValidationStatus,
    PlannedEntityCandidate,
    ProviderQuery,
    SourceMatch,
    SourceMatchClass,
)


PROVIDER_KEY = "saucenao"
PROVIDER_CATEGORY = "saucenao_style_reverse_search"
QUERY_TYPE = "reverse_search_derived_image"
PROVIDER_POLICY_VERSION = "phase44b1-derived-saucenao-policy-v1"
ACCEPTANCE_POLICY_VERSION = "phase44c0-manual-validated-source-evidence-v1"
SCORE_KIND = "saucenao_similarity_percent"
INPUT_KIND = "derived_resized_stripped_image"
UPLOADED_INPUT_KIND = "derived_resized_stripped_image"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, tuple):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _request_shape_status(request_shape: Mapping[str, Any]) -> str:
    return "present" if bool(request_shape) else "missing"


def _host_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.netloc.lower() or None


def _manual_status(manual_item: Mapping[str, Any]) -> ManualValidationStatus:
    judgment = str(manual_item.get("judgment") or "").lower()
    action = str(manual_item.get("recommended_action") or "").lower()
    if action == "keep_as_strong_evidence" or judgment == "correct":
        return ManualValidationStatus.validated_correct
    if action == "discard" or "wrong" in judgment or "invalid" in judgment or "unrelated" in judgment:
        return ManualValidationStatus.validated_wrong
    return ManualValidationStatus.not_validated


def _provider_index_label(*, top_result: Mapping[str, Any], metadata_item: Mapping[str, Any]) -> str | None:
    label = _as_str(metadata_item.get("provider_index_label"))
    if label:
        return label
    index_name = _as_str(top_result.get("index_name"))
    if not index_name:
        index_id = _as_str(top_result.get("index_id"))
        return f"index:{index_id}" if index_id else None
    # Drop provider-returned filename/hash suffixes such as
    # "Index #9: Danbooru - <provider filename>" from public contract data.
    match = re.match(r"Index\s+#\d+:\s*([^-]+?)(?:\s+-\s+.*)?$", index_name)
    if match:
        return match.group(1).strip()
    return index_name.split(" - ", 1)[0].strip()


def _post_url(source_host: str | None, result_id: str | None) -> str | None:
    if not source_host or not result_id:
        return None
    if source_host == "danbooru.donmai.us":
        return f"https://danbooru.donmai.us/posts/{result_id}"
    if source_host == "gelbooru.com":
        return f"https://gelbooru.com/index.php?page=post&s=view&id={result_id}"
    return None


def _source_identifier_status(
    *,
    result_id: str | None,
    source_url: str | None,
    post_url: str | None,
    provider_external_id: str | None,
) -> str:
    if result_id or source_url or post_url or provider_external_id:
        return "present"
    return "missing"


def _source_match_class(
    *,
    result_class: str,
    manual_status: ManualValidationStatus,
    source_identifier_status: str,
) -> tuple[SourceMatchClass, EvidenceStrength]:
    if manual_status == ManualValidationStatus.validated_wrong:
        return SourceMatchClass.discarded, EvidenceStrength.discard
    if (
        result_class == "high_confidence_match"
        and manual_status == ManualValidationStatus.validated_correct
        and source_identifier_status == "present"
    ):
        return SourceMatchClass.exact_or_near_exact, EvidenceStrength.strong
    if result_class == "high_confidence_match":
        return SourceMatchClass.low_confidence, EvidenceStrength.weak
    if result_class == "no_match":
        return SourceMatchClass.no_match, EvidenceStrength.discard
    if result_class == "conflict":
        return SourceMatchClass.conflict, EvidenceStrength.unknown
    if result_class in {"provider_error", "schema_changed", "rate_limited", "auth_failed", "forbidden"}:
        return SourceMatchClass.provider_error, EvidenceStrength.unknown
    if result_class == "low_confidence_match":
        return SourceMatchClass.low_confidence, EvidenceStrength.weak
    return SourceMatchClass.provider_error, EvidenceStrength.unknown


def _metadata_from_item(
    *,
    metadata_item: Mapping[str, Any],
    top_result: Mapping[str, Any],
    strong: bool,
) -> ExtractedProviderMetadata:
    artists = _as_list(metadata_item.get("artist") or metadata_item.get("artist_raw") or top_result.get("creator"))
    works = _as_list(
        metadata_item.get("work_or_copyright")
        or metadata_item.get("work_raw")
        or metadata_item.get("copyright_raw")
        or top_result.get("material")
        or top_result.get("title")
    )
    characters = _as_list(metadata_item.get("characters") or metadata_item.get("character_raw"))
    general_tags = _as_list(metadata_item.get("general_tags") or metadata_item.get("general_tags_raw"))
    parser_status = _as_str(metadata_item.get("metadata_extraction_status")) or (
        "normalized_payload_only" if top_result else "not_available"
    )
    raw_available = bool(strong and (artists or works or characters or general_tags))
    return ExtractedProviderMetadata(
        artist_raw=artists if strong else (),
        work_raw=works if strong else (),
        copyright_raw=works if strong else (),
        character_raw=characters if strong else (),
        general_tags_raw=general_tags if strong else (),
        source_title=_as_str(top_result.get("title")) if strong else None,
        localization_status=LocalizationStatus.pending if raw_available else LocalizationStatus.not_applicable,
        raw_metadata_available=raw_available,
        parser_status=parser_status,
    )


def _planned_candidates(metadata: ExtractedProviderMetadata, strength: EvidenceStrength) -> tuple[PlannedEntityCandidate, ...]:
    if strength != EvidenceStrength.strong:
        return ()
    rows: list[PlannedEntityCandidate] = []
    for name in metadata.artist_raw:
        rows.append(PlannedEntityCandidate("artist", name, "artist_raw", strength))
    for name in metadata.work_raw:
        rows.append(PlannedEntityCandidate("work", name, "work_raw", strength))
    for name in metadata.character_raw:
        rows.append(PlannedEntityCandidate("character", name, "character_raw", strength))
    return tuple(rows)


def map_saucenao_result_to_plan(
    *,
    live_item: Mapping[str, Any],
    manual_item: Mapping[str, Any],
    metadata_item: Mapping[str, Any] | None = None,
) -> EvidencePersistencePlan:
    """Return a C0 non-mutating plan for one SauceNAO result."""
    metadata_item = _as_mapping(metadata_item)
    provider_result = _as_mapping(live_item.get("provider_result"))
    result_class = str(
        provider_result.get("result_class")
        or live_item.get("result_class")
        or metadata_item.get("result_class")
        or "provider_error"
    )
    normalized_payload = _as_mapping(provider_result.get("normalized_payload"))
    top_result = _as_mapping(normalized_payload.get("top_result") or live_item.get("top_result"))
    header = _as_mapping(normalized_payload.get("saucenao_header") or live_item.get("saucenao_header"))
    media_id = int(live_item.get("media_id") or manual_item.get("media_id") or metadata_item.get("media_id"))
    score = _as_float(provider_result.get("score") or live_item.get("score") or metadata_item.get("score") or top_result.get("similarity"))
    minimum_similarity = _as_float(
        header.get("minimum_similarity")
        or live_item.get("minimum_similarity")
        or metadata_item.get("minimum_similarity")
    )
    source_host = _as_str(
        top_result.get("source_url_host")
        or live_item.get("source_url_host")
        or metadata_item.get("source_url_host")
    )
    source_url = _as_str(top_result.get("source_url") or live_item.get("source_url") or metadata_item.get("source_url"))
    post_url = _as_str(live_item.get("post_url") or metadata_item.get("post_url"))
    result_id = _as_str(top_result.get("result_id") or live_item.get("result_id") or metadata_item.get("result_id"))
    provider_external_id = _as_str(
        top_result.get("provider_external_id")
        or live_item.get("provider_external_id")
        or metadata_item.get("provider_external_id")
        or metadata_item.get("external_id")
        or top_result.get("post_id")
        or live_item.get("post_id")
        or metadata_item.get("post_id")
    )
    provider_result_id = result_id or provider_external_id
    if source_host is None:
        source_host = _host_from_url(source_url) or _host_from_url(post_url)
    if post_url is None:
        post_url = _post_url(source_host, result_id)
    source_identifier_status = _source_identifier_status(
        result_id=provider_result_id,
        source_url=source_url,
        post_url=post_url,
        provider_external_id=provider_external_id,
    )
    manual_status = _manual_status(manual_item)
    match_class, evidence_strength = _source_match_class(
        result_class=result_class,
        manual_status=manual_status,
        source_identifier_status=source_identifier_status,
    )
    strong = evidence_strength == EvidenceStrength.strong
    query_hash = _as_str(live_item.get("query_hash") or metadata_item.get("query_hash"))
    query_hash_status = "present" if query_hash else "missing"
    request_shape = dict(_as_mapping(live_item.get("request_shape_redacted") or metadata_item.get("request_shape_redacted")))
    request_shape_status = _request_shape_status(request_shape)
    provider_cache_allowed = query_hash_status == "present" and request_shape_status == "present"
    persistence_blocked_reasons = []
    if query_hash_status == "missing":
        persistence_blocked_reasons.append("missing_query_hash")
    if request_shape_status == "missing":
        persistence_blocked_reasons.append("missing_request_shape")
    if source_identifier_status == "missing" and result_class == "high_confidence_match":
        persistence_blocked_reasons.append("missing_source_identifier")

    provider_query = ProviderQuery(
        provider_key=PROVIDER_KEY,
        provider_category=PROVIDER_CATEGORY,
        media_id=media_id,
        input_kind=INPUT_KIND,
        query_hash=query_hash,
        query_hash_status=query_hash_status,
        request_shape_redacted=request_shape,
        request_shape_status=request_shape_status,
        live_request=True,
        uploaded_input_kind=UPLOADED_INPUT_KIND,
        provider_policy_version=PROVIDER_POLICY_VERSION,
        query_type=QUERY_TYPE,
    )
    source_match = SourceMatch(
        media_id=media_id,
        provider_key=PROVIDER_KEY,
        provider_result_id=provider_result_id,
        provider_index=_provider_index_label(top_result=top_result, metadata_item=metadata_item),
        source_host=source_host,
        source_url=source_url,
        post_url=post_url,
        source_identifier_status=source_identifier_status,
        rank=1 if top_result else None,
        score_value=score,
        score_kind=SCORE_KIND,
        provider_minimum_similarity=minimum_similarity,
        match_class=match_class,
        evidence_strength=evidence_strength,
        manual_validation_status=manual_status,
        acceptance_policy_version=ACCEPTANCE_POLICY_VERSION,
    )
    metadata = _metadata_from_item(metadata_item=metadata_item, top_result=top_result, strong=strong)
    planned_candidates = _planned_candidates(metadata, evidence_strength)
    positive_plan = strong and bool(planned_candidates)
    discard_plan = evidence_strength == EvidenceStrength.discard or match_class == SourceMatchClass.discarded
    entity_evidence_planned = strong and source_identifier_status == "present"
    return EvidencePersistencePlan(
        media_id=media_id,
        provider_query=provider_query,
        source_match=source_match,
        extracted_metadata=metadata,
        provider_cache_persistence_allowed=provider_cache_allowed,
        provider_cache_planned=provider_cache_allowed,
        entity_evidence_planned=entity_evidence_planned,
        media_entity_candidate_planned=positive_plan,
        negative_lookup_cache_planned=discard_plan and provider_cache_allowed,
        persistence_blocked_reason=persistence_blocked_reasons[0] if persistence_blocked_reasons else None,
        persistence_blocked_reasons=tuple(persistence_blocked_reasons),
        planned_entity_candidates=planned_candidates,
        localization_pending=metadata.localization_status == LocalizationStatus.pending,
        db_write_allowed=False,
        notes=(
            "C0 contract object only; no DB write allowed.",
            "Confirmed assignments and automatic Entity creation remain blocked.",
        ),
    )


def map_saucenao_samples_to_plans(
    *,
    live_details: Mapping[str, Any],
    manual_validation_summary: Mapping[str, Any],
    metadata_audit_summary: Mapping[str, Any] | None = None,
) -> list[EvidencePersistencePlan]:
    """Map the five known B1/B1V samples from report-style dictionaries."""
    metadata_audit_summary = _as_mapping(metadata_audit_summary or manual_validation_summary)
    live_rows = live_details.get("provider_results") or live_details.get("provider_result_items") or []
    manual_rows = _as_mapping(manual_validation_summary.get("manual_validation")).get("items") or []
    metadata_rows = _as_mapping(metadata_audit_summary.get("metadata_extraction_audit")).get("items") or []

    manual_by_id = {int(row["media_id"]): row for row in manual_rows if isinstance(row, Mapping) and "media_id" in row}
    metadata_by_id = {int(row["media_id"]): row for row in metadata_rows if isinstance(row, Mapping) and "media_id" in row}

    plans: list[EvidencePersistencePlan] = []
    for row in live_rows:
        if not isinstance(row, Mapping) or "media_id" not in row:
            continue
        media_id = int(row["media_id"])
        plans.append(
            map_saucenao_result_to_plan(
                live_item=row,
                manual_item=manual_by_id.get(media_id, {"media_id": media_id}),
                metadata_item=metadata_by_id.get(media_id),
            )
        )
    return plans
