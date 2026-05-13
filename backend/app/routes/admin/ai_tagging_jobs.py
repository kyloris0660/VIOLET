"""Admin API endpoints for AI tagging background jobs (Phase 2.3).

Provides CRUD + cancel for background AI tagging jobs.
Workers create their own DB sessions — request-scoped sessions are
not passed into background threads.
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...config import settings
from ...database import get_db
from ...models import AITagJob, User
from ...utils.logger import logger

router = APIRouter()


class CreateAITagJobRequest(BaseModel):
    media_ids: Optional[List[int]] = None
    max_items: int = Field(default=10, ge=1)
    dry_run: bool = False
    only_without_ai_tags: bool = True
    force_suggestions: bool = False
    content_class_filter: Optional[List[str]] = Field(
        default=None,
        description="Filter media by content_class (e.g. ['anime', 'illustration']). "
                    "Only media with matching content_class will be included. "
                    "None means no filtering (all classes)."
    )


def _serialize_ai_job(job: AITagJob) -> dict:
    """Convert an AITagJob ORM object to a JSON-safe dict."""
    media_ids = None
    if job.media_ids_json:
        try:
            media_ids = json.loads(job.media_ids_json)
        except (json.JSONDecodeError, TypeError):
            pass

    failed_items = []
    if job.failed_items_json:
        try:
            failed_items = json.loads(job.failed_items_json)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "id": job.id,
        "status": job.status,
        "trigger_source": job.trigger_source,
        "scan_job_id": job.scan_job_id,
        "media_ids": media_ids,
        "max_items": job.max_items,
        "dry_run": job.dry_run,
        "only_without_ai_tags": job.only_without_ai_tags,
        "force_suggestions": job.force_suggestions,
        "processed": job.processed,
        "tags_added": job.tags_added,
        "suggestions_added": job.suggestions_added,
        "skipped_locked": job.skipped_locked,
        "ignored_low_confidence": job.ignored_low_confidence,
        "failed": job.failed,
        "failed_items": failed_items,
        "error_message": job.error_message,
        "localization_status": job.localization_status,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.post("/ai-tagging/jobs")
async def create_ai_tag_job(
    body: CreateAITagJobRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Create a background AI tagging job. Returns immediately with a job_id."""
    if not settings.AI_TAGGING_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="AI tagging is disabled. Set AI_TAGGING_ENABLED=true in .env",
        )

    from ...services.ai_tagging_job_service import (
        create_ai_tag_job as _create_job,
        is_ai_job_active,
        start_ai_tag_job,
    )

    active = (
        db.query(AITagJob)
        .filter(AITagJob.status.in_(["pending", "running", "cancelling"]))
        .first()
    )
    if active or is_ai_job_active():
        raise HTTPException(
            status_code=409,
            detail="Another AI tagging job is already running",
        )

    hard_limit = settings.AI_TAGGING_BATCH_MAX_ITEMS
    if body.media_ids is not None and len(body.media_ids) > hard_limit:
        raise HTTPException(
            status_code=400,
            detail=f"Too many media_ids: {len(body.media_ids)} exceeds AI_TAGGING_BATCH_MAX_ITEMS={hard_limit}",
        )

    if body.media_ids is None and body.max_items > hard_limit:
        body.max_items = hard_limit

    # Pre-filter by content_class if requested (resolve to explicit media_ids)
    effective_media_ids = body.media_ids
    if body.content_class_filter is not None:
        # Empty list is invalid — caller must omit the field to disable filtering
        if len(body.content_class_filter) == 0:
            raise HTTPException(
                status_code=400,
                detail="content_class_filter must not be empty; omit it to disable filtering.",
            )
        if effective_media_ids is not None:
            raise HTTPException(
                status_code=400,
                detail="Cannot combine media_ids with content_class_filter. Use one or the other.",
            )
        from sqlalchemy import or_
        from ...models import Media
        from ...enums import ContentClassEnum
        valid_classes = []
        for cc in body.content_class_filter:
            try:
                valid_classes.append(ContentClassEnum(cc))
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid content_class value: {cc!r}. "
                           f"Valid values: {[e.value for e in ContentClassEnum]}",
                )
        # Build per-class conditions; "unknown" also matches NULL rows
        # (unclassified media), consistent with gallery content_class filtering.
        conditions = []
        for cls in valid_classes:
            conditions.append(Media.content_class == cls)
            if cls == ContentClassEnum.unknown:
                conditions.append(Media.content_class.is_(None))
        query = db.query(Media.id).filter(or_(*conditions))
        if body.only_without_ai_tags:
            from ...models import blombooru_media_tags
            ai_tagged = (
                db.query(blombooru_media_tags.c.media_id)
                .filter(blombooru_media_tags.c.source == "ai_wd")
                .distinct()
                .subquery()
            )
            query = query.filter(~Media.id.in_(ai_tagged))
        # Apply max_items DB-side to bound memory for large libraries
        query = query.order_by(Media.id.asc()).limit(body.max_items)
        effective_media_ids = [row[0] for row in query.all()]
        logger.info(
            f"AI tagging content_class_filter={body.content_class_filter}: "
            f"resolved {len(effective_media_ids)} media IDs (limit={body.max_items})"
        )
        # Zero-match rejection: do not create a fallback full-scope job.
        # If the filter matched nothing, it means no media in the requested
        # content classes are eligible — creating a job would be misleading.
        if not effective_media_ids:
            raise HTTPException(
                status_code=400,
                detail="content_class_filter resolved to zero eligible media; "
                       "no AI tagging job created.",
            )

    job = _create_job(
        db,
        media_ids=effective_media_ids,
        max_items=body.max_items,
        dry_run=body.dry_run,
        only_without_ai_tags=body.only_without_ai_tags,
        force_suggestions=body.force_suggestions,
        trigger_source="manual",
    )

    start_ai_tag_job(job.id)
    return _serialize_ai_job(job)


