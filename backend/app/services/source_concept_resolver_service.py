"""Phase 4.5-SC1 provider-neutral source concept resolver core.

The SourceConcept resolver is a source-layer soft linker. It groups
provider/source/tag/model name signals into unconfirmed concepts for review and
search-preview use. It deliberately does not write Entity truth,
MediaEntityAssignment truth, media_tags, TagTranslation, ProviderCache, or
confirmed assignment state.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from ..enums import TagCategoryEnum
from ..models import (
    ProviderCache,
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptResolutionRun,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
    SourceMetadataRecord,
    SourceNameAliasCandidate,
    SourceNameCandidate,
    SourceNameCandidateExtractionRun,
    SourceNameObservation,
    SourceSearchableNameAssertion,
    SourceTagObservation,
    Tag,
    blombooru_media_tags,
)
from .source_metadata_registry_service import canonical_source_key, normalize_source_text
from .source_name_candidate_extraction_service import (
    CandidateDraft,
    ExtractionResultBundle,
    FORBIDDEN_TRUTH_TABLES,
    RecordVerdictDraft,
    SourceCandidateInputGroup,
    persist_extraction_bundle,
    table_counts,
)

RESOLVER_VERSION = "source_concept_resolver_core_v1"
SOURCE_CONCEPT_SCHEMA_VERSION = "source_concept_schema_v1"

SOURCE_CONCEPT_ALLOWED_WRITE_TABLES = (
    "blombooru_source_concept_resolution_runs",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
)

CONCEPT_ROLES = {"character", "person", "work", "artist", "source_title", "unknown"}
STRONG_TRUST = {"strong"}
MEDIUM_TRUST = {"medium", "medium_ai"}
ACTIVE_INPUT_STATUSES = {"active", "searchable_active", "observed", "candidate"}
REJECTED_INPUT_STATUSES = {"rejected", "superseded", "blocked"}
AI_SOURCE_HINTS = ("ai", "wd", "model", "tagger", "llm")
TRUST_WEIGHTS = {
    "strong": 1.0,
    "medium": 0.65,
    "medium_ai": 0.45,
    "weak": 0.25,
    "rejected": 0.0,
}

POPULARITY_RE = re.compile(
    r"(?i)(users|bookmarks|views|fav|favorites|收藏|入り|users入り|bookmarks入り)"
)
PAREN_RE = re.compile(r"^\s*(?P<base>.+?)(?:_\(|\(|（)(?P<context>.+?)(?:\)|）)\s*$")


@dataclass(frozen=True)
class SourceConceptSignalDraft:
    signal_key: str
    origin_type: str
    origin_table: str | None
    origin_id: str | None
    provider: str | None
    media_id: int | None
    source_metadata_record_id: int | None
    source_record_id: str | None
    raw_value: str
    display_value: str
    normalized_key: str
    canonical_key: str | None
    role_hint: str
    work_context_key: str | None
    parenthetical_base: str | None
    parenthetical_context: str | None
    source_kind: str | None
    trust_tier: str
    confidence: float | None
    status: str
    evidence_payload: dict[str, Any] = field(default_factory=dict)
    source_run_id: str | None = None
    created_by_run_id: str | None = None


@dataclass(frozen=True)
class SourceConceptDraft:
    concept_key: str
    primary_display_name: str
    concept_type_hint: str
    status: str
    confidence_score: float
    evidence_score: float
    media_count: int
    source_count: int
    evidence_summary: dict[str, Any]
    signals: tuple[SourceConceptSignalDraft, ...]


@dataclass(frozen=True)
class SourceConceptLinkDraft:
    signal_key: str
    concept_key: str
    link_status: str
    confidence: float
    resolution_reason_code: str
    negative_reason_code: str | None
    evidence_payload: dict[str, Any]


@dataclass(frozen=True)
class SourceConceptAliasDraft:
    concept_key: str
    signal_key: str
    alias_value: str
    alias_key: str
    display_name: str
    language_hint: str | None
    script_hint: str | None
    alias_role: str
    status: str
    confidence: float | None
    evidence_payload: dict[str, Any]


@dataclass(frozen=True)
class SourceConceptEvidenceDraft:
    concept_key: str
    signal_key: str
    media_id: int | None
    source_metadata_record_id: int | None
    provider: str | None
    evidence_type: str
    evidence_strength: str
    payload: dict[str, Any]
    status: str


@dataclass(frozen=True)
class SourceConceptSearchIndexDraft:
    concept_key: str
    search_key: str
    display_name: str
    alias_role: str
    weight: float
    status: str
    evidence_refs: dict[str, Any]


@dataclass(frozen=True)
class SourceConceptResolutionResult:
    run_id: str
    signals: tuple[SourceConceptSignalDraft, ...]
    concepts: tuple[SourceConceptDraft, ...]
    aliases: tuple[SourceConceptAliasDraft, ...]
    evidence: tuple[SourceConceptEvidenceDraft, ...]
    links: tuple[SourceConceptLinkDraft, ...]
    search_index: tuple[SourceConceptSearchIndexDraft, ...]
    rejected_signals: tuple[dict[str, Any], ...]
    ambiguous_links: tuple[dict[str, Any], ...]
    merge_candidates: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_count(db: Session, table_name: str) -> int:
    try:
        return int(db.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0)
    except Exception:
        return -1


def canonical_key(value: Any) -> str:
    key = canonical_source_key(value)
    if len(key) > 500:
        return key[:500]
    return key


def display_text(value: Any) -> str:
    return normalize_source_text(value)[:1000]


def value_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def is_ai_tag_source(source: Any) -> bool:
    value = (source or "").lower()
    return any(hint in value for hint in AI_SOURCE_HINTS)


def is_popularity_or_meta(value: Any) -> bool:
    text_value = normalize_source_text(value)
    return bool(text_value and POPULARITY_RE.search(text_value))


def parse_parenthetical(value: Any) -> tuple[str | None, str | None]:
    text_value = normalize_source_text(value)
    if not text_value:
        return None, None
    match = PAREN_RE.match(text_value)
    if not match:
        return None, None
    base = normalize_source_text(match.group("base").strip("_ "))
    context = normalize_source_text(match.group("context").strip())
    return base or None, context or None


def is_short_ambiguous_key(value: str | None) -> bool:
    if not value:
        return True
    stripped = value.strip("_")
    if len(stripped) <= 3:
        return True
    if "_" not in stripped and len(stripped) <= 7:
        return True
    return False


def role_from_source_role(value: Any) -> str:
    raw = (value or "").lower()
    mapping = {
        "character": "character",
        "person": "person",
        "artist": "artist",
        "artist_creator": "artist",
        "creator": "artist",
        "author": "artist",
        "work": "work",
        "work_title": "work",
        "copyright": "work",
        "source_title": "source_title",
        "title": "source_title",
        "unknown_name": "unknown",
        "unknown_name_like": "unknown",
        "unknown": "unknown",
    }
    return mapping.get(raw, "unknown")


def role_from_tag_category(value: Any) -> str:
    raw = (enum_value(value) or "").lower()
    mapping = {
        TagCategoryEnum.character.value: "character",
        TagCategoryEnum.artist.value: "artist",
        TagCategoryEnum.copyright.value: "work",
        "4": "character",
        "1": "artist",
        "3": "work",
    }
    return mapping.get(raw, "unknown")


def role_from_source_tag_category(value: Any) -> str:
    raw = normalize_source_text(value).lower()
    mapping = {
        "4": "character",
        "1": "artist",
        "3": "work",
        "character": "character",
        "artist": "artist",
        "copyright": "work",
        "work": "work",
        "series": "work",
    }
    return mapping.get(raw, "unknown")


def source_status_to_concept_status(value: Any, *, default: str = "needs_review") -> str:
    raw = (value or "").lower()
    if raw in {"active", "active_candidate", "searchable_active", "observed", "accepted", "confirmed"}:
        return "active"
    if raw in REJECTED_INPUT_STATUSES or raw in {"rejected_candidate"}:
        return "rejected"
    if raw in {"ambiguous"}:
        return "ambiguous"
    if raw in {"needs_review", "requires_review", "candidate", "pending"}:
        return "needs_review"
    return default


def source_confidence_score(value: Any, fallback: float | None = None) -> float | None:
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).lower()
    mapping = {
        "high": 0.9,
        "medium": 0.65,
        "low": 0.35,
        "none": 0.0,
    }
    return mapping.get(raw, fallback)


def source_signal_inventory(db: Session, *, f7a_run_id: str | None = None) -> dict[str, Any]:
    """Return redacted aggregate inventory for current source-layer signals."""

    inventory: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "f7a_run_id_scope": f7a_run_id,
        "sources": {},
    }

    f7a_query = (
        db.query(
            SourceNameCandidate.candidate_role,
            SourceNameCandidate.candidate_status,
            SourceNameCandidate.origin_type,
            SourceNameCandidate.provider,
            func.count(SourceNameCandidate.id),
        )
        .outerjoin(
            SourceNameCandidateExtractionRun,
            SourceNameCandidate.extraction_run_id == SourceNameCandidateExtractionRun.id,
        )
        .filter(SourceNameCandidate.status == "active")
    )
    if f7a_run_id:
        f7a_query = f7a_query.filter(SourceNameCandidateExtractionRun.run_id == f7a_run_id)
    f7a_groups = [
        {
            "role": role or "unknown",
            "status": status or "unknown",
            "origin_type": origin or "unknown",
            "provider": provider or "unknown",
            "count": int(count or 0),
        }
        for role, status, origin, provider, count in f7a_query.group_by(
            SourceNameCandidate.candidate_role,
            SourceNameCandidate.candidate_status,
            SourceNameCandidate.origin_type,
            SourceNameCandidate.provider,
        ).all()
    ]
    inventory["sources"]["f7a_source_name_candidate"] = {
        "real_dev_db_availability": "available" if f7a_groups else "absent_or_not_persisted",
        "fixture_only_availability": "not_required",
        "trust_tier": "medium",
        "recommended_adapter": "f7a_candidate_adapter",
        "phase_45_sc1": "include",
        "count": sum(row["count"] for row in f7a_groups),
        "groups": f7a_groups,
    }

    media_tag_rows = db.execute(
        select(
            Tag.category,
            blombooru_media_tags.c.source,
            blombooru_media_tags.c.is_suggestion,
            func.count(),
        )
        .select_from(blombooru_media_tags.join(Tag, Tag.id == blombooru_media_tags.c.tag_id))
        .group_by(Tag.category, blombooru_media_tags.c.source, blombooru_media_tags.c.is_suggestion)
    ).all()
    proper_count = db.execute(
        select(func.count())
        .select_from(blombooru_media_tags.join(Tag, Tag.id == blombooru_media_tags.c.tag_id))
        .where(Tag.category.in_([TagCategoryEnum.character, TagCategoryEnum.artist, TagCategoryEnum.copyright]))
    ).scalar()
    parenthetical_count = db.execute(
        select(func.count())
        .select_from(blombooru_media_tags.join(Tag, Tag.id == blombooru_media_tags.c.tag_id))
        .where(or_(Tag.name.like("%_(%)"), Tag.name.like("%(%)"), Tag.name.like("%（%）%")))
    ).scalar()
    ai_count = sum(
        int(count or 0)
        for category, source, is_suggestion, count in media_tag_rows
        if is_suggestion or is_ai_tag_source(source)
    )
    inventory["sources"]["media_tags"] = {
        "real_dev_db_availability": "available" if media_tag_rows else "absent",
        "fixture_only_availability": "not_required",
        "trust_tier": "strong_for_curated_categories_medium_ai_or_weak_for_model_tags",
        "recommended_adapter": "normal_media_tag_and_ai_model_tag_adapters",
        "phase_45_sc1": "include_concept_eligible_categories",
        "total": sum(int(count or 0) for *_, count in media_tag_rows),
        "proper_noun_or_category_count": int(proper_count or 0),
        "parenthetical_count": int(parenthetical_count or 0),
        "ai_or_suggestion_count": ai_count,
        "groups": [
            {
                "category": enum_value(category) or "unknown",
                "source": source or "unknown",
                "is_suggestion": bool(is_suggestion),
                "count": int(count or 0),
            }
            for category, source, is_suggestion, count in media_tag_rows
        ],
    }

    assertion_groups = [
        {
            "provider": provider or "unknown",
            "status": status or "unknown",
            "role": role or "unknown",
            "count": int(count or 0),
        }
        for provider, status, role, count in db.query(
            SourceSearchableNameAssertion.provider,
            SourceSearchableNameAssertion.status,
            SourceSearchableNameAssertion.asserted_role,
            func.count(SourceSearchableNameAssertion.id),
        )
        .group_by(
            SourceSearchableNameAssertion.provider,
            SourceSearchableNameAssertion.status,
            SourceSearchableNameAssertion.asserted_role,
        )
        .all()
    ]
    inventory["sources"]["source_searchable_name_assertion"] = {
        "real_dev_db_availability": "available" if assertion_groups else "absent",
        "fixture_only_availability": "not_required",
        "trust_tier": "strong_when_role_explicit_and_searchable_active",
        "recommended_adapter": "source_assertion_adapter",
        "phase_45_sc1": "include",
        "count": sum(row["count"] for row in assertion_groups),
        "groups": assertion_groups,
    }

    name_observation_groups = [
        {
            "provider": provider or "unknown",
            "status": status or "unknown",
            "role": role or "unknown",
            "count": int(count or 0),
        }
        for provider, status, role, count in db.query(
            SourceNameObservation.provider,
            SourceNameObservation.status,
            SourceNameObservation.name_role,
            func.count(SourceNameObservation.id),
        )
        .group_by(SourceNameObservation.provider, SourceNameObservation.status, SourceNameObservation.name_role)
        .all()
    ]
    inventory["sources"]["source_name_observation"] = {
        "real_dev_db_availability": "available" if name_observation_groups else "absent",
        "fixture_only_availability": "not_required",
        "trust_tier": "medium",
        "recommended_adapter": "source_name_observation_adapter",
        "phase_45_sc1": "include",
        "count": sum(row["count"] for row in name_observation_groups),
        "groups": name_observation_groups,
    }

    tag_observation_groups = [
        {
            "provider": provider or "unknown",
            "status": status or "unknown",
            "source_tag_kind": kind or "unknown",
            "category": category or "unknown",
            "count": int(count or 0),
        }
        for provider, status, kind, category, count in db.query(
            SourceTagObservation.provider,
            SourceTagObservation.status,
            SourceTagObservation.source_tag_kind,
            SourceTagObservation.source_category_raw,
            func.count(SourceTagObservation.id),
        )
        .group_by(
            SourceTagObservation.provider,
            SourceTagObservation.status,
            SourceTagObservation.source_tag_kind,
            SourceTagObservation.source_category_raw,
        )
        .all()
    ]
    tag_name_like_count = (
        db.query(SourceTagObservation)
        .filter(
            or_(
                SourceTagObservation.source_category_raw.in_(["1", "3", "4", "artist", "character", "copyright", "work"]),
                SourceTagObservation.canonical_tag_key.like("%_(%)"),
                SourceTagObservation.raw_tag.like("%(%)"),
                SourceTagObservation.raw_tag.like("%（%）%"),
            )
        )
        .count()
    )
    inventory["sources"]["source_tag_observation"] = {
        "real_dev_db_availability": "available" if tag_observation_groups else "absent",
        "fixture_only_availability": "not_required",
        "trust_tier": "medium_or_weak_depending_on_category",
        "recommended_adapter": "source_tag_observation_adapter",
        "phase_45_sc1": "include_name_like_or_category_supported",
        "count": sum(row["count"] for row in tag_observation_groups),
        "name_like_or_category_supported_count": int(tag_name_like_count or 0),
        "groups": tag_observation_groups,
    }

    alias_groups = [
        {
            "relation_type": relation_type or "unknown",
            "status": status or "unknown",
            "evidence_source": evidence_source or "unknown",
            "count": int(count or 0),
        }
        for relation_type, status, evidence_source, count in db.query(
            SourceNameAliasCandidate.relation_type,
            SourceNameAliasCandidate.status,
            SourceNameAliasCandidate.evidence_source,
            func.count(SourceNameAliasCandidate.id),
        )
        .group_by(
            SourceNameAliasCandidate.relation_type,
            SourceNameAliasCandidate.status,
            SourceNameAliasCandidate.evidence_source,
        )
        .all()
    ]
    inventory["sources"]["source_name_alias_candidate"] = {
        "real_dev_db_availability": "available" if alias_groups else "absent",
        "fixture_only_availability": "not_required",
        "trust_tier": "medium_needs_corrobation",
        "recommended_adapter": "source_alias_candidate_adapter",
        "phase_45_sc1": "include_as_edge_evidence",
        "count": sum(row["count"] for row in alias_groups),
        "groups": alias_groups,
    }

    metadata_groups = [
        {
            "provider": provider or "unknown",
            "metadata_kind": kind or "unknown",
            "data_type_label": data_type or "unknown",
            "status": status or "unknown",
            "count": int(count or 0),
        }
        for provider, kind, data_type, status, count in db.query(
            SourceMetadataRecord.provider,
            SourceMetadataRecord.metadata_kind,
            SourceMetadataRecord.data_type_label,
            SourceMetadataRecord.status,
            func.count(SourceMetadataRecord.id),
        )
        .group_by(
            SourceMetadataRecord.provider,
            SourceMetadataRecord.metadata_kind,
            SourceMetadataRecord.data_type_label,
            SourceMetadataRecord.status,
        )
        .all()
    ]
    structured_count = (
        db.query(SourceMetadataRecord)
        .filter(
            or_(
                SourceMetadataRecord.title.isnot(None),
                SourceMetadataRecord.artist_name.isnot(None),
                SourceMetadataRecord.raw_metadata_json.isnot(None),
            )
        )
        .count()
    )
    inventory["sources"]["provider_structured_fields"] = {
        "real_dev_db_availability": "available" if metadata_groups else "absent",
        "fixture_only_availability": "available_when_data_type_label_is_fixture_or_mock",
        "trust_tier": "strong_or_medium_for_explicit_fields_weak_for_title_only",
        "recommended_adapter": "provider_structured_field_adapter",
        "phase_45_sc1": "include_when_fields_are_present",
        "structured_record_count": int(structured_count or 0),
        "groups": metadata_groups,
    }

    provider_cache_groups = [
        {
            "provider": provider or "unknown",
            "query_type": query_type or "unknown",
            "response_status": response_status or "unknown",
            "count": int(count or 0),
        }
        for provider, query_type, response_status, count in db.query(
            ProviderCache.provider,
            ProviderCache.query_type,
            ProviderCache.response_status,
            func.count(ProviderCache.id),
        )
        .group_by(ProviderCache.provider, ProviderCache.query_type, ProviderCache.response_status)
        .all()
    ]
    inventory["sources"]["provider_cache"] = {
        "real_dev_db_availability": "available" if provider_cache_groups else "absent",
        "fixture_only_availability": "not_required",
        "trust_tier": "context_only_unless_redacted_structured_fields_exist",
        "recommended_adapter": "provider_cache_structured_field_adapter",
        "phase_45_sc1": "include_only_redacted_name_like_structured_fields",
        "count": sum(row["count"] for row in provider_cache_groups),
        "groups": provider_cache_groups,
    }

    return inventory


def _make_signal(
    *,
    origin_type: str,
    origin_table: str | None,
    origin_id: str | int | None,
    provider: str | None,
    media_id: int | None,
    source_metadata_record_id: int | None,
    source_record_id: str | None,
    raw_value: Any,
    display_value: Any | None = None,
    canonical_value: Any | None = None,
    role_hint: str,
    work_context_key: str | None = None,
    parenthetical_base: str | None = None,
    parenthetical_context: str | None = None,
    source_kind: str | None = None,
    trust_tier: str,
    confidence: float | None,
    status: str,
    evidence_payload: Mapping[str, Any] | None = None,
    source_run_id: str | None = None,
    created_by_run_id: str | None = None,
    signal_suffix: str | None = None,
) -> SourceConceptSignalDraft | None:
    raw_text = display_text(raw_value)
    if not raw_text:
        return None
    display = display_text(display_value if display_value is not None else raw_text)
    normalized = canonical_key(raw_text)
    canonical = canonical_key(canonical_value if canonical_value is not None else raw_text)
    if not canonical:
        return None
    if parenthetical_base is None or parenthetical_context is None:
        parsed_base, parsed_context = parse_parenthetical(raw_text)
        parenthetical_base = parenthetical_base or parsed_base
        parenthetical_context = parenthetical_context or parsed_context
    if not work_context_key and parenthetical_context:
        work_context_key = canonical_key(parenthetical_context)
    role = role_hint if role_hint in CONCEPT_ROLES else "unknown"
    if is_popularity_or_meta(raw_text):
        trust_tier = "rejected"
        status = "rejected"
    origin_id_text = str(origin_id) if origin_id is not None else None
    suffix = signal_suffix or origin_id_text or value_hash(
        {
            "origin_type": origin_type,
            "origin_table": origin_table,
            "provider": provider,
            "media_id": media_id,
            "source_metadata_record_id": source_metadata_record_id,
            "raw": raw_text,
            "role": role,
        }
    )
    signal_key = f"{origin_type}:{suffix}"
    return SourceConceptSignalDraft(
        signal_key=signal_key[:900],
        origin_type=origin_type,
        origin_table=origin_table,
        origin_id=origin_id_text,
        provider=provider,
        media_id=media_id,
        source_metadata_record_id=source_metadata_record_id,
        source_record_id=source_record_id,
        raw_value=raw_text,
        display_value=display,
        normalized_key=normalized,
        canonical_key=canonical,
        role_hint=role,
        work_context_key=work_context_key,
        parenthetical_base=parenthetical_base,
        parenthetical_context=parenthetical_context,
        source_kind=source_kind,
        trust_tier=trust_tier,
        confidence=confidence,
        status=status,
        evidence_payload=dict(evidence_payload or {}),
        source_run_id=source_run_id,
        created_by_run_id=created_by_run_id,
    )


def _dedupe_signals(signals: Iterable[SourceConceptSignalDraft | None]) -> tuple[SourceConceptSignalDraft, ...]:
    by_key: dict[str, SourceConceptSignalDraft] = {}
    for signal in signals:
        if signal is None:
            continue
        by_key[signal.signal_key] = signal
    return tuple(by_key.values())


def _trust_for_f7a_candidate(row: SourceNameCandidate) -> tuple[str, str]:
    role = role_from_source_role(row.candidate_role)
    status = source_status_to_concept_status(row.candidate_status)
    origin = (row.origin_type or "").lower()
    if status == "rejected" or row.status != "active":
        return "rejected", "rejected"
    if origin == "ai_model_tag":
        return "medium_ai" if status == "active" else "weak", "needs_review"
    if role == "source_title":
        return "weak", "needs_review"
    if role == "unknown":
        return "weak", "needs_review"
    return "medium", status


def _trust_for_media_tag(category: Any, source: str | None, is_suggestion: bool, name: str) -> tuple[str, str, str]:
    role = role_from_tag_category(category)
    ai_source = is_ai_tag_source(source)
    if role == "unknown" and parse_parenthetical(name)[0]:
        role = "character"
    if is_popularity_or_meta(name):
        return role, "rejected", "rejected"
    if role == "unknown":
        return role, "rejected", "rejected"
    if ai_source and is_suggestion:
        return role, "weak", "needs_review"
    if ai_source:
        return role, "medium_ai", "needs_review"
    return role, "strong", "active"


def build_source_concept_signals(
    db: Session,
    *,
    run_id: str,
    f7a_run_id: str | None = None,
) -> tuple[SourceConceptSignalDraft, ...]:
    """Build provider-neutral resolver signals from current source-layer inputs."""

    signals: list[SourceConceptSignalDraft | None] = []

    f7a_query = (
        db.query(SourceNameCandidate, SourceNameCandidateExtractionRun.run_id)
        .outerjoin(
            SourceNameCandidateExtractionRun,
            SourceNameCandidate.extraction_run_id == SourceNameCandidateExtractionRun.id,
        )
        .filter(SourceNameCandidate.status == "active")
    )
    if f7a_run_id:
        f7a_query = f7a_query.filter(SourceNameCandidateExtractionRun.run_id == f7a_run_id)
    for row, candidate_run_id in f7a_query.all():
        trust, signal_status = _trust_for_f7a_candidate(row)
        signals.append(
            _make_signal(
                origin_type="f7a_candidate",
                origin_table="blombooru_source_name_candidates",
                origin_id=row.id,
                provider=row.provider,
                media_id=row.media_id,
                source_metadata_record_id=row.source_metadata_record_id,
                source_record_id=str(row.source_metadata_record_id) if row.source_metadata_record_id else None,
                raw_value=row.raw_value,
                display_value=row.display_name,
                canonical_value=row.canonical_key,
                role_hint=role_from_source_role(row.candidate_role),
                work_context_key=row.work_context_key,
                parenthetical_base=row.parenthetical_base,
                parenthetical_context=row.parenthetical_context,
                source_kind=row.origin_type,
                trust_tier=trust,
                confidence=row.confidence,
                status=signal_status,
                evidence_payload={
                    "candidate_key": row.candidate_key,
                    "candidate_status": row.candidate_status,
                    "extraction_verdict": row.extraction_verdict,
                    "source_kind": row.origin_type,
                },
                source_run_id=candidate_run_id,
                created_by_run_id=run_id,
            )
        )

    media_tag_rows = db.execute(
        select(
            blombooru_media_tags.c.media_id,
            blombooru_media_tags.c.source,
            blombooru_media_tags.c.confidence,
            blombooru_media_tags.c.is_suggestion,
            Tag.id,
            Tag.name,
            Tag.category,
        )
        .select_from(blombooru_media_tags.join(Tag, Tag.id == blombooru_media_tags.c.tag_id))
        .where(
            or_(
                Tag.category.in_([TagCategoryEnum.character, TagCategoryEnum.artist, TagCategoryEnum.copyright]),
                Tag.name.like("%_(%)"),
                Tag.name.like("%(%)"),
                Tag.name.like("%（%）%"),
            )
        )
    ).all()
    for media_id, source, confidence, is_suggestion, tag_id, name, category in media_tag_rows:
        role, trust, signal_status = _trust_for_media_tag(category, source, bool(is_suggestion), name)
        if trust == "rejected":
            continue
        origin_type = "ai_model_tag" if is_ai_tag_source(source) or is_suggestion else "normal_media_tag"
        signals.append(
            _make_signal(
                origin_type=origin_type,
                origin_table="blombooru_media_tags",
                origin_id=f"{media_id}:{tag_id}",
                provider=source or "media_tags",
                media_id=int(media_id) if media_id is not None else None,
                source_metadata_record_id=None,
                source_record_id=None,
                raw_value=name,
                display_value=name,
                canonical_value=name,
                role_hint=role,
                source_kind=f"tag_category:{enum_value(category) or 'unknown'}",
                trust_tier=trust,
                confidence=source_confidence_score(confidence, TRUST_WEIGHTS.get(trust)),
                status=signal_status,
                evidence_payload={
                    "tag_id": tag_id,
                    "tag_category": enum_value(category),
                    "is_suggestion": bool(is_suggestion),
                    "source": source,
                },
                created_by_run_id=run_id,
            )
        )

    for row in db.query(SourceSearchableNameAssertion).all():
        role = role_from_source_role(row.asserted_role)
        status = source_status_to_concept_status(row.status)
        trust = "strong" if status == "active" and role not in {"unknown", "source_title"} else "medium"
        if role == "source_title":
            trust = "weak"
            status = "needs_review"
        if status == "rejected":
            trust = "rejected"
        signals.append(
            _make_signal(
                origin_type="source_assertion",
                origin_table="blombooru_source_searchable_name_assertions",
                origin_id=row.id,
                provider=row.provider,
                media_id=None,
                source_metadata_record_id=row.source_metadata_record_id,
                source_record_id=str(row.source_metadata_record_id) if row.source_metadata_record_id else None,
                raw_value=row.asserted_name or row.raw_input,
                display_value=row.asserted_name or row.raw_input,
                canonical_value=row.canonical_name_key,
                role_hint=role,
                source_kind=row.asserted_role,
                trust_tier=trust,
                confidence=source_confidence_score(row.confidence, row.confidence_score),
                status=status,
                evidence_payload={
                    "assertion_key": row.assertion_key,
                    "confidence": row.confidence,
                    "source_tag_observation_id": row.source_tag_observation_id,
                    "source_name_observation_id": row.source_name_observation_id,
                },
                created_by_run_id=run_id,
            )
        )

    for row in db.query(SourceNameObservation).all():
        role = role_from_source_role(row.name_role)
        status = "needs_review" if row.requires_review else source_status_to_concept_status(row.status, default="active")
        trust = "medium"
        if role in {"unknown", "source_title"}:
            trust = "weak"
            status = "needs_review"
        if status == "rejected":
            trust = "rejected"
        signals.append(
            _make_signal(
                origin_type="source_name_observation",
                origin_table="blombooru_source_name_observations",
                origin_id=row.id,
                provider=row.provider,
                media_id=row.media_id,
                source_metadata_record_id=row.source_metadata_record_id,
                source_record_id=str(row.source_metadata_record_id),
                raw_value=row.raw_name,
                display_value=row.normalized_name or row.raw_name,
                canonical_value=row.canonical_name_key,
                role_hint=role,
                source_kind=row.source_field,
                trust_tier=trust,
                confidence=row.confidence,
                status=status,
                evidence_payload={"observation_key": row.observation_key, "source_field": row.source_field},
                created_by_run_id=run_id,
            )
        )

    for row in db.query(SourceTagObservation).all():
        role = role_from_source_tag_category(row.source_category_raw)
        if role == "unknown" and parse_parenthetical(row.raw_tag)[0]:
            role = "character"
        trust = "medium" if role != "unknown" else "weak"
        status = source_status_to_concept_status(row.status, default="needs_review")
        if row.source_category_raw in {None, "", "0", "general", "meta", "5"} and role == "unknown":
            status = "needs_review"
        if is_popularity_or_meta(row.raw_tag):
            trust = "rejected"
            status = "rejected"
        if trust != "rejected":
            signals.append(
                _make_signal(
                    origin_type="source_tag_observation",
                    origin_table="blombooru_source_tag_observations",
                    origin_id=row.id,
                    provider=row.provider,
                    media_id=None,
                    source_metadata_record_id=row.source_metadata_record_id,
                    source_record_id=str(row.source_metadata_record_id),
                    raw_value=row.raw_tag,
                    display_value=row.normalized_tag or row.raw_tag,
                    canonical_value=row.canonical_tag_key,
                    role_hint=role,
                    source_kind=row.source_tag_kind,
                    trust_tier=trust,
                    confidence=row.confidence,
                    status=status,
                    evidence_payload={
                        "observation_key": row.observation_key,
                        "source_category_raw": row.source_category_raw,
                    },
                    created_by_run_id=run_id,
                )
            )

    for row in db.query(SourceNameAliasCandidate).all():
        status = "needs_review" if row.requires_review else source_status_to_concept_status(row.status)
        trust = "medium" if status != "rejected" else "rejected"
        edge_payload = {
            "relation_type": row.relation_type,
            "evidence_source": row.evidence_source,
            "source_name_key": row.source_name_key,
            "target_name_key": row.target_name_key,
        }
        for side, key_value, display in (
            ("source", row.source_name_key, row.source_display_name),
            ("target", row.target_name_key, row.target_display_name),
        ):
            signals.append(
                _make_signal(
                    origin_type="source_alias_candidate",
                    origin_table="blombooru_source_name_alias_candidates",
                    origin_id=row.id,
                    provider=row.evidence_source,
                    media_id=None,
                    source_metadata_record_id=None,
                    source_record_id=None,
                    raw_value=display,
                    display_value=display,
                    canonical_value=key_value,
                    role_hint="unknown",
                    source_kind=f"alias_edge_{side}",
                    trust_tier=trust,
                    confidence=row.confidence,
                    status=status,
                    evidence_payload=edge_payload,
                    created_by_run_id=run_id,
                    signal_suffix=f"{row.id}:{side}",
                )
            )

    for row in db.query(SourceMetadataRecord).all():
        for field_name, value, role, trust in _structured_fields_from_metadata(row):
            status = "active" if trust in STRONG_TRUST and role != "source_title" else "needs_review"
            signals.append(
                _make_signal(
                    origin_type="provider_structured_field",
                    origin_table="blombooru_source_metadata_records",
                    origin_id=row.id,
                    provider=row.provider,
                    media_id=row.media_id,
                    source_metadata_record_id=row.id,
                    source_record_id=row.provider_record_key,
                    raw_value=value,
                    display_value=value,
                    role_hint=role,
                    source_kind=field_name,
                    trust_tier=trust,
                    confidence=row.confidence,
                    status=status,
                    evidence_payload={
                        "provider_record_key": row.provider_record_key,
                        "metadata_kind": row.metadata_kind,
                        "data_type_label": row.data_type_label,
                    },
                    source_run_id=row.provider_run_id,
                    created_by_run_id=run_id,
                    signal_suffix=f"{row.id}:{field_name}:{value_hash(value, 12)}",
                )
            )

    for row in db.query(ProviderCache).all():
        for field_name, value, role, trust in _structured_fields_from_provider_cache(row):
            signals.append(
                _make_signal(
                    origin_type="provider_structured_field",
                    origin_table="blombooru_provider_cache",
                    origin_id=row.id,
                    provider=row.provider,
                    media_id=None,
                    source_metadata_record_id=None,
                    source_record_id=row.query_hash,
                    raw_value=value,
                    display_value=value,
                    role_hint=role,
                    source_kind=f"provider_cache:{field_name}",
                    trust_tier=trust,
                    confidence=None,
                    status="needs_review",
                    evidence_payload={"query_type": row.query_type, "response_status": row.response_status},
                    created_by_run_id=run_id,
                    signal_suffix=f"{row.id}:{field_name}:{value_hash(value, 12)}",
                )
            )

    return _dedupe_signals(signals)


def _iter_scalar_values(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        if normalize_source_text(value):
            yield value
        return
    if isinstance(value, (int, float, bool)):
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _iter_scalar_values(item)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_scalar_values(item)


def _structured_fields_from_metadata(row: SourceMetadataRecord) -> list[tuple[str, str, str, str]]:
    fields: list[tuple[str, str, str, str]] = []
    if row.artist_name:
        fields.append(("artist_name", row.artist_name, "artist", "medium"))
    if row.title:
        fields.append(("title", row.title, "source_title", "weak"))
    raw = row.raw_metadata_json or {}
    if not isinstance(raw, Mapping):
        return fields
    field_specs = {
        "character": ("character", "medium"),
        "characters": ("character", "medium"),
        "person": ("person", "medium"),
        "artist": ("artist", "medium"),
        "creator": ("artist", "medium"),
        "author": ("artist", "medium"),
        "work": ("work", "medium"),
        "copyright": ("work", "medium"),
        "work_or_copyright": ("work", "medium"),
        "title": ("source_title", "weak"),
    }
    for key, spec in field_specs.items():
        if key not in raw:
            continue
        role, trust = spec
        for value in _iter_scalar_values(raw.get(key)):
            fields.append((f"raw_metadata_json.{key}", value, role, trust))
    return fields


def _structured_fields_from_provider_cache(row: ProviderCache) -> list[tuple[str, str, str, str]]:
    raw = row.response_json_redacted or {}
    if not isinstance(raw, Mapping):
        return []
    fields: list[tuple[str, str, str, str]] = []
    field_specs = {
        "character": ("character", "medium"),
        "characters": ("character", "medium"),
        "artist": ("artist", "medium"),
        "creator": ("artist", "medium"),
        "author": ("artist", "medium"),
        "work": ("work", "medium"),
        "copyright": ("work", "medium"),
        "title": ("source_title", "weak"),
    }
    for key, spec in field_specs.items():
        if key not in raw:
            continue
        role, trust = spec
        for value in _iter_scalar_values(raw.get(key)):
            fields.append((key, value, role, trust))
    return fields


def _context_candidates_by_scope(signals: Sequence[SourceConceptSignalDraft]) -> dict[tuple[str, int], set[str]]:
    contexts: dict[tuple[str, int], set[str]] = defaultdict(set)
    for signal in signals:
        if signal.status == "rejected" or signal.trust_tier == "rejected":
            continue
        if signal.role_hint not in {"work", "source_title"}:
            continue
        context = signal.work_context_key or signal.canonical_key
        if not context:
            continue
        if signal.media_id is not None:
            contexts[("media", signal.media_id)].add(context)
        if signal.source_metadata_record_id is not None:
            contexts[("record", signal.source_metadata_record_id)].add(context)
    return contexts


def _alias_group_lookup(signals: Sequence[SourceConceptSignalDraft]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for signal in signals:
        if signal.origin_type != "source_alias_candidate":
            continue
        source_key = signal.evidence_payload.get("source_name_key")
        target_key = signal.evidence_payload.get("target_name_key")
        if not source_key or not target_key:
            continue
        source_canonical = canonical_key(source_key)
        target_canonical = canonical_key(target_key)
        if not source_canonical or not target_canonical:
            continue
        pair = tuple(sorted((source_canonical, target_canonical)))
        group_key = f"{pair[0]}:{pair[1]}"
        lookup[source_canonical] = group_key
        lookup[target_canonical] = group_key
    return lookup


def _infer_unique_context(
    signal: SourceConceptSignalDraft,
    context_by_scope: Mapping[tuple[str, int], set[str]],
) -> tuple[str | None, str | None]:
    if signal.work_context_key:
        return signal.work_context_key, "explicit_work_context"
    contexts: set[str] = set()
    if signal.source_metadata_record_id is not None:
        contexts.update(context_by_scope.get(("record", signal.source_metadata_record_id), set()))
    if signal.media_id is not None:
        contexts.update(context_by_scope.get(("media", signal.media_id), set()))
    contexts.discard(signal.canonical_key or "")
    if len(contexts) == 1:
        return next(iter(contexts)), "unique_source_or_media_work_context"
    return None, None


def _concept_identity(
    signal: SourceConceptSignalDraft,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    alias_group_by_key: Mapping[str, str],
) -> tuple[str | None, str, str | None, str]:
    if signal.status == "rejected" or signal.trust_tier == "rejected":
        return None, "rejected_signal", None, "rejected_signal"
    key = signal.canonical_key or signal.normalized_key
    if not key:
        return None, "empty_key", None, "rejected_empty_key"
    if key in alias_group_by_key:
        return f"alias_edge:{alias_group_by_key[key]}", "active_or_review", None, "alias_candidate_edge"
    role = signal.role_hint or "unknown"
    base_key = canonical_key(signal.parenthetical_base) if signal.parenthetical_base else key
    context, context_reason = _infer_unique_context(signal, context_by_scope)
    if not context and signal.parenthetical_context:
        context = canonical_key(signal.parenthetical_context)
        context_reason = "parenthetical_context"
    if role in {"character", "person"}:
        if context:
            return f"{role}:{base_key}:work:{context}", "active_or_review", None, context_reason or "role_name_context"
        if is_short_ambiguous_key(base_key):
            return (
                f"{role}:ambiguous:{base_key}:signal:{value_hash(signal.signal_key, 12)}",
                "needs_review",
                "ambiguous_short_without_work_context",
                "ambiguous_short_guard",
            )
        return f"{role}:{base_key}", "active_or_review", None, "role_name_exact"
    if role == "source_title":
        return (
            f"source_title:{key}:signal:{value_hash(signal.signal_key, 12)}",
            "needs_review",
            "source_title_only_guard",
            "source_title_context_only",
        )
    if role in {"work", "artist"}:
        return f"{role}:{key}", "active_or_review", None, "role_name_exact"
    return (
        f"unknown:{key}:signal:{value_hash(signal.signal_key, 12)}",
        "needs_review",
        "unknown_role_guard",
        "unknown_role_review",
    )


def resolve_source_concepts(
    signals: Sequence[SourceConceptSignalDraft],
    *,
    run_id: str,
) -> SourceConceptResolutionResult:
    context_by_scope = _context_candidates_by_scope(signals)
    alias_group_by_key = _alias_group_lookup(signals)
    buckets: dict[str, list[tuple[SourceConceptSignalDraft, str | None, str]]] = defaultdict(list)
    rejected: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    for signal in signals:
        concept_key, initial_status, negative_reason, reason_code = _concept_identity(
            signal,
            context_by_scope,
            alias_group_by_key,
        )
        if concept_key is None:
            rejected.append(
                {
                    "signal_key": signal.signal_key,
                    "origin_type": signal.origin_type,
                    "role_hint": signal.role_hint,
                    "negative_reason_code": negative_reason or reason_code,
                    "trust_tier": signal.trust_tier,
                    "status": signal.status,
                }
            )
            continue
        if negative_reason:
            ambiguous.append(
                {
                    "signal_key": signal.signal_key,
                    "concept_key": concept_key,
                    "negative_reason_code": negative_reason,
                    "origin_type": signal.origin_type,
                    "role_hint": signal.role_hint,
                }
            )
        buckets[concept_key].append((signal, negative_reason, reason_code))

    concepts: list[SourceConceptDraft] = []
    aliases: list[SourceConceptAliasDraft] = []
    evidence: list[SourceConceptEvidenceDraft] = []
    links: list[SourceConceptLinkDraft] = []
    search_index: list[SourceConceptSearchIndexDraft] = []

    for concept_key, items in buckets.items():
        bucket_signals = tuple(signal for signal, _, _ in items)
        concept = _build_concept_draft(concept_key, bucket_signals, run_id=run_id)
        concepts.append(concept)
        for signal, negative_reason, reason_code in items:
            link_status = concept.status
            if negative_reason:
                link_status = "needs_review"
            link_confidence = min(concept.confidence_score, source_confidence_score(signal.confidence, TRUST_WEIGHTS[signal.trust_tier]) or 0.0)
            links.append(
                SourceConceptLinkDraft(
                    signal_key=signal.signal_key,
                    concept_key=concept_key,
                    link_status=link_status,
                    confidence=round(link_confidence, 4),
                    resolution_reason_code=reason_code,
                    negative_reason_code=negative_reason,
                    evidence_payload={
                        "origin_type": signal.origin_type,
                        "trust_tier": signal.trust_tier,
                        "status_guard": negative_reason,
                    },
                )
            )
            alias_key = signal.canonical_key or signal.normalized_key
            aliases.append(
                SourceConceptAliasDraft(
                    concept_key=concept_key,
                    signal_key=signal.signal_key,
                    alias_value=signal.raw_value,
                    alias_key=alias_key,
                    display_name=signal.display_value,
                    language_hint=(signal.evidence_payload or {}).get("language_hint"),
                    script_hint=(signal.evidence_payload or {}).get("script_hint"),
                    alias_role=signal.origin_type,
                    status=link_status,
                    confidence=signal.confidence,
                    evidence_payload={"source_kind": signal.source_kind, "trust_tier": signal.trust_tier},
                )
            )
            evidence.append(
                SourceConceptEvidenceDraft(
                    concept_key=concept_key,
                    signal_key=signal.signal_key,
                    media_id=signal.media_id,
                    source_metadata_record_id=signal.source_metadata_record_id,
                    provider=signal.provider,
                    evidence_type=signal.origin_type,
                    evidence_strength=signal.trust_tier,
                    payload={
                        "origin_table": signal.origin_table,
                        "origin_id": signal.origin_id,
                        "source_kind": signal.source_kind,
                        "evidence_payload": signal.evidence_payload,
                    },
                    status=link_status,
                )
            )
        for alias in aliases_for_concept(concept_key, aliases):
            search_index.append(
                SourceConceptSearchIndexDraft(
                    concept_key=concept_key,
                    search_key=alias.alias_key,
                    display_name=alias.display_name,
                    alias_role=alias.alias_role,
                    weight=round(TRUST_WEIGHTS.get(concept.evidence_summary["max_trust_tier"], 0.25), 4),
                    status=alias.status,
                    evidence_refs={"signal_key": alias.signal_key, "concept_status": concept.status},
                )
            )

    deduped_aliases = tuple(_dedupe_aliases(aliases))
    deduped_search_index = tuple(_dedupe_search_index(search_index))
    signal_counts = Counter(signal.origin_type for signal in signals)
    concept_counts = Counter(concept.status for concept in concepts)
    link_counts = Counter(link.link_status for link in links)
    summary = {
        "run_id": run_id,
        "resolver_version": RESOLVER_VERSION,
        "schema_version": SOURCE_CONCEPT_SCHEMA_VERSION,
        "signal_count": len(signals),
        "signal_counts_by_origin": dict(signal_counts),
        "concept_count": len(concepts),
        "concept_counts_by_status": dict(concept_counts),
        "link_count": len(links),
        "link_counts_by_status": dict(link_counts),
        "alias_count": len(deduped_aliases),
        "evidence_count": len(evidence),
        "search_index_preview_count": len(deduped_search_index),
        "rejected_signal_count": len(rejected),
        "ambiguous_link_count": len(ambiguous),
        "llm_usage": {"used": False, "policy": "disabled_for_sc1_deterministic_core"},
    }
    return SourceConceptResolutionResult(
        run_id=run_id,
        signals=tuple(signals),
        concepts=tuple(concepts),
        aliases=deduped_aliases,
        evidence=tuple(evidence),
        links=tuple(links),
        search_index=deduped_search_index,
        rejected_signals=tuple(rejected),
        ambiguous_links=tuple(ambiguous),
        merge_candidates=tuple(_merge_candidate_review(concepts)),
        summary=summary,
    )


def aliases_for_concept(
    concept_key: str,
    aliases: Sequence[SourceConceptAliasDraft],
) -> list[SourceConceptAliasDraft]:
    return [alias for alias in aliases if alias.concept_key == concept_key]


def _dedupe_aliases(aliases: Sequence[SourceConceptAliasDraft]) -> list[SourceConceptAliasDraft]:
    by_key: dict[tuple[str, str, str], SourceConceptAliasDraft] = {}
    for alias in aliases:
        by_key[(alias.concept_key, alias.alias_key, alias.alias_role)] = alias
    return list(by_key.values())


def _dedupe_search_index(items: Sequence[SourceConceptSearchIndexDraft]) -> list[SourceConceptSearchIndexDraft]:
    by_key: dict[tuple[str, str, str], SourceConceptSearchIndexDraft] = {}
    for item in items:
        by_key[(item.concept_key, item.search_key, item.alias_role)] = item
    return list(by_key.values())


def _build_concept_draft(
    concept_key: str,
    signals: Sequence[SourceConceptSignalDraft],
    *,
    run_id: str,
) -> SourceConceptDraft:
    trust_counts = Counter(signal.trust_tier for signal in signals)
    origin_counts = Counter(signal.origin_type for signal in signals)
    role_counts = Counter(signal.role_hint for signal in signals)
    providers = {signal.provider for signal in signals if signal.provider}
    medias = {signal.media_id for signal in signals if signal.media_id is not None}
    records = {signal.source_metadata_record_id for signal in signals if signal.source_metadata_record_id is not None}
    non_ai_signals = [signal for signal in signals if signal.origin_type != "ai_model_tag"]
    non_alias_signals = [signal for signal in signals if signal.origin_type != "source_alias_candidate"]
    all_ai = bool(signals) and not non_ai_signals
    all_alias_candidate = bool(signals) and not non_alias_signals
    all_weak_or_title = all(signal.trust_tier in {"weak"} or signal.role_hint == "source_title" for signal in signals)
    has_strong = any(signal.trust_tier == "strong" for signal in signals)
    has_medium = any(signal.trust_tier in {"medium", "medium_ai"} for signal in signals)
    has_guard = "ambiguous:" in concept_key or concept_key.startswith("source_title:") or concept_key.startswith("unknown:")
    role_conflict = len({role for role in role_counts if role != "unknown"}) > 1

    if role_conflict or has_guard or all_ai or all_alias_candidate or all_weak_or_title:
        status = "needs_review"
    elif has_strong:
        status = "active"
    elif has_medium and (len(signals) >= 2 or len(providers) >= 2 or records):
        status = "active"
    else:
        status = "needs_review"

    max_weight = max((TRUST_WEIGHTS.get(signal.trust_tier, 0.0) for signal in signals), default=0.0)
    evidence_score = sum(TRUST_WEIGHTS.get(signal.trust_tier, 0.0) for signal in signals)
    confidence_candidates = [
        source_confidence_score(signal.confidence, TRUST_WEIGHTS.get(signal.trust_tier, 0.25)) or 0.0
        for signal in signals
    ]
    confidence = max(confidence_candidates, default=max_weight)
    if status != "active":
        confidence = min(confidence, 0.74)
    if all_ai:
        confidence = min(confidence, 0.59)
    best_signal = sorted(
        signals,
        key=lambda signal: (
            TRUST_WEIGHTS.get(signal.trust_tier, 0.0),
            source_confidence_score(signal.confidence, 0.0) or 0.0,
            -len(signal.display_value),
        ),
        reverse=True,
    )[0]
    max_tier = max(TRUST_WEIGHTS, key=lambda tier: max_weight if TRUST_WEIGHTS[tier] == max_weight else -1)
    evidence_summary = {
        "trust_counts": dict(trust_counts),
        "origin_counts": dict(origin_counts),
        "role_counts": dict(role_counts),
        "provider_count": len(providers),
        "media_count": len(medias),
        "source_record_count": len(records),
        "guards": {
            "all_ai": all_ai,
            "all_alias_candidate": all_alias_candidate,
            "all_weak_or_title": all_weak_or_title,
            "role_conflict": role_conflict,
            "has_ambiguous_or_title_guard": has_guard,
        },
        "max_trust_tier": max_tier,
        "created_by_run_id": run_id,
    }
    return SourceConceptDraft(
        concept_key=concept_key,
        primary_display_name=best_signal.display_value,
        concept_type_hint=best_signal.role_hint if best_signal.role_hint in CONCEPT_ROLES else "unknown",
        status=status,
        confidence_score=round(confidence, 4),
        evidence_score=round(evidence_score, 4),
        media_count=len(medias),
        source_count=len(records) + len(providers),
        evidence_summary=evidence_summary,
        signals=tuple(signals),
    )


def _merge_candidate_review(concepts: Sequence[SourceConceptDraft]) -> list[dict[str, Any]]:
    by_alias: dict[str, list[SourceConceptDraft]] = defaultdict(list)
    for concept in concepts:
        parts = concept.concept_key.split(":")
        if len(parts) >= 2:
            by_alias[parts[1]].append(concept)
    review: list[dict[str, Any]] = []
    for alias_key, grouped in by_alias.items():
        if len(grouped) < 2:
            continue
        review.append(
            {
                "alias_key": alias_key,
                "concept_keys": [concept.concept_key for concept in grouped],
                "reason": "same_surface_key_multiple_contexts_review_only",
            }
        )
    return review


def persist_source_concept_resolution(
    db: Session,
    result: SourceConceptResolutionResult,
    *,
    apply: bool,
    inventory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    before_allowed = table_counts(db, SOURCE_CONCEPT_ALLOWED_WRITE_TABLES)
    before_forbidden = table_counts(db, FORBIDDEN_TRUTH_TABLES)
    if not apply:
        return {
            "apply": False,
            "planned": result.summary,
            "allowed_table_row_counts_before": before_allowed,
            "forbidden_table_row_counts_before": before_forbidden,
            "forbidden_truth_table_write_count": 0,
        }

    started = datetime.now(timezone.utc)
    run_row = db.query(SourceConceptResolutionRun).filter_by(run_id=result.run_id).one_or_none()
    if run_row is None:
        run_row = SourceConceptResolutionRun(
            run_id=result.run_id,
            run_label="phase_4_5_sc1_source_concept_resolver_core",
            scope="source_concept_core",
            resolver_version=RESOLVER_VERSION,
            mode="apply_db",
            status="running",
            started_at=started,
        )
        db.add(run_row)
        db.flush()

    signal_rows: dict[str, SourceConceptSignal] = {}
    for signal in result.signals:
        row = db.query(SourceConceptSignal).filter_by(signal_key=signal.signal_key).one_or_none()
        values = {
            "resolution_run_id": run_row.id,
            "origin_type": signal.origin_type,
            "origin_table": signal.origin_table,
            "origin_id": signal.origin_id,
            "provider": signal.provider,
            "media_id": signal.media_id,
            "source_metadata_record_id": signal.source_metadata_record_id,
            "source_record_id": signal.source_record_id,
            "raw_value": signal.raw_value,
            "display_value": signal.display_value,
            "normalized_key": signal.normalized_key,
            "canonical_key": signal.canonical_key,
            "role_hint": signal.role_hint,
            "work_context_key": signal.work_context_key,
            "parenthetical_base": signal.parenthetical_base,
            "parenthetical_context": signal.parenthetical_context,
            "source_kind": signal.source_kind,
            "trust_tier": signal.trust_tier,
            "confidence": signal.confidence,
            "status": signal.status,
            "evidence_payload": signal.evidence_payload,
            "source_run_id": signal.source_run_id,
            "created_by_run_id": result.run_id,
        }
        if row is None:
            row = SourceConceptSignal(signal_key=signal.signal_key, **values)
            db.add(row)
            db.flush()
        else:
            for key, value in values.items():
                setattr(row, key, value)
        signal_rows[signal.signal_key] = row

    concept_rows: dict[str, SourceConcept] = {}
    for concept in result.concepts:
        row = db.query(SourceConcept).filter_by(concept_key=concept.concept_key).one_or_none()
        values = {
            "primary_display_name": concept.primary_display_name,
            "concept_type_hint": concept.concept_type_hint,
            "status": concept.status,
            "confidence_score": concept.confidence_score,
            "evidence_score": concept.evidence_score,
            "media_count": concept.media_count,
            "source_count": concept.source_count,
            "created_by_run_id": result.run_id,
            "evidence_summary_json": concept.evidence_summary,
            "lifecycle_payload": {"resolver_version": RESOLVER_VERSION, "last_run_id": result.run_id},
        }
        if row is None:
            row = SourceConcept(concept_key=concept.concept_key, **values)
            db.add(row)
            db.flush()
        else:
            for key, value in values.items():
                setattr(row, key, value)
        concept_rows[concept.concept_key] = row

    for alias in result.aliases:
        concept_row = concept_rows.get(alias.concept_key)
        signal_row = signal_rows.get(alias.signal_key)
        if concept_row is None:
            continue
        row = (
            db.query(SourceConceptAlias)
            .filter_by(concept_id=concept_row.id, alias_key=alias.alias_key, alias_role=alias.alias_role)
            .one_or_none()
        )
        values = {
            "alias_value": alias.alias_value,
            "display_name": alias.display_name,
            "language_hint": alias.language_hint,
            "script_hint": alias.script_hint,
            "status": alias.status,
            "confidence": alias.confidence,
            "source_signal_id": signal_row.id if signal_row else None,
            "evidence_payload": alias.evidence_payload,
            "created_by_run_id": result.run_id,
        }
        if row is None:
            row = SourceConceptAlias(
                concept_id=concept_row.id,
                alias_key=alias.alias_key,
                alias_role=alias.alias_role,
                **values,
            )
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)

    for evidence in result.evidence:
        concept_row = concept_rows.get(evidence.concept_key)
        signal_row = signal_rows.get(evidence.signal_key)
        if concept_row is None or signal_row is None:
            continue
        row = (
            db.query(SourceConceptEvidence)
            .filter_by(concept_id=concept_row.id, signal_id=signal_row.id, evidence_type=evidence.evidence_type)
            .one_or_none()
        )
        values = {
            "media_id": evidence.media_id,
            "source_metadata_record_id": evidence.source_metadata_record_id,
            "provider": evidence.provider,
            "evidence_strength": evidence.evidence_strength,
            "payload": evidence.payload,
            "run_id": result.run_id,
            "status": evidence.status,
        }
        if row is None:
            row = SourceConceptEvidence(
                concept_id=concept_row.id,
                signal_id=signal_row.id,
                evidence_type=evidence.evidence_type,
                **values,
            )
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)

    for link in result.links:
        concept_row = concept_rows.get(link.concept_key)
        signal_row = signal_rows.get(link.signal_key)
        if concept_row is None or signal_row is None:
            continue
        row = (
            db.query(SourceConceptSignalLink)
            .filter_by(signal_id=signal_row.id, concept_id=concept_row.id, run_id=result.run_id)
            .one_or_none()
        )
        values = {
            "link_status": link.link_status,
            "confidence": link.confidence,
            "resolution_reason_code": link.resolution_reason_code,
            "negative_reason_code": link.negative_reason_code,
            "resolver_version": RESOLVER_VERSION,
            "evidence_payload": link.evidence_payload,
        }
        if row is None:
            row = SourceConceptSignalLink(
                signal_id=signal_row.id,
                concept_id=concept_row.id,
                run_id=result.run_id,
                **values,
            )
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)

    for item in result.search_index:
        concept_row = concept_rows.get(item.concept_key)
        if concept_row is None:
            continue
        row = (
            db.query(SourceConceptSearchIndex)
            .filter_by(concept_id=concept_row.id, search_key=item.search_key, alias_role=item.alias_role)
            .one_or_none()
        )
        values = {
            "display_name": item.display_name,
            "weight": item.weight,
            "status": item.status,
            "evidence_refs_json": item.evidence_refs,
            "run_id": result.run_id,
        }
        if row is None:
            row = SourceConceptSearchIndex(
                concept_id=concept_row.id,
                search_key=item.search_key,
                alias_role=item.alias_role,
                **values,
            )
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)

    finished = datetime.now(timezone.utc)
    run_row.status = "completed"
    run_row.mode = "apply_db"
    run_row.resolver_version = RESOLVER_VERSION
    run_row.finished_at = finished
    run_row.runtime_seconds = (finished - started).total_seconds()
    run_row.input_signal_counts_json = result.summary.get("signal_counts_by_origin", {})
    run_row.linked_counts_json = result.summary.get("link_counts_by_status", {})
    run_row.concept_counts_json = result.summary.get("concept_counts_by_status", {})
    run_row.review_counts_json = {
        "ambiguous_links": len(result.ambiguous_links),
        "rejected_signals": len(result.rejected_signals),
        "merge_candidates": len(result.merge_candidates),
    }
    run_row.summary_json = {**result.summary, "inventory_counts_present": bool(inventory)}
    db.flush()
    after_allowed = table_counts(db, SOURCE_CONCEPT_ALLOWED_WRITE_TABLES)
    after_forbidden = table_counts(db, FORBIDDEN_TRUTH_TABLES)
    forbidden_deltas = {key: after_forbidden[key] - before_forbidden.get(key, 0) for key in after_forbidden}
    proof = {
        "allowed_tables": list(SOURCE_CONCEPT_ALLOWED_WRITE_TABLES),
        "allowed_table_row_counts_before": before_allowed,
        "allowed_table_row_counts_after": after_allowed,
        "allowed_table_row_deltas": {key: after_allowed[key] - before_allowed.get(key, 0) for key in after_allowed},
        "forbidden_tables": list(FORBIDDEN_TRUTH_TABLES),
        "forbidden_table_row_counts_before": before_forbidden,
        "forbidden_table_row_counts_after": after_forbidden,
        "forbidden_table_row_deltas": forbidden_deltas,
        "forbidden_truth_table_write_count": sum(1 for delta in forbidden_deltas.values() if delta != 0),
    }
    run_row.no_truth_write_proof_json = proof
    db.commit()
    return {"apply": True, "run_db_id": run_row.id, **proof}


def run_source_concept_resolution(
    db: Session,
    *,
    run_id: str,
    f7a_run_id: str | None = None,
    apply: bool,
) -> tuple[SourceConceptResolutionResult, dict[str, Any], dict[str, Any]]:
    inventory = source_signal_inventory(db, f7a_run_id=f7a_run_id)
    signals = build_source_concept_signals(db, run_id=run_id, f7a_run_id=f7a_run_id)
    result = resolve_source_concepts(signals, run_id=run_id)
    persistence = persist_source_concept_resolution(db, result, apply=apply, inventory=inventory)
    return result, inventory, persistence


def _candidate_bundle_rows(candidate_bundle_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with candidate_bundle_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def import_f7a_final_pack_candidates(
    db: Session,
    *,
    pack_dir: Path,
    apply: bool,
) -> dict[str, Any]:
    """Persist final F7a artifact candidates without LLM/API/provider calls."""

    summary_path = pack_dir / "summary.json"
    candidate_bundle_path = pack_dir / "candidate-bundle.jsonl"
    if not summary_path.exists() or not candidate_bundle_path.exists():
        raise FileNotFoundError(f"Missing F7a final pack summary or candidate bundle under {pack_dir}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    run_id = str(summary["run_id"])
    rows = _candidate_bundle_rows(candidate_bundle_path)
    existing_count = (
        db.query(SourceNameCandidate)
        .join(
            SourceNameCandidateExtractionRun,
            SourceNameCandidate.extraction_run_id == SourceNameCandidateExtractionRun.id,
        )
        .filter(SourceNameCandidateExtractionRun.run_id == run_id)
        .filter(SourceNameCandidate.status == "active")
        .count()
    )
    audit = {
        "run_id": run_id,
        "pack_dir": str(pack_dir),
        "candidate_bundle_count": len(rows),
        "existing_db_candidate_count_for_run": existing_count,
        "needs_import": existing_count != len(rows),
        "apply": apply,
    }
    if not audit["needs_import"]:
        return {**audit, "persistence": {"skipped": True, "reason": "db_count_matches_candidate_bundle"}}

    candidates = tuple(_candidate_from_pack_row(row) for row in rows)
    verdicts = tuple(_verdicts_from_pack_rows(rows))
    groups = tuple(_groups_from_pack_rows(rows))
    bundle = ExtractionResultBundle(
        run_id=run_id,
        run_label="f7a_final_validation_pack_local_backfill",
        groups=groups,
        record_verdicts=verdicts,
        candidates=candidates,
        rejected_tags=(),
        meta_tags=(),
        ambiguous_items=(),
        llm_inputs=(),
        llm_outputs=(),
        validation_failures=(),
        summary={
            "source": "f7a_final_validation_pack_local_backfill",
            "candidate_bundle_count": len(rows),
            "original_summary": {
                "total_candidates": summary.get("candidate_summary", {}).get("total"),
                "active_candidates": summary.get("candidate_summary", {}).get("status", {}).get("active"),
                "needs_review_candidates": summary.get("candidate_summary", {}).get("status", {}).get("needs_review"),
                "run_id": run_id,
                "validated_head": summary.get("validated_head"),
            },
            "llm_or_provider_calls": False,
        },
    )
    persistence = persist_extraction_bundle(
        db,
        bundle,
        apply=apply,
        provider_summary={
            "source": "f7a_final_validation_pack_local_backfill",
            "llm_or_provider_calls": False,
        },
        input_scope={
            "pack_dir": str(pack_dir),
            "candidate_bundle": str(candidate_bundle_path),
            "summary": str(summary_path),
        },
    )
    return {**audit, "persistence": persistence}


def _candidate_from_pack_row(row: Mapping[str, Any]) -> CandidateDraft:
    return CandidateDraft(
        group_key=str(row.get("group_key") or "unknown_group"),
        provider=str(row.get("provider") or "unknown"),
        source_metadata_record_id=row.get("source_metadata_record_id"),
        media_id=row.get("media_id"),
        origin_type=str(row.get("origin_type") or "unknown"),
        origin_id=str(row.get("origin_id")) if row.get("origin_id") is not None else None,
        raw_value=str(row.get("raw_value") or row.get("display_name") or row.get("canonical_key") or ""),
        display_name=str(row.get("display_name") or row.get("raw_value") or row.get("canonical_key") or ""),
        normalized_value=str(row.get("normalized_value") or row.get("canonical_key") or ""),
        canonical_key=str(row.get("canonical_key") or canonical_key(row.get("display_name") or row.get("raw_value"))),
        candidate_role=str(row.get("candidate_role") or "unknown_name_like"),
        candidate_status=str(row.get("candidate_status") or "needs_review"),
        extraction_verdict=str(row.get("extraction_verdict") or "candidate_from_final_pack"),
        language_hint=row.get("language_hint"),
        script_hint=row.get("script_hint"),
        work_context=row.get("work_context"),
        work_context_key=row.get("work_context_key"),
        parenthetical_base=row.get("parenthetical_base"),
        parenthetical_context=row.get("parenthetical_context"),
        extraction_action=str(row.get("extraction_action") or "accepted_from_final_validation_pack"),
        confidence=row.get("confidence"),
        reason=row.get("reason"),
        rejection_reason=row.get("rejection_reason"),
        no_name_reason=row.get("no_name_reason"),
        evidence_payload=dict(row.get("evidence_payload") or {}),
        candidate_key=str(row.get("candidate_key") or value_hash(row)),
    )


def _groups_from_pack_rows(rows: Sequence[Mapping[str, Any]]) -> list[SourceCandidateInputGroup]:
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[str(row.get("group_key") or "unknown_group")].append(row)
    groups: list[SourceCandidateInputGroup] = []
    for group_key, group_rows in by_group.items():
        first = group_rows[0]
        groups.append(
            SourceCandidateInputGroup(
                group_key=group_key,
                provider=str(first.get("provider") or "unknown"),
                source_metadata_record_id=first.get("source_metadata_record_id"),
                media_id=first.get("media_id"),
                data_origin="f7a_final_validation_pack_local_backfill",
            )
        )
    return groups


def _verdicts_from_pack_rows(rows: Sequence[Mapping[str, Any]]) -> list[RecordVerdictDraft]:
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[str(row.get("group_key") or "unknown_group")].append(row)
    verdicts: list[RecordVerdictDraft] = []
    for group_key, group_rows in by_group.items():
        first = group_rows[0]
        statuses = Counter(str(row.get("candidate_status") or "unknown") for row in group_rows)
        roles = Counter(str(row.get("candidate_role") or "unknown") for row in group_rows)
        verdicts.append(
            RecordVerdictDraft(
                group_key=group_key,
                provider=str(first.get("provider") or "unknown"),
                source_metadata_record_id=first.get("source_metadata_record_id"),
                media_id=first.get("media_id"),
                extraction_verdict=str(first.get("extraction_verdict") or "candidate_from_final_pack"),
                verdict_reason="Synthesized from F7a final validation pack candidate-bundle.jsonl; no LLM/provider calls.",
                no_name_reason=None,
                candidate_count=len(group_rows),
                rejected_count=0,
                meta_count=0,
                ambiguous_count=0,
                confidence_summary={"candidate_status": dict(statuses), "candidate_role": dict(roles)},
                extraction_warnings_json=[],
                evidence_payload={
                    "source": "f7a_final_validation_pack_local_backfill",
                    "candidate_bundle_rows": len(group_rows),
                },
            )
        )
    return verdicts


def result_to_artifact_payload(result: SourceConceptResolutionResult) -> dict[str, Any]:
    return {
        "summary": result.summary,
        "signals": [asdict(item) for item in result.signals],
        "concepts": [
            {
                **asdict(item),
                "signals": [signal.signal_key for signal in item.signals],
            }
            for item in result.concepts
        ],
        "aliases": [asdict(item) for item in result.aliases],
        "evidence": [asdict(item) for item in result.evidence],
        "links": [asdict(item) for item in result.links],
        "search_index": [asdict(item) for item in result.search_index],
        "rejected_signals": list(result.rejected_signals),
        "ambiguous_links": list(result.ambiguous_links),
        "merge_candidates": list(result.merge_candidates),
    }


def build_artifact_consistency_check(payload: Mapping[str, Any], persistence: Mapping[str, Any]) -> dict[str, Any]:
    signal_keys = {row["signal_key"] for row in payload["signals"]}
    concept_keys = {row["concept_key"] for row in payload["concepts"]}
    link_signal_missing = [row for row in payload["links"] if row["signal_key"] not in signal_keys]
    link_concept_missing = [row for row in payload["links"] if row["concept_key"] not in concept_keys]
    alias_concept_missing = [row for row in payload["aliases"] if row["concept_key"] not in concept_keys]
    evidence_concept_missing = [row for row in payload["evidence"] if row["concept_key"] not in concept_keys]
    forbidden_writes = int(persistence.get("forbidden_truth_table_write_count", 0) or 0)
    passed = not any(
        [
            link_signal_missing,
            link_concept_missing,
            alias_concept_missing,
            evidence_concept_missing,
            forbidden_writes,
        ]
    )
    return {
        "passed": passed,
        "checked_at": utc_now_iso(),
        "signal_count": len(signal_keys),
        "concept_count": len(concept_keys),
        "missing_link_signals": len(link_signal_missing),
        "missing_link_concepts": len(link_concept_missing),
        "missing_alias_concepts": len(alias_concept_missing),
        "missing_evidence_concepts": len(evidence_concept_missing),
        "forbidden_truth_table_write_count": forbidden_writes,
    }
