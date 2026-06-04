"""Read-only source-layer chip and search helpers for Phase 4.4-P2R-F6.

This module deliberately treats source tags and source/name assertions as an
unconfirmed search layer. It must not create Entity, EntityAlias,
MediaEntityCandidate, LocalSourceHint, media_tags, or other truth-path rows.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, exists, false, func, or_
from sqlalchemy.orm import Query, Session, aliased

from ..models import (
    Media,
    SourceMetadataRecord,
    SourceSearchableNameAssertion,
    SourceTagObservation,
)

ACTIVE_ASSERTION_STATUSES = ("searchable_active",)
REVIEW_ASSERTION_STATUSES = ("needs_review",)
DEFAULT_ASSERTION_ROLES = (
    "character",
    "person",
    "artist",
    "creator",
    "work_title",
    "source_title",
)
EXCLUDED_DEFAULT_ASSERTION_ROLES = ("general_descriptor", "popularity_marker", "unknown")
ACTIVE_SOURCE_TAG_STATUSES = ("observed",)
FORBIDDEN_TRUTH_PATHS = (
    "Entity",
    "EntityAlias",
    "EntityEvidence",
    "MediaEntityCandidate",
    "MediaEntityAssignment",
    "LocalSourceHint",
    "media_tags",
    "TagTranslation",
    "confirmed assignment",
)


@dataclass(frozen=True)
class SourceAssertionFilter:
    provider: str | None = None
    canonical_name_key: str | None = None
    asserted_role: str | None = None
    assertion_key: str | None = None
    normalized_input: str | None = None


@dataclass(frozen=True)
class SourceTagFilter:
    provider: str | None = None
    canonical_tag_key: str | None = None
    observation_key: str | None = None
    normalized_tag: str | None = None


def _compact_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _encode_filter_payload(payload: dict[str, Any]) -> str:
    raw = _compact_json(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_filter_payload(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        padded = value + ("=" * (-len(value) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
        return payload if isinstance(payload, dict) else None
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def encode_source_assertion_filter(
    *,
    provider: str,
    canonical_name_key: str,
    asserted_role: str,
    assertion_key: str | None = None,
) -> str:
    payload = {
        "kind": "source_assertion",
        "provider": provider,
        "canonical_name_key": canonical_name_key,
        "asserted_role": asserted_role,
    }
    if assertion_key:
        payload["assertion_key"] = assertion_key
    return _encode_filter_payload(payload)


def encode_source_tag_filter(
    *,
    provider: str,
    canonical_tag_key: str,
    observation_key: str | None = None,
) -> str:
    payload = {
        "kind": "source_tag",
        "provider": provider,
        "canonical_tag_key": canonical_tag_key,
    }
    if observation_key:
        payload["observation_key"] = observation_key
    return _encode_filter_payload(payload)


def parse_source_assertion_filter(value: str) -> SourceAssertionFilter:
    payload = _decode_filter_payload(value)
    if payload:
        return SourceAssertionFilter(
            provider=payload.get("provider") or None,
            canonical_name_key=payload.get("canonical_name_key") or None,
            asserted_role=payload.get("asserted_role") or None,
            assertion_key=payload.get("assertion_key") or None,
            normalized_input=payload.get("normalized_input") or None,
        )

    return SourceAssertionFilter(
        assertion_key=value,
        canonical_name_key=value,
        normalized_input=value.lower(),
    )


def parse_source_tag_filter(value: str) -> SourceTagFilter:
    payload = _decode_filter_payload(value)
    if payload:
        return SourceTagFilter(
            provider=payload.get("provider") or None,
            canonical_tag_key=payload.get("canonical_tag_key") or None,
            observation_key=payload.get("observation_key") or None,
            normalized_tag=payload.get("normalized_tag") or None,
        )

    return SourceTagFilter(
        observation_key=value,
        canonical_tag_key=value,
        normalized_tag=value.lower(),
    )


def _assertion_statuses(include_needs_review: bool = False) -> tuple[str, ...]:
    if include_needs_review:
        return ACTIVE_ASSERTION_STATUSES + REVIEW_ASSERTION_STATUSES
    return ACTIVE_ASSERTION_STATUSES


def _assertion_chip(row: SourceSearchableNameAssertion, record: SourceMetadataRecord | None) -> dict[str, Any]:
    display_name = row.asserted_name or row.raw_input
    search_value = encode_source_assertion_filter(
        provider=row.provider,
        canonical_name_key=row.canonical_name_key,
        asserted_role=row.asserted_role,
        assertion_key=row.assertion_key,
    )
    include_needs_review = row.status in REVIEW_ASSERTION_STATUSES
    search_url = f"/?source_assertion={search_value}"
    if include_needs_review:
        search_url += "&include_source_needs_review=1"
    return {
        "type": "source_assertion",
        "layer": "source_assertion",
        "marker": "source assertion",
        "label_zh": "来源断言",
        "unconfirmed_label_zh": "未确认实体",
        "is_entity_truth": False,
        "is_confirmed_entity": False,
        "search_param": "source_assertion",
        "search_value": search_value,
        "search_url": search_url,
        "include_source_needs_review": include_needs_review,
        "id": row.id,
        "assertion_key": row.assertion_key,
        "display_name": display_name,
        "raw_input": row.raw_input,
        "normalized_input": row.normalized_input,
        "canonical_name_key": row.canonical_name_key,
        "role": row.asserted_role,
        "provider": row.provider,
        "status": row.status,
        "confidence": row.confidence,
        "confidence_score": row.confidence_score,
        "requires_review": bool(row.requires_review),
        "source_metadata_record_id": row.source_metadata_record_id,
        "source_url": record.source_url if record else None,
        "source_title": record.title if record else None,
        "source_artist_name": record.artist_name if record else None,
    }


def _source_tag_chip(row: SourceTagObservation, record: SourceMetadataRecord | None) -> dict[str, Any]:
    search_value = encode_source_tag_filter(
        provider=row.provider,
        canonical_tag_key=row.canonical_tag_key,
        observation_key=row.observation_key,
    )
    return {
        "type": "source_tag",
        "layer": "source_tag",
        "marker": "source tag",
        "label_zh": "来源标签",
        "unconfirmed_label_zh": "未确认来源",
        "is_entity_truth": False,
        "is_confirmed_entity": False,
        "search_param": "source_tag",
        "search_value": search_value,
        "search_url": f"/?source_tag={search_value}",
        "id": row.id,
        "observation_key": row.observation_key,
        "display_name": row.raw_tag,
        "raw_input": row.raw_tag,
        "normalized_input": row.normalized_tag,
        "canonical_tag_key": row.canonical_tag_key,
        "role": row.source_tag_kind,
        "provider": row.provider,
        "status": row.status,
        "confidence": None,
        "confidence_score": row.confidence,
        "source_metadata_record_id": row.source_metadata_record_id,
        "source_url": record.source_url if record else None,
        "source_title": record.title if record else None,
        "source_artist_name": record.artist_name if record else None,
    }


def list_media_source_layer(db: Session, media_id: int, source_tag_limit: int = 60) -> dict[str, Any]:
    """Return source-layer chips for one media item without mutating state."""

    assertion_rows = (
        db.query(SourceSearchableNameAssertion, SourceMetadataRecord)
        .join(
            SourceMetadataRecord,
            SourceSearchableNameAssertion.source_metadata_record_id == SourceMetadataRecord.id,
        )
        .filter(SourceMetadataRecord.media_id == media_id)
        .order_by(
            SourceSearchableNameAssertion.asserted_role.asc(),
            SourceSearchableNameAssertion.provider.asc(),
            SourceSearchableNameAssertion.asserted_name.asc(),
            SourceSearchableNameAssertion.raw_input.asc(),
        )
        .all()
    )

    source_tag_rows = (
        db.query(SourceTagObservation, SourceMetadataRecord)
        .join(SourceMetadataRecord, SourceTagObservation.source_metadata_record_id == SourceMetadataRecord.id)
        .filter(
            SourceMetadataRecord.media_id == media_id,
            SourceTagObservation.status.in_(ACTIVE_SOURCE_TAG_STATUSES),
        )
        .order_by(
            SourceTagObservation.provider.asc(),
            SourceTagObservation.source_tag_kind.asc(),
            SourceTagObservation.order_index.asc().nullslast(),
            SourceTagObservation.raw_tag.asc(),
        )
        .limit(source_tag_limit)
        .all()
    )

    active_assertions: list[dict[str, Any]] = []
    needs_review_assertions: list[dict[str, Any]] = []
    hidden_assertion_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}

    for assertion, record in assertion_rows:
        status_counts[assertion.status] = status_counts.get(assertion.status, 0) + 1
        if assertion.asserted_role in EXCLUDED_DEFAULT_ASSERTION_ROLES:
            hidden_assertion_counts[assertion.asserted_role] = hidden_assertion_counts.get(assertion.asserted_role, 0) + 1
            continue
        chip = _assertion_chip(assertion, record)
        if assertion.status in ACTIVE_ASSERTION_STATUSES:
            active_assertions.append(chip)
        elif assertion.status in REVIEW_ASSERTION_STATUSES:
            needs_review_assertions.append(chip)
        else:
            hidden_assertion_counts[assertion.status] = hidden_assertion_counts.get(assertion.status, 0) + 1

    source_tags = [_source_tag_chip(tag, record) for tag, record in source_tag_rows]

    return {
        "media_id": media_id,
        "source_assertions": active_assertions,
        "needs_review_assertions": needs_review_assertions,
        "source_tags": source_tags,
        "counts": {
            "source_assertions": len(active_assertions),
            "needs_review_assertions": len(needs_review_assertions),
            "source_tags": len(source_tags),
            "assertion_statuses": status_counts,
            "hidden_assertions": hidden_assertion_counts,
        },
        "manual_promotion": {
            "preview_only": True,
            "disabled": True,
            "truth_writes_allowed": False,
            "forbidden_paths": list(FORBIDDEN_TRUTH_PATHS),
        },
    }


def _apply_assertion_filter(
    query: Query,
    criteria: SourceAssertionFilter,
    *,
    include_needs_review: bool,
) -> Query:
    assertion = aliased(SourceSearchableNameAssertion)
    record = aliased(SourceMetadataRecord)

    conditions = [
        assertion.source_metadata_record_id == record.id,
        record.media_id == Media.id,
        assertion.status.in_(_assertion_statuses(include_needs_review)),
    ]
    if criteria.provider:
        conditions.append(assertion.provider == criteria.provider)
    if criteria.asserted_role:
        conditions.append(assertion.asserted_role == criteria.asserted_role)
    if criteria.assertion_key and not criteria.canonical_name_key:
        conditions.append(assertion.assertion_key == criteria.assertion_key)
    elif criteria.assertion_key and criteria.canonical_name_key == criteria.assertion_key:
        conditions.append(
            or_(
                assertion.assertion_key == criteria.assertion_key,
                assertion.canonical_name_key == criteria.canonical_name_key,
                assertion.normalized_input == criteria.normalized_input,
            )
        )
    elif criteria.canonical_name_key:
        conditions.append(assertion.canonical_name_key == criteria.canonical_name_key)
    elif criteria.normalized_input:
        conditions.append(assertion.normalized_input == criteria.normalized_input)
    else:
        return query.filter(false())

    return query.filter(exists().where(and_(*conditions)))


def _apply_source_tag_filter(query: Query, criteria: SourceTagFilter) -> Query:
    source_tag = aliased(SourceTagObservation)
    record = aliased(SourceMetadataRecord)

    conditions = [
        source_tag.source_metadata_record_id == record.id,
        record.media_id == Media.id,
        source_tag.status.in_(ACTIVE_SOURCE_TAG_STATUSES),
    ]
    if criteria.provider:
        conditions.append(source_tag.provider == criteria.provider)
    if criteria.observation_key and not criteria.canonical_tag_key:
        conditions.append(source_tag.observation_key == criteria.observation_key)
    elif criteria.observation_key and criteria.canonical_tag_key == criteria.observation_key:
        conditions.append(
            or_(
                source_tag.observation_key == criteria.observation_key,
                source_tag.canonical_tag_key == criteria.canonical_tag_key,
                source_tag.normalized_tag == criteria.normalized_tag,
            )
        )
    elif criteria.canonical_tag_key:
        conditions.append(source_tag.canonical_tag_key == criteria.canonical_tag_key)
    elif criteria.normalized_tag:
        conditions.append(source_tag.normalized_tag == criteria.normalized_tag)
    else:
        return query.filter(false())

    return query.filter(exists().where(and_(*conditions)))


def apply_source_layer_filters(
    query: Query,
    source_assertions: Sequence[str] | None = None,
    source_tags: Sequence[str] | None = None,
    *,
    include_needs_review: bool = False,
) -> Query:
    """Apply source-layer filters as AND/intersection constraints."""

    for value in _clean_values(source_assertions):
        query = _apply_assertion_filter(
            query,
            parse_source_assertion_filter(value),
            include_needs_review=include_needs_review,
        )

    for value in _clean_values(source_tags):
        query = _apply_source_tag_filter(query, parse_source_tag_filter(value))

    return query


def _clean_values(values: Sequence[str] | None) -> list[str]:
    if not values:
        return []
    return [value.strip() for value in values if value and value.strip()]


def has_source_layer_filters(source_assertions: Sequence[str] | None, source_tags: Sequence[str] | None) -> bool:
    return bool(_clean_values(source_assertions) or _clean_values(source_tags))


def resolve_source_filter_labels(
    db: Session,
    source_assertions: Sequence[str] | None = None,
    source_tags: Sequence[str] | None = None,
    *,
    include_needs_review: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Resolve URL filter values into display chips for the gallery/search UI."""

    return {
        "source_assertions": [
            _resolve_assertion_label(db, value, include_needs_review=include_needs_review)
            for value in _clean_values(source_assertions)
        ],
        "source_tags": [_resolve_source_tag_label(db, value) for value in _clean_values(source_tags)],
    }


