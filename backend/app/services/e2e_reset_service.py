"""E2E test data reset service (Phase 2.3a).

Safely removes media imported from a specific source directory,
along with associated scan jobs, AI tag jobs, copied files, and thumbnails.
"""
import json
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AITagJob, Media, ScanJob, ScanJobMedia, Tag,
    blombooru_media_tags,
)
from ..utils.logger import logger


def _find_media_by_source(db: Session, source_path: str) -> List[Media]:
    """Find all media imported from the given source path.

    Matches both forward-slash and backslash variants since the scanner
    may store either form depending on OS. Backslashes are doubled in
    LIKE patterns because PostgreSQL treats \\ as an escape character.
    """
    fwd = source_path.replace("\\", "/").rstrip("/")
    bck = source_path.replace("/", "\\").rstrip("\\")
    bck_escaped = bck.replace("\\", "\\\\")
    return (
        db.query(Media)
        .filter(
            (Media.source.like(f"file://{fwd}/%")) |
            (Media.source.like(f"file://{fwd}%")) |
            (Media.source.like(f"file://{bck_escaped}\\\\%")) |
            (Media.source.like(f"file://{bck_escaped}%"))
        )
        .all()
    )


def compute_reset_summary(db: Session, source_path: str) -> Dict[str, Any]:
    """Compute what would be deleted without actually deleting anything."""
    media_list = _find_media_by_source(db, source_path)
    media_ids = [m.id for m in media_list]

    if not media_ids:
        return {
            "media_count": 0,
            "scan_job_count": 0,
            "ai_tag_job_count": 0,
            "copied_files_count": 0,
            "thumbnail_files_count": 0,
            "affected_tags_count": 0,
            "tag_associations_count": 0,
            "scan_job_media_count": 0,
            "message": "No media found from this source path",
        }

    tag_assoc_count = (
        db.query(blombooru_media_tags)
        .filter(blombooru_media_tags.c.media_id.in_(media_ids))
        .count()
    )

    affected_tag_ids = (
        db.query(blombooru_media_tags.c.tag_id)
        .filter(blombooru_media_tags.c.media_id.in_(media_ids))
        .distinct()
        .all()
    )
    affected_tags_count = len(affected_tag_ids)

    sjm_count = (
        db.query(ScanJobMedia)
        .filter(ScanJobMedia.media_id.in_(media_ids))
        .count()
    )

    related_scan_job_ids = set()
    sjm_records = (
        db.query(ScanJobMedia.scan_job_id)
        .filter(ScanJobMedia.media_id.in_(media_ids))
        .distinct()
        .all()
    )
    for row in sjm_records:
        related_scan_job_ids.add(row[0])

    ai_jobs = db.query(AITagJob).all()
    related_ai_job_ids = set()
    for job in ai_jobs:
        if job.media_ids_json:
            try:
                job_media_ids = json.loads(job.media_ids_json)
                if any(mid in media_ids for mid in job_media_ids):
                    related_ai_job_ids.add(job.id)
            except (json.JSONDecodeError, TypeError):
                pass
        if job.scan_job_id and job.scan_job_id in related_scan_job_ids:
            related_ai_job_ids.add(job.id)

    copied_files = 0
    thumbnail_files = 0
    for m in media_list:
        if m.path:
            full_path = settings.BASE_DIR / m.path
            if full_path.exists():
                copied_files += 1
        if m.thumbnail_path:
            thumb_path = settings.BASE_DIR / m.thumbnail_path
            if thumb_path.exists():
                thumbnail_files += 1

    return {
        "media_count": len(media_ids),
        "media_ids": media_ids[:50],
        "scan_job_count": len(related_scan_job_ids),
        "scan_job_ids": sorted(related_scan_job_ids),
        "ai_tag_job_count": len(related_ai_job_ids),
        "ai_tag_job_ids": sorted(related_ai_job_ids),
        "copied_files_count": copied_files,
        "thumbnail_files_count": thumbnail_files,
        "affected_tags_count": affected_tags_count,
        "tag_associations_count": tag_assoc_count,
        "scan_job_media_count": sjm_count,
    }


