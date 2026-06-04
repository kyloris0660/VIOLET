"""Focused tests for Phase 4.4-P2R-F7a source name candidate extraction."""

from __future__ import annotations

import sys
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
from app.models import (  # noqa: E402
    SourceNameCandidate,
    SourceNameCandidateExtractionRun,
    SourceNameCandidateRecordVerdict,
)
from app.services.llm_translation_provider import BaseLLMProvider, LLMResponseFormatError  # noqa: E402
from app.services.source_name_candidate_extraction_service import (  # noqa: E402
    SourceCandidateInputGroup,
    collect_source_candidate_input_groups,
    persist_extraction_bundle,
    popularity_suffix_prefix,
    run_extraction_sync,
    table_counts,
    validate_extraction_record,
)


class FakeProvider(BaseLLMProvider):
    def __init__(self, payload=None, *, fail_json: bool = False, chat_payload: str = ""):
        self.payload = payload
        self.fail_json = fail_json
        self.chat_payload = chat_payload
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
