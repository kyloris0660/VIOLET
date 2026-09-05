"""Read-only SourceConcept search and evidence helpers for Phase 4.5-SC2.

SourceConcept rows are an unconfirmed source layer. This module must not create
or mutate Entity, EntityAlias, EntityEvidence, MediaEntityCandidate,
MediaEntityAssignment, LocalSourceHint, media_tags, TagTranslation, or any
confirmed assignment path.
"""

from __future__ import annotations

import re
import hashlib
import json
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Query, Session, aliased

from ..models import (
    Media,
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptFallbackSearchIndex,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
    SourceConceptProductMediaBinding,
    SourceConceptProductRun,
    SourceMetadataRecord,
)
from .source_metadata_registry_service import canonical_source_key, normalize_source_text
from .pixiv_identity_policy import canonical_pixiv_work_id, canonical_pixiv_page_index
from .pixiv_product_media_binding import (
    active_binding_condition, current_binding_columns_condition,
    current_product_alias_condition, current_product_evidence_condition,
)

ACTIVE_SOURCE_CONCEPT_STATUSES = ("active",)
REVIEW_SOURCE_CONCEPT_STATUSES = ("needs_review",)
VISIBLE_SOURCE_CONCEPT_STATUSES = ACTIVE_SOURCE_CONCEPT_STATUSES + REVIEW_SOURCE_CONCEPT_STATUSES
FALLBACK_ELIGIBLE_SIGNAL_STATUSES = (
    "materialized_identity",
    "isolated_evidence",
    "active",
    # Legacy source-layer rows may remain query-visible during reversible
    # supersession. This status is evidence compatibility, never a human queue.
    "needs_review",
)
EVIDENCE_FALLBACK_SIGNAL_STATUSES = FALLBACK_ELIGIBLE_SIGNAL_STATUSES
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
# V2 keeps cannot-link diagnostics but no longer globally suppresses direct
# same-name evidence. Rebuild only on an isolated ML1 clone; accepted R2R rows
# remain immutable historical evidence.
R2R_FALLBACK_INDEX_VERSION = (
    "source_concept_deferred_overlay_v3_stable_signal_identity"
)
R2R_FALLBACK_DISPOSITION_VERSION = "r2r_machine_disposition_v1"
REDACTED_TEXT = "[redacted source value]"
MEDIA_EXTENSION_PARTS = (
    "jpg",
    "jpeg",
    "png",
    "webp",
    "gif",
    "bmp",
    "avif",
    "mp4",
    "webm",
    "mov",
    "zip",
    "rar",
    "7z",
)
PATH_MARKER_PARTS = (
    "users",
    "home",
    "mnt",
    "volumes",
    "icloud",
    "pictures",
    "storage",
    "documents",
    "downloads",
    "desktop",
    "media",
    "original",
    "thumbnails",
    "thumbs",
)
SEARCH_TOKEN_META_RE = re.compile(r'[\s:"*?\[\]\(\)]|^-')


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
    if re.search(r"(?i)\bfile://", text):
        return "file_url"
    if re.search(r"(?i)\b[a-z]:[\\/]", text) or text.startswith("\\\\"):
        return "local_path"
    if re.search(r"(?i)(^|[\\/])(users|home|mnt|volumes|icloud|pictures|storage)([\\/]|$)", text):
        return "local_path"
    if re.search(r"(?i)\.(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov|zip|rar|7z)$", text):
        return "filename_like"
    canonical = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    if not canonical:
        return None
    parts = [part for part in canonical.split("_") if part]
    part_set = set(parts)
    has_extension_part = bool(part_set.intersection(MEDIA_EXTENSION_PARTS))
    has_trailing_extension_part = len(parts) >= 2 and parts[-1] in MEDIA_EXTENSION_PARTS
    has_path_marker = bool(part_set.intersection(PATH_MARKER_PARTS))
    has_windows_user_shape = any(
        idx + 1 < len(parts) and len(part) == 1 and parts[idx + 1] == "users"
        for idx, part in enumerate(parts)
    )
    has_posix_user_shape = any(
        part in {"home", "users"} and idx + 1 < len(parts)
        for idx, part in enumerate(parts)
    )
    if has_windows_user_shape or has_posix_user_shape:
        return "canonical_path"
    if has_path_marker and has_extension_part:
        return "canonical_path"
    if has_trailing_extension_part:
        return "canonical_filename"
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
        .filter(current_product_alias_condition(SourceConceptAlias))
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


