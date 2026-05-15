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


class TranslationPatchRequest(BaseModel):
    """Request body for PATCH /translations/{id} — manual correction."""
    display_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    needs_review: Optional[bool] = None


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
    """Create or update a tag translation. Admin manual operations use force=True."""
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
        force=True,
    )

    invalidate_translation_cache()

    if trans is None:
        raise HTTPException(status_code=409, detail="Translation update blocked by priority")

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
        "base_url_configured": bool(settings.TAG_TRANSLATION_LLM_BASE_URL),
        "api_key_configured": bool(settings.TAG_TRANSLATION_LLM_API_KEY),
        "batch_max_items": settings.TAG_TRANSLATION_BATCH_MAX_ITEMS,
        "auto_enabled": settings.TAG_TRANSLATION_AUTO_ENABLED,
        "auto_max_items": settings.TAG_TRANSLATION_AUTO_MAX_ITEMS,
    }


@router.post("/test-llm")
async def test_llm_translation(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Test LLM by translating a single known tag (dry-run style)."""
    from ...services.llm_translation_provider import get_llm_provider

    provider = get_llm_provider()
    if not provider.is_available():
        return {"success": False, "error": "LLM provider not available or not configured"}

    try:
        results = await provider.translate_tags([{"name": "blue_eyes", "category": "general"}])
        if results:
            r = results[0]
            return {
                "success": True,
                "result": {
                    "canonical_name": r.canonical_name,
                    "display_name_zh": r.display_name_zh,
                    "aliases_zh": r.aliases_zh,
                    "needs_review": r.needs_review,
                },
            }
        return {"success": False, "error": "LLM returned empty results"}
    except Exception as e:
        return {"success": False, "error": str(e)}


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


@router.patch("/translations/{translation_id}")
async def patch_translation(
    translation_id: int,
    req: TranslationPatchRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """
    Manually correct an existing translation's display_name, aliases, and/or needs_review.
    Sets source='manual' and status='reviewed' to protect against future LLM overwrites.
    """
    from ...models import TagTranslation
    from ...utils.search_parser import invalidate_translation_cache

    # At least one field must be provided
    if req.display_name is None and req.aliases is None and req.needs_review is None:
        raise HTTPException(
            status_code=422,
            detail="At least one of display_name, aliases, or needs_review must be provided",
        )

    trans = db.query(TagTranslation).filter(TagTranslation.id == translation_id).first()
    if not trans:
        raise HTTPException(status_code=404, detail="Translation not found")

    # Validate display_name if provided
    if req.display_name is not None:
        cleaned = req.display_name.strip()
        if not cleaned:
            raise HTTPException(status_code=422, detail="display_name must not be empty")

    # Capture old values for the response diff
    old_display_name = trans.display_name
    old_aliases = _parse_aliases(trans.aliases_json)
    old_needs_review = trans.needs_review

    # Apply updates
    if req.display_name is not None:
        trans.display_name = req.display_name.strip()

    if req.aliases is not None:
        # Normalize aliases: trim whitespace, remove empty strings, deduplicate,
        # remove any alias that equals the (possibly updated) display_name
        current_display = trans.display_name  # use the possibly-updated value
        seen = set()
        normalized = []
        for alias in req.aliases:
            a = alias.strip()
            if a and a not in seen and a != current_display:
                seen.add(a)
                normalized.append(a)
        trans.aliases_json = json.dumps(normalized, ensure_ascii=False) if normalized else None

    if req.needs_review is not None:
        trans.needs_review = req.needs_review

    # Mark as manual correction
    trans.source = "manual"
    trans.status = "reviewed"

    db.commit()
    db.refresh(trans)
    invalidate_translation_cache()

    return {
        "id": trans.id,
        "canonical_name": trans.canonical_name,
        "display_name": trans.display_name,
        "aliases": _parse_aliases(trans.aliases_json),
        "needs_review": trans.needs_review,
        "source": trans.source,
        "status": trans.status,
        "old": {
            "display_name": old_display_name,
            "aliases": old_aliases,
            "needs_review": old_needs_review,
        },
        "message": "Translation updated",
    }


@router.get("/worker/status")
async def get_worker_status_endpoint(
    current_user: User = Depends(require_admin_mode),
):
    """Get background tag translation worker status."""
    from ...services.tag_translation_worker import get_worker_status
    return get_worker_status()


@router.post("/worker/run-now")
async def trigger_worker_run_now(
    current_user: User = Depends(require_admin_mode),
):
    """Trigger an immediate background translation run."""
    from ...services.tag_translation_worker import trigger_run_now, _worker_thread
    if not _worker_thread or not _worker_thread.is_alive():
        from ...services.tag_translation_worker import start_worker
        start_worker()
    trigger_run_now()
    return {"message": "Run-now triggered", "status": "ok"}


@router.post("/worker/pause")
async def pause_worker_endpoint(
    current_user: User = Depends(require_admin_mode),
):
    """Pause the background translation worker."""
    from ...services.tag_translation_worker import pause_worker
    pause_worker()
    return {"message": "Worker paused", "paused": True}


@router.post("/worker/resume")
async def resume_worker_endpoint(
    current_user: User = Depends(require_admin_mode),
):
    """Resume the background translation worker."""
    from ...services.tag_translation_worker import resume_worker
    resume_worker()
    return {"message": "Worker resumed", "paused": False}


@router.get("/worker/jobs")
async def list_worker_jobs(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """List recent background translation jobs."""
    from ...models import TagTranslationJob

    jobs = (
        db.query(TagTranslationJob)
        .order_by(TagTranslationJob.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "jobs": [
            {
                "id": j.id,
                "status": j.status,
                "source": j.source,
                "language": j.language,
                "batch_size": j.batch_size,
                "max_per_run": j.max_per_run,
                "processed": j.processed,
                "translated": j.translated,
                "failed": j.failed,
                "skipped": j.skipped,
                "remaining_before": j.remaining_before,
                "remaining_after": j.remaining_after,
                "last_error": j.last_error,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in jobs
        ],
        "total": len(jobs),
    }


# ── Entity Alias Resolver (Phase 2.3e) ──────────────────────────


@router.get("/entity/status")
async def get_entity_resolver_status_endpoint(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Get entity alias resolver status and statistics."""
    from ...services.entity_alias_resolver import get_entity_resolver_status

    return get_entity_resolver_status(db)


@router.get("/entity/pending")
async def list_pending_entity_tags(
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """List proper-noun tags pending alias resolution."""
    from ...services.entity_alias_resolver import list_pending_proper_nouns

    return list_pending_proper_nouns(db, limit=limit)


@router.post("/entity/resolve")
async def run_entity_resolution_endpoint(
    limit: Optional[int] = Query(None, ge=1, le=500),
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Run entity alias resolution for pending proper-noun tags."""
    from ...config import settings
    from ...services.entity_alias_resolver import run_entity_resolution
    from ...services.llm_translation_provider import (
        LLMAllProvidersFailed,
        LLMBatchAggregateError,
        LLMHTTPStatusError,
        LLMProviderError,
        LLMResponseFormatError,
        LLMTransportError,
    )

    if not settings.ENTITY_ALIAS_RESOLVER_ENABLED:
        raise HTTPException(status_code=400, detail="Entity alias resolver is disabled")

    try:
        return await run_entity_resolution(db, limit=limit)
    except LLMProviderError as e:
        if isinstance(e, LLMTransportError):
            raise HTTPException(status_code=502, detail={
                "error": "llm_transport_error",
                "message": "LLM 服务连接失败",
            })
        if isinstance(e, LLMAllProvidersFailed):
            raise HTTPException(status_code=502, detail={
                "error": "llm_all_providers_failed",
                "message": "所有 LLM 提供方均失败",
            })
        if isinstance(e, LLMResponseFormatError):
            raise HTTPException(status_code=502, detail={
                "error": "llm_response_format_error",
                "message": "LLM 返回格式异常",
            })
        if isinstance(e, LLMHTTPStatusError):
            raise HTTPException(status_code=502, detail={
                "error": "llm_http_error",
                "message": f"LLM API 返回 HTTP {e.status_code}",
            })
        if isinstance(e, LLMBatchAggregateError):
            raise HTTPException(status_code=502, detail={
                "error": "llm_batch_failed",
                "message": "LLM 批量处理全部失败",
            })
        if "already running" in str(e):
            raise HTTPException(status_code=409, detail={
                "error": "entity_resolve_conflict",
                "message": "实体别名解析正在进行中，请稍后再试",
            })
        raise HTTPException(status_code=502, detail={
            "error": "llm_provider_error",
            "message": "LLM 提供方错误",
        })


def _parse_aliases(aliases_json: Optional[str]) -> List[str]:
    if not aliases_json:
        return []
    try:
        return json.loads(aliases_json)
    except (json.JSONDecodeError, TypeError):
        return []
