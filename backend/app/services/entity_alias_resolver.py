"""Entity Alias Resolver — proper-noun alias resolution for character/copyright/artist tags.

This is fundamentally different from visual tag translation:
- Visual tags (general, meta) are TRANSLATED: blue_eyes → 蓝眼睛
- Proper-noun tags (character, copyright, artist) need ALIAS RESOLUTION:
  hatsune_miku → 初音ミク / 初音未来 (established names, not translations)

The LLM prompt explicitly forbids inventing names — if the entity's Chinese name
is not well-known, the canonical tag is returned unchanged with needs_review=true.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .llm_translation_provider import TranslationResult, get_llm_provider

logger = logging.getLogger(__name__)

ENTITY_SYSTEM_PROMPT = (
    "You are an anime/manga entity name resolver. Your task is to find the ESTABLISHED "
    "Chinese names for character, copyright (series), and artist tags from Danbooru.\n\n"
    "CRITICAL RULES:\n"
    "1. ONLY provide Chinese names that are WELL-KNOWN and WIDELY USED by the Chinese "
    "anime/manga community. These are established names, NOT translations you invent.\n"
    "2. If you are NOT CONFIDENT that a Chinese name exists and is commonly used, "
    "set the display_name_zh to the ORIGINAL tag name (e.g., 'hatsune_miku') and "
    "set needs_review=true. DO NOT GUESS OR INVENT NAMES.\n"
    "3. Character names: use the established Chinese community name "
    "(e.g., hatsune_miku → 初音未来, rem_(re:zero) → 蕾姆)\n"
    "4. Copyright/series names: use the official or widely-used Chinese title "
    "(e.g., fate/stay_night → Fate/stay night, bocchi_the_rock! → 孤独摇滚!)\n"
    "5. Artist names: almost always keep the original name unchanged. Only provide "
    "a Chinese alias if the artist is widely known by a Chinese name.\n"
    "6. aliases_zh should contain alternate Chinese names/spellings if they exist.\n"
    "7. When in doubt, KEEP THE ORIGINAL. A missing Chinese name is better than a wrong one.\n\n"
    "Respond with a JSON array. Each element:\n"
    '{"canonical_name": "...", "display_name_zh": "...", "aliases_zh": ["..."], '
    '"notes": "...", "needs_review": true/false, "confidence": "high"/"medium"/"low"}\n'
    "ONLY output valid JSON array, no markdown, no explanation."
)


async def _resolve_chunk(
    provider,
    tags: List[Dict[str, str]],
) -> List[TranslationResult]:
    """Resolve a single chunk of proper-noun tags via LLM."""
    import httpx

    tags_text = json.dumps(tags, ensure_ascii=False)
    user_prompt = (
        f"Find the established Chinese names for these anime/manga entity tags:\n{tags_text}"
    )

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{provider.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {provider.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": provider.model,
                "messages": [
                    {"role": "system", "content": ENTITY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 4096,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(
            lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        )

    results_raw = json.loads(content)
    if not isinstance(results_raw, list):
        raise ValueError(f"LLM returned non-array response: {content[:200]}")

    results = []
    for item in results_raw:
        if not isinstance(item, dict):
            continue
        cn = item.get("canonical_name", "")
        dn = item.get("display_name_zh", "")
        if not cn or not dn:
            continue
        cat = "general"
        for t in tags:
            if t["name"] == cn:
                cat = t.get("category", "general")
                break
        results.append(TranslationResult(
            canonical_name=cn,
            display_name_zh=dn,
            aliases_zh=item.get("aliases_zh", []),
            notes=item.get("notes", ""),
            needs_review=item.get("needs_review", True),
            category=cat,
        ))
    return results


async def resolve_entity_aliases(
    tags: List[Dict[str, str]],
) -> List[TranslationResult]:
    """Resolve proper-noun tags to their established Chinese aliases via LLM.

    Uses the entity-specific prompt that forbids inventing names.
    """
    from ..config import settings

    provider = get_llm_provider()
    if not provider.is_available():
        raise RuntimeError("LLM provider not available")

    if not hasattr(provider, "api_key"):
        raise RuntimeError("Entity alias resolver requires OpenAI-compatible provider")

    chunk_size = settings.ENTITY_ALIAS_BATCH_SIZE
    all_results: List[TranslationResult] = []
    errors: List[str] = []

    for i in range(0, len(tags), chunk_size):
        chunk = tags[i:i + chunk_size]
        try:
            chunk_results = await _resolve_chunk(provider, chunk)
            all_results.extend(chunk_results)
        except Exception as e:
            logger.error("Entity resolver chunk %d failed: %s", i // chunk_size + 1, e)
            errors.append(f"Chunk {i // chunk_size + 1} ({len(chunk)} tags): {e}")

    if errors and not all_results:
        raise RuntimeError(f"All entity resolver chunks failed: {'; '.join(errors)}")

    if errors:
        logger.warning("Entity resolver partially failed: %s", "; ".join(errors))

    return all_results


def list_pending_proper_nouns(
    db: Session,
    lang: str = "zh-CN",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Find proper-noun tags that need alias resolution.

    Returns character/copyright/artist tags that either:
    - Have no translation at all
    - Have an LLM translation with needs_review=true
    """
    from ..models import Tag, TagTranslation
    from ..enums import TagCategoryEnum

    proper_cats = [TagCategoryEnum.character, TagCategoryEnum.copyright, TagCategoryEnum.artist]

    unresolved_subq = (
        db.query(TagTranslation.canonical_name)
        .filter(
            TagTranslation.language == lang,
            TagTranslation.status != "rejected",
            TagTranslation.needs_review == False,
        )
        .subquery()
    )

    tags = (
        db.query(Tag)
        .filter(
            Tag.category.in_(proper_cats),
            ~Tag.name.in_(db.query(unresolved_subq.c.canonical_name)),
        )
        .order_by(Tag.post_count.desc())
        .limit(limit)
        .all()
    )

    result = []
    for t in tags:
        existing = (
            db.query(TagTranslation)
            .filter(
                TagTranslation.canonical_name == t.name,
                TagTranslation.language == lang,
            )
            .first()
        )
        result.append({
            "tag_id": t.id,
            "canonical_name": t.name,
            "category": t.category.value if hasattr(t.category, "value") else str(t.category),
            "post_count": t.post_count,
            "has_unreviewed_llm": existing is not None and existing.source == "llm" and existing.needs_review,
            "current_display": existing.display_name if existing else None,
        })

    return result


