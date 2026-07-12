"""Durable Pixiv metadata-on-import gate and acquisition safety tests."""

from __future__ import annotations

import json
import hashlib
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import SourceMetadataRecord, SourceNameObservation, SourceTagObservation
from app.services.pixiv_metadata_ingestion_service import (
    PixivMetadataGateError,
    PixivMetadataState,
    build_gallery_dl_metadata_command,
    classify_gallery_dl_failure,
    llm_budget_policy,
    pending_distinct_work_ids,
    persist_complete_work,
    promotion_manifest,
    queue_media_for_pixiv_metadata,
    require_rotation_confirmation,
    run_bounded_acquisition,
    summarize_batch_closure,
)
from scripts import run_pixiv_metadata_ingestion as ingestion_runner
from scripts.run_pixiv_metadata_ingestion import scan_text_for_fingerprints


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            SourceMetadataRecord.__table__,
            SourceNameObservation.__table__,
            SourceTagObservation.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_import_gate_persists_pending_then_closes_all_pages(db) -> None:
    decision = queue_media_for_pixiv_metadata(
        db,
        {"id": 7, "filename": "prefix-123456789_p1.jpg", "path": "media/original/prefix-123456789_p1.jpg"},
    )
    db.commit()

    assert decision.state == PixivMetadataState.PENDING.value
    assert pending_distinct_work_ids(db) == ("123456789",)
    assert summarize_batch_closure(db, [7])["closed"] is False

    linked = persist_complete_work(
        db,
        "123456789",
        [
            {
                "work_id": "123456789",
                "page_index": 1,
                "title": "Work",
                "creator_id": "42",
                "creator_name": "Display",
                "creator_account": "handle",
                "creator_profile_identity": "https://www.pixiv.net/users/42",
                "tags": ("tag_a", "tag_b"),
                "raw": {"id": 123456789, "num": 1},
            }
        ],
    )
    db.commit()

    assert linked == 1
    assert summarize_batch_closure(db, [7])["closed"] is True
    assert pending_distinct_work_ids(db) == ()
    fields = {row.source_field for row in db.query(SourceNameObservation).all()}
    assert {"pixiv_user_metadata", "pixiv_user_account", "pixiv_title"} <= fields
    assert db.query(SourceTagObservation).count() == 2


def test_import_gate_records_every_conflicted_work_membership(db) -> None:
    decision = queue_media_for_pixiv_metadata(
        db,
        {"id": 8, "filename": "123456789_p0__987654321_p2.jpg", "path": "media/original/conflict.jpg"},
    )
    db.commit()

    assert decision.state == PixivMetadataState.CONFLICT.value
    assert decision.work_pages == (("123456789", 0), ("987654321", 2))
    records = db.query(SourceMetadataRecord).order_by(SourceMetadataRecord.source_work_id).all()
    assert [record.source_work_id for record in records] == ["123456789", "987654321"]
    assert {record.status for record in records} == {PixivMetadataState.CONFLICT.value}
    closure = summarize_batch_closure(db, [8])
    assert closure["pixiv_candidate_count"] == 1
    assert closure["open_candidate_count"] == 1
    assert closure["closed"] is False


def test_complete_compatible_record_is_reused_without_reacquisition(db) -> None:
    db.add(
        SourceMetadataRecord(
            provider="pixiv",
            provider_record_key="existing:123456789:p0",
            source_work_id="123456789",
            source_page_index=0,
            metadata_kind="provider_metadata",
            data_type_label="authenticated_provider_metadata",
            status="observed",
        )
    )
    db.commit()
    decision = queue_media_for_pixiv_metadata(
        db,
        {"id": 9, "filename": "123456789_p0.jpg", "path": "media/original/123456789_p0.jpg"},
    )
    db.commit()

    assert decision.state == PixivMetadataState.COMPLETE.value
    assert decision.reused_complete_record_ids
    assert pending_distinct_work_ids(db) == ()
    assert summarize_batch_closure(db, [9])["closed"] is True


def test_non_pixiv_import_is_not_applicable_and_closed(db) -> None:
    decision = queue_media_for_pixiv_metadata(db, {"id": 10, "filename": "ordinary.jpg", "path": "media/original/ordinary.jpg"})
    db.commit()
    assert decision.state == PixivMetadataState.NOT_APPLICABLE.value
    closure = summarize_batch_closure(db, [10])
    assert closure["pixiv_candidate_count"] == 0
    assert closure["closed"] is True