def _query_search_index_concept_ids(
    db: Session,
    term: str,
    *,
    include_needs_review: bool = False,
) -> list[int]:
    keys = _search_keys_for_term(term)
    if not keys:
        return []

    statuses = _status_scope(include_needs_review)
    rows = (
        db.query(SourceConcept.id)
        .join(SourceConceptSearchIndex, SourceConcept.id == SourceConceptSearchIndex.concept_id)
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
        .filter(current_product_alias_condition(SourceConceptAlias))
        .filter(SourceConcept.status.in_(statuses))
        .distinct()
        .all()
    )
    # Identity search returns every concept directly matched by its own
    # materialized alias row. A shared surface key may therefore return media
    # from several cannot-linked concepts; result union is not identity union.
    return sorted({int(row[0]) for row in rows})


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

    # 分开物化绑定集合，避免 PostgreSQL 在每个 Media 上重复执行嵌套 OR/EXISTS。
    binding = SourceConceptProductMediaBinding
    bound_ids = select(binding.media_id).where(
        evidence.id == binding.evidence_id,
        evidence.concept_id.in_(ids),
        evidence.status.in_(statuses),
        current_binding_columns_condition(binding, SourceConceptProductRun, SourceMetadataRecord),
    ).correlate(None)
    return or_(evidence_condition, signal_condition, Media.id.in_(bound_ids))


def _query_overlay_fallback_rows(
    db: Session,
    keys: set[str],
) -> list[tuple[int]]:
    """Load only indexed overlay rows matching the normalized query keys."""

    if not keys:
        return []
    source_signal = aliased(SourceConceptSignal)
    neighbor_signal = aliased(SourceConceptSignal)
    return (
        db.query(SourceConceptFallbackSearchIndex.media_id)
        .join(source_signal, source_signal.id == SourceConceptFallbackSearchIndex.source_signal_id)
        .join(neighbor_signal, neighbor_signal.id == SourceConceptFallbackSearchIndex.neighbor_signal_id)
        .filter(SourceConceptFallbackSearchIndex.alias_key.in_(sorted(keys)))
        .filter(SourceConceptFallbackSearchIndex.status == "active")
        .filter(
            SourceConceptFallbackSearchIndex.overlay_version
            == R2R_FALLBACK_INDEX_VERSION
        )
        .filter(
            SourceConceptFallbackSearchIndex.relation.in_(
                ("direct_evidence", "must_link", "deferred_nonblocking")
            )
        )
        .filter(source_signal.status.in_(FALLBACK_ELIGIBLE_SIGNAL_STATUSES))
        .filter(neighbor_signal.status.in_(FALLBACK_ELIGIBLE_SIGNAL_STATUSES))
        .filter(SourceConceptFallbackSearchIndex.media_id.isnot(None))
        .distinct()
        .all()
    )


def _overlay_fallback_media_ids(db: Session, keys: set[str]) -> set[int]:
    """Resolve indexed evidence fallback without scanning all signals in Python."""

    return {int(row[0]) for row in _query_overlay_fallback_rows(db, keys)}


def _blocked_cannot_alias_keys(db: Session, keys: set[str]) -> set[str]:
    if not keys:
        return set()
    return {
        str(row[0])
        for row in (
            db.query(SourceConceptFallbackSearchIndex.alias_key)
            .filter(SourceConceptFallbackSearchIndex.alias_key.in_(sorted(keys)))
            .filter(SourceConceptFallbackSearchIndex.status == "blocked")
            .filter(SourceConceptFallbackSearchIndex.relation == "cannot_link")
            .filter(
                SourceConceptFallbackSearchIndex.overlay_version
                == R2R_FALLBACK_INDEX_VERSION
            )
            .distinct()
            .all()
        )
    }


