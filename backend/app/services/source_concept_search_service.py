"""Read-only SourceConcept search and evidence helpers for Phase 4.5-SC2.

SourceConcept rows are an unconfirmed source layer. This module must not create
or mutate Entity, EntityAlias, EntityEvidence, MediaEntityCandidate,
MediaEntityAssignment, LocalSourceHint, media_tags, TagTranslation, or any
confirmed assignment path.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import Query, Session, aliased

from ..models import (
    Media,
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
)
from .source_metadata_registry_service import canonical_source_key, normalize_source_text

ACTIVE_SOURCE_CONCEPT_STATUSES = ("active",)
REVIEW_SOURCE_CONCEPT_STATUSES = ("needs_review",)
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
MAX_SEARCH_EXPANSIONS_PER_TERM = 8
MAX_ALIASES_PER_CONCEPT = 18
MAX_EVIDENCE_ITEMS_PER_CONCEPT = 12
REDACTED_TEXT = "[redacted source value]"


def _status_scope(include_needs_review: bool) -> tuple[str, ...]:
    if include_needs_review:
        return ACTIVE_SOURCE_CONCEPT_STATUSES + REVIEW_SOURCE_CONCEPT_STATUSES
    return ACTIVE_SOURCE_CONCEPT_STATUSES


def _search_keys_for_term(value: str | None) -> set[str]:
    normalized = normalize_source_text(value)
    if not normalized:
        return set()

    variants = {
        normalized,
        normalized.casefold(),
        normalized.replace("_", " "),
        normalized.replace("_(", "("),
        canonical_source_key(normalized),
    }
    parenthetical = re.match(r"^(.+?)_?\(([^()]+)\)$", normalized.replace("\uff08", "(").replace("\uff09", ")"))
    if parenthetical:
        variants.add(parenthetical.group(1))

    keys = {canonical_source_key(variant) for variant in variants if normalize_source_text(variant)}
    return {key for key in keys if key}


def _unsafe_text_reason(value: Any) -> str | None:
    text = normalize_source_text(value)
    if not text:
        return None
    if re.search(r"(?i)(api[_-]?key|secret|token|password|authorization)", text):
        return "secret_like"
    if re.search(r"(?i)\b[a-z]:[\\/]", text) or text.startswith("\\\\"):
        return "local_path"
    if re.search(r"(?i)(^|[\\/])(users|home|mnt|volumes|icloud|pictures|storage)([\\/]|$)", text):
        return "local_path"
    if re.search(r"(?i)\.(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov|zip|rar|7z)$", text):
        return "filename_like"
    return None


def _safe_text(value: Any, *, fallback: str | None = None) -> str | None:
    text = normalize_source_text(value)
    if not text:
        return fallback
    if _unsafe_text_reason(text):
        return fallback if fallback is not None else REDACTED_TEXT
    return text


def _safe_list(values: Iterable[Any], *, limit: int | None = None) -> list[str]:
    safe_values = []
    seen = set()
    for value in values:
        safe = _safe_text(value)
        if not safe or safe in seen:
            continue
        seen.add(safe)
        safe_values.append(safe)
        if limit is not None and len(safe_values) >= limit:
            break
    return safe_values


def _query_search_index_rows(
    db: Session,
    term: str,
    *,
    include_needs_review: bool = False,
    active_only_for_hint: bool = False,
) -> list[tuple[SourceConceptSearchIndex, SourceConcept, SourceConceptAlias]]:
    keys = _search_keys_for_term(term)
    if not keys:
        return []

    statuses = _status_scope(include_needs_review)
    query = (
        db.query(SourceConceptSearchIndex, SourceConcept, SourceConceptAlias)
        .join(SourceConcept, SourceConcept.id == SourceConceptSearchIndex.concept_id)
        .join(
            SourceConceptAlias,
            and_(
                SourceConceptAlias.concept_id == SourceConceptSearchIndex.concept_id,
                SourceConceptAlias.alias_key == SourceConceptSearchIndex.search_key,
                SourceConceptAlias.alias_role == SourceConceptSearchIndex.alias_role,
            ),
        )
        .filter(SourceConceptSearchIndex.search_key.in_(sorted(keys)))
        .filter(SourceConceptSearchIndex.status.in_(statuses))
        .filter(SourceConceptAlias.status.in_(statuses))
        .filter(SourceConcept.status.in_(statuses))
    )
    if active_only_for_hint:
        query = query.filter(SourceConcept.status == "needs_review")

    return (
        query.order_by(
            SourceConcept.status.asc(),
            SourceConceptSearchIndex.weight.desc(),
            SourceConcept.primary_display_name.asc(),
            SourceConceptSearchIndex.display_name.asc(),
        )
        .limit(MAX_SEARCH_EXPANSIONS_PER_TERM * 3)
        .all()
    )


def _source_concept_media_condition(concept_ids: Sequence[int], *, include_needs_review: bool = False):
    ids = sorted({int(concept_id) for concept_id in concept_ids if concept_id is not None})
    if not ids:
        return None

    statuses = _status_scope(include_needs_review)

    evidence = aliased(SourceConceptEvidence)
    evidence_condition = exists().where(
        and_(
            evidence.concept_id.in_(ids),
            evidence.media_id == Media.id,
            evidence.status.in_(statuses),
        )
    )

    link = aliased(SourceConceptSignalLink)
    signal = aliased(SourceConceptSignal)
    signal_condition = exists().where(
        and_(
            link.concept_id.in_(ids),
            link.link_status.in_(statuses),
            signal.id == link.signal_id,
            signal.media_id == Media.id,
            signal.status.in_(statuses),
        )
    )

    return or_(evidence_condition, signal_condition)


def source_concept_media_condition_for_term(
    db: Session,
    term: str,
    *,
    include_needs_review: bool = False,
):
    """Return a read-only Media condition for SourceConcept expansion."""

    rows = _query_search_index_rows(db, term, include_needs_review=include_needs_review)
    concept_ids = [concept.id for _search, concept, _alias in rows]
    return _source_concept_media_condition(concept_ids, include_needs_review=include_needs_review)


def apply_source_concept_filter(
    query: Query,
    concept_ids: Sequence[int],
    *,
    include_needs_review: bool = True,
) -> Query:
    condition = _source_concept_media_condition(concept_ids, include_needs_review=include_needs_review)
    if condition is None:
        from sqlalchemy import false

        return query.filter(false())
    return query.filter(condition)


def _alias_payload(alias: SourceConceptAlias) -> dict[str, Any]:
    display_name = _safe_text(alias.display_name) or _safe_text(alias.alias_value) or REDACTED_TEXT
    return {
        "id": alias.id,
        "display_name": display_name,
        "alias_value": _safe_text(alias.alias_value, fallback=display_name),
        "alias_key": _safe_text(alias.alias_key, fallback=REDACTED_TEXT),
        "alias_role": _safe_text(alias.alias_role, fallback="unknown"),
        "status": alias.status,
        "confidence": alias.confidence,
        "redacted": bool(_unsafe_text_reason(alias.display_name) or _unsafe_text_reason(alias.alias_value)),
    }


def _matched_alias_payload(search: SourceConceptSearchIndex, alias: SourceConceptAlias) -> dict[str, Any]:
    payload = _alias_payload(alias)
    payload.update(
        {
            "search_key": _safe_text(search.search_key, fallback=REDACTED_TEXT),
            "weight": search.weight,
            "search_status": search.status,
        }
    )
    return payload


def _concept_summary(
    db: Session,
    concept: SourceConcept,
    *,
    media_id: int | None = None,
    matched_aliases: Sequence[dict[str, Any]] = (),
    include_evidence_items: bool = False,
) -> dict[str, Any]:
    statuses = _status_scope(True)
    alias_rows = (
        db.query(SourceConceptAlias)
        .filter(SourceConceptAlias.concept_id == concept.id)
        .filter(SourceConceptAlias.status.in_(statuses))
        .order_by(SourceConceptAlias.status.asc(), SourceConceptAlias.confidence.desc().nullslast(), SourceConceptAlias.display_name.asc())
        .limit(MAX_ALIASES_PER_CONCEPT)
        .all()
    )
    evidence_query = (
        db.query(SourceConceptEvidence)
        .filter(SourceConceptEvidence.concept_id == concept.id)
        .filter(SourceConceptEvidence.status.in_(statuses))
    )
    if media_id is not None:
        evidence_query = evidence_query.filter(SourceConceptEvidence.media_id == media_id)
    evidence_rows = (
        evidence_query.order_by(
            SourceConceptEvidence.status.asc(),
            SourceConceptEvidence.evidence_strength.asc(),
            SourceConceptEvidence.provider.asc().nullslast(),
            SourceConceptEvidence.evidence_type.asc(),
        )
        .limit(MAX_EVIDENCE_ITEMS_PER_CONCEPT)
        .all()
    )
    all_evidence_rows = (
        db.query(SourceConceptEvidence.provider, SourceConceptEvidence.evidence_type, SourceConceptEvidence.evidence_strength)
        .filter(SourceConceptEvidence.concept_id == concept.id)
        .filter(SourceConceptEvidence.status.in_(statuses))
        .all()
    )
    linked_media_count = int(
        db.query(func.count(func.distinct(SourceConceptEvidence.media_id)))
        .filter(SourceConceptEvidence.concept_id == concept.id)
        .filter(SourceConceptEvidence.status.in_(statuses))
        .filter(SourceConceptEvidence.media_id.isnot(None))
        .scalar()
        or 0
    )
    evidence_count = int(
        db.query(func.count(SourceConceptEvidence.id))
        .filter(SourceConceptEvidence.concept_id == concept.id)
        .filter(SourceConceptEvidence.status.in_(statuses))
        .scalar()
        or 0
    )

    display_name = _safe_text(concept.primary_display_name, fallback=f"SourceConcept {concept.id}")
    concept_key = _safe_text(concept.concept_key, fallback=f"redacted:{concept.id}")
    aliases = [_alias_payload(alias) for alias in alias_rows]
    search_label = next((alias["display_name"] for alias in aliases if alias.get("display_name") and not alias.get("redacted")), display_name)
    providers = _safe_list((row[0] for row in all_evidence_rows), limit=12)
    signal_origins = _safe_list((row[1] for row in all_evidence_rows), limit=12)
    trust_tiers = _safe_list((row[2] for row in all_evidence_rows), limit=12)

    payload = {
        "type": "source_concept",
        "layer": "source_concept",
        "label": "SourceConcept",
        "label_zh": "\u6765\u6e90\u6982\u5ff5",
        "unconfirmed_label_zh": "\u672a\u786e\u8ba4\u6765\u6e90\u6982\u5ff5",
        "source_layer_label": "unconfirmed source-layer",
        "is_entity_truth": False,
        "is_confirmed_entity": False,
        "truth_writes_allowed": False,
        "concept_id": concept.id,
        "id": concept.id,
        "concept_key": concept_key,
        "concept_key_redacted": concept_key.startswith("redacted:"),
        "display_name": display_name,
        "primary_display_name": display_name,
        "concept_type_hint": _safe_text(concept.concept_type_hint, fallback="unknown"),
        "status": concept.status,
        "confidence_score": concept.confidence_score,
        "evidence_score": concept.evidence_score,
        "media_count": concept.media_count,
        "source_count": concept.source_count,
        "aliases": aliases,
        "matched_aliases": list(matched_aliases),
        "providers": providers,
        "signal_origins": signal_origins,
        "trust_tiers": trust_tiers,
        "evidence_count": evidence_count,
        "linked_media_count": linked_media_count,
        "search_label": search_label,
        "search_param": "q",
        "search_value": search_label,
        "search_url": _build_search_url(search_label, include_needs_review=concept.status == "needs_review"),
        "manual_promotion": {
            "preview_only": True,
            "disabled": True,
            "truth_writes_allowed": False,
            "forbidden_paths": list(FORBIDDEN_TRUTH_PATHS),
            "affected_media_count": linked_media_count,
        },
    }
    if include_evidence_items:
        payload["evidence_items"] = [
            {
                "id": row.id,
                "provider": _safe_text(row.provider, fallback="unknown"),
                "evidence_type": _safe_text(row.evidence_type, fallback="unknown"),
                "evidence_strength": _safe_text(row.evidence_strength, fallback="unknown"),
                "status": row.status,
                "media_scope": "current_media" if media_id is not None and row.media_id == media_id else "linked_media",
            }
            for row in evidence_rows
        ]
    return payload


def _build_search_url(value: str | None, *, include_needs_review: bool = False) -> str:
    text = normalize_source_text(value)
    if not text:
        return "/"
    token = f'"{text.replace(chr(34), "")}"' if re.search(r"\s", text) else text
    from urllib.parse import urlencode

    params = {"q": token}
    if include_needs_review:
        params["include_source_needs_review"] = "1"
    return "/?" + urlencode(params)


def resolve_source_concept_query_expansions(
    db: Session,
    parsed_query: dict[str, Any],
    *,
    include_needs_review: bool = False,
) -> list[dict[str, Any]]:
    """Return safe explanation chips for SourceConcept-expanded query terms."""

    tags = parsed_query.get("tags") or {}
    term_specs = [(term, False) for term in tags.get("include", [])]
    term_specs.extend((term, True) for term in tags.get("exclude", []))
    expansions: list[dict[str, Any]] = []

    for term, negated in term_specs:
        rows = _query_search_index_rows(db, term, include_needs_review=include_needs_review)
        grouped: dict[int, dict[str, Any]] = {}
        concept_by_id: dict[int, SourceConcept] = {}
        for search, concept, alias in rows:
            if concept.id not in grouped:
                grouped[concept.id] = {
                    "term": term,
                    "negated": negated,
                    "matched_aliases": [],
                }
                concept_by_id[concept.id] = concept
            grouped[concept.id]["matched_aliases"].append(_matched_alias_payload(search, alias))

        for concept_id, item in list(grouped.items())[:MAX_SEARCH_EXPANSIONS_PER_TERM]:
            summary = _concept_summary(
                db,
                concept_by_id[concept_id],
                matched_aliases=item["matched_aliases"],
                include_evidence_items=False,
            )
            summary["term"] = term
            summary["negated"] = negated
            summary["expansion_kind"] = "source_concept_search_index"
            expansions.append(summary)

    return expansions


def resolve_source_concept_needs_review_hints(
    db: Session,
    parsed_query: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return display-only hints for review concepts that were not expanded."""

    tags = parsed_query.get("tags") or {}
    hints: list[dict[str, Any]] = []
    for term in list(tags.get("include", [])) + list(tags.get("exclude", [])):
        rows = _query_search_index_rows(
            db,
            term,
            include_needs_review=True,
            active_only_for_hint=True,
        )
        active_rows = _query_search_index_rows(db, term, include_needs_review=False)
        if active_rows:
            continue
        seen = set()
        for search, concept, alias in rows:
            if concept.id in seen:
                continue
            seen.add(concept.id)
            summary = _concept_summary(
                db,
                concept,
                matched_aliases=[_matched_alias_payload(search, alias)],
                include_evidence_items=False,
            )
            summary["term"] = term
            summary["expanded"] = False
            summary["requires_opt_in"] = True
            hints.append(summary)
            if len(hints) >= MAX_SEARCH_EXPANSIONS_PER_TERM:
                return hints
    return hints


