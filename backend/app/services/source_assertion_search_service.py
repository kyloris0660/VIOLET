"""Read-only source-layer chip and search helpers for Phase 4.4-P2R-F6.

This module deliberately treats source tags and source/name assertions as an
unconfirmed search layer. It must not create Entity, EntityAlias,
MediaEntityCandidate, LocalSourceHint, media_tags, or other truth-path rows.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, exists, false, func, or_
from sqlalchemy.orm import Query, Session, aliased

from ..models import (
    Media,
    SourceMetadataRecord,
    SourceNameAliasCandidate,
    SourceNameObservation,
    SourceNameRegistry,
    SourceSearchableNameAssertion,
    SourceTagObservation,
    Tag,
)
from .source_concept_search_service import (
    list_media_source_concepts,
    source_concept_media_condition_for_term,
)
from .source_metadata_registry_service import canonical_source_key, normalize_source_text

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
ACTIVE_NAME_OBSERVATION_STATUSES = ("observed",)
DEFAULT_QUERY_VISIBLE_EXACT_PIXIV_NAME_FIELDS = (
    "pixiv_user_metadata",
    "pixiv_user_account",
    "pixiv_title",
    "pixiv_parenthetical_inner_work",
    "pixiv_character_tag",
    "pixiv_work_title_tag",
)
SOFT_ALIAS_RELATION_TYPES = (
    "curated_alias",
    "provider_canonical",
    "same_as",
    "alias",
    "translation_alias",
)
INACTIVE_ALIAS_STATUSES = ("rejected", "superseded")
LIKE_ESCAPE = "\\"
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


def _source_concept_key_candidates(value: str | None) -> set[str]:
    normalized = normalize_source_text(value)
    if not normalized:
        return set()

    variants = {
        normalized,
        normalized.replace("_", " "),
        normalized.replace("_(", "("),
    }
    keys = {canonical_source_key(variant) for variant in variants if variant}

    parenthetical = re.match(r"^(.+?)_?\(([^()]+)\)$", normalized.replace("（", "(").replace("）", ")"))
    if parenthetical:
        # Danbooru-style character tags such as barbara_(genshin_impact)
        # should bridge to the character/person key, not to the work key.
        keys.add(canonical_source_key(parenthetical.group(1)))

    return {key for key in keys if key}


def _expand_source_name_keys(db: Session | None, keys: set[str]) -> set[str]:
    if not keys or db is None:
        return keys

    expanded = set(keys)
    registry_rows = (
        db.query(SourceNameRegistry)
        .filter(SourceNameRegistry.canonical_name_key.in_(expanded))
        .all()
    )
    expanded.update(row.canonical_name_key for row in registry_rows)

    for _ in range(2):
        alias_rows = (
            db.query(SourceNameAliasCandidate)
            .filter(
                SourceNameAliasCandidate.relation_type.in_(SOFT_ALIAS_RELATION_TYPES),
                ~SourceNameAliasCandidate.status.in_(INACTIVE_ALIAS_STATUSES),
                or_(
                    SourceNameAliasCandidate.source_name_key.in_(expanded),
                    SourceNameAliasCandidate.target_name_key.in_(expanded),
                ),
            )
            .all()
        )
        before = len(expanded)
        for row in alias_rows:
            expanded.add(row.source_name_key)
            expanded.add(row.target_name_key)
        if len(expanded) == before:
            break

    return {key for key in expanded if key}


def _source_name_keys_for_text(db: Session | None, value: str | None) -> set[str]:
    keys = _source_concept_key_candidates(value)
    if not keys:
        return set()
    return _expand_source_name_keys(db, keys)


def _tag_names_for_source_keys(db: Session | None, keys: set[str]) -> set[str]:
    if not keys or db is None:
        return set()

    conditions = [Tag.name.in_(keys)]
    for key in sorted(keys):
        if key and re.match(r"^[a-z0-9_]+$", key):
            conditions.append(Tag.name.like(f"{_escape_like_pattern(key)}\\_(%", escape=LIKE_ESCAPE))

    rows = db.query(Tag.name).filter(or_(*conditions)).all()
    return {row[0] for row in rows}


def _escape_like_pattern(value: str) -> str:
    return (
        value.replace(LIKE_ESCAPE, LIKE_ESCAPE + LIKE_ESCAPE)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def _media_has_tag_condition(tag_names: set[str]):
    if not tag_names:
        return None
    return Media.tags.any(Tag.name.in_(sorted(tag_names)))


def _source_name_condition(
    keys: set[str],
    *,
    provider: str | None = None,
    role: str | None = None,
    include_needs_review: bool = False,
):
    if not keys:
        return None

    assertion = aliased(SourceSearchableNameAssertion)
    assertion_record = aliased(SourceMetadataRecord)
    assertion_conditions = [
        assertion.source_metadata_record_id == assertion_record.id,
        assertion_record.media_id == Media.id,
        assertion.status.in_(_assertion_statuses(include_needs_review)),
        assertion.canonical_name_key.in_(sorted(keys)),
    ]
    if provider:
        assertion_conditions.append(assertion.provider == provider)
    if role:
        assertion_conditions.append(assertion.asserted_role == role)

    name = aliased(SourceNameObservation)
    name_record = aliased(SourceMetadataRecord)
    name_conditions = [
        name.source_metadata_record_id == name_record.id,
        name_record.media_id == Media.id,
        name.status.in_(ACTIVE_NAME_OBSERVATION_STATUSES),
        name.canonical_name_key.in_(sorted(keys)),
    ]
    if not include_needs_review:
        name_conditions.append(
            or_(
                name.requires_review == False,
                and_(
                    name.provider == "pixiv",
                    name.source_field.in_(DEFAULT_QUERY_VISIBLE_EXACT_PIXIV_NAME_FIELDS),
                ),
            )
        )
    if provider:
        name_conditions.append(name.provider == provider)
    if role:
        name_conditions.append(name.name_role == role)

    return or_(
        exists().where(and_(*assertion_conditions)),
        exists().where(and_(*name_conditions)),
    )


def _source_tag_condition(keys: set[str], *, provider: str | None = None):
    if not keys:
        return None

    source_tag = aliased(SourceTagObservation)
    record = aliased(SourceMetadataRecord)
    conditions = [
        source_tag.source_metadata_record_id == record.id,
        record.media_id == Media.id,
        source_tag.status.in_(ACTIVE_SOURCE_TAG_STATUSES),
        source_tag.canonical_tag_key.in_(sorted(keys)),
    ]
    if provider:
        conditions.append(source_tag.provider == provider)
    return exists().where(and_(*conditions))


def _source_layer_exact_text_condition(term: str, *, include_needs_review: bool = False):
    normalized = normalize_source_text(term)
    if not normalized:
        return None

    exact_values = {
        normalized,
        normalized.casefold(),
        canonical_source_key(normalized),
    }
    exact_values = {value for value in exact_values if value}
    if not exact_values:
        return None

    assertion = aliased(SourceSearchableNameAssertion)
    assertion_record = aliased(SourceMetadataRecord)
    assertion_condition = exists().where(
        and_(
            assertion.source_metadata_record_id == assertion_record.id,
            assertion_record.media_id == Media.id,
            assertion.status.in_(_assertion_statuses(include_needs_review)),
            or_(
                assertion.raw_input.in_(sorted(exact_values)),
                assertion.normalized_input.in_(sorted(exact_values)),
                assertion.canonical_name_key.in_(sorted(exact_values)),
                assertion.asserted_name.in_(sorted(exact_values)),
            ),
        )
    )

    name = aliased(SourceNameObservation)
    name_record = aliased(SourceMetadataRecord)
    name_conditions = [
        name.source_metadata_record_id == name_record.id,
        name_record.media_id == Media.id,
        name.status.in_(ACTIVE_NAME_OBSERVATION_STATUSES),
        or_(
            name.raw_name.in_(sorted(exact_values)),
            name.normalized_name.in_(sorted(exact_values)),
            name.canonical_name_key.in_(sorted(exact_values)),
        ),
    ]
    if not include_needs_review:
        name_conditions.append(
            or_(
                name.requires_review == False,
                and_(
                    name.provider == "pixiv",
                    name.source_field.in_(DEFAULT_QUERY_VISIBLE_EXACT_PIXIV_NAME_FIELDS),
                ),
            )
        )

    source_tag = aliased(SourceTagObservation)
    source_tag_record = aliased(SourceMetadataRecord)
    source_tag_condition = exists().where(
        and_(
            source_tag.source_metadata_record_id == source_tag_record.id,
            source_tag_record.media_id == Media.id,
            source_tag.status.in_(ACTIVE_SOURCE_TAG_STATUSES),
            or_(
                source_tag.raw_tag.in_(sorted(exact_values)),
                source_tag.normalized_tag.in_(sorted(exact_values)),
                source_tag.canonical_tag_key.in_(sorted(exact_values)),
            ),
        )
    )

    return or_(
        assertion_condition,
        exists().where(and_(*name_conditions)),
        source_tag_condition,
    )


def _or_non_empty(conditions: Iterable[Any]):
    present = [condition for condition in conditions if condition is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return or_(*present)


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

    normalized = normalize_source_text(value)
    return SourceAssertionFilter(
        canonical_name_key=canonical_source_key(normalized),
        normalized_input=normalized.casefold(),
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

    normalized = normalize_source_text(value)
    return SourceTagFilter(
        canonical_tag_key=canonical_source_key(normalized),
        normalized_tag=normalized.casefold(),
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


def _name_observation_chip(row: SourceNameObservation, record: SourceMetadataRecord | None) -> dict[str, Any]:
    display_name = row.raw_name
    search_value = encode_source_assertion_filter(
        provider=row.provider,
        canonical_name_key=row.canonical_name_key,
        asserted_role=row.name_role,
    )
    include_needs_review = bool(row.requires_review)
    search_url = f"/?source_assertion={search_value}"
    if include_needs_review:
        search_url += "&include_source_needs_review=1"
    return {
        "type": "source_assertion",
        "layer": "source_name_observation",
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
        "assertion_key": None,
        "display_name": display_name,
        "raw_input": row.raw_name,
        "normalized_input": row.normalized_name,
        "canonical_name_key": row.canonical_name_key,
        "role": row.name_role,
        "provider": row.provider,
        "status": "needs_review" if row.requires_review else row.status,
        "confidence": None,
        "confidence_score": row.confidence,
        "requires_review": bool(row.requires_review),
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

    name_rows = (
        db.query(SourceNameObservation, SourceMetadataRecord)
        .join(SourceMetadataRecord, SourceNameObservation.source_metadata_record_id == SourceMetadataRecord.id)
        .filter(
            SourceMetadataRecord.media_id == media_id,
            SourceNameObservation.status.in_(ACTIVE_NAME_OBSERVATION_STATUSES),
            SourceNameObservation.name_role.in_(DEFAULT_ASSERTION_ROLES),
        )
        .order_by(
            SourceNameObservation.name_role.asc(),
            SourceNameObservation.provider.asc(),
            SourceNameObservation.raw_name.asc(),
        )
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

    represented_name_keys = {
        (chip["provider"], chip["canonical_name_key"], chip["role"])
        for chip in active_assertions + needs_review_assertions
        if chip.get("canonical_name_key") and chip.get("role")
    }
    for name, record in name_rows:
        key = (name.provider, name.canonical_name_key, name.name_role)
        if key in represented_name_keys:
            continue
        chip = _name_observation_chip(name, record)
        if name.requires_review:
            needs_review_assertions.append(chip)
        else:
            active_assertions.append(chip)
        represented_name_keys.add(key)

    source_tags = [_source_tag_chip(tag, record) for tag, record in source_tag_rows]
    source_concepts = list_media_source_concepts(db, media_id)

    return {
        "media_id": media_id,
        "source_assertions": active_assertions,
        "needs_review_assertions": needs_review_assertions,
        "source_tags": source_tags,
        "source_concepts": source_concepts,
        "counts": {
            "source_assertions": len(active_assertions),
            "needs_review_assertions": len(needs_review_assertions),
            "source_tags": len(source_tags),
            "source_concepts": len(source_concepts),
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
    db: Session | None = None,
) -> Query:
    db = db or query.session
    keys = set()
    keys.update(_source_name_keys_for_text(db, criteria.canonical_name_key))
    keys.update(_source_name_keys_for_text(db, criteria.normalized_input))
    if not keys and criteria.assertion_key:
        keys.update(_source_name_keys_for_text(db, criteria.assertion_key))

    condition = _source_name_condition(
        keys,
        provider=criteria.provider,
        role=criteria.asserted_role,
        include_needs_review=include_needs_review,
    )
    if condition is None:
        return query.filter(false())

    return query.filter(condition)


def _apply_source_tag_filter(query: Query, criteria: SourceTagFilter, *, db: Session | None = None) -> Query:
    db = db or query.session
    tag_keys = set()
    tag_keys.update(_source_concept_key_candidates(criteria.canonical_tag_key))
    tag_keys.update(_source_concept_key_candidates(criteria.normalized_tag))
    if not tag_keys and criteria.observation_key:
        tag_keys.update(_source_concept_key_candidates(criteria.observation_key))

    condition = _source_tag_condition(tag_keys, provider=criteria.provider)
    if condition is None:
        return query.filter(false())

    return query.filter(condition)


def apply_source_layer_filters(
    query: Query,
    source_assertions: Sequence[str] | None = None,
    source_tags: Sequence[str] | None = None,
    *,
    include_needs_review: bool = False,
    db: Session | None = None,
) -> Query:
    """Apply source-layer filters as AND/intersection constraints."""

    for value in _clean_values(source_assertions):
        query = _apply_assertion_filter(
            query,
            parse_source_assertion_filter(value),
            include_needs_review=include_needs_review,
            db=db,
        )

    for value in _clean_values(source_tags):
        query = _apply_source_tag_filter(query, parse_source_tag_filter(value), db=db)

    return query


def apply_source_soft_search(
    query: Query,
    parsed_query: dict[str, Any],
    db: Session,
    *,
    include_needs_review: bool = False,
    include_source_concept_needs_review: bool | None = None,
):
    """Apply ordinary query terms with read-time source concept expansion.

    This keeps normal tag search behavior, but a name-like term may also match
    source assertion/name/source-tag rows and compatible Danbooru-style tags.
    It is read-only and does not create Entity, aliases, media_tags, or truth.
    """

    from ..utils.search_parser import apply_search_criteria

    parsed = {
        "tags": {
            "include": list(parsed_query.get("tags", {}).get("include", [])),
            "exclude": list(parsed_query.get("tags", {}).get("exclude", [])),
            "wildcards": list(parsed_query.get("tags", {}).get("wildcards", [])),
        },
        "meta": {key: list(value) for key, value in (parsed_query.get("meta") or {}).items()},
    }

    remaining_include: list[str] = []
    for term in parsed["tags"]["include"]:
        condition = _soft_search_condition_for_term(
            db,
            term,
            include_needs_review=include_needs_review,
            include_source_concept_needs_review=include_source_concept_needs_review,
        )
        if condition is None:
            remaining_include.append(term)
        else:
            query = query.filter(condition)

    parsed["tags"]["include"] = remaining_include
    remaining_exclude: list[str] = []
    for term in parsed["tags"]["exclude"]:
        condition = _soft_search_condition_for_term(
            db,
            term,
            include_needs_review=include_needs_review,
            include_source_concept_needs_review=include_source_concept_needs_review,
        )
        if condition is None:
            remaining_exclude.append(term)
        else:
            query = query.filter(~condition)

    parsed["tags"]["exclude"] = remaining_exclude
    return apply_search_criteria(query, parsed, db)


def _soft_search_condition_for_term(
    db: Session,
    term: str,
    *,
    include_needs_review: bool = False,
    include_source_concept_needs_review: bool | None = None,
):
    normalized = normalize_source_text(term)
    if not normalized:
        return None

    exact_tag_names = {
        row[0]
        for row in db.query(Tag.name)
        .filter(Tag.name == normalized.casefold())
        .all()
    }
    source_keys = _source_name_keys_for_text(db, normalized)
    source_keys.update(_source_concept_key_candidates(normalized))
    linked_tag_names = _tag_names_for_source_keys(db, source_keys)
    tag_keys = _source_concept_key_candidates(normalized)

    condition = _or_non_empty(
        (
            _media_has_tag_condition(exact_tag_names | linked_tag_names),
            _source_layer_exact_text_condition(normalized, include_needs_review=include_needs_review),
            _source_name_condition(source_keys, include_needs_review=include_needs_review),
            _source_tag_condition(tag_keys),
            source_concept_media_condition_for_term(
                db,
                normalized,
                include_needs_review=include_needs_review
                if include_source_concept_needs_review is None
                else include_source_concept_needs_review,
            ),
        )
    )
    return condition


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
        name_row = _lookup_name_observation_label(db, criteria, include_needs_review=include_needs_review)
        if not name_row:
            return {
                "type": "source_assertion",
                "layer": "source_assertion",
                "display_name": value,
                "search_param": "source_assertion",
                "search_value": value,
                "is_entity_truth": False,
                "missing": True,
            }
        chip = _name_observation_chip(name_row[0], name_row[1])
        chip["search_value"] = value
        chip["result_media_count"] = _count_assertion_media(db, criteria, include_needs_review=include_needs_review)
        return chip

    chip = _assertion_chip(row[0], row[1])
    chip["search_value"] = value
    chip["result_media_count"] = _count_assertion_media(db, criteria, include_needs_review=include_needs_review)
    return chip


def _lookup_name_observation_label(
    db: Session,
    criteria: SourceAssertionFilter,
    *,
    include_needs_review: bool,
) -> tuple[SourceNameObservation, SourceMetadataRecord] | None:
    query = (
        db.query(SourceNameObservation, SourceMetadataRecord)
        .join(SourceMetadataRecord, SourceNameObservation.source_metadata_record_id == SourceMetadataRecord.id)
        .filter(SourceNameObservation.status.in_(ACTIVE_NAME_OBSERVATION_STATUSES))
    )
    query = _filter_name_observation_lookup_query(
        db,
        query,
        criteria,
        include_needs_review=include_needs_review,
    )
    return query.order_by(SourceNameObservation.raw_name.asc()).first()


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


def _filter_name_observation_lookup_query(
    db: Session,
    query: Query,
    criteria: SourceAssertionFilter,
    *,
    include_needs_review: bool,
) -> Query:
    if criteria.provider:
        query = query.filter(SourceNameObservation.provider == criteria.provider)
    if criteria.asserted_role:
        query = query.filter(SourceNameObservation.name_role == criteria.asserted_role)
    if not include_needs_review:
        query = query.filter(SourceNameObservation.requires_review == False)

    keys = set()
    keys.update(_source_name_keys_for_text(db, criteria.canonical_name_key))
    keys.update(_source_name_keys_for_text(db, criteria.normalized_input))
    if not keys and criteria.assertion_key:
        keys.update(_source_name_keys_for_text(db, criteria.assertion_key))

    if keys:
        query = query.filter(SourceNameObservation.canonical_name_key.in_(sorted(keys)))
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
    return int(
        _apply_assertion_filter(
            db.query(Media),
            criteria,
            include_needs_review=include_needs_review,
            db=db,
        ).count()
        or 0
    )


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
