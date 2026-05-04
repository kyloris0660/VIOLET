"""Admin API endpoints for AI Tag Review (Phase 2.2).

Provides endpoints to list, confirm, reject, lock, delete, and bulk-manage
AI-generated tag suggestions.
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, asc, func
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...database import get_db
from ...models import Media, Tag, User, blombooru_media_tags
from ...services.tag_service import (
    TAG_SOURCE_MANUAL,
    confirm_suggestion,
    reject_suggestion,
    remove_tag_from_media,
    update_tag_provenance,
)
from ...utils.logger import logger

router = APIRouter()

BULK_LIMIT = 100


class BulkItem(BaseModel):
    media_id: int
    tag_id: int


class BulkRequest(BaseModel):
    action: Literal["confirm", "reject", "lock", "delete"]
    items: List[BulkItem] = Field(..., max_length=BULK_LIMIT)


@router.get("/ai-tags/review")
async def list_review_suggestions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    min_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    max_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    tag_name: Optional[str] = Query(default=None),
    media_id: Optional[int] = Query(default=None),
    source: Optional[str] = Query(default=None),
    order: Optional[str] = Query(default="confidence_desc"),
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """List AI suggestion tags pending review.

    Returns suggestions (is_suggestion=true) with media and tag info.
    Does NOT include manual/locked tags.
    """
    query = (
        db.query(
            blombooru_media_tags.c.media_id,
            blombooru_media_tags.c.tag_id,
            blombooru_media_tags.c.source,
            blombooru_media_tags.c.confidence,
            blombooru_media_tags.c.is_locked,
            blombooru_media_tags.c.is_suggestion,
            blombooru_media_tags.c.created_at,
            blombooru_media_tags.c.updated_at,
            Tag.name.label("tag_name"),
            Tag.category.label("tag_category"),
            Media.thumbnail_path,
            Media.filename.label("media_filename"),
        )
        .join(Tag, blombooru_media_tags.c.tag_id == Tag.id)
        .join(Media, blombooru_media_tags.c.media_id == Media.id)
        .filter(blombooru_media_tags.c.is_suggestion == True)
    )

    if min_confidence is not None:
        query = query.filter(blombooru_media_tags.c.confidence >= min_confidence)
    if max_confidence is not None:
        query = query.filter(blombooru_media_tags.c.confidence <= max_confidence)
    if tag_name:
        query = query.filter(Tag.name.ilike(f"%{tag_name}%"))
    if media_id is not None:
        query = query.filter(blombooru_media_tags.c.media_id == media_id)
    if source:
        query = query.filter(blombooru_media_tags.c.source == source)

    # Count before pagination
    total = query.count()

    # Ordering
    if order == "confidence_asc":
        query = query.order_by(asc(blombooru_media_tags.c.confidence))
    elif order == "created_desc":
        query = query.order_by(desc(blombooru_media_tags.c.created_at))
    else:
        query = query.order_by(desc(blombooru_media_tags.c.confidence))

    rows = query.offset(offset).limit(limit).all()

    items = []
    for row in rows:
        items.append({
            "media_id": row.media_id,
            "tag_id": row.tag_id,
            "tag_name": row.tag_name,
            "tag_category": row.tag_category.value if row.tag_category else "general",
            "source": row.source,
            "confidence": round(row.confidence, 4) if row.confidence else None,
            "is_locked": row.is_locked,
            "is_suggestion": row.is_suggestion,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "thumbnail_url": f"/api/media/{row.media_id}/thumbnail" if row.thumbnail_path else None,
            "media_filename": row.media_filename,
        })

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/ai-tags/{media_id}/{tag_id}/confirm")
async def confirm_ai_tag(
    media_id: int,
    tag_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Confirm an AI suggestion tag.

    Sets is_suggestion=false, is_locked=true. Preserves source and confidence.
    """
    updated = confirm_suggestion(db, media_id, tag_id, preserve_source=True)
    if not updated:
        existing = db.execute(
            blombooru_media_tags.select().where(
                and_(
                    blombooru_media_tags.c.media_id == media_id,
                    blombooru_media_tags.c.tag_id == tag_id,
                )
            )
        ).first()
        if not existing:
            raise HTTPException(status_code=404, detail="Tag association not found")
        if not existing.is_suggestion:
            raise HTTPException(status_code=400, detail="Tag is already confirmed")
    db.commit()
    _update_tag_count(db, tag_id)
    return {"status": "confirmed", "media_id": media_id, "tag_id": tag_id}


