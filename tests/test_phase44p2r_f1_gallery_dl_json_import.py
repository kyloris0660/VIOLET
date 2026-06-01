"""Focused tests for the Phase 4.4-P2R-F1 gallery-dl JSON import pilot."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base  # noqa: E402
from app.enums import ContentClassEnum, FileTypeEnum, RatingEnum  # noqa: E402
from app.models import Media  # noqa: E402
from scripts import run_phase44p2r_f1_gallery_dl_json_import_pilot as pilot  # noqa: E402


CONFIG_ENV_KEYS = [
    "VIOLET_ENV",
    "VIOLET_STORAGE_ROOT",
    "TEST_DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
]


def _clear_config_env(monkeypatch):
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


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


def _media(
    db,
    media_id: int,
    *,
    filename: str,
    content_class=ContentClassEnum.anime,
) -> Media:
    item = Media(
        id=media_id,
        filename=filename,
        path=f"media/original/{filename}",
        thumbnail_path=f"media/thumbnails/{filename}",
        hash=f"{media_id:064x}",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=123,
        width=16,
        height=12,
        rating=RatingEnum.safe,
        source=None,
        content_class=content_class,
    )
    db.add(item)
    db.commit()
    return item


def _write(path: Path, payload: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def _write_settings(storage_root: Path, database: dict) -> None:
    settings_file = storage_root / "data" / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps({"database": database}), encoding="utf-8")


def test_jsonl_parsing_accepts_gallery_dl_event_lines(tmp_path):
    input_file = _write(
        tmp_path / "sample.jsonl",
        "\n".join(
            [
                json.dumps([2, {"id": 100000001, "num": 0, "page_count": 1, "title": "t"}]),
                "",
                json.dumps([3, "https://i.pximg.net/img-original/img/x/100000001_p0.jpg", {"id": 100000001, "filename": "100000001_p0"}]),
            ]
        ),
    )

    result = pilot.parse_gallery_dl_json_inputs(input_file)
    records = pilot.normalize_records(result, adapter_version="test")

    assert result.raw_event_count == 2
    assert result.directory_context_event_count == 1
    assert result.url_media_event_count == 1
    assert len(result.directory_context_records) == 1
    assert len(records) == 1
    assert records[0].work_id == "100000001"
    assert records[0].page_index == 0
    assert records[0].record_shape == "gallery_dl_url_media_event"


def test_json_array_parsing_accepts_gallery_dl_dump_shape(tmp_path):
    input_file = _write(
        tmp_path / "sample.json",
        json.dumps(
            [
                [2, {"id": 100000002, "num": 0, "page_count": 2, "title": "root"}],
                [3, "https://example.invalid/100000002_p1.jpg", {"id": 100000002, "filename": "100000002_p1", "num": 1}],
            ]
        ),
    )

    result = pilot.parse_gallery_dl_json_inputs(input_file)
    records = pilot.normalize_records(result, adapter_version=None)

    assert result.raw_event_count == 2
    assert result.directory_context_event_count == 1
    assert result.url_media_event_count == 1
    assert len(records) == 1
    assert records[0].work_id == "100000002"
    assert records[0].page_index == 1


def test_directory_event_is_private_context_not_media_record(tmp_path):
    input_file = _write(
        tmp_path / "directory-only.json",
        json.dumps([2, {"id": 100000013, "title": "directory context", "num": 0, "page_count": 1}]),
    )

    result = pilot.parse_gallery_dl_json_inputs(input_file)
    records = pilot.normalize_records(result, adapter_version=None)

    assert len(result.records) == 1
    assert result.records[0].is_media_record is False
    assert result.directory_context_event_count == 1
    assert records == []


def test_json_parser_accepts_powershell_utf16_redirect_output(tmp_path):
    input_file = tmp_path / "powershell-smoke.json"
    input_file.write_text(
        json.dumps([[3, "https://example.invalid/100000012_p0.jpg", {"id": 100000012, "num": 0, "page_count": 1}]]),
        encoding="utf-16",
    )

    result = pilot.parse_gallery_dl_json_inputs(input_file)
    record = pilot.normalize_records(result, adapter_version=None)[0]

    assert record.work_id == "100000012"


def test_invalid_json_fails_closed_and_skip_invalid_is_explicit(tmp_path):
    input_file = _write(tmp_path / "bad.jsonl", '{"id": 100000003}\n{bad json}\n')

    with pytest.raises(pilot.JsonInputError):
        pilot.parse_gallery_dl_json_inputs(input_file)

    result = pilot.parse_gallery_dl_json_inputs(input_file, skip_invalid=True)
    assert len(result.records) == 1
    assert result.skipped_invalid_count == 1


def test_schema_variations_are_normalized(tmp_path):
    input_file = _write(
        tmp_path / "variation.json",
        json.dumps(
            {
                "illust_id": "100000004",
                "pageIndex": "2",
                "pageCount": "3",
                "illust_title": "variant title",
                "user": {"id": "55", "name": "artist"},
                "tags": [{"name": "tag-a", "translated_name": "tag a"}],
                "caption": "caption",
                "image_urls": {"medium": "https://example.invalid/m.jpg"},
                "category": "pixiv",
            }
        ),
    )

    result = pilot.parse_gallery_dl_json_inputs(input_file)
    record = pilot.normalize_records(result, adapter_version="1.0")[0]

    assert record.work_id == "100000004"
    assert record.page_index == 2
    assert record.page_count == 3
    assert record.title == "variant title"
    assert record.artist_name == "artist"
    assert record.artist_id == "55"
    assert record.tags == ("tag-a",)
    assert record.translated_tags == ("tag a",)
    assert record.metadata_richness == "rich_structured_metadata"
    assert record.image_url_kinds_available


def test_settings_json_database_precedence_is_honored(tmp_path, monkeypatch):
    _clear_config_env(monkeypatch)
    storage_root = tmp_path / "storage"
    _write_settings(
        storage_root,
        {
            "name": "from_settings",
            "host": "settings-host",
            "port": 5544,
            "user": "settings-user",
            "password": "settings-password",
        },
    )
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("POSTGRES_DB", "from_env")
    monkeypatch.setenv("POSTGRES_HOST", "env-host")
    monkeypatch.setenv("POSTGRES_USER", "env-user")

    config = pilot.load_project_config(project_root=tmp_path)

    assert config.db_name == "from_settings"
    assert config.db_host == "settings-host"
    assert config.db_port == 5544
    assert config.db_user == "settings-user"
    assert config.database_url.database == "from_settings"
    assert config.settings_source == "settings_json"
    assert config.storage_root_mode == "explicit_violet_storage_root"


def test_violet_storage_root_controls_settings_location(tmp_path, monkeypatch):
    _clear_config_env(monkeypatch)
    root_storage = tmp_path / "data"
    root_storage.mkdir()
    (root_storage / "settings.json").write_text(
        json.dumps({"database": {"name": "wrong_root_settings"}}),
        encoding="utf-8",
    )
    storage_root = tmp_path / "storage"
    _write_settings(storage_root, {"name": "storage_settings"})
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(storage_root))

    config = pilot.load_project_config(project_root=tmp_path)

    assert config.db_name == "storage_settings"
    assert config.settings_file_exists is True
    assert config.storage_root_mode == "explicit_violet_storage_root"


def test_test_database_url_is_honored_and_forbidden_dev_db_fails_closed(tmp_path, monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("POSTGRES_DB", "blombooru")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://u:p@localhost/custom_test_db")

    config = pilot.load_project_config(project_root=tmp_path)

    assert config.db_name == "custom_test_db"
    assert config.database_url.database == "custom_test_db"
    assert config.database_url_source == "test_database_url"

    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://u:p@localhost/blombooru")
    with pytest.raises(pilot.ConfigBlockedError, match="production-like DB"):
        pilot.load_project_config(project_root=tmp_path)

    monkeypatch.setenv("TEST_DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_DB", "blombooru")
    with pytest.raises(pilot.ConfigBlockedError, match="test-specific POSTGRES_DB"):
        pilot.load_project_config(project_root=tmp_path)


def test_public_db_identity_labels_do_not_emit_secret_fields(tmp_path, monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_PASSWORD", "super-private-password")
    monkeypatch.setenv("POSTGRES_DB", "public_safe_db")
    config = pilot.load_project_config(project_root=tmp_path)

    payload = pilot.build_db_identity_payload(config, "public_safe_db")
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "super-private-password" not in serialized
    assert "password" not in serialized.lower()
    pilot.assert_public_payload_safe(payload)


def test_public_report_redacts_exact_pixiv_ids_and_local_paths(tmp_path):
    raw = pilot.GalleryDlRawRecord(
        data={"id": 100000005, "num": 0, "page_count": 1, "title": "safe"},
        source_file="private.json",
        source_line=None,
        record_shape="dict_record",
    )
    records = pilot.join_records_to_local_priors(
        pilot.normalize_records(pilot.ParseResult(records=[raw], files=[]), adapter_version="1.0"),
        None,
    )[0]
    summary = pilot.build_public_summary(
        generated_at="2026-06-01T00:00:00+00:00",
        parse_result=pilot.ParseResult(records=[raw], files=[tmp_path / "private.json"]),
        records=records,
        gallery_env={"gallery_dl_available": True, "gallery_dl_version": "1.0"},
        command_summary={"metadata_command_count": 1, "metadata_success_count": 1, "metadata_failure_count": 0},
        join_summary={"status_counts": {"local_prior_join_not_run": 1}, "page_index_status_counts": {"page_index_within_page_count": 1}},
        db_identity=None,
        download_public={"bounded_downloads_used": False, "downloaded_file_count": 0, "downloaded_total_bytes": 0, "cleanup_performed": False},
        unexpected_images={"unexpected_image_files_detected": False, "unexpected_image_file_count": 0},
        pr_context={"pr87_state": "MERGED", "pr86_state": "CLOSED"},
    )
    report = pilot.build_markdown_report(summary, private_markers=pilot._private_markers(records))

    assert "100000005" not in json.dumps(summary, ensure_ascii=False)
    assert "100000005" not in report
    with pytest.raises(pilot.PrivacyBlocked):
        pilot.assert_public_payload_safe({"leak": "C:\\Users\\person\\100000005_p0.jpg"})


def test_public_summary_separates_raw_directory_url_and_media_counts(tmp_path):
    input_file = _write(
        tmp_path / "events.json",
        json.dumps(
            [
                [2, {"id": 100000014, "num": 0, "page_count": 1, "title": "context"}],
                [
                    3,
                    "https://example.invalid/100000014_p0.jpg",
                    {"id": 100000014, "filename": "100000014_p0", "num": 0, "page_count": 1},
                ],
            ]
        ),
    )
    parse_result = pilot.parse_gallery_dl_json_inputs(input_file)
    records = pilot.normalize_records(parse_result, adapter_version="1.0")
    joined, join_summary = pilot.join_records_to_local_priors(records, None)

    summary = pilot.build_public_summary(
        generated_at="2026-06-01T00:00:00+00:00",
        parse_result=parse_result,
        records=joined,
        gallery_env={"gallery_dl_available": True, "gallery_dl_version": "1.0"},
        command_summary={"metadata_command_count": 1, "metadata_success_count": 1, "metadata_failure_count": 0},
        join_summary=join_summary,
        db_identity=None,
        download_public={
            "bounded_downloads_used": False,
            "downloaded_file_count": 0,
            "downloaded_total_bytes": 0,
            "cleanup_performed": False,
        },
        unexpected_images={"unexpected_image_files_detected": False, "unexpected_image_file_count": 0},
        pr_context={"pr87_state": "MERGED", "pr86_state": "CLOSED"},
    )

    assert summary["input_summary"]["raw_event_count"] == 2
    assert summary["input_summary"]["directory_context_event_count"] == 1
    assert summary["input_summary"]["url_media_event_count"] == 1
    assert summary["input_summary"]["normalized_media_record_count"] == 1
    assert summary["raw_record_shape_distribution"]["gallery_dl_directory_context_event"] == 1
    assert summary["record_shape_distribution"]["gallery_dl_url_media_event"] == 1


def test_secret_like_token_or_cookie_payload_is_blocked():
    with pytest.raises(pilot.PrivacyBlocked):
        pilot.normalize_gallery_dl_record(
            pilot.GalleryDlRawRecord(
                data={"id": 100000006, "refresh_token": "abc"},
                source_file="private.json",
                source_line=None,
                record_shape="dict_record",
            )
        )
    with pytest.raises(pilot.PrivacyBlocked):
        pilot.assert_no_secret_like_payload({"nested": {"Authorization": "Bearer abcdefghijklmnop"}})


def test_local_filename_prior_join_success_duplicate_and_page_out_of_range(db):
    _media(db, 1, filename="100000007_p0.jpg")
    _media(db, 2, filename="100000008_p0.jpg")
    _media(db, 3, filename="copy-100000008_p0.jpg")
    prior_index = pilot.build_local_prior_index(db)
    records = [
        pilot.PixivGalleryDlMetadataRecord(work_id="100000007", page_index=0, page_count=1, metadata_richness="rich_structured_metadata"),
        pilot.PixivGalleryDlMetadataRecord(work_id="100000008", page_index=0, page_count=1, metadata_richness="rich_structured_metadata"),
        pilot.PixivGalleryDlMetadataRecord(work_id="100000009", page_index=4, page_count=2, metadata_richness="rich_structured_metadata"),
    ]

    joined, summary = pilot.join_records_to_local_priors(records, prior_index)

    assert joined[0].local_match_status == "metadata_matches_eligible_anime_local_prior"
    assert joined[0].local_media_id_private == 1
    assert joined[0].eligible_for_future_local_source_hint is True
    assert joined[0].eligible_for_future_entity_candidate is True
    assert joined[1].local_match_status == "duplicate_or_ambiguous_local_match"
    assert joined[2].local_match_status == "page_index_out_of_range"
    assert summary["status_counts"]["metadata_matches_eligible_anime_local_prior"] == 1
    assert summary["status_counts"]["duplicate_or_ambiguous_local_match"] == 1
    assert summary["status_counts"]["page_index_out_of_range"] == 1
    assert summary["match_content_class_counts"]["anime"] == 2


def test_missing_page_index_is_classified():
    joined, summary = pilot.join_records_to_local_priors(
        [pilot.PixivGalleryDlMetadataRecord(work_id="100000010", page_index=None)],
        None,
    )
    assert joined[0].local_match_status == "missing_page_index"
    assert summary["page_index_status_counts"]["missing_page_index"] == 1


@pytest.mark.parametrize(
    ("content_class", "expected_status"),
    [
        (ContentClassEnum.anime, "metadata_matches_eligible_anime_local_prior"),
        (ContentClassEnum.non_anime, "metadata_matches_ineligible_content_class"),
        (ContentClassEnum.unknown, "metadata_matches_ineligible_content_class"),
    ],
)
def test_future_candidate_eligibility_requires_anime_content_class(db, content_class, expected_status):
    _media(db, 20, filename="100000020_p0.jpg", content_class=content_class)
    prior_index = pilot.build_local_prior_index(db)
    records = [
        pilot.PixivGalleryDlMetadataRecord(
            work_id="100000020",
            page_index=0,
            page_count=1,
            metadata_richness="rich_structured_metadata",
        )
    ]

    joined, summary = pilot.join_records_to_local_priors(records, prior_index)

    assert joined[0].local_match_status == expected_status
    assert joined[0].local_match_content_classes_private == (content_class.value,)
    assert summary["match_content_class_counts"][content_class.value] == 1
    if content_class is ContentClassEnum.anime:
        assert joined[0].eligible_for_future_local_source_hint is True
        assert joined[0].eligible_for_future_entity_candidate is True
    else:
        assert joined[0].eligible_for_future_local_source_hint is False
        assert joined[0].eligible_for_future_entity_candidate is False


def test_read_only_guard_blocks_db_writes():
    engine = create_engine("sqlite://")
    pilot.install_read_only_guard(engine)
    with engine.connect() as conn:
        with pytest.raises(pilot.ReadOnlyViolation):
            conn.execute(text("INSERT INTO example VALUES (1)"))
    engine.dispose()


def test_missing_input_has_actionable_code(tmp_path):
    with pytest.raises(pilot.MissingInputError, match="missing_gallery_dl_json_input"):
        pilot.parse_gallery_dl_json_inputs(tmp_path / "missing")


def test_download_artifacts_are_phase_scoped_and_cleanup_stays_inside(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    downloads = tmp_path / ".local_manifests" / "phase-4.4p2r-f1-gallery-dl-json-import-downloads"
    downloads.mkdir(parents=True)
    image_file = downloads / "ref.jpg"
    image_file.write_bytes(b"abc")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"do not delete")

    public, private = pilot.download_artifact_summary([downloads], cleanup=True)

    assert public["downloaded_file_count"] == 1
    assert public["downloaded_total_bytes"] == 3
    assert public["cleanup_file_count"] == 1
    assert not image_file.exists()
    assert outside.exists()
    assert private["download_files_private"]


def test_unexpected_image_files_under_phase_output_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    phase_root = tmp_path / ".local_manifests" / "phase-4.4p2r-f1-gallery-dl-json-import-pilot"
    allowed = phase_root / "downloads"
    phase_root.mkdir(parents=True)
    allowed.mkdir()
    (phase_root / "unexpected.png").write_bytes(b"x")
    (allowed / "allowed.png").write_bytes(b"x")

    result = pilot.detect_unexpected_images_under_phase([phase_root], allowed_download_dirs=[allowed])

    assert result["unexpected_image_files_detected"] is True
    assert result["unexpected_image_file_count"] == 1


def test_public_report_includes_download_counts_but_not_exact_image_urls(tmp_path):
    raw = pilot.GalleryDlRawRecord(
        data={"id": 100000011, "num": 0, "page_count": 1, "url": "https://i.pximg.net/img-original/img/x/100000011_p0.jpg"},
        source_file="private.json",
        source_line=None,
        record_shape="dict_record",
    )
    records = pilot.normalize_records(pilot.ParseResult(records=[raw], files=[]), adapter_version="1.0")
    joined, join_summary = pilot.join_records_to_local_priors(records, None)
    summary = pilot.build_public_summary(
        generated_at="2026-06-01T00:00:00+00:00",
        parse_result=pilot.ParseResult(records=[raw], files=[tmp_path / "private.json"]),
        records=joined,
        gallery_env={"gallery_dl_available": True, "gallery_dl_version": "1.0"},
        command_summary={"metadata_command_count": 1, "metadata_success_count": 1, "metadata_failure_count": 0},
        join_summary=join_summary,
        db_identity=None,
        download_public={"bounded_downloads_used": True, "downloaded_file_count": 1, "downloaded_total_bytes": 3, "cleanup_performed": False},
        unexpected_images={"unexpected_image_files_detected": False, "unexpected_image_file_count": 0},
        pr_context={"pr87_state": "MERGED", "pr86_state": "CLOSED"},
    )
    report = pilot.build_markdown_report(summary, private_markers=pilot._private_markers(joined))

    assert "downloaded_file_count" in json.dumps(summary)
    assert "100000011_p0" not in report
    assert "i.pximg.net" not in report
