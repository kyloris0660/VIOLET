"""S3A-M1 guarded manual sync execution.

This module is deliberately manual-only. It never schedules itself, never
starts from application startup, and never mutates source files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import asyncio
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AITagJob,
    ClassificationJob,
    DynamicSourceItem,
    DynamicSourceRoot,
    DynamicSyncRun,
    DynamicSyncRunItem,
    Media,
    Tag,
    TagTranslation,
    blombooru_media_tags,
)
from ..routes.media import process_and_save_media
from ..schemas import RatingEnum
from ..utils.logger import logger
from ..utils.local_library_scanner import _is_scannable_file
from ..utils.media_helpers import get_unique_filename
from .dynamic_library_sync_service import (
    MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS,
    S3A_M2_MANUAL_EXECUTE_CONFIRMATION_PREFIX,
    S3A_M2_PRODUCTION_EXECUTE_CONFIRMATION_PREFIX,
    _calculate_manual_plan_file_hash,
    _hash_text,
    _manual_public_reason_code,
    _metadata_for_path,
    _normalize_relative_path,
    _relative_identity_and_preflight_reason,
    _utcnow,
    manual_sync_execute_confirmation_phrase,
    manual_sync_operator_confirmation_statement,
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

MANUAL_SYNC_EXECUTE_ACTIVE_TIMEOUT_SECONDS = 30 * 60
MANUAL_SYNC_EXECUTE_MAX_ITEM_FAILURES = 20
MANUAL_SYNC_EXECUTE_MAX_FAILURE_RATE = 0.05
MANUAL_SYNC_EXECUTE_FAILURE_RATE_MIN_ITEMS = 20
MANUAL_SYNC_EXECUTE_MAX_CONSECUTIVE_FAILURES = 10
MANUAL_SYNC_EXECUTE_MAX_DURATION_SECONDS = 10 * 60
MANUAL_SYNC_EXECUTE_MAX_FILES = 5
_ACTIVE_JOB_STATUSES = ("pending", "running", "cancelling")
LOCALIZABLE_TAG_CATEGORIES = {"general", "meta"}
PROPER_NOUN_TAG_CATEGORIES = {"character", "copyright", "artist"}
CONFIRMED_NON_TARGET_CONTENT_CLASSES = {"non_anime"}
UNKNOWN_OR_UNCERTAIN_CONTENT_CLASSES = {"", "none", "null", "unknown", "unclassified", "uncertain"}
CLASSIFICATION_DONE_STATUSES = {"classified", "classified_reused"}
RETRYABLE_SOURCE_FAILURE_REASONS = {
    "cloud_hydration_failed",
    "cloud_network_unavailable",
    "icloud_placeholder",
    "permission_denied",
    "read_error",
    "read_timeout",
    "source_missing",
}


def _manual_sync_target_content_classes() -> set[str]:
    return {
        str(value).strip().lower()
        for value in settings.DYNAMIC_LIBRARY_MANUAL_SYNC_TARGET_CONTENT_CLASSES
        if str(value).strip()
    }


def _media_content_class_value(media: Optional[Media]) -> str:
    value = getattr(media, "content_class", None)
    value = getattr(value, "value", value)
    return str(value or "unclassified").strip().lower()


def _manual_sync_media_ai_eligibility(
    media: Optional[Media],
    source_item: Optional[DynamicSourceItem] = None,
) -> tuple[str, str]:
    """Return manual-sync AI/localization eligibility without collapsing unknown into non-target."""
    if not settings.CONTENT_CLASSIFICATION_ENABLED:
        return ("eligible", "classification_disabled")

    content_class = _media_content_class_value(media)
    if content_class in _manual_sync_target_content_classes():
        return ("eligible", "target_content_class")
    if content_class in CONFIRMED_NON_TARGET_CONTENT_CLASSES:
        return ("non_target", "confirmed_non_target_content_class")

    classification_status = str(getattr(source_item, "classification_status", "") or "").strip().lower()
    if content_class in UNKNOWN_OR_UNCERTAIN_CONTENT_CLASSES and classification_status not in CLASSIFICATION_DONE_STATUSES:
        return ("classification_blocked", "classification_not_completed")

    return ("eligible_unknown", "content_class_unknown_or_uncertain")


def _manual_sync_media_is_target_for_ai(media: Optional[Media]) -> bool:
    return _manual_sync_media_ai_eligibility(media)[0] in {"eligible", "eligible_unknown"}


def _manual_sync_execute_skip_state_for_reason(reason: Optional[str]) -> Optional[str]:
    if reason in {"unsupported_extension", "hidden", "too_large", "zero_byte", "zero_byte_file"}:
        return "skipped_unsupported"
    if reason in {"cloud_placeholder", "icloud_placeholder"}:
        return "skipped_placeholder"
    if reason == "existing_media_hash":
        return "skipped_existing_media"
    if reason == "duplicate_hash":
        return "skipped_duplicate"
    return None


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


def _aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _translation_side_effect_blockers() -> List[str]:
    blockers: List[str] = []
    if settings.TAG_TRANSLATION_LLM_ENABLED and settings.TAG_TRANSLATION_BG_ENABLED:
        blockers.append("tag_translation_background_llm_enabled")
    if settings.TAG_TRANSLATION_LLM_ENABLED and settings.TAG_TRANSLATION_AUTO_ENABLED:
        blockers.append("tag_translation_auto_llm_enabled")
    worker_state = _translation_worker_runtime_state()
    if worker_state.get("status_unavailable"):
        blockers.append("tag_translation_worker_status_unavailable")
    elif worker_state.get("thread_alive") or worker_state.get("running"):
        blockers.append(f"tag_translation_worker_{worker_state.get('status', 'live')}")
    return blockers


def _translation_worker_runtime_state() -> Dict[str, Any]:
    try:
        from .tag_translation_worker import get_worker_runtime_state

        return get_worker_runtime_state()
    except Exception as exc:
        return {"status_unavailable": True, "error": exc.__class__.__name__}


def _assert_translation_side_effects_disabled() -> None:
    blockers = _translation_side_effect_blockers()
    if blockers:
        raise ManualSyncExecuteError(
            "translation_llm_side_effects_enabled",
            "Manual sync execute requires tag translation LLM background/auto paths to be disabled.",
            status_code=409,
        )


def _translation_llm_provider_configured() -> bool:
    primary_ready = bool(
        settings.TAG_TRANSLATION_LLM_API_KEY
        and settings.TAG_TRANSLATION_LLM_MODEL
        and settings.TAG_TRANSLATION_LLM_BASE_URL
    )
    fallback_ready = bool(
        settings.TAG_TRANSLATION_LLM_FALLBACK_ENABLED
        and settings.TAG_TRANSLATION_LLM_FALLBACK_API_KEY
        and settings.TAG_TRANSLATION_LLM_FALLBACK_MODEL
        and settings.TAG_TRANSLATION_LLM_FALLBACK_BASE_URL
    )
    return primary_ready or fallback_ready


def _assert_manual_e2e_components_ready_for_production() -> None:
    if not settings.IS_PRODUCTION_ENV:
        return
    if not settings.CONTENT_CLASSIFICATION_ENABLED:
        raise ManualSyncExecuteError(
            "manual_sync_classification_disabled",
            "Production manual E2E execute requires content classification to be enabled.",
            status_code=409,
        )
    if str(settings.CONTENT_CLASSIFICATION_METHOD or "").lower() != "clip":
        raise ManualSyncExecuteError(
            "manual_sync_classification_gate_requires_clip",
            "Production manual E2E execute requires classification-before-AI gating; use CONTENT_CLASSIFICATION_METHOD=clip.",
            status_code=409,
        )
    clip_ready, clip_reason = _ensure_clip_model_cache_only()
    if not clip_ready:
        raise ManualSyncExecuteError(
            str(clip_reason or "classification_model_uncached"),
            "Production manual E2E execute requires the CLIP classifier to be available from local cache before import writes begin.",
            status_code=409,
        )
    if not settings.AI_TAGGING_ENABLED:
        raise ManualSyncExecuteError(
            "manual_sync_ai_tagging_disabled",
            "Production manual E2E execute requires AI tagging to be enabled.",
            status_code=409,
        )
    if not settings.TAG_TRANSLATION_LLM_ENABLED:
        raise ManualSyncExecuteError(
            "manual_sync_localization_llm_disabled",
            "Production manual E2E execute requires tag localization LLM to be enabled or an accepted stable policy.",
            status_code=409,
        )
    if not _translation_llm_provider_configured():
        raise ManualSyncExecuteError(
            "manual_sync_localization_llm_provider_unconfigured",
            "Production manual E2E execute requires a configured tag localization LLM provider.",
            status_code=409,
        )


def _assert_no_active_ai_or_classification_jobs(db: Optional[Session] = None) -> None:
    from .ai_tagging_job_service import is_ai_job_active
    from .classification_job_service import is_classification_job_active

    if is_ai_job_active():
        raise ManualSyncExecuteError(
            "ai_job_active_blocks_manual_sync_execute",
            "Manual sync execute is blocked while an AI tagging job is active.",
            status_code=409,
        )
    if is_classification_job_active():
        raise ManualSyncExecuteError(
            "classification_job_active_blocks_manual_sync_execute",
            "Manual sync execute is blocked while a classification job is active.",
            status_code=409,
        )
    if db is not None:
        queued_ai_job = (
            db.query(AITagJob.id)
            .filter(AITagJob.status.in_(_ACTIVE_JOB_STATUSES))
            .first()
        )
        if queued_ai_job is not None:
            raise ManualSyncExecuteError(
                "ai_job_active_blocks_manual_sync_execute",
                "Manual sync execute is blocked while an AI tagging job is pending, running, or cancelling.",
                status_code=409,
            )
        queued_classification_job = (
            db.query(ClassificationJob.id)
            .filter(ClassificationJob.status.in_(_ACTIVE_JOB_STATUSES))
            .first()
        )
        if queued_classification_job is not None:
            raise ManualSyncExecuteError(
                "classification_job_active_blocks_manual_sync_execute",
                "Manual sync execute is blocked while a classification job is pending, running, or cancelling.",
                status_code=409,
            )


def _manual_sync_execute_db_run_active(db: Optional[Session] = None) -> bool:
    if db is None:
        return False
    return (
        db.query(DynamicSyncRun.id)
        .filter(
            DynamicSyncRun.run_type == "manual_sync_execute",
            DynamicSyncRun.status.in_(_ACTIVE_JOB_STATUSES),
        )
        .first()
        is not None
    )


def assert_manual_sync_execute_inactive_for_ai_job(db: Optional[Session] = None) -> None:
    if is_manual_sync_execute_active() or _manual_sync_execute_db_run_active(db):
        raise ManualSyncExecuteError(
            "manual_sync_execute_active_blocks_ai_job",
            "AI tagging is blocked while a manual sync execute run is active or queued.",
            status_code=409,
        )


def assert_manual_sync_execute_inactive_for_classification_job(db: Optional[Session] = None) -> None:
    if is_manual_sync_execute_active() or _manual_sync_execute_db_run_active(db):
        raise ManualSyncExecuteError(
            "manual_sync_execute_active_blocks_classification_job",
            "Content classification is blocked while a manual sync execute run is active or queued.",
            status_code=409,
        )


def _manual_sync_runtime_provenance() -> Dict[str, Any]:
    def _git_value(*args: str) -> Optional[str]:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=settings.CODE_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception:
            return None
        value = completed.stdout.strip()
        return value or None

    return {
        "git_head": _git_value("rev-parse", "HEAD"),
        "git_branch": _git_value("branch", "--show-current"),
        "profile_id": os.getenv("VIOLET_PRODUCTION_PROFILE_ID") or None,
        "app_port": os.getenv("APP_PORT") or None,
        "violet_env": settings.VIOLET_ENV,
        "db_name": settings.DB_NAME,
        "code_root_public_marker": _hash_text(str(settings.CODE_ROOT))[:16],
    }


def _plan_partial_scan_allows_execute(plan: Dict[str, Any]) -> bool:
    counts = plan.get("counts") or {}
    limits = plan.get("limits") or {}
    partial_reason = str(counts.get("partial_scan_reason") or limits.get("partial_scan_reason") or "")
    unsafe_partial = bool(counts.get("unsafe_partial_scan") or limits.get("unsafe_partial_scan"))
    return bool(
        partial_reason == "cap_limited_actionable_batch"
        and not unsafe_partial
        and (counts.get("batch_executable") or limits.get("batch_executable"))
    )


def _localization_policy_payload(blockers: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "scheduled": False,
        "status": "blocked_current_phase",
        "safe_to_schedule": False,
        "blocked_reason": "localization_waiting_for_manual_execute_finalizer",
        "side_effect_blockers": sorted(blockers if blockers is not None else _translation_side_effect_blockers()),
    }


def _budget_policy_payload() -> Dict[str, Any]:
    return {
        "max_files": manual_sync_execute_max_files_cap(),
        "max_item_failures": MANUAL_SYNC_EXECUTE_MAX_ITEM_FAILURES,
        "max_failure_rate": MANUAL_SYNC_EXECUTE_MAX_FAILURE_RATE,
        "failure_rate_min_items": MANUAL_SYNC_EXECUTE_FAILURE_RATE_MIN_ITEMS,
        "max_consecutive_failures": MANUAL_SYNC_EXECUTE_MAX_CONSECUTIVE_FAILURES,
        "max_duration_seconds": manual_sync_execute_max_duration_seconds(),
        "stop_reasons": ["stopped_by_failure_budget", "stopped_by_duration_budget"],
    }


def _budget_stop_reason(
    *,
    started_at: datetime,
    processed_items: int,
    failed_items: int,
    consecutive_failures: int,
) -> Optional[str]:
    started = _aware_utc(started_at) or _utcnow()
    if (_utcnow() - started).total_seconds() > manual_sync_execute_max_duration_seconds():
        return "stopped_by_duration_budget"
    if failed_items > MANUAL_SYNC_EXECUTE_MAX_ITEM_FAILURES:
        return "stopped_by_failure_budget"
    if consecutive_failures > MANUAL_SYNC_EXECUTE_MAX_CONSECUTIVE_FAILURES:
        return "stopped_by_failure_budget"
    if (
        processed_items >= MANUAL_SYNC_EXECUTE_FAILURE_RATE_MIN_ITEMS
        and processed_items > 0
        and failed_items / processed_items > MANUAL_SYNC_EXECUTE_MAX_FAILURE_RATE
    ):
        return "stopped_by_failure_budget"
    return None


def _retryable_source_failure_count(counts: Counter[str]) -> int:
    return sum(int(counts.get(reason, 0) or 0) for reason in RETRYABLE_SOURCE_FAILURE_REASONS)


def _manual_sync_plan_item_execute_priority(plan_item: Dict[str, Any]) -> int:
    state = str(plan_item.get("state") or "")
    if state == "downstream_followup_planned":
        return 0
    if state == "import_planned":
        return 10
    return 20


def _order_manual_sync_execute_plan_items(plan_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        item
        for _index, item in sorted(
            enumerate(plan_items),
            key=lambda row: (_manual_sync_plan_item_execute_priority(row[1]), row[0]),
        )
    ]


def _record_retryable_source_failure_attempt(item: DynamicSourceItem, reason: str) -> None:
    if reason not in RETRYABLE_SOURCE_FAILURE_REASONS:
        return
    metadata = dict(item.metadata_json or {})
    retry = dict(metadata.get("manual_sync_retry") or {})
    try:
        attempts = int(retry.get("attempt_count") or 0)
    except (TypeError, ValueError):
        attempts = 0
    retry.update(
        {
            "attempt_count": attempts + 1,
            "last_retry_at": _utcnow().isoformat(),
            "last_failure_reason": reason,
            "retryable": True,
            "long_term_state": "needs_diagnosis" if attempts + 1 >= 5 else "retryable",
        }
    )
    metadata["manual_sync_retry"] = retry
    item.metadata_json = metadata


def _import_budget_stop_allows_downstream(
    *,
    stop_reason: Optional[str],
    counts: Counter[str],
    downstream_targets: List[Dict[str, int]],
    run_status: Optional[str],
) -> bool:
    if stop_reason != "stopped_by_failure_budget":
        return False
    if run_status == "cancelled" or not downstream_targets:
        return False
    failed = int(counts.get("failed", 0) or 0)
    if failed <= 0:
        return False
    return failed == _retryable_source_failure_count(counts)


def manual_sync_execute_max_files_cap() -> int:
    return int(settings.DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_MAX_FILES)


def manual_sync_execute_max_duration_seconds() -> int:
    return int(settings.DYNAMIC_LIBRARY_MANUAL_SYNC_MAX_DURATION_SECONDS)


def manual_sync_execute_effective_max_files(max_files: Optional[int]) -> int:
    cap = manual_sync_execute_max_files_cap()
    if max_files is None:
        return cap
    effective = int(max_files)
    if effective > cap:
        raise ManualSyncExecuteError(
            "manual_sync_execute_max_files_exceeded",
            f"Manual sync execute is capped at {cap} files.",
            status_code=400,
        )
    return max(1, effective)


_effective_execute_max_files = manual_sync_execute_effective_max_files


def _public_request_payload(
    *,
    root_id: int,
    max_files: Optional[int],
    effective_max_files: int,
    hydrated_only: bool,
    stable_age_seconds: Optional[float],
    plan_mode: str,
    expected_plan_hash: str,
    plan_created_at: str,
    production_acceptance_approved: bool,
    request_source: str = "api_or_runner",
    gui_validation_session_id: Optional[str] = None,
    gui_validation_session_signature_valid: bool = False,
    client_route: Optional[str] = None,
    gui_plan_request_id: Optional[str] = None,
    gui_plan_hash_bound: bool = False,
    gui_plan_flow_verified: bool = False,
    runtime_provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "root_id": root_id,
        "max_files": max_files,
        "effective_max_files": effective_max_files,
        "execute_max_files_cap": manual_sync_execute_max_files_cap(),
        "hydrated_only": hydrated_only,
        "stable_age_seconds": stable_age_seconds,
        "plan_mode": str(plan_mode or "incremental"),
        "expected_plan_hash": expected_plan_hash,
        "plan_created_at": plan_created_at,
        "production_acceptance_approved": bool(production_acceptance_approved),
        "trigger_type": "manual_operator",
        "request_source": request_source,
    }
    if gui_validation_session_id:
        payload["gui_validation_session_id"] = str(gui_validation_session_id)
        payload["gui_validation_session_id_hash"] = _hash_text(str(gui_validation_session_id))[:16]
    payload["gui_validation_session_signature_valid"] = bool(gui_validation_session_signature_valid)
    if client_route:
        payload["client_route"] = str(client_route)
    if gui_plan_request_id:
        payload["gui_plan_request_id"] = str(gui_plan_request_id)
        payload["gui_plan_request_id_hash"] = _hash_text(str(gui_plan_request_id))[:16]
    payload["gui_plan_hash_bound"] = bool(gui_plan_hash_bound)
    payload["gui_plan_flow_verified"] = bool(gui_plan_flow_verified)
    if runtime_provenance:
        payload["runtime_git_head"] = runtime_provenance.get("git_head")
        payload["runtime_git_branch"] = runtime_provenance.get("git_branch")
    return payload


def _verify_execute_gates(
    *,
    db: Session,
    plan: Dict[str, Any],
    expected_plan_hash: str,
    confirmation_phrase: str,
    operator_confirmation_statement: Optional[str] = None,
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
    if not settings.DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED:
        raise ManualSyncExecuteError(
            "manual_sync_execute_disabled",
            "Manual sync execute is disabled. Set DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED=true only for an approved bounded run.",
            status_code=409,
        )
    if settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED or settings.S3B_UNATTENDED_SYNC_ENABLED:
        raise ManualSyncExecuteError(
            "unattended_sync_flag_enabled",
            "Automatic or unattended sync flags must remain disabled before manual execute.",
            status_code=409,
        )
    _assert_translation_side_effects_disabled()
    _assert_no_active_ai_or_classification_jobs(db)

    created = _parse_datetime(plan_created_at)
    if created is None:
        raise ManualSyncExecuteError(
            "plan_created_at_required",
            "plan_created_at must be the dry-run plan job.created_at value.",
            status_code=400,
        )
    generated_at = _parse_datetime(str((plan.get("job") or {}).get("created_at") or ""))
    if generated_at is None or generated_at != created:
        raise ManualSyncExecuteError(
            "stale_plan_timestamp_mismatch",
            "plan_created_at must match the generated dry-run plan timestamp.",
            status_code=409,
        )

    plan_hash = str((plan.get("integrity") or {}).get("plan_hash") or "")
    if not expected_plan_hash or expected_plan_hash != plan_hash:
        raise ManualSyncExecuteError(
            "stale_or_mismatched_plan_hash",
            "Plan hash does not match the current dry-run plan. Re-run dry-run before execute.",
            status_code=409,
        )

    age = (_utcnow() - created).total_seconds()
    if age < 0 or age > MANUAL_SYNC_PLAN_STALE_AFTER_SECONDS:
        raise ManualSyncExecuteError(
            "stale_plan_expired",
            "Dry-run plan is stale. Re-run dry-run before execute.",
            status_code=409,
        )

    counts = plan.get("counts") or {}
    if bool(counts.get("partial_scan")) and not _plan_partial_scan_allows_execute(plan):
        reason = str(counts.get("partial_scan_reason") or (plan.get("limits") or {}).get("partial_scan_reason") or "")
        raise ManualSyncExecuteError(
            "manual_sync_plan_partial_scan",
            (
                "Manual sync execute requires a complete safe batch. "
                f"Partial reason: {reason or 'unknown'}."
            ),
            status_code=409,
        )

    if settings.IS_PRODUCTION_ENV:
        expected_phrase = manual_sync_execute_confirmation_phrase(plan_hash, production=True)
        expected_statement = manual_sync_operator_confirmation_statement(plan, production=True)
        confirmed = confirmation_phrase == expected_phrase or operator_confirmation_statement == expected_statement
        if not production_acceptance_approved or not confirmed:
            raise ManualSyncExecuteError(
                "production_acceptance_approval_required",
                "Production execute requires an explicit operator confirmation after a fresh dry-run plan.",
                status_code=409,
            )
    else:
        expected_phrase = manual_sync_execute_confirmation_phrase(plan_hash)
        expected_statement = manual_sync_operator_confirmation_statement(plan)
        if production_acceptance_approved:
            raise ManualSyncExecuteError(
                "production_acceptance_not_allowed_outside_production",
                "production_acceptance_approved is only valid in VIOLET_ENV=production.",
                status_code=400,
            )
        if confirmation_phrase != expected_phrase and operator_confirmation_statement != expected_statement:
            raise ManualSyncExecuteError(
                "manual_execute_confirmation_required",
                "Manual sync execute requires explicit operator confirmation from the dry-run plan.",
                status_code=409,
            )
    _assert_manual_e2e_components_ready_for_production()


def _verify_execute_recheck(
    *,
    db: Session,
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
    if not settings.DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED:
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
    _assert_manual_e2e_components_ready_for_production()
    _assert_translation_side_effects_disabled()
    _assert_no_active_ai_or_classification_jobs(db)
    current_hash = str((plan.get("integrity") or {}).get("plan_hash") or "")
    if current_hash != expected_plan_hash:
        raise ManualSyncExecuteError(
            "stale_or_mismatched_plan_hash",
            "Source contents changed after enqueue. Re-run dry-run before execute.",
            status_code=409,
        )
    counts = plan.get("counts") or {}
    if bool(counts.get("partial_scan")) and not _plan_partial_scan_allows_execute(plan):
        raise ManualSyncExecuteError(
            "manual_sync_plan_partial_scan",
            "Manual sync execute recheck rejected an unsafe partial plan. Re-run the dry-run.",
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
    plan_mode: str = "incremental",
    plan_now: Optional[datetime] = None,
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
        now=plan_now,
        plan_mode=plan_mode,
    )
    return root, source_path, plan


def validate_manual_sync_execute_request(
    db: Session,
    *,
    root_id: int,
    max_files: Optional[int],
    hydrated_only: bool,
    stable_age_seconds: Optional[float],
    plan_mode: str = "incremental",
    expected_plan_hash: str,
    confirmation_phrase: str,
    operator_confirmation_statement: Optional[str] = None,
    plan_created_at: str,
    production_acceptance_approved: bool = False,
) -> Dict[str, Any]:
    created = _parse_datetime(plan_created_at)
    effective_max_files = _effective_execute_max_files(max_files)
    _root, _source_path, plan = _plan_for_root(
        db,
        root_id=root_id,
        max_files=effective_max_files,
        hydrated_only=hydrated_only,
        stable_age_seconds=stable_age_seconds,
        include_private_details=False,
        plan_mode=plan_mode,
        plan_now=created,
    )
    _verify_execute_gates(
        db=db,
        plan=plan,
        expected_plan_hash=expected_plan_hash,
        confirmation_phrase=confirmation_phrase,
        operator_confirmation_statement=operator_confirmation_statement,
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
    plan_mode: str = "incremental",
    expected_plan_hash: str,
    confirmation_phrase: str,
    operator_confirmation_statement: Optional[str] = None,
    plan_created_at: str,
    production_acceptance_approved: bool = False,
    request_source: str = "api_or_runner",
    gui_validation_session_id: Optional[str] = None,
    gui_validation_session_signature_valid: bool = False,
    client_route: Optional[str] = None,
    gui_plan_request_id: Optional[str] = None,
    gui_plan_hash_bound: bool = False,
    gui_plan_flow_verified: bool = False,
    runtime_provenance: Optional[Dict[str, Any]] = None,
) -> DynamicSyncRun:
    if is_manual_sync_execute_active():
        raise ManualSyncExecuteError(
            "manual_sync_execute_already_active",
            "Another manual sync execute run is active.",
            status_code=409,
        )
    _recover_stale_manual_sync_execute_runs(db)
    active_run = _find_active_manual_sync_execute_run(db)
    if active_run is not None:
        raise ManualSyncExecuteError(
            "manual_sync_execute_already_active",
            "Another manual sync execute run is pending or active.",
            status_code=409,
        )

    effective_max_files = _effective_execute_max_files(max_files)
    plan = validate_manual_sync_execute_request(
        db,
        root_id=root_id,
        max_files=effective_max_files,
        hydrated_only=hydrated_only,
        stable_age_seconds=stable_age_seconds,
        plan_mode=plan_mode,
        expected_plan_hash=expected_plan_hash,
        confirmation_phrase=confirmation_phrase,
        operator_confirmation_statement=operator_confirmation_statement,
        plan_created_at=plan_created_at,
        production_acceptance_approved=production_acceptance_approved,
    )
    created = _parse_datetime(plan_created_at)
    _root, _source_path, private_plan = _plan_for_root(
        db,
        root_id=root_id,
        max_files=effective_max_files,
        hydrated_only=hydrated_only,
        stable_age_seconds=stable_age_seconds,
        include_private_details=True,
        plan_mode=plan_mode,
        plan_now=created,
    )
    if str((private_plan.get("integrity") or {}).get("plan_hash") or "") != expected_plan_hash:
        raise ManualSyncExecuteError(
            "stale_or_mismatched_plan_hash",
            "Plan hash changed while preparing execute. Re-run dry-run before execute.",
            status_code=409,
        )
    runtime_payload = runtime_provenance or _manual_sync_runtime_provenance()
    private_plan_items = list(((private_plan.get("private_details") or {}).get("items") or []))
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
            "phase": "S3A-M2",
            "manual_sync_execute": {
                "status": "pending",
                "current_stage": "queued",
                "request": _public_request_payload(
                    root_id=root_id,
                    max_files=max_files,
                    effective_max_files=effective_max_files,
                    hydrated_only=hydrated_only,
                    stable_age_seconds=stable_age_seconds,
                    plan_mode=plan_mode,
                    expected_plan_hash=expected_plan_hash,
                    plan_created_at=plan_created_at,
                    production_acceptance_approved=production_acceptance_approved,
                    request_source=request_source,
                    gui_validation_session_id=gui_validation_session_id,
                    gui_validation_session_signature_valid=gui_validation_session_signature_valid,
                    client_route=client_route,
                    gui_plan_request_id=gui_plan_request_id,
                    gui_plan_hash_bound=gui_plan_hash_bound,
                    gui_plan_flow_verified=gui_plan_flow_verified,
                    runtime_provenance=runtime_payload,
                ),
                "plan": plan,
                "runtime_provenance": runtime_payload,
                "private_plan_items": private_plan_items,
                "stage_rows": stage_rows,
                "outcome_counts": {},
                "budgets": _budget_policy_payload(),
                "localization": _localization_policy_payload([]),
                "classification": {
                    "local_only": True,
                    "clip_cache_only_required": True,
                    "uncached_clip_reason": "classification_model_uncached",
                    "external_download_allowed": False,
                },
                "safety": {
                    "manual_trigger_only": True,
                    "automatic_sync_enabled": False,
                    "scheduled_sync_enabled": False,
                    "startup_sync_enabled": False,
                    "source_mutation_performed": False,
                    "local_files_only_ai": True,
                    "external_provider_calls_performed": False,
                    "model_downloads_allowed": False,
                    "llm_calls_enabled": False,
                    "localization_scheduled": False,
                    "translation_llm_side_effects_blocked": True,
                    "production_acceptance_pending": not production_acceptance_approved,
                    "confirmation_prefix": S3A_M2_MANUAL_EXECUTE_CONFIRMATION_PREFIX,
                    "production_confirmation_prefix": S3A_M2_PRODUCTION_EXECUTE_CONFIRMATION_PREFIX,
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
    safety = dict(execute.get("safety") or {})
    if "llm_calls_performed" in updates:
        safety["llm_calls_enabled"] = bool(updates.get("llm_calls_performed"))
        safety["translation_llm_side_effects_blocked"] = not bool(updates.get("llm_calls_performed"))
    if "external_provider_calls_performed" in updates:
        safety["external_provider_calls_performed"] = bool(updates.get("external_provider_calls_performed"))
    if safety:
        execute["safety"] = safety
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


def _manual_sync_run_reference_time(run: DynamicSyncRun) -> Optional[datetime]:
    return _aware_utc(run.started_at) or _aware_utc(run.created_at)


def _is_stale_active_run(run: DynamicSyncRun, *, now: Optional[datetime] = None) -> bool:
    reference = _manual_sync_run_reference_time(run)
    if reference is None:
        return True
    current = now or _utcnow()
    return (current - reference).total_seconds() > MANUAL_SYNC_EXECUTE_ACTIVE_TIMEOUT_SECONDS


def _recover_stale_manual_sync_execute_runs(db: Session, *, now: Optional[datetime] = None) -> List[int]:
    current = now or _utcnow()
    stale_runs = (
        db.query(DynamicSyncRun)
        .filter(
            DynamicSyncRun.run_type == "manual_sync_execute",
            DynamicSyncRun.status.in_(("pending", "running", "cancelling")),
        )
        .order_by(DynamicSyncRun.created_at.asc(), DynamicSyncRun.id.asc())
        .all()
    )
    recovered: List[int] = []
    for stale in stale_runs:
        if not _is_stale_active_run(stale, now=current):
            continue
        recovered.append(int(stale.id))
        if stale.status == "cancelling":
            stale.status = "cancelled"
            status = "cancelled"
            code = "stale_manual_sync_execute_cancelled"
            stale.error_message = "Recovered stale cancelling manual sync execute run with no active worker."
        else:
            stale.status = "failed"
            status = "failed"
            code = "stale_manual_sync_execute_recovered"
            stale.error_message = "Recovered stale manual sync execute run with no active worker."
        stale.finished_at = current
        _update_execute_summary(
            stale,
            status=status,
            error_code=code,
            recovered_stale_run=True,
            stale_recovery_finished_at=current.isoformat(),
        )
    if recovered:
        db.commit()
    return recovered


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
    if content_hash:
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
    run_item = (
        db.query(DynamicSyncRunItem)
        .filter(
            DynamicSyncRunItem.sync_run_id == run.id,
            DynamicSyncRunItem.source_item_id == item.id,
        )
        .first()
    )
    if run_item is None:
        run_item = DynamicSyncRunItem(sync_run_id=run.id, source_item_id=item.id)
        db.add(run_item)
    run_item.item_state = state
    run_item.action = action
    run_item.reason = reason
    run_item.eligible_for_db_import = eligible
    run_item.bytes_copied = bytes_copied
    run_item.media_id = media_id
    run_item.current_metadata_json = current_metadata or {}
    return run_item


def _annotate_run_item_stage(
    db: Session,
    *,
    run: DynamicSyncRun,
    item: DynamicSourceItem,
    stage: str,
    status: str,
    reason: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    run_item = (
        db.query(DynamicSyncRunItem)
        .filter(
            DynamicSyncRunItem.sync_run_id == run.id,
            DynamicSyncRunItem.source_item_id == item.id,
        )
        .first()
    )
    if run_item is None:
        return
    metadata = dict(run_item.current_metadata_json or {})
    stage_payload = {"status": status}
    if reason:
        stage_payload["reason"] = reason
    if extra:
        stage_payload.update(extra)
    metadata[stage] = stage_payload
    run_item.current_metadata_json = metadata


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
    if reason:
        _record_retryable_source_failure_attempt(item, reason)
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


def _mark_item_failed(
    db: Session,
    *,
    run: DynamicSyncRun,
    item: DynamicSourceItem,
    reason: str,
    metadata: Dict[str, Any],
    action: str = "import",
) -> None:
    item.sync_state = "failed"
    item.source_status = "missing" if reason == "source_missing" else "failed"
    item.import_status = "failed"
    item.classification_status = "deferred"
    item.ai_tagging_status = "deferred"
    item.localization_status = "blocked_import_failed"
    item.failure_reason = reason
    item.deferred_reason = None
    _record_retryable_source_failure_attempt(item, reason)
    _record_run_item(
        db,
        run=run,
        item=item,
        state="failed",
        action=action,
        reason=reason,
        eligible=False,
        current_metadata=metadata,
    )


def _mark_item_import_in_progress(
    db: Session,
    *,
    run: DynamicSyncRun,
    item: DynamicSourceItem,
    metadata: Dict[str, Any],
) -> DynamicSyncRunItem:
    item.sync_state = "import_in_progress"
    item.import_status = "import_in_progress"
    item.classification_status = "deferred"
    item.ai_tagging_status = "deferred"
    item.localization_status = "waiting_import"
    item.failure_reason = None
    item.deferred_reason = None
    return _record_run_item(
        db,
        run=run,
        item=item,
        state="import_in_progress",
        action="import",
        reason=None,
        eligible=True,
        current_metadata=metadata,
    )


def _materialize_deferred_unprocessed_items(
    db: Session,
    *,
    root: DynamicSourceRoot,
    run: DynamicSyncRun,
    plan_items: List[Dict[str, Any]],
    reason: str,
) -> int:
    created = 0
    for plan_item in plan_items:
        relative_path = str(plan_item.get("relative_path") or "")
        metadata = {"safe_label": plan_item.get("safe_label"), "planned_state": plan_item.get("state")}
        item = _get_or_create_source_item(
            db,
            root=root,
            run=run,
            relative_path=relative_path,
            metadata={"suffix": Path(relative_path).suffix.lower()},
            content_hash=None,
        )
        planned_state = str(plan_item.get("state") or "")
        media_id = int(plan_item.get("media_id") or item.media_id or 0)
        item.sync_state = "deferred_unprocessed"
        if planned_state == "downstream_followup_planned" and media_id > 0:
            item.media_id = media_id
            item.import_status = "imported"
        else:
            item.import_status = "deferred"
        item.classification_status = "deferred"
        item.ai_tagging_status = "deferred"
        item.localization_status = "deferred"
        item.failure_reason = None
        item.deferred_reason = reason
        _record_run_item(
            db,
            run=run,
            item=item,
            state="deferred_unprocessed",
            action="defer",
            reason=reason,
            eligible=False,
            media_id=media_id if media_id > 0 else None,
            current_metadata=metadata,
        )
        created += 1
    return created


def _has_ai_wd_tags(db: Session, media_id: int) -> bool:
    return bool(
        db.query(blombooru_media_tags.c.media_id)
        .filter(
            blombooru_media_tags.c.media_id == media_id,
            blombooru_media_tags.c.source == "ai_wd",
        )
        .first()
    )


def _heuristic_classification_ai_tag_block_reason(db: Session, item: Optional[DynamicSourceItem], media_id: int) -> Optional[str]:
    if item is None:
        return "classification_deferred_ai_tags_unavailable"
    ai_status = str(item.ai_tagging_status or "")
    if ai_status.startswith("failed_"):
        return "classification_skipped_ai_tagging_failed"
    if ai_status != "ai_tagged":
        return "classification_deferred_ai_tags_unavailable"
    if not _has_ai_wd_tags(db, media_id):
        return "classification_deferred_ai_tags_unavailable"
    return None


def _ensure_clip_model_cache_only() -> tuple[bool, Optional[str]]:
    try:
        from huggingface_hub import try_to_load_from_cache

        from .clip_classifier import CLIP_REPO_ID, CLIP_REVISION, CLIP_VISION_FILE, EMBEDDINGS_FILE, CLIPClassifier
    except Exception as exc:
        return False, f"clip_import_error:{exc.__class__.__name__}"

    previous_offline = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        classifier = CLIPClassifier()
        if classifier._session is not None and classifier._text_embeddings is not None:
            return True, None
        if not EMBEDDINGS_FILE.exists():
            return False, "classification_model_uncached"
        cached_model = try_to_load_from_cache(
            repo_id=CLIP_REPO_ID,
            filename=CLIP_VISION_FILE,
            revision=CLIP_REVISION,
        )
        if not cached_model or not Path(str(cached_model)).exists():
            return False, "classification_model_uncached"
        classifier._load_session(str(cached_model))
        classifier._load_text_embeddings()
        return True, None
    except Exception as exc:
        return False, f"classification_model_uncached:{exc.__class__.__name__}"
    finally:
        if previous_offline is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous_offline


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
    method = str(settings.CONTENT_CLASSIFICATION_METHOD or "").lower()
    if not settings.CONTENT_CLASSIFICATION_ENABLED:
        return {"media_id": media_id, "skipped": True, "reason": "classification_disabled", "method": method}
    if method == "clip":
        ready, detail = _ensure_clip_model_cache_only()
        if not ready:
            return {
                "media_id": media_id,
                "skipped": True,
                "reason": "classification_model_uncached",
                "detail": detail,
                "method": method,
            }
    from .content_classifier import classify_media

    result = classify_media(db, media_id)
    result.setdefault("method", method)
    return result


def _ai_tagging_failure_reason(error: Any) -> str:
    if isinstance(error, BaseException):
        text = f"{error.__class__.__name__}: {error}".casefold()
    else:
        text = str(error or "").casefold()
    cache_markers = (
        "localentrynotfound",
        "local_files_only",
        "local files only",
        "not found in local cache",
        "cache",
        "cached",
        "model file",
        "label file",
    )
    if any(marker in text for marker in cache_markers):
        return "ai_tagger_model_uncached"
    file_missing_markers = ("file not found", "no such file", "filenotfound")
    if any(marker in text for marker in file_missing_markers):
        return "ai_tagger_file_missing"
    return "ai_tagger_inference_failed"


def _ai_tag_imported_media(db: Session, media_id: int) -> Dict[str, Any]:
    if not settings.AI_TAGGING_ENABLED:
        return {"media_id": media_id, "skipped": True, "reason": "ai_tagging_disabled"}
    from .ai_tagging_service import run_ai_tagging

    return run_ai_tagging(
        db,
        media_id,
        dry_run=False,
        # Mature media-tag semantics: high-confidence WD tags may become normal
        # media tags, but AI-only tags must not create Entity/SourceConcept truth.
        force_suggestions=False,
        local_files_only=True,
        schedule_localization=False,
    )


def _tag_category_name(category: Any) -> str:
    value = getattr(category, "value", category)
    return str(value or "").casefold()


def _covered_translation_names(db: Session, *, lang: str) -> set[str]:
    from .tag_localization_service import _load_static_dict

    translated = {
        str(name)
        for (name,) in db.query(TagTranslation.canonical_name)
        .filter(TagTranslation.language == lang, TagTranslation.status != "rejected")
        .all()
    }
    static = set((_load_static_dict().get("tags") or {}).keys())
    return translated | static


def _manual_sync_finalize_localization(
    db: Session,
    *,
    run: DynamicSyncRun,
    media_ids: List[int],
    source_item_ids: Optional[List[int]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    lang: str = "zh-CN",
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "scheduled": False,
        "safe_to_schedule": False,
        "status": "completed_noop_no_imports",
        "background_worker_started": False,
        "auto_translation_enabled": False,
        "llm_called": False,
        "provider_call_count": 0,
        "translated": 0,
        "failed": 0,
        "skipped": 0,
        "language": lang,
        "localizable_categories": sorted(LOCALIZABLE_TAG_CATEGORIES),
        "proper_noun_categories": sorted(PROPER_NOUN_TAG_CATEGORIES),
        "distinct_ai_wd_tag_count": 0,
        "localizable_distinct_tags": 0,
        "localizable_already_localized_or_static": 0,
        "tags_requiring_localization_after_runner": 0,
        "proper_noun_distinct_tags": 0,
        "proper_noun_missing_translation_stable_reason_count": 0,
        "dynamic_source_items_updated": 0,
        "dynamic_source_items_target_status": None,
        "blocked_reason": None,
        "public_safe": True,
    }
    if not media_ids:
        return result
    if cancel_check is not None and cancel_check():
        return _manual_sync_skipped_localization_result(reason="cancelled", media_ids=media_ids, lang=lang)

    imported_query = db.query(DynamicSourceItem).filter(DynamicSourceItem.import_status == "imported")
    if source_item_ids:
        imported_query = imported_query.filter(DynamicSourceItem.id.in_([int(value) for value in source_item_ids]))
    else:
        imported_query = imported_query.filter(DynamicSourceItem.media_id.in_(media_ids))
    imported_items = imported_query.all()
    eligible_media_ids = {
        int(item.media_id)
        for item in imported_items
        if item.media_id is not None and str(item.ai_tagging_status or "") in {"ai_tagged", "tagged", "tagged_reused"}
    }
    eligible_source_item_ids = {
        int(item.id)
        for item in imported_items
        if item.media_id is not None
        and int(item.media_id) in eligible_media_ids
        and item.id is not None
    }
    if not eligible_media_ids:
        update_query = db.query(DynamicSourceItem).filter(DynamicSourceItem.import_status == "imported")
        if source_item_ids:
            update_query = update_query.filter(DynamicSourceItem.id.in_([int(value) for value in source_item_ids]))
        else:
            update_query = update_query.filter(DynamicSourceItem.media_id.in_(media_ids))
        updated = update_query.update(
            {
                DynamicSourceItem.localization_status: "blocked_ai_tagging_not_completed",
                DynamicSourceItem.deferred_reason: "localization_requires_completed_ai_tags",
            },
            synchronize_session=False,
        )
        db.commit()
        return {
            **result,
            "status": "blocked_ai_tagging_not_completed",
            "blocked_reason": "localization_requires_completed_ai_tags",
            "dynamic_source_items_updated": int(updated or 0),
            "dynamic_source_items_target_status": "blocked_ai_tagging_not_completed",
        }

    covered = _covered_translation_names(db, lang=lang)
    rows = (
        db.query(Tag.name, Tag.category)
        .join(blombooru_media_tags, Tag.id == blombooru_media_tags.c.tag_id)
        .filter(blombooru_media_tags.c.media_id.in_(eligible_media_ids))
        .filter(blombooru_media_tags.c.source == "ai_wd")
        .distinct()
        .all()
    )
    tag_categories = {str(name): _tag_category_name(category) for name, category in rows}
    localizable = {name for name, category in tag_categories.items() if category in LOCALIZABLE_TAG_CATEGORIES}
    proper = {name for name, category in tag_categories.items() if category in PROPER_NOUN_TAG_CATEGORIES}
    localizable_missing = sorted(name for name in localizable if name not in covered)
    translated = 0
    failed = 0
    provider_call_count = 0
    llm_called = False
    errors: list[str] = []

    def _cancelled_localization_result() -> Dict[str, Any]:
        cancelled = _manual_sync_skipped_localization_result(reason="cancelled", media_ids=media_ids, lang=lang)
        cancelled.update(
            {
                "llm_called": llm_called,
                "provider_call_count": provider_call_count,
                "translated": translated,
                "failed": failed,
                "skipped": result.get("skipped", 0),
                "errors": sorted(set(errors)),
                "blocked_reason": "manual_sync_cancelled_during_localization",
                "localization_finalizer_called": True,
                "localization_db_writes_performed": bool(translated),
            }
        )
        return cancelled

    if localizable_missing:
        if not settings.TAG_TRANSLATION_LLM_ENABLED:
            blocked_reason = "localization_backend_disabled"
        else:
            if cancel_check is not None and cancel_check():
                return _cancelled_localization_result()
            try:
                from .llm_translation_provider import get_llm_provider
                from .tag_localization_service import upsert_translation
                from ..utils.search_parser import invalidate_translation_cache

                provider = get_llm_provider()
                if not provider.is_available():
                    blocked_reason = "localization_provider_unavailable"
                else:
                    blocked_reason = None
                    batch_size = max(1, min(int(settings.TAG_TRANSLATION_BATCH_MAX_ITEMS), len(localizable_missing)))
                    candidate_by_name = {
                        name: {"name": name, "category": tag_categories.get(name, "general")}
                        for name in localizable_missing
                    }
                    for start in range(0, len(localizable_missing), batch_size):
                        if cancel_check is not None and cancel_check():
                            return _cancelled_localization_result()
                        batch_names = localizable_missing[start : start + batch_size]
                        inputs = [candidate_by_name[name] for name in batch_names]
                        provider_call_count += 1
                        llm_called = True
                        try:
                            translations = asyncio.run(provider.translate_tags(inputs))
                        except Exception:
                            errors.append("provider_batch_failed")
                            failed += len(batch_names)
                            continue
                        seen_outputs: set[str] = set()
                        for translation in translations:
                            if cancel_check is not None and cancel_check():
                                return _cancelled_localization_result()
                            canonical = str(getattr(translation, "canonical_name", "") or "")
                            if canonical not in candidate_by_name or canonical in seen_outputs:
                                continue
                            seen_outputs.add(canonical)
                            item = candidate_by_name[canonical]
                            try:
                                saved = upsert_translation(
                                    db,
                                    canonical_name=canonical,
                                    display_name=getattr(translation, "display_name_zh", ""),
                                    lang=lang,
                                    aliases=getattr(translation, "aliases_zh", []) or [],
                                    category=item["category"],
                                    source="llm",
                                    status="translated",
                                    confidence=getattr(translation, "confidence", None),
                                    needs_review=bool(getattr(translation, "needs_review", False)),
                                    provider=provider.get_provider_name(),
                                )
                            except Exception:
                                db.rollback()
                                errors.append("translation_save_failed")
                                failed += 1
                                continue
                            if saved is None:
                                result["skipped"] += 1
                            else:
                                translated += 1
                        missing_from_provider = max(0, len(batch_names) - len(seen_outputs))
                        failed += missing_from_provider
                    invalidate_translation_cache()
                    covered = _covered_translation_names(db, lang=lang)
                    localizable_missing = sorted(name for name in localizable if name not in covered)
            except Exception:
                blocked_reason = "localization_execution_failed"
                errors.append("localization_execution_failed")
                failed += len(localizable_missing)
    else:
        blocked_reason = None
    if cancel_check is not None and cancel_check():
        return _cancelled_localization_result()

    if localizable_missing or failed:
        target_status = "deferred"
        target_reason = blocked_reason or "localization_failed"
        status = "blocked_localization_gap_remaining" if localizable_missing else "completed_with_failures"
    else:
        target_status = "localized"
        target_reason = None
        status = "completed_existing_coverage" if translated == 0 else "completed"

    update_values: Dict[Any, Any] = {DynamicSourceItem.localization_status: target_status}
    if target_reason:
        update_values[DynamicSourceItem.deferred_reason] = target_reason
    else:
        update_values[DynamicSourceItem.deferred_reason] = None
    update_query = db.query(DynamicSourceItem).filter(DynamicSourceItem.import_status == "imported")
    if eligible_source_item_ids:
        update_query = update_query.filter(DynamicSourceItem.id.in_(eligible_source_item_ids))
    else:
        update_query = update_query.filter(DynamicSourceItem.media_id.in_(eligible_media_ids))
    updated_items = update_query.update(update_values, synchronize_session=False)
    db.commit()

    return {
        **result,
        "status": status,
        "blocked_reason": target_reason,
        "llm_called": llm_called,
        "provider_call_count": provider_call_count,
        "translated": translated,
        "failed": failed,
        "errors": sorted(set(errors)),
        "distinct_ai_wd_tag_count": len(tag_categories),
        "localizable_distinct_tags": len(localizable),
        "localizable_already_localized_or_static": len(localizable - set(localizable_missing)),
        "tags_requiring_localization_after_runner": len(localizable_missing),
        "proper_noun_distinct_tags": len(proper),
        "proper_noun_missing_translation_stable_reason_count": len(proper - covered),
        "proper_noun_missing_translation_reason": "proper_noun_translation_not_required_for_current_media_tag_layer",
        "dynamic_source_items_updated": int(updated_items or 0),
        "dynamic_source_items_target_status": target_status,
        "localization_finalizer_called": True,
        "localization_db_writes_performed": bool(translated),
    }


def _manual_sync_skipped_localization_result(
    *,
    reason: str,
    media_ids: List[int],
    lang: str = "zh-CN",
) -> Dict[str, Any]:
    safe_reason = "cancelled" if reason == "cancelled" else "stopped"
    blocked_reason = f"manual_sync_{safe_reason}_before_localization"
    return {
        "scheduled": False,
        "safe_to_schedule": False,
        "status": f"skipped_{safe_reason}_run",
        "background_worker_started": False,
        "auto_translation_enabled": False,
        "llm_called": False,
        "provider_call_count": 0,
        "translated": 0,
        "failed": 0,
        "skipped": len(media_ids),
        "language": lang,
        "localizable_categories": sorted(LOCALIZABLE_TAG_CATEGORIES),
        "proper_noun_categories": sorted(PROPER_NOUN_TAG_CATEGORIES),
        "distinct_ai_wd_tag_count": 0,
        "localizable_distinct_tags": 0,
        "localizable_already_localized_or_static": 0,
        "tags_requiring_localization_after_runner": 0,
        "proper_noun_distinct_tags": 0,
        "proper_noun_missing_translation_stable_reason_count": 0,
        "dynamic_source_items_updated": 0,
        "dynamic_source_items_target_status": "unchanged",
        "blocked_reason": blocked_reason,
        "public_safe": True,
        "localization_finalizer_called": False,
        "localization_db_writes_performed": False,
    }


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
    downstream_media_ids: List[int] = []
    downstream_targets: List[Dict[str, int]] = []
    ai_provenance: Optional[Dict[str, Any]] = None

    try:
        summary = run.summary_json or {}
        execute_payload = summary.get("manual_sync_execute") or {}
        request = (execute_payload.get("request") or {})
        root_id = int(request.get("root_id") or 0)
        request_hydrated_only = bool(request.get("hydrated_only", True))
        root = db.get(DynamicSourceRoot, root_id)
        if root is None or not root.is_active:
            raise ManualSyncExecuteError(
                "source_root_not_found",
                "Manual sync execute requires an active registered source root.",
                status_code=404,
            )
        root_path = validate_source_root_path(root.root_path)
        persisted_plan = execute_payload.get("plan") or {}
        _verify_execute_recheck(
            db=db,
            plan=persisted_plan,
            expected_plan_hash=str(request.get("expected_plan_hash") or ""),
            hydrated_only=bool(request.get("hydrated_only", True)),
            production_acceptance_approved=bool(request.get("production_acceptance_approved")),
        )
        private_items = _order_manual_sync_execute_plan_items(list(execute_payload.get("private_plan_items") or []))
        if not private_items and int((persisted_plan.get("counts") or {}).get("total_seen") or 0) > 0:
            raise ManualSyncExecuteError(
                "private_plan_snapshot_missing",
                "Manual sync execute requires the private per-item plan snapshot captured at enqueue.",
                status_code=409,
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
    # run could only be created after validation, so rechecks use the persisted
    # hash scope and each item is revalidated against the captured snapshot.
    run.status = "running"
    run.started_at = _utcnow()
    run.summary_json = _set_stage(run.summary_json or {}, "candidate_discovery", status="completed")
    db.commit()

    try:
        processed_items = 0
        item_failure_count = 0
        consecutive_failures = 0
        stop_reason: Optional[str] = None
        processed_plan_items = 0
        run.total_seen = len(private_items)

        def _append_downstream_media_id(media_id: Optional[int]) -> None:
            if media_id is None:
                return
            value = int(media_id)
            if value not in downstream_media_ids:
                downstream_media_ids.append(value)

        def _append_downstream_target(media_id: Optional[int], source_item_id: Optional[int]) -> None:
            if media_id is None:
                return
            media_value = int(media_id)
            source_item_value = int(source_item_id) if source_item_id is not None else 0
            if not any(
                int(target.get("media_id") or 0) == media_value
                and int(target.get("source_item_id") or 0) == source_item_value
                for target in downstream_targets
            ):
                downstream_targets.append({"media_id": media_value, "source_item_id": source_item_value})
            _append_downstream_media_id(media_value)

        def _target_source_item(target: Dict[str, int]) -> Optional[DynamicSourceItem]:
            source_item_id = int(target.get("source_item_id") or 0)
            if source_item_id > 0:
                return db.get(DynamicSourceItem, source_item_id)
            media_id = int(target.get("media_id") or 0)
            if media_id <= 0:
                return None
            return (
                db.query(DynamicSourceItem)
                .filter(DynamicSourceItem.last_sync_run_id == run.id)
                .filter(DynamicSourceItem.media_id == media_id)
                .first()
            )

        for plan_item in private_items:
            if _is_cancel_requested(run_id):
                run.status = "cancelled"
                break
            stop_reason = _budget_stop_reason(
                started_at=run.started_at or _utcnow(),
                processed_items=processed_items,
                failed_items=item_failure_count,
                consecutive_failures=consecutive_failures,
            )
            if stop_reason:
                break

            relative_path = str(plan_item.get("relative_path") or "")
            rel = relative_path
            metadata: Dict[str, Any] = {}
            current_content_hash = plan_item.get("content_hash")
            state = str(plan_item.get("state") or "failed")
            downstream_source_item_id = int(
                ((plan_item.get("downstream_followup") or {}).get("source_item_id") or 0)
            )
            source_file: Optional[Path] = None
            item_failure_reason: Optional[str] = None
            try:
                source_file = _safe_source_file(root_path, relative_path)
                rel, preflight_reason = _relative_identity_and_preflight_reason(root_path, source_file)
                if not source_file.exists() or not source_file.is_file():
                    if state == "downstream_followup_planned":
                        metadata = {
                            "file_size": plan_item.get("file_size"),
                            "mtime_ns": plan_item.get("mtime_ns"),
                            "source_file_available": False,
                        }
                    else:
                        item_failure_reason = "source_missing"
                else:
                    metadata = _metadata_for_path(source_file, follow_symlinks=not bool(preflight_reason))
                    item_failure_reason = _manual_public_reason_code(preflight_reason)
                    if item_failure_reason is None and state == "import_planned":
                        item_failure_reason = _manual_public_reason_code(
                            _is_scannable_file(source_file, hydrated_only=request_hydrated_only)
                        )
                    expected_hash = str(plan_item.get("content_hash") or "")
                    if (
                        item_failure_reason is None
                        and state in {"skipped_existing_media", "skipped_duplicate"}
                        and not expected_hash
                    ):
                        item_failure_reason = "plan_integrity_missing_content_hash"
                    planned_size = plan_item.get("file_size")
                    planned_mtime_ns = plan_item.get("mtime_ns")
                    if item_failure_reason is None and planned_size is not None:
                        try:
                            if int(planned_size) != int(metadata.get("file_size")):
                                item_failure_reason = "content_changed_after_plan"
                        except (TypeError, ValueError):
                            item_failure_reason = "content_changed_after_plan"
                    if item_failure_reason is None and planned_mtime_ns is not None:
                        try:
                            if int(planned_mtime_ns) != int(metadata.get("mtime_ns")):
                                item_failure_reason = "content_changed_after_plan"
                        except (TypeError, ValueError):
                            item_failure_reason = "content_changed_after_plan"
                    should_verify_content = state == "import_planned" or bool(expected_hash)
                    if item_failure_reason is None and should_verify_content:
                        current_hash, hash_reason = _calculate_manual_plan_file_hash(
                            source_file,
                            max(1, int(settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS)),
                        )
                        item_failure_reason = _manual_public_reason_code(hash_reason)
                        if item_failure_reason is None:
                            if expected_hash and current_hash and current_hash != expected_hash:
                                item_failure_reason = "content_changed_after_plan"
                            current_content_hash = current_hash or current_content_hash
            except ManualSyncExecuteError as exc:
                item_failure_reason = _manual_public_reason_code(exc.code)
            except OSError:
                item_failure_reason = "read_error"
            except Exception as exc:
                item_failure_reason = _manual_public_reason_code(str(exc))
            if (
                bool(plan_item.get("cloud_placeholder_before_hydration"))
                and item_failure_reason in {"read_error", "read_timeout", "stat_error"}
            ):
                item_failure_reason = "cloud_hydration_failed"

            item = _get_or_create_source_item(
                db,
                root=root,
                run=run,
                relative_path=rel,
                metadata=metadata,
                content_hash=str(current_content_hash) if current_content_hash else None,
            )
            if state == "downstream_followup_planned" and downstream_source_item_id > 0:
                planned_source_item = db.get(DynamicSourceItem, downstream_source_item_id)
                if planned_source_item is not None and planned_source_item.source_root_id == root.id:
                    item = planned_source_item

            reason = _manual_public_reason_code(plan_item.get("reason"))
            if item_failure_reason:
                stable_skip_state = _manual_sync_execute_skip_state_for_reason(item_failure_reason)
                if stable_skip_state:
                    _mark_item_skipped(
                        db,
                        run=run,
                        item=item,
                        state=stable_skip_state,
                        reason=item_failure_reason,
                        metadata={**metadata, "safe_label": plan_item.get("safe_label")},
                    )
                    counts[stable_skip_state] += 1
                    counts[item_failure_reason] += 1
                    processed_items += 1
                    processed_plan_items += 1
                    consecutive_failures = 0
                    db.commit()
                    continue
                failure_metadata = {**metadata, "safe_label": plan_item.get("safe_label")}
                _mark_item_failed(
                    db,
                    run=run,
                    item=item,
                    reason=item_failure_reason,
                    metadata=failure_metadata,
                )
                counts["failed"] += 1
                counts[item_failure_reason] += 1
                item_failure_count += 1
                consecutive_failures += 1
                processed_items += 1
                processed_plan_items += 1
                run.failed_items = int(counts["failed"])
                db.commit()
                stop_reason = _budget_stop_reason(
                    started_at=run.started_at or _utcnow(),
                    processed_items=processed_items,
                    failed_items=item_failure_count,
                    consecutive_failures=consecutive_failures,
                )
                if stop_reason:
                    break
                continue

            if state == "downstream_followup_planned":
                media_id = int(plan_item.get("media_id") or item.media_id or 0)
                if media_id <= 0:
                    _mark_item_failed(
                        db,
                        run=run,
                        item=item,
                        reason="existing_media_hash",
                        metadata={**metadata, "safe_label": plan_item.get("safe_label"), "followup": "missing_media_id"},
                    )
                    counts["failed"] += 1
                    counts["existing_media_hash"] += 1
                    item_failure_count += 1
                    consecutive_failures += 1
                    processed_items += 1
                    processed_plan_items += 1
                    run.failed_items = int(counts["failed"])
                    db.commit()
                    continue
                item.media_id = media_id
                item.sync_state = "downstream_followup_planned"
                item.import_status = "imported"
                item.last_sync_run_id = run.id
                item.failure_reason = None
                item.deferred_reason = "downstream_followup"
                _record_run_item(
                    db,
                    run=run,
                    item=item,
                    state="downstream_followup_planned",
                    action="downstream_followup",
                    reason="downstream_followup",
                    eligible=False,
                    media_id=media_id,
                    current_metadata={
                        **metadata,
                        "safe_label": plan_item.get("safe_label"),
                        "downstream_followup": plan_item.get("downstream_followup") or {},
                    },
                )
                counts["downstream_followup_planned"] += 1
                _append_downstream_target(media_id, item.id)
                processed_items += 1
                processed_plan_items += 1
                consecutive_failures = 0
                db.commit()
                stop_reason = _budget_stop_reason(
                    started_at=run.started_at or _utcnow(),
                    processed_items=processed_items,
                    failed_items=item_failure_count,
                    consecutive_failures=consecutive_failures,
                )
                if stop_reason:
                    break
                continue

            if state != "import_planned":
                _mark_item_skipped(db, run=run, item=item, state=state, reason=reason, metadata=metadata)
                counts[state] += 1
                processed_items += 1
                processed_plan_items += 1
                consecutive_failures = 0
                db.commit()
                continue

            try:
                if source_file is None:
                    raise ManualSyncExecuteError("source_missing", "Source file is missing.")
                import_metadata = {**metadata, "safe_label": plan_item.get("safe_label")}
                _mark_item_import_in_progress(
                    db,
                    run=run,
                    item=item,
                    metadata={**import_metadata, "import": {"status": "in_progress"}},
                )
                db.commit()
                media_id, bytes_copied = _copy_and_import_media(db, source_file)
                imported_media_ids.append(media_id)
                item = _get_or_create_source_item(
                    db,
                    root=root,
                    run=run,
                    relative_path=rel,
                    metadata=metadata,
                    content_hash=str(current_content_hash) if current_content_hash else None,
                )
                item.media_id = media_id
                item.sync_state = "imported"
                item.import_status = "imported"
                item.classification_status = "pending"
                item.ai_tagging_status = "pending"
                item.localization_status = "waiting_ai_tags"
                item.last_imported_at = _utcnow()
                _append_downstream_target(media_id, item.id)
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
                    current_metadata=import_metadata,
                )
                counts["imported"] += 1
                run.new_items = int(counts["imported"])
                processed_items += 1
                processed_plan_items += 1
                consecutive_failures = 0
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
                    current_metadata={**metadata, "safe_label": plan_item.get("safe_label")},
                )
                counts[item.sync_state] += 1
                if duplicate:
                    processed_items += 1
                    processed_plan_items += 1
                    consecutive_failures = 0
                else:
                    item_failure_count += 1
                    consecutive_failures += 1
                    processed_items += 1
                    processed_plan_items += 1
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
                    current_metadata={**metadata, "safe_label": plan_item.get("safe_label"), "error_code": exc.__class__.__name__},
                )
                counts["failed"] += 1
                item_failure_count += 1
                consecutive_failures += 1
                processed_items += 1
                processed_plan_items += 1
                db.commit()
            stop_reason = _budget_stop_reason(
                started_at=run.started_at or _utcnow(),
                processed_items=processed_items,
                failed_items=item_failure_count,
                consecutive_failures=consecutive_failures,
            )
            if stop_reason:
                break

        if run.status == "cancelled" and stop_reason is None:
            stop_reason = "cancelled"
        remaining_plan_items = private_items[processed_plan_items:]
        unprocessed_count = len(remaining_plan_items) if stop_reason or run.status == "cancelled" else 0
        unprocessed_import_planned_count = (
            sum(1 for item in remaining_plan_items if str(item.get("state") or "") == "import_planned")
            if unprocessed_count
            else 0
        )
        if unprocessed_count:
            deferred_reason = "not_processed_cancelled" if stop_reason == "cancelled" or run.status == "cancelled" else "not_processed_budget_stop"
            _materialize_deferred_unprocessed_items(
                db,
                root=root,
                run=run,
                plan_items=remaining_plan_items,
                reason=deferred_reason,
            )
            counts["deferred_unprocessed"] += unprocessed_count
        import_status = stop_reason or ("cancelled" if run.status == "cancelled" else "completed")
        run.summary_json = _set_stage(
            run.summary_json or {},
            "import",
            status=import_status,
            processed=processed_items,
            failed=int(counts["failed"]),
        )
        db.commit()

        classification_method = str(settings.CONTENT_CLASSIFICATION_METHOD or "").lower()
        classification_order = "classification_before_ai_tagging"
        import_stop_reason = stop_reason
        downstream_continued_after_import_stop = _import_budget_stop_allows_downstream(
            stop_reason=import_stop_reason,
            counts=counts,
            downstream_targets=downstream_targets,
            run_status=run.status,
        )
        downstream_budget_processed_baseline = 0
        downstream_budget_failed_baseline = 0
        if downstream_continued_after_import_stop:
            downstream_budget_processed_baseline = processed_items
            downstream_budget_failed_baseline = item_failure_count
            consecutive_failures = 0
            stop_reason = None

        def _check_stop_budget() -> Optional[str]:
            budget_processed_items = processed_items
            budget_failed_items = item_failure_count
            if downstream_continued_after_import_stop:
                budget_processed_items = max(0, processed_items - downstream_budget_processed_baseline)
                budget_failed_items = max(0, item_failure_count - downstream_budget_failed_baseline)
            return _budget_stop_reason(
                started_at=run.started_at or _utcnow(),
                processed_items=budget_processed_items,
                failed_items=budget_failed_items,
                consecutive_failures=consecutive_failures,
            )

        def _run_classification_stage() -> None:
            nonlocal consecutive_failures, item_failure_count, processed_items, stop_reason
            stage_processed = 0
            for target in downstream_targets:
                media_id = int(target.get("media_id") or 0)
                if stop_reason or run.status == "cancelled":
                    break
                if _is_cancel_requested(run_id):
                    run.status = "cancelled"
                    stop_reason = "cancelled"
                    break
                item = _target_source_item(target)
                if item and str(item.classification_status or "") in {"classified", "classified_reused"}:
                    counts["classified_reused"] += 1
                    consecutive_failures = 0
                    _annotate_run_item_stage(
                        db,
                        run=run,
                        item=item,
                        stage="classification",
                        status="classified_reused",
                        extra={"method": classification_method},
                    )
                    db.commit()
                    stage_processed += 1
                    processed_items += 1
                    stop_reason = _check_stop_budget()
                    if stop_reason:
                        break
                    continue
                if settings.CONTENT_CLASSIFICATION_ENABLED and classification_method != "clip":
                    blocked_reason = _heuristic_classification_ai_tag_block_reason(db, item, media_id)
                    if blocked_reason:
                        if item:
                            item.classification_status = blocked_reason[:50]
                            item.deferred_reason = blocked_reason
                            counts["classification_skipped"] += 1
                            consecutive_failures = 0
                            _annotate_run_item_stage(
                                db,
                                run=run,
                                item=item,
                                stage="classification",
                                status="skipped",
                                reason=blocked_reason,
                                extra={"method": classification_method},
                            )
                        db.commit()
                        stage_processed += 1
                        processed_items += 1
                        stop_reason = _check_stop_budget()
                        if stop_reason:
                            break
                        continue
                try:
                    result = _classify_imported_media(db, media_id)
                except Exception as exc:
                    db.rollback()
                    result = {
                        "media_id": media_id,
                        "error": "classification_failed",
                        "detail": str(exc)[:200],
                        "method": classification_method,
                    }
                if item:
                    if result.get("error"):
                        item.classification_status = "failed"
                        item.failure_reason = "classification_failed"
                        counts["classification_failed"] += 1
                        item_failure_count += 1
                        consecutive_failures += 1
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="classification",
                            status="failed",
                            reason="classification_failed",
                            extra={"method": result.get("method")},
                        )
                    elif result.get("skipped"):
                        reason = str(result.get("reason", "unknown"))
                        item.classification_status = f"skipped_{reason}"[:50]
                        counts["classification_skipped"] += 1
                        consecutive_failures = 0
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="classification",
                            status="skipped",
                            reason=reason,
                            extra={"method": result.get("method")},
                        )
                    else:
                        item.classification_status = "classified"
                        counts["classified"] += 1
                        consecutive_failures = 0
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="classification",
                            status="classified",
                            extra={"method": result.get("method"), "content_class": result.get("content_class")},
                        )
                db.commit()
                stage_processed += 1
                processed_items += 1
                stop_reason = _check_stop_budget()
                if stop_reason:
                    break
            stage_status = stop_reason or ("cancelled" if run.status == "cancelled" else "completed")
            run.summary_json = _set_stage(
                run.summary_json or {},
                "classification",
                status=stage_status,
                processed=stage_processed,
                failed=int(counts["classification_failed"]),
                method=classification_method,
                order=classification_order,
            )
            db.commit()

        def _run_ai_tagging_stage() -> None:
            nonlocal ai_provenance, consecutive_failures, item_failure_count, processed_items, stop_reason
            stage_processed = 0
            for target in downstream_targets:
                media_id = int(target.get("media_id") or 0)
                if stop_reason or run.status == "cancelled":
                    break
                if _is_cancel_requested(run_id):
                    run.status = "cancelled"
                    stop_reason = "cancelled"
                    break
                item = _target_source_item(target)
                if (
                    item
                    and str(item.ai_tagging_status or "") in {"ai_tagged", "tagged", "tagged_reused"}
                    and _has_ai_wd_tags(db, media_id)
                ):
                    media = db.get(Media, media_id)
                    eligibility, eligibility_reason = _manual_sync_media_ai_eligibility(media, item)
                    if eligibility == "non_target":
                        item.ai_tagging_status = "ai_tagging_skipped_non_target"
                        item.localization_status = "localization_not_applicable_non_target"
                        item.deferred_reason = "non_target_content_class"
                        counts["ai_tagging_skipped_non_target"] += 1
                        counts["localization_not_applicable_non_target"] += 1
                        consecutive_failures = 0
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="ai_tagging",
                            status="skipped",
                            reason="ai_tagging_skipped_non_target",
                            extra={"content_class": _media_content_class_value(media)},
                        )
                        db.commit()
                        stage_processed += 1
                        processed_items += 1
                        stop_reason = _check_stop_budget()
                        if stop_reason:
                            break
                        continue
                    if eligibility == "classification_blocked":
                        item.ai_tagging_status = "blocked_classification_not_completed"
                        item.localization_status = "blocked_classification_not_completed"
                        item.deferred_reason = "classification_not_completed"
                        counts["ai_tagging_deferred_classification_not_completed"] += 1
                        counts["localization_deferred"] += 1
                        consecutive_failures = 0
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="ai_tagging",
                            status="deferred",
                            reason=eligibility_reason,
                            extra={"content_class": _media_content_class_value(media)},
                        )
                        db.commit()
                        stage_processed += 1
                        processed_items += 1
                        stop_reason = _check_stop_budget()
                        if stop_reason:
                            break
                        continue
                    counts["ai_tagged_reused"] += 1
                    if str(item.localization_status or "") not in {
                        "localized",
                        "completed",
                        "skipped_no_localizable_tags",
                        "skipped_no_new_tags",
                        "skipped_static_coverage",
                    }:
                        item.localization_status = "waiting_localization"
                    consecutive_failures = 0
                    _annotate_run_item_stage(
                        db,
                        run=run,
                        item=item,
                        stage="ai_tagging",
                        status="tagged_reused",
                    )
                    db.commit()
                    stage_processed += 1
                    processed_items += 1
                    stop_reason = _check_stop_budget()
                    if stop_reason:
                        break
                    continue
                media = db.get(Media, media_id)
                eligibility, eligibility_reason = _manual_sync_media_ai_eligibility(media, item)
                if eligibility == "non_target":
                    if item:
                        item.ai_tagging_status = "ai_tagging_skipped_non_target"
                        item.localization_status = "localization_not_applicable_non_target"
                        item.deferred_reason = "non_target_content_class"
                        counts["ai_tagging_skipped_non_target"] += 1
                        counts["localization_not_applicable_non_target"] += 1
                        consecutive_failures = 0
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="ai_tagging",
                            status="skipped",
                            reason="ai_tagging_skipped_non_target",
                            extra={"content_class": _media_content_class_value(media)},
                        )
                    db.commit()
                    stage_processed += 1
                    processed_items += 1
                    stop_reason = _check_stop_budget()
                    if stop_reason:
                        break
                    continue
                if eligibility == "classification_blocked":
                    if item:
                        item.ai_tagging_status = "blocked_classification_not_completed"
                        item.localization_status = "blocked_classification_not_completed"
                        item.deferred_reason = "classification_not_completed"
                        counts["ai_tagging_deferred_classification_not_completed"] += 1
                        counts["localization_deferred"] += 1
                        consecutive_failures = 0
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="ai_tagging",
                            status="deferred",
                            reason=eligibility_reason,
                            extra={"content_class": _media_content_class_value(media)},
                        )
                    db.commit()
                    stage_processed += 1
                    processed_items += 1
                    stop_reason = _check_stop_budget()
                    if stop_reason:
                        break
                    continue
                try:
                    result = _ai_tag_imported_media(db, media_id)
                except Exception as exc:
                    db.rollback()
                    reason = _ai_tagging_failure_reason(exc)
                    result = {
                        "media_id": media_id,
                        "error": reason,
                        "detail": str(exc)[:200],
                    }
                if ai_provenance is None and result.get("provenance"):
                    ai_provenance = result.get("provenance")
                item = item or _target_source_item(target)
                if item:
                    if result.get("error"):
                        reason = _ai_tagging_failure_reason(result.get("error"))
                        result["error"] = reason
                        item.ai_tagging_status = f"failed_{reason}"[:50]
                        item.localization_status = "blocked_ai_tagging_failed"
                        item.failure_reason = reason
                        counts["ai_tagging_failed"] += 1
                        counts[reason] += 1
                        item_failure_count += 1
                        consecutive_failures += 1
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="ai_tagging",
                            status="failed",
                            reason=reason,
                        )
                    elif result.get("skipped"):
                        reason = str(result.get("reason", "unknown"))
                        item.ai_tagging_status = f"skipped_{reason}"[:50]
                        item.localization_status = "blocked_ai_tagging_skipped"
                        item.deferred_reason = reason
                        counts["ai_tagging_skipped"] += 1
                        consecutive_failures = 0
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="ai_tagging",
                            status="skipped",
                            reason=reason,
                        )
                    else:
                        item.ai_tagging_status = "ai_tagged"
                        item.localization_status = "waiting_localization"
                        counts["ai_tagged"] += 1
                        consecutive_failures = 0
                        _annotate_run_item_stage(
                            db,
                            run=run,
                            item=item,
                            stage="ai_tagging",
                            status="ai_tagged",
                            extra={
                                "tags_added": result.get("tags_added", 0),
                                "suggestions_added": result.get("suggestions_added", 0),
                            },
                        )
                db.commit()
                stage_processed += 1
                processed_items += 1
                stop_reason = _check_stop_budget()
                if stop_reason:
                    break
            stage_status = stop_reason or ("cancelled" if run.status == "cancelled" else "completed")
            run.summary_json = _set_stage(
                run.summary_json or {},
                "ai_tagging",
                status=stage_status,
                processed=stage_processed,
                failed=int(counts["ai_tagging_failed"]),
            )
            db.commit()

        if not stop_reason and run.status != "cancelled":
            _run_classification_stage()
            _run_ai_tagging_stage()
        else:
            stage_status = stop_reason or ("cancelled" if run.status == "cancelled" else "completed")
            run.summary_json = _set_stage(
                run.summary_json or {},
                "classification",
                status=stage_status,
                processed=0,
                failed=0,
                method=classification_method,
                order=classification_order,
            )
            run.summary_json = _set_stage(run.summary_json or {}, "ai_tagging", status=stage_status, processed=0, failed=0)
            db.commit()
        if not stop_reason and run.status not in {"cancelled", "failed"} and _is_cancel_requested(run_id):
            run.status = "cancelled"
            stop_reason = "cancelled"
            db.commit()
        if stop_reason or run.status in {"cancelled", "failed"}:
            localization_result = _manual_sync_skipped_localization_result(
                reason=stop_reason or str(run.status or "stopped"),
                media_ids=downstream_media_ids,
            )
        else:
            localization_targets: List[Dict[str, int]] = []
            for target in downstream_targets:
                item = _target_source_item(target)
                if item is None:
                    continue
                if str(item.localization_status or "") != "waiting_localization":
                    continue
                media_id = int(target.get("media_id") or 0)
                if media_id <= 0:
                    continue
                media = db.get(Media, media_id)
                eligibility, eligibility_reason = _manual_sync_media_ai_eligibility(media, item)
                if eligibility == "non_target":
                    item.localization_status = "localization_not_applicable_non_target"
                    item.deferred_reason = "non_target_content_class"
                    counts["localization_not_applicable_non_target"] += 1
                    _annotate_run_item_stage(
                        db,
                        run=run,
                        item=item,
                        stage="localization",
                        status="skipped",
                        reason="localization_not_applicable_non_target",
                        extra={"content_class": _media_content_class_value(media)},
                    )
                    continue
                if eligibility == "classification_blocked":
                    item.localization_status = "blocked_classification_not_completed"
                    item.deferred_reason = "classification_not_completed"
                    counts["localization_deferred"] += 1
                    _annotate_run_item_stage(
                        db,
                        run=run,
                        item=item,
                        stage="localization",
                        status="deferred",
                        reason=eligibility_reason,
                        extra={"content_class": _media_content_class_value(media)},
                    )
                    continue
                localization_targets.append(target)
            db.commit()
            localization_result = _manual_sync_finalize_localization(
                db,
                run=run,
                media_ids=[
                    int(target.get("media_id") or 0)
                    for target in localization_targets
                    if int(target.get("media_id") or 0) > 0
                ],
                source_item_ids=[
                    int(target.get("source_item_id") or 0)
                    for target in localization_targets
                    if int(target.get("source_item_id") or 0) > 0
                ],
                cancel_check=lambda: _is_cancel_requested(run_id),
            )
            if str(localization_result.get("status") or "") == "skipped_cancelled_run":
                run.status = "cancelled"
                stop_reason = "cancelled"
        run.summary_json = _set_stage(
            run.summary_json or {},
            "localization",
            status=str(localization_result.get("status") or "unknown"),
            processed=int(localization_result.get("dynamic_source_items_updated") or 0),
            failed=int(localization_result.get("failed") or 0),
        )
        localization_items_updated = int(localization_result.get("dynamic_source_items_updated") or 0)
        if localization_result.get("dynamic_source_items_target_status") == "localized":
            counts["localized"] += localization_items_updated
        elif localization_items_updated:
            counts["localization_deferred"] += localization_items_updated
        if int(localization_result.get("failed") or 0):
            counts["localization_failed"] += int(localization_result.get("failed") or 0)

        run.failed_items = int(
            counts["failed"]
            + counts["classification_failed"]
            + counts["ai_tagging_failed"]
            + counts["localization_failed"]
        )
        run.pending_import_items = int(unprocessed_import_planned_count)
        summary_status = stop_reason or ("cancelled" if run.status == "cancelled" else "completed")
        localization_incomplete = bool(
            int(counts["localization_deferred"])
            or str(localization_result.get("status") or "").startswith("blocked_")
            or localization_result.get("dynamic_source_items_target_status") == "deferred"
            or int(localization_result.get("tags_requiring_localization_after_runner") or 0)
        )
        if not stop_reason and run.status not in {"cancelled", "failed"} and int(counts["localization_failed"]):
            summary_status = "completed_with_localization_failures"
        elif not stop_reason and run.status not in {"cancelled", "failed"} and localization_incomplete:
            summary_status = "completed_with_followup_required"
        elif (
            not stop_reason
            and run.status not in {"cancelled", "failed"}
            and (
                int(counts["failed"])
                or int(unprocessed_import_planned_count)
                or bool(import_stop_reason)
            )
        ):
            summary_status = "completed_with_failures"
        run.summary_json = _set_stage(run.summary_json or {}, "summary", status=summary_status, processed=1, failed=0)
        _update_execute_summary(
            run,
            status=summary_status,
            current_stage="summary",
            outcome_counts=dict(sorted(counts.items())),
            localization_failed_items=int(counts["localization_failed"]),
            item_failure_count=int(item_failure_count),
            imported_media_ids=imported_media_ids,
            downstream_media_ids=downstream_media_ids,
            downstream_targets=downstream_targets,
            ai_provider_provenance=ai_provenance,
            stopped_by=stop_reason,
            stop_reason=stop_reason,
            import_stopped_by=import_stop_reason,
            downstream_continued_after_import_stop=downstream_continued_after_import_stop,
            retryable_source_failure_count=_retryable_source_failure_count(counts),
            unprocessed_count=unprocessed_count,
            unprocessed_import_planned_count=unprocessed_import_planned_count,
            budgets=_budget_policy_payload(),
            localization=localization_result,
            source_mutation_performed=False,
            app_storage_mutation_performed=bool(imported_media_ids),
            llm_calls_performed=bool(localization_result.get("llm_called")),
            external_provider_calls_performed=bool(localization_result.get("llm_called")),
            model_downloads_performed=False,
        )
        if stop_reason and stop_reason != "cancelled":
            run.status = "failed"
            run.error_message = f"Manual sync execute stopped safely: {stop_reason}"
        elif run.status != "cancelled":
            if int(counts["localization_failed"]):
                run.status = "completed_with_failures"
            elif localization_incomplete:
                run.status = "completed_with_followup_required"
            elif int(counts["failed"]) or int(unprocessed_import_planned_count) or bool(import_stop_reason):
                run.status = "completed_with_failures"
            else:
                run.status = "completed"
            run.error_message = None
        run.finished_at = _utcnow()
        root.last_checked_at = run.finished_at
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
    summary = dict(payload.get("summary") or {})
    execute = dict(summary.get("manual_sync_execute") or {})
    execute.pop("private_plan_items", None)
    summary["manual_sync_execute"] = execute
    payload["summary"] = summary
    payload["manual_sync_execute"] = execute
    return payload


def get_latest_manual_sync_execute_run(db: Session) -> Optional[DynamicSyncRun]:
    return (
        db.query(DynamicSyncRun)
        .filter(DynamicSyncRun.run_type == "manual_sync_execute")
        .order_by(DynamicSyncRun.created_at.desc(), DynamicSyncRun.id.desc())
        .first()
    )