def _resolve_assertion_label(db: Session, value: str, *, include_needs_review: bool) -> dict[str, Any]:
    criteria = parse_source_assertion_filter(value)
    query = (
        db.query(SourceSearchableNameAssertion, SourceMetadataRecord)
        .join(
            SourceMetadataRecord,
            SourceSearchableNameAssertion.source_metadata_record_id == SourceMetadataRecord.id,
        )
        .filter(SourceSearchableNameAssertion.status.in_(_assertion_statuses(include_needs_review)))
    )
    query = _filter_assertion_lookup_query(query, criteria)
    row = query.order_by(SourceSearchableNameAssertion.asserted_name.asc(), SourceSearchableNameAssertion.raw_input.asc()).first()
    if not row:
        return {
            "type": "source_assertion",
            "layer": "source_assertion",
            "display_name": value,
            "search_param": "source_assertion",
            "search_value": value,
            "is_entity_truth": False,
            "missing": True,
        }

    chip = _assertion_chip(row[0], row[1])
    chip["search_value"] = value
    chip["result_media_count"] = _count_assertion_media(db, criteria, include_needs_review=include_needs_review)
    return chip


def _filter_assertion_lookup_query(query: Query, criteria: SourceAssertionFilter) -> Query:
    if criteria.provider:
        query = query.filter(SourceSearchableNameAssertion.provider == criteria.provider)
    if criteria.asserted_role:
        query = query.filter(SourceSearchableNameAssertion.asserted_role == criteria.asserted_role)
    if criteria.assertion_key and not criteria.canonical_name_key:
        query = query.filter(SourceSearchableNameAssertion.assertion_key == criteria.assertion_key)
    elif criteria.assertion_key and criteria.canonical_name_key == criteria.assertion_key:
        query = query.filter(
            or_(
                SourceSearchableNameAssertion.assertion_key == criteria.assertion_key,
                SourceSearchableNameAssertion.canonical_name_key == criteria.canonical_name_key,
                SourceSearchableNameAssertion.normalized_input == criteria.normalized_input,
            )
        )
    elif criteria.canonical_name_key:
        query = query.filter(SourceSearchableNameAssertion.canonical_name_key == criteria.canonical_name_key)
    elif criteria.normalized_input:
        query = query.filter(SourceSearchableNameAssertion.normalized_input == criteria.normalized_input)
    else:
        query = query.filter(false())
    return query


