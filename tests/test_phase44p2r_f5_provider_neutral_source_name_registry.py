"""Focused tests for Phase 4.4-P2R-F5 source name registry foundation."""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base, migrate_add_source_metadata_name_registry  # noqa: E402
from app.models import (  # noqa: E402
    Entity,
    EntityAlias,
    EntityEvidence,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    ProviderCache,
    SourceMetadataRecord,
    SourceMetadataEvidence,
    SourceNameAliasCandidate,
    SourceNameObservation,
    SourceNameRegistry,
    SourceSearchableNameAssertion,
    SourceTagObservation,
    SourceTagRegistry,
    TagTranslation,
    blombooru_media_tags,
)
from app.enums import ContentClassEnum, FileTypeEnum  # noqa: E402
from app.services import source_metadata_registry_service as service  # noqa: E402
from scripts import run_phase44p2r_f5_provider_neutral_source_name_registry as runner  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_f5_migration_creates_additive_source_tables():
    engine = create_engine("sqlite://")
    try:
        migrate_add_source_metadata_name_registry(engine, inspect(engine))
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert runner.ALLOWED_WRITE_TABLES.issubset(tables)
        assert "blombooru_source_name_registry" in tables
        assert "blombooru_source_name_alias_candidates" in tables
        assert "blombooru_source_searchable_name_assertions" in tables
    finally:
        engine.dispose()


def test_pixiv_source_metadata_creates_metadata_tag_and_name_rows():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:1",
            "artist_name": "Creator Test",
            "title": "Work Test",
            "tags": ["Character Test (Work Test)", "blue hair"],
        }
    ])

    assert len(bundle.metadata_records) == 1
    assert len(bundle.tag_observations) == 2
    roles = {row.name_role for row in bundle.name_observations}
    assert {"artist", "work_title", "character"}.issubset(roles)
    assert bundle.record_statuses[service.provider_record_lookup_key("pixiv", "pixiv:test:1")] == "applicable_name_signal_covered"


def test_explicit_empty_raw_metadata_json_is_preserved_when_persisted(db):
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:explicit-empty-raw",
            "raw_metadata_json": {},
            "raw_metadata": {"excluded_provider_payload": "must_not_fallback"},
            "title": "Visible Title",
            "tags": ["Visible Hero(Visible Work)"],
        }
    ])

    assert bundle.metadata_records[0].raw_metadata_json == {}

    service.persist_source_registry_bundle(db, bundle, apply=True)

    row = db.query(SourceMetadataRecord).one()
    assert row.raw_metadata_json == {}


def test_pixiv_parenthetical_character_extraction_and_relation():
    bundle = service.build_source_registry_bundle([
        {"provider": "pixiv", "provider_record_key": "pixiv:test:2", "tags": ["Hero Name(Work Name)"]}
    ])

    character = [row for row in bundle.name_observations if row.name_role == "character"]
    work = [row for row in bundle.name_observations if row.name_role == "work_title"]
    assert character and character[0].source_field == "pixiv_parenthetical_outer"
    assert work and work[0].source_field == "pixiv_parenthetical_inner_work"
    assert any(row.relation_type == "parenthetical_character_of_work" for row in bundle.alias_candidates)


def test_pixiv_user_metadata_artist_extraction():
    bundle = service.build_source_registry_bundle([
        {"provider": "pixiv", "provider_record_key": "pixiv:test:artist", "artist_name": "Artist Test"}
    ])
    artist = [row for row in bundle.name_observations if row.name_role == "artist"]
    assert len(artist) == 1
    assert artist[0].source_field == "pixiv_user_metadata"
    assert artist[0].requires_review is False


def test_saucenao_style_artist_and_title_extraction():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "saucenao",
            "provider_record_key": "saucenao:test:1",
            "artist": "Sauce Artist",
            "title": "Sauce Work",
            "similarity": 95.5,
        }
    ])
    roles = {row.name_role for row in bundle.name_observations}
    assert {"artist", "work_title"}.issubset(roles)
    fields = {row.source_field for row in bundle.name_observations}
    assert "saucenao_artist" in fields
    assert "saucenao_title" in fields


def test_saucenao_work_or_copyright_extraction():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "saucenao",
            "provider_record_key": "saucenao:test:work-or-copyright",
            "creator": "Sauce Creator",
            "work_or_copyright": ["Sauce Work"],
        }
    ])
    assert any(
        row.raw_name == "Sauce Work"
        and row.name_role == "work_title"
        and row.source_field == "saucenao_work_or_copyright"
        for row in bundle.name_observations
    )


def test_danbooru_character_tag_becomes_source_name_observation():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "danbooru",
            "provider_record_key": "danbooru:test:1",
            "tags": [{"name": "Dan Character", "category": "character"}],
        }
    ])
    assert any(row.raw_name == "Dan Character" and row.name_role == "character" for row in bundle.name_observations)


def test_booru_numeric_categories_become_source_name_roles():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "danbooru",
            "provider_record_key": "danbooru:test:numeric-categories",
            "tags": [
                {"name": "Numeric Artist", "category": 1},
                {"name": "Numeric Work", "category": 3},
                {"name": "Numeric Character", "category": 4},
                {"name": "Numeric General", "category": 0},
                {"name": "Numeric Meta", "category": 5},
            ],
        }
    ])
    by_name = {row.raw_name: row.name_role for row in bundle.name_observations}
    assert by_name["Numeric Artist"] == "artist"
    assert by_name["Numeric Work"] == "work_title"
    assert by_name["Numeric Character"] == "character"
    assert "Numeric General" not in by_name
    assert "Numeric Meta" not in by_name


def test_native_booru_tag_string_fields_become_source_name_roles():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "danbooru",
            "provider_record_key": "danbooru:test:native-tag-strings",
            "tag_string_artist": "native_artist",
            "tag_string_copyright": "native_work",
            "tag_string_character": "native_character",
            "tag_string_general": "blue_hair",
        }
    ])

    by_name = {row.raw_name: row.name_role for row in bundle.name_observations}
    assert by_name["native_artist"] == "artist"
    assert by_name["native_work"] == "work_title"
    assert by_name["native_character"] == "character"
    assert "blue_hair" not in by_name
    assert {row.raw_tag for row in bundle.tag_observations} >= {
        "native_artist",
        "native_work",
        "native_character",
        "blue_hair",
    }


def test_danbooru_copyright_tag_does_not_become_person():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "danbooru",
            "provider_record_key": "danbooru:test:2",
            "tags": [{"name": "Dan Work", "category": "copyright"}],
        }
    ])
    assert any(row.raw_name == "Dan Work" and row.name_role == "work_title" for row in bundle.name_observations)
    assert not any(row.raw_name == "Dan Work" and row.name_role in {"person", "character"} for row in bundle.name_observations)


def test_google_vision_generic_label_does_not_become_person():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "google_vision",
            "provider_record_key": "google:test:1",
            "labels": [{"name": "anime", "kind": "generic_provider_label"}],
        }
    ])
    assert len(bundle.tag_observations) == 1
    assert not bundle.name_observations
    coverage = service.provider_name_coverage(bundle)
    assert coverage["providers"]["google_vision"]["not_applicable_no_person_signal_count"] == 1


def test_no_tag_provider_has_zero_tags_names_and_is_not_failure():
    bundle = service.build_source_registry_bundle([
        {"provider": "no_tag_provider", "provider_record_key": "none:test:1"}
    ])
    assert len(bundle.metadata_records) == 1
    assert len(bundle.tag_observations) == 0
    assert len(bundle.name_observations) == 0
    assert bundle.record_statuses[service.provider_record_lookup_key("no_tag_provider", "none:test:1")] == "not_applicable_no_person_signal"


def test_canonical_name_key_normalization():
    assert service.canonical_source_key("  Ｃreator／Alpha  ") == "creator_alpha"


def test_alias_candidate_generation_from_provider_canonical():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:alias",
            "artist_name": "Alias Artist",
            "provider_canonical_aliases": {"Alias Artist": ["Alias A"]},
        }
    ])
    assert any(row.relation_type == "provider_canonical" for row in bundle.alias_candidates)


def test_curated_mapping_import_path():
    path = ROOT / ".local_manifests" / runner.PHASE_SLUG / "test-curated.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "source_name",
                    "target_name",
                    "relation_type",
                    "name_role",
                    "candidate_namespace",
                    "confidence",
                    "source",
                    "notes",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "source_name": "Short Name",
                    "target_name": "Long Name",
                    "relation_type": "curated_alias",
                    "name_role": "character",
                    "candidate_namespace": "source_name",
                    "confidence": "0.97",
                    "source": "operator_curated",
                    "notes": "test",
                }
            )
        mappings = runner.load_curated_mappings(path)
        bundle = service.build_source_registry_bundle([], curated_mappings=mappings)
        assert len(mappings) == 1
        assert any(row.relation_type == "curated_alias" for row in bundle.alias_candidates)
    finally:
        path.unlink(missing_ok=True)


