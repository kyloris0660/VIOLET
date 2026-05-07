"""
Tag Localization Service — manages tag translations (zh-CN display names and search aliases).

Priority: manual/reviewed DB > static dictionary > LLM translated cache > fallback canonical tag

Overwrite rules (upsert_translation):
  - A lower-priority source NEVER overwrites a higher-priority source.
    e.g. llm (priority 2) cannot overwrite static (priority 1) or manual (priority 0).
  - Same source: always updates (refreshes display_name, aliases, etc.).
  - Higher-priority source can overwrite lower-priority source.
  - To force-overwrite regardless of priority, pass force=True (Admin explicit action only).
"""
import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models import Tag, TagTranslation

logger = logging.getLogger(__name__)

_STATIC_DICT_CACHE: Optional[Dict] = None
_auto_translate_lock = threading.Lock()

SOURCE_PRIORITY = {"manual": 0, "static": 1, "llm": 2, "imported": 3}
STATUS_PRIORITY = {"reviewed": 0, "translated": 1, "pending": 2}


def _load_static_dict() -> Dict:
    global _STATIC_DICT_CACHE
    if _STATIC_DICT_CACHE is not None:
        return _STATIC_DICT_CACHE
    dict_path = (Path(__file__).parent.parent.parent.parent
                 / "frontend" / "static" / "data" / "tag_translations_zh.json")
    try:
        with open(dict_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            _STATIC_DICT_CACHE = {
                "tags": data.get("tags", {}),
                "reverse": data.get("reverse", {}),
            }
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        _STATIC_DICT_CACHE = {"tags": {}, "reverse": {}}
    return _STATIC_DICT_CACHE


def get_tag_display_name(db: Session, tag_name: str, lang: str = "zh-CN") -> str:
    """Return Chinese display name or fallback to canonical.
    Priority: reviewed/manual DB > static dict > llm DB > canonical."""
    trans = (
        db.query(TagTranslation)
        .filter(
            TagTranslation.canonical_name == tag_name,
            TagTranslation.language == lang,
            TagTranslation.status != "rejected",
        )
        .all()
    )
    if trans:
        best = _pick_best_translation(trans)
        if best:
            return best.display_name

    static = _load_static_dict()
    if tag_name in static["tags"]:
        return static["tags"][tag_name]

    return tag_name


def _pick_best_translation(translations: List[TagTranslation]) -> Optional[TagTranslation]:
    """Pick the highest-priority translation from a list."""
    if not translations:
        return None
    best = translations[0]
    for t in translations[1:]:
        b_src = SOURCE_PRIORITY.get(best.source, 99)
        t_src = SOURCE_PRIORITY.get(t.source, 99)
        b_st = STATUS_PRIORITY.get(best.status, 99)
        t_st = STATUS_PRIORITY.get(t.status, 99)
        if (t_src, t_st) < (b_src, b_st):
            best = t
    return best


def get_tag_display_names_batch(db: Session, tag_names: List[str], lang: str = "zh-CN") -> Dict[str, str]:
    """Batch lookup: {canonical_name: display_name}"""
    if not tag_names:
        return {}

    result = {}
    static = _load_static_dict()

    translations = (
        db.query(TagTranslation)
        .filter(
            TagTranslation.canonical_name.in_(tag_names),
            TagTranslation.language == lang,
            TagTranslation.status != "rejected",
        )
        .all()
    )

    db_map: Dict[str, TagTranslation] = {}
    for t in translations:
        existing = db_map.get(t.canonical_name)
        if existing is None:
            db_map[t.canonical_name] = t
        else:
            e_src = SOURCE_PRIORITY.get(existing.source, 99)
            t_src = SOURCE_PRIORITY.get(t.source, 99)
            e_st = STATUS_PRIORITY.get(existing.status, 99)
            t_st = STATUS_PRIORITY.get(t.status, 99)
            if (t_src, t_st) < (e_src, e_st):
                db_map[t.canonical_name] = t

    for name in tag_names:
        if name in db_map:
            result[name] = db_map[name].display_name
        elif name in static["tags"]:
            result[name] = static["tags"][name]
        else:
            result[name] = name

    return result


def resolve_tag_alias(db: Session, query_token: str, lang: str = "zh-CN") -> str:
    """Resolve a Chinese search term to its canonical English tag name.
    Checks DB aliases first, then static dict, then returns original."""
    trans = (
        db.query(TagTranslation)
        .filter(
            TagTranslation.language == lang,
            TagTranslation.status != "rejected",
            or_(
                TagTranslation.display_name == query_token,
                TagTranslation.aliases_json.contains(f'"{query_token}"'),
            ),
        )
        .all()
    )
    if trans:
        best = _pick_best_translation(trans)
        if best:
            return best.canonical_name

    static = _load_static_dict()
    if query_token in static["reverse"]:
        return static["reverse"][query_token]

    return query_token


def list_missing_translations(db: Session, lang: str = "zh-CN", limit: int = 100,
                              category: Optional[str] = None,
                              categories: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Find tags that have no translation in the given language."""
    static = _load_static_dict()

    subq = (
        db.query(TagTranslation.canonical_name)
        .filter(
            TagTranslation.language == lang,
            TagTranslation.status != "rejected",
        )
        .subquery()
    )

    query = db.query(Tag).filter(~Tag.name.in_(db.query(subq.c.canonical_name)))
    static_names = list(static["tags"].keys())
    if static_names:
        query = query.filter(~Tag.name.in_(static_names))

    from ..enums import TagCategoryEnum
    cat_map = {
        "general": TagCategoryEnum.general,
        "character": TagCategoryEnum.character,
        "copyright": TagCategoryEnum.copyright,
        "artist": TagCategoryEnum.artist,
        "meta": TagCategoryEnum.meta,
    }

    if categories:
        enums = [cat_map[c] for c in categories if c in cat_map]
        if enums:
            query = query.filter(Tag.category.in_(enums))
    elif category:
        if category in cat_map:
            query = query.filter(Tag.category == cat_map[category])

    query = query.order_by(Tag.post_count.desc())
    tags = query.limit(limit).all()

    return [
        {
            "tag_id": t.id,
            "canonical_name": t.name,
            "category": t.category.value if hasattr(t.category, 'value') else str(t.category),
            "post_count": t.post_count,
        }
        for t in tags
    ]


def upsert_translation(
    db: Session,
    canonical_name: str,
    display_name: str,
    lang: str = "zh-CN",
    aliases: Optional[List[str]] = None,
    category: Optional[str] = None,
    source: str = "manual",
    status: str = "reviewed",
    confidence: Optional[float] = None,
    needs_review: bool = False,
    provider: Optional[str] = None,
    force: bool = False,
) -> Optional[TagTranslation]:
    """Insert or update a translation with strict priority enforcement.

    Returns the translation record, or None if the update was blocked by priority.

    Priority rules:
      - Lower-priority source CANNOT overwrite higher-priority source.
        e.g. llm(2) cannot overwrite static(1) or manual(0).
      - Same source: always updates.
      - Higher-priority source can overwrite lower-priority source.
      - force=True bypasses priority check (Admin explicit action only).
    """
    existing = (
        db.query(TagTranslation)
        .filter(
            TagTranslation.canonical_name == canonical_name,
            TagTranslation.language == lang,
        )
        .first()
    )

    tag = db.query(Tag).filter(Tag.name == canonical_name).first()
    tag_id = tag.id if tag else None

    if existing:
        new_src_pri = SOURCE_PRIORITY.get(source, 99)
        old_src_pri = SOURCE_PRIORITY.get(existing.source, 99)

        if not force and new_src_pri > old_src_pri:
            logger.debug(
                f"Blocked: {source}(pri={new_src_pri}) cannot overwrite "
                f"{existing.source}(pri={old_src_pri}) for '{canonical_name}'"
            )
            return None

        existing.display_name = display_name
        existing.aliases_json = json.dumps(aliases or [], ensure_ascii=False)
        existing.source = source
        existing.status = status
        existing.confidence = confidence
        existing.needs_review = needs_review
        existing.provider = provider
        existing.tag_id = tag_id
        if category:
            existing.category = category
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing
    else:
        trans = TagTranslation(
            tag_id=tag_id,
            canonical_name=canonical_name,
            language=lang,
            display_name=display_name,
            aliases_json=json.dumps(aliases or [], ensure_ascii=False),
            category=category,
            source=source,
            status=status,
            confidence=confidence,
            needs_review=needs_review,
            provider=provider,
        )
        db.add(trans)
        db.commit()
        db.refresh(trans)
        return trans


async def batch_translate_missing_tags(
    db: Session,
    dry_run: bool = True,
    max_items: int = 50,
    category: Optional[str] = None,
    lang: str = "zh-CN",
) -> Dict[str, Any]:
    """Batch translate missing tags using the configured LLM provider."""
    from .llm_translation_provider import get_llm_provider
    from ..config import settings

    provider = get_llm_provider()
    effective_max = min(max_items, settings.TAG_TRANSLATION_BATCH_MAX_ITEMS)
    result = {
        "dry_run": dry_run,
        "provider": provider.get_provider_name(),
        "provider_available": provider.is_available(),
        "requested_max": max_items,
        "effective_max": effective_max,
        "candidates": 0,
        "translated": 0,
        "failed": 0,
        "skipped": 0,
        "translations": [],
        "errors": [],
    }

    if not provider.is_available():
        result["errors"].append("LLM provider not available or not configured")
        return result

    missing = list_missing_translations(db, lang=lang, limit=effective_max, category=category)
    result["candidates"] = len(missing)

    if not missing:
        result["errors"].append(
            "No untranslated tags found matching the criteria. "
            "All tags may already have translations in DB or static dictionary."
        )
        return result

    tag_inputs = [{"name": t["canonical_name"], "category": t["category"]} for t in missing]

    try:
        translations = await provider.translate_tags(tag_inputs)
    except Exception as e:
        logger.error(f"Batch translation failed: {e}")
        result["errors"].append(str(e))
        result["failed"] = len(missing)
        return result

    if not translations and missing:
        result["errors"].append(
            f"LLM returned 0 translations for {len(missing)} candidates. "
            "The LLM response may have been empty or malformed."
        )
        result["failed"] = len(missing)
        return result

    for tr in translations:
        entry = {
            "canonical_name": tr.canonical_name,
            "display_name_zh": tr.display_name_zh,
            "aliases_zh": tr.aliases_zh,
            "needs_review": tr.needs_review,
            "category": tr.category,
        }
        if dry_run:
            result["translations"].append(entry)
            result["translated"] += 1
        else:
            try:
                saved = upsert_translation(
                    db,
                    canonical_name=tr.canonical_name,
                    display_name=tr.display_name_zh,
                    lang=lang,
                    aliases=tr.aliases_zh,
                    category=tr.category,
                    source="llm",
                    status="translated",
                    needs_review=tr.needs_review,
                    provider=provider.get_provider_name(),
                )
                if saved is None:
                    result["skipped"] += 1
                    result["errors"].append(f"{tr.canonical_name}: blocked by higher-priority translation")
                else:
                    result["translations"].append(entry)
                    result["translated"] += 1
            except Exception as e:
                logger.error(f"Failed to save translation for {tr.canonical_name}: {e}")
                result["errors"].append(f"{tr.canonical_name}: {str(e)}")
                result["failed"] += 1

    if dry_run:
        result["skipped"] = result["candidates"] - result["translated"] - result["failed"]
    return result


def seed_static_translations(db: Session, lang: str = "zh-CN"):
    """Import static JSON translations into DB as source=static.
    Only inserts if no existing translation (any source) for that canonical_name+lang.
    Called during app startup."""
    static = _load_static_dict()
    if not static["tags"]:
        return 0

    count = 0
    for canonical, display_zh in static["tags"].items():
        existing = (
            db.query(TagTranslation)
            .filter(
                TagTranslation.canonical_name == canonical,
                TagTranslation.language == lang,
            )
            .first()
        )
        if existing:
            continue

        tag = db.query(Tag).filter(Tag.name == canonical).first()
        aliases = []
        for zh_alias, en_name in static.get("reverse", {}).items():
            if en_name == canonical and zh_alias != display_zh:
                aliases.append(zh_alias)

        trans = TagTranslation(
            tag_id=tag.id if tag else None,
            canonical_name=canonical,
            language=lang,
            display_name=display_zh,
            aliases_json=json.dumps(aliases, ensure_ascii=False) if aliases else "[]",
            category=tag.category.value if tag and hasattr(tag.category, 'value') else "general",
            source="static",
            status="translated",
            needs_review=False,
            provider="local_static",
        )
        db.add(trans)
        count += 1

    if count > 0:
        db.commit()
        logger.info(f"Seeded {count} static tag translations into DB")
    return count


def get_translation_stats(db: Session, lang: str = "zh-CN") -> Dict[str, Any]:
    """Get translation statistics."""
    total_tags = db.query(func.count(Tag.id)).scalar() or 0

    total_translations = (
        db.query(func.count(TagTranslation.id))
        .filter(TagTranslation.language == lang, TagTranslation.status != "rejected")
        .scalar() or 0
    )

    static_dict = _load_static_dict()
    static_count = len(static_dict["tags"])

    source_counts = {}
    rows = (
        db.query(TagTranslation.source, func.count(TagTranslation.id))
        .filter(TagTranslation.language == lang, TagTranslation.status != "rejected")
        .group_by(TagTranslation.source)
        .all()
    )
    for src, cnt in rows:
        source_counts[src] = cnt

    needs_review = (
        db.query(func.count(TagTranslation.id))
        .filter(
            TagTranslation.language == lang,
            TagTranslation.needs_review == True,
            TagTranslation.status != "rejected",
        )
        .scalar() or 0
    )

    db_translated_names = set(
        r[0] for r in db.query(TagTranslation.canonical_name)
        .filter(TagTranslation.language == lang, TagTranslation.status != "rejected")
        .all()
    )
    all_covered_names = db_translated_names | set(static_dict["tags"].keys())

    total_tag_names = set(r[0] for r in db.query(Tag.name).all())
    missing = len(total_tag_names - all_covered_names)

    return {
        "total_tags": total_tags,
        "translated_db": total_translations,
        "translated_static_only": static_count - source_counts.get("static", 0),
        "total_covered": len(all_covered_names & total_tag_names),
        "missing": missing,
        "needs_review": needs_review,
        "source_breakdown": source_counts,
    }


def schedule_auto_translate(tag_names: List[str], lang: str = "zh-CN"):
    """Schedule background auto-translation for newly created tags.

    Non-blocking: spawns a daemon thread that creates its own DB session.
    Only runs if both TAG_TRANSLATION_LLM_ENABLED and TAG_TRANSLATION_AUTO_ENABLED
    are true.  Respects TAG_TRANSLATION_AUTO_MAX_ITEMS throttle.
    """
    from ..config import settings

    if not settings.TAG_TRANSLATION_LLM_ENABLED:
        return
    if not settings.TAG_TRANSLATION_AUTO_ENABLED:
        return
    if not tag_names:
        return

    thread = threading.Thread(
        target=_auto_translate_worker,
        args=(list(tag_names), lang),
        daemon=True,
    )
    thread.start()


def _auto_translate_worker(tag_names: List[str], lang: str):
    """Background worker: translate tags missing zh-CN translations via LLM.

    Uses independent DB session (not request-scoped).  Catches all exceptions
    to avoid crashing the thread.
    """
    if not _auto_translate_lock.acquire(blocking=False):
        logger.info("Auto-translate already running, skipping")
        return

    try:
        from ..config import settings
        from ..database import SessionLocal
        from .llm_translation_provider import get_llm_provider

        if SessionLocal is None:
            return

        provider = get_llm_provider()
        if not provider.is_available():
            return

        db = SessionLocal()
        try:
            static = _load_static_dict()
            max_items = settings.TAG_TRANSLATION_AUTO_MAX_ITEMS

            candidates = []
            for name in tag_names:
                if len(candidates) >= max_items:
                    break
                if name in static["tags"]:
                    continue
                existing = (
                    db.query(TagTranslation)
                    .filter(
                        TagTranslation.canonical_name == name,
                        TagTranslation.language == lang,
                        TagTranslation.status != "rejected",
                    )
                    .first()
                )
                if existing:
                    continue

                tag = db.query(Tag).filter(Tag.name == name).first()
                cat = "general"
                if tag and hasattr(tag.category, "value"):
                    cat = tag.category.value

                proper_noun_cats = {"character", "copyright", "artist"}
                if cat in proper_noun_cats:
                    continue

                candidates.append({"name": name, "category": cat})

            skipped = len(tag_names) - len(candidates)
            if skipped > 0:
                logger.info(f"Auto-translate: {skipped} tags already translated or in static dict")

            if not candidates:
                return

            logger.info(f"Auto-translate: translating {len(candidates)} new tags via LLM")

            loop = asyncio.new_event_loop()
            try:
                results = loop.run_until_complete(provider.translate_tags(candidates))
            finally:
                loop.close()

            translated = 0
            for tr in results:
                try:
                    saved = upsert_translation(
                        db,
                        canonical_name=tr.canonical_name,
                        display_name=tr.display_name_zh,
                        lang=lang,
                        aliases=tr.aliases_zh,
                        category=tr.category,
                        source="llm",
                        status="translated",
                        needs_review=tr.needs_review,
                        provider=provider.get_provider_name(),
                    )
                    if saved:
                        translated += 1
                except Exception as e:
                    logger.error(f"Auto-translate save failed for {tr.canonical_name}: {e}")
                    db.rollback()

            if translated > 0:
                from ..utils.search_parser import invalidate_translation_cache
                invalidate_translation_cache()
                logger.info(f"Auto-translate: saved {translated} translations, cache invalidated")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Auto-translate worker error: {e}")
    finally:
        _auto_translate_lock.release()
