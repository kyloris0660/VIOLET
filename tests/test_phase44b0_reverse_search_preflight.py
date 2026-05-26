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
    EnvBlockedError,
    Phase44B0Error,
    ReadOnlyViolation,
    SampleGateError,
    ServerPreflightBlockedError,
    assert_public_payload_safe,
    build_arg_parser,
    build_markdown_report,
    build_preflight,
    apply_strict_policy,
    enforce_strict_policy,
    install_read_only_guard,
    load_project_config,
    parse_media_ids,
    validate_no_active_server_preflight,
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


def _parser_base_args() -> list[str]:
    return [
        "--media-ids",
        *(str(item) for item in APPROVED_SAMPLE_IDS),
        "--report-json",
        "docs/reports/phase-4.4b0-sample-gated-reverse-search-preflight-summary.json",
        "--report-md",
        "docs/reports/phase-4.4b0-sample-gated-reverse-search-preflight.md",
        "--local-details-json",
        ".local_manifests/phase-4.4b0-sample-gated-reverse-search-preflight-details.json",
    ]


def _parser_clean_preflight_args() -> list[str]:
    return [
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


def test_project_config_blocks_test_env_before_db_access(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(EnvBlockedError, match="VIOLET_ENV must be 'development'"):
        load_project_config(tmp_path)


def test_project_config_blocks_production_env_before_db_access(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "production")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(EnvBlockedError, match="got 'production'"):
        load_project_config(tmp_path)


def test_project_config_allows_explicit_development_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VIOLET_ENV", "development")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("VIOLET_STORAGE_ROOT", raising=False)
    monkeypatch.setenv("POSTGRES_DB", "blombooru")
    config = load_project_config(tmp_path)
    assert config.violet_env == "development"
    assert config.db_name == "blombooru"


def test_missing_no_active_server_preflight_args_fail_closed():
    parser = build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(_parser_base_args())


def test_explicit_clean_no_active_server_preflight_args_are_accepted():
    parser = build_arg_parser()
    args = parser.parse_args([*_parser_base_args(), *_parser_clean_preflight_args()])
    validate_no_active_server_preflight(
        {
            "result": args.no_active_server_preflight_result,
            "listener_backend": args.no_active_server_listener_backend,
            "occupied_count": args.no_active_server_occupied_count,
            "confirmed_violet_count": args.no_active_server_confirmed_violet_count,
            "suspected_violet_count": args.no_active_server_suspected_violet_count,
        }
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"result": "blocked"},
        {"occupied_count": 1},
        {"confirmed_violet_count": 1},
        {"suspected_violet_count": 1},
        {"listener_backend": "not_a_real_audit_backend"},
    ],
)
def test_non_clean_no_active_server_preflight_blocks_execution(patch):
    preflight = _server_preflight()
    preflight.update(patch)
    with pytest.raises(ServerPreflightBlockedError, match="server_preflight_blocked"):
        validate_no_active_server_preflight(preflight)


def test_clean_no_active_server_preflight_allows_execution():
    validate_no_active_server_preflight(_server_preflight())


def test_strict_mode_blocks_after_report_metadata_when_samples_are_blocked():
    summary = {"counts": {"blocked_count": 1}}
    apply_strict_policy(summary, enabled=True)
    assert summary["strict_mode"]["status"] == "blocked_after_report_generation"
    with pytest.raises(Phase44B0Error, match="strict_blocked"):
        enforce_strict_policy(summary)


def test_strict_mode_allows_zero_blocked_samples():
    summary = {"counts": {"blocked_count": 0}}
    apply_strict_policy(summary, enabled=True)
    enforce_strict_policy(summary)
    assert summary["strict_mode"]["status"] == "passed"


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


def test_read_only_guard_blocks_data_modifying_cte():
    engine = create_engine("sqlite:///:memory:")
    install_read_only_guard(engine)
    try:
        with engine.connect() as conn:
            with pytest.raises(ReadOnlyViolation, match="db_write_blocked"):
                conn.exec_driver_sql("WITH changed AS (UPDATE media SET filename='x' RETURNING id) SELECT id FROM changed")
    finally:
        engine.dispose()


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
