import json
import multiprocessing
import os
import platform
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Media, ScanJob, ScanJobMedia
from ..schemas import RatingEnum
from .logger import logger
from .media_helpers import get_unique_filename
from .media_processor import calculate_file_hash

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

SKIP_EXTENSIONS = {".icloud"}

MAX_FAILED_REPORT = 50

_PROGRESS_FLUSH_INTERVAL = 10

_BLOCKED_DIR_NAMES = {"venv", "data", "media", "storage", ".git", "__pycache__"}

_IS_WINDOWS = platform.system() == "Windows"

_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
_FILE_ATTRIBUTE_HIDDEN = 0x2
_CLOUD_ATTR_MASK = (
    _FILE_ATTRIBUTE_OFFLINE
    | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    | _FILE_ATTRIBUTE_RECALL_ON_OPEN
)

_SKIP_REASON_STAT_MAP = {
    "icloud_placeholder": "skipped_cloud_placeholder",
    "cloud_placeholder": "skipped_cloud_placeholder",
    "zero_byte_file": "skipped_zero_byte",
    "hidden": "skipped_hidden",
    "too_large": "skipped_too_large",
}


def _hash_file_in_subprocess(file_path: str, result_queue: multiprocessing.Queue):
    """Target function for subprocess-based hash calculation."""
    import hashlib
    try:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        result_queue.put(("ok", hash_md5.hexdigest()))
    except Exception as e:
        result_queue.put(("error", str(e)))


def _calculate_file_hash_with_timeout(file_path: Path, timeout_sec: int) -> tuple:
    """Calculate file hash with hard timeout via subprocess.

    Returns ("ok", hash_str) on success, ("timeout", msg) on timeout,
    or ("error", msg) on read error. The subprocess is terminated on timeout
    so no stuck I/O thread can block the scan.
    """
    result_queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_hash_file_in_subprocess,
        args=(str(file_path), result_queue),
        daemon=True,
    )
    proc.start()
    proc.join(timeout=timeout_sec)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        return ("timeout", f"hash timed out after {timeout_sec}s")

    try:
        status, value = result_queue.get_nowait()
        return (status, value)
    except Exception:
        exit_code = proc.exitcode
        return ("error", f"subprocess exited unexpectedly (code={exit_code})")


def _is_cloud_only(file_path: Path) -> bool:
    """Check if a file is cloud-only (not locally hydrated) on Windows.

    Uses GetFileAttributesW via ctypes to check for OFFLINE,
    RECALL_ON_DATA_ACCESS, and RECALL_ON_OPEN attributes.
    Returns False on non-Windows or on any error.
    """
    if not _IS_WINDOWS:
        return False
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(file_path))
        if attrs == -1:
            return False
        return bool(attrs & _CLOUD_ATTR_MASK)
    except Exception:
        return False


def _is_hidden(file_path: Path) -> bool:
    """Check if a file is hidden (dotfile or Windows hidden attribute)."""
    if file_path.name.startswith("."):
        return True
    if _IS_WINDOWS:
        try:
            import ctypes
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(file_path))
            if attrs != -1 and (attrs & _FILE_ATTRIBUTE_HIDDEN):
                return True
        except Exception:
            pass
    return False


def validate_scan_paths(paths: List[Path]) -> Optional[str]:
    """Return an error message if any path is unsafe, else None."""
    base = settings.BASE_DIR.resolve()
    blocked = [base / n for n in _BLOCKED_DIR_NAMES]
    blocked.append(base)

    for p in paths:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        for b in blocked:
            try:
                if resolved == b or resolved.is_relative_to(b):
                    return f"Refused to scan project-internal path: {p}"
            except (ValueError, TypeError):
                pass
    return None


def _is_scannable_file(file_path: Path, *, hydrated_only: bool = True) -> str | None:
    """Return None if the file is scannable, or a skip-reason string."""
    if file_path.is_symlink():
        return "symlink"

    if not file_path.is_file():
        return "not_a_file"

    if _is_hidden(file_path):
        return "hidden"

    if file_path.suffix.lower() in SKIP_EXTENSIONS:
        return "icloud_placeholder"

    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return "unsupported_extension"

    if hydrated_only and _is_cloud_only(file_path):
        return "cloud_placeholder"

    try:
        size = file_path.stat().st_size
    except OSError as e:
        return f"stat_error: {e}"

    if size == 0:
        return "zero_byte_file"

    max_bytes = settings.SCAN_MAX_FILE_SIZE_MB * 1024 * 1024
    if size > max_bytes:
        return "too_large"

    return None


