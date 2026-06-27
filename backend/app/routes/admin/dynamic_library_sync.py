"""Admin API for Dynamic Library Sync (Phase 4.7-S1)."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
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
    hydrated_only: bool = True
    stable_age_seconds: Optional[float] = Field(default=None, ge=0, le=3600)


class ManualSyncExecuteRequest(BaseModel):
    root_id: int = Field(..., ge=1)
    max_files: Optional[int] = Field(default=None, ge=1, le=100000)
    hydrated_only: bool = True
    stable_age_seconds: Optional[float] = Field(default=None, ge=0, le=3600)
    expected_plan_hash: str = Field(..., min_length=12, max_length=128)
    confirmation_phrase: str = Field(..., min_length=1, max_length=200)
    plan_created_at: str = Field(..., min_length=1, max_length=80)
    production_acceptance_approved: bool = False


@router.get("/dynamic-library-sync")
async def dynamic_library_sync_dashboard(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    payload = get_dashboard_state(db)
    policy = dict(payload.get("default_off_policy") or {})
    policy["manual_execute_max_files_cap"] = manual_sync_execute_max_files_cap()
    policy["manual_execute_default_max_files"] = manual_sync_execute_max_files_cap()
    payload["default_off_policy"] = policy
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
        "production_execute_enabled_this_phase": False,
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
    }


@router.post("/dynamic-library-sync/manual-sync/plan")
def plan_manual_sync(
    body: ManualSyncDryRunPlanRequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    if body.hydrated_only is False:
        raise HTTPException(
            status_code=400,
            detail="manual sync dry-run requires hydrated_only=true",
        )
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
        return plan_manual_sync_dry_run(
            db,
            source_path=source_path or "",
            source_record_id=source_record_id,
            max_files=manual_sync_execute_effective_max_files(body.max_files),
            hydrated_only=body.hydrated_only,
            stable_age_seconds=body.stable_age_seconds,
            include_private_details=False,
        )
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
