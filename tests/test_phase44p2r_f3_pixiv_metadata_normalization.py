"""Focused tests for the Phase 4.4-P2R-F3 Pixiv metadata normalization pilot."""

from __future__ import annotations

import json
import shutil
import subprocess
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

from app.database import Base, migrate_add_external_tag_category_lookup_cache  # noqa: E402
from app.enums import (  # noqa: E402
    EntityExternalIdentityStatusEnum,
    EntityMetadataSourceEnum,
    EntityStatusEnum,
    EntityTypeEnum,
    TagCategoryEnum,
)
from app.models import (  # noqa: E402
    Entity,
    EntityAlias,
    EntityEvidence,
    EntityExternalIdentity,
    ExternalTagCategoryLookupCache,
    MediaEntityCandidate,
    Tag,
)
from scripts import run_phase44p2r_f3_pixiv_metadata_normalization_pilot as pilot  # noqa: E402


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


def _entrypoint(mode: str = "explicit_operator_command_mode") -> pilot.GalleryDlEntrypoint:
    return pilot.GalleryDlEntrypoint(
        mode=mode,
        command=("py", "-m", "gallery_dl"),
        version="1.32.1",
        available=True,
        reproducibility_status="conditional_explicit_operator_command",
    )


def _completed(args, returncode=0, stdout="1.32.1\n", stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def _record(**overrides):
    values = {
        "work_id": "100000001",
        "page_index": 0,
        "page_count": 1,
        "title": "Private title",
        "artist_name": "Private artist",
        "artist_id": "200",
        "tags": ("　女の子　", "R-18", "Blue Archive", "mystery_tag"),
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


def test_raw_pixiv_tags_and_unicode_are_preserved():
    record = _record(tags=("　女の子　", "ＡＢＣ", "謎タグ"))
    normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())

    assert normalized[0].raw_pixiv_tags == ("　女の子　", "ＡＢＣ", "謎タグ")
    assert normalized[0].normalized_unicode_tags == ("女の子", "ABC", "謎タグ")
    assert any(row.raw_tag == "　女の子　" and row.normalized_tag == "女の子" for row in rows)
    assert all(row.db_write_allowed is False for row in rows)


def test_pixiv_user_metadata_becomes_high_confidence_artist_candidate():
    record = _record(artist_name="Artist Name", artist_id="12345", tags=())
    normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())

    artist_rows = [row for row in rows if row.reason == "pixiv_user_metadata"]
    assert len(artist_rows) == 1
    assert artist_rows[0].candidate_namespace == "artist"
    assert artist_rows[0].confidence >= 0.9
    assert artist_rows[0].future_entity_candidate_eligible is True
    assert normalized[0].entity_candidates[0]["reason"] == "pixiv_user_metadata"


def test_raw_tags_are_not_forced_into_entity_candidates():
    record = _record(tags=("mystery_tag", "another_unknown"), artist_name=None, artist_id=None)
    _normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())

    assert rows
    assert all(row.candidate_kind != "entity_candidate" for row in rows)
    assert {row.candidate_kind for row in rows} == {"unknown_or_unresolved_pixiv_tag"}


def test_original_no_series_work_does_not_force_copyright():
    record = _record(tags=("オリジナル", "女の子"), artist_name=None, artist_id=None)
    normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())

    assert normalized[0].original_work_status == "original_work_context_claimed_by_pixiv_tag"
    assert not any(row.candidate_namespace == "copyright" for row in rows)
    assert any(row.candidate_kind == "original_work_context" for row in rows)


def test_existing_entity_and_alias_match_are_read_only_entity_candidates(db):
    entity = Entity(
        type=EntityTypeEnum.work,
        canonical_name="Blue Archive",
        normalized_key="blue_archive",
        status=EntityStatusEnum.active,
    )
    db.add(entity)
    db.flush()
    db.add(
        EntityAlias(
            entity_id=entity.id,
            alias="ブルーアーカイブ",
            normalized_alias="ブルーアーカイブ",
            source=EntityMetadataSourceEnum.manual,
            needs_review=False,
        )
    )
    db.commit()
    index = pilot.build_local_classification_index(db)
    record = _record(tags=("Blue Archive", "ブルーアーカイブ"), artist_name=None, artist_id=None)

    _normalized, rows = pilot.normalize_metadata_candidates([record], index)

    entity_rows = [row for row in rows if row.candidate_kind == "entity_candidate"]
    assert len(entity_rows) == 2
    assert {row.candidate_namespace for row in entity_rows} == {"copyright"}
    assert any(row.existing_alias_match for row in entity_rows)
    alias_groups = [row for row in rows if row.candidate_kind == "candidate_alias_group"]
    assert len(alias_groups) == 1
    assert alias_groups[0].db_write_allowed is False


