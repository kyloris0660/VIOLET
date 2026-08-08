"""Focused tests for Phase 4.5-SCV2-R1 post-PX1 SourceConcept triage."""

from __future__ import annotations

import inspect
import json
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
from app.enums import FileTypeEnum  # noqa: E402
from app.models import (  # noqa: E402
    Media,
    SourceMetadataRecord,
    SourceNameObservation,
    SourceSearchableNameAssertion,
)
from app.services.source_concept_resolver_service import (  # noqa: E402
    build_source_concept_signals,
    resolve_source_concepts,
)
from scripts import run_phase45_scv2_r1_post_px1_source_concept_triage as runner  # noqa: E402


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal()


def _media(media_id: int = 1) -> Media:
    return Media(
        id=media_id,
        filename=f"m{media_id}.jpg",
        path=f"/tmp/m{media_id}.jpg",
        file_type=FileTypeEnum.image,
    )


def _seed_px1_needs_review_assertion(session) -> tuple[int, int]:
    media = _media(10)
    metadata = SourceMetadataRecord(
        id=50,
        provider="pixiv",
        provider_run_id="px1-test",
        run_label=runner.PX1_SLUG,
        provider_record_key="gallery-dl-real-pixiv:metadata:12345678:p0:m10",
        media_id=media.id,
        source_work_id="12345678",
        source_page_index=0,
        title="Test Work",
        artist_name="Test Artist",
        metadata_kind="provider_metadata",
        data_type_label="real_live_or_local_provider_data",
        status="observed",
    )
    name = SourceNameObservation(
        id=60,
        source_metadata_record_id=metadata.id,
        provider="pixiv",
        observation_key="px1:name:artist:test_artist",
        media_id=media.id,
        raw_name="Test Artist",
        normalized_name="Test Artist",
        canonical_name_key="test_artist",
        name_role="artist",
        source_field="artist_name",
        requires_review=True,
        status="observed",
    )
    assertion = SourceSearchableNameAssertion(
        id=70,
        provider="pixiv",
        source_metadata_record_id=metadata.id,
        source_name_observation_id=name.id,
        assertion_key="px1:assertion:artist:test_artist",
        raw_input="Test Artist",
        normalized_input="Test Artist",
        canonical_name_key="test_artist",
        asserted_name="Test Artist",
        asserted_role="artist",
        status="needs_review",
        confidence="medium",
        confidence_score=0.85,
        structured_output_schema_version="phase45_px1_direct_source_metadata_v2",
        requires_review=True,
    )
    session.add_all([media, metadata, name, assertion])
    session.commit()
    return metadata.id, assertion.id


def test_px1_needs_review_assertions_are_review_scoped_resolver_input() -> None:
    _engine, session = _db()
    metadata_id, _assertion_id = _seed_px1_needs_review_assertion(session)

    signals = build_source_concept_signals(session, run_id="r1-test")
    summary = runner.summarize_signals(signals, {metadata_id})

    assert summary["px1_needs_review_source_assertion_signal_count"] == 1
    assert summary["px1_active_source_assertion_signal_count"] == 0
    assert summary["px1_assertions_included_only_as_review_scoped_input"] is True


def test_needs_review_assertions_do_not_become_searchable_active() -> None:
    _engine, session = _db()
    _metadata_id, assertion_id = _seed_px1_needs_review_assertion(session)

    signals = build_source_concept_signals(session, run_id="r1-test")
    result = resolve_source_concepts(signals, run_id="r1-test")
    assertion = session.get(SourceSearchableNameAssertion, assertion_id)

    assert assertion.status == "needs_review"
    assert all(signal.status != "searchable_active" for signal in result.signals)
    assert any(item.status == "needs_review" for item in result.search_index)
    assert all(item.status != "searchable_active" for item in result.search_index)


