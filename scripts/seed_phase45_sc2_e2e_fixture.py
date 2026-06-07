"""Seed a minimal Phase 4.5-SC2 SourceConcept E2E fixture in the test DB only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.enums import FileTypeEnum, TagCategoryEnum  # noqa: E402
from app.models import (  # noqa: E402
    Media,
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
    Tag,
)
from app.services.source_metadata_registry_service import canonical_source_key  # noqa: E402
from app.utils.cache import invalidate_source_concept_search_cache  # noqa: E402

RUN_ID = "phase45-sc2-e2e-fixture"
MARKER_TAG = "phase45_sc2_e2e_marker"
AYAKA_JA = "\u795e\u91cc\u7dbe\u83ef"


def _ensure_test_db() -> None:
    database.assert_test_db()
    if not settings.IS_TEST_ENV:
        raise RuntimeError("VIOLET_ENV must be test for SC2 E2E fixture seeding")


def _ensure_tag(db, name: str, category: TagCategoryEnum) -> Tag:
    row = db.query(Tag).filter(Tag.name == name).one_or_none()
    if row is None:
        row = Tag(name=name, category=category, post_count=1)
        db.add(row)
        db.flush()
    return row


def _write_media_file(relative_path: str) -> int:
    path = settings.resolve_storage_path(relative_path)
    if path is None:
        raise RuntimeError(f"Unsafe fixture media path: {relative_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    image = Image.new("RGB", (1, 1), (236, 236, 236))
    image.save(path, format="JPEG")
    return path.stat().st_size


def _create_media(db, name: str, tag_names: list[str]) -> Media:
    original = f"media/original/{name}.jpg"
    thumbnail = f"media/thumbnails/{name}.jpg"
    file_size = _write_media_file(original)
    _write_media_file(thumbnail)
    media = Media(
        filename=f"{name}.jpg",
        path=original,
        thumbnail_path=thumbnail,
        hash=f"hash-{RUN_ID}-{name}",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=file_size,
        width=1,
        height=1,
    )
    db.add(media)
    db.flush()
    for tag_name in tag_names:
        category = TagCategoryEnum.copyright if tag_name == "genshin_impact" else TagCategoryEnum.general
        tag = _ensure_tag(db, tag_name, category)
        media.tags.append(tag)
    db.flush()
    return media


def _add_source_concept(
    db,
    medias: list[Media],
    *,
    status: str = "active",
    display_name: str = "Kamisato Ayaka",
    aliases: list[str] | None = None,
    concept_key_suffix: str | None = None,
) -> SourceConcept:
    suffix = concept_key_suffix or canonical_source_key(display_name)
    concept = SourceConcept(
        concept_key=f"character:phase45_sc2_e2e:{suffix}:{status}",
        primary_display_name=display_name,
        concept_type_hint="character",
        status=status,
        confidence_score=0.94 if status == "active" else 0.41,
        evidence_score=0.9 if status == "active" else 0.34,
        media_count=len(medias),
        source_count=1,
        evidence_summary_json={
            "origin_counts": {"source_searchable_name_assertion": len(medias)},
            "max_trust_tier": "strong" if status == "active" else "weak",
            "fixture": RUN_ID,
        },
        created_by_run_id=RUN_ID,
    )
    db.add(concept)
    db.flush()

    first_signal = None
    for idx, media in enumerate(medias, start=1):
        display_key = canonical_source_key(display_name)
        signal = SourceConceptSignal(
            signal_key=f"{RUN_ID}:{status}:{suffix}:{display_key}:signal:{media.id}:{idx}",
            origin_type="source_searchable_name_assertion",
            origin_table="blombooru_source_searchable_name_assertions",
            origin_id=f"{RUN_ID}:{media.id}:{idx}",
            provider="pixiv",
            media_id=media.id,
            raw_value=display_name,
            display_value=display_name,
            normalized_key=canonical_source_key(display_name),
            canonical_key=canonical_source_key(display_name),
            role_hint="character",
            work_context_key="genshin_impact",
            source_kind="source_assertion",
            trust_tier="strong" if status == "active" else "weak",
            confidence=0.94 if status == "active" else 0.41,
            status=status,
            evidence_payload={"fixture": RUN_ID, "source_layer_only": True},
            created_by_run_id=RUN_ID,
            source_run_id=RUN_ID,
        )
        db.add(signal)
        db.flush()
        first_signal = first_signal or signal
        db.add(
            SourceConceptEvidence(
                concept_id=concept.id,
                signal_id=signal.id,
                media_id=media.id,
                provider="pixiv",
                evidence_type="source_searchable_name_assertion",
                evidence_strength="strong" if status == "active" else "weak",
                payload={"fixture": RUN_ID, "source_layer_only": True},
                run_id=RUN_ID,
                status=status,
            )
        )
        db.add(
            SourceConceptSignalLink(
                signal_id=signal.id,
                concept_id=concept.id,
                link_status=status,
                confidence=0.94 if status == "active" else 0.41,
                resolution_reason_code="phase45_sc2_e2e_fixture",
                resolver_version=RUN_ID,
                run_id=RUN_ID,
                evidence_payload={"source_layer_only": True},
            )
        )

    seen_keys: set[str] = set()
    aliases = aliases or [display_name, "kamisato_ayaka", AYAKA_JA]
    for alias_value in aliases:
        alias_key = canonical_source_key(alias_value)
        if alias_key in seen_keys:
            continue
        seen_keys.add(alias_key)
        alias_role = "source_searchable_name_assertion"
        db.add(
            SourceConceptAlias(
                concept_id=concept.id,
                alias_value=alias_value,
                alias_key=alias_key,
                display_name=alias_value,
                alias_role=alias_role,
                status=status,
                confidence=0.94 if status == "active" else 0.41,
                source_signal_id=first_signal.id if first_signal else None,
                evidence_payload={"fixture": RUN_ID, "source_layer_only": True},
                created_by_run_id=RUN_ID,
            )
        )
        db.add(
            SourceConceptSearchIndex(
                concept_id=concept.id,
                search_key=alias_key,
                display_name=alias_value,
                alias_role=alias_role,
                weight=0.94 if status == "active" else 0.41,
                status=status,
                evidence_refs_json={"fixture": RUN_ID},
                run_id=RUN_ID,
            )
        )
    db.flush()
    return concept


def _delete_existing_fixture(db) -> None:
    db.query(SourceConcept).filter(SourceConcept.created_by_run_id == RUN_ID).delete(synchronize_session=False)
    db.query(SourceConceptSignal).filter(SourceConceptSignal.created_by_run_id == RUN_ID).delete(synchronize_session=False)
    db.query(Media).filter(Media.hash.like(f"hash-{RUN_ID}-%")).delete(synchronize_session=False)
    db.commit()
    invalidate_source_concept_search_cache()


def main() -> int:
    database.init_engine()
    database.init_db()
    _ensure_test_db()
    db = database.SessionLocal()
    try:
        _delete_existing_fixture(db)
        both = _create_media(db, "sc2-e2e-ayaka-both", [MARKER_TAG, "genshin_impact"])
        concept_only = _create_media(db, "sc2-e2e-ayaka-concept-only", [])
        tag_only = _create_media(db, "sc2-e2e-ayaka-tag-only", [MARKER_TAG, "genshin_impact"])
        concept = _add_source_concept(db, [both, concept_only])
        duplicate_concept = _add_source_concept(
            db,
            [both],
            display_name="Kamisato Ayaka",
            aliases=["Kamisato Ayaka", "kamisato_ayaka"],
            concept_key_suffix="kamisato_ayaka_duplicate",
        )
        metachar_concept = _add_source_concept(
            db,
            [both],
            display_name="Re:Zero",
            aliases=["Re:Zero"],
            concept_key_suffix="rezero",
        )
        review_media = _create_media(db, "sc2-e2e-review-only", [])
        review_concept = _add_source_concept(
            db,
            [review_media],
            status="needs_review",
            display_name="Review Only Character",
            aliases=["Review Only Character", "review_only_character"],
        )
        db.commit()
        invalidate_source_concept_search_cache()
        print(
            json.dumps(
                {
                    "status": "ready",
                    "run_id": RUN_ID,
                    "db_name": settings.DB_NAME,
                    "media_ids": {
                        "tag_and_concept": both.id,
                        "concept_only": concept_only.id,
                        "tag_only": tag_only.id,
                        "needs_review": review_media.id,
                    },
                    "concept_ids": {
                        "active": concept.id,
                        "duplicate": duplicate_concept.id,
                        "metachar": metachar_concept.id,
                        "needs_review": review_concept.id,
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
