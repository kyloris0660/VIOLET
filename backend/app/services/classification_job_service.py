"""Classification Job Service — manages background content classification jobs.

Follows the same architecture as ai_tagging_job_service.py:
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
from ..models import ClassificationJob, Media, blombooru_media_tags

logger = logging.getLogger(__name__)

_PROGRESS_FLUSH_INTERVAL = 10

_active_job_lock = threading.Lock()
_active_job_cancel: Dict[int, bool] = {}


def is_classification_job_active() -> bool:
    with _active_job_lock:
        return bool(_active_job_cancel)


def request_classification_job_cancel(job_id: int) -> None:
    with _active_job_lock:
        _active_job_cancel[job_id] = True


def create_classification_job(
    db: Session,
    *,
    media_ids: Optional[List[int]] = None,
    max_items: int = 100,
    only_unclassified: bool = True,
    force_reclassify: bool = False,
    trigger_source: str = "manual",
    scan_job_id: Optional[int] = None,
) -> ClassificationJob:
    hard_limit = settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS
    effective_max = min(max_items, hard_limit)

    job = ClassificationJob(
        status="pending",
        trigger_source=trigger_source,
        scan_job_id=scan_job_id,
        media_ids_json=json.dumps(media_ids) if media_ids else None,
        max_items=effective_max,
        only_unclassified=only_unclassified,
        force_reclassify=force_reclassify,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def start_classification_job(job_id: int) -> None:
    t = threading.Thread(target=run_classification_job, args=(job_id,), daemon=True)
    t.start()


def run_classification_job(job_id: int) -> None:
    from ..database import SessionLocal
    from ..services.content_classifier import classify_media

    db: Session = SessionLocal()
    try:
        job: ClassificationJob = db.query(ClassificationJob).get(job_id)
        if job is None:
            logger.error(f"Classification job {job_id} not found")
            return

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

        media_ids = _resolve_media_ids(db, job)

        if not media_ids:
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc)
            job.error_message = "No eligible media items found"
            db.commit()
            return

        # ── CLIP readiness pre-check ──────────────────────────────────
        # When classification method is 'clip', verify the model is
        # loadable BEFORE entering the per-item loop.  Without this,
        # a missing/corrupted CLIP model causes every single item to
        # fail individually — the first failure triggers a 300-second
        # cooldown (CLIPClassifier._LOAD_COOLDOWN_SECONDS), and all
        # remaining items silently fail with the cooldown error.
        # Failing fast here gives a clear, single error message.
        #
        # Skip the pre-check when NO candidate actually requires CLIP
        # inference (e.g. video-only jobs).  The per-item skip condition
        # is defined in content_classifier.requires_clip_inference().
        _needs_clip = False
        if settings.CONTENT_CLASSIFICATION_METHOD == "clip":
            from ..services.content_classifier import requires_clip_inference
            clip_candidates = (
                db.query(Media)
                .filter(Media.id.in_(media_ids))
                .all()
            )
            _needs_clip = any(requires_clip_inference(m) for m in clip_candidates)

        if _needs_clip:
            try:
                from .clip_classifier import CLIPClassifier
                _clip = CLIPClassifier()
                if not _clip.ensure_loaded():
                    err = getattr(_clip, "_load_error", None) or "CLIP model not loadable"
                    job.status = "failed"
                    job.finished_at = datetime.now(timezone.utc)
                    job.error_message = (
                        f"CLIP readiness pre-check failed: {err}. "
                        "Run 'python scripts/check_clip_model_ready.py' to diagnose. "
                        "If the model is cached, set HF_HUB_OFFLINE=1 to avoid network issues."
                    )
                    logger.warning(
                        "Classification job %d: CLIP pre-check failed — %s",
                        job_id, err,
                    )
                    db.commit()
                    return
            except ImportError as e:
                job.status = "failed"
                job.finished_at = datetime.now(timezone.utc)
                job.error_message = (
                    f"CLIP pre-check import error: {e}. "
                    "Ensure CLIP dependencies (onnxruntime, numpy, Pillow) are installed."
                )
                logger.warning(
                    "Classification job %d: CLIP import failed — %s",
                    job_id, e,
                )
                db.commit()
                return

        items_since_flush = 0
        failed_items: List[Dict[str, Any]] = []

        for media_id in media_ids:
            with _active_job_lock:
                if _active_job_cancel.get(job_id, False):
                    break

            try:
                result = classify_media(db, media_id)
                job.processed += 1

                if result.get("error"):
                    job.failed += 1
                    if len(failed_items) < 50:
                        failed_items.append({
                            "media_id": media_id,
                            "error": result["error"],
                        })
                elif result.get("skipped"):
                    pass
                else:
                    cls = result.get("content_class")
                    if cls == "anime":
                        job.classified_anime += 1
                    elif cls == "non_anime":
                        job.classified_non_anime += 1
                    else:
                        job.classified_unknown += 1

            except Exception as exc:
                logger.error(f"Classification job {job_id}: failed on media {media_id}: {exc}", exc_info=True)
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

        was_cancelled = False
        with _active_job_lock:
            was_cancelled = _active_job_cancel.get(job_id, False)

        job.failed_items_json = json.dumps(failed_items[:50]) if failed_items else None
        job.finished_at = datetime.now(timezone.utc)
        job.status = "cancelled" if was_cancelled else "completed"
        db.commit()

    except Exception as exc:
        logger.error(f"Classification job {job_id} failed: {exc}", exc_info=True)
        try:
            job = db.query(ClassificationJob).get(job_id)
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


def _resolve_media_ids(db: Session, job: ClassificationJob) -> List[int]:
    if job.media_ids_json:
        all_ids = json.loads(job.media_ids_json)
    else:
        query = db.query(Media.id)
        if job.only_unclassified and not getattr(job, 'force_reclassify', False):
            query = query.filter(Media.content_class.is_(None))
        query = query.order_by(Media.id.asc())
        all_ids = [row[0] for row in query.all()]

    return all_ids[:job.max_items]


def _flush_progress(db: Session, job: ClassificationJob, failed_items: List[Dict]) -> None:
    try:
        job.failed_items_json = json.dumps(failed_items[:50]) if failed_items else None
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to flush classification job progress: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def mark_stale_classification_jobs(db: Session) -> int:
    stale = (
        db.query(ClassificationJob)
        .filter(ClassificationJob.status.in_(["pending", "running", "cancelling"]))
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
        logger.info(f"Marked {count} stale classification job(s) as interrupted")
    return count


def create_auto_classification_job_after_scan(
    scan_job_id: int,
    imported_media_ids: List[int],
) -> Optional[int]:
    if not settings.CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT:
        return None

    if not settings.CONTENT_CLASSIFICATION_ENABLED:
        logger.info(f"Scan job {scan_job_id}: auto-classify skipped (CONTENT_CLASSIFICATION_ENABLED=false)")
        return None

    if not imported_media_ids:
        logger.info(f"Scan job {scan_job_id}: auto-classify skipped (no new imports)")
        return None

    if is_classification_job_active():
        logger.info(f"Scan job {scan_job_id}: auto-classify skipped (another classification job is running)")
        return None
    try:
        from .manual_sync_execute_service import is_manual_sync_execute_active

        if is_manual_sync_execute_active():
            logger.info(f"Scan job {scan_job_id}: auto-classify skipped (manual sync execute is active)")
            return None
    except Exception as exc:
        logger.warning("Scan job %s: manual sync active check failed before auto-classify: %s", scan_job_id, exc)
        return None

    from ..database import SessionLocal
    db = SessionLocal()
    try:
        try:
            from .manual_sync_execute_service import assert_manual_sync_execute_inactive_for_classification_job

            assert_manual_sync_execute_inactive_for_classification_job(db)
        except Exception as exc:
            logger.info("Scan job %s: auto-classify skipped (manual sync execute guard: %s)", scan_job_id, exc)
            return None

        max_items = settings.CONTENT_CLASSIFICATION_AUTO_MAX_ITEMS
        media_ids = imported_media_ids[:max_items]

        if not media_ids:
            return None

        job = create_classification_job(
            db,
            media_ids=media_ids,
            max_items=max_items,
            only_unclassified=True,
            trigger_source="scan_job",
            scan_job_id=scan_job_id,
        )

        start_classification_job(job.id)
        logger.info(
            f"Scan job {scan_job_id}: created classification job {job.id} for "
            f"{len(media_ids)} media (max_items={max_items})"
        )
        return job.id

    except Exception as exc:
        logger.error(f"Scan job {scan_job_id}: failed to create auto-classify job: {exc}", exc_info=True)
        return None
    finally:
        db.close()