def test_source_name_registry_seen_count_and_variants():
    bundle = service.build_source_registry_bundle([
        {"provider": "pixiv", "provider_record_key": "r1", "artist_name": "Same Name"},
        {"provider": "saucenao", "provider_record_key": "r2", "creator": "same name"},
    ])
    row = next(item for item in bundle.name_registry if item.canonical_name_key == "same_name")
    assert row.seen_count == 2
    assert set(row.provider_coverage_json) == {"pixiv", "saucenao"}
    assert len(row.raw_variants_json) == 2


def test_search_index_validation_maps_alias_variant():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:search",
            "artist_name": "Search Artist",
            "provider_canonical_aliases": {"Search Artist": ["Search Alias"]},
        }
    ])
    rows = service.validate_search_queries(bundle, ["Search Alias"])
    assert rows[0]["matched"] is True
    assert any(match["match_type"] == "alias_candidate" for match in rows[0]["matches"])


def test_coverage_metric_excludes_not_applicable_no_person_signal():
    bundle = service.build_source_registry_bundle([
        {"provider": "pixiv", "provider_record_key": "covered", "artist_name": "Covered Artist"},
        {"provider": "pixiv", "provider_record_key": "not-applicable", "tags": ["background"]},
    ])
    coverage = service.provider_name_coverage(bundle)["providers"]["pixiv"]
    assert coverage["applicable_name_signal_count"] == 1
    assert coverage["not_applicable_no_person_signal_count"] == 1
    assert coverage["coverage"] == 1.0


def test_real_pixiv_source_prior_only_is_not_metadata_rich():
    source_prior = {
        "provider": "pixiv",
        "provider_record_key": "db-source-prior:pixiv:unit:p0:m1",
        "media_id": 1,
        "source_work_id": "100000001",
        "source_page_index": 0,
        "metadata_kind": "local_pixiv_source_prior",
        "data_type_label": runner.DATA_TYPE_REAL,
    }

    assert runner.is_real_pixiv_metadata_rich_record(source_prior) is False


def test_gallery_dl_pixiv_metadata_becomes_real_metadata_rich_record():
    metadata = runner.f1.PixivGalleryDlMetadataRecord(
        work_id="100000002",
        page_index=0,
        page_count=1,
        title="Metadata Title",
        artist_name="Metadata Artist",
        artist_id="artist-1",
        tags=("Hero Name(Work Name)", "blue hair"),
        canonical_url="https://www.pixiv.net/artworks/100000002",
        metadata_richness="rich_structured_metadata",
        record_shape="gallery_dl_url_media_event",
    )
    row = runner.pixiv_gallery_dl_record_to_source_record(
        metadata,
        source_prior_lookup={
            ("100000002", 0): {
                "media_id": 42,
                "source_work_id": "100000002",
                "source_page_index": 0,
            }
        },
        source_index=1,
        stdout_path=ROOT / ".local_manifests" / runner.PHASE_SLUG / "unit-gallery.jsonl",
    )

    assert row is not None
    assert runner.is_real_pixiv_metadata_rich_record(row) is True
    assert row["metadata_kind"] == "gallery_dl_real_pixiv_metadata"
    assert row["provenance"]["gallery_dl_metadata_only"] is True
    assert row["provenance"]["no_download"] is True
    assert row["_source_title_only_fields"] == ["title", "pixiv_title"]


def test_gallery_dl_pixiv_metadata_requires_exact_source_prior_page_match():
    metadata = runner.f1.PixivGalleryDlMetadataRecord(
        work_id="100000003",
        page_index=1,
        page_count=2,
        title="Metadata Title",
        artist_name="Metadata Artist",
        artist_id="artist-1",
        tags=("Hero Name(Work Name)",),
        canonical_url="https://www.pixiv.net/artworks/100000003",
        metadata_richness="rich_structured_metadata",
        record_shape="gallery_dl_url_media_event",
    )
    row = runner.pixiv_gallery_dl_record_to_source_record(
        metadata,
        source_prior_lookup={
            ("100000003", 0): {
                "media_id": 42,
                "source_work_id": "100000003",
                "source_page_index": 0,
            }
        },
        source_index=1,
        stdout_path=ROOT / ".local_manifests" / runner.PHASE_SLUG / "unit-gallery.jsonl",
    )

    assert row is None


def test_gallery_dl_pixiv_metadata_key_is_stable_for_same_work_page_media():
    metadata = runner.f1.PixivGalleryDlMetadataRecord(
        work_id="100000005",
        page_index=0,
        page_count=1,
        title="Metadata Title",
        artist_name="Metadata Artist",
        tags=("Hero Name(Work Name)",),
        canonical_url="https://www.pixiv.net/artworks/100000005",
        metadata_richness="rich_structured_metadata",
        record_shape="gallery_dl_url_media_event",
    )
    prior = {
        ("100000005", 0): {
            "media_id": 77,
            "source_work_id": "100000005",
            "source_page_index": 0,
        }
    }

    first = runner.pixiv_gallery_dl_record_to_source_record(
        metadata,
        source_prior_lookup=prior,
        source_index=1,
        stdout_path=Path("metadata-001.jsonl"),
    )
    second = runner.pixiv_gallery_dl_record_to_source_record(
        metadata,
        source_prior_lookup=prior,
        source_index=99,
        stdout_path=Path("metadata-099.jsonl"),
    )

    assert first is not None and second is not None
    assert first["provider_record_key"] == second["provider_record_key"]
    assert first["provider_record_key"] == "gallery-dl-real-pixiv:metadata:100000005:p0:m77"


def test_gallery_dl_sparse_pixiv_metadata_is_not_metadata_rich_even_with_exact_match():
    metadata = runner.f1.PixivGalleryDlMetadataRecord(
        work_id="100000004",
        page_index=0,
        canonical_url="https://www.pixiv.net/artworks/100000004",
        metadata_richness="minimal_metadata",
        record_shape="gallery_dl_url_media_event",
    )
    row = runner.pixiv_gallery_dl_record_to_source_record(
        metadata,
        source_prior_lookup={
            ("100000004", 0): {
                "media_id": 42,
                "source_work_id": "100000004",
                "source_page_index": 0,
            }
        },
        source_index=1,
        stdout_path=ROOT / ".local_manifests" / runner.PHASE_SLUG / "unit-gallery.jsonl",
    )

    assert row is None


