"""Admin API endpoints for content classification (Phase 3).

Provides CRUD + cancel for background classification jobs, single-media
classification, stats, and read-only config status.
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from ...auth import require_admin_mode
from ...config import settings
from ...database import get_db
from ...models import ClassificationJob, Media, User
from ...enums import ContentClassEnum
from ...utils.logger import logger

router = APIRouter()


class CreateClassificationJobRequest(BaseModel):
    media_ids: Optional[List[int]] = None
    max_items: int = Field(default=100, ge=1)
    only_unclassified: bool = True
    force_reclassify: bool = False


class UpdateMediaClassRequest(BaseModel):
    content_class: ContentClassEnum
    lock: Optional[bool] = None


def _serialize_classification_job(job: ClassificationJob) -> dict:
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
        "only_unclassified": job.only_unclassified,
        "force_reclassify": getattr(job, 'force_reclassify', False),
        "processed": job.processed,
        "classified_anime": job.classified_anime,
        "classified_non_anime": job.classified_non_anime,
        "classified_unknown": job.classified_unknown,
        "failed": job.failed,
        "failed_items": failed_items,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.post("/content-classification/jobs")
async def create_classification_job(
    body: CreateClassificationJobRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    if not settings.CONTENT_CLASSIFICATION_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Content classification is disabled. Set CONTENT_CLASSIFICATION_ENABLED=true in .env",
        )

    from ...services.classification_job_service import (
        create_classification_job as _create_job,
        is_classification_job_active,
        start_classification_job,
    )

    active = (
        db.query(ClassificationJob)
        .filter(ClassificationJob.status.in_(["pending", "running", "cancelling"]))
        .first()
    )
    if active or is_classification_job_active():
        raise HTTPException(
            status_code=409,
            detail="Another classification job is already running",
        )

    hard_limit = settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS
    if body.media_ids is not None and len(body.media_ids) > hard_limit:
        raise HTTPException(
            status_code=400,
            detail=f"Too many media_ids: {len(body.media_ids)} exceeds CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS={hard_limit}",
        )

    if body.media_ids is None and body.max_items > hard_limit:
        body.max_items = hard_limit

    job = _create_job(
        db,
        media_ids=body.media_ids,
        max_items=body.max_items,
        only_unclassified=body.only_unclassified,
        force_reclassify=body.force_reclassify,
        trigger_source="manual",
    )

    start_classification_job(job.id)
    return _serialize_classification_job(job)


@router.get("/content-classification/jobs")
async def list_classification_jobs(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    jobs = (
        db.query(ClassificationJob)
        .order_by(ClassificationJob.created_at.desc())
        .limit(20)
        .all()
    )
    return [_serialize_classification_job(j) for j in jobs]


@router.get("/content-classification/jobs/{job_id}")
async def get_classification_job(
    job_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    job = db.query(ClassificationJob).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Classification job not found")
    return _serialize_classification_job(job)


@router.post("/content-classification/jobs/{job_id}/cancel")
async def cancel_classification_job(
    job_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    from ...services.classification_job_service import request_classification_job_cancel

    job = db.query(ClassificationJob).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Classification job not found")

    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel a job with status '{job.status}'",
        )

    job.status = "cancelling"
    db.commit()
    request_classification_job_cancel(job_id)

    return _serialize_classification_job(job)


@router.get("/content-classification/config")
async def get_classification_config(
    current_user: User = Depends(require_admin_mode),
):
    return {
        "enabled": settings.CONTENT_CLASSIFICATION_ENABLED,
        "method": settings.CONTENT_CLASSIFICATION_METHOD,
        "batch_max_items": settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS,
        "auto_after_import": settings.CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT,
        "auto_max_items": settings.CONTENT_CLASSIFICATION_AUTO_MAX_ITEMS,
        "anime_tag_threshold": settings.CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD,
        "anime_confidence_threshold": settings.CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD,
    }


@router.post("/content-classification/media/{media_id}")
async def classify_single_media(
    media_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    if not settings.CONTENT_CLASSIFICATION_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Content classification is disabled",
        )

    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    from ...services.content_classifier import classify_media

    # Call directly — classify_media does lightweight DB queries + commit,
    # safe to run in the request thread.  Do NOT use run_in_threadpool here
    # because `db` is a request-scoped session and is not thread-safe.
    result = classify_media(db, media_id)
    return result


@router.put("/content-classification/media/{media_id}")
async def update_media_class(
    media_id: int,
    body: UpdateMediaClassRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    media.content_class = body.content_class
    media.content_class_source = "manual"
    media.content_class_model = None
    media.content_class_confidence = 1.0
    media.content_class_reviewed = True
    if body.lock is not None:
        media.content_class_locked = body.lock
    db.commit()

    return {
        "media_id": media_id,
        "content_class": media.content_class.value,
        "locked": media.content_class_locked,
    }


@router.get("/content-classification/stats")
async def get_classification_stats(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    total = db.query(func.count(Media.id)).scalar() or 0
    classified = db.query(func.count(Media.id)).filter(Media.content_class.isnot(None)).scalar() or 0
    unclassified = total - classified

    by_class = (
        db.query(Media.content_class, func.count(Media.id))
        .filter(Media.content_class.isnot(None))
        .group_by(Media.content_class)
        .all()
    )
    breakdown = {row[0].value if row[0] else "null": row[1] for row in by_class}

    locked_count = db.query(func.count(Media.id)).filter(Media.content_class_locked == True).scalar() or 0
    reviewed_count = db.query(func.count(Media.id)).filter(Media.content_class_reviewed == True).scalar() or 0

    return {
        "total_media": total,
        "classified": classified,
        "unclassified": unclassified,
        "breakdown": breakdown,
        "locked": locked_count,
        "reviewed": reviewed_count,
        "enabled": settings.CONTENT_CLASSIFICATION_ENABLED,
    }
