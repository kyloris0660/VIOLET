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
    acquisition_work_lifecycle_counts,
    backfill_creator_source_observations,
    build_gallery_dl_metadata_command,
    classify_gallery_dl_failure,
    mark_work_state,
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
        attempted_record_ids=[db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 7).one().id],
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


def test_historical_gallery_dl_complete_kind_is_reused(db) -> None:
    db.add(SourceMetadataRecord(
        provider="pixiv", provider_record_key="historical:123456789:p0",
        source_work_id="123456789", source_page_index=0,
        metadata_kind="gallery_dl_real_pixiv_metadata",
        data_type_label="authenticated_provider_metadata", status="observed",
    ))
    db.commit()
    decision = queue_media_for_pixiv_metadata(db, {"id": 16, "filename": "123456789_p0.jpg", "path": "media/16.jpg"})
    db.commit()
    assert decision.state == PixivMetadataState.COMPLETE.value
    assert pending_distinct_work_ids(db) == ()


def test_historical_wrong_work_or_page_is_queued_as_conflict_not_acquisition(db) -> None:
    db.add(SourceMetadataRecord(
        provider="pixiv", provider_record_key="historical:wrong:p1", media_id=17,
        source_work_id="999999999", source_page_index=1,
        metadata_kind="gallery_dl_real_pixiv_metadata",
        data_type_label="authenticated_provider_metadata", status="observed",
    ))
    db.commit()
    decision = queue_media_for_pixiv_metadata(db, {"id": 17, "filename": "123456789_p0.jpg", "path": "media/17.jpg"})
    db.commit()
    assert decision.state == PixivMetadataState.CONFLICT.value
    assert pending_distinct_work_ids(db) == ()


def test_current_media_mismatch_wins_over_compatible_record_on_other_media(db) -> None:
    db.add_all(
        [
            SourceMetadataRecord(
                provider="pixiv",
                provider_record_key="historical:compatible:other-media",
                media_id=170,
                source_work_id="123456789",
                source_page_index=0,
                metadata_kind="gallery_dl_real_pixiv_metadata",
                data_type_label="authenticated_provider_metadata",
                status="metadata_complete",
            ),
            SourceMetadataRecord(
                provider="pixiv",
                provider_record_key="historical:mismatch:current-media",
                media_id=171,
                source_work_id="999999999",
                source_page_index=1,
                metadata_kind="gallery_dl_real_pixiv_metadata",
                data_type_label="authenticated_provider_metadata",
                status="observed",
            ),
        ]
    )
    db.commit()

    decision = queue_media_for_pixiv_metadata(
        db, {"id": 171, "filename": "123456789_p0.jpg", "path": "media/171.jpg"}
    )

    assert decision.state == PixivMetadataState.CONFLICT.value
    assert decision.reused_complete_record_ids == ()
    assert pending_distinct_work_ids(db) == ()


def test_explicit_conflict_resolution_preserves_historical_metadata_and_links_exact_page(db) -> None:
    historical = SourceMetadataRecord(
        provider="pixiv", provider_record_key="historical:wrong:resolve", media_id=18,
        source_work_id="999999999", source_page_index=1,
        metadata_kind="gallery_dl_real_pixiv_metadata", data_type_label="authenticated_provider_metadata", status="observed",
    )
    db.add(historical); db.commit()
    queue_media_for_pixiv_metadata(db, {"id": 18, "filename": "123456789_p0.jpg", "path": "media/18.jpg"}); db.commit()
    payload = [[3, "url", {"id": 123456789, "num": 0, "title": "Exact", "user": {"id": 42, "name": "Creator"}}]]
    result = run_bounded_acquisition(
        db, ["123456789"], entrypoint=("gallery-dl",), authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        command_runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr=""),
        sleeper=lambda _seconds: None, max_attempts_per_work=1, allow_conflict_resolution=True,
    )[0]
    db.refresh(historical)
    queue = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.provider_record_key.like("pixiv-ingestion:%")).one()
    assert result.state == PixivMetadataState.COMPLETE.value
    assert queue.status == PixivMetadataState.COMPLETE.value
    assert historical.source_work_id == "999999999" and historical.status == "observed"