def test_pixiv_source_prior_ignores_filename_and_requires_anime_content_class(monkeypatch, tmp_path):
    db_path = tmp_path / "f5-source-prior.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        session.add_all(
            [
                Media(
                    id=1,
                    filename="100000101_p0.jpg",
                    path="media/filename-only.jpg",
                    hash="hash-filename",
                    file_type=FileTypeEnum.image,
                    source=None,
                    content_class=ContentClassEnum.anime,
                ),
                Media(
                    id=2,
                    filename="non-anime.jpg",
                    path="media/non-anime.jpg",
                    hash="hash-non-anime",
                    file_type=FileTypeEnum.image,
                    source="https://www.pixiv.net/artworks/100000102",
                    content_class=ContentClassEnum.non_anime,
                ),
                Media(
                    id=3,
                    filename="anime.jpg",
                    path="media/anime.jpg",
                    hash="hash-anime",
                    file_type=FileTypeEnum.image,
                    source="https://www.pixiv.net/artworks/100000103",
                    content_class=ContentClassEnum.anime,
                ),
                Media(
                    id=4,
                    filename="unknown.jpg",
                    path="media/unknown.jpg",
                    hash="hash-unknown",
                    file_type=FileTypeEnum.image,
                    source="https://www.pixiv.net/artworks/100000104",
                    content_class=ContentClassEnum.unknown,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()
        engine.dispose()

    class _Config:
        database_url = f"sqlite:///{db_path}"

    monkeypatch.setattr(runner, "load_f5_project_config", lambda: _Config())
    monkeypatch.setattr(
        runner.f1,
        "prove_db_identity",
        lambda _session, _config: {
            "violet_env": "development",
            "actual_db_name": "sqlite-test",
            "configured_db_name": "sqlite-test",
            "configured_db_host": "local",
            "configured_db_port": None,
            "database_url_source": "unit-test",
        },
    )

    records, summary = runner.load_real_pixiv_source_prior_records_from_db(limit=10)

    assert [row["source_work_id"] for row in records] == ["100000103"]
    assert records[0]["raw_metadata_json"]["matched_field"] == "source"
    assert summary["filename_values_included"] is False
    assert summary["trusted_source_fields_used"] == ["source"]
    assert summary["approved_content_classes"] == ["anime"]
    assert summary["skipped_content_class_counts"]["non_anime"] == 1
    assert summary["skipped_content_class_counts"]["unknown"] == 1


def test_pixiv_source_title_candidate_does_not_auto_create_work_title_identity():
    records = [
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:title-only",
            "title": "Only Source Title",
            "data_type_label": runner.DATA_TYPE_REAL,
            "_source_title_only_fields": ["title", "pixiv_title"],
        }
    ]
    bundle = service.build_source_registry_bundle(records)
    candidates = runner.build_searchable_name_candidates(bundle, records, max_candidates=10)

    assert not any(row.source_field == "pixiv_title" and row.name_role == "work_title" for row in bundle.name_observations)
    assert bundle.metadata_records[0].applicability_status == "not_applicable_no_person_signal"
    title_candidate = next(row for row in candidates if row.source_kind == "source_title_candidate")
    assert title_candidate.raw_input == "Only Source Title"
    assert title_candidate.role_hint == "source_title"
    assert title_candidate.context["title_is_not_deterministic_work_title_identity"] is True


def test_searchable_candidate_dedupe_preserves_real_pixiv_provenance():
    records = [
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:artifact:shared",
            "tags": ["Shared Hero(Shared Work)"],
            "metadata_kind": "old_artifact_metadata",
            "data_type_label": runner.DATA_TYPE_ARTIFACT,
        },
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:real:shared",
            "tags": ["Shared Hero(Shared Work)"],
            "metadata_kind": "gallery_dl_real_pixiv_metadata",
            "data_type_label": runner.DATA_TYPE_REAL,
            "provider_run_id": "unit-final-run",
        },
    ]
    bundle = service.build_source_registry_bundle(records)
    candidates = runner.build_searchable_name_candidates(bundle, records, max_candidates=10)

    candidate = next(row for row in candidates if row.raw_input == "Shared Hero(Shared Work)")
    assert candidate.data_type_label == runner.DATA_TYPE_REAL
    assert candidate.provider_record_key == "pixiv:real:shared"
    assert candidate.context["metadata_kind"] == "gallery_dl_real_pixiv_metadata"
    assert candidate.context["run_id"] == "unit-final-run"
    assert candidate.occurrence_count == 2


def test_boolean_and_non_string_values_are_not_sent_to_llm_candidates():
    records = [
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:bad-values",
            "title": True,
            "artist_name": False,
            "tags": [True, {"name": False}, 123, "Hero Name(Work Name)"],
            "data_type_label": runner.DATA_TYPE_REAL,
        }
    ]
    bundle = service.build_source_registry_bundle(records)
    candidates = runner.build_searchable_name_candidates(bundle, records, max_candidates=10)

    assert {row.raw_tag for row in bundle.tag_observations} == {"Hero Name(Work Name)"}
    assert all(row.raw_input not in {"True", "False", "123"} for row in candidates)
    assert any(row.raw_input == "Hero Name(Work Name)" for row in candidates)


def test_raw_saucenao_signal_denominator_is_not_derived_from_extracted_names():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "saucenao",
            "provider_record_key": "sauce:raw-work-disabled",
            "work_or_copyright": "Raw Sauce Work",
            "disable_name_extraction_fields": ["work_or_copyright"],
        }
    ])

    coverage = service.provider_name_coverage(bundle)["providers"]["saucenao"]
    assert coverage["applicable_name_signal_count"] == 1
    assert coverage["covered_name_signal_count"] == 0
    assert coverage["failed_applicable_name_signal_count"] == 1
    assert coverage["role_applicable_counts"] == {"work_title": 1}
    assert coverage["role_covered_counts"] == {}
    assert coverage["coverage"] == 0.0
    assert bundle.metadata_records[0].applicability_status == "applicable_name_signal_uncovered"


def test_raw_signal_denominator_ignores_uncanonicalizable_creator_value():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "saucenao",
            "provider_record_key": "sauce:empty-creator-key",
            "creator": "!!!",
            "work_or_copyright": "Indexable Work",
        }
    ])

    coverage = service.provider_name_coverage(bundle)["providers"]["saucenao"]
    assert coverage["applicable_name_signal_count"] == 1
    assert coverage["role_applicable_counts"] == {"work_title": 1}
    assert coverage["role_covered_counts"] == {"work_title": 1}
    assert coverage["coverage"] == 1.0
    assert not bundle.metadata_records[0].raw_name_signal_flags["has_raw_creator_signal"]


def test_raw_pixiv_parenthetical_signal_denominator_survives_disabled_extraction():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:raw-parenthetical-disabled",
            "tags": ["Raw Hero(Raw Work)"],
            "disable_parenthetical_name_extraction": True,
        }
    ])

    coverage = service.provider_name_coverage(bundle)["providers"]["pixiv"]
    assert coverage["applicable_name_signal_count"] == 1
    assert coverage["role_applicable_counts"] == {"character": 1, "work_title": 1}
    assert coverage["role_covered_counts"] == {}
    assert coverage["coverage"] == 0.0
    raw_rows = service.raw_applicable_signal_rows(bundle)
    assert raw_rows[0]["has_raw_parenthetical_character_work_signal"] is True
    assert raw_rows[0]["all_raw_roles_extracted"] is False


def test_no_forbidden_truth_table_writes_when_source_registry_is_persisted(db):
    bundle = service.build_source_registry_bundle(runner.default_provider_shape_records())
    summary = service.persist_source_registry_bundle(db, bundle, apply=True)

    assert summary["forbidden_truth_table_write_count"] == 0
    assert db.query(SourceMetadataRecord).count() == len(bundle.metadata_records)
    assert db.query(SourceNameRegistry).count() == len(bundle.name_registry)
    assert db.query(SourceNameAliasCandidate).count() == len(bundle.alias_candidates)
    assert db.query(SourceSearchableNameAssertion).count() == 0
    assert db.query(Entity).count() == 0
    assert db.query(EntityAlias).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0
    assert db.query(MediaEntityAssignment).count() == 0
    assert db.query(ProviderCache).count() == 0
    assert db.query(TagTranslation).count() == 0
    assert db.execute(text(f"SELECT COUNT(*) FROM {blombooru_media_tags.name}")).scalar() == 0


def test_tag_metadata_evidence_observation_id_points_to_source_tag_observation(db):
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:tag-evidence",
            "tags": ["Evidence Hero(Evidence Work)"],
        }
    ])
    tag = bundle.tag_observations[0]
    tag_evidence = service.SourceMetadataEvidenceDraft(
        provider=tag.provider,
        provider_record_key=tag.provider_record_key,
        evidence_key=f"{tag.observation_key}:tag_evidence",
        observation_type="source_tag_observation",
        observation_key=tag.observation_key,
        evidence_kind="source_tag_evidence",
        evidence_strength="source_observation",
        provenance={"unit_test": True},
    )
    bundle = replace(bundle, evidence=tuple(bundle.evidence) + (tag_evidence,))

    service.persist_source_registry_bundle(db, bundle, apply=True)

    tag_row = db.query(SourceTagObservation).filter_by(observation_key=tag.observation_key).one()
    evidence_row = db.query(SourceMetadataEvidence).filter_by(evidence_key=tag_evidence.evidence_key).one()
    assert evidence_row.observation_type == "source_tag_observation"
    assert evidence_row.observation_id == tag_row.id


def _with_tag_evidence(bundle):
    tag_evidence = tuple(
        service.SourceMetadataEvidenceDraft(
            provider=tag.provider,
            provider_record_key=tag.provider_record_key,
            evidence_key=f"{tag.observation_key}:tag_evidence",
            observation_type="source_tag_observation",
            observation_key=tag.observation_key,
            evidence_kind="source_tag_evidence",
            evidence_strength="source_observation",
            provenance={"unit_test": True},
        )
        for tag in bundle.tag_observations
    )
    return replace(bundle, evidence=tuple(bundle.evidence) + tag_evidence)