def test_existing_tag_category_classifies_without_entity_creation(db):
    db.add(Tag(name="1girl", category=TagCategoryEnum.general))
    db.add(Tag(name="sample_character", category=TagCategoryEnum.character))
    db.commit()
    index = pilot.build_local_classification_index(db)
    record = _record(tags=("1girl", "sample_character"), artist_name=None, artist_id=None)

    _normalized, rows = pilot.normalize_metadata_candidates([record], index)

    general = next(row for row in rows if row.normalized_tag == "1girl")
    character = next(row for row in rows if row.normalized_tag == "sample_character")
    assert general.candidate_kind == "tag_candidate"
    assert general.candidate_namespace == "general"
    assert character.candidate_kind == "entity_candidate"
    assert character.candidate_namespace == "character"
    assert character.existing_entity_match is False


def test_fallback_classifies_ambiguous_general_sensitive_and_unknown():
    record = _record(tags=("Blue Archive", "女の子", "R-18", "mystery_tag"), artist_name=None, artist_id=None)
    _normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())
    by_tag = {row.normalized_tag: row for row in rows}

    assert by_tag["Blue Archive"].candidate_kind == "ambiguous_proper_noun_candidate"
    assert by_tag["女の子"].candidate_namespace == "general"
    assert by_tag["R-18"].candidate_kind == "sensitive_or_meta_tag_candidate"
    assert by_tag["mystery_tag"].candidate_kind == "unknown_or_unresolved_pixiv_tag"
    assert all(row.reason for row in rows)
    assert all(row.confidence > 0 for row in rows)


def test_sample_and_record_caps_fail_closed():
    pilot.enforce_sample_size(50)
    pilot.enforce_record_count(200, 200)
    with pytest.raises(pilot.SampleGateError, match="sample_or_record_cap_exceeded"):
        pilot.enforce_sample_size(51)
    with pytest.raises(pilot.SampleGateError, match="sample_or_record_cap_exceeded"):
        pilot.enforce_record_count(201, 200)
    with pytest.raises(pilot.SampleGateError, match="sample_or_record_cap_exceeded"):
        pilot.enforce_record_count(0, 201)


def test_record_cap_blocks_before_accepted_raw_write():
    raw_dir = pilot.PHASE_OUTPUT_DIR / "raw-unit-test-cap-block"
    raw_root = ROOT / raw_dir
    shutil.rmtree(raw_root, ignore_errors=True)
    sample = pilot.SelectedSample(
        work_id="100000025",
        page_indexes=(0,),
        content_classes=("anime",),
        local_media_ids_private=(25,),
        local_basenames_private=("100000025_p0.jpg",),
        has_p0_page=True,
        has_non_p0_page=False,
        duplicate_or_ambiguous=False,
    )

    def fake_run(args, **kwargs):
        return _completed(
            args,
            stdout="\n".join(
                [
                    json.dumps([3, {"id": 100000025, "num": 0, "filename": "100000025_p0"}]),
                    json.dumps([3, {"id": 100000025, "num": 1, "filename": "100000025_p1"}]),
                ]
            ),
        )

    try:
        results, parse_result = pilot.run_metadata_commands([sample], _entrypoint(), raw_dir, max_records=1, runner=fake_run)
        assert results[0].success is False
        assert results[0].blocked_over_limit is True
        assert results[0].error_class == "sample_or_record_cap_exceeded"
        assert len(parse_result.media_records) == 0
        assert not (raw_root / "metadata-01.jsonl").exists()
    finally:
        shutil.rmtree(raw_root, ignore_errors=True)


def test_bearer_authorization_redaction_redacts_token():
    redacted = pilot.redact_text(
        "Authorization: Bearer abcdefghijklmnop.1234567890\ncookie=session-secret",
        private_markers=["session-secret"],
    )

    assert "abcdefghijklmnop" not in redacted
    assert "1234567890" not in redacted
    assert "session-secret" not in redacted
    assert "Bearer [REDACTED]" in redacted