def get_entity_resolver_status(db: Session) -> Dict[str, Any]:
    """Return status summary for the entity alias resolver."""
    from ..config import settings
    from ..models import Tag, TagTranslation
    from ..enums import TagCategoryEnum

    proper_cats = [TagCategoryEnum.character, TagCategoryEnum.copyright, TagCategoryEnum.artist]

    total_proper = db.query(Tag).filter(Tag.category.in_(proper_cats)).count()

    resolved = (
        db.query(TagTranslation)
        .filter(
            TagTranslation.language == "zh-CN",
            TagTranslation.category.in_(["character", "copyright", "artist"]),
            TagTranslation.needs_review == False,
            TagTranslation.status != "rejected",
        )
        .count()
    )

    needs_review = (
        db.query(TagTranslation)
        .filter(
            TagTranslation.language == "zh-CN",
            TagTranslation.category.in_(["character", "copyright", "artist"]),
            TagTranslation.needs_review == True,
            TagTranslation.status != "rejected",
        )
        .count()
    )

    no_translation = total_proper - resolved - needs_review
    if no_translation < 0:
        no_translation = 0

    return {
        "enabled": settings.ENTITY_ALIAS_RESOLVER_ENABLED,
        "llm_available": get_llm_provider().is_available(),
        "total_proper_noun_tags": total_proper,
        "resolved": resolved,
        "needs_review": needs_review,
        "no_translation": no_translation,
        "config": {
            "batch_size": settings.ENTITY_ALIAS_BATCH_SIZE,
            "max_per_run": settings.ENTITY_ALIAS_MAX_PER_RUN,
        },
    }


async def run_entity_resolution(
    db: Session,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Run entity alias resolution for pending proper-noun tags.

    Returns a summary of results. Saves resolved aliases to TagTranslation table.
    """
    from ..config import settings
    from .tag_localization_service import upsert_translation
    from ..utils.search_parser import invalidate_translation_cache

    if limit is None:
        limit = settings.ENTITY_ALIAS_MAX_PER_RUN

    pending = list_pending_proper_nouns(db, limit=limit)
    if not pending:
        return {"processed": 0, "resolved": 0, "kept_original": 0, "failed": 0, "message": "No pending proper-noun tags"}

    tag_inputs = [{"name": p["canonical_name"], "category": p["category"]} for p in pending]

    results = await resolve_entity_aliases(tag_inputs)

    resolved = 0
    kept_original = 0
    failed = 0

    for tr in results:
        try:
            is_unchanged = tr.display_name_zh.replace(" ", "_") == tr.canonical_name or tr.display_name_zh == tr.canonical_name
            saved = upsert_translation(
                db,
                canonical_name=tr.canonical_name,
                display_name=tr.display_name_zh,
                lang="zh-CN",
                aliases=tr.aliases_zh,
                category=tr.category,
                source="llm",
                status="translated",
                needs_review=tr.needs_review or is_unchanged,
                provider="entity_resolver",
                force=True,
            )
            if saved:
                if tr.needs_review or is_unchanged:
                    kept_original += 1
                else:
                    resolved += 1
            else:
                kept_original += 1
        except Exception as e:
            failed += 1
            logger.error("Entity resolver save error for %s: %s", tr.canonical_name, e)
            try:
                db.rollback()
            except Exception:
                pass

    if resolved > 0:
        invalidate_translation_cache()

    return {
        "processed": len(results),
        "resolved": resolved,
        "kept_original": kept_original,
        "failed": failed,
    }
