"""Developer / E2E tools API endpoints (Phase 2.3a+).

Config diagnostics, E2E test data reset, missing-media maintenance,
and recommended config. Admin-only, requires admin_mode.
"""
import json
import os
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...config import settings, _PROJECT_ROOT, APP_VERSION
from ...database import get_db
from ...models import (
    AITagJob, ClassificationJob, Media, ScanJobMedia, Tag, User,
    blombooru_album_media, blombooru_media_tags,
)
from ...utils.logger import logger

router = APIRouter()


BLOCKED_ROOTS = {"", "c:", "c:/", "/", "c:/users"}


def _is_dangerous_path(source_path: str) -> bool:
    """Block dangerous or restricted paths from E2E reset."""
    stripped = source_path.strip()
    if not stripped:
        return True
    lowered = stripped.lower().replace("\\", "/").rstrip("/")
    if not lowered:
        return True
    if lowered in BLOCKED_ROOTS:
        logger.debug("Blocked by BLOCKED_ROOTS: %s -> %s", source_path, lowered)
        return True
    if "icloud" in lowered and "photos" in lowered:
        return True
    project_root = str(_PROJECT_ROOT).lower().replace("\\", "/").rstrip("/")
    if lowered == project_root:
        logger.debug("Blocked by project_root match: %s == %s", lowered, project_root)
        return True
    if lowered.startswith(project_root + "/"):
        logger.debug("Blocked by project_root prefix: %s starts with %s/", lowered, project_root)
        return True
    if lowered == "data" or lowered.endswith("/data"):
        return True
    if lowered == "media/original" or lowered.endswith("/media/original"):
        return True
    return False


class ResetE2ERequest(BaseModel):
    source_path: str
    confirm: bool = False
    dry_run: bool = True


@router.get("/dev/config-diagnostics")
async def get_config_diagnostics(
    current_user: User = Depends(require_admin_mode),
):
    """Return runtime configuration diagnostics (no secrets)."""
    return {
        "ai_tagging": {
            "enabled": settings.AI_TAGGING_ENABLED,
            "batch_max_items": settings.AI_TAGGING_BATCH_MAX_ITEMS,
            "model_name": settings.AI_MODEL_NAME,
            "general_threshold": settings.AI_GENERAL_THRESHOLD,
            "character_threshold": settings.AI_CHARACTER_THRESHOLD,
            "rating_threshold": settings.AI_RATING_THRESHOLD,
            "suggestion_threshold": settings.AI_SUGGESTION_THRESHOLD,
        },
        "auto_tag_after_import": {
            "enabled": settings.AI_AUTO_TAG_AFTER_IMPORT,
            "max_items": settings.AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS,
            "only_new": settings.AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW,
            "dry_run": settings.AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN,
            "force_suggestions": settings.AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS,
        },
        "tag_localization": {
            "llm_enabled": settings.TAG_TRANSLATION_LLM_ENABLED,
            "auto_enabled": settings.TAG_TRANSLATION_AUTO_ENABLED,
            "auto_max_items": settings.TAG_TRANSLATION_AUTO_MAX_ITEMS,
            "batch_max_items": settings.TAG_TRANSLATION_BATCH_MAX_ITEMS,
            "provider": settings.TAG_TRANSLATION_LLM_PROVIDER,
            "model": settings.TAG_TRANSLATION_LLM_MODEL or "(not configured)",
            "api_key_configured": bool(settings.TAG_TRANSLATION_LLM_API_KEY),
            "base_url_configured": bool(settings.TAG_TRANSLATION_LLM_BASE_URL),
            "background_enabled": settings.TAG_TRANSLATION_BG_ENABLED,
            "background_interval": settings.TAG_TRANSLATION_BG_INTERVAL,
            "background_batch_size": settings.TAG_TRANSLATION_BG_BATCH_SIZE,
            "background_max_per_run": settings.TAG_TRANSLATION_BG_MAX_PER_RUN,
            "background_daily_limit": settings.TAG_TRANSLATION_BG_DAILY_LIMIT,
            "background_error_limit": settings.TAG_TRANSLATION_BG_ERROR_LIMIT,
            "background_priority": settings.TAG_TRANSLATION_BG_PRIORITY,
        },
        "paths": {
            "local_library_paths": [str(p) for p in settings.LOCAL_LIBRARY_PATHS],
        },
        "scan": {
            "hydrated_only_default": settings.SCAN_HYDRATED_ONLY_DEFAULT,
            "file_open_timeout_seconds": settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS,
            "max_file_size_mb": settings.SCAN_MAX_FILE_SIZE_MB,
        },
        "content_classification": {
            "enabled": settings.CONTENT_CLASSIFICATION_ENABLED,
            "method": settings.CONTENT_CLASSIFICATION_METHOD,
            "batch_max_items": settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS,
            "auto_after_import": settings.CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT,
            "auto_max_items": settings.CONTENT_CLASSIFICATION_AUTO_MAX_ITEMS,
            "clip_unknown_margin": settings.CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN,
            "heuristic_anime_tag_threshold": settings.CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD,
            "heuristic_anime_confidence_threshold": settings.CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD,
        },
        "server": {
            "pid": os.getpid(),
            "python_version": sys.version,
            "app_version": APP_VERSION,
            "base_dir": str(settings.BASE_DIR),
            "platform": sys.platform,
            "debug": settings.DEBUG,
        },
        "env_file": str(_PROJECT_ROOT / ".env"),
    }


