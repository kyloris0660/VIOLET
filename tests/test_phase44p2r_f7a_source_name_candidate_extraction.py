"""Focused tests for Phase 4.4-P2R-F7a source name candidate extraction."""

from __future__ import annotations

import sys
import asyncio
import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base  # noqa: E402
from app.enums import ContentClassEnum, FileTypeEnum, TagCategoryEnum  # noqa: E402
from app.models import (  # noqa: E402
    Media,
    SourceNameCandidate,
    SourceNameCandidateExtractionRun,
    SourceNameCandidateRecordVerdict,
    SourceMetadataRecord,
    SourceTagObservation,
    Tag,
    blombooru_media_tags,
)
from app.services.llm_translation_provider import BaseLLMProvider, LLMResponseFormatError  # noqa: E402
from app.services.source_name_candidate_extraction_service import (  # noqa: E402
    PROMPT_VERSION,
    SOURCE_NAME_CANDIDATE_VALUE_MAX_LENGTH,
    SourceCandidateInputGroup,
    SourceNameCandidateExtractionError,
    build_extraction_units,
    collect_source_candidate_input_groups,
    deterministic_bundle_for_unit,
    fallback_openai_provider_from_settings,
    group_input_payload_hash,
    llm_cache_fingerprint,
    media_llm_eligibility,
    persist_extraction_bundle,
    popularity_suffix_prefix,
    primary_openai_provider_from_settings,
    run_extraction_sync,
    table_counts,
    validate_extraction_record,
    reattach_unit_bundles_to_records,
    source_name_candidate_system_prompt,
)


class FakeProvider(BaseLLMProvider):
    def __init__(self, payload=None, *, fail_json: bool = False, chat_payload: str = "", delay: float = 0.0):
        self.payload = payload
        self.fail_json = fail_json
        self.chat_payload = chat_payload
        self.delay = delay
        self.complete_json_calls = 0
        self.complete_chat_calls = 0

    def is_available(self) -> bool:
        return True

    def get_provider_name(self) -> str:
        return "fake"

    async def complete_chat(self, messages, *, temperature=0.3, max_tokens=4096) -> str:
        self.complete_chat_calls += 1
        return self.chat_payload

    async def complete_json(self, messages, *, temperature=0.3, max_tokens=4096):
        self.complete_json_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_json:
            raise LLMResponseFormatError("bad json")
        return self.payload

    async def translate_tags(self, tags):
        return []


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


def _pixiv_group() -> SourceCandidateInputGroup:
    genshin = "\u539f\u795e"
    mona = "\u30e2\u30ca"
    return SourceCandidateInputGroup(
        group_key="source_record:1",
        provider="pixiv",
        source_metadata_record_id=1,
        media_id=10,
        data_type_label="real_live_or_local_provider_data",
        metadata_kind="gallery_dl_real_pixiv_metadata",
        tags=(
            {"raw_tag": f"{genshin}500users\u5165\u308a", "observation_key": "tag:pop"},
            {"raw_tag": "\u30bb\u30fc\u30e9\u30fc\u670d", "observation_key": "tag:sailor"},
            {"raw_tag": f"{mona}({genshin})", "observation_key": "tag:mona"},
        ),
        source_assertions=(
            {
                "assertion_key": "assert:ayaka",
                "raw_input": "Kamisato Ayaka",
                "asserted_name": "Kamisato Ayaka",
                "asserted_role": "character",
                "status": "searchable_active",
                "confidence_score": 0.91,
            },
        ),
        data_origin="real_dev_db",
    )


def _simple_pixiv_tag_group(index: int, tag: str = "\u795e\u91cc\u7dbe\u83ef") -> SourceCandidateInputGroup:
    return SourceCandidateInputGroup(
        group_key=f"source_record:{index}",
        provider="pixiv",
        source_metadata_record_id=index,
        media_id=100 + index,
        tags=({"raw_tag": tag, "observation_key": f"tag:{index}"},),
        data_origin="real_dev_db",
        eligibility_status="eligible",
        eligibility_reason="eligible_anime",
        content_class="anime",
    )


def _media(db, media_id: int, content_class, *, reviewed=False, locked=False) -> Media:
    row = Media(
        id=media_id,
        filename=f"m{media_id}.jpg",
        path=f"media/m{media_id}.jpg",
        hash=f"hash-{media_id}",
        file_type=FileTypeEnum.image,
        content_class=content_class,
        content_class_reviewed=reviewed,
        content_class_locked=locked,
    )
    db.add(row)
    return row


def _source_record(db, record_id: int, media_id: int | None, key: str) -> SourceMetadataRecord:
    row = SourceMetadataRecord(
        id=record_id,
        provider="pixiv",
        provider_record_key=key,
        media_id=media_id,
        title="Kamisato Ayaka",
        artist_name="artist",
        metadata_kind="gallery_dl_real_pixiv_metadata",
        data_type_label="real_live_or_local_provider_data",
        status="observed",
    )
    db.add(row)
    db.flush()
    db.add(
        SourceTagObservation(
            source_metadata_record_id=row.id,
            provider="pixiv",
            observation_key=f"{key}:tag",
            raw_tag="\u795e\u91cc\u7dbe\u83ef",
            normalized_tag="\u795e\u91cc\u7dbe\u83ef",
            canonical_tag_key="kamisato_ayaka",
            source_tag_kind="provider_tag",
            status="observed",
        )
    )
    return row


def test_popularity_suffix_prefix_strips_pixiv_marker():
    result = popularity_suffix_prefix("\u539f\u795e500users\u5165\u308a")

    assert result["extraction_action"] == "popularity_suffix_stripped"
    assert result["extracted_prefix"] == "\u539f\u795e"
    assert result["original_tag_role"] == "popularity_meta"