def _map_skip_reason(stats: Dict[str, Any], reason: str) -> None:
    """Increment the appropriate skip counter based on the reason string."""
    stat_key = _SKIP_REASON_STAT_MAP.get(reason)
    if stat_key and stat_key in stats:
        stats[stat_key] += 1
    elif reason.startswith("stat_error"):
        stats["skipped_unreadable"] += 1
    else:
        stats["skipped_unsupported"] += 1


def scan_and_import(
    db: Session,
    paths: List[Path],
    *,
    dry_run: bool = False,
    max_files: Optional[int] = None,
    hydrated_only: bool = True,
    cancel_check: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Scan external directories and import supported images.

    This function is used by both the legacy synchronous endpoint and the
    background job runner.

    *cancel_check*: callable returning True when the job should abort.
    *progress_callback*: called periodically with the current stats dict.

    The returned stats dict includes ``imported_media_ids`` — a list of
    Media.id values for successfully imported items (empty during dry-run).
    """
    if not dry_run:
        from ..routes.media import process_and_save_media

    stats: Dict[str, Any] = {
        "dry_run": dry_run,
        "max_files": max_files,
        "total_seen": 0,
        "processed": 0,
        "imported": 0,
        "skipped_duplicate": 0,
        "skipped_unsupported": 0,
        "skipped_limit": 0,
        "skipped_cloud_placeholder": 0,
        "skipped_zero_byte": 0,
        "skipped_timeout": 0,
        "skipped_unreadable": 0,
        "skipped_hidden": 0,
        "skipped_too_large": 0,
        "failed": 0,
        "limit_reached": False,
        "failed_files": [],
        "imported_media_ids": [],
    }

    candidates_processed = 0
    files_since_flush = 0

    existing_hashes: set = set()
    for row in db.query(Media.hash).all():
        if row[0]:
            existing_hashes.add(row[0])

    valid_paths: List[Path] = []
    for p in paths:
        if not p.exists():
            stats["failed"] += 1
            _record_failure(stats, str(p), "directory does not exist")
            continue
        if not p.is_dir():
            stats["failed"] += 1
            _record_failure(stats, str(p), "path is not a directory")
            continue
        valid_paths.append(p)

    def _should_cancel() -> bool:
        return cancel_check is not None and cancel_check()

    def _maybe_flush_progress():
        nonlocal files_since_flush
        files_since_flush += 1
        if files_since_flush >= _PROGRESS_FLUSH_INTERVAL and progress_callback:
            progress_callback(stats)
            files_since_flush = 0

    for scan_dir in valid_paths:
        if _should_cancel():
            break
        if stats["limit_reached"]:
            break

        logger.info(f"Scanning local library: {scan_dir} (dry_run={dry_run})")

        try:
            entries = scan_dir.rglob("*")
        except OSError as e:
            _record_failure(stats, str(scan_dir), f"rglob error: {e}")
            stats["failed"] += 1
            continue

        for file_path in entries:
            if _should_cancel():
                break

            if max_files is not None and candidates_processed >= max_files:
                stats["limit_reached"] = True
                break

            stats["total_seen"] += 1

            skip_reason = _is_scannable_file(file_path, hydrated_only=hydrated_only)
            if skip_reason:
                _map_skip_reason(stats, skip_reason)
                _maybe_flush_progress()
                continue

            candidates_processed += 1
            stats["processed"] = candidates_processed

            copied_path: Path | None = None
            try:
                timeout_sec = settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS
                hash_status, hash_value = _calculate_file_hash_with_timeout(
                    file_path, timeout_sec
                )
                if hash_status == "timeout":
                    stats["skipped_timeout"] += 1
                    _record_failure(stats, str(file_path), hash_value)
                    _maybe_flush_progress()
                    continue
                elif hash_status == "error":
                    stats["skipped_unreadable"] += 1
                    _record_failure(stats, str(file_path), f"read error: {hash_value}")
                    _maybe_flush_progress()
                    continue
                file_hash = hash_value

                if file_hash in existing_hashes:
                    stats["skipped_duplicate"] += 1
                    _maybe_flush_progress()
                    continue

                if db.query(Media.id).filter(Media.hash == file_hash).first():
                    existing_hashes.add(file_hash)
                    stats["skipped_duplicate"] += 1
                    _maybe_flush_progress()
                    continue

                if dry_run:
                    existing_hashes.add(file_hash)
                    stats["imported"] += 1
                    logger.debug(f"Dry-run would import: {file_path.name}")
                    _maybe_flush_progress()
                    continue

                unique_name = get_unique_filename(
                    settings.ORIGINAL_DIR, file_path.name
                )
                copied_path = settings.ORIGINAL_DIR / unique_name

                shutil.copy2(str(file_path), str(copied_path))

                source_uri = f"file://{file_path}"

                media_resp = process_and_save_media(
                    db=db,
                    file_path=copied_path,
                    unique_filename=unique_name,
                    rating=RatingEnum.safe,
                    tags="",
                    album_ids=None,
                    source=source_uri,
                    category_hints=None,
                )

                existing_hashes.add(file_hash)
                stats["imported"] += 1
                if hasattr(media_resp, "id") and media_resp.id:
                    stats["imported_media_ids"].append(media_resp.id)
                logger.debug(f"Imported: {file_path.name}")

            except Exception as e:
                if copied_path and copied_path.exists():
                    try:
                        copied_path.unlink()
                    except OSError:
                        pass

                error_msg = str(e)
                if hasattr(e, "detail"):
                    error_msg = e.detail
                if "duplicate" in error_msg.lower() or "already exists" in error_msg.lower():
                    existing_hashes.add(file_hash if "file_hash" in dir() else "")
                    stats["skipped_duplicate"] += 1
                else:
                    stats["failed"] += 1
                    _record_failure(stats, str(file_path), error_msg)

            _maybe_flush_progress()

    if progress_callback:
        progress_callback(stats)

    return stats


def _record_failure(stats: Dict[str, Any], path: str, reason: str):
    if len(stats["failed_files"]) < MAX_FAILED_REPORT:
        stats["failed_files"].append({"path": path, "reason": reason})


def preflight_analyze(
    paths: List[Path],
    *,
    hydrated_only: bool = True,
    max_files: Optional[int] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Stat-only analysis of scan directories.  Never calls open().

    Returns the same-shape stats dict as scan_and_import (minus import fields)
    plus ``estimated_size_bytes``, ``extensions``, and ``largest_file_bytes``.
    """
    stats: Dict[str, Any] = {
        "dry_run": True,
        "max_files": max_files,
        "total_seen": 0,
        "processed": 0,
        "imported": 0,
        "skipped_duplicate": 0,
        "skipped_unsupported": 0,
        "skipped_limit": 0,
        "skipped_cloud_placeholder": 0,
        "skipped_zero_byte": 0,
        "skipped_timeout": 0,
        "skipped_unreadable": 0,
        "skipped_hidden": 0,
        "skipped_too_large": 0,
        "failed": 0,
        "limit_reached": False,
        "failed_files": [],
        "imported_media_ids": [],
        "estimated_size_bytes": 0,
        "largest_file_bytes": 0,
        "extensions": {},
    }

    candidates_processed = 0
    files_since_flush = 0

    def _should_cancel() -> bool:
        return cancel_check is not None and cancel_check()

    def _maybe_flush():
        nonlocal files_since_flush
        files_since_flush += 1
        if files_since_flush >= _PROGRESS_FLUSH_INTERVAL and progress_callback:
            progress_callback(stats)
            files_since_flush = 0

    valid_paths: List[Path] = []
    for p in paths:
        if not p.exists():
            stats["failed"] += 1
            _record_failure(stats, str(p), "directory does not exist")
            continue
        if not p.is_dir():
            stats["failed"] += 1
            _record_failure(stats, str(p), "path is not a directory")
            continue
        valid_paths.append(p)

    for scan_dir in valid_paths:
        if _should_cancel():
            break
        if stats["limit_reached"]:
            break

        try:
            entries = scan_dir.rglob("*")
        except OSError as e:
            _record_failure(stats, str(scan_dir), f"rglob error: {e}")
            stats["failed"] += 1
            continue

        for file_path in entries:
            if _should_cancel():
                break

            if max_files is not None and candidates_processed >= max_files:
                stats["limit_reached"] = True
                break

            stats["total_seen"] += 1

            skip_reason = _is_scannable_file(file_path, hydrated_only=hydrated_only)
            if skip_reason:
                _map_skip_reason(stats, skip_reason)
                _maybe_flush()
                continue

            candidates_processed += 1
            stats["processed"] = candidates_processed

            try:
                size = file_path.stat().st_size
            except OSError:
                size = 0

            stats["estimated_size_bytes"] += size
            if size > stats["largest_file_bytes"]:
                stats["largest_file_bytes"] = size

            ext = file_path.suffix.lower()
            stats["extensions"][ext] = stats["extensions"].get(ext, 0) + 1

            stats["imported"] += 1
            _maybe_flush()

    if progress_callback:
        progress_callback(stats)

    return stats


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

_active_job_lock = threading.Lock()
_active_job_cancel: Dict[int, bool] = {}


def is_job_active() -> bool:
    """Check if a scan job is currently running (thread-safe)."""
    with _active_job_lock:
        return bool(_active_job_cancel)


def request_cancel(job_id: int) -> None:
    """Signal a running job to stop.

    Works even if the worker hasn't registered yet — the flag is pre-set
    so that when the worker registers it will see the cancellation immediately.
    """
    with _active_job_lock:
        _active_job_cancel[job_id] = True


def run_scan_job(job_id: int) -> None:
    """Execute a scan job in a background thread with its own DB session."""
    from ..database import SessionLocal

    db: Session = SessionLocal()
    try:
        job: ScanJob = db.query(ScanJob).get(job_id)
        if job is None:
            logger.error(f"Scan job {job_id} not found")
            return

        # Check if cancel was requested before the worker started (race fix).
        # The cancel endpoint persists status='cancelling' to DB and pre-sets
        # the in-memory flag via request_cancel().
        with _active_job_lock:
            already_cancelled = _active_job_cancel.get(job_id, False)
            _active_job_cancel[job_id] = already_cancelled

        if already_cancelled or job.status == "cancelling":
            job.status = "cancelled"
            job.finished_at = datetime.now(timezone.utc)
            job.error_message = "Cancelled before scan started"
            db.commit()
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        paths = [Path(p) for p in json.loads(job.paths_json)]

        def cancel_check() -> bool:
            with _active_job_lock:
                return _active_job_cancel.get(job_id, False)

        def progress_callback(stats: Dict[str, Any]) -> None:
            try:
                j = db.query(ScanJob).get(job_id)
                if j is None:
                    return
                j.total_seen = stats["total_seen"]
                j.processed = stats["processed"]
                j.imported = stats["imported"]
                j.skipped_duplicate = stats["skipped_duplicate"]
                j.skipped_unsupported = stats["skipped_unsupported"]
                j.skipped_limit = stats["skipped_limit"]
                j.skipped_cloud_placeholder = stats["skipped_cloud_placeholder"]
                j.skipped_zero_byte = stats["skipped_zero_byte"]
                j.skipped_timeout = stats["skipped_timeout"]
                j.skipped_unreadable = stats["skipped_unreadable"]
                j.skipped_hidden = stats["skipped_hidden"]
                j.skipped_too_large = stats["skipped_too_large"]
                j.failed = stats["failed"]
                j.limit_reached = stats.get("limit_reached", False)
                j.failed_files_json = json.dumps(stats["failed_files"][:MAX_FAILED_REPORT])
                db.commit()
            except Exception as e:
                logger.warning(f"Failed to flush scan job progress: {e}")
                try:
                    db.rollback()
                except Exception:
                    pass

        result = scan_and_import(
            db, paths,
            dry_run=job.dry_run,
            max_files=job.max_files,
            hydrated_only=job.hydrated_only if job.hydrated_only is not None else True,
            cancel_check=cancel_check,
            progress_callback=progress_callback,
        )

        was_cancelled = cancel_check()

        job = db.query(ScanJob).get(job_id)
        job.total_seen = result["total_seen"]
        job.processed = result["processed"]
        job.imported = result["imported"]
        job.skipped_duplicate = result["skipped_duplicate"]
        job.skipped_unsupported = result["skipped_unsupported"]
        job.skipped_limit = result["skipped_limit"]
        job.skipped_cloud_placeholder = result["skipped_cloud_placeholder"]
        job.skipped_zero_byte = result["skipped_zero_byte"]
        job.skipped_timeout = result["skipped_timeout"]
        job.skipped_unreadable = result["skipped_unreadable"]
        job.skipped_hidden = result["skipped_hidden"]
        job.skipped_too_large = result["skipped_too_large"]
        job.failed = result["failed"]
        job.limit_reached = result.get("limit_reached", False)
        job.failed_files_json = json.dumps(result["failed_files"][:MAX_FAILED_REPORT])
        job.finished_at = datetime.now(timezone.utc)
        job.status = "cancelled" if was_cancelled else "completed"

        imported_media_ids = result.get("imported_media_ids", [])
        for mid in imported_media_ids:
            db.add(ScanJobMedia(scan_job_id=job_id, media_id=mid))

        db.commit()

        if job.status == "completed" and imported_media_ids and not job.dry_run:
            try:
                from ..services.ai_tagging_job_service import create_auto_tag_job_after_scan
                create_auto_tag_job_after_scan(job_id, imported_media_ids)
            except Exception as auto_exc:
                logger.error(
                    f"Scan job {job_id}: auto-tag trigger failed (scan still completed): {auto_exc}",
                    exc_info=True,
                )

    except Exception as exc:
        logger.error(f"Scan job {job_id} failed: {exc}", exc_info=True)
        try:
            job = db.query(ScanJob).get(job_id)
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


def mark_stale_jobs(db: Session) -> int:
    """Mark any leftover pending/running/cancelling jobs as interrupted.

    Called at application startup to recover from unclean shutdowns.
    Returns the number of jobs marked.
    """
    stale = (
        db.query(ScanJob)
        .filter(ScanJob.status.in_(["pending", "running", "cancelling"]))
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
        logger.info(f"Marked {count} stale scan job(s) as interrupted")
    return count
