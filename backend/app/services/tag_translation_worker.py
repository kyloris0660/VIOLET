"""Background Tag Translation Worker — continuously translates missing tags via LLM.

Architecture:
- Single worker thread with asyncio event loop
- Periodic interval checks for missing tags
- Batch LLM translation with error handling and backoff
- Daily limit tracking (resets at midnight UTC)
- Pause/resume support via in-memory flag
- Job history via TagTranslationJob table
"""
import asyncio
import logging
import threading
import time
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()
_worker_paused = False
_worker_running = False
_worker_lock = threading.Lock()
_run_now_event = threading.Event()

_daily_translated = 0
_daily_date: Optional[date] = None
_last_run_at: Optional[datetime] = None
_next_run_at: Optional[datetime] = None
_last_error: Optional[str] = None
_current_job_id: Optional[int] = None
_consecutive_errors = 0


def get_worker_status() -> Dict[str, Any]:
    """Return current worker status for the API."""
    from ..config import settings
    from ..database import SessionLocal
    from .tag_localization_service import get_translation_stats

    status_str = "disabled"
    if not settings.TAG_TRANSLATION_BG_ENABLED:
        status_str = "disabled"
    elif _worker_paused:
        status_str = "paused"
    elif _worker_running:
        status_str = "running"
    elif _worker_thread and _worker_thread.is_alive():
        status_str = "idle"
    else:
        status_str = "stopped"

    missing_count = 0
    if SessionLocal:
        db = SessionLocal()
        try:
            stats = get_translation_stats(db)
            missing_count = stats.get("missing", 0)
        except Exception:
            pass
        finally:
            db.close()

    return {
        "enabled": settings.TAG_TRANSLATION_BG_ENABLED,
        "status": status_str,
        "paused": _worker_paused,
        "running": _worker_running,
        "last_run_at": _last_run_at.isoformat() if _last_run_at else None,
        "next_run_at": _next_run_at.isoformat() if _next_run_at and not _worker_paused else None,
        "processed_today": _daily_translated,
        "daily_limit": settings.TAG_TRANSLATION_BG_DAILY_LIMIT,
        "daily_remaining": max(0, settings.TAG_TRANSLATION_BG_DAILY_LIMIT - _daily_translated),
        "missing_count": missing_count,
        "last_error": _last_error,
        "current_job_id": _current_job_id,
        "consecutive_errors": _consecutive_errors,
        "config": {
            "interval_seconds": settings.TAG_TRANSLATION_BG_INTERVAL,
            "batch_size": settings.TAG_TRANSLATION_BG_BATCH_SIZE,
            "max_per_run": settings.TAG_TRANSLATION_BG_MAX_PER_RUN,
            "error_limit": settings.TAG_TRANSLATION_BG_ERROR_LIMIT,
            "priority": settings.TAG_TRANSLATION_BG_PRIORITY,
            "categories": settings.TAG_TRANSLATION_BG_CATEGORIES,
        },
    }


def start_worker():
    """Start the background translation worker thread."""
    global _worker_thread
    from ..config import settings

    if not settings.TAG_TRANSLATION_BG_ENABLED:
        logger.info("Background tag translation worker disabled")
        return

    if not settings.TAG_TRANSLATION_LLM_ENABLED:
        logger.info("Background tag translation worker skipped: LLM not enabled")
        return

    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            logger.info("Background tag translation worker already running")
            return

        _worker_stop.clear()
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        _worker_thread.start()
        logger.info("Background tag translation worker started (interval=%ds)", settings.TAG_TRANSLATION_BG_INTERVAL)


def stop_worker():
    """Signal the worker to stop gracefully."""
    _worker_stop.set()
    _run_now_event.set()
    logger.info("Background tag translation worker stop requested")


def pause_worker():
    """Pause the worker (it stays alive but skips runs)."""
    global _worker_paused
    _worker_paused = True
    logger.info("Background tag translation worker paused")


def resume_worker():
    """Resume the worker."""
    global _worker_paused
    _worker_paused = False
    logger.info("Background tag translation worker resumed")


def trigger_run_now():
    """Trigger an immediate worker run."""
    _run_now_event.set()
    logger.info("Background tag translation run-now triggered")


def _worker_loop():
    """Main worker loop running in a daemon thread."""
    global _next_run_at, _last_error, _consecutive_errors
    from ..config import settings

    logger.info("Background tag translation worker loop started")

    while not _worker_stop.is_set():
        interval = settings.TAG_TRANSLATION_BG_INTERVAL
        _next_run_at = datetime.now(timezone.utc).replace(
            microsecond=0
        ) + timedelta(seconds=interval)

        _run_now_event.wait(timeout=interval)
        _run_now_event.clear()

        if _worker_stop.is_set():
            break

        if _worker_paused:
            continue

        _check_daily_reset()

        if _daily_translated >= settings.TAG_TRANSLATION_BG_DAILY_LIMIT:
            logger.debug("Background translation: daily limit reached (%d/%d)",
                         _daily_translated, settings.TAG_TRANSLATION_BG_DAILY_LIMIT)
            continue

        if _consecutive_errors >= settings.TAG_TRANSLATION_BG_ERROR_LIMIT:
            logger.warning("Background translation: paused due to %d consecutive errors", _consecutive_errors)
            continue

        try:
            _run_one_cycle()
        except Exception as e:
            _consecutive_errors += 1
            _last_error = f"[{datetime.now(timezone.utc).isoformat()}] {str(e)[:500]}"
            logger.error("Background translation cycle error: %s", e, exc_info=True)

    logger.info("Background tag translation worker loop exited")


