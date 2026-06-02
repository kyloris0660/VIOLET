"""Focused tests for Phase 4.4-P2R-F5 source name registry foundation."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
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
    MediaEntityAssignment,
    MediaEntityCandidate,
    ProviderCache,
    SourceMetadataRecord,
    SourceNameAliasCandidate,
    SourceNameObservation,
    SourceNameRegistry,
    SourceTagObservation,
    TagTranslation,
    blombooru_media_tags,
)
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
    assert bundle.record_statuses["pixiv:test:1"] == "applicable_name_signal_covered"


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


def test_danbooru_character_tag_becomes_source_name_observation():
    bundle = service.build_source_registry_bundle([
        {
            "provider": "danbooru",
            "provider_record_key": "danbooru:test:1",
            "tags": [{"name": "Dan Character", "category": "character"}],
        }
    ])
    assert any(row.raw_name == "Dan Character" and row.name_role == "character" for row in bundle.name_observations)


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
    assert bundle.record_statuses["none:test:1"] == "not_applicable_no_person_signal"


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


def test_no_forbidden_truth_table_writes_when_source_registry_is_persisted(db):
    bundle = service.build_source_registry_bundle(runner.default_provider_shape_records())
    summary = service.persist_source_registry_bundle(db, bundle, apply=True)

    assert summary["forbidden_truth_table_write_count"] == 0
    assert db.query(SourceMetadataRecord).count() == len(bundle.metadata_records)
    assert db.query(SourceNameRegistry).count() == len(bundle.name_registry)
    assert db.query(SourceNameAliasCandidate).count() == len(bundle.alias_candidates)
    assert db.query(Entity).count() == 0
    assert db.query(EntityAlias).count() == 0
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0
    assert db.query(MediaEntityAssignment).count() == 0
    assert db.query(ProviderCache).count() == 0
    assert db.query(TagTranslation).count() == 0
    assert db.execute(text(f"SELECT COUNT(*) FROM {blombooru_media_tags.name}")).scalar() == 0


def test_dry_run_no_db_writes(db):
    bundle = service.build_source_registry_bundle(runner.default_provider_shape_records())
    summary = service.persist_source_registry_bundle(db, bundle, apply=False)
    assert summary["apply"] is False
    assert db.query(SourceMetadataRecord).count() == 0
    assert db.query(SourceNameObservation).count() == 0


def test_no_db_mode_no_db_writes():
    args = runner.build_arg_parser().parse_args(["--no-db"])
    bundle = service.build_source_registry_bundle(runner.default_provider_shape_records())
    _identity, summary = runner.db_apply_summary(args, bundle)
    assert summary["apply"] is False
    assert summary["guard_installed"] is False


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
        input_summary={"input_source": "test"},
        curated_mapping_count=0,
        db_identity=None,
        db_write_summary={"apply": False, "guard_installed": False},
        search_rows=search_rows,
    )
    report = runner.build_markdown_report(summary, private_markers=runner.private_markers(bundle, records))
    assert "Creator Alpha" not in report
    assert "Character Alpha" not in report
    assert summary["public_report_redaction"]["raw_names_and_aliases_private_only"] is True