def test_r1_allowed_write_tables_are_only_source_concept_tables() -> None:
    assert set(runner.ALLOWED_WRITE_TABLES) == {
        "blombooru_source_concept_resolution_runs",
        "blombooru_source_concept_signals",
        "blombooru_source_concepts",
        "blombooru_source_concept_aliases",
        "blombooru_source_concept_evidence",
        "blombooru_source_concept_signal_links",
        "blombooru_source_concept_search_index",
    }


def test_entity_media_tags_and_source_metadata_tables_are_forbidden() -> None:
    forbidden = set(runner.PROMPT_FORBIDDEN_WRITE_TABLES)

    assert "blombooru_entities" in forbidden
    assert "blombooru_entity_aliases" in forbidden
    assert "blombooru_media_entity_assignments" in forbidden
    assert "blombooru_media_tags" in forbidden
    assert "blombooru_source_metadata_records" in forbidden
    assert "blombooru_source_searchable_name_assertions" in forbidden


def test_mutation_proof_allows_only_source_concept_tables() -> None:
    before = {
        "tables": {
            "blombooru_source_concepts": {"status": "present", "count": 10, "fingerprint": "a"},
            "blombooru_source_concept_aliases": {"status": "present", "count": 20, "fingerprint": "b"},
        }
    }
    after = {
        "tables": {
            "blombooru_source_concepts": {"status": "present", "count": 11, "fingerprint": "c"},
            "blombooru_source_concept_aliases": {"status": "present", "count": 20, "fingerprint": "b"},
        }
    }

    proof = runner.compare_table_state(before, after)

    assert proof["passed"] is True
    assert [row["table"] for row in proof["allowed_changed_tables"]] == ["blombooru_source_concepts"]
    assert proof["unexpected_changed_tables"] == []


def test_mutation_proof_fails_on_forbidden_or_unexpected_writes() -> None:
    before = {
        "tables": {
            "blombooru_media_tags": {"status": "present", "count": 100, "fingerprint": "a"},
            "blombooru_provider_cache": {"status": "present", "count": 2, "fingerprint": "b"},
        }
    }
    after = {
        "tables": {
            "blombooru_media_tags": {"status": "present", "count": 101, "fingerprint": "c"},
            "blombooru_provider_cache": {"status": "present", "count": 2, "fingerprint": "d"},
        }
    }

    proof = runner.compare_table_state(before, after)

    assert proof["passed"] is False
    assert {row["table"] for row in proof["forbidden_changed_tables"]} == {
        "blombooru_media_tags",
        "blombooru_provider_cache",
    }
    assert {row["table"] for row in proof["unexpected_changed_tables"]} == {
        "blombooru_media_tags",
        "blombooru_provider_cache",
    }


def test_media_tags_same_row_provenance_update_changes_mutation_proof() -> None:
    columns = ("media_id", "tag_id", "source", "confidence", "is_locked", "is_suggestion")
    before_content = runner.content_fingerprint_for_rows(
        [
            {
                "media_id": 1,
                "tag_id": 2,
                "source": "ai_wd",
                "confidence": 0.91,
                "is_locked": False,
                "is_suggestion": True,
            }
        ],
        columns,
    )
    after_content = runner.content_fingerprint_for_rows(
        [
            {
                "media_id": 1,
                "tag_id": 2,
                "source": "manual",
                "confidence": 1.0,
                "is_locked": True,
                "is_suggestion": False,
            }
        ],
        columns,
    )
    before = {
        "tables": {
            "blombooru_media_tags": {
                "status": "present",
                "count": 1,
                "fingerprint": before_content,
                "content_fingerprint": before_content,
                "content_fingerprint_columns": list(columns),
            }
        }
    }
    after = {
        "tables": {
            "blombooru_media_tags": {
                "status": "present",
                "count": 1,
                "fingerprint": after_content,
                "content_fingerprint": after_content,
                "content_fingerprint_columns": list(columns),
            }
        }
    }

    proof = runner.compare_table_state(before, after)

    assert before_content != after_content
    assert proof["passed"] is False
    assert proof["changed_tables"][0]["table"] == "blombooru_media_tags"
    assert proof["changed_tables"][0]["content_fingerprint_changed"] is True
    assert proof["forbidden_changed_tables"][0]["table"] == "blombooru_media_tags"


