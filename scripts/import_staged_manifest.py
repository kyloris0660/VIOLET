#!/usr/bin/env python3
"""Import an audited staged manifest into app-managed media storage.

Phase 3.5 intentionally avoids the local-library scan job path.  This tool is
manifest-driven, keeps staged/source files read-only, copies accepted media into
app-managed storage, and records privacy-sensitive per-file paths only in the
caller-provided local CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL, make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.enums import FileTypeEnum, RatingEnum  # noqa: E402
from app.utils.media_helpers import get_unique_filename  # noqa: E402
from app.utils.media_processor import calculate_file_hash, process_media_file  # noqa: E402
from app.utils.thumbnail_generator import generate_thumbnail  # noqa: E402

CONFIRM_PHRASE = "IMPORT_TIER1000_TO_DB"
IMPORT_SOURCE_LABEL = "violet:tier1000:phase3.5"
DEFAULT_COPY_REASONS = {"existing_tier500", "new_candidate"}
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VALID_ENVS = {"development", "test", "production"}
FORBIDDEN_TEST_DB_NAMES = {"blombooru", "production", "main", "postgres"}

DISABLED_FLAG_DEFAULTS = {
    "AI_TAGGING_ENABLED": False,
    "AI_AUTO_TAG_AFTER_IMPORT": False,
    "AI_TAGGING_AUTO_LOCALIZATION": True,
    "TAG_TRANSLATION_BACKGROUND_ENABLED": False,
    "TAG_TRANSLATION_AUTO_ENABLED": False,
    "TAG_TRANSLATION_LLM_ENABLED": False,
    "ENTITY_ALIAS_RESOLVER_ENABLED": False,
    "CONTENT_CLASSIFICATION_ENABLED": False,
    "CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT": False,
}


class ImportGateError(RuntimeError):
    """Raised when a hard safety gate fails."""


@dataclass
class RuntimeContext:
    repo_root: Path
    violet_env: str
    storage_root: Path
    original_dir: Path
    thumbnail_dir: Path
    database_url: URL
    safe_database_url: str
    db_name: str
    disabled_flags: dict[str, bool]


@dataclass
class ManifestStats:
    total_rows: int = 0
    copy_rows: int = 0
    duplicate_target_rows: int = 0
    duplicate_source_rows: int = 0


@dataclass
class ManifestCandidate:
    row_number: int
    row_id: str
    source_path: str
    proposed_target_path: str
    extension: str
    size_bytes: int
    selection_reason: str
    staged_path: Path | None = None
    staged_label: str | None = None
    file_hash: str | None = None
    invalid_reason: str | None = None


@dataclass
class ImportItem:
    candidate: ManifestCandidate
    status: str
    message: str = ""
    duplicate_media_id: int | None = None
    duplicate_media_path: str | None = None
    media_id: int | None = None
    managed_path: str | None = None
    thumbnail_path: str | None = None


@dataclass
class ImportReport:
    import_run_id: str
    mode: str
    started_at: str
    finished_at: str | None = None
    source_label: str = IMPORT_SOURCE_LABEL
    expected_copy_count: int = 0
    audit_summary: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    database: dict[str, Any] = field(default_factory=dict)
    storage: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    media_count: dict[str, int | None] = field(default_factory=dict)
    storage_stats: dict[str, Any] = field(default_factory=dict)
    imported_media_ids_sample: list[int] = field(default_factory=list)
    post_import_audit: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy_falsey(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    raise ImportGateError(
        f"Invalid boolean env {name}={raw!r}; expected true/false style value."
    )


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ImportGateError(f"Expected JSON object in {path}")
    return data


def _load_file_settings(storage_root: Path) -> dict[str, Any]:
    settings_file = storage_root / "data" / "settings.json"
    if not settings_file.exists():
        return {}
    return _read_json_file(settings_file)


def _parse_db_name_from_url(url: str) -> str:
    parsed = make_url(url)
    if not parsed.database:
        raise ImportGateError(f"Cannot parse database name from TEST_DATABASE_URL")
    return parsed.database


def _safe_url(url: URL) -> str:
    return str(url.set(password="***" if url.password else None))


def build_runtime_context(repo_root: Path = REPO_ROOT) -> RuntimeContext:
    load_dotenv(dotenv_path=repo_root / ".env", override=False)

    violet_env = os.getenv("VIOLET_ENV", "development").strip().lower()
    if violet_env not in VALID_ENVS:
        raise ImportGateError(f"Invalid VIOLET_ENV={violet_env!r}; expected one of {sorted(VALID_ENVS)}")

    storage_env = os.getenv("VIOLET_STORAGE_ROOT", "").strip()
    storage_root = Path(storage_env).expanduser() if storage_env else repo_root
    storage_root = storage_root.resolve()
    original_dir = storage_root / "media" / "original"
    thumbnail_dir = storage_root / "media" / "thumbnails"

    file_settings = _load_file_settings(storage_root)
    database_settings = file_settings.get("database", {})

    test_url = os.getenv("TEST_DATABASE_URL", "").strip()
    if violet_env == "test" and test_url:
        database_url = make_url(test_url)
        db_name = _parse_db_name_from_url(test_url)
    else:
        if violet_env == "test":
            db_name = os.getenv("POSTGRES_DB", "").strip()
            if not db_name or db_name.lower() in FORBIDDEN_TEST_DB_NAMES:
                raise ImportGateError(
                    "VIOLET_ENV=test requires TEST_DATABASE_URL or a test-specific POSTGRES_DB."
                )
        else:
            db_name = (
                database_settings.get("name")
                or os.getenv("POSTGRES_DB")
                or "blombooru"
            )

        database_url = URL.create(
            drivername="postgresql",
            username=database_settings.get("user") or os.getenv("POSTGRES_USER") or "postgres",
            password=database_settings.get("password") or os.getenv("POSTGRES_PASSWORD") or "",
            host=database_settings.get("host") or os.getenv("POSTGRES_HOST") or "db",
            port=int(database_settings.get("port") or os.getenv("POSTGRES_PORT") or 5432),
            database=db_name,
        )

    if violet_env == "test" and db_name.lower() in FORBIDDEN_TEST_DB_NAMES:
        raise ImportGateError(f"Refusing test environment with production-like DB {db_name!r}")

    disabled_flags = {
        name: not _truthy_falsey(name, default)
        for name, default in DISABLED_FLAG_DEFAULTS.items()
    }

    return RuntimeContext(
        repo_root=repo_root,
        violet_env=violet_env,
        storage_root=storage_root,
        original_dir=original_dir,
        thumbnail_dir=thumbnail_dir,
        database_url=database_url,
        safe_database_url=_safe_url(database_url),
        db_name=db_name,
        disabled_flags=disabled_flags,
    )


def create_db_engine(context: RuntimeContext) -> Engine:
    return create_engine(
        context.database_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 10,
            "options": "-c statement_timeout=300000",
        },
    )


def _is_placeholder(row: dict[str, str]) -> bool:
    raw = (row.get("placeholder_flag") or "").strip().lower()
    return raw in {"1", "true", "yes", "placeholder"}


def _is_copy_row(row: dict[str, str]) -> bool:
    reason = (row.get("selection_reason") or "").strip()
    if reason not in DEFAULT_COPY_REASONS:
        return False
    if (row.get("exclusion_reason") or "").strip():
        return False
    if _is_placeholder(row):
        return False
    return bool((row.get("proposed_target_path") or "").strip())


def read_manifest(manifest_path: Path) -> tuple[list[ManifestCandidate], ManifestStats]:
    if not manifest_path.exists():
        raise ImportGateError(f"Manifest not found: {manifest_path}")

    required = {
        "row_id",
        "source_path",
        "proposed_target_path",
        "extension",
        "size_bytes",
        "selection_reason",
        "exclusion_reason",
        "placeholder_flag",
    }
    candidates: list[ManifestCandidate] = []
    stats = ManifestStats()
    seen_targets: set[str] = set()
    seen_sources: set[str] = set()

    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ImportGateError(f"Manifest missing required columns: {sorted(missing)}")

        for row_number, row in enumerate(reader, start=2):
            stats.total_rows += 1
            source_path = (row.get("source_path") or "").strip()
            proposed_target_path = (row.get("proposed_target_path") or "").strip()
            if source_path:
                if source_path in seen_sources:
                    stats.duplicate_source_rows += 1
                seen_sources.add(source_path)
            if proposed_target_path:
                if proposed_target_path in seen_targets:
                    stats.duplicate_target_rows += 1
                seen_targets.add(proposed_target_path)

            if not _is_copy_row(row):
                continue

            size_raw = (row.get("size_bytes") or "").strip()
            try:
                size_bytes = int(size_raw)
            except ValueError as exc:
                raise ImportGateError(f"Invalid size_bytes at manifest row {row_number}: {size_raw!r}") from exc

            candidates.append(
                ManifestCandidate(
                    row_number=row_number,
                    row_id=(row.get("row_id") or "").strip(),
                    source_path=source_path,
                    proposed_target_path=proposed_target_path,
                    extension=(row.get("extension") or "").strip().lower(),
                    size_bytes=size_bytes,
                    selection_reason=(row.get("selection_reason") or "").strip(),
                )
            )

    stats.copy_rows = len(candidates)
    return candidates, stats


def validate_audit_summary(audit_summary_path: Path, expected_copy_count: int) -> dict[str, Any]:
    if not audit_summary_path.exists():
        raise ImportGateError(f"Audit summary not found: {audit_summary_path}")
    data = _read_json_file(audit_summary_path)
    checks = {
        "result": "PASS",
        "expected_copy_count": expected_copy_count,
        "copy_rows": expected_copy_count,
        "target_pass": expected_copy_count,
        "copy_count_matches_expected": True,
    }
    failures = []
    for key, expected in checks.items():
        actual = data.get(key)
        if actual != expected:
            failures.append(f"{key}={actual!r} expected {expected!r}")
    if failures:
        raise ImportGateError("Phase 3.4 audit summary gate failed: " + "; ".join(failures))
    return data


def _resolve_target_path(raw_path: str, target_root: Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute() and not PureWindowsPath(raw_path).is_absolute():
        path = target_root / raw_path
    return path.resolve()


def _relative_posix(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _ensure_under(child: Path, parent: Path, label: str) -> None:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise ImportGateError(f"{label} is outside allowed root: {child}") from exc


def _ensure_storage_relative(path: Path, storage_root: Path) -> str:
    _ensure_under(path, storage_root, "Managed storage path")
    return _relative_posix(path, storage_root)


def validate_roots(context: RuntimeContext, target_root: Path, execute: bool) -> None:
    target_root = target_root.resolve()
    if not target_root.exists() or not target_root.is_dir():
        raise ImportGateError(f"Target root does not exist or is not a directory: {target_root}")
    if not context.storage_root.exists() or not context.storage_root.is_dir():
        raise ImportGateError(f"Storage root does not exist or is not a directory: {context.storage_root}")
    if not context.original_dir.exists() or not context.thumbnail_dir.exists():
        if execute:
            context.original_dir.mkdir(parents=True, exist_ok=True)
            context.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise ImportGateError(
                "Dry-run refuses to create storage directories; original/thumbnail dirs must already exist."
            )

    try:
        target_root.relative_to(context.storage_root)
        raise ImportGateError("Target root is inside app-managed storage; refusing import.")
    except ValueError:
        pass

    try:
        context.storage_root.relative_to(target_root)
        raise ImportGateError("App-managed storage is inside target root; refusing import.")
    except ValueError:
        pass

    if any(not disabled for disabled in context.disabled_flags.values()):
        enabled = [name for name, disabled in context.disabled_flags.items() if not disabled]
        raise ImportGateError(
            "Phase 3.5 requires AI/LLM/classification/localization/entity flags disabled: "
            + ", ".join(enabled)
        )


def validate_candidates(
    candidates: list[ManifestCandidate],
    target_root: Path,
) -> tuple[list[ManifestCandidate], list[ManifestCandidate], int]:
    valid: list[ManifestCandidate] = []
    invalid: list[ManifestCandidate] = []
    total_bytes = 0
    target_root = target_root.resolve()

    for candidate in candidates:
        try:
            staged_path = _resolve_target_path(candidate.proposed_target_path, target_root)
            _ensure_under(staged_path, target_root, "Staged target path")
            candidate.staged_path = staged_path
            candidate.staged_label = _relative_posix(staged_path, target_root)

            if not staged_path.exists() or not staged_path.is_file():
                raise ImportGateError(f"missing staged file: {candidate.staged_label}")
            stat = staged_path.stat()
            if stat.st_size != candidate.size_bytes:
                raise ImportGateError(
                    f"size mismatch for {candidate.staged_label}: {stat.st_size} != {candidate.size_bytes}"
                )
            if candidate.extension not in SUPPORTED_EXTENSIONS:
                raise ImportGateError(f"unsupported extension {candidate.extension!r}")
            if staged_path.suffix.lower() != candidate.extension:
                raise ImportGateError(
                    f"extension mismatch for {candidate.staged_label}: {staged_path.suffix.lower()} != {candidate.extension}"
                )
            candidate.file_hash = calculate_file_hash(staged_path)
            total_bytes += stat.st_size
            valid.append(candidate)
        except Exception as exc:
            candidate.invalid_reason = str(exc)
            invalid.append(candidate)

    return valid, invalid, total_bytes


def get_media_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM blombooru_media")).scalar_one())


def get_existing_media_by_hash(engine: Engine, hashes: Iterable[str]) -> dict[str, dict[str, Any]]:
    existing: dict[str, dict[str, Any]] = {}
    unique_hashes = sorted({h for h in hashes if h})
    with engine.connect() as conn:
        for file_hash in unique_hashes:
            row = conn.execute(
                text(
                    "SELECT id, filename, path "
                    "FROM blombooru_media WHERE hash = :hash LIMIT 1"
                ),
                {"hash": file_hash},
            ).mappings().first()
            if row:
                existing[file_hash] = dict(row)
    return existing


def directory_stats(path: Path) -> dict[str, int | bool]:
    if not path.exists():
        return {"exists": False, "file_count": 0, "bytes": 0}
    file_count = 0
    byte_count = 0
    for item in path.rglob("*"):
        if item.is_file():
            file_count += 1
            byte_count += item.stat().st_size
    return {"exists": True, "file_count": file_count, "bytes": byte_count}


def build_import_items(
    candidates: list[ManifestCandidate],
    invalid: list[ManifestCandidate],
    existing_by_hash: dict[str, dict[str, Any]],
) -> list[ImportItem]:
    items: list[ImportItem] = []
    seen_manifest_hashes: dict[str, ManifestCandidate] = {}
    for candidate in invalid:
        items.append(ImportItem(candidate=candidate, status="invalid", message=candidate.invalid_reason or "invalid"))
    for candidate in candidates:
        file_hash = candidate.file_hash or ""
        existing = existing_by_hash.get(file_hash)
        if existing:
            items.append(
                ImportItem(
                    candidate=candidate,
                    status="duplicate_by_hash",
                    duplicate_media_id=int(existing["id"]),
                    duplicate_media_path=str(existing["path"]),
                    message="existing DB media has same hash",
                )
            )
        elif file_hash in seen_manifest_hashes:
            first = seen_manifest_hashes[file_hash]
            items.append(
                ImportItem(
                    candidate=candidate,
                    status="duplicate_by_hash",
                    duplicate_media_path=f"manifest:{first.row_id}",
                    message=f"duplicate hash within manifest; first row_id={first.row_id}",
                )
            )
        else:
            seen_manifest_hashes[file_hash] = candidate
            items.append(ImportItem(candidate=candidate, status="would_create"))
    return items


def _item_counts(items: Iterable[ImportItem], dry_run: bool) -> dict[str, int]:
    counts = {
        "invalid": 0,
        "duplicates_by_hash": 0,
        "would_create": 0,
        "imported": 0,
        "failed": 0,
        "skipped": 0,
        "manifest_internal_hash_duplicates": 0,
    }
    for item in items:
        if item.status == "invalid":
            counts["invalid"] += 1
        elif item.status == "duplicate_by_hash":
            counts["duplicates_by_hash"] += 1
            if item.message.startswith("duplicate hash within manifest"):
                counts["manifest_internal_hash_duplicates"] += 1
        elif item.status == "would_create":
            counts["would_create"] += 1
        elif item.status == "imported":
            counts["imported"] += 1
        elif item.status == "failed":
            counts["failed"] += 1
        elif item.status.startswith("skipped"):
            counts["skipped"] += 1
    if not dry_run:
        counts["would_create"] = 0
    return counts


def _cleanup_created(paths: Iterable[Path | None]) -> None:
    for path in paths:
        if path and path.exists() and path.is_file():
            path.unlink()


def execute_import_items(
    items: list[ImportItem],
    context: RuntimeContext,
    engine: Engine,
) -> list[ImportItem]:
    for item in items:
        if item.status == "invalid":
            return items
        if item.status == "duplicate_by_hash":
            continue
        if item.status != "would_create":
            continue

        candidate = item.candidate
        assert candidate.staged_path is not None
        assert candidate.file_hash is not None

        copied_path: Path | None = None
        thumbnail_path: Path | None = None
        try:
            unique_filename = get_unique_filename(context.original_dir, candidate.staged_path.name)
            copied_path = context.original_dir / unique_filename
            shutil.copy2(candidate.staged_path, copied_path)

            metadata = process_media_file(copied_path)
            if metadata["hash"] != candidate.file_hash:
                raise RuntimeError("Copied file hash does not match staged file hash")

            thumb_filename = get_unique_filename(context.thumbnail_dir, f"{copied_path.stem}.jpg")
            thumbnail_path = context.thumbnail_dir / thumb_filename
            thumb_ok = generate_thumbnail(copied_path, thumbnail_path, metadata["file_type"])
            if not thumb_ok:
                thumbnail_path = None

            managed_path = _ensure_storage_relative(copied_path, context.storage_root)
            managed_thumbnail = (
                _ensure_storage_relative(thumbnail_path, context.storage_root)
                if thumbnail_path is not None
                else None
            )
            file_type = metadata["file_type"]
            if isinstance(file_type, FileTypeEnum):
                file_type_value = file_type.value
            else:
                file_type_value = str(file_type)

            with engine.begin() as conn:
                existing = conn.execute(
                    text(
                        "SELECT id, path FROM blombooru_media "
                        "WHERE hash = :hash LIMIT 1"
                    ),
                    {"hash": candidate.file_hash},
                ).mappings().first()
                if existing:
                    item.status = "duplicate_by_hash"
                    item.duplicate_media_id = int(existing["id"])
                    item.duplicate_media_path = str(existing["path"])
                    item.message = "duplicate appeared before insert"
                    _cleanup_created([copied_path, thumbnail_path])
                    continue

                result = conn.execute(
                    text(
                        "INSERT INTO blombooru_media "
                        "(filename, path, thumbnail_path, hash, file_type, mime_type, "
                        "file_size, width, height, duration, rating, is_shared, "
                        "share_ai_metadata, content_class_locked, content_class_reviewed, source) "
                        "VALUES (:filename, :path, :thumbnail_path, :hash, :file_type, :mime_type, "
                        ":file_size, :width, :height, :duration, :rating, :is_shared, "
                        ":share_ai_metadata, :content_class_locked, :content_class_reviewed, :source) "
                        "RETURNING id"
                    ),
                    {
                        "filename": copied_path.name,
                        "path": managed_path,
                        "thumbnail_path": managed_thumbnail,
                        "hash": metadata["hash"],
                        "file_type": file_type_value,
                        "mime_type": metadata.get("mime_type"),
                        "file_size": metadata.get("file_size"),
                        "width": metadata.get("width"),
                        "height": metadata.get("height"),
                        "duration": metadata.get("duration"),
                        "rating": RatingEnum.safe.value,
                        "is_shared": False,
                        "share_ai_metadata": False,
                        "content_class_locked": False,
                        "content_class_reviewed": False,
                        "source": IMPORT_SOURCE_LABEL,
                    },
                )
                media_id = int(result.scalar_one())

            item.status = "imported"
            item.media_id = media_id
            item.managed_path = managed_path
            item.thumbnail_path = managed_thumbnail
            item.message = "created media row"
        except Exception as exc:
            _cleanup_created([copied_path, thumbnail_path])
            item.status = "failed"
            item.message = str(exc)
            return items

    return items


def post_import_audit(items: list[ImportItem], context: RuntimeContext, engine: Engine) -> dict[str, Any]:
    imported_ids = [item.media_id for item in items if item.status == "imported" and item.media_id]
    if not imported_ids:
        return {
            "imported_ids_checked": 0,
            "db_rows_found": 0,
            "original_files_found": 0,
            "thumbnails_found": 0,
            "missing": [],
            "source_label_mismatches": 0,
        }

    rows: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for media_id in imported_ids:
            row = conn.execute(
                text(
                    "SELECT id, path, thumbnail_path, hash, source "
                    "FROM blombooru_media WHERE id = :id"
                ),
                {"id": media_id},
            ).mappings().first()
            if row:
                rows.append(dict(row))

    original_files_found = 0
    thumbnails_found = 0
    source_label_mismatches = 0
    missing: list[dict[str, Any]] = []
    for row in rows:
        original_path = (context.storage_root / str(row["path"])).resolve()
        try:
            _ensure_under(original_path, context.storage_root, "Post-audit original path")
        except ImportGateError:
            missing.append({"id": row["id"], "kind": "path_outside_storage"})
            continue
        if original_path.exists():
            original_files_found += 1
        else:
            missing.append({"id": row["id"], "kind": "original_missing"})

        thumbnail_value = row.get("thumbnail_path")
        if thumbnail_value:
            thumb_path = (context.storage_root / str(thumbnail_value)).resolve()
            try:
                _ensure_under(thumb_path, context.storage_root, "Post-audit thumbnail path")
            except ImportGateError:
                missing.append({"id": row["id"], "kind": "thumbnail_path_outside_storage"})
                continue
            if thumb_path.exists():
                thumbnails_found += 1
            else:
                missing.append({"id": row["id"], "kind": "thumbnail_missing"})

        if row.get("source") != IMPORT_SOURCE_LABEL:
            source_label_mismatches += 1

    return {
        "imported_ids_checked": len(imported_ids),
        "db_rows_found": len(rows),
        "original_files_found": original_files_found,
        "thumbnails_found": thumbnails_found,
        "missing": missing[:20],
        "missing_count": len(missing),
        "source_label_mismatches": source_label_mismatches,
    }


def write_local_result_csv(path: Path, import_run_id: str, items: list[ImportItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "import_run_id",
        "row_id",
        "status",
        "media_id",
        "duplicate_media_id",
        "hash",
        "source_path",
        "staged_path",
        "managed_path",
        "thumbnail_path",
        "message",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            candidate = item.candidate
            writer.writerow(
                {
                    "import_run_id": import_run_id,
                    "row_id": candidate.row_id,
                    "status": item.status,
                    "media_id": item.media_id or "",
                    "duplicate_media_id": item.duplicate_media_id or "",
                    "hash": candidate.file_hash or "",
                    "source_path": candidate.source_path,
                    "staged_path": str(candidate.staged_path or candidate.proposed_target_path),
                    "managed_path": item.managed_path or item.duplicate_media_path or "",
                    "thumbnail_path": item.thumbnail_path or "",
                    "message": item.message,
                }
            )


def write_report(path: Path, report: ImportReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.__dict__.copy()
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def prepare_report(
    mode: str,
    expected_copy_count: int,
    audit_summary: dict[str, Any],
    manifest_stats: ManifestStats,
    context: RuntimeContext,
    target_root: Path,
) -> ImportReport:
    return ImportReport(
        import_run_id=str(uuid.uuid4()),
        mode=mode,
        started_at=utc_now(),
        expected_copy_count=expected_copy_count,
        audit_summary={
            "result": audit_summary.get("result"),
            "expected_copy_count": audit_summary.get("expected_copy_count"),
            "copy_rows": audit_summary.get("copy_rows"),
            "target_pass": audit_summary.get("target_pass"),
            "copy_count_matches_expected": audit_summary.get("copy_count_matches_expected"),
            "total_verified_bytes": audit_summary.get("total_verified_bytes"),
        },
        manifest={
            "total_rows": manifest_stats.total_rows,
            "copy_rows": manifest_stats.copy_rows,
            "duplicate_target_rows": manifest_stats.duplicate_target_rows,
            "duplicate_source_rows": manifest_stats.duplicate_source_rows,
        },
        database={
            "violet_env": context.violet_env,
            "db_name": context.db_name,
            "database_url_safe": context.safe_database_url,
        },
        storage={
            "storage_root": str(context.storage_root),
            "original_dir": str(context.original_dir),
            "thumbnail_dir": str(context.thumbnail_dir),
            "target_root": str(target_root.resolve()),
            "target_outside_storage": True,
            "media_source_label": IMPORT_SOURCE_LABEL,
        },
        gates={
            "background_systems_disabled": all(context.disabled_flags.values()),
            "disabled_flags": context.disabled_flags,
            "source_staging_mutation": False,
            "ai_llm_classification_localization_entity_resolver": "disabled",
        },
    )


def run_import(args: argparse.Namespace) -> int:
    execute = bool(args.execute)
    mode = "execute" if execute else "dry_run"

    context = build_runtime_context()
    target_root = Path(args.target_root).expanduser().resolve()
    validate_roots(context, target_root, execute=execute)
    audit_summary = validate_audit_summary(Path(args.audit_summary), args.expected_copy_count)
    candidates, manifest_stats = read_manifest(Path(args.manifest))
    if manifest_stats.copy_rows != args.expected_copy_count:
        raise ImportGateError(
            f"Manifest copy_rows={manifest_stats.copy_rows} expected {args.expected_copy_count}"
        )
    if args.limit is not None:
        candidates = candidates[: args.limit]

    report = prepare_report(mode, args.expected_copy_count, audit_summary, manifest_stats, context, target_root)
    engine = create_db_engine(context)
    pre_media_count = get_media_count(engine)
    report.media_count["before"] = pre_media_count
    report.storage_stats["before_original"] = directory_stats(context.original_dir)
    report.storage_stats["before_thumbnails"] = directory_stats(context.thumbnail_dir)

    valid, invalid, estimated_bytes = validate_candidates(candidates, target_root)
    existing_by_hash = get_existing_media_by_hash(engine, [c.file_hash or "" for c in valid])
    items = build_import_items(valid, invalid, existing_by_hash)

    if execute:
        if invalid:
            raise ImportGateError(f"Execute refuses to proceed with {len(invalid)} invalid manifest targets.")
        items = execute_import_items(items, context, engine)
        report.media_count["after"] = get_media_count(engine)
        report.storage_stats["after_original"] = directory_stats(context.original_dir)
        report.storage_stats["after_thumbnails"] = directory_stats(context.thumbnail_dir)
        report.post_import_audit = post_import_audit(items, context, engine)
    else:
        report.media_count["after"] = pre_media_count

    report.counts = _item_counts(items, dry_run=not execute)
    report.counts["target_files_checked"] = len(valid)
    report.counts["manifest_copy_rows"] = manifest_stats.copy_rows
    report.counts["estimated_bytes_to_copy"] = estimated_bytes
    report.imported_media_ids_sample = [
        item.media_id for item in items if item.media_id is not None
    ][:20]

    failures = [item for item in items if item.status == "failed"]
    if failures:
        report.errors.extend(f"{item.candidate.row_id}: {item.message}" for item in failures[:20])
    if invalid:
        report.errors.extend(f"{item.row_id}: {item.invalid_reason}" for item in invalid[:20])

    report.finished_at = utc_now()
    write_local_result_csv(Path(args.local_result_csv), report.import_run_id, items)
    write_report(Path(args.report_json), report)
    engine.dispose()

    print(json.dumps({
        "mode": mode,
        "report_json": str(Path(args.report_json)),
        "local_result_csv": str(Path(args.local_result_csv)),
        "db_name": context.db_name,
        "storage_root": str(context.storage_root),
        "counts": report.counts,
        "media_count": report.media_count,
        "post_import_audit": report.post_import_audit,
    }, ensure_ascii=False, indent=2))

    if report.errors:
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import audited Tier-1000 staged manifest into V.I.O.L.E.T. DB")
    parser.add_argument("--manifest", required=True, help="Candidate manifest CSV path")
    parser.add_argument("--target-root", required=True, help="Audited staging root")
    parser.add_argument("--audit-summary", required=True, help="Phase 3.4 audit summary JSON path")
    parser.add_argument("--expected-copy-count", required=True, type=int)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-import-tier1000", default="")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--local-result-csv", required=True)
    parser.add_argument("--limit", type=int, default=None, help="Limit copy rows for isolated tests only")
    args = parser.parse_args(argv)

    if not args.dry_run and not args.execute:
        parser.error("Specify exactly one of --dry-run or --execute")
    if args.execute and args.confirm_import_tier1000 != CONFIRM_PHRASE:
        parser.error(f"--execute requires --confirm-import-tier1000 {CONFIRM_PHRASE}")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        return run_import(args)
    except ImportGateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