def test_validate_record_merges_deterministic_candidates_and_rejections():
    group = _pixiv_group()
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "extraction_verdict": "rejected_general_only",
        "verdict_reason": "model over-rejected rich metadata",
        "candidates": [],
        "rejected_tags": [],
        "meta_tags": [],
        "ambiguous_items": [],
        "no_name_reason": "model found only general tags",
        "extraction_warnings": [],
        "confidence_summary": {},
        "should_not_create_entity_truth": True,
    }

    verdict, candidates, rejected, meta, _ambiguous = validate_extraction_record(row, group)
    names = {candidate.raw_value for candidate in candidates}

    assert verdict.extraction_verdict == "multiple_candidates_found"
    assert "verdict_promoted_by_candidate_recovery" in verdict.extraction_warnings_json
    assert "\u539f\u795e" in names
    assert "\u30e2\u30ca" in names
    assert "\u539f\u795e500users\u5165\u308a" not in names
    assert any(item.raw_value == "\u30bb\u30fc\u30e9\u30fc\u670d" for item in rejected)
    assert any(item.extracted_prefix == "\u539f\u795e" for item in meta)
    assert any(candidate.extraction_action == "popularity_suffix_stripped" for candidate in candidates)
    assert any(candidate.extraction_action == "parenthetical_split" for candidate in candidates)


def test_invalid_llm_output_downgrades_with_explicit_verdict():
    group = _pixiv_group()
    provider = FakeProvider(fail_json=True, chat_payload="not json")

    bundle = run_extraction_sync(
        provider,
        [group],
        run_id="test-run-invalid",
        run_label="unit",
        chunk_size=1,
        retries=0,
        max_tokens=1000,
    )

    assert bundle.record_verdicts[0].extraction_verdict == "extraction_error_terminal"
    assert bundle.record_verdicts[0].verdict_reason
    assert bundle.validation_failures
    assert bundle.candidates


def test_timeout_failure_is_retryable_not_terminal():
    class TimeoutProvider(FakeProvider):
        async def complete_json(self, messages, *, temperature=0.3, max_tokens=4096):
            self.complete_json_calls += 1
            raise asyncio.TimeoutError("timed out")

    group = _pixiv_group()
    provider = TimeoutProvider()

    bundle = run_extraction_sync(
        provider,
        [group],
        run_id="test-run-timeout",
        run_label="unit",
        chunk_size=1,
        retries=0,
        max_tokens=1000,
    )

    assert bundle.record_verdicts[0].extraction_verdict == "extraction_error_retryable"
    assert bundle.summary["llm"]["failed_records_downgraded"] == 1


def test_persist_extraction_bundle_writes_only_f7a_tables():
    engine, db = _db()
    try:
        group = _pixiv_group()
        provider = FakeProvider(
            {
                "records": [
                    {
                        "group_key": group.group_key,
                        "provider": "pixiv",
                        "extraction_verdict": "name_candidate_found",
                        "verdict_reason": "structured output found source names",
                        "candidates": [
                            {
                                "raw_value": "Kamisato Ayaka",
                                "display_name": "Kamisato Ayaka",
                                "normalized_value": "Kamisato Ayaka",
                                "canonical_key": "kamisato_ayaka",
                                "candidate_role": "character",
                                "candidate_status": "active_candidate",
                                "language_hint": "latin",
                                "script_hint": "latin",
                                "work_context": "Genshin Impact",
                                "parenthetical_base": None,
                                "parenthetical_context": None,
                                "extracted_from": "source_assertion",
                                "extraction_action": "direct_name",
                                "evidence_tags": ["Kamisato Ayaka"],
                                "sibling_context": [],
                                "confidence": 0.91,
                                "reason": "source assertion",
                            }
                        ],
                        "rejected_tags": [],
                        "meta_tags": [],
                        "ambiguous_items": [],
                        "no_name_reason": None,
                        "extraction_warnings": [],
                        "confidence_summary": {"overall": 0.91},
                        "should_not_create_entity_truth": True,
                    }
                ]
            }
        )
        bundle = run_extraction_sync(
            provider,
            [group],
            run_id="test-run-persist",
            run_label="unit",
            chunk_size=1,
            retries=0,
            max_tokens=1000,
        )
        before_forbidden = table_counts(db, ("blombooru_media_tags", "blombooru_entities", "blombooru_tag_translations"))
        summary = persist_extraction_bundle(db, bundle, apply=True)
        after_forbidden = table_counts(db, ("blombooru_media_tags", "blombooru_entities", "blombooru_tag_translations"))

        assert summary["forbidden_truth_table_write_count"] == 0
        assert before_forbidden == after_forbidden
        assert db.query(SourceNameCandidateExtractionRun).count() == 1
        assert db.query(SourceNameCandidateRecordVerdict).count() == 1
        assert db.query(SourceNameCandidate).count() >= 1
    finally:
        db.close()
        engine.dispose()


def test_collect_groups_handles_empty_db_without_crashing():
    engine, db = _db()
    try:
        groups, summary = collect_source_candidate_input_groups(db, max_records=10, max_unique_strings=50)

        assert groups == []
        assert summary["groups_collected"] == 0
    finally:
        db.close()
        engine.dispose()


def test_eligibility_gate_allows_anime_and_approved_illustration_only():
    engine, db = _db()
    try:
        _media(db, 1, ContentClassEnum.anime)
        _media(db, 2, ContentClassEnum.illustration, reviewed=True)
        _media(db, 3, ContentClassEnum.illustration)
        _media(db, 4, ContentClassEnum.unknown)
        _media(db, 5, ContentClassEnum.non_anime)
        for record_id, media_id in enumerate([1, 2, 3, 4, 5], start=1):
            _source_record(db, record_id, media_id, f"r{record_id}")
        db.commit()

        groups, summary = collect_source_candidate_input_groups(
            db,
            max_records=10,
            max_unique_strings=200,
            include_media_tag_only_groups=False,
        )

        assert {group.media_id for group in groups} == {1, 2}
        assert summary["excluded_counts"]["excluded_unapproved_illustration"] == 1
        assert summary["excluded_counts"]["excluded_unknown_or_unclassified"] == 1
        assert summary["excluded_counts"]["excluded_non_anime"] == 1
    finally:
        db.close()
        engine.dispose()