def test_explicit_conflict_resolution_can_persist_authenticated_terminal(db) -> None:
    db.add(SourceMetadataRecord(
        provider="pixiv", provider_record_key="historical:wrong:terminal", media_id=19,
        source_work_id="999999999", source_page_index=1,
        metadata_kind="gallery_dl_real_pixiv_metadata", data_type_label="authenticated_provider_metadata", status="observed",
    )); db.commit()
    queue_media_for_pixiv_metadata(db, {"id": 19, "filename": "123456789_p0.jpg", "path": "media/19.jpg"}); db.commit()
    result = run_bounded_acquisition(
        db, ["123456789"], entrypoint=("gallery-dl",), authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        command_runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="404 private"),
        sleeper=lambda _seconds: None, max_attempts_per_work=1, allow_conflict_resolution=True,
    )[0]
    queue = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.provider_record_key.like("pixiv-ingestion:%")).one()
    assert result.state == PixivMetadataState.TERMINAL.value
    assert queue.status == PixivMetadataState.TERMINAL.value


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


def test_credential_gate_is_the_only_operator_confirmation_before_provider_call(db) -> None:
    queue_media_for_pixiv_metadata(db, {"id": 15, "filename": "123456789_p0.jpg", "path": "media/15.jpg"})
    db.commit()
    calls = []
    with pytest.raises(PixivMetadataGateError, match="blocked_credential_rotation_confirmation_required"):
        run_bounded_acquisition(
            db, ["123456789"], entrypoint=("gallery-dl",), authentication_passed=True,
            env={},
            command_runner=lambda *args, **kwargs: calls.append(args),
        )
    assert calls == []
    assert pending_distinct_work_ids(db) == ("123456789",)


def test_explicit_auth_failure_in_canary_blocks_without_raw_output(db) -> None:
    queue_media_for_pixiv_metadata(
        db,
        {"id": 14, "filename": "123456789_p0.jpg", "path": "media/original/123456789_p0.jpg"},
    )
    db.commit()

    result = type("R", (), {"work_id": "123456789", "state": PixivMetadataState.RETRYABLE.value, "request_attempted": True, "attempt_count": 1, "error_class": "retryable_authentication", "systemic_stop": True})()
    results, evidence = ingestion_runner.run_deterministic_auth_canary(
        db, ["123456789"], entrypoint=("gallery-dl",),
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        acquire=lambda *_args, **_kwargs: [result],
    )
    assert results == [result]
    assert evidence["passed"] is False
    assert evidence["systemic_stop"] is True
    assert evidence["raw_values_exposed"] is False


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


@pytest.mark.parametrize(
    "stderr,expected_error",
    [
        ("401 authentication expired", "retryable_authentication"),
        ("429 rate limit", "retryable_rate_limit"),
        ("network connection timeout", "retryable_network_transport"),
    ],
)
def test_systemic_failure_stops_all_later_main_and_conflict_calls(db, stderr: str, expected_error: str) -> None:
    for media_id, work_id in ((21, "123456789"), (22, "223456789")):
        queue_media_for_pixiv_metadata(db, {"id": media_id, "filename": f"{work_id}_p0.jpg", "path": f"media/{work_id}_p0.jpg"})
    db.commit()
    calls = []

    def failed(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=stderr)

    results = run_bounded_acquisition(
        db,
        ["123456789", "223456789"],
        entrypoint=("gallery-dl",),
        authentication_passed=True,
        accept_local_credential_risk=True,
        command_runner=failed,
        sleeper=lambda _seconds: None,
        max_attempts_per_work=1,
    )
    assert len(calls) == 1
    assert len(results) == 1
    assert results[0].systemic_stop is True
    assert results[0].error_class == expected_error
    assert ingestion_runner.conflict_manifest_may_start(results) is False