def test_public_report_redacts_exact_ids_paths_and_private_markers(tmp_path):
    record = _record(work_id="100000003", artist_name="Private Artist", artist_id="100", tags=("mystery_tag",))
    normalized, rows = pilot.normalize_metadata_candidates([record], pilot.LocalClassificationIndex())
    media_summaries = pilot.build_media_summaries(normalized, rows)
    parse_result = pilot.f1.ParseResult(records=[], files=[])
    summary = pilot.build_public_summary(
        generated_at="2026-06-01T00:00:00+00:00",
        pr_context={"state": "MERGED", "url": "https://github.com/kyloris0660/VIOLET/pull/89"},
        git_context={"branch": "test"},
        entrypoint=_entrypoint(),
        db_identity={"configured_db_host": "localhost", "db_sensitive_value_included": False},
        sample_public={"selected_count": 1, "exact_work_ids_public": False},
        parse_result=parse_result,
        records=[record],
        normalized_records=normalized,
        candidate_rows=rows,
        media_summaries=media_summaries,
        join_summary={"status_counts": {}, "page_index_status_counts": {}},
        command_public={"metadata_command_count": 1},
        raw_scope={"raw_input_scope": "current_run_only", "current_run_raw_file_count": 1, "stale_raw_files_ignored_count": 0},
        local_index_summary=pilot.LocalClassificationIndex().public_summary(),
        lookup_summary=pilot.ExternalLookupSummary(unique_normalized_tag_count=1).public_dict(),
        db_cache_summary=pilot.tag_category_lookup_cache_summary(
            db_enabled=True,
            table_available=True,
            cache_writes_enabled=True,
            lookup_summary=pilot.ExternalLookupSummary(unique_normalized_tag_count=1).public_dict(),
        ),
        containment={"output_path_violation": False},
        manual_review_guide=pilot.PRIVATE_MANUAL_REVIEW_GUIDE,
    )
    report = pilot.build_markdown_report(summary, private_markers=["100000003", str(tmp_path)])

    assert "100000003" not in json.dumps(summary, ensure_ascii=False)
    assert "100000003" not in report
    assert str(tmp_path) not in report


def test_private_artifact_paths_must_stay_under_local_manifests(tmp_path):
    containment = pilot.output_containment_summary(
        pilot.PHASE_OUTPUT_DIR,
        private_paths=[
            pilot.PRIVATE_DETAILS_JSON,
            pilot.PRIVATE_MEDIA_SUMMARY_CSV,
            pilot.PRIVATE_TAG_CANDIDATES_CSV,
            pilot.PRIVATE_ENTITY_CANDIDATES_CSV,
            pilot.PRIVATE_LOOKUP_CACHE_CSV,
            pilot.PRIVATE_MANUAL_REVIEW_GUIDE,
            pilot.PRIVATE_RAW_DIR,
        ],
    )
    assert containment["private_artifacts_under_phase_root"] is True
    with pytest.raises(pilot.OutputPathError, match="f3_output_path_violation"):
        pilot.output_containment_summary(pilot.PHASE_OUTPUT_DIR, private_paths=[tmp_path / "outside.json"])


def test_danbooru_category_mapping_covers_expected_namespaces():
    assert pilot.map_danbooru_category_to_namespace(0) == "general"
    assert pilot.map_danbooru_category_to_namespace(1) == "artist"
    assert pilot.map_danbooru_category_to_namespace(3) == "copyright"
    assert pilot.map_danbooru_category_to_namespace(4) == "character"
    assert pilot.map_danbooru_category_to_namespace(5) == "meta"
    assert pilot.map_danbooru_category_to_namespace("unexpected") == "unknown"


def test_external_identity_lookup_is_provider_namespaced(db):
    work = Entity(
        type=EntityTypeEnum.work,
        canonical_name="Provider foreign work",
        normalized_key="provider_foreign_work",
        status=EntityStatusEnum.active,
    )
    artist = Entity(
        type=EntityTypeEnum.artist,
        canonical_name="Pixiv artist",
        normalized_key="pixiv_artist",
        status=EntityStatusEnum.active,
    )
    db.add_all([work, artist])
    db.flush()
    db.add(
        EntityExternalIdentity(
            entity_id=work.id,
            provider="danbooru",
            external_id="12345",
            identity_status=EntityExternalIdentityStatusEnum.verified,
        )
    )
    db.add(
        EntityExternalIdentity(
            entity_id=artist.id,
            provider="pixiv",
            external_id="67890",
            identity_status=EntityExternalIdentityStatusEnum.verified,
        )
    )
    db.commit()

    index = pilot.build_local_classification_index(db)
    raw_numeric_record = _record(tags=("12345",), artist_name=None, artist_id=None)
    _normalized, raw_rows = pilot.normalize_metadata_candidates([raw_numeric_record], index)

    assert raw_rows[0].candidate_kind == "unknown_or_unresolved_pixiv_tag"
    assert raw_rows[0].existing_entity_match is False

    artist_record = _record(tags=(), artist_name="Pixiv artist", artist_id="67890")
    _normalized, artist_rows = pilot.normalize_metadata_candidates([artist_record], index)
    artist_row = artist_rows[0]
    assert artist_row.reason == "pixiv_user_metadata_verified_local_pixiv_identity"
    assert artist_row.existing_entity_match is True
    assert artist_row.existing_entity_id_private == artist.id


