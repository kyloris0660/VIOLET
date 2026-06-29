"""Admin API for Dynamic Library Sync (Phase 4.7-S1)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import subprocess
import sys
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


def _public_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


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
    }


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
    gui_validation_session_id: Optional[str] = Field(default=None, min_length=8, max_length=128)
    gui_validation_session_token: Optional[str] = Field(default=None, min_length=16, max_length=256)
    client_route: Optional[str] = Field(default=None, max_length=200)


class ManualSyncExecuteRequest(BaseModel):
    root_id: int = Field(..., ge=1)
    max_files: Optional[int] = Field(default=None, ge=1, le=100000)
    hydrated_only: bool = False
    stable_age_seconds: Optional[float] = Field(default=None, ge=0, le=3600)
    expected_plan_hash: str = Field(..., min_length=12, max_length=128)
    confirmation_phrase: str = Field(..., min_length=1, max_length=200)
    plan_created_at: str = Field(..., min_length=1, max_length=80)
    production_acceptance_approved: bool = False
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
        plan = plan_manual_sync_dry_run(
            db,
            source_path=source_path or "",
            source_record_id=source_record_id,
            max_files=manual_sync_execute_effective_max_files(body.max_files),
            hydrated_only=body.hydrated_only,
            stable_age_seconds=body.stable_age_seconds,
            include_private_details=False,
        )
        if gui_provenance.get("request_source") == "web_admin_gui":
            plan["gui_provenance"] = {
                **gui_provenance,
                "dry_run_requested_from_gui": True,
            }
        return plan
    except ManualSyncExecuteError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
        run = create_manual_sync_execute_run(
            db,
            root_id=body.root_id,
            max_files=body.max_files,
            hydrated_only=body.hydrated_only,
            stable_age_seconds=body.stable_age_seconds,
            expected_plan_hash=body.expected_plan_hash,
            confirmation_phrase=body.confirmation_phrase,
            plan_created_at=body.plan_created_at,
            production_acceptance_approved=body.production_acceptance_approved,
            request_source=str(gui_provenance.get("request_source") or "api_or_runner"),
            gui_validation_session_id=gui_provenance.get("gui_validation_session_id"),
            gui_validation_session_signature_valid=bool(gui_provenance.get("gui_validation_session_signature_valid")),
            client_route=str(gui_provenance.get("client_route") or _canonical_gui_route(body.client_route)),
        )
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
