"""Tag provenance service — helpers for managing media–tag relationships
with source, confidence, lock, and suggestion metadata.

All media–tag association writes should go through these functions to ensure
consistent provenance tracking.  The underlying ``blombooru_media_tags`` table
is still used as a ``secondary`` relationship by SQLAlchemy for reads, so
regular ``Media.tags`` / ``Tag.media`` access continues to work unchanged.
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import and_, delete, func, insert, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import Tag, blombooru_media_tags
from ..utils.logger import logger

TAG_SOURCE_MANUAL = "manual"
TAG_SOURCE_AI_WD = "ai_wd"
TAG_SOURCE_BOORU_IMPORT = "booru_import"
TAG_SOURCE_REVERSE_SEARCH = "reverse_search"
TAG_SOURCE_SYSTEM = "system"

DEFAULT_AI_CONFIRM_THRESHOLD = 0.35


def add_manual_tag_to_media(
    db: Session,
    media_id: int,
    tag_id: int,
) -> None:
    """Attach a tag to a media item as a manual, locked, confirmed tag.

    If the association already exists, it is upgraded to manual/locked.
    """
    now = datetime.now(timezone.utc)

    stmt = pg_insert(blombooru_media_tags).values(
        media_id=media_id,
        tag_id=tag_id,
        source=TAG_SOURCE_MANUAL,
        confidence=1.0,
        is_locked=True,
        is_suggestion=False,
        created_at=now,
        updated_at=now,
    ).on_conflict_do_update(
        index_elements=["media_id", "tag_id"],
        set_={
            "source": TAG_SOURCE_MANUAL,
            "confidence": 1.0,
            "is_locked": True,
            "is_suggestion": False,
            "updated_at": now,
        },
    )
    db.execute(stmt)


def add_manual_tags_to_media(
    db: Session,
    media_id: int,
    tag_ids: List[int],
) -> None:
    """Bulk-add manual tags.  Existing associations are upgraded."""
    for tid in tag_ids:
        add_manual_tag_to_media(db, media_id, tid)


def add_booru_import_tag_to_media(
    db: Session,
    media_id: int,
    tag_id: int,
) -> None:
    """Attach a tag from a Booru import — treated like manual but with distinct source."""
    now = datetime.now(timezone.utc)

    stmt = pg_insert(blombooru_media_tags).values(
        media_id=media_id,
        tag_id=tag_id,
        source=TAG_SOURCE_BOORU_IMPORT,
        confidence=1.0,
        is_locked=True,
        is_suggestion=False,
        created_at=now,
        updated_at=now,
    ).on_conflict_do_update(
        index_elements=["media_id", "tag_id"],
        set_={
            "source": TAG_SOURCE_BOORU_IMPORT,
            "confidence": 1.0,
            "is_locked": True,
            "is_suggestion": False,
            "updated_at": now,
        },
    )
    db.execute(stmt)


def add_ai_tag_to_media(
    db: Session,
    media_id: int,
    tag_id: int,
    confidence: float,
    source: str = TAG_SOURCE_AI_WD,
    confirm_threshold: float = DEFAULT_AI_CONFIRM_THRESHOLD,
) -> bool:
    """Attach an AI-predicted tag to a media item.

    Priority rules:
    - If the same media–tag pair already exists with ``is_locked=True`` or
      ``source='manual'``, **do not overwrite** — return ``False``.
    - Otherwise upsert with the AI provenance data.

    Returns ``True`` if the row was inserted/updated, ``False`` if skipped.
    """
    existing = db.execute(
        blombooru_media_tags.select().where(
            and_(
                blombooru_media_tags.c.media_id == media_id,
                blombooru_media_tags.c.tag_id == tag_id,
            )
        )
    ).first()

    if existing:
        if existing.is_locked or existing.source == TAG_SOURCE_MANUAL:
            return False

        now = datetime.now(timezone.utc)
        is_suggestion = confidence < confirm_threshold
        db.execute(
            blombooru_media_tags.update()
            .where(
                and_(
                    blombooru_media_tags.c.media_id == media_id,
                    blombooru_media_tags.c.tag_id == tag_id,
                )
            )
            .values(
                source=source,
                confidence=confidence,
                is_suggestion=is_suggestion,
                updated_at=now,
            )
        )
        return True

    now = datetime.now(timezone.utc)
    is_suggestion = confidence < confirm_threshold
    db.execute(
        blombooru_media_tags.insert().values(
            media_id=media_id,
            tag_id=tag_id,
            source=source,
            confidence=confidence,
            is_locked=False,
            is_suggestion=is_suggestion,
            created_at=now,
            updated_at=now,
        )
    )
    return True


def confirm_suggestion(
    db: Session,
    media_id: int,
    tag_id: int,
    *,
    preserve_source: bool = False,
) -> bool:
    """Confirm a suggestion tag — mark it as non-suggestion and locked.

    If *preserve_source* is True, keeps the original source and confidence
    (useful for AI tag review where we want to retain provenance).

    Returns ``True`` if a row was updated.
    """
    now = datetime.now(timezone.utc)

    values: dict = {
        "is_suggestion": False,
        "is_locked": True,
        "updated_at": now,
    }
    if not preserve_source:
        values["source"] = TAG_SOURCE_MANUAL
        values["confidence"] = 1.0

    result = db.execute(
        blombooru_media_tags.update()
        .where(
            and_(
                blombooru_media_tags.c.media_id == media_id,
                blombooru_media_tags.c.tag_id == tag_id,
                blombooru_media_tags.c.is_suggestion == True,
            )
        )
        .values(**values)
    )
    return result.rowcount > 0


def reject_suggestion(
    db: Session,
    media_id: int,
    tag_id: int,
) -> bool:
    """Reject (delete) a suggestion tag.

    Returns ``True`` if a row was deleted.
    """
    result = db.execute(
        blombooru_media_tags.delete().where(
            and_(
                blombooru_media_tags.c.media_id == media_id,
                blombooru_media_tags.c.tag_id == tag_id,
                blombooru_media_tags.c.is_suggestion == True,
            )
        )
    )
    return result.rowcount > 0


def update_tag_provenance(
    db: Session,
    media_id: int,
    tag_id: int,
    *,
    source: Optional[str] = None,
    confidence: Optional[float] = None,
    is_locked: Optional[bool] = None,
    is_suggestion: Optional[bool] = None,
) -> bool:
    """Update provenance fields on an existing media–tag association.

    Only provided (non-None) fields are updated.
    Returns ``True`` if a row was updated.
    """
    values: dict = {"updated_at": datetime.now(timezone.utc)}
    if source is not None:
        values["source"] = source
    if confidence is not None:
        values["confidence"] = confidence
    if is_locked is not None:
        values["is_locked"] = is_locked
    if is_suggestion is not None:
        values["is_suggestion"] = is_suggestion

    result = db.execute(
        blombooru_media_tags.update()
        .where(
            and_(
                blombooru_media_tags.c.media_id == media_id,
                blombooru_media_tags.c.tag_id == tag_id,
            )
        )
        .values(**values)
    )
    return result.rowcount > 0


def remove_tag_from_media(
    db: Session,
    media_id: int,
    tag_id: int,
) -> bool:
    """Remove a tag association from a media item.

    Returns ``True`` if a row was deleted.
    """
    result = db.execute(
        blombooru_media_tags.delete().where(
            and_(
                blombooru_media_tags.c.media_id == media_id,
                blombooru_media_tags.c.tag_id == tag_id,
            )
        )
    )
    return result.rowcount > 0


def set_media_tags_manual(
    db: Session,
    media_id: int,
    new_tag_ids: List[int],
) -> None:
    """Replace all tags on a media item with a new set of manual tags.

    This mirrors the existing ``media.tags = [...]`` assignment behaviour but
    writes proper provenance.  Tags that were in the old set but not the new
    one are removed.  Tags in both sets are upgraded to manual/locked.
    """
    existing_rows = db.execute(
        blombooru_media_tags.select().where(
            blombooru_media_tags.c.media_id == media_id,
        )
    ).fetchall()
    existing_tag_ids = {row.tag_id for row in existing_rows}
    new_set = set(new_tag_ids)

    to_remove = existing_tag_ids - new_set
    if to_remove:
        db.execute(
            blombooru_media_tags.delete().where(
                and_(
                    blombooru_media_tags.c.media_id == media_id,
                    blombooru_media_tags.c.tag_id.in_(to_remove),
                )
            )
        )

    for tid in new_set:
        add_manual_tag_to_media(db, media_id, tid)


def get_media_tag_provenance(
    db: Session,
    media_id: int,
) -> list:
    """Return provenance metadata for all tags on a media item.

    Returns a list of dicts with tag_id, source, confidence, is_locked,
    is_suggestion, created_at, updated_at.
    """
    rows = db.execute(
        blombooru_media_tags.select().where(
            blombooru_media_tags.c.media_id == media_id,
        )
    ).fetchall()

    return [
        {
            "tag_id": row.tag_id,
            "source": row.source,
            "confidence": row.confidence,
            "is_locked": row.is_locked,
            "is_suggestion": row.is_suggestion,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]