def _resolve_source_tag_label(db: Session, value: str) -> dict[str, Any]:
    criteria = parse_source_tag_filter(value)
    query = (
        db.query(SourceTagObservation, SourceMetadataRecord)
        .join(SourceMetadataRecord, SourceTagObservation.source_metadata_record_id == SourceMetadataRecord.id)
        .filter(SourceTagObservation.status.in_(ACTIVE_SOURCE_TAG_STATUSES))
    )
    query = _filter_source_tag_lookup_query(query, criteria)
    row = query.order_by(SourceTagObservation.raw_tag.asc()).first()
    if not row:
        return {
            "type": "source_tag",
            "layer": "source_tag",
            "display_name": value,
            "search_param": "source_tag",
            "search_value": value,
            "is_entity_truth": False,
            "missing": True,
        }

    chip = _source_tag_chip(row[0], row[1])
    chip["search_value"] = value
    chip["result_media_count"] = _count_source_tag_media(db, criteria)
    return chip


def _filter_source_tag_lookup_query(query: Query, criteria: SourceTagFilter) -> Query:
    if criteria.provider:
        query = query.filter(SourceTagObservation.provider == criteria.provider)
    if criteria.observation_key and not criteria.canonical_tag_key:
        query = query.filter(SourceTagObservation.observation_key == criteria.observation_key)
    elif criteria.observation_key and criteria.canonical_tag_key == criteria.observation_key:
        query = query.filter(
            or_(
                SourceTagObservation.observation_key == criteria.observation_key,
                SourceTagObservation.canonical_tag_key == criteria.canonical_tag_key,
                SourceTagObservation.normalized_tag == criteria.normalized_tag,
            )
        )
    elif criteria.canonical_tag_key:
        query = query.filter(SourceTagObservation.canonical_tag_key == criteria.canonical_tag_key)
    elif criteria.normalized_tag:
        query = query.filter(SourceTagObservation.normalized_tag == criteria.normalized_tag)
    else:
        query = query.filter(false())
    return query