def rebuild_source_concept_fallback_search_index(
    db: Session,
    *,
    signals: Sequence[Any],
    dispositions: Sequence[Any],
    run_id: str,
    cannot_pairs: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    """Deterministically rebuild the non-materialized source-layer lookup."""

    signal_rows = {
        str(row.signal_key): row
        for row in db.query(SourceConceptSignal)
        .filter(SourceConceptSignal.signal_key.in_([str(signal.signal_key) for signal in signals]))
        .all()
    }
    signal_drafts = {str(signal.signal_key): signal for signal in signals}
    eligible_signal_keys = {
        signal_key
        for signal_key, signal in signal_drafts.items()
        if str(signal.status) in FALLBACK_ELIGIBLE_SIGNAL_STATUSES
    }
    blocked_alias_pairs: dict[str, set[tuple[str, str]]] = {}
    for left_key, right_key in cannot_pairs:
        left = signal_drafts.get(str(left_key))
        right = signal_drafts.get(str(right_key))
        if (
            left is None
            or right is None
            or str(left_key) not in eligible_signal_keys
            or str(right_key) not in eligible_signal_keys
        ):
            continue
        left_aliases = {
            str(value) for value in (left.canonical_key, left.normalized_key) if value
        }
        right_aliases = {
            str(value) for value in (right.canonical_key, right.normalized_key) if value
        }
        for alias_key in left_aliases.intersection(right_aliases):
            blocked_alias_pairs.setdefault(alias_key, set()).add(
                tuple(sorted((str(left_key), str(right_key))))
            )

    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for signal_key in sorted(eligible_signal_keys):
        signal = signal_drafts[signal_key]
        signal_row = signal_rows.get(signal_key)
        if signal_row is None or signal.media_id is None:
            continue
        pair_id = hashlib.sha256(f"direct:{signal_key}".encode("utf-8")).hexdigest()
        for alias_key in sorted(
            {
                str(value)
                for value in (signal.canonical_key, signal.normalized_key)
                if value
            }
        ):
            key = (
                alias_key,
                int(signal.media_id),
                int(signal_row.id),
                int(signal_row.id),
                pair_id,
                R2R_FALLBACK_INDEX_VERSION,
            )
            rows[key] = {
                "alias_key": alias_key,
                "media_id": int(signal.media_id),
                "source_signal_id": int(signal_row.id),
                "neighbor_signal_id": int(signal_row.id),
                "pair_id": pair_id,
                "relation": "direct_evidence",
                "overlay_version": R2R_FALLBACK_INDEX_VERSION,
                "disposition_version": R2R_FALLBACK_DISPOSITION_VERSION,
                "role_hint": str(signal.role_hint or "unknown"),
                "work_context_key": signal.work_context_key,
                "provenance_payload": {
                    "source_layer_only": True,
                    "identity_union_allowed": False,
                    "human_review_required": False,
                    "direct_signal_evidence": True,
                },
                "status": "active",
                "run_id": run_id,
            }
    for disposition in sorted(dispositions, key=lambda row: str(row.pair_id)):
        relation = str(disposition.disposition)
        if relation not in {"must_link", "deferred_nonblocking"}:
            continue
        left = signal_drafts.get(str(disposition.left_signal_key))
        right = signal_drafts.get(str(disposition.right_signal_key))
        if (
            left is None
            or right is None
            or str(disposition.left_signal_key) not in eligible_signal_keys
            or str(disposition.right_signal_key) not in eligible_signal_keys
        ):
            continue
        for query_signal, target_signal in ((left, right), (right, left)):
            query_row = signal_rows.get(str(query_signal.signal_key))
            target_row = signal_rows.get(str(target_signal.signal_key))
            if query_row is None or target_row is None or target_signal.media_id is None:
                continue
            for alias_key in sorted(
                {
                    str(value)
                    for value in (query_signal.canonical_key, query_signal.normalized_key)
                    if value
                }
            ):
                key = (
                    alias_key,
                    int(target_signal.media_id),
                    int(target_row.id),
                    int(query_row.id),
                    str(disposition.pair_id),
                    R2R_FALLBACK_INDEX_VERSION,
                )
                rows[key] = {
                    "alias_key": alias_key,
                    "media_id": int(target_signal.media_id),
                    "source_signal_id": int(target_row.id),
                    "neighbor_signal_id": int(query_row.id),
                    "pair_id": str(disposition.pair_id),
                    "relation": relation,
                    "overlay_version": R2R_FALLBACK_INDEX_VERSION,
                    "disposition_version": R2R_FALLBACK_DISPOSITION_VERSION,
                    "role_hint": str(target_signal.role_hint or "unknown"),
                    "work_context_key": target_signal.work_context_key,
                    "provenance_payload": {
                        "source_layer_only": True,
                        "identity_union_allowed": False,
                        "human_review_required": False,
                    },
                    "status": "active",
                    "run_id": run_id,
                }

    for alias_key, pairs in sorted(blocked_alias_pairs.items()):
        for left_key, right_key in sorted(pairs):
            left_row = signal_rows.get(left_key)
            right_row = signal_rows.get(right_key)
            if left_row is None or right_row is None:
                continue
            media_id = left_row.media_id if left_row.media_id is not None else right_row.media_id
            pair_id = hashlib.sha256(
                json.dumps([left_key, right_key], separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            key = (
                alias_key,
                int(media_id) if media_id is not None else None,
                int(left_row.id),
                int(right_row.id),
                pair_id,
                R2R_FALLBACK_INDEX_VERSION,
            )
            rows[key] = {
                "alias_key": alias_key,
                "media_id": int(media_id) if media_id is not None else None,
                "source_signal_id": int(left_row.id),
                "neighbor_signal_id": int(right_row.id),
                "pair_id": pair_id,
                "relation": "cannot_link",
                "overlay_version": R2R_FALLBACK_INDEX_VERSION,
                "disposition_version": R2R_FALLBACK_DISPOSITION_VERSION,
                "role_hint": "constraint_guard",
                "work_context_key": None,
                "provenance_payload": {
                    "source_layer_only": True,
                    "identity_union_allowed": False,
                    "cannot_ambiguous_alias_guard": True,
                },
                "status": "blocked",
                "run_id": run_id,
            }

    fingerprint_payload = [
        {
            "alias_key": values["alias_key"],
            "media_id": values["media_id"],
            "source_signal_id": values["source_signal_id"],
            "neighbor_signal_id": values["neighbor_signal_id"],
            "pair_id": values["pair_id"],
            "relation": values["relation"],
            "overlay_version": values["overlay_version"],
            "disposition_version": values["disposition_version"],
            "role_hint": values["role_hint"],
            "work_context_key": values["work_context_key"],
            "provenance_payload": values["provenance_payload"],
            "status": values["status"],
        }
        for values in sorted(
            rows.values(),
            key=lambda item: (
                str(item["alias_key"]),
                int(item["media_id"]) if item["media_id"] is not None else -1,
                int(item["source_signal_id"]),
                int(item["neighbor_signal_id"]),
                str(item["pair_id"]),
            ),
        )
    ]
    deterministic_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    existing_payload = [
        {
            "alias_key": row.alias_key,
            "media_id": row.media_id,
            "source_signal_id": row.source_signal_id,
            "neighbor_signal_id": row.neighbor_signal_id,
            "pair_id": row.pair_id,
            "relation": row.relation,
            "overlay_version": row.overlay_version,
            "disposition_version": row.disposition_version,
            "role_hint": row.role_hint,
            "work_context_key": row.work_context_key,
            "provenance_payload": row.provenance_payload,
            "status": row.status,
        }
        for row in db.query(SourceConceptFallbackSearchIndex)
        .filter(
            SourceConceptFallbackSearchIndex.overlay_version
            == R2R_FALLBACK_INDEX_VERSION
        )
        .order_by(
            SourceConceptFallbackSearchIndex.alias_key.asc(),
            SourceConceptFallbackSearchIndex.media_id.asc().nullsfirst(),
            SourceConceptFallbackSearchIndex.source_signal_id.asc(),
            SourceConceptFallbackSearchIndex.neighbor_signal_id.asc(),
            SourceConceptFallbackSearchIndex.pair_id.asc(),
        )
        .all()
    ]
    existing_payload.sort(
        key=lambda item: (
            str(item["alias_key"]),
            int(item["media_id"]) if item["media_id"] is not None else -1,
            int(item["source_signal_id"]),
            int(item["neighbor_signal_id"]),
            str(item["pair_id"]),
        )
    )
    existing_fingerprint = hashlib.sha256(
        json.dumps(
            existing_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    write_performed = existing_fingerprint != deterministic_fingerprint
    if write_performed:
        db.query(SourceConceptFallbackSearchIndex).filter(
            SourceConceptFallbackSearchIndex.overlay_version == R2R_FALLBACK_INDEX_VERSION
        ).delete(synchronize_session=False)
        for values in rows.values():
            db.add(SourceConceptFallbackSearchIndex(**values))
        db.flush()
    return {
        "index_version": R2R_FALLBACK_INDEX_VERSION,
        "row_count": len(rows),
        "unique_alias_key_count": len({key[0] for key in rows}),
        "blocked_cannot_alias_key_count": len(blocked_alias_pairs),
        "active_row_count": sum(row["status"] == "active" for row in rows.values()),
        "blocked_row_count": sum(row["status"] == "blocked" for row in rows.values()),
        "relation_counts": dict(sorted(Counter(row["relation"] for row in rows.values()).items())),
        "deterministic_fingerprint": deterministic_fingerprint,
        "write_performed": write_performed,
        "identity_union_allowed": False,
        "full_signal_python_scan_per_query": False,
    }


def source_concept_media_condition_for_term(
    db: Session,
    term: str,
    *,
    include_needs_review: bool = False,
    include_evidence_fallback: bool = False,
):
    """Return a read-only Media condition for SourceConcept expansion."""

    keys = _search_keys_for_term(term)
    concept_ids = _query_search_index_concept_ids(
        db,
        term,
        include_needs_review=include_needs_review,
    )
    identity_condition = _source_concept_media_condition(
        concept_ids,
        include_needs_review=include_needs_review,
    )
    if not include_evidence_fallback or not keys:
        return identity_condition
    overlay_media_ids = _overlay_fallback_media_ids(db, keys)
    evidence_fallback_condition = Media.id.in_(sorted(overlay_media_ids)) if overlay_media_ids else None
    if identity_condition is None:
        return evidence_fallback_condition
    if evidence_fallback_condition is None:
        return identity_condition
    return or_(identity_condition, evidence_fallback_condition)


def source_layer_search_path_media_ids(
    db: Session,
    term: str,
    *,
    include_needs_review: bool = True,
    include_evidence_fallback: bool = False,
) -> dict[str, set[int]]:
    """Return separate identity and evidence-fallback result sets for QA.

    The fallback set contains media with direct matching signal evidence or a
    versioned pair-overlay neighbor. It never traverses alias-sharing concepts
    or creates identity unions.
    """

    keys = _search_keys_for_term(term)
    concept_ids = _query_search_index_concept_ids(
        db,
        term,
        include_needs_review=include_needs_review,
    )
    identity_ids: set[int] = set()
    if concept_ids:
        identity_ids.update(int(row[0]) for row in db.query(Media.id).filter(
            _source_concept_media_condition(concept_ids, include_needs_review=include_needs_review)
        ).all())
        statuses = _status_scope(include_needs_review)
        identity_ids.update(
            int(row[0])
            for row in (
                db.query(SourceConceptEvidence.media_id)
                .filter(SourceConceptEvidence.concept_id.in_(concept_ids))
                .filter(SourceConceptEvidence.status.in_(statuses))
                .filter(SourceConceptEvidence.media_id.isnot(None))
                .distinct()
                .all()
            )
        )
        identity_ids.update(
            int(row[0])
            for row in (
                db.query(SourceConceptSignal.media_id)
                .join(
                    SourceConceptSignalLink,
                    SourceConceptSignalLink.signal_id == SourceConceptSignal.id,
                )
                .filter(SourceConceptSignalLink.concept_id.in_(concept_ids))
                .filter(SourceConceptSignalLink.link_status.in_(statuses))
                .filter(SourceConceptSignal.status.in_(statuses))
                .filter(SourceConceptSignal.media_id.isnot(None))
                .distinct()
                .all()
            )
        )

    fallback_ids: set[int] = set()
    if include_evidence_fallback and keys:
        fallback_ids.update(
            _overlay_fallback_media_ids(db, keys)
        )
    return {
        "identity": identity_ids,
        "evidence_fallback": fallback_ids,
        "combined": identity_ids | fallback_ids,
    }


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
        "redacted": bool(
            _unsafe_text_reason(alias.display_name)
            or _unsafe_text_reason(alias.alias_value)
            or _unsafe_text_reason(alias.alias_key)
        ),
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
        .filter(current_product_alias_condition(SourceConceptAlias))
        .order_by(SourceConceptAlias.status.asc(), SourceConceptAlias.confidence.desc().nullslast(), SourceConceptAlias.display_name.asc())
        .limit(MAX_ALIASES_PER_CONCEPT)
        .all()
    )
    evidence_query = (
        db.query(SourceConceptEvidence)
        .filter(SourceConceptEvidence.concept_id == concept.id)
        .filter(SourceConceptEvidence.status.in_(statuses))
        .filter(current_product_evidence_condition(SourceConceptEvidence))
    )
    if media_id is not None:
        evidence_query = evidence_query.filter(or_(
            SourceConceptEvidence.media_id == media_id,
            active_binding_condition(SourceConceptEvidence.id, media_id),
        ))
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
        .filter(current_product_evidence_condition(SourceConceptEvidence))
        .all()
    )
    linked_media_count = db.query(Media.id).filter(
        _source_concept_media_condition([concept.id], include_needs_review=True)
    ).count()
    evidence_count = int(
        db.query(func.count(SourceConceptEvidence.id))
        .filter(SourceConceptEvidence.concept_id == concept.id)
        .filter(SourceConceptEvidence.status.in_(statuses))
        .filter(current_product_evidence_condition(SourceConceptEvidence))
        .scalar()
        or 0
    )

    display_name = _safe_text(concept.primary_display_name, fallback=f"SourceConcept {concept.id}")
    aliases = [_alias_payload(alias) for alias in alias_rows]
    search_label = next((alias["display_name"] for alias in aliases if alias.get("display_name") and not alias.get("redacted")), display_name)
    if (not any(alias.display_name == concept.primary_display_name for alias in alias_rows)
        and db.query(SourceConceptProductRun.id).filter_by(resolver_run_id=concept.created_by_run_id).first()):
        display_name = search_label if aliases else f'SourceConcept {concept.id}'
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
                "media_scope": "current_media" if media_id is not None else "linked_media",
            }
            for row in evidence_rows
        ]
        binding = SourceConceptProductMediaBinding
        binding_query = db.query(binding, SourceConceptSignal).join(
            SourceConceptEvidence, SourceConceptEvidence.id == binding.evidence_id
        ).join(SourceConceptSignal, SourceConceptSignal.id == SourceConceptEvidence.signal_id).join(
            SourceConceptProductRun, SourceConceptProductRun.id == binding.product_run_id
        ).join(SourceMetadataRecord, SourceMetadataRecord.id == binding.source_metadata_record_id
        ).filter(SourceConceptEvidence.concept_id == concept.id,
                 SourceConceptEvidence.status.in_(statuses),
                 current_binding_columns_condition(binding, SourceConceptProductRun, SourceMetadataRecord))
        if media_id is not None:
            binding_query = binding_query.filter(binding.media_id == media_id)
        payload['local_media_support'] = [
            {'provider': 'pixiv', 'work_id': canonical_pixiv_work_id((signal.evidence_payload or {}).get('work_id')),
             'page_index': canonical_pixiv_page_index((signal.evidence_payload or {}).get('page_index')),
             'source_metadata_record_id': edge.source_metadata_record_id,
             'media_id': edge.media_id}
            for edge, signal in binding_query.limit(MAX_EVIDENCE_ITEMS_PER_CONCEPT).all()
        ]
    return payload


def _build_search_url(value: str | None, *, include_needs_review: bool = False) -> str:
    text = normalize_source_text(value)
    if not text:
        return "/"
    token = _format_search_query_token(text)
    from urllib.parse import urlencode

    params = {"q": token}
    if include_needs_review:
        params["include_source_needs_review"] = "1"
    return "/?" + urlencode(params)


def _format_search_query_token(value: str | None) -> str:
    text = normalize_source_text(value)
    if not text:
        return ""
    if SEARCH_TOKEN_META_RE.search(text):
        return f'"{text.replace(chr(34), "")}"'
    return text


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
            .filter(or_(SourceConceptEvidence.media_id == media_id,
                        active_binding_condition(SourceConceptEvidence.id, media_id)))
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
    concept = (
        db.query(SourceConcept)
        .filter(SourceConcept.id == concept_id)
        .filter(SourceConcept.status.in_(VISIBLE_SOURCE_CONCEPT_STATUSES))
        .one_or_none()
    )
    if concept is None:
        return None
    return _concept_summary(db, concept, include_evidence_items=True)


def preview_source_concept_promotion(
    db: Session,
    concept_id: int,
    *,
    limit: int = 50,
) -> dict[str, Any] | None:
    concept = (
        db.query(SourceConcept)
        .filter(SourceConcept.id == concept_id)
        .filter(SourceConcept.status.in_(VISIBLE_SOURCE_CONCEPT_STATUSES))
        .one_or_none()
    )
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