def test_rotation_gate_and_metadata_only_command() -> None:
    with pytest.raises(PixivMetadataGateError, match="blocked_credential_rotation_confirmation_required"):
        require_rotation_confirmation({})
    command = build_gallery_dl_metadata_command(("python", "-m", "gallery_dl"), "123456789")
    assert "--dump-json" in command
    assert "--no-download" in command
    assert not any(value in command for value in ("--range", "-D", "--dest"))
    old_value = "old-compromised-token-value"
    fingerprints = {hashlib.sha256(old_value.encode("utf-8")).hexdigest()}
    assert scan_text_for_fingerprints(f"prefix {old_value} suffix", fingerprints) == 1
    assert scan_text_for_fingerprints("clean retained log", fingerprints) == 0
    assert ingestion_runner._isolated_database_allowed("violet_ml1_test") is True
    assert ingestion_runner._isolated_database_allowed("violet_prod_ml1_test") is False
    assert ingestion_runner._isolated_database_allowed("blombooru") is False


def test_redacted_auth_failure_is_checkpointed_without_raw_output(db, monkeypatch) -> None:
    queue_media_for_pixiv_metadata(
        db,
        {"id": 14, "filename": "123456789_p0.jpg", "path": "media/original/123456789_p0.jpg"},
    )
    db.commit()

    monkeypatch.setattr(
        ingestion_runner.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout="", stderr="401 authentication failed"),
    )
    preflight, passed = ingestion_runner._redacted_auth_and_first_acquisition(
        db,
        entrypoint=("gallery-dl",),
        work_id="123456789",
        timeout_seconds=5,
    )

    assert passed is False
    assert preflight["authentication_test_passed"] is False
    assert preflight["raw_values_exposed"] is False
    record = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 14).one()
    assert record.status == PixivMetadataState.RETRYABLE.value


def test_bounded_acquisition_deduplicates_manifest_and_checkpoints(db) -> None:
    for media_id, page_index in ((11, 0), (12, 1)):
        queue_media_for_pixiv_metadata(
            db,
            {"id": media_id, "filename": f"123456789_p{page_index}.jpg", "path": f"media/original/123456789_p{page_index}.jpg"},
        )
    db.commit()
    payload = [
        [3, "url", {"id": 123456789, "num": 0, "title": "Work", "user": {"id": 42, "name": "Display", "account": "handle"}, "tags": ["a"]}],
        [3, "url", {"id": 123456789, "num": 1, "title": "Work", "user": {"id": 42, "name": "Display", "account": "handle"}, "tags": ["b"]}],
    ]
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    results = run_bounded_acquisition(
        db,
        ["123456789", "123456789"],
        entrypoint=("gallery-dl",),
        authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        command_runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert len(calls) == 1
    assert len(results) == 1 and results[0].state == PixivMetadataState.COMPLETE.value
    assert summarize_batch_closure(db, [11, 12])["closed"] is True


def test_terminal_classification_requires_authenticated_evidence() -> None:
    assert classify_gallery_dl_failure("404 not found", authentication_passed=False)[0] == PixivMetadataState.RETRYABLE.value
    assert classify_gallery_dl_failure("404 not found", authentication_passed=True)[0] == PixivMetadataState.TERMINAL.value
    assert classify_gallery_dl_failure("429 rate limit", authentication_passed=True)[1] == "retryable_rate_limit"


def test_rate_limit_retry_is_bounded_spaced_and_checkpointed(db) -> None:
    queue_media_for_pixiv_metadata(
        db,
        {"id": 13, "filename": "123456789_p0.jpg", "path": "media/original/123456789_p0.jpg"},
    )
    db.commit()
    attempts = 0
    sleeps: list[float] = []

    def runner(command, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="429 rate limit")
        payload = [[3, "url", {"id": 123456789, "num": 0, "title": "Work", "user": {"id": 42, "name": "Display"}}]]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    results = run_bounded_acquisition(
        db,
        ["123456789"],
        entrypoint=("gallery-dl",),
        authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        command_runner=runner,
        sleeper=sleeps.append,
    )

    assert attempts == 2
    assert sleeps and min(sleeps) >= 2.0
    assert results[0].attempt_count == 2
    assert results[0].state == PixivMetadataState.COMPLETE.value


def test_promotion_and_usd10_llm_policy_are_fail_closed() -> None:
    manifest = promotion_manifest()
    assert manifest["copy_development_database_row_ids"] is False
    assert "SourceConcept_component_membership" in manifest["required_recomputation_targets"]
    assert llm_budget_policy(10.0, finite_manifest=True, primary_provider=True, cache_first=True, fallback_provider=False, production_or_truth_write=False)["preauthorized"] is True
    assert llm_budget_policy(10.01, finite_manifest=True, primary_provider=True, cache_first=True, fallback_provider=False, production_or_truth_write=False)["approval_required"] is True
    assert llm_budget_policy(1.0, finite_manifest=True, primary_provider=True, cache_first=True, fallback_provider=True, production_or_truth_write=False)["approval_required"] is True