def _count_assertion_media(db: Session, criteria: SourceAssertionFilter, *, include_needs_review: bool) -> int:
    query = (
        db.query(func.count(func.distinct(SourceMetadataRecord.media_id)))
        .select_from(SourceSearchableNameAssertion)
        .join(
            SourceMetadataRecord,
            SourceSearchableNameAssertion.source_metadata_record_id == SourceMetadataRecord.id,
        )
        .filter(
            SourceMetadataRecord.media_id.isnot(None),
            SourceSearchableNameAssertion.status.in_(_assertion_statuses(include_needs_review)),
        )
    )
    return int(_filter_assertion_lookup_query(query, criteria).scalar() or 0)


def _count_source_tag_media(db: Session, criteria: SourceTagFilter) -> int:
    query = (
        db.query(func.count(func.distinct(SourceMetadataRecord.media_id)))
        .select_from(SourceTagObservation)
        .join(SourceMetadataRecord, SourceTagObservation.source_metadata_record_id == SourceMetadataRecord.id)
        .filter(
            SourceMetadataRecord.media_id.isnot(None),
            SourceTagObservation.status.in_(ACTIVE_SOURCE_TAG_STATUSES),
        )
    )
    return int(_filter_source_tag_lookup_query(query, criteria).scalar() or 0)