def test_external_lookup_cache_hit_avoids_network_and_classifies(db):
    db.add(
        ExternalTagCategoryLookupCache(
            raw_tag="hakurei_reimu",
            normalized_tag="hakurei_reimu",
            lookup_source=pilot.EXTERNAL_TAG_LOOKUP_SOURCE,
            source_tag_id="1",
            source_tag_name="hakurei_reimu",
            source_category_raw="4",
            mapped_candidate_namespace="character",
            confidence=0.84,
            provenance_url_or_key="https://danbooru.donmai.us/tags/1",
            status="hit",
        )
    )
    db.commit()

    def fail_fetcher(_key, _timeout):
        raise AssertionError("network should not be called for cache hit")

    record = _record(tags=("hakurei_reimu",), artist_name=None, artist_id=None)
    lookup_results, summary = pilot.lookup_external_tag_categories(
        [record],
        session=db,
        lookup_limit=10,
        delay_seconds=0,
        timeout_seconds=1,
        cache_writes_enabled=True,
        fetcher=fail_fetcher,
    )
    _normalized, rows = pilot.normalize_metadata_candidates(
        [record],
        pilot.LocalClassificationIndex(),
        external_lookup_results=lookup_results,
    )

    assert summary.cache_hit_count == 1
    assert summary.request_count == 0
    assert rows[0].candidate_namespace == "character"
    assert rows[0].reason == "external_tag_category_lookup"
    assert rows[0].cache_status == "hit"


def test_external_lookup_cache_miss_fetches_and_writes_cache(db):
    calls = []

    def fake_fetcher(key, _timeout):
        calls.append(key)
        return [{"id": 2, "name": key, "category": 3}]

    record = _record(tags=("blue_archive",), artist_name=None, artist_id=None)
    lookup_results, summary = pilot.lookup_external_tag_categories(
        [record],
        session=db,
        lookup_limit=10,
        delay_seconds=0,
        timeout_seconds=1,
        cache_writes_enabled=True,
        fetcher=fake_fetcher,
    )

    cached = db.query(ExternalTagCategoryLookupCache).one()
    assert calls == ["blue_archive"]
    assert summary.cache_miss_count == 1
    assert summary.request_count == 1
    assert summary.cache_write_count == 1
    assert lookup_results["blue_archive"].mapped_candidate_namespace == "copyright"
    assert cached.lookup_source == pilot.EXTERNAL_TAG_LOOKUP_SOURCE
    assert cached.mapped_candidate_namespace == "copyright"


def test_danbooru_alias_lookup_resolves_to_canonical_tag(monkeypatch):
    def fake_tag_payload(key, *, timeout):
        assert timeout == 1
        if key == "alias_name":
            return []
        if key == "canonical_character":
            return [{"id": 4, "name": "canonical_character", "category": 4}]
        raise AssertionError(key)

    def fake_alias_payload(key, *, timeout):
        assert key == "alias_name"
        assert timeout == 1
        return [{"antecedent_name": "alias_name", "consequent_name": "canonical_character", "status": "active"}]

    monkeypatch.setattr(pilot, "fetch_danbooru_tag_payload", fake_tag_payload)
    monkeypatch.setattr(pilot, "fetch_danbooru_tag_alias_payload", fake_alias_payload)

    payload, request_count, matched_lookup_key = pilot.fetch_danbooru_tag_category_payload("alias_name", timeout=1)
    result = pilot._lookup_result_from_danbooru_payload(
        raw_tag="alias_name",
        normalized_tag="alias_name",
        payload=payload,
        cache_status="miss",
        matched_lookup_key=matched_lookup_key,
    )

    assert request_count == 3
    assert result.status == "hit"
    assert result.mapped_candidate_namespace == "character"