@router.post("/dev/reset-e2e-test-data")
async def reset_e2e_test_data(
    body: ResetE2ERequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    if _is_dangerous_path(body.source_path):
        raise HTTPException(
            status_code=400,
            detail=f"Blocked: '{body.source_path}' is a dangerous or restricted path",
        )

    from ...services.e2e_reset_service import compute_reset_summary, execute_reset

    summary = compute_reset_summary(db, body.source_path)

    if body.dry_run:
        return {
            "dry_run": True,
            "summary": summary,
            "message": "No data was deleted. Set dry_run=false and confirm=true to execute.",
        }

    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Must set confirm=true to execute real deletion",
        )

    result = execute_reset(db, body.source_path)
    return {
        "dry_run": False,
        "summary": result,
        "message": "Reset completed successfully",
    }


@router.get("/dev/recommended-e2e-config")
async def get_recommended_e2e_config(
    current_user: User = Depends(require_admin_mode),
):
    return {
        "snippet": """# Recommended E2E Test Configuration
AI_TAGGING_ENABLED=true
AI_TAGGING_BATCH_MAX_ITEMS=200
AI_AUTO_TAG_AFTER_IMPORT=true
AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS=200
AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW=true
AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN=false
AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS=false
TAG_TRANSLATION_LLM_ENABLED=true
TAG_TRANSLATION_AUTO_ENABLED=true
TAG_TRANSLATION_AUTO_MAX_ITEMS=200
TAG_TRANSLATION_BATCH_MAX_ITEMS=200
LOCAL_LIBRARY_PATHS=C:\\Users\\kyloris\\Pictures\\VioletTest100

# Fill in your LLM API credentials:
# TAG_TRANSLATION_LLM_API_KEY=your-api-key
# TAG_TRANSLATION_LLM_BASE_URL=https://your-api-url
# TAG_TRANSLATION_LLM_MODEL=your-model-name""",
        "note": "Copy these values to your .env file and restart the server.",
    }


class MissingMediaCleanupRequest(BaseModel):
    confirm: bool = False
    dry_run: bool = True