def test_tag_observation_keys_are_stable_when_tag_order_changes(db):
    first = _with_tag_evidence(
        service.build_source_registry_bundle([
            {
                "provider": "pixiv",
                "provider_record_key": "pixiv:test:tag-order",
                "tags": ["Stable Alpha", "Stable Beta"],
            }
        ])
    )
    second = _with_tag_evidence(
        service.build_source_registry_bundle([
            {
                "provider": "pixiv",
                "provider_record_key": "pixiv:test:tag-order",
                "tags": ["Stable Beta", "Stable Alpha"],
            }
        ])
    )

    first_keys = {row.observation_key for row in first.tag_observations}
    second_keys = {row.observation_key for row in second.tag_observations}
    assert first_keys == second_keys

    service.persist_source_registry_bundle(db, first, apply=True)
    summary = service.persist_source_registry_bundle(db, second, apply=True)

    assert summary["retired"].get("SourceTagObservation", 0) == 0
    assert summary["retired"].get("SourceMetadataEvidence", 0) == 0
    assert db.query(SourceTagObservation).filter_by(status="observed").count() == 2
    assert db.query(SourceMetadataEvidence).filter_by(status="superseded").count() == 0


def test_clean_f5_state_scopes_to_current_run_and_full_cleanup_requires_opt_in(db):
    prior = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_run_id": "prior-f5-run",
            "provider_record_key": "pixiv:test:prior-run",
            "tags": ["Prior Hero(Prior Work)"],
        }
    ])
    current = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_run_id": "current-f5-run",
            "provider_record_key": "pixiv:test:current-run",
            "tags": ["Current Hero(Current Work)"],
        }
    ])
    service.persist_source_registry_bundle(db, prior, apply=True)
    service.persist_source_registry_bundle(db, current, apply=True)

    with pytest.raises(runner.Phase44P2RF5Error, match="requires_current_run_scope"):
        runner.cleanup_f5_source_tables(db)

    scoped = runner.cleanup_f5_source_tables(
        db,
        provider_run_ids=["current-f5-run"],
        provider_record_keys=[("pixiv", "pixiv:test:current-run")],
    )

    assert scoped["cleanup_mode"] == "current_run_scope"
    assert scoped["destructive_full_cleanup"] is False
    assert db.query(SourceMetadataRecord).filter_by(provider_run_id="current-f5-run").count() == 0
    assert db.query(SourceMetadataRecord).filter_by(provider_run_id="prior-f5-run").count() == 1
    assert db.query(SourceTagObservation).join(SourceMetadataRecord).filter(
        SourceMetadataRecord.provider_run_id == "prior-f5-run"
    ).count() == 1

    full = runner.cleanup_f5_source_tables(db, allow_destructive_full_cleanup=True)

    assert full["cleanup_mode"] == "destructive_full_f5_cleanup"
    assert full["destructive_full_cleanup"] is True
    assert db.query(SourceMetadataRecord).count() == 0
    assert db.query(SourceTagRegistry).count() == 0


def test_metadata_lookup_key_includes_provider_when_provider_record_key_collides(db):
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "shared-record-key",
            "artist_name": "Pixiv Collision Artist",
            "tags": [{"name": "Pixiv Collision Tag", "category": "artist"}],
        },
        {
            "provider": "danbooru",
            "provider_record_key": "shared-record-key",
            "tags": [{"name": "Danbooru Collision Character", "category": "character"}],
        },
    ])
    service.persist_source_registry_bundle(db, bundle, apply=True)

    pixiv_record = db.query(SourceMetadataRecord).filter_by(provider="pixiv", provider_record_key="shared-record-key").one()
    danbooru_record = db.query(SourceMetadataRecord).filter_by(provider="danbooru", provider_record_key="shared-record-key").one()
    assert pixiv_record.id != danbooru_record.id
    assert db.query(SourceTagObservation).filter_by(source_metadata_record_id=pixiv_record.id).count() == 1
    assert db.query(SourceTagObservation).filter_by(source_metadata_record_id=danbooru_record.id).count() == 1
    assert {
        row.provider
        for row in db.query(SourceNameObservation).filter(
            SourceNameObservation.source_metadata_record_id.in_([pixiv_record.id, danbooru_record.id])
        )
    } == {"pixiv", "danbooru"}


def test_source_name_registry_merges_across_apply_batches(db):
    first = service.build_source_registry_bundle([
        {"provider": "pixiv", "provider_record_key": "batch:1", "artist_name": "Shared Registry Name"}
    ])
    second = service.build_source_registry_bundle([
        {"provider": "saucenao", "provider_record_key": "batch:2", "creator": "shared registry name"}
    ])

    service.persist_source_registry_bundle(db, first, apply=True)
    service.persist_source_registry_bundle(db, second, apply=True)

    row = db.query(SourceNameRegistry).filter_by(canonical_name_key="shared_registry_name").one()
    assert row.seen_count == 2
    assert set(row.provider_coverage_json) == {"pixiv", "saucenao"}
    assert row.role_distribution_json == {"artist": 1, "creator": 1}


def test_refresh_retires_stale_name_tag_and_evidence_observations(db):
    first = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "refresh:same",
            "artist_name": "Retired Artist",
            "tags": ["Retired Tag"],
        }
    ])
    second = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "refresh:same",
            "tags": [],
        }
    ])

    service.persist_source_registry_bundle(db, first, apply=True)
    summary = service.persist_source_registry_bundle(db, second, apply=True)

    assert summary["retired"]["SourceNameObservation"] == 1
    assert summary["retired"]["SourceTagObservation"] == 1
    assert summary["retired"]["SourceMetadataEvidence"] >= 1
    record = db.query(SourceMetadataRecord).filter_by(provider="pixiv", provider_record_key="refresh:same").one()
    assert db.query(SourceNameObservation).filter_by(source_metadata_record_id=record.id, status="observed").count() == 0
    assert db.query(SourceTagObservation).filter_by(source_metadata_record_id=record.id, status="observed").count() == 0
    retired_name = db.query(SourceNameRegistry).filter_by(canonical_name_key="retired_artist").one()
    assert retired_name.seen_count == 0
    assert retired_name.governance_status == "retired"
    retired_tag = db.query(SourceTagRegistry).filter_by(canonical_tag_key="retired_tag").one()
    assert retired_tag.seen_count == 0
    assert retired_tag.governance_status == "retired"


def test_source_tag_registry_merges_across_apply_batches(db):
    first = service.build_source_registry_bundle([
        {"provider": "pixiv", "provider_record_key": "tag-batch:1", "tags": ["Shared Registry Tag"]}
    ])
    second = service.build_source_registry_bundle([
        {"provider": "saucenao", "provider_record_key": "tag-batch:2", "tags": ["shared registry tag"]}
    ])

    service.persist_source_registry_bundle(db, first, apply=True)
    service.persist_source_registry_bundle(db, second, apply=True)

    row = db.query(SourceTagRegistry).filter_by(canonical_tag_key="shared_registry_tag").one()
    assert row.seen_count == 2
    assert set(row.raw_variants_json) == {"Shared Registry Tag", "shared registry tag"}


def test_dry_run_no_db_writes(db):
    bundle = service.build_source_registry_bundle(runner.default_provider_shape_records())
    summary = service.persist_source_registry_bundle(db, bundle, apply=False)
    assert summary["apply"] is False
    assert db.query(SourceMetadataRecord).count() == 0
    assert db.query(SourceNameObservation).count() == 0


def test_no_db_mode_no_db_writes():
    args = runner.build_arg_parser().parse_args(["--no-db"])
    bundle = service.build_source_registry_bundle(runner.default_provider_shape_records())
    _identity, summary = runner.db_apply_summary(args, bundle, [])
    assert summary["apply"] is False
    assert summary["guard_installed"] is False


def test_scale_up_labels_records_and_adds_no_match_control():
    args = runner.build_arg_parser().parse_args(["--no-db"])
    records, input_summary = runner.load_provider_records(None)
    scaled, scale_summary = runner.scale_provider_records(records, args)
    bundle = service.build_source_registry_bundle(scaled)
    search_rows = service.validate_search_queries(bundle, runner.search_validation_queries(bundle))
    search_summary = runner.search_validation_summary(search_rows)

    assert len(scaled) >= runner.MIN_RECORD_COUNT
    assert all(row.get("data_type_label") in runner.DATA_TYPE_LABELS for row in scaled)
    assert scale_summary["scale_up_enabled"] is True
    assert search_summary["positive_queries_matched"] is True
    assert search_summary["no_match_control_present"] is True
    assert search_summary["unmatched_count"] == 1


def test_scale_minimums_are_reported_after_max_record_truncation():
    args = runner.build_arg_parser().parse_args(["--no-db", "--max-records", "10"])
    records, _input_summary = runner.load_provider_records(None)
    scaled, scale_summary = runner.scale_provider_records(records, args)

    assert len(scaled) == 10
    assert scale_summary["actual_record_count"] == 10
    assert scale_summary["meets_scale_minimum"] is False