def test_collect_groups_applies_source_record_scan_limit_before_materialization():
    engine, db = _db()
    try:
        for record_id in range(1, 151):
            db.add(
                SourceMetadataRecord(
                    id=record_id,
                    provider="pixiv",
                    provider_record_key=f"r{record_id}",
                    metadata_kind="gallery_dl_real_pixiv_metadata",
                    data_type_label="real_live_or_local_provider_data",
                    status="observed",
                )
            )
        db.commit()

        groups, summary = collect_source_candidate_input_groups(
            db,
            max_records=1,
            max_unique_strings=2,
            include_media_tag_only_groups=False,
        )

        assert groups == []
        assert summary["source_metadata_records_available"] == 150
        assert summary["source_metadata_record_scan_limit"] < summary["source_metadata_records_available"]
        assert summary["source_metadata_records_scanned"] == summary["source_metadata_record_scan_limit"]
    finally:
        db.close()
        engine.dispose()


def test_first_group_cannot_bypass_unique_string_cap():
    engine, db = _db()
    try:
        _media(db, 1, ContentClassEnum.anime)
        _source_record(db, 1, 1, "oversized-first")
        db.commit()

        groups, summary = collect_source_candidate_input_groups(
            db,
            max_records=10,
            max_unique_strings=1,
            include_media_tag_only_groups=False,
        )

        assert groups == []
        assert summary["excluded_counts"]["max_unique_strings_group_oversized"] == 1
    finally:
        db.close()
        engine.dispose()


def test_media_tag_only_groups_use_same_eligibility_gate():
    engine, db = _db()
    try:
        _media(db, 1, ContentClassEnum.anime)
        _media(db, 2, ContentClassEnum.non_anime)
        character = Tag(id=1, name="barbara_(genshin_impact)", category=TagCategoryEnum.character)
        db.add(character)
        db.flush()
        db.execute(blombooru_media_tags.insert().values(media_id=1, tag_id=1, source="ai", confidence=0.7, is_suggestion=True))
        db.execute(blombooru_media_tags.insert().values(media_id=2, tag_id=1, source="ai", confidence=0.7, is_suggestion=True))
        db.commit()

        groups, summary = collect_source_candidate_input_groups(db, max_records=10, max_unique_strings=50)

        assert [group.media_id for group in groups] == [1]
        assert summary["eligible_groups_collected"] == 1
    finally:
        db.close()
        engine.dispose()


def test_non_numeric_confidence_downgrades_candidate_without_crashing():
    group = _simple_pixiv_tag_group(1, "Kamisato Ayaka")
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "verdict": "name_candidate_found",
        "candidates": [
            {
                "raw_value": "Kamisato Ayaka",
                "display_name": "Kamisato Ayaka",
                "canonical_key": "kamisato_ayaka",
                "role": "character",
                "status": "active_candidate",
                "source_field": "pixiv_tag",
                "extraction_action": "direct_name",
                "confidence": "high",
            }
        ],
        "rejected_summary": {},
    }

    _verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)

    assert candidates[0].candidate_status == "needs_review"
    assert candidates[0].confidence == 0.0
    assert candidates[0].evidence_payload["candidate_status_guard"]["candidate_confidence_invalid_downgraded"] is True


def test_malformed_candidate_array_fails_when_verdict_claims_candidates():
    group = _pixiv_group()
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "verdict": "name_candidate_found",
        "candidates": ["Kamisato Ayaka"],
        "rejected_summary": {},
    }

    try:
        validate_extraction_record(row, group)
    except SourceNameCandidateExtractionError as exc:
        assert "malformed_candidate_array" in str(exc)
    else:
        raise AssertionError("malformed candidate array must fail validation")


def test_descriptive_unknown_name_like_candidates_are_not_active():
    false_positive_terms = (
        "\u306f\u3044\u3066\u306a\u3044",
        "\u304a\u5c3b",
        "\u30ac\u30fc\u30bf\u30fc\u30d9\u30eb\u30c8",
        "\u30e1\u30a4\u30c9",
        "\u30a6\u30a3\u30f3\u30af",
        "\u5c11\u5973",
        "\u3071\u3093\u3064",
        "\u30b9\u30af\u6c34",
        "\u88f8\u8db3",
    )
    for index, term in enumerate(false_positive_terms, start=1):
        group = _simple_pixiv_tag_group(index, term)
        row = {
            "group_key": group.group_key,
            "provider": "pixiv",
            "verdict": "name_candidate_found",
            "candidates": [
                {
                    "raw_value": term,
                    "display_name": term,
                    "normalized_value": term,
                    "role": "unknown_name_like",
                    "status": "active_candidate",
                    "source_field": "pixiv_tag",
                    "extraction_action": "direct_name",
                    "confidence": 0.8,
                }
            ],
            "rejected_summary": {},
        }

        _verdict, candidates, rejected, _meta, _ambiguous = validate_extraction_record(row, group)

        assert any(item.raw_value == term for item in rejected)
        assert all(
            not (
                candidate.raw_value == term
                and candidate.candidate_role == "unknown_name_like"
                and candidate.candidate_status == "active_candidate"
            )
            for candidate in candidates
        )


def test_true_name_candidates_remain_active_after_false_positive_guard():
    group = _simple_pixiv_tag_group(1, "\u30d0\u30fc\u30d0\u30e9")
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "verdict": "name_candidate_found",
        "candidates": [
            {
                "raw_value": "\u30d0\u30fc\u30d0\u30e9",
                "display_name": "\u30d0\u30fc\u30d0\u30e9",
                "normalized_value": "\u30d0\u30fc\u30d0\u30e9",
                "role": "character",
                "status": "active_candidate",
                "source_field": "pixiv_tag",
                "extraction_action": "direct_name",
                "confidence": 0.9,
            }
        ],
        "rejected_summary": {},
    }

    _verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)

    assert any(
        candidate.raw_value == "\u30d0\u30fc\u30d0\u30e9"
        and candidate.candidate_role == "character"
        and candidate.candidate_status == "active_candidate"
        for candidate in candidates
    )


