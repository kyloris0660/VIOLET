"""S3A-M1 guarded manual sync execution.

This module is deliberately manual-only. It never schedules itself, never
starts from application startup, and never mutates source files.
"""

from __future__ import annotations

import shutil
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..models import DynamicSourceItem, DynamicSourceRoot, DynamicSyncRun, DynamicSyncRunItem
from ..routes.media import process_and_save_media
from ..schemas import RatingEnum
from ..utils.logger import logger
from ..utils.media_helpers import get_unique_filename
from .dynamic_library_sync_service import (
    MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
    S3A_M1_MANUAL_EXECUTE_CONFIRMATION_PREFIX,
    S3A_M1_PRODUCTION_EXECUTE_CONFIRMATION_PREFIX,
    _hash_text,
    _manual_public_reason_code,
    _metadata_for_path,
    _normalize_relative_path,
    _relative_identity_and_preflight_reason,
    _utcnow,
    manual_sync_execute_confirmation_phrase,
    plan_manual_sync_dry_run,
    serialize_sync_run,
    validate_source_root_path,
)


class ManualSyncExecuteError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


_active_execute_lock = threading.Lock()
_active_execute_run_id: Optional[int] = None
_cancel_flags: Dict[int, bool] = {}


def is_manual_sync_execute_active() -> bool:
    with _active_execute_lock:
        return _active_execute_run_id is not None


def request_manual_sync_execute_cancel(run_id: int) -> None:
    with _active_execute_lock:
        _cancel_flags[int(run_id)] = True


def _is_cancel_requested(run_id: int) -> bool:
    with _active_execute_lock:
        return bool(_cancel_flags.get(int(run_id), False))


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _public_request_payload(
    *,
    root_id: int,
    max_files: Optional[int],
    hydrated_only: bool,
    stable_age_seconds: Optional[float],
    expected_plan_hash: str,
    plan_created_at: str,
    production_acceptance_approved: bool,
) -> Dict[str, Any]:
    return {
        "root_id": root_id,
        "max_files": max_files,
        "hydrated_only": hydrated_only,
        "stable_age_seconds": stable_age_seconds,
        "expected_plan_hash": expected_plan_hash,
        "plan_created_at": plan_created_at,
        "production_acceptance_approved": bool(production_acceptance_approved),
        "trigger_type": "manual_operator",
    }


def _verify_execute_gates(
    *,
    plan: Dict[str, Any],
    expected_plan_hash: str,
    confirmation_phrase: str,
    plan_created_at: str,
    hydrated_only: bool,
    production_acceptance_approved: bool,
) -> None:
    if not settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED:
        raise ManualSyncExecuteError(
            "manual_sync_execute_disabled",
            "Manual sync execute is disabled. Set DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED=true only for an approved bounded run.",
            status_code=409,
        )
    if settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED or settings.S3B_UNATTENDED_SYNC_ENABLED:
        raise ManualSyncExecuteError(
            "unattended_sync_flag_enabled",
            "Automatic or unattended sync flags must remain disabled before manual execute.",
            status_code=409,
        )
    if not hydrated_only:
        raise ManualSyncExecuteError(
            "hydrated_only_required",
            "Manual sync execute requires hydrated_only=true.",
            status_code=400,
        )

    plan_hash = str((plan.get("integrity") or {}).get("plan_hash") or "")
    if not expected_plan_hash or expected_plan_hash != plan_hash:
        raise ManualSyncExecuteError(
            "stale_or_mismatched_plan_hash",
            "Plan hash does not match the current dry-run plan. Re-run dry-run before execute.",
            status_code=409,
        )

    created = _parse_datetime(plan_created_at)
    if created is None:
        raise ManualSyncExecuteError(
            "plan_created_at_required",
            "plan_created_at must be the dry-run plan job.created_at value.",
            status_code=400,
        )
    age = (_utcnow() - created).total_seconds()
    if age < 0 or age > MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS:
        raise ManualSyncExecuteError(
            "stale_plan_expired",
            "Dry-run plan is stale. Re-run dry-run before execute.",
            status_code=409,
        )

    if settings.IS_PRODUCTION_ENV:
        expected_phrase = manual_sync_execute_confirmation_phrase(plan_hash, production=True)
        if not production_acceptance_approved or confirmation_phrase != expected_phrase:
            raise ManualSyncExecuteError(
                "production_acceptance_approval_required",
                "Production execute requires the production confirmation phrase after a fresh dry-run plan.",
                status_code=409,
            )
    else:
        expected_phrase = manual_sync_execute_confirmation_phrase(plan_hash)
        if production_acceptance_approved:
            raise ManualSyncExecuteError(
                "production_acceptance_not_allowed_outside_production",
                "production_acceptance_approved is only valid in VIOLET_ENV=production.",
                status_code=400,
            )
        if confirmation_phrase != expected_phrase:
            raise ManualSyncExecuteError(
                "manual_execute_confirmation_required",
                "Manual sync execute requires the exact confirmation phrase from the dry-run plan.",
                status_code=409,
            )


