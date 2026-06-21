"""AI tagging service — orchestrates WDv3 tagger predictions and writes
tag provenance data through the tag service layer.

This module does NOT import the ONNX model at module level so that the
application can start even when the model is unavailable.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Media, Tag, blombooru_media_tags
from ..routes.media import get_or_create_tags, update_tag_counts
from ..services.tag_service import add_ai_tag_to_media

logger = logging.getLogger(__name__)

WD_CATEGORY_MAP = {
    "general": "general",
    "character": "character",
    "rating": "meta",
}


def _get_tagger():
    """Late import to avoid startup failure when deps are missing."""
    from ..services.wd_tagger import get_wd_tagger
    return get_wd_tagger()


def _threshold_summary() -> Dict[str, float]:
    return {
        "general_threshold": settings.AI_GENERAL_THRESHOLD,
        "character_threshold": settings.AI_CHARACTER_THRESHOLD,
        "rating_threshold": settings.AI_RATING_THRESHOLD,
        "suggestion_threshold": settings.AI_SUGGESTION_THRESHOLD,
    }


def get_ai_tagging_runtime_provenance(tagger: Any = None) -> Dict[str, Any]:
    """Return public-safe model/provider/load-control provenance."""
    tagger = tagger or _get_tagger()
    model_name = settings.AI_MODEL_NAME
    thresholds = _threshold_summary()
    if hasattr(tagger, "get_runtime_provenance"):
        return tagger.get_runtime_provenance(
            model_name=model_name,
            thresholds=thresholds,
            batch_size=getattr(settings, "AI_TAGGING_BATCH_SIZE", None),
        )
    return {
        "model_name": model_name,
        "model_repo_id": None,
        "thresholds": thresholds,
        "provider": {},
        "load_control": {},
        "batch_size": getattr(settings, "AI_TAGGING_BATCH_SIZE", None),
        "tagger_version_source": "unknown",
        "backend": "unknown",
    }


def check_model_status() -> Dict[str, Any]:
    """Return model availability information without loading the model."""
    result: Dict[str, Any] = {
        "enabled": settings.AI_TAGGING_ENABLED,
        "model_name": settings.AI_MODEL_NAME,
        "available": False,
        "loaded": False,
        "error": None,
        "config": {
            "general_threshold": settings.AI_GENERAL_THRESHOLD,
            "character_threshold": settings.AI_CHARACTER_THRESHOLD,
            "rating_threshold": settings.AI_RATING_THRESHOLD,
            "suggestion_threshold": settings.AI_SUGGESTION_THRESHOLD,
            "batch_max_items": settings.AI_TAGGING_BATCH_MAX_ITEMS,
            "batch_size": settings.AI_TAGGING_BATCH_SIZE,
        },
        "provenance": None,
    }

    try:
        tagger = _get_tagger()
        result["available"] = True
        result["loaded"] = tagger.is_loaded and tagger.current_model == settings.AI_MODEL_NAME
        result["provenance"] = get_ai_tagging_runtime_provenance(tagger)

        try:
            import huggingface_hub
            from ..services.wd_tagger import WDTagger
            model_repo = WDTagger.AVAILABLE_MODELS.get(settings.AI_MODEL_NAME)
            if model_repo:
                try:
                    huggingface_hub.hf_hub_download(
                        model_repo, WDTagger.MODEL_FILENAME, local_files_only=True
                    )
                    huggingface_hub.hf_hub_download(
                        model_repo, WDTagger.LABEL_FILENAME, local_files_only=True
                    )
                    result["model_downloaded"] = True
                except Exception:
                    result["model_downloaded"] = False
        except ImportError:
            result["model_downloaded"] = False

    except ImportError as exc:
        result["error"] = f"Missing dependency: {exc}"
    except Exception as exc:
        result["error"] = str(exc)

    return result


def _resolve_media_file(media: Media) -> Optional[Path]:
    """Resolve the on-disk path for a media item."""
    file_path = settings.resolve_storage_path(media.path)
    if file_path and file_path.exists():
        return file_path
    direct = settings.ORIGINAL_DIR / media.filename
    if direct.exists():
        return direct
    return None


def _determine_thresholds(category: str):
    """Return (confirm_threshold, suggestion_threshold) for a WD category."""
    if category == "character":
        return settings.AI_CHARACTER_THRESHOLD, settings.AI_SUGGESTION_THRESHOLD
    if category == "rating":
        return settings.AI_RATING_THRESHOLD, settings.AI_SUGGESTION_THRESHOLD
    return settings.AI_GENERAL_THRESHOLD, settings.AI_SUGGESTION_THRESHOLD


def run_ai_tagging(
    db: Session,
    media_id: int,
    *,
    dry_run: bool = False,
    force_suggestions: bool = False,
    local_files_only: bool = False,
) -> Dict[str, Any]:
    """Run WDv3 inference on a single media item and write tags.

    Returns a summary dict with keys: media_id, tags_added, suggestions_added,
    skipped_locked, ignored_low_confidence, predictions (list of dicts).
    """
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        return {"media_id": media_id, "error": "Media not found"}

    file_path = _resolve_media_file(media)
    if not file_path:
        return {"media_id": media_id, "error": f"File not found: {media.filename}"}

    tagger = _get_tagger()
    model_name = settings.AI_MODEL_NAME
    tagger.ensure_loaded(model_name, local_files_only=local_files_only)
    provenance = get_ai_tagging_runtime_provenance(tagger)

    predictions = tagger.predict_from_file(
        str(file_path),
        general_threshold=0.0,
        character_threshold=0.0,
        hide_rating_tags=False,
        character_tags_first=True,
        model_name=model_name,
        local_files_only=local_files_only,
    )

    summary: Dict[str, Any] = {
        "media_id": media_id,
        "tags_added": 0,
        "suggestions_added": 0,
        "skipped_locked": 0,
        "ignored_low_confidence": 0,
        "predictions": [],
        "provenance": provenance,
    }

    for pred in predictions:
        tag_name: str = pred["name"]
        wd_category: str = pred["category"]
        confidence: float = pred["confidence"]

        confirm_thresh, suggest_thresh = _determine_thresholds(wd_category)

        action = "ignored"
        if confidence >= confirm_thresh:
            action = "confirmed"
        elif confidence >= suggest_thresh:
            action = "suggestion"

        if force_suggestions and action == "confirmed":
            action = "suggestion"

        pred_entry = {
            "name": tag_name,
            "category": wd_category,
            "confidence": round(confidence, 4),
            "action": action,
        }
        summary["predictions"].append(pred_entry)

        if action == "ignored":
            summary["ignored_low_confidence"] += 1
            continue

        if dry_run:
            if action == "confirmed":
                summary["tags_added"] += 1
            else:
                summary["suggestions_added"] += 1
            continue

        db_category = WD_CATEGORY_MAP.get(wd_category, "general")
        tag_objects = get_or_create_tags(
            db, [tag_name], category_hints={tag_name: db_category}
        )
        if not tag_objects:
            continue
        tag_obj = tag_objects[0]

        effective_confirm_threshold = 1.1 if force_suggestions else confirm_thresh
        added = add_ai_tag_to_media(
            db,
            media_id=media_id,
            tag_id=tag_obj.id,
            confidence=confidence,
            source="ai_wd",
            confirm_threshold=effective_confirm_threshold,
        )

        if added:
            if action == "confirmed":
                summary["tags_added"] += 1
            else:
                summary["suggestions_added"] += 1
        else:
            summary["skipped_locked"] += 1

    if not dry_run and (summary["tags_added"] > 0 or summary["suggestions_added"] > 0):
        db.commit()
        affected_tag_ids = [
            t.id for t in db.query(Tag).join(
                blombooru_media_tags,
                Tag.id == blombooru_media_tags.c.tag_id,
            ).filter(blombooru_media_tags.c.media_id == media_id).all()
        ]
        if affected_tag_ids:
            update_tag_counts(db, affected_tag_ids)
            db.commit()

    return summary


def run_ai_tagging_batch(
    db: Session,
    *,
    media_ids: Optional[List[int]] = None,
    max_items: int = 10,
    dry_run: bool = False,
    only_without_ai_tags: bool = True,
    local_files_only: bool = False,
) -> Dict[str, Any]:
    """Run AI tagging on a batch of media items.

    If *media_ids* is None, selects up to *max_items* images that have no AI
    tags yet.  Returns an aggregate summary.
    """
    hard_limit = settings.AI_TAGGING_BATCH_MAX_ITEMS
    effective_max = min(max_items, hard_limit)

    if media_ids is not None:
        if len(media_ids) > hard_limit:
            return {
                "error": f"Too many items: {len(media_ids)} exceeds max {hard_limit}",
                "max_allowed": hard_limit,
            }
        ids = media_ids[:effective_max]
    else:
        query = db.query(Media.id)
        if only_without_ai_tags:
            ai_tagged = (
                db.query(blombooru_media_tags.c.media_id)
                .filter(blombooru_media_tags.c.source == "ai_wd")
                .distinct()
                .subquery()
            )
            query = query.filter(~Media.id.in_(ai_tagged))
        query = query.order_by(Media.id.asc()).limit(effective_max)
        ids = [row[0] for row in query.all()]

    batch_summary: Dict[str, Any] = {
        "processed": 0,
        "tags_added": 0,
        "suggestions_added": 0,
        "skipped_locked": 0,
        "ignored_low_confidence": 0,
        "failed": 0,
        "dry_run": dry_run,
        "max_items": effective_max,
        "total_selected": len(ids),
        "results": [],
        "provenance": None,
    }

    for mid in ids:
        try:
            result = run_ai_tagging(db, mid, dry_run=dry_run, local_files_only=local_files_only)
            if batch_summary["provenance"] is None and result.get("provenance"):
                batch_summary["provenance"] = result["provenance"]
            batch_summary["processed"] += 1
            batch_summary["tags_added"] += result.get("tags_added", 0)
            batch_summary["suggestions_added"] += result.get("suggestions_added", 0)
            batch_summary["skipped_locked"] += result.get("skipped_locked", 0)
            batch_summary["ignored_low_confidence"] += result.get("ignored_low_confidence", 0)
            if result.get("error"):
                batch_summary["failed"] += 1
            batch_summary["results"].append(result)
        except Exception as exc:
            logger.error("AI tagging failed for media %d: %s", mid, exc, exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
            batch_summary["failed"] += 1
            batch_summary["processed"] += 1
            batch_summary["results"].append({
                "media_id": mid,
                "error": str(exc),
            })

    return batch_summary
