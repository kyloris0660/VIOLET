import json
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ...auth import get_current_admin_user, require_admin_mode
from ...config import settings
from ...database import get_db
from ...models import Media, ScanJob, ScanJobMedia, User
from ...services.pixiv_metadata_ingestion_service import summarize_batch_closure
from ...utils.file_scanner import find_untracked_media
from ...utils.local_library_scanner import (
    is_job_active,
    preflight_analyze,
    request_cancel,
    run_scan_job,
    scan_and_import,
    validate_scan_paths,
)
from ...utils.logger import logger
from ...utils.thumbnail_generator import generate_thumbnail
from sqlalchemy.orm import Session

router = APIRouter()

@router.post("/scan-media")
async def scan_media(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db)
):
    """Find untracked media files"""    
    result = find_untracked_media(db)
    
    return {
        'new_files': result['new_files'],
        'files': [f['path'] for f in result['files']]
    }

class ScanLocalLibraryRequest(BaseModel):
    paths: Optional[List[str]] = None
    dry_run: bool = False
    max_files: Optional[int] = None
    hydrated_only: Optional[bool] = None


def _resolve_scan_params(body: Optional[ScanLocalLibraryRequest]):
    """Extract and validate scan parameters from request body.

    Path resolution semantics:
    - body.paths is a non-empty list → use those paths
    - body.paths is explicitly [] → reject with 400 (prevents silent env fallback)
    - body.paths is None / body is None → fallback to LOCAL_LIBRARY_PATHS
    """
    if body and body.paths is not None:
        if len(body.paths) == 0:
            raise HTTPException(
                status_code=400,
                detail="paths must not be empty. Omit the field to use configured LOCAL_LIBRARY_PATHS.",
            )
        scan_paths = [Path(p) for p in body.paths]
    else:
        scan_paths = settings.LOCAL_LIBRARY_PATHS

    if not scan_paths:
        raise HTTPException(
            status_code=400,
            detail="No scan paths configured. Set LOCAL_LIBRARY_PATHS in .env or pass paths in request body.",
        )

    path_err = validate_scan_paths(scan_paths)
    if path_err:
        raise HTTPException(status_code=400, detail=path_err)

    dry_run = body.dry_run if body else False
    max_files = body.max_files if body else None
    hydrated_only = body.hydrated_only if body and body.hydrated_only is not None else settings.SCAN_HYDRATED_ONLY_DEFAULT

    if max_files is not None and max_files < 1:
        raise HTTPException(status_code=400, detail="max_files must be >= 1")

    return scan_paths, dry_run, max_files, hydrated_only