def _verify_execute_recheck(
    *,
    plan: Dict[str, Any],
    expected_plan_hash: str,
    hydrated_only: bool,
    production_acceptance_approved: bool,
) -> None:
    if not settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED:
        raise ManualSyncExecuteError(
            "manual_sync_execute_disabled",
            "Manual sync execute became disabled before the run started.",
            status_code=409,
        )
    if settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED or settings.S3B_UNATTENDED_SYNC_ENABLED:
        raise ManualSyncExecuteError(
            "unattended_sync_flag_enabled",
            "Automatic or unattended sync flags became enabled before execute.",
            status_code=409,
        )
    if not hydrated_only:
        raise ManualSyncExecuteError("hydrated_only_required", "Manual sync execute requires hydrated_only=true.")
    current_hash = str((plan.get("integrity") or {}).get("plan_hash") or "")
    if current_hash != expected_plan_hash:
        raise ManualSyncExecuteError(
            "stale_or_mismatched_plan_hash",
            "Source contents changed after enqueue. Re-run dry-run before execute.",
            status_code=409,
        )
    if settings.IS_PRODUCTION_ENV and not production_acceptance_approved:
        raise ManualSyncExecuteError(
            "production_acceptance_approval_required",
            "Production execute requires separate operator approval.",
            status_code=409,
        )


def _plan_for_root(
    db: Session,
    *,
    root_id: int,
    max_files: Optional[int],
    hydrated_only: bool,
    stable_age_seconds: Optional[float],
    include_private_details: bool,
) -> tuple[DynamicSourceRoot, Path, Dict[str, Any]]:
    root = db.get(DynamicSourceRoot, root_id)
    if root is None or not root.is_active:
        raise ManualSyncExecuteError(
            "source_root_not_found",
            "Manual sync execute requires an active registered source root.",
            status_code=404,
        )
    source_path = validate_source_root_path(root.root_path)
    plan = plan_manual_sync_dry_run(
        db,
        source_path=source_path,
        source_record_id=root.id,
        max_files=max_files,
        hydrated_only=hydrated_only,
        stable_age_seconds=stable_age_seconds,
        include_private_details=include_private_details,
    )
    return root, source_path, plan


def validate_manual_sync_execute_request(
    db: Session,
    *,
    root_id: int,
    max_files: Optional[int],
    hydrated_only: bool,
    stable_age_seconds: Optional[float],
    expected_plan_hash: str,
    confirmation_phrase: str,
    plan_created_at: str,
    production_acceptance_approved: bool = False,
) -> Dict[str, Any]:
    _root, _source_path, plan = _plan_for_root(
        db,
        root_id=root_id,
        max_files=max_files,
        hydrated_only=hydrated_only,
        stable_age_seconds=stable_age_seconds,
        include_private_details=False,
    )
    _verify_execute_gates(
        plan=plan,
        expected_plan_hash=expected_plan_hash,
        confirmation_phrase=confirmation_phrase,
        plan_created_at=plan_created_at,
        hydrated_only=hydrated_only,
        production_acceptance_approved=production_acceptance_approved,
    )
    return plan