@router.post("/ai-tags/{media_id}/{tag_id}/reject")
async def reject_ai_tag(
    media_id: int,
    tag_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Reject an AI suggestion tag (deletes the association).

    Only deletes if is_suggestion=true. Does not affect manual/locked tags.
    Note: Current reject does NOT persist the negative decision. If AI tagging
    runs again, the same suggestion may be regenerated.
    """
    deleted = reject_suggestion(db, media_id, tag_id)
    if not deleted:
        existing = db.execute(
            blombooru_media_tags.select().where(
                and_(
                    blombooru_media_tags.c.media_id == media_id,
                    blombooru_media_tags.c.tag_id == tag_id,
                )
            )
        ).first()
        if not existing:
            raise HTTPException(status_code=404, detail="Tag association not found")
        if not existing.is_suggestion:
            raise HTTPException(
                status_code=400,
                detail="Cannot reject a confirmed tag. Use delete instead.",
            )
    db.commit()
    return {"status": "rejected", "media_id": media_id, "tag_id": tag_id}


@router.post("/ai-tags/{media_id}/{tag_id}/lock")
async def lock_ai_tag(
    media_id: int,
    tag_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Lock a tag (sets is_locked=true, is_suggestion=false)."""
    updated = update_tag_provenance(
        db, media_id, tag_id, is_locked=True, is_suggestion=False
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Tag association not found")
    db.commit()
    _update_tag_count(db, tag_id)
    return {"status": "locked", "media_id": media_id, "tag_id": tag_id}


@router.delete("/ai-tags/{media_id}/{tag_id}")
async def delete_ai_tag(
    media_id: int,
    tag_id: int,
    force: bool = Query(default=False),
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Delete an AI tag association.

    By default, refuses to delete manual+locked tags (returns 400).
    Set force=true to override this protection.
    """
    existing = db.execute(
        blombooru_media_tags.select().where(
            and_(
                blombooru_media_tags.c.media_id == media_id,
                blombooru_media_tags.c.tag_id == tag_id,
            )
        )
    ).first()

    if not existing:
        raise HTTPException(status_code=404, detail="Tag association not found")

    if existing.source == TAG_SOURCE_MANUAL and existing.is_locked and not force:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a manual locked tag. Use force=true to override.",
        )

    removed = remove_tag_from_media(db, media_id, tag_id)
    if not removed:
        raise HTTPException(status_code=500, detail="Failed to remove tag")
    db.commit()
    _update_tag_count(db, tag_id)
    return {"status": "deleted", "media_id": media_id, "tag_id": tag_id}


@router.post("/ai-tags/bulk")
async def bulk_ai_tag_action(
    body: BulkRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Perform bulk operations on AI tag associations.

    Supports: confirm, reject, lock, delete.
    Max 100 items per request.
    Individual failures do not abort the entire batch.
    """
    if len(body.items) > BULK_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"Too many items: {len(body.items)} exceeds max {BULK_LIMIT}",
        )

    results = {"success": 0, "failed": 0, "errors": []}
    affected_tag_ids = set()

    for item in body.items:
        try:
            if body.action == "confirm":
                ok = confirm_suggestion(db, item.media_id, item.tag_id, preserve_source=True)
                if ok:
                    affected_tag_ids.add(item.tag_id)
                else:
                    results["errors"].append(
                        {"media_id": item.media_id, "tag_id": item.tag_id, "error": "not a suggestion or not found"}
                    )
                    results["failed"] += 1
                    continue
            elif body.action == "reject":
                ok = reject_suggestion(db, item.media_id, item.tag_id)
                if not ok:
                    results["errors"].append(
                        {"media_id": item.media_id, "tag_id": item.tag_id, "error": "not a suggestion or not found"}
                    )
                    results["failed"] += 1
                    continue
            elif body.action == "lock":
                ok = update_tag_provenance(
                    db, item.media_id, item.tag_id, is_locked=True, is_suggestion=False
                )
                if ok:
                    affected_tag_ids.add(item.tag_id)
                else:
                    results["errors"].append(
                        {"media_id": item.media_id, "tag_id": item.tag_id, "error": "not found"}
                    )
                    results["failed"] += 1
                    continue
            elif body.action == "delete":
                existing = db.execute(
                    blombooru_media_tags.select().where(
                        and_(
                            blombooru_media_tags.c.media_id == item.media_id,
                            blombooru_media_tags.c.tag_id == item.tag_id,
                        )
                    )
                ).first()
                if not existing:
                    results["errors"].append(
                        {"media_id": item.media_id, "tag_id": item.tag_id, "error": "not found"}
                    )
                    results["failed"] += 1
                    continue
                if existing.source == TAG_SOURCE_MANUAL and existing.is_locked:
                    results["errors"].append(
                        {"media_id": item.media_id, "tag_id": item.tag_id, "error": "manual locked tag, skipped"}
                    )
                    results["failed"] += 1
                    continue
                remove_tag_from_media(db, item.media_id, item.tag_id)
                affected_tag_ids.add(item.tag_id)

            results["success"] += 1
        except Exception as exc:
            logger.error("Bulk action failed for media=%d tag=%d: %s", item.media_id, item.tag_id, exc)
            results["errors"].append(
                {"media_id": item.media_id, "tag_id": item.tag_id, "error": str(exc)}
            )
            results["failed"] += 1

    db.commit()

    if affected_tag_ids:
        _update_tag_counts_batch(db, list(affected_tag_ids))

    return {
        "action": body.action,
        "total": len(body.items),
        **results,
    }


def _update_tag_count(db: Session, tag_id: int):
    """Update post_count for a single tag after review action."""
    from ...routes.media import update_tag_counts
    update_tag_counts(db, [tag_id])
    db.commit()


def _update_tag_counts_batch(db: Session, tag_ids: List[int]):
    """Update post_counts for multiple tags after bulk action."""
    from ...routes.media import update_tag_counts
    update_tag_counts(db, tag_ids)
    db.commit()
