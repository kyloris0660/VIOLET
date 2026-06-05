"""Phase 4.5-SC1 provider-neutral source concept resolver core.

The SourceConcept resolver is a source-layer soft linker. It groups
provider/source/tag/model name signals into unconfirmed concepts for review and
search-preview use. It deliberately does not write Entity truth,
MediaEntityAssignment truth, media_tags, TagTranslation, ProviderCache, or
confirmed assignment state.
"""

from __future__ import annotations

import asyncio
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
    primary_openai_provider_from_settings,
    table_counts,
)

RESOLVER_VERSION = "source_concept_resolver_core_v2_graph"
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
GENERIC_CONTEXT_STOP_KEYS = {
    "animal",
    "bunny",
    "cheerleading",
    "clothes",
    "clothing",
    "creature",
    "cup",
    "flower",
    "food",
    "medium",
    "object",
    "place",
    "series",
    "shape",
    "sky",
    "small",
    "spring",
    "swimsuit",
    "symbol",
    "track",
}
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


POPULARITY_RE = re.compile(
    r"(?i)(users|bookmarks|views|fav|favorites|"
    r"\u6536\u85cf|\u5165\u308a|users\u5165\u308a|bookmarks\u5165\u308a)"
)
PAREN_RE = re.compile(r"^\s*(?P<base>.+?)(?:_\(|\(|\uff08)(?P<context>.+?)(?:\)|\uff09)\s*$")


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
class SourceConceptEdgeDraft:
    edge_key: str
    left_signal_key: str
    right_signal_key: str
    edge_type: str
    weight: float
    evidence_source: str
    status: str
    resolution_reason_code: str
    negative_reason_code: str | None
    union_allowed: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class LLMAdjudicationConfig:
    enabled: bool = False
    max_calls: int = 0
    max_budget_usd: float = 50.0
    max_block_size: int = 12
    prompt_version: str = "source_concept_llm_pair_adjudication_v1"
    model_label: str = "primary_openai"
    cache_dir: str | None = None
    fail_if_unavailable: bool = False


