"""Focused tests for the Phase 4.4-P2R-F4 Pixiv taxonomy / alias KB pilot."""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
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

from app.database import Base, migrate_add_pixiv_tag_taxonomy_alias_kb  # noqa: E402
from app.models import (  # noqa: E402
    EntityEvidence,
    ExternalTagCategoryLookupCache,
    MediaEntityCandidate,
    PixivTagAliasKnowledgeBase,
    PixivTagTaxonomyKnowledgeBase,
)
from scripts import run_phase44p2r_f4_pixiv_taxonomy_alias_kb_pilot as pilot  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _record(**overrides):
    values = {
        "work_id": "100000001",
        "page_index": 0,
        "page_count": 1,
        "title": "Private title",
        "artist_name": "Private artist",
        "artist_id": "200",
        "tags": ("　女の子　", "모나（原神）", "Blue Archive", "mystery_tag"),
        "metadata_richness": "rich_structured_metadata",
        "local_match_status": "metadata_matches_eligible_anime_local_prior",
        "local_match_content_class": "anime",
        "local_match_content_class_approved": True,
        "local_media_id_private": 1,
        "eligible_for_future_local_source_hint": True,
        "eligible_for_future_entity_candidate": True,
    }
    values.update(overrides)
    return pilot.f2.PixivGalleryDlAdapterRecord(**values)


def test_pixiv_taxonomy_alias_kb_migration_creates_additive_tables():
    engine = create_engine("sqlite://")
    try:
        migrate_add_pixiv_tag_taxonomy_alias_kb(engine, inspect(engine))
        inspector = inspect(engine)
        assert "blombooru_pixiv_tag_taxonomy_kb" in inspector.get_table_names()
        assert "blombooru_pixiv_tag_alias_kb" in inspector.get_table_names()
        taxonomy_columns = {column["name"] for column in inspector.get_columns("blombooru_pixiv_tag_taxonomy_kb")}
        alias_columns = {column["name"] for column in inspector.get_columns("blombooru_pixiv_tag_alias_kb")}
        assert {
            "raw_tag",
            "normalized_tag",
            "canonical_key",
            "candidate_namespace",
            "unresolved_reason",
            "next_action",
            "manual_override_status",
        }.issubset(taxonomy_columns)
        assert {
            "source_tag",
            "target_tag",
            "relation_type",
            "evidence_source",
            "manual_override_status",
        }.issubset(alias_columns)
    finally:
        engine.dispose()


def test_f4_write_guard_allows_only_kb_and_cache_tables():
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        pilot.install_taxonomy_alias_kb_write_guard(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO blombooru_pixiv_tag_taxonomy_kb "
                    "(raw_tag, normalized_tag, canonical_key, source_scope, candidate_namespace, status) "
                    "VALUES ('x', 'x', 'x', 'pixiv_raw_tag_v1', 'unknown', 'unresolved')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO blombooru_pixiv_tag_alias_kb "
                    "(source_tag, source_canonical_key, target_tag, target_canonical_key, relation_type, evidence_source) "
                    "VALUES ('x', 'x', 'y', 'y', 'alias', 'test')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO blombooru_external_tag_category_lookup_cache "
                    "(raw_tag, normalized_tag, canonical_lookup_key, lookup_source, status) "
                    "VALUES ('x', 'x', 'x', 'danbooru_tags_api_v2', 'not_found')"
                )
            )
        with pytest.raises(pilot.f1.ReadOnlyViolation):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO blombooru_media_tags (media_id, tag_id) VALUES (1, 1)"))
        with pytest.raises(pilot.f1.ReadOnlyViolation):
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM blombooru_pixiv_tag_taxonomy_kb WHERE canonical_key = 'x'"))
    finally:
        engine.dispose()