def create_manual_sync_execute_run(
    db: Session,
    *,
    root_id: int,
    max_files: Optional[int],
    hydrated_only: bool,
    stable_age_seconds: Optional[float],
    expected_plan_hash: str,
    confirmation_phrase: str,
    plan_created_at: str,
    production_acceptance_approved: bool = False,
) -> DynamicSyncRun:
    if is_manual_sync_execute_active():
        raise ManualSyncExecuteError(
            "manual_sync_execute_already_active",
            "Another manual sync execute run is active.",
            status_code=409,
        )
    active_run = _find_active_manual_sync_execute_run(db)
    if active_run is not None:
        raise ManualSyncExecuteError(
            "manual_sync_execute_already_active",
            "Another manual sync execute run is pending or active.",
            status_code=409,
        )

    plan = validate_manual_sync_execute_request(
        db,
        root_id=root_id,
        max_files=max_files,
        hydrated_only=hydrated_only,
        stable_age_seconds=stable_age_seconds,
        expected_plan_hash=expected_plan_hash,
        confirmation_phrase=confirmation_phrase,
        plan_created_at=plan_created_at,
        production_acceptance_approved=production_acceptance_approved,
    )
    now = _utcnow()
    stage_rows = [
        {"name": name, "status": "pending", "processed": 0, "failed": 0}
        for name in ("candidate_discovery", "import", "classification", "ai_tagging", "localization", "summary")
    ]
    run = DynamicSyncRun(
        run_type="manual_sync_execute",
        mode="production_acceptance" if settings.IS_PRODUCTION_ENV else "dev_test_execute",
        status="pending",
        dry_run=False,
        roots_checked=1,
        total_seen=int((plan.get("counts") or {}).get("total_seen") or 0),
        pending_import_items=int((plan.get("counts") or {}).get("estimated_import_count") or 0),
        started_at=now,
        summary_json={
            "phase": "S3A-M1",
            "manual_sync_execute": {
                "status": "pending",
                "current_stage": "queued",
                "request": _public_request_payload(
                    root_id=root_id,
                    max_files=max_files,
                    hydrated_only=hydrated_only,
                    stable_age_seconds=stable_age_seconds,
                    expected_plan_hash=expected_plan_hash,
                    plan_created_at=plan_created_at,
                    production_acceptance_approved=production_acceptance_approved,
                ),
                "plan": plan,
                "stage_rows": stage_rows,
                "outcome_counts": {},
                "safety": {
                    "manual_trigger_only": True,
                    "automatic_sync_enabled": False,
                    "scheduled_sync_enabled": False,
                    "startup_sync_enabled": False,
                    "source_mutation_performed": False,
                    "local_files_only_ai": True,
                    "llm_calls_enabled": False,
                    "production_acceptance_pending": not production_acceptance_approved,
                    "confirmation_prefix": S3A_M1_MANUAL_EXECUTE_CONFIRMATION_PREFIX,
                    "production_confirmation_prefix": S3A_M1_PRODUCTION_EXECUTE_CONFIRMATION_PREFIX,
                },
            },
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def start_manual_sync_execute_run(run_id: int) -> None:
    thread = threading.Thread(target=_run_manual_sync_execute_thread, args=(run_id,), daemon=True)
    thread.start()


def _run_manual_sync_execute_thread(run_id: int) -> None:
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        execute_manual_sync_run(db, run_id=run_id)
    finally:
        db.close()


def _set_stage(summary: Dict[str, Any], name: str, **updates: Any) -> Dict[str, Any]:
    payload = dict(summary)
    execute = dict(payload.get("manual_sync_execute") or {})
    rows = [dict(row) for row in execute.get("stage_rows") or []]
    for row in rows:
        if row.get("name") == name:
            row.update(updates)
            break
    execute["stage_rows"] = rows
    execute["current_stage"] = name
    payload["manual_sync_execute"] = execute
    return payload


def _update_execute_summary(run: DynamicSyncRun, **updates: Any) -> None:
    payload = dict(run.summary_json or {})
    execute = dict(payload.get("manual_sync_execute") or {})
    execute.update(updates)
    payload["manual_sync_execute"] = execute
    run.summary_json = payload


def _mark_manual_sync_run_failed(
    db: Session,
    run: DynamicSyncRun,
    *,
    code: str,
    message: str,
) -> DynamicSyncRun:
    run.status = "failed"
    run.error_message = message[:1000]
    run.finished_at = _utcnow()
    _update_execute_summary(run, status="failed", error_code=code)
    db.commit()
    db.refresh(run)
    return run


def _find_active_manual_sync_execute_run(db: Session) -> Optional[DynamicSyncRun]:
    return (
        db.query(DynamicSyncRun)
        .filter(
            DynamicSyncRun.run_type == "manual_sync_execute",
            DynamicSyncRun.status.in_(("pending", "running", "cancelling")),
        )
        .order_by(DynamicSyncRun.created_at.asc(), DynamicSyncRun.id.asc())
        .first()
    )


def _safe_source_file(root_path: Path, relative_path: str) -> Path:
    candidate = (root_path / relative_path).resolve()
    try:
        candidate.relative_to(root_path.resolve())
    except ValueError as exc:
        raise ManualSyncExecuteError("source_path_escape", "Source item escaped the registered root.") from exc
    return candidate


def _get_or_create_source_item(
    db: Session,
    *,
    root: DynamicSourceRoot,
    run: DynamicSyncRun,
    relative_path: str,
    metadata: Dict[str, Any],
    content_hash: Optional[str],
) -> DynamicSourceItem:
    rel_hash = _hash_text(_normalize_relative_path(Path(relative_path)))
    item = (
        db.query(DynamicSourceItem)
        .filter(
            DynamicSourceItem.source_root_id == root.id,
            DynamicSourceItem.relative_path_hash == rel_hash,
        )
        .first()
    )
    now = _utcnow()
    if item is None:
        item = DynamicSourceItem(
            source_root_id=root.id,
            relative_path=relative_path,
            relative_path_hash=rel_hash,
            first_seen_at=now,
        )
        db.add(item)
        db.flush()
    item.relative_path = relative_path
    item.file_size = metadata.get("file_size")
    item.mtime = metadata.get("mtime")
    item.mtime_ns = metadata.get("mtime_ns")
    item.content_hash = content_hash
    item.source_status = "available"
    item.last_checked_at = now
    item.last_seen_at = now
    item.last_sync_run_id = run.id
    item.last_seen_run_id = run.id
    item.metadata_json = {
        "suffix": metadata.get("suffix"),
        "content_hash_computed": bool(content_hash),
    }
    return item


def _record_run_item(
    db: Session,
    *,
    run: DynamicSyncRun,
    item: DynamicSourceItem,
    state: str,
    action: str,
    reason: Optional[str],
    eligible: bool,
    bytes_copied: int = 0,
    media_id: Optional[int] = None,
    current_metadata: Optional[Dict[str, Any]] = None,
) -> DynamicSyncRunItem:
    run_item = DynamicSyncRunItem(
        sync_run_id=run.id,
        source_item_id=item.id,
        item_state=state,
        action=action,
        reason=reason,
        eligible_for_db_import=eligible,
        bytes_copied=bytes_copied,
        media_id=media_id,
        current_metadata_json=current_metadata or {},
    )
    db.add(run_item)
    return run_item


def _mark_item_skipped(
    db: Session,
    *,
    run: DynamicSyncRun,
    item: DynamicSourceItem,
    state: str,
    reason: Optional[str],
    metadata: Dict[str, Any],
) -> None:
    item.sync_state = state
    item.import_status = "deferred"
    item.classification_status = "deferred"
    item.ai_tagging_status = "deferred"
    item.localization_status = "deferred"
    item.failure_reason = reason if state == "failed" else None
    item.deferred_reason = reason if state != "failed" else None
    _record_run_item(
        db,
        run=run,
        item=item,
        state=state,
        action="skip",
        reason=reason,
        eligible=False,
        current_metadata=metadata,
    )


def _copy_and_import_media(db: Session, source_file: Path) -> tuple[int, int]:
    settings.ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)
    unique_filename = get_unique_filename(settings.ORIGINAL_DIR, source_file.name)
    destination = settings.ORIGINAL_DIR / unique_filename
    copied = False
    try:
        shutil.copy2(source_file, destination)
        copied = True
        bytes_copied = destination.stat().st_size
        media = process_and_save_media(
            db=db,
            file_path=destination,
            unique_filename=unique_filename,
            rating=RatingEnum.safe,
            tags="",
            album_ids=None,
            source=None,
            category_hints=None,
        )
        return int(media.id), int(bytes_copied)
    except HTTPException:
        if copied:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to remove copied manual sync media after HTTPException: %s", destination)
        raise
    except Exception:
        if copied:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to remove copied manual sync media after error: %s", destination)
        raise


def _classify_imported_media(db: Session, media_id: int) -> Dict[str, Any]:
    if not settings.CONTENT_CLASSIFICATION_ENABLED:
        return {"media_id": media_id, "skipped": True, "reason": "classification_disabled"}
    from .content_classifier import classify_media

    return classify_media(db, media_id)


def _ai_tag_imported_media(db: Session, media_id: int) -> Dict[str, Any]:
    if not settings.AI_TAGGING_ENABLED:
        return {"media_id": media_id, "skipped": True, "reason": "ai_tagging_disabled"}
    from .ai_tagging_service import run_ai_tagging

    return run_ai_tagging(
        db,
        media_id,
        dry_run=False,
        local_files_only=True,
        schedule_localization=False,
    )


def execute_manual_sync_run(db: Session, *, run_id: int) -> Dict[str, Any]:
    global _active_execute_run_id

    run = db.get(DynamicSyncRun, run_id)
    if run is None:
        with _active_execute_lock:
            if _active_execute_run_id == run_id:
                _active_execute_run_id = None
            _cancel_flags.pop(run_id, None)
        raise ManualSyncExecuteError("manual_sync_run_not_found", "Manual sync run not found.", status_code=404)

    active_conflict = False
    with _active_execute_lock:
        if _active_execute_run_id is not None and _active_execute_run_id != run_id:
            active_conflict = True
        else:
            _active_execute_run_id = run_id
            _cancel_flags.setdefault(run_id, False)

    if active_conflict:
        exc = ManualSyncExecuteError(
            "manual_sync_execute_already_active",
            "Another manual sync execute run is active.",
            status_code=409,
        )
        failed = _mark_manual_sync_run_failed(db, run, code=exc.code, message=str(exc))
        return serialize_manual_sync_execute_run(failed)

    counts: Counter[str] = Counter()
    imported_media_ids: List[int] = []
    ai_provenance: Optional[Dict[str, Any]] = None

    try:
        summary = run.summary_json or {}
        request = ((summary.get("manual_sync_execute") or {}).get("request") or {})
        root_id = int(request.get("root_id") or 0)
        root, root_path, plan = _plan_for_root(
            db,
            root_id=root_id,
            max_files=request.get("max_files"),
            hydrated_only=bool(request.get("hydrated_only", True)),
            stable_age_seconds=request.get("stable_age_seconds"),
            include_private_details=True,
        )
        _verify_execute_recheck(
            plan=plan,
            expected_plan_hash=str(request.get("expected_plan_hash") or ""),
            hydrated_only=bool(request.get("hydrated_only", True)),
            production_acceptance_approved=bool(request.get("production_acceptance_approved")),
        )
    except ManualSyncExecuteError as exc:
        failed = _mark_manual_sync_run_failed(db, run, code=exc.code, message=str(exc))
        with _active_execute_lock:
            if _active_execute_run_id == run_id:
                _active_execute_run_id = None
            _cancel_flags.pop(run_id, None)
        return serialize_manual_sync_execute_run(failed)
    except Exception as exc:
        failed = _mark_manual_sync_run_failed(
            db,
            run,
            code="manual_sync_execute_prepare_failed",
            message=str(exc),
        )
        with _active_execute_lock:
            if _active_execute_run_id == run_id:
                _active_execute_run_id = None
            _cancel_flags.pop(run_id, None)
        return serialize_manual_sync_execute_run(failed)

    # The confirmation phrase is not persisted after the enqueue request. The
    # run could only be created after validation, so subsequent rechecks compare
    # the freshly generated hash and the persisted request scope.
    run.status = "running"
    run.started_at = _utcnow()
    run.summary_json = _set_stage(run.summary_json or {}, "candidate_discovery", status="completed")
    db.commit()

    try:
        private_items = ((plan.get("private_details") or {}).get("items") or [])
        run.total_seen = len(private_items)
        for plan_item in private_items:
            if _is_cancel_requested(run_id):
                run.status = "cancelled"
                break

            relative_path = str(plan_item.get("relative_path") or "")
            source_file = _safe_source_file(root_path, relative_path)
            rel, preflight_reason = _relative_identity_and_preflight_reason(root_path, source_file)
            metadata = _metadata_for_path(source_file, follow_symlinks=not bool(preflight_reason))
            item = _get_or_create_source_item(
                db,
                root=root,
                run=run,
                relative_path=rel,
                metadata=metadata,
                content_hash=None,
            )

            state = str(plan_item.get("state") or "failed")
            reason = _manual_public_reason_code(plan_item.get("reason"))
            if state != "import_planned":
                _mark_item_skipped(db, run=run, item=item, state=state, reason=reason, metadata=metadata)
                counts[state] += 1
                db.commit()
                continue

            try:
                media_id, bytes_copied = _copy_and_import_media(db, source_file)
                imported_media_ids.append(media_id)
                item.media_id = media_id
                item.sync_state = "imported"
                item.import_status = "imported"
                item.classification_status = "pending"
                item.ai_tagging_status = "pending"
                item.localization_status = "waiting_ai_tags"
                item.last_imported_at = _utcnow()
                _record_run_item(
                    db,
                    run=run,
                    item=item,
                    state="imported_in_test" if not settings.IS_PRODUCTION_ENV else "imported",
                    action="import",
                    reason=None,
                    eligible=True,
                    bytes_copied=bytes_copied,
                    media_id=media_id,
                    current_metadata={**metadata, "safe_label": plan_item.get("safe_label")},
                )
                counts["imported"] += 1
                run.new_items = int(counts["imported"])
                db.commit()
            except HTTPException as exc:
                db.rollback()
                duplicate = exc.status_code == 409
                item = _get_or_create_source_item(
                    db,
                    root=root,
                    run=run,
                    relative_path=rel,
                    metadata=metadata,
                    content_hash=None,
                )
                item.sync_state = "skipped_existing_media" if duplicate else "failed"
                item.import_status = "deferred" if duplicate else "failed"
                item.classification_status = "deferred"
                item.ai_tagging_status = "deferred"
                item.localization_status = "deferred"
                item.failure_reason = None if duplicate else "import_failed"
                item.deferred_reason = "existing_media_hash" if duplicate else None
                _record_run_item(
                    db,
                    run=run,
                    item=item,
                    state=item.sync_state,
                    action="skip" if duplicate else "import",
                    reason="existing_media_hash" if duplicate else "import_failed",
                    eligible=False,
                    current_metadata=metadata,
                )
                counts[item.sync_state] += 1
                db.commit()
            except Exception as exc:
                db.rollback()
                item = _get_or_create_source_item(
                    db,
                    root=root,
                    run=run,
                    relative_path=rel,
                    metadata=metadata,
                    content_hash=None,
                )
                item.sync_state = "failed"
                item.import_status = "failed"
                item.classification_status = "deferred"
                item.ai_tagging_status = "deferred"
                item.localization_status = "deferred"
                item.failure_reason = "import_failed"
                _record_run_item(
                    db,
                    run=run,
                    item=item,
                    state="failed",
                    action="import",
                    reason="import_failed",
                    eligible=False,
                    current_metadata={**metadata, "error": str(exc)[:200]},
                )
                counts["failed"] += 1
                db.commit()

        run.summary_json = _set_stage(run.summary_json or {}, "import", status="completed", processed=len(imported_media_ids), failed=int(counts["failed"]))
        db.commit()

        for media_id in imported_media_ids:
            if _is_cancel_requested(run_id):
                run.status = "cancelled"
                break
            result = _classify_imported_media(db, media_id)
            item = db.query(DynamicSourceItem).filter(DynamicSourceItem.media_id == media_id).first()
            if item:
                if result.get("error"):
                    item.classification_status = "failed"
                    counts["classification_failed"] += 1
                elif result.get("skipped"):
                    item.classification_status = f"skipped_{result.get('reason', 'unknown')}"[:50]
                    counts["classification_skipped"] += 1
                else:
                    item.classification_status = "classified"
                    counts["classified"] += 1
            db.commit()
        run.summary_json = _set_stage(run.summary_json or {}, "classification", status="completed", processed=len(imported_media_ids), failed=int(counts["classification_failed"]))
        db.commit()

        for media_id in imported_media_ids:
            if _is_cancel_requested(run_id):
                run.status = "cancelled"
                break
            result = _ai_tag_imported_media(db, media_id)
            if ai_provenance is None and result.get("provenance"):
                ai_provenance = result.get("provenance")
            item = db.query(DynamicSourceItem).filter(DynamicSourceItem.media_id == media_id).first()
            if item:
                if result.get("error"):
                    item.ai_tagging_status = "failed"
                    counts["ai_tagging_failed"] += 1
                elif result.get("skipped"):
                    item.ai_tagging_status = f"skipped_{result.get('reason', 'unknown')}"[:50]
                    counts["ai_tagging_skipped"] += 1
                else:
                    item.ai_tagging_status = "ai_tagged"
                    counts["ai_tagged"] += 1
                item.localization_status = "skipped_llm_calls_forbidden_current_phase"
            db.commit()
        run.summary_json = _set_stage(run.summary_json or {}, "ai_tagging", status="completed", processed=len(imported_media_ids), failed=int(counts["ai_tagging_failed"]))
        run.summary_json = _set_stage(run.summary_json or {}, "localization", status="completed", processed=0, failed=0)

        run.failed_items = int(counts["failed"] + counts["classification_failed"] + counts["ai_tagging_failed"])
        run.pending_import_items = 0
        run.summary_json = _set_stage(run.summary_json or {}, "summary", status="completed", processed=1, failed=0)
        _update_execute_summary(
            run,
            status="cancelled" if run.status == "cancelled" else "completed",
            current_stage="summary",
            outcome_counts=dict(sorted(counts.items())),
            imported_media_ids=imported_media_ids,
            ai_provider_provenance=ai_provenance,
            source_mutation_performed=False,
            app_storage_mutation_performed=bool(imported_media_ids),
            llm_calls_performed=False,
        )
        if run.status != "cancelled":
            run.status = "completed"
        run.finished_at = _utcnow()
        db.commit()
        db.refresh(run)
        return serialize_manual_sync_execute_run(run)
    except Exception as exc:
        logger.exception("Manual sync execute run failed")
        db.rollback()
        failed = db.get(DynamicSyncRun, run_id)
        if failed:
            failed.status = "failed"
            failed.error_message = str(exc)[:1000]
            failed.finished_at = _utcnow()
            _update_execute_summary(failed, status="failed", error_code=exc.__class__.__name__)
            db.commit()
            db.refresh(failed)
            return serialize_manual_sync_execute_run(failed)
        raise
    finally:
        with _active_execute_lock:
            if _active_execute_run_id == run_id:
                _active_execute_run_id = None
            _cancel_flags.pop(run_id, None)


def serialize_manual_sync_execute_run(run: DynamicSyncRun) -> Dict[str, Any]:
    payload = serialize_sync_run(run)
    payload["manual_sync_execute"] = (run.summary_json or {}).get("manual_sync_execute") or {}
    return payload


def get_latest_manual_sync_execute_run(db: Session) -> Optional[DynamicSyncRun]:
    return (
        db.query(DynamicSyncRun)
        .filter(DynamicSyncRun.run_type == "manual_sync_execute")
        .order_by(DynamicSyncRun.created_at.desc(), DynamicSyncRun.id.desc())
        .first()
    )