def test_source_metadata_rows_are_read_only_inputs() -> None:
    before = {
        "tables": {
            "blombooru_source_metadata_records": {"status": "present", "count": 470, "fingerprint": "a"},
        }
    }
    after = {
        "tables": {
            "blombooru_source_metadata_records": {"status": "present", "count": 470, "fingerprint": "b"},
        }
    }

    proof = runner.compare_table_state(before, after)

    assert proof["passed"] is False
    assert proof["source_metadata_readonly_changed_tables"][0]["table"] == "blombooru_source_metadata_records"


def test_finalize_transaction_commits_execute_and_rolls_back_dry_run() -> None:
    class FakeTransaction:
        def __init__(self) -> None:
            self.actions: list[str] = []

        def commit(self) -> None:
            self.actions.append("commit")

        def rollback(self) -> None:
            self.actions.append("rollback")

    execute_tx = FakeTransaction()
    execute_result = runner.finalize_transaction(execute_tx, mode="execute", validation_passed=True)
    dry_run_tx = FakeTransaction()
    dry_run_result = runner.finalize_transaction(dry_run_tx, mode="dry_run", validation_passed=True)
    failed_tx = FakeTransaction()
    failed_result = runner.finalize_transaction(failed_tx, mode="execute", validation_passed=False)

    assert execute_tx.actions == ["commit"]
    assert execute_result["execute_transaction_committed"] is True
    assert dry_run_tx.actions == ["rollback"]
    assert dry_run_result["execute_transaction_committed"] is False
    assert failed_tx.actions == ["rollback"]
    assert failed_result["transaction_final_action"] == "rollback"


def test_zip_directory_preserves_full_phase_slug_and_unrelated_bundle(tmp_path: Path) -> None:
    output_dir = tmp_path / runner.PHASE_SLUG
    output_dir.mkdir()
    (output_dir / "artifact.json").write_text("{}", encoding="utf-8")
    unrelated = tmp_path / "phase-4.zip"
    unrelated.write_text("sentinel", encoding="utf-8")

    zip_path = runner.zip_directory(output_dir)

    assert zip_path.name == runner.PHASE_SLUG + ".zip"
    assert zip_path.exists()
    assert unrelated.read_text(encoding="utf-8") == "sentinel"


def test_report_generation_metadata_records_runtime_sha_and_no_artifact_reuse() -> None:
    metadata = runner.report_generation_metadata(
        mode="execute",
        db_identity_after={"git_branch": "branch-x", "git_sha": "sha-x"},
    )

    assert metadata["branch"] == "branch-x"
    assert metadata["runtime_git_sha_used_for_execute"] == "sha-x"
    assert metadata["operational_result_reused_older_artifacts"] is False
    assert metadata["final_pr_head_sha_if_different"]


def test_post_commit_count_mismatch_detection() -> None:
    expected = {"total_source_concepts": 10, "active_concepts": 4}
    actual = {"total_source_concepts": 10, "active_concepts": 5}

    assert runner.mismatch_keys(expected, actual) == ["active_concepts"]


def test_alias_gap_delta_calculation() -> None:
    before = {"total_gap_signals": 10, "gap_buckets": {"a": 7, "b": 3}}
    after = {"total_gap_signals": 8, "gap_buckets": {"a": 5, "b": 4, "c": 1}}

    delta = runner.build_alias_gap_delta(before, after)

    assert delta["total_gap_signals_delta"] == -2
    assert delta["gap_bucket_delta"] == {"a": -2, "b": 1, "c": 1}
    assert delta["improved_bucket_count"] == 1
    assert delta["regressed_bucket_count"] == 2


def test_needs_review_triage_delta_calculation() -> None:
    before = {"total_needs_review_concepts": 5, "needs_review_with_media": 4}
    after = {"total_needs_review_concepts": 7, "needs_review_with_media": 3}

    delta = runner.build_needs_review_delta(before, after)

    assert delta["total_needs_review_concepts_delta"] == 2
    assert delta["numeric_delta"]["needs_review_with_media"] == -1