def test_pr90_cache_rows_are_reusable_without_network(db):
    db.add(
        ExternalTagCategoryLookupCache(
            raw_tag="Blue Archive",
            normalized_tag="Blue Archive",
            canonical_lookup_key="blue_archive",
            lookup_source=pilot.f3.DANBOORU_TAGS_SOURCE,
            lookup_source_version=pilot.f3.DANBOORU_TAGS_SOURCE,
            source_tag_id="42",
            source_tag_name="blue_archive",
            source_category_raw="3",
            mapped_candidate_namespace="copyright",
            confidence=0.84,
            provenance_url_or_key="https://danbooru.donmai.us/tags/42",
            status="hit",
        )
    )
    db.commit()

    def fail_fetcher(_key, _timeout):
        raise AssertionError("cache hit should avoid network")

    results, summary = pilot.lookup_external_tag_categories_f4(
        [_record(tags=("Blue Archive",), artist_name=None, artist_id=None)],
        session=db,
        lookup_limit=10,
        max_external_requests=10,
        delay_seconds=0,
        timeout_seconds=1,
        cache_writes_enabled=True,
        lookup_sources=(pilot.f3.DANBOORU_TAGS_SOURCE,),
        fetcher=fail_fetcher,
    )

    assert summary.cache_hit_count == 1
    assert summary.request_count == 0
    assert results["blue_archive"].mapped_candidate_namespace == "copyright"


def test_cache_keys_are_source_aware(db):
    for source, namespace in ((pilot.f3.DANBOORU_TAGS_SOURCE, "copyright"), (pilot.GELBOORU_TAGS_SOURCE, "character")):
        db.add(
            ExternalTagCategoryLookupCache(
                raw_tag="same",
                normalized_tag="same",
                canonical_lookup_key="same",
                lookup_source=source,
                mapped_candidate_namespace=namespace,
                status="hit",
            )
        )
    db.commit()

    rows = db.query(ExternalTagCategoryLookupCache).filter_by(canonical_lookup_key="same").all()
    assert {row.lookup_source for row in rows} == {pilot.f3.DANBOORU_TAGS_SOURCE, pilot.GELBOORU_TAGS_SOURCE}


def test_taxonomy_manual_override_survives_refresh(db):
    db.add(
        PixivTagTaxonomyKnowledgeBase(
            raw_tag="x",
            normalized_tag="x",
            canonical_key="x",
            source_scope=pilot.SOURCE_SCOPE,
            candidate_namespace="character",
            status="resolved_manual_override",
            manual_override_status="operator_curated",
            manual_override_value="character",
        )
    )
    db.commit()
    entry = pilot.TaxonomyKBEntry(
        raw_tag_private="x",
        normalized_tag="x",
        canonical_key="x",
        candidate_namespace="general",
        confidence=0.1,
        status="resolved",
        source_summary={"source_kinds": ["provider"]},
        frequency=1,
        high_value_score=1,
        language_script_hints={},
    )

    inserts, updates = pilot.upsert_taxonomy_entries(db, [entry])
    db.commit()
    row = db.query(PixivTagTaxonomyKnowledgeBase).one()
    assert inserts == 0
    assert updates == 1
    assert row.manual_override_status == "operator_curated"
    assert row.manual_override_value == "character"
    assert row.candidate_namespace == "character"