def _check_daily_reset():
    """Reset daily counter if the date has changed."""
    global _daily_translated, _daily_date
    today = date.today()
    if _daily_date != today:
        _daily_translated = 0
        _daily_date = today


def _run_one_cycle():
    """Execute a single translation cycle."""
    global _worker_running, _last_run_at, _last_error, _current_job_id
    global _daily_translated, _consecutive_errors

    from ..config import settings
    from ..database import SessionLocal
    from .llm_translation_provider import get_llm_provider
    from .tag_localization_service import list_missing_translations, upsert_translation
    from ..utils.search_parser import invalidate_translation_cache
    from ..models import TagTranslationJob

    if not SessionLocal:
        return

    provider = get_llm_provider()
    if not provider.is_available():
        return

    db: Session = SessionLocal()
    _worker_running = True
    _last_run_at = datetime.now(timezone.utc)

    remaining_budget = settings.TAG_TRANSLATION_BG_DAILY_LIMIT - _daily_translated
    max_this_run = min(settings.TAG_TRANSLATION_BG_MAX_PER_RUN, remaining_budget)

    if max_this_run <= 0:
        _worker_running = False
        db.close()
        return

    missing = list_missing_translations(db, limit=max_this_run,
                                        categories=settings.TAG_TRANSLATION_BG_CATEGORIES)
    if not missing:
        _worker_running = False
        db.close()
        return

    job = TagTranslationJob(
        status="running",
        source="background",
        category=",".join(settings.TAG_TRANSLATION_BG_CATEGORIES),
        batch_size=settings.TAG_TRANSLATION_BG_BATCH_SIZE,
        max_per_run=max_this_run,
        remaining_before=len(missing),
    )
    job.started_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    db.refresh(job)
    _current_job_id = job.id

    total_translated = 0
    total_failed = 0
    total_skipped = 0
    total_processed = 0
    last_err = None
    batch_size = settings.TAG_TRANSLATION_BG_BATCH_SIZE

    try:
        for i in range(0, len(missing), batch_size):
            if _worker_stop.is_set() or _worker_paused:
                break

            if _daily_translated >= settings.TAG_TRANSLATION_BG_DAILY_LIMIT:
                break

            batch = missing[i:i + batch_size]
            tag_inputs = [{"name": t["canonical_name"], "category": t["category"]} for t in batch]

            try:
                loop = asyncio.new_event_loop()
                try:
                    translations = loop.run_until_complete(provider.translate_tags(tag_inputs))
                finally:
                    loop.close()

                for tr in translations:
                    try:
                        saved = upsert_translation(
                            db,
                            canonical_name=tr.canonical_name,
                            display_name=tr.display_name_zh,
                            lang="zh-CN",
                            aliases=tr.aliases_zh,
                            category=tr.category,
                            source="llm",
                            status="translated",
                            needs_review=tr.needs_review,
                            provider=provider.get_provider_name(),
                        )
                        if saved:
                            total_translated += 1
                            _daily_translated += 1
                        else:
                            total_skipped += 1
                    except Exception as e:
                        total_failed += 1
                        last_err = f"{tr.canonical_name}: {str(e)[:200]}"
                        logger.error("Background translate save error: %s", e)
                        try:
                            db.rollback()
                        except Exception:
                            pass

                total_processed += len(batch)
                _consecutive_errors = 0

                job.processed = total_processed
                job.translated = total_translated
                job.failed = total_failed
                job.skipped = total_skipped
                db.commit()

            except Exception as e:
                _consecutive_errors += 1
                total_failed += len(batch)
                total_processed += len(batch)
                last_err = str(e)[:500]
                logger.error("Background translate batch error: %s", e)

                if _consecutive_errors >= settings.TAG_TRANSLATION_BG_ERROR_LIMIT:
                    break

                backoff = min(60, 5 * _consecutive_errors)
                time.sleep(backoff)

    finally:
        job.processed = total_processed
        job.translated = total_translated
        job.failed = total_failed
        job.skipped = total_skipped
        job.last_error = last_err
        job.finished_at = datetime.now(timezone.utc)
        job.status = "completed" if total_failed == 0 else ("rate_limited" if _consecutive_errors >= settings.TAG_TRANSLATION_BG_ERROR_LIMIT else "completed")

        if _worker_paused:
            job.status = "paused"
        if _worker_stop.is_set():
            job.status = "interrupted"

        from .tag_localization_service import get_translation_stats
        try:
            stats = get_translation_stats(db)
            job.remaining_after = stats.get("missing", None)
        except Exception:
            pass

        try:
            db.commit()
        except Exception:
            pass
        db.close()

        if total_translated > 0:
            invalidate_translation_cache()

        _current_job_id = None
        _worker_running = False
        _last_error = last_err

        logger.info(
            "Background translation cycle: processed=%d translated=%d failed=%d skipped=%d",
            total_processed, total_translated, total_failed, total_skipped,
        )


def mark_stale_translation_jobs(db: Session) -> int:
    """Mark leftover running/pending translation jobs as interrupted on startup."""
    from ..models import TagTranslationJob

    stale = (
        db.query(TagTranslationJob)
        .filter(TagTranslationJob.status.in_(["pending", "running"]))
        .all()
    )
    count = 0
    for j in stale:
        j.status = "interrupted"
        j.finished_at = datetime.now(timezone.utc)
        count += 1
    if count:
        db.commit()
        logger.info("Marked %d stale translation jobs as interrupted", count)
    return count
