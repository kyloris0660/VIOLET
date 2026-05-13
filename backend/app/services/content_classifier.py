"""Content classifier — determines whether media is anime/illustration/non-anime.

Supports two methods (configurable via CONTENT_CLASSIFICATION_METHOD):
  - "clip": CLIP ViT-B/32 zero-shot visual classifier (default, recommended)
  - "heuristic": legacy WD tagger tag-count heuristic
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..enums import ContentClassEnum, FileTypeEnum
from ..models import Media, blombooru_media_tags

logger = logging.getLogger(__name__)


def requires_clip_inference(media: "Media") -> bool:
    """Return True if this media item would require CLIP model inference.

    Mirrors the skip condition in ``_classify_clip``: video files are
    handled without loading the CLIP model, so they do not require it.
    All other file types (image, gif, etc.) need CLIP inference.
    """
    return media.file_type != FileTypeEnum.video


def _resolve_media_file(media: Media) -> Optional[Path]:
    """Resolve the on-disk path for a media item."""
    file_path = settings.resolve_storage_path(media.path)
    if file_path and file_path.exists():
        return file_path
    direct = settings.ORIGINAL_DIR / media.filename
    if direct.exists():
        return direct
    return None


def _classify_clip(media: Media) -> Dict[str, Any]:
    """Run CLIP zero-shot classification on a media item's image file."""
    from .clip_classifier import CLIPClassifier

    if media.file_type == FileTypeEnum.video:
        return {
            "content_class": ContentClassEnum.unknown,
            "confidence": 0.0,
            "source": "clip",
            "model": "clip-vit-base-patch32",
            "reason": "Skipped: CLIP does not support video files",
            "skipped": True,
        }

    file_path = _resolve_media_file(media)
    if not file_path:
        return {"error": f"File not found: {media.filename}"}

    classifier = CLIPClassifier()
    margin = settings.CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN
    result = classifier.classify_file(str(file_path), unknown_margin=margin)

    if result.get("content_class") == "error":
        return {"error": result.get("reason", "CLIP classification failed")}

    raw_class = result["content_class"]
    try:
        content_class = ContentClassEnum(raw_class)
    except ValueError:
        content_class = ContentClassEnum.unknown

    return {
        "content_class": content_class,
        "confidence": result.get("confidence", 0.0),
        "source": "clip",
        "model": "clip-vit-base-patch32",
        "scores": result.get("scores", {}),
        "margin": result.get("margin", 0),
        "best_category": result.get("best_category", ""),
    }


def _classify_heuristic_from_db(db: Session, media_id: int) -> Dict[str, Any]:
    """Legacy WD tag-count heuristic using existing DB tags."""
    confidence_threshold = settings.CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD
    tag_threshold = settings.CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD

    ai_tags = (
        db.query(blombooru_media_tags)
        .filter(
            blombooru_media_tags.c.media_id == media_id,
            blombooru_media_tags.c.source == "ai_wd",
            blombooru_media_tags.c.confidence >= confidence_threshold,
            blombooru_media_tags.c.is_suggestion == False,
        )
        .all()
    )

    if not ai_tags:
        content_class = ContentClassEnum.unknown
        conf = 0.0
    elif len(ai_tags) >= tag_threshold:
        content_class = ContentClassEnum.anime
        conf = min(1.0, len(ai_tags) / (tag_threshold * 2))
    else:
        content_class = ContentClassEnum.non_anime
        conf = min(1.0, 1.0 - (len(ai_tags) / tag_threshold))

    return {
        "content_class": content_class,
        "confidence": conf,
        "source": "heuristic",
        "model": "wd_tag_count",
        "ai_tag_count": len(ai_tags),
        "threshold": tag_threshold,
        "confidence_threshold": confidence_threshold,
    }