def test_negative_error_ttl_retry_suppresses_same_provider_request(db):
    db.add(
        ExternalTagCategoryLookupCache(
            raw_tag="cooldown",
            normalized_tag="cooldown",
            canonical_lookup_key="cooldown",
            lookup_source=pilot.f3.DANBOORU_TAGS_SOURCE,
            status="lookup_error",
            lookup_error="provider_blocked",
            retry_after=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db.commit()

    def fail_fetcher(_key, _timeout):
        raise AssertionError("future retry_after should suppress requery")

    results, summary = pilot.lookup_external_tag_categories_f4(
        [_record(tags=("cooldown",), artist_name=None, artist_id=None)],
        session=db,
        lookup_limit=10,
        max_external_requests=10,
        delay_seconds=0,
        timeout_seconds=1,
        cache_writes_enabled=True,
        lookup_sources=(pilot.f3.DANBOORU_TAGS_SOURCE,),
        fetcher=fail_fetcher,
    )

    assert summary.cache_error_cooldown_count == 1
    assert summary.request_count == 0
    assert results["cooldown"].cache_status == "error_cooldown"


def test_multilingual_normalization_and_parenthetical_parser():
    profile = pilot.f3.multilingual_normalize_tag("　모나／Mona（原神）")
    assert "mona" in "_".join(profile.lookup_candidates)
    assert profile.punctuation_normalized_form == "모나/Mona(原神)"
    pattern = pilot.f3.parse_pixiv_parenthetical_tag("モナ（原神）")
    assert pattern is not None
    assert pattern.outer_name == "モナ"
    assert pattern.inner_work_or_context == "原神"


def test_alias_kb_relation_creation_from_parenthetical_and_cooccurrence():
    records = [
        _record(work_id="1", tags=("モナ（原神）", "unknown_alias", "原神"), artist_name=None, artist_id=None),
        _record(work_id="2", tags=("unknown_alias", "原神"), artist_name=None, artist_id=None),
    ]
    lookup = {
        "原神": pilot.f3.ExternalTagLookupResult(
            raw_tag="原神",
            normalized_tag="原神",
            canonical_lookup_key="原神",
            lookup_source=pilot.f3.DANBOORU_TAGS_SOURCE,
            lookup_source_version=pilot.f3.DANBOORU_TAGS_SOURCE,
            source_tag_id="10",
            source_tag_name="genshin_impact",
            source_category_raw="3",
            mapped_candidate_namespace="copyright",
            confidence=0.84,
            provenance_url_or_key="provider:10",
            status="hit",
            cache_status="miss",
        )
    }
    local_index = pilot.f3.LocalClassificationIndex()
    _normalized, rows = pilot.f3.normalize_metadata_candidates(records, local_index, external_lookup_results=lookup)
    aliases = pilot.build_alias_entries(records, rows, lookup, {})
    relation_types = {entry.relation_type for entry in aliases}
    assert "parenthetical_character_of_work" in relation_types
    assert "cooccurrence_candidate" in relation_types


def test_curated_mapping_import_path(tmp_path):
    path = ROOT / ".local_manifests" / "phase-4.4p2r-f4-pixiv-taxonomy-alias-kb" / "test-curated.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["source_tag", "candidate_namespace", "confidence", "target_tag", "relation_type", "notes"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "source_tag": "手動タグ",
                    "candidate_namespace": "character",
                    "confidence": "0.97",
                    "target_tag": "manual_tag",
                    "relation_type": "alias",
                    "notes": "operator supplied",
                }
            )
        mappings = pilot.load_curated_mappings(path)
        key = pilot.f3.multilingual_normalize_tag("手動タグ").canonical_lookup_key
        assert mappings[key].candidate_namespace == "character"
        assert mappings[key].target_tag == "manual_tag"
    finally:
        path.unlink(missing_ok=True)


def test_no_truth_table_writes_when_kb_entries_are_written(db):
    taxonomy = pilot.TaxonomyKBEntry(
        raw_tag_private="x",
        normalized_tag="x",
        canonical_key="x",
        candidate_namespace="unknown",
        confidence=0.0,
        status="unresolved_governed",
        source_summary={"source_kinds": []},
        frequency=1,
        high_value_score=1,
        language_script_hints={},
        unresolved_reason="provider_not_found",
        next_action="curated_mapping_or_new_source_required",
    )
    alias = pilot.AliasKBEntry(
        source_tag_private="x",
        source_canonical_key="x",
        target_tag_private="y",
        target_canonical_key="y",
        relation_type="alias",
        evidence_source="test",
        evidence_payload={},
        confidence=0.5,
    )

    summary = pilot.write_kb_entries(db, [taxonomy], [alias], external_cache_write_count=0)

    assert summary.taxonomy_insert_count == 1
    assert summary.alias_insert_count == 1
    assert db.query(PixivTagTaxonomyKnowledgeBase).count() == 1
    assert db.query(PixivTagAliasKnowledgeBase).count() == 1
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


def test_dry_run_no_db_and_skip_network_have_no_side_effects(monkeypatch):
    writes: list[tuple[str, object]] = []
    monkeypatch.setattr(pilot, "write_public_json", lambda path, payload: writes.append(("public_json", path)))
    monkeypatch.setattr(pilot, "write_public_text", lambda path, content, private_markers: writes.append(("public_text", path)))
    monkeypatch.setattr(pilot, "write_private_json", lambda path, payload: writes.append(("private_json", path)))
    monkeypatch.setattr(pilot, "write_private_text", lambda path, content: writes.append(("private_text", path)))

    def fail_probe(_command):
        raise AssertionError("dry-run/skip-network must not probe gallery-dl")

    monkeypatch.setattr(pilot.f3, "probe_gallery_dl_entrypoint", fail_probe)
    args = pilot.build_arg_parser().parse_args(["--dry-run", "--skip-network", "--no-db"])
    result = pilot.run(args)

    assert result["summary"]["kb_write_summary"]["kb_migration_ran"] is False
    assert result["summary"]["automated_tag_category_lookup"]["request_count"] == 0
    assert result["summary"]["input_summary"]["metadata_command_count"] == 0
    assert any(kind == "public_json" for kind, _path in writes)