def test_pixiv_title_full_string_is_not_active_work_title_but_embedded_name_can_remain_active():
    group = SourceCandidateInputGroup(
        group_key="source_record:title",
        provider="pixiv",
        source_metadata_record_id=1,
        media_id=10,
        title="Yoimiya in bikini",
        data_origin="real_dev_db",
    )
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "verdict": "multiple_candidates_found",
        "candidates": [
            {
                "raw_value": "Yoimiya in bikini",
                "display_name": "Yoimiya in bikini",
                "normalized_value": "Yoimiya in bikini",
                "role": "work_title",
                "status": "active_candidate",
                "source_field": "pixiv_title",
                "extraction_action": "direct_name",
                "confidence": 0.8,
            },
            {
                "raw_value": "Yoimiya",
                "display_name": "Yoimiya",
                "normalized_value": "Yoimiya",
                "role": "character",
                "status": "active_candidate",
                "source_field": "pixiv_title",
                "extraction_action": "context_inferred",
                "confidence": 0.55,
            },
        ],
        "rejected_summary": {},
    }

    _verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)
    full_title = next(candidate for candidate in candidates if candidate.raw_value == "Yoimiya in bikini")
    embedded_name = next(candidate for candidate in candidates if candidate.raw_value == "Yoimiya")

    assert full_title.candidate_role == "source_title"
    assert full_title.candidate_status == "needs_review"
    assert full_title.evidence_payload["candidate_status_guard"]["pixiv_title_work_title_role_downgraded"] is True
    assert embedded_name.candidate_role == "character"
    assert embedded_name.candidate_status == "active_candidate"


def test_overlength_candidate_values_are_bounded_before_persistence():
    long_value = "A" * (SOURCE_NAME_CANDIDATE_VALUE_MAX_LENGTH + 50)
    group = SourceCandidateInputGroup(
        group_key="source_record:long-title",
        provider="pixiv",
        source_metadata_record_id=1,
        media_id=10,
        title=long_value,
        data_origin="real_dev_db",
    )
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "verdict": "work_candidate_found",
        "candidates": [
            {
                "raw_value": long_value,
                "display_name": long_value,
                "normalized_value": long_value,
                "canonical_key": long_value.lower(),
                "role": "work_title",
                "status": "active_candidate",
                "source_field": "pixiv_title",
                "extraction_action": "direct_name",
                "confidence": 0.8,
            }
        ],
        "rejected_summary": {},
    }

    _verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)
    candidate = candidates[0]

    assert len(candidate.raw_value) == SOURCE_NAME_CANDIDATE_VALUE_MAX_LENGTH
    assert len(candidate.display_name) == SOURCE_NAME_CANDIDATE_VALUE_MAX_LENGTH
    assert len(candidate.normalized_value) == SOURCE_NAME_CANDIDATE_VALUE_MAX_LENGTH
    assert len(candidate.canonical_key) <= SOURCE_NAME_CANDIDATE_VALUE_MAX_LENGTH
    assert candidate.candidate_status == "needs_review"
    assert "candidate_value_length_guard" in candidate.evidence_payload["candidate_status_guard"]


def test_context_inferred_high_confidence_is_downgraded_not_terminal():
    group = SourceCandidateInputGroup(
        group_key="source_record:title-context",
        provider="pixiv",
        source_metadata_record_id=1,
        media_id=10,
        title="Character portrait",
        data_origin="real_dev_db",
    )
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "verdict": "name_candidate_found",
        "candidates": [
            {
                "raw_value": "Character",
                "display_name": "Character",
                "normalized_value": "Character",
                "role": "unknown_name_like",
                "status": "active_candidate",
                "source_field": "pixiv_title",
                "extraction_action": "context_inferred",
                "confidence": 0.8,
            }
        ],
        "rejected_summary": {},
    }

    _verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)

    assert candidates[0].confidence == 0.55
    assert candidates[0].candidate_status == "needs_review"
    assert candidates[0].evidence_payload["candidate_status_guard"]["context_inferred_confidence_capped"] is True


def test_deduped_unit_truncated_group_key_and_person_verdict_are_repaired():
    group = SourceCandidateInputGroup(
        group_key="extraction_unit:1234567890abcdef12345678",
        provider="pixiv",
        tags=({"raw_tag": "Name"},),
        data_origin="deduped_extraction_unit",
    )
    row = {
        "group_key": "extraction_unit:1234567890abcdef",
        "provider": "pixiv",
        "verdict": "person_candidate_found",
        "candidates": [
            {
                "raw_value": "Name",
                "display_name": "Name",
                "normalized_value": "Name",
                "role": "person",
                "status": "active_candidate",
                "source_field": "pixiv_tag",
                "extraction_action": "direct_name",
                "confidence": 0.7,
            }
        ],
        "rejected_summary": {},
    }

    verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)

    assert verdict.extraction_verdict == "name_candidate_found"
    assert "deduped_extraction_unit_group_key_truncation_repaired" in verdict.extraction_warnings_json
    assert candidates[0].candidate_role == "person"


def test_role_value_used_as_verdict_is_safely_mapped():
    group = SourceCandidateInputGroup(
        group_key="extraction_unit:role-verdict",
        provider="pixiv",
        tags=({"raw_tag": "Maybe Name"},),
        data_origin="deduped_extraction_unit",
    )
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "verdict": "unknown_name_like",
        "candidates": [
            {
                "raw_value": "Maybe Name",
                "display_name": "Maybe Name",
                "normalized_value": "Maybe Name",
                "role": "unknown_name_like",
                "status": "needs_review",
                "source_field": "pixiv_tag",
                "extraction_action": "direct_name",
                "confidence": 0.5,
            }
        ],
        "rejected_summary": {},
    }

    verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)

    assert verdict.extraction_verdict == "ambiguous_needs_review"
    assert candidates[0].candidate_status == "needs_review"


def test_wd_tagger_suggestions_are_weak_ai_evidence_not_active_normal_tags():
    group = SourceCandidateInputGroup(
        group_key="media_tag_only:1",
        provider="local_media_tags",
        media_id=1,
        media_tags=(
            {
                "tag_id": 7,
                "name": "barbara_(genshin_impact)",
                "category": TagCategoryEnum.character.value,
                "source": "wd_tagger",
                "is_suggestion": True,
            },
        ),
        data_origin="real_dev_db",
    )
    row = {
        "group_key": group.group_key,
        "provider": "local_media_tags",
        "verdict": "no_explicit_name",
        "candidates": [],
        "rejected_summary": {},
    }

    _verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)

    assert len(candidates) == 1
    assert candidates[0].origin_type == "ai_model_tag"
    assert candidates[0].extraction_action == "ai_model_character_tag"
    assert candidates[0].candidate_status == "needs_review"


