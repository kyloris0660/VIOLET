"""Read-only SourceConcept APIs for source-layer search/evidence UI."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.source_concept_search_service import (
    get_source_concept_detail,
    list_media_source_concepts,
    preview_source_concept_promotion,
)

router = APIRouter(prefix="/api/source-concepts", tags=["source-concepts"])


@router.get("/media/{media_id}")
async def get_media_source_concepts(media_id: int, db: Session = Depends(get_db)):
    return {
        "media_id": media_id,
        "source_concepts": list_media_source_concepts(db, media_id),
    }


@router.get("/{concept_id}/promotion-preview")
async def get_source_concept_promotion_preview(
    concept_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    preview = preview_source_concept_promotion(db, concept_id, limit=limit)
    if preview is None:
        raise HTTPException(status_code=404, detail="SourceConcept not found")
    return preview


@router.get("/{concept_id}")
async def get_source_concept(concept_id: int, db: Session = Depends(get_db)):
    detail = get_source_concept_detail(db, concept_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="SourceConcept not found")
    return detail