@router.get("/dev/missing-media-scan")
async def scan_missing_media(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Scan for media with missing files. 4 categories:
    A: original/app file missing → deletable
    B: only thumbnail missing, original exists → suggest regenerate
    C: original exists, thumbnail damaged → suggest regenerate
    D: both DB record files and app media missing → deletable
    """
    all_media = db.query(Media).all()

    valid = []
    missing_original = []  # Category A: media file missing
    missing_thumbnail_only = []  # Category B/C: thumb missing, original exists
    missing_both = []  # Category D: both missing

    for m in all_media:
        media_path = settings.BASE_DIR / m.path if m.path else None
        thumb_path = settings.BASE_DIR / m.thumbnail_path if m.thumbnail_path else None

        media_exists = media_path and media_path.exists() if media_path else False
        thumb_exists = thumb_path and thumb_path.exists() if thumb_path else False

        if media_exists and thumb_exists:
            valid.append(m.id)
        elif media_exists and not thumb_exists:
            missing_thumbnail_only.append(m.id)
        elif not media_exists and thumb_exists:
            missing_original.append(m.id)
        else:
            missing_both.append(m.id)

    cap = 100
    return {
        "total_media": len(all_media),
        "valid": len(valid),
        "missing_original_or_media_file": len(missing_original),
        "missing_thumbnail_only": len(missing_thumbnail_only),
        "missing_both": len(missing_both),
        "deletable_count": len(missing_original) + len(missing_both),
        "samples": {
            "missing_original": missing_original[:cap],
            "missing_thumbnail_only": missing_thumbnail_only[:cap],
            "missing_both": missing_both[:cap],
        },
    }


@router.post("/dev/missing-media-cleanup")
async def cleanup_missing_media(
    body: MissingMediaCleanupRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Delete DB records for media whose original/app file is missing (categories A+D).
    Source files are NEVER deleted by this operation.
    """
    all_media = db.query(Media).all()

    deletable_ids = []
    for m in all_media:
        media_path = settings.BASE_DIR / m.path if m.path else None
        media_exists = media_path and media_path.exists() if media_path else False
        if not media_exists:
            deletable_ids.append(m.id)

    if not deletable_ids:
        return {
            "dry_run": body.dry_run,
            "message": "No media with missing files found",
            "deleted": 0,
        }

    if body.dry_run:
        return {
            "dry_run": True,
            "deletable_count": len(deletable_ids),
            "deletable_ids_sample": deletable_ids[:100],
            "message": "No data was deleted. Set dry_run=false and confirm=true to execute.",
        }

    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Must set confirm=true to execute real deletion",
        )

    logger.info("Source files are NEVER deleted by this operation")

    affected_tag_ids = [
        row[0] for row in
        db.query(blombooru_media_tags.c.tag_id)
        .filter(blombooru_media_tags.c.media_id.in_(deletable_ids))
        .distinct()
        .all()
    ]

    thumbs_deleted = 0
    for mid in deletable_ids:
        m = db.query(Media).get(mid)
        if m and m.thumbnail_path:
            thumb_path = settings.BASE_DIR / m.thumbnail_path
            if thumb_path.exists() and str(thumb_path).startswith(str(settings.THUMBNAIL_DIR)):
                try:
                    thumb_path.unlink()
                    thumbs_deleted += 1
                except Exception as e:
                    logger.warning(f"Failed to delete thumbnail {thumb_path}: {e}")

    db.query(Media).filter(Media.parent_id.in_(deletable_ids)).update(
        {Media.parent_id: None}, synchronize_session=False
    )

    tag_assocs_deleted = (
        db.query(blombooru_media_tags)
        .filter(blombooru_media_tags.c.media_id.in_(deletable_ids))
        .delete(synchronize_session=False)
    )

    album_assocs_deleted = (
        db.query(blombooru_album_media)
        .filter(blombooru_album_media.c.media_id.in_(deletable_ids))
        .delete(synchronize_session=False)
    )

    sjm_deleted = (
        db.query(ScanJobMedia)
        .filter(ScanJobMedia.media_id.in_(deletable_ids))
        .delete(synchronize_session=False)
    )

    deletable_set = set(deletable_ids)
    ai_jobs_cleaned = 0
    for job in db.query(AITagJob).all():
        if job.media_ids_json:
            try:
                ids = json.loads(job.media_ids_json)
                filtered = [i for i in ids if i not in deletable_set]
                if len(filtered) != len(ids):
                    job.media_ids_json = json.dumps(filtered) if filtered else None
                    ai_jobs_cleaned += 1
            except (json.JSONDecodeError, TypeError):
                pass

    cls_jobs_cleaned = 0
    for job in db.query(ClassificationJob).all():
        if job.media_ids_json:
            try:
                ids = json.loads(job.media_ids_json)
                filtered = [i for i in ids if i not in deletable_set]
                if len(filtered) != len(ids):
                    job.media_ids_json = json.dumps(filtered) if filtered else None
                    cls_jobs_cleaned += 1
            except (json.JSONDecodeError, TypeError):
                pass

    media_deleted = (
        db.query(Media)
        .filter(Media.id.in_(deletable_ids))
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
        f"Missing-media cleanup: {media_deleted} media records deleted, "
        f"{thumbs_deleted} orphan thumbnails removed, "
        f"{tag_assocs_deleted} tag assocs, {album_assocs_deleted} album assocs, "
        f"{sjm_deleted} scan_job_media, {ai_jobs_cleaned} AI jobs cleaned, "
        f"{cls_jobs_cleaned} classification jobs cleaned, {tags_updated} tags recalculated"
    )

    return {
        "dry_run": False,
        "media_deleted": media_deleted,
        "thumbnails_deleted": thumbs_deleted,
        "tag_associations_deleted": tag_assocs_deleted,
        "album_associations_deleted": album_assocs_deleted,
        "scan_job_media_deleted": sjm_deleted,
        "ai_jobs_cleaned": ai_jobs_cleaned,
        "classification_jobs_cleaned": cls_jobs_cleaned,
        "tags_recalculated": tags_updated,
        "source_files_deleted": 0,
        "message": "Cleanup completed. Source files were NOT touched.",
    }
