"""Provider-neutral source metadata, tag, and name registry helpers.

The F5 registry layer stores source observations and searchable name keys. It
is not an Entity, EntityAlias, ProviderCache, MediaEntityCandidate, or confirmed
assignment path.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from ..models import (
    SourceMetadataEvidence,
    SourceMetadataRecord,
    SourceNameAliasCandidate,
    SourceNameObservation,
    SourceNameRegistry,
    SourceSearchableNameAssertion,
    SourceTagObservation,
    SourceTagRegistry,
)

NAME_ROLES = frozenset({"character", "person", "artist", "creator", "work_title", "unknown_name"})
PERSON_LIKE_ROLES = frozenset({"character", "person", "artist", "creator"})
TAG_CATEGORIES_TO_NAME_ROLE = {
    "1": "artist",
    "3": "work_title",
    "4": "character",
    "character": "character",
    "artist": "artist",
    "copyright": "work_title",
    "work": "work_title",
}
RAW_SIGNAL_FLAG_NAMES = (
    "has_raw_artist_signal",
    "has_raw_creator_signal",
    "has_raw_character_signal",
    "has_raw_person_signal",
    "has_raw_work_title_signal",
    "has_raw_parenthetical_character_work_signal",
    "has_raw_booru_character_category_signal",
    "has_raw_booru_artist_category_signal",
    "has_raw_saucenao_work_or_copyright_signal",
)
SOURCE_FIELD_SPECS = (
    ("artist_name", "artist", 0.95, False),
    ("artist", "artist", 0.94, False),
    ("creator", "creator", 0.94, False),
    ("author", "creator", 0.92, False),
    ("title", "work_title", 0.72, True),
    ("work", "work_title", 0.84, True),
    ("material", "work_title", 0.82, True),
    ("copyright", "work_title", 0.84, True),
    ("work_or_copyright", "work_title", 0.86, True),
    ("characters", "character", 0.9, True),
    ("character", "character", 0.9, True),
    ("person", "person", 0.86, True),
)
POPULARITY_TAG_RE = re.compile(r"(?i)(users|bookmarks|views|入り|閲覧|收藏|users入り)")


@dataclass(frozen=True)
class SourceTagDraft:
    provider: str
    provider_record_key: str
    observation_key: str
    raw_tag: str
    normalized_tag: str
    canonical_tag_key: str
    source_tag_kind: str = "provider_tag"
    source_category_raw: str | None = None
    language_hint: str | None = None
    confidence: float | None = None
    order_index: int | None = None
    taxonomy_kb_id: int | None = None
    status: str = "observed"


@dataclass(frozen=True)
class SourceNameDraft:
    provider: str
    provider_record_key: str
    observation_key: str
    raw_name: str
    normalized_name: str
    canonical_name_key: str
    name_role: str
    source_field: str
    media_id: int | None = None
    source_work_id: str | None = None
    source_page_index: int | None = None
    language_hint: str | None = None
    script_hint: str | None = None
    confidence: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    requires_review: bool = True
    status: str = "observed"


@dataclass(frozen=True)
class SourceMetadataDraft:
    provider: str
    provider_record_key: str
    provider_run_id: str | None = None
    run_label: str | None = None
    media_id: int | None = None
    source_work_id: str | None = None
    source_page_index: int | None = None
    source_url: str | None = None
    title: str | None = None
    artist_name: str | None = None
    artist_id: str | None = None
    confidence: float | None = None
    similarity: float | None = None
    metadata_kind: str = "provider_metadata"
    data_type_label: str = "fixture_or_mock"
    raw_metadata_json: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "observed"
    retrieved_at: datetime | None = None
    signal_roles: tuple[str, ...] = ()
    applicability_status: str = "not_applicable_no_person_signal"
    raw_name_signal_flags: dict[str, bool] = field(default_factory=dict)
    no_applicable_name_signal_reason: str | None = "no_raw_name_signal"


@dataclass(frozen=True)
class SourceNameAliasDraft:
    source_name_key: str
    target_name_key: str
    source_display_name: str
    target_display_name: str
    relation_type: str
    evidence_source: str
    evidence_payload: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    status: str = "candidate"
    requires_review: bool = True


@dataclass(frozen=True)
class SourceMetadataEvidenceDraft:
    provider: str
    provider_record_key: str
    evidence_key: str
    observation_type: str
    observation_key: str | None
    evidence_kind: str
    evidence_strength: str
    provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "staged"


@dataclass(frozen=True)
class SourceSearchableNameAssertionDraft:
    provider: str
    provider_record_key: str
    assertion_key: str
    raw_input: str
    normalized_input: str
    canonical_name_key: str
    asserted_name: str | None
    asserted_role: str
    status: str
    confidence: str
    structured_output_schema_version: str
    source_tag_observation_key: str | None = None
    source_name_observation_key: str | None = None
    confidence_score: float | None = None
    evidence_sources_json: dict[str, Any] = field(default_factory=dict)
    model_name: str | None = None
    prompt_version: str | None = None
    reasoning_summary_private: str | None = None
    provenance_summary: dict[str, Any] = field(default_factory=dict)
    requires_review: bool = True


@dataclass(frozen=True)
class SourceTagRegistryDraft:
    provider_scope: str
    normalized_tag: str
    canonical_tag_key: str
    raw_variants_json: list[str]
    seen_count: int
    example_provider: str | None
    example_provider_record_key: str | None
    taxonomy_status: str = "unclassified"
    governance_status: str = "candidate"


@dataclass(frozen=True)
class SourceNameRegistryDraft:
    canonical_name_key: str
    primary_display_name: str
    normalized_display_name: str
    raw_variants_json: list[str]
    provider_coverage_json: dict[str, int]
    role_distribution_json: dict[str, int]
    seen_count: int
    governance_status: str = "candidate"
    manual_override_status: str = "none"
    notes: str | None = None


@dataclass(frozen=True)
class SourceRegistryBundle:
    metadata_records: tuple[SourceMetadataDraft, ...]
    tag_observations: tuple[SourceTagDraft, ...]
    tag_registry: tuple[SourceTagRegistryDraft, ...]
    name_observations: tuple[SourceNameDraft, ...]
    name_registry: tuple[SourceNameRegistryDraft, ...]
    alias_candidates: tuple[SourceNameAliasDraft, ...]
    evidence: tuple[SourceMetadataEvidenceDraft, ...]
    record_statuses: dict[str, str]


@dataclass(frozen=True)
class CuratedNameMapping:
    source_name: str
    target_name: str
    relation_type: str = "curated_alias"
    name_role: str = "unknown_name"
    candidate_namespace: str = "source_name"
    confidence: float = 0.95
    source: str = "curated_mapping"
    notes: str | None = None


def normalize_source_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def canonical_source_key(value: Any) -> str:
    text = normalize_source_text(value).casefold()
    text = re.sub(r"[\s/／・·,，|]+", "_", text)
    text = re.sub(r"[^\w:()+.-]+", "_", text, flags=re.UNICODE)
    return re.sub(r"_+", "_", text).strip("_")


def provider_record_lookup_key(provider: str, provider_record_key: str) -> str:
    return f"{canonical_source_key(provider)}::{normalize_source_text(provider_record_key)}"


def language_and_script_hint(value: str) -> tuple[str | None, str | None]:
    text = normalize_source_text(value)
    if not text:
        return None, None
    has_latin = any("a" <= char.casefold() <= "z" for char in text)
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in text)
    has_kana = any("\u3040" <= char <= "\u30ff" for char in text)
    has_hangul = any("\uac00" <= char <= "\ud7af" for char in text)
    if has_kana:
        return "ja", "kana_or_japanese"
    if has_hangul:
        return "ko", "hangul"
    if has_cjk and has_latin:
        return "mixed", "cjk_latin"
    if has_cjk:
        return "cjk", "cjk"
    if has_latin:
        return "latin", "latin"
    return None, "other"


def parse_parenthetical_name(value: str) -> tuple[str, str] | None:
    normalized = normalize_source_text(value)
    match = re.match(r"^(.+?)\(([^()]+)\)$", normalized)
    if not match:
        return None
    outer = normalize_source_text(match.group(1))
    inner = normalize_source_text(match.group(2))
    if not outer or not inner:
        return None
    return outer, inner


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _as_text_list(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        if isinstance(item, Mapping):
            text = item.get("name") or item.get("tag") or item.get("label") or item.get("value")
        else:
            text = item
        if not isinstance(text, str):
            continue
        normalized = normalize_source_text(text)
        if normalized:
            result.append(normalized)
    return result


def _split_provider_tag_string(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [item for item in re.split(r"\s+", value.strip()) if item]


def _tag_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    values = payload.get("tags")
    if values is None:
        values = payload.get("labels")
    if values is None:
        values = payload.get("keywords")
    for index, item in enumerate(_as_list(values)):
        if isinstance(item, Mapping):
            raw = item.get("name") or item.get("tag") or item.get("label") or item.get("value")
            category = item.get("category") or item.get("source_category_raw")
            kind = item.get("kind") or item.get("source_tag_kind") or "provider_tag"
            confidence = item.get("confidence")
        else:
            raw = item
            category = None
            kind = "provider_tag"
            confidence = None
        if not isinstance(raw, str):
            continue
        raw_text = normalize_source_text(raw)
        if raw_text:
            rows.append(
                {
                    "raw_tag": raw_text,
                    "source_category_raw": normalize_source_text(category) or None,
                    "source_tag_kind": normalize_source_text(kind) or "provider_tag",
                    "confidence": _float_or_none(confidence),
                    "order_index": index,
                }
            )
    native_booru_fields = (
        ("tag_string_artist", "artist"),
        ("tag_string_copyright", "copyright"),
        ("tag_string_character", "character"),
        ("tag_string_general", "general"),
        ("tag_string_meta", "meta"),
    )
    next_index = len(rows)
    for field_name, category in native_booru_fields:
        for raw in _split_provider_tag_string(payload.get(field_name)):
            raw_text = normalize_source_text(raw)
            if raw_text:
                rows.append(
                    {
                        "raw_tag": raw_text,
                        "source_category_raw": category,
                        "source_tag_kind": field_name,
                        "confidence": None,
                        "order_index": next_index,
                    }
                )
                next_index += 1
    return rows


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _source_field_name(provider: str, field_name: str) -> str:
    if provider == "pixiv" and field_name == "artist_name":
        return "pixiv_user_metadata"
    if provider == "pixiv" and field_name == "title":
        return "pixiv_title"
    if provider == "saucenao" and field_name in {"artist", "creator", "author"}:
        return f"saucenao_{field_name}"
    if provider == "saucenao" and field_name in {"title", "work", "material", "copyright"}:
        return "saucenao_title"
    if provider == "saucenao" and field_name == "work_or_copyright":
        return "saucenao_work_or_copyright"
    return f"{provider}_{field_name}"


def _source_title_only_fields(payload: Mapping[str, Any]) -> set[str]:
    return {
        canonical_source_key(value)
        for value in _as_text_list(
            payload.get("source_title_only_fields")
            or payload.get("_source_title_only_fields")
        )
        if canonical_source_key(value)
    }


def raw_applicable_name_signal_summary(
    payload: Mapping[str, Any],
    *,
    provider: str,
    tag_rows: Sequence[Mapping[str, Any]] | None = None,
    tag_category_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    flags = {name: False for name in RAW_SIGNAL_FLAG_NAMES}
    roles: set[str] = set()
    tag_rows = list(tag_rows if tag_rows is not None else _tag_rows(payload))
    tag_category_map = dict(tag_category_map or {})
    source_title_only_fields = _source_title_only_fields(payload)

    def mark(role: str, *flag_names: str) -> None:
        if role in NAME_ROLES:
            roles.add(role)
        for flag_name in flag_names:
            flags[flag_name] = True
        if role in PERSON_LIKE_ROLES:
            flags["has_raw_person_signal"] = True
        if role == "work_title":
            flags["has_raw_work_title_signal"] = True

    for field_name, role, _confidence, _requires_review in SOURCE_FIELD_SPECS:
        if _extraction_field_disabled(source_title_only_fields, provider=provider, field_name=field_name):
            continue
        if not any(canonical_source_key(value) for value in _as_text_list(payload.get(field_name))):
            continue
        if field_name in {"artist_name", "artist"}:
            mark(role, "has_raw_artist_signal")
        elif field_name in {"creator", "author"}:
            mark(role, "has_raw_creator_signal")
        elif field_name in {"characters", "character"}:
            mark(role, "has_raw_character_signal")
        elif field_name == "person":
            mark(role, "has_raw_person_signal")
        elif field_name == "work_or_copyright" and provider == "saucenao":
            mark(role, "has_raw_saucenao_work_or_copyright_signal")
        else:
            mark(role)

    for tag in tag_rows:
        raw_tag = normalize_source_text(tag.get("raw_tag"))
        key = canonical_source_key(raw_tag)
        category = tag.get("source_category_raw") or tag_category_map.get(key)
        mapped_role = TAG_CATEGORIES_TO_NAME_ROLE.get(str(category or "").casefold())
        if mapped_role == "artist":
            mark("artist", "has_raw_artist_signal", "has_raw_booru_artist_category_signal")
        elif mapped_role == "character":
            mark("character", "has_raw_character_signal", "has_raw_booru_character_category_signal")
        elif mapped_role == "work_title":
            mark("work_title")
        if provider == "pixiv" and parse_parenthetical_name(raw_tag):
            mark("character", "has_raw_character_signal", "has_raw_parenthetical_character_work_signal")
            mark("work_title", "has_raw_parenthetical_character_work_signal")

    if roles:
        reason = None
    elif tag_rows or any(_as_text_list(payload.get(key)) for key in ("labels", "keywords")):
        reason = "no_applicable_name_signal_in_raw_tags_or_labels"
    else:
        reason = "no_raw_tags_or_name_fields"
    return {
        **flags,
        "raw_signal_roles": tuple(sorted(roles)),
        "no_applicable_name_signal_reason": reason,
    }


def _disabled_name_extraction_fields(payload: Mapping[str, Any]) -> set[str]:
    return {
        canonical_source_key(value)
        for value in _as_text_list(
            payload.get("disable_name_extraction_fields")
            or payload.get("_disable_name_extraction_fields")
        )
        if canonical_source_key(value)
    }


def _extraction_field_disabled(
    disabled_fields: set[str],
    *,
    provider: str,
    field_name: str,
    source_field_name: str | None = None,
) -> bool:
    source_name = source_field_name or _source_field_name(provider, field_name)
    return (
        canonical_source_key(field_name) in disabled_fields
        or canonical_source_key(source_name) in disabled_fields
    )


def _name_draft(
    *,
    provider: str,
    provider_record_key: str,
    raw_name: str,
    name_role: str,
    source_field: str,
    media_id: int | None,
    source_work_id: str | None,
    source_page_index: int | None,
    confidence: float | None,
    provenance: Mapping[str, Any],
    requires_review: bool,
    suffix: str,
) -> SourceNameDraft | None:
    normalized = normalize_source_text(raw_name)
    key = canonical_source_key(normalized)
    if not normalized or not key:
        return None
    language, script = language_and_script_hint(normalized)
    role = name_role if name_role in NAME_ROLES else "unknown_name"
    return SourceNameDraft(
        provider=provider,
        provider_record_key=provider_record_key,
        observation_key=f"{provider_record_key}:name:{source_field}:{role}:{key}:{suffix}",
        raw_name=normalized,
        normalized_name=normalized,
        canonical_name_key=key,
        name_role=role,
        source_field=source_field,
        media_id=media_id,
        source_work_id=source_work_id,
        source_page_index=source_page_index,
        language_hint=language,
        script_hint=script,
        confidence=confidence,
        provenance=dict(provenance),
        requires_review=requires_review,
    )


def _add_name_once(rows: list[SourceNameDraft], row: SourceNameDraft | None) -> None:
    if row is None:
        return
    if not any(existing.observation_key == row.observation_key for existing in rows):
        rows.append(row)


def _relation(
    *,
    source_name: str,
    target_name: str,
    relation_type: str,
    evidence_source: str,
    confidence: float,
    evidence_payload: Mapping[str, Any],
    requires_review: bool = True,
) -> SourceNameAliasDraft | None:
    source_key = canonical_source_key(source_name)
    target_key = canonical_source_key(target_name)
    if not source_key or not target_key or source_key == target_key:
        return None
    return SourceNameAliasDraft(
        source_name_key=source_key,
        target_name_key=target_key,
        source_display_name=normalize_source_text(source_name),
        target_display_name=normalize_source_text(target_name),
        relation_type=relation_type,
        evidence_source=evidence_source,
        evidence_payload=dict(evidence_payload),
        confidence=confidence,
        requires_review=requires_review,
    )


def _record_key(payload: Mapping[str, Any], provider: str, index: int) -> str:
    explicit = normalize_source_text(payload.get("provider_record_key"))
    if explicit:
        return explicit
    work_id = normalize_source_text(payload.get("source_work_id") or payload.get("work_id") or payload.get("result_id"))
    page = payload.get("source_page_index", payload.get("page_index"))
    if work_id:
        return f"{provider}:{work_id}:p{page if page is not None else 'na'}"
    return f"{provider}:record:{index}"


def extract_source_record(payload: Mapping[str, Any], *, index: int = 0) -> tuple[
    SourceMetadataDraft,
    list[SourceTagDraft],
    list[SourceNameDraft],
    list[SourceNameAliasDraft],
    list[SourceMetadataEvidenceDraft],
]:
    provider = canonical_source_key(payload.get("provider") or "generic_provider")
    provider_record_key = _record_key(payload, provider, index)
    media_id = _int_or_none(payload.get("media_id"))
    source_work_id = normalize_source_text(payload.get("source_work_id") or payload.get("work_id")) or None
    source_page_index = _int_or_none(payload.get("source_page_index", payload.get("page_index")))
    raw_metadata = dict(_as_mapping(payload.get("raw_metadata_json") or payload.get("raw_metadata") or payload))
    tags: list[SourceTagDraft] = []
    names: list[SourceNameDraft] = []
    aliases: list[SourceNameAliasDraft] = []
    evidence: list[SourceMetadataEvidenceDraft] = [
        SourceMetadataEvidenceDraft(
            provider=provider,
            provider_record_key=provider_record_key,
            evidence_key=f"{provider_record_key}:metadata_snapshot",
            observation_type="source_metadata_record",
            observation_key=None,
            evidence_kind="provider_metadata_snapshot",
            evidence_strength="source_observation",
            provenance={"provider": provider, "metadata_kind": payload.get("metadata_kind") or "provider_metadata"},
        )
    ]

    tag_category_map = {
        canonical_source_key(key): normalize_source_text(value).casefold()
        for key, value in _as_mapping(payload.get("tag_categories") or payload.get("pixiv_tag_category_map")).items()
        if canonical_source_key(key)
    }
    raw_tag_rows = _tag_rows(payload)
    raw_signal = raw_applicable_name_signal_summary(
        payload,
        provider=provider,
        tag_rows=raw_tag_rows,
        tag_category_map=tag_category_map,
    )
    disabled_fields = _disabled_name_extraction_fields(payload) | _source_title_only_fields(payload)
    disable_parenthetical = bool(payload.get("disable_parenthetical_name_extraction"))
    disable_category_names = bool(payload.get("disable_category_name_extraction"))

    for tag in raw_tag_rows:
        raw_tag = tag["raw_tag"]
        normalized = normalize_source_text(raw_tag)
        key = canonical_source_key(normalized)
        category = tag["source_category_raw"] or tag_category_map.get(key)
        kind = tag["source_tag_kind"]
        if provider == "pixiv" and POPULARITY_TAG_RE.search(normalized):
            kind = "popularity_tag"
        language, _script = language_and_script_hint(normalized)
        tags.append(
            SourceTagDraft(
                provider=provider,
                provider_record_key=provider_record_key,
                observation_key=f"{provider_record_key}:tag:{key}:{tag['order_index']}",
                raw_tag=raw_tag,
                normalized_tag=normalized,
                canonical_tag_key=key,
                source_tag_kind=kind,
                source_category_raw=category,
                language_hint=language,
                confidence=tag["confidence"],
                order_index=tag["order_index"],
            )
        )
        mapped_role = TAG_CATEGORIES_TO_NAME_ROLE.get(str(category or "").casefold())
        if mapped_role and not disable_category_names:
            field_name = f"{provider}_{mapped_role}_tag"
            _add_name_once(
                names,
                _name_draft(
                    provider=provider,
                    provider_record_key=provider_record_key,
                    raw_name=raw_tag,
                    name_role=mapped_role,
                    source_field=field_name,
                    media_id=media_id,
                    source_work_id=source_work_id,
                    source_page_index=source_page_index,
                    confidence=0.82,
                    provenance={"source_category_raw": category, "provider_tag": True},
                    requires_review=True,
                    suffix=str(tag["order_index"]),
                ),
            )
        if provider == "pixiv" and not disable_parenthetical:
            parsed = parse_parenthetical_name(raw_tag)
            if parsed:
                outer, inner = parsed
                _add_name_once(
                    names,
                    _name_draft(
                        provider=provider,
                        provider_record_key=provider_record_key,
                        raw_name=outer,
                        name_role="character",
                        source_field="pixiv_parenthetical_outer",
                        media_id=media_id,
                        source_work_id=source_work_id,
                        source_page_index=source_page_index,
                        confidence=0.76,
                        provenance={"raw_tag": raw_tag, "pattern": "character(work)"},
                        requires_review=True,
                        suffix=str(tag["order_index"]),
                    ),
                )
                _add_name_once(
                    names,
                    _name_draft(
                        provider=provider,
                        provider_record_key=provider_record_key,
                        raw_name=inner,
                        name_role="work_title",
                        source_field="pixiv_parenthetical_inner_work",
                        media_id=media_id,
                        source_work_id=source_work_id,
                        source_page_index=source_page_index,
                        confidence=0.72,
                        provenance={"raw_tag": raw_tag, "pattern": "character(work)"},
                        requires_review=True,
                        suffix=str(tag["order_index"]),
                    ),
                )
                relation = _relation(
                    source_name=outer,
                    target_name=inner,
                    relation_type="parenthetical_character_of_work",
                    evidence_source="pixiv_parenthetical_pattern",
                    confidence=0.72,
                    evidence_payload={"provider_record_key": provider_record_key, "raw_tag_present": True},
                )
                if relation:
                    aliases.append(relation)

    for field_name, role, confidence, requires_review in SOURCE_FIELD_SPECS:
        if _extraction_field_disabled(disabled_fields, provider=provider, field_name=field_name):
            continue
        for item_index, value in enumerate(_as_text_list(payload.get(field_name))):
            _add_name_once(
                names,
                _name_draft(
                    provider=provider,
                    provider_record_key=provider_record_key,
                    raw_name=value,
                    name_role=role,
                    source_field=_source_field_name(provider, field_name),
                    media_id=media_id,
                    source_work_id=source_work_id,
                    source_page_index=source_page_index,
                    confidence=confidence,
                    provenance={"source_field": field_name, "provider": provider},
                    requires_review=requires_review,
                    suffix=str(item_index),
                ),
            )

    provider_aliases = _as_mapping(payload.get("provider_canonical_aliases"))
    for source_name, target_names in provider_aliases.items():
        for target_name in _as_text_list(target_names):
            relation = _relation(
                source_name=source_name,
                target_name=target_name,
                relation_type="provider_canonical",
                evidence_source=f"{provider}_provider_canonical",
                confidence=0.88,
                evidence_payload={"provider_record_key": provider_record_key},
                requires_review=True,
            )
            if relation:
                aliases.append(relation)

    signal_roles = tuple(raw_signal["raw_signal_roles"])
    extracted_roles = {
        name.name_role
        for name in names
        if name.name_role in PERSON_LIKE_ROLES or name.name_role == "work_title"
    }
    if not signal_roles:
        status = "not_applicable_no_person_signal"
    elif set(signal_roles).issubset(extracted_roles):
        status = "applicable_name_signal_covered"
    else:
        status = "applicable_name_signal_uncovered"
    metadata = SourceMetadataDraft(
        provider=provider,
        provider_run_id=normalize_source_text(payload.get("provider_run_id")) or None,
        run_label=normalize_source_text(payload.get("run_label")) or None,
        provider_record_key=provider_record_key,
        media_id=media_id,
        source_work_id=source_work_id,
        source_page_index=source_page_index,
        source_url=normalize_source_text(payload.get("source_url") or payload.get("post_url")) or None,
        title=normalize_source_text(payload.get("title")) or None,
        artist_name=normalize_source_text(payload.get("artist_name") or payload.get("artist") or payload.get("creator")) or None,
        artist_id=normalize_source_text(payload.get("artist_id")) or None,
        confidence=_float_or_none(payload.get("confidence")),
        similarity=_float_or_none(payload.get("similarity") or payload.get("score")),
        metadata_kind=normalize_source_text(payload.get("metadata_kind")) or "provider_metadata",
        data_type_label=normalize_source_text(payload.get("data_type_label") or payload.get("source_data_type_label"))
        or "fixture_or_mock",
        raw_metadata_json=raw_metadata,
        provenance=dict(_as_mapping(payload.get("provenance") or {"input_shape": "provider_metadata_fixture"})),
        status="observed",
        retrieved_at=datetime.now(timezone.utc),
        signal_roles=tuple(signal_roles),
        applicability_status=status,
        raw_name_signal_flags={
            key: bool(raw_signal.get(key))
            for key in RAW_SIGNAL_FLAG_NAMES
        },
        no_applicable_name_signal_reason=raw_signal["no_applicable_name_signal_reason"],
    )
    for name in names:
        evidence.append(
            SourceMetadataEvidenceDraft(
                provider=name.provider,
                provider_record_key=provider_record_key,
                evidence_key=f"{name.observation_key}:name_extraction",
                observation_type="source_name_observation",
                observation_key=name.observation_key,
                evidence_kind="source_name_extraction",
                evidence_strength="strong" if (name.confidence or 0.0) >= 0.9 else "medium",
                provenance=name.provenance,
            )
        )
    return metadata, tags, names, aliases, evidence


def _dedupe_aliases(aliases: Iterable[SourceNameAliasDraft]) -> tuple[SourceNameAliasDraft, ...]:
    result: dict[tuple[str, str, str, str], SourceNameAliasDraft] = {}
    for alias in aliases:
        key = (alias.source_name_key, alias.target_name_key, alias.relation_type, alias.evidence_source)
        existing = result.get(key)
        if existing is None or (alias.confidence or 0.0) > (existing.confidence or 0.0):
            result[key] = alias
    return tuple(sorted(result.values(), key=lambda row: (row.source_name_key, row.target_name_key, row.relation_type)))


def build_source_registry_bundle(
    records: Sequence[Mapping[str, Any]],
    *,
    curated_mappings: Sequence[CuratedNameMapping] = (),
) -> SourceRegistryBundle:
    metadata_rows: list[SourceMetadataDraft] = []
    tag_rows: list[SourceTagDraft] = []
    name_rows: list[SourceNameDraft] = []
    alias_rows: list[SourceNameAliasDraft] = []
    evidence_rows: list[SourceMetadataEvidenceDraft] = []
    for index, record in enumerate(records):
        metadata, tags, names, aliases, evidence = extract_source_record(record, index=index)
        metadata_rows.append(metadata)
        tag_rows.extend(tags)
        name_rows.extend(names)
        alias_rows.extend(aliases)
        evidence_rows.extend(evidence)

    for mapping in curated_mappings:
        source_key = canonical_source_key(mapping.source_name)
        target_key = canonical_source_key(mapping.target_name)
        if not source_key or not target_key:
            continue
        alias_rows.append(
            SourceNameAliasDraft(
                source_name_key=source_key,
                target_name_key=target_key,
                source_display_name=normalize_source_text(mapping.source_name),
                target_display_name=normalize_source_text(mapping.target_name),
                relation_type=mapping.relation_type or "curated_alias",
                evidence_source=mapping.source or "curated_mapping",
                evidence_payload={
                    "name_role": mapping.name_role,
                    "candidate_namespace": mapping.candidate_namespace,
                    "notes": mapping.notes,
                },
                confidence=mapping.confidence,
                requires_review=False,
            )
        )

    tag_registry = _build_tag_registry(tag_rows)
    alias_rows = list(_dedupe_aliases(alias_rows))
    name_registry = _build_name_registry(name_rows, alias_rows)
    return SourceRegistryBundle(
        metadata_records=tuple(metadata_rows),
        tag_observations=tuple(tag_rows),
        tag_registry=tuple(tag_registry),
        name_observations=tuple(name_rows),
        name_registry=tuple(name_registry),
        alias_candidates=tuple(alias_rows),
        evidence=tuple(evidence_rows),
        record_statuses={
            provider_record_lookup_key(row.provider, row.provider_record_key): row.applicability_status
            for row in metadata_rows
        },
    )


def _build_tag_registry(tags: Sequence[SourceTagDraft]) -> list[SourceTagRegistryDraft]:
    grouped: dict[str, list[SourceTagDraft]] = defaultdict(list)
    for tag in tags:
        grouped[tag.canonical_tag_key].append(tag)
    rows: list[SourceTagRegistryDraft] = []
    for key, values in sorted(grouped.items()):
        variants = sorted({row.raw_tag for row in values})
        rows.append(
            SourceTagRegistryDraft(
                provider_scope="global",
                normalized_tag=values[0].normalized_tag,
                canonical_tag_key=key,
                raw_variants_json=variants,
                seen_count=len(values),
                example_provider=values[0].provider,
                example_provider_record_key=values[0].provider_record_key,
            )
        )
    return rows


def _build_name_registry(
    names: Sequence[SourceNameDraft],
    aliases: Sequence[SourceNameAliasDraft],
) -> list[SourceNameRegistryDraft]:
    grouped: dict[str, list[SourceNameDraft]] = defaultdict(list)
    for name in names:
        grouped[name.canonical_name_key].append(name)
    for alias in aliases:
        grouped.setdefault(alias.source_name_key, [])
        grouped.setdefault(alias.target_name_key, [])

    rows: list[SourceNameRegistryDraft] = []
    for key, values in sorted(grouped.items()):
        if values:
            variants = sorted({row.raw_name for row in values})
            provider_counts = Counter(row.provider for row in values)
            role_counts = Counter(row.name_role for row in values)
            primary = variants[0]
            normalized = values[0].normalized_name
            seen_count = len(values)
        else:
            display_candidates = [
                alias.source_display_name
                for alias in aliases
                if alias.source_name_key == key
            ] + [
                alias.target_display_name
                for alias in aliases
                if alias.target_name_key == key
            ]
            primary = display_candidates[0] if display_candidates else key
            normalized = normalize_source_text(primary)
            variants = sorted({normalize_source_text(value) for value in display_candidates if normalize_source_text(value)})
            provider_counts = Counter()
            role_counts = Counter()
            seen_count = 0
        rows.append(
            SourceNameRegistryDraft(
                canonical_name_key=key,
                primary_display_name=primary,
                normalized_display_name=normalized,
                raw_variants_json=variants,
                provider_coverage_json=dict(sorted(provider_counts.items())),
                role_distribution_json=dict(sorted(role_counts.items())),
                seen_count=seen_count,
            )
        )
    return rows


def provider_name_coverage(bundle: SourceRegistryBundle) -> dict[str, Any]:
    by_record = {
        provider_record_lookup_key(row.provider, row.provider_record_key): row
        for row in bundle.metadata_records
    }
    names_by_record: dict[str, list[SourceNameDraft]] = defaultdict(list)
    for name in bundle.name_observations:
        names_by_record[provider_record_lookup_key(name.provider, name.provider_record_key)].append(name)

    providers = sorted({row.provider for row in bundle.metadata_records})
    provider_summary: dict[str, Any] = {}
    for provider in providers:
        records = [row for row in bundle.metadata_records if row.provider == provider]
        applicable = [row for row in records if row.signal_roles]
        covered = []
        role_applicable: Counter[str] = Counter()
        role_covered: Counter[str] = Counter()
        role_extracted: Counter[str] = Counter()
        raw_flag_counts: Counter[str] = Counter()
        not_applicable_reasons: Counter[str] = Counter()
        failed_applicable_records = 0
        for record in applicable:
            record_key = provider_record_lookup_key(record.provider, record.provider_record_key)
            extracted_roles = {
                name.name_role
                for name in names_by_record.get(record_key, [])
                if name.name_role in PERSON_LIKE_ROLES or name.name_role == "work_title"
            }
            for role in record.signal_roles:
                role_applicable[role] += 1
                if role in extracted_roles:
                    role_covered[role] += 1
            for role in extracted_roles:
                role_extracted[role] += 1
            if set(record.signal_roles).issubset(extracted_roles):
                covered.append(record)
            else:
                failed_applicable_records += 1
        for record in records:
            for flag_name, flag_value in record.raw_name_signal_flags.items():
                if flag_value:
                    raw_flag_counts[flag_name] += 1
            if not record.signal_roles:
                not_applicable_reasons[record.no_applicable_name_signal_reason or "no_raw_name_signal"] += 1
        provider_summary[provider] = {
            "record_count": len(records),
            "applicable_name_signal_count": len(applicable),
            "covered_name_signal_count": len(covered),
            "not_applicable_no_person_signal_count": len(records) - len(applicable),
            "failed_applicable_name_signal_count": failed_applicable_records,
            "not_applicable_no_person_signal_reason_counts": dict(sorted(not_applicable_reasons.items())),
            "raw_signal_flag_counts": dict(sorted(raw_flag_counts.items())),
            "coverage": _ratio(len(covered), len(applicable)),
            "role_applicable_counts": dict(sorted(role_applicable.items())),
            "role_extracted_counts": dict(sorted(role_extracted.items())),
            "role_covered_counts": dict(sorted(role_covered.items())),
            "role_coverage": {
                role: _ratio(role_covered[role], role_applicable[role])
                for role in sorted(role_applicable)
            },
        }
    return {
        "providers": provider_summary,
        "record_status_counts": dict(sorted(Counter(bundle.record_statuses.values()).items())),
        "records_indexed": len(by_record),
    }


def raw_applicable_signal_rows(bundle: SourceRegistryBundle) -> list[dict[str, Any]]:
    names_by_record: dict[str, list[SourceNameDraft]] = defaultdict(list)
    for name in bundle.name_observations:
        names_by_record[provider_record_lookup_key(name.provider, name.provider_record_key)].append(name)

    rows: list[dict[str, Any]] = []
    for record in bundle.metadata_records:
        record_key = provider_record_lookup_key(record.provider, record.provider_record_key)
        extracted_roles = sorted(
            {
                name.name_role
                for name in names_by_record.get(record_key, [])
                if name.name_role in PERSON_LIKE_ROLES or name.name_role == "work_title"
            }
        )
        raw_roles = tuple(record.signal_roles)
        flags = {key: bool(record.raw_name_signal_flags.get(key)) for key in RAW_SIGNAL_FLAG_NAMES}
        rows.append(
            {
                "provider": record.provider,
                "provider_record_key": record.provider_record_key,
                "data_type_label": record.data_type_label,
                "applicability_status": record.applicability_status,
                "raw_signal_roles": list(raw_roles),
                "extracted_roles": extracted_roles,
                "raw_role_count": len(raw_roles),
                "extracted_role_count": len(extracted_roles),
                "all_raw_roles_extracted": bool(raw_roles) and set(raw_roles).issubset(extracted_roles),
                "no_applicable_name_signal_reason": record.no_applicable_name_signal_reason,
                **flags,
            }
        )
    return rows


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def build_name_search_index(bundle: SourceRegistryBundle) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in bundle.name_registry:
        candidates = {row.canonical_name_key, canonical_source_key(row.primary_display_name)}
        candidates.update(canonical_source_key(value) for value in row.raw_variants_json)
        for key in {candidate for candidate in candidates if candidate}:
            index[key].append(
                {
                    "match_type": "registry",
                    "canonical_name_key": row.canonical_name_key,
                    "status": row.governance_status,
                }
            )
    for alias in bundle.alias_candidates:
        index[alias.source_name_key].append(
            {
                "match_type": "alias_candidate",
                "canonical_name_key": alias.target_name_key,
                "source_name_key": alias.source_name_key,
                "relation_type": alias.relation_type,
                "status": alias.status,
                "requires_review": alias.requires_review,
            }
        )
        index[alias.target_name_key].append(
            {
                "match_type": "alias_candidate",
                "canonical_name_key": alias.source_name_key,
                "source_name_key": alias.target_name_key,
                "relation_type": alias.relation_type,
                "status": alias.status,
                "requires_review": alias.requires_review,
                "reverse_candidate": True,
            }
        )
    return dict(index)


def validate_search_queries(bundle: SourceRegistryBundle, queries: Iterable[str]) -> list[dict[str, Any]]:
    index = build_name_search_index(bundle)
    rows: list[dict[str, Any]] = []
    for query in queries:
        key = canonical_source_key(query)
        matches = index.get(key, [])
        rows.append(
            {
                "query": normalize_source_text(query),
                "query_key": key,
                "match_count": len(matches),
                "matched": bool(matches),
                "matches": matches,
            }
        )
    return rows


def bundle_public_counts(bundle: SourceRegistryBundle) -> dict[str, Any]:
    return {
        "source_metadata_records": len(bundle.metadata_records),
        "source_tag_observations": len(bundle.tag_observations),
        "source_tag_registry": len(bundle.tag_registry),
        "source_name_observations": len(bundle.name_observations),
        "source_name_registry": len(bundle.name_registry),
        "source_name_alias_candidates": len(bundle.alias_candidates),
        "source_metadata_evidence": len(bundle.evidence),
        "metadata_records_by_provider": dict(sorted(Counter(row.provider for row in bundle.metadata_records).items())),
        "metadata_records_by_data_type": dict(
            sorted(Counter(row.data_type_label for row in bundle.metadata_records).items())
        ),
        "metadata_records_by_provider_and_data_type": dict(
            sorted(
                Counter(
                    f"{row.provider}:{row.data_type_label}"
                    for row in bundle.metadata_records
                ).items()
            )
        ),
        "tag_observations_by_provider": dict(sorted(Counter(row.provider for row in bundle.tag_observations).items())),
        "name_observations_by_provider": dict(sorted(Counter(row.provider for row in bundle.name_observations).items())),
        "alias_candidates_by_relation_type": dict(sorted(Counter(row.relation_type for row in bundle.alias_candidates).items())),
        "search_key_collision_count": search_key_collision_count(bundle),
    }


def search_key_collision_count(bundle: SourceRegistryBundle) -> int:
    display_by_key: dict[str, set[str]] = defaultdict(set)
    for name in bundle.name_observations:
        display_by_key[name.canonical_name_key].add(name.normalized_name)
    return sum(1 for values in display_by_key.values() if len(values) > 1)


def persist_source_registry_bundle(
    session: Session,
    bundle: SourceRegistryBundle,
    *,
    apply: bool,
    searchable_name_assertions: Sequence[SourceSearchableNameAssertionDraft] = (),
) -> dict[str, Any]:
    searchable_name_assertions = tuple(searchable_name_assertions)
    assertion_count = len(searchable_name_assertions)
    summary: dict[str, Any] = {
        "apply": bool(apply),
        "success": True,
        "planned": bundle_public_counts(bundle),
        "inserted": Counter(),
        "updated": Counter(),
        "retired": Counter(),
        "existing": Counter(),
        "allowed_tables": [
            "blombooru_source_metadata_records",
            "blombooru_source_tag_observations",
            "blombooru_source_tag_registry",
            "blombooru_source_name_observations",
            "blombooru_source_name_registry",
            "blombooru_source_name_alias_candidates",
            "blombooru_source_metadata_evidence",
            "blombooru_source_searchable_name_assertions",
        ],
        "forbidden_truth_table_write_count": 0,
    }
    summary["planned"]["source_searchable_name_assertions"] = assertion_count
    if not apply:
        summary["inserted"] = {}
        summary["updated"] = {}
        summary["retired"] = {}
        summary["existing"] = {}
        return summary

    tag_drafts_by_record: dict[str, list[SourceTagDraft]] = defaultdict(list)
    for draft in bundle.tag_observations:
        tag_drafts_by_record[provider_record_lookup_key(draft.provider, draft.provider_record_key)].append(draft)
    name_drafts_by_record: dict[str, list[SourceNameDraft]] = defaultdict(list)
    for draft in bundle.name_observations:
        name_drafts_by_record[provider_record_lookup_key(draft.provider, draft.provider_record_key)].append(draft)
    evidence_drafts_by_record: dict[str, list[SourceMetadataEvidenceDraft]] = defaultdict(list)
    for draft in bundle.evidence:
        evidence_drafts_by_record[provider_record_lookup_key(draft.provider, draft.provider_record_key)].append(draft)
    assertion_drafts_by_record: dict[str, list[SourceSearchableNameAssertionDraft]] = defaultdict(list)
    for draft in searchable_name_assertions:
        assertion_drafts_by_record[provider_record_lookup_key(draft.provider, draft.provider_record_key)].append(draft)
    refreshed_provider_records = {
        (draft.provider, draft.provider_record_key)
        for draft in bundle.metadata_records
    }

    metadata_by_key: dict[str, SourceMetadataRecord] = {}
    for draft in bundle.metadata_records:
        row = (
            session.query(SourceMetadataRecord)
            .filter_by(provider=draft.provider, provider_record_key=draft.provider_record_key)
            .one_or_none()
        )
        fields = _metadata_fields(draft)
        if row is None:
            row = SourceMetadataRecord(**fields)
            session.add(row)
            summary["inserted"]["SourceMetadataRecord"] += 1
        else:
            for key, value in fields.items():
                setattr(row, key, value)
            summary["updated"]["SourceMetadataRecord"] += 1
        metadata_by_key[provider_record_lookup_key(draft.provider, draft.provider_record_key)] = row
    session.flush()

    retired_tag_keys: set[str] = set()
    retired_name_keys: set[str] = set()
    for record_key, metadata in metadata_by_key.items():
        incoming_tag_keys = {draft.observation_key for draft in tag_drafts_by_record.get(record_key, [])}
        for row in (
            session.query(SourceTagObservation)
            .filter_by(source_metadata_record_id=metadata.id, status="observed")
            .all()
        ):
            if row.observation_key not in incoming_tag_keys:
                row.status = "superseded"
                retired_tag_keys.add(row.canonical_tag_key)
                summary["retired"]["SourceTagObservation"] += 1

        incoming_name_keys = {draft.observation_key for draft in name_drafts_by_record.get(record_key, [])}
        for row in (
            session.query(SourceNameObservation)
            .filter_by(source_metadata_record_id=metadata.id, status="observed")
            .all()
        ):
            if row.observation_key not in incoming_name_keys:
                row.status = "superseded"
                retired_name_keys.add(row.canonical_name_key)
                summary["retired"]["SourceNameObservation"] += 1

        incoming_evidence_keys = {draft.evidence_key for draft in evidence_drafts_by_record.get(record_key, [])}
        for row in (
            session.query(SourceMetadataEvidence)
            .filter(
                SourceMetadataEvidence.source_metadata_record_id == metadata.id,
                SourceMetadataEvidence.status != "superseded",
            )
            .all()
        ):
            if row.evidence_key not in incoming_evidence_keys:
                row.status = "superseded"
                summary["retired"]["SourceMetadataEvidence"] += 1

    tag_by_observation_key: dict[str, SourceTagObservation] = {}
    for draft in bundle.tag_observations:
        metadata = metadata_by_key[provider_record_lookup_key(draft.provider, draft.provider_record_key)]
        row = (
            session.query(SourceTagObservation)
            .filter_by(source_metadata_record_id=metadata.id, observation_key=draft.observation_key)
            .one_or_none()
        )
        fields = _tag_observation_fields(draft, int(metadata.id))
        if row is None:
            row = SourceTagObservation(**fields)
            session.add(row)
            summary["inserted"]["SourceTagObservation"] += 1
        else:
            for key, value in fields.items():
                setattr(row, key, value)
            summary["updated"]["SourceTagObservation"] += 1
        tag_by_observation_key[
            provider_record_lookup_key(draft.provider, draft.provider_record_key) + f"::{draft.observation_key}"
        ] = row
    session.flush()

    tag_registry_by_key = {draft.canonical_tag_key: draft for draft in bundle.tag_registry}
    for canonical_tag_key in sorted(set(tag_registry_by_key) | retired_tag_keys):
        draft = tag_registry_by_key.get(canonical_tag_key)
        row = (
            session.query(SourceTagRegistry)
            .filter_by(provider_scope="global", canonical_tag_key=canonical_tag_key)
            .one_or_none()
        )
        if draft is None:
            draft = _tag_registry_draft_for_key(canonical_tag_key, row)
        fields = _merged_tag_registry_fields(session, draft, row, metadata_by_key)
        if row is None:
            session.add(SourceTagRegistry(**fields))
            summary["inserted"]["SourceTagRegistry"] += 1
        else:
            for key, value in fields.items():
                setattr(row, key, value)
            summary["updated"]["SourceTagRegistry"] += 1

    name_by_observation_key: dict[str, SourceNameObservation] = {}
    for draft in bundle.name_observations:
        metadata = metadata_by_key[provider_record_lookup_key(draft.provider, draft.provider_record_key)]
        row = (
            session.query(SourceNameObservation)
            .filter_by(source_metadata_record_id=metadata.id, observation_key=draft.observation_key)
            .one_or_none()
        )
        fields = _name_observation_fields(draft, int(metadata.id))
        if row is None:
            row = SourceNameObservation(**fields)
            session.add(row)
            summary["inserted"]["SourceNameObservation"] += 1
        else:
            for key, value in fields.items():
                setattr(row, key, value)
            summary["updated"]["SourceNameObservation"] += 1
        name_by_observation_key[
            provider_record_lookup_key(draft.provider, draft.provider_record_key) + f"::{draft.observation_key}"
        ] = row
    session.flush()

    name_registry_by_key = {draft.canonical_name_key: draft for draft in bundle.name_registry}
    for canonical_name_key in sorted(set(name_registry_by_key) | retired_name_keys):
        draft = name_registry_by_key.get(canonical_name_key)
        row = session.query(SourceNameRegistry).filter_by(canonical_name_key=canonical_name_key).one_or_none()
        if draft is None:
            draft = _name_registry_draft_for_key(canonical_name_key, row)
        fields = _merged_name_registry_fields(session, draft, row)
        if row is None:
            session.add(SourceNameRegistry(**fields))
            summary["inserted"]["SourceNameRegistry"] += 1
        else:
            if str(row.manual_override_status or "none") != "none":
                fields.pop("manual_override_status", None)
                fields.pop("primary_display_name", None)
                fields.pop("normalized_display_name", None)
                fields.pop("notes", None)
            for key, value in fields.items():
                setattr(row, key, value)
            summary["updated"]["SourceNameRegistry"] += 1

    incoming_alias_keys = {
        (draft.source_name_key, draft.target_name_key, draft.relation_type, draft.evidence_source)
        for draft in bundle.alias_candidates
    }
    for draft in bundle.alias_candidates:
        row = (
            session.query(SourceNameAliasCandidate)
            .filter_by(
                source_name_key=draft.source_name_key,
                target_name_key=draft.target_name_key,
                relation_type=draft.relation_type,
                evidence_source=draft.evidence_source,
            )
            .one_or_none()
        )
        fields = _alias_fields(draft)
        if row is None:
            session.add(SourceNameAliasCandidate(**fields))
            summary["inserted"]["SourceNameAliasCandidate"] += 1
        else:
            for key, value in fields.items():
                setattr(row, key, value)
            summary["updated"]["SourceNameAliasCandidate"] += 1
    for row in (
        session.query(SourceNameAliasCandidate)
        .filter(SourceNameAliasCandidate.status != "superseded")
        .all()
    ):
        key = (row.source_name_key, row.target_name_key, row.relation_type, row.evidence_source)
        if key in incoming_alias_keys:
            continue
        payload = row.evidence_payload if isinstance(row.evidence_payload, Mapping) else {}
        provider_record_key = normalize_source_text(payload.get("provider_record_key"))
        if not provider_record_key:
            continue
        evidence_provider = None
        evidence_source = str(row.evidence_source or "")
        if evidence_source.endswith("_provider_canonical"):
            evidence_provider = evidence_source.removesuffix("_provider_canonical")
        elif evidence_source == "pixiv_parenthetical_pattern":
            evidence_provider = "pixiv"
        if evidence_provider and (evidence_provider, provider_record_key) in refreshed_provider_records:
            row.status = "superseded"
            summary["retired"]["SourceNameAliasCandidate"] += 1

    for draft in bundle.evidence:
        metadata = metadata_by_key[provider_record_lookup_key(draft.provider, draft.provider_record_key)]
        observation_id = None
        scoped_observation_key = (
            provider_record_lookup_key(draft.provider, draft.provider_record_key) + f"::{draft.observation_key}"
            if draft.observation_key
            else None
        )
        if scoped_observation_key and scoped_observation_key in name_by_observation_key:
            observation_id = int(name_by_observation_key[scoped_observation_key].id)
        row = (
            session.query(SourceMetadataEvidence)
            .filter_by(source_metadata_record_id=metadata.id, evidence_key=draft.evidence_key)
            .one_or_none()
        )
        fields = _evidence_fields(draft, int(metadata.id), observation_id)
        if row is None:
            session.add(SourceMetadataEvidence(**fields))
            summary["inserted"]["SourceMetadataEvidence"] += 1
        else:
            for key, value in fields.items():
                setattr(row, key, value)
            summary["updated"]["SourceMetadataEvidence"] += 1

    refreshed_record_keys = set(metadata_by_key)
    for record_key in sorted(refreshed_record_keys | set(assertion_drafts_by_record)):
        drafts = assertion_drafts_by_record.get(record_key, [])
        metadata = metadata_by_key.get(record_key)
        if metadata is None:
            raise ValueError(f"source_searchable_name_assertion_metadata_missing:{record_key}")
        incoming_assertion_keys = {draft.assertion_key for draft in drafts}
        for row in (
            session.query(SourceSearchableNameAssertion)
            .filter(
                SourceSearchableNameAssertion.source_metadata_record_id == metadata.id,
                SourceSearchableNameAssertion.status != "superseded",
            )
            .all()
        ):
            if row.assertion_key not in incoming_assertion_keys:
                row.status = "superseded"
                row.requires_review = True
                summary["retired"]["SourceSearchableNameAssertion"] += 1

    for draft in searchable_name_assertions:
        record_key = provider_record_lookup_key(draft.provider, draft.provider_record_key)
        metadata = metadata_by_key.get(record_key)
        if metadata is None:
            raise ValueError(f"source_searchable_name_assertion_metadata_missing:{record_key}")
        tag_observation_id = None
        name_observation_id = None
        if draft.source_tag_observation_key:
            scoped_tag_key = record_key + f"::{draft.source_tag_observation_key}"
            tag_row = tag_by_observation_key.get(scoped_tag_key)
            tag_observation_id = int(tag_row.id) if tag_row is not None and tag_row.id else None
        if draft.source_name_observation_key:
            scoped_name_key = record_key + f"::{draft.source_name_observation_key}"
            name_row = name_by_observation_key.get(scoped_name_key)
            name_observation_id = int(name_row.id) if name_row is not None and name_row.id else None

        row = (
            session.query(SourceSearchableNameAssertion)
            .filter_by(assertion_key=draft.assertion_key)
            .one_or_none()
        )
        fields = _searchable_name_assertion_fields(
            draft,
            int(metadata.id),
            tag_observation_id,
            name_observation_id,
        )
        if row is None:
            session.add(SourceSearchableNameAssertion(**fields))
            summary["inserted"]["SourceSearchableNameAssertion"] += 1
        else:
            for key, value in fields.items():
                setattr(row, key, value)
            summary["updated"]["SourceSearchableNameAssertion"] += 1

    session.commit()
    summary["inserted"] = dict(summary["inserted"])
    summary["updated"] = dict(summary["updated"])
    summary["retired"] = dict(summary["retired"])
    summary["existing"] = dict(summary["existing"])
    return summary


def _metadata_fields(draft: SourceMetadataDraft) -> dict[str, Any]:
    fields = asdict(draft)
    fields.pop("signal_roles", None)
    fields.pop("applicability_status", None)
    fields.pop("raw_name_signal_flags", None)
    fields.pop("no_applicable_name_signal_reason", None)
    return fields


def _tag_observation_fields(draft: SourceTagDraft, source_metadata_record_id: int) -> dict[str, Any]:
    fields = asdict(draft)
    fields.pop("provider_record_key", None)
    fields["source_metadata_record_id"] = source_metadata_record_id
    return fields


def _tag_registry_fields(
    draft: SourceTagRegistryDraft,
    metadata_by_key: Mapping[str, SourceMetadataRecord],
) -> dict[str, Any]:
    fields = asdict(draft)
    example_provider = fields.pop("example_provider", None)
    provider_record_key = fields.pop("example_provider_record_key", None)
    metadata = (
        metadata_by_key.get(provider_record_lookup_key(example_provider, provider_record_key))
        if example_provider and provider_record_key
        else None
    )
    fields["example_source_metadata_id"] = int(metadata.id) if metadata and metadata.id else None
    fields["last_seen_at"] = datetime.now(timezone.utc)
    return fields


def _tag_registry_draft_for_key(
    canonical_tag_key: str,
    existing: SourceTagRegistry | None,
) -> SourceTagRegistryDraft:
    return SourceTagRegistryDraft(
        provider_scope="global",
        normalized_tag=normalize_source_text(existing.normalized_tag) if existing else canonical_tag_key,
        canonical_tag_key=canonical_tag_key,
        raw_variants_json=list(existing.raw_variants_json or []) if existing else [],
        seen_count=0,
        example_provider=None,
        example_provider_record_key=None,
        taxonomy_status=str(existing.taxonomy_status or "unclassified") if existing else "unclassified",
        governance_status="retired",
    )


def _merged_tag_registry_fields(
    session: Session,
    draft: SourceTagRegistryDraft,
    existing: SourceTagRegistry | None,
    metadata_by_key: Mapping[str, SourceMetadataRecord],
) -> dict[str, Any]:
    fields = _tag_registry_fields(draft, metadata_by_key)
    observations = (
        session.query(SourceTagObservation)
        .filter_by(canonical_tag_key=draft.canonical_tag_key, status="observed")
        .all()
    )
    if observations:
        variants = sorted({normalize_source_text(row.raw_tag) for row in observations if normalize_source_text(row.raw_tag)})
        normalized = normalize_source_text(observations[0].normalized_tag) or draft.normalized_tag
        fields.update(
            {
                "normalized_tag": normalized,
                "raw_variants_json": variants,
                "seen_count": len(observations),
                "example_source_metadata_id": int(observations[0].source_metadata_record_id),
                "taxonomy_status": str(existing.taxonomy_status or draft.taxonomy_status)
                if existing is not None
                else draft.taxonomy_status,
                "governance_status": str(existing.governance_status or draft.governance_status)
                if existing is not None and str(existing.governance_status or "candidate") != "retired"
                else "candidate",
            }
        )
    else:
        variants = sorted(
            {
                normalize_source_text(value)
                for value in list(draft.raw_variants_json or [])
                + list(existing.raw_variants_json or [] if existing is not None else [])
                if normalize_source_text(value)
            }
        )
        fields.update(
            {
                "raw_variants_json": variants,
                "seen_count": 0,
                "example_source_metadata_id": None,
                "taxonomy_status": str(existing.taxonomy_status or draft.taxonomy_status)
                if existing is not None
                else draft.taxonomy_status,
                "governance_status": "retired",
            }
        )
    return fields


def _name_observation_fields(draft: SourceNameDraft, source_metadata_record_id: int) -> dict[str, Any]:
    fields = asdict(draft)
    fields.pop("provider_record_key", None)
    fields["source_metadata_record_id"] = source_metadata_record_id
    return fields


def _name_registry_fields(draft: SourceNameRegistryDraft) -> dict[str, Any]:
    fields = asdict(draft)
    fields["last_seen_at"] = datetime.now(timezone.utc)
    return fields


def _name_registry_draft_for_key(
    canonical_name_key: str,
    existing: SourceNameRegistry | None,
) -> SourceNameRegistryDraft:
    primary = normalize_source_text(existing.primary_display_name) if existing else canonical_name_key
    return SourceNameRegistryDraft(
        canonical_name_key=canonical_name_key,
        primary_display_name=primary or canonical_name_key,
        normalized_display_name=normalize_source_text(primary or canonical_name_key),
        raw_variants_json=list(existing.raw_variants_json or []) if existing else [],
        provider_coverage_json={},
        role_distribution_json={},
        seen_count=0,
        governance_status="retired",
        manual_override_status=str(existing.manual_override_status or "none") if existing else "none",
        notes=existing.notes if existing else None,
    )


def _merged_name_registry_fields(
    session: Session,
    draft: SourceNameRegistryDraft,
    existing: SourceNameRegistry | None,
) -> dict[str, Any]:
    fields = _name_registry_fields(draft)
    observations = (
        session.query(SourceNameObservation)
        .filter_by(canonical_name_key=draft.canonical_name_key, status="observed")
        .all()
    )
    if not observations:
        if existing is not None:
            fields.update(
                {
                    "primary_display_name": existing.primary_display_name,
                    "normalized_display_name": normalize_source_text(existing.primary_display_name),
                    "raw_variants_json": list(existing.raw_variants_json or draft.raw_variants_json or []),
                    "provider_coverage_json": {},
                    "role_distribution_json": {},
                    "seen_count": 0,
                    "governance_status": draft.governance_status,
                }
            )
        return fields

    variants = sorted({normalize_source_text(row.raw_name) for row in observations if normalize_source_text(row.raw_name)})
    provider_counts = Counter(row.provider for row in observations)
    role_counts = Counter(row.name_role for row in observations)
    primary = (
        existing.primary_display_name
        if existing is not None and str(existing.manual_override_status or "none") != "none"
        else (variants[0] if variants else draft.primary_display_name)
    )
    fields.update(
        {
            "primary_display_name": primary,
            "normalized_display_name": normalize_source_text(primary),
            "raw_variants_json": variants,
            "provider_coverage_json": dict(sorted(provider_counts.items())),
            "role_distribution_json": dict(sorted(role_counts.items())),
            "seen_count": len(observations),
            "governance_status": "candidate"
            if draft.governance_status == "retired"
            else draft.governance_status,
        }
    )
    return fields


def _alias_fields(draft: SourceNameAliasDraft) -> dict[str, Any]:
    return asdict(draft)


def _evidence_fields(
    draft: SourceMetadataEvidenceDraft,
    source_metadata_record_id: int,
    observation_id: int | None,
) -> dict[str, Any]:
    fields = asdict(draft)
    fields.pop("provider", None)
    fields.pop("provider_record_key", None)
    fields.pop("observation_key", None)
    fields["source_metadata_record_id"] = source_metadata_record_id
    fields["observation_id"] = observation_id
    return fields


def _searchable_name_assertion_fields(
    draft: SourceSearchableNameAssertionDraft,
    source_metadata_record_id: int,
    source_tag_observation_id: int | None,
    source_name_observation_id: int | None,
) -> dict[str, Any]:
    fields = asdict(draft)
    fields.pop("provider_record_key", None)
    fields.pop("source_tag_observation_key", None)
    fields.pop("source_name_observation_key", None)
    fields["source_metadata_record_id"] = source_metadata_record_id
    fields["source_tag_observation_id"] = source_tag_observation_id
    fields["source_name_observation_id"] = source_name_observation_id
    return fields
