"""
Admin API for Tag Localization / Translation Management.
Phase 2.2.2 — Dynamic Tag Localization.
"""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...database import get_db
from ...models import User

router = APIRouter(prefix="/tag-localization", tags=["tag-localization"])


class TranslationUpsertRequest(BaseModel):
    canonical_name: str
    display_name: str
    language: str = "zh-CN"
    aliases: Optional[List[str]] = None
    category: Optional[str] = None
    source: str = "manual"
    status: str = "reviewed"
    needs_review: bool = False


class BatchTranslateRequest(BaseModel):
    dry_run: bool = True
    max_items: int = 50
    category: Optional[str] = None
    language: str = "zh-CN"


@router.get("/stats")
async def get_localization_stats(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Get tag translation statistics."""
    from ...services.tag_localization_service import get_translation_stats

    return get_translation_stats(db)


@router.get("/missing")
async def get_missing_translations(
    limit: int = Query(100, ge=1, le=500),
    category: Optional[str] = None,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """List tags without translations."""
    from ...services.tag_localization_service import list_missing_translations

    return list_missing_translations(db, limit=limit, category=category)


@router.get("/translations")
async def list_translations(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    source: Optional[str] = None,
    status: Optional[str] = None,
    needs_review: Optional[bool] = None,
    search: Optional[str] = None,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """List existing translations with filtering."""
    from ...models import TagTranslation

    query = db.query(TagTranslation).filter(TagTranslation.language == "zh-CN")

    if source:
        query = query.filter(TagTranslation.source == source)
    if status:
        query = query.filter(TagTranslation.status == status)
    if needs_review is not None:
        query = query.filter(TagTranslation.needs_review == needs_review)
    if search:
        query = query.filter(
            or_(
                TagTranslation.canonical_name.ilike(f"%{search}%"),
                TagTranslation.display_name.ilike(f"%{search}%"),
            )
        )

    total = query.count()
    items = query.order_by(TagTranslation.updated_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [
            {
                "id": t.id,
                "tag_id": t.tag_id,
                "canonical_name": t.canonical_name,
                "display_name": t.display_name,
                "aliases": _parse_aliases(t.aliases_json),
                "category": t.category,
                "source": t.source,
                "status": t.status,
                "confidence": t.confidence,
                "needs_review": t.needs_review,
                "provider": t.provider,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/translations")
async def upsert_translation_endpoint(
    req: TranslationUpsertRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Create or update a tag translation."""
    from ...services.tag_localization_service import upsert_translation
    from ...utils.search_parser import invalidate_translation_cache

    trans = upsert_translation(
        db,
        canonical_name=req.canonical_name,
        display_name=req.display_name,
        lang=req.language,
        aliases=req.aliases,
        category=req.category,
        source=req.source,
        status=req.status,
        needs_review=req.needs_review,
    )

    invalidate_translation_cache()

    return {
        "id": trans.id,
        "canonical_name": trans.canonical_name,
        "display_name": trans.display_name,
        "source": trans.source,
        "status": trans.status,
        "message": "Translation saved",
    }


@router.delete("/translations/{translation_id}")
async def delete_translation(
    translation_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Delete a translation."""
    from ...models import TagTranslation
    from ...utils.search_parser import invalidate_translation_cache

    trans = db.query(TagTranslation).filter(TagTranslation.id == translation_id).first()
    if not trans:
        raise HTTPException(status_code=404, detail="Translation not found")

    db.delete(trans)
    db.commit()
    invalidate_translation_cache()

    return {"message": "Translation deleted"}


@router.post("/batch-translate")
async def batch_translate(
    req: BatchTranslateRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Batch translate missing tags using LLM."""
    from ...services.tag_localization_service import batch_translate_missing_tags
    from ...utils.search_parser import invalidate_translation_cache

    result = await batch_translate_missing_tags(
        db,
        dry_run=req.dry_run,
        max_items=req.max_items,
        category=req.category,
        lang=req.language,
    )

    if not req.dry_run:
        invalidate_translation_cache()

    return result


@router.get("/llm-status")
async def get_llm_status(
    current_user: User = Depends(require_admin_mode),
):
    """Check LLM translation provider status."""
    from ...config import settings
    from ...services.llm_translation_provider import get_llm_provider

    provider = get_llm_provider()
    return {
        "enabled": settings.TAG_TRANSLATION_LLM_ENABLED,
        "provider": provider.get_provider_name(),
        "available": provider.is_available(),
        "model": settings.TAG_TRANSLATION_LLM_MODEL or None,
        "batch_max_items": settings.TAG_TRANSLATION_BATCH_MAX_ITEMS,
    }


@router.post("/translations/{translation_id}/review")
async def review_translation(
    translation_id: int,
    action: str = Query(..., pattern="^(approve|reject)$"),
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Approve or reject a translation."""
    from ...models import TagTranslation
    from ...utils.search_parser import invalidate_translation_cache

    trans = db.query(TagTranslation).filter(TagTranslation.id == translation_id).first()
    if not trans:
        raise HTTPException(status_code=404, detail="Translation not found")

    if action == "approve":
        trans.status = "reviewed"
        trans.needs_review = False
    elif action == "reject":
        trans.status = "rejected"
        trans.needs_review = False

    db.commit()
    invalidate_translation_cache()

    return {"message": f"Translation {action}d", "id": translation_id, "status": trans.status}


def _parse_aliases(aliases_json: Optional[str]) -> List[str]:
    if not aliases_json:
        return []
    try:
        return json.loads(aliases_json)
    except (json.JSONDecodeError, TypeError):
        return []