@router.get("/ai-tagging/jobs")
async def list_ai_tag_jobs(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Return the 20 most recent AI tagging jobs (newest first)."""
    jobs = (
        db.query(AITagJob)
        .order_by(AITagJob.created_at.desc())
        .limit(20)
        .all()
    )
    return [_serialize_ai_job(j) for j in jobs]


@router.get("/ai-tagging/jobs/{job_id}")
async def get_ai_tag_job(
    job_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Return the status and progress of a single AI tagging job."""
    job = db.query(AITagJob).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="AI tagging job not found")
    return _serialize_ai_job(job)


@router.post("/ai-tagging/jobs/{job_id}/cancel")
async def cancel_ai_tag_job(
    job_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Request cancellation of a running AI tagging job."""
    from ...services.ai_tagging_job_service import request_ai_job_cancel

    job = db.query(AITagJob).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="AI tagging job not found")

    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel a job with status '{job.status}'",
        )

    job.status = "cancelling"
    db.commit()
    request_ai_job_cancel(job_id)

    return _serialize_ai_job(job)


@router.get("/ai-tagging/auto-config")
async def get_auto_tag_config(
    current_user: User = Depends(require_admin_mode),
):
    """Return current auto-tagging-after-import configuration (read-only)."""
    return {
        "ai_tagging_enabled": settings.AI_TAGGING_ENABLED,
        "auto_tag_after_import": settings.AI_AUTO_TAG_AFTER_IMPORT,
        "auto_tag_max_items": settings.AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS,
        "auto_tag_only_new": settings.AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW,
        "auto_tag_dry_run": settings.AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN,
        "auto_tag_force_suggestions": settings.AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS,
        "batch_max_items": settings.AI_TAGGING_BATCH_MAX_ITEMS,
        "tag_translation_auto": settings.TAG_TRANSLATION_AUTO_ENABLED,
        "tag_translation_llm": settings.TAG_TRANSLATION_LLM_ENABLED,
    }