def test_write_guard_blocks_forbidden_truth_table_write():
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        runner.install_source_registry_write_guard(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO blombooru_source_metadata_records "
                    "(provider, provider_record_key, metadata_kind, status) "
                    "VALUES ('test', 'test-key', 'provider_metadata', 'observed')"
                )
            )
            with pytest.raises(runner.f1.ReadOnlyViolation):
                conn.execute(
                    text(
                        "INSERT INTO blombooru_entity_evidence "
                        "(source_type, evidence_type, privacy_redacted) "
                        "VALUES ('test', 'manual', 1)"
                    )
                )
    finally:
        engine.dispose()


def test_write_guard_blocks_merge_replace_copy_and_allows_source_update():
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        runner.install_source_registry_write_guard(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO blombooru_source_metadata_records "
                    "(provider, provider_record_key, metadata_kind, data_type_label, status) "
                    "VALUES ('test', 'guard-key', 'provider_metadata', 'fixture_or_mock', 'observed')"
                )
            )
            conn.execute(
                text(
                    "UPDATE blombooru_source_metadata_records "
                    "SET status='observed' WHERE provider_record_key='guard-key'"
                )
            )
            for statement in (
                "MERGE INTO blombooru_entities AS target USING blombooru_entities AS source ON 1=0 WHEN NOT MATCHED THEN INSERT DEFAULT VALUES",
                "REPLACE INTO blombooru_entity_evidence (source_type, evidence_type, privacy_redacted) VALUES ('x', 'y', 1)",
                "COPY blombooru_entities FROM STDIN",
            ):
                with pytest.raises(runner.f1.ReadOnlyViolation):
                    conn.execute(text(statement))
    finally:
        engine.dispose()


def test_write_guard_allows_only_f5_cleanup_deletes_when_enabled():
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        runner.install_source_registry_write_guard(engine, allow_cleanup_deletes=True)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO blombooru_source_metadata_records "
                    "(provider, provider_record_key, metadata_kind, data_type_label, status) "
                    "VALUES ('test', 'cleanup-key', 'provider_metadata', 'fixture_or_mock', 'observed')"
                )
            )
            conn.execute(text("DELETE FROM blombooru_source_metadata_records WHERE provider_record_key='cleanup-key'"))
            with pytest.raises(runner.f1.ReadOnlyViolation):
                conn.execute(text("DELETE FROM blombooru_entities"))
    finally:
        engine.dispose()


def test_report_split_keeps_real_pixiv_applicable_rows_separate_from_fixtures():
    records = [
        {
            "provider": "pixiv",
            "provider_record_key": "real:pixiv:applicable",
            "artist_name": "Real Split Artist",
            "data_type_label": runner.DATA_TYPE_REAL,
        },
        {
            "provider": "pixiv",
            "provider_record_key": "fixture:pixiv:applicable",
            "artist_name": "Fixture Split Artist",
            "data_type_label": runner.DATA_TYPE_FIXTURE,
        },
    ]
    bundle = service.build_source_registry_bundle(records)
    search_rows = service.validate_search_queries(bundle, runner.search_validation_queries(bundle))
    summary = runner.build_public_summary(
        records=records,
        bundle=bundle,
        searchable_candidates=[],
        searchable_name_assertions=[],
        llm_classification_summary={"api_call_attempted": False, "mode": "test"},
        input_summary={"input_source": "test"},
        curated_mapping_count=0,
        db_identity=None,
        db_write_summary={"apply": False, "guard_installed": False, "success": False},
        search_rows=search_rows,
        assertion_search_rows=[],
    )

    assert summary["coverage_by_provider_and_data_type"][f"pixiv:{runner.DATA_TYPE_REAL}"]["applicable_name_signal_count"] == 1
    assert summary["coverage_by_provider_and_data_type"][f"pixiv:{runner.DATA_TYPE_FIXTURE}"]["applicable_name_signal_count"] == 1
    assert summary["expanded_real_data_validation"]["real_pixiv"]["applicable_name_signal_count"] == 1
    assert summary["expanded_real_data_validation"]["fixture_coverage_satisfies_real_targets"] is False
    assert summary["f5_minimum_requirements"]["f5_minimum_stage_goal_met"] is False


def test_report_metadata_rich_gate_uses_actual_enriched_rows_not_real_record_count():
    records = [
        {
            "provider": "pixiv",
            "provider_record_key": "db-source-prior:pixiv:100:p0:m1",
            "source_work_id": "100",
            "source_page_index": 0,
            "metadata_kind": "local_pixiv_source_prior",
            "data_type_label": runner.DATA_TYPE_REAL,
        }
    ]
    bundle = service.build_source_registry_bundle(records)
    summary = runner.build_public_summary(
        records=records,
        bundle=bundle,
        searchable_candidates=[],
        searchable_name_assertions=[],
        llm_classification_summary={"api_call_attempted": False, "mode": "test"},
        input_summary={
            "input_source": "test",
            "scale_up": {
                "real_pixiv_source_prior_minimum": 50,
                "real_pixiv_source_prior_summary": {"record_count": 50},
                "real_pixiv_metadata_rich_minimum": 60,
                "real_pixiv_metadata_enrichment_summary": {"metadata_rich_record_count": 0},
            },
        },
        curated_mapping_count=0,
        db_identity=None,
        db_write_summary={"apply": False, "guard_installed": False, "success": False},
        search_rows=[],
        assertion_search_rows=[],
    )

    flow = summary["expanded_real_data_validation"]["real_pixiv_metadata_flow"]
    assert flow["metadata_rich_record_count"] == 0
    assert flow["metadata_rich_count_uses_actual_records_not_provider_record_fallback"] is True
    assert summary["f5_minimum_requirements"]["real_pixiv_metadata_rich_records_at_least_60"] is False


def test_path_validation_before_side_effects_blocks_bad_output():
    with pytest.raises(runner.OutputPathError):
        runner.validate_private_output_paths_before_effects(
            ROOT / "docs",
            private_paths=[ROOT / "docs" / "bad.csv"],
        )


def test_public_report_redacts_private_names():
    records = runner.default_provider_shape_records()
    bundle = service.build_source_registry_bundle(records)
    search_rows = service.validate_search_queries(bundle, runner.search_validation_queries(bundle))
    summary = runner.build_public_summary(
        records=records,
        bundle=bundle,
        searchable_candidates=[],
        searchable_name_assertions=[],
        llm_classification_summary={"api_call_attempted": False, "mode": "test"},
        input_summary={"input_source": "test"},
        curated_mapping_count=0,
        db_identity=None,
        db_write_summary={"apply": False, "guard_installed": False},
        search_rows=search_rows,
        assertion_search_rows=[],
    )
    report = runner.build_markdown_report(summary, private_markers=runner.private_markers(bundle, records, []))
    assert "Creator Alpha" not in report
    assert "Character Alpha" not in report
    assert summary["public_report_redaction"]["raw_names_and_aliases_private_only"] is True


def _candidate(
    raw_input="Hero Name(Work Name)",
    *,
    provider="pixiv",
    role_hint=None,
    source_kind="source_tag_observation",
    candidate_key=None,
    provider_record_key=None,
):
    return runner.SearchableNameCandidate(
        candidate_key=candidate_key or f"test-candidate:{runner.canonical_source_key(provider + ':' + raw_input)}",
        provider=provider,
        provider_record_key=provider_record_key or f"{provider}:test:assertion",
        raw_input=raw_input,
        normalized_input=runner.normalize_source_text(raw_input),
        data_type_label=runner.DATA_TYPE_REAL,
        source_kind=source_kind,
        source_field="test_field",
        role_hint=role_hint,
        source_tag_observation_key="tag:test:1" if source_kind == "source_tag_observation" else None,
        source_name_observation_key="name:test:1" if source_kind == "source_name_observation" else None,
        parenthetical_outer="Hero Name" if "(" in raw_input else None,
        parenthetical_inner="Work Name" if "(" in raw_input else None,
        high_impact=True,
    )


def _valid_model_output(candidate, **overrides):
    payload = {
        "candidate_key": candidate.candidate_key,
        "input": candidate.raw_input,
        "normalized_input": candidate.normalized_input,
        "is_name_like": True,
        "asserted_role": "character",
        "extracted_name": "Hero Name",
        "base_name": "Hero Name",
        "work_context": "Work Name",
        "alias_candidates": [],
        "is_searchable_identity": True,
        "searchable_status": "searchable_active",
        "confidence": "high",
        "reason_code": "parenthetical_character_work",
        "evidence_summary": "Parenthetical tag separates a candidate name and work context.",
        "requires_review": True,
        "should_not_be_entity_truth": True,
    }
    payload.update(overrides)
    return payload


