from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.source_assertion_search_service import (
    list_media_source_layer,
    preview_source_assertion_promotion,
    resolve_source_filter_labels,
)

router = APIRouter(prefix="/api/source-assertions", tags=["source-assertions"])


@router.get("/media/{media_id}")
async def get_media_source_assertions(
    media_id: int,
    db: Session = Depends(get_db),
):
    """Read source-layer chips for a media item. This endpoint is read-only."""
    return list_media_source_layer(db, media_id)


@router.get("/filters")
async def resolve_source_filters(
    source_assertion: Optional[List[str]] = Query(None),
    source_tag: Optional[List[str]] = Query(None),
    include_needs_review: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Resolve source-layer URL filter values into display labels."""
    return resolve_source_filter_labels(
        db,
        source_assertions=source_assertion,
        source_tags=source_tag,
        include_needs_review=include_needs_review,
    )


@router.get("/promotion-preview")
async def get_source_assertion_promotion_preview(
    source_assertion: str = Query(...),
    include_needs_review: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Preview affected media only; F6 deliberately disables truth-path writes."""
    return preview_source_assertion_promotion(
        db,
        source_assertion,
        include_needs_review=include_needs_review,
        limit=limit,
    )
