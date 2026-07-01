"""Admin API for Dynamic Library Sync (Phase 4.7-S1)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import subprocess
import sys
import threading
import time
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...config import settings
from ...database import get_db
from ...models import DynamicSourceRoot, DynamicSyncRun, User
from ...services.dynamic_library_sync_service import (
    MANUAL_SYNC_PLAN_NO_PROGRESS_TIMEOUT_SECONDS,
    assert_manual_sync_allowed,
    get_dashboard_state,
    get_pending_summary,
    get_production_readiness,
    list_source_roots,
    plan_manual_sync_dry_run,
    register_source_root,
    run_update_check,
    serialize_source_root,
)
from ...services.job_control import build_ai_tagging_execution_profile
from ...services.manual_sync_execute_service import (
    ManualSyncExecuteError,
    create_manual_sync_execute_run,
    get_latest_manual_sync_execute_run,
    is_manual_sync_execute_active,
    manual_sync_execute_effective_max_files,
    manual_sync_execute_max_files_cap,
    request_manual_sync_execute_cancel,
    serialize_manual_sync_execute_run,
    start_manual_sync_execute_run,
)

router = APIRouter()

GUI_VALIDATION_SESSION_TTL_SECONDS = 2 * 60 * 60
GUI_VALIDATION_CLIENT_HEADER = "x-violet-gui-client"
GUI_VALIDATION_CLIENT_VALUE = "web-admin-manual-sync-v1"
GUI_MANUAL_SYNC_ROUTE = "/admin?tab=content#dynamic-library-sync-section"
MANUAL_SYNC_PLAN_PROGRESS_RETAIN_SECONDS = 2 * 60 * 60
MANUAL_SYNC_PLAN_CANCEL_STALE_SECONDS = MANUAL_SYNC_PLAN_NO_PROGRESS_TIMEOUT_SECONDS + 60

_MANUAL_PLAN_LOCK = threading.Lock()
_MANUAL_PLAN_PROGRESS: dict[str, dict[str, Any]] = {}
_MANUAL_PLAN_CANCELLED: set[str] = set()
_ACTIVE_MANUAL_PLAN_BY_KEY: dict[str, str] = {}
_GUI_PLAN_BINDINGS: dict[str, dict[str, Any]] = {}


def _public_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _now_epoch() -> float:
    return time.time()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _manual_plan_key(*, root_id: Optional[int], source_path: Optional[str]) -> str:
    if root_id is not None:
        return f"root:{int(root_id)}"
    return f"path:{_public_hash(str(source_path or ''))}"


def _cleanup_manual_plan_progress_locked(now_epoch: Optional[float] = None) -> None:
    now_value = _now_epoch() if now_epoch is None else now_epoch
    stale_ids = [
        plan_id
        for plan_id, progress in _MANUAL_PLAN_PROGRESS.items()
        if now_value - float(progress.get("updated_at_epoch") or progress.get("started_at_epoch") or now_value)
        > MANUAL_SYNC_PLAN_PROGRESS_RETAIN_SECONDS
    ]
    for plan_id in stale_ids:
        _MANUAL_PLAN_PROGRESS.pop(plan_id, None)
        _MANUAL_PLAN_CANCELLED.discard(plan_id)
    active_stale = [
        key for key, plan_id in _ACTIVE_MANUAL_PLAN_BY_KEY.items() if plan_id not in _MANUAL_PLAN_PROGRESS
    ]
    for key in active_stale:
        _ACTIVE_MANUAL_PLAN_BY_KEY.pop(key, None)


def _public_manual_plan_progress(plan_id: str) -> dict:
    with _MANUAL_PLAN_LOCK:
        progress_ref = _MANUAL_PLAN_PROGRESS.get(plan_id)
        if progress_ref and progress_ref.get("status") == "cancelling":
            now_value = _now_epoch()
            updated_at = float(progress_ref.get("updated_at_epoch") or progress_ref.get("started_at_epoch") or now_value)
            if now_value - updated_at > MANUAL_SYNC_PLAN_CANCEL_STALE_SECONDS:
                progress_ref.update(
                    {
                        "status": "cancel_failed",
                        "phase": "cancel_failed",
                        "updated_at": _now_iso(),
                        "updated_at_epoch": now_value,
                        "ended_at": _now_iso(),
                        "error_code": "manual_sync_plan_cancel_stale",
                        "message": "Cancel was requested but the planner did not report a terminal state within the watchdog window. Audit server logs before retrying.",
                    }
                )
                plan_key = progress_ref.get("plan_key")
                if plan_key and _ACTIVE_MANUAL_PLAN_BY_KEY.get(str(plan_key)) == plan_id:
                    _ACTIVE_MANUAL_PLAN_BY_KEY.pop(str(plan_key), None)
        progress = dict(_MANUAL_PLAN_PROGRESS.get(plan_id) or {})
    if not progress:
        raise HTTPException(status_code=404, detail={"code": "manual_sync_plan_progress_not_found"})
    started = float(progress.get("started_at_epoch") or _now_epoch())
    progress["elapsed_seconds"] = round(max(0.0, _now_epoch() - started), 3)
    progress.pop("source_path", None)
    return progress


def _seed_manual_plan_progress(
    *,
    plan_request_id: str,
    plan_key: str,
    root_id: Optional[int],
    source_path: Optional[str],
    max_files: Optional[int],
    hydrated_only: bool,
    request_source: str,
) -> None:
    now_value = _now_epoch()
    with _MANUAL_PLAN_LOCK:
        _cleanup_manual_plan_progress_locked(now_value)
        active_id = _ACTIVE_MANUAL_PLAN_BY_KEY.get(plan_key)
        active = _MANUAL_PLAN_PROGRESS.get(active_id or "")
        if active and active.get("status") in {"running", "cancelling"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "manual_sync_plan_already_active",
                    "message": "A manual sync plan is already active for this source root. Watch or cancel the active plan before retrying.",
                    "active_plan_request_id": active_id,
                    "active_status": active.get("status"),
                    "active_phase": active.get("phase"),
                },
            )
        _ACTIVE_MANUAL_PLAN_BY_KEY[plan_key] = plan_request_id
        _MANUAL_PLAN_CANCELLED.discard(plan_request_id)
        _MANUAL_PLAN_PROGRESS[plan_request_id] = {
            "plan_request_id": plan_request_id,
            "plan_key": plan_key,
            "status": "running",
            "phase": "queued",
            "root_id": root_id,
            "source_public_id": _public_hash(str(source_path or root_id or "")),
            "source_path": source_path,
            "max_files": max_files,
            "hydrated_only": hydrated_only,
            "request_source": request_source,
            "endpoint": "/api/admin/dynamic-library-sync/manual-sync/plan",
            "started_at": _now_iso(),
            "started_at_epoch": now_value,
            "updated_at": _now_iso(),
            "updated_at_epoch": now_value,
            "counts": {},
            "events": [],
            "cancel_requested": False,
        }


def _update_manual_plan_progress(plan_request_id: str, updates: dict) -> None:
    event = {
        "at": _now_iso(),
        "phase": updates.get("phase"),
        "status": updates.get("status") or "running",
        "current_item_index": updates.get("current_item_index"),
        "current_item_label": updates.get("current_item_label"),
    }
    with _MANUAL_PLAN_LOCK:
        progress = _MANUAL_PLAN_PROGRESS.get(plan_request_id)
        if not progress:
            return
        if progress.get("status") == "cancelling" and updates.get("status") == "running":
            updates = {**updates, "status": "cancelling", "cancel_requested": True}
        progress.update(updates)
        progress["updated_at"] = _now_iso()
        progress["updated_at_epoch"] = _now_epoch()
        events = list(progress.get("events") or [])
        events.append({key: value for key, value in event.items() if value is not None})
        progress["events"] = events[-12:]


def _manual_plan_cancel_requested(plan_request_id: str) -> bool:
    with _MANUAL_PLAN_LOCK:
        return plan_request_id in _MANUAL_PLAN_CANCELLED


def _finish_manual_plan_progress(
    *,
    plan_request_id: str,
    plan_key: str,
    status: str,
    phase: str,
    error_code: Optional[str] = None,
    message: Optional[str] = None,
    counts: Optional[dict] = None,
    limits: Optional[dict] = None,
) -> None:
    with _MANUAL_PLAN_LOCK:
        progress = _MANUAL_PLAN_PROGRESS.get(plan_request_id)
        if progress is not None:
            progress.update(
                {
                    "status": status,
                    "phase": phase,
                    "updated_at": _now_iso(),
                    "updated_at_epoch": _now_epoch(),
                    "ended_at": _now_iso(),
                    "error_code": error_code,
                    "message": message,
                }
            )
            if counts is not None:
                progress["counts"] = counts
            if limits is not None:
                progress["limits"] = limits
            events = list(progress.get("events") or [])
            events.append({"at": _now_iso(), "phase": phase, "status": status})
            progress["events"] = events[-12:]
        if _ACTIVE_MANUAL_PLAN_BY_KEY.get(plan_key) == plan_request_id:
            _ACTIVE_MANUAL_PLAN_BY_KEY.pop(plan_key, None)


def _canonical_gui_route(route: Optional[str]) -> str:
    value = str(route or GUI_MANUAL_SYNC_ROUTE).strip()
    if not value.startswith("/admin"):
        return GUI_MANUAL_SYNC_ROUTE
    if "#dynamic-library-sync-section" not in value:
        return GUI_MANUAL_SYNC_ROUTE
    return value


def _gui_session_message(session_id: str, client_route: str, expires_at: int) -> str:
    return "\n".join(
        [
            "violet-manual-sync-gui-session-v1",
            str(session_id),
            _canonical_gui_route(client_route),
            str(int(expires_at)),
        ]
    )


def _sign_gui_session(session_id: str, client_route: str, expires_at: int) -> str:
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8", errors="ignore"),
        _gui_session_message(session_id, client_route, expires_at).encode("utf-8", errors="ignore"),
        hashlib.sha256,
    ).hexdigest()
    return f"v1.{int(expires_at)}.{digest}"


def _validate_gui_session_token(
    *,
    session_id: Optional[str],
    token: Optional[str],
    client_route: Optional[str],
) -> bool:
    if not session_id or not token:
        return False
    parts = str(token).split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return False
    try:
        expires_at = int(parts[1])
    except ValueError:
        return False
    if expires_at < int(time.time()):
        return False
    expected = _sign_gui_session(str(session_id), _canonical_gui_route(client_route), expires_at)
    return hmac.compare_digest(expected, str(token))


def _gui_session_token_expires_at(token: Optional[str]) -> Optional[int]:
    parts = str(token or "").split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _gui_provenance_from_body(body: Any) -> dict:
    client_route = _canonical_gui_route(getattr(body, "client_route", None))
    session_id = getattr(body, "gui_validation_session_id", None)
    token = getattr(body, "gui_validation_session_token", None)
    token_valid = _validate_gui_session_token(
        session_id=session_id,
        token=token,
        client_route=client_route,
    )
    if not token_valid:
        return {
            "request_source": "api_or_runner",
            "client_route": client_route,
            "gui_validation_session_signature_valid": False,
        }
    return {
        "request_source": "web_admin_gui",
        "gui_validation_session_id": str(session_id),
        "gui_validation_session_id_hash": _public_hash(str(session_id)),
        "client_route": client_route,
        "gui_validation_session_signature_valid": True,
        "gui_validation_session_expires_at": _gui_session_token_expires_at(token),
    }


def _bind_gui_plan_flow(
    *,
    gui_provenance: dict[str, Any],
    plan_request_id: str,
    plan_hash: str,
    root_id: Optional[int],
) -> dict[str, Any]:
    session_id = str(gui_provenance.get("gui_validation_session_id") or "")
    expires_at = int(gui_provenance.get("gui_validation_session_expires_at") or (int(time.time()) + 60))
    binding = {
        "session_id": session_id,
        "session_id_hash": _public_hash(session_id),
        "plan_request_id": str(plan_request_id),
        "plan_request_id_hash": _public_hash(str(plan_request_id)),
        "plan_hash": str(plan_hash),
        "plan_hash_prefix": str(plan_hash)[:16],
        "root_id": int(root_id) if root_id is not None else None,
        "client_route": str(gui_provenance.get("client_route") or GUI_MANUAL_SYNC_ROUTE),
        "created_at_epoch": int(time.time()),
        "expires_at_epoch": expires_at,
        "used_for_execute": False,
    }
    with _MANUAL_PLAN_LOCK:
        _GUI_PLAN_BINDINGS[session_id] = binding
    return binding


def _validate_gui_plan_flow_for_execute(
    *,
    gui_provenance: dict[str, Any],
    plan_request_id: Optional[str],
    plan_hash: str,
    root_id: int,
) -> dict[str, Any]:
    session_id = str(gui_provenance.get("gui_validation_session_id") or "")
    with _MANUAL_PLAN_LOCK:
        binding = dict(_GUI_PLAN_BINDINGS.get(session_id) or {})
    if not binding:
        raise ManualSyncExecuteError(
            "gui_plan_flow_not_bound",
            "Web Admin execute requires a plan generated by the same GUI session before Execute.",
            status_code=409,
        )
    if bool(binding.get("used_for_execute")):
        raise ManualSyncExecuteError(
            "gui_plan_flow_already_used",
            "Web Admin plan flow was already used for Execute. Re-run Start manual sync before another Execute.",
            status_code=409,
        )
    if int(binding.get("expires_at_epoch") or 0) < int(time.time()):
        raise ManualSyncExecuteError(
            "gui_plan_flow_expired",
            "Web Admin plan flow expired. Re-run Start manual sync before Execute.",
            status_code=409,
        )
    if str(binding.get("plan_request_id") or "") != str(plan_request_id or ""):
        raise ManualSyncExecuteError(
            "gui_plan_flow_request_mismatch",
            "Web Admin execute plan request id does not match the GUI dry-run flow.",
            status_code=409,
        )
    if str(binding.get("plan_hash") or "") != str(plan_hash or ""):
        raise ManualSyncExecuteError(
            "gui_plan_flow_hash_mismatch",
            "Web Admin execute plan hash does not match the GUI dry-run flow.",
            status_code=409,
        )
    if int(binding.get("root_id") or 0) != int(root_id):
        raise ManualSyncExecuteError(
            "gui_plan_flow_root_mismatch",
            "Web Admin execute root does not match the GUI dry-run flow.",
            status_code=409,
        )
    return binding


def _mark_gui_plan_flow_used(session_id: Optional[str]) -> None:
    if not session_id:
        return
    with _MANUAL_PLAN_LOCK:
        if session_id in _GUI_PLAN_BINDINGS:
            _GUI_PLAN_BINDINGS[session_id]["used_for_execute"] = True


def _runtime_provenance() -> dict:
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
        "python_executable_name": os.path.basename(sys.executable),
    }


class RegisterSourceRootRequest(BaseModel):
    path: str = Field(..., min_length=1)
    label: Optional[str] = None
    sync_threshold: Optional[int] = Field(default=None, ge=1)
    notes: Optional[str] = None


class UpdateCheckRequest(BaseModel):
    root_ids: Optional[List[int]] = None
    max_files: Optional[int] = Field(default=None, ge=1, le=100000)
    hydrated_only: bool = True


class ManualSyncDryRunPlanRequest(BaseModel):
    root_id: Optional[int] = Field(default=None, ge=1)
    source_path: Optional[str] = Field(default=None, min_length=1)
    max_files: Optional[int] = Field(default=None, ge=1, le=100000)
    hydrated_only: bool = False
    stable_age_seconds: Optional[float] = Field(default=None, ge=0, le=3600)
    plan_mode: str = Field(default="incremental", max_length=64)
    plan_request_id: Optional[str] = Field(default=None, min_length=8, max_length=128)
    gui_validation_session_id: Optional[str] = Field(default=None, min_length=8, max_length=128)
    gui_validation_session_token: Optional[str] = Field(default=None, min_length=16, max_length=256)
    client_route: Optional[str] = Field(default=None, max_length=200)


class ManualSyncExecuteRequest(BaseModel):
    root_id: int = Field(..., ge=1)
    max_files: Optional[int] = Field(default=None, ge=1, le=100000)
    hydrated_only: bool = False
    stable_age_seconds: Optional[float] = Field(default=None, ge=0, le=3600)
    plan_mode: str = Field(default="incremental", max_length=64)
    expected_plan_hash: str = Field(..., min_length=12, max_length=128)
    confirmation_phrase: str = Field(default="", max_length=200)
    operator_confirmation_statement: Optional[str] = Field(default=None, max_length=300)
    plan_created_at: str = Field(..., min_length=1, max_length=80)
    production_acceptance_approved: bool = False
    plan_request_id: Optional[str] = Field(default=None, min_length=8, max_length=128)
    gui_validation_session_id: Optional[str] = Field(default=None, min_length=8, max_length=128)
    gui_validation_session_token: Optional[str] = Field(default=None, min_length=16, max_length=256)
    client_route: Optional[str] = Field(default=None, max_length=200)


class ManualSyncGuiSessionRequest(BaseModel):
    client_route: Optional[str] = Field(default=None, max_length=200)


@router.post("/dynamic-library-sync/manual-sync/gui-session")
def create_manual_sync_gui_session(
    body: ManualSyncGuiSessionRequest,
    request: Request,
    current_user: User = Depends(require_admin_mode),
):
    client_header = request.headers.get(GUI_VALIDATION_CLIENT_HEADER)
    if client_header != GUI_VALIDATION_CLIENT_VALUE:
        raise HTTPException(
            status_code=400,
            detail="Web Admin GUI session creation requires the manual sync browser client marker.",
        )
    client_route = _canonical_gui_route(body.client_route)
    session_id = f"gui-{secrets.token_urlsafe(24)}"
    expires_at = int(time.time()) + GUI_VALIDATION_SESSION_TTL_SECONDS
    token = _sign_gui_session(session_id, client_route, expires_at)
    return {
        "request_source": "web_admin_gui",
        "gui_validation_session_id": session_id,
        "gui_validation_session_id_hash": _public_hash(session_id),
        "gui_validation_session_token": token,
        "gui_validation_session_signature_valid": True,
        "client_route": client_route,
        "expires_at_epoch": expires_at,
    }


@router.get("/dynamic-library-sync")
async def dynamic_library_sync_dashboard(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    payload = get_dashboard_state(db)
    policy = dict(payload.get("default_off_policy") or {})
    policy["manual_execute_max_files_cap"] = manual_sync_execute_max_files_cap()
    policy["manual_execute_default_max_files"] = manual_sync_execute_max_files_cap()
    policy["manual_sync_execute_enabled"] = settings.DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED
    policy["production_execute_enabled_this_phase"] = settings.DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED
    payload["default_off_policy"] = policy
    payload["runtime_provenance"] = _runtime_provenance()
    return payload


@router.get("/dynamic-library-sync/source-roots")
async def get_dynamic_source_roots(
    include_inactive: bool = False,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    return [
        serialize_source_root(root)
        for root in list_source_roots(db, include_inactive=include_inactive)
    ]


@router.post("/dynamic-library-sync/source-roots")
async def create_dynamic_source_root(
    body: RegisterSourceRootRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    try:
        root = register_source_root(
            db,
            path=body.path,
            label=body.label,
            sync_threshold=body.sync_threshold,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return serialize_source_root(root)


@router.post("/dynamic-library-sync/check")
async def run_dynamic_update_check(
    body: UpdateCheckRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    try:
        return run_update_check(
            db,
            root_ids=body.root_ids,
            max_files=body.max_files,
            hydrated_only=body.hydrated_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/dynamic-library-sync/pending-summary")
async def get_dynamic_pending_summary(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    return get_pending_summary(db)


@router.get("/dynamic-library-sync/readiness")
async def get_dynamic_readiness(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    return get_production_readiness(db)


@router.get("/dynamic-library-sync/manual-sync/status")
async def get_manual_sync_foundation_status(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    from ...services.ai_tagging_job_service import is_ai_job_active
    from ...services.classification_job_service import is_classification_job_active

    return {
        "dry_run_plan_available": True,
        "manual_sync_execution_enabled": settings.DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED,
        "production_execute_enabled_this_phase": settings.DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED,
        "manual_execute_max_files_cap": manual_sync_execute_max_files_cap(),
        "manual_execute_default_max_files": manual_sync_execute_max_files_cap(),
        "automatic_sync_enabled": False,
        "scheduled_sync_enabled": False,
        "single_active_ai_execution_guard": True,
        "single_active_manual_sync_execute_guard": True,
        "manual_sync_execute_active": is_manual_sync_execute_active(),
        "ai_job_active": is_ai_job_active(),
        "classification_job_active": is_classification_job_active(),
        "pending_summary": get_pending_summary(db),
        "ai_execution_profile": build_ai_tagging_execution_profile(settings).to_public_dict(),
        "runtime_provenance": _runtime_provenance(),
    }


@router.get("/dynamic-library-sync/manual-sync/plan-progress/{plan_request_id}")
def get_manual_sync_plan_progress(
    plan_request_id: str,
    current_user: User = Depends(require_admin_mode),
):
    return _public_manual_plan_progress(plan_request_id)


@router.post("/dynamic-library-sync/manual-sync/plan-progress/{plan_request_id}/cancel")
def cancel_manual_sync_plan_progress(
    plan_request_id: str,
    current_user: User = Depends(require_admin_mode),
):
    with _MANUAL_PLAN_LOCK:
        progress = _MANUAL_PLAN_PROGRESS.get(plan_request_id)
        if not progress:
            raise HTTPException(status_code=404, detail={"code": "manual_sync_plan_progress_not_found"})
        _MANUAL_PLAN_CANCELLED.add(plan_request_id)
        progress["cancel_requested"] = True
        if progress.get("status") == "running":
            progress["status"] = "cancelling"
        progress["updated_at"] = _now_iso()
        progress["updated_at_epoch"] = _now_epoch()
        events = list(progress.get("events") or [])
        events.append({"at": _now_iso(), "phase": progress.get("phase"), "status": "cancelling"})
        progress["events"] = events[-12:]
    return _public_manual_plan_progress(plan_request_id)


@router.post("/dynamic-library-sync/manual-sync/plan")
def plan_manual_sync(
    body: ManualSyncDryRunPlanRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    if bool(body.root_id) == bool(body.source_path):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of root_id or source_path.",
        )

    plan_request_id = body.plan_request_id or f"plan-{secrets.token_urlsafe(16)}"
    plan_key = _manual_plan_key(root_id=body.root_id, source_path=body.source_path)
    try:
        source_path = body.source_path
        source_record_id = None
        if body.root_id:
            root = db.get(DynamicSourceRoot, body.root_id)
            if root is None:
                raise ValueError("dynamic source record not found")
            source_path = root.root_path
            source_record_id = root.id
        gui_provenance = _gui_provenance_from_body(body)
        if (body.gui_validation_session_id or body.gui_validation_session_token) and not gui_provenance.get(
            "gui_validation_session_signature_valid"
        ):
            raise ManualSyncExecuteError(
                "gui_validation_session_invalid",
                "Web Admin GUI plan requires a valid signed GUI validation session. Refresh the page and retry.",
                status_code=409,
            )
        fields_set = body.model_fields_set if hasattr(body, "model_fields_set") else getattr(body, "__fields_set__", set())
        hydrated_only = bool(body.hydrated_only)
        if str(body.plan_mode or "").strip().lower() == "advanced_full_rescan" and "hydrated_only" not in fields_set:
            hydrated_only = True
        _seed_manual_plan_progress(
            plan_request_id=plan_request_id,
            plan_key=plan_key,
            root_id=source_record_id,
            source_path=source_path,
            max_files=manual_sync_execute_effective_max_files(body.max_files),
            hydrated_only=hydrated_only,
            request_source=str(gui_provenance.get("request_source") or "api_or_runner"),
        )
        plan = plan_manual_sync_dry_run(
            db,
            source_path=source_path or "",
            source_record_id=source_record_id,
            max_files=manual_sync_execute_effective_max_files(body.max_files),
            hydrated_only=hydrated_only,
            stable_age_seconds=body.stable_age_seconds,
            include_private_details=False,
            progress_callback=lambda payload: _update_manual_plan_progress(plan_request_id, payload),
            cancel_check=lambda: _manual_plan_cancel_requested(plan_request_id),
            plan_mode=body.plan_mode,
        )
        _finish_manual_plan_progress(
            plan_request_id=plan_request_id,
            plan_key=plan_key,
            status="completed",
            phase="completed",
            counts=plan.get("counts"),
            limits=plan.get("limits"),
        )
        if (plan.get("limits") or {}).get("plan_cancelled"):
            raise ManualSyncExecuteError(
                "manual_sync_plan_cancelled",
                "Manual sync plan was cancelled before completion.",
                status_code=409,
            )
        if gui_provenance.get("request_source") == "web_admin_gui":
            binding = _bind_gui_plan_flow(
                gui_provenance=gui_provenance,
                plan_request_id=plan_request_id,
                plan_hash=str((plan.get("integrity") or {}).get("plan_hash") or ""),
                root_id=source_record_id,
            )
            plan["gui_provenance"] = {
                **gui_provenance,
                "dry_run_requested_from_gui": True,
                "plan_request_id": plan_request_id,
                "plan_request_id_hash": binding.get("plan_request_id_hash"),
                "plan_hash_bound": True,
                "gui_plan_flow_bound": True,
            }
        plan["plan_request_id"] = plan_request_id
        return plan
    except ManualSyncExecuteError as exc:
        _finish_manual_plan_progress(
            plan_request_id=plan_request_id,
            plan_key=plan_key,
            status="cancelled" if exc.code == "manual_sync_plan_cancelled" else "failed",
            phase="cancelled" if exc.code == "manual_sync_plan_cancelled" else "failed",
            error_code=exc.code,
            message=str(exc),
        )
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})
    except ValueError as exc:
        _finish_manual_plan_progress(
            plan_request_id=plan_request_id,
            plan_key=plan_key,
            status="failed",
            phase="failed",
            error_code="manual_sync_plan_value_error",
            message=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - release GUI plan locks on unexpected planner failures.
        _finish_manual_plan_progress(
            plan_request_id=plan_request_id,
            plan_key=plan_key,
            status="failed",
            phase="failed",
            error_code="manual_sync_plan_unexpected_error",
            message="Manual sync plan failed unexpectedly. Check server logs before retrying.",
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "manual_sync_plan_unexpected_error",
                "message": "Manual sync plan failed unexpectedly. Check server logs before retrying.",
            },
        ) from exc


@router.post("/dynamic-library-sync/manual-sync/execute")
def execute_manual_sync(
    body: ManualSyncExecuteRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    try:
        gui_provenance = _gui_provenance_from_body(body)
        if (body.gui_validation_session_id or body.gui_validation_session_token) and not gui_provenance.get(
            "gui_validation_session_signature_valid"
        ):
            raise ManualSyncExecuteError(
                "gui_validation_session_invalid",
                "Web Admin GUI execute requires a valid signed GUI validation session. Re-run the GUI plan before executing.",
                status_code=409,
            )
        gui_plan_binding: dict[str, Any] = {}
        if gui_provenance.get("request_source") == "web_admin_gui":
            gui_plan_binding = _validate_gui_plan_flow_for_execute(
                gui_provenance=gui_provenance,
                plan_request_id=body.plan_request_id,
                plan_hash=body.expected_plan_hash,
                root_id=body.root_id,
            )
        run = create_manual_sync_execute_run(
            db,
            root_id=body.root_id,
            max_files=body.max_files,
            hydrated_only=body.hydrated_only,
            stable_age_seconds=body.stable_age_seconds,
            plan_mode=body.plan_mode,
            expected_plan_hash=body.expected_plan_hash,
            confirmation_phrase=body.confirmation_phrase,
            operator_confirmation_statement=body.operator_confirmation_statement,
            plan_created_at=body.plan_created_at,
            production_acceptance_approved=body.production_acceptance_approved,
            request_source=str(gui_provenance.get("request_source") or "api_or_runner"),
            gui_validation_session_id=gui_provenance.get("gui_validation_session_id"),
            gui_validation_session_signature_valid=bool(gui_provenance.get("gui_validation_session_signature_valid")),
            client_route=str(gui_provenance.get("client_route") or _canonical_gui_route(body.client_route)),
            gui_plan_request_id=body.plan_request_id,
            gui_plan_hash_bound=bool(gui_plan_binding),
            gui_plan_flow_verified=bool(gui_plan_binding),
            runtime_provenance=_runtime_provenance(),
        )
        if gui_plan_binding:
            _mark_gui_plan_flow_used(gui_provenance.get("gui_validation_session_id"))
        start_manual_sync_execute_run(run.id)
        return serialize_manual_sync_execute_run(run)
    except ManualSyncExecuteError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/dynamic-library-sync/manual-sync/jobs/latest")
def get_latest_manual_sync_job(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    run = get_latest_manual_sync_execute_run(db)
    if run is None:
        return {"job": None}
    return {"job": serialize_manual_sync_execute_run(run)}


@router.get("/dynamic-library-sync/manual-sync/jobs/{run_id}")
def get_manual_sync_job(
    run_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    run = db.get(DynamicSyncRun, run_id)
    if run is None or run.run_type != "manual_sync_execute":
        raise HTTPException(status_code=404, detail="manual sync execute job not found")
    return serialize_manual_sync_execute_run(run)


@router.post("/dynamic-library-sync/manual-sync/jobs/{run_id}/cancel")
def cancel_manual_sync_job(
    run_id: int,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    run = db.get(DynamicSyncRun, run_id)
    if run is None or run.run_type != "manual_sync_execute":
        raise HTTPException(status_code=404, detail="manual sync execute job not found")
    request_manual_sync_execute_cancel(run_id)
    if run.status in {"pending", "running"}:
        run.status = "cancelling"
        db.commit()
        db.refresh(run)
    return serialize_manual_sync_execute_run(run)


@router.post("/dynamic-library-sync/sync-pending")
async def sync_dynamic_pending_items(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    try:
        assert_manual_sync_allowed()
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(
        status_code=501,
        detail="S1 prepares manual sync state only; import execution belongs to approved S2.",
    )