def test_cache_fingerprint_changes_by_provider_and_prompt_payload():
    group = _pixiv_group()
    primary = {
        "provider_mode": "primary_serial",
        "llm_provider_label": "primary_openai",
        "model_label": "primary_model_configured",
        "primary_provider_type": "openai_compatible",
        "llm_access_configured": True,
    }
    primary_config_changed = {
        "provider_mode": "primary_concurrent",
        "llm_provider_label": "primary_openai",
        "model_label": "primary_model_configured",
        "primary_provider_type": "other_compatible",
        "llm_access_configured": True,
    }
    primary_same_config_different_scheduler = {
        "provider_mode": "primary_concurrent",
        "llm_provider_label": "primary_openai",
        "model_label": "primary_model_configured",
        "primary_provider_type": "openai_compatible",
        "llm_access_configured": True,
    }
    fallback = {"llm_provider_label": "fallback", "model_label": "fallback_model_configured"}
    first = llm_cache_fingerprint(group, primary)
    second = llm_cache_fingerprint(group, fallback)
    changed_group = SourceCandidateInputGroup(**{**group.__dict__, "title": "Different"})
    changed_eligibility_group = SourceCandidateInputGroup(**{**group.__dict__, "content_class": "non_anime", "eligibility_reason": "excluded_non_anime"})

    assert PROMPT_VERSION.endswith("_v3")
    assert first != second
    assert first != llm_cache_fingerprint(group, primary_config_changed)
    assert first == llm_cache_fingerprint(group, primary_same_config_different_scheduler)
    assert first != llm_cache_fingerprint(changed_group, primary)
    assert first != llm_cache_fingerprint(changed_eligibility_group, primary)
    assert group_input_payload_hash(group)
    prompt = source_name_candidate_system_prompt()
    assert "Pixiv titles and captions are weak title evidence" in prompt
    assert "AI/model suggestion tags are weak identity evidence" in prompt


def test_deterministic_and_llm_candidates_dedupe_by_canonical_role_action():
    group = _pixiv_group()
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "verdict": "name_candidate_found",
        "candidates": [
            {
                "raw_value": "Kamisato Ayaka",
                "display_name": "Kamisato Ayaka",
                "normalized_value": "kamisato ayaka",
                "canonical_key": "kamisato_ayaka",
                "role": "character",
                "status": "active_candidate",
                "source_field": "source_assertion",
                "extraction_action": "direct_name",
                "confidence": 0.8,
            }
        ],
        "rejected_summary": {},
    }

    _verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)
    matching = [
        candidate
        for candidate in candidates
        if candidate.canonical_key == "kamisato_ayaka"
        and candidate.candidate_role == "character"
        and candidate.extraction_action == "direct_name"
    ]

    assert len(matching) == 1


def test_run_scoped_candidates_do_not_move_between_runs():
    engine, db = _db()
    try:
        group = _pixiv_group()
        provider = FakeProvider(
            {
                "records": [
                    {
                        "group_key": group.group_key,
                        "provider": "pixiv",
                        "verdict": "name_candidate_found",
                        "candidates": [
                            {
                                "raw_value": "Kamisato Ayaka",
                                "display_name": "Kamisato Ayaka",
                                "canonical_key": "kamisato_ayaka",
                                "role": "character",
                                "status": "active_candidate",
                                "source_field": "source_assertion",
                                "extraction_action": "direct_name",
                                "confidence": 0.91,
                            }
                        ],
                        "rejected_summary": {},
                    }
                ]
            }
        )
        first = run_extraction_sync(provider, [group], run_id="run-a", run_label="unit", chunk_size=1, retries=0)
        second = run_extraction_sync(provider, [group], run_id="run-b", run_label="unit", chunk_size=1, retries=0)
        persist_extraction_bundle(db, first, apply=True)
        persist_extraction_bundle(db, second, apply=True)

        assert db.query(SourceNameCandidateExtractionRun).count() == 2
        assert db.query(SourceNameCandidate).count() >= 2
        assert {row.run_id for row in db.query(SourceNameCandidateExtractionRun).all()} == {"run-a", "run-b"}
    finally:
        db.close()
        engine.dispose()


def test_same_run_rerun_with_no_candidates_supersedes_stale_group_rows():
    engine, db = _db()
    try:
        group = _pixiv_group()
        provider = FakeProvider(
            {
                "records": [
                    {
                        "group_key": group.group_key,
                        "provider": "pixiv",
                        "verdict": "name_candidate_found",
                        "candidates": [
                            {
                                "raw_value": "Kamisato Ayaka",
                                "display_name": "Kamisato Ayaka",
                                "canonical_key": "kamisato_ayaka",
                                "role": "character",
                                "status": "active_candidate",
                                "source_field": "source_assertion",
                                "extraction_action": "direct_name",
                                "confidence": 0.91,
                            }
                        ],
                        "rejected_summary": {},
                    }
                ]
            }
        )
        initial = run_extraction_sync(provider, [group], run_id="run-stale", run_label="unit", chunk_size=1, retries=0)
        persist_extraction_bundle(db, initial, apply=True)
        failure_provider = FakeProvider(fail_json=True, chat_payload="not json")
        rerun = run_extraction_sync(failure_provider, [group], run_id="run-stale", run_label="unit", chunk_size=1, retries=0)
        object.__setattr__(rerun, "candidates", ())
        persist_extraction_bundle(db, rerun, apply=True)

        assert db.query(SourceNameCandidate).filter_by(status="superseded").count() >= 1
    finally:
        db.close()
        engine.dispose()


