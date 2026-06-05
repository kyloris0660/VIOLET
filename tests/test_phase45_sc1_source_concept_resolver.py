"""Focused tests for Phase 4.5-SC1 source concept resolver core."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base, migrate_add_source_concept_resolver_core  # noqa: E402
from app.enums import FileTypeEnum, TagCategoryEnum  # noqa: E402
from app.models import (  # noqa: E402
    Media,
    SourceConcept,
    SourceConceptAlias,
    SourceMetadataRecord,
    SourceNameAliasCandidate,
    SourceNameCandidate,
    SourceNameCandidateExtractionRun,
    SourceNameObservation,
    SourceSearchableNameAssertion,
    Tag,
    blombooru_media_tags,
)
from app.services.source_concept_resolver_service import (  # noqa: E402
    SourceConceptSignalDraft,
    build_source_concept_signals,
    import_f7a_final_pack_candidates,
    resolve_source_concepts,
    run_source_concept_resolution,
)


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    return engine, session


def _media(media_id: int = 1) -> Media:
    return Media(
        id=media_id,
        filename=f"m{media_id}.jpg",
        path=f"/tmp/m{media_id}.jpg",
        file_type=FileTypeEnum.image,
    )


def _seed_multi_source_case(session) -> str:
    media = _media(10)
    metadata = SourceMetadataRecord(
        id=1,
        provider="pixiv",
        provider_record_key="pixiv:1",
        media_id=media.id,
        title="Genshin Impact illustration",
        artist_name="artist_one",
        metadata_kind="gallery_dl_real_pixiv_metadata",
        data_type_label="real_live_or_local_provider_data",
        status="observed",
    )
    run = SourceNameCandidateExtractionRun(
        run_id="f7a-final",
        run_label="f7a",
        extractor_version="test",
        structured_output_schema_version="test",
        mode="apply_db",
        status="completed",
    )
    session.add_all([media, metadata, run])
    session.flush()
    f7a_candidate = SourceNameCandidate(
        extraction_run_id=run.id,
        source_metadata_record_id=metadata.id,
        media_id=media.id,
        provider="pixiv",
        group_key="source_record:1",
        candidate_key="f7a-final:jp-ayaka",
        origin_type="pixiv_tag",
        origin_id="tag:jp-ayaka",
        raw_value="\u795e\u91cc\u7dbe\u83ef",
        display_name="\u795e\u91cc\u7dbe\u83ef",
        normalized_value="\u795e\u91cc\u7dbe\u83ef",
        canonical_key="\u795e\u91cc\u7dbe\u83ef",
        candidate_role="character",
        candidate_status="active_candidate",
        extraction_verdict="single_candidate_found",
        extraction_action="accepted",
        confidence=0.86,
        extractor_version="test",
        status="active",
    )
    assertion = SourceSearchableNameAssertion(
        provider="pixiv",
        source_metadata_record_id=metadata.id,
        assertion_key="assert:kamisato-ayaka",
        raw_input="Kamisato Ayaka",
        normalized_input="kamisato ayaka",
        canonical_name_key="kamisato_ayaka",
        asserted_name="Kamisato Ayaka",
        asserted_role="character",
        status="searchable_active",
        confidence="high",
        confidence_score=0.92,
        structured_output_schema_version="test",
        requires_review=False,
    )
    work_observation = SourceNameObservation(
        source_metadata_record_id=metadata.id,
        provider="pixiv",
        observation_key="work:genshin",
        media_id=media.id,
        raw_name="Genshin Impact",
        normalized_name="Genshin Impact",
        canonical_name_key="genshin_impact",
        name_role="work_title",
        source_field="work",
        confidence=0.9,
        requires_review=False,
        status="observed",
    )
    alias_edge = SourceNameAliasCandidate(
        source_name_key="\u795e\u91cc\u7dbe\u83ef",
        target_name_key="kamisato_ayaka",
        source_display_name="\u795e\u91cc\u7dbe\u83ef",
        target_display_name="Kamisato Ayaka",
        relation_type="same_source_concept",
        evidence_source="manual_fixture",
        confidence=0.9,
        status="candidate",
        requires_review=True,
    )
    tag = Tag(name="kamisato_ayaka", category=TagCategoryEnum.character)
    ai_tag = Tag(name="ai_only_character", category=TagCategoryEnum.character)
    session.add_all([f7a_candidate, assertion, work_observation, alias_edge, tag, ai_tag])
    session.flush()
    session.execute(
        blombooru_media_tags.insert().values(
            media_id=media.id,
            tag_id=tag.id,
            source="manual",
            confidence=1.0,
            is_locked=True,
            is_suggestion=False,
        )
    )
    session.execute(
        blombooru_media_tags.insert().values(
            media_id=media.id,
            tag_id=ai_tag.id,
            source="wd_tagger",
            confidence=0.72,
            is_locked=False,
            is_suggestion=False,
        )
    )
    session.commit()
    return run.run_id


def test_migration_creates_source_concept_tables_additive_only():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE blombooru_media (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE blombooru_source_metadata_records (id INTEGER PRIMARY KEY)"))
    inspector = inspect(engine)

    migrate_add_source_concept_resolver_core(engine, inspector)

    tables = set(inspect(engine).get_table_names())
    assert "blombooru_source_concept_resolution_runs" in tables
    assert "blombooru_source_concept_signals" in tables
    assert "blombooru_source_concepts" in tables
    assert "blombooru_source_concept_aliases" in tables
    assert "blombooru_media_tags" not in tables


def test_alias_key_is_concept_scoped_not_global():
    _engine, session = _db()
    concept_one = SourceConcept(concept_key="character:mona:work:genshin", primary_display_name="Mona", concept_type_hint="character")
    concept_two = SourceConcept(concept_key="character:mona:work:other", primary_display_name="Mona", concept_type_hint="character")
    session.add_all([concept_one, concept_two])
    session.flush()
    session.add_all(
        [
            SourceConceptAlias(concept_id=concept_one.id, alias_value="Mona", alias_key="mona", display_name="Mona", alias_role="normal_media_tag"),
            SourceConceptAlias(concept_id=concept_two.id, alias_value="Mona", alias_key="mona", display_name="Mona", alias_role="normal_media_tag"),
        ]
    )
    session.commit()

    assert session.query(SourceConceptAlias).filter_by(alias_key="mona").count() == 2


def test_adapter_builds_multi_source_signals_and_medium_ai_distinction():
    _engine, session = _db()
    f7a_run_id = _seed_multi_source_case(session)

    signals = build_source_concept_signals(session, run_id="sc1-test", f7a_run_id=f7a_run_id)
    origins = {signal.origin_type for signal in signals}

    assert "f7a_candidate" in origins
    assert "normal_media_tag" in origins
    assert "ai_model_tag" in origins
    assert "source_assertion" in origins
    assert "source_name_observation" in origins
    assert "source_alias_candidate" in origins
    assert "provider_structured_field" in origins
    assert any(signal.origin_type == "ai_model_tag" and signal.trust_tier == "medium_ai" for signal in signals)


def test_alias_edge_links_multilingual_sources_without_entity_truth():
    _engine, session = _db()
    f7a_run_id = _seed_multi_source_case(session)
    result, _inventory, persistence = run_source_concept_resolution(
        session,
        run_id="sc1-test",
        f7a_run_id=f7a_run_id,
        apply=True,
    )

    alias_edge_concepts = [concept for concept in result.concepts if concept.concept_key.startswith("alias_edge:")]
    assert alias_edge_concepts
    concept = alias_edge_concepts[0]
    assert concept.status == "active"
    assert {"f7a_candidate", "source_assertion", "normal_media_tag", "source_alias_candidate"}.issubset(
        concept.evidence_summary["origin_counts"].keys()
    )
    assert persistence["forbidden_truth_table_write_count"] == 0


def test_ai_only_signal_creates_needs_review_not_active_truth():
    signal = SourceConceptSignalDraft(
        signal_key="ai:1",
        origin_type="ai_model_tag",
        origin_table="blombooru_media_tags",
        origin_id="1:1",
        provider="wd_tagger",
        media_id=1,
        source_metadata_record_id=None,
        source_record_id=None,
        raw_value="Mona",
        display_value="Mona",
        normalized_key="mona",
        canonical_key="mona",
        role_hint="character",
        work_context_key=None,
        parenthetical_base=None,
        parenthetical_context=None,
        source_kind="tag_category:character",
        trust_tier="medium_ai",
        confidence=0.8,
        status="needs_review",
    )

    result = resolve_source_concepts([signal], run_id="sc1-test")

    assert result.concepts[0].status == "needs_review"
    assert result.concepts[0].confidence_score <= 0.59
    assert result.links[0].negative_reason_code == "ambiguous_short_without_work_context"


def test_short_name_without_work_context_does_not_overmerge_active():
    signals = [
        SourceConceptSignalDraft(
            signal_key=f"manual:{idx}",
            origin_type="normal_media_tag",
            origin_table="blombooru_media_tags",
            origin_id=f"{idx}:{idx}",
            provider="manual",
            media_id=idx,
            source_metadata_record_id=None,
            source_record_id=None,
            raw_value="Mona",
            display_value="Mona",
            normalized_key="mona",
            canonical_key="mona",
            role_hint="character",
            work_context_key=None,
            parenthetical_base=None,
            parenthetical_context=None,
            source_kind="tag_category:character",
            trust_tier="strong",
            confidence=1.0,
            status="active",
        )
        for idx in (1, 2)
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert len(result.concepts) == 2
    assert all(concept.status == "needs_review" for concept in result.concepts)
    assert all(link.negative_reason_code == "ambiguous_short_without_work_context" for link in result.links)


def test_source_title_only_signal_remains_needs_review_context():
    signal = SourceConceptSignalDraft(
        signal_key="title:1",
        origin_type="provider_structured_field",
        origin_table="blombooru_source_metadata_records",
        origin_id="1",
        provider="saucenao",
        media_id=1,
        source_metadata_record_id=1,
        source_record_id="saucenao:1",
        raw_value="Pretty blue dress",
        display_value="Pretty blue dress",
        normalized_key="pretty_blue_dress",
        canonical_key="pretty_blue_dress",
        role_hint="source_title",
        work_context_key=None,
        parenthetical_base=None,
        parenthetical_context=None,
        source_kind="title",
        trust_tier="weak",
        confidence=0.5,
        status="needs_review",
    )

    result = resolve_source_concepts([signal], run_id="sc1-test")

    assert result.concepts[0].status == "needs_review"
    assert result.links[0].negative_reason_code == "source_title_only_guard"


def test_f7a_final_pack_backfill_uses_candidate_bundle_without_provider_calls(tmp_path):
    _engine, session = _db()
    pack = tmp_path / "f7a-pack"
    pack.mkdir()
    (pack / "summary.json").write_text(
        json.dumps({"run_id": "f7a-pack-run", "validated_head": "abc", "candidate_summary": {"total": 1}}),
        encoding="utf-8",
    )
    (pack / "candidate-bundle.jsonl").write_text(
        json.dumps(
            {
                "group_key": "source_record:1",
                "provider": "pixiv",
                "source_metadata_record_id": None,
                "media_id": None,
                "origin_type": "pixiv_tag",
                "origin_id": "tag:1",
                "raw_value": "\u795e\u91cc\u7dbe\u83ef",
                "display_name": "\u795e\u91cc\u7dbe\u83ef",
                "normalized_value": "\u795e\u91cc\u7dbe\u83ef",
                "canonical_key": "\u795e\u91cc\u7dbe\u83ef",
                "candidate_role": "character",
                "candidate_status": "active_candidate",
                "extraction_verdict": "single_candidate_found",
                "extraction_action": "accepted",
                "confidence": 0.9,
                "candidate_key": "logical-key",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=True)

    assert result["candidate_bundle_count"] == 1
    assert result["persistence"]["forbidden_truth_table_write_count"] == 0
    assert session.query(SourceNameCandidate).count() == 1
