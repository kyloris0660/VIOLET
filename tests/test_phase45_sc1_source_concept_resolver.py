"""Focused tests for Phase 4.5-SC1 source concept resolver core."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
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
from app.services import source_concept_resolver_service as sc_resolver_service  # noqa: E402
from app.models import (  # noqa: E402
    Media,
    ProviderCache,
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
    SourceConceptEdgeDraft,
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
    select_llm_adjudication_edges,
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
    origin_table: str = "fixture",
    provider: str = "fixture",
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
        origin_table=origin_table,
        origin_id=key,
        provider=provider,
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


def _source_concept_signal_row(signal_key: str, *, source_run_id: str | None, status: str = "active") -> SourceConceptSignal:
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


def _scoped_source_concept_signal_row(
    signal_key: str,
    *,
    source_run_id: str | None = None,
    origin_type: str = "normal_media_tag",
    origin_table: str = "fixture",
    provider: str = "fixture",
    media_id: int | None = None,
    source_metadata_record_id: int | None = None,
    source_record_id: str | None = None,
    status: str = "active",
) -> SourceConceptSignal:
    return SourceConceptSignal(
        signal_key=signal_key,
        origin_type=origin_type,
        origin_table=origin_table,
        origin_id=signal_key,
        provider=provider,
        media_id=media_id,
        source_metadata_record_id=source_metadata_record_id,
        source_record_id=source_record_id,
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


def _seed_persisted_concept_for_signal(session, signal: SourceConceptSignal, concept_key: str):
    concept = SourceConcept(
        concept_key=concept_key,
        primary_display_name=signal.display_value,
        concept_type_hint=signal.role_hint,
        status="active",
        created_by_run_id="old-run",
    )
    session.add_all([signal, concept])
    session.flush()
    alias = SourceConceptAlias(
        concept_id=concept.id,
        alias_value=signal.display_value,
        alias_key=signal.canonical_key,
        display_name=signal.display_value,
        alias_role=signal.origin_type,
        status="active",
        source_signal_id=signal.id,
        created_by_run_id="old-run",
    )
    evidence = SourceConceptEvidence(
        concept_id=concept.id,
        signal_id=signal.id,
        evidence_type=signal.origin_type,
        evidence_strength=signal.trust_tier,
        status="active",
        run_id="old-run",
    )
    link = SourceConceptSignalLink(
        signal_id=signal.id,
        concept_id=concept.id,
        link_status="active",
        resolver_version="old",
        run_id="old-run",
    )
    search = SourceConceptSearchIndex(
        concept_id=concept.id,
        search_key=signal.canonical_key,
        display_name=signal.display_value,
        alias_role=signal.origin_type,
        weight=0.5,
        status="active",
        run_id="old-run",
    )
    session.add_all([alias, evidence, link, search])
    session.flush()
    return concept, alias, evidence, link, search


def _media(media_id: int = 1) -> Media:
    return Media(
        id=media_id,
        filename=f"m{media_id}.jpg",
        path=f"/tmp/m{media_id}.jpg",
        hash=f"{media_id:064x}",
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


def _stable_signal_projection_with_local_ids(offset: int) -> tuple[tuple[str, ...], ...]:
    engine, session = _db()
    metadata = SourceMetadataRecord(
        id=offset + 1,
        provider="pixiv",
        provider_record_key="pixiv:stable-work:page:0",
        artist_name="Stable Artist",
        metadata_kind="provider_metadata",
        data_type_label="fixture",
        status="observed",
    )
    session.add(metadata)
    session.flush()
    session.add_all(
        [
            SourceNameObservation(
                id=offset + 2,
                source_metadata_record_id=metadata.id,
                provider="pixiv",
                observation_key="name:artist:stable",
                raw_name="Stable Artist",
                normalized_name="Stable Artist",
                canonical_name_key="stable_artist",
                name_role="artist",
                source_field="artist_name",
                requires_review=False,
                status="observed",
            ),
            SourceTagObservation(
                id=offset + 3,
                source_metadata_record_id=metadata.id,
                provider="pixiv",
                observation_key="tag:character:stable",
                raw_tag="Stable Character",
                normalized_tag="Stable Character",
                canonical_tag_key="stable_character",
                source_tag_kind="provider_tag",
                source_category_raw="character",
                status="observed",
            ),
            SourceSearchableNameAssertion(
                id=offset + 4,
                provider="pixiv",
                source_metadata_record_id=metadata.id,
                assertion_key="assertion:stable-character",
                raw_input="Stable Character",
                normalized_input="stable character",
                canonical_name_key="stable_character",
                asserted_name="Stable Character",
                asserted_role="character",
                status="searchable_active",
                confidence="high",
                structured_output_schema_version="test",
                requires_review=False,
            ),
            SourceNameAliasCandidate(
                id=offset + 5,
                source_name_key="stable_character",
                target_name_key="stable_character_zh",
                source_display_name="Stable Character",
                target_display_name="稳定角色",
                relation_type="same_source_concept",
                evidence_source="fixture",
                status="candidate",
                requires_review=True,
            ),
        ]
    )
    session.commit()
    signals = build_source_concept_signals(session, run_id="stable-signal-test")
    projection = tuple(
        sorted(
            (
                signal.signal_key,
                signal.origin_type,
                signal.source_kind or "",
                signal.source_record_id or "",
                signal.canonical_key or "",
                signal.role_hint,
            )
            for signal in signals
        )
    )
    session.close()
    engine.dispose()
    return projection


def test_signal_identity_is_stable_across_development_row_ids():
    assert _stable_signal_projection_with_local_ids(0) == (
        _stable_signal_projection_with_local_ids(10_000)
    )


def test_signal_identity_is_schema_path_aware():
    identity = {
        "provider_record_key": "shared",
        "field_name": "artist_name",
    }
    metadata_key = sc_resolver_service._stable_signal_suffix(
        "source_metadata_record.structured_field", identity
    )
    cache_key = sc_resolver_service._stable_signal_suffix(
        "provider_cache.structured_field", identity
    )
    assert metadata_key != cache_key


def test_nested_provider_metadata_extraction_uses_name_title_allowlist():
    _engine, session = _db()
    media = _media(21)
    metadata = SourceMetadataRecord(
        id=21,
        provider="pixiv",
        provider_record_key="pixiv:nested",
        media_id=media.id,
        metadata_kind="provider_metadata",
        data_type_label="fixture",
        status="observed",
        raw_metadata_json={
            "characters": [
                {
                    "id": "123456",
                    "url": "https://example.invalid/character",
                    "image_url": "https://example.invalid/character.jpg",
                    "label": "metadata label",
                    "name": "Kamisato Ayaka",
                }
            ],
            "work": {"title": "Genshin Impact", "url": "https://example.invalid/work"},
            "artist": {"name": "Artist One", "profile_url": "https://example.invalid/artist"},
        },
    )
    cache = ProviderCache(
        provider="saucenao",
        query_hash="nested-cache",
        query_type="reverse_search",
        response_status="ok",
        response_json_redacted={
            "characters": [{"name": "Cache Character", "id": "999", "image_url": "https://example.invalid/cache.png"}],
            "artist": {"artist_name": "Cache Artist", "profile_url": "https://example.invalid/profile"},
            "work": {"work_title": "Cache Work", "hash": "deadbeefdeadbeefdeadbeefdeadbeef"},
        },
    )
    session.add_all([media, metadata, cache])
    session.commit()

    signals = build_source_concept_signals(session, run_id="sc1-test")
    values = {signal.raw_value for signal in signals if signal.origin_type == "provider_structured_field"}

    assert {"Kamisato Ayaka", "Genshin Impact", "Artist One", "Cache Character", "Cache Artist", "Cache Work"}.issubset(values)
    assert "123456" not in values
    assert "999" not in values
    assert "metadata label" not in values
    assert not any(str(value).startswith("https://") for value in values)
    assert not any(str(value).endswith((".jpg", ".png")) for value in values)


def test_path_like_provider_values_are_rejected_before_signals_and_llm():
    _engine, session = _db()
    media = _media(23)
    metadata = SourceMetadataRecord(
        id=23,
        provider="pixiv",
        provider_record_key="pixiv:pathlike",
        media_id=media.id,
        title="file:///Users/name/Pictures/private/foo",
        artist_name=r"C:\Users\name\Pictures\artist",
        metadata_kind="provider_metadata",
        data_type_label="fixture",
        status="observed",
        raw_metadata_json={
            "characters": [
                {"name": "/home/user/Pictures/private/foo.mp4"},
                {"name": "/mnt/library/item"},
                {"name": "/Users/name/Pictures/foo"},
                {"name": r"\\server\share\foo"},
                {"name": "Fate/Grand Order"},
            ],
            "work": {"title": "Normal Work/Side Story"},
        },
    )
    session.add_all([media, metadata])
    session.commit()

    signals = build_source_concept_signals(session, run_id="sc1-test")
    values = {signal.raw_value for signal in signals}

    assert "/home/user/Pictures/private/foo.mp4" not in values
    assert "/mnt/library/item" not in values
    assert "/Users/name/Pictures/foo" not in values
    assert r"C:\Users\name\Pictures\artist" not in values
    assert r"\\server\share\foo" not in values
    assert "file:///Users/name/Pictures/private/foo" not in values
    assert "Fate/Grand Order" in values
    assert "Normal Work/Side Story" in values
    assert not any(value.startswith(("/", "file://")) or "\\" in value for value in values)

    result = resolve_source_concepts(signals, run_id="sc1-test")
    llm_edges = result.edge_candidates
    assert all("/home/user" not in edge.payload.get("left_display", "") for edge in llm_edges)
    assert all("/home/user" not in edge.payload.get("right_display", "") for edge in llm_edges)


def test_general_parenthetical_media_tag_does_not_promote_to_active_character():
    _engine, session = _db()
    media = _media(22)
    general_tag = Tag(name="blue_hair_(genshin_impact)", category=TagCategoryEnum.general)
    character_tag = Tag(name="barbara_(genshin_impact)", category=TagCategoryEnum.character)
    metadata = SourceMetadataRecord(
        id=22,
        provider="pixiv",
        provider_record_key="pixiv:parenthetical",
        media_id=media.id,
        metadata_kind="provider_metadata",
        data_type_label="fixture",
        status="observed",
        raw_metadata_json={"character": {"name": "Barbara (Genshin Impact)"}},
    )
    session.add_all([media, general_tag, character_tag, metadata])
    session.flush()
    for tag in (general_tag, character_tag):
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
    session.commit()

    signals = build_source_concept_signals(session, run_id="sc1-test")
    general_signal = next((signal for signal in signals if signal.raw_value == "blue_hair_(genshin_impact)"), None)
    character_signal = next(signal for signal in signals if signal.raw_value == "barbara_(genshin_impact)")
    provider_signal = next(signal for signal in signals if signal.raw_value == "Barbara (Genshin Impact)")

    assert general_signal is None
    assert character_signal.role_hint == "character"
    assert character_signal.trust_tier == "strong"
    assert character_signal.status == "active"
    assert character_signal.work_context_key == "genshin_impact"
    assert provider_signal.role_hint == "character"
    assert provider_signal.work_context_key == "genshin_impact"

    result = resolve_source_concepts(signals, run_id="sc1-test")
    assert all(alias.alias_key != "blue_hair" for alias in result.aliases)
    assert all(item.search_key != "blue_hair" for item in result.search_index)


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
        if {"f7a_candidate", "source_assertion", "normal_media_tag"}.issubset(
            concept.evidence_summary["origin_counts"].keys()
        )
    )
    assert concept.status == "active"
    assert concept.evidence_summary["work_context_key"] == "genshin_impact"
    assert all(
        "source_alias_candidate" not in candidate.evidence_summary["origin_counts"]
        for candidate in result.concepts
        if candidate.status == "active"
    )
    assert any(edge.edge_type == "unknown_role_review" and not edge.union_allowed for edge in result.edge_candidates)
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
    assert persistence["stale_supersede_scope"]["mode"] == "scoped_source_run"
    assert persistence["stale_supersede_scope"]["source_run_ids"] == ["scope-a"]
    assert persistence["stale_supersede_scope_violation_count"] == 0


def test_empty_source_run_scope_does_not_globally_supersede_previous_rows():
    _engine, session = _db()
    old_signal = _source_concept_signal_row("old-scope-signal", source_run_id="old-scope")
    old_concept = SourceConcept(
        concept_key="character:old_scope",
        primary_display_name="old",
        concept_type_hint="character",
        status="active",
        created_by_run_id="old-run",
    )
    session.add_all([old_signal, old_concept])
    session.flush()
    old_alias = SourceConceptAlias(
        concept_id=old_concept.id,
        alias_value="old",
        alias_key="old",
        display_name="old",
        alias_role="f7a_candidate",
        status="active",
        source_signal_id=old_signal.id,
        created_by_run_id="old-run",
    )
    old_evidence = SourceConceptEvidence(
        concept_id=old_concept.id,
        signal_id=old_signal.id,
        evidence_type="f7a_candidate",
        evidence_strength="medium",
        status="active",
        run_id="old-run",
    )
    old_link = SourceConceptSignalLink(
        signal_id=old_signal.id,
        concept_id=old_concept.id,
        link_status="active",
        resolver_version="old",
        run_id="old-run",
    )
    old_search = SourceConceptSearchIndex(
        concept_id=old_concept.id,
        search_key="old",
        display_name="old",
        alias_role="f7a_candidate",
        weight=0.5,
        status="active",
        run_id="old-run",
    )
    session.add_all([old_alias, old_evidence, old_link, old_search])
    session.commit()

    result = resolve_source_concepts(
        [_signal("current-no-scope", "current_no_scope", source_run_id=None)],
        run_id="new-run",
    )
    persistence = persist_source_concept_resolution(session, result, apply=True)

    for row in (old_signal, old_concept, old_alias, old_evidence, old_link, old_search):
        session.refresh(row)

    assert old_signal.status == "active"
    assert old_concept.status == "active"
    assert old_alias.status == "active"
    assert old_evidence.status == "active"
    assert old_link.link_status == "active"
    assert old_search.status == "active"
    assert persistence["stale_supersede_scope"]["mode"] == "skipped_empty_scope"
    assert persistence["stale_supersede_scope"]["source_run_ids"] == []
    assert all(count == 0 for count in persistence["stale_transition_counts"].values())


def test_known_scope_cleanup_runs_when_last_signal_disappears():
    _engine, session = _db()
    scope_a_signal = _source_concept_signal_row("scope-a-old-signal", source_run_id="scope-a")
    scope_b_signal = _source_concept_signal_row("scope-b-old-signal", source_run_id="scope-b")
    scope_a_rows = _seed_persisted_concept_for_signal(session, scope_a_signal, "character:scope_a_old")
    scope_b_rows = _seed_persisted_concept_for_signal(session, scope_b_signal, "character:scope_b_old")
    session.commit()

    result = resolve_source_concepts([], run_id="new-run")
    persistence = persist_source_concept_resolution(
        session,
        result,
        apply=True,
        input_scope={"source_run_ids": ["scope-a"]},
    )

    for row in (scope_a_signal, *scope_a_rows, scope_b_signal, *scope_b_rows):
        session.refresh(row)

    assert scope_a_signal.status == "superseded"
    assert scope_a_rows[0].status == "superseded"
    assert scope_a_rows[1].status == "superseded"
    assert scope_a_rows[2].status == "superseded"
    assert scope_a_rows[3].link_status == "superseded"
    assert scope_a_rows[4].status == "superseded"
    assert scope_b_signal.status == "active"
    assert scope_b_rows[0].status == "active"
    assert scope_b_rows[1].status == "active"
    assert scope_b_rows[2].status == "active"
    assert scope_b_rows[3].link_status == "active"
    assert scope_b_rows[4].status == "active"
    assert persistence["stale_supersede_scope"]["mode"] == "scoped_source_run"
    assert persistence["stale_supersede_scope"]["source_run_ids"] == ["scope-a"]
    assert persistence["stale_transition_counts"]["signals"] == 1
    assert persistence["stale_transition_counts"]["concepts"] == 1


def test_scoped_signal_cleanup_does_not_hide_shared_out_of_scope_concept():
    _engine, session = _db()
    signal_a = _source_concept_signal_row("shared-scope-a", source_run_id="scope-a")
    signal_b = _source_concept_signal_row("shared-scope-b", source_run_id="scope-b")
    concept = SourceConcept(
        concept_key="character:shared_scope",
        primary_display_name="shared",
        concept_type_hint="character",
        status="active",
        created_by_run_id="old-run",
    )
    session.add_all([signal_a, signal_b, concept])
    session.flush()
    alias_a = SourceConceptAlias(
        concept_id=concept.id,
        alias_value="alias-a",
        alias_key="alias_a",
        display_name="alias-a",
        alias_role="f7a_candidate",
        status="active",
        source_signal_id=signal_a.id,
        created_by_run_id="old-run",
    )
    alias_b = SourceConceptAlias(
        concept_id=concept.id,
        alias_value="alias-b",
        alias_key="alias_b",
        display_name="alias-b",
        alias_role="f7a_candidate",
        status="active",
        source_signal_id=signal_b.id,
        created_by_run_id="old-run",
    )
    evidence_a = SourceConceptEvidence(
        concept_id=concept.id,
        signal_id=signal_a.id,
        evidence_type="f7a_candidate",
        evidence_strength="medium",
        status="active",
        run_id="old-run",
    )
    evidence_b = SourceConceptEvidence(
        concept_id=concept.id,
        signal_id=signal_b.id,
        evidence_type="f7a_candidate",
        evidence_strength="medium",
        status="active",
        run_id="old-run",
    )
    link_a = SourceConceptSignalLink(
        signal_id=signal_a.id,
        concept_id=concept.id,
        link_status="active",
        resolver_version="old",
        run_id="old-run",
    )
    link_b = SourceConceptSignalLink(
        signal_id=signal_b.id,
        concept_id=concept.id,
        link_status="active",
        resolver_version="old",
        run_id="old-run",
    )
    search_a = SourceConceptSearchIndex(
        concept_id=concept.id,
        search_key="alias_a",
        display_name="alias-a",
        alias_role="f7a_candidate",
        weight=0.5,
        status="active",
        run_id="old-run",
    )
    search_b = SourceConceptSearchIndex(
        concept_id=concept.id,
        search_key="alias_b",
        display_name="alias-b",
        alias_role="f7a_candidate",
        weight=0.5,
        status="active",
        run_id="old-run",
    )
    session.add_all([alias_a, alias_b, evidence_a, evidence_b, link_a, link_b, search_a, search_b])
    session.commit()

    result = resolve_source_concepts([], run_id="remove-scope-a")
    persistence = persist_source_concept_resolution(
        session,
        result,
        apply=True,
        input_scope={"source_run_ids": ["scope-a"]},
    )

    for row in (signal_a, signal_b, concept, alias_a, alias_b, evidence_a, evidence_b, link_a, link_b, search_a, search_b):
        session.refresh(row)

    assert signal_a.status == "superseded"
    assert alias_a.status == "superseded"
    assert evidence_a.status == "superseded"
    assert link_a.link_status == "superseded"
    assert search_a.status == "superseded"
    assert concept.status == "active"
    assert signal_b.status == "active"
    assert alias_b.status == "active"
    assert evidence_b.status == "active"
    assert link_b.link_status == "active"
    assert search_b.status == "active"
    assert persistence["stale_transition_counts"]["concepts"] == 0
    assert persistence["stale_transition_counts"]["search_index"] == 1

    result = resolve_source_concepts([], run_id="remove-both-scopes")
    persistence = persist_source_concept_resolution(
        session,
        result,
        apply=True,
        input_scope={"source_run_ids": ["scope-a", "scope-b"]},
    )

    for row in (signal_b, concept, alias_b, evidence_b, link_b, search_b):
        session.refresh(row)

    assert signal_b.status == "superseded"
    assert alias_b.status == "superseded"
    assert evidence_b.status == "superseded"
    assert link_b.link_status == "superseded"
    assert search_b.status == "superseded"
    assert concept.status == "superseded"
    assert persistence["stale_transition_counts"]["concepts"] == 1


def test_unscoped_media_tag_scope_supersedes_deleted_signal_only():
    _engine, session = _db()
    deleted_signal = _scoped_source_concept_signal_row(
        "deleted-media-tag",
        origin_type="normal_media_tag",
        origin_table="blombooru_media_tags",
        provider="manual",
        media_id=1,
    )
    unrelated_signal = _scoped_source_concept_signal_row(
        "unrelated-media-tag",
        origin_type="normal_media_tag",
        origin_table="blombooru_media_tags",
        provider="manual",
        media_id=2,
    )
    deleted_rows = _seed_persisted_concept_for_signal(session, deleted_signal, "character:deleted_media_tag")
    unrelated_rows = _seed_persisted_concept_for_signal(session, unrelated_signal, "character:unrelated_media_tag")
    session.commit()

    result = resolve_source_concepts(
        [
            _signal(
                "current-media-tag",
                "current_media_tag",
                origin_type="normal_media_tag",
                origin_table="blombooru_media_tags",
                provider="manual",
                media_id=1,
                source_run_id=None,
            )
        ],
        run_id="new-run",
    )
    persistence = persist_source_concept_resolution(session, result, apply=True)

    for row in (deleted_signal, *deleted_rows, unrelated_signal, *unrelated_rows):
        session.refresh(row)

    assert deleted_signal.status == "superseded"
    assert deleted_rows[0].status == "superseded"
    assert deleted_rows[1].status == "superseded"
    assert deleted_rows[2].status == "superseded"
    assert deleted_rows[3].link_status == "superseded"
    assert deleted_rows[4].status == "superseded"
    assert unrelated_signal.status == "active"
    assert all(getattr(row, "status", getattr(row, "link_status", None)) == "active" for row in unrelated_rows[:3])
    assert unrelated_rows[3].link_status == "active"
    assert unrelated_rows[4].status == "active"
    assert persistence["stale_supersede_scope"]["mode"] == "origin_manifest_scope"
    assert persistence["stale_supersede_scope"]["origin_scope_count"] == 1
    assert persistence["stale_supersede_scope"]["origin_scoped_signal_count"] >= 2


def test_unscoped_source_assertion_and_provider_scope_supersede_deleted_signals_only():
    _engine, session = _db()
    deleted_assertion = _scoped_source_concept_signal_row(
        "deleted-assertion",
        origin_type="source_assertion",
        origin_table="blombooru_source_searchable_name_assertions",
        provider="pixiv",
        source_metadata_record_id=10,
        source_record_id="10",
    )
    deleted_provider = _scoped_source_concept_signal_row(
        "deleted-provider-field",
        origin_type="provider_structured_field",
        origin_table="blombooru_source_metadata_records",
        provider="pixiv",
        source_metadata_record_id=20,
        source_record_id="20",
    )
    unrelated_assertion = _scoped_source_concept_signal_row(
        "unrelated-assertion",
        origin_type="source_assertion",
        origin_table="blombooru_source_searchable_name_assertions",
        provider="pixiv",
        source_metadata_record_id=99,
        source_record_id="99",
    )
    deleted_assertion_rows = _seed_persisted_concept_for_signal(session, deleted_assertion, "character:deleted_assertion")
    deleted_provider_rows = _seed_persisted_concept_for_signal(session, deleted_provider, "character:deleted_provider")
    unrelated_rows = _seed_persisted_concept_for_signal(session, unrelated_assertion, "character:unrelated_assertion")
    session.commit()

    result = resolve_source_concepts(
        [
            _signal(
                "current-assertion",
                "current_assertion",
                origin_type="source_assertion",
                origin_table="blombooru_source_searchable_name_assertions",
                provider="pixiv",
                source_record_id=10,
                source_run_id=None,
            ),
            _signal(
                "current-provider",
                "current_provider",
                origin_type="provider_structured_field",
                origin_table="blombooru_source_metadata_records",
                provider="pixiv",
                source_record_id=20,
                source_run_id=None,
            ),
        ],
        run_id="new-run",
    )
    persistence = persist_source_concept_resolution(session, result, apply=True)

    for row in (deleted_assertion, *deleted_assertion_rows, deleted_provider, *deleted_provider_rows, unrelated_assertion, *unrelated_rows):
        session.refresh(row)

    assert deleted_assertion.status == "superseded"
    assert deleted_assertion_rows[0].status == "superseded"
    assert deleted_provider.status == "superseded"
    assert deleted_provider_rows[0].status == "superseded"
    assert unrelated_assertion.status == "active"
    assert unrelated_rows[0].status == "active"
    assert persistence["stale_supersede_scope"]["mode"] == "origin_manifest_scope"
    assert persistence["stale_supersede_scope"]["origin_scope_count"] == 2


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
    assert result.links[0].negative_reason_code == "data_aware_ambiguity_without_work_context"


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
    assert all(link.negative_reason_code == "data_aware_ambiguity_without_work_context" for link in result.links)


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


def test_exact_same_canonical_partial_context_remains_review_overlay_not_identity_union():
    signals = [
        _signal("left", "alexandria", work_context_key="work_a"),
        _signal("right", "alexandria"),
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert len(result.concepts) == 2
    exact_edges = [edge for edge in result.edge_candidates if edge.edge_type == "exact_canonical_key"]
    assert exact_edges
    assert exact_edges[0].status == "needs_review"
    assert exact_edges[0].union_allowed is True
    assert result.summary["review_only_edge_used_in_union_count"] == 0


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


def test_ai_only_same_identity_anchor_stays_review_overlay_not_identity_union():
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

    assert len(result.concepts) == 2
    assert all(concept.status == "needs_review" for concept in result.concepts)
    assert all(concept.confidence_score <= 0.59 for concept in result.concepts)
    assert result.summary["ai_only_active_violation_count"] == 0
    assert result.summary["review_only_edge_used_in_union_count"] == 0


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


def test_context_equivalence_rejects_conflicting_explicit_context_support():
    signals = [
        _signal("work:1", "原神", role="work", media_id=1, source_record_id=10),
        _signal("tag:1", "barbara_(genshin_impact)", trust="weak", status="needs_review", media_id=1, source_record_id=10),
        _signal("work:2", "原神", role="work", media_id=2, source_record_id=11),
        _signal("tag:2", "ganyu_(genshin_impact)", trust="weak", status="needs_review", media_id=2, source_record_id=11),
        _signal("work:kaguya", "かぐや様は告らせたい", role="work", media_id=3, source_record_id=12),
        _signal("tag:kaguya", "jean_(genshin_impact)", trust="weak", status="needs_review", media_id=3, source_record_id=12),
    ]

    result = resolve_source_concepts(signals, run_id="sc1-test")

    assert result.summary["context_alias_count"] == 0
    assert result.summary["context_equivalence"]["rejected_pair_count"] >= 1
    contexts = {concept.evidence_summary.get("work_context_key") for concept in result.concepts}
    assert "genshin_impact" in contexts
    kaguya_concepts = [
        concept
        for concept in result.concepts
        if any(signal.canonical_key == "かぐや様は告らせたい" for signal in concept.signals)
    ]
    assert kaguya_concepts
    assert all(concept.evidence_summary.get("work_context_key") != "genshin_impact" for concept in kaguya_concepts)


def test_oversized_context_block_partitions_without_review_edge_union():
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
    assert all(len(concept.signals) == 1 for concept in nilou_concepts)
    graph = result.summary["edge_graph"]
    assert graph["oversized_partition_count"] >= 1
    assert graph["oversized_hub_edges_prevented"] >= 1


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


def test_same_scope_duplicate_short_name_stays_review_overlay_only():
    result = resolve_source_concepts(
        [
            _signal("mona:1", "Mona", trust="medium", status="needs_review", media_id=1),
            _signal("mona:2", "Mona", trust="medium", status="needs_review", media_id=1),
        ],
        run_id="sc1-test",
    )

    assert len(result.concepts) == 2
    assert all(concept.status == "needs_review" for concept in result.concepts)
    assert any(edge.edge_type == "same_scope_duplicate_review" for edge in result.edge_candidates)
    assert result.summary["review_only_edge_used_in_union_count"] == 0


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


def test_llm_same_scope_cross_script_review_relation_does_not_union():
    left = _signal("left", "\u795e\u91cc\u7dbe\u83ef", trust="medium", status="needs_review", media_id=1)
    right = _signal("right", "kamisato_ayaka", trust="medium", status="needs_review", media_id=1)
    judgments = [
        {"left_signal_key": "left", "right_signal_key": "right", "decision": "must_link", "confidence": 0.9, "judgment_id": "j1"}
    ]

    result = resolve_source_concepts([left, right], run_id="sc1-test", llm_judgments=judgments)

    assert len(result.concepts) == 2
    assert all(concept.status == "needs_review" for concept in result.concepts)
    llm_edges = [edge for edge in result.edge_candidates if edge.edge_type == "llm_same_concept"]
    assert llm_edges
    assert llm_edges[0].status == "needs_review"
    assert llm_edges[0].payload["review_reason"] == "same_scope_cross_script_canonical_bridge"
    assert result.summary["review_only_edge_used_in_union_count"] == 0


def test_llm_cannot_link_blocks_stable_identity_anchor_union():
    left = _signal("left", "sangonomiya_kokomi", trust="medium", status="needs_review", work_context_key="genshin_impact")
    right = _signal("right", "sangonomiya_kokomi", trust="medium", status="needs_review", work_context_key="genshin_impact")
    judgments = [
        {"left_signal_key": "left", "right_signal_key": "right", "decision": "cannot_link", "confidence": 0.9, "judgment_id": "j1"}
    ]

    result = resolve_source_concepts([left, right], run_id="sc1-test", llm_judgments=judgments)

    assert len(result.concepts) == 2
    assert any(edge.negative_reason_code == "llm_cannot_link" for edge in result.edge_candidates)
    assert result.summary["undermerge_violation_count"] == 0
    assert result.summary["overmerge_violation_count"] == 0
    assert result.summary["direct_llm_cannot_pair_in_materialized_component_count"] == 0
    assert result.summary["transitive_cannot_violation_count"] == 0


def test_artist_same_surface_without_stable_identity_remains_independent():
    left = _signal(
        "left",
        "same artist",
        role="artist",
        media_id=1,
        payload={},
    )
    right = _signal(
        "right",
        "same artist",
        role="artist",
        media_id=1,
        payload={},
    )
    result = resolve_source_concepts([left, right], run_id="sc1-test")
    assert len(result.concepts) == 2
    guards = [
        edge
        for edge in result.edge_candidates
        if edge.edge_type == "creator_identity_guard"
    ]
    assert guards
    assert guards[0].union_allowed is False


def test_artist_same_provider_stable_id_may_union_different_names():
    payload = {"stable_creator_id": "42"}
    left = _signal(
        "left",
        "display artist",
        role="artist",
        provider="pixiv",
        payload=payload,
    )
    right = _signal(
        "right",
        "account_artist",
        role="artist",
        provider="pixiv",
        payload=payload,
    )
    result = resolve_source_concepts([left, right], run_id="sc1-test")
    assert len(result.concepts) == 1
    assert any(
        edge.edge_type == "stable_identity_anchor"
        for edge in result.edge_candidates
    )


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
    assert summary["reason"] == "llm_budget_exceeded"
    assert summary["provider"]["provider_mode"] == "not_initialized_budget_blocked"


def test_budget_driven_all_eligible_llm_policy_selects_every_eligible_edge():
    signals = [
        _signal("s1", "Kamisato Ayaka", trust="weak", status="needs_review", media_id=1, source_record_id=1),
        _signal("s2", "\u795e\u91cc\u7dbe\u83ef", trust="weak", status="needs_review", media_id=1, source_record_id=1),
        _signal("s3", "Ayaka", trust="weak", status="needs_review", media_id=1, source_record_id=1),
        _signal("s4", "Shirasagi Himegimi", trust="weak", status="needs_review", media_id=1, source_record_id=1),
    ]
    edges = [
        SourceConceptEdgeDraft(
            edge_key=f"edge:{index}",
            left_signal_key=left,
            right_signal_key=right,
            edge_type="cooccurrence_context",
            weight=1.0,
            evidence_source="fixture",
            status="weak",
            resolution_reason_code="fixture_eligible_pair",
            negative_reason_code=None,
            union_allowed=False,
            payload={},
        )
        for index, (left, right) in enumerate((("s1", "s2"), ("s1", "s3"), ("s2", "s4")), start=1)
    ]
    config = LLMAdjudicationConfig(
        enabled=True,
        max_calls=100,
        max_budget_usd=15.0,
        selection_policy="budget_driven_all_eligible",
    )

    plan = plan_llm_adjudication(edges, signals=signals, config=config)
    selected = select_llm_adjudication_edges(edges, signals=signals, config=config)

    assert plan.status == "ready"
    assert plan.selection_policy == "budget_driven_all_eligible"
    assert plan.projected_calls == 3
    assert plan.skipped_block_count == 0
    assert len(selected) == 3
    assert [edge.edge_key for edge in selected] == ["edge:1", "edge:2", "edge:3"]


class _FakeLLMProvider:
    def __init__(self, *, fail_on_call: int | None = None, decision: str = "must_link") -> None:
        self.model = "gpt-test"
        self.calls = 0
        self.fail_on_call = fail_on_call
        self.decision = decision

    def get_provider_name(self) -> str:
        return "fake-primary-openai"

    async def complete_json(self, _messages, *, temperature: float = 0.0, max_tokens: int = 256):
        self.calls += 1
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            raise RuntimeError("fixture_provider_failure")
        return {"decision": self.decision, "confidence": 0.8, "reason_code": "fixture_reason"}


def _eligible_llm_edges(count: int) -> tuple[list[SourceConceptSignalDraft], list[SourceConceptEdgeDraft]]:
    signals = [
        _signal(f"s{index}", f"Name {index}", trust="weak", status="needs_review", media_id=1, source_record_id=1)
        for index in range(count + 1)
    ]
    edges = [
        SourceConceptEdgeDraft(
            edge_key=f"edge:{index}",
            left_signal_key="s0",
            right_signal_key=f"s{index}",
            edge_type="cooccurrence_context",
            weight=1.0,
            evidence_source="fixture",
            status="weak",
            resolution_reason_code="fixture_eligible_pair",
            negative_reason_code=None,
            union_allowed=False,
            payload={"fixture_index": index},
        )
        for index in range(1, count + 1)
    ]
    return signals, edges


def _cache_config(tmp_path: Path, **overrides) -> LLMAdjudicationConfig:
    values = {
        "enabled": True,
        "max_calls": 100,
        "max_budget_usd": 15.0,
        "selection_policy": "budget_driven_all_eligible",
        "durable_cache_dir": str(tmp_path / "durable-cache"),
        "run_id": "cache-test-run",
    }
    values.update(overrides)
    return LLMAdjudicationConfig(**values)


def test_successful_llm_judgment_is_cached_and_reused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    signals, edges = _eligible_llm_edges(1)
    first_provider = _FakeLLMProvider()
    monkeypatch.setattr(
        sc_resolver_service,
        "primary_openai_provider_from_settings",
        lambda: (
            first_provider,
            {"provider_mode": "primary_openai", "llm_provider_label": "primary_openai", "uses_fallback_provider": False},
        ),
    )

    first_judgments, first_summary = run_bounded_llm_adjudication(edges, signals=signals, config=_cache_config(tmp_path))

    assert first_provider.calls == 1
    assert first_summary["new_provider_call_count"] == 1
    assert first_summary["durable_cache_write_success_count"] == 1
    assert first_judgments[0]["cache_status"] == "miss"

    second_provider = _FakeLLMProvider(decision="cannot_link")
    monkeypatch.setattr(
        sc_resolver_service,
        "primary_openai_provider_from_settings",
        lambda: (
            second_provider,
            {"provider_mode": "primary_openai", "llm_provider_label": "primary_openai", "uses_fallback_provider": False},
        ),
    )
    second_judgments, second_summary = run_bounded_llm_adjudication(edges, signals=signals, config=_cache_config(tmp_path))

    assert second_provider.calls == 0
    assert second_summary["cache_hits"] == 1
    assert second_summary["new_provider_call_count"] == 0
    assert second_judgments[0]["cache_status"] == "hit"
    assert second_judgments[0]["cache_reuse_level"] == "exact_compatible"
    assert second_judgments[0]["decision"] == "must_link"


def test_all_pairs_cached_does_not_require_provider_availability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    signals, edges = _eligible_llm_edges(1)
    first_provider = _FakeLLMProvider()
    monkeypatch.setattr(
        sc_resolver_service,
        "primary_openai_provider_from_settings",
        lambda: (
            first_provider,
            {"provider_mode": "primary_openai", "llm_provider_label": "primary_openai", "uses_fallback_provider": False},
        ),
    )
    config = _cache_config(tmp_path)
    run_bounded_llm_adjudication(edges, signals=signals, config=config)
    assert first_provider.calls == 1

    def unavailable_provider():
        raise AssertionError("provider should not be initialized when all selected pairs are exact-cache hits")

    monkeypatch.setattr(sc_resolver_service, "primary_openai_provider_from_settings", unavailable_provider)
    judgments, summary = run_bounded_llm_adjudication(edges, signals=signals, config=config)

    assert len(judgments) == 1
    assert judgments[0]["cache_status"] == "hit"
    assert summary["provider"]["provider_name"] == "cache_only"
    assert summary["provider"]["model_name"] == config.model_label
    assert summary["cache_hits"] == 1
    assert summary["new_provider_call_count"] == 0
    assert summary["remaining_missing_pair_count"] == 0


def test_missing_pairs_require_provider_availability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    signals, edges = _eligible_llm_edges(1)
    monkeypatch.setattr(
        sc_resolver_service,
        "primary_openai_provider_from_settings",
        lambda: (
            None,
            {
                "provider_mode": "primary_openai",
                "llm_provider_label": "primary_openai",
                "uses_fallback_provider": False,
                "unavailable_reason": "fixture_provider_unavailable",
            },
        ),
    )

    judgments, summary = run_bounded_llm_adjudication(edges, signals=signals, config=_cache_config(tmp_path))

    assert judgments == []
    assert summary["used"] is False
    assert summary["reason"] == "provider_unavailable"


def test_provider_failure_after_partial_success_preserves_successful_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    signals, edges = _eligible_llm_edges(2)
    provider = _FakeLLMProvider(fail_on_call=2)
    monkeypatch.setattr(
        sc_resolver_service,
        "primary_openai_provider_from_settings",
        lambda: (
            provider,
            {"provider_mode": "primary_openai", "llm_provider_label": "primary_openai", "uses_fallback_provider": False},
        ),
    )

    judgments, summary = run_bounded_llm_adjudication(edges, signals=signals, config=_cache_config(tmp_path))

    assert provider.calls == 2
    assert summary["new_provider_success_count"] == 1
    assert summary["failed_provider_call_count"] == 1
    assert summary["remaining_missing_pair_count"] == 1
    assert len(list((tmp_path / "durable-cache" / "records").glob("*.json"))) == 1
    assert len(list((tmp_path / "durable-cache" / "failures").glob("*.json"))) == 1
    assert sum(1 for row in judgments if not row.get("error_type")) == 1


def test_stale_prompt_version_is_semantic_prior_not_exact_cache_hit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    signals, edges = _eligible_llm_edges(1)
    first_provider = _FakeLLMProvider()
    monkeypatch.setattr(
        sc_resolver_service,
        "primary_openai_provider_from_settings",
        lambda: (
            first_provider,
            {"provider_mode": "primary_openai", "llm_provider_label": "primary_openai", "uses_fallback_provider": False},
        ),
    )
    run_bounded_llm_adjudication(
        edges,
        signals=signals,
        config=_cache_config(tmp_path, prompt_version="prompt-v1"),
    )

    second_provider = _FakeLLMProvider(decision="cannot_link")
    monkeypatch.setattr(
        sc_resolver_service,
        "primary_openai_provider_from_settings",
        lambda: (
            second_provider,
            {"provider_mode": "primary_openai", "llm_provider_label": "primary_openai", "uses_fallback_provider": False},
        ),
    )
    judgments, summary = run_bounded_llm_adjudication(
        edges,
        signals=signals,
        config=_cache_config(tmp_path, prompt_version="prompt-v2"),
    )

    assert second_provider.calls == 1
    assert summary["cache_hits"] == 0
    assert summary["semantic_prior_judgment_count"] == 1
    assert judgments[0]["decision"] == "cannot_link"


def test_provider_error_rows_do_not_count_as_valid_cached_judgments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    signals, edges = _eligible_llm_edges(1)
    failing_provider = _FakeLLMProvider(fail_on_call=1)
    monkeypatch.setattr(
        sc_resolver_service,
        "primary_openai_provider_from_settings",
        lambda: (
            failing_provider,
            {"provider_mode": "primary_openai", "llm_provider_label": "primary_openai", "uses_fallback_provider": False},
        ),
    )
    first_judgments, first_summary = run_bounded_llm_adjudication(edges, signals=signals, config=_cache_config(tmp_path))

    assert first_summary["failed_provider_call_count"] == 1
    assert first_summary["cache_hits"] == 0
    assert first_judgments[0]["error_type"] == "RuntimeError"
    assert len(list((tmp_path / "durable-cache" / "records").glob("*.json"))) == 0

    succeeding_provider = _FakeLLMProvider()
    monkeypatch.setattr(
        sc_resolver_service,
        "primary_openai_provider_from_settings",
        lambda: (
            succeeding_provider,
            {"provider_mode": "primary_openai", "llm_provider_label": "primary_openai", "uses_fallback_provider": False},
        ),
    )
    second_judgments, second_summary = run_bounded_llm_adjudication(edges, signals=signals, config=_cache_config(tmp_path))

    assert succeeding_provider.calls == 1
    assert second_summary["cache_hits"] == 0
    assert second_summary["new_provider_success_count"] == 1
    assert second_judgments[0]["error_state"] is None


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
    base_row = {
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
        "language_hint": "ja",
        "script_hint": "jpan",
        "work_context": "\u539f\u795e",
        "work_context_key": "genshin_impact",
        "parenthetical_base": None,
        "parenthetical_context": None,
        "extraction_action": "accepted",
        "confidence": 0.9,
        "reason": "fixture",
        "candidate_key": "logical-key",
        "evidence_payload": {"source_field": "tag"},
    }

    def write_candidate(row: dict) -> None:
        (pack / "candidate-bundle.jsonl").write_text(
            json.dumps(row, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    write_candidate(base_row)

    result = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=True)

    assert result["candidate_bundle_count"] == 1
    assert result["persistence"]["forbidden_truth_table_write_count"] == 0
    assert session.query(SourceNameCandidate).count() == 1
    repeat = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=False)
    assert repeat["needs_import"] is False
    assert repeat["stable_checksum_matches"] is True

    changed_display = {**base_row, "display_name": "Kamisato Ayaka"}
    write_candidate(changed_display)
    display_mismatch = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=False)
    assert display_mismatch["candidate_bundle_count"] == display_mismatch["existing_db_candidate_count_for_run"] == 1
    assert display_mismatch["needs_import"] is True
    assert display_mismatch["stable_checksum_matches"] is False

    changed_context = {**base_row, "work_context": "Genshin Impact", "work_context_key": "genshin_impact_en"}
    write_candidate(changed_context)
    context_mismatch = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=False)
    assert context_mismatch["candidate_bundle_count"] == context_mismatch["existing_db_candidate_count_for_run"] == 1
    assert context_mismatch["needs_import"] is True
    assert context_mismatch["stable_checksum_matches"] is False

    write_candidate({**base_row, "candidate_key": "different-logical-key"})
    mismatch = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=False)
    assert mismatch["candidate_bundle_count"] == mismatch["existing_db_candidate_count_for_run"] == 1
    assert mismatch["needs_import"] is True
    assert mismatch["stable_checksum_matches"] is False


def test_f7a_final_pack_backfill_supersedes_removed_groups_only_within_same_run(tmp_path):
    _engine, session = _db()

    def write_pack(pack: Path, run_id: str, rows: list[dict]) -> None:
        pack.mkdir(exist_ok=True)
        (pack / "summary.json").write_text(
            json.dumps({"run_id": run_id, "validated_head": "abc", "candidate_summary": {"total": len(rows)}}),
            encoding="utf-8",
        )
        (pack / "candidate-bundle.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def candidate(group: str, key: str, value: str) -> dict:
        return {
            "group_key": group,
            "provider": "pixiv",
            "source_metadata_record_id": None,
            "media_id": None,
            "origin_type": "pixiv_tag",
            "origin_id": key,
            "raw_value": value,
            "display_name": value,
            "normalized_value": value.lower(),
            "canonical_key": canonical_key(value),
            "candidate_role": "character",
            "candidate_status": "active_candidate",
            "extraction_verdict": "single_candidate_found",
            "extraction_action": "accepted",
            "confidence": 0.9,
            "candidate_key": key,
        }

    pack = tmp_path / "f7a-pack"
    other_pack = tmp_path / "other-pack"
    group_a = candidate("source_record:A", "logical-a", "Kamisato Ayaka")
    group_b = candidate("source_record:B", "logical-b", "Barbara")
    other_row = candidate("source_record:B", "other-logical-b", "Barbara Other Run")
    write_pack(pack, "f7a-pack-run", [group_a, group_b])
    write_pack(other_pack, "other-run", [other_row])

    first = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=True)
    other = import_f7a_final_pack_candidates(session, pack_dir=other_pack, apply=True)
    assert first["persistence"]["forbidden_truth_table_write_count"] == 0
    assert other["persistence"]["forbidden_truth_table_write_count"] == 0

    write_pack(pack, "f7a-pack-run", [group_a])
    second = import_f7a_final_pack_candidates(session, pack_dir=pack, apply=True)

    same_run = (
        session.query(SourceNameCandidate, SourceNameCandidateExtractionRun.run_id)
        .join(SourceNameCandidateExtractionRun, SourceNameCandidate.extraction_run_id == SourceNameCandidateExtractionRun.id)
        .all()
    )
    by_logical = {
        (dict(row.evidence_payload or {}).get("logical_candidate_key"), run_id): row
        for row, run_id in same_run
    }

    assert by_logical[("logical-a", "f7a-pack-run")].status == "active"
    assert by_logical[("logical-b", "f7a-pack-run")].status == "superseded"
    assert by_logical[("logical-b", "f7a-pack-run")].candidate_status == "rejected"
    assert by_logical[("other-logical-b", "other-run")].status == "active"
    assert second["persistence"]["removed_group_superseded_candidates"] == 1
    assert second["persistence"]["post_import_existing_db_candidate_count_for_run"] == 1
    assert second["persistence"]["post_import_stable_checksum_matches"] is True