def test_compact_schema_preserves_multilingual_candidates():
    group = _pixiv_group()
    row = {
        "group_key": group.group_key,
        "provider": "pixiv",
        "verdict": "multiple_candidates_found",
        "candidates": [
            {
                "raw_value": "\u795e\u91cc\u7dbe\u83ef",
                "display_name": "\u795e\u91cc\u7dbe\u83ef",
                "canonical_key": "kamisato_ayaka",
                "role": "character",
                "status": "active_candidate",
                "source_field": "pixiv_tag",
                "extraction_action": "direct_name",
                "confidence": 0.9,
            },
            {
                "raw_value": "Kamisato Ayaka",
                "display_name": "Kamisato Ayaka",
                "canonical_key": "kamisato_ayaka",
                "role": "character",
                "status": "active_candidate",
                "source_field": "source_assertion",
                "extraction_action": "direct_name",
                "confidence": "0.9",
            },
        ],
        "rejected_summary": {"descriptive_general_count": 1},
    }

    verdict, candidates, _rejected, _meta, _ambiguous = validate_extraction_record(row, group)

    assert verdict.extraction_verdict == "multiple_candidates_found"
    assert {"\u795e\u91cc\u7dbe\u83ef", "Kamisato Ayaka"} <= {candidate.raw_value for candidate in candidates}


def test_duplicate_tag_across_records_creates_one_extraction_unit_and_one_llm_call():
    groups = [_simple_pixiv_tag_group(index) for index in range(10)]
    units, summary = build_extraction_units(groups)
    llm_units = [unit for unit in units if unit.llm_required]

    assert summary["raw_string_occurrences_total"] == 10
    assert summary["unique_extraction_units_total"] == 1
    assert summary["llm_calls_avoided_by_dedupe"] == 9
    assert len(llm_units) == 1

    unit = llm_units[0]
    provider = FakeProvider(
        {
            "records": [
                {
                    "group_key": unit.unit_group.group_key,
                    "provider": "pixiv",
                    "verdict": "name_candidate_found",
                    "candidates": [
                        {
                            "raw_value": "\u795e\u91cc\u7dbe\u83ef",
                            "display_name": "\u795e\u91cc\u7dbe\u83ef",
                            "canonical_key": "kamisato_ayaka",
                            "role": "character",
                            "status": "active_candidate",
                            "source_field": "pixiv_tag",
                            "extraction_action": "direct_name",
                            "confidence": 0.9,
                        }
                    ],
                    "rejected_summary": {},
                }
            ]
        }
    )
    unit_bundle = run_extraction_sync(provider, [unit.unit_group], run_id="unit-run", run_label="unit", chunk_size=1, retries=0)
    record_bundle = reattach_unit_bundles_to_records(
        groups,
        units,
        {unit.extraction_key: unit_bundle},
        run_id="record-run",
        run_label="unit",
    )

    assert provider.complete_json_calls == 1
    assert len(record_bundle.record_verdicts) == 10
    assert all(verdict.extraction_verdict == "name_candidate_found" for verdict in record_bundle.record_verdicts)


def test_popularity_suffix_prefix_extraction_units_are_deduped():
    groups = [
        _simple_pixiv_tag_group(1, "\u539f\u795e500users\u5165\u308a"),
        _simple_pixiv_tag_group(2, "\u539f\u795e1000users\u5165\u308a"),
    ]
    units, summary = build_extraction_units(groups)

    assert summary["raw_string_occurrences_total"] == 2
    assert summary["unique_extraction_units_total"] == 1
    assert summary["llm_required_units"] == 0
    assert units[0].normalized_value == "\u539f\u795e"
    assert set(units[0].raw_values) == {"\u539f\u795e500users\u5165\u308a", "\u539f\u795e1000users\u5165\u308a"}


def test_deterministic_rejected_tags_do_not_go_to_llm_repeatedly():
    groups = [_simple_pixiv_tag_group(index, "R-18") for index in range(1, 6)]
    units, summary = build_extraction_units(groups)

    assert len(units) == 1
    assert units[0].llm_required is False
    assert summary["llm_required_units"] == 0
    assert summary["llm_calls_avoided_by_dedupe"] == 4


def test_ambiguous_short_names_remain_f7a_candidates_not_concepts():
    group = _simple_pixiv_tag_group(1, "2B")
    units, _summary = build_extraction_units([group])

    assert len(units) == 1
    assert units[0].llm_required is True
    assert "concept" not in units[0].extraction_key


def test_inflight_duplicate_guard_prevents_concurrent_duplicate_llm_calls(tmp_path):
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_for_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    unit = build_extraction_units([_simple_pixiv_tag_group(1)])[0][0]
    provider = FakeProvider(
        {
            "records": [
                {
                    "group_key": unit.unit_group.group_key,
                    "provider": "pixiv",
                    "verdict": "name_candidate_found",
                    "candidates": [
                        {
                            "raw_value": "\u795e\u91cc\u7dbe\u83ef",
                            "display_name": "\u795e\u91cc\u7dbe\u83ef",
                            "canonical_key": "kamisato_ayaka",
                            "role": "character",
                            "status": "active_candidate",
                            "source_field": "pixiv_tag",
                            "extraction_action": "direct_name",
                            "confidence": 0.9,
                        }
                    ],
                    "rejected_summary": {},
                }
            ]
        },
        delay=0.05,
    )
    args = argparse.Namespace(
        reuse_checkpoint=False,
        checkpoint_dir=str(tmp_path / "checkpoints"),
        checkpoint_status_json=str(tmp_path / "run-checkpoint-status.json"),
        llm_retries=0,
        max_tokens=1000,
        llm_timeout_seconds=5.0,
        apply_db=False,
    )
    checkpoint = {"runs": {}}
    mode_state = {"units": {}}
    in_flight = {}

    async def run_two():
        return await asyncio.gather(
            runner._process_unit(
                args=args,
                provider=provider,
                provider_summary={"llm_provider_label": "fake", "model_label": "fake"},
                provider_mode="primary_serial",
                run_id="inflight-run",
                run_label="unit",
                unit=unit,
                checkpoint=checkpoint,
                mode_state=mode_state,
                in_flight=in_flight,
            ),
            runner._process_unit(
                args=args,
                provider=provider,
                provider_summary={"llm_provider_label": "fake", "model_label": "fake"},
                provider_mode="primary_serial",
                run_id="inflight-run",
                run_label="unit",
                unit=unit,
                checkpoint=checkpoint,
                mode_state=mode_state,
                in_flight=in_flight,
            ),
        )

    first, second = asyncio.run(run_two())

    assert provider.complete_json_calls == 1
    assert mode_state["inflight_dedupe_hits"] == 1
    assert first["cache_fingerprint"] == second["cache_fingerprint"]


