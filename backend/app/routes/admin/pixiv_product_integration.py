"""Admin API for the bounded PX3 Pixiv product integration."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictInt
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...config import settings
from ...database import get_db
from ...models import User
from ...services.pixiv_metadata_vertical_slice_service import (
    repository_synthetic_pixiv_fixture,
    run_synthetic_pixiv_vertical_slice,
)
from ...services.pixiv_metadata_clustering_service import (
    PixivMetadataClusteringError,
)
from ...services.pixiv_metadata_projection_service import (
    PixivMetadataProjectionError,
)
from ...services.pixiv_product_integration_service import (
    PixivProductIntegrationError,
    apply_pixiv_product_plan,
    build_clustering_from_source_metadata_session,
    clustering_from_px1_summary,
    get_pixiv_product_run,
    list_pixiv_product_runs,
    rollback_pixiv_product_run,
    select_existing_pixiv_canary_work_ids,
)


router = APIRouter(prefix="/pixiv-product-integration")


class ProductRunRequest(BaseModel):
    mode: Literal["dry_run", "apply"] = "dry_run"
    confirm: bool = False
    confirm_phrase: str = ""
    canary_percent: StrictInt | None = None


class ProductRollbackRequest(BaseModel):
    confirm: bool = False
    confirm_phrase: str = ""


def _require_product_enabled() -> None:
    if not settings.SCV2_PX3_PRODUCT_INTEGRATION_ENABLED:
        raise HTTPException(status_code=403, detail="px3_product_integration_disabled")


def _require_apply(request: ProductRunRequest, expected_phrase: str) -> None:
    if request.mode != "apply":
        return
    if not settings.SCV2_PX3_PRODUCT_APPLY_ENABLED:
        raise HTTPException(status_code=403, detail="px3_product_apply_disabled")
    if not request.confirm or request.confirm_phrase != expected_phrase:
        raise HTTPException(status_code=400, detail="px3_apply_confirmation_invalid")


@router.get("/status")
async def product_status(
    _current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    runs = list_pixiv_product_runs(db)
    return {
        "enabled": settings.SCV2_PX3_PRODUCT_INTEGRATION_ENABLED,
        "apply_enabled": settings.SCV2_PX3_PRODUCT_APPLY_ENABLED,
        "synthetic_ui_enabled": settings.SCV2_PX3_SYNTHETIC_UI_ENABLED,
        "real_provider_execution_enabled": False,
        "run_count": len(runs),
        "active_run_count": sum(row["status"] == "active" for row in runs),
        "latest_run": runs[0] if runs else None,
        "owner_gates": {
            "controlled_provider_smoke": "not_authorized",
            "existing_database_canary": "not_authorized",
            "backup_restore": "not_authorized",
            "bounded_import_canary": "not_authorized",
        },
    }


@router.get("/runs")
async def product_runs(
    _current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    return {"runs": list_pixiv_product_runs(db)}


@router.get("/runs/{run_key:path}")
async def product_run_detail(
    run_key: str,
    _current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    detail = get_pixiv_product_run(db, run_key)
    if detail is None:
        raise HTTPException(status_code=404, detail="px3_product_run_not_found")
    return detail


@router.post("/source-metadata/run")
async def run_source_metadata_product_integration(
    request: ProductRunRequest,
    _current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Plan or apply current persisted Pixiv metadata without provider I/O."""

    _require_product_enabled()
    _require_apply(request, "APPLY_PIXIV_SOURCE_CONCEPTS")
    if request.canary_percent is None:
        raise HTTPException(
            status_code=400,
            detail="px3_existing_source_requires_bounded_canary",
        )
    try:
        selection = select_existing_pixiv_canary_work_ids(
            db, percentage=request.canary_percent
        )
        clustering = build_clustering_from_source_metadata_session(
            db,
            work_ids=selection["selected_work_ids"],
        )
        if not clustering.consumer.aggregates:
            raise PixivProductIntegrationError("px3_no_pixiv_source_metadata")
        scope_key = (
            "pixiv:existing-source-metadata:"
            f"canary:{selection['percentage']}:"
            f"{selection['canonical_fingerprint'][:16]}"
        )
        return apply_pixiv_product_plan(
            db,
            clustering,
            scope_key=scope_key,
            source_mode="existing_source_metadata",
            apply=request.mode == "apply",
            input_selection=selection,
        )
    except (
        PixivMetadataClusteringError,
        PixivMetadataProjectionError,
        PixivProductIntegrationError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/synthetic/run")
async def run_synthetic_product_integration(
    request: ProductRunRequest,
    _current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    """Exercise the product route with repository-owned data in test only."""

    _require_product_enabled()
    if not settings.SCV2_PX3_SYNTHETIC_UI_ENABLED:
        raise HTTPException(status_code=403, detail="px3_synthetic_ui_disabled")
    if request.canary_percent is not None:
        raise HTTPException(status_code=400, detail="px3_canary_only_for_existing_source")
    _require_apply(request, "APPLY_SYNTHETIC_PIXIV_PRODUCT")
    try:
        with tempfile.TemporaryDirectory(prefix="violet-px3-ui-") as temporary:
            summary = run_synthetic_pixiv_vertical_slice(
                workspace=Path(temporary),
                fixture=repository_synthetic_pixiv_fixture(),
            )
            clustering = clustering_from_px1_summary(summary)
        return apply_pixiv_product_plan(
            db,
            clustering,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=request.mode == "apply",
        )
    except (
        PixivMetadataClusteringError,
        PixivMetadataProjectionError,
        PixivProductIntegrationError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_key:path}/rollback")
async def rollback_product_run(
    run_key: str,
    request: ProductRollbackRequest,
    _current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    _require_product_enabled()
    if not settings.SCV2_PX3_PRODUCT_APPLY_ENABLED:
        raise HTTPException(status_code=403, detail="px3_product_apply_disabled")
    expected = f"ROLLBACK_PIXIV_PRODUCT:{run_key}"
    if not request.confirm or request.confirm_phrase != expected:
        raise HTTPException(status_code=400, detail="px3_rollback_confirmation_invalid")
    try:
        return rollback_pixiv_product_run(db, run_key)
    except PixivProductIntegrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
