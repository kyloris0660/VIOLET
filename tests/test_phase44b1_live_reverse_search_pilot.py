"""Focused tests for the Phase 4.4-B1 live reverse-search pilot runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base  # noqa: E402
from app.enums import ContentClassEnum, FileTypeEnum, RatingEnum  # noqa: E402
from app.models import (  # noqa: E402
    EntityEvidence,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    NegativeLookupCache,
    ProviderCache,
    TagTranslation,
    blombooru_media_tags,
)
from scripts import run_phase44b1_one_provider_live_reverse_search_pilot as b1  # noqa: E402


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


def _write_image(path: Path, *, size=(16, 12), color=(120, 80, 40)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path, format="JPEG")


def _media(
    db,
    media_id: int,
    *,
    content_class=ContentClassEnum.anime,
    file_type=FileTypeEnum.image,
    filename: str | None = None,
    source: str | None = None,
    path: str | None = None,
    thumbnail_path: str | None = None,
) -> Media:
    item = Media(
        id=media_id,
        filename=filename or f"private_original_name_{media_id}.jpg",
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
    _write_image(storage_root / "media" / "thumbnails" / f"{media_id}.jpg", size=(32, 24))


def _server_preflight() -> dict:
    return {
        "result": "clean",
        "listener_backend": "windows_netstat",
        "occupied_count": 0,
        "confirmed_violet_count": 0,
        "suspected_violet_count": 0,
    }


def _identity() -> dict:
    return {
        "violet_env": "development",
        "configured_db_host": "localhost",
        "configured_db_port": 5432,
        "configured_db_user": "postgres",
        "configured_db_name": "blombooru",
        "actual_db_name": "blombooru",
        "db_identity_result": "development_blombooru_confirmed",
        "db_password_included": False,
        "storage_root_mode": "code_root_default",
        "storage_root_explicitly_set": False,
        "storage_root_test_path": False,
        "storage_root_equals_code_root": True,
        "local_paths_redacted": True,
    }


def _base_summary(sample_gate: dict, credential_present: bool = False) -> dict:
    return b1.build_base_summary(
        generated_at="2026-05-26T00:00:00+00:00",
        no_active_server_preflight=_server_preflight(),
        identity=_identity(),
        sample_gate=sample_gate,
        credential_status={
            "provider_requires_api_key": True,
            "credential_name": "SAUCENAO_API_KEY",
            "present": credential_present,
            "value_printed": False,
            "included_in_public_report": False,
            "included_in_local_details": False,
        },
        execute_live=True,
        upload_derived_approved=True,
        provider_docs_verified=True,
        write_db_records=False,
    )


def test_sample_gate_rejects_ids_outside_approved_list():
    with pytest.raises(b1.SampleGateError, match="outside the approved"):
        b1.parse_media_ids([b1.APPROVED_SAMPLE_IDS[0], 999999])


def test_sample_gate_requires_exact_approved_set():
    with pytest.raises(b1.SampleGateError, match="exact approved sample set"):
        b1.parse_media_ids([b1.APPROVED_SAMPLE_IDS[0]])


def test_no_api_key_status_becomes_credential_required(db, tmp_path):
    for media_id in b1.APPROVED_SAMPLE_IDS:
        _media(db, media_id)
        _make_storage_for_media(tmp_path, media_id)
    sample_gate, _media_by_id = b1.build_sample_gate(db, media_ids=list(b1.APPROVED_SAMPLE_IDS), storage_root=tmp_path)
    summary = _base_summary(sample_gate, credential_present=False)
    b1._update_summary_for_stop(summary, status="credential_required", stop_condition="credential_required")
    assert summary["status"] == "credential_required"
    assert summary["credential_status"]["present"] is False
    assert summary["live_requests"]["attempted"] == 0


def test_unknown_non_anime_and_illustration_are_blocked(db, tmp_path):
    rows = [
        (b1.APPROVED_SAMPLE_IDS[0], ContentClassEnum.unknown, "blocked_by_content_class:unknown"),
        (b1.APPROVED_SAMPLE_IDS[1], ContentClassEnum.non_anime, "blocked_by_content_class:non_anime"),
        (b1.APPROVED_SAMPLE_IDS[2], ContentClassEnum.illustration, "blocked_by_content_class:illustration"),
    ]
    for media_id, content_class, _reason in rows:
        _media(db, media_id, content_class=content_class)
        _make_storage_for_media(tmp_path, media_id)
    sample_gate, _media_by_id = b1.build_sample_gate(db, media_ids=[row[0] for row in rows], storage_root=tmp_path)
    reasons = {item["media_id"]: item["blocked_reason"] for item in sample_gate["request_plan"]}
    for media_id, _content_class, expected_reason in rows:
        assert reasons[media_id] == expected_reason


def test_request_builder_excludes_local_path_filename_source_label(db, tmp_path):
    media_id = b1.APPROVED_SAMPLE_IDS[0]
    private_filename = "very_private_original_name.jpg"
    private_source = "private_source_label"
    _media(db, media_id, filename=private_filename, source=private_source)
    _make_storage_for_media(tmp_path, media_id)
    sample_gate, _media_by_id = b1.build_sample_gate(db, media_ids=[media_id], storage_root=tmp_path)
    payload = json.dumps(sample_gate, ensure_ascii=False)
    assert str(tmp_path) not in payload
    assert "media/original" not in payload
    assert "media/thumbnails" not in payload
    assert private_filename not in payload
    assert private_source not in payload
    row = sample_gate["request_plan"][0]
    assert row["local_path_included"] is False
    assert row["filename_included"] is False
    assert row["source_label_included"] is False
    assert row["would_send_original"] is False
    assert row["would_send_thumbnail"] is False
    assert row["would_send_derived_image"] is True


def test_derived_input_uses_safe_generated_name_and_strips_metadata(db, tmp_path):
    media_id = b1.APPROVED_SAMPLE_IDS[0]
    private_filename = "secret_family_album_name.jpg"
    media = _media(db, media_id, filename=private_filename)
    _make_storage_for_media(tmp_path, media_id)
    derived = b1.generate_derived_input(media, storage_root=tmp_path, output_dir=tmp_path / "derived")
    assert derived.safe_filename == f"phase44b1_m{media_id}_derived.jpg"
    assert private_filename not in derived.safe_filename
    assert derived.path.name == derived.safe_filename
    assert derived.width <= b1.MAX_DERIVED_DIMENSION
    assert derived.height <= b1.MAX_DERIVED_DIMENSION


def test_saucenao_request_uses_only_safe_derived_file(db, tmp_path):
    media_id = b1.APPROVED_SAMPLE_IDS[0]
    media = _media(db, media_id)
    _make_storage_for_media(tmp_path, media_id)
    derived = b1.generate_derived_input(media, storage_root=tmp_path, output_dir=tmp_path / "derived")
    request = b1.build_saucenao_request("secret-api-key", derived)
    filename, _content, content_type = request["files"]["file"]
    assert filename == derived.safe_filename
    assert content_type == "image/jpeg"
    redacted = request["redacted_request_shape"]
    assert redacted["api_key" if "api_key" in redacted else "params"]["api_key"] == "<redacted>"
    assert redacted["local_path_included"] is False
    assert redacted["original_filename_included"] is False
    assert redacted["source_label_included"] is False
    assert redacted["original_upload"] is False
    assert redacted["derived_upload"] is True


def test_provider_response_classification_high_low_no_match_and_conflict():
    high_payload = {
        "header": {"status": 0},
        "results": [
            {
                "header": {"similarity": "92.5", "index_name": "Pixiv", "result_id": "1"},
                "data": {"title": "Work", "member_name": "Artist", "ext_urls": ["https://www.pixiv.net/artworks/1"]},
            }
        ],
    }
    low_payload = {
        "header": {"status": 0},
        "results": [
            {
                "header": {"similarity": "73.0", "index_name": "Pixiv"},
                "data": {"title": "Maybe", "ext_urls": ["https://example.com/post"]},
            }
        ],
    }
    conflict_payload = {
        "header": {"status": 0},
        "results": [
            {"header": {"similarity": "91.0"}, "data": {"ext_urls": ["https://a.example/post"]}},
            {"header": {"similarity": "90.0"}, "data": {"ext_urls": ["https://b.example/post"]}},
        ],
    }
    assert b1.classify_saucenao_response(media_id=1, status_code=200, headers={}, payload=high_payload).result_class == "high_confidence_match"
    assert b1.classify_saucenao_response(media_id=1, status_code=200, headers={}, payload=low_payload).result_class == "low_confidence_match"
    assert b1.classify_saucenao_response(media_id=1, status_code=200, headers={}, payload={"header": {"status": 0}, "results": []}).result_class == "no_match"
    assert b1.classify_saucenao_response(media_id=1, status_code=200, headers={}, payload=conflict_payload).result_class == "conflict"
    assert b1.classify_saucenao_response(media_id=1, status_code=429, headers={"Retry-After": "10"}, payload={}).result_class == "rate_limited"


def test_db_write_mapping_is_limited_to_allowed_tables_and_no_assignment(db):
    media_id = b1.APPROVED_SAMPLE_IDS[0]
    _media(db, media_id)
    request_shape, query_hash = b1.make_request_shape(media_id=media_id, content_class="anime", derived_sha256="privatehash")
    result = b1.ProviderResult(
        media_id=media_id,
        result_class="high_confidence_match",
        response_status="ok",
        error_class=None,
        score=91.5,
        normalized_payload={"top_result": {"source_url_present": True}, "privacy_redacted": True},
    )
    counts = b1.write_db_records_for_result(
        db,
        media_id=media_id,
        query_hash=query_hash,
        request_shape_redacted=request_shape,
        result=result,
    )
    db.commit()
    assert counts["ProviderCache"] == 1
    assert counts["EntityEvidence"] == 1
    assert counts["MediaEntityCandidate"] == 0
    assert db.query(ProviderCache).count() == 1
    assert db.query(EntityEvidence).count() == 1
    assert db.query(NegativeLookupCache).count() == 0
    assert db.query(MediaEntityCandidate).count() == 0
    assert db.query(MediaEntityAssignment).count() == 0
    assert db.query(TagTranslation).count() == 0
    assert db.execute(blombooru_media_tags.select()).all() == []


def test_low_confidence_writes_negative_cache_but_no_assignment(db):
    media_id = b1.APPROVED_SAMPLE_IDS[0]
    _media(db, media_id)
    request_shape, query_hash = b1.make_request_shape(media_id=media_id, content_class="anime", derived_sha256="privatehash")
    result = b1.ProviderResult(
        media_id=media_id,
        result_class="low_confidence_match",
        response_status="ok",
        error_class=None,
        score=61.0,
        normalized_payload={"top_result": {"source_url_present": True}, "privacy_redacted": True},
    )
    counts = b1.write_db_records_for_result(
        db,
        media_id=media_id,
        query_hash=query_hash,
        request_shape_redacted=request_shape,
        result=result,
    )
    db.commit()
    assert counts["ProviderCache"] == 1
    assert counts["NegativeLookupCache"] == 1
    assert counts["EntityEvidence"] == 1
    assert db.query(MediaEntityAssignment).count() == 0


def test_credential_redaction_and_public_report_privacy_scan(db, tmp_path):
    media_id = b1.APPROVED_SAMPLE_IDS[0]
    _media(db, media_id)
    _make_storage_for_media(tmp_path, media_id)
    sample_gate, _media_by_id = b1.build_sample_gate(db, media_ids=[media_id], storage_root=tmp_path)
    summary = _base_summary(sample_gate, credential_present=True)
    text = json.dumps(summary, ensure_ascii=False) + b1.build_markdown_report(summary)
    assert "secret-api-key" not in text
    b1.assert_public_payload_safe({"report": text})
    with pytest.raises(b1.Phase44B1Error, match="privacy_scan_failed"):
        b1.assert_public_payload_safe({"report": r"leaked C:\Users\name\Pictures\private.jpg"})
    with pytest.raises(b1.Phase44B1Error, match="privacy_scan_failed"):
        b1.assert_public_payload_safe({"report": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"})


def test_no_external_call_when_credential_missing_gate(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        for media_id in b1.APPROVED_SAMPLE_IDS:
            _media(session, media_id)
            _make_storage_for_media(tmp_path, media_id)
    finally:
        session.close()

    monkeypatch.setattr(b1, "create_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(
        b1,
        "load_project_config",
        lambda _root=b1.ROOT: b1.ProjectConfig(
            project_root=tmp_path,
            violet_env="development",
            storage_root=tmp_path,
            storage_root_explicitly_set=False,
            db_user="postgres",
            db_password="",
            db_host="localhost",
            db_port=5432,
            db_name="blombooru",
        ),
    )
    monkeypatch.setattr(b1, "prove_db_identity", lambda _session, _config: _identity())
    monkeypatch.setattr(b1, "get_saucenao_api_key", lambda _root=b1.ROOT: None)
    monkeypatch.setattr(b1, "resolve_output_path", lambda raw, expected_parent: Path(raw))
    called = {"http": False}

    def _http_post(*_args, **_kwargs):
        called["http"] = True
        raise AssertionError("external call should not be attempted")

    parser = b1.build_arg_parser()
    args = parser.parse_args(
        [
            "--media-ids",
            *(str(item) for item in b1.APPROVED_SAMPLE_IDS),
            "--execute-live",
            "--upload-derived-approved",
            "--provider-docs-verified",
            "--report-json",
            str(tmp_path / "report.json"),
            "--report-md",
            str(tmp_path / "report.md"),
            "--local-details-json",
            str(tmp_path / "details.json"),
            "--derived-dir",
            str(tmp_path / "derived"),
            "--no-active-server-preflight-result",
            "clean",
            "--no-active-server-listener-backend",
            "windows_netstat",
            "--no-active-server-occupied-count",
            "0",
            "--no-active-server-confirmed-violet-count",
            "0",
            "--no-active-server-suspected-violet-count",
            "0",
        ]
    )
    summary = b1.run(args, http_post=_http_post)
    try:
        assert summary["status"] == "credential_required"
        assert summary["stop_condition"] == "credential_required"
        assert called["http"] is False
        assert summary["live_requests"]["attempted"] == 0
        assert summary["db_writes"]["attempted"] is False
        assert not (tmp_path / "derived").exists()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
