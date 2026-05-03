import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Media
from ..schemas import RatingEnum
from .logger import logger
from .media_helpers import get_unique_filename
from .media_processor import calculate_file_hash

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

SKIP_EXTENSIONS = {".icloud"}

MAX_FAILED_REPORT = 50


def _is_scannable_file(file_path: Path) -> str | None:
    """Return None if the file is scannable, or a skip-reason string."""
    if file_path.is_symlink():
        return "symlink"

    if not file_path.is_file():
        return "not_a_file"

    if file_path.suffix.lower() in SKIP_EXTENSIONS:
        return "icloud_placeholder"

    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return "unsupported_extension"

    try:
        size = file_path.stat().st_size
    except OSError as e:
        return f"stat_error: {e}"

    if size == 0:
        return "zero_byte_file"

    return None


def scan_and_import(
    db: Session,
    paths: List[Path],
    *,
    dry_run: bool = False,
    max_files: Optional[int] = None,
) -> Dict[str, Any]:
    """Scan external directories and import supported images into Blombooru.

    Files are *copied* into ``media/original``; originals are never moved or
    deleted.  Duplicates are detected by MD5 hash (``Media.hash`` unique
    constraint).

    When *dry_run* is ``True`` the scan still walks directories, hashes files,
    and checks for duplicates, but **no files are copied and no database rows
    are created**.  The returned statistics show what *would* happen.

    When *max_files* is set, at most that many **candidate** files (files that
    pass the extension/symlink/size filter) will be processed.  Files beyond
    the cap are not hashed or imported.

    Returns a statistics dict suitable for direct JSON serialisation.
    """
    if not dry_run:
        from ..routes.media import process_and_save_media

    stats: Dict[str, Any] = {
        "dry_run": dry_run,
        "max_files": max_files,
        "total_seen": 0,
        "imported": 0,
        "skipped_duplicate": 0,
        "skipped_unsupported": 0,
        "skipped_limit": 0,
        "failed": 0,
        "failed_files": [],
    }

    candidates_processed = 0

    existing_hashes: set = set()
    for row in db.query(Media.hash).all():
        if row[0]:
            existing_hashes.add(row[0])

    valid_paths: List[Path] = []
    for p in paths:
        if not p.exists():
            _record_failure(stats, str(p), "directory does not exist")
            continue
        if not p.is_dir():
            _record_failure(stats, str(p), "path is not a directory")
            continue
        valid_paths.append(p)

    limit_reached = False

    for scan_dir in valid_paths:
        if limit_reached:
            break

        logger.info(f"Scanning local library: {scan_dir} (dry_run={dry_run})")

        try:
            entries = list(scan_dir.rglob("*"))
        except OSError as e:
            _record_failure(stats, str(scan_dir), f"rglob error: {e}")
            continue

        for file_path in entries:
            stats["total_seen"] += 1

            skip_reason = _is_scannable_file(file_path)
            if skip_reason:
                stats["skipped_unsupported"] += 1
                continue

            if max_files is not None and candidates_processed >= max_files:
                stats["skipped_limit"] += 1
                continue

            candidates_processed += 1

            copied_path: Path | None = None
            try:
                file_hash = calculate_file_hash(file_path)

                if file_hash in existing_hashes:
                    stats["skipped_duplicate"] += 1
                    continue

                if db.query(Media.id).filter(Media.hash == file_hash).first():
                    existing_hashes.add(file_hash)
                    stats["skipped_duplicate"] += 1
                    continue

                if dry_run:
                    existing_hashes.add(file_hash)
                    stats["imported"] += 1
                    logger.debug(f"Dry-run would import: {file_path.name}")
                    continue

                unique_name = get_unique_filename(
                    settings.ORIGINAL_DIR, file_path.name
                )
                copied_path = settings.ORIGINAL_DIR / unique_name

                shutil.copy2(str(file_path), str(copied_path))

                source_uri = f"file://{file_path}"

                process_and_save_media(
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

    return stats


def _record_failure(stats: Dict[str, Any], path: str, reason: str):
    if len(stats["failed_files"]) < MAX_FAILED_REPORT:
        stats["failed_files"].append({"path": path, "reason": reason})
