"""Admin entity metadata correction endpoints.

Phase 4.2 keeps this surface local and manual: no provider calls, no automatic
candidate generation, and no writes to media_tags.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...database import get_db
from ...enums import (
    EntityAliasTypeEnum,
    EntityCandidateStatusEnum,
    EntityEvidenceTypeEnum,
    EntityMetadataSourceEnum,
    EntityReviewStatusEnum,
    EntityTranslationStatusEnum,
    EntityTypeEnum,
    MediaEntityRoleEnum,
)
from ...models import (
    Entity,
    EntityAlias,
    EntityExternalIdentity,
    EntityTranslation,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    User,
)
from ...services.entity_metadata_service import (
    EntityMetadataError,
    accept_candidate,
    add_alias,
    add_entity_translation,
    create_entity,
    create_or_update_assignment,
    normalize_entity_key,
    record_evidence,
    reject_candidate,
)
from ...utils.request_helpers import safe_error_detail

router = APIRouter(tags=["entity-metadata"])


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _entity_or_404(db: Session, entity_id: int) -> Entity:
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


def _media_or_404(db: Session, media_id: int) -> Media:
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


def _assignment_or_404(db: Session, media_id: int, assignment_id: int) -> MediaEntityAssignment:
    assignment = db.get(MediaEntityAssignment, assignment_id)
    if assignment is None or assignment.media_id != media_id:
        raise HTTPException(status_code=404, detail="Entity assignment not found")
    return assignment


def _serialize_alias(alias: EntityAlias) -> dict[str, Any]:
    return {
        "id": alias.id,
        "entity_id": alias.entity_id,
        "alias": alias.alias,
        "normalized_alias": alias.normalized_alias,
        "language": alias.language,
        "alias_type": _enum_value(alias.alias_type),
        "source": _enum_value(alias.source),
        "confidence": alias.confidence,
        "is_primary": bool(alias.is_primary),
        "needs_review": bool(alias.needs_review),
        "created_at": _iso(alias.created_at),
        "updated_at": _iso(alias.updated_at),
    }


def _serialize_translation(row: EntityTranslation) -> dict[str, Any]:
    return {
        "id": row.id,
        "entity_id": row.entity_id,
        "language": row.language,
        "display_name": row.display_name,
        "source": _enum_value(row.source),
        "status": _enum_value(row.status),
        "is_primary": bool(row.is_primary),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _serialize_external_identity(row: EntityExternalIdentity) -> dict[str, Any]:
    return {
        "id": row.id,
        "entity_id": row.entity_id,
        "provider": row.provider,
        "external_id": row.external_id,
        "external_url": row.external_url,
        "identity_status": _enum_value(row.identity_status),
        "confidence": row.confidence,
        "last_verified_at": _iso(row.last_verified_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _serialize_entity(entity: Entity, *, assignment_count: int | None = None) -> dict[str, Any]:
    return {
        "id": entity.id,
        "type": _enum_value(entity.type),
        "canonical_name": entity.canonical_name,
        "normalized_key": entity.normalized_key,
        "slug": entity.slug,
        "status": _enum_value(entity.status),
        "description": entity.description,
        "assignment_count": int(assignment_count or 0),
        "created_at": _iso(entity.created_at),
        "updated_at": _iso(entity.updated_at),
    }


def _serialize_assignment(row: MediaEntityAssignment) -> dict[str, Any]:
    entity = row.entity
    return {
        "id": row.id,
        "media_id": row.media_id,
        "entity_id": row.entity_id,
        "entity": _serialize_entity(entity) if entity else None,
        "role": _enum_value(row.role),
        "confidence": row.confidence,
        "review_status": _enum_value(row.review_status),
        "source": _enum_value(row.source),
        "locked": bool(row.locked),
        "created_from_candidate_id": row.created_from_candidate_id,
        "evidence_id": row.evidence_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _serialize_candidate(row: MediaEntityCandidate) -> dict[str, Any]:
    evidence = row.evidence
    return {
        "id": row.id,
        "media_id": row.media_id,
        "entity_id": row.entity_id,
        "entity": _serialize_entity(row.entity) if row.entity else None,
        "entity_type": _enum_value(row.entity_type),
        "label": row.label,
        "candidate_name": row.candidate_name,
        "score": row.score,
        "status": _enum_value(row.status),
        "generator": _enum_value(row.generator),
        "evidence_id": row.evidence_id,
        "evidence": {
            "id": evidence.id,
            "source_type": evidence.source_type,
            "evidence_type": _enum_value(evidence.evidence_type),
            "summary": evidence.summary,
            "score": evidence.score,
            "privacy_redacted": bool(evidence.privacy_redacted),
        } if evidence else None,
        "review_reason": row.review_reason,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


class EntityCreateRequest(BaseModel):
    entity_type: EntityTypeEnum
    canonical_name: str = Field(..., min_length=1, max_length=500)
    slug: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2000)


class EntityAliasCreateRequest(BaseModel):
    alias: str = Field(..., min_length=1, max_length=500)
    language: str | None = Field(default=None, max_length=20)
    alias_type: EntityAliasTypeEnum = EntityAliasTypeEnum.search
    is_primary: bool = False


class EntityTranslationCreateRequest(BaseModel):
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    display_name: str = Field(..., min_length=1, max_length=500)
    status: EntityTranslationStatusEnum = EntityTranslationStatusEnum.confirmed
    is_primary: bool = False


class MediaEntityAssignmentCreateRequest(BaseModel):
    entity_id: int = Field(..., ge=1)
    role: MediaEntityRoleEnum
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    locked: bool = True
    allow_locked_update: bool = False
    note: str | None = Field(default=None, max_length=1000)


class MediaEntityAssignmentPatchRequest(BaseModel):
    role: MediaEntityRoleEnum | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    review_status: EntityReviewStatusEnum | None = None
    locked: bool | None = None
    allow_locked_update: bool = False
    note: str | None = Field(default=None, max_length=1000)


class MediaEntityAssignmentRejectRequest(BaseModel):
    review_reason: str | None = Field(default=None, max_length=1000)


class CandidateAcceptRequest(BaseModel):
    role: MediaEntityRoleEnum | None = None
    locked: bool = True


class CandidateRejectRequest(BaseModel):
    review_reason: str | None = Field(default=None, max_length=1000)


@router.get("/entities")
async def list_entities(
    search: str | None = Query(default=None, max_length=500),
    entity_type: EntityTypeEnum | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    query = db.query(Entity)

    if entity_type is not None:
        query = query.filter(Entity.type == entity_type)

    if search and search.strip():
        raw_search = search.strip()
        normalized = normalize_entity_key(raw_search)
        raw_pattern = f"%{raw_search}%"
        normalized_pattern = f"%{normalized}%"
        query = (
            query.outerjoin(EntityAlias)
            .filter(
                or_(
                    Entity.canonical_name.ilike(raw_pattern),
                    Entity.normalized_key.ilike(normalized_pattern),
                    EntityAlias.alias.ilike(raw_pattern),
                    EntityAlias.normalized_alias.ilike(normalized_pattern),
                )
            )
            .distinct()
        )

    total = query.count()
    entities = query.order_by(Entity.type.asc(), Entity.canonical_name.asc()).offset(offset).limit(limit).all()
    entity_ids = [entity.id for entity in entities]
    assignment_counts = {}
    if entity_ids:
        rows = (
            db.query(MediaEntityAssignment.entity_id, func.count(MediaEntityAssignment.id))
            .filter(
                MediaEntityAssignment.entity_id.in_(entity_ids),
                MediaEntityAssignment.review_status == EntityReviewStatusEnum.confirmed,
            )
            .group_by(MediaEntityAssignment.entity_id)
            .all()
        )
        assignment_counts = {int(entity_id): int(count) for entity_id, count in rows}

    return {
        "items": [
            _serialize_entity(entity, assignment_count=assignment_counts.get(entity.id, 0))
            for entity in entities
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "review_model": "targeted_correction",
        "exhaustive_review_required": False,
    }


@router.post("/entities")
async def create_entity_endpoint(
    req: EntityCreateRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    try:
        entity = create_entity(
            db,
            entity_type=req.entity_type,
            canonical_name=req.canonical_name,
            slug=req.slug,
            description=req.description,
        )
        db.commit()
        db.refresh(entity)
        return {"status": "ok", "entity": _serialize_entity(entity)}
    except EntityMetadataError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Entity already exists or violates a uniqueness rule") from exc


@router.get("/entities/{entity_id}")
async def get_entity_detail(
    entity_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    entity = _entity_or_404(db, entity_id)
    assignment_count = (
        db.query(func.count(MediaEntityAssignment.id))
        .filter(
            MediaEntityAssignment.entity_id == entity.id,
            MediaEntityAssignment.review_status == EntityReviewStatusEnum.confirmed,
        )
        .scalar()
        or 0
    )
    return {
        "entity": _serialize_entity(entity, assignment_count=assignment_count),
        "aliases": [_serialize_alias(row) for row in entity.aliases],
        "translations": [_serialize_translation(row) for row in entity.translations],
        "external_identities": [_serialize_external_identity(row) for row in entity.external_identities],
    }


@router.post("/entities/{entity_id}/aliases")
async def add_entity_alias_endpoint(
    entity_id: int,
    req: EntityAliasCreateRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    _entity_or_404(db, entity_id)
    try:
        alias = add_alias(
            db,
            entity_id=entity_id,
            alias=req.alias,
            language=req.language,
            alias_type=req.alias_type,
            source=EntityMetadataSourceEnum.manual,
            is_primary=req.is_primary,
            needs_review=False,
        )
        db.commit()
        db.refresh(alias)
        return {"status": "ok", "alias": _serialize_alias(alias)}
    except EntityMetadataError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Alias already exists or violates a uniqueness rule") from exc


@router.post("/entities/{entity_id}/translations")
async def add_entity_translation_endpoint(
    entity_id: int,
    req: EntityTranslationCreateRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    _entity_or_404(db, entity_id)
    try:
        translation = add_entity_translation(
            db,
            entity_id=entity_id,
            language=req.language,
            display_name=req.display_name,
            source=EntityMetadataSourceEnum.manual,
            status=req.status,
            is_primary=req.is_primary,
        )
        db.commit()
        db.refresh(translation)
        return {"status": "ok", "translation": _serialize_translation(translation)}
    except EntityMetadataError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Translation already exists or violates a uniqueness rule") from exc


@router.get("/media/{media_id}/entity-assignments")
async def list_media_entity_assignments(
    media_id: int,
    include_all: bool = Query(default=False),
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    _media_or_404(db, media_id)
    query = db.query(MediaEntityAssignment).filter(MediaEntityAssignment.media_id == media_id)
    if not include_all:
        query = query.filter(MediaEntityAssignment.review_status == EntityReviewStatusEnum.confirmed)
    rows = query.order_by(MediaEntityAssignment.role.asc(), MediaEntityAssignment.entity_id.asc()).all()
    return {
        "media_id": media_id,
        "items": [_serialize_assignment(row) for row in rows],
        "include_all": include_all,
    }


@router.post("/media/{media_id}/entity-assignments")
async def assign_entity_to_media(
    media_id: int,
    req: MediaEntityAssignmentCreateRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    _media_or_404(db, media_id)
    _entity_or_404(db, req.entity_id)
    existing = (
        db.query(MediaEntityAssignment)
        .filter(
            MediaEntityAssignment.media_id == media_id,
            MediaEntityAssignment.entity_id == req.entity_id,
            MediaEntityAssignment.role == req.role,
        )
        .first()
    )
    if existing and existing.locked and not req.allow_locked_update:
        raise HTTPException(
            status_code=409,
            detail="Assignment is locked; set allow_locked_update=true for an explicit manual correction",
        )

    try:
        evidence = record_evidence(
            db,
            evidence_type=EntityEvidenceTypeEnum.user_confirmation,
            source_type="manual",
            media_id=media_id,
            entity_id=req.entity_id,
            summary=req.note or "Manual entity assignment via admin correction.",
        )
        assignment = create_or_update_assignment(
            db,
            media_id=media_id,
            entity_id=req.entity_id,
            role=req.role,
            confidence=req.confidence,
            review_status=EntityReviewStatusEnum.confirmed,
            source=EntityMetadataSourceEnum.manual,
            locked=req.locked,
            evidence_id=evidence.id,
        )
        db.commit()
        db.refresh(assignment)
        return {"status": "ok", "assignment": _serialize_assignment(assignment)}
    except EntityMetadataError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Assignment already exists or violates a uniqueness rule") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=safe_error_detail("Failed to assign entity", exc)) from exc


@router.patch("/media/{media_id}/entity-assignments/{assignment_id}")
async def patch_media_entity_assignment(
    media_id: int,
    assignment_id: int,
    req: MediaEntityAssignmentPatchRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    assignment = _assignment_or_404(db, media_id, assignment_id)
    if assignment.locked and not req.allow_locked_update:
        raise HTTPException(
            status_code=409,
            detail="Assignment is locked; set allow_locked_update=true for an explicit manual correction",
        )

    new_role = req.role or assignment.role
    duplicate = (
        db.query(MediaEntityAssignment)
        .filter(
            MediaEntityAssignment.media_id == media_id,
            MediaEntityAssignment.entity_id == assignment.entity_id,
            MediaEntityAssignment.role == new_role,
            MediaEntityAssignment.id != assignment.id,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Another assignment already uses this media/entity/role")

    try:
        evidence = record_evidence(
            db,
            evidence_type=EntityEvidenceTypeEnum.user_confirmation,
            source_type="manual",
            media_id=media_id,
            entity_id=assignment.entity_id,
            summary=req.note or "Manual entity assignment correction via admin.",
        )
        assignment.role = new_role
        if req.confidence is not None:
            assignment.confidence = req.confidence
        if req.review_status is not None:
            assignment.review_status = req.review_status
        assignment.source = EntityMetadataSourceEnum.manual
        if req.locked is not None:
            assignment.locked = req.locked
        assignment.evidence_id = evidence.id
        assignment.updated_at = datetime.now(timezone.utc)
        db.flush()
        db.commit()
        db.refresh(assignment)
        return {"status": "ok", "assignment": _serialize_assignment(assignment)}
    except EntityMetadataError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Assignment correction violates a uniqueness rule") from exc


@router.post("/media/{media_id}/entity-assignments/{assignment_id}/reject")
async def reject_media_entity_assignment(
    media_id: int,
    assignment_id: int,
    req: MediaEntityAssignmentRejectRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    assignment = _assignment_or_404(db, media_id, assignment_id)
    try:
        evidence = record_evidence(
            db,
            evidence_type=EntityEvidenceTypeEnum.user_confirmation,
            source_type="manual",
            media_id=media_id,
            entity_id=assignment.entity_id,
            summary=req.review_reason or "Manual entity assignment rejected via admin correction.",
        )
        assignment.review_status = EntityReviewStatusEnum.rejected
        assignment.source = EntityMetadataSourceEnum.manual
        assignment.locked = True
        assignment.evidence_id = evidence.id
        assignment.updated_at = datetime.now(timezone.utc)
        db.flush()
        db.commit()
        db.refresh(assignment)
        return {"status": "rejected", "assignment": _serialize_assignment(assignment)}
    except EntityMetadataError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/entity-candidates")
async def list_entity_candidates(
    status: EntityCandidateStatusEnum | None = Query(default=EntityCandidateStatusEnum.suggested),
    media_id: int | None = Query(default=None, ge=1),
    entity_type: EntityTypeEnum | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    query = db.query(MediaEntityCandidate)
    if status is not None:
        query = query.filter(MediaEntityCandidate.status == status)
    if media_id is not None:
        query = query.filter(MediaEntityCandidate.media_id == media_id)
    if entity_type is not None:
        query = query.filter(MediaEntityCandidate.entity_type == entity_type)

    total = query.count()
    rows = (
        query.order_by(MediaEntityCandidate.updated_at.desc(), MediaEntityCandidate.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [_serialize_candidate(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "review_model": "targeted_correction",
        "exhaustive_review_required": False,
    }


@router.post("/entity-candidates/{candidate_id}/accept")
async def accept_entity_candidate(
    candidate_id: int,
    req: CandidateAcceptRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    try:
        assignment = accept_candidate(
            db,
            candidate_id=candidate_id,
            role=req.role,
            source=EntityMetadataSourceEnum.manual,
            locked=req.locked,
        )
        db.commit()
        db.refresh(assignment)
        return {"status": "accepted", "assignment": _serialize_assignment(assignment)}
    except EntityMetadataError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Candidate acceptance violates a uniqueness rule") from exc


@router.post("/entity-candidates/{candidate_id}/reject")
async def reject_entity_candidate(
    candidate_id: int,
    req: CandidateRejectRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    try:
        candidate = reject_candidate(db, candidate_id=candidate_id, review_reason=req.review_reason)
        db.commit()
        db.refresh(candidate)
        return {"status": "rejected", "candidate": _serialize_candidate(candidate)}
    except EntityMetadataError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