def execute_reset(db: Session, source_path: str) -> Dict[str, Any]:
    """Execute the actual reset: delete media, associations, files, and jobs."""
    media_list = _find_media_by_source(db, source_path)
    media_ids = [m.id for m in media_list]

    if not media_ids:
        return {
            "media_deleted": 0,
            "message": "No media found from this source path",
        }

    sjm_records = (
        db.query(ScanJobMedia.scan_job_id)
        .filter(ScanJobMedia.media_id.in_(media_ids))
        .distinct()
        .all()
    )
    related_scan_job_ids = {row[0] for row in sjm_records}

    ai_jobs = db.query(AITagJob).all()
    related_ai_job_ids = set()
    for job in ai_jobs:
        if job.media_ids_json:
            try:
                job_media_ids = json.loads(job.media_ids_json)
                if any(mid in media_ids for mid in job_media_ids):
                    related_ai_job_ids.add(job.id)
            except (json.JSONDecodeError, TypeError):
                pass
        if job.scan_job_id and job.scan_job_id in related_scan_job_ids:
            related_ai_job_ids.add(job.id)

    affected_tag_ids = [
        row[0] for row in
        db.query(blombooru_media_tags.c.tag_id)
        .filter(blombooru_media_tags.c.media_id.in_(media_ids))
        .distinct()
        .all()
    ]

    files_deleted = 0
    thumbs_deleted = 0
    for m in media_list:
        if m.path:
            full_path = settings.BASE_DIR / m.path
            try:
                if full_path.exists():
                    full_path.unlink()
                    files_deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete file {full_path}: {e}")
        if m.thumbnail_path:
            thumb_path = settings.BASE_DIR / m.thumbnail_path
            try:
                if thumb_path.exists():
                    thumb_path.unlink()
                    thumbs_deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete thumbnail {thumb_path}: {e}")

    tag_assocs_deleted = (
        db.query(blombooru_media_tags)
        .filter(blombooru_media_tags.c.media_id.in_(media_ids))
        .delete(synchronize_session=False)
    )

    sjm_deleted = (
        db.query(ScanJobMedia)
        .filter(ScanJobMedia.media_id.in_(media_ids))
        .delete(synchronize_session=False)
    )

    media_deleted = (
        db.query(Media)
        .filter(Media.id.in_(media_ids))
        .delete(synchronize_session=False)
    )

    scan_jobs_deleted = 0
    if related_scan_job_ids:
        db.query(ScanJobMedia).filter(
            ScanJobMedia.scan_job_id.in_(related_scan_job_ids)
        ).delete(synchronize_session=False)
        scan_jobs_deleted = (
            db.query(ScanJob)
            .filter(ScanJob.id.in_(related_scan_job_ids))
            .delete(synchronize_session=False)
        )

    ai_jobs_deleted = 0
    if related_ai_job_ids:
        ai_jobs_deleted = (
            db.query(AITagJob)
            .filter(AITagJob.id.in_(related_ai_job_ids))
            .delete(synchronize_session=False)
        )

    tags_updated = 0
    if affected_tag_ids:
        for tag_id in affected_tag_ids:
            tag = db.query(Tag).get(tag_id)
            if tag:
                new_count = (
                    db.query(blombooru_media_tags)
                    .filter(blombooru_media_tags.c.tag_id == tag_id)
                    .count()
                )
                tag.post_count = new_count
                tags_updated += 1

    db.commit()
    logger.info(
        f"E2E reset completed for '{source_path}': "
        f"{media_deleted} media, {files_deleted} files, {thumbs_deleted} thumbs, "
        f"{tag_assocs_deleted} tag assocs, {scan_jobs_deleted} scan jobs, "
        f"{ai_jobs_deleted} AI jobs, {tags_updated} tags recalculated"
    )

    return {
        "media_deleted": media_deleted,
        "files_deleted": files_deleted,
        "thumbnails_deleted": thumbs_deleted,
        "tag_associations_deleted": tag_assocs_deleted,
        "scan_job_media_deleted": sjm_deleted,
        "scan_jobs_deleted": scan_jobs_deleted,
        "ai_tag_jobs_deleted": ai_jobs_deleted,
        "tags_recalculated": tags_updated,
    }