def test_request_budget_is_enforced():
    calls: list[str] = []

    def fake_fetcher(key, _timeout):
        calls.append(key)
        return [{"id": len(calls), "name": key, "category": 0}]

    _results, summary = pilot.lookup_external_tag_categories_f4(
        [_record(tags=("tag_a", "tag_b", "tag_c"), artist_name=None, artist_id=None)],
        session=None,
        lookup_limit=3,
        max_external_requests=2,
        delay_seconds=0,
        timeout_seconds=1,
        cache_writes_enabled=False,
        lookup_sources=(pilot.f3.DANBOORU_TAGS_SOURCE,),
        fetcher=fake_fetcher,
    )

    assert summary.request_count == 2
    assert summary.request_budget_exhausted is True
    assert calls == ["tag_a", "tag_b"]


def test_provider_block_stops_same_provider_requests(monkeypatch):
    calls: list[str] = []

    def blocked_fetch(key, *, timeout):
        _ = timeout
        calls.append(key)
        raise pilot.f3.ExternalLookupProviderBlocked("gelbooru_lookup_blocked_http_401")

    monkeypatch.setattr(pilot, "fetch_gelbooru_tag_payload", blocked_fetch)
    _results, summary = pilot.lookup_external_tag_categories_f4(
        [_record(tags=("tag_a", "tag_b"), artist_name=None, artist_id=None)],
        session=None,
        lookup_limit=2,
        max_external_requests=10,
        delay_seconds=0,
        timeout_seconds=1,
        cache_writes_enabled=False,
        lookup_sources=(pilot.GELBOORU_TAGS_SOURCE,),
    )

    assert calls == ["tag_a"]
    assert summary.provider_blocked is True
    assert summary.provider_blocked_sources == [pilot.GELBOORU_TAGS_SOURCE]


def test_public_report_redacts_raw_private_markers():
    entry = pilot.TaxonomyKBEntry(
        raw_tag_private="PrivateRawTag",
        normalized_tag="PrivateRawTag",
        canonical_key="private_raw_tag",
        candidate_namespace="unknown",
        confidence=0.0,
        status="unresolved_governed",
        source_summary={"source_kinds": []},
        frequency=1,
        high_value_score=1,
        language_script_hints={},
        unresolved_reason="provider_not_found",
        next_action="curated_mapping_or_new_source_required",
    )
    coverage = pilot.coverage_target_summary([entry], [])
    summary = pilot.build_public_summary(
        generated_at="2026-06-02T00:00:00+00:00",
        pr90_confirmation={"number": 90, "state": "MERGED"},
        pr90_summary={},
        pr90_private_artifacts={},
        git_context={},
        db_identity=None,
        sample_public={},
        command_public={},
        input_summary={},
        lookup_summary={},
        taxonomy_entries=[entry],
        alias_entries=[],
        kb_write_summary={},
        coverage=coverage,
        recommendation=pilot.recommendation_from_coverage(coverage),
        curated_mapping_count=0,
    )
    report = pilot.build_markdown_report(summary, private_markers=["PrivateRawTag"])
    assert "PrivateRawTag" not in report
    assert summary["public_report_redaction"]["contains_raw_pixiv_tags"] is False


def test_target_status_and_unresolved_reason_bucketing():
    entries = [
        pilot.TaxonomyKBEntry(
            raw_tag_private=f"未解決{i}",
            normalized_tag=f"未解決{i}",
            canonical_key=f"unresolved_{i}",
            candidate_namespace="ambiguous",
            confidence=0,
            status="unresolved_governed",
            source_summary={"source_kinds": []},
            frequency=1,
            high_value_score=1.0,
            language_script_hints={"has_han": True},
            unresolved_reason="language_alias_mismatch_or_pixiv_only_tag",
            next_action="curated_mapping_or_add_multilingual_alias_source",
        )
        for i in range(50)
    ]
    coverage = pilot.coverage_target_summary(entries, [])
    taxonomy_summary = pilot.taxonomy_kb_summary(entries)

    assert coverage["target_status"] == "classification_not_reached_top_high_impact_governed"
    assert coverage["target_reached"] is False
    assert coverage["classification_target_reached"] is False
    assert coverage["governance_target_reached"] is True
    assert coverage["top_high_impact_governance_rate"] == 1.0
    assert taxonomy_summary["unresolved_reason_buckets"] == {
        "language_alias_mismatch_or_pixiv_only_tag": 50
    }
