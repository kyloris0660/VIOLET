"""Dynamic library sync state and readiness service (Phase 4.7-S1).

This service records source-library update state in the database. S1 check
runs are metadata-only and never import media, copy files, call providers, run
AI tagging, or run LLM translation.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from uuid import uuid4

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    DynamicSourceItem,
    DynamicSourceRoot,
    DynamicSyncRun,
    DynamicSyncRunItem,
    Media,
    TagTranslation,
)
from ..services.job_control import build_ai_tagging_execution_profile
from ..utils.local_library_scanner import (
    SUPPORTED_EXTENSIONS,
    _calculate_file_hash_with_timeout,
    _is_cloud_only,
    _is_scannable_file,
    validate_scan_paths,
)
from ..utils.logger import logger

PROPER_NOUN_CATEGORIES = {"character", "copyright", "artist"}
GENERAL_LOCALIZATION_CATEGORIES = {"general", "meta"}

MANUAL_SYNC_FILE_STATES: tuple[str, ...] = (
    "candidate",
    "skipped_unsupported",
    "skipped_placeholder",
    "skipped_zero_byte",
    "skipped_changing",
    "skipped_path_policy_error",
    "skipped_duplicate",
    "skipped_existing_media",
    "downstream_followup_planned",
    "import_planned",
    "imported_in_test",
    "classified_in_test",
    "ai_tagged_in_test",
    "localization_scheduled_in_test",
    "failed",
)

MANUAL_SYNC_PIPELINE_STAGES: tuple[str, ...] = (
    "candidate_discovery",
    "import",
    "classification",
    "ai_tagging",
    "localization",
    "summary",
)

S3A_M1_MANUAL_EXECUTE_CONFIRMATION_PREFIX = "I APPROVE S3A-M1 MANUAL SYNC EXECUTE"
S3A_M1_PRODUCTION_EXECUTE_CONFIRMATION_PREFIX = "I APPROVE S3A-M1 PRODUCTION MANUAL SYNC EXECUTE"
S3A_M2_MANUAL_EXECUTE_CONFIRMATION_PREFIX = "I UNDERSTAND VIOLET WILL MANUALLY IMPORT CLASSIFY AI-TAG AND LOCALIZE"
S3A_M2_PRODUCTION_EXECUTE_CONFIRMATION_PREFIX = (
    "I UNDERSTAND VIOLET PRODUCTION WILL MANUALLY IMPORT CLASSIFY AI-TAG AND LOCALIZE"
)
MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS = 600
MANUAL_SYNC_PLAN_NO_PROGRESS_TIMEOUT_SECONDS = 5 * 60
MANUAL_SYNC_NORMAL_PLAN_MODE = "incremental"
MANUAL_SYNC_ADVANCED_FULL_RESCAN_MODE = "advanced_full_rescan"

MANUAL_SYNC_PUBLIC_REASON_CODES: frozenset[str] = frozenset(
    {
        "cloud_placeholder",
        "cloud_hydration_failed",
        "classification_model_uncached",
        "content_changed_after_plan",
        "corrupted_image",
        "duplicate_hash",
        "downstream_followup",
        "existing_media_hash",
        "file_still_changing",
        "hidden",
        "icloud_placeholder",
        "image_verify_failed",
        "import_failed",
        "not_a_file",
        "path_escape",
        "plan_no_progress_timeout",
        "plan_timeout",
        "plan_cancelled",
        "read_error",
        "read_timeout",
        "source_missing",
        "source_walk_error",
        "stat_error",
        "stopped_by_duration_budget",
        "stopped_by_failure_budget",
        "symlink",
        "too_large",
        "unsafe_path",
        "unsupported_extension",
        "zero_byte_file",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_json_hash(payload: Dict[str, Any]) -> str:
    return _hash_text(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))


def _normalized_path_identity(path: Path) -> str:
    resolved = path.resolve()
    normalized = os.path.normcase(str(resolved))
    return normalized.replace("\\", "/").rstrip("/")


def _normalize_relative_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _path_overlaps(left: Path, right: Path) -> bool:
    try:
        l_resolved = left.resolve()
        r_resolved = right.resolve()
    except OSError:
        return False
    try:
        l_resolved.relative_to(r_resolved)
        return True
    except ValueError:
        pass
    try:
        r_resolved.relative_to(l_resolved)
        return True
    except ValueError:
        return False


def _is_root_like(path: Path) -> bool:
    resolved = path.resolve()
    if resolved.parent == resolved:
        return True
    anchor = Path(resolved.anchor) if resolved.anchor else None
    return bool(anchor and resolved == anchor)


def validate_source_root_path(path: str | Path) -> Path:
    """Validate a source root for metadata-only sync tracking."""
    if not str(path).strip():
        raise ValueError("source root path is required")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError("source root path must be absolute")
    resolved = candidate.resolve()
    if _is_root_like(resolved):
        raise ValueError("source root must not be a filesystem root")
    if not resolved.exists():
        raise ValueError("source root does not exist")
    if not resolved.is_dir():
        raise ValueError("source root must be a directory")

    scan_error = validate_scan_paths([resolved])
    if scan_error:
        raise ValueError(scan_error)

    if _path_overlaps(resolved, settings.STORAGE_ROOT):
        raise ValueError("source root must not overlap app storage root")
    if _path_overlaps(resolved, settings.CODE_ROOT):
        raise ValueError("source root must not overlap project code root")
    return resolved


def register_source_root(
    db: Session,
    *,
    path: str | Path,
    label: Optional[str] = None,
    sync_threshold: Optional[int] = None,
    notes: Optional[str] = None,
) -> DynamicSourceRoot:
    resolved = validate_source_root_path(path)
    root_hash = _hash_text(_normalized_path_identity(resolved))
    root = (
        db.query(DynamicSourceRoot)
        .filter(DynamicSourceRoot.root_path_hash == root_hash)
        .first()
    )
    threshold = sync_threshold or settings.DYNAMIC_LIBRARY_SYNC_THRESHOLD
    if root:
        root.label = label or root.label or resolved.name
        root.root_path = str(resolved)
        root.is_active = True
        root.sync_threshold = threshold
        if notes is not None:
            root.notes = notes
    else:
        root = DynamicSourceRoot(
            label=label or resolved.name,
            root_path=str(resolved),
            root_path_hash=root_hash,
            sync_threshold=threshold,
            notes=notes,
            auto_sync_enabled=False,
        )
        db.add(root)
    db.commit()
    db.refresh(root)
    return root


def list_source_roots(db: Session, *, include_inactive: bool = False) -> List[DynamicSourceRoot]:
    query = db.query(DynamicSourceRoot)
    if not include_inactive:
        query = query.filter(DynamicSourceRoot.is_active == True)
    return query.order_by(DynamicSourceRoot.id.asc()).all()


def serialize_source_root(root: DynamicSourceRoot) -> Dict[str, Any]:
    return {
        "id": root.id,
        "label": root.label,
        "root_path": root.root_path,
        "root_path_hash": root.root_path_hash,
        "source_type": root.source_type,
        "is_active": root.is_active,
        "auto_sync_enabled": root.auto_sync_enabled,
        "sync_threshold": root.sync_threshold,
        "last_checked_at": root.last_checked_at.isoformat() if root.last_checked_at else None,
        "created_at": root.created_at.isoformat() if root.created_at else None,
        "updated_at": root.updated_at.isoformat() if root.updated_at else None,
    }


def _public_source_identity(path: Path) -> str:
    return _hash_text(_normalized_path_identity(path))[:16]


def _manual_public_reason_code(reason: Optional[str]) -> Optional[str]:
    if not reason:
        return None
    code = str(reason).split(":", 1)[0].strip().casefold().replace("-", "_")
    if code in MANUAL_SYNC_PUBLIC_REASON_CODES:
        return code
    if code.startswith("stat_error"):
        return "stat_error"
    if code.startswith("read_timeout") or code == "timeout":
        return "read_timeout"
    if code.startswith("read_error") or code.endswith("error"):
        return "read_error"
    return "read_error"


def _manual_state_for_reason(reason: str) -> str:
    reason = _manual_public_reason_code(reason) or "read_error"
    if reason in {"icloud_placeholder", "cloud_placeholder"}:
        return "skipped_placeholder"
    if reason == "existing_media_hash":
        return "skipped_existing_media"
    if reason == "duplicate_hash":
        return "skipped_duplicate"
    if reason == "downstream_followup":
        return "downstream_followup_planned"
    if reason == "zero_byte_file":
        return "skipped_zero_byte"
    if reason in {"unsupported_extension", "hidden", "too_large"}:
        return "skipped_unsupported"
    if reason in {"path_escape", "symlink", "unsafe_path", "not_a_file"}:
        return "skipped_path_policy_error"
    if reason == "file_still_changing":
        return "skipped_changing"
    if reason in {"corrupted_image", "image_verify_failed"}:
        return "failed"
    if reason in {
        "source_missing",
        "stat_error",
        "read_error",
        "read_timeout",
        "content_changed_after_plan",
        "cloud_hydration_failed",
    }:
        return "failed"
    return "skipped_unsupported"


def _public_counter(counter: Counter, keys: Iterable[str]) -> Dict[str, int]:
    return {key: int(counter.get(key, 0)) for key in keys}


def _verify_supported_image_file(path: Path) -> Optional[str]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.verify()
    except Exception:
        return "corrupted_image"
    return None


def _verify_supported_image_file_worker(path: str, conn) -> None:
    try:
        reason = _verify_supported_image_file(Path(path))
        conn.send(("ok", reason))
    except Exception:  # noqa: BLE001 - child process returns only public-safe codes.
        try:
            conn.send(("error", "corrupted_image"))
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _verify_supported_image_file_with_timeout(path: Path, timeout_sec: int) -> Optional[str]:
    timeout_sec = max(1, int(timeout_sec))
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.Process(
        target=_verify_supported_image_file_worker,
        args=(str(path), child_conn),
    )
    process.daemon = True
    try:
        process.start()
        child_conn.close()
        if parent_conn.poll(timeout_sec):
            status, payload = parent_conn.recv()
            process.join(timeout=1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if status == "ok":
                return _manual_public_reason_code(payload)
            return _manual_public_reason_code(payload) or "corrupted_image"
        process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
        return "read_timeout"
    except Exception:
        return "read_error"
    finally:
        try:
            parent_conn.close()
        except Exception:
            pass
        try:
            child_conn.close()
        except Exception:
            pass
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)


def _calculate_manual_plan_file_hash(path: Path, timeout_sec: int) -> tuple[Optional[str], Optional[str]]:
    status, payload = _calculate_file_hash_with_timeout(path, max(1, int(timeout_sec)))
    if status == "ok":
        return str(payload), None
    if status == "timeout":
        return None, "read_timeout"
    return None, "read_error"


def _query_existing_media_by_hashes(db: Session, content_hashes: Iterable[str]) -> Dict[str, int]:
    hashes = sorted({value for value in content_hashes if value})
    if not hashes:
        return {}
    existing: Dict[str, int] = {}
    chunk_size = 500
    for index in range(0, len(hashes), chunk_size):
        chunk = hashes[index : index + chunk_size]
        rows = db.query(Media.hash, Media.id).filter(Media.hash.in_(chunk)).all()
        for content_hash, media_id in rows:
            if content_hash and media_id is not None:
                existing.setdefault(str(content_hash), int(media_id))
    return existing


def _lookup_existing_media_id_by_hash(
    db: Session,
    content_hash: Optional[str],
    cache: Dict[str, Optional[int]],
) -> Optional[int]:
    if not content_hash:
        return None
    key = str(content_hash)
    if key not in cache:
        cache[key] = _query_existing_media_by_hashes(db, [key]).get(key)
    return cache[key]


def _build_manual_pipeline_stages(
    *,
    state_counts: Counter,
    ai_profile: Dict[str, Any],
    max_duration_seconds: int,
    estimated_runtime_seconds: float,
) -> List[Dict[str, Any]]:
    import_count = int(state_counts.get("import_planned", 0))
    downstream_followup_count = int(state_counts.get("downstream_followup_planned", 0))
    downstream_count = import_count + downstream_followup_count
    duration_limited = estimated_runtime_seconds > max_duration_seconds
    stage_rows = [
        {
            "name": "candidate_discovery",
            "state": "completed",
            "writes_enabled": False,
            "input_count": int(sum(state_counts.values())),
            "output_count": downstream_count,
        },
        {
            "name": "import",
            "state": "planned",
            "writes_enabled": False,
            "estimated_count": import_count,
        },
        {
            "name": "classification",
            "state": "planned",
            "writes_enabled": False,
            "estimated_count": downstream_count,
            "followup_count": downstream_followup_count,
        },
        {
            "name": "ai_tagging",
            "state": "planned",
            "writes_enabled": False,
            "estimated_count": downstream_count,
            "followup_count": downstream_followup_count,
            "profile_id": ai_profile.get("profile_id"),
            "batch_size": ai_profile.get("batch_size"),
            "concurrency": ai_profile.get("concurrency"),
        },
        {
            "name": "localization",
            "state": "handoff_planned",
            "writes_enabled": False,
            "estimated_count": downstream_count,
            "followup_count": downstream_followup_count,
            "llm_calls_enabled": False,
        },
        {
            "name": "summary",
            "state": "planned",
            "writes_enabled": False,
            "estimated_count": 1,
        },
    ]
    for row in stage_rows:
        row["dry_run_only_this_phase"] = True
        row["production_execution_enabled"] = False
    return [
        {
            **row,
            "max_duration_seconds": max_duration_seconds if row["name"] == "summary" else None,
            "duration_limited": duration_limited if row["name"] == "summary" else None,
        }
        for row in stage_rows
    ]


def _estimate_manual_sync_runtime_seconds(
    *,
    import_count: int,
    ai_profile: Dict[str, Any],
    benchmark: Optional[Dict[str, Any]],
) -> float:
    seconds_per_ai_item = None
    if benchmark:
        seconds_per_ai_item = benchmark.get("recommended_seconds_per_item")
        if seconds_per_ai_item is None:
            seconds_per_ai_item = benchmark.get("single_image_latency_seconds")
    try:
        ai_seconds = float(seconds_per_ai_item) if seconds_per_ai_item is not None else 2.0
    except (TypeError, ValueError):
        ai_seconds = 2.0
    batch_size = max(1, int(ai_profile.get("batch_size") or 1))
    ai_batches = (import_count + batch_size - 1) // batch_size
    return round(
        import_count * 0.25
        + import_count * 0.10
        + ai_batches * max(ai_seconds, 0.01)
        + import_count * 0.02,
        3,
    )


def _isoformat_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _manual_plan_root_scan_state(
    db: Session,
    *,
    source_record_id: Optional[int],
    known_items_by_rel_hash: Dict[str, DynamicSourceItem],
) -> Dict[str, Any]:
    """Public-safe root scan basis for the operator UI.

    S3A-M2 uses the existing source-item ledger as the durable source index.
    This avoids a schema migration while still keeping repeated scans from
    relying on content hashing over stable known files.
    """
    root = db.get(DynamicSourceRoot, int(source_record_id)) if source_record_id is not None else None
    latest_run = None
    if source_record_id is not None:
        latest_run = (
            db.query(DynamicSyncRun)
            .join(DynamicSyncRunItem, DynamicSyncRunItem.sync_run_id == DynamicSyncRun.id)
            .join(DynamicSourceItem, DynamicSyncRunItem.source_item_id == DynamicSourceItem.id)
            .filter(DynamicSourceItem.source_root_id == int(source_record_id))
            .filter(
                DynamicSyncRun.status.in_(
                    ("completed", "completed_with_failures", "completed_with_followup_required", "cancelled", "failed")
                )
            )
            .order_by(DynamicSyncRun.id.desc())
            .first()
        )
    last_seen_values = [
        value
        for value in (getattr(item, "last_seen_at", None) for item in known_items_by_rel_hash.values())
        if value is not None
    ]
    last_checked_values = [
        value
        for value in (getattr(item, "last_checked_at", None) for item in known_items_by_rel_hash.values())
        if value is not None
    ]
    return {
        "model": "hybrid_source_ledger_metadata_fast_path_with_filesystem_fallback",
        "durable_global_filesystem_cursor": False,
        "durable_state_tables": [
            "blombooru_dynamic_source_items",
            "blombooru_dynamic_sync_runs",
            "blombooru_dynamic_sync_run_items",
        ],
        "known_source_items": int(len(known_items_by_rel_hash)),
        "root_last_checked_at": _isoformat_or_none(getattr(root, "last_checked_at", None)),
        "last_successful_or_terminal_run_id": int(latest_run.id) if latest_run is not None else None,
        "last_successful_or_terminal_run_status": str(latest_run.status) if latest_run is not None else None,
        "last_successful_or_terminal_run_finished_at": _isoformat_or_none(
            getattr(latest_run, "finished_at", None) if latest_run is not None else None
        ),
        "latest_source_item_seen_at": _isoformat_or_none(max(last_seen_values)) if last_seen_values else None,
        "latest_source_item_checked_at": _isoformat_or_none(max(last_checked_values)) if last_checked_values else None,
        "fast_precheck_identity": ["source_root_id", "relative_path_hash", "file_size", "mtime_ns"],
        "hash_only_when": [
            "new_source_path",
            "metadata_changed",
            "downstream_followup",
            "ambiguous_existing_media",
            "execute_integrity_revalidation",
        ],
        "cache_invalidated_by": [
            "source_root_id",
            "relative_path_hash",
            "file_size",
            "mtime_ns",
            "content_hash_mismatch",
            "hydration_policy",
            "profile_or_pipeline_settings",
        ],
        "moved_deleted_renamed_handling": (
            "old source ledger rows remain historical until a check/run marks missing; "
            "a new relative path appears as new work"
        ),
        "cloud_placeholder_handling": (
            "cloud-aware plan detects placeholders and hydrates by approved non-destructive reads "
            "when hydrated_only=false"
        ),
    }


def _manual_plan_integrity_payload(
    *,
    source_record_id: Optional[int],
    source_identity_hash: str,
    limits: Dict[str, Any],
    integrity_items: List[Dict[str, Any]],
    created_at: datetime,
) -> Dict[str, Any]:
    return {
        "schema": "s3a_m1_manual_sync_plan_integrity_v1",
        "created_at": created_at.isoformat(),
        "source": {
            "source_record_id": source_record_id,
            "source_identity_hash": source_identity_hash,
        },
        "limits": {
            "max_files": int(limits.get("max_files") or 0),
            "hydrated_only": bool(limits.get("hydrated_only")),
            "stable_age_seconds": float(limits.get("stable_age_seconds") or 0.0),
            "max_duration_seconds": int(limits.get("max_duration_seconds") or 0),
            "file_read_timeout_seconds": int(limits.get("file_read_timeout_seconds") or 0),
            "plan_mode": str(limits.get("plan_mode") or ""),
            "safety_lookback_seconds": int(limits.get("safety_lookback_seconds") or 0),
            "source_mtime_watermark_ns": limits.get("source_mtime_watermark_ns"),
            "mtime_cutoff_ns": limits.get("mtime_cutoff_ns"),
        },
        "items": integrity_items,
    }


def _redact_private_sync_payload(value: Any) -> Any:
    private_keys = {"private_plan_items", "private_details", "relative_path", "content_hash"}
    if isinstance(value, dict):
        return {
            key: _redact_private_sync_payload(item)
            for key, item in value.items()
            if key not in private_keys
        }
    if isinstance(value, list):
        return [_redact_private_sync_payload(item) for item in value]
    return value


def manual_sync_execute_confirmation_phrase(plan_hash: str, *, production: bool = False) -> str:
    prefix = (
        S3A_M2_PRODUCTION_EXECUTE_CONFIRMATION_PREFIX
        if production
        else S3A_M2_MANUAL_EXECUTE_CONFIRMATION_PREFIX
    )
    return f"{prefix} {str(plan_hash)[:12]}"


def manual_sync_operator_confirmation_statement(plan: Dict[str, Any], *, production: bool = False) -> str:
    counts = plan.get("counts") or {}
    plan_hash = str((plan.get("integrity") or {}).get("plan_hash") or "")
    import_count = int(counts.get("estimated_import_count") or 0)
    followup_count = int(counts.get("estimated_downstream_followup_count") or 0)
    env = "production" if production else "test"
    return (
        f"I confirm VIOLET {env} manual sync will process "
        f"{import_count} imports and {followup_count} follow-up items for plan {plan_hash[:12]}"
    )


def _manual_plan_source_mtime_watermark_ns(known_items: Iterable[DynamicSourceItem]) -> Optional[int]:
    values: List[int] = []
    for item in known_items:
        mtime_ns = getattr(item, "mtime_ns", None)
        if mtime_ns is None:
            continue
        import_status = str(getattr(item, "import_status", "") or "")
        sync_state = str(getattr(item, "sync_state", "") or "")
        if import_status == "imported" and sync_state in {
            "imported",
            "skipped_existing_media",
            "skipped_duplicate",
            "unchanged",
        }:
            values.append(int(mtime_ns))
    return max(values) if values else None


def _manual_plan_mtime_cutoff_ns(watermark_ns: Optional[int], lookback_seconds: int) -> Optional[int]:
    if watermark_ns is None:
        return None
    return max(0, int(watermark_ns) - int(max(0, lookback_seconds)) * 1_000_000_000)


def _manual_plan_is_extension_candidate(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix == ".icloud":
        return "icloud_placeholder"
    if suffix not in SUPPORTED_EXTENSIONS:
        return "unsupported_extension"
    return None


def _plan_manual_sync_incremental_dry_run(
    db: Session,
    *,
    source_path: str | Path,
    source_record_id: Optional[int],
    max_files: Optional[int],
    hydrated_only: bool,
    stable_age_seconds: Optional[float],
    include_private_details: bool,
    ai_profile: Optional[Dict[str, Any]],
    benchmark: Optional[Dict[str, Any]],
    now: Optional[datetime],
    progress_callback: Optional[Callable[[Dict[str, Any]], None]],
    cancel_check: Optional[Callable[[], bool]],
) -> Dict[str, Any]:
    """Build the normal manual-sync plan as cheap candidate discovery only.

    The normal operator flow must not open/decode files, hydrate cloud files, or
    compute hashes. Execute performs import-time validation and records per-item
    failures for unsupported/corrupt/duplicate/cloud-placeholder outcomes.
    """

    resolved = validate_source_root_path(source_path)
    created_at = now or _utcnow()
    created_ts = created_at.timestamp()
    effective_max_files = max(1, int(max_files or settings.DYNAMIC_LIBRARY_MANUAL_SYNC_PLAN_MAX_FILES))
    effective_stable_age = (
        settings.DYNAMIC_LIBRARY_MANUAL_SYNC_STABLE_AGE_SECONDS
        if stable_age_seconds is None
        else max(0.0, float(stable_age_seconds))
    )
    safety_lookback_seconds = int(settings.DYNAMIC_LIBRARY_MANUAL_SYNC_SAFETY_LOOKBACK_SECONDS)
    profile = ai_profile or build_ai_tagging_execution_profile(settings).to_public_dict()
    max_duration_seconds = settings.DYNAMIC_LIBRARY_MANUAL_SYNC_MAX_DURATION_SECONDS

    state_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    unsupported_extension_counts: Counter = Counter()
    reason_extension_counts: Dict[str, Counter] = {}
    public_items: List[Dict[str, Any]] = []
    private_items: List[Dict[str, Any]] = []
    candidate_records: List[Dict[str, Any]] = []
    integrity_items: List[Dict[str, Any]] = []
    walk_errors: List[str] = []

    metadata_entries_seen = 0
    db_followup_candidates = 0
    mtime_new_candidates = 0
    safety_window_candidates = 0
    stable_old_files_skipped = 0
    unchanged_ledger_skips = 0
    stat_required_count = 0
    hash_required_count = 0
    content_read_count = 0
    image_decode_count = 0
    hydration_attempt_count = 0
    current_stage = "initializing"
    last_progress_item_label: Optional[str] = None
    plan_started_monotonic = time.monotonic()
    last_progress_monotonic = plan_started_monotonic
    plan_no_progress_timeout_seconds = MANUAL_SYNC_PLAN_NO_PROGRESS_TIMEOUT_SECONDS
    partial_scan = False
    partial_scan_reason: Optional[str] = None
    plan_cancelled = False
    plan_no_progress_timeout = False
    plan_cap_limited = False
    filesystem_walk_completed = False

    def _record_reason_extension(reason_code: Optional[str], rel_path: str) -> None:
        if not reason_code:
            return
        extension = Path(str(rel_path or "")).suffix.lower() or "<none>"
        reason_extension_counts.setdefault(reason_code, Counter())[extension] += 1
        if reason_code == "unsupported_extension":
            unsupported_extension_counts[extension] += 1

    def _progress(phase: str, **updates: Any) -> None:
        nonlocal current_stage, last_progress_item_label, last_progress_monotonic
        current_stage = phase
        last_progress_monotonic = time.monotonic()
        if "current_item_label" in updates and updates["current_item_label"]:
            last_progress_item_label = str(updates["current_item_label"])
        if progress_callback is None:
            return
        counts = {
            "seen": int(metadata_entries_seen),
            "metadata_entries_seen": int(metadata_entries_seen),
            "db_followup_candidates": int(db_followup_candidates),
            "mtime_new_candidates": int(mtime_new_candidates),
            "safety_window_candidates": int(safety_window_candidates),
            "stable_old_files_skipped": int(stable_old_files_skipped),
            "skipped_historical": int(unchanged_ledger_skips),
            "skipped_unsupported": int(reason_counts.get("unsupported_extension", 0)),
            "placeholders_found": int(reason_counts.get("icloud_placeholder", 0)),
            "hydrated": 0,
            "importable": int(state_counts.get("import_planned", 0)),
            "planned": int(len(candidate_records)),
            "batch_candidates": int(len(candidate_records)),
            "failed": int(reason_counts.get("stat_error", 0) + reason_counts.get("source_walk_error", 0)),
            "content_reads": 0,
            "hashes": 0,
            "decodes": 0,
            "hydrations": 0,
        }
        payload = {
            "phase": phase,
            "status": "running",
            "current_item_index": int(metadata_entries_seen),
            "current_item_label": last_progress_item_label,
            "counts": counts,
            "elapsed_seconds": round(max(0.0, time.monotonic() - plan_started_monotonic), 3),
            "last_progress_at": _utcnow().isoformat(),
        }
        payload.update(updates)
        progress_callback(payload)

    def _cancel_requested() -> bool:
        if cancel_check is None:
            return False
        try:
            return bool(cancel_check())
        except Exception:
            return False

    def _no_progress_timed_out() -> bool:
        return (time.monotonic() - last_progress_monotonic) > plan_no_progress_timeout_seconds

    _progress("loading_source_ledger")
    known_items_by_rel_hash: Dict[str, DynamicSourceItem] = {}
    if source_record_id is not None:
        known_items_by_rel_hash = {
            item.relative_path_hash: item
            for item in db.query(DynamicSourceItem)
            .filter(DynamicSourceItem.source_root_id == int(source_record_id))
            .all()
        }

    source_mtime_watermark_ns = _manual_plan_source_mtime_watermark_ns(known_items_by_rel_hash.values())
    mtime_cutoff_ns = _manual_plan_mtime_cutoff_ns(source_mtime_watermark_ns, safety_lookback_seconds)
    priority_source_files = (
        _manual_plan_priority_source_files(resolved, known_items_by_rel_hash)
        if source_record_id is not None
        else []
    )
    priority_workset_files = len(priority_source_files)
    priority_workset_processed = 0
    priority_workset_exhausted = False
    filesystem_walk_after_priority_workset = False
    priority_workset_mode = (
        "source_ledger_followup_then_incremental_mtime_filesystem_metadata_walk"
        if priority_source_files
        else "incremental_mtime_filesystem_metadata_walk"
    )
    processed_path_identities: set[str] = set()

    def _candidate_files() -> Iterable[tuple[Path, str]]:
        nonlocal filesystem_walk_after_priority_workset, filesystem_walk_completed
        for priority_path in priority_source_files:
            path_identity = _normalized_path_identity(priority_path)
            processed_path_identities.add(path_identity)
            yield priority_path, "source_ledger_followup"
        if priority_source_files:
            filesystem_walk_after_priority_workset = True
        _progress("filesystem_metadata_walk")
        for walked_path in _iter_source_files(resolved, walk_errors=walk_errors):
            path_identity = _normalized_path_identity(walked_path)
            if path_identity in processed_path_identities:
                continue
            processed_path_identities.add(path_identity)
            yield walked_path, "mtime_window_metadata"
        filesystem_walk_completed = True

    for index, (file_path, scan_source) in enumerate(_candidate_files(), start=1):
        if _cancel_requested():
            partial_scan = True
            plan_cancelled = True
            partial_scan_reason = "cancelled"
            reason_counts["plan_cancelled"] += 1
            break
        if _no_progress_timed_out():
            partial_scan = True
            plan_no_progress_timeout = True
            partial_scan_reason = "no_progress_timeout"
            reason_counts["plan_no_progress_timeout"] += 1
            break

        metadata_entries_seen = index
        if scan_source == "source_ledger_followup":
            priority_workset_processed += 1
        safe_label = f"file-{index:05d}"
        _progress("metadata_candidate_scan", current_item_index=index, current_item_label=safe_label, scan_source=scan_source)

        rel, preflight_reason = _relative_identity_and_preflight_reason(resolved, file_path)
        rel_hash_full = _hash_text(rel)
        rel_hash = rel_hash_full[:16]
        known_item = known_items_by_rel_hash.get(rel_hash_full)
        reason = _manual_public_reason_code(preflight_reason)
        metadata: Dict[str, Any] = {}
        try:
            metadata = _metadata_for_path(file_path, follow_symlinks=not bool(preflight_reason))
            stat_required_count += 1
        except OSError:
            reason = "stat_error"

        if reason is None and effective_stable_age > 0:
            mtime = metadata.get("mtime")
            if mtime is not None and created_ts - float(mtime) < effective_stable_age:
                reason = "file_still_changing"

        if reason is None:
            reason = _manual_plan_is_extension_candidate(file_path)

        followup_payload: Dict[str, Any] = {}
        media_id: Optional[int] = None
        if known_item is not None and _manual_plan_existing_requires_followup(known_item):
            followup_payload = _manual_plan_followup_payload(known_item)
            if known_item.media_id is not None and str(known_item.import_status or "") == "imported":
                media_id = int(known_item.media_id)
                reason = "downstream_followup"
            else:
                reason = reason

        mtime_ns = metadata.get("mtime_ns")
        in_mtime_window = bool(mtime_cutoff_ns is None or (mtime_ns is not None and int(mtime_ns) >= int(mtime_cutoff_ns)))
        if (
            scan_source == "mtime_window_metadata"
            and known_item is not None
            and not in_mtime_window
            and not _manual_plan_existing_requires_followup(known_item)
            and _manual_plan_can_skip_unchanged_known_item(known_item, metadata)
        ):
            stable_old_files_skipped += 1
            continue

        if known_item is not None and _manual_plan_can_skip_unchanged_known_item(known_item, metadata):
            unchanged_ledger_skips += 1
            continue

        if reason in {"unsupported_extension", "icloud_placeholder", "file_still_changing"} and scan_source == "mtime_window_metadata":
            state = _manual_state_for_reason(reason)
            state_counts[state] += 1
            reason_counts[reason] += 1
            _record_reason_extension(reason, rel)
            continue
        if reason is not None and reason != "downstream_followup":
            state = _manual_state_for_reason(reason)
            state_counts[state] += 1
            reason_counts[reason] += 1
            _record_reason_extension(reason, rel)
            continue

        if len(candidate_records) >= effective_max_files:
            partial_scan = True
            plan_cap_limited = True
            partial_scan_reason = "cap_limited_actionable_batch"
            break

        if reason == "downstream_followup" and media_id:
            state = "downstream_followup_planned"
            db_followup_candidates += 1
        else:
            state = "import_planned"
            if source_mtime_watermark_ns is None or (mtime_ns is not None and int(mtime_ns) > int(source_mtime_watermark_ns)):
                mtime_new_candidates += 1
            elif in_mtime_window:
                safety_window_candidates += 1
        record = {
            "safe_label": safe_label,
            "relative_path": rel,
            "relative_path_hash": rel_hash,
            "relative_path_hash_full": rel_hash_full,
            "metadata": metadata,
            "reason": "downstream_followup" if state == "downstream_followup_planned" else None,
            "content_hash": None,
            "media_id": media_id,
            "cloud_placeholder_before_hydration": False,
            "followup": followup_payload,
            "scan_source": scan_source,
            "state": state,
        }
        candidate_records.append(record)
        _progress("candidate_selected", current_item_index=index, current_item_label=safe_label, scan_source=scan_source)

    if priority_source_files and priority_workset_processed >= priority_workset_files:
        priority_workset_exhausted = True
    if not filesystem_walk_completed and not (plan_cancelled or plan_no_progress_timeout):
        partial_scan = True
        partial_scan_reason = partial_scan_reason or (
            "cap_limited_actionable_batch" if plan_cap_limited else "filesystem_metadata_walk_incomplete"
        )

    _progress(
        "cancelled"
        if plan_cancelled
        else ("no_progress_timeout" if plan_no_progress_timeout else "planning")
    )

    for record in candidate_records:
        state = str(record.get("state") or "import_planned")
        public_reason = _manual_public_reason_code(record.get("reason"))
        state_counts[state] += 1
        if public_reason:
            reason_counts[public_reason] += 1
            _record_reason_extension(public_reason, str(record["relative_path"] or ""))
        metadata = record["metadata"]
        item = {
            "safe_label": record["safe_label"],
            "relative_path_hash": record["relative_path_hash"],
            "initial_state": "candidate",
            "state": state,
            "reason": public_reason,
            "eligible_for_db_import": state == "import_planned",
            "bytes_copied": 0,
            "media_id": record.get("media_id"),
            "file_size": metadata.get("file_size"),
            "content_hash_computed": False,
            "cloud_placeholder_before_hydration": False,
            "scan_source": str(record.get("scan_source") or ""),
        }
        if state == "downstream_followup_planned":
            item["downstream_followup"] = dict(record.get("followup") or {})
        public_items.append(item)
        integrity_items.append(
            {
                "safe_label": record["safe_label"],
                "relative_path_hash": record["relative_path_hash_full"],
                "file_size": metadata.get("file_size"),
                "mtime_ns": metadata.get("mtime_ns"),
                "state": state,
                "reason": public_reason,
                "content_hash": None,
                "cloud_placeholder_before_hydration": False,
                "scan_source": str(record.get("scan_source") or ""),
                "downstream_followup": dict(record.get("followup") or {}),
            }
        )
        if include_private_details:
            private_items.append(
                {
                    **item,
                    "relative_path": record["relative_path"],
                    "content_hash": None,
                    "mtime_ns": metadata.get("mtime_ns"),
                    "cloud_placeholder_before_hydration": False,
                    "downstream_followup": dict(record.get("followup") or {}),
                }
            )

    if walk_errors:
        partial_scan = True
        partial_scan_reason = "source_walk_error"
        reason_counts["source_walk_error"] += len(walk_errors)

    import_count = int(state_counts.get("import_planned", 0))
    downstream_followup_count = int(state_counts.get("downstream_followup_planned", 0))
    downstream_stage_count = import_count + downstream_followup_count
    unsafe_partial_scan = bool(
        plan_cancelled
        or plan_no_progress_timeout
        or walk_errors
        or (partial_scan and partial_scan_reason not in {None, "cap_limited_actionable_batch"})
    )
    cap_limited_batch = bool(partial_scan and partial_scan_reason == "cap_limited_actionable_batch")
    batch_executable = bool(downstream_stage_count > 0 and not unsafe_partial_scan)
    more_batches_remain = bool(cap_limited_batch)
    estimated_runtime_seconds = _estimate_manual_sync_runtime_seconds(
        import_count=import_count,
        ai_profile=profile,
        benchmark=benchmark,
    )
    stages = _build_manual_pipeline_stages(
        state_counts=state_counts,
        ai_profile=profile,
        max_duration_seconds=max_duration_seconds,
        estimated_runtime_seconds=estimated_runtime_seconds,
    )
    state_counts_public = _public_counter(state_counts, MANUAL_SYNC_FILE_STATES)
    reason_counts_public = dict(sorted((key, int(value)) for key, value in reason_counts.items()))
    source_identity_hash = _public_source_identity(resolved)
    root_scan_state = _manual_plan_root_scan_state(
        db,
        source_record_id=source_record_id,
        known_items_by_rel_hash=known_items_by_rel_hash,
    )
    elapsed_seconds = round(max(0.0, time.monotonic() - plan_started_monotonic), 3)
    avg_ms_per_candidate = (
        round((elapsed_seconds * 1000.0) / max(1, metadata_entries_seen), 3)
        if metadata_entries_seen
        else 0.0
    )
    limits = {
        "max_files": effective_max_files,
        "hydrated_only": hydrated_only,
        "hydration_policy": "cloud_aware_non_destructive_read_execute_stage",
        "plan_mode": MANUAL_SYNC_NORMAL_PLAN_MODE,
        "normal_plan_candidate_discovery_only": True,
        "advanced_full_rescan_available": True,
        "stable_age_seconds": effective_stable_age,
        "max_duration_seconds": max_duration_seconds,
        "file_read_timeout_seconds": None,
        "scanned_files": metadata_entries_seen,
        "metadata_entries_seen": metadata_entries_seen,
        "stat_required_count": stat_required_count,
        "hash_required_count": hash_required_count,
        "content_read_count": content_read_count,
        "image_decode_count": image_decode_count,
        "hydration_attempt_count": hydration_attempt_count,
        "expensive_plan_operations": {
            "content_reads": content_read_count,
            "hashes": hash_required_count,
            "decodes": image_decode_count,
            "hydrations": hydration_attempt_count,
        },
        "source_mtime_watermark_ns": source_mtime_watermark_ns,
        "safety_lookback_seconds": safety_lookback_seconds,
        "mtime_cutoff_ns": mtime_cutoff_ns,
        "db_followup_candidates": db_followup_candidates,
        "mtime_new_candidates": mtime_new_candidates,
        "safety_window_candidates": safety_window_candidates,
        "stable_old_files_skipped": stable_old_files_skipped,
        "unchanged_known_files": unchanged_ledger_skips,
        "fast_skipped_from_ledger": unchanged_ledger_skips,
        "skipped_existing_before_cap": 0,
        "skipped_duplicate_before_cap": 0,
        "import_candidate_count": import_count,
        "actionable_candidate_count": downstream_stage_count,
        "downstream_followup_count": downstream_followup_count,
        "max_files_scope": "manual_sync_actionable_delta_candidates",
        "cap_semantics": "unique_importable_or_downstream_followup_candidates_not_unchanged_or_existing_media",
        "batch_mode": "bounded_actionable_batch",
        "batch_candidate_cap": effective_max_files,
        "batch_executable": batch_executable,
        "cap_limited_batch": cap_limited_batch,
        "more_batches_remain": more_batches_remain,
        "unsafe_partial_scan": unsafe_partial_scan,
        "partial_scan_reason": partial_scan_reason,
        "plan_elapsed_seconds": elapsed_seconds,
        "average_ms_per_metadata_candidate": avg_ms_per_candidate,
        "performance_gate": {
            "content_reads_during_plan": 0,
            "hashes_during_plan": 0,
            "decodes_during_plan": 0,
            "hydrations_during_plan": 0,
            "warn_if_average_ms_per_candidate_gt": 100,
            "blocker_if_under_1000_candidates_gt_seconds": 30,
        },
        "continuation": {
            "more_batches_remain": more_batches_remain,
            "next_batch_requires_new_plan": True,
            "next_batch_start_basis": (
                "same_watermark_with_executed_items_committed_to_source_ledger"
                if more_batches_remain
                else "root_exhausted_or_no_continuation_required"
            ),
            "current_batch_actionable_count": downstream_stage_count,
            "cap_limited_after_actionable_count": downstream_stage_count if cap_limited_batch else 0,
        },
        "root_scan_state": {
            **root_scan_state,
            "current_scan_mode": "incremental_watermark_candidate_discovery",
            "current_scan_start_basis": (
                "source_mtime_watermark_minus_safety_lookback"
                if source_mtime_watermark_ns is not None
                else "no_watermark_initial_metadata_walk"
            ),
            "last_successful_import_source_mtime_ns": source_mtime_watermark_ns,
            "safety_lookback_seconds": safety_lookback_seconds,
            "mtime_cutoff_ns": mtime_cutoff_ns,
            "ledger_evidence_reused": bool(source_record_id is not None),
            "files_stat_checked": stat_required_count,
            "files_hash_checked": 0,
            "new_or_changed_actionable_candidates": downstream_stage_count,
            "more_batches_remain": more_batches_remain,
        },
        "source_delta_workset": {
            "scan_order": priority_workset_mode,
            "priority_workset_files": priority_workset_files,
            "priority_workset_processed": priority_workset_processed,
            "priority_workset_exhausted": priority_workset_exhausted,
            "filesystem_walk_after_priority_workset": filesystem_walk_after_priority_workset,
            "filesystem_walk_completed": filesystem_walk_completed,
            "starts_from_filesystem_root_when_no_priority_workset": True,
            "durable_global_filesystem_cursor": False,
            "incremental_source_ledger_used": bool(source_record_id is not None),
            "source_index_model": "dynamic_source_item_ledger_plus_mtime_watermark",
            "fast_skip_identity": ["source_root_id", "relative_path_hash", "file_size", "mtime_ns"],
            "hash_revalidation_identity": ["execute_time_content_hash"],
            "stable_known_entries_do_not_consume_actionable_cap": True,
        },
        "batch_policy": {
            "user_starts_one_manual_session": True,
            "plan_one_bounded_batch_at_a_time": True,
            "cap_limited_batches_are_executable": True,
            "execute_revalidates_source_identity": True,
            "already_imported_batches_are_reused_from_source_ledger": True,
            "normal_plan_does_not_hash_or_decode": True,
            "next_batch_requires_new_plan": True,
        },
        "resume_policy": {
            "reuses_committed_dynamic_source_item_states": True,
            "revalidates_size_mtime_before_execute": True,
            "hashes_only_during_execute_or_advanced_repair": True,
            "invalidates_on_source_identity_or_metadata_change": True,
            "private_uncommitted_plan_items_are_not_trusted_for_execute": True,
        },
        "plan_source": "source_delta" if source_record_id is not None else "ad_hoc_source_path",
        "plan_stale_after_seconds": MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
        "global_elapsed_timeout_enabled": False,
        "plan_no_progress_timeout_seconds": plan_no_progress_timeout_seconds,
        "plan_timeout_seconds": None,
        "plan_timeout": False,
        "plan_no_progress_timeout": plan_no_progress_timeout,
        "plan_cancelled": plan_cancelled,
        "last_progress_stage": current_stage,
        "last_progress_item_label": last_progress_item_label,
    }
    integrity_payload = _manual_plan_integrity_payload(
        source_record_id=source_record_id,
        source_identity_hash=source_identity_hash,
        limits=limits,
        integrity_items=integrity_items,
        created_at=created_at,
    )
    plan_hash = _stable_json_hash(integrity_payload)
    expires_at = created_at + timedelta(seconds=MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS)
    plan: Dict[str, Any] = {
        "job": {
            "job_id": f"s3a-m2-plan-{uuid4()}",
            "mode": "dry_run",
            "state": "planned",
            "trigger_type": "manual_operator",
            "requested_by": "admin_or_cli",
            "created_at": created_at.isoformat(),
            "started_at": None,
            "ended_at": None,
            "production_execution_enabled": False,
            "automatic_sync_enabled": False,
            "scheduled_sync_enabled": False,
        },
        "source": {
            "source_record_id": source_record_id,
            "source_identity_hash": source_identity_hash,
            "path_public": False,
        },
        "limits": limits,
        "counts": {
            "total_seen": len(public_items),
            "scanned_files": metadata_entries_seen,
            "metadata_entries_seen": metadata_entries_seen,
            "plan_items": len(public_items),
            "estimated_import_count": import_count,
            "estimated_downstream_followup_count": downstream_followup_count,
            "estimated_classification_count": downstream_stage_count,
            "estimated_ai_tagging_count": downstream_stage_count,
            "estimated_localization_workload": downstream_stage_count,
            "state_counts": _public_counter(state_counts, MANUAL_SYNC_FILE_STATES),
            "failure_reasons": reason_counts_public,
            "partial_scan": partial_scan,
            "partial_scan_reason": partial_scan_reason,
            "unsafe_partial_scan": unsafe_partial_scan,
            "cap_limited_batch": cap_limited_batch,
            "batch_executable": batch_executable,
            "more_batches_remain": more_batches_remain,
            "unsupported_extension_breakdown": dict(
                sorted((key, int(value)) for key, value in unsupported_extension_counts.items())
            ),
            "failure_reason_extension_breakdown": {
                reason: dict(sorted((key, int(value)) for key, value in counter.items()))
                for reason, counter in sorted(reason_extension_counts.items())
            },
        },
        "ledger": {
            "db_write_performed": False,
            "source_mutation_performed": False,
            "app_storage_mutation_performed": False,
            "persistent_tables_available": [
                "blombooru_dynamic_source_roots",
                "blombooru_dynamic_source_items",
                "blombooru_dynamic_sync_runs",
                "blombooru_dynamic_sync_run_items",
            ],
            "ledger_mode": "ephemeral_public_candidate_plan_current_phase",
            "per_file_public_records": public_items,
            "private_details_included": include_private_details,
        },
        "pipeline": {
            "status": "dry_run_planned",
            "dry_run_only_this_phase": False,
            "production_execute_enabled": False,
            "dev_test_execute_supported": True,
            "production_execute_requires_separate_operator_approval": True,
            "stages": stages,
            "estimated_runtime_seconds": estimated_runtime_seconds,
            "partial_failure_policy": "execute_records_item_validation_failures_after_candidate_discovery",
        },
        "ai_execution_profile": profile,
        "integrity": {
            "schema": "s3a_m2_incremental_candidate_plan_integrity_v1",
            "plan_hash": plan_hash,
            "hash_algorithm": "sha256",
            "stale_after_seconds": MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
            "expires_at": expires_at.isoformat(),
            "hash_excludes_paths": True,
            "hash_includes_private_content_fingerprint": False,
            "confirmation_phrase": manual_sync_execute_confirmation_phrase(plan_hash),
            "production_confirmation_phrase": manual_sync_execute_confirmation_phrase(plan_hash, production=True),
        },
        "public_safe": True,
    }
    plan["integrity"]["operator_confirmation_statement"] = manual_sync_operator_confirmation_statement(plan)
    plan["integrity"]["production_operator_confirmation_statement"] = manual_sync_operator_confirmation_statement(
        plan,
        production=True,
    )
    if include_private_details:
        plan["private_details"] = {
            "not_for_public_reports": True,
            "items": private_items,
        }
    return plan


def plan_manual_sync_dry_run(
    db: Session,
    *,
    source_path: str | Path,
    source_record_id: Optional[int] = None,
    max_files: Optional[int] = None,
    hydrated_only: bool = True,
    stable_age_seconds: Optional[float] = None,
    include_private_details: bool = False,
    ai_profile: Optional[Dict[str, Any]] = None,
    benchmark: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    plan_mode: str = MANUAL_SYNC_NORMAL_PLAN_MODE,
) -> Dict[str, Any]:
    """Build a public-safe manual sync dry-run plan without DB or file writes."""
    normalized_plan_mode = str(plan_mode or MANUAL_SYNC_NORMAL_PLAN_MODE).strip().lower()
    if normalized_plan_mode in {"normal", "incremental", MANUAL_SYNC_NORMAL_PLAN_MODE}:
        return _plan_manual_sync_incremental_dry_run(
            db,
            source_path=source_path,
            source_record_id=source_record_id,
            max_files=max_files,
            hydrated_only=hydrated_only,
            stable_age_seconds=stable_age_seconds,
            include_private_details=include_private_details,
            ai_profile=ai_profile,
            benchmark=benchmark,
            now=now,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    if normalized_plan_mode != MANUAL_SYNC_ADVANCED_FULL_RESCAN_MODE:
        raise ValueError("manual sync plan_mode must be incremental or advanced_full_rescan")
    resolved = validate_source_root_path(source_path)
    created_at = now or _utcnow()
    created_ts = created_at.timestamp()
    effective_max_files = max_files or settings.DYNAMIC_LIBRARY_MANUAL_SYNC_PLAN_MAX_FILES
    effective_max_files = max(1, int(effective_max_files))
    effective_stable_age = (
        settings.DYNAMIC_LIBRARY_MANUAL_SYNC_STABLE_AGE_SECONDS
        if stable_age_seconds is None
        else max(0.0, float(stable_age_seconds))
    )
    profile = ai_profile or build_ai_tagging_execution_profile(settings).to_public_dict()
    max_duration_seconds = settings.DYNAMIC_LIBRARY_MANUAL_SYNC_MAX_DURATION_SECONDS
    read_timeout_seconds = max(1, int(settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS))

    state_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    public_items: List[Dict[str, Any]] = []
    private_items: List[Dict[str, Any]] = []
    candidate_records: List[Dict[str, Any]] = []
    candidate_hashes: set[str] = set()
    candidate_seen_hashes: set[str] = set()
    existing_media_lookup_cache: Dict[str, Optional[int]] = {}
    partial_scan = False
    partial_scan_reason: Optional[str] = None
    plan_cap_limited = False
    walk_errors: List[str] = []
    scanned_files = 0
    stat_required_count = 0
    hash_required_count = 0
    unchanged_known_files = 0
    skipped_existing_before_cap = 0
    skipped_duplicate_before_cap = 0
    import_candidate_count = 0
    actionable_candidate_count = 0
    priority_workset_files = 0
    priority_workset_mode = "filesystem_walk"
    priority_workset_processed = 0
    priority_workset_exhausted = False
    filesystem_walk_after_priority_workset = False
    filesystem_walk_completed = False
    plan_timeout = False
    plan_no_progress_timeout = False
    plan_cancelled = False
    current_stage = "initializing"
    last_progress_item_label: Optional[str] = None
    plan_started_monotonic = time.monotonic()
    last_progress_monotonic = plan_started_monotonic
    plan_no_progress_timeout_seconds = MANUAL_SYNC_PLAN_NO_PROGRESS_TIMEOUT_SECONDS
    unsupported_extension_counts: Counter = Counter()
    reason_extension_counts: Dict[str, Counter] = {}

    def _record_reason_extension(reason_code: Optional[str], rel_path: str) -> None:
        if not reason_code:
            return
        extension = Path(str(rel_path or "")).suffix.lower() or "<none>"
        reason_extension_counts.setdefault(reason_code, Counter())[extension] += 1
        if reason_code == "unsupported_extension":
            unsupported_extension_counts[extension] += 1

    def _progress(phase: str, **updates: Any) -> None:
        nonlocal current_stage, last_progress_item_label, last_progress_monotonic
        current_stage = phase
        last_progress_monotonic = time.monotonic()
        if "current_item_label" in updates and updates["current_item_label"]:
            last_progress_item_label = str(updates["current_item_label"])
        if progress_callback is None:
            return
        counts = {
            "seen": int(scanned_files),
            "skipped_historical": int(unchanged_known_files),
            "skipped_unsupported": int(reason_counts.get("unsupported_extension", 0)),
            "placeholders_found": int(reason_counts.get("cloud_hydration_failed", 0) + reason_counts.get("cloud_placeholder", 0)),
            "hydrated": 0,
            "importable": int(state_counts.get("import_planned", 0)),
            "planned": int(len(candidate_records)),
            "skipped_existing": int(skipped_existing_before_cap + state_counts.get("skipped_existing_media", 0)),
            "skipped_duplicate": int(skipped_duplicate_before_cap + state_counts.get("skipped_duplicate", 0)),
            "failed": int(
                reason_counts.get("read_error", 0)
                + reason_counts.get("read_timeout", 0)
                + reason_counts.get("stat_error", 0)
                + reason_counts.get("source_walk_error", 0)
            ),
            "batch_candidates": int(len(candidate_records)),
        }
        payload = {
            "phase": phase,
            "status": "running",
            "current_item_index": int(scanned_files),
            "current_item_label": last_progress_item_label,
            "counts": counts,
            "elapsed_seconds": round(max(0.0, time.monotonic() - plan_started_monotonic), 3),
            "last_progress_at": _utcnow().isoformat(),
        }
        payload.update(updates)
        progress_callback(payload)

    def _cancel_requested() -> bool:
        if cancel_check is None:
            return False
        try:
            return bool(cancel_check())
        except Exception:
            return False

    def _no_progress_timed_out() -> bool:
        return (time.monotonic() - last_progress_monotonic) > plan_no_progress_timeout_seconds

    _progress("loading_known_source_items")
    known_items_by_rel_hash: Dict[str, DynamicSourceItem] = {}
    if source_record_id is not None:
        known_items_by_rel_hash = {
            item.relative_path_hash: item
            for item in db.query(DynamicSourceItem)
            .filter(DynamicSourceItem.source_root_id == int(source_record_id))
            .all()
        }
    _progress("scanning")
    priority_source_files = (
        _manual_plan_priority_source_files(resolved, known_items_by_rel_hash)
        if source_record_id is not None
        else []
    )
    priority_workset_files = len(priority_source_files)
    if priority_source_files:
        priority_workset_mode = "source_delta_priority_workset_then_filesystem_walk"

    processed_path_identities: set[str] = set()

    def _source_file_batches() -> Iterable[tuple[Path, str]]:
        nonlocal filesystem_walk_after_priority_workset, filesystem_walk_completed
        for priority_path in priority_source_files:
            path_identity = _normalized_path_identity(priority_path)
            processed_path_identities.add(path_identity)
            yield priority_path, "source_delta_priority_workset"
        if priority_source_files:
            filesystem_walk_after_priority_workset = True
            _progress("scanning_filesystem_after_priority_workset")
        for walked_path in _iter_source_files(resolved, walk_errors=walk_errors):
            path_identity = _normalized_path_identity(walked_path)
            if path_identity in processed_path_identities:
                continue
            processed_path_identities.add(path_identity)
            yield walked_path, "filesystem_walk"
        filesystem_walk_completed = True

    for index, (file_path, scan_source) in enumerate(_source_file_batches(), start=1):
        if _cancel_requested():
            partial_scan = True
            plan_cancelled = True
            partial_scan_reason = "cancelled"
            reason_counts["plan_cancelled"] += 1
            break
        if _no_progress_timed_out():
            partial_scan = True
            plan_no_progress_timeout = True
            partial_scan_reason = "no_progress_timeout"
            reason_counts["plan_no_progress_timeout"] += 1
            break
        scanned_files = index
        if scan_source == "source_delta_priority_workset":
            priority_workset_processed += 1

        safe_label = f"file-{index:05d}"
        _progress("scanning", current_item_index=index, current_item_label=safe_label, scan_source=scan_source)
        rel, preflight_reason = _relative_identity_and_preflight_reason(resolved, file_path)
        rel_hash_full = _hash_text(rel)
        rel_hash = rel_hash_full[:16]
        metadata: Dict[str, Any] = {}
        reason = _manual_public_reason_code(preflight_reason)
        content_hash = None
        cloud_placeholder_before_hydration = False

        try:
            _progress("stat", current_item_index=index, current_item_label=safe_label)
            metadata = _metadata_for_path(file_path, follow_symlinks=not bool(preflight_reason))
            stat_required_count += 1
        except OSError:
            reason = "stat_error"

        if reason is None and effective_stable_age > 0:
            mtime = metadata.get("mtime")
            if mtime is not None and created_ts - float(mtime) < effective_stable_age:
                reason = "file_still_changing"

        known_item = known_items_by_rel_hash.get(rel_hash_full)
        if reason is None and _manual_plan_can_skip_unchanged_known_item(known_item, metadata):
            unchanged_known_files += 1
            if unchanged_known_files == 1 or unchanged_known_files % 250 == 0:
                _progress("checking_existing_ledger", current_item_index=index, current_item_label=safe_label)
            continue

        if actionable_candidate_count >= effective_max_files:
            partial_scan = True
            plan_cap_limited = True
            partial_scan_reason = "cap_limited_actionable_batch"
            break

        if reason is None and not hydrated_only:
            try:
                _progress("detecting_placeholders", current_item_index=index, current_item_label=safe_label)
                cloud_placeholder_before_hydration = bool(_is_cloud_only(file_path))
            except Exception:
                cloud_placeholder_before_hydration = False

        if reason is None:
            _progress("checking_supported", current_item_index=index, current_item_label=safe_label)
            reason = _manual_public_reason_code(_is_scannable_file(file_path, hydrated_only=hydrated_only))

        if reason is None:
            if _cancel_requested():
                partial_scan = True
                plan_cancelled = True
                partial_scan_reason = "cancelled"
                reason_counts["plan_cancelled"] += 1
                break
            _progress("checking_supported", current_item_index=index, current_item_label=safe_label)
            reason = _verify_supported_image_file_with_timeout(file_path, read_timeout_seconds)

        if reason is None:
            if _cancel_requested():
                partial_scan = True
                plan_cancelled = True
                partial_scan_reason = "cancelled"
                reason_counts["plan_cancelled"] += 1
                break
            _progress("hashing", current_item_index=index, current_item_label=safe_label)
            hash_required_count += 1
            content_hash, reason = _calculate_manual_plan_file_hash(file_path, read_timeout_seconds)

        reason = _manual_public_reason_code(reason)
        if cloud_placeholder_before_hydration and reason in {"read_error", "read_timeout", "stat_error"}:
            reason = "cloud_hydration_failed"
        if reason is None and content_hash:
            existing_media_id = _lookup_existing_media_id_by_hash(db, content_hash, existing_media_lookup_cache)
            if (
                known_item is not None
                and known_item.media_id is not None
                and _manual_plan_existing_requires_followup(known_item)
                and (
                    existing_media_id == int(known_item.media_id)
                    or (str(known_item.content_hash or "") and str(known_item.content_hash) == str(content_hash))
                )
            ):
                actionable_candidate_count += 1
                candidate_records.append(
                    {
                        "safe_label": safe_label,
                        "relative_path": rel,
                        "relative_path_hash": rel_hash,
                        "relative_path_hash_full": rel_hash_full,
                        "metadata": metadata,
                        "reason": "downstream_followup",
                        "content_hash": content_hash,
                        "media_id": int(known_item.media_id),
                        "cloud_placeholder_before_hydration": cloud_placeholder_before_hydration,
                        "followup": _manual_plan_followup_payload(known_item),
                        "scan_source": scan_source,
                    }
                )
                _progress("planning_downstream_followup", current_item_index=index, current_item_label=safe_label)
                continue
            if existing_media_id is not None:
                skipped_existing_before_cap += 1
                candidate_records.append(
                    {
                        "safe_label": safe_label,
                        "relative_path": rel,
                        "relative_path_hash": rel_hash,
                        "relative_path_hash_full": rel_hash_full,
                        "metadata": metadata,
                        "reason": "existing_media_hash",
                        "content_hash": content_hash,
                        "media_id": existing_media_id,
                        "cloud_placeholder_before_hydration": cloud_placeholder_before_hydration,
                        "pre_cap_skip": True,
                        "scan_source": scan_source,
                    }
                )
                if skipped_existing_before_cap == 1 or skipped_existing_before_cap % 250 == 0:
                    _progress("skipping_existing_media", current_item_index=index, current_item_label=safe_label)
                continue
            if content_hash in candidate_seen_hashes:
                skipped_duplicate_before_cap += 1
                candidate_records.append(
                    {
                        "safe_label": safe_label,
                        "relative_path": rel,
                        "relative_path_hash": rel_hash,
                        "relative_path_hash_full": rel_hash_full,
                        "metadata": metadata,
                        "reason": "duplicate_hash",
                        "content_hash": content_hash,
                        "media_id": None,
                        "cloud_placeholder_before_hydration": cloud_placeholder_before_hydration,
                        "pre_cap_skip": True,
                        "scan_source": scan_source,
                    }
                )
                if skipped_duplicate_before_cap == 1 or skipped_duplicate_before_cap % 250 == 0:
                    _progress("skipping_duplicate", current_item_index=index, current_item_label=safe_label)
                continue
            candidate_seen_hashes.add(content_hash)
            candidate_hashes.add(content_hash)
            import_candidate_count += 1
            actionable_candidate_count += 1
        candidate_records.append(
            {
                "safe_label": safe_label,
                "relative_path": rel,
                "relative_path_hash": rel_hash,
                "relative_path_hash_full": rel_hash_full,
                "metadata": metadata,
                "reason": reason,
                "content_hash": content_hash,
                "cloud_placeholder_before_hydration": cloud_placeholder_before_hydration,
                "scan_source": scan_source,
            }
        )

    if priority_source_files and priority_workset_processed >= priority_workset_files:
        priority_workset_exhausted = True
    if not filesystem_walk_completed and not (plan_cancelled or plan_no_progress_timeout):
        partial_scan = True
        partial_scan_reason = partial_scan_reason or (
            "cap_limited_actionable_batch" if plan_cap_limited else "filesystem_walk_incomplete"
        )

    _progress(
        "cancelled"
        if plan_cancelled
        else ("no_progress_timeout" if plan_no_progress_timeout else "checking_existing_media")
    )
    existing_media_by_hash = _query_existing_media_by_hashes(db, candidate_hashes) if candidate_hashes else {}
    seen_hashes: set[str] = set()
    integrity_items: List[Dict[str, Any]] = []
    for record in candidate_records:
        _progress("planning", current_item_label=record.get("safe_label"))
        reason = record["reason"]
        content_hash = record["content_hash"]
        media_id = record.get("media_id")

        if reason is None and content_hash:
            if content_hash in existing_media_by_hash:
                state = "skipped_existing_media"
                reason = "existing_media_hash"
                media_id = existing_media_by_hash.get(content_hash)
            elif content_hash in seen_hashes:
                state = "skipped_duplicate"
                reason = "duplicate_hash"
            else:
                state = "import_planned"
                seen_hashes.add(content_hash)
        else:
            state = _manual_state_for_reason(str(reason or "read_error"))

        public_reason = _manual_public_reason_code(reason)
        state_counts[state] += 1
        if public_reason:
            reason_counts[public_reason] += 1
            _record_reason_extension(public_reason, str(record["relative_path"] or ""))

        metadata = record["metadata"]
        item = {
            "safe_label": record["safe_label"],
            "relative_path_hash": record["relative_path_hash"],
            "initial_state": "candidate",
            "state": state,
            "reason": public_reason,
            "eligible_for_db_import": state == "import_planned",
            "bytes_copied": 0,
            "media_id": media_id,
            "file_size": metadata.get("file_size"),
            "content_hash_computed": bool(content_hash),
            "cloud_placeholder_before_hydration": bool(record.get("cloud_placeholder_before_hydration")),
            "scan_source": str(record.get("scan_source") or ""),
        }
        if state == "downstream_followup_planned":
            item["downstream_followup"] = dict(record.get("followup") or {})
        public_items.append(item)
        integrity_items.append(
            {
                "safe_label": record["safe_label"],
                "relative_path_hash": record["relative_path_hash_full"],
                "file_size": metadata.get("file_size"),
                "mtime_ns": metadata.get("mtime_ns"),
                "state": state,
                "reason": public_reason,
                "content_hash": content_hash,
                "cloud_placeholder_before_hydration": bool(record.get("cloud_placeholder_before_hydration")),
                "scan_source": str(record.get("scan_source") or ""),
                "downstream_followup": dict(record.get("followup") or {}),
            }
        )
        if include_private_details:
            private_items.append(
                {
                    **item,
                    "relative_path": record["relative_path"],
                    "content_hash": content_hash,
                    "mtime_ns": metadata.get("mtime_ns"),
                    "cloud_placeholder_before_hydration": bool(record.get("cloud_placeholder_before_hydration")),
                    "downstream_followup": dict(record.get("followup") or {}),
                }
            )

    if walk_errors:
        partial_scan = True
        partial_scan_reason = "source_walk_error"
        reason_counts["source_walk_error"] += len(walk_errors)

    import_count = int(state_counts.get("import_planned", 0))
    downstream_followup_count = int(state_counts.get("downstream_followup_planned", 0))
    downstream_stage_count = import_count + downstream_followup_count
    unsafe_partial_scan = bool(
        plan_cancelled
        or plan_no_progress_timeout
        or walk_errors
        or (partial_scan and partial_scan_reason not in {None, "cap_limited_actionable_batch"})
    )
    cap_limited_batch = bool(partial_scan and partial_scan_reason == "cap_limited_actionable_batch")
    batch_executable = bool(downstream_stage_count > 0 and not unsafe_partial_scan)
    more_batches_remain = bool(cap_limited_batch)
    estimated_runtime_seconds = _estimate_manual_sync_runtime_seconds(
        import_count=import_count,
        ai_profile=profile,
        benchmark=benchmark,
    )
    stages = _build_manual_pipeline_stages(
        state_counts=state_counts,
        ai_profile=profile,
        max_duration_seconds=max_duration_seconds,
        estimated_runtime_seconds=estimated_runtime_seconds,
    )
    state_counts_public = _public_counter(state_counts, MANUAL_SYNC_FILE_STATES)
    reason_counts_public = dict(sorted((key, int(value)) for key, value in reason_counts.items()))
    source_identity_hash = _public_source_identity(resolved)
    root_scan_state = _manual_plan_root_scan_state(
        db,
        source_record_id=source_record_id,
        known_items_by_rel_hash=known_items_by_rel_hash,
    )
    limits = {
        "max_files": effective_max_files,
        "hydrated_only": hydrated_only,
        "hydration_policy": "local_readable_only" if hydrated_only else "cloud_aware_non_destructive_read",
        "cloud_placeholders_detected_before_hydration": int(
            sum(1 for record in candidate_records if record.get("cloud_placeholder_before_hydration"))
        ),
        "stable_age_seconds": effective_stable_age,
        "max_duration_seconds": max_duration_seconds,
        "file_read_timeout_seconds": read_timeout_seconds,
        "scanned_files": scanned_files,
        "stat_required_count": stat_required_count,
        "hash_required_count": hash_required_count,
        "unchanged_known_files": unchanged_known_files,
        "fast_skipped_from_ledger": unchanged_known_files,
        "skipped_existing_before_cap": skipped_existing_before_cap,
        "skipped_duplicate_before_cap": skipped_duplicate_before_cap,
        "import_candidate_count": import_candidate_count,
        "actionable_candidate_count": actionable_candidate_count,
        "downstream_followup_count": downstream_followup_count,
        "max_files_scope": "manual_sync_actionable_delta_candidates",
        "cap_semantics": "unique_importable_or_downstream_followup_candidates_not_unchanged_or_existing_media",
        "batch_mode": "bounded_actionable_batch",
        "batch_candidate_cap": effective_max_files,
        "batch_executable": batch_executable,
        "cap_limited_batch": cap_limited_batch,
        "more_batches_remain": more_batches_remain,
        "unsafe_partial_scan": unsafe_partial_scan,
        "partial_scan_reason": partial_scan_reason,
        "continuation": {
            "more_batches_remain": more_batches_remain,
            "next_batch_requires_new_plan": True,
            "next_batch_start_basis": (
                "source_ledger_after_executed_batch_plus_metadata_filesystem_fallback"
                if more_batches_remain
                else "root_exhausted_or_no_continuation_required"
            ),
            "current_batch_actionable_count": downstream_stage_count,
            "cap_limited_after_actionable_count": actionable_candidate_count if cap_limited_batch else 0,
        },
        "root_scan_state": {
            **root_scan_state,
            "current_scan_mode": (
                "incremental_source_ledger_priority_then_filesystem_metadata_walk"
                if source_record_id is not None
                else "ad_hoc_filesystem_metadata_walk"
            ),
            "current_scan_start_basis": priority_workset_mode,
            "ledger_evidence_reused": bool(source_record_id is not None),
            "fast_skipped_from_ledger": unchanged_known_files,
            "files_stat_checked": stat_required_count,
            "files_hash_checked": hash_required_count,
            "new_or_changed_actionable_candidates": downstream_stage_count,
            "more_batches_remain": more_batches_remain,
        },
        "source_delta_workset": {
            "scan_order": priority_workset_mode,
            "priority_workset_files": priority_workset_files,
            "priority_workset_processed": priority_workset_processed,
            "priority_workset_exhausted": priority_workset_exhausted,
            "filesystem_walk_after_priority_workset": filesystem_walk_after_priority_workset,
            "filesystem_walk_completed": filesystem_walk_completed,
            "filesystem_walk_deferred_after_priority_workset": bool(priority_source_files)
            and not filesystem_walk_after_priority_workset,
            "starts_from_filesystem_root_when_no_priority_workset": not bool(priority_source_files),
            "durable_global_filesystem_cursor": False,
            "incremental_source_ledger_used": bool(source_record_id is not None),
            "source_index_model": "dynamic_source_item_ledger_metadata_fast_path",
            "fast_skip_identity": ["source_root_id", "relative_path_hash", "file_size", "mtime_ns"],
            "hash_revalidation_identity": ["content_hash"],
            "stable_known_entries_do_not_consume_actionable_cap": True,
        },
        "batch_policy": {
            "user_starts_one_manual_session": True,
            "plan_one_bounded_batch_at_a_time": True,
            "cap_limited_batches_are_executable": True,
            "execute_revalidates_source_identity": True,
            "already_imported_batches_are_reused_from_source_ledger": True,
            "unexecuted_candidate_plan_reuse": "not_reused_without_execute_revalidation",
            "next_batch_requires_new_plan": True,
        },
        "resume_policy": {
            "reuses_committed_dynamic_source_item_states": True,
            "revalidates_size_mtime_and_hash_before_execute": True,
            "invalidates_on_source_identity_or_metadata_change": True,
            "private_uncommitted_plan_items_are_not_trusted_for_execute": True,
        },
        "plan_source": "source_delta" if source_record_id is not None else "ad_hoc_source_path",
        "plan_stale_after_seconds": MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
        "global_elapsed_timeout_enabled": False,
        "plan_no_progress_timeout_seconds": plan_no_progress_timeout_seconds,
        "plan_timeout_seconds": None,
        "plan_timeout": plan_timeout,
        "plan_no_progress_timeout": plan_no_progress_timeout,
        "plan_cancelled": plan_cancelled,
        "last_progress_stage": current_stage,
        "last_progress_item_label": last_progress_item_label,
    }
    integrity_payload = _manual_plan_integrity_payload(
        source_record_id=source_record_id,
        source_identity_hash=source_identity_hash,
        limits=limits,
        integrity_items=integrity_items,
        created_at=created_at,
    )
    plan_hash = _stable_json_hash(integrity_payload)
    expires_at = created_at + timedelta(seconds=MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS)

    plan: Dict[str, Any] = {
        "job": {
            "job_id": f"s2g-m1-plan-{uuid4()}",
            "mode": "dry_run",
            "state": "planned",
            "trigger_type": "manual_operator",
            "requested_by": "admin_or_cli",
            "created_at": created_at.isoformat(),
            "started_at": None,
            "ended_at": None,
            "production_execution_enabled": False,
            "automatic_sync_enabled": False,
            "scheduled_sync_enabled": False,
        },
        "source": {
            "source_record_id": source_record_id,
            "source_identity_hash": source_identity_hash,
            "path_public": False,
        },
        "limits": limits,
        "counts": {
            "total_seen": len(public_items),
            "scanned_files": scanned_files,
            "plan_items": len(public_items),
            "estimated_import_count": import_count,
            "estimated_downstream_followup_count": downstream_followup_count,
            "estimated_classification_count": downstream_stage_count,
            "estimated_ai_tagging_count": downstream_stage_count,
            "estimated_localization_workload": downstream_stage_count,
            "state_counts": state_counts_public,
            "failure_reasons": reason_counts_public,
            "partial_scan": partial_scan,
            "partial_scan_reason": partial_scan_reason,
            "unsafe_partial_scan": unsafe_partial_scan,
            "cap_limited_batch": cap_limited_batch,
            "batch_executable": batch_executable,
            "more_batches_remain": more_batches_remain,
            "unsupported_extension_breakdown": dict(
                sorted((key, int(value)) for key, value in unsupported_extension_counts.items())
            ),
            "failure_reason_extension_breakdown": {
                reason: dict(sorted((key, int(value)) for key, value in counter.items()))
                for reason, counter in sorted(reason_extension_counts.items())
            },
        },
        "ledger": {
            "db_write_performed": False,
            "source_mutation_performed": False,
            "app_storage_mutation_performed": False,
            "persistent_tables_available": [
                "blombooru_dynamic_source_roots",
                "blombooru_dynamic_source_items",
                "blombooru_dynamic_sync_runs",
                "blombooru_dynamic_sync_run_items",
            ],
            "ledger_mode": "ephemeral_public_plan_current_phase",
            "per_file_public_records": public_items,
            "private_details_included": include_private_details,
        },
        "pipeline": {
            "status": "dry_run_planned",
            "dry_run_only_this_phase": False,
            "production_execute_enabled": False,
            "dev_test_execute_supported": True,
            "production_execute_requires_separate_operator_approval": True,
            "stages": stages,
            "estimated_runtime_seconds": estimated_runtime_seconds,
            "partial_failure_policy": "item_failures_recorded_and_continues_until_failure_budget_or_hard_gate",
        },
        "ai_execution_profile": profile,
        "integrity": {
            "schema": "s3a_m1_manual_sync_plan_integrity_v1",
            "plan_hash": plan_hash,
            "hash_algorithm": "sha256",
            "stale_after_seconds": MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
            "expires_at": expires_at.isoformat(),
            "hash_excludes_paths": True,
            "hash_includes_private_content_fingerprint": True,
            "confirmation_phrase": manual_sync_execute_confirmation_phrase(plan_hash),
            "production_confirmation_phrase": manual_sync_execute_confirmation_phrase(plan_hash, production=True),
        },
        "public_safe": True,
    }
    plan["integrity"]["operator_confirmation_statement"] = manual_sync_operator_confirmation_statement(plan)
    plan["integrity"]["production_operator_confirmation_statement"] = manual_sync_operator_confirmation_statement(
        plan,
        production=True,
    )
    if include_private_details:
        plan["private_details"] = {
            "not_for_public_reports": True,
            "items": private_items,
        }
    return plan


def _iter_source_files(root_path: Path, *, walk_errors: Optional[List[str]] = None) -> Iterable[Path]:
    def _on_walk_error(exc: OSError) -> None:
        if walk_errors is not None:
            walk_errors.append(type(exc).__name__ or "OSError")

    for dirpath, dirnames, filenames in os.walk(root_path, onerror=_on_walk_error):
        dirnames[:] = sorted(d for d in dirnames if d not in {".git", "__pycache__", "venv"})
        for filename in sorted(filenames):
            yield Path(dirpath) / filename


def _source_item_file_path(root_path: Path, item: DynamicSourceItem) -> Optional[Path]:
    rel = str(item.relative_path or "")
    if not rel:
        return None
    try:
        root_resolved = root_path.resolve()
        candidate = (root_resolved / rel).resolve()
        if not candidate.is_relative_to(root_resolved):
            return None
        return candidate
    except (OSError, RuntimeError, ValueError):
        return None


def _manual_plan_priority_for_known_item(item: DynamicSourceItem) -> Optional[int]:
    import_status = str(item.import_status or "")
    sync_state = str(item.sync_state or "")
    reason = str(item.deferred_reason or item.failure_reason or "")
    if import_status == "pending":
        return 10 if sync_state == "new" else 20
    if sync_state == "skipped_placeholder" or reason in {"cloud_placeholder", "icloud_placeholder"}:
        return 30
    if import_status in {"failed", "deferred"} and reason in {"read_error", "read_timeout", "cloud_hydration_failed"}:
        return 40
    if import_status == "imported" and item.media_id is not None and _manual_plan_existing_requires_followup(item):
        return 50
    return None


def _manual_plan_priority_source_files(
    root_path: Path,
    known_items_by_rel_hash: Dict[str, DynamicSourceItem],
) -> List[Path]:
    prioritized: List[tuple[int, str, Path]] = []
    for item in known_items_by_rel_hash.values():
        priority = _manual_plan_priority_for_known_item(item)
        if priority is None:
            continue
        file_path = _source_item_file_path(root_path, item)
        if file_path is None:
            continue
        prioritized.append((priority, str(item.relative_path_hash or ""), file_path))
    return [path for _priority, _rel_hash, path in sorted(prioritized, key=lambda row: (row[0], row[1]))]


def _metadata_for_path(path: Path, *, follow_symlinks: bool = True) -> Dict[str, Any]:
    stat = path.stat() if follow_symlinks else path.lstat()
    return {
        "file_size": stat.st_size,
        "mtime": stat.st_mtime,
        "mtime_ns": getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
        "suffix": path.suffix.lower(),
    }


def _relative_identity_and_preflight_reason(root_path: Path, file_path: Path) -> tuple[str, Optional[str]]:
    try:
        rel = _normalize_relative_path(file_path.relative_to(root_path))
    except ValueError:
        rel = file_path.name
        return rel, "path_escape"

    if file_path.is_symlink():
        return rel, "symlink"

    try:
        root_resolved = root_path.resolve()
        file_resolved = file_path.resolve()
        file_resolved.relative_to(root_resolved)
    except ValueError:
        return rel, "path_escape"
    except OSError:
        return rel, "stat_error"
    except RuntimeError:
        return rel, "path_escape"
    return rel, None


def _manual_plan_existing_requires_followup(item: Optional[DynamicSourceItem]) -> bool:
    if item is None:
        return False
    import_status = str(item.import_status or "")
    sync_state = str(item.sync_state or "")
    classification_status = str(item.classification_status or "")
    ai_tagging_status = str(item.ai_tagging_status or "")
    localization_status = str(item.localization_status or "")
    reason = str(item.deferred_reason or item.failure_reason or "")
    if sync_state == "skipped_placeholder" or reason in {"cloud_placeholder", "icloud_placeholder"}:
        return True
    if import_status == "pending":
        return True
    if import_status == "imported" and item.media_id is None:
        return True
    if import_status == "imported" and item.media_id is not None:
        classification_done = classification_status in {"classified", "classified_reused"}
        ai_done = ai_tagging_status in {
            "ai_tagged",
            "tagged",
            "tagged_reused",
            "ai_tagging_skipped_non_target",
            "skipped_non_target",
        }
        localization_done = localization_status in {
            "localized",
            "completed",
            "skipped_no_localizable_tags",
            "skipped_no_new_tags",
            "skipped_static_coverage",
            "localization_not_applicable_non_target",
        }
        if not (classification_done and ai_done and localization_done):
            return True
    stable_non_actionable = {
        "unsupported_extension",
        "hidden",
        "zero_byte",
        "zero_byte_file",
        "source_missing",
        "permission_denied",
        "existing_media_hash",
        "duplicate_hash",
    }
    if reason in stable_non_actionable:
        return False
    return import_status in {"deferred", "failed"} or sync_state in {"failed", "deferred"}


def _manual_plan_followup_payload(item: DynamicSourceItem) -> Dict[str, Any]:
    classification_status = str(item.classification_status or "")
    ai_tagging_status = str(item.ai_tagging_status or "")
    localization_status = str(item.localization_status or "")
    classification_done = classification_status in {"classified", "classified_reused"}
    ai_done = ai_tagging_status in {
        "ai_tagged",
        "tagged",
        "tagged_reused",
        "ai_tagging_skipped_non_target",
        "skipped_non_target",
    }
    localization_done = localization_status in {
        "localized",
        "completed",
        "skipped_no_localizable_tags",
        "skipped_no_new_tags",
        "skipped_static_coverage",
        "localization_not_applicable_non_target",
    }
    return {
        "source_item_id": int(item.id) if item.id is not None else None,
        "classification_required": not classification_done,
        "ai_tagging_required": not ai_done,
        "localization_required": not localization_done,
        "classification_status": classification_status or "unknown",
        "ai_tagging_status": ai_tagging_status or "unknown",
        "localization_status": localization_status or "unknown",
    }


def _manual_plan_can_skip_unchanged_known_item(
    item: Optional[DynamicSourceItem],
    metadata: Dict[str, Any],
) -> bool:
    if item is None:
        return False
    if item.file_size != metadata.get("file_size") or item.mtime_ns != metadata.get("mtime_ns"):
        return False
    return not _manual_plan_existing_requires_followup(item)


def _apply_item_state(
    item: DynamicSourceItem,
    *,
    state: str,
    eligible: bool,
    reason: Optional[str],
    metadata: Dict[str, Any],
    run: DynamicSyncRun,
    now: datetime,
    previous_metadata: Optional[Dict[str, Any]],
) -> DynamicSyncRunItem:
    item.file_size = metadata.get("file_size")
    item.mtime = metadata.get("mtime")
    item.mtime_ns = metadata.get("mtime_ns")
    item.source_status = "available" if eligible else ("failed" if state == "failed" else "deferred")
    item.sync_state = state
    item.failure_reason = reason if state == "failed" else None
    item.deferred_reason = reason if not eligible and state != "failed" else None
    item.last_checked_at = now
    item.last_sync_run_id = run.id
    item.last_seen_at = now
    item.last_seen_run_id = run.id
    item.metadata_json = {
        "suffix": metadata.get("suffix"),
        "content_hash_computed": bool(item.content_hash),
    }
    if eligible:
        if state in {"new", "changed"}:
            item.import_status = "pending"
            item.classification_status = "waiting_import"
            item.ai_tagging_status = "waiting_import"
            item.localization_status = "waiting_ai_tags"
        elif item.media_id and item.import_status == "pending":
            item.import_status = "imported"
    else:
        item.import_status = "deferred"
        item.classification_status = "deferred"
        item.ai_tagging_status = "deferred"
        item.localization_status = "deferred"

    run_item = DynamicSyncRunItem(
        sync_run_id=run.id,
        source_item_id=item.id,
        item_state=state,
        action="record_only",
        reason=reason,
        eligible_for_db_import=eligible and item.import_status == "pending",
        bytes_copied=0,
        media_id=item.media_id,
        previous_metadata_json=previous_metadata,
        current_metadata_json=metadata,
    )
    return run_item


def _record_file_observation(
    db: Session,
    *,
    root: DynamicSourceRoot,
    run: DynamicSyncRun,
    root_path: Path,
    file_path: Path,
    now: datetime,
    hydrated_only: bool,
) -> str:
    rel, preflight_reason = _relative_identity_and_preflight_reason(root_path, file_path)
    rel_hash = _hash_text(rel)
    item = (
        db.query(DynamicSourceItem)
        .filter(
            DynamicSourceItem.source_root_id == root.id,
            DynamicSourceItem.relative_path_hash == rel_hash,
        )
        .first()
    )
    is_new = item is None
    if is_new:
        item = DynamicSourceItem(
            source_root_id=root.id,
            relative_path=rel,
            relative_path_hash=rel_hash,
            first_seen_at=now,
        )
        db.add(item)
        db.flush()

    previous_metadata = None
    if not is_new:
        previous_metadata = {
            "file_size": item.file_size,
            "mtime": item.mtime,
            "mtime_ns": item.mtime_ns,
            "sync_state": item.sync_state,
            "import_status": item.import_status,
        }

    try:
        reason = preflight_reason or _is_scannable_file(file_path, hydrated_only=hydrated_only)
        metadata = _metadata_for_path(file_path, follow_symlinks=not bool(preflight_reason))
    except OSError as exc:
        metadata = {}
        reason = f"stat_error: {exc}"

    eligible = reason is None
    requeue_from_deferred = (
        not is_new
        and eligible
        and (
            item.import_status == "deferred"
            or item.source_status in {"deferred", "failed", "missing"}
            or item.sync_state in {"deferred", "failed", "missing"}
        )
    )
    if not eligible:
        state = "failed" if str(reason).startswith("stat_error") else "deferred"
    elif is_new:
        state = "new"
    elif requeue_from_deferred:
        state = "changed" if item.media_id else "new"
    elif item.file_size != metadata.get("file_size") or item.mtime_ns != metadata.get("mtime_ns"):
        state = "changed"
    elif item.import_status == "pending" and item.sync_state in {"new", "changed"}:
        state = item.sync_state
    else:
        state = "unchanged"

    item.relative_path = rel
    db.flush()
    run_item = _apply_item_state(
        item,
        state=state,
        eligible=eligible,
        reason=reason,
        metadata=metadata,
        run=run,
        now=now,
        previous_metadata=previous_metadata,
    )
    db.add(run_item)
    return state


def _mark_missing_items(db: Session, *, root: DynamicSourceRoot, run: DynamicSyncRun, now: datetime) -> int:
    db.flush()
    missing_items = (
        db.query(DynamicSourceItem)
        .filter(DynamicSourceItem.source_root_id == root.id)
        .filter(or_(DynamicSourceItem.last_seen_run_id != run.id, DynamicSourceItem.last_seen_run_id.is_(None)))
        .filter(DynamicSourceItem.source_status != "missing")
        .all()
    )
    for item in missing_items:
        previous_metadata = {
            "file_size": item.file_size,
            "mtime": item.mtime,
            "mtime_ns": item.mtime_ns,
            "sync_state": item.sync_state,
            "import_status": item.import_status,
        }
        item.source_status = "missing"
        item.sync_state = "missing"
        item.import_status = "deferred"
        item.classification_status = "deferred"
        item.ai_tagging_status = "deferred"
        item.localization_status = "deferred"
        item.failure_reason = "source_missing"
        item.last_checked_at = now
        item.last_sync_run_id = run.id
        db.add(
            DynamicSyncRunItem(
                sync_run_id=run.id,
                source_item_id=item.id,
                item_state="missing",
                action="record_only",
                reason="source_missing",
                eligible_for_db_import=False,
                bytes_copied=0,
                media_id=item.media_id,
                previous_metadata_json=previous_metadata,
                current_metadata_json={"source_status": "missing"},
            )
        )
    return len(missing_items)


def run_update_check(
    db: Session,
    *,
    root_ids: Optional[List[int]] = None,
    max_files: Optional[int] = None,
    hydrated_only: bool = True,
) -> Dict[str, Any]:
    """Run a metadata-only source update check and persist state."""
    if hydrated_only is False:
        raise ValueError("dynamic sync S1 requires hydrated_only=true")

    threshold = settings.DYNAMIC_LIBRARY_SYNC_THRESHOLD
    query = db.query(DynamicSourceRoot).filter(DynamicSourceRoot.is_active == True)
    if root_ids is not None:
        if not root_ids:
            raise ValueError("root_ids must not be empty; omit root_ids to scan all active roots")
        query = query.filter(DynamicSourceRoot.id.in_(root_ids))
    roots = query.order_by(DynamicSourceRoot.id.asc()).all()
    if not roots:
        raise ValueError("no active source roots configured")

    now = _utcnow()
    run = DynamicSyncRun(
        run_type="check",
        mode="dry_run",
        status="running",
        dry_run=True,
        threshold=threshold,
        roots_checked=len(roots),
        started_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    counts = {
        "total_seen": 0,
        "new_items": 0,
        "changed_items": 0,
        "unchanged_items": 0,
        "deferred_items": 0,
        "failed_items": 0,
        "missing_items": 0,
    }
    root_summaries: List[Dict[str, Any]] = []

    run_id = run.id
    try:
        for root in roots:
            root_path = validate_source_root_path(root.root_path)
            root_counts = {key: 0 for key in counts}
            partial_scan = False
            missing_reconciliation_skipped = False
            missing_reconciliation_reason = None
            walk_errors: List[str] = []
            if max_files is not None and counts["total_seen"] >= max_files:
                partial_scan = True
                missing_reconciliation_skipped = True
                missing_reconciliation_reason = "max_files_cap"
            if not missing_reconciliation_skipped:
                for file_path in _iter_source_files(root_path, walk_errors=walk_errors):
                    if max_files is not None and counts["total_seen"] >= max_files:
                        partial_scan = True
                        missing_reconciliation_skipped = True
                        missing_reconciliation_reason = "max_files_cap"
                        break
                    state = _record_file_observation(
                        db,
                        root=root,
                        run=run,
                        root_path=root_path,
                        file_path=file_path,
                        now=now,
                        hydrated_only=hydrated_only,
                    )
                    counts["total_seen"] += 1
                    root_counts["total_seen"] += 1
                    key = f"{state}_items"
                    if key in counts:
                        counts[key] += 1
                        root_counts[key] += 1

            db.flush()
            if walk_errors and missing_reconciliation_reason != "max_files_cap":
                partial_scan = True
                missing_reconciliation_skipped = True
                missing_reconciliation_reason = "source_walk_error"
            missing = 0
            if not missing_reconciliation_skipped:
                missing = _mark_missing_items(db, root=root, run=run, now=now)
            counts["missing_items"] += missing
            root_counts["missing_items"] += missing
            root.last_checked_at = now
            root_summaries.append({
                "root_id": root.id,
                "label": root.label,
                "path_hash": root.root_path_hash,
                "partial_scan": partial_scan,
                "missing_reconciliation_skipped": missing_reconciliation_skipped,
                "missing_reconciliation_reason": missing_reconciliation_reason,
                "source_walk_error_count": len(walk_errors),
                "counts": root_counts,
            })

        db.flush()
        pending_counts = get_pending_summary(db)
        threshold_reached = pending_counts["threshold_reached"]
        run.total_seen = counts["total_seen"]
        run.new_items = counts["new_items"]
        run.changed_items = counts["changed_items"]
        run.unchanged_items = counts["unchanged_items"]
        run.deferred_items = counts["deferred_items"]
        run.failed_items = counts["failed_items"]
        run.missing_items = counts["missing_items"]
        run.pending_import_items = pending_counts["pending_import"]
        run.threshold_reached = threshold_reached
        run.status = "completed"
        run.finished_at = _utcnow()
        db.flush()
        pending = get_pending_summary(db)
        run.summary_json = {
            "root_summaries": root_summaries,
            "partial_scan": any(root_summary["partial_scan"] for root_summary in root_summaries),
            "max_files_scope": "aggregate",
            "pending_summary": pending,
            "s1_no_import_performed": True,
        }
        db.commit()
        return serialize_sync_run(run, pending_summary=pending)
    except Exception as exc:
        logger.exception("Dynamic library update check failed")
        try:
            db.rollback()
        except Exception:
            logger.exception("Dynamic library update check rollback failed")
        try:
            failed_run = db.get(DynamicSyncRun, run_id) if run_id is not None else None
            if failed_run is None:
                failed_run = db.merge(run)
            failed_run.status = "failed"
            failed_run.error_message = str(exc)[:1000]
            failed_run.finished_at = _utcnow()
            db.commit()
        except Exception:
            logger.exception("Dynamic library update check failed status could not be persisted")
            try:
                db.rollback()
            except Exception:
                logger.exception("Dynamic library failed-status rollback failed")
        raise


def serialize_sync_run(run: DynamicSyncRun, *, pending_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "id": run.id,
        "run_type": run.run_type,
        "mode": run.mode,
        "status": run.status,
        "dry_run": run.dry_run,
        "threshold": run.threshold,
        "threshold_reached": run.threshold_reached,
        "roots_checked": run.roots_checked,
        "total_seen": run.total_seen,
        "new_items": run.new_items,
        "changed_items": run.changed_items,
        "unchanged_items": run.unchanged_items,
        "deferred_items": run.deferred_items,
        "failed_items": run.failed_items,
        "missing_items": run.missing_items,
        "pending_import_items": run.pending_import_items,
        "summary": _redact_private_sync_payload(run.summary_json or {}),
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
    if pending_summary is not None:
        payload["pending_summary"] = _redact_private_sync_payload(pending_summary)
    return payload


def get_last_sync_run(db: Session) -> Optional[DynamicSyncRun]:
    return db.query(DynamicSyncRun).order_by(DynamicSyncRun.created_at.desc(), DynamicSyncRun.id.desc()).first()


def _dynamic_source_item_scope(db: Session, *, include_inactive: bool = False):
    query = db.query(DynamicSourceItem).join(DynamicSourceRoot)
    if not include_inactive:
        query = query.filter(DynamicSourceRoot.is_active == True)
    return query


def _inactive_historical_pending_summary(db: Session) -> Dict[str, int]:
    inactive = db.query(DynamicSourceItem).join(DynamicSourceRoot).filter(DynamicSourceRoot.is_active == False)
    pending_new = inactive.filter(DynamicSourceItem.import_status == "pending", DynamicSourceItem.sync_state == "new").count()
    pending_changed = inactive.filter(
        DynamicSourceItem.import_status == "pending", DynamicSourceItem.sync_state == "changed"
    ).count()
    deferred = inactive.filter(
        or_(
            DynamicSourceItem.import_status == "deferred",
            DynamicSourceItem.source_status.in_(["deferred", "failed", "missing"]),
        )
    ).count()
    return {
        "pending_new": pending_new,
        "pending_changed": pending_changed,
        "pending_import": pending_new + pending_changed,
        "pending_deferred": deferred,
        "total_pending": pending_new + pending_changed + deferred,
    }


def get_pending_summary(db: Session, *, include_inactive: bool = False) -> Dict[str, Any]:
    threshold = settings.DYNAMIC_LIBRARY_SYNC_THRESHOLD
    pending_new = (
        _dynamic_source_item_scope(db, include_inactive=include_inactive).with_entities(func.count(DynamicSourceItem.id))
        .filter(DynamicSourceItem.import_status == "pending", DynamicSourceItem.sync_state == "new")
        .scalar() or 0
    )
    pending_changed = (
        _dynamic_source_item_scope(db, include_inactive=include_inactive).with_entities(func.count(DynamicSourceItem.id))
        .filter(DynamicSourceItem.import_status == "pending", DynamicSourceItem.sync_state == "changed")
        .scalar() or 0
    )
    deferred = (
        _dynamic_source_item_scope(db, include_inactive=include_inactive).with_entities(func.count(DynamicSourceItem.id))
        .filter(
            or_(
                DynamicSourceItem.import_status == "deferred",
                DynamicSourceItem.source_status.in_(["deferred", "failed", "missing"]),
            )
        )
        .scalar() or 0
    )
    pending_import = pending_new + pending_changed
    total_pending = pending_import + deferred
    status_breakdown = dict(
        _dynamic_source_item_scope(db, include_inactive=include_inactive)
        .with_entities(DynamicSourceItem.import_status, func.count(DynamicSourceItem.id))
        .group_by(DynamicSourceItem.import_status)
        .all()
    )
    last_run = get_last_sync_run(db)
    return {
        "scope": "all_source_roots" if include_inactive else "active_source_roots",
        "inactive_historical": _inactive_historical_pending_summary(db),
        "pending_new": pending_new,
        "pending_changed": pending_changed,
        "pending_deferred": deferred,
        "pending_deferred_scope": "historical_accumulated_active_source_roots",
        "pending_deferred_includes_historical": True,
        "current_actionable_pending_import": pending_import,
        "pending_import": pending_import,
        "total_pending": total_pending,
        "threshold": threshold,
        "threshold_reached": total_pending >= threshold,
        "status_breakdown": status_breakdown,
        "last_sync_run": serialize_sync_run(last_run) if last_run else None,
        "automatic_production_writes_enabled": settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED,
        "manual_sync_execution_enabled": settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED,
    }


def get_localization_gap_summary(db: Session) -> Dict[str, Any]:
    from .tag_localization_service import get_translation_stats, list_missing_translations

    stats = get_translation_stats(db)
    missing_by_category: Dict[str, int] = {}
    for category in ["general", "meta", "character", "copyright", "artist"]:
        missing_by_category[category] = len(
            list_missing_translations(db, categories=[category], limit=100000)
        )

    worker_categories = set(settings.TAG_TRANSLATION_BG_CATEGORIES)
    unreviewed_proper_llm = (
        db.query(func.count(TagTranslation.id))
        .filter(
            TagTranslation.language == "zh-CN",
            TagTranslation.source.notin_(["manual", "static"]),
            TagTranslation.category.in_(list(PROPER_NOUN_CATEGORIES)),
            or_(TagTranslation.needs_review == True, TagTranslation.status != "reviewed"),
            TagTranslation.status != "rejected",
        )
        .scalar() or 0
    )
    return {
        "total_tags": stats.get("total_tags", 0),
        "total_covered": stats.get("total_covered", 0),
        "missing": stats.get("missing", 0),
        "needs_review": stats.get("needs_review", 0),
        "missing_by_category": missing_by_category,
        "general_meta_missing": sum(missing_by_category.get(c, 0) for c in GENERAL_LOCALIZATION_CATEGORIES),
        "proper_noun_missing": sum(missing_by_category.get(c, 0) for c in PROPER_NOUN_CATEGORIES),
        "worker_categories": sorted(worker_categories),
        "worker_excludes_proper_nouns": worker_categories.isdisjoint(PROPER_NOUN_CATEGORIES),
        "unreviewed_proper_noun_llm_aliases": unreviewed_proper_llm,
        "proper_noun_policy": {
            "general_meta_worker_allowed": True,
            "proper_noun_llm_requires_review": True,
            "search_alias_trust_sources": ["manual", "static", "operator_reviewed"],
            "entity_alias_resolver_separate": True,
        },
    }


def get_ai_localization_readiness(db: Session) -> Dict[str, Any]:
    localization_gap = get_localization_gap_summary(db)
    background_categories = settings.TAG_TRANSLATION_BG_CATEGORIES
    auto_path_available = settings.TAG_TRANSLATION_AUTO_ENABLED and settings.TAG_TRANSLATION_LLM_ENABLED
    worker_path_available = settings.TAG_TRANSLATION_BG_ENABLED and settings.TAG_TRANSLATION_LLM_ENABLED
    llm_provider_configured = bool(
        settings.TAG_TRANSLATION_LLM_API_KEY
        and settings.TAG_TRANSLATION_LLM_MODEL
        and settings.TAG_TRANSLATION_LLM_BASE_URL
    )
    fallback_provider_configured = bool(
        settings.TAG_TRANSLATION_LLM_FALLBACK_ENABLED
        and settings.TAG_TRANSLATION_LLM_FALLBACK_API_KEY
        and settings.TAG_TRANSLATION_LLM_FALLBACK_MODEL
        and settings.TAG_TRANSLATION_LLM_FALLBACK_BASE_URL
    )
    return {
        "ai_tagging": {
            "enabled": settings.AI_TAGGING_ENABLED,
            "batch_max_items": settings.AI_TAGGING_BATCH_MAX_ITEMS,
            "model_name": settings.AI_MODEL_NAME,
            "auto_tag_after_import": settings.AI_AUTO_TAG_AFTER_IMPORT,
            "auto_tag_after_import_max_items": settings.AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS,
            "auto_tag_after_import_only_new": settings.AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW,
            "auto_tag_after_import_dry_run": settings.AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN,
            "auto_tagging_localization_enabled": settings.AI_TAGGING_AUTO_LOCALIZATION,
        },
        "tag_localization": {
            "llm_enabled": settings.TAG_TRANSLATION_LLM_ENABLED,
            "llm_provider_configured": llm_provider_configured,
            "llm_fallback_provider_configured": fallback_provider_configured,
            "auto_enabled": settings.TAG_TRANSLATION_AUTO_ENABLED,
            "background_enabled": settings.TAG_TRANSLATION_BG_ENABLED,
            "background_categories": background_categories,
            "background_daily_limit": settings.TAG_TRANSLATION_BG_DAILY_LIMIT,
            "background_batch_size": settings.TAG_TRANSLATION_BG_BATCH_SIZE,
            "background_max_per_run": settings.TAG_TRANSLATION_BG_MAX_PER_RUN,
            "auto_or_background_path_available": auto_path_available or worker_path_available,
            "ai_to_localization_chain_ready": (
                settings.AI_TAGGING_AUTO_LOCALIZATION
                and settings.TAG_TRANSLATION_LLM_ENABLED
                and (settings.TAG_TRANSLATION_AUTO_ENABLED or settings.TAG_TRANSLATION_BG_ENABLED)
            ),
            "gap_summary": localization_gap,
        },
        "integration_chain": [
            "baseline import",
            "AI tagging job",
            "new tags collected",
            "_schedule_localization",
            "background worker / auto translate",
            "blombooru_tag_translations",
            "frontend Chinese display and trusted search aliases",
        ],
    }


def get_production_readiness(db: Session) -> Dict[str, Any]:
    roots = list_source_roots(db)
    pending = get_pending_summary(db)
    ai_localization = get_ai_localization_readiness(db)
    blockers: List[str] = []
    warnings: List[str] = []
    manual_execute_blockers: List[Dict[str, str]] = []
    manual_execute_warnings: List[Dict[str, str]] = []
    background_warnings: List[Dict[str, str]] = []

    if not roots:
        blockers.append("no_dynamic_source_roots_configured")
        manual_execute_blockers.append(
            {
                "code": "no_dynamic_source_roots_configured",
                "label": "No source root is registered.",
                "scope": "manual_execute",
            }
        )
    if not settings.STORAGE_ROOT_EXPLICITLY_SET:
        warnings.append("VIOLET_STORAGE_ROOT_not_explicitly_set")
    if settings.DB_NAME == "blombooru_test" or settings.IS_TEST_ENV:
        warnings.append("running_in_test_environment")
    if not settings.AI_TAGGING_ENABLED:
        blockers.append("AI_TAGGING_ENABLED_false")
        manual_execute_blockers.append(
            {
                "code": "AI_TAGGING_ENABLED_false",
                "label": "AI tagging is disabled for this server, so a manual E2E run cannot complete AI tagging.",
                "scope": "manual_execute",
            }
        )
    if not settings.CONTENT_CLASSIFICATION_ENABLED:
        blockers.append("CONTENT_CLASSIFICATION_ENABLED_false")
        manual_execute_blockers.append(
            {
                "code": "CONTENT_CLASSIFICATION_ENABLED_false",
                "label": "Content classification is disabled for this server, so a manual E2E run cannot complete classification.",
                "scope": "manual_execute",
            }
        )
    if not settings.AI_TAGGING_AUTO_LOCALIZATION:
        background_warnings.append(
            {
                "code": "AI_TAGGING_AUTO_LOCALIZATION_false",
                "label": "Background AI-to-localization chaining is disabled; this is expected because manual execute finalizes localization.",
                "scope": "background_only",
            }
        )
    if not settings.TAG_TRANSLATION_LLM_ENABLED:
        blockers.append("TAG_TRANSLATION_LLM_ENABLED_false")
        manual_execute_blockers.append(
            {
                "code": "TAG_TRANSLATION_LLM_ENABLED_false",
                "label": "LLM translation is disabled, so a manual E2E run cannot localize newly discovered localizable tags.",
                "scope": "manual_execute",
            }
        )
    elif not (
        ai_localization["tag_localization"]["llm_provider_configured"]
        or ai_localization["tag_localization"]["llm_fallback_provider_configured"]
    ):
        blockers.append("TAG_TRANSLATION_LLM_PROVIDER_unconfigured")
        manual_execute_blockers.append(
            {
                "code": "TAG_TRANSLATION_LLM_PROVIDER_unconfigured",
                "label": "LLM translation is enabled but provider credentials/model/base URL are not configured for this production profile.",
                "scope": "manual_execute",
            }
        )
    if str(settings.CONTENT_CLASSIFICATION_METHOD or "").lower() != "clip":
        blockers.append("CONTENT_CLASSIFICATION_METHOD_not_clip")
        manual_execute_blockers.append(
            {
                "code": "CONTENT_CLASSIFICATION_METHOD_not_clip",
                "label": "Manual E2E requires classification-before-AI gating; set CONTENT_CLASSIFICATION_METHOD=clip for production acceptance.",
                "scope": "manual_execute",
            }
        )
    if not (settings.TAG_TRANSLATION_AUTO_ENABLED or settings.TAG_TRANSLATION_BG_ENABLED):
        background_warnings.append(
            {
                "code": "tag_translation_auto_and_background_disabled",
                "label": "Automatic/background translation workers are disabled; this is expected for manual-only sync.",
                "scope": "background_only",
            }
        )
    if settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED:
        warnings.append("automatic_dynamic_sync_enabled_requires_explicit_operator_review")
    if not settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED:
        warnings.append("manual_pending_sync_execution_disabled_by_default")
    if not ai_localization["tag_localization"]["gap_summary"]["worker_excludes_proper_nouns"]:
        blockers.append("background_translation_categories_include_proper_nouns")
    if ai_localization["tag_localization"]["gap_summary"]["unreviewed_proper_noun_llm_aliases"] > 0:
        blockers.append("unreviewed_proper_noun_llm_aliases_present")
        manual_execute_warnings.append(
            {
                "code": "unreviewed_proper_noun_llm_aliases_present",
                "label": "Unreviewed proper-noun translations exist; they do not create Entity truth, but search aliases still need review.",
                "scope": "localization_review",
            }
        )

    return {
        "production_settings": {
            "violet_env": settings.VIOLET_ENV,
            "db_name": settings.DB_NAME,
            "storage_root_explicitly_set": settings.STORAGE_ROOT_EXPLICITLY_SET,
            "auto_sync_enabled": settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED,
            "manual_sync_enabled": settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED,
            "manual_sync_execute_enabled": settings.DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED,
            "classification_enabled": settings.CONTENT_CLASSIFICATION_ENABLED,
            "content_classification_method": settings.CONTENT_CLASSIFICATION_METHOD,
            "ai_tagging_enabled": settings.AI_TAGGING_ENABLED,
            "tag_translation_llm_enabled": settings.TAG_TRANSLATION_LLM_ENABLED,
            "tag_translation_llm_provider_configured": bool(
                ai_localization["tag_localization"]["llm_provider_configured"]
                or ai_localization["tag_localization"]["llm_fallback_provider_configured"]
            ),
            "cloud_placeholder_hydration_enabled": True,
        },
        "dynamic_sync_state_ready": True,
        "manual_update_ready": bool(roots),
        "manual_sync_execution_ready": settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED,
        "threshold_policy": {
            "default_threshold": settings.DYNAMIC_LIBRARY_SYNC_THRESHOLD,
            "threshold_reached": pending["threshold_reached"],
        },
        "source_roots": [serialize_source_root(root) for root in roots],
        "pending_summary": pending,
        "ai_localization_readiness": ai_localization,
        "manual_sync_operator_readiness": {
            "manual_execute_ready": (
                bool(roots)
                and settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED
                and settings.DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED
                and settings.AI_TAGGING_ENABLED
                and settings.CONTENT_CLASSIFICATION_ENABLED
                and str(settings.CONTENT_CLASSIFICATION_METHOD or "").lower() == "clip"
                and settings.TAG_TRANSLATION_LLM_ENABLED
                and (
                    ai_localization["tag_localization"]["llm_provider_configured"]
                    or ai_localization["tag_localization"]["llm_fallback_provider_configured"]
                )
            ),
            "manual_execute_blockers": manual_execute_blockers,
            "manual_execute_warnings": manual_execute_warnings,
            "background_warnings": background_warnings,
            "historical_deferred_inventory_is_actionable": False,
            "cloud_placeholder_hydration_policy": "cloud_aware_non_destructive_read",
            "normal_operator_plan_endpoint": "POST /api/admin/dynamic-library-sync/manual-sync/plan",
            "legacy_update_check_endpoint": "POST /api/admin/dynamic-library-sync/check",
        },
        "blockers_before_s2": blockers,
        "warnings": warnings,
        "s2_ready": not blockers,
    }


def get_dashboard_state(db: Session) -> Dict[str, Any]:
    roots = list_source_roots(db)
    pending = get_pending_summary(db)
    last_run = get_last_sync_run(db)
    readiness = get_production_readiness(db)
    return {
        "source_roots": [serialize_source_root(root) for root in roots],
        "pending_summary": pending,
        "last_sync_run": serialize_sync_run(last_run) if last_run else None,
        "readiness": readiness,
        "default_off_policy": {
            "manual_check_available": True,
            "pending_count_visible": True,
            "threshold_visible": True,
            "threshold": settings.DYNAMIC_LIBRARY_SYNC_THRESHOLD,
            "automatic_production_writes_enabled": settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED,
            "manual_sync_execution_enabled": settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED,
        },
    }


def assert_manual_sync_allowed() -> None:
    if not settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED:
        raise PermissionError(
            "Manual pending-item sync execution is disabled by default. "
            "Set DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED=true only after S2 execution approval."
        )
