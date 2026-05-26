"""Focused tests for the Phase 4.4-B0 reverse-search preflight runner."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base  # noqa: E402
from app.enums import ContentClassEnum, FileTypeEnum, RatingEnum  # noqa: E402
from app.models import Media  # noqa: E402
from scripts.run_phase44b0_sample_gated_reverse_search_preflight import (  # noqa: E402
    APPROVED_SAMPLE_IDS,
    Phase44B0Error,
    SampleGateError,
    assert_public_payload_safe,
    build_markdown_report,
    build_preflight,
    parse_media_ids,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _write_image(path: Path, *, size=(16, 12)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(120, 80, 40)).save(path, format="JPEG")


def _media(
    db,
    media_id: int,
    *,
    content_class=ContentClassEnum.anime,
    file_type=FileTypeEnum.image,
    path: str | None = None,
    thumbnail_path: str | None = None,
    filename: str | None = None,
    source: str | None = None,
) -> Media:
    item = Media(
        id=media_id,
        filename=filename or f"private_name_{media_id}.jpg",
        path=path if path is not None else f"media/original/{media_id}.jpg",
        thumbnail_path=thumbnail_path if thumbnail_path is not None else f"media/thumbnails/{media_id}.jpg",
        hash=f"{media_id:064x}",
        file_type=file_type,
        mime_type="image/jpeg",
        file_size=123,
        width=16,
        height=12,
        rating=RatingEnum.safe,
        source=source,
        content_class=content_class,
    )
    db.add(item)
    db.commit()
    return item


def _make_storage_for_media(storage_root: Path, media_id: int) -> None:
    _write_image(storage_root / "media" / "original" / f"{media_id}.jpg")
    _write_image(storage_root / "media" / "thumbnails" / f"{media_id}.jpg", size=(8, 8))


def _identity() -> dict:
    return {
        "violet_env": "development",
        "configured_db_name": "blombooru",
        "actual_db_name": "blombooru",
        "db_identity_result": "development_blombooru_confirmed",
        "storage_root_mode": "code_root_default",
        "storage_root_explicitly_set": False,
        "storage_root_test_path": False,
        "storage_root_equals_code_root": True,
        "local_paths_redacted": True,
    }


def _server_preflight() -> dict:
    return {
        "result": "clean",
        "listener_backend": "windows_netstat",
        "occupied_count": 0,
        "confirmed_violet_count": 0,
        "suspected_violet_count": 0,
    }


def test_no_media_ids_fail_closed():
    with pytest.raises(SampleGateError, match="--media-ids is required"):
        parse_media_ids([])


def test_media_ids_outside_approved_list_fail_closed():
    with pytest.raises(SampleGateError, match="outside the approved"):
        parse_media_ids([APPROVED_SAMPLE_IDS[0], 999999])


def test_subset_requires_explicit_allow_subset():
    with pytest.raises(SampleGateError, match="subset requires --allow-subset"):
        parse_media_ids([APPROVED_SAMPLE_IDS[0]])
    assert parse_media_ids([APPROVED_SAMPLE_IDS[0]], allow_subset=True) == [APPROVED_SAMPLE_IDS[0]]


def test_missing_media_id_is_blocked(db, tmp_path):
    summary, details = build_preflight(
        db,
        media_ids=[APPROVED_SAMPLE_IDS[0]],
        storage_root=tmp_path,
        identity=_identity(),
        no_active_server_preflight=_server_preflight(),
    )
    assert summary["request_plan"][0]["eligibility_status"] == "blocked"
    assert summary["request_plan"][0]["blocked_reason"] == "missing_media"
    assert summary["counts"]["blocked_count_by_reason"] == {"missing_media": 1}
    assert details["sample_details"][0]["found"] is False


@pytest.mark.parametrize(
    "content_class,expected_reason",
    [
        (ContentClassEnum.unknown, "blocked_by_content_class:unknown"),
        (ContentClassEnum.non_anime, "blocked_by_content_class:non_anime"),
        (ContentClassEnum.illustration, "blocked_by_content_class:illustration"),
        (None, "blocked_by_content_class:null_unclassified"),
    ],
)
def test_non_eligible_content_classes_are_blocked(db, tmp_path, content_class, expected_reason):
    media_id = APPROVED_SAMPLE_IDS[0]
    _media(db, media_id, content_class=content_class)
    _make_storage_for_media(tmp_path, media_id)
    summary, _details = build_preflight(
        db,
        media_ids=[media_id],
        storage_root=tmp_path,
        identity=_identity(),
        no_active_server_preflight=_server_preflight(),
    )
    assert summary["request_plan"][0]["eligibility_status"] == "blocked"
    assert summary["request_plan"][0]["blocked_reason"] == expected_reason


def test_anime_sample_is_eligible_when_app_managed_media_is_safe(db, tmp_path):
    media_id = APPROVED_SAMPLE_IDS[0]
    _media(db, media_id, content_class=ContentClassEnum.anime)
    _make_storage_for_media(tmp_path, media_id)
    summary, details = build_preflight(
        db,
        media_ids=[media_id],
        storage_root=tmp_path,
        identity=_identity(),
        no_active_server_preflight=_server_preflight(),
    )
    row = summary["request_plan"][0]
    assert row["eligibility_status"] == "eligible"
    assert row["blocked_reason"] is None
    assert row["would_send_original"] is False
    assert row["would_send_thumbnail"] is False
    assert row["would_send_derived_image"] is False
    assert details["sample_details"][0]["thumbnail_dimensions"] == {"width": 8, "height": 8}


def test_no_fallback_to_source_field_when_app_managed_original_is_missing(db, tmp_path):
    media_id = APPROVED_SAMPLE_IDS[0]
    private_source = "private_source_label_that_must_not_be_used"
    _media(db, media_id, content_class=ContentClassEnum.anime, source=private_source)
    _write_image(tmp_path / "media" / "thumbnails" / f"{media_id}.jpg")
    summary, details = build_preflight(
        db,
        media_ids=[media_id],
        storage_root=tmp_path,
        identity=_identity(),
        no_active_server_preflight=_server_preflight(),
    )
    assert summary["request_plan"][0]["blocked_reason"] == "blocked_by_original:missing"
    assert details["sample_details"][0]["source_field_read_for_fallback"] is False
    assert private_source not in json.dumps(summary, ensure_ascii=False)
    assert private_source not in json.dumps(details, ensure_ascii=False)


def test_request_plan_excludes_local_paths_filenames_and_source_labels(db, tmp_path):
    media_id = APPROVED_SAMPLE_IDS[0]
    private_filename = "very_private_original_name.jpg"
    private_source = "private_source_label"
    _media(db, media_id, filename=private_filename, source=private_source)
    _make_storage_for_media(tmp_path, media_id)
    summary, details = build_preflight(
        db,
        media_ids=[media_id],
        storage_root=tmp_path,
        identity=_identity(),
        no_active_server_preflight=_server_preflight(),
    )
    public_payload = json.dumps(summary, ensure_ascii=False) + build_markdown_report(summary)
    local_payload = json.dumps(details, ensure_ascii=False)
    for payload in (public_payload, local_payload):
        assert str(tmp_path) not in payload
        assert "media/original" not in payload
        assert "media/thumbnails" not in payload
        assert private_filename not in payload
        assert private_source not in payload
    row = summary["request_plan"][0]
    assert row["local_path_included"] is False
    assert row["filename_included"] is False
    assert row["source_label_included"] is False


def test_public_report_privacy_scan_rejects_local_paths():
    with pytest.raises(Phase44B0Error, match="privacy_scan_failed"):
        assert_public_payload_safe({"report": r"leaked C:\Users\name\Pictures\private.jpg"})


def test_no_db_writes_are_attempted(db, tmp_path):
    media_id = APPROVED_SAMPLE_IDS[0]
    _media(db, media_id)
    _make_storage_for_media(tmp_path, media_id)
    writes: list[str] = []

    @event.listens_for(db.bind, "before_cursor_execute")
    def _capture_writes(_conn, _cursor, statement, _parameters, _context, _executemany):
        sql = str(statement).lstrip().upper()
        if sql.startswith(("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "TRUNCATE")):
            writes.append(sql.split(None, 1)[0])

    build_preflight(
        db,
        media_ids=[media_id],
        storage_root=tmp_path,
        identity=_identity(),
        no_active_server_preflight=_server_preflight(),
    )
    assert writes == []


def test_no_external_call_path_is_exercised(db, tmp_path, monkeypatch):
    media_id = APPROVED_SAMPLE_IDS[0]
    _media(db, media_id)
    _make_storage_for_media(tmp_path, media_id)

    def _blocked_connect(*_args, **_kwargs):
        raise AssertionError("external network call attempted")

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    summary, _details = build_preflight(
        db,
        media_ids=[media_id],
        storage_root=tmp_path,
        identity=_identity(),
        no_active_server_preflight=_server_preflight(),
    )
    assert summary["external_call_policy"]["external_provider_calls_attempted"] is False
    assert summary["external_call_policy"]["reverse_search_execution_attempted"] is False
    assert summary["provider_policy_stub"]["enabled"] is False