def _valid_model_output_from_prompt_candidate(candidate_payload, **overrides):
    raw_input = candidate_payload["input"]
    normalized_input = candidate_payload["normalized_input"]
    output = {
        "candidate_key": candidate_payload["candidate_key"],
        "input": raw_input,
        "normalized_input": normalized_input,
        "is_name_like": True,
        "asserted_role": "character",
        "extracted_name": raw_input.split("(")[0],
        "base_name": raw_input.split("(")[0],
        "work_context": "Work Name" if "(" in raw_input else None,
        "alias_candidates": [],
        "is_searchable_identity": True,
        "searchable_status": "searchable_active",
        "confidence": "high",
        "reason_code": "parenthetical_character_work",
        "evidence_summary": "Unit fake provider produced a valid searchable assertion.",
        "requires_review": True,
        "should_not_be_entity_truth": True,
    }
    output.update(overrides)
    return output


def _prompt_candidates(messages):
    return json.loads(messages[-1]["content"])["candidates"]


class _ChunkRepairFakeProvider(runner.BaseLLMProvider):
    def is_available(self):
        return True

    def get_provider_name(self):
        return "unit-fake"

    async def complete_chat(self, messages, *, temperature=0.3, max_tokens=4096):
        candidates = _prompt_candidates(messages)
        if len(candidates) > 1:
            return "not valid json"
        return json.dumps([_valid_model_output_from_prompt_candidate(candidates[0])])

    async def complete_json(self, messages, *, temperature=0.3, max_tokens=4096):
        candidates = _prompt_candidates(messages)
        if len(candidates) > 1:
            raise runner.LLMResponseFormatError("unit format failure")
        return [_valid_model_output_from_prompt_candidate(candidates[0])]

    async def translate_tags(self, tags):
        return []


class _AlwaysInvalidFakeProvider(_ChunkRepairFakeProvider):
    async def complete_chat(self, messages, *, temperature=0.3, max_tokens=4096):
        return "not valid json"

    async def complete_json(self, messages, *, temperature=0.3, max_tokens=4096):
        candidates = _prompt_candidates(messages)
        return [
            _valid_model_output_from_prompt_candidate(
                candidates[0],
                searchable_status="searchable_active",
                is_searchable_identity=False,
            )
        ]


class _TransientTransportFakeProvider(_ChunkRepairFakeProvider):
    def __init__(self):
        self.calls = 0

    async def complete_json(self, messages, *, temperature=0.3, max_tokens=4096):
        self.calls += 1
        if self.calls == 1:
            raise runner.LLMTransportError("unit transient timeout")
        candidates = _prompt_candidates(messages)
        return [_valid_model_output_from_prompt_candidate(candidate) for candidate in candidates]


def test_structured_model_output_schema_validation():
    candidate = _candidate()
    validated = runner.validate_model_assertion_output(_valid_model_output(candidate), candidate)
    assert validated.asserted_role == "character"
    assert validated.searchable_status == "searchable_active"
    assert validated.should_not_be_entity_truth is True


def test_invalid_model_output_fails_closed():
    candidate = _candidate()
    payload = _valid_model_output(candidate, should_not_be_entity_truth=False)
    with pytest.raises(runner.Phase44P2RF5Error):
        runner.validate_model_assertion_output(payload, candidate)


def test_model_output_missing_candidate_key_fails_closed():
    candidate = _candidate()
    payload = _valid_model_output(candidate)
    payload.pop("candidate_key")
    with pytest.raises(runner.Phase44P2RF5Error, match="candidate_key_missing"):
        runner.validate_model_assertion_output(payload, candidate)


def test_model_output_active_with_non_searchable_reason_fails_closed():
    candidate = _candidate("blue hair")
    payload = _valid_model_output(
        candidate,
        asserted_role="general_descriptor",
        is_searchable_identity=True,
        searchable_status="searchable_active",
        reason_code="descriptive_tag",
    )
    with pytest.raises(runner.Phase44P2RF5Error, match="active_contradictory_reason"):
        runner.validate_model_assertion_output(payload, candidate)


def test_model_output_active_with_non_identity_role_fails_closed():
    candidate = _candidate("Hero Name")
    payload = _valid_model_output(
        candidate,
        asserted_role="general_descriptor",
        is_searchable_identity=True,
        searchable_status="searchable_active",
        reason_code="known_character_name",
    )
    with pytest.raises(runner.Phase44P2RF5Error, match="active_non_identity_role"):
        runner.validate_model_assertion_output(payload, candidate)


def test_parenthetical_tag_model_classification_to_assertion_draft():
    candidate = _candidate()
    validated = runner.validate_model_assertion_output(_valid_model_output(candidate), candidate)
    draft = runner.assertion_draft_from_model_output(candidate, validated, model_name="unit-model")
    assert draft.asserted_name == "Hero Name"
    assert draft.asserted_role == "character"
    assert draft.status == "searchable_active"
    assert draft.evidence_sources_json["reason_code"] == "parenthetical_character_work"
    assert draft.provenance_summary["should_not_be_entity_truth"] is True


def test_popularity_marker_classification_is_not_person():
    candidate = _candidate("Work1000users")
    payload = _valid_model_output(
        candidate,
        is_name_like=False,
        asserted_role="popularity_marker",
        extracted_name=None,
        base_name="Work",
        work_context=None,
        is_searchable_identity=False,
        searchable_status="rejected",
        confidence="high",
        reason_code="popularity_marker",
    )
    validated = runner.validate_model_assertion_output(payload, candidate)
    assert validated.asserted_role == "popularity_marker"
    assert validated.searchable_status == "rejected"


def test_general_descriptor_classification_is_not_person():
    candidate = _candidate("blue hair")
    payload = _valid_model_output(
        candidate,
        is_name_like=False,
        asserted_role="general_descriptor",
        extracted_name=None,
        base_name=None,
        work_context=None,
        is_searchable_identity=False,
        searchable_status="rejected",
        confidence="high",
        reason_code="descriptive_tag",
    )
    validated = runner.validate_model_assertion_output(payload, candidate)
    assert validated.asserted_role == "general_descriptor"
    assert validated.is_searchable_identity is False


def test_saucenao_work_or_copyright_assertion_role():
    candidate = _candidate(
        "Sauce Work",
        provider="saucenao",
        role_hint="work_title",
        source_kind="source_name_observation",
    )
    payload = _valid_model_output(
        candidate,
        asserted_role="work_title",
        extracted_name="Sauce Work",
        base_name="Sauce Work",
        work_context=None,
        reason_code="known_work_title",
    )
    validated = runner.validate_model_assertion_output(payload, candidate)
    assert validated.asserted_role == "work_title"
    assert validated.searchable_status == "searchable_active"


def test_saucenao_provider_field_assertion_is_provider_backed_without_llm(monkeypatch):
    records = [
        {
            "provider": "saucenao",
            "provider_record_key": "saucenao:test:provider-backed",
            "work_or_copyright": "Sauce Work",
            "data_type_label": runner.DATA_TYPE_ARTIFACT,
        }
    ]
    bundle = service.build_source_registry_bundle(records)
    args = runner.build_arg_parser().parse_args(["--use-llm-api"])

    def fail_provider():
        raise AssertionError("provider field assertion should not call LLM when no LLM candidates remain")

    monkeypatch.setattr(runner, "source_assertion_provider_from_env", fail_provider)
    candidates, assertions, summary, inputs, outputs, review_rows = runner.classify_source_searchable_name_assertions(
        args,
        bundle,
        records,
    )

    assert len(candidates) == 1
    assert len(assertions) == 1
    assert assertions[0].provider == "saucenao"
    assert assertions[0].asserted_role == "work_title"
    assert assertions[0].status == "searchable_active"
    assert assertions[0].model_name == "provider_field_saucenao"
    assert summary["provider_field_assertions"] == 1
    assert summary["api_call_attempted"] is False
    assert inputs == []
    assert outputs == []
    assert review_rows[0]["status"] == "searchable_active"


def test_model_searchable_active_writes_only_assertion_table(db):
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:assertion",
            "tags": ["Hero Name(Work Name)"],
        }
    ])
    candidates = runner.build_searchable_name_candidates(bundle, [{"provider": "pixiv", "provider_record_key": "pixiv:test:assertion", "tags": ["Hero Name(Work Name)"], "data_type_label": runner.DATA_TYPE_REAL}], max_candidates=10)
    candidate = next(row for row in candidates if row.source_kind == "source_tag_observation")
    validated = runner.validate_model_assertion_output(_valid_model_output(candidate), candidate)
    draft = runner.assertion_draft_from_model_output(candidate, validated, model_name="unit-model")

    summary = service.persist_source_registry_bundle(
        db,
        bundle,
        apply=True,
        searchable_name_assertions=[draft],
    )

    assert summary["inserted"]["SourceSearchableNameAssertion"] == 1
    row = db.query(SourceSearchableNameAssertion).one()
    assert row.source_tag_observation_id is not None
    assert row.status == "searchable_active"
    assert db.query(Entity).count() == 0
    assert db.query(EntityAlias).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