@dataclass(frozen=True)
class LLMAdjudicationPlan:
    enabled: bool
    projected_calls: int
    projected_input_tokens: int
    projected_output_tokens: int
    projected_cost_usd: float
    max_calls: int
    max_budget_usd: float
    selected_block_count: int
    skipped_block_count: int
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class SourceConceptResolutionResult:
    run_id: str
    signals: tuple[SourceConceptSignalDraft, ...]
    edge_candidates: tuple[SourceConceptEdgeDraft, ...]
    concepts: tuple[SourceConceptDraft, ...]
    aliases: tuple[SourceConceptAliasDraft, ...]
    evidence: tuple[SourceConceptEvidenceDraft, ...]
    links: tuple[SourceConceptLinkDraft, ...]
    search_index: tuple[SourceConceptSearchIndexDraft, ...]
    rejected_signals: tuple[dict[str, Any], ...]
    ambiguous_links: tuple[dict[str, Any], ...]
    merge_candidates: tuple[dict[str, Any], ...]
    overmerge_review: tuple[dict[str, Any], ...]
    undermerge_review: tuple[dict[str, Any], ...]
    ai_signal_review: tuple[dict[str, Any], ...]
    llm_judgments: tuple[dict[str, Any], ...]
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
        parsed_base, parsed_context = parse_parenthetical(row.raw_tag)
        if role == "unknown" and parsed_base:
            role = "character"
        trust = "medium" if role != "unknown" else "weak"
        status = source_status_to_concept_status(row.status, default="needs_review")
        category_raw = normalize_source_text(row.source_category_raw).lower()
        if category_raw in {"", "0", "general", "meta", "5"} and role == "unknown" and not parsed_base:
            trust = "rejected"
            status = "rejected"
        if is_popularity_or_meta(row.raw_tag):
            trust = "rejected"
            status = "rejected"
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
                parenthetical_base=parsed_base,
                parenthetical_context=parsed_context,
                source_kind=row.source_tag_kind,
                trust_tier=trust,
                confidence=row.confidence,
                status=status,
                evidence_payload={
                    "observation_key": row.observation_key,
                    "source_category_raw": row.source_category_raw,
                    "non_concept_reason": "general_source_tag_without_name_context" if trust == "rejected" else None,
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


def _work_context_keys(signal: SourceConceptSignalDraft) -> set[str]:
    contexts: set[str] = set()
    if signal.role_hint == "work":
        context = signal.work_context_key or signal.canonical_key
        if context:
            contexts.add(canonical_key(context))
    contexts.discard("")
    return contexts


def _declared_context_keys(signal: SourceConceptSignalDraft) -> set[str]:
    contexts: set[str] = set(_work_context_keys(signal))
    if signal.role_hint in {"character", "person", "unknown"}:
        for value in (signal.work_context_key, signal.parenthetical_context):
            key = canonical_key(value)
            if key:
                contexts.add(key)
        _parsed_base, parsed_context = parse_parenthetical(signal.raw_value)
        parsed_key = canonical_key(parsed_context)
        if parsed_key:
            contexts.add(parsed_key)
    contexts.discard("")
    return contexts


def _context_candidates_by_scope(
    signals: Sequence[SourceConceptSignalDraft],
    context_alias_by_key: Mapping[str, str] | None = None,
    include_parenthetical_contexts: bool = False,
) -> dict[tuple[str, int], set[str]]:
    contexts: dict[tuple[str, int], set[str]] = defaultdict(set)
    for signal in signals:
        if signal.status == "rejected" or signal.trust_tier == "rejected":
            continue
        declared = _declared_context_keys(signal) if include_parenthetical_contexts else _work_context_keys(signal)
        signal_contexts = {_context_alias_key(context, context_alias_by_key) for context in declared}
        signal_contexts.discard(None)
        if not signal_contexts:
            continue
        if signal.media_id is not None:
            contexts[("media", signal.media_id)].update(str(context) for context in signal_contexts)
        if signal.source_metadata_record_id is not None:
            contexts[("record", signal.source_metadata_record_id)].update(str(context) for context in signal_contexts)
    return contexts


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, key: str) -> None:
        self.parent.setdefault(key, key)

    def find(self, key: str) -> str:
        self.add(key)
        parent = self.parent[key]
        if parent != key:
            self.parent[key] = self.find(parent)
        return self.parent[key]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root

    def groups(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for key in list(self.parent):
            grouped[self.find(key)].append(key)
        return {root: sorted(values) for root, values in grouped.items()}


def _compact_context_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def _is_context_alias_candidate(value: str) -> bool:
    compact = _compact_context_key(value)
    if not compact or compact in GENERIC_CONTEXT_STOP_KEYS:
        return False
    if any(ord(char) > 127 for char in value):
        return True
    return "_" in value or ":" in value or any(char.isdigit() for char in value) or len(compact) > 8


def _context_equivalence_lookup(signals: Sequence[SourceConceptSignalDraft]) -> dict[str, str]:
    work_contexts_by_scope = _context_candidates_by_scope(signals, include_parenthetical_contexts=False)
    declared_contexts_by_scope = _context_candidates_by_scope(signals, include_parenthetical_contexts=True)
    uf = UnionFind()
    cooccurrence_counts: Counter[tuple[str, str]] = Counter()
    for scope, work_contexts in work_contexts_by_scope.items():
        if scope[0] != "record":
            continue
        work_scoped = sorted(context for context in work_contexts if context)
        if len(work_scoped) != 1:
            continue
        declared_scoped = sorted(
            context
            for context in declared_contexts_by_scope.get(scope, set())
            if context and context not in work_contexts and _is_context_alias_candidate(context)
        )
        if not work_scoped or not declared_scoped or len(declared_scoped) > 12:
            continue
        for work_context in work_scoped:
            uf.add(work_context)
            for declared_context in declared_scoped:
                if (
                    any(ord(char) > 127 for char in work_context)
                    and any(ord(char) > 127 for char in declared_context)
                    and _compact_context_key(work_context) != _compact_context_key(declared_context)
                ):
                    continue
                uf.add(declared_context)
                cooccurrence_counts[tuple(sorted((work_context, declared_context)))] += 1
    for (left, right), count in cooccurrence_counts.items():
        if count >= 2 or _compact_context_key(left) == _compact_context_key(right):
            uf.union(left, right)
    lookup: dict[str, str] = {}
    for members in uf.groups().values():
        if len(members) < 2:
            continue
        root = sorted(members, key=lambda value: (not _has_latin(value), len(value), value))[0]
        for member in members:
            lookup[member] = root
    return lookup


def signal_is_ai_origin(signal: SourceConceptSignalDraft) -> bool:
    payload = signal.evidence_payload or {}
    text = " ".join(
        str(value or "").lower()
        for value in (
            signal.origin_type,
            signal.source_kind,
            signal.provider,
            signal.trust_tier,
            payload.get("source"),
            payload.get("source_kind"),
            payload.get("unit_candidate_origin_type"),
        )
    )
    return signal.trust_tier == "medium_ai" or signal.origin_type == "ai_model_tag" or any(hint in text for hint in AI_SOURCE_HINTS)


def signal_surface_key(signal: SourceConceptSignalDraft) -> str | None:
    if signal.role_hint in {"character", "person"}:
        if signal.parenthetical_base:
            return canonical_key(signal.parenthetical_base)
        parsed_base, _parsed_context = parse_parenthetical(signal.raw_value)
        if parsed_base:
            return canonical_key(parsed_base)
    return signal.canonical_key or signal.normalized_key


def _context_alias_key(context: str | None, context_alias_by_key: Mapping[str, str] | None = None) -> str | None:
    key = canonical_key(context)
    if not key:
        return None
    if not context_alias_by_key:
        return key
    return context_alias_by_key.get(key, key)


def signal_context_key(
    signal: SourceConceptSignalDraft,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    context_alias_by_key: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None]:
    if signal.work_context_key:
        return _context_alias_key(signal.work_context_key, context_alias_by_key), "explicit_work_context"
    if signal.parenthetical_context:
        return _context_alias_key(signal.parenthetical_context, context_alias_by_key), "parenthetical_context"
    _parsed_base, parsed_context = parse_parenthetical(signal.raw_value)
    if parsed_context:
        return _context_alias_key(parsed_context, context_alias_by_key), "parenthetical_context"
    return _infer_unique_context(signal, context_by_scope, context_alias_by_key=context_alias_by_key)


def signal_role_group(signal: SourceConceptSignalDraft) -> str:
    role = signal.role_hint or "unknown"
    if role in {"character", "person"}:
        return "character_person"
    return role


def roles_compatible(left: SourceConceptSignalDraft, right: SourceConceptSignalDraft) -> bool:
    left_role = left.role_hint or "unknown"
    right_role = right.role_hint or "unknown"
    if left_role == right_role:
        return True
    if "unknown" in {left_role, right_role}:
        return True
    if left_role in {"character", "person"} and right_role in {"character", "person"}:
        return True
    return False


def _alias_relation_is_same_concept(signal: SourceConceptSignalDraft) -> bool:
    relation = normalize_source_text((signal.evidence_payload or {}).get("relation_type")).lower()
    if not relation:
        return False
    return relation in {
        "same_source_concept",
        "same_concept",
        "same_character",
        "same_name",
        "alias",
        "translation",
        "translation_alias",
        "same_as",
    }


def _alias_component_lookup(signals: Sequence[SourceConceptSignalDraft]) -> dict[str, str]:
    uf = UnionFind()
    for signal in signals:
        if signal.origin_type != "source_alias_candidate":
            continue
        if signal.status == "rejected" or signal.trust_tier == "rejected":
            continue
        if not _alias_relation_is_same_concept(signal):
            continue
        source_key = canonical_key((signal.evidence_payload or {}).get("source_name_key"))
        target_key = canonical_key((signal.evidence_payload or {}).get("target_name_key"))
        if not source_key or not target_key or source_key == target_key:
            continue
        uf.union(source_key, target_key)
    lookup: dict[str, str] = {}
    for members in uf.groups().values():
        if len(members) < 2:
            continue
        component_key = "alias_component:" + value_hash(members, 16)
        for member in members:
            lookup[member] = component_key
    return lookup


def _signal_alias_component(signal: SourceConceptSignalDraft, alias_component_by_key: Mapping[str, str]) -> str | None:
    candidates = [signal.canonical_key, signal.normalized_key, signal_surface_key(signal)]
    for key in candidates:
        if key and key in alias_component_by_key:
            return alias_component_by_key[key]
    return None


def _signal_blocking_keys(
    signal: SourceConceptSignalDraft,
    *,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    alias_component_by_key: Mapping[str, str],
    context_alias_by_key: Mapping[str, str] | None = None,
) -> set[str]:
    keys: set[str] = set()
    if signal.status == "rejected" or signal.trust_tier == "rejected":
        return keys
    surface = signal_surface_key(signal)
    canonical = signal.canonical_key or signal.normalized_key
    role_group = signal_role_group(signal)
    context, _context_reason = signal_context_key(signal, context_by_scope, context_alias_by_key)
    if canonical:
        keys.add(f"exact:{role_group}:{canonical}")
    if surface and surface != canonical:
        keys.add(f"surface:{role_group}:{surface}")
    if surface and context:
        keys.add(f"context:{role_group}:{surface}:work:{context}")
    alias_component = _signal_alias_component(signal, alias_component_by_key)
    if alias_component:
        keys.add(alias_component)
    if signal.source_metadata_record_id is not None and signal.role_hint in {"character", "person", "unknown"}:
        keys.add(f"record_context:{signal.source_metadata_record_id}")
    if signal.media_id is not None and signal.role_hint in {"character", "person", "unknown"}:
        keys.add(f"media_context:{signal.media_id}")
    return keys


def _edge_key(left: SourceConceptSignalDraft, right: SourceConceptSignalDraft, edge_type: str) -> str:
    left_key, right_key = sorted((left.signal_key, right.signal_key))
    return f"{edge_type}:{value_hash([left_key, right_key], 20)}"


def _edge(
    left: SourceConceptSignalDraft,
    right: SourceConceptSignalDraft,
    *,
    edge_type: str,
    weight: float,
    evidence_source: str,
    status: str,
    reason_code: str,
    union_allowed: bool,
    negative_reason_code: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> SourceConceptEdgeDraft:
    return SourceConceptEdgeDraft(
        edge_key=_edge_key(left, right, edge_type),
        left_signal_key=left.signal_key,
        right_signal_key=right.signal_key,
        edge_type=edge_type,
        weight=round(weight, 4),
        evidence_source=evidence_source,
        status=status,
        resolution_reason_code=reason_code,
        negative_reason_code=negative_reason_code,
        union_allowed=union_allowed,
        payload=dict(payload or {}),
    )


def _pair_edge(
    left: SourceConceptSignalDraft,
    right: SourceConceptSignalDraft,
    *,
    block_key: str,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    alias_component_by_key: Mapping[str, str],
    context_alias_by_key: Mapping[str, str] | None = None,
) -> SourceConceptEdgeDraft | None:
    if left.signal_key == right.signal_key:
        return None
    left_surface = signal_surface_key(left)
    right_surface = signal_surface_key(right)
    left_context, left_context_reason = signal_context_key(left, context_by_scope, context_alias_by_key)
    right_context, right_context_reason = signal_context_key(right, context_by_scope, context_alias_by_key)
    same_context = bool(left_context and right_context and left_context == right_context)
    same_canonical = bool((left.canonical_key or left.normalized_key) == (right.canonical_key or right.normalized_key))
    same_surface = bool(left_surface and right_surface and left_surface == right_surface)
    alias_component = _signal_alias_component(left, alias_component_by_key)
    same_alias_component = bool(alias_component and alias_component == _signal_alias_component(right, alias_component_by_key))
    left_ai = signal_is_ai_origin(left)
    right_ai = signal_is_ai_origin(right)
    has_non_ai = not (left_ai and right_ai)

    if not roles_compatible(left, right):
        return _edge(
            left,
            right,
            edge_type="negative_guard",
            weight=0.0,
            evidence_source=block_key,
            status="rejected",
            reason_code="role_conflict_guard",
            negative_reason_code="role_conflict",
            union_allowed=False,
            payload={
                "left_role": left.role_hint,
                "right_role": right.role_hint,
                "left_surface_key": left_surface,
                "right_surface_key": right_surface,
            },
        )

    if left.role_hint == "source_title" or right.role_hint == "source_title":
        return _edge(
            left,
            right,
            edge_type="context_only",
            weight=0.1,
            evidence_source=block_key,
            status="weak",
            reason_code="source_title_context_only",
            negative_reason_code="source_title_only_guard",
            union_allowed=False,
            payload={"left_role": left.role_hint, "right_role": right.role_hint},
        )

    surface_for_guard = left_surface if left_surface == right_surface else left_surface or right_surface
    if (
        same_surface
        and not same_context
        and {left.role_hint, right.role_hint} & {"character", "person", "unknown"}
        and is_short_ambiguous_key(surface_for_guard)
    ):
        same_scope = (
            (left.media_id is not None and left.media_id == right.media_id)
            or (
                left.source_metadata_record_id is not None
                and left.source_metadata_record_id == right.source_metadata_record_id
            )
        )
        return _edge(
            left,
            right,
            edge_type="negative_guard",
            weight=0.0,
            evidence_source=block_key,
            status="needs_review",
            reason_code="ambiguous_short_guard",
            negative_reason_code=None if same_scope else "ambiguous_short_without_work_context",
            union_allowed=False,
            payload={"surface_key": surface_for_guard, "left_context": left_context, "right_context": right_context},
        )

    if same_surface and same_context:
        if left_ai and right_ai:
            status = "needs_review"
            union_allowed = True
            reason = "medium_ai_same_context_review"
            weight = 0.45
        else:
            status = "active"
            union_allowed = True
            reason = "same_surface_and_work_context"
            weight = 0.92 if has_non_ai else 0.45
        return _edge(
            left,
            right,
            edge_type="same_surface_context",
            weight=weight,
            evidence_source=block_key,
            status=status,
            reason_code=reason,
            union_allowed=union_allowed,
            payload={
                "surface_key": left_surface,
                "work_context_key": left_context,
                "left_context_reason": left_context_reason,
                "right_context_reason": right_context_reason,
            },
        )

    if same_alias_component:
        non_alias_count = int(left.origin_type != "source_alias_candidate") + int(right.origin_type != "source_alias_candidate")
        alias_only = non_alias_count == 0
        status = "needs_review" if alias_only or (left_ai and right_ai) else "active"
        return _edge(
            left,
            right,
            edge_type="alias_candidate_edge",
            weight=0.82 if status == "active" else 0.58,
            evidence_source=block_key,
            status=status,
            reason_code="alias_candidate_component",
            union_allowed=True,
            payload={"alias_component_key": alias_component, "alias_only": alias_only},
        )

    if same_canonical:
        if left_ai and right_ai:
            status = "needs_review"
            reason = "medium_ai_exact_key_review"
            weight = 0.45
        elif left.trust_tier == "weak" and right.trust_tier == "weak":
            status = "weak"
            reason = "weak_exact_key_context_needed"
            weight = 0.25
        else:
            status = "active"
            reason = "exact_canonical_key"
            weight = 0.86
        return _edge(
            left,
            right,
            edge_type="exact_canonical_key",
            weight=weight,
            evidence_source=block_key,
            status=status,
            reason_code=reason,
            union_allowed=status in {"active", "needs_review"},
            payload={"canonical_key": left.canonical_key or left.normalized_key},
        )

    if block_key.startswith("record_context:") or block_key.startswith("media_context:"):
        return _edge(
            left,
            right,
            edge_type="cooccurrence_context",
            weight=0.18,
            evidence_source=block_key,
            status="weak",
            reason_code="cooccurrence_only_context",
            union_allowed=False,
            payload={
                "left_surface_key": left_surface,
                "right_surface_key": right_surface,
                "left_context": left_context,
                "right_context": right_context,
            },
        )
    return None


def _generate_edges(
    signals: Sequence[SourceConceptSignalDraft],
    *,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    alias_component_by_key: Mapping[str, str],
    context_alias_by_key: Mapping[str, str] | None = None,
    max_block_size: int = 60,
) -> tuple[tuple[SourceConceptEdgeDraft, ...], list[dict[str, Any]], dict[str, Any]]:
    blocks: dict[str, list[SourceConceptSignalDraft]] = defaultdict(list)
    for signal in signals:
        for key in _signal_blocking_keys(
            signal,
            context_by_scope=context_by_scope,
            alias_component_by_key=alias_component_by_key,
            context_alias_by_key=context_alias_by_key,
        ):
            blocks[key].append(signal)

    edges: dict[str, SourceConceptEdgeDraft] = {}
    oversized_blocks: list[dict[str, Any]] = []
    processed_blocks = 0
    for block_key, block_signals in sorted(blocks.items()):
        unique_block_signals = list({signal.signal_key: signal for signal in block_signals}.values())
        if len(unique_block_signals) < 2:
            continue
        if len(unique_block_signals) > max_block_size:
            oversized_blocks.append(
                {
                    "block_key_hash": value_hash(block_key, 16),
                    "block_kind": block_key.split(":", 1)[0],
                    "signal_count": len(unique_block_signals),
                    "max_block_size": max_block_size,
                    "reason": "block_exceeded_pairwise_cap",
                }
            )
            if not (block_key.startswith("exact:") or block_key.startswith("context:")):
                continue
            ranked_block_signals = sorted(
                unique_block_signals,
                key=lambda signal: (
                    TRUST_WEIGHTS.get(signal.trust_tier, 0.0),
                    0 if signal_is_ai_origin(signal) else 1,
                    source_confidence_score(signal.confidence, 0.0) or 0.0,
                    signal.signal_key,
                ),
                reverse=True,
            )
            anchor = ranked_block_signals[0]
            processed_blocks += 1
            for other in ranked_block_signals[1:]:
                edge = _pair_edge(
                    anchor,
                    other,
                    block_key=block_key,
                    context_by_scope=context_by_scope,
                    alias_component_by_key=alias_component_by_key,
                    context_alias_by_key=context_alias_by_key,
                )
                if edge is None:
                    continue
                previous = edges.get(edge.edge_key)
                if previous is None or edge.weight > previous.weight or (edge.status == "active" and previous.status != "active"):
                    edges[edge.edge_key] = edge
            continue
        processed_blocks += 1
        for index, left in enumerate(unique_block_signals):
            for right in unique_block_signals[index + 1 :]:
                edge = _pair_edge(
                    left,
                    right,
                    block_key=block_key,
                    context_by_scope=context_by_scope,
                    alias_component_by_key=alias_component_by_key,
                    context_alias_by_key=context_alias_by_key,
                )
                if edge is None:
                    continue
                previous = edges.get(edge.edge_key)
                if previous is None or edge.weight > previous.weight or (edge.status == "active" and previous.status != "active"):
                    edges[edge.edge_key] = edge
    stats = {
        "blocking_block_count": len(blocks),
        "processed_block_count": processed_blocks,
        "oversized_block_count": len(oversized_blocks),
        "edge_count": len(edges),
        "edge_counts_by_status": dict(Counter(edge.status for edge in edges.values())),
        "edge_counts_by_type": dict(Counter(edge.edge_type for edge in edges.values())),
    }
    return tuple(edges.values()), oversized_blocks, stats


def llm_cache_fingerprint(
    *,
    prompt_version: str,
    model_label: str,
    block_payload: Mapping[str, Any],
) -> str:
    return value_hash(
        {
            "prompt_version": prompt_version,
            "model_label": model_label,
            "block_payload": block_payload,
        },
        32,
    )


def plan_llm_adjudication(
    edges: Sequence[SourceConceptEdgeDraft],
    *,
    signals: Sequence[SourceConceptSignalDraft],
    config: LLMAdjudicationConfig,
) -> LLMAdjudicationPlan:
    if not config.enabled:
        return LLMAdjudicationPlan(
            enabled=False,
            projected_calls=0,
            projected_input_tokens=0,
            projected_output_tokens=0,
            projected_cost_usd=0.0,
            max_calls=config.max_calls,
            max_budget_usd=config.max_budget_usd,
            selected_block_count=0,
            skipped_block_count=0,
            status="disabled",
            reason="llm_adjudication_not_requested",
        )
    weak_edges = [
        edge for edge in edges
        if edge.status in {"weak", "needs_review"} and edge.edge_type in {"cooccurrence_context", "alias_candidate_edge", "exact_canonical_key"}
    ]
    candidate_pairs = min(len(weak_edges), max(config.max_calls, 0))
    signal_lookup = {signal.signal_key: signal for signal in signals}
    estimated_chars = 0
    for edge in weak_edges[:candidate_pairs]:
        left = signal_lookup.get(edge.left_signal_key)
        right = signal_lookup.get(edge.right_signal_key)
        if left and right:
            estimated_chars += len(left.display_value) + len(right.display_value) + 300
    projected_input_tokens = max(0, estimated_chars // 4)
    projected_output_tokens = candidate_pairs * 80
    projected_cost_usd = round(((projected_input_tokens + projected_output_tokens) / 1000.0) * 0.002, 6)
    over_call_cap = candidate_pairs > config.max_calls
    over_budget = projected_cost_usd > config.max_budget_usd
    return LLMAdjudicationPlan(
        enabled=True,
        projected_calls=candidate_pairs,
        projected_input_tokens=projected_input_tokens,
        projected_output_tokens=projected_output_tokens,
        projected_cost_usd=projected_cost_usd,
        max_calls=config.max_calls,
        max_budget_usd=config.max_budget_usd,
        selected_block_count=candidate_pairs,
        skipped_block_count=max(0, len(weak_edges) - candidate_pairs),
        status="blocked" if over_call_cap or over_budget else "ready",
        reason="llm_budget_or_call_cap_exceeded" if over_call_cap or over_budget else None,
    )


def edges_from_llm_judgments(
    judgments: Sequence[Mapping[str, Any]],
    *,
    signal_by_key: Mapping[str, SourceConceptSignalDraft],
) -> list[SourceConceptEdgeDraft]:
    edges: list[SourceConceptEdgeDraft] = []
    for row in judgments:
        left = signal_by_key.get(str(row.get("left_signal_key") or ""))
        right = signal_by_key.get(str(row.get("right_signal_key") or ""))
        if left is None or right is None:
            continue
        decision = str(row.get("decision") or row.get("link_status") or "needs_review").lower()
        confidence = source_confidence_score(row.get("confidence"), 0.5) or 0.5
        if decision in {"must_link", "same_concept", "same"}:
            status = "active" if confidence >= 0.75 and not (signal_is_ai_origin(left) and signal_is_ai_origin(right)) else "needs_review"
            edges.append(
                _edge(
                    left,
                    right,
                    edge_type="llm_same_concept",
                    weight=confidence,
                    evidence_source="bounded_llm_adjudication",
                    status=status,
                    reason_code="llm_must_link_source_layer_evidence",
                    union_allowed=True,
                    payload={
                        "source_layer_only": True,
                        "llm_judgment_id": row.get("judgment_id"),
                        "decision": decision,
                    },
                )
            )
        elif decision in {"cannot_link", "different", "not_same"}:
            edges.append(
                _edge(
                    left,
                    right,
                    edge_type="llm_negative_guard",
                    weight=confidence,
                    evidence_source="bounded_llm_adjudication",
                    status="rejected",
                    reason_code="llm_cannot_link_source_layer_guard",
                    negative_reason_code="llm_cannot_link",
                    union_allowed=False,
                    payload={
                        "source_layer_only": True,
                        "llm_judgment_id": row.get("judgment_id"),
                        "decision": decision,
                    },
                )
            )
        else:
            edges.append(
                _edge(
                    left,
                    right,
                    edge_type="llm_needs_review",
                    weight=confidence,
                    evidence_source="bounded_llm_adjudication",
                    status="needs_review",
                    reason_code="llm_needs_review_source_layer_evidence",
                    union_allowed=False,
                    payload={
                        "source_layer_only": True,
                        "llm_judgment_id": row.get("judgment_id"),
                        "decision": decision,
                    },
                )
            )
    return edges


def _has_latin(value: str | None) -> bool:
    return bool(value and re.search(r"[A-Za-z]", value))


def _has_non_ascii(value: str | None) -> bool:
    return bool(value and any(ord(char) > 127 for char in value))


def _looks_like_latin_canonical_name(value: str | None) -> bool:
    return bool(value and _has_latin(value) and ("_" in value or len(value) > 8))


def _is_high_value_cross_script_edge(
    edge: SourceConceptEdgeDraft,
    *,
    signal_by_key: Mapping[str, SourceConceptSignalDraft],
) -> bool:
    if not (edge.evidence_source.startswith("record_context:") or edge.evidence_source.startswith("media_context:")):
        return False
    left = signal_by_key.get(edge.left_signal_key)
    right = signal_by_key.get(edge.right_signal_key)
    if left is None or right is None:
        return False
    if left.role_hint not in {"character", "person"} or right.role_hint not in {"character", "person"}:
        return False
    left_latin_canonical = _looks_like_latin_canonical_name(left.canonical_key or left.normalized_key)
    right_latin_canonical = _looks_like_latin_canonical_name(right.canonical_key or right.normalized_key)
    left_non_ascii = _has_non_ascii(left.display_value) or _has_non_ascii(left.canonical_key)
    right_non_ascii = _has_non_ascii(right.display_value) or _has_non_ascii(right.canonical_key)
    return bool((left_latin_canonical and right_non_ascii) or (right_latin_canonical and left_non_ascii))


def _llm_pair_score(
    edge: SourceConceptEdgeDraft,
    *,
    signal_by_key: Mapping[str, SourceConceptSignalDraft],
    context_by_scope: Mapping[tuple[str, int], set[str]],
    context_alias_by_key: Mapping[str, str] | None = None,
) -> float:
    left = signal_by_key.get(edge.left_signal_key)
    right = signal_by_key.get(edge.right_signal_key)
    if left is None or right is None:
        return -1.0
    if left.role_hint not in {"character", "person", "unknown"} or right.role_hint not in {"character", "person", "unknown"}:
        return -1.0
    if left.role_hint == "source_title" or right.role_hint == "source_title":
        return -1.0
    score = 0.0
    left_surface = signal_surface_key(left)
    right_surface = signal_surface_key(right)
    left_context, _left_reason = signal_context_key(left, context_by_scope, context_alias_by_key)
    right_context, _right_reason = signal_context_key(right, context_by_scope, context_alias_by_key)
    same_scope = (
        (left.media_id is not None and left.media_id == right.media_id)
        or (
            left.source_metadata_record_id is not None
            and left.source_metadata_record_id == right.source_metadata_record_id
        )
    )
    left_parenthetical = bool(left.parenthetical_base and left.parenthetical_context)
    right_parenthetical = bool(right.parenthetical_base and right.parenthetical_context)
    both_characterish = left.role_hint in {"character", "person"} and right.role_hint in {"character", "person"}
    if left_context or right_context:
        score += 2.0
    if left_context and right_context and left_context == right_context:
        score += 2.5
    if same_scope:
        score += 1.5
    if left_parenthetical != right_parenthetical and left_context and right_context and left_context == right_context:
        score += 4.0
    if both_characterish:
        score += 1.5
    if "unknown" in {left.role_hint, right.role_hint}:
        score -= 2.0
    if same_scope and both_characterish:
        left_latin_canonical = _looks_like_latin_canonical_name(left.canonical_key or left.normalized_key)
        right_latin_canonical = _looks_like_latin_canonical_name(right.canonical_key or right.normalized_key)
        left_non_ascii = _has_non_ascii(left.display_value) or _has_non_ascii(left.canonical_key)
        right_non_ascii = _has_non_ascii(right.display_value) or _has_non_ascii(right.canonical_key)
        if (left_latin_canonical and right_non_ascii) or (right_latin_canonical and left_non_ascii):
            score += 8.0
    if bool(_has_latin(left.display_value)) != bool(_has_latin(right.display_value)):
        score += 1.0
    if bool(_has_non_ascii(left.display_value)) != bool(_has_non_ascii(right.display_value)):
        score += 2.0
    if {left.origin_type, right.origin_type} & {"normal_media_tag", "ai_model_tag"} and {
        left.origin_type,
        right.origin_type,
    } & {"f7a_candidate", "source_assertion", "source_name_observation", "source_tag_observation", "provider_structured_field"}:
        score += 2.0
    if not signal_is_ai_origin(left) and not signal_is_ai_origin(right):
        score += 3.0
    elif signal_is_ai_origin(left) and signal_is_ai_origin(right):
        score -= 3.0
    if edge.edge_type == "cooccurrence_context":
        score += 1.0
    if left_surface == right_surface:
        score -= 1.0
    if signal_is_ai_origin(left) and signal_is_ai_origin(right):
        score -= 2.0
    return score


def select_llm_adjudication_edges(
    edges: Sequence[SourceConceptEdgeDraft],
    *,
    signals: Sequence[SourceConceptSignalDraft],
    config: LLMAdjudicationConfig,
) -> list[SourceConceptEdgeDraft]:
    if not config.enabled or config.max_calls <= 0:
        return []
    signal_by_key = {signal.signal_key: signal for signal in signals}
    context_alias_by_key = _context_equivalence_lookup(signals)
    context_by_scope = _context_candidates_by_scope(signals, context_alias_by_key=context_alias_by_key)
    scored: list[tuple[float, SourceConceptEdgeDraft]] = []
    for edge in edges:
        if edge.status not in {"weak", "needs_review"}:
            continue
        if edge.edge_type not in {"cooccurrence_context", "alias_candidate_edge", "same_surface_context", "exact_canonical_key"}:
            continue
        score = _llm_pair_score(
            edge,
            signal_by_key=signal_by_key,
            context_by_scope=context_by_scope,
            context_alias_by_key=context_alias_by_key,
        )
        if score <= 0:
            continue
        scored.append((score, edge))
    scored.sort(key=lambda item: (item[0], item[1].weight), reverse=True)
    selected: list[SourceConceptEdgeDraft] = []
    seen_pairs: set[tuple[str, str]] = set()
    signal_pair_counts: Counter[str] = Counter()

    def add_edge(edge: SourceConceptEdgeDraft, *, per_signal_cap: int) -> bool:
        pair = _pair_id(edge)
        if pair in seen_pairs:
            return False
        if signal_pair_counts[pair[0]] >= per_signal_cap or signal_pair_counts[pair[1]] >= per_signal_cap:
            return False
        seen_pairs.add(pair)
        signal_pair_counts[pair[0]] += 1
        signal_pair_counts[pair[1]] += 1
        selected.append(edge)
        return True

    coverage_by_scope: dict[str, tuple[float, SourceConceptEdgeDraft]] = {}
    for score, edge in scored:
        if score < 9.0 or not _is_high_value_cross_script_edge(edge, signal_by_key=signal_by_key):
            continue
        previous = coverage_by_scope.get(edge.evidence_source)
        if previous is None or score > previous[0] or (score == previous[0] and edge.weight > previous[1].weight):
            coverage_by_scope[edge.evidence_source] = (score, edge)
    coverage_items = sorted(
        coverage_by_scope.items(),
        key=lambda item: (
            item[0].startswith("record_context:"),
            item[1][0],
            item[1][1].weight,
        ),
        reverse=True,
    )
    for _scope, (_score, edge) in coverage_items:
        add_edge(edge, per_signal_cap=6)
        if len(selected) >= config.max_calls:
            return selected

    for _score, edge in scored:
        add_edge(edge, per_signal_cap=4)
        if len(selected) >= config.max_calls:
            break
    return selected


def _llm_signal_payload(
    signal: SourceConceptSignalDraft,
    *,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    context_alias_by_key: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    context, context_reason = signal_context_key(signal, context_by_scope, context_alias_by_key)
    return {
        "signal_key": signal.signal_key,
        "origin_type": signal.origin_type,
        "provider": signal.provider,
        "display_value": signal.display_value,
        "canonical_key": signal.canonical_key,
        "surface_key": signal_surface_key(signal),
        "role_hint": signal.role_hint,
        "work_context_key": context,
        "context_reason": context_reason,
        "trust_tier": signal.trust_tier,
        "status": signal.status,
        "source_kind": signal.source_kind,
    }


def _run_async_json(provider: Any, messages: list[dict[str, str]], *, max_tokens: int = 600) -> Any:
    return asyncio.run(provider.complete_json(messages, temperature=0.0, max_tokens=max_tokens))


def run_bounded_llm_adjudication(
    edges: Sequence[SourceConceptEdgeDraft],
    *,
    signals: Sequence[SourceConceptSignalDraft],
    config: LLMAdjudicationConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plan = plan_llm_adjudication(edges, signals=signals, config=config)
    if not config.enabled:
        return [], {"used": False, "plan": asdict(plan), "reason": "disabled"}
    selected_edges = select_llm_adjudication_edges(edges, signals=signals, config=config)
    if not selected_edges:
        return [], {"used": False, "plan": asdict(plan), "reason": "no_eligible_pairs"}
    provider, provider_summary = primary_openai_provider_from_settings()
    if provider is None:
        if config.fail_if_unavailable:
            raise RuntimeError(f"llm_provider_unavailable:{provider_summary.get('unavailable_reason')}")
        return [], {"used": False, "plan": asdict(plan), "provider": provider_summary, "reason": "provider_unavailable"}

    cache_dir = Path(config.cache_dir) if config.cache_dir else Path(".local_manifests") / "phase-4.5-sc1-llm-adjudication-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    signal_by_key = {signal.signal_key: signal for signal in signals}
    context_alias_by_key = _context_equivalence_lookup(signals)
    context_by_scope = _context_candidates_by_scope(signals, context_alias_by_key=context_alias_by_key)
    judgments: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for edge in selected_edges:
        left = signal_by_key.get(edge.left_signal_key)
        right = signal_by_key.get(edge.right_signal_key)
        if left is None or right is None:
            continue
        block_payload = {
            "edge": asdict(edge),
            "left": _llm_signal_payload(left, context_by_scope=context_by_scope, context_alias_by_key=context_alias_by_key),
            "right": _llm_signal_payload(right, context_by_scope=context_by_scope, context_alias_by_key=context_alias_by_key),
        }
        fingerprint = llm_cache_fingerprint(
            prompt_version=config.prompt_version,
            model_label=config.model_label,
            block_payload=block_payload,
        )
        cache_path = cache_dir / f"{fingerprint}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            judgments.append(cached)
            continue
        messages = [
            {
                "role": "system",
                "content": (
                    "You are adjudicating unconfirmed source-layer name signals. "
                    "Return JSON only with keys decision, confidence, and optional reason_code. "
                    "decision must be must_link, cannot_link, or needs_review. "
                    "Do not create Entity truth. Do not include chain-of-thought."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Decide whether the two source-layer signals refer to the same character/person/work concept.",
                        "allowed_decisions": ["must_link", "cannot_link", "needs_review"],
                        "signals": [block_payload["left"], block_payload["right"]],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ]
        try:
            response = _run_async_json(provider, messages)
            if isinstance(response, list):
                response = response[0] if response else {}
            if not isinstance(response, Mapping):
                response = {}
            decision = str(response.get("decision") or "needs_review").lower()
            if decision not in {"must_link", "cannot_link", "needs_review"}:
                decision = "needs_review"
            judgment = {
                "judgment_id": fingerprint,
                "left_signal_key": left.signal_key,
                "right_signal_key": right.signal_key,
                "decision": decision,
                "confidence": source_confidence_score(response.get("confidence"), 0.5),
                "reason_code": response.get("reason_code"),
                "source_layer_only": True,
                "provider_label": provider_summary.get("llm_provider_label"),
                "model_label": config.model_label,
                "cache_fingerprint": fingerprint,
            }
            cache_path.write_text(json.dumps(judgment, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            judgments.append(judgment)
        except Exception as exc:  # pragma: no cover - provider failures are environment dependent
            error = {
                "judgment_id": fingerprint,
                "left_signal_key": left.signal_key,
                "right_signal_key": right.signal_key,
                "decision": "needs_review",
                "confidence": 0.0,
                "source_layer_only": True,
                "error_type": type(exc).__name__,
            }
            errors.append(error)
            judgments.append(error)
            if config.fail_if_unavailable:
                raise
    return judgments, {
        "used": bool(judgments),
        "plan": asdict(plan),
        "provider": provider_summary,
        "selected_pair_count": len(selected_edges),
        "judgment_count": len(judgments),
        "error_count": len(errors),
        "cache_dir": str(cache_dir),
    }


def _infer_unique_context(
    signal: SourceConceptSignalDraft,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    context_alias_by_key: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None]:
    if signal.work_context_key:
        return _context_alias_key(signal.work_context_key, context_alias_by_key), "explicit_work_context"
    contexts: set[str] = set()
    if signal.source_metadata_record_id is not None:
        contexts.update(context_by_scope.get(("record", signal.source_metadata_record_id), set()))
    if signal.media_id is not None:
        contexts.update(context_by_scope.get(("media", signal.media_id), set()))
    contexts = {_context_alias_key(context, context_alias_by_key) or context for context in contexts}
    contexts.discard(_context_alias_key(signal.canonical_key, context_alias_by_key) or "")
    if len(contexts) == 1:
        return next(iter(contexts)), "unique_source_or_media_work_context"
    return None, None


def _signal_guard_reason(
    signal: SourceConceptSignalDraft,
    *,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    context_alias_by_key: Mapping[str, str] | None = None,
) -> str | None:
    if signal.status == "rejected" or signal.trust_tier == "rejected":
        return "rejected_signal"
    if signal.origin_type == "source_alias_candidate":
        return None
    if signal.role_hint == "source_title":
        return "source_title_only_guard"
    if signal.role_hint == "unknown":
        return "unknown_role_guard"
    surface = signal_surface_key(signal)
    context, _context_reason = signal_context_key(signal, context_by_scope, context_alias_by_key)
    if signal.role_hint in {"character", "person"} and is_short_ambiguous_key(surface) and not context:
        return "ambiguous_short_without_work_context"
    return None


def _component_role(signals: Sequence[SourceConceptSignalDraft]) -> str:
    role_counts = Counter(signal.role_hint for signal in signals if signal.role_hint != "unknown")
    if not role_counts:
        return "unknown"
    return role_counts.most_common(1)[0][0]


def _component_surface_and_context(
    signals: Sequence[SourceConceptSignalDraft],
    *,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    context_alias_by_key: Mapping[str, str] | None = None,
) -> tuple[str, str | None]:
    ranked = sorted(
        signals,
        key=lambda signal: (
            TRUST_WEIGHTS.get(signal.trust_tier, 0.0),
            0 if signal_is_ai_origin(signal) else 1,
            source_confidence_score(signal.confidence, 0.0) or 0.0,
            -len(signal.display_value),
        ),
        reverse=True,
    )
    for signal in ranked:
        surface = signal_surface_key(signal)
        if surface:
            context, _context_reason = signal_context_key(signal, context_by_scope, context_alias_by_key)
            return surface, context
    fallback = ranked[0].signal_key if ranked else "empty"
    return value_hash(fallback, 12), None


def _component_concept_key(
    signals: Sequence[SourceConceptSignalDraft],
    *,
    context_by_scope: Mapping[tuple[str, int], set[str]],
    context_alias_by_key: Mapping[str, str] | None = None,
) -> str:
    role = _component_role(signals)
    surface, context = _component_surface_and_context(
        signals,
        context_by_scope=context_by_scope,
        context_alias_by_key=context_alias_by_key,
    )
    component_hash = value_hash(sorted(signal.signal_key for signal in signals), 12)
    guards = {
        _signal_guard_reason(
            signal,
            context_by_scope=context_by_scope,
            context_alias_by_key=context_alias_by_key,
        )
        for signal in signals
    }
    guards.discard(None)
    if role in {"character", "person"} and context:
        return f"{role}:{surface}:work:{context}:component:{component_hash}"
    if role in {"character", "person"} and "ambiguous_short_without_work_context" in guards:
        if len(signals) == 1:
            return f"{role}:ambiguous:{surface}:signal:{component_hash}"
        return f"{role}:ambiguous:{surface}:component:{component_hash}"
    if role == "source_title":
        return f"source_title:{surface}:signal:{component_hash}"
    if role == "unknown":
        return f"unknown:{surface}:signal:{component_hash}"
    return f"{role}:{surface}:component:{component_hash}"


def _pair_id(edge: SourceConceptEdgeDraft) -> tuple[str, str]:
    return tuple(sorted((edge.left_signal_key, edge.right_signal_key)))


def resolve_source_concepts(
    signals: Sequence[SourceConceptSignalDraft],
    *,
    run_id: str,
    llm_config: LLMAdjudicationConfig | None = None,
    llm_judgments: Sequence[Mapping[str, Any]] | None = None,
) -> SourceConceptResolutionResult:
    context_alias_by_key = _context_equivalence_lookup(signals)
    context_by_scope = _context_candidates_by_scope(signals, context_alias_by_key=context_alias_by_key)
    alias_component_by_key = _alias_component_lookup(signals)
    rejected: list[dict[str, Any]] = []
    eligible_signals: list[SourceConceptSignalDraft] = []
    for signal in signals:
        if signal.status == "rejected" or signal.trust_tier == "rejected":
            rejected.append(
                {
                    "signal_key": signal.signal_key,
                    "origin_type": signal.origin_type,
                    "role_hint": signal.role_hint,
                    "negative_reason_code": (signal.evidence_payload or {}).get("non_concept_reason") or "rejected_signal",
                    "trust_tier": signal.trust_tier,
                    "status": signal.status,
                }
            )
            continue
        eligible_signals.append(signal)

    edge_candidates, oversized_blocks, edge_stats = _generate_edges(
        eligible_signals,
        context_by_scope=context_by_scope,
        alias_component_by_key=alias_component_by_key,
        context_alias_by_key=context_alias_by_key,
    )

    llm_plan = plan_llm_adjudication(
        edge_candidates,
        signals=eligible_signals,
        config=llm_config or LLMAdjudicationConfig(),
    )
    llm_edges = tuple(edges_from_llm_judgments(llm_judgments or (), signal_by_key={signal.signal_key: signal for signal in eligible_signals}))
    if llm_edges:
        edge_candidates = tuple({edge.edge_key: edge for edge in (*edge_candidates, *llm_edges)}.values())

    uf = UnionFind()
    uf_members: dict[str, set[str]] = {}
    for signal in eligible_signals:
        uf.add(signal.signal_key)
        uf_members[signal.signal_key] = {signal.signal_key}
    blocked_pairs = {
        _pair_id(edge)
        for edge in edge_candidates
        if edge.status == "rejected" or edge.negative_reason_code
    }
    union_edges = sorted(
        (edge for edge in edge_candidates if edge.union_allowed and edge.status == "active"),
        key=lambda edge: (edge.edge_type.startswith("llm_"), -edge.weight),
    )
    for edge in union_edges:
        if not edge.union_allowed:
            continue
        if _pair_id(edge) in blocked_pairs:
            continue
        left_root = uf.find(edge.left_signal_key)
        right_root = uf.find(edge.right_signal_key)
        if left_root == right_root:
            continue
        merged_members = set(uf_members.get(left_root, {left_root})) | set(uf_members.get(right_root, {right_root}))
        if any(left in merged_members and right in merged_members for left, right in blocked_pairs):
            continue
        uf.union(edge.left_signal_key, edge.right_signal_key)
        merged_root = uf.find(edge.left_signal_key)
        uf_members[merged_root] = merged_members
        for old_root in (left_root, right_root):
            if old_root != merged_root:
                uf_members.pop(old_root, None)

    signal_by_key = {signal.signal_key: signal for signal in eligible_signals}
    component_signals: dict[str, list[SourceConceptSignalDraft]] = defaultdict(list)
    for signal in eligible_signals:
        component_signals[uf.find(signal.signal_key)].append(signal)
    component_edges: dict[str, list[SourceConceptEdgeDraft]] = defaultdict(list)
    for edge in edge_candidates:
        if edge.left_signal_key not in signal_by_key or edge.right_signal_key not in signal_by_key:
            continue
        left_root = uf.find(edge.left_signal_key)
        right_root = uf.find(edge.right_signal_key)
        if left_root == right_root:
            component_edges[left_root].append(edge)

    concept_items: list[tuple[str, tuple[SourceConceptSignalDraft, ...], tuple[SourceConceptEdgeDraft, ...]]] = []
    for root, grouped_signals in component_signals.items():
        ordered_signals = tuple(sorted(grouped_signals, key=lambda signal: signal.signal_key))
        concept_key = _component_concept_key(
            ordered_signals,
            context_by_scope=context_by_scope,
            context_alias_by_key=context_alias_by_key,
        )
        concept_items.append((concept_key, ordered_signals, tuple(component_edges.get(root, []))))

    concepts: list[SourceConceptDraft] = []
    aliases: list[SourceConceptAliasDraft] = []
    evidence: list[SourceConceptEvidenceDraft] = []
    links: list[SourceConceptLinkDraft] = []
    search_index: list[SourceConceptSearchIndexDraft] = []
    ambiguous: list[dict[str, Any]] = []

    edge_lookup_by_signal: dict[str, list[SourceConceptEdgeDraft]] = defaultdict(list)
    for edge in edge_candidates:
        edge_lookup_by_signal[edge.left_signal_key].append(edge)
        edge_lookup_by_signal[edge.right_signal_key].append(edge)

    for concept_key, bucket_signals, bucket_edges in concept_items:
        concept = _build_concept_draft(
            concept_key,
            bucket_signals,
            run_id=run_id,
            component_edges=bucket_edges,
            context_by_scope=context_by_scope,
            context_alias_by_key=context_alias_by_key,
        )
        concepts.append(concept)
        for signal in bucket_signals:
            signal_guard = _signal_guard_reason(
                signal,
                context_by_scope=context_by_scope,
                context_alias_by_key=context_alias_by_key,
            )
            if signal_guard:
                ambiguous.append(
                    {
                        "signal_key": signal.signal_key,
                        "concept_key": concept_key,
                        "negative_reason_code": signal_guard,
                        "origin_type": signal.origin_type,
                        "role_hint": signal.role_hint,
                    }
                )
            signal_edges = edge_lookup_by_signal.get(signal.signal_key, [])
            best_edge = sorted(
                signal_edges,
                key=lambda edge: (
                    edge.status == "active",
                    edge.weight,
                    edge.status == "needs_review",
                ),
                reverse=True,
            )[0] if signal_edges else None
            reason_code = best_edge.resolution_reason_code if best_edge else "single_signal_component"
            negative_reason = signal_guard or (best_edge.negative_reason_code if best_edge else None)
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
                        "component_edge_count": len(bucket_edges),
                        "best_edge_key": best_edge.edge_key if best_edge else None,
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
    ai_signal_review = tuple(_ai_signal_review(concepts))
    overmerge_review = tuple(_overmerge_review(concepts, edge_candidates))
    undermerge_review = tuple(_undermerge_review(edge_candidates, concepts))
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
        "edge_graph": edge_stats,
        "blocking_oversized_blocks": len(oversized_blocks),
        "context_alias_count": len(context_alias_by_key),
        "ai_only_active_violation_count": sum(1 for row in ai_signal_review if row.get("violation")),
        "general_source_tag_pollution_count": sum(
            1
            for concept in concepts
            for signal in concept.signals
            if (signal.evidence_payload or {}).get("non_concept_reason") == "general_source_tag_without_name_context"
        ),
        "source_title_only_active_violation_count": sum(
            1
            for concept in concepts
            if concept.status == "active" and all(signal.role_hint == "source_title" for signal in concept.signals)
        ),
        "llm_usage": {
            "used": bool(llm_judgments),
            "policy": "bounded_optional_primary_openai_only_after_deterministic_blocking",
            "plan": asdict(llm_plan),
            "judgment_count": len(llm_judgments or ()),
        },
    }
    return SourceConceptResolutionResult(
        run_id=run_id,
        signals=tuple(signals),
        edge_candidates=edge_candidates,
        concepts=tuple(concepts),
        aliases=deduped_aliases,
        evidence=tuple(evidence),
        links=tuple(links),
        search_index=deduped_search_index,
        rejected_signals=tuple([*rejected, *oversized_blocks]),
        ambiguous_links=tuple(ambiguous),
        merge_candidates=tuple(_merge_candidate_review(concepts)),
        overmerge_review=overmerge_review,
        undermerge_review=undermerge_review,
        ai_signal_review=ai_signal_review,
        llm_judgments=tuple(dict(row) for row in (llm_judgments or ())),
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
    component_edges: Sequence[SourceConceptEdgeDraft] = (),
    context_by_scope: Mapping[tuple[str, int], set[str]] | None = None,
    context_alias_by_key: Mapping[str, str] | None = None,
) -> SourceConceptDraft:
    trust_counts = Counter(signal.trust_tier for signal in signals)
    origin_counts = Counter(signal.origin_type for signal in signals)
    role_counts = Counter(signal.role_hint for signal in signals)
    providers = {signal.provider for signal in signals if signal.provider}
    medias = {signal.media_id for signal in signals if signal.media_id is not None}
    records = {signal.source_metadata_record_id for signal in signals if signal.source_metadata_record_id is not None}
    context_lookup = context_by_scope or {}
    non_ai_signals = [signal for signal in signals if not signal_is_ai_origin(signal)]
    non_alias_signals = [signal for signal in signals if signal.origin_type != "source_alias_candidate"]
    non_ai_non_alias_signals = [signal for signal in non_ai_signals if signal.origin_type != "source_alias_candidate"]
    all_ai = bool(non_alias_signals) and all(signal_is_ai_origin(signal) for signal in non_alias_signals)
    all_alias_candidate = bool(signals) and not non_alias_signals
    all_weak_or_title = all(signal.trust_tier in {"weak"} or signal.role_hint == "source_title" for signal in signals)
    has_strong_non_ai = any(signal.trust_tier == "strong" and not signal_is_ai_origin(signal) for signal in signals)
    has_medium_non_ai = any(signal.trust_tier == "medium" and not signal_is_ai_origin(signal) for signal in signals)
    has_medium_ai = any(signal.trust_tier == "medium_ai" or signal_is_ai_origin(signal) for signal in signals)
    signal_guards = {
        _signal_guard_reason(
            signal,
            context_by_scope=context_lookup,
            context_alias_by_key=context_alias_by_key,
        )
        for signal in signals
    }
    signal_guards.discard(None)
    has_guard = bool(signal_guards) or "ambiguous:" in concept_key or concept_key.startswith("source_title:") or concept_key.startswith("unknown:")
    role_conflict = len({role for role in role_counts if role != "unknown"}) > 1
    active_edge_count = sum(1 for edge in component_edges if edge.status == "active" and edge.union_allowed)
    medium_needs_review_without_corroboration = has_medium_non_ai and not has_strong_non_ai and not active_edge_count and len(non_ai_non_alias_signals) < 2

    if role_conflict or has_guard or all_ai or all_alias_candidate or all_weak_or_title:
        status = "needs_review"
    elif has_strong_non_ai:
        status = "active"
    elif has_medium_non_ai and not medium_needs_review_without_corroboration:
        status = "active"
    elif has_medium_ai and non_ai_non_alias_signals and active_edge_count:
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
    max_tier = max(trust_counts.keys(), key=lambda tier: TRUST_WEIGHTS.get(tier, 0.0), default="weak")
    surface, context = _component_surface_and_context(
        signals,
        context_by_scope=context_lookup,
        context_alias_by_key=context_alias_by_key,
    )
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
            "signal_guards": sorted(signal_guards),
            "medium_ai_present": has_medium_ai,
            "medium_needs_review_without_corroboration": medium_needs_review_without_corroboration,
        },
        "surface_key": surface,
        "work_context_key": context,
        "component_edge_counts": dict(Counter(edge.status for edge in component_edges)),
        "component_edge_type_counts": dict(Counter(edge.edge_type for edge in component_edges)),
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
        surface_key = (concept.evidence_summary or {}).get("surface_key")
        if surface_key:
            by_alias[str(surface_key)].append(concept)
    review: list[dict[str, Any]] = []
    for alias_key, grouped in by_alias.items():
        if len(grouped) < 2:
            continue
        review.append(
            {
                "surface_key": alias_key,
                "concept_keys": [concept.concept_key for concept in grouped],
                "reason": "same_surface_key_multiple_contexts_review_only",
            }
        )
    return review


def _ai_signal_review(concepts: Sequence[SourceConceptDraft]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for concept in concepts:
        non_alias = [signal for signal in concept.signals if signal.origin_type != "source_alias_candidate"]
        if not non_alias:
            continue
        ai_count = sum(1 for signal in non_alias if signal_is_ai_origin(signal))
        if not ai_count:
            continue
        non_ai_count = len(non_alias) - ai_count
        violation = concept.status == "active" and non_ai_count == 0
        rows.append(
            {
                "concept_key": concept.concept_key,
                "concept_status": concept.status,
                "ai_signal_count": ai_count,
                "non_ai_signal_count": non_ai_count,
                "violation": violation,
                "reason": "ai_only_concept_must_not_be_active" if violation else "ai_signal_review",
            }
        )
    return rows


def _overmerge_review(
    concepts: Sequence[SourceConceptDraft],
    edges: Sequence[SourceConceptEdgeDraft],
) -> list[dict[str, Any]]:
    negative_pairs = {_pair_id(edge): edge for edge in edges if edge.negative_reason_code}
    concept_by_signal: dict[str, SourceConceptDraft] = {}
    for concept in concepts:
        for signal in concept.signals:
            concept_by_signal[signal.signal_key] = concept
    rows: list[dict[str, Any]] = []
    for pair, edge in negative_pairs.items():
        left_concept = concept_by_signal.get(pair[0])
        right_concept = concept_by_signal.get(pair[1])
        if left_concept and right_concept and left_concept.concept_key == right_concept.concept_key:
            rows.append(
                {
                    "concept_key": left_concept.concept_key,
                    "left_signal_key": pair[0],
                    "right_signal_key": pair[1],
                    "negative_reason_code": edge.negative_reason_code,
                    "violation": True,
                }
            )
    return rows


def _undermerge_review(
    edges: Sequence[SourceConceptEdgeDraft],
    concepts: Sequence[SourceConceptDraft],
) -> list[dict[str, Any]]:
    concept_by_signal: dict[str, str] = {}
    for concept in concepts:
        for signal in concept.signals:
            concept_by_signal[signal.signal_key] = concept.concept_key
    rows: list[dict[str, Any]] = []
    for edge in edges:
        if edge.status != "active" or not edge.union_allowed:
            continue
        left_concept = concept_by_signal.get(edge.left_signal_key)
        right_concept = concept_by_signal.get(edge.right_signal_key)
        if left_concept and right_concept and left_concept != right_concept:
            rows.append(
                {
                    "left_signal_key": edge.left_signal_key,
                    "right_signal_key": edge.right_signal_key,
                    "left_concept_key": left_concept,
                    "right_concept_key": right_concept,
                    "edge_key": edge.edge_key,
                    "edge_type": edge.edge_type,
                    "violation": True,
                    "reason": "active_safe_edge_not_materialized_in_same_component",
                }
            )
    return rows


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
    llm_config: LLMAdjudicationConfig | None = None,
    llm_judgments: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[SourceConceptResolutionResult, dict[str, Any], dict[str, Any]]:
    inventory = source_signal_inventory(db, f7a_run_id=f7a_run_id)
    signals = build_source_concept_signals(db, run_id=run_id, f7a_run_id=f7a_run_id)
    effective_llm_config = llm_config or LLMAdjudicationConfig()
    llm_summary: dict[str, Any] | None = None
    if effective_llm_config.enabled and llm_judgments is None:
        initial_result = resolve_source_concepts(signals, run_id=run_id, llm_config=effective_llm_config, llm_judgments=())
        generated_judgments, llm_summary = run_bounded_llm_adjudication(
            initial_result.edge_candidates,
            signals=signals,
            config=effective_llm_config,
        )
        llm_judgments = generated_judgments
    result = resolve_source_concepts(signals, run_id=run_id, llm_config=effective_llm_config, llm_judgments=llm_judgments)
    if llm_summary is not None:
        result.summary["llm_usage"] = {**result.summary.get("llm_usage", {}), **llm_summary}
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


def _pack_candidate_stable_key(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row.get("evidence_payload") or {})
    return {
        "logical_candidate_key": payload.get("logical_candidate_key") or row.get("candidate_key"),
        "group_key": row.get("group_key"),
        "provider": row.get("provider"),
        "origin_type": row.get("origin_type"),
        "origin_id": row.get("origin_id"),
        "canonical_key": row.get("canonical_key"),
        "candidate_role": row.get("candidate_role"),
        "candidate_status": row.get("candidate_status"),
        "source_metadata_record_id": row.get("source_metadata_record_id"),
        "media_id": row.get("media_id"),
    }


def _candidate_rows_checksum(rows: Sequence[Mapping[str, Any]]) -> str:
    stable_rows = sorted((_pack_candidate_stable_key(row) for row in rows), key=lambda row: json.dumps(row, sort_keys=True, default=str))
    return value_hash(stable_rows, 32)


def _db_candidates_checksum(db: Session, run_id: str) -> tuple[int, str]:
    rows = (
        db.query(SourceNameCandidate)
        .join(
            SourceNameCandidateExtractionRun,
            SourceNameCandidate.extraction_run_id == SourceNameCandidateExtractionRun.id,
        )
        .filter(SourceNameCandidateExtractionRun.run_id == run_id)
        .filter(SourceNameCandidate.status == "active")
        .all()
    )
    stable_rows = []
    for row in rows:
        payload = dict(row.evidence_payload or {})
        stable_rows.append(
            {
                "logical_candidate_key": payload.get("logical_candidate_key") or row.candidate_key,
                "group_key": row.group_key,
                "provider": row.provider,
                "origin_type": row.origin_type,
                "origin_id": row.origin_id,
                "canonical_key": row.canonical_key,
                "candidate_role": row.candidate_role,
                "candidate_status": row.candidate_status,
                "source_metadata_record_id": row.source_metadata_record_id,
                "media_id": row.media_id,
            }
        )
    return len(rows), _candidate_rows_checksum(stable_rows)


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
    existing_count, existing_checksum = _db_candidates_checksum(db, run_id)
    bundle_checksum = _candidate_rows_checksum(rows)
    audit = {
        "run_id": run_id,
        "pack_dir": str(pack_dir),
        "candidate_bundle_count": len(rows),
        "existing_db_candidate_count_for_run": existing_count,
        "candidate_bundle_stable_checksum": bundle_checksum,
        "existing_db_stable_checksum_for_run": existing_checksum,
        "stable_checksum_matches": existing_checksum == bundle_checksum,
        "needs_import": existing_count != len(rows) or existing_checksum != bundle_checksum,
        "apply": apply,
    }
    if not audit["needs_import"]:
        return {**audit, "persistence": {"skipped": True, "reason": "db_count_and_stable_checksum_match_candidate_bundle"}}

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
        "edge_candidates": [asdict(item) for item in result.edge_candidates],
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
        "overmerge_review": list(result.overmerge_review),
        "undermerge_review": list(result.undermerge_review),
        "ai_signal_review": list(result.ai_signal_review),
        "llm_judgments": list(result.llm_judgments),
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