def test_terminal_and_normalization_failures_do_not_stop_unrelated_works(db) -> None:
    for media_id, work_id in ((23, "123456789"), (24, "223456789"), (25, "323456789")):
        queue_media_for_pixiv_metadata(db, {"id": media_id, "filename": f"{work_id}_p0.jpg", "path": f"media/{work_id}_p0.jpg"})
    db.commit()
    calls = []

    def mixed(command, **_kwargs):
        work_id = command[-1].rsplit("/", 1)[-1]
        calls.append(work_id)
        if work_id == "123456789":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="404 unavailable")
        if work_id == "223456789":
            return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")
        payload = [[3, "url", {"id": int(work_id), "num": 0, "title": "ok"}]]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    results = run_bounded_acquisition(
        db,
        ["123456789", "223456789", "323456789"],
        entrypoint=("gallery-dl",),
        authentication_passed=True,
        accept_local_credential_risk=True,
        command_runner=mixed,
        sleeper=lambda _seconds: None,
        max_attempts_per_work=1,
    )
    assert calls == ["123456789", "223456789", "323456789"]
    assert [item.state for item in results] == [
        PixivMetadataState.TERMINAL.value,
        PixivMetadataState.NORMALIZATION_FAILED.value,
        PixivMetadataState.COMPLETE.value,
    ]
    assert ingestion_runner.conflict_manifest_may_start(results) is True


def test_gallery_dl_zero_exit_error_event_is_classified_terminal_not_normalization(db) -> None:
    queue_media_for_pixiv_metadata(db, {"id": 26, "filename": "123456789_p0.jpg", "path": "media/123456789_p0.jpg"})
    db.commit()
    payload = [[1, {"error": "NotFoundError", "message": "provider item unavailable"}]]
    results = run_bounded_acquisition(
        db,
        ["123456789"],
        entrypoint=("gallery-dl",),
        authentication_passed=True,
        accept_local_credential_risk=True,
        command_runner=lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr=""),
        sleeper=lambda _seconds: None,
        max_attempts_per_work=1,
    )
    queue = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.metadata_kind == "pixiv_ingestion_gate").one()
    assert results[0].state == PixivMetadataState.TERMINAL.value
    assert results[0].systemic_stop is False
    assert queue.status == PixivMetadataState.TERMINAL.value


def test_generic_queue_does_not_reopen_normalization_but_explicit_replay_can(db) -> None:
    queue_media_for_pixiv_metadata(db, {"id": 27, "filename": "123456789_p0.jpg", "path": "media/123456789_p0.jpg"})
    db.commit()
    queue = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.metadata_kind == "pixiv_ingestion_gate").one()
    queue.status = PixivMetadataState.NORMALIZATION_FAILED.value
    db.commit()
    assert queue_media_for_pixiv_metadata(db, {"id": 27, "filename": "123456789_p0.jpg", "path": "media/123456789_p0.jpg"}).state == PixivMetadataState.NORMALIZATION_FAILED.value
    payload = [[3, "url", {"id": 123456789, "num": 0, "title": "fixed"}]]
    result = run_bounded_acquisition(
        db,
        ["123456789"],
        entrypoint=("gallery-dl",),
        authentication_passed=True,
        accept_local_credential_risk=True,
        allow_normalization_replay=True,
        command_runner=lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr=""),
        sleeper=lambda _seconds: None,
        max_attempts_per_work=1,
    )[0]
    assert result.state == PixivMetadataState.COMPLETE.value


def test_page_failure_preserves_complete_page_and_updates_only_attempted_pending(db) -> None:
    for media_id, page in ((101, 0), (102, 1)):
        queue_media_for_pixiv_metadata(db, {"id": media_id, "filename": f"123456789_p{page}.jpg", "path": f"media/{media_id}.jpg"})
    db.flush()
    p0 = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 101).one()
    p1 = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 102).one()
    p0.status = PixivMetadataState.COMPLETE.value
    result = mark_work_state(
        db, "123456789", PixivMetadataState.RETRYABLE.value,
        reason="retryable_network_transport", attempted_record_ids=[p1.id],
    )
    db.flush()
    assert p0.status == PixivMetadataState.COMPLETE.value
    assert p1.status == PixivMetadataState.RETRYABLE.value
    assert result == {"attempted": 1, "updated": 1, "preserved_complete": 0, "preserved_terminal": 0, "preserved_conflict": 0, "not_found": 0}


def test_page_failure_preserves_terminal_page(db) -> None:
    for media_id, page in ((103, 0), (104, 1)):
        queue_media_for_pixiv_metadata(db, {"id": media_id, "filename": f"123456789_p{page}.jpg", "path": f"media/{media_id}.jpg"})
    db.flush()
    terminal = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 103).one()
    pending = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 104).one()
    terminal.status = PixivMetadataState.TERMINAL.value
    mark_work_state(db, "123456789", PixivMetadataState.RETRYABLE.value, reason="retry", attempted_record_ids=[pending.id])
    assert terminal.status == PixivMetadataState.TERMINAL.value
    assert pending.status == PixivMetadataState.RETRYABLE.value