def test_external_lookup_not_found_or_failure_stays_unresolved(db):
    record = _record(tags=("missing_tag", "broken_tag"), artist_name=None, artist_id=None)

    def fake_fetcher(key, _timeout):
        if key == "missing_tag":
            return []
        raise RuntimeError("boom")

    lookup_results, summary = pilot.lookup_external_tag_categories(
        [record],
        session=db,
        lookup_limit=10,
        delay_seconds=0,
        timeout_seconds=1,
        cache_writes_enabled=True,
        fetcher=fake_fetcher,
    )
    _normalized, rows = pilot.normalize_metadata_candidates(
        [record],
        pilot.LocalClassificationIndex(),
        external_lookup_results=lookup_results,
    )

    assert summary.not_found_count == 1
    assert summary.lookup_error_count == 1
    assert lookup_results["missing_tag"].status == "not_found"
    assert lookup_results["broken_tag"].status == "lookup_error"
    assert {row.candidate_kind for row in rows} == {"unknown_or_unresolved_pixiv_tag"}


def test_external_lookup_cap_limits_requests(db):
    calls = []

    def fake_fetcher(key, _timeout):
        calls.append(key)
        return [{"id": len(calls), "name": key, "category": 0}]

    record = _record(tags=("tag_a", "tag_b", "tag_c"), artist_name=None, artist_id=None)
    _lookup_results, summary = pilot.lookup_external_tag_categories(
        [record],
        session=None,
        lookup_limit=2,
        delay_seconds=0,
        timeout_seconds=1,
        cache_writes_enabled=False,
        fetcher=fake_fetcher,
    )

    assert summary.unique_normalized_tag_count == 3
    assert summary.request_count == 2
    assert calls == ["tag_a", "tag_b"]
    with pytest.raises(pilot.SampleGateError, match="tag_lookup_cap_exceeded"):
        pilot.enforce_tag_lookup_limit(pilot.MAX_TAG_LOOKUP_LIMIT_WITHOUT_RENEWED_APPROVAL + 1)


def test_external_lookup_cache_migration_creates_table():
    engine = create_engine("sqlite://")
    try:
        migrate_add_external_tag_category_lookup_cache(engine, inspect(engine))
        inspector = inspect(engine)
        assert "blombooru_external_tag_category_lookup_cache" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("blombooru_external_tag_category_lookup_cache")}
        assert {
            "lookup_source",
            "normalized_tag",
            "source_tag_id",
            "mapped_candidate_namespace",
            "status",
            "manual_override_status",
        }.issubset(columns)
    finally:
        engine.dispose()


def test_external_lookup_write_guard_allows_cache_only():
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        pilot.install_external_lookup_cache_write_guard(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO blombooru_external_tag_category_lookup_cache "
                    "(raw_tag, normalized_tag, lookup_source, status) "
                    "VALUES ('x', 'x', 'danbooru_tags_api_v1', 'not_found')"
                )
            )
        with pytest.raises(pilot.f1.ReadOnlyViolation):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO blombooru_media_tags (media_id, tag_id) VALUES (1, 1)"))
    finally:
        engine.dispose()


def test_cache_lookup_writes_no_truth_tables(db):
    def fake_fetcher(key, _timeout):
        return [{"id": 3, "name": key, "category": 4}]

    record = _record(tags=("sample_character",), artist_name=None, artist_id=None)
    pilot.lookup_external_tag_categories(
        [record],
        session=db,
        lookup_limit=10,
        delay_seconds=0,
        timeout_seconds=1,
        cache_writes_enabled=True,
        fetcher=fake_fetcher,
    )

    assert db.query(ExternalTagCategoryLookupCache).count() == 1
    assert db.query(EntityEvidence).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0


def test_dry_run_entrypoint_does_not_probe_gallery_dl(monkeypatch):
    def fail_probe(_command):
        raise AssertionError("dry-run must not probe gallery-dl")

    monkeypatch.setattr(pilot, "probe_gallery_dl_entrypoint", fail_probe)
    args = pilot.build_arg_parser().parse_args(["--dry-run", "--gallery-dl-command", "gallery-dl"])
    entrypoint = pilot.entrypoint_for_args(args)

    assert entrypoint.mode == "dry_run_no_gallery_dl_probe"
    assert entrypoint.reproducibility_status == "dry_run_no_gallery_dl_probe"