def _classify_heuristic_from_predictions(predictions: list) -> Dict[str, Any]:
    """Legacy WD tag-count heuristic using fresh predictions."""
    confidence_threshold = settings.CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD
    tag_threshold = settings.CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD

    high_conf_tags = [
        p for p in predictions
        if p.get("confidence", 0) >= confidence_threshold
        and p.get("action") == "confirmed"
    ]

    if not predictions or not high_conf_tags:
        content_class = ContentClassEnum.unknown
        conf = 0.0
    elif len(high_conf_tags) >= tag_threshold:
        content_class = ContentClassEnum.anime
        conf = min(1.0, len(high_conf_tags) / (tag_threshold * 2))
    else:
        content_class = ContentClassEnum.non_anime
        conf = min(1.0, 1.0 - (len(high_conf_tags) / tag_threshold))

    return {
        "content_class": content_class,
        "confidence": conf,
        "source": "heuristic",
        "model": "wd_tag_count",
        "high_conf_tag_count": len(high_conf_tags),
    }


def classify_media(
    db: Session,
    media_id: int,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Classify a single media item.

    Uses CLIP zero-shot or WD tag-count heuristic depending on
    CONTENT_CLASSIFICATION_METHOD config.
    """
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        return {"media_id": media_id, "error": "Media not found"}

    if media.content_class_locked:
        return {
            "media_id": media_id,
            "skipped": True,
            "reason": "locked",
            "current_class": media.content_class.value if media.content_class else None,
        }

    method = settings.CONTENT_CLASSIFICATION_METHOD

    if method == "clip":
        cls_result = _classify_clip(media)
    else:
        cls_result = _classify_heuristic_from_db(db, media_id)

    if "error" in cls_result:
        return {"media_id": media_id, "error": cls_result["error"]}

    if cls_result.get("skipped"):
        return {
            "media_id": media_id,
            "skipped": True,
            "reason": cls_result.get("reason", "skipped"),
            "current_class": media.content_class.value if media.content_class else None,
        }

    new_class = cls_result["content_class"]
    conf = cls_result["confidence"]

    result: Dict[str, Any] = {
        "media_id": media_id,
        "content_class": new_class.value,
        "confidence": round(conf, 4),
        "method": method,
        "changed": media.content_class != new_class,
    }

    if method == "clip":
        result["scores"] = cls_result.get("scores", {})
        result["margin"] = cls_result.get("margin", 0)
        result["best_category"] = cls_result.get("best_category", "")
    else:
        result["ai_tag_count"] = cls_result.get("ai_tag_count", 0)
        result["threshold"] = cls_result.get("threshold", 0)
        result["confidence_threshold"] = cls_result.get("confidence_threshold", 0)

    if dry_run:
        result["dry_run"] = True
        return result

    media.content_class = new_class
    media.content_class_confidence = conf
    media.content_class_source = cls_result["source"]
    media.content_class_model = cls_result["model"]
    db.commit()

    return result


def classify_from_predictions(
    db: Session,
    media_id: int,
    predictions: list,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Classify media inline during AI tagging.

    With CLIP method: ignores predictions entirely and classifies from the
    image file directly (CLIP doesn't need WD tags).
    With heuristic method: uses the fresh WD tagger predictions.
    """
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        return {"media_id": media_id, "error": "Media not found"}

    if media.content_class_locked:
        return {
            "media_id": media_id,
            "skipped": True,
            "reason": "locked",
        }

    method = settings.CONTENT_CLASSIFICATION_METHOD

    if method == "clip":
        cls_result = _classify_clip(media)
    else:
        cls_result = _classify_heuristic_from_predictions(predictions)

    if "error" in cls_result:
        return {"media_id": media_id, "error": cls_result["error"]}

    if cls_result.get("skipped"):
        return {
            "media_id": media_id,
            "skipped": True,
            "reason": cls_result.get("reason", "skipped"),
        }

    new_class = cls_result["content_class"]
    conf = cls_result["confidence"]

    result = {
        "media_id": media_id,
        "content_class": new_class.value,
        "confidence": round(conf, 4),
        "method": method,
    }

    if method != "clip":
        result["high_conf_tag_count"] = cls_result.get("high_conf_tag_count", 0)

    if dry_run:
        result["dry_run"] = True
        return result

    media.content_class = new_class
    media.content_class_confidence = conf
    media.content_class_source = cls_result["source"]
    media.content_class_model = cls_result["model"]

    return result