def test_refresh_retires_stale_searchable_name_assertions(db):
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:assertion-refresh",
            "tags": ["Hero Name(Work Name)"],
        }
    ])
    candidates = runner.build_searchable_name_candidates(
        bundle,
        [
            {
                "provider": "pixiv",
                "provider_record_key": "pixiv:test:assertion-refresh",
                "tags": ["Hero Name(Work Name)"],
                "data_type_label": runner.DATA_TYPE_REAL,
            }
        ],
        max_candidates=10,
    )
    candidate = next(row for row in candidates if row.source_kind == "source_tag_observation")
    validated = runner.validate_model_assertion_output(_valid_model_output(candidate), candidate)
    first = runner.assertion_draft_from_model_output(candidate, validated, model_name="unit-model")
    second = replace(first, assertion_key=first.assertion_key + ":refresh", asserted_name="Hero Name Refreshed")

    service.persist_source_registry_bundle(db, bundle, apply=True, searchable_name_assertions=[first])
    summary = service.persist_source_registry_bundle(db, bundle, apply=True, searchable_name_assertions=[second])

    assert summary["retired"]["SourceSearchableNameAssertion"] == 1
    assert db.query(SourceSearchableNameAssertion).filter_by(status="superseded").count() == 1
    assert db.query(SourceSearchableNameAssertion).filter_by(status="searchable_active").count() == 1


def test_refresh_with_no_current_assertion_drafts_retires_stale_assertions(db):
    original_bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:assertion-refresh-zero",
            "tags": ["Hero Name(Work Name)"],
        }
    ])
    candidates = runner.build_searchable_name_candidates(
        original_bundle,
        [
            {
                "provider": "pixiv",
                "provider_record_key": "pixiv:test:assertion-refresh-zero",
                "tags": ["Hero Name(Work Name)"],
                "data_type_label": runner.DATA_TYPE_REAL,
            }
        ],
        max_candidates=10,
    )
    candidate = next(row for row in candidates if row.source_kind == "source_tag_observation")
    validated = runner.validate_model_assertion_output(_valid_model_output(candidate), candidate)
    draft = runner.assertion_draft_from_model_output(candidate, validated, model_name="unit-model")
    service.persist_source_registry_bundle(db, original_bundle, apply=True, searchable_name_assertions=[draft])

    refreshed_bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:assertion-refresh-zero",
            "tags": [],
        }
    ])
    summary = service.persist_source_registry_bundle(db, refreshed_bundle, apply=True, searchable_name_assertions=[])

    assert summary["retired"]["SourceSearchableNameAssertion"] == 1
    assert db.query(SourceSearchableNameAssertion).filter_by(status="superseded").count() == 1
    assert db.query(SourceSearchableNameAssertion).filter_by(status="searchable_active").count() == 0


def test_refresh_retires_stale_alias_candidates(db):
    original_bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:alias-refresh",
            "tags": ["Alias Hero(Alias Work)"],
        }
    ])
    service.persist_source_registry_bundle(db, original_bundle, apply=True)

    assert db.query(SourceNameAliasCandidate).filter_by(status="candidate").count() == 1

    refreshed_bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:alias-refresh",
            "tags": [],
        }
    ])
    summary = service.persist_source_registry_bundle(db, refreshed_bundle, apply=True)

    assert summary["retired"]["SourceNameAliasCandidate"] == 1
    assert db.query(SourceNameAliasCandidate).filter_by(status="superseded").count() == 1
    assert db.query(SourceNameAliasCandidate).filter_by(status="candidate").count() == 0


def test_search_validation_from_assertions():
    candidate = _candidate()
    validated = runner.validate_model_assertion_output(_valid_model_output(candidate), candidate)
    draft = runner.assertion_draft_from_model_output(candidate, validated, model_name="unit-model")
    rows = runner.searchable_assertion_search_rows([draft], [candidate])
    summary = runner.assertion_search_validation_summary(rows)
    assert summary["positive_matched_count"] >= 1
    assert summary["false_positive_suspected_count"] == 0


def test_zero_unresolved_rate_satisfies_real_pixiv_assertion_target():
    candidates = [_candidate(f"Hero {index}(Work Name)") for index in range(runner.REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET)]
    assertions = []
    for candidate in candidates:
        validated = runner.validate_model_assertion_output(_valid_model_output(candidate), candidate)
        assertions.append(runner.assertion_draft_from_model_output(candidate, validated, model_name="unit-model"))

    summary = runner.source_searchable_assertion_coverage_summary(candidates, assertions)

    assert summary["real_pixiv_high_impact"]["coverage"] == 1.0
    assert summary["real_pixiv_high_impact"]["unresolved_rate"] == 0.0
    assert summary["real_pixiv_searchable_assertion_target_met"] is True


def test_assertion_coverage_uses_candidate_key_not_raw_input_tuple():
    tag_candidate = _candidate(
        "Shared Name",
        source_kind="source_tag_observation",
        candidate_key="candidate:shared:tag",
    )
    name_candidate = _candidate(
        "Shared Name",
        source_kind="source_name_observation",
        candidate_key="candidate:shared:name",
    )
    active = runner.assertion_draft_from_model_output(
        tag_candidate,
        runner.validate_model_assertion_output(_valid_model_output(tag_candidate), tag_candidate),
        model_name="unit-model",
    )
    rejected = runner.assertion_draft_from_model_output(
        name_candidate,
        runner.validate_model_assertion_output(
            _valid_model_output(
                name_candidate,
                is_searchable_identity=False,
                searchable_status="rejected",
                reason_code="known_character_name",
                asserted_role="character",
                extracted_name="Shared Name",
                base_name="Shared Name",
                work_context=None,
            ),
            name_candidate,
        ),
        model_name="unit-model",
    )

    summary = runner.source_searchable_assertion_coverage_summary(
        [tag_candidate, name_candidate],
        [active, rejected],
    )

    assert summary["real_pixiv_high_impact"]["candidate_count"] == 2
    assert summary["real_pixiv_high_impact"]["terminal_active_or_valid_rejected_count"] == 1
    assert summary["real_pixiv_high_impact"]["coverage"] == 0.5


def test_rejected_known_name_like_reason_does_not_count_as_terminal_coverage():
    candidate = _candidate("Hero Name")
    validated = runner.validate_model_assertion_output(
        _valid_model_output(
            candidate,
            is_searchable_identity=False,
            searchable_status="rejected",
            reason_code="known_character_name",
            asserted_role="character",
            extracted_name="Hero Name",
            base_name="Hero Name",
            work_context=None,
        ),
        candidate,
    )
    draft = runner.assertion_draft_from_model_output(candidate, validated, model_name="unit-model")

    summary = runner.source_searchable_assertion_coverage_summary([candidate], [draft])

    assert summary["real_pixiv_high_impact"]["terminal_active_or_valid_rejected_count"] == 0
    assert summary["real_pixiv_high_impact"]["coverage"] == 0.0


def test_saucenao_assertion_gate_uses_available_non_fixture_supply():
    candidates = [
        _candidate(
            f"Sauce Work {index}",
            provider="saucenao",
            role_hint="work_title",
            source_kind="source_name_observation",
        )
        for index in range(3)
    ]
    assertions = []
    for candidate in candidates:
        validated = runner.validate_model_assertion_output(
            _valid_model_output(
                candidate,
                asserted_role="work_title",
                extracted_name=candidate.raw_input,
                base_name=candidate.raw_input,
                work_context=None,
                reason_code="known_work_title",
            ),
            candidate,
        )
        assertions.append(runner.assertion_draft_from_model_output(candidate, validated, model_name="unit-model"))

    summary = runner.source_searchable_assertion_coverage_summary(candidates, assertions)

    assert summary["saucenao_requested_candidate_target"] == runner.SAUCENAO_ARTIFACT_RECORD_TARGET
    assert summary["saucenao_available_candidate_supply_target"] == 3
    assert summary["saucenao_candidate_supply_sufficient_for_available_data"] is True
    assert summary["saucenao_assertion_target_met"] is True


