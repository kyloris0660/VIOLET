"""Content classifier — determines whether media is anime/illustration/non-anime.

Uses a heuristic approach based on WD tagger predictions: if a media item has
enough high-confidence anime-specific tags, it's classified as anime.  This
avoids requiring a separate ONNX model download.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..enums import ContentClassEnum
from ..models import Media, blombooru_media_tags

logger = logging.getLogger(__name__)


def classify_media(
    db: Session,
    media_id: int,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Classify a single media item using the WD tagger heuristic.

    Counts existing AI tags with confidence above threshold.  If the count
    meets the anime tag threshold the media is classified as anime; otherwise
    non_anime (or unknown when no AI tags exist at all).
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

    result: Dict[str, Any] = {
        "media_id": media_id,
        "ai_tag_count": len(ai_tags),
        "threshold": tag_threshold,
        "confidence_threshold": confidence_threshold,
    }

    if not ai_tags:
        new_class = ContentClassEnum.unknown
        conf = 0.0
    elif len(ai_tags) >= tag_threshold:
        new_class = ContentClassEnum.anime
        conf = min(1.0, len(ai_tags) / (tag_threshold * 2))
    else:
        new_class = ContentClassEnum.non_anime
        conf = min(1.0, 1.0 - (len(ai_tags) / tag_threshold))

    result["content_class"] = new_class.value
    result["confidence"] = round(conf, 4)
    result["changed"] = media.content_class != new_class

    if dry_run:
        result["dry_run"] = True
        return result

    media.content_class = new_class
    media.content_class_confidence = conf
    media.content_class_source = "heuristic"
    media.content_class_model = "wd_tag_count"
    db.commit()

    return result


def classify_from_predictions(
    db: Session,
    media_id: int,
    predictions: list,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Classify media using fresh WD tagger predictions (inline with AI tagging).

    Called from the AI tagging flow so that classification happens in the same
    pass without needing a separate DB query.
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

    confidence_threshold = settings.CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD
    tag_threshold = settings.CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD

    high_conf_tags = [
        p for p in predictions
        if p.get("confidence", 0) >= confidence_threshold
        and p.get("action") == "confirmed"
    ]

    if not predictions:
        new_class = ContentClassEnum.unknown
        conf = 0.0
    elif len(high_conf_tags) >= tag_threshold:
        new_class = ContentClassEnum.anime
        conf = min(1.0, len(high_conf_tags) / (tag_threshold * 2))
    else:
        new_class = ContentClassEnum.non_anime
        conf = min(1.0, 1.0 - (len(high_conf_tags) / tag_threshold))

    result = {
        "media_id": media_id,
        "content_class": new_class.value,
        "confidence": round(conf, 4),
        "high_conf_tag_count": len(high_conf_tags),
    }

    if dry_run:
        result["dry_run"] = True
        return result

    media.content_class = new_class
    media.content_class_confidence = conf
    media.content_class_source = "heuristic"
    media.content_class_model = "wd_tag_count"

    return result
