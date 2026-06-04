import random
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..database import get_db
from ..models import Media
from ..schemas import MediaResponse
from ..services.source_assertion_search_service import (
    apply_source_layer_filters,
    has_source_layer_filters,
    resolve_source_filter_labels,
)
from ..utils.cache import cache_response
from ..utils.media_helpers import VALID_CONTENT_CLASSES, apply_content_class_filter
from ..utils.search_parser import apply_search_criteria, parse_search_query

router = APIRouter(prefix="/api/search", tags=["search"])


def _has_query_order(parsed: dict) -> bool:
    meta = parsed.get("meta") or {}
    return bool(meta.get("order") or meta.get("sort"))


def _is_default_gallery_sort(sort: Optional[str], order: Optional[str]) -> bool:
    return (sort in (None, "uploaded_at")) and (order in (None, "desc"))


def _should_apply_external_sort(request: Request, parsed: dict, sort: Optional[str], order: Optional[str]) -> bool:
    has_external_sort_param = "sort" in request.query_params or "order" in request.query_params
    if not has_external_sort_param:
        return False
    if _has_query_order(parsed) and _is_default_gallery_sort(sort, order):
        return False
    return True


def _apply_search_sort(query, sort: Optional[str], order: Optional[str]):
    query = query.order_by(None)
    sort_column = Media.uploaded_at
    if sort == "filename":
        sort_column = Media.filename
    elif sort == "file_size":
        sort_column = Media.file_size
    elif sort == "file_type":
        sort_column = Media.file_type

    if order == "asc":
        return query.order_by(sort_column.asc(), Media.id.asc())
    return query.order_by(sort_column.desc(), Media.id.desc())

@router.get("/")
@router.get("")
@cache_response(expire=3600, key_prefix="search")
async def search_media(
    request: Request,
    q: str = Query("", description="Search query"),
    rating: Optional[str] = None,
    content_class: Optional[str] = Query(None),
    source_assertion: Optional[List[str]] = Query(None),
    source_tag: Optional[List[str]] = Query(None),
    include_source_needs_review: bool = Query(False),
    sort: Optional[str] = None,
    order: Optional[str] = None,
    page: int = 1,
    limit: int = Query(None),
    db: Session = Depends(get_db)
):
    """Search media with tag-based and read-only source-layer filters."""
    if limit is None:
        limit = settings.get_items_per_page()

    query = db.query(Media).options(selectinload(Media.tags))
    parsed = parse_search_query(q)
    
    if rating and rating != "explicit":
        rating_value = "safe" if rating == "safe" else "safe,questionable"
        
        if 'rating' not in parsed['meta']:
            parsed['meta']['rating'] = []
        parsed['meta']['rating'].append({'value': rating_value, 'negated': False})

    # Apply all criteria
    query = apply_search_criteria(query, parsed, db)
    query = apply_source_layer_filters(
        query,
        source_assertions=source_assertion,
        source_tags=source_tag,
        include_needs_review=include_source_needs_review,
    )
    query = apply_content_class_filter(query, content_class)
    if _should_apply_external_sort(request, parsed, sort, order):
        query = _apply_search_sort(query, sort, order)

    # Pagination
    offset = (page - 1) * limit
    total = query.count()
    media_list = query.offset(offset).limit(limit).all()
    
    items = [MediaResponse.model_validate(m) for m in media_list]
    
    result = {
        "items": items,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
        "query": q,
        "source_layer": "unconfirmed_source_assertion",
        "source_assertion_filters": source_assertion or [],
        "source_tag_filters": source_tag or [],
    }
    if has_source_layer_filters(source_assertion, source_tag):
        result["source_filters"] = resolve_source_filter_labels(
            db,
            source_assertions=source_assertion,
            source_tags=source_tag,
            include_needs_review=include_source_needs_review,
        )
    return result

@router.get("/random")
async def get_random_media(
    q: str = Query("", description="Search query"),
    rating: Optional[str] = None,
    source_assertion: Optional[List[str]] = Query(None),
    source_tag: Optional[List[str]] = Query(None),
    include_source_needs_review: bool = Query(False),
    db: Session = Depends(get_db)
):
    """Get a random media ID matching the search criteria"""
    query = db.query(Media.id)
    parsed = parse_search_query(q)
    
    if rating and rating != "explicit":
        rating_value = "safe" if rating == "safe" else "safe,questionable"
        
        if 'rating' not in parsed['meta']:
            parsed['meta']['rating'] = []
        parsed['meta']['rating'].append({'value': rating_value, 'negated': False})

    query = apply_search_criteria(query, parsed, db)
    query = apply_source_layer_filters(
        query,
        source_assertions=source_assertion,
        source_tags=source_tag,
        include_needs_review=include_source_needs_review,
    )
    total = query.count()
    
    if total == 0:
        return {"id": None}
    
    offset = random.randint(0, total - 1)
    media_id = query.offset(offset).limit(1).scalar()
    
    return {"id": media_id}