def test_generic_work_failure_never_changes_conflict_row(db) -> None:
    queue_media_for_pixiv_metadata(db, {"id": 105, "filename": "123456789_p0__987654321_p2.jpg", "path": "media/conflict.jpg"})
    db.flush()
    conflict = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.source_work_id == "123456789").one()
    result = mark_work_state(db, "123456789", PixivMetadataState.RETRYABLE.value, reason="retry", attempted_record_ids=[conflict.id])
    assert conflict.status == PixivMetadataState.CONFLICT.value
    assert result["preserved_conflict"] == 1 and result["updated"] == 0


def test_retry_updates_only_exact_attempted_page(db) -> None:
    for media_id, page in ((106, 1), (107, 2)):
        queue_media_for_pixiv_metadata(db, {"id": media_id, "filename": f"123456789_p{page}.jpg", "path": f"media/{media_id}.jpg"})
    db.flush()
    p1 = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 106).one()
    p2 = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 107).one()
    mark_work_state(db, "123456789", PixivMetadataState.RETRYABLE.value, reason="retry", attempted_page_indexes=[1])
    assert p1.status == PixivMetadataState.RETRYABLE.value
    assert p2.status == PixivMetadataState.PENDING.value


def test_resume_does_not_reopen_or_request_completed_page(db) -> None:
    queue_media_for_pixiv_metadata(db, {"id": 108, "filename": "123456789_p0.jpg", "path": "media/108.jpg"})
    db.flush()
    row = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 108).one()
    row.status = PixivMetadataState.COMPLETE.value
    decision = queue_media_for_pixiv_metadata(db, {"id": 108, "filename": "123456789_p0.jpg", "path": "media/108.jpg"})
    assert decision.state == PixivMetadataState.COMPLETE.value
    calls = []
    results = run_bounded_acquisition(
        db, ["123456789"], entrypoint=("gallery-dl",), authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        command_runner=lambda *args, **kwargs: calls.append(args), sleeper=lambda _seconds: None,
    )
    assert calls == []
    assert results[0].state == "skipped_complete_or_closed"
    assert row.status == PixivMetadataState.COMPLETE.value


def test_mixed_page_work_closes_only_when_every_page_complete_or_terminal(db) -> None:
    for media_id, page in ((109, 0), (110, 1)):
        queue_media_for_pixiv_metadata(db, {"id": media_id, "filename": f"123456789_p{page}.jpg", "path": f"media/{media_id}.jpg"})
    db.flush()
    p0 = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 109).one()
    p1 = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 110).one()
    p0.status = PixivMetadataState.COMPLETE.value
    assert summarize_batch_closure(db, [109, 110])["closed"] is False
    p1.status = PixivMetadataState.TERMINAL.value
    assert summarize_batch_closure(db, [109, 110])["closed"] is True


def test_provider_identity_mismatch_is_counted_as_unfinished_work(db) -> None:
    queue_media_for_pixiv_metadata(
        db, {"id": 111, "filename": "123456789_p0.jpg", "path": "media/111.jpg"}
    )
    db.flush()
    row = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 111).one()
    row.status = PixivMetadataState.PROVIDER_IDENTITY_MISMATCH.value

    assert acquisition_work_lifecycle_counts(db) == {"provider_identity_mismatch": 1}


def test_success_invalidates_source_metadata_search_cache_after_commit(db, monkeypatch) -> None:
    queue_media_for_pixiv_metadata(
        db, {"id": 112, "filename": "123456789_p0.jpg", "path": "media/112.jpg"}
    )
    db.commit()
    events: list[str] = []
    original_commit = db.commit

    def recording_commit() -> None:
        original_commit()
        events.append("commit")

    monkeypatch.setattr(db, "commit", recording_commit)
    monkeypatch.setattr(
        "app.services.pixiv_metadata_ingestion_service.invalidate_source_metadata_search_cache",
        lambda: events.append("invalidate"),
    )
    payload = [[3, "url", {"id": 123456789, "num": 0, "title": "Work", "tags": ["tag"]}]]

    run_bounded_acquisition(
        db,
        ["123456789"],
        entrypoint=("gallery-dl",),
        authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        command_runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload), stderr=""
        ),
        sleeper=lambda _seconds: None,
        max_attempts_per_work=1,
    )

    assert events[-2:] == ["commit", "invalidate"]