def list_media_source_concepts(db: Session, media_id: int) -> list[dict[str, Any]]:
    """Return SourceConcept groups linked to a media item without write side effects."""

    statuses = _status_scope(True)
    concept_ids = [
        row[0]
        for row in (
            db.query(SourceConcept.id)
            .join(SourceConceptEvidence, SourceConceptEvidence.concept_id == SourceConcept.id)
            .filter(SourceConceptEvidence.media_id == media_id)
            .filter(SourceConceptEvidence.status.in_(statuses))
            .filter(SourceConcept.status.in_(statuses))
            .distinct()
            .all()
        )
    ]
    if not concept_ids:
        return []

    concept_rows = (
        db.query(SourceConcept)
        .filter(SourceConcept.id.in_(concept_ids))
        .order_by(SourceConcept.status.asc(), SourceConcept.primary_display_name.asc(), SourceConcept.id.asc())
        .all()
    )
    return [
        _concept_summary(db, concept, media_id=media_id, include_evidence_items=True)
        for concept in concept_rows
    ]


def get_source_concept_detail(db: Session, concept_id: int) -> dict[str, Any] | None:
    concept = db.query(SourceConcept).filter(SourceConcept.id == concept_id).one_or_none()
    if concept is None:
        return None
    return _concept_summary(db, concept, include_evidence_items=True)


def preview_source_concept_promotion(
    db: Session,
    concept_id: int,
    *,
    limit: int = 50,
) -> dict[str, Any] | None:
    concept = db.query(SourceConcept).filter(SourceConcept.id == concept_id).one_or_none()
    if concept is None:
        return None

    query = apply_source_concept_filter(
        db.query(Media).order_by(Media.uploaded_at.desc(), Media.id.desc()),
        [concept_id],
        include_needs_review=True,
    )
    total = int(query.count() or 0)
    rows = query.limit(limit).all()
    summary = _concept_summary(db, concept, include_evidence_items=False)
    return {
        "preview_only": True,
        "disabled": True,
        "truth_writes_allowed": False,
        "forbidden_paths": list(FORBIDDEN_TRUTH_PATHS),
        "source_concept": summary,
        "affected_media_count": total,
        "affected_media": [
            {
                "id": media.id,
                "thumbnail_url": f"/api/media/{media.id}/thumbnail" if media.thumbnail_path else None,
            }
            for media in rows
        ],
    }