def test_same_fingerprint_reuses_cache_across_serial_and_concurrent_modes(tmp_path):
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_cross_mode_cache_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    unit = build_extraction_units([_simple_pixiv_tag_group(1)])[0][0]
    provider = FakeProvider(
        {
            "records": [
                {
                    "group_key": unit.unit_group.group_key,
                    "provider": "pixiv",
                    "verdict": "name_candidate_found",
                    "candidates": [
                        {
                            "raw_value": "\u795e\u91cc\u7dbe\u83ef",
                            "display_name": "\u795e\u91cc\u7dbe\u83ef",
                            "canonical_key": "kamisato_ayaka",
                            "role": "character",
                            "status": "active_candidate",
                            "source_field": "pixiv_tag",
                            "extraction_action": "direct_name",
                            "confidence": 0.9,
                        }
                    ],
                    "rejected_summary": {},
                }
            ]
        }
    )
    args = argparse.Namespace(
        reuse_checkpoint=True,
        checkpoint_dir=str(tmp_path / "checkpoints"),
        checkpoint_status_json=str(tmp_path / "run-checkpoint-status.json"),
        llm_retries=0,
        max_tokens=1000,
        llm_timeout_seconds=5.0,
        apply_db=False,
    )
    checkpoint = {"runs": {}}
    provider_summary = {
        "llm_provider_label": "primary_openai",
        "model_label": "primary_model_configured",
        "primary_provider_type": "openai_compatible",
    }

    first = asyncio.run(
        runner._process_unit(
            args=args,
            provider=provider,
            provider_summary=provider_summary,
            provider_mode="primary_serial",
            run_id="cross-mode-run",
            run_label="unit",
            unit=unit,
            checkpoint=checkpoint,
            mode_state={"units": {}},
            in_flight={},
        )
    )
    second_mode_state = {"units": {}}
    second = asyncio.run(
        runner._process_unit(
            args=args,
            provider=provider,
            provider_summary={**provider_summary, "provider_mode": "primary_concurrent"},
            provider_mode="primary_concurrent",
            run_id="cross-mode-run",
            run_label="unit",
            unit=unit,
            checkpoint=checkpoint,
            mode_state=second_mode_state,
            in_flight={},
        )
    )

    assert first["status"] == "completed"
    assert second["from_checkpoint_cache"] is True
    assert second["api_call_attempts"] == 0
    assert second["cached_api_call_attempts"] == first["api_call_attempts"]
    assert provider.complete_json_calls == 1
    assert second_mode_state["cache_hits"] == 1


def test_failed_extraction_units_preserve_error_record_verdicts():
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_failed_unit_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    group = _simple_pixiv_tag_group(1)
    unit = build_extraction_units([group])[0][0]
    failure_bundle = runner._failure_bundle_for_unit(
        run_id="failed-unit-run",
        run_label="unit",
        provider_mode="primary_concurrent",
        unit=unit,
        row={
            "status": "retryable_error",
            "error_type": "TimeoutError",
            "error": "timeout",
            "extraction_key": unit.extraction_key,
        },
    )
    record_bundle = reattach_unit_bundles_to_records(
        [group],
        [unit],
        {unit.extraction_key: failure_bundle},
        run_id="record-run",
        run_label="unit",
    )

    assert record_bundle.record_verdicts[0].extraction_verdict == "extraction_error_retryable"
    assert record_bundle.record_verdicts[0].no_name_reason == "retryable_unit_error"
    assert record_bundle.validation_failures[0]["error_code"] == "TimeoutError"


def test_cached_manifest_revalidation_rechecks_current_media_eligibility():
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_manifest_revalidation_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    engine, db = _db()
    try:
        _media(db, 101, ContentClassEnum.non_anime)
        _source_record(db, 1, 101, "r1")
        db.commit()
        args = argparse.Namespace(max_records=10, max_unique_strings=100)

        groups, summary = runner._revalidate_cached_manifest_groups(db, [_simple_pixiv_tag_group(1)], args)

        assert groups == []
        assert summary["cached_manifest_revalidated"] is True
        assert summary["cached_manifest_drop_counts"]["excluded_non_anime"] == 1
    finally:
        db.close()
        engine.dispose()


def test_cached_manifest_revalidation_rechecks_unique_string_cap_for_first_group():
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_manifest_cap_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    engine, db = _db()
    try:
        _media(db, 101, ContentClassEnum.anime)
        _source_record(db, 1, 101, "r1")
        db.commit()
        oversized = SourceCandidateInputGroup(
            group_key="source_record:1",
            provider="pixiv",
            source_metadata_record_id=1,
            media_id=101,
            tags=(
                {"raw_tag": "Kamisato Ayaka"},
                {"raw_tag": "\u795e\u91cc\u7dbe\u83ef"},
            ),
            data_origin="real_dev_db",
        )
        args = argparse.Namespace(max_records=10, max_unique_strings=1)

        groups, summary = runner._revalidate_cached_manifest_groups(db, [oversized], args)

        assert groups == []
        assert summary["cached_manifest_drop_counts"]["max_unique_strings_group_oversized"] == 1
    finally:
        db.close()
        engine.dispose()