def test_search_seed_symmetry_checker_handles_aliases_without_truth_writes(monkeypatch) -> None:
    def fake_concepts_for_term(_conn, value, statuses):
        return ([1] if value in {"Nahida", "nahida_(genshin_impact)"} else [], [])

    def fake_media_for_ids(_conn, concept_ids, statuses):
        return {10, 11} if concept_ids else set()

    monkeypatch.setattr(runner.scv1, "concept_ids_for_term", fake_concepts_for_term)
    monkeypatch.setattr(runner.scv1, "concept_media_set_for_ids", fake_media_for_ids)

    result = runner.evaluate_seed_groups(None, {"nahida": ["Nahida", "nahida_(genshin_impact)"]})

    assert result["aggregate"]["matched_seeds"] == 2
    assert result["groups"]["nahida"]["symmetric_media_result"] is True
    source = inspect.getsource(runner.evaluate_seed_groups)
    assert "INSERT" not in source
    assert "UPDATE" not in source


def test_public_redaction_catches_paths_filenames_raw_values_and_tokens() -> None:
    unsafe = (
        r"C:\Users\kyloris\Pictures\private.png "
        "Authorization: Bearer abcdefghijk "
        "api_key=secret-token-12345 "
        "vacation_2024_jpg"
    )

    findings = runner.scv1.scan_public_text(unsafe)
    finding_types = {item["type"] for item in findings}

    assert "local_path_or_private_root" in finding_types
    assert "media_filename_like" in finding_types
    assert "canonical_filename_like" in finding_types
    assert "secret_assignment_like" in finding_types


def test_summary_json_required_fields() -> None:
    payload = {field: None for field in runner.SUMMARY_REQUIRED_FIELDS}

    validation = runner.validate_summary_schema(payload)

    assert validation["passed"] is True
    assert validation["missing_fields"] == []


def test_public_source_concept_summary_omits_private_sample_rows() -> None:
    snapshot = {
        "total_source_concepts": 2,
        "by_status": {"active": 1, "needs_review": 1},
        "same_alias_key_across_multiple_concepts": [
            {"alias_key": "raw_private_name", "concept_ids": [1, 2], "concept_count": 2}
        ],
        "high_media_count_concepts": [
            {"concept_id": 1, "display": "raw_private_display", "media_count": 10}
        ],
        "px1_influenced_concept_ids_private": [1, 2],
    }

    public = runner.public_source_concept_summary(snapshot)

    assert public["total_source_concepts"] == 2
    assert "same_alias_key_across_multiple_concepts" not in public
    assert "high_media_count_concepts" not in public
    assert "px1_influenced_concept_ids_private" not in public
    assert public["omitted_private_sample_sets"]["same_alias_key_across_multiple_concepts"] == 1


def test_runner_does_not_import_provider_network_or_truth_promoters() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert "gallery_dl" not in source
    assert "subprocess.run" not in source
    assert "requests." not in source
    assert "MediaEntityAssignment(" not in source
    assert "blombooru_media_tags INSERT" not in source


def test_handoff_and_roadmap_follow_current_phase_state_not_r1_history() -> None:
    handoff = (ROOT / "docs" / "current-handoff.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "docs" / "project-roadmap.md").read_text(encoding="utf-8")
    state = json.loads(
        (ROOT / "docs" / "state" / "current-phase.json").read_text(
            encoding="utf-8"
        )
    )

    if state["pr_number"] is None:
        assert "Draft PR pending creation" in handoff
    else:
        assert f"Draft PR #{state['pr_number']}" in handoff
    assert state["phase_id"] in handoff
    assert state["current_status"] in handoff
    assert state["active_blocker"]["code"] in handoff
    assert state["next_required_checkpoint"] in handoff
    assert f"<!-- CURRENT_PHASE: {state['phase_id']} -->" in roadmap
    assert "PR #133" not in handoff
    assert "target_met_constraint_aware_r2" not in handoff