def test_negative_additional_diagnostic_calls_fail_before_replay_io(tmp_path) -> None:
    args = ingestion_runner.argparse.Namespace(additional_diagnostic_calls=-1)

    with pytest.raises(PixivMetadataGateError, match="blocked_negative_additional_diagnostic_calls"):
        ingestion_runner.run_normalization_replay(
            args, None, tmp_path, waiver_accepted=True
        )


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


@pytest.mark.parametrize("secret", [
    "token-padding=", "token-padding==", "token+plus", "token/slash",
    "token-dash", "token_under", "token.dot", "cookie:part:material==",
])
def test_compromised_secret_scan_hashes_full_punctuated_value(secret: str) -> None:
    fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    assert scan_text_for_fingerprints(f"prefix {secret} suffix", {fingerprint}) == 1


def test_malformed_secret_fingerprint_fails_closed_without_raw_value() -> None:
    with pytest.raises(PixivMetadataGateError) as caught:
        ingestion_runner._known_secret_fingerprints({"VIOLET_COMPROMISED_SECRET_SHA256": "not-a-sha256"})
    assert "not-a-sha256" not in str(caught.value)


def test_malformed_json_persists_normalization_failed_and_continues(db) -> None:
    for media_id, work_id in ((201, "123456789"), (202, "223456789")):
        queue_media_for_pixiv_metadata(db, {"id": media_id, "filename": f"{work_id}_p0.jpg", "path": f"media/{media_id}.jpg"})
    db.commit()
    calls = 0
    def runner(command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(command, 0, stdout="{malformed", stderr="")
        work_id = command[-1].rsplit("/", 1)[-1]
        payload = [[3, "url", {"id": int(work_id), "num": 0, "title": "Work", "user": {"id": 42, "name": "Display"}}]]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
    results = run_bounded_acquisition(
        db, ["123456789", "223456789"], entrypoint=("gallery-dl",), authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"}, command_runner=runner,
        sleeper=lambda _seconds: None, max_attempts_per_work=1,
    )
    assert [item.state for item in results] == [PixivMetadataState.NORMALIZATION_FAILED.value, PixivMetadataState.COMPLETE.value]
    failed = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 201).one()
    assert failed.status == PixivMetadataState.NORMALIZATION_FAILED.value
    assert failed.raw_metadata_json["structural_diagnostics"]["provider_output_returned"] is True
    assert failed.raw_metadata_json["structural_diagnostics"]["raw_provider_output_retained_in_diagnostic"] is False
    assert pending_distinct_work_ids(db) == ()


def test_missing_required_local_page_persists_normalization_failed(db) -> None:
    queue_media_for_pixiv_metadata(db, {"id": 203, "filename": "123456789_p1.jpg", "path": "media/203.jpg"})
    db.commit()
    payload = [[3, "url", {"id": 123456789, "num": 0, "title": "Work", "user": {"id": 42}}]]
    results = run_bounded_acquisition(
        db, ["123456789"], entrypoint=("gallery-dl",), authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        command_runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr=""),
        sleeper=lambda _seconds: None, max_attempts_per_work=1,
    )
    assert results[0].state == PixivMetadataState.NORMALIZATION_FAILED.value
    assert db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 203).one().status == PixivMetadataState.NORMALIZATION_FAILED.value


def test_transport_timeout_persists_retryable(db) -> None:
    queue_media_for_pixiv_metadata(db, {"id": 204, "filename": "123456789_p0.jpg", "path": "media/204.jpg"})
    db.commit()
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("gallery-dl", 1)
    result = run_bounded_acquisition(
        db, ["123456789"], entrypoint=("gallery-dl",), authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"}, command_runner=timeout,
        sleeper=lambda _seconds: None, max_attempts_per_work=1,
    )[0]
    assert result.state == PixivMetadataState.RETRYABLE.value
    assert db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 204).one().status == PixivMetadataState.RETRYABLE.value


