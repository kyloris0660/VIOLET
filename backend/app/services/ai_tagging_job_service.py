"""AI Tagging Job Service — manages background AI tagging jobs.

Similar architecture to the scan job system (local_library_scanner.py):
- Background daemon threads with independent DB sessions
- Single-job concurrency with threading lock
- Cancel support via in-memory flag
- Progress flush to DB every N items
- Stale recovery at startup
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import AITagJob, Media, blombooru_media_tags

logger = logging.getLogger(__name__)

_PROGRESS_FLUSH_INTERVAL = 5

_active_job_lock = threading.Lock()
_active_job_cancel: Dict[int, bool] = {}


def is_ai_job_active() -> bool:
    """Check if an AI tagging job is currently running (thread-safe)."""
    with _active_job_lock:
        return bool(_active_job_cancel)


def request_ai_job_cancel(job_id: int) -> None:
    """Signal a running AI tag job to stop."""
    with _active_job_lock:
        _active_job_cancel[job_id] = True


def create_ai_tag_job(
    db: Session,
    *,
    media_ids: Optional[List[int]] = None,
    max_items: int = 10,
    dry_run: bool = False,
    only_without_ai_tags: bool = True,
    force_suggestions: bool = False,
    trigger_source: str = "manual",
    scan_job_id: Optional[int] = None,
) -> AITagJob:
    """Create an AI tagging job record in the database."""
    hard_limit = settings.AI_TAGGING_BATCH_MAX_ITEMS
    effective_max = min(max_items, hard_limit)

    job = AITagJob(
        status="pending",
        trigger_source=trigger_source,
        scan_job_id=scan_job_id,
        media_ids_json=json.dumps(media_ids) if media_ids else None,
        max_items=effective_max,
        dry_run=dry_run,
        only_without_ai_tags=only_without_ai_tags,
        force_suggestions=force_suggestions,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def start_ai_tag_job(job_id: int) -> None:
    """Launch a background thread for the given AI tag job."""
    t = threading.Thread(target=run_ai_tag_job, args=(job_id,), daemon=True)
    t.start()


def run_ai_tag_job(job_id: int) -> None:
    """Execute an AI tagging job in a background thread with its own DB session."""
    from ..database import SessionLocal
    from ..services.ai_tagging_service import run_ai_tagging

    db: Session = SessionLocal()
    try:
        job: AITagJob = db.query(AITagJob).get(job_id)
        if job is None:
            logger.error(f"AI tag job {job_id} not found")
            return

        # Check pre-cancel
        with _active_job_lock:
            already_cancelled = _active_job_cancel.get(job_id, False)
            _active_job_cancel[job_id] = already_cancelled

        if already_cancelled or job.status == "cancelling":
            job.status = "cancelled"
            job.finished_at = datetime.now(timezone.utc)
            job.error_message = "Cancelled before processing started"
            db.commit()
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        # Resolve media IDs
        media_ids = _resolve_media_ids(db, job)

        if not media_ids:
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc)
            job.error_message = "No eligible media items found"
            db.commit()
            return

        # Process items
        new_tag_names: List[str] = []
        items_since_flush = 0
        failed_items: List[Dict[str, Any]] = []

        for media_id in media_ids:
            # Check cancel
            with _active_job_lock:
                if _active_job_cancel.get(job_id, False):
                    break

            try:
                result = run_ai_tagging(
                    db, media_id,
                    dry_run=job.dry_run,
                    force_suggestions=job.force_suggestions,
                )

                job.processed += 1

                if result.get("error"):
                    job.failed += 1
                    if len(failed_items) < 50:
                        failed_items.append({
                            "media_id": media_id,
                            "error": result["error"],
                        })
                else:
                    job.tags_added += result.get("tags_added", 0)
                    job.suggestions_added += result.get("suggestions_added", 0)
                    job.skipped_locked += result.get("skipped_locked", 0)
                    job.ignored_low_confidence += result.get("ignored_low_confidence", 0)

                    # Collect new tag names for localization
                    for pred in result.get("predictions", []):
                        if pred.get("action") in ("confirmed", "suggestion"):
                            new_tag_names.append(pred["name"])

            except Exception as exc:
                logger.error(f"AI tag job {job_id}: failed on media {media_id}: {exc}", exc_info=True)
                saved_processed = job.processed
                saved_failed = job.failed
                try:
                    db.rollback()
                except Exception:
                    pass
                job.processed = saved_processed + 1
                job.failed = saved_failed + 1
                if len(failed_items) < 50:
                    failed_items.append({
                        "media_id": media_id,
                        "error": str(exc)[:500],
                    })

            items_since_flush += 1
            if items_since_flush >= _PROGRESS_FLUSH_INTERVAL:
                _flush_progress(db, job, failed_items)
                items_since_flush = 0

        # Final status
        was_cancelled = False
        with _active_job_lock:
            was_cancelled = _active_job_cancel.get(job_id, False)

        job.failed_items_json = json.dumps(failed_items[:50]) if failed_items else None
        job.finished_at = datetime.now(timezone.utc)
        job.status = "cancelled" if was_cancelled else "completed"

        # Schedule tag localization for new tags
        _schedule_localization(job, new_tag_names)

        db.commit()

    except Exception as exc:
        logger.error(f"AI tag job {job_id} failed: {exc}", exc_info=True)
        try:
            job = db.query(AITagJob).get(job_id)
            if job:
                job.status = "failed"
                job.error_message = str(exc)[:2000]
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
    finally:
        with _active_job_lock:
            _active_job_cancel.pop(job_id, None)
        db.close()


def _resolve_media_ids(db: Session, job: AITagJob) -> List[int]:
    """Determine which media items to process for this job."""
    if job.media_ids_json:
        all_ids = json.loads(job.media_ids_json)
    else:
        query = db.query(Media.id)
        if job.only_without_ai_tags:
            ai_tagged = (
                db.query(blombooru_media_tags.c.media_id)
                .filter(blombooru_media_tags.c.source == "ai_wd")
                .distinct()
                .subquery()
            )
            query = query.filter(~Media.id.in_(ai_tagged))
        query = query.order_by(Media.id.asc())
        all_ids = [row[0] for row in query.all()]

    return all_ids[:job.max_items]


def _flush_progress(db: Session, job: AITagJob, failed_items: List[Dict]) -> None:
    """Persist current progress to the database."""
    try:
        job.failed_items_json = json.dumps(failed_items[:50]) if failed_items else None
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to flush AI tag job progress: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def _schedule_localization(job: AITagJob, new_tag_names: List[str]) -> None:
    """Queue missing tags for background translation after an AI tagging job.

    If the background worker is running, triggers an immediate run.
    Falls back to the legacy schedule_auto_translate for compatibility.
    """
    if not new_tag_names or job.dry_run:
        job.localization_status = "skipped_dry_run" if job.dry_run else "skipped_no_new_tags"
        return

    unique_names = list(set(new_tag_names))

    try:
        if settings.TAG_TRANSLATION_BG_ENABLED and settings.TAG_TRANSLATION_LLM_ENABLED:
            from ..services.tag_translation_worker import trigger_run_now, _worker_thread
            if _worker_thread and _worker_thread.is_alive():
                trigger_run_now()
                job.localization_status = f"queued_{len(unique_names)}_tags_worker_running"
            else:
                from ..services.tag_translation_worker import start_worker
                start_worker()
                trigger_run_now()
                job.localization_status = f"queued_{len(unique_names)}_tags_worker_started"
            logger.info(f"AI tag job {job.id}: queued {len(unique_names)} tags for background worker")
        elif settings.TAG_TRANSLATION_AUTO_ENABLED and settings.TAG_TRANSLATION_LLM_ENABLED:
            from ..services.tag_localization_service import schedule_auto_translate
            schedule_auto_translate(unique_names)
            job.localization_status = f"scheduled_{len(unique_names)}_tags"
            logger.info(f"AI tag job {job.id}: scheduled localization for {len(unique_names)} tags")
        elif settings.TAG_TRANSLATION_LLM_ENABLED:
            job.localization_status = f"queued_{len(unique_names)}_tags_auto_disabled"
        else:
            job.localization_status = "skipped_llm_disabled"
    except Exception as e:
        logger.error(f"AI tag job {job.id}: localization scheduling failed: {e}")
        job.localization_status = f"error: {str(e)[:200]}"


def mark_stale_ai_jobs(db: Session) -> int:
    """Mark any leftover pending/running/cancelling AI tag jobs as interrupted.
    Called at application startup to recover from unclean shutdowns."""
    stale = (
        db.query(AITagJob)
        .filter(AITagJob.status.in_(["pending", "running", "cancelling"]))
        .all()
    )
    count = 0
    for job in stale:
        job.status = "interrupted"
        job.error_message = "Application stopped while this job was running"
        job.finished_at = datetime.now(timezone.utc)
        count += 1
    if count:
        db.commit()
        logger.info(f"Marked {count} stale AI tag job(s) as interrupted")
    return count


def create_auto_tag_job_after_scan(
    scan_job_id: int,
    imported_media_ids: List[int],
) -> Optional[int]:
    """Create an AI tagging job triggered by a completed scan job.

    Called from the scan job worker after completion. Uses its own DB session.
    Returns the created AI job ID, or None if skipped/failed.
    """
    if not settings.AI_AUTO_TAG_AFTER_IMPORT:
        return None

    if not settings.AI_TAGGING_ENABLED:
        logger.info(f"Scan job {scan_job_id}: auto-tag skipped (AI_TAGGING_ENABLED=false)")
        return None

    if not imported_media_ids:
        logger.info(f"Scan job {scan_job_id}: auto-tag skipped (no new imports)")
        return None

    if is_ai_job_active():
        logger.info(f"Scan job {scan_job_id}: auto-tag skipped (another AI job is already running)")
        return None

    from ..database import SessionLocal
    db = SessionLocal()
    try:
        max_items = settings.AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS

        if settings.AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW:
            media_ids = imported_media_ids[:max_items]
        else:
            ai_tagged = (
                db.query(blombooru_media_tags.c.media_id)
                .filter(blombooru_media_tags.c.source == "ai_wd")
                .distinct()
                .subquery()
            )
            untagged = (
                db.query(Media.id)
                .filter(~Media.id.in_(ai_tagged))
                .order_by(Media.id.asc())
                .limit(max_items)
                .all()
            )
            media_ids = [row[0] for row in untagged]

        if not media_ids:
            logger.info(f"Scan job {scan_job_id}: auto-tag skipped (no eligible media after filtering)")
            return None

        job = create_ai_tag_job(
            db,
            media_ids=media_ids,
            max_items=max_items,
            dry_run=settings.AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN,
            only_without_ai_tags=settings.AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW,
            force_suggestions=settings.AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS,
            trigger_source="scan_job",
            scan_job_id=scan_job_id,
        )

        start_ai_tag_job(job.id)
        logger.info(
            f"Scan job {scan_job_id}: created AI tag job {job.id} for "
            f"{len(media_ids)} media (max_items={max_items}, "
            f"dry_run={job.dry_run}, force_suggestions={job.force_suggestions})"
        )
        return job.id

    except Exception as exc:
        logger.error(f"Scan job {scan_job_id}: failed to create auto-tag job: {exc}", exc_info=True)
        return None
    finally:
        db.close()