def preview_source_assertion_promotion(
    db: Session,
    source_assertion: str,
    *,
    limit: int = 50,
    include_needs_review: bool = False,
) -> dict[str, Any]:
    """Return a read-only promotion preview. It never writes truth-path rows."""

    criteria = parse_source_assertion_filter(source_assertion)
    query = (
        db.query(Media)
        .options()
        .order_by(Media.uploaded_at.desc(), Media.id.desc())
    )
    query = _apply_assertion_filter(query, criteria, include_needs_review=include_needs_review)
    total = query.count()
    rows = query.limit(limit).all()
    return {
        "preview_only": True,
        "disabled": True,
        "truth_writes_allowed": False,
        "forbidden_paths": list(FORBIDDEN_TRUTH_PATHS),
        "source_assertion": _resolve_assertion_label(db, source_assertion, include_needs_review=include_needs_review),
        "affected_media_count": total,
        "affected_media": [
            {
                "id": media.id,
                "filename": media.filename,
                "thumbnail_url": f"/api/media/{media.id}/thumbnail" if media.thumbnail_path else None,
            }
            for media in rows
        ],
    }


def source_filter_query_params(chips: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    params: dict[str, list[str]] = {"source_assertion": [], "source_tag": []}
    for chip in chips:
        param = chip.get("search_param")
        value = chip.get("search_value")
        if param in params and value:
            params[param].append(value)
    return params
