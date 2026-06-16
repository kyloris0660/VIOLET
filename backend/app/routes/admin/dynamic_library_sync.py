"""Admin API for Dynamic Library Sync (Phase 4.7-S1)."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...database import get_db
from ...models import User
from ...services.dynamic_library_sync_service import (
    assert_manual_sync_allowed,
    get_dashboard_state,
    get_pending_summary,
    get_production_readiness,
    list_source_roots,
    register_source_root,
    run_update_check,
    serialize_source_root,
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


@router.get("/dynamic-library-sync")
async def dynamic_library_sync_dashboard(
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    return get_dashboard_state(db)


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
