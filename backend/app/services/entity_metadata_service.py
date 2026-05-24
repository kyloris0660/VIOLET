"""Entity metadata foundation service.

Phase 4.1 is schema and local service scaffolding only.  This module does not
call external providers, run reverse image search, infer entities from tags, or
perform automatic confirmed writes.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from ..enums import (
    ContentClassEnum,
    EntityAliasTypeEnum,
    EntityCandidateGeneratorEnum,
    EntityCandidateStatusEnum,
    EntityEvidenceTypeEnum,
    EntityExternalIdentityStatusEnum,
    EntityMetadataSourceEnum,
    EntityReviewStatusEnum,
    EntityStatusEnum,
    EntityTranslationStatusEnum,
    EntityTypeEnum,
    MediaEntityRoleEnum,
)
from ..models import (
    Entity,
    EntityAlias,
    EntityEvidence,
    EntityExternalIdentity,
    EntityTranslation,
    ExternalSource,
    MediaEntityAssignment,
    MediaEntityCandidate,
)


class EntityMetadataError(ValueError):
    """Raised when a Phase 4.1 entity metadata rule is violated."""


LOCAL_PATH_RE = re.compile(
    r"(?i)(file://|(?<![A-Z0-9_])[A-Z]:[\\/]|\\\\|/(?:Users|home|root|mnt|Volumes|workspace|tmp|var)/)"
)
SECRET_RE = re.compile(r"(?i)(Bearer\s+[A-Za-z0-9._~+\-/]+=*|(?:sk|key)[-_][A-Za-z0-9_-]{8,})")
SOURCE_PRIORITY = {
    EntityMetadataSourceEnum.manual.value: 0,
    EntityMetadataSourceEnum.trusted_external.value: 1,
    EntityMetadataSourceEnum.imported.value: 2,
    EntityMetadataSourceEnum.external.value: 3,
    EntityMetadataSourceEnum.tag.value: 4,
    EntityMetadataSourceEnum.llm_suggestion.value: 5,
    EntityMetadataSourceEnum.system.value: 6,
}


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _source_priority(value: Any) -> int:
    return SOURCE_PRIORITY.get(_enum_value(value), 99)


def _coerce(enum_cls, value: Any, field_name: str):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_cls)
        raise EntityMetadataError(f"Invalid {field_name}: {value!r}. Allowed: {allowed}") from exc


def _assert_public_safe_text(value: str | None, *, field_name: str) -> None:
    if not value:
        return
    text = str(value)
    if LOCAL_PATH_RE.search(text) or SECRET_RE.search(text):
        raise EntityMetadataError(f"{field_name} must be privacy-redacted and must not contain local paths or secrets")


def normalize_entity_key(value: str) -> str:
    """Normalize entity names for stable dedupe/search keys."""
    if value is None:
        raise EntityMetadataError("Entity key cannot be None")
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def hash_provider_query(query: Mapping[str, Any] | str) -> str:
    """Create a stable hash for a redacted provider query shape."""
    if isinstance(query, str):
        payload = query
    else:
        payload = json.dumps(query, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _assert_public_safe_text(payload, field_name="query")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_entity(
    db: Session,
    *,
    entity_type: EntityTypeEnum | str,
    canonical_name: str,
    slug: str | None = None,
    status: EntityStatusEnum | str = EntityStatusEnum.active,
    description: str | None = None,
) -> Entity:
    """Create or return an entity keyed by type + normalized canonical name."""
    entity_type = _coerce(EntityTypeEnum, entity_type, "entity_type")
    status = _coerce(EntityStatusEnum, status, "status")
    normalized_key = normalize_entity_key(canonical_name)
    if not normalized_key:
        raise EntityMetadataError("canonical_name must normalize to a non-empty key")

    existing = (
        db.query(Entity)
        .filter(Entity.type == entity_type, Entity.normalized_key == normalized_key)
        .first()
    )
    if existing:
        return existing

    entity = Entity(
        type=entity_type,
        canonical_name=canonical_name.strip(),
        normalized_key=normalized_key,
        slug=slug or normalized_key,
        status=status,
        description=description,
    )
    db.add(entity)
    db.flush()
    return entity


def add_alias(
    db: Session,
    *,
    entity_id: int,
    alias: str,
    language: str | None = None,
    alias_type: EntityAliasTypeEnum | str = EntityAliasTypeEnum.search,
    source: EntityMetadataSourceEnum | str = EntityMetadataSourceEnum.manual,
    confidence: float | None = None,
    is_primary: bool = False,
    needs_review: bool = False,
) -> EntityAlias:
    alias_type = _coerce(EntityAliasTypeEnum, alias_type, "alias_type")
    source = _coerce(EntityMetadataSourceEnum, source, "source")
    normalized_alias = normalize_entity_key(alias)
    if not normalized_alias:
        raise EntityMetadataError("alias must normalize to a non-empty key")

    existing = (
        db.query(EntityAlias)
        .filter(
            EntityAlias.entity_id == entity_id,
            EntityAlias.normalized_alias == normalized_alias,
        )
        .first()
    )
    if existing:
        # Lower-trust suggestions should not silently downgrade manual/trusted aliases.
        if _source_priority(source) <= _source_priority(existing.source):
            existing.alias = alias.strip()
            existing.language = language
            existing.alias_type = alias_type
            existing.source = source
            existing.confidence = confidence
            existing.is_primary = is_primary
            existing.needs_review = needs_review
            existing.updated_at = datetime.now(timezone.utc)
        db.flush()
        return existing

    row = EntityAlias(
        entity_id=entity_id,
        alias=alias.strip(),
        normalized_alias=normalized_alias,
        language=language,
        alias_type=alias_type,
        source=source,
        confidence=confidence,
        is_primary=is_primary,
        needs_review=needs_review,
    )
    db.add(row)
    db.flush()
    return row


def add_external_identity(
    db: Session,
    *,
    entity_id: int,
    provider: str,
    external_id: str,
    external_url: str | None = None,
    identity_status: EntityExternalIdentityStatusEnum | str = EntityExternalIdentityStatusEnum.candidate,
    confidence: float | None = None,
    last_verified_at: datetime | None = None,
) -> EntityExternalIdentity:
    """Attach a redacted provider identity without calling that provider."""
    identity_status = _coerce(EntityExternalIdentityStatusEnum, identity_status, "identity_status")
    provider_key = normalize_entity_key(provider)
    if not provider_key or not str(external_id).strip():
        raise EntityMetadataError("provider and external_id are required")
    _assert_public_safe_text(external_url, field_name="external_url")

    existing = (
        db.query(EntityExternalIdentity)
        .filter(
            EntityExternalIdentity.provider == provider_key,
            EntityExternalIdentity.external_id == str(external_id).strip(),
        )
        .first()
    )
    if existing:
        if existing.entity_id != entity_id:
            raise EntityMetadataError("External identity already belongs to a different entity")
        existing.external_url = external_url
        existing.identity_status = identity_status
        existing.confidence = confidence
        existing.last_verified_at = last_verified_at
        existing.updated_at = datetime.now(timezone.utc)
        db.flush()
        return existing

    row = EntityExternalIdentity(
        entity_id=entity_id,
        provider=provider_key,
        external_id=str(external_id).strip(),
        external_url=external_url,
        identity_status=identity_status,
        confidence=confidence,
        last_verified_at=last_verified_at,
    )
    db.add(row)
    db.flush()
    return row


def record_evidence(
    db: Session,
    *,
    evidence_type: EntityEvidenceTypeEnum | str,
    source_type: str = "manual",
    provider: str | None = None,
    media_id: int | None = None,
    tag_id: int | None = None,
    entity_id: int | None = None,
    query_hash: str | None = None,
    payload_ref: str | None = None,
    score: float | None = None,
    summary: str | None = None,
    privacy_redacted: bool = True,
    observed_at: datetime | None = None,
) -> EntityEvidence:
    evidence_type = _coerce(EntityEvidenceTypeEnum, evidence_type, "evidence_type")
    _assert_public_safe_text(payload_ref, field_name="payload_ref")
    _assert_public_safe_text(summary, field_name="summary")
    if not privacy_redacted:
        raise EntityMetadataError("Entity evidence must be privacy_redacted in Phase 4.1")

    row = EntityEvidence(
        provider=normalize_entity_key(provider) if provider else None,
        source_type=normalize_entity_key(source_type),
        evidence_type=evidence_type,
        media_id=media_id,
        tag_id=tag_id,
        entity_id=entity_id,
        query_hash=query_hash,
        payload_ref=payload_ref,
        score=score,
        summary=summary,
        privacy_redacted=privacy_redacted,
        observed_at=observed_at or datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def create_candidate(
    db: Session,
    *,
    media_id: int,
    entity_type: EntityTypeEnum | str,
    candidate_name: str,
    entity_id: int | None = None,
    label: str | None = None,
    score: float | None = None,
    status: EntityCandidateStatusEnum | str = EntityCandidateStatusEnum.suggested,
    generator: EntityCandidateGeneratorEnum | str = EntityCandidateGeneratorEnum.manual,
    evidence_id: int | None = None,
    review_reason: str | None = None,
) -> MediaEntityCandidate:
    entity_type = _coerce(EntityTypeEnum, entity_type, "entity_type")
    status = _coerce(EntityCandidateStatusEnum, status, "status")
    generator = _coerce(EntityCandidateGeneratorEnum, generator, "generator")
    if not candidate_name or not candidate_name.strip():
        raise EntityMetadataError("candidate_name is required")

    row = MediaEntityCandidate(
        media_id=media_id,
        entity_id=entity_id,
        entity_type=entity_type,
        label=label,
        candidate_name=candidate_name.strip(),
        score=score,
        status=status,
        generator=generator,
        evidence_id=evidence_id,
        review_reason=review_reason,
    )
    db.add(row)
    db.flush()
    return row


def _confirmed_assignment_requires_provenance(
    *,
    review_status: EntityReviewStatusEnum,
    source: EntityMetadataSourceEnum,
    evidence_id: int | None,
) -> None:
    if review_status != EntityReviewStatusEnum.confirmed:
        return
    if source == EntityMetadataSourceEnum.manual:
        return
    if evidence_id is not None:
        return
    raise EntityMetadataError("Confirmed non-manual assignments require evidence provenance")


def create_or_update_assignment(
    db: Session,
    *,
    media_id: int,
    entity_id: int,
    role: MediaEntityRoleEnum | str,
    confidence: float | None = None,
    review_status: EntityReviewStatusEnum | str = EntityReviewStatusEnum.needs_review,
    source: EntityMetadataSourceEnum | str = EntityMetadataSourceEnum.manual,
    locked: bool = False,
    created_from_candidate_id: int | None = None,
    evidence_id: int | None = None,
) -> MediaEntityAssignment:
    role = _coerce(MediaEntityRoleEnum, role, "role")
    review_status = _coerce(EntityReviewStatusEnum, review_status, "review_status")
    source = _coerce(EntityMetadataSourceEnum, source, "source")
    _confirmed_assignment_requires_provenance(
        review_status=review_status,
        source=source,
        evidence_id=evidence_id,
    )

    existing = (
        db.query(MediaEntityAssignment)
        .filter(
            MediaEntityAssignment.media_id == media_id,
            MediaEntityAssignment.entity_id == entity_id,
            MediaEntityAssignment.role == role,
        )
        .first()
    )
    if existing:
        if existing.locked and source != EntityMetadataSourceEnum.manual:
            raise EntityMetadataError("Locked assignments can only be changed by explicit manual action")
        existing.confidence = confidence
        existing.review_status = review_status
        existing.source = source
        existing.locked = locked
        existing.created_from_candidate_id = created_from_candidate_id
        existing.evidence_id = evidence_id
        existing.updated_at = datetime.now(timezone.utc)
        db.flush()
        return existing

    row = MediaEntityAssignment(
        media_id=media_id,
        entity_id=entity_id,
        role=role,
        confidence=confidence,
        review_status=review_status,
        source=source,
        locked=locked,
        created_from_candidate_id=created_from_candidate_id,
        evidence_id=evidence_id,
    )
    db.add(row)
    db.flush()
    return row


def accept_candidate(
    db: Session,
    *,
    candidate_id: int,
    role: MediaEntityRoleEnum | str | None = None,
    source: EntityMetadataSourceEnum | str = EntityMetadataSourceEnum.manual,
    evidence_id: int | None = None,
    locked: bool = True,
) -> MediaEntityAssignment:
    candidate = db.query(MediaEntityCandidate).filter(MediaEntityCandidate.id == candidate_id).first()
    if candidate is None:
        raise EntityMetadataError("Candidate not found")
    if candidate.entity_id is None:
        raise EntityMetadataError("Candidate must be linked to an entity before acceptance")

    candidate.status = EntityCandidateStatusEnum.accepted
    candidate.updated_at = datetime.now(timezone.utc)
    effective_evidence_id = evidence_id if evidence_id is not None else candidate.evidence_id
    default_role = _enum_value(candidate.entity_type)
    role_values = {item.value for item in MediaEntityRoleEnum}
    assignment = create_or_update_assignment(
        db,
        media_id=candidate.media_id,
        entity_id=candidate.entity_id,
        role=role or (default_role if default_role in role_values else MediaEntityRoleEnum.primary),
        confidence=candidate.score,
        review_status=EntityReviewStatusEnum.confirmed,
        source=source,
        locked=locked,
        created_from_candidate_id=candidate.id,
        evidence_id=effective_evidence_id,
    )
    db.flush()
    return assignment


def reject_candidate(
    db: Session,
    *,
    candidate_id: int,
    review_reason: str | None = None,
) -> MediaEntityCandidate:
    candidate = db.query(MediaEntityCandidate).filter(MediaEntityCandidate.id == candidate_id).first()
    if candidate is None:
        raise EntityMetadataError("Candidate not found")
    candidate.status = EntityCandidateStatusEnum.rejected
    candidate.review_reason = review_reason
    candidate.updated_at = datetime.now(timezone.utc)
    db.flush()
    return candidate


def add_entity_translation(
    db: Session,
    *,
    entity_id: int,
    language: str,
    display_name: str,
    source: EntityMetadataSourceEnum | str = EntityMetadataSourceEnum.manual,
    status: EntityTranslationStatusEnum | str = EntityTranslationStatusEnum.needs_review,
    is_primary: bool = False,
) -> EntityTranslation:
    source = _coerce(EntityMetadataSourceEnum, source, "source")
    status = _coerce(EntityTranslationStatusEnum, status, "status")
    if not display_name or not display_name.strip():
        raise EntityMetadataError("display_name is required")

    existing = (
        db.query(EntityTranslation)
        .filter(
            EntityTranslation.entity_id == entity_id,
            EntityTranslation.language == language,
            EntityTranslation.display_name == display_name.strip(),
        )
        .first()
    )
    if existing:
        existing.source = source
        existing.status = status
        existing.is_primary = is_primary
        existing.updated_at = datetime.now(timezone.utc)
        db.flush()
        return existing

    row = EntityTranslation(
        entity_id=entity_id,
        language=language,
        display_name=display_name.strip(),
        source=source,
        status=status,
        is_primary=is_primary,
    )
    db.add(row)
    db.flush()
    return row


def list_media_entities(db: Session, media_id: int) -> list[MediaEntityAssignment]:
    return (
        db.query(MediaEntityAssignment)
        .filter(MediaEntityAssignment.media_id == media_id)
        .order_by(MediaEntityAssignment.role.asc(), MediaEntityAssignment.entity_id.asc())
        .all()
    )


def list_entity_aliases(db: Session, entity_id: int) -> list[EntityAlias]:
    return (
        db.query(EntityAlias)
        .filter(EntityAlias.entity_id == entity_id)
        .order_by(EntityAlias.is_primary.desc(), EntityAlias.alias.asc())
        .all()
    )


def is_external_lookup_allowed(
    content_class: ContentClassEnum | str | None,
    *,
    external_source: ExternalSource | None = None,
    provider_enabled: bool = False,
    allow_unknown: bool = False,
    allow_non_anime: bool = False,
) -> bool:
    """Phase 4 provider privacy gate.

    External lookup is denied by default.  Anime can be eligible only when the
    provider is explicitly enabled.  Unknown and non-anime/illustration stay
    blocked unless a future explicit provider policy opts them in.
    """
    if external_source is not None:
        provider_enabled = bool(external_source.enabled)
        policy = external_source.privacy_policy or {}
        if isinstance(policy, dict):
            allow_unknown = bool(policy.get("allow_unknown", allow_unknown))
            allow_non_anime = bool(policy.get("allow_non_anime", allow_non_anime))

    if not provider_enabled:
        return False

    normalized = _enum_value(content_class)
    if normalized == ContentClassEnum.anime.value:
        return True
    if normalized == ContentClassEnum.unknown.value:
        return allow_unknown
    if normalized in {ContentClassEnum.non_anime.value, ContentClassEnum.illustration.value}:
        return allow_non_anime
    return False