@router.post("/scan-local-library")
async def scan_local_library(
    body: Optional[ScanLocalLibraryRequest] = None,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Legacy synchronous scan endpoint (Phase 1.5 compatible)."""
    scan_paths, dry_run, max_files, hydrated_only = _resolve_scan_params(body)

    result = await run_in_threadpool(
        scan_and_import, db, scan_paths, dry_run=dry_run, max_files=max_files, hydrated_only=hydrated_only
    )
    return result


def _source_metadata_job_status(job: ScanJob, db: Session | None) -> dict:
    if db is None or job.id is None or job.status not in {"completed", "failed", "cancelled", "interrupted"}:
        return {"source_metadata_status": "not_evaluated", "source_metadata_open_count": 0, "source_metadata_blocked": False}
    media_ids = [int(row[0]) for row in db.query(ScanJobMedia.media_id).filter(ScanJobMedia.scan_job_id == job.id).all()]
    closure = summarize_batch_closure(db, media_ids)
    lifecycle = closure.get("lifecycle_counts") or {}
    if not closure.get("pixiv_candidate_count"):
        status = "not_applicable"
    elif closure.get("closed"):
        status = "complete"
    elif int(lifecycle.get("normalization_failed", 0)):
        status = "normalization_failed"
    elif int(lifecycle.get("conflict", 0)):
        status = "conflict"
    elif int(lifecycle.get("retryable", 0)):
        status = "retryable"
    else:
        status = "pending"
    return {
        "source_metadata_status": status,
        "source_metadata_open_count": int(closure.get("open_candidate_count", 0)),
        "source_metadata_blocked": not bool(closure.get("closed", False)),
    }


def _serialize_job(job: ScanJob, db: Session | None = None) -> dict:
    """Convert a ScanJob ORM object to a JSON-safe dict."""
    failed_files = []
    if job.failed_files_json:
        try:
            failed_files = json.loads(job.failed_files_json)
        except (json.JSONDecodeError, TypeError):
            pass

    paths = []
    if job.paths_json:
        try:
            paths = json.loads(job.paths_json)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "id": job.id,
        "status": job.status,
        "paths": paths,
        "dry_run": job.dry_run,
        "max_files": job.max_files,
        "hydrated_only": job.hydrated_only if job.hydrated_only is not None else True,
        "is_preflight": job.is_preflight if job.is_preflight is not None else False,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "total_seen": job.total_seen,
        "processed": job.processed,
        "imported": job.imported,
        "skipped_duplicate": job.skipped_duplicate,
        "skipped_unsupported": job.skipped_unsupported,
        "skipped_limit": job.skipped_limit,
        "skipped_cloud_placeholder": job.skipped_cloud_placeholder or 0,
        "skipped_zero_byte": job.skipped_zero_byte or 0,
        "skipped_timeout": job.skipped_timeout or 0,
        "skipped_unreadable": job.skipped_unreadable or 0,
        "skipped_hidden": job.skipped_hidden or 0,
        "skipped_too_large": job.skipped_too_large or 0,
        "failed": job.failed,
        "limit_reached": job.limit_reached,
        "failed_files": failed_files,
        "error_message": job.error_message,
        **_source_metadata_job_status(job, db),
    }


@router.post("/scan-local-library/jobs")
async def create_scan_job(
    body: Optional[ScanLocalLibraryRequest] = None,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Create a background scan job. Returns immediately with a job_id."""
    scan_paths, dry_run, max_files, hydrated_only = _resolve_scan_params(body)

    active = (
        db.query(ScanJob)
        .filter(ScanJob.status.in_(["pending", "running", "cancelling"]))
        .first()
    )
    if active or is_job_active():
        raise HTTPException(
            status_code=409,
            detail="Another scan job is already running",
        )

    job = ScanJob(
        status="pending",
        paths_json=json.dumps([str(p) for p in scan_paths]),
        dry_run=dry_run,
        max_files=max_files,
        hydrated_only=hydrated_only,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    t = threading.Thread(target=run_scan_job, args=(job.id,), daemon=True)
    t.start()

    return _serialize_job(job, db)


@router.get("/scan-local-library/jobs")
async def list_scan_jobs(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Return the 20 most recent scan jobs (newest first).

    NOTE: stale job recovery runs only at application startup (in main.py),
    not here, to avoid marking actively-running jobs as interrupted during
    normal UI polling.
    """
    jobs = (
        db.query(ScanJob)
        .order_by(ScanJob.created_at.desc())
        .limit(20)
        .all()
    )
    return [_serialize_job(j, db) for j in jobs]


@router.get("/scan-local-library/jobs/{job_id}")
async def get_scan_job(
    job_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Return the status and progress of a single scan job."""
    job = db.query(ScanJob).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")
    return _serialize_job(job, db)


@router.post("/scan-local-library/jobs/{job_id}/cancel")
async def cancel_scan_job(
    job_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Request cancellation of a running scan job.

    Already-imported files are kept; cancellation does not roll back imports.
    """
    job = db.query(ScanJob).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")

    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel a job with status '{job.status}'",
        )

    job.status = "cancelling"
    db.commit()

    request_cancel(job_id)

    return _serialize_job(job, db)


@router.post("/scan-local-library/preflight")
async def preflight_scan(
    body: Optional[ScanLocalLibraryRequest] = None,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Stat-only preflight analysis. Never opens files, never triggers hydration."""
    scan_paths, dry_run, max_files, hydrated_only = _resolve_scan_params(body)

    result = await run_in_threadpool(
        preflight_analyze,
        scan_paths,
        hydrated_only=hydrated_only,
        max_files=max_files,
    )

    job = ScanJob(
        status="completed",
        paths_json=json.dumps([str(p) for p in scan_paths]),
        dry_run=True,
        max_files=max_files,
        hydrated_only=hydrated_only,
        is_preflight=True,
        total_seen=result.get("total_seen", 0),
        processed=result.get("processed", 0),
        imported=0,
        skipped_duplicate=result.get("skipped_duplicate", 0),
        skipped_unsupported=result.get("skipped_unsupported", 0),
        skipped_limit=result.get("skipped_limit", 0),
        skipped_cloud_placeholder=result.get("skipped_cloud_placeholder", 0),
        skipped_zero_byte=result.get("skipped_zero_byte", 0),
        skipped_timeout=0,
        skipped_unreadable=result.get("skipped_unreadable", 0),
        skipped_hidden=result.get("skipped_hidden", 0),
        skipped_too_large=result.get("skipped_too_large", 0),
        failed=result.get("failed", 0),
        failed_files_json=json.dumps(result.get("failed_files", [])[:50]),
        limit_reached=result.get("limit_reached", False),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    serialized = _serialize_job(job, db)
    serialized["estimated_size_bytes"] = result.get("estimated_size_bytes", 0)
    serialized["largest_file_bytes"] = result.get("largest_file_bytes", 0)
    serialized["extensions"] = result.get("extensions", {})
    return serialized


@router.get("/get-untracked-file")
async def get_untracked_file(
    path: str,
    current_user: User = Depends(require_admin_mode)
):
    """Serve an untracked file for importing"""
    import mimetypes
    from pathlib import Path

    from fastapi.responses import FileResponse
    
    file_path = Path(path)
    
    if not file_path.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    try:
        file_path = file_path.resolve()
        if not file_path.is_relative_to(settings.ORIGINAL_DIR.resolve()):
            raise ValueError()
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=403, detail="Access denied")
    
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if not mime_type:
        mime_type = "application/octet-stream"
    
    return FileResponse(
        path=str(file_path),
        media_type=mime_type,
        filename=file_path.name
    )

@router.get("/media-stats")
async def get_media_stats(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get media statistics"""
    from sqlalchemy import func

    from ...models import Media
    
    total_media = db.query(Media).count()
    total_images = db.query(Media).filter(Media.file_type == 'image').count()
    total_gifs = db.query(Media).filter(Media.file_type == 'gif').count()
    total_videos = db.query(Media).filter(Media.file_type == 'video').count()
    
    return {
        "total_media": total_media,
        "total_images": total_images,
        "total_gifs": total_gifs,
        "total_videos": total_videos,
    }

@router.get("/stats")
async def get_comprehensive_stats(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get comprehensive statistics for admin dashboard"""
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from ...models import Album, Media, Tag, TagAlias

    total_media = db.query(Media).count()
    media_by_type = {
        'image': db.query(Media).filter(Media.file_type == 'image').count(),
        'gif': db.query(Media).filter(Media.file_type == 'gif').count(),
        'video': db.query(Media).filter(Media.file_type == 'video').count()
    }
    
    media_by_rating = {
        'safe': db.query(Media).filter(Media.rating == 'safe').count(),
        'questionable': db.query(Media).filter(Media.rating == 'questionable').count(),
        'explicit': db.query(Media).filter(Media.rating == 'explicit').count()
    }
    
    thirty_days_ago = datetime.now() - timedelta(days=30)
    upload_trends = db.query(
        func.date(Media.uploaded_at).label('date'),
        func.count(Media.id).label('count')
    ).filter(
        Media.uploaded_at >= thirty_days_ago
    ).group_by(
        func.date(Media.uploaded_at)
    ).order_by('date').all()
    
    upload_trends_data = [
        {'date': str(trend.date), 'count': trend.count}
        for trend in upload_trends
    ]
    
    total_tags = db.query(Tag).count()
    total_aliases = db.query(TagAlias).count()
    
    top_tags = db.query(Tag).order_by(Tag.post_count.desc()).limit(10).all()
    top_tags_data = [
        {'name': tag.name, 'count': tag.post_count, 'category': tag.category.value}
        for tag in top_tags
    ]
    
    from ...models import TagCategoryEnum
    top_tags_by_category = {}
    for category in TagCategoryEnum:
        category_tags = db.query(Tag).filter(
            Tag.category == category
        ).order_by(Tag.post_count.desc()).limit(10).all()
        
        top_tags_by_category[category.value] = [
            {'name': tag.name, 'count': tag.post_count}
            for tag in category_tags
        ]
    
    tag_categories = db.query(
        Tag.category,
        func.count(Tag.id).label('count')
    ).group_by(Tag.category).all()
    
    tag_category_data = {
        cat.category.value: cat.count
        for cat in tag_categories
    }
    
    total_albums = db.query(Album).count()
    
    from sqlalchemy import func as sql_func

    album_media_counts = db.query(
        Album.id,
        sql_func.count(Media.id).label('media_count')
    ).outerjoin(
        Album.media
    ).group_by(Album.id).all()
    
    album_size_distribution = {
        '0': 0,
        '1-10': 0,
        '11-50': 0,
        '51-100': 0,
        '100+': 0
    }
    
    for album_id, count in album_media_counts:
        if count == 0:
            album_size_distribution['0'] += 1
        elif count <= 10:
            album_size_distribution['1-10'] += 1
        elif count <= 50:
            album_size_distribution['11-50'] += 1
        elif count <= 100:
            album_size_distribution['51-100'] += 1
        else:
            album_size_distribution['100+'] += 1
    
    storage_stats = db.query(
        func.sum(Media.file_size).label('total_size'),
        func.avg(Media.file_size).label('avg_size')
    ).first()
    
    total_storage = storage_stats.total_size or 0
    avg_file_size = int(storage_stats.avg_size or 0)
    
    from sqlalchemy import exists
    from sqlalchemy.orm import aliased
    
    ChildMedia = aliased(Media)
    
    total_parents = db.query(Media).filter(
        exists().where(ChildMedia.parent_id == Media.id)
    ).count()
    
    total_children = db.query(Media).filter(Media.parent_id != None).count()

    return {
        "media": {
            "total": total_media,
            "by_type": media_by_type,
            "by_rating": media_by_rating,
            "relationships": {
                "total_parents": total_parents,
                "total_children": total_children
            }
        },
        "upload_trends": upload_trends_data,
        "tags": {
            "total": total_tags,
            "total_aliases": total_aliases,
            "total_with_aliases": total_tags + total_aliases,
            "top_tags": top_tags_data,
            "top_tags_by_category": top_tags_by_category,
            "by_category": tag_category_data
        },
        "albums": {
            "total": total_albums,
            "size_distribution": album_size_distribution
        },
        "storage": {
            "total_bytes": total_storage,
            "avg_file_size_bytes": avg_file_size
        }
    }

def _do_regenerate_all_thumbnails(db: Session) -> dict:
    """Synchronous worker for regenerating all thumbnails."""
    thumbnail_dir = settings.THUMBNAIL_DIR
    original_dir = settings.ORIGINAL_DIR

    # Delete all existing thumbnails
    deleted = 0
    if thumbnail_dir.exists():
        for f in thumbnail_dir.rglob("*"):
            if f.is_file():
                try:
                    f.unlink()
                    deleted += 1
                except Exception as e:
                    logger.error(f"Error deleting thumbnail {f}: {e}")

    # Re-generate thumbnails for all media items
    all_media = db.query(Media).all()
    generated = 0
    failed = 0

    for item in all_media:
        source_path = settings.resolve_storage_path(item.path)

        if not source_path or not source_path.exists():
            logger.warning(f"Source file missing for media {item.id}: {item.path}")
            failed += 1
            continue

        thumbnail_filename = f"{item.hash}.jpg"
        thumbnail_path = thumbnail_dir / thumbnail_filename

        try:
            ok = generate_thumbnail(source_path, thumbnail_path, item.file_type)
            if ok:
                item.thumbnail_path = settings.storage_relative_path(thumbnail_path)
                generated += 1
            else:
                item.thumbnail_path = None
                failed += 1
        except Exception as e:
            logger.error(f"Error regenerating thumbnail for media {item.id}: {e}", exc_info=True)
            item.thumbnail_path = None
            failed += 1

    db.commit()

    return {
        "deleted": deleted,
        "generated": generated,
        "failed": failed,
        "total": len(all_media),
    }

def _do_generate_missing_thumbnails(db: Session) -> dict:
    """Synchronous worker for generating missing thumbnails only."""
    thumbnail_dir = settings.THUMBNAIL_DIR

    # Collect all thumbnail paths registered in the DB (resolved to absolute)
    all_media = db.query(Media).all()
    registered_paths: set = set()
    for item in all_media:
        if item.thumbnail_path:
            resolved = settings.resolve_storage_path(item.thumbnail_path)
            if resolved:
                registered_paths.add(str(resolved))

    # Delete orphaned thumbnail files (files with no registered DB path)
    orphans_deleted = 0
    if thumbnail_dir.exists():
        for f in thumbnail_dir.rglob("*"):
            if f.is_file() and str(f.resolve()) not in registered_paths:
                try:
                    f.unlink()
                    orphans_deleted += 1
                except Exception as e:
                    logger.error(f"Error deleting orphaned thumbnail {f}: {e}")

    # Generate thumbnails for media items whose thumbnail file is missing
    generated = 0
    failed = 0
    skipped = 0

    for item in all_media:
        # Check whether the recorded thumbnail file actually exists
        thumb_exists = False
        if item.thumbnail_path:
            resolved_thumb = settings.resolve_storage_path(item.thumbnail_path)
            thumb_exists = resolved_thumb is not None and resolved_thumb.exists()
        if thumb_exists:
            skipped += 1
            continue

        source_path = settings.resolve_storage_path(item.path)

        if not source_path or not source_path.exists():
            logger.warning(f"Source file missing for media {item.id}: {item.path}")
            failed += 1
            continue

        thumbnail_filename = f"{item.hash}.jpg"
        thumbnail_path = thumbnail_dir / thumbnail_filename

        try:
            ok = generate_thumbnail(source_path, thumbnail_path, item.file_type)
            if ok:
                item.thumbnail_path = settings.storage_relative_path(thumbnail_path)
                generated += 1
            else:
                item.thumbnail_path = None
                failed += 1
        except Exception as e:
            logger.error(f"Error generating thumbnail for media {item.id}: {e}", exc_info=True)
            item.thumbnail_path = None
            failed += 1

    db.commit()

    return {
        "orphans_deleted": orphans_deleted,
        "generated": generated,
        "failed": failed,
        "skipped": skipped,
        "total": len(all_media),
    }

@router.post("/regenerate-all-thumbnails")
async def regenerate_all_thumbnails(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Delete all thumbnails and regenerate them from source files, updating DB paths."""
    try:
        result = await run_in_threadpool(_do_regenerate_all_thumbnails, db)
        return result
    except Exception as e:
        logger.error(f"Error regenerating all thumbnails: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-missing-thumbnails")
async def generate_missing_thumbnails(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Remove orphaned thumbnail files and generate thumbnails for media items that are missing one."""
    try:
        result = await run_in_threadpool(_do_generate_missing_thumbnails, db)
        return result
    except Exception as e:
        logger.error(f"Error generating missing thumbnails: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
