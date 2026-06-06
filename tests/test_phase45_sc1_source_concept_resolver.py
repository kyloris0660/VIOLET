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
    SourceConceptEvidence,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
    SourceMetadataRecord,
    SourceNameAliasCandidate,
    SourceNameCandidate,
    SourceNameCandidateExtractionRun,
    SourceNameObservation,
    SourceSearchableNameAssertion,
    SourceTagObservation,
    Tag,
    blombooru_media_tags,
)
from app.services.source_concept_resolver_service import (  # noqa: E402
    LLMAdjudicationConfig,
    SourceConceptSignalDraft,
    build_artifact_consistency_check,
    canonical_key,
    edges_from_llm_judgments,
    build_source_concept_signals,
    llm_cache_fingerprint,
    plan_llm_adjudication,
    import_f7a_final_pack_candidates,
    resolve_source_concepts,
    run_bounded_llm_adjudication,
    run_source_concept_resolution,
    persist_source_concept_resolution,
)
from scripts.run_phase45_sc1_source_concept_resolver import (
    build_readiness_check,
    concept_case_review,
    random_holdout_review,
)


def _signal(
    key: str,
    raw: str,
    *,
    origin_type: str = "normal_media_tag",
    role: str = "character",
    trust: str = "strong",
    status: str = "active",
    media_id: int | None = None,
    source_record_id: int | None = None,
    work_context_key: str | None = None,
    source_kind: str | None = "tag_category:character",
    payload: dict | None = None,
    source_run_id: str | None = None,
) -> SourceConceptSignalDraft:
    return SourceConceptSignalDraft(
        signal_key=key,
        origin_type=origin_type,
        origin_table="fixture",
        origin_id=key,
        provider="fixture",
        media_id=media_id,
        source_metadata_record_id=source_record_id,
        source_record_id=str(source_record_id) if source_record_id is not None else None,
        raw_value=raw,
        display_value=raw,
        normalized_key=raw.lower().replace(" ", "_"),
        canonical_key=raw.lower().replace(" ", "_"),
        role_hint=role,
        work_context_key=work_context_key,
        parenthetical_base=None,
        parenthetical_context=None,
        source_kind=source_kind,
        trust_tier=trust,
        confidence=0.9,
        status=status,
        evidence_payload=payload or {},
        source_run_id=source_run_id,
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


def _source_concept_signal_row(signal_key: str, *, source_run_id: str, status: str = "active") -> SourceConceptSignal:
    return SourceConceptSignal(
        signal_key=signal_key,
        origin_type="f7a_candidate",
        origin_table="fixture",
        origin_id=signal_key,
        provider="fixture",
        raw_value=signal_key,
        display_value=signal_key,
        normalized_key=signal_key,
        canonical_key=signal_key,
        role_hint="character",
        trust_tier="medium",
        status=status,
        source_run_id=source_run_id,
        created_by_run_id="old-run",
    )


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

    concept = next(
        concept
        for concept in result.concepts
        if {"f7a_candidate", "source_assertion", "normal_media_tag", "source_alias_candidate"}.issubset(
            concept.evidence_summary["origin_counts"].keys()
        )
    )
    assert concept.status == "active"
    assert concept.evidence_summary["work_context_key"] == "genshin_impact"
    assert persistence["forbidden_truth_table_write_count"] == 0


def test_scoped_stale_supersede_leaves_unrelated_runs_untouched():
    _engine, session = _db()
    stale_signal = _source_concept_signal_row("stale-scope-signal", source_run_id="scope-a")
    other_signal = _source_concept_signal_row("other-scope-signal", source_run_id="scope-b")
    stale_concept = SourceConcept(
        concept_key="character:stale_scope",
        primary_display_name="stale",
        concept_type_hint="character",
        status="active",
        created_by_run_id="old-run",
    )
    other_concept = SourceConcept(
        concept_key="character:other_scope",
        primary_display_name="other",
        concept_type_hint="character",
        status="active",
        created_by_run_id="old-run",
    )
    session.add_all([stale_signal, other_signal, stale_concept, other_concept])
    session.flush()
    stale_alias = SourceConceptAlias(
        concept_id=stale_concept.id,
        alias_value="stale",
        alias_key="stale",
        display_name="stale",
        alias_role="f7a_candidate",
        status="active",
        source_signal_id=stale_signal.id,
        created_by_run_id="old-run",
    )
    other_alias = SourceConceptAlias(
        concept_id=other_concept.id,
        alias_value="other",
        alias_key="other",
        display_name="other",
        alias_role="f7a_candidate",
        status="active",
        source_signal_id=other_signal.id,
        created_by_run_id="old-run",
    )
    stale_evidence = SourceConceptEvidence(
        concept_id=stale_concept.id,
        signal_id=stale_signal.id,
        evidence_type="f7a_candidate",
        evidence_strength="medium",
        status="active",
        run_id="old-run",
    )
    other_evidence = SourceConceptEvidence(
        concept_id=other_concept.id,
        signal_id=other_signal.id,
        evidence_type="f7a_candidate",
        evidence_strength="medium",
        status="active",
        run_id="old-run",
    )
    stale_link = SourceConceptSignalLink(
        signal_id=stale_signal.id,
        concept_id=stale_concept.id,
        link_status="active",
        resolver_version="old",
        run_id="old-run",
    )
    other_link = SourceConceptSignalLink(
        signal_id=other_signal.id,
        concept_id=other_concept.id,
        link_status="active",
        resolver_version="old",
        run_id="old-run",
    )
    stale_search = SourceConceptSearchIndex(
        concept_id=stale_concept.id,
        search_key="stale",
        display_name="stale",
        alias_role="f7a_candidate",
        weight=0.5,
        status="active",
        run_id="old-run",
    )
    other_search = SourceConceptSearchIndex(
        concept_id=other_concept.id,
        search_key="other",
        display_name="other",
        alias_role="f7a_candidate",
        weight=0.5,
        status="active",
        run_id="old-run",
    )
    session.add_all([stale_alias, other_alias, stale_evidence, other_evidence, stale_link, other_link, stale_search, other_search])
    session.commit()

    result = resolve_source_concepts(
        [_signal("fresh-scope-signal", "fresh_scope", source_run_id="scope-a")],
        run_id="new-run",
    )
    persistence = persist_source_concept_resolution(session, result, apply=True)

    for row in (stale_signal, stale_concept, stale_alias, stale_evidence, stale_link, stale_search):
        session.refresh(row)
    for row in (other_signal, other_concept, other_alias, other_evidence, other_link, other_search):
        session.refresh(row)

    assert stale_signal.status == "superseded"
    assert stale_concept.status == "superseded"
    assert stale_alias.status == "superseded"
    assert stale_evidence.status == "superseded"
    assert stale_link.link_status == "superseded"
    assert stale_search.status == "superseded"
    assert other_signal.status == "active"
    assert other_concept.status == "active"
    assert other_alias.status == "active"
    assert other_evidence.status == "active"
    assert other_link.link_status == "active"
    assert other_search.status == "active"
    assert persistence["stale_supersede_scope"]["source_run_ids"] == ["scope-a"]
    assert persistence["stale_supersede_scope_violation_count"] == 0


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


def test_general_source_tag_observation_is_excluded_from_concept_buckets():
    _engine, session = _db()
    media = _media(20)
    metadata = SourceMetadataRecord(
        id=20,
        provider="pixiv",
        provider_record_key="pixiv:general",
        media_id=media.id,
        metadata_kind="gallery_dl_real_pixiv_metadata",
        data_type_label="real_live_or_local_provider_data",
        status="observed",
    )
    tag_observation = SourceTagObservation(
        source_metadata_record_id=metadata.id,
        provider="pixiv",
        observation_key="tag:blue_dress",
        raw_tag="blue_dress",
        normalized_tag="blue_dress",
        canonical_tag_key="blue_dress",
        source_tag_kind="provider_tag",
        source_category_raw="0",
        status="observed",
    )
    session.add_all([media, metadata, tag_observation])
    session.commit()

    signals = build_source_concept_signals(session, run_id="sc1-test")
    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert any(signal.raw_value == "blue_dress" and signal.trust_tier == "rejected" for signal in result.signals)
    assert all("blue_dress" not in {signal.raw_value for signal in concept.signals} for concept in result.concepts)
    assert all(item.search_key != "blue_dress" for item in result.search_index)


def test_medium_ai_f7a_candidates_remain_review_without_non_ai_corroboration():
    signals = [
        _signal(
            "f7a-ai:1",
            "Nilou",
            origin_type="f7a_candidate",
            trust="medium_ai",
            status="needs_review",
            work_context_key="genshin_impact",
            source_kind="ai_model_tag",
        ),
        _signal(
            "f7a-ai:2",
            "Nilou",
            origin_type="f7a_candidate",
            trust="medium_ai",
            status="needs_review",
            work_context_key="genshin_impact",
            source_kind="ai_model_tag",
        ),
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert result.concepts
    assert all(concept.status == "needs_review" for concept in result.concepts)
    assert result.summary["ai_only_active_violation_count"] == 0


def test_ai_signal_can_activate_only_with_non_ai_corroboration():
    signals = [
        _signal(
            "f7a-ai:1",
            "nilou_(genshin_impact)",
            origin_type="f7a_candidate",
            trust="medium_ai",
            status="needs_review",
            source_kind="ai_model_tag",
        ),
        _signal(
            "manual:1",
            "nilou_(genshin_impact)",
            origin_type="normal_media_tag",
            trust="strong",
            status="active",
        ),
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    concept = result.concepts[0]
    assert concept.status == "active"
    assert concept.evidence_summary["guards"]["medium_ai_present"] is True


def test_repeated_exact_identity_anchor_materializes_one_concept():
    signals = [
        _signal(
            f"kamisato:{idx}",
            "kamisato_ayaka",
            media_id=idx,
            work_context_key="genshin_impact",
        )
        for idx in (1, 2, 3)
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert len(result.concepts) == 1
    assert result.concepts[0].concept_key == "character:kamisato_ayaka:work:genshin_impact"
    assert result.summary["undermerge_violation_count"] == 0
    assert result.summary["fragmentation_violation_count"] == 0


def test_exact_same_canonical_different_work_contexts_do_not_active_merge():
    signals = [
        _signal("left", "alexandria", work_context_key="work_a"),
        _signal("right", "alexandria", work_context_key="work_b"),
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert len(result.concepts) == 2
    conflict_edges = [edge for edge in result.edge_candidates if edge.negative_reason_code == "work_context_conflict"]
    assert conflict_edges
    assert all(edge.status == "rejected" for edge in conflict_edges)
    assert result.summary["context_conflict_active_merge_count"] == 0
    assert result.summary["overmerge_violation_count"] == 0


def test_exact_same_canonical_same_work_context_can_merge():
    signals = [
        _signal("left", "alexandria", work_context_key="work_a"),
        _signal("right", "alexandria", work_context_key="work_a"),
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert len(result.concepts) == 1
    assert result.concepts[0].status == "active"
    assert result.summary["context_conflict_active_merge_count"] == 0


def test_exact_same_canonical_partial_context_is_review_not_active():
    signals = [
        _signal("left", "alexandria", work_context_key="work_a"),
        _signal("right", "alexandria"),
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert len(result.concepts) == 1
    assert result.concepts[0].status == "needs_review"
    exact_edges = [edge for edge in result.edge_candidates if edge.edge_type == "exact_canonical_key"]
    assert exact_edges
    assert exact_edges[0].status == "needs_review"


def test_repeated_danbooru_style_tags_materialize_one_context_concept():
    signals = [
        _signal(
            f"mona:{idx}",
            "mona_(genshin_impact)",
            media_id=idx,
            work_context_key=None,
        )
        for idx in (1, 2, 3)
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert len(result.concepts) == 1
    assert result.concepts[0].concept_key == "character:mona:work:genshin_impact"
    assert result.summary["fragmentation_violation_count"] == 0


def test_ai_only_same_identity_anchor_groups_as_needs_review_not_active():
    signals = [
        _signal(
            f"ai:nilou:{idx}",
            "nilou_(genshin_impact)",
            origin_type="ai_model_tag",
            trust="medium_ai",
            status="needs_review",
            source_kind="wd_tagger",
            media_id=idx,
        )
        for idx in (1, 2)
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert len(result.concepts) == 1
    assert result.concepts[0].status == "needs_review"
    assert result.concepts[0].confidence_score <= 0.59
    assert result.summary["ai_only_active_violation_count"] == 0
    assert result.summary["fragmentation_violation_count"] == 0


def test_alias_component_union_links_a_b_and_a_c_in_one_component():
    alias_ab_source = _signal(
        "alias:ab:source",
        "A",
        origin_type="source_alias_candidate",
        role="unknown",
        trust="medium",
        status="needs_review",
        source_kind="alias_edge_source",
        payload={"relation_type": "same_source_concept", "source_name_key": "a", "target_name_key": "b"},
    )
    alias_ab_target = _signal(
        "alias:ab:target",
        "B",
        origin_type="source_alias_candidate",
        role="unknown",
        trust="medium",
        status="needs_review",
        source_kind="alias_edge_target",
        payload={"relation_type": "same_source_concept", "source_name_key": "a", "target_name_key": "b"},
    )
    alias_ac_source = _signal(
        "alias:ac:source",
        "A",
        origin_type="source_alias_candidate",
        role="unknown",
        trust="medium",
        status="needs_review",
        source_kind="alias_edge_source",
        payload={"relation_type": "same_source_concept", "source_name_key": "a", "target_name_key": "c"},
    )
    alias_ac_target = _signal(
        "alias:ac:target",
        "C",
        origin_type="source_alias_candidate",
        role="unknown",
        trust="medium",
        status="needs_review",
        source_kind="alias_edge_target",
        payload={"relation_type": "same_source_concept", "source_name_key": "a", "target_name_key": "c"},
    )
    signals = [
        _signal("normal:a", "A", work_context_key="work"),
        _signal("normal:b", "B", work_context_key="work"),
        _signal("normal:c", "C", work_context_key="work"),
        alias_ab_source,
        alias_ab_target,
        alias_ac_source,
        alias_ac_target,
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    concepts_with_aliases = [
        concept for concept in result.concepts
        if {"a", "b", "c"}.issubset({signal.canonical_key for signal in concept.signals})
    ]
    assert len(concepts_with_aliases) == 1


def test_alias_edge_same_context_can_link():
    alias = _signal(
        "alias:edge",
        "Alex alias",
        origin_type="source_alias_candidate",
        role="unknown",
        trust="medium",
        status="needs_review",
        payload={"relation_type": "same_source_concept", "source_name_key": "alexandria", "target_name_key": "alex"},
    )
    signals = [
        _signal("left", "alexandria", work_context_key="work_a"),
        _signal("right", "alex", work_context_key="work_a"),
        alias,
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    linked = [
        concept
        for concept in result.concepts
        if {"alexandria", "alex"}.issubset({signal.canonical_key for signal in concept.signals})
    ]
    assert len(linked) == 1
    assert any(edge.edge_type == "alias_candidate_edge" and edge.status == "active" for edge in result.edge_candidates)


def test_alias_edge_conflicting_contexts_is_blocked():
    alias = _signal(
        "alias:edge",
        "Alex alias",
        origin_type="source_alias_candidate",
        role="unknown",
        trust="medium",
        status="needs_review",
        payload={"relation_type": "same_source_concept", "source_name_key": "alexandria", "target_name_key": "alex"},
    )
    signals = [
        _signal("left", "alexandria", work_context_key="work_a"),
        _signal("right", "alex", work_context_key="work_b"),
        alias,
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert not any(
        {"alexandria", "alex"}.issubset({signal.canonical_key for signal in concept.signals})
        for concept in result.concepts
    )
    assert any(edge.negative_reason_code == "alias_work_context_conflict" for edge in result.edge_candidates)
    assert result.summary["alias_context_conflict_active_merge_count"] == 0
    assert result.summary["overmerge_violation_count"] == 0


def test_broad_alias_reused_across_works_does_not_overmerge():
    alias = _signal(
        "alias:edge",
        "Alex alias",
        origin_type="source_alias_candidate",
        role="unknown",
        trust="medium",
        status="needs_review",
        payload={"relation_type": "same_source_concept", "source_name_key": "alexandria", "target_name_key": "alex"},
    )
    signals = [
        _signal("a:1", "alexandria", work_context_key="work_a"),
        _signal("a:2", "alex", work_context_key="work_a"),
        _signal("b:1", "alexandria", work_context_key="work_b"),
        _signal("b:2", "alex", work_context_key="work_b"),
        alias,
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert result.summary["overmerge_violation_count"] == 0
    assert result.summary["alias_context_conflict_active_merge_count"] == 0
    assert not any(
        {"work_a", "work_b"}.issubset(
            {
                signal.work_context_key
                for signal in concept.signals
                if signal.role_hint in {"character", "person"}
            }
        )
        for concept in result.concepts
    )


def test_context_equivalence_uses_record_scope_without_single_record_overmerge():
    signals = [
        _signal("work:1", "原神", role="work", media_id=1, source_record_id=10),
        _signal("tag:1", "barbara_(genshin_impact)", trust="weak", status="needs_review", media_id=1, source_record_id=10),
        _signal("work:2", "原神", role="work", media_id=2, source_record_id=11),
        _signal("tag:2", "ganyu_(genshin_impact)", trust="weak", status="needs_review", media_id=2, source_record_id=11),
        _signal("work:kaguya", "かぐや様は告らせたい", role="work", media_id=3, source_record_id=12),
        _signal("tag:kaguya", "jean_(genshin_impact)", trust="weak", status="needs_review", media_id=3, source_record_id=12),
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert result.summary["context_alias_count"] == 2
    contexts = {concept.evidence_summary.get("work_context_key") for concept in result.concepts}
    assert "genshin_impact" in contexts
    kaguya_concepts = [
        concept
        for concept in result.concepts
        if any(signal.canonical_key == "かぐや様は告らせたい" for signal in concept.signals)
    ]
    assert kaguya_concepts
    assert all(concept.evidence_summary.get("work_context_key") != "genshin_impact" for concept in kaguya_concepts)


def test_oversized_context_block_uses_star_edges_without_all_pairs():
    signals = [
        _signal(
            f"sig:{idx}",
            f"shared_{idx}_(genshin_impact)",
            trust="weak",
            status="needs_review",
            media_id=idx,
        )
        for idx in range(65)
    ]
    signals.extend(
        _signal(
            f"nilou:{idx}",
            "nilou_(genshin_impact)",
            trust="weak",
            status="needs_review",
            media_id=1000 + idx,
        )
        for idx in range(65)
    )

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert result.summary["blocking_oversized_blocks"] >= 1
    nilou_concepts = [
        concept
        for concept in result.concepts
        if {signal.canonical_key for signal in concept.signals} == {"nilou_(genshin_impact)"}
    ]
    assert any(len(concept.signals) > 1 for concept in nilou_concepts)


def test_strict_positive_case_review_requires_same_concept():
    split_payload = {
        "concepts": [
            {"concept_key": "c1", "status": "active", "evidence_summary": {"surface_key": "kamisato_ayaka"}},
            {"concept_key": "c2", "status": "active", "evidence_summary": {"surface_key": "kamisato_ayaka"}},
        ],
        "aliases": [
            {"concept_key": "c1", "alias_key": "kamisato_ayaka", "alias_role": "normal_media_tag"},
            {"concept_key": "c2", "alias_key": canonical_key("\u795e\u91cc\u7dbe\u83ef"), "alias_role": "f7a_candidate"},
        ],
        "ai_signal_review": [],
        "overmerge_review": [],
    }
    positive_rows, _negative_rows = concept_case_review(split_payload)
    kamisato = next(row for row in positive_rows if row["case_id"] == "kamisato_ayaka_multi_origin")
    assert kamisato["validation_status"] == "fail"

    joined_payload = {
        **split_payload,
        "concepts": [{"concept_key": "c1", "status": "active", "evidence_summary": {"surface_key": "kamisato_ayaka"}}],
        "aliases": [
            {"concept_key": "c1", "alias_key": "kamisato_ayaka", "alias_role": "normal_media_tag"},
            {"concept_key": "c1", "alias_key": canonical_key("\u795e\u91cc\u7dbe\u83ef"), "alias_role": "f7a_candidate"},
        ],
    }
    positive_rows, _negative_rows = concept_case_review(joined_payload)
    kamisato = next(row for row in positive_rows if row["case_id"] == "kamisato_ayaka_multi_origin")
    assert kamisato["validation_status"] == "pass"


def test_runner_readiness_fails_required_validation_failures():
    readiness = build_readiness_check(
        summary={"resolver_summary": {"ai_only_active_violation_count": 0, "general_source_tag_pollution_count": 0, "source_title_only_active_violation_count": 0}, "persistence": {"forbidden_truth_table_write_count": 0}},
        positive_rows=[{"case_id": "kamisato_ayaka_multi_origin", "validation_status": "fail"}],
        negative_rows=[],
        consistency={"passed": True},
        redaction={"passed": True},
    )

    assert readiness["passed"] is False
    assert readiness["failures"][0]["check"] == "positive_same_concept"


def test_undermerge_violation_fails_artifact_consistency_and_readiness():
    payload = {
        "signals": [{"signal_key": "left"}, {"signal_key": "right"}],
        "concepts": [{"concept_key": "c1"}, {"concept_key": "c2"}],
        "links": [{"signal_key": "left", "concept_key": "c1"}, {"signal_key": "right", "concept_key": "c2"}],
        "aliases": [],
        "evidence": [],
        "undermerge_review": [{"edge_key": "e1", "violation": True}],
        "overmerge_review": [],
        "fragmentation_review": [],
        "summary": {
            "undermerge_violation_count": 1,
            "overmerge_violation_count": 0,
            "fragmentation_violation_count": 0,
            "ai_only_active_violation_count": 0,
            "general_source_tag_pollution_count": 0,
            "source_title_only_active_violation_count": 0,
        },
    }
    consistency = build_artifact_consistency_check(payload, {"forbidden_truth_table_write_count": 0})
    readiness = build_readiness_check(
        summary={"resolver_summary": payload["summary"], "persistence": {"forbidden_truth_table_write_count": 0}},
        positive_rows=[],
        negative_rows=[],
        consistency=consistency,
        redaction={"passed": True},
    )

    assert consistency["passed"] is False
    assert consistency["undermerge_violation_count"] == 1
    assert readiness["passed"] is False
    assert any(failure["check"] == "undermerge_violation_count" for failure in readiness["failures"])


def test_context_conflict_violation_fails_artifact_consistency_and_readiness():
    payload = {
        "signals": [{"signal_key": "s1"}, {"signal_key": "s2"}],
        "concepts": [{"concept_key": "c1"}],
        "links": [{"signal_key": "s1", "concept_key": "c1"}, {"signal_key": "s2", "concept_key": "c1"}],
        "aliases": [],
        "evidence": [],
        "undermerge_review": [],
        "overmerge_review": [],
        "fragmentation_review": [],
        "context_conflict_review": [{"edge_key": "e1", "alias_edge": True, "violation": True}],
        "summary": {
            "undermerge_violation_count": 0,
            "overmerge_violation_count": 0,
            "fragmentation_violation_count": 0,
            "context_conflict_active_merge_count": 1,
            "alias_context_conflict_active_merge_count": 1,
            "ai_only_active_violation_count": 0,
            "general_source_tag_pollution_count": 0,
            "source_title_only_active_violation_count": 0,
            "llm_budget_violation_count": 0,
        },
    }

    consistency = build_artifact_consistency_check(payload, {"forbidden_truth_table_write_count": 0})
    readiness = build_readiness_check(
        summary={"resolver_summary": {**payload["summary"], "random_holdout_severe_violation_count": 0}, "persistence": {"forbidden_truth_table_write_count": 0}},
        positive_rows=[],
        negative_rows=[],
        consistency=consistency,
        redaction={"passed": True},
    )

    assert consistency["passed"] is False
    assert consistency["context_conflict_active_merge_count"] == 1
    assert readiness["passed"] is False
    assert any(failure["check"] == "context_conflict_active_merge_count" for failure in readiness["failures"])


def test_random_holdout_review_generates_rows_and_flags_severe_pollution():
    payload = {
        "signals": [
            {
                "signal_key": "bad-general",
                "origin_type": "source_tag_observation",
                "role_hint": "unknown",
                "trust_tier": "rejected",
                "status": "rejected",
                "source_kind": "provider_tag",
                "evidence_payload": {"non_concept_reason": "general_source_tag_without_name_context"},
            }
        ],
        "concepts": [{"concept_key": "c1", "status": "needs_review", "evidence_summary": {}, "signals": ["bad-general"]}],
        "links": [{"signal_key": "bad-general", "concept_key": "c1"}],
    }

    rows = random_holdout_review(payload, sample_size=10)
    readiness = build_readiness_check(
        summary={
            "resolver_summary": {
                "undermerge_violation_count": 0,
                "overmerge_violation_count": 0,
                "fragmentation_violation_count": 0,
                "context_conflict_active_merge_count": 0,
                "alias_context_conflict_active_merge_count": 0,
                "ai_only_active_violation_count": 0,
                "general_source_tag_pollution_count": 0,
                "source_title_only_active_violation_count": 0,
                "llm_budget_violation_count": 0,
                "random_holdout_severe_violation_count": sum(1 for row in rows if row["severe_violation"]),
            },
            "persistence": {"forbidden_truth_table_write_count": 0},
        },
        positive_rows=[],
        negative_rows=[],
        consistency={"passed": True},
        redaction={"passed": True},
    )

    assert rows
    assert rows[0]["severe_violation"] is True
    assert readiness["passed"] is False
    assert any(failure["check"] == "random_holdout_severe_violation_count" for failure in readiness["failures"])


def test_guarded_merge_review_uses_surface_key_not_ambiguous_literal():
    result = resolve_source_concepts(
        [
            _signal("mona:1", "Mona"),
            _signal("mona:2", "Mona"),
            _signal("nicole:1", "Nicole"),
            _signal("nicole:2", "Nicole"),
        ],
        run_id="sc1-test",
    )

    surfaces = {row["surface_key"] for row in result.merge_candidates}
    assert "ambiguous" not in surfaces
    assert {"mona", "nicole"}.issubset(surfaces)


def test_same_scope_duplicate_short_name_groups_for_review_only():
    result = resolve_source_concepts(
        [
            _signal("mona:1", "Mona", trust="medium", status="needs_review", media_id=1),
            _signal("mona:2", "Mona", trust="medium", status="needs_review", media_id=1),
        ],
        run_id="sc1-test",
    )

    assert len(result.concepts) == 1
    assert result.concepts[0].status == "needs_review"
    assert any(edge.edge_type == "same_scope_duplicate_review" for edge in result.edge_candidates)


def test_llm_budget_cache_and_judgment_edges_are_source_layer_only():
    left = _signal("left", "kamisato_ayaka", trust="medium", status="needs_review", work_context_key="genshin_impact")
    right = _signal("right", "Kamisato Ayaka", trust="medium", status="needs_review", work_context_key="genshin_impact")
    result = resolve_source_concepts([left, right], run_id="sc1-test", llm_config=LLMAdjudicationConfig(enabled=True, max_calls=5))
    plan = result.summary["llm_usage"]["plan"]
    assert plan["status"] in {"ready", "disabled"}
    first = llm_cache_fingerprint(prompt_version="v1", model_label="primary", block_payload={"signals": ["a", "b"]})
    second = llm_cache_fingerprint(prompt_version="v1", model_label="primary", block_payload={"signals": ["a", "b"]})
    assert first == second

    edges = edges_from_llm_judgments(
        [{"left_signal_key": "left", "right_signal_key": "right", "decision": "must_link", "confidence": 0.9, "judgment_id": "j1"}],
        signal_by_key={"left": left, "right": right},
    )
    assert edges[0].edge_type == "llm_same_concept"
    assert edges[0].payload["source_layer_only"] is True


def test_llm_must_link_materializes_after_deterministic_guard():
    left = _signal("left", "Kamisato Ayaka", trust="medium", status="needs_review", work_context_key="genshin_impact")
    right = _signal("right", "\u795e\u91cc\u7dbe\u83ef", trust="medium", status="needs_review", work_context_key="genshin_impact")
    judgments = [
        {"left_signal_key": "left", "right_signal_key": "right", "decision": "must_link", "confidence": 0.93, "judgment_id": "j1"}
    ]

    result = resolve_source_concepts([left, right], run_id="sc1-test", llm_judgments=judgments)

    assert len(result.concepts) == 1
    assert any(edge.edge_type == "llm_same_concept" for edge in result.edge_candidates)
    assert result.summary["undermerge_violation_count"] == 0


def test_llm_must_link_blocked_by_short_name_guard_is_not_undermerge():
    left = _signal("left", "Mona", trust="medium", status="needs_review")
    right = _signal("right", "Nicole", trust="medium", status="needs_review")
    judgments = [
        {"left_signal_key": "left", "right_signal_key": "right", "decision": "must_link", "confidence": 0.95, "judgment_id": "j1"}
    ]

    result = resolve_source_concepts([left, right], run_id="sc1-test", llm_judgments=judgments)

    assert len(result.concepts) == 2
    blocked_edges = [edge for edge in result.edge_candidates if edge.edge_type == "llm_blocked_guard"]
    assert blocked_edges
    assert blocked_edges[0].negative_reason_code == "ambiguous_short_without_work_context"
    assert result.summary["undermerge_violation_count"] == 0


def test_llm_same_scope_cross_script_canonical_bridge_groups_for_review():
    left = _signal("left", "\u795e\u91cc\u7dbe\u83ef", trust="medium", status="needs_review", media_id=1)
    right = _signal("right", "kamisato_ayaka", trust="medium", status="needs_review", media_id=1)
    judgments = [
        {"left_signal_key": "left", "right_signal_key": "right", "decision": "must_link", "confidence": 0.9, "judgment_id": "j1"}
    ]

    result = resolve_source_concepts([left, right], run_id="sc1-test", llm_judgments=judgments)

    assert len(result.concepts) == 1
    assert result.concepts[0].status == "needs_review"
    llm_edges = [edge for edge in result.edge_candidates if edge.edge_type == "llm_same_concept"]
    assert llm_edges
    assert llm_edges[0].status == "needs_review"
    assert llm_edges[0].payload["review_reason"] == "same_scope_cross_script_canonical_bridge"
    assert result.summary["undermerge_violation_count"] == 0


def test_llm_cannot_link_does_not_fragment_stable_identity_anchor():
    left = _signal("left", "sangonomiya_kokomi", trust="medium", status="needs_review", work_context_key="genshin_impact")
    right = _signal("right", "sangonomiya_kokomi", trust="medium", status="needs_review", work_context_key="genshin_impact")
    judgments = [
        {"left_signal_key": "left", "right_signal_key": "right", "decision": "cannot_link", "confidence": 0.9, "judgment_id": "j1"}
    ]

    result = resolve_source_concepts([left, right], run_id="sc1-test", llm_judgments=judgments)

    assert len(result.concepts) == 1
    assert any(edge.negative_reason_code == "llm_cannot_link" for edge in result.edge_candidates)
    assert result.summary["undermerge_violation_count"] == 0
    assert result.summary["overmerge_violation_count"] == 0


def test_llm_budget_block_returns_before_provider_initialization():
    left = _signal("left", "Kamisato Ayaka", trust="weak", status="needs_review", media_id=1, source_record_id=1)
    right = _signal("right", "\u795e\u91cc\u7dbe\u83ef", trust="weak", status="needs_review", media_id=1, source_record_id=1)
    initial = resolve_source_concepts([left, right], run_id="sc1-test")

    judgments, summary = run_bounded_llm_adjudication(
        initial.edge_candidates,
        signals=[left, right],
        config=LLMAdjudicationConfig(enabled=True, max_calls=10, max_budget_usd=0.0),
    )

    assert judgments == []
    assert summary["used"] is False
    assert summary["reason"] == "llm_budget_or_call_cap_exceeded"
    assert summary["provider"]["provider_mode"] == "not_initialized_budget_blocked"


def test_long_identity_anchor_concept_key_is_bounded():
    long_name = "character_" + ("x" * 520)
    long_context = "work_" + ("y" * 520)
    signals = [
        _signal("long:1", long_name, work_context_key=long_context),
        _signal("long:2", long_name, work_context_key=long_context),
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert result.concepts
    assert all(len(concept.concept_key) <= 900 for concept in result.concepts)


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
    repeat = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=False)
    assert repeat["needs_import"] is False
    assert repeat["stable_checksum_matches"] is True

    (pack / "candidate-bundle.jsonl").write_text(
        json.dumps(
            {
                "group_key": "source_record:1",
                "provider": "pixiv",
                "source_metadata_record_id": None,
                "media_id": None,
                "origin_type": "pixiv_tag",
                "origin_id": "tag:1",
                "raw_value": "Different",
                "display_name": "Different",
                "normalized_value": "different",
                "canonical_key": "different",
                "candidate_role": "character",
                "candidate_status": "active_candidate",
                "extraction_verdict": "single_candidate_found",
                "extraction_action": "accepted",
                "confidence": 0.9,
                "candidate_key": "different-logical-key",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    mismatch = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=False)
    assert mismatch["candidate_bundle_count"] == mismatch["existing_db_candidate_count_for_run"] == 1
    assert mismatch["needs_import"] is True
    assert mismatch["stable_checksum_matches"] is False
