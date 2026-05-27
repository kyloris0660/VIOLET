"""Provider-neutral reverse-search evidence contract.

Phase 4.4-C0 is an internal, non-mutating contract foundation.  The objects in
this module are DTOs for mapper/tests/reports only: they do not write DB rows,
call providers, upload images, or imply automatic entity creation.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit


class ProviderRunStatus(str, Enum):
    completed = "completed"
    partial = "partial"
    stopped = "stopped"
    provider_error = "provider_error"
    not_run = "not_run"


class SourceMatchClass(str, Enum):
    exact_or_near_exact = "exact_or_near_exact"
    low_confidence = "low_confidence"
    no_match = "no_match"
    conflict = "conflict"
    provider_error = "provider_error"
    discarded = "discarded"


class EvidenceStrength(str, Enum):
    strong = "strong"
    weak = "weak"
    discard = "discard"
    unknown = "unknown"


class ManualValidationStatus(str, Enum):
    validated_correct = "validated_correct"
    validated_wrong = "validated_wrong"
    not_validated = "not_validated"


class LocalizationStatus(str, Enum):
    pending = "pending"
    not_applicable = "not_applicable"


LOCAL_PATH_RE = re.compile(r"(?i)(^|[\s\"'({\[=:;,])([a-z]:[\\/]|\\\\|file://|/(users|home|root|mnt|volumes|workspace|tmp|var)(/|$))")
SECRET_RE = re.compile(r"(?i)(api[_-]?key|authorization|bearer\s+[A-Za-z0-9._~+\-/]{16,}|sk-[A-Za-z0-9_-]{16,})")
FORBIDDEN_PUBLIC_KEYS = {
    "api_key",
    "authorization",
    "derived_sha256_private",
    "image_bytes",
    "local_path",
    "original_filename",
    "path",
    "raw_image_bytes",
    "safe_filename",
    "source_label",
}


def _coerce_public_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _coerce_public_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _coerce_public_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_coerce_public_value(item) for item in value]
    if isinstance(value, list):
        return [_coerce_public_value(item) for item in value]
    return value


def assert_public_payload_safe(payload: Any) -> None:
    """Fail closed if a public contract payload contains local/private data."""
    normalized = _coerce_public_value(payload)

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key).lower()
                if key_text in FORBIDDEN_PUBLIC_KEYS:
                    raise ValueError(f"public payload contains forbidden key: {key}")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            split = urlsplit(value)
            query_fragment = f"{split.query} {split.fragment}" if split.scheme in {"http", "https"} else value
            if LOCAL_PATH_RE.search(value) or LOCAL_PATH_RE.search(query_fragment):
                raise ValueError("public payload contains a local path")
            if SECRET_RE.search(value):
                raise ValueError("public payload contains a secret-like token")

    visit(normalized)
    json.dumps(normalized, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class PublicSerializable:
    def to_public_dict(self) -> dict[str, Any]:
        payload = _coerce_public_value(self)
        assert_public_payload_safe(payload)
        return payload


@dataclass(frozen=True)
class ProviderQuery(PublicSerializable):
    provider_key: str
    provider_category: str
    media_id: int
    input_kind: str
    query_hash: str
    request_shape_redacted: dict[str, Any]
    live_request: bool
    uploaded_input_kind: str | None
    provider_policy_version: str
    query_type: str = "reverse_search_derived_image"


@dataclass(frozen=True)
class ProviderRunOutcome(PublicSerializable):
    provider_key: str
    status: ProviderRunStatus
    requests_attempted: int
    requests_succeeded: int
    requests_failed: int
    quota_short_remaining: int | None = None
    quota_long_remaining: int | None = None
    stop_reason: str | None = None
    ran_at: str | None = None


@dataclass(frozen=True)
class SourceMatch(PublicSerializable):
    media_id: int
    provider_key: str
    provider_result_id: str | None
    provider_index: str | None
    source_host: str | None
    source_url: str | None
    post_url: str | None
    rank: int | None
    score_value: float | None
    score_kind: str
    provider_minimum_similarity: float | None
    match_class: SourceMatchClass
    evidence_strength: EvidenceStrength
    manual_validation_status: ManualValidationStatus
    acceptance_policy_version: str


@dataclass(frozen=True)
class ExtractedProviderMetadata(PublicSerializable):
    artist_raw: tuple[str, ...] = ()
    work_raw: tuple[str, ...] = ()
    copyright_raw: tuple[str, ...] = ()
    character_raw: tuple[str, ...] = ()
    general_tags_raw: tuple[str, ...] = ()
    source_title: str | None = None
    provider_metadata_language: str = "provider_canonical"
    tag_style: str = "source_tag_style"
    localization_status: LocalizationStatus = LocalizationStatus.not_applicable
    raw_metadata_available: bool = False
    parser_status: str = "not_available"


@dataclass(frozen=True)
class PlannedEntityCandidate(PublicSerializable):
    entity_type: str
    candidate_name: str
    source_field: str
    evidence_strength: EvidenceStrength
    entity_id: int | None = None


@dataclass(frozen=True)
class EvidencePersistencePlan(PublicSerializable):
    media_id: int
    provider_query: ProviderQuery
    source_match: SourceMatch
    extracted_metadata: ExtractedProviderMetadata
    provider_cache_planned: bool
    entity_evidence_planned: bool
    media_entity_candidate_planned: bool
    negative_lookup_cache_planned: bool = False
    planned_entity_candidates: tuple[PlannedEntityCandidate, ...] = ()
    confirmed_assignment_allowed: bool = False
    entity_auto_create_allowed: bool = False
    localization_pending: bool = True
    db_write_allowed: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