@pytest.mark.parametrize(
    ("stderr", "authentication_passed", "expected_state", "expected_error"),
    [
        ("429 rate limit", True, PixivMetadataState.RETRYABLE.value, "retryable_rate_limit"),
        ("401 authentication failed", True, PixivMetadataState.RETRYABLE.value, "retryable_authentication"),
        ("404 private unavailable", True, PixivMetadataState.TERMINAL.value, "authenticated_remote_deleted_private_unavailable"),
    ],
)
def test_provider_failures_persist_exact_retry_or_terminal_class(
    db, stderr: str, authentication_passed: bool, expected_state: str, expected_error: str
) -> None:
    queue_media_for_pixiv_metadata(db, {"id": 205, "filename": "123456789_p0.jpg", "path": "media/205.jpg"})
    db.commit()
    result = run_bounded_acquisition(
        db, ["123456789"], entrypoint=("gallery-dl",), authentication_passed=authentication_passed,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        command_runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr=stderr),
        sleeper=lambda _seconds: None, max_attempts_per_work=1,
    )[0]
    record = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.media_id == 205).one()
    assert result.state == expected_state and result.error_class == expected_error
    assert record.status == expected_state


def test_pending_to_terminal_lifecycle_closes_batch(db) -> None:
    queue_media_for_pixiv_metadata(db, {"id": 206, "filename": "123456789_p0.jpg", "path": "media/206.jpg"})
    db.commit()
    assert summarize_batch_closure(db, [206])["closed"] is False
    run_bounded_acquisition(
        db, ["123456789"], entrypoint=("gallery-dl",), authentication_passed=True,
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        command_runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="404 private"),
        sleeper=lambda _seconds: None, max_attempts_per_work=1,
    )
    closure = summarize_batch_closure(db, [206])
    assert closure["closed"] is True and closure["terminal_remote_unavailable_count"] == 1


def test_deleted_first_canary_does_not_block_later_success(db) -> None:
    terminal = type("R", (), {"work_id": "123456789", "state": PixivMetadataState.TERMINAL.value, "request_attempted": True, "attempt_count": 1, "error_class": "authenticated_remote_deleted_private_unavailable"})()
    success = type("R", (), {"work_id": "223456789", "state": PixivMetadataState.COMPLETE.value, "request_attempted": True, "attempt_count": 1, "error_class": None})()
    results, proof = ingestion_runner.run_deterministic_auth_canary(
        db, ["123456789", "223456789"], entrypoint=("gallery-dl",),
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"},
        acquire=lambda *_args, **_kwargs: [terminal, success],
    )
    assert len(results) == 2 and proof["passed"] is True
    assert proof["terminal_count"] == 1 and proof["success_count"] == 1


def test_terminal_only_canary_advances_to_next_bounded_batch(db) -> None:
    calls = []
    def acquire(_session, work_ids, **_kwargs):
        calls.append(tuple(work_ids))
        state = PixivMetadataState.TERMINAL.value if len(calls) == 1 else PixivMetadataState.COMPLETE.value
        return [type("R", (), {"work_id": work_ids[0], "state": state, "request_attempted": True, "attempt_count": 1, "error_class": None})()]
    _, proof = ingestion_runner.run_deterministic_auth_canary(
        db, ["123456789", "223456789"], entrypoint=("gallery-dl",),
        env={"VIOLET_CREDENTIAL_ROTATION_CONFIRMED": "true"}, acquire=acquire, batch_size=1,
    )
    assert calls == [("123456789",), ("223456789",)]
    assert proof["passed"] is True


def test_creator_source_backfill_is_additive_query_visible_and_role_safe(db) -> None:
    record = SourceMetadataRecord(
        provider="pixiv", provider_record_key="existing:123456789:p0", media_id=1,
        source_work_id="123456789", source_page_index=0, metadata_kind="provider_metadata",
        data_type_label="authenticated_provider_metadata", status="metadata_complete",
        raw_metadata_json={"user": {"id": 42, "name": "Display", "account": "handle"}},
    )
    db.add(record); db.commit()
    proof = backfill_creator_source_observations(db); db.commit()
    observations = db.query(SourceNameObservation).all()
    assert {(item.raw_name, item.name_role) for item in observations} == {("Display", "artist"), ("handle", "artist")}
    assert proof["query_visible_creator_name_count"] == 1
    assert proof["query_visible_creator_account_count"] == 1
    assert proof["explicit_creator_role_misclassification_count"] == 0
    assert record.raw_metadata_json["creator_profile_identity_source"] == "derived_from_stable_creator_id"
