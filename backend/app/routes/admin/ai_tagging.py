"""Admin API endpoints for AI auto tagging (Phase 2.1).

Workers create their own DB sessions to avoid passing request-scoped
sessions into run_in_threadpool (Session is not thread-safe).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ...auth import require_admin_mode
from ...config import settings
from ... import database as _db
from ...models import User
from ...utils.logger import logger


def _get_session():
    """Create a new DB session, initializing engine if needed."""
    if _db.SessionLocal is None:
        _db.init_engine()
    if _db.SessionLocal is None:
        raise RuntimeError("Database not initialized")
    return _db.SessionLocal()

router = APIRouter()


class BatchAITaggingRequest(BaseModel):
    media_ids: Optional[List[int]] = None
    max_items: int = Field(default=10, ge=1)
    dry_run: bool = False
    only_without_ai_tags: bool = True


@router.get("/ai-tagging/model-status")
async def get_model_status(
    current_user: User = Depends(require_admin_mode),
):
    """Check model availability and configuration. No DB needed."""
    from ...services.ai_tagging_service import check_model_status
    return check_model_status()


def _single_tag_worker(media_id: int, dry_run: bool):
    """Worker that creates its own DB session for single-image tagging."""
    from ...services.ai_tagging_service import run_ai_tagging

    db = _get_session()
    try:
        result = run_ai_tagging(db, media_id, dry_run=dry_run)
        return result
    finally:
        db.close()


def _batch_tag_worker(
    media_ids: Optional[List[int]],
    max_items: int,
    dry_run: bool,
    only_without_ai_tags: bool,
):
    """Worker that creates its own DB session for batch tagging."""
    from ...services.ai_tagging_service import run_ai_tagging_batch

    db = _get_session()
    try:
        result = run_ai_tagging_batch(
            db,
            media_ids=media_ids,
            max_items=max_items,
            dry_run=dry_run,
            only_without_ai_tags=only_without_ai_tags,
        )
        return result
    finally:
        db.close()


@router.post("/ai-tagging/media/{media_id}")
async def tag_single_media(
    media_id: int,
    dry_run: bool = False,
    current_user: User = Depends(require_admin_mode),
):
    """Run AI tagging on a single media item."""
    if not settings.AI_TAGGING_ENABLED:
        raise HTTPException(status_code=400, detail="AI tagging is disabled. Set AI_TAGGING_ENABLED=true in .env")

    try:
        result = await run_in_threadpool(_single_tag_worker, media_id, dry_run)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"AI tagger dependencies not available: {exc}")
    except Exception as exc:
        logger.error("AI tagging failed for media %d: %s", media_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"AI tagging failed: {exc}")

    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])

    return result


@router.post("/ai-tagging/batch")
async def tag_batch(
    body: BatchAITaggingRequest,
    current_user: User = Depends(require_admin_mode),
):
    """Run AI tagging on a batch of media items.

    Safety:
    - max_items is clamped to AI_TAGGING_BATCH_MAX_ITEMS from config.
    - If media_ids is omitted, only selects items without existing AI tags.
    - dry_run=true returns predictions without writing to DB.
    """
    if not settings.AI_TAGGING_ENABLED:
        raise HTTPException(status_code=400, detail="AI tagging is disabled. Set AI_TAGGING_ENABLED=true in .env")

    hard_limit = settings.AI_TAGGING_BATCH_MAX_ITEMS

    if body.media_ids is not None and len(body.media_ids) > hard_limit:
        raise HTTPException(
            status_code=400,
            detail=f"Too many media_ids: {len(body.media_ids)} exceeds AI_TAGGING_BATCH_MAX_ITEMS={hard_limit}",
        )

    try:
        result = await run_in_threadpool(
            _batch_tag_worker,
            body.media_ids,
            body.max_items,
            body.dry_run,
            body.only_without_ai_tags,
        )
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"AI tagger dependencies not available: {exc}")
    except Exception as exc:
        logger.error("Batch AI tagging failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch AI tagging failed: {exc}")

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    return result