def test_saucenao_report_gate_uses_available_artifact_record_supply():
    records = [
        {
            "provider": "saucenao",
            "provider_record_key": f"saucenao:artifact:{index}",
            "work_or_copyright": f"Sauce Work {index}",
            "data_type_label": runner.DATA_TYPE_ARTIFACT,
        }
        for index in range(3)
    ]
    bundle = service.build_source_registry_bundle(records)
    summary = runner.build_public_summary(
        records=records,
        bundle=bundle,
        searchable_candidates=[],
        searchable_name_assertions=[],
        llm_classification_summary={"api_call_attempted": False, "mode": "test"},
        input_summary={
            "input_source": "test",
            "scale_up": {
                "existing_saucenao_artifact_record_count": 3,
                "real_pixiv_source_prior_summary": {"record_count": 0},
                "real_pixiv_metadata_enrichment_summary": {"metadata_rich_record_count": 0},
            },
        },
        curated_mapping_count=0,
        db_identity=None,
        db_write_summary={"apply": False, "guard_installed": False, "success": False},
        search_rows=[],
        assertion_search_rows=[],
    )

    expanded = summary["expanded_real_data_validation"]
    assert expanded["saucenao_real_or_artifact_requested_record_target"] == runner.SAUCENAO_ARTIFACT_RECORD_TARGET
    assert expanded["saucenao_real_or_artifact_available_record_count"] == 3
    assert expanded["saucenao_real_or_artifact_available_record_target"] == 3
    assert expanded["saucenao_real_or_artifact_records_meet_available_supply"] is True
    assert expanded["saucenao_real_or_artifact_requested_20_available"] is False


def test_no_db_llm_flag_does_not_call_api_or_write_db():
    args = runner.build_arg_parser().parse_args(["--no-db", "--use-llm-api"])
    bundle = service.build_source_registry_bundle(runner.default_provider_shape_records())
    candidates, assertions, summary, inputs, outputs, review_rows = runner.classify_source_searchable_name_assertions(
        args,
        bundle,
        runner.default_provider_shape_records(),
    )
    assert candidates
    assert assertions == []
    assert inputs == []
    assert outputs == []
    assert review_rows == []
    assert summary["api_call_attempted"] is False
    assert summary["mode"] == "api_skipped_no_db_or_dry_run"


def test_llm_chunk_format_failure_splits_and_recovers(monkeypatch):
    records = [
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:repair:1",
            "tags": ["Hero One(Work Name)"],
            "data_type_label": runner.DATA_TYPE_REAL,
        },
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:repair:2",
            "tags": ["Hero Two(Work Name)"],
            "data_type_label": runner.DATA_TYPE_REAL,
        },
    ]
    bundle = service.build_source_registry_bundle(records)
    args = runner.build_arg_parser().parse_args(
        ["--use-llm-api", "--source-assertion-chunk-size", "2", "--source-assertion-api-retries", "0"]
    )
    monkeypatch.setattr(
        runner,
        "source_assertion_provider_from_env",
        lambda: (
            _ChunkRepairFakeProvider(),
            {"model_label": "unit-fake", "llm_access_configured": True, "uses_fallback_provider": True},
        ),
    )

    _candidates, assertions, summary, inputs, outputs, review_rows = runner.classify_source_searchable_name_assertions(
        args,
        bundle,
        records,
    )

    assert assertions
    assert all(row.status == "searchable_active" for row in assertions)
    assert summary["chunk_split_recoveries"] >= 1
    assert summary["outputs_valid"] == len(assertions)
    assert summary["invalid_outputs"] == 0
    assert "split_after_invalid_output" in summary["repair_strategies_attempted"]
    assert inputs
    assert outputs
    assert all(row["validation_error"] is None for row in review_rows)


def test_llm_transient_transport_error_retries_without_aborting(monkeypatch):
    records = [
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:transport-retry",
            "tags": ["Hero Retry(Work Name)"],
            "data_type_label": runner.DATA_TYPE_REAL,
        }
    ]
    bundle = service.build_source_registry_bundle(records)
    args = runner.build_arg_parser().parse_args(
        ["--use-llm-api", "--source-assertion-chunk-size", "1", "--source-assertion-api-retries", "1"]
    )
    fake_provider = _TransientTransportFakeProvider()
    monkeypatch.setattr(
        runner,
        "source_assertion_provider_from_env",
        lambda: (
            fake_provider,
            {"model_label": "unit-fake", "llm_access_configured": True, "uses_fallback_provider": True},
        ),
    )

    _candidates, assertions, summary, _inputs, _outputs, review_rows = runner.classify_source_searchable_name_assertions(
        args,
        bundle,
        records,
    )

    assert fake_provider.calls >= 2
    assert assertions
    assert summary["chunk_retries"] == 1
    assert summary["outputs_valid"] == len(assertions)
    assert summary["invalid_outputs"] == 0
    assert all(row["validation_error"] is None for row in review_rows)


def test_persistent_single_candidate_invalid_output_downgrades_to_unresolved(monkeypatch):
    records = [
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:repair:invalid",
            "tags": ["Hero Broken(Work Name)"],
            "data_type_label": runner.DATA_TYPE_REAL,
        }
    ]
    bundle = service.build_source_registry_bundle(records)
    args = runner.build_arg_parser().parse_args(
        ["--use-llm-api", "--source-assertion-chunk-size", "1", "--source-assertion-api-retries", "0"]
    )
    monkeypatch.setattr(
        runner,
        "source_assertion_provider_from_env",
        lambda: (
            _AlwaysInvalidFakeProvider(),
            {"model_label": "unit-fake", "llm_access_configured": True, "uses_fallback_provider": True},
        ),
    )

    _candidates, assertions, summary, _inputs, _outputs, review_rows = runner.classify_source_searchable_name_assertions(
        args,
        bundle,
        records,
    )

    assert assertions
    assert all(row.status == "unresolved" for row in assertions)
    assert all(row.asserted_role == "unknown" for row in assertions)
    assert all(row.evidence_sources_json["reason_code"] == "model_output_invalid" for row in assertions)
    assert summary["outputs_valid"] == 0
    assert summary["invalid_outputs"] == len(assertions)
    assert summary["single_candidate_failures_downgraded"] == len(assertions)
    assert summary["unresolved"] == len(assertions)
    assert all(row["validation_error"].startswith("source_searchable_name_assertion_schema_invalid:") for row in review_rows)


def test_api_unavailable_reports_blocker(monkeypatch):
    for key in (
        "TAG_TRANSLATION_LLM_API_KEY",
        "OPENAI_API_KEY",
        "TAG_TRANSLATION_LLM_BASE_URL",
        "OPENAI_BASE_URL",
        "TAG_TRANSLATION_LLM_FALLBACK_ENABLED",
        "TAG_TRANSLATION_LLM_FALLBACK_PROVIDER",
        "TAG_TRANSLATION_LLM_FALLBACK_API_KEY",
        "TAG_TRANSLATION_LLM_FALLBACK_BASE_URL",
        "TAG_TRANSLATION_LLM_FALLBACK_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    args = runner.build_arg_parser().parse_args(["--use-llm-api", "--disable-scale-up"])
    bundle = service.build_source_registry_bundle([
        {
            "provider": "pixiv",
            "provider_record_key": "pixiv:test:api-unavailable",
            "tags": ["Hero Name(Work Name)"],
            "data_type_label": runner.DATA_TYPE_REAL,
        }
    ])
    with pytest.raises(runner.Phase44P2RF5Error, match="api_unavailable"):
        runner.classify_source_searchable_name_assertions(
            args,
            bundle,
            [
                {
                    "provider": "pixiv",
                    "provider_record_key": "pixiv:test:api-unavailable",
                    "tags": ["Hero Name(Work Name)"],
                    "data_type_label": runner.DATA_TYPE_REAL,
                }
            ],
        )


def test_f5_project_config_uses_process_host_override_when_db_service_unresolvable(monkeypatch):
    config = runner.f1.ProjectConfig(
        project_root=ROOT,
        violet_env="development",
        database_url=URL.create(
            drivername="postgresql",
            username="postgres",
            password="secret",
            host="db",
            port=5432,
            database="blombooru",
        ),
        db_user="postgres",
        db_password="secret",
        db_host="db",
        db_port=5432,
        db_name="blombooru",
        settings_source="settings_json",
        storage_root_mode="code_root_default",
        settings_file_exists=True,
        database_url_source="settings_env_or_default",
    )

    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setattr(runner.f1, "load_project_config", lambda _root: config)
    monkeypatch.setattr(runner, "_host_resolves", lambda host: False)

    resolved = runner.load_f5_project_config()

    assert resolved.db_host == "localhost"
    assert resolved.database_url.host == "localhost"
    assert resolved.database_url_source == "settings_env_or_default+process_host_override"