def test_public_summary_redacts_repeated_units_counts_preflight_and_uses_primary_readiness():
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_public_summary_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    summary = runner._public_summary(
        run_id="summary-run",
        branch="codex/phase44p2r-f7a-llm-source-name-candidates",
        head_sha="abc123",
        db_identity={"database_url_redacted": True},
        input_summary={
            "raw_string_occurrences_total": 10,
            "unique_extraction_units_total": 2,
            "deterministic_resolved_units": 1,
            "llm_required_units": 1,
            "llm_calls_avoided_by_dedupe": 8,
            "average_source_records_per_extraction_unit": 5,
            "top_repeated_units": [
                {
                    "normalized_value": "\u795e\u91cc\u7dbe\u83ef",
                    "source_field": "pixiv_tag",
                    "provider": "pixiv",
                    "source_count": 9,
                    "llm_calls_avoided": 8,
                }
            ],
        },
        mode_results=[
            {
                "summary": {
                    "provider_mode": "primary_concurrent",
                    "group_count": 5,
                    "llm_calls_attempted": 1,
                    "terminal_error_count": 0,
                    "retryable_error_count": 0,
                    "invalid_json_count": 0,
                    "schema_failure_count": 0,
                }
            },
            {
                "summary": {
                    "provider_mode": "fallback_serial",
                    "group_count": 5,
                    "llm_calls_attempted": 1,
                    "terminal_error_count": 2,
                    "retryable_error_count": 1,
                    "invalid_json_count": 1,
                    "schema_failure_count": 1,
                }
            },
        ],
        provider_preflight={"llm_preflight_calls": 1},
        db_write_summary={"forbidden_truth_table_write_count": 0},
        artifact_summary={},
        timing={},
    )

    public_json = json.dumps(summary, ensure_ascii=False)
    assert "\u795e\u91cc\u7dbe\u83ef" not in public_json
    assert "top_repeated_units" not in summary["dedupe_metrics"]
    assert summary["dedupe_metrics"]["top_repeated_units_public_redacted"] is True
    assert summary["safety"]["llm_provider_calls"] is True
    assert summary["safety"]["llm_preflight_calls"] == 1
    assert summary["readiness"]["f7a_mergeable"] is True
    assert summary["validated_code_head_sha"] == "abc123"
    assert summary["report_validation_scope"] == "code_and_runner_state_at_validation_time"
    assert summary["readiness"]["fallback_provider_mode"] == "diagnostic_only"
    assert summary["readiness"]["fallback_blocks_readiness"] is False
    assert summary["readiness"]["recommended_default_provider"] == "primary_openai"


def test_public_summary_blocks_readiness_on_primary_retryable_failures():
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_public_summary_retryable_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    summary = runner._public_summary(
        run_id="summary-run",
        branch="codex/phase44p2r-f7a-llm-source-name-candidates",
        head_sha="abc123",
        db_identity={"database_url_redacted": True},
        input_summary={},
        mode_results=[
            {
                "summary": {
                    "provider_mode": "primary_concurrent",
                    "group_count": 5,
                    "llm_calls_attempted": 1,
                    "terminal_error_count": 0,
                    "retryable_error_count": 1,
                    "invalid_json_count": 0,
                    "schema_failure_count": 0,
                }
            }
        ],
        provider_preflight={"llm_preflight_calls": 1},
        db_write_summary={"forbidden_truth_table_write_count": 0},
        artifact_summary={},
        timing={},
    )

    assert summary["readiness"]["f7a_mergeable"] is False
    assert summary["readiness"]["primary_blocking_error_total"] == 1


def test_public_summary_blocks_readiness_on_primary_quality_guards():
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_public_summary_quality_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    summary = runner._public_summary(
        run_id="summary-run",
        branch="codex/phase44p2r-f7a-llm-source-name-candidates",
        head_sha="abc123",
        db_identity={"database_url_redacted": True},
        input_summary={},
        mode_results=[
            {
                "summary": {
                    "provider_mode": "primary_concurrent",
                    "group_count": 5,
                    "llm_calls_attempted": 1,
                    "terminal_error_count": 0,
                    "retryable_error_count": 0,
                    "invalid_json_count": 0,
                    "schema_failure_count": 0,
                    "unknown_name_like_active_count": 0,
                    "pixiv_title_active_work_title_count": 1,
                }
            }
        ],
        provider_preflight={"llm_preflight_calls": 1},
        db_write_summary={"forbidden_truth_table_write_count": 0},
        artifact_summary={},
        timing={},
    )

    assert summary["readiness"]["f7a_mergeable"] is False
    assert summary["readiness"]["primary_quality_blocker_total"] == 1


def test_provider_json_preflight_retries_format_error():
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_preflight_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    class FlakyProvider(FakeProvider):
        async def complete_json(self, messages, *, temperature=0.3, max_tokens=4096):
            self.complete_json_calls += 1
            if self.complete_json_calls == 1:
                raise LLMResponseFormatError("empty_json_response")
            return {"ok": True, "stage": "f7a_preflight"}

    provider = FlakyProvider()

    result = asyncio.run(
        runner._provider_json_preflight(
            provider=provider,
            provider_mode="fallback_serial",
            timeout_seconds=5,
        )
    )

    assert result["preflight"] == "pass"
    assert result["attempts"] == 2
    assert provider.complete_json_calls == 2


def test_provider_json_preflight_reports_controlled_failure():
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_preflight_failure_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    provider = FakeProvider(fail_json=True)

    result = asyncio.run(
        runner._provider_json_preflight(
            provider=provider,
            provider_mode="fallback_serial",
            timeout_seconds=5,
        )
    )

    assert result["preflight"] == "fail"
    assert result["error_type"] == "LLMResponseFormatError"
    assert provider.complete_json_calls == 2


def test_mode_summary_counts_unit_level_terminal_errors():
    import importlib.util

    script_path = ROOT / "scripts" / "run_phase44p2r_f7a_llm_source_name_candidates.py"
    spec = importlib.util.spec_from_file_location("f7a_runner_summary_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    summary = runner._mode_summary(
        "fallback_serial",
        [
            {
                "status": "completed",
                "elapsed_seconds": 1.0,
                "bundle": {
                    "groups": [{"group_key": "g1"}],
                    "record_verdicts": [{"extraction_verdict": "multiple_candidates_found"}],
                    "candidates": [],
                    "rejected_tags": [],
                    "meta_tags": [],
                    "ambiguous_items": [],
                    "validation_failures": [],
                },
            },
            {
                "status": "terminal_error",
                "elapsed_seconds": 2.0,
                "error_type": "LLMResponseFormatError",
                "error": "empty_json_response",
            },
            {
                "status": "retryable_error",
                "elapsed_seconds": 3.0,
                "error_type": "TimeoutError",
                "error": "timeout",
            },
        ],
        {"provider_mode": "fallback", "llm_provider_label": "fallback", "model_label": "fallback_model_configured"},
        1,
    )

    assert summary["completed_unit_count"] == 1
    assert summary["terminal_error_count"] == 1
    assert summary["retryable_error_count"] == 1
    assert summary["invalid_json_count"] == 1
    assert summary["timeout_count"] == 1
