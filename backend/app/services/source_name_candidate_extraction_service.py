"""F7a source-layer name candidate extraction.

This module builds an unconfirmed source-layer candidate pool. It must not
create SourceConcept rows, Entity rows, Entity aliases, media entity candidates,
confirmed assignments, LocalSourceHint-style rows, TagTranslation rows, or
media_tags rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from ..enums import ContentClassEnum, TagCategoryEnum
from ..models import (
    Media,
    SourceMetadataRecord,
    SourceNameAliasCandidate,
    SourceNameCandidate,
    SourceNameCandidateExtractionRun,
    SourceNameCandidateRecordVerdict,
    SourceNameObservation,
    SourceSearchableNameAssertion,
    SourceTagObservation,
    Tag,
    blombooru_media_tags,
)
from .llm_translation_provider import (
    BaseLLMProvider,
    FallbackProvider,
    LLMAllProvidersFailed,
    LLMBatchAggregateError,
    LLMHTTPStatusError,
    LLMProviderError,
    LLMResponseFormatError,
    LLMTransportError,
    OpenAICompatibleProvider,
)
from .source_metadata_registry_service import (
    canonical_source_key,
    language_and_script_hint,
    normalize_source_text,
    parse_parenthetical_name,
)

PHASE = "4.4-P2R-F7a"
EXTRACTOR_VERSION = "phase44p2r_f7a_source_name_candidate_extractor_v2"
PROMPT_VERSION = "phase44p2r_f7a_llm_source_name_candidate_extraction_compact_v2"
SCHEMA_VERSION = "source_name_candidate_extraction_compact_v2"

ALLOWED_VERDICTS = frozenset(
    {
        "name_candidate_found",
        "work_candidate_found",
        "artist_candidate_found",
        "multiple_candidates_found",
        "ambiguous_needs_review",
        "original_without_explicit_name",
        "no_explicit_name",
        "metadata_insufficient",
        "provider_not_applicable",
        "rejected_general_only",
        "rejected_popularity_or_meta_only",
        "extraction_error",
        "extraction_error_retryable",
        "extraction_error_terminal",
    }
)
ALLOWED_CANDIDATE_ROLES = frozenset(
    {
        "character",
        "person",
        "work_title",
        "artist_creator",
        "alias_like",
        "source_title",
        "unknown_name_like",
    }
)
ALLOWED_CANDIDATE_STATUSES = frozenset({"active_candidate", "needs_review", "rejected"})
ALLOWED_EXTRACTION_ACTIONS = frozenset(
    {
        "direct_name",
        "parenthetical_split",
        "popularity_suffix_stripped",
        "provider_structured_field",
        "normal_tag_candidate",
        "ai_model_character_tag",
        "context_inferred",
        "llm_inferred_from_context",
    }
)
ALLOWED_REJECTION_REASONS = frozenset(
    {
        "descriptive_general",
        "popularity_meta",
        "explicit_r18_meta",
        "empty_or_invalid",
        "not_name_like",
        "url_or_path",
        "duplicate",
    }
)
ALLOWED_ORIGINS = frozenset(
    {
        "pixiv_tag",
        "pixiv_title",
        "pixiv_caption",
        "pixiv_artist",
        "source_assertion",
        "source_name_observation",
        "source_tag_observation",
        "normal_tag",
        "ai_model_tag",
        "saucenao_field",
        "booru_tag",
        "provider_field",
        "deterministic_preprocessing",
    }
)
VERDICT_SYNONYMS = {
    "has_names": "multiple_candidates_found",
    "candidates_found": "multiple_candidates_found",
    "candidate_found": "name_candidate_found",
    "names_found": "multiple_candidates_found",
    "name_found": "name_candidate_found",
    "work_found": "work_candidate_found",
    "artist_found": "artist_candidate_found",
    "error": "extraction_error",
    "failed": "extraction_error",
    "no_names": "no_explicit_name",
    "no_name": "no_explicit_name",
}
ORIGIN_SYNONYMS = {
    "pixiv_user_metadata": "pixiv_artist",
    "pixiv_user": "pixiv_artist",
    "artist_metadata": "pixiv_artist",
    "source_tag": "source_tag_observation",
    "source_name": "source_name_observation",
    "source_searchable_assertion": "source_assertion",
    "ai_wd": "ai_model_tag",
    "wd_tagger": "ai_model_tag",
    "media_tag": "normal_tag",
    "tag": "source_tag_observation",
    "title": "pixiv_title",
}
ACTION_SYNONYMS = {
    "llm_inferred_from_context": "context_inferred",
    "inferred_from_context": "context_inferred",
    "structured_field": "provider_structured_field",
}
LLM_CANDIDATE_FOUND_VERDICTS = frozenset(
    {
        "name_candidate_found",
        "work_candidate_found",
        "artist_candidate_found",
        "multiple_candidates_found",
    }
)
COMPACT_REJECTED_SUMMARY_KEYS = (
    "descriptive_general_count",
    "popularity_meta_count",
    "explicit_r18_meta_count",
    "invalid_or_empty_count",
    "duplicate_count",
    "other_rejected_count",
)
APPROVED_LLM_CONTENT_CLASSES = frozenset({ContentClassEnum.anime.value})

POPULARITY_SUFFIX_RE = re.compile(
    r"^(?P<prefix>.+?)(?P<count>[0-9\uff10-\uff19]+)\s*"
    r"(?P<marker>users?\u5165\u308a|users?\u5165|user\u5165\u308a)$",
    re.IGNORECASE,
)
STANDALONE_POPULARITY_RE = re.compile(
    r"^(users?\u5165\u308a|users?\u5165|user\u5165\u308a)$",
    re.IGNORECASE,
)
R18_RE = re.compile(r"^(r-?18|r18g|nsfw)$", re.IGNORECASE)
URL_OR_PATH_RE = re.compile(r"([a-z][a-z0-9+.-]*://|^[a-zA-Z]:\\|^\\\\)")

GENERAL_DESCRIPTIVE_KEYS = frozenset(
    {
        "1girl",
        "1boy",
        "solo",
        "smile",
        "standing",
        "sitting",
        "blue_hair",
        "long_hair",
        "short_hair",
        "breasts",
        "large_breasts",
        "water",
        "swimsuit",
        canonical_source_key("\u30bb\u30fc\u30e9\u30fc\u670d"),
        canonical_source_key("\u304a\u306a\u304b"),
        canonical_source_key("\u6c34\u7740"),
        canonical_source_key("\u80f8\u90e8"),
        canonical_source_key("\u5de8\u4e73"),
        canonical_source_key("\u306f\u3044\u3066\u306a\u3044"),
        canonical_source_key("\u304a\u5c3b"),
        canonical_source_key("\u30ac\u30fc\u30bf\u30fc\u30d9\u30eb\u30c8"),
        canonical_source_key("\u30e1\u30a4\u30c9"),
        canonical_source_key("\u30a6\u30a3\u30f3\u30af"),
        canonical_source_key("\u5c11\u5973"),
        canonical_source_key("\u3071\u3093\u3064"),
        canonical_source_key("\u30b9\u30af\u6c34"),
        canonical_source_key("\u88f8\u8db3"),
    }
)
ORIGINAL_KEYS = frozenset(
    {
        "original",
        canonical_source_key("\u30aa\u30ea\u30b8\u30ca\u30eb"),
        canonical_source_key("\u539f\u521b"),
        canonical_source_key("\u539f\u5275"),
    }
)
ROLE_BY_TAG_CATEGORY = {
    TagCategoryEnum.character.value: "character",
    TagCategoryEnum.copyright.value: "work_title",
    TagCategoryEnum.artist.value: "artist_creator",
    "character": "character",
    "copyright": "work_title",
    "artist": "artist_creator",
    "4": "character",
    "3": "work_title",
    "1": "artist_creator",
}
FORBIDDEN_TRUTH_TABLES = (
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_evidence",
    "blombooru_entity_external_identities",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    "blombooru_media_tags",
    "blombooru_tag_translations",
    "blombooru_tag_translation_jobs",
    "blombooru_provider_cache",
    "blombooru_negative_lookup_cache",
)
ALLOWED_WRITE_TABLES = (
    "blombooru_source_name_candidate_extraction_runs",
    "blombooru_source_name_candidate_record_verdicts",
    "blombooru_source_name_candidates",
)


class SourceNameCandidateExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceCandidateInputGroup:
    group_key: str
    provider: str
    source_metadata_record_id: int | None = None
    media_id: int | None = None
    data_type_label: str | None = None
    metadata_kind: str | None = None
    content_class: str | None = None
    content_class_reviewed: bool = False
    content_class_locked: bool = False
    eligibility_status: str = "not_checked"
    eligibility_reason: str | None = None
    title: str | None = None
    caption: str | None = None
    artist_name: str | None = None
    source_work_id_present: bool = False
    source_url_present: bool = False
    tags: tuple[dict[str, Any], ...] = ()
    source_names: tuple[dict[str, Any], ...] = ()
    source_assertions: tuple[dict[str, Any], ...] = ()
    alias_candidates: tuple[dict[str, Any], ...] = ()
    media_tags: tuple[dict[str, Any], ...] = ()
    data_origin: str = "real_dev_db"


@dataclass(frozen=True)
class SourceExtractionUnitOccurrence:
    group_key: str
    provider: str
    source_metadata_record_id: int | None
    media_id: int | None
    source_field: str
    origin_id: str | None
    raw_value: str
    role_hint: str | None = None
    context_key: str | None = None


@dataclass(frozen=True)
class SourceExtractionUnit:
    extraction_key: str
    normalized_value: str
    canonical_key: str
    raw_values: tuple[str, ...]
    provider: str
    source_field: str
    role_hint: str | None
    context_key: str | None
    language_hint: str | None
    script_hint: str | None
    occurrences: tuple[SourceExtractionUnitOccurrence, ...]
    llm_required: bool
    deterministic_resolution: str
    unit_group: SourceCandidateInputGroup


@dataclass(frozen=True)
class DeterministicHint:
    raw_value: str
    action: str
    extracted_value: str | None
    role_hint: str | None
    reason: str
    origin_type: str
    origin_id: str | None = None
    confidence: float = 0.0
    evidence_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateDraft:
    group_key: str
    provider: str
    source_metadata_record_id: int | None
    media_id: int | None
    origin_type: str
    origin_id: str | None
    raw_value: str
    display_name: str
    normalized_value: str
    canonical_key: str
    candidate_role: str
    candidate_status: str
    extraction_verdict: str
    language_hint: str | None
    script_hint: str | None
    work_context: str | None
    work_context_key: str | None
    parenthetical_base: str | None
    parenthetical_context: str | None
    extraction_action: str
    confidence: float | None
    reason: str | None
    rejection_reason: str | None
    no_name_reason: str | None
    evidence_payload: dict[str, Any]
    candidate_key: str


@dataclass(frozen=True)
class RejectedTagDraft:
    group_key: str
    provider: str
    raw_value: str
    normalized_value: str
    rejection_reason: str
    reason: str
    origin_type: str
    origin_id: str | None = None


@dataclass(frozen=True)
class MetaTagDraft:
    group_key: str
    provider: str
    raw_value: str
    normalized_value: str
    meta_role: str
    reason: str
    extracted_prefix: str | None = None
    origin_type: str | None = None
    origin_id: str | None = None


@dataclass(frozen=True)
class AmbiguousItemDraft:
    group_key: str
    provider: str
    raw_value: str
    reason: str
    origin_type: str | None = None
    origin_id: str | None = None


@dataclass(frozen=True)
class RecordVerdictDraft:
    group_key: str
    provider: str
    source_metadata_record_id: int | None
    media_id: int | None
    extraction_verdict: str
    verdict_reason: str
    no_name_reason: str | None
    candidate_count: int
    rejected_count: int
    meta_count: int
    ambiguous_count: int
    confidence_summary: dict[str, Any]
    extraction_warnings_json: list[str]
    evidence_payload: dict[str, Any]


@dataclass(frozen=True)
class ExtractionResultBundle:
    run_id: str
    run_label: str
    groups: tuple[SourceCandidateInputGroup, ...]
    record_verdicts: tuple[RecordVerdictDraft, ...]
    candidates: tuple[CandidateDraft, ...]
    rejected_tags: tuple[RejectedTagDraft, ...]
    meta_tags: tuple[MetaTagDraft, ...]
    ambiguous_items: tuple[AmbiguousItemDraft, ...]
    llm_inputs: tuple[dict[str, Any], ...]
    llm_outputs: tuple[dict[str, Any], ...]
    validation_failures: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def popularity_suffix_prefix(value: Any) -> dict[str, Any] | None:
    raw = normalize_source_text(value)
    if not raw:
        return None
    normalized = normalize_source_text(raw)
    if STANDALONE_POPULARITY_RE.match(normalized):
        return {
            "raw_tag": raw,
            "extraction_action": "popularity_suffix_detected_no_prefix",
            "extracted_prefix": None,
            "original_tag_role": "popularity_meta",
            "prefix_candidate_role": None,
        }
    match = POPULARITY_SUFFIX_RE.match(normalized)
    if not match:
        return None
    prefix = normalize_source_text(match.group("prefix"))
    if not prefix:
        return None
    return {
        "raw_tag": raw,
        "extraction_action": "popularity_suffix_stripped",
        "extracted_prefix": prefix,
        "original_tag_role": "popularity_meta",
        "prefix_candidate_role": "unknown_name_like",
        "count": normalize_source_text(match.group("count")),
        "marker": normalize_source_text(match.group("marker")),
    }


def is_meta_or_descriptive_rejection(value: Any) -> tuple[str, str] | None:
    raw = normalize_source_text(value)
    if not raw:
        return "empty_or_invalid", "empty_or_invalid"
    if URL_OR_PATH_RE.search(raw):
        return "url_or_path", "url_or_path"
    if R18_RE.match(raw):
        return "explicit_r18_meta", "explicit_r18_meta"
    if popularity_suffix_prefix(raw):
        return "popularity_meta", "pixiv_popularity_marker"
    key = canonical_source_key(raw)
    if key in GENERAL_DESCRIPTIVE_KEYS:
        return "descriptive_general", "known_general_descriptive_tag"
    return None


def _source_text_rows(rows: Iterable[Any], *, keys: Sequence[str]) -> list[str]:
    values: list[str] = []
    for row in rows:
        if isinstance(row, Mapping):
            for key in keys:
                value = normalize_source_text(row.get(key))
                if value:
                    values.append(value)
        else:
            value = normalize_source_text(row)
            if value:
                values.append(value)
    return values


def _raw_strings_for_group(group: SourceCandidateInputGroup) -> set[str]:
    values: set[str] = set()
    values.update(_source_text_rows(group.tags, keys=("raw_tag", "normalized_tag")))
    values.update(_source_text_rows(group.source_names, keys=("raw_name", "normalized_name")))
    values.update(_source_text_rows(group.source_assertions, keys=("raw_input", "asserted_name")))
    values.update(_source_text_rows(group.media_tags, keys=("name",)))
    values.update(_source_text_rows(group.alias_candidates, keys=("source_display_name", "target_display_name")))
    for value in (group.title, group.caption, group.artist_name):
        normalized = normalize_source_text(value)
        if normalized:
            values.add(normalized)
    return values


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return normalize_source_text(value.value) or None
    return normalize_source_text(value) or None


def media_llm_eligibility(media: Media | None) -> tuple[bool, str, dict[str, Any]]:
    """Return whether metadata for this media may be sent to an external LLM."""

    if media is None:
        return False, "media_missing_or_unlinked", {
            "content_class": None,
            "content_class_reviewed": False,
            "content_class_locked": False,
        }
    content_class = _enum_value(media.content_class)
    reviewed = bool(getattr(media, "content_class_reviewed", False))
    locked = bool(getattr(media, "content_class_locked", False))
    payload = {
        "content_class": content_class,
        "content_class_reviewed": reviewed,
        "content_class_locked": locked,
    }
    if content_class in APPROVED_LLM_CONTENT_CLASSES:
        return True, "eligible_anime", payload
    if content_class == ContentClassEnum.illustration.value and (reviewed or locked):
        return True, "eligible_approved_illustration", payload
    if content_class == ContentClassEnum.illustration.value:
        return False, "excluded_unapproved_illustration", payload
    if content_class == ContentClassEnum.non_anime.value:
        return False, "excluded_non_anime", payload
    if content_class == ContentClassEnum.unknown.value or not content_class:
        return False, "excluded_unknown_or_unclassified", payload
    return False, "excluded_unsupported_content_class", payload


def _stable_json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_payload_hash(payload: Any) -> str:
    return hashlib.sha256(_stable_json_dumps(payload).encode("utf-8")).hexdigest()


def group_input_payload_hash(group: SourceCandidateInputGroup) -> str:
    return stable_payload_hash(group_prompt_payload(group))


def llm_cache_fingerprint(group: SourceCandidateInputGroup, provider_summary: Mapping[str, Any]) -> str:
    config_fingerprint = stable_payload_hash(
        {
            "llm_provider_label": provider_summary.get("llm_provider_label"),
            "model_label": provider_summary.get("model_label"),
            "provider_type": provider_summary.get("provider_type"),
            "primary_provider_type": provider_summary.get("primary_provider_type"),
            "uses_primary_model": provider_summary.get("uses_primary_model"),
            "uses_fallback_provider": provider_summary.get("uses_fallback_provider"),
            "fallback_provider_type": provider_summary.get("fallback_provider_type"),
            "llm_access_configured": provider_summary.get("llm_access_configured"),
            "fallback_access_configured": provider_summary.get("fallback_access_configured"),
        }
    )
    payload = {
        "provider_label": provider_summary.get("llm_provider_label") or provider_summary.get("provider_mode"),
        "model_label": provider_summary.get("model_label"),
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "input_payload_hash": group_input_payload_hash(group),
        "extractor_version": EXTRACTOR_VERSION,
        "relevant_config_fingerprint": config_fingerprint,
    }
    return stable_payload_hash(payload)


def _source_field_for_provider_tag(group: SourceCandidateInputGroup) -> str:
    provider = normalize_source_text(group.provider)
    if provider == "pixiv":
        return "pixiv_tag"
    if provider in {"danbooru", "gelbooru"}:
        return "booru_tag"
    return "source_tag_observation"


def _unit_key(
    *,
    normalized_value: str,
    provider: str,
    source_field: str,
    role_hint: str | None,
    context_key: str | None,
) -> str:
    payload = {
        "normalized_value": canonical_source_key(normalized_value),
        "provider": canonical_source_key(provider),
        "source_field": canonical_source_key(source_field),
        "role_hint": canonical_source_key(role_hint),
        "context_key": canonical_source_key(context_key),
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
    }
    return f"source-extraction-unit:{stable_payload_hash(payload)}"


def _add_unit_occurrence(
    rows: list[SourceExtractionUnitOccurrence],
    *,
    group: SourceCandidateInputGroup,
    source_field: str,
    raw_value: Any,
    origin_id: str | None = None,
    role_hint: str | None = None,
    context_key: str | None = None,
) -> None:
    raw = normalize_source_text(raw_value)
    if not raw:
        return
    popularity = popularity_suffix_prefix(raw)
    if popularity and normalize_source_text(popularity.get("extracted_prefix")):
        context_key = "popularity_suffix_stripped"
    rows.append(
        SourceExtractionUnitOccurrence(
            group_key=group.group_key,
            provider=group.provider,
            source_metadata_record_id=group.source_metadata_record_id,
            media_id=group.media_id,
            source_field=source_field,
            origin_id=origin_id,
            raw_value=normalize_source_text(raw),
            role_hint=role_hint,
            context_key=context_key,
        )
    )


def extraction_unit_occurrences_for_group(group: SourceCandidateInputGroup) -> tuple[SourceExtractionUnitOccurrence, ...]:
    rows: list[SourceExtractionUnitOccurrence] = []
    tag_source_field = _source_field_for_provider_tag(group)
    for tag in group.tags:
        role_hint = _candidate_role_from_source_role(tag.get("source_category_raw"))
        if role_hint == "unknown_name_like":
            role_hint = None
        _add_unit_occurrence(
            rows,
            group=group,
            source_field=tag_source_field,
            raw_value=tag.get("raw_tag") or tag.get("normalized_tag"),
            origin_id=normalize_source_text(tag.get("observation_key")) or None,
            role_hint=role_hint,
        )
    for name in group.source_names:
        _add_unit_occurrence(
            rows,
            group=group,
            source_field="source_name_observation",
            raw_value=name.get("raw_name") or name.get("normalized_name"),
            origin_id=normalize_source_text(name.get("observation_key")) or None,
            role_hint=_candidate_role_from_source_role(name.get("name_role")),
        )
    for assertion in group.source_assertions:
        _add_unit_occurrence(
            rows,
            group=group,
            source_field="source_assertion",
            raw_value=assertion.get("asserted_name") or assertion.get("raw_input"),
            origin_id=normalize_source_text(assertion.get("assertion_key")) or None,
            role_hint=_candidate_role_from_source_role(assertion.get("asserted_role")),
        )
    for media_tag in group.media_tags:
        action_source = normalize_source_text(media_tag.get("source"))
        source_field = "ai_model_tag" if media_tag.get("is_suggestion") or action_source in {"ai", "wd", "wd_tagger"} else "normal_tag"
        _add_unit_occurrence(
            rows,
            group=group,
            source_field=source_field,
            raw_value=media_tag.get("name"),
            origin_id=normalize_source_text(media_tag.get("tag_id")) or None,
            role_hint=_candidate_role_from_source_role(media_tag.get("category")),
        )
    if group.title:
        _add_unit_occurrence(rows, group=group, source_field="pixiv_title", raw_value=group.title)
    if group.caption:
        _add_unit_occurrence(rows, group=group, source_field="pixiv_caption", raw_value=group.caption)
    if group.artist_name:
        _add_unit_occurrence(rows, group=group, source_field="pixiv_artist", raw_value=group.artist_name, role_hint="artist_creator")
    return tuple(rows)


def _unit_group_from_occurrences(
    extraction_key: str,
    normalized_value: str,
    provider: str,
    source_field: str,
    role_hint: str | None,
    occurrences: Sequence[SourceExtractionUnitOccurrence],
) -> SourceCandidateInputGroup:
    first = occurrences[0]
    base = {
        "unit_extraction_key": extraction_key,
        "source_count": len(occurrences),
        "source_field": source_field,
    }
    if source_field in {"pixiv_tag", "booru_tag", "source_tag_observation"}:
        tag_value = first.raw_value if first.context_key == "popularity_suffix_stripped" else normalized_value
        return SourceCandidateInputGroup(
            group_key=f"extraction_unit:{stable_payload_hash(base)[:24]}",
            provider=provider,
            tags=({"raw_tag": tag_value, "source_tag_kind": "provider_tag", "source_category_raw": role_hint},),
            data_origin="deduped_extraction_unit",
        )
    if source_field == "source_assertion":
        return SourceCandidateInputGroup(
            group_key=f"extraction_unit:{stable_payload_hash(base)[:24]}",
            provider=provider,
            source_assertions=({"asserted_name": normalized_value, "asserted_role": role_hint, "assertion_key": first.origin_id},),
            data_origin="deduped_extraction_unit",
        )
    if source_field == "source_name_observation":
        return SourceCandidateInputGroup(
            group_key=f"extraction_unit:{stable_payload_hash(base)[:24]}",
            provider=provider,
            source_names=({"raw_name": normalized_value, "name_role": role_hint, "observation_key": first.origin_id},),
            data_origin="deduped_extraction_unit",
        )
    if source_field in {"normal_tag", "ai_model_tag"}:
        return SourceCandidateInputGroup(
            group_key=f"extraction_unit:{stable_payload_hash(base)[:24]}",
            provider="local_media_tags",
            media_tags=({"name": normalized_value, "category": role_hint, "source": source_field, "is_suggestion": source_field == "ai_model_tag"},),
            data_origin="deduped_extraction_unit",
        )
    kwargs: dict[str, Any] = {}
    if source_field == "pixiv_title":
        kwargs["title"] = normalized_value
    elif source_field == "pixiv_caption":
        kwargs["caption"] = normalized_value
    elif source_field == "pixiv_artist":
        kwargs["artist_name"] = normalized_value
    return SourceCandidateInputGroup(
        group_key=f"extraction_unit:{stable_payload_hash(base)[:24]}",
        provider=provider,
        data_origin="deduped_extraction_unit",
        **kwargs,
    )


def build_extraction_units(groups: Sequence[SourceCandidateInputGroup]) -> tuple[list[SourceExtractionUnit], dict[str, Any]]:
    occurrences: list[SourceExtractionUnitOccurrence] = []
    for group in groups:
        occurrences.extend(extraction_unit_occurrences_for_group(group))
    by_key: dict[str, list[SourceExtractionUnitOccurrence]] = defaultdict(list)
    normalized_by_key: dict[str, str] = {}
    for occurrence in occurrences:
        normalized = normalize_source_text(occurrence.raw_value)
        if not normalized:
            continue
        popularity = popularity_suffix_prefix(normalized)
        if popularity and normalize_source_text(popularity.get("extracted_prefix")):
            normalized = normalize_source_text(popularity["extracted_prefix"])
        key = _unit_key(
            normalized_value=normalized,
            provider=occurrence.provider,
            source_field=occurrence.source_field,
            role_hint=occurrence.role_hint,
            context_key=occurrence.context_key,
        )
        by_key[key].append(occurrence)
        normalized_by_key[key] = normalized

    units: list[SourceExtractionUnit] = []
    deterministic_resolved = 0
    llm_required = 0
    for key, unit_occurrences in sorted(by_key.items(), key=lambda item: item[0]):
        first = unit_occurrences[0]
        normalized = normalized_by_key[key]
        language, script = language_and_script_hint(normalized)
        unit_group = _unit_group_from_occurrences(
            key,
            normalized,
            first.provider,
            first.source_field,
            first.role_hint,
            unit_occurrences,
        )
        hints = deterministic_hints_for_group(unit_group)
        has_deterministic_resolution = bool(hints)
        needs_llm = not has_deterministic_resolution
        if needs_llm:
            llm_required += 1
        else:
            deterministic_resolved += 1
        units.append(
            SourceExtractionUnit(
                extraction_key=key,
                normalized_value=normalized,
                canonical_key=canonical_source_key(normalized),
                raw_values=tuple(sorted({item.raw_value for item in unit_occurrences})),
                provider=first.provider,
                source_field=first.source_field,
                role_hint=first.role_hint,
                context_key=first.context_key,
                language_hint=language,
                script_hint=script,
                occurrences=tuple(unit_occurrences),
                llm_required=needs_llm,
                deterministic_resolution="unresolved_needs_llm" if needs_llm else "deterministic_resolved",
                unit_group=unit_group,
            )
        )
    repeat_counts = sorted(
        (
            {
                "normalized_value": unit.normalized_value,
                "source_field": unit.source_field,
                "source_count": len(unit.occurrences),
                "llm_calls_avoided": max(0, len(unit.occurrences) - 1),
            }
            for unit in units
            if len(unit.occurrences) > 1
        ),
        key=lambda row: (-int(row["source_count"]), str(row["normalized_value"])),
    )
    raw_occurrences = len(occurrences)
    unique_units = len(units)
    return units, {
        "raw_string_occurrences_total": raw_occurrences,
        "unique_extraction_units_total": unique_units,
        "deterministic_resolved_units": deterministic_resolved,
        "llm_required_units": llm_required,
        "llm_calls_avoided_by_dedupe": max(0, raw_occurrences - unique_units),
        "average_source_records_per_extraction_unit": round(raw_occurrences / unique_units, 4) if unique_units else 0.0,
        "top_repeated_units": repeat_counts[:25],
    }


def deterministic_bundle_for_unit(
    unit: SourceExtractionUnit,
    *,
    run_id: str,
    run_label: str,
) -> ExtractionResultBundle:
    verdict_seed = "rejected_popularity_or_meta_only" if unit.context_key == "popularity_suffix_stripped" else "no_explicit_name"
    row = {
        "group_key": unit.unit_group.group_key,
        "provider": unit.unit_group.provider,
        "verdict": verdict_seed,
        "candidates": [],
        "rejected_summary": {},
        "no_name_reason": verdict_seed,
    }
    verdict, candidates, rejected, meta, ambiguous = validate_extraction_record(row, unit.unit_group)
    summary = build_extraction_summary(
        groups=[unit.unit_group],
        record_verdicts=[verdict],
        candidates=candidates,
        rejected_tags=rejected,
        meta_tags=meta,
        ambiguous_items=ambiguous,
        llm_counters={"deterministic_unit_resolution": 1},
        validation_failures=[],
    )
    return ExtractionResultBundle(
        run_id=run_id,
        run_label=run_label,
        groups=(unit.unit_group,),
        record_verdicts=(verdict,),
        candidates=candidates,
        rejected_tags=rejected,
        meta_tags=meta,
        ambiguous_items=ambiguous,
        llm_inputs=(),
        llm_outputs=(),
        validation_failures=(),
        summary=summary,
    )


def _copy_candidate_for_occurrence(
    candidate: CandidateDraft,
    unit: SourceExtractionUnit,
    occurrence: SourceExtractionUnitOccurrence,
) -> CandidateDraft:
    logical_key = _candidate_key(
        group_key=occurrence.group_key,
        raw_value=candidate.normalized_value,
        candidate_role=candidate.candidate_role,
        extraction_action=candidate.extraction_action,
        origin_type=occurrence.source_field,
        origin_id=occurrence.origin_id or unit.extraction_key,
    )
    return replace(
        candidate,
        group_key=occurrence.group_key,
        provider=occurrence.provider,
        source_metadata_record_id=occurrence.source_metadata_record_id,
        media_id=occurrence.media_id,
        origin_type=occurrence.source_field,
        origin_id=occurrence.origin_id,
        candidate_key=logical_key,
        evidence_payload={
            **candidate.evidence_payload,
            "extraction_unit_key": unit.extraction_key,
            "extraction_unit_normalized_value": unit.normalized_value,
            "deduped_llm_extraction_unit": True,
        },
    )


def reattach_unit_bundles_to_records(
    groups: Sequence[SourceCandidateInputGroup],
    units: Sequence[SourceExtractionUnit],
    bundles_by_unit_key: Mapping[str, ExtractionResultBundle],
    *,
    run_id: str,
    run_label: str,
) -> ExtractionResultBundle:
    groups_by_key = {group.group_key: group for group in groups}
    candidates_by_group: dict[str, list[CandidateDraft]] = defaultdict(list)
    rejected_by_group: dict[str, list[RejectedTagDraft]] = defaultdict(list)
    meta_by_group: dict[str, list[MetaTagDraft]] = defaultdict(list)
    ambiguous_by_group: dict[str, list[AmbiguousItemDraft]] = defaultdict(list)
    unit_verdicts_by_group: dict[str, list[str]] = defaultdict(list)
    llm_inputs: list[dict[str, Any]] = []
    llm_outputs: list[dict[str, Any]] = []
    validation_failures: list[dict[str, Any]] = []

    for unit in units:
        bundle = bundles_by_unit_key.get(unit.extraction_key)
        if bundle is None:
            continue
        llm_inputs.extend(bundle.llm_inputs)
        llm_outputs.extend(bundle.llm_outputs)
        validation_failures.extend(bundle.validation_failures)
        unit_verdict = bundle.record_verdicts[0].extraction_verdict if bundle.record_verdicts else "extraction_error_terminal"
        for occurrence in unit.occurrences:
            unit_verdicts_by_group[occurrence.group_key].append(unit_verdict)
            for candidate in bundle.candidates:
                candidates_by_group[occurrence.group_key].append(_copy_candidate_for_occurrence(candidate, unit, occurrence))
            for item in bundle.rejected_tags:
                rejected_by_group[occurrence.group_key].append(
                    RejectedTagDraft(
                        group_key=occurrence.group_key,
                        provider=occurrence.provider,
                        raw_value=occurrence.raw_value if unit.context_key == "popularity_suffix_stripped" else item.raw_value,
                        normalized_value=unit.normalized_value,
                        rejection_reason=item.rejection_reason,
                        reason=item.reason,
                        origin_type=occurrence.source_field,
                        origin_id=occurrence.origin_id,
                    )
                )
            for item in bundle.meta_tags:
                meta_by_group[occurrence.group_key].append(
                    MetaTagDraft(
                        group_key=occurrence.group_key,
                        provider=occurrence.provider,
                        raw_value=occurrence.raw_value if unit.context_key == "popularity_suffix_stripped" else item.raw_value,
                        normalized_value=unit.normalized_value,
                        meta_role=item.meta_role,
                        reason=item.reason,
                        extracted_prefix=item.extracted_prefix or (unit.normalized_value if unit.context_key == "popularity_suffix_stripped" else None),
                        origin_type=occurrence.source_field,
                        origin_id=occurrence.origin_id,
                    )
                )
            for item in bundle.ambiguous_items:
                ambiguous_by_group[occurrence.group_key].append(
                    AmbiguousItemDraft(
                        group_key=occurrence.group_key,
                        provider=occurrence.provider,
                        raw_value=unit.normalized_value,
                        reason=item.reason,
                        origin_type=occurrence.source_field,
                        origin_id=occurrence.origin_id,
                    )
                )

    record_verdicts: list[RecordVerdictDraft] = []
    all_candidates: list[CandidateDraft] = []
    all_rejected: list[RejectedTagDraft] = []
    all_meta: list[MetaTagDraft] = []
    all_ambiguous: list[AmbiguousItemDraft] = []
    for group in groups:
        group_candidates = list(_dedupe_candidates(candidates_by_group.get(group.group_key, [])))
        group_rejected = list(_dedupe_rejected(rejected_by_group.get(group.group_key, [])))
        group_meta = list(_dedupe_meta(meta_by_group.get(group.group_key, [])))
        group_ambiguous = ambiguous_by_group.get(group.group_key, [])
        unit_verdicts = unit_verdicts_by_group.get(group.group_key, [])
        if group_candidates:
            roles = {candidate.candidate_role for candidate in group_candidates}
            if len(group_candidates) > 1:
                verdict = "multiple_candidates_found"
            elif roles & {"character", "person", "alias_like", "unknown_name_like"}:
                verdict = "name_candidate_found"
            elif roles & {"work_title", "source_title"}:
                verdict = "work_candidate_found"
            elif "artist_creator" in roles:
                verdict = "artist_candidate_found"
            else:
                verdict = "ambiguous_needs_review"
            no_name_reason = None
        elif any(value == "extraction_error_retryable" for value in unit_verdicts):
            verdict = "extraction_error_retryable"
            no_name_reason = "retryable_unit_error"
        elif any(value in {"extraction_error_terminal", "extraction_error"} for value in unit_verdicts):
            verdict = "extraction_error_terminal"
            no_name_reason = "terminal_unit_error"
        elif group_ambiguous:
            verdict = "ambiguous_needs_review"
            no_name_reason = None
        elif group_meta and not group_rejected:
            verdict = "rejected_popularity_or_meta_only"
            no_name_reason = "all_units_popularity_or_meta"
        elif group_rejected:
            verdict = "rejected_general_only"
            no_name_reason = "all_units_rejected_general_or_meta"
        else:
            verdict = "no_explicit_name"
            no_name_reason = "no_extraction_units"
        group_candidates = [replace(candidate, extraction_verdict=verdict) for candidate in group_candidates]
        all_candidates.extend(group_candidates)
        all_rejected.extend(group_rejected)
        all_meta.extend(group_meta)
        all_ambiguous.extend(group_ambiguous)
        record_verdicts.append(
            RecordVerdictDraft(
                group_key=group.group_key,
                provider=group.provider,
                source_metadata_record_id=group.source_metadata_record_id,
                media_id=group.media_id,
                extraction_verdict=verdict,
                verdict_reason="derived_from_deduped_extraction_units",
                no_name_reason=no_name_reason,
                candidate_count=len(group_candidates),
                rejected_count=len(group_rejected),
                meta_count=len(group_meta),
                ambiguous_count=len(group_ambiguous),
                confidence_summary={"deduped_extraction_units": True, "unit_verdicts": dict(Counter(unit_verdicts))},
                extraction_warnings_json=["record_verdict_derived_from_unit_cache"],
                evidence_payload={
                    "source_layer_only": True,
                    "should_not_create_entity_truth": True,
                    "data_origin": group.data_origin,
                    "deduped_extraction_units": True,
                },
            )
        )
    summary = build_extraction_summary(
        groups=groups,
        record_verdicts=record_verdicts,
        candidates=all_candidates,
        rejected_tags=all_rejected,
        meta_tags=all_meta,
        ambiguous_items=all_ambiguous,
        llm_counters={"record_verdicts_derived_from_extraction_units": 1},
        validation_failures=validation_failures,
    )
    return ExtractionResultBundle(
        run_id=run_id,
        run_label=run_label,
        groups=tuple(groups_by_key.values()),
        record_verdicts=tuple(record_verdicts),
        candidates=tuple(_dedupe_candidates(all_candidates)),
        rejected_tags=tuple(_dedupe_rejected(all_rejected)),
        meta_tags=tuple(_dedupe_meta(all_meta)),
        ambiguous_items=tuple(all_ambiguous),
        llm_inputs=tuple(llm_inputs),
        llm_outputs=tuple(llm_outputs),
        validation_failures=tuple(validation_failures),
        summary=summary,
    )


def deterministic_hints_for_group(group: SourceCandidateInputGroup) -> list[DeterministicHint]:
    hints: list[DeterministicHint] = []

    for tag in group.tags:
        raw = normalize_source_text(tag.get("raw_tag"))
        origin_id = normalize_source_text(tag.get("observation_key")) or None
        if not raw:
            continue
        popularity = popularity_suffix_prefix(raw)
        if popularity:
            hints.append(
                DeterministicHint(
                    raw_value=raw,
                    action="popularity_meta",
                    extracted_value=None,
                    role_hint=None,
                    reason="full_tag_is_pixiv_popularity_marker",
                    origin_type="source_tag_observation",
                    origin_id=origin_id,
                    confidence=0.98,
                    evidence_payload=popularity,
                )
            )
            prefix = normalize_source_text(popularity.get("extracted_prefix"))
            if prefix:
                hints.append(
                    DeterministicHint(
                        raw_value=raw,
                        action="popularity_suffix_stripped",
                        extracted_value=prefix,
                        role_hint=_role_hint_from_context(group, prefix) or "unknown_name_like",
                        reason="pixiv_popularity_prefix_preserved_as_candidate",
                        origin_type="source_tag_observation",
                        origin_id=origin_id,
                        confidence=0.68,
                        evidence_payload=popularity,
                    )
                )
            continue
        rejection = is_meta_or_descriptive_rejection(raw)
        if rejection:
            hints.append(
                DeterministicHint(
                    raw_value=raw,
                    action="rejected_tag",
                    extracted_value=None,
                    role_hint=None,
                    reason=rejection[1],
                    origin_type="source_tag_observation",
                    origin_id=origin_id,
                    confidence=0.95,
                    evidence_payload={"rejection_reason": rejection[0]},
                )
            )
        parsed = parse_parenthetical_name(raw)
        if parsed:
            outer, inner = parsed
            hints.append(
                DeterministicHint(
                    raw_value=raw,
                    action="parenthetical_split",
                    extracted_value=outer,
                    role_hint="character",
                    reason="parenthetical_base_name_candidate",
                    origin_type="source_tag_observation",
                    origin_id=origin_id,
                    confidence=0.76,
                    evidence_payload={"parenthetical_base": outer, "parenthetical_context": inner},
                )
            )
            hints.append(
                DeterministicHint(
                    raw_value=raw,
                    action="parenthetical_split",
                    extracted_value=inner,
                    role_hint="work_title",
                    reason="parenthetical_work_context_candidate",
                    origin_type="source_tag_observation",
                    origin_id=origin_id,
                    confidence=0.72,
                    evidence_payload={"parenthetical_base": outer, "parenthetical_context": inner},
                )
            )

    for name in group.source_names:
        raw = normalize_source_text(name.get("raw_name"))
        if not raw:
            continue
        role = _candidate_role_from_source_role(name.get("name_role"))
        hints.append(
            DeterministicHint(
                raw_value=raw,
                action="provider_structured_field",
                extracted_value=raw,
                role_hint=role,
                reason="source_name_observation_preserved",
                origin_type="source_name_observation",
                origin_id=normalize_source_text(name.get("observation_key")) or None,
                confidence=float(name.get("confidence") or 0.74),
                evidence_payload={"source_field": name.get("source_field"), "requires_review": name.get("requires_review")},
            )
        )

    for assertion in group.source_assertions:
        raw = normalize_source_text(assertion.get("asserted_name") or assertion.get("raw_input"))
        if not raw:
            continue
        role = _candidate_role_from_source_role(assertion.get("asserted_role"))
        if role not in {"character", "person", "work_title", "artist_creator", "source_title"}:
            continue
        hints.append(
            DeterministicHint(
                raw_value=raw,
                action="direct_name",
                extracted_value=raw,
                role_hint=role,
                reason="source_searchable_name_assertion_preserved",
                origin_type="source_assertion",
                origin_id=normalize_source_text(assertion.get("assertion_key")) or None,
                confidence=float(assertion.get("confidence_score") or 0.72),
                evidence_payload={"assertion_status": assertion.get("status"), "confidence": assertion.get("confidence")},
            )
        )

    for media_tag in group.media_tags:
        raw = normalize_source_text(media_tag.get("name"))
        if not raw:
            continue
        category = normalize_source_text(media_tag.get("category"))
        role = ROLE_BY_TAG_CATEGORY.get(category)
        if not role:
            continue
        action = "ai_model_character_tag" if normalize_source_text(media_tag.get("source")).startswith("ai") else "normal_tag_candidate"
        hints.append(
            DeterministicHint(
                raw_value=raw,
                action=action,
                extracted_value=raw,
                role_hint=role,
                reason="media_tag_category_signal_preserved",
                origin_type="ai_model_tag" if action == "ai_model_character_tag" else "normal_tag",
                origin_id=str(media_tag.get("tag_id") or ""),
                confidence=0.64 if action == "ai_model_character_tag" else 0.78,
                evidence_payload={
                    "tag_category": category,
                    "source": media_tag.get("source"),
                    "is_suggestion": media_tag.get("is_suggestion"),
                },
            )
        )

    if group.artist_name:
        hints.append(
            DeterministicHint(
                raw_value=group.artist_name,
                action="provider_structured_field",
                extracted_value=group.artist_name,
                role_hint="artist_creator",
                reason="provider_artist_field_preserved",
                origin_type="provider_field",
                origin_id="artist_name",
                confidence=0.84,
            )
        )
    return hints


def _role_hint_from_context(group: SourceCandidateInputGroup, value: str) -> str | None:
    key = canonical_source_key(value)
    for media_tag in group.media_tags:
        if canonical_source_key(media_tag.get("name")) == key:
            return ROLE_BY_TAG_CATEGORY.get(normalize_source_text(media_tag.get("category")))
    for name in group.source_names:
        if canonical_source_key(name.get("raw_name")) == key:
            return _candidate_role_from_source_role(name.get("name_role"))
    for assertion in group.source_assertions:
        if canonical_source_key(assertion.get("asserted_name") or assertion.get("raw_input")) == key:
            return _candidate_role_from_source_role(assertion.get("asserted_role"))
    return None


def _candidate_role_from_source_role(role: Any) -> str:
    value = canonical_source_key(role)
    if value in {"artist", "creator"}:
        return "artist_creator"
    if value in {"character", "person", "work_title", "source_title"}:
        return value
    return "unknown_name_like"


def _candidate_key(
    *,
    group_key: str,
    raw_value: str,
    candidate_role: str,
    extraction_action: str,
    origin_type: str,
    origin_id: str | None,
) -> str:
    raw_key = canonical_source_key(raw_value)
    origin_key = canonical_source_key(origin_id or "")
    base = canonical_source_key(f"{group_key}:{origin_type}:{origin_key}:{candidate_role}:{extraction_action}:{raw_key}")
    return f"source-name-candidate:{base[:860]}"


def _run_scoped_candidate_key(run_id: str, logical_candidate_key: str) -> str:
    run_key = stable_payload_hash({"run_id": run_id})[:24]
    logical_key = canonical_source_key(logical_candidate_key)
    logical_hash = stable_payload_hash({"candidate_key": logical_candidate_key})[:24]
    compact_logical = logical_key[:780]
    return f"source-name-candidate-run:{run_key}:{compact_logical}:{logical_hash}"[:900]


def _candidate_from_hint(group: SourceCandidateInputGroup, hint: DeterministicHint, *, verdict: str) -> CandidateDraft | None:
    raw_value = normalize_source_text(hint.extracted_value or hint.raw_value)
    if not raw_value:
        return None
    if hint.action in {"popularity_meta", "rejected_tag"}:
        return None
    role = hint.role_hint if hint.role_hint in ALLOWED_CANDIDATE_ROLES else "unknown_name_like"
    normalized = normalize_source_text(raw_value)
    canonical = canonical_source_key(normalized)
    language, script = language_and_script_hint(normalized)
    parenthetical_base = normalize_source_text(hint.evidence_payload.get("parenthetical_base")) or None
    parenthetical_context = normalize_source_text(hint.evidence_payload.get("parenthetical_context")) or None
    work_context = parenthetical_context if role != "work_title" else None
    key = _candidate_key(
        group_key=group.group_key,
        raw_value=normalized,
        candidate_role=role,
        extraction_action=hint.action,
        origin_type=hint.origin_type,
        origin_id=hint.origin_id,
    )
    return CandidateDraft(
        group_key=group.group_key,
        provider=group.provider,
        source_metadata_record_id=group.source_metadata_record_id,
        media_id=group.media_id,
        origin_type=hint.origin_type,
        origin_id=hint.origin_id,
        raw_value=normalized,
        display_name=normalized,
        normalized_value=normalized,
        canonical_key=canonical,
        candidate_role=role,
        candidate_status="needs_review" if role == "unknown_name_like" or hint.action == "ai_model_character_tag" else "active_candidate",
        extraction_verdict=verdict,
        language_hint=language,
        script_hint=script,
        work_context=work_context,
        work_context_key=canonical_source_key(work_context) if work_context else None,
        parenthetical_base=parenthetical_base,
        parenthetical_context=parenthetical_context,
        extraction_action=hint.action,
        confidence=hint.confidence,
        reason=hint.reason,
        rejection_reason=None,
        no_name_reason=None,
        evidence_payload={
            **hint.evidence_payload,
            "deterministic_hint": True,
            "source_layer_only": True,
            "should_not_create_entity_truth": True,
            "full_popularity_tag_is_alias": False if hint.action == "popularity_suffix_stripped" else None,
        },
        candidate_key=key,
    )


def _rejected_from_hint(group: SourceCandidateInputGroup, hint: DeterministicHint) -> RejectedTagDraft | None:
    if hint.action not in {"rejected_tag", "popularity_meta"}:
        return None
    raw = normalize_source_text(hint.raw_value)
    if not raw:
        return None
    rejection_reason = normalize_source_text(hint.evidence_payload.get("rejection_reason"))
    if not rejection_reason:
        rejection_reason = "popularity_meta" if hint.action == "popularity_meta" else "not_name_like"
    if rejection_reason not in ALLOWED_REJECTION_REASONS:
        rejection_reason = "not_name_like"
    return RejectedTagDraft(
        group_key=group.group_key,
        provider=group.provider,
        raw_value=raw,
        normalized_value=normalize_source_text(raw),
        rejection_reason=rejection_reason,
        reason=hint.reason,
        origin_type=hint.origin_type,
        origin_id=hint.origin_id,
    )


def _meta_from_hint(group: SourceCandidateInputGroup, hint: DeterministicHint) -> MetaTagDraft | None:
    if hint.action != "popularity_meta":
        return None
    payload = hint.evidence_payload
    return MetaTagDraft(
        group_key=group.group_key,
        provider=group.provider,
        raw_value=hint.raw_value,
        normalized_value=normalize_source_text(hint.raw_value),
        meta_role="popularity_meta",
        reason=hint.reason,
        extracted_prefix=normalize_source_text(payload.get("extracted_prefix")) or None,
        origin_type=hint.origin_type,
        origin_id=hint.origin_id,
    )


def group_prompt_payload(group: SourceCandidateInputGroup) -> dict[str, Any]:
    hints = deterministic_hints_for_group(group)
    return {
        "group_key": group.group_key,
        "provider": group.provider,
        "source_metadata_record_id": group.source_metadata_record_id,
        "media_id": group.media_id,
        "data_origin": group.data_origin,
        "data_type_label": group.data_type_label,
        "metadata_kind": group.metadata_kind,
        "content_eligibility": {
            "status": group.eligibility_status,
            "reason": group.eligibility_reason,
            "content_class": group.content_class,
            "content_class_reviewed": group.content_class_reviewed,
            "content_class_locked": group.content_class_locked,
        },
        "title": group.title,
        "caption": group.caption,
        "artist_name": group.artist_name,
        "source_work_id_present": group.source_work_id_present,
        "source_url_present": group.source_url_present,
        "tags": list(group.tags),
        "source_names": list(group.source_names),
        "source_assertions": list(group.source_assertions),
        "alias_candidates": list(group.alias_candidates),
        "media_tags": list(group.media_tags),
        "deterministic_hints": [asdict(hint) for hint in hints],
        "must_not_create_entity_truth": True,
    }


def source_name_candidate_system_prompt() -> str:
    iri = "\u5165\u308a"
    return (
        "You extract unconfirmed source-layer name candidates from anime/illustration metadata groups. "
        "This is NOT Entity truth, NOT EntityAlias, NOT MediaEntityCandidate, NOT a confirmed assignment.\n"
        "Return ONLY compact valid JSON with a top-level object: {\"records\": [...]}.\n"
        "For every input group, return exactly one record with the same group_key.\n"
        "Each record must include: group_key, provider, verdict, candidates, rejected_summary. "
        "Only include ambiguous_items or error_code when needed. You may include should_not_create_entity_truth=true.\n"
        "Allowed verdict values: name_candidate_found, work_candidate_found, artist_candidate_found, "
        "multiple_candidates_found, ambiguous_needs_review, original_without_explicit_name, no_explicit_name, "
        "metadata_insufficient, provider_not_applicable, rejected_general_only, rejected_popularity_or_meta_only, extraction_error.\n"
        "Candidate fields only: raw_value, display_name, normalized_value or canonical_key, role, status, confidence, source_field, extraction_action. "
        "Optional compact fields: work_context, parenthetical_base, parenthetical_context.\n"
        "Allowed role values: character, person, work_title, artist_creator, source_title, alias_like, unknown_name_like.\n"
        "Allowed status values: active_candidate, needs_review.\n"
        "unknown_name_like must default to needs_review unless there is strong structured source evidence. "
        "Obvious body, clothing, pose, age/appearance, R-18/meta, and general descriptive tags must be rejected or needs_review, never active unknown_name_like.\n"
        "Allowed extraction_action values: direct_name, parenthetical_split, popularity_suffix_stripped, "
        "provider_structured_field, normal_tag_candidate, ai_model_character_tag, context_inferred.\n"
        f"Allowed source_field values: {sorted(ALLOWED_ORIGINS)}.\n"
        "rejected_summary must be an object with integer counts: descriptive_general_count, popularity_meta_count, "
        "explicit_r18_meta_count, invalid_or_empty_count, duplicate_count, other_rejected_count.\n"
        "Use compact error_code values when needed: invalid_json, schema_validation_failed, no_candidates_after_validation, "
        "timeout, provider_error, unsupported_payload, candidate_confidence_invalid, malformed_candidate_array, "
        "eligibility_excluded, cache_fingerprint_mismatch.\n"
        "Preserve all plausible multilingual spellings and aliases; do not choose only one best name. "
        "Use group context: all tags, title, caption, artist, source assertions, source name observations, normal tags, and AI tags.\n"
        "Pixiv tags must not all be generic provider tags. Identify likely character/person/work/artist/name-like strings.\n"
        "Original/original-work tags do not automatically mean no name; if an explicit OC/name-like tag exists, extract it. "
        "If no explicit name-like tag exists, give a compact no-name verdict.\n"
        f"Popularity tags ending with a count plus users{iri} or similar are meta tags as a whole. "
        "Do not make the full popularity tag a concept/name alias. If a prefix exists, emit the prefix as a candidate "
        "with extraction_action=popularity_suffix_stripped and keep the full raw tag only as meta/evidence.\n"
        "Parenthetical tags such as name(work) should emit the base name candidate, work/context candidate, and preserve combined alias evidence.\n"
        "R-18, general visual descriptors, poses, body parts, clothing, popularity markers, URLs, and local paths are not character/person names. "
        "Do not over-promote Pixiv/source titles to source_title/work_title unless they clearly identify a work/source title. "
        "Source assertions are evidence but not truth; descriptive assertions can still be rejected or needs_review.\n"
        "If uncertain, use ambiguous_needs_review rather than silently rejecting.\n"
        "Do not output verbose reasons, prose explanations, chain-of-thought, or per-tag rationale text."
    )


def extraction_messages(groups: Sequence[SourceCandidateInputGroup]) -> list[dict[str, str]]:
    payload = {
        "prompt_version": PROMPT_VERSION,
        "structured_output_schema_version": SCHEMA_VERSION,
        "record_count": len(groups),
        "records": [group_prompt_payload(group) for group in groups],
    }
    return [
        {"role": "system", "content": source_name_candidate_system_prompt()},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)},
    ]


def _coerce_record_array(payload: Any, *, expected_count: int) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        rows = payload.get("records") or payload.get("results") or payload.get("items")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:records_not_array")
    if len(rows) != expected_count:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:record_count_mismatch")
    if not all(isinstance(row, Mapping) for row in rows):
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:record_not_object")
    return rows


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, bool):
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:candidate_confidence_invalid")
    if isinstance(value, (int, float)):
        confidence = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not re.fullmatch(r"(0(?:\.\d+)?|1(?:\.0+)?)", stripped):
            raise SourceNameCandidateExtractionError("llm_output_schema_invalid:candidate_confidence_invalid")
        confidence = float(stripped)
    else:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:candidate_confidence_invalid")
    if confidence < 0.0 or confidence > 1.0:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:candidate_confidence_invalid")
    return confidence


def _compact_rejected_summary(row: Mapping[str, Any]) -> dict[str, int]:
    raw = row.get("rejected_summary")
    if raw is None:
        return {key: 0 for key in COMPACT_REJECTED_SUMMARY_KEYS}
    if not isinstance(raw, Mapping):
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:rejected_summary")
    result: dict[str, int] = {}
    for key in COMPACT_REJECTED_SUMMARY_KEYS:
        value = raw.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SourceNameCandidateExtractionError("llm_output_schema_invalid:rejected_summary")
        if int(value) < 0:
            raise SourceNameCandidateExtractionError("llm_output_schema_invalid:rejected_summary")
        result[key] = int(value)
    return result


def _validate_candidate(
    item: Mapping[str, Any],
    group: SourceCandidateInputGroup,
    *,
    verdict: str,
) -> CandidateDraft:
    raw = normalize_source_text(item.get("raw_value"))
    display = normalize_source_text(item.get("display_name")) or raw
    normalized = normalize_source_text(item.get("normalized_value")) or normalize_source_text(display)
    canonical = normalize_source_text(item.get("canonical_key")) or canonical_source_key(normalized)
    if not raw or not normalized or not canonical:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:candidate_value_missing")
    role = normalize_source_text(item.get("role")) or normalize_source_text(item.get("candidate_role"))
    status = normalize_source_text(item.get("status")) or normalize_source_text(item.get("candidate_status"))
    action = normalize_source_text(item.get("extraction_action"))
    action = ACTION_SYNONYMS.get(action, action)
    origin = normalize_source_text(item.get("source_field")) or normalize_source_text(item.get("extracted_from"))
    origin = ORIGIN_SYNONYMS.get(origin, origin)
    if role not in ALLOWED_CANDIDATE_ROLES:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:candidate_role")
    if status not in {"active_candidate", "needs_review"}:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:candidate_status")
    if action not in ALLOWED_EXTRACTION_ACTIONS:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:extraction_action")
    if origin not in ALLOWED_ORIGINS:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:extracted_from")
    candidate_status_guard: dict[str, Any] = {}
    try:
        confidence = _coerce_confidence(item.get("confidence"))
    except SourceNameCandidateExtractionError as exc:
        if "candidate_confidence_invalid" not in str(exc):
            raise
        confidence = 0.0
        status = "needs_review"
        candidate_status_guard["candidate_confidence_invalid_downgraded"] = True
    popularity = popularity_suffix_prefix(raw)
    rejection_reason = None
    if popularity and action != "popularity_suffix_stripped":
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:full_popularity_tag_candidate")
    if popularity and action == "popularity_suffix_stripped":
        prefix = normalize_source_text(popularity.get("extracted_prefix"))
        if prefix:
            display = prefix if canonical_source_key(display) == canonical_source_key(raw) else display
            normalized = prefix if canonical_source_key(normalized) == canonical_source_key(raw) else normalized
            canonical = canonical_source_key(normalized)
    if action == "context_inferred" and confidence > 0.55:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:inferred_confidence_too_high")
    guard_rejection = None
    if not (popularity and action == "popularity_suffix_stripped"):
        guard_rejection = is_meta_or_descriptive_rejection(normalized) or is_meta_or_descriptive_rejection(raw)
    if role == "unknown_name_like" and status == "active_candidate":
        status = "needs_review"
        candidate_status_guard["unknown_name_like_active_downgraded"] = True
    if guard_rejection and status == "active_candidate":
        status = "needs_review"
        rejection_reason = guard_rejection[0]
        candidate_status_guard["descriptive_or_meta_active_downgraded"] = True
        candidate_status_guard["guard_rejection_reason"] = guard_rejection[0]
    language = normalize_source_text(item.get("language_hint")) or None
    script = normalize_source_text(item.get("script_hint")) or None
    if not language or not script:
        language, script = language_and_script_hint(normalized)
    work_context = normalize_source_text(item.get("work_context")) or None
    parenthetical_base = normalize_source_text(item.get("parenthetical_base")) or None
    parenthetical_context = normalize_source_text(item.get("parenthetical_context")) or None
    evidence_tags = item.get("evidence_tags") if isinstance(item.get("evidence_tags"), list) else []
    sibling_context = item.get("sibling_context") if isinstance(item.get("sibling_context"), list) else []
    key = _candidate_key(
        group_key=group.group_key,
        raw_value=normalized,
        candidate_role=role,
        extraction_action=action,
        origin_type=origin,
        origin_id=None,
    )
    return CandidateDraft(
        group_key=group.group_key,
        provider=group.provider,
        source_metadata_record_id=group.source_metadata_record_id,
        media_id=group.media_id,
        origin_type=origin,
        origin_id=None,
        raw_value=raw,
        display_name=display,
        normalized_value=normalized,
        canonical_key=canonical_source_key(canonical) or canonical_source_key(normalized),
        candidate_role=role,
        candidate_status=status,
        extraction_verdict=verdict,
        language_hint=language,
        script_hint=script,
        work_context=work_context,
        work_context_key=canonical_source_key(work_context) if work_context else None,
        parenthetical_base=parenthetical_base,
        parenthetical_context=parenthetical_context,
        extraction_action=action,
        confidence=confidence,
        reason=None,
        rejection_reason=rejection_reason,
        no_name_reason=None,
        evidence_payload={
            "evidence_tags": [normalize_source_text(value) for value in evidence_tags if normalize_source_text(value)],
            "sibling_context": [normalize_source_text(value) for value in sibling_context if normalize_source_text(value)],
            "llm_structured_extraction": True,
            "source_layer_only": True,
            "should_not_create_entity_truth": True,
            "full_popularity_tag_is_alias": False if popularity else None,
            "candidate_status_guard": candidate_status_guard or None,
        },
        candidate_key=key,
    )


def validate_extraction_record(row: Mapping[str, Any], group: SourceCandidateInputGroup) -> tuple[
    RecordVerdictDraft,
    tuple[CandidateDraft, ...],
    tuple[RejectedTagDraft, ...],
    tuple[MetaTagDraft, ...],
    tuple[AmbiguousItemDraft, ...],
]:
    group_key = normalize_source_text(row.get("group_key"))
    if group_key != group.group_key:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:group_key_mismatch")
    if row.get("should_not_create_entity_truth") is False:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:truth_flag")
    verdict = normalize_source_text(row.get("verdict")) or normalize_source_text(row.get("extraction_verdict"))
    verdict = VERDICT_SYNONYMS.get(verdict, verdict)
    if verdict not in ALLOWED_VERDICTS:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:extraction_verdict")
    verdict_reason = normalize_source_text(row.get("error_code")) or verdict
    warnings = [normalize_source_text(value) for value in row.get("extraction_warnings") or [] if normalize_source_text(value)]
    no_name_reason = normalize_source_text(row.get("no_name_reason")) or None
    rejected_summary = _compact_rejected_summary(row)
    confidence_summary = {
        "rejected_summary": rejected_summary,
        "compact_schema": True,
    }

    candidates_raw = row.get("candidates")
    if candidates_raw is None:
        candidates_raw = []
    if not isinstance(candidates_raw, list):
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:malformed_candidate_array")
    if any(not isinstance(item, Mapping) for item in candidates_raw):
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:malformed_candidate_array")
    candidates = tuple(_validate_candidate(item, group, verdict=verdict) for item in candidates_raw)
    if verdict in LLM_CANDIDATE_FOUND_VERDICTS and not candidates:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:no_candidates_after_validation")
    rejected: list[RejectedTagDraft] = []
    for item in row.get("rejected_tags") or []:
        if not isinstance(item, Mapping):
            continue
        raw = normalize_source_text(item.get("raw_value"))
        reason = normalize_source_text(item.get("rejection_reason"))
        if not reason:
            deterministic_rejection = is_meta_or_descriptive_rejection(raw)
            reason = deterministic_rejection[0] if deterministic_rejection else "not_name_like"
        if not raw or reason not in ALLOWED_REJECTION_REASONS:
            raise SourceNameCandidateExtractionError("llm_output_schema_invalid:rejected_tag")
        rejected.append(
            RejectedTagDraft(
                group_key=group.group_key,
                provider=group.provider,
                raw_value=raw,
                normalized_value=normalize_source_text(item.get("normalized_value")) or raw,
                rejection_reason=reason,
                reason=normalize_source_text(item.get("reason")) or reason,
                origin_type=normalize_source_text(item.get("extracted_from")) or "source_tag_observation",
                origin_id=None,
            )
        )
    meta_tags: list[MetaTagDraft] = []
    for item in row.get("meta_tags") or []:
        if not isinstance(item, Mapping):
            continue
        raw = normalize_source_text(item.get("raw_value"))
        if not raw:
            continue
        meta_tags.append(
            MetaTagDraft(
                group_key=group.group_key,
                provider=group.provider,
                raw_value=raw,
                normalized_value=normalize_source_text(item.get("normalized_value")) or raw,
                meta_role=normalize_source_text(item.get("meta_role")) or "meta",
                reason=normalize_source_text(item.get("reason")) or "meta_tag",
                extracted_prefix=normalize_source_text(item.get("extracted_prefix")) or None,
                origin_type=normalize_source_text(item.get("extracted_from")) or None,
                origin_id=None,
            )
        )
    ambiguous: list[AmbiguousItemDraft] = []
    for item in row.get("ambiguous_items") or []:
        if not isinstance(item, Mapping):
            continue
        raw = normalize_source_text(item.get("raw_value"))
        if raw:
            ambiguous.append(
                AmbiguousItemDraft(
                    group_key=group.group_key,
                    provider=group.provider,
                    raw_value=raw,
                    reason=normalize_source_text(item.get("reason")) or "ambiguous_needs_review",
                    origin_type=normalize_source_text(item.get("extracted_from")) or None,
                    origin_id=None,
                )
            )

    hints = deterministic_hints_for_group(group)
    deterministic_candidates = tuple(
        candidate
        for candidate in (_candidate_from_hint(group, hint, verdict=verdict) for hint in hints)
        if candidate is not None
    )
    deterministic_rejected = tuple(
        item for item in (_rejected_from_hint(group, hint) for hint in hints) if item is not None
    )
    deterministic_meta = tuple(item for item in (_meta_from_hint(group, hint) for hint in hints) if item is not None)
    candidates = _dedupe_candidates((*candidates, *deterministic_candidates))
    rejected = list(_dedupe_rejected((*rejected, *deterministic_rejected)))
    meta_tags = list(_dedupe_meta((*meta_tags, *deterministic_meta)))

    name_like_signals = [hint for hint in hints if hint.action in {"parenthetical_split", "popularity_suffix_stripped", "provider_structured_field", "direct_name", "normal_tag_candidate", "ai_model_character_tag"}]
    if not candidates and name_like_signals and verdict in {"no_explicit_name", "rejected_general_only", "rejected_popularity_or_meta_only"}:
        verdict = "ambiguous_needs_review"
        warnings.append("suspicious_no_candidate_with_deterministic_name_like_signals")
        no_name_reason = None
    if candidates and verdict in {
        "no_explicit_name",
        "original_without_explicit_name",
        "metadata_insufficient",
        "provider_not_applicable",
        "rejected_general_only",
        "rejected_popularity_or_meta_only",
    }:
        roles = {candidate.candidate_role for candidate in candidates}
        if len(candidates) > 1:
            verdict = "multiple_candidates_found"
        elif roles & {"character", "person", "alias_like", "unknown_name_like"}:
            verdict = "name_candidate_found"
        elif "work_title" in roles or "source_title" in roles:
            verdict = "work_candidate_found"
        elif "artist_creator" in roles:
            verdict = "artist_candidate_found"
        warnings.append("verdict_promoted_by_candidate_recovery")
        no_name_reason = None
        candidates = tuple(replace(candidate, extraction_verdict=verdict) for candidate in candidates)
    if not candidates and verdict in LLM_CANDIDATE_FOUND_VERDICTS:
        raise SourceNameCandidateExtractionError("llm_output_schema_invalid:no_candidates_after_validation")
    if not candidates and not no_name_reason and verdict in {"no_explicit_name", "original_without_explicit_name", "metadata_insufficient", "provider_not_applicable"}:
        no_name_reason = verdict

    verdict_draft = RecordVerdictDraft(
        group_key=group.group_key,
        provider=group.provider,
        source_metadata_record_id=group.source_metadata_record_id,
        media_id=group.media_id,
        extraction_verdict=verdict,
        verdict_reason=verdict_reason,
        no_name_reason=no_name_reason,
        candidate_count=len(candidates),
        rejected_count=len(rejected),
        meta_count=len(meta_tags),
        ambiguous_count=len(ambiguous),
        confidence_summary=dict(confidence_summary),
        extraction_warnings_json=warnings,
        evidence_payload={
            "source_layer_only": True,
            "should_not_create_entity_truth": True,
            "data_origin": group.data_origin,
            "input_string_count": len(_raw_strings_for_group(group)),
            "deterministic_hint_count": len(hints),
        },
    )
    return verdict_draft, candidates, tuple(rejected), tuple(meta_tags), tuple(ambiguous)


def _dedupe_candidates(items: Sequence[CandidateDraft]) -> tuple[CandidateDraft, ...]:
    by_key: dict[str, CandidateDraft] = {}
    for item in items:
        canonical_value = canonical_source_key(item.canonical_key or item.normalized_value or item.raw_value)
        raw_variant = canonical_source_key(item.raw_value or item.display_name or item.normalized_value)
        dedupe_key = ":".join(
            [
                canonical_source_key(item.group_key),
                canonical_value,
                raw_variant,
                canonical_source_key(item.candidate_role),
                canonical_source_key(item.extraction_action),
            ]
        )
        existing = by_key.get(dedupe_key)
        if existing is None:
            by_key[dedupe_key] = item
            continue
        existing_score = (existing.confidence or 0.0) + (0.05 if existing.candidate_status == "active_candidate" else 0.0)
        item_score = (item.confidence or 0.0) + (0.05 if item.candidate_status == "active_candidate" else 0.0)
        if existing_score < item_score:
            by_key[dedupe_key] = item
    return tuple(by_key.values())


def _dedupe_rejected(items: Sequence[RejectedTagDraft]) -> tuple[RejectedTagDraft, ...]:
    seen: set[tuple[str, str, str]] = set()
    result: list[RejectedTagDraft] = []
    for item in items:
        key = (item.group_key, canonical_source_key(item.raw_value), item.rejection_reason)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def _dedupe_meta(items: Sequence[MetaTagDraft]) -> tuple[MetaTagDraft, ...]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[MetaTagDraft] = []
    for item in items:
        key = (item.group_key, canonical_source_key(item.raw_value), canonical_source_key(item.extracted_prefix))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def _record_from_failure(group: SourceCandidateInputGroup, *, error: str, retryable: bool) -> tuple[RecordVerdictDraft, tuple[CandidateDraft, ...], tuple[RejectedTagDraft, ...], tuple[MetaTagDraft, ...], tuple[AmbiguousItemDraft, ...]]:
    verdict = "extraction_error_retryable" if retryable else "extraction_error_terminal"
    hints = deterministic_hints_for_group(group)
    candidates = tuple(
        candidate
        for candidate in (_candidate_from_hint(group, hint, verdict=verdict) for hint in hints)
        if candidate is not None
    )
    rejected = tuple(item for item in (_rejected_from_hint(group, hint) for hint in hints) if item is not None)
    meta = tuple(item for item in (_meta_from_hint(group, hint) for hint in hints) if item is not None)
    ambiguous = (AmbiguousItemDraft(group.group_key, group.provider, "*", error, origin_type="llm_validation"),)
    return (
        RecordVerdictDraft(
            group_key=group.group_key,
            provider=group.provider,
            source_metadata_record_id=group.source_metadata_record_id,
            media_id=group.media_id,
            extraction_verdict=verdict,
            verdict_reason=error,
            no_name_reason=None,
            candidate_count=len(candidates),
            rejected_count=len(rejected),
            meta_count=len(meta),
            ambiguous_count=len(ambiguous),
            confidence_summary={"valid_llm_output": False},
            extraction_warnings_json=["llm_output_downgraded_to_failure_verdict"],
            evidence_payload={"error": error, "source_layer_only": True, "should_not_create_entity_truth": True},
        ),
        candidates,
        rejected,
        meta,
        ambiguous,
    )


def _is_retryable_llm_error(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if isinstance(exc, LLMTransportError):
        return True
    if isinstance(exc, LLMHTTPStatusError):
        return bool(exc.should_fallback)
    if isinstance(exc, LLMBatchAggregateError):
        return bool(exc.all_fallback_eligible_errors)
    if isinstance(exc, LLMAllProvidersFailed):
        return any(_is_retryable_llm_error(error) for error in (exc.primary_error, exc.fallback_error) if error)
    return False


async def _classify_group_chunk(
    provider: BaseLLMProvider,
    groups: Sequence[SourceCandidateInputGroup],
    *,
    max_tokens: int,
    timeout_seconds: float | None = None,
) -> tuple[list[Mapping[str, Any]], dict[str, Any], dict[str, Any]]:
    messages = extraction_messages(groups)
    input_payload = json.loads(messages[1]["content"])
    try:
        call = provider.complete_json(messages, temperature=0.0, max_tokens=max_tokens)
        payload = await asyncio.wait_for(call, timeout=timeout_seconds) if timeout_seconds else await call
        records = _coerce_record_array(payload, expected_count=len(groups))
        return records, input_payload, {"parsed_response": payload, "repair_strategy": "complete_json"}
    except LLMResponseFormatError:
        call = provider.complete_chat(messages, temperature=0.0, max_tokens=max_tokens)
        content = await asyncio.wait_for(call, timeout=timeout_seconds) if timeout_seconds else await call
        stripped = _extract_json_object(content)
        payload = json.loads(stripped)
        records = _coerce_record_array(payload, expected_count=len(groups))
        return records, input_payload, {"parsed_response": payload, "repair_strategy": "complete_chat_json_object_extract"}


def _extract_json_object(content: str) -> str:
    text_value = content.strip()
    if text_value.startswith("```"):
        lines = text_value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text_value = "\n".join(lines).strip()
    if text_value.startswith("{") and text_value.endswith("}"):
        return text_value
    start = text_value.find("{")
    end = text_value.rfind("}")
    if start >= 0 and end > start:
        return text_value[start : end + 1]
    return text_value


async def extract_groups_with_llm(
    provider: BaseLLMProvider,
    groups: Sequence[SourceCandidateInputGroup],
    *,
    run_id: str,
    run_label: str,
    chunk_size: int = 5,
    retries: int = 1,
    max_tokens: int = 6000,
    timeout_seconds: float | None = None,
    provider_summary: Mapping[str, Any] | None = None,
    cached_records_by_fingerprint: Mapping[str, Mapping[str, Any]] | None = None,
) -> ExtractionResultBundle:
    if chunk_size <= 0:
        raise SourceNameCandidateExtractionError("chunk_size_invalid")
    record_verdicts: list[RecordVerdictDraft] = []
    candidates: list[CandidateDraft] = []
    rejected_tags: list[RejectedTagDraft] = []
    meta_tags: list[MetaTagDraft] = []
    ambiguous_items: list[AmbiguousItemDraft] = []
    llm_inputs: list[dict[str, Any]] = []
    llm_outputs: list[dict[str, Any]] = []
    validation_failures: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    provider_summary = dict(provider_summary or {})
    cached_records_by_fingerprint = dict(cached_records_by_fingerprint or {})
    uncached_groups: list[SourceCandidateInputGroup] = []

    for group in groups:
        fingerprint = llm_cache_fingerprint(group, provider_summary)
        cached = cached_records_by_fingerprint.get(fingerprint)
        if cached is None:
            uncached_groups.append(group)
            continue
        try:
            verdict, group_candidates, group_rejected, group_meta, group_ambiguous = validate_extraction_record(cached, group)
            record_verdicts.append(verdict)
            candidates.extend(group_candidates)
            rejected_tags.extend(group_rejected)
            meta_tags.extend(group_meta)
            ambiguous_items.extend(group_ambiguous)
            llm_outputs.append(
                {
                    "group_key": group.group_key,
                    "cache_fingerprint": fingerprint,
                    "parsed_response": cached,
                    "repair_strategy": "cache_hit",
                }
            )
            counters["cache_hits"] += 1
            counters["valid_records"] += 1
        except SourceNameCandidateExtractionError as exc:
            validation_failures.append(
                {
                    "strategy": "cache_validation_failed",
                    "group_keys": [group.group_key],
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                }
            )
            uncached_groups.append(group)

    chunks = [uncached_groups[index : index + chunk_size] for index in range(0, len(uncached_groups), chunk_size)]

    async def classify_with_repair(chunk: Sequence[SourceCandidateInputGroup], *, strategy: str) -> None:
        last_error: BaseException | None = None
        for attempt in range(retries + 1):
            counters["api_call_attempts"] += 1
            try:
                rows, input_payload, output_payload = await _classify_group_chunk(
                    provider,
                    chunk,
                    max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds,
                )
                fingerprints = {
                    group.group_key: llm_cache_fingerprint(group, provider_summary)
                    for group in chunk
                }
                llm_inputs.append(
                    {
                        **input_payload,
                        "repair_strategy": strategy,
                        "cache_fingerprints": fingerprints,
                    }
                )
                llm_outputs.append(
                    {
                        **output_payload,
                        "repair_strategy": strategy,
                        "cache_fingerprints": fingerprints,
                    }
                )
                for group, row in zip(chunk, rows):
                    verdict, group_candidates, group_rejected, group_meta, group_ambiguous = validate_extraction_record(row, group)
                    record_verdicts.append(verdict)
                    candidates.extend(group_candidates)
                    rejected_tags.extend(group_rejected)
                    meta_tags.extend(group_meta)
                    ambiguous_items.extend(group_ambiguous)
                    counters["valid_records"] += 1
                return
            except (LLMProviderError, SourceNameCandidateExtractionError, json.JSONDecodeError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt < retries:
                    counters["chunk_retries"] += 1
        if last_error is None:
            last_error = SourceNameCandidateExtractionError("llm_extraction_unknown_failure")
        validation_failures.append(
            {
                "strategy": strategy,
                "group_keys": [group.group_key for group in chunk],
                "error_type": type(last_error).__name__,
                "error": str(last_error)[:1000],
            }
        )
        if len(chunk) > 1:
            counters["chunk_split_recoveries"] += 1
            midpoint = max(1, len(chunk) // 2)
            await classify_with_repair(chunk[:midpoint], strategy="split_after_invalid_output")
            await classify_with_repair(chunk[midpoint:], strategy="split_after_invalid_output")
            return
        retryable = _is_retryable_llm_error(last_error)
        verdict, group_candidates, group_rejected, group_meta, group_ambiguous = _record_from_failure(
            chunk[0],
            error=f"{type(last_error).__name__}:{str(last_error)[:500]}",
            retryable=retryable,
        )
        record_verdicts.append(verdict)
        candidates.extend(group_candidates)
        rejected_tags.extend(group_rejected)
        meta_tags.extend(group_meta)
        ambiguous_items.extend(group_ambiguous)
        counters["failed_records_downgraded"] += 1

    for chunk in chunks:
        counters["api_chunks_attempted"] += 1
        await classify_with_repair(chunk, strategy="initial_chunk")

    candidates_tuple = _dedupe_candidates(tuple(candidates))
    summary = build_extraction_summary(
        groups=groups,
        record_verdicts=record_verdicts,
        candidates=candidates_tuple,
        rejected_tags=rejected_tags,
        meta_tags=meta_tags,
        ambiguous_items=ambiguous_items,
        llm_counters=dict(counters),
        validation_failures=validation_failures,
    )
    return ExtractionResultBundle(
        run_id=run_id,
        run_label=run_label,
        groups=tuple(groups),
        record_verdicts=tuple(record_verdicts),
        candidates=candidates_tuple,
        rejected_tags=tuple(_dedupe_rejected(tuple(rejected_tags))),
        meta_tags=tuple(_dedupe_meta(tuple(meta_tags))),
        ambiguous_items=tuple(ambiguous_items),
        llm_inputs=tuple(llm_inputs),
        llm_outputs=tuple(llm_outputs),
        validation_failures=tuple(validation_failures),
        summary=summary,
    )


def run_extraction_sync(
    provider: BaseLLMProvider,
    groups: Sequence[SourceCandidateInputGroup],
    *,
    run_id: str,
    run_label: str,
    chunk_size: int = 5,
    retries: int = 1,
    max_tokens: int = 6000,
    timeout_seconds: float | None = None,
    provider_summary: Mapping[str, Any] | None = None,
    cached_records_by_fingerprint: Mapping[str, Mapping[str, Any]] | None = None,
) -> ExtractionResultBundle:
    return asyncio.run(
        extract_groups_with_llm(
            provider,
            groups,
            run_id=run_id,
            run_label=run_label,
            chunk_size=chunk_size,
            retries=retries,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            provider_summary=provider_summary,
            cached_records_by_fingerprint=cached_records_by_fingerprint,
        )
    )


def build_extraction_summary(
    *,
    groups: Sequence[SourceCandidateInputGroup],
    record_verdicts: Sequence[RecordVerdictDraft],
    candidates: Sequence[CandidateDraft],
    rejected_tags: Sequence[RejectedTagDraft],
    meta_tags: Sequence[MetaTagDraft],
    ambiguous_items: Sequence[AmbiguousItemDraft],
    llm_counters: Mapping[str, Any],
    validation_failures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    unique_strings: set[str] = set()
    for group in groups:
        unique_strings.update(canonical_source_key(value) for value in _raw_strings_for_group(group) if canonical_source_key(value))
    popularity_prefix_count = sum(1 for candidate in candidates if candidate.extraction_action == "popularity_suffix_stripped")
    return {
        "phase": PHASE,
        "extractor_version": EXTRACTOR_VERSION,
        "prompt_version": PROMPT_VERSION,
        "structured_output_schema_version": SCHEMA_VERSION,
        "input": {
            "group_count": len(groups),
            "unique_raw_string_count": len(unique_strings),
            "groups_by_provider": dict(Counter(group.provider for group in groups)),
            "groups_by_data_origin": dict(Counter(group.data_origin for group in groups)),
        },
        "record_verdict_counts": dict(Counter(row.extraction_verdict for row in record_verdicts)),
        "candidate_counts": {
            "total": len(candidates),
            "by_role": dict(Counter(row.candidate_role for row in candidates)),
            "by_status": dict(Counter(row.candidate_status for row in candidates)),
            "by_action": dict(Counter(row.extraction_action for row in candidates)),
            "popularity_prefix_extractions": popularity_prefix_count,
        },
        "rejected_counts": {
            "total": len(rejected_tags),
            "by_reason": dict(Counter(row.rejection_reason for row in rejected_tags)),
        },
        "meta_counts": {
            "total": len(meta_tags),
            "by_role": dict(Counter(row.meta_role for row in meta_tags)),
        },
        "ambiguous_count": len(ambiguous_items),
        "validation_failure_count": len(validation_failures),
        "llm": dict(llm_counters),
        "safety": {
            "source_layer_only": True,
            "entity_write": False,
            "entity_alias_write": False,
            "media_entity_candidate_write": False,
            "local_source_hint_write": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "source_concept_linking": False,
            "image_upload": False,
        },
    }


def fallback_only_provider_from_settings() -> tuple[BaseLLMProvider | None, dict[str, Any]]:
    from ..config import settings

    fallback_enabled = bool(settings.TAG_TRANSLATION_LLM_FALLBACK_ENABLED)
    fallback_key = settings.TAG_TRANSLATION_LLM_FALLBACK_API_KEY
    fallback_model = settings.TAG_TRANSLATION_LLM_FALLBACK_MODEL
    fallback_url = settings.TAG_TRANSLATION_LLM_FALLBACK_BASE_URL
    fallback_provider_type = settings.TAG_TRANSLATION_LLM_FALLBACK_PROVIDER
    summary = {
        "provider_mode": "fallback_only",
        "llm_provider_label": "fallback",
        "uses_primary_model": False,
        "uses_fallback_provider": True,
        "fallback_enabled": fallback_enabled,
        "fallback_provider_type": fallback_provider_type,
        "llm_access_configured": bool(fallback_key and fallback_model and fallback_url),
        "model_label": "fallback_model_configured" if fallback_model else "unknown",
        "model_name_redacted": bool(fallback_model),
        "base_url_redacted": True,
        "llm_access_stored": False,
    }
    if not fallback_enabled:
        return None, {**summary, "unavailable_reason": "fallback_disabled"}
    if fallback_provider_type not in {"openai_compatible", "deepseek"}:
        return None, {**summary, "unavailable_reason": "fallback_provider_not_openai_compatible"}
    if not (fallback_key and fallback_model and fallback_url):
        return None, {**summary, "unavailable_reason": "fallback_config_incomplete"}
    primary_disabled = OpenAICompatibleProvider(
        api_key="",
        model="",
        base_url=fallback_url,
        label="primary_disabled_for_f7a_source_name_candidate_extraction",
    )
    fallback = OpenAICompatibleProvider(
        api_key=fallback_key,
        model=fallback_model,
        base_url=fallback_url,
        label="fallback",
    )
    provider = FallbackProvider(primary_disabled, fallback)
    return (provider if provider.is_available() else None), summary


def primary_openai_provider_from_settings() -> tuple[BaseLLMProvider | None, dict[str, Any]]:
    from ..config import settings

    provider_type = settings.TAG_TRANSLATION_LLM_PROVIDER
    primary_key = settings.TAG_TRANSLATION_LLM_API_KEY
    primary_model = settings.TAG_TRANSLATION_LLM_MODEL
    primary_url = settings.TAG_TRANSLATION_LLM_BASE_URL
    summary = {
        "provider_mode": "primary_openai",
        "llm_provider_label": "primary_openai",
        "uses_primary_model": True,
        "uses_fallback_provider": False,
        "primary_enabled": bool(settings.TAG_TRANSLATION_LLM_ENABLED),
        "primary_provider_type": provider_type,
        "llm_access_configured": bool(primary_key and primary_model and primary_url and settings.TAG_TRANSLATION_LLM_ENABLED),
        "model_label": "primary_model_configured" if primary_model else "unknown",
        "model_name_redacted": bool(primary_model),
        "base_url_redacted": True,
        "llm_access_stored": False,
    }
    if not settings.TAG_TRANSLATION_LLM_ENABLED:
        return None, {**summary, "unavailable_reason": "primary_disabled"}
    if provider_type != "openai_compatible":
        return None, {**summary, "unavailable_reason": "primary_provider_not_openai_compatible"}
    if not (primary_key and primary_model and primary_url):
        return None, {**summary, "unavailable_reason": "primary_config_incomplete"}
    provider = OpenAICompatibleProvider(
        api_key=primary_key,
        model=primary_model,
        base_url=primary_url,
        label="primary_openai",
    )
    return (provider if provider.is_available() else None), summary


def fallback_openai_provider_from_settings() -> tuple[BaseLLMProvider | None, dict[str, Any]]:
    from ..config import settings

    fallback_enabled = bool(settings.TAG_TRANSLATION_LLM_FALLBACK_ENABLED)
    fallback_key = settings.TAG_TRANSLATION_LLM_FALLBACK_API_KEY
    fallback_model = settings.TAG_TRANSLATION_LLM_FALLBACK_MODEL
    fallback_url = settings.TAG_TRANSLATION_LLM_FALLBACK_BASE_URL
    fallback_provider_type = settings.TAG_TRANSLATION_LLM_FALLBACK_PROVIDER
    summary = {
        "provider_mode": "fallback",
        "llm_provider_label": "fallback",
        "uses_primary_model": False,
        "uses_fallback_provider": True,
        "fallback_enabled": fallback_enabled,
        "fallback_provider_type": fallback_provider_type,
        "llm_access_configured": bool(fallback_key and fallback_model and fallback_url),
        "model_label": "fallback_model_configured" if fallback_model else "unknown",
        "model_name_redacted": bool(fallback_model),
        "base_url_redacted": True,
        "llm_access_stored": False,
    }
    if not fallback_enabled:
        return None, {**summary, "unavailable_reason": "fallback_disabled"}
    if fallback_provider_type not in {"openai_compatible", "deepseek"}:
        return None, {**summary, "unavailable_reason": "fallback_provider_not_openai_compatible"}
    if not (fallback_key and fallback_model and fallback_url):
        return None, {**summary, "unavailable_reason": "fallback_config_incomplete"}
    provider = OpenAICompatibleProvider(
        api_key=fallback_key,
        model=fallback_model,
        base_url=fallback_url,
        label="fallback",
    )
    return (provider if provider.is_available() else None), summary


def _source_record_scan_limit(*, max_records: int, max_unique_strings: int) -> int:
    # Bounded over-read lets eligibility/cap filters work without materializing the whole table.
    soft_limit = max(max_records * 20, max_records + 100, max_unique_strings)
    return max(max_records, min(soft_limit, 5000))


def collect_source_candidate_input_groups(
    db: Session,
    *,
    max_records: int,
    max_unique_strings: int,
    include_media_tag_only_groups: bool = True,
) -> tuple[list[SourceCandidateInputGroup], dict[str, Any]]:
    if max_records <= 0:
        raise SourceNameCandidateExtractionError("max_records_invalid")
    if max_unique_strings <= 0:
        raise SourceNameCandidateExtractionError("max_unique_strings_invalid")

    source_record_scan_limit = _source_record_scan_limit(
        max_records=max_records,
        max_unique_strings=max_unique_strings,
    )
    record_rows = (
        db.query(SourceMetadataRecord, Media)
        .outerjoin(Media, SourceMetadataRecord.media_id == Media.id)
        .filter(SourceMetadataRecord.status == "observed")
        .order_by(
            SourceMetadataRecord.data_type_label.desc(),
            SourceMetadataRecord.provider.asc(),
            SourceMetadataRecord.id.asc(),
        )
        .limit(source_record_scan_limit)
        .yield_per(100)
    )
    groups: list[SourceCandidateInputGroup] = []
    unique_strings: set[str] = set()
    eligibility_counts: Counter[str] = Counter()
    excluded_counts: Counter[str] = Counter()
    source_metadata_records_scanned = 0

    for record, media in record_rows:
        source_metadata_records_scanned += 1
        eligible, eligibility_reason, eligibility_payload = media_llm_eligibility(media)
        eligibility_counts[eligibility_reason] += 1
        if not eligible:
            excluded_counts[eligibility_reason] += 1
            continue
        tags = [
            {
                "id": row.id,
                "observation_key": row.observation_key,
                "raw_tag": row.raw_tag,
                "normalized_tag": row.normalized_tag,
                "canonical_tag_key": row.canonical_tag_key,
                "source_tag_kind": row.source_tag_kind,
                "source_category_raw": row.source_category_raw,
                "language_hint": row.language_hint,
                "confidence": row.confidence,
            }
            for row in db.query(SourceTagObservation)
            .filter(SourceTagObservation.source_metadata_record_id == record.id)
            .order_by(SourceTagObservation.order_index.asc().nullslast(), SourceTagObservation.id.asc())
            .all()
        ]
        names = [
            {
                "id": row.id,
                "observation_key": row.observation_key,
                "raw_name": row.raw_name,
                "normalized_name": row.normalized_name,
                "canonical_name_key": row.canonical_name_key,
                "name_role": row.name_role,
                "source_field": row.source_field,
                "language_hint": row.language_hint,
                "script_hint": row.script_hint,
                "confidence": row.confidence,
                "requires_review": row.requires_review,
            }
            for row in db.query(SourceNameObservation)
            .filter(SourceNameObservation.source_metadata_record_id == record.id)
            .order_by(SourceNameObservation.id.asc())
            .all()
        ]
        assertions = [
            {
                "id": row.id,
                "assertion_key": row.assertion_key,
                "raw_input": row.raw_input,
                "normalized_input": row.normalized_input,
                "canonical_name_key": row.canonical_name_key,
                "asserted_name": row.asserted_name,
                "asserted_role": row.asserted_role,
                "status": row.status,
                "confidence": row.confidence,
                "confidence_score": row.confidence_score,
                "requires_review": row.requires_review,
            }
            for row in db.query(SourceSearchableNameAssertion)
            .filter(SourceSearchableNameAssertion.source_metadata_record_id == record.id)
            .order_by(SourceSearchableNameAssertion.id.asc())
            .all()
        ]
        media_tags = _media_tag_rows(db, record.media_id) if record.media_id else []
        alias_keys = {canonical_source_key(row.get("canonical_name_key")) for row in [*names, *assertions] if canonical_source_key(row.get("canonical_name_key"))}
        aliases = _alias_rows_for_keys(db, alias_keys)
        group = SourceCandidateInputGroup(
            group_key=f"source_record:{record.id}",
            provider=record.provider,
            source_metadata_record_id=record.id,
            media_id=record.media_id,
            data_type_label=record.data_type_label,
            metadata_kind=record.metadata_kind,
            content_class=eligibility_payload["content_class"],
            content_class_reviewed=bool(eligibility_payload["content_class_reviewed"]),
            content_class_locked=bool(eligibility_payload["content_class_locked"]),
            eligibility_status="eligible",
            eligibility_reason=eligibility_reason,
            title=record.title,
            caption=_caption_from_raw_metadata(record.raw_metadata_json),
            artist_name=record.artist_name,
            source_work_id_present=bool(record.source_work_id),
            source_url_present=bool(record.source_url),
            tags=tuple(tags),
            source_names=tuple(names),
            source_assertions=tuple(assertions),
            alias_candidates=tuple(aliases),
            media_tags=tuple(media_tags),
            data_origin=_data_origin(record.data_type_label),
        )
        group_strings = {canonical_source_key(value) for value in _raw_strings_for_group(group) if canonical_source_key(value)}
        if not group_strings and not record.title and not record.artist_name:
            continue
        if len(unique_strings | group_strings) > max_unique_strings and groups:
            break
        unique_strings.update(group_strings)
        groups.append(group)
        if len(groups) >= max_records:
            break

    if include_media_tag_only_groups and len(groups) < max_records and len(unique_strings) < max_unique_strings:
        for group, eligibility_reason in _media_tag_only_groups(db, limit=max_records - len(groups)):
            eligibility_counts[eligibility_reason] += 1
            group_strings = {canonical_source_key(value) for value in _raw_strings_for_group(group) if canonical_source_key(value)}
            if not group_strings:
                continue
            if len(unique_strings | group_strings) > max_unique_strings and groups:
                break
            unique_strings.update(group_strings)
            groups.append(group)

    return groups, {
        "source_metadata_records_available": db.query(func.count(SourceMetadataRecord.id)).scalar() or 0,
        "source_metadata_record_scan_limit": source_record_scan_limit,
        "source_metadata_records_scanned": source_metadata_records_scanned,
        "groups_collected": len(groups),
        "eligible_groups_collected": len(groups),
        "eligibility_counts": dict(eligibility_counts),
        "excluded_counts": dict(excluded_counts),
        "approved_content_classes": sorted(APPROVED_LLM_CONTENT_CLASSES | {ContentClassEnum.illustration.value}),
        "approved_illustration_rule": "illustration requires content_class_reviewed or content_class_locked",
        "unique_raw_string_count": len(unique_strings),
        "groups_by_provider": dict(Counter(group.provider for group in groups)),
        "groups_by_data_origin": dict(Counter(group.data_origin for group in groups)),
        "max_records": max_records,
        "max_unique_strings": max_unique_strings,
    }


def _caption_from_raw_metadata(raw_metadata: Any) -> str | None:
    if not isinstance(raw_metadata, Mapping):
        return None
    for key in ("caption", "description", "commentary", "body"):
        value = normalize_source_text(raw_metadata.get(key))
        if value:
            return value[:1000]
    return None


def _data_origin(data_type_label: str | None) -> str:
    label = normalize_source_text(data_type_label)
    if label == "real_live_or_local_provider_data":
        return "real_dev_db"
    if label == "existing_artifact_or_report_derived":
        return "artifact_derived"
    return "fixture_or_test"


def _media_tag_rows(db: Session, media_id: int | None) -> list[dict[str, Any]]:
    if not media_id:
        return []
    rows = (
        db.query(Tag, blombooru_media_tags.c.source, blombooru_media_tags.c.confidence, blombooru_media_tags.c.is_suggestion)
        .join(blombooru_media_tags, blombooru_media_tags.c.tag_id == Tag.id)
        .filter(blombooru_media_tags.c.media_id == media_id)
        .filter(Tag.category.in_([TagCategoryEnum.character, TagCategoryEnum.copyright, TagCategoryEnum.artist]))
        .order_by(Tag.category.asc(), Tag.name.asc())
        .limit(30)
        .all()
    )
    return [
        {
            "tag_id": tag.id,
            "name": tag.name,
            "category": tag.category.value if hasattr(tag.category, "value") else str(tag.category),
            "source": source,
            "confidence": confidence,
            "is_suggestion": is_suggestion,
        }
        for tag, source, confidence, is_suggestion in rows
    ]


def _media_tag_only_groups(db: Session, *, limit: int) -> list[tuple[SourceCandidateInputGroup, str]]:
    rows = (
        db.query(Media)
        .join(blombooru_media_tags, blombooru_media_tags.c.media_id == Media.id)
        .join(Tag, Tag.id == blombooru_media_tags.c.tag_id)
        .filter(Tag.category.in_([TagCategoryEnum.character, TagCategoryEnum.copyright, TagCategoryEnum.artist]))
        .group_by(Media.id)
        .order_by(Media.id.asc())
        .limit(limit * 10 if limit > 0 else 0)
        .all()
    )
    groups: list[tuple[SourceCandidateInputGroup, str]] = []
    for media in rows:
        eligible, eligibility_reason, eligibility_payload = media_llm_eligibility(media)
        if not eligible:
            continue
        media_id = media.id
        media_tags = _media_tag_rows(db, media_id)
        if not media_tags:
            continue
        groups.append(
            (
                SourceCandidateInputGroup(
                    group_key=f"media_tags:{media_id}",
                    provider="local_media_tags",
                    media_id=media_id,
                    content_class=eligibility_payload["content_class"],
                    content_class_reviewed=bool(eligibility_payload["content_class_reviewed"]),
                    content_class_locked=bool(eligibility_payload["content_class_locked"]),
                    eligibility_status="eligible",
                    eligibility_reason=eligibility_reason,
                    media_tags=tuple(media_tags),
                    data_origin="real_dev_db",
                ),
                eligibility_reason,
            )
        )
        if len(groups) >= limit:
            break
    return groups


def _alias_rows_for_keys(db: Session, keys: set[str]) -> list[dict[str, Any]]:
    if not keys:
        return []
    rows = (
        db.query(SourceNameAliasCandidate)
        .filter(
            or_(
                SourceNameAliasCandidate.source_name_key.in_(keys),
                SourceNameAliasCandidate.target_name_key.in_(keys),
            )
        )
        .order_by(SourceNameAliasCandidate.id.asc())
        .limit(30)
        .all()
    )
    return [
        {
            "id": row.id,
            "source_name_key": row.source_name_key,
            "target_name_key": row.target_name_key,
            "source_display_name": row.source_display_name,
            "target_display_name": row.target_display_name,
            "relation_type": row.relation_type,
            "evidence_source": row.evidence_source,
            "confidence": row.confidence,
            "status": row.status,
            "requires_review": row.requires_review,
        }
        for row in rows
    ]


def table_counts(db: Session, tables: Sequence[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for table in tables:
        try:
            result[table] = int(db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        except Exception:
            result[table] = -1
    return result


def persist_extraction_bundle(
    db: Session,
    bundle: ExtractionResultBundle,
    *,
    apply: bool,
    provider_summary: Mapping[str, Any] | None = None,
    input_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    before_allowed = table_counts(db, ALLOWED_WRITE_TABLES)
    before_forbidden = table_counts(db, FORBIDDEN_TRUTH_TABLES)
    if not apply:
        return {
            "apply": False,
            "planned": {
                "runs": 1,
                "record_verdicts": len(bundle.record_verdicts),
                "candidates": len(bundle.candidates),
            },
            "allowed_table_row_counts_before": before_allowed,
            "forbidden_table_row_counts_before": before_forbidden,
            "forbidden_truth_table_write_count": 0,
        }

    run_row = db.query(SourceNameCandidateExtractionRun).filter_by(run_id=bundle.run_id).one_or_none()
    now = datetime.now(timezone.utc)
    if run_row is None:
        run_row = SourceNameCandidateExtractionRun(
            run_id=bundle.run_id,
            run_label=bundle.run_label,
            extractor_version=EXTRACTOR_VERSION,
            prompt_version=PROMPT_VERSION,
            structured_output_schema_version=SCHEMA_VERSION,
            mode="apply_db",
            status="completed",
            input_scope_json=dict(input_scope or {}),
            summary_json=bundle.summary,
            provider_summary_json=dict(provider_summary or {}),
            started_at=now,
            finished_at=now,
        )
        db.add(run_row)
        db.flush()
    else:
        run_row.run_label = bundle.run_label
        run_row.mode = "apply_db"
        run_row.status = "completed"
        run_row.input_scope_json = dict(input_scope or {})
        run_row.summary_json = bundle.summary
        run_row.provider_summary_json = dict(provider_summary or {})
        run_row.finished_at = now
        db.flush()

    verdict_by_group: dict[str, SourceNameCandidateRecordVerdict] = {}
    for verdict in bundle.record_verdicts:
        row = (
            db.query(SourceNameCandidateRecordVerdict)
            .filter_by(extraction_run_id=run_row.id, group_key=verdict.group_key)
            .one_or_none()
        )
        values = {
            "source_metadata_record_id": verdict.source_metadata_record_id,
            "media_id": verdict.media_id,
            "provider": verdict.provider,
            "extraction_verdict": verdict.extraction_verdict,
            "verdict_reason": verdict.verdict_reason,
            "no_name_reason": verdict.no_name_reason,
            "candidate_count": verdict.candidate_count,
            "rejected_count": verdict.rejected_count,
            "meta_count": verdict.meta_count,
            "ambiguous_count": verdict.ambiguous_count,
            "confidence_summary": verdict.confidence_summary,
            "extraction_warnings_json": verdict.extraction_warnings_json,
            "evidence_payload": verdict.evidence_payload,
            "status": "observed",
        }
        if row is None:
            row = SourceNameCandidateRecordVerdict(
                extraction_run_id=run_row.id,
                group_key=verdict.group_key,
                **values,
            )
            db.add(row)
            db.flush()
        else:
            for key, value in values.items():
                setattr(row, key, value)
        verdict_by_group[verdict.group_key] = row

    inserted = 0
    updated = 0
    active_keys: set[str] = set()
    processed_group_keys = {verdict.group_key for verdict in bundle.record_verdicts}
    for candidate in bundle.candidates:
        persisted_candidate_key = _run_scoped_candidate_key(bundle.run_id, candidate.candidate_key)
        active_keys.add(persisted_candidate_key)
        row = (
            db.query(SourceNameCandidate)
            .filter_by(extraction_run_id=run_row.id, candidate_key=persisted_candidate_key)
            .one_or_none()
        )
        values = {
            "extraction_run_id": run_row.id,
            "record_verdict_id": verdict_by_group.get(candidate.group_key).id if candidate.group_key in verdict_by_group else None,
            "source_metadata_record_id": candidate.source_metadata_record_id,
            "media_id": candidate.media_id,
            "provider": candidate.provider,
            "group_key": candidate.group_key,
            "origin_type": candidate.origin_type,
            "origin_id": candidate.origin_id,
            "raw_value": candidate.raw_value,
            "display_name": candidate.display_name,
            "normalized_value": candidate.normalized_value,
            "canonical_key": candidate.canonical_key,
            "candidate_role": candidate.candidate_role,
            "candidate_status": candidate.candidate_status,
            "extraction_verdict": candidate.extraction_verdict,
            "language_hint": candidate.language_hint,
            "script_hint": candidate.script_hint,
            "work_context": candidate.work_context,
            "work_context_key": candidate.work_context_key,
            "parenthetical_base": candidate.parenthetical_base,
            "parenthetical_context": candidate.parenthetical_context,
            "extraction_action": candidate.extraction_action,
            "confidence": candidate.confidence,
            "reason": candidate.reason,
            "rejection_reason": candidate.rejection_reason,
            "no_name_reason": candidate.no_name_reason,
            "evidence_payload": {
                **candidate.evidence_payload,
                "logical_candidate_key": candidate.candidate_key,
                "run_scoped_candidate_key": True,
            },
            "extractor_version": EXTRACTOR_VERSION,
            "status": "active",
        }
        if row is None:
            row = SourceNameCandidate(candidate_key=persisted_candidate_key, **values)
            db.add(row)
            inserted += 1
        else:
            for key, value in values.items():
                setattr(row, key, value)
            updated += 1
    db.flush()

    stale_query = (
        db.query(SourceNameCandidate)
        .filter(SourceNameCandidate.extraction_run_id == run_row.id)
        .filter(SourceNameCandidate.group_key.in_(processed_group_keys))
    )
    if active_keys:
        stale_query = stale_query.filter(~SourceNameCandidate.candidate_key.in_(active_keys))
    stale_rows = stale_query.all()
    for stale in stale_rows:
        stale.status = "superseded"
        stale.candidate_status = "rejected"
    db.commit()

    after_allowed = table_counts(db, ALLOWED_WRITE_TABLES)
    after_forbidden = table_counts(db, FORBIDDEN_TRUTH_TABLES)
    forbidden_deltas = {key: after_forbidden[key] - before_forbidden.get(key, 0) for key in after_forbidden}
    return {
        "apply": True,
        "run_db_id": run_row.id,
        "inserted": {"SourceNameCandidate": inserted},
        "updated": {"SourceNameCandidate": updated},
        "record_verdicts": len(bundle.record_verdicts),
        "stale_candidates_superseded": len(stale_rows),
        "allowed_tables": list(ALLOWED_WRITE_TABLES),
        "allowed_table_row_counts_before": before_allowed,
        "allowed_table_row_counts_after": after_allowed,
        "allowed_table_row_deltas": {key: after_allowed[key] - before_allowed.get(key, 0) for key in after_allowed},
        "forbidden_tables": list(FORBIDDEN_TRUTH_TABLES),
        "forbidden_table_row_counts_before": before_forbidden,
        "forbidden_table_row_counts_after": after_forbidden,
        "forbidden_table_row_deltas": forbidden_deltas,
        "forbidden_truth_table_write_count": sum(1 for delta in forbidden_deltas.values() if delta != 0),
    }
