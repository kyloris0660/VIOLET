"""Focused tests for Phase 4.4-P1 Pixiv live reference pilot."""

from __future__ import annotations

import json
import io
import sys
from pathlib import Path

import pytest
from PIL import Image
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
from scripts import run_phase44p0_pixiv_source_prior_auto_verify as p0  # noqa: E402
from scripts import run_phase44p1_pixiv_live_reference_metadata_pilot as p1  # noqa: E402


def _jpeg_bytes(color=(120, 80, 40)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 12), color=color).save(output, format="JPEG")
    return output.getvalue()


def _html_result(url: str, body: str = '<meta property="og:title" content="Public work">') -> p1.HttpResult:
    return p1.HttpResult(
        url=url,
        final_url=url,
        status=200,
        content_type="text/html; charset=utf-8",
        content_length_header=len(body),
        body=body.encode("utf-8"),
    )


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
    filename: str | None = None,
    path: str | None = None,
    thumbnail_path: str | None = None,
    content_class=ContentClassEnum.anime,
) -> Media:
    item = Media(
        id=media_id,
        filename=filename or f"private_original_name_{media_id}.jpg",
        path=path if path is not None else f"media/original/{media_id}.jpg",
        thumbnail_path=thumbnail_path if thumbnail_path is not None else f"media/thumbnails/{media_id}.jpg",
        hash=f"{media_id:064x}",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=123,
        width=16,
        height=12,
        rating=RatingEnum.safe,
        content_class=content_class,
    )
    db.add(item)
    db.commit()
    return item


def test_violet_storage_root_settings_loading(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    storage = tmp_path / "storage"
    (project / ".env").parent.mkdir(parents=True, exist_ok=True)
    (project / ".env").write_text("POSTGRES_HOST=env-host\n", encoding="utf-8")
    settings_dir = storage / "data"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "database": {
                    "host": "storage-host",
                    "port": 5544,
                    "name": "blombooru",
                    "user": "storage-user",
                    "password": "storage-pass",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("VIOLET_ENV", "development")
    for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "TEST_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)

    config = p1.load_project_config(project_root=project)

    assert config.storage_root == storage.resolve()
    assert config.settings_source == "storage_root_data_settings_json"
    assert config.db_host == "storage-host"
    assert config.db_port == 5544
    assert config.db_user == "storage-user"
    assert config.db_password == "storage-pass"


def test_output_override_path_reporting_uses_actual_resolved_paths(tmp_path):
    args = p1.build_arg_parser().parse_args(
        [
            "--reference-details-json",
            ".local_manifests/p1-custom/reference.json",
            "--correspondence-details-json",
            ".local_manifests/p1-custom/correspondence.json",
            "--sheet-md",
            ".local_manifests/p1-custom/sheet.md",
            "--sheet-csv",
            ".local_manifests/p1-custom/sheet.csv",
            "--preview-dir",
            ".local_manifests/p1-custom/previews",
        ]
    )
    paths = p1.resolve_output_paths(args)
    labels = p1.build_public_artifact_labels(paths)

    assert labels["reference_details_json"] == ".local_manifests/p1-custom/reference.json"
    assert labels["correspondence_details_json"] == ".local_manifests/p1-custom/correspondence.json"
    assert labels["metadata_sheet_md"] == ".local_manifests/p1-custom/sheet.md"
    assert labels["metadata_sheet_csv"] == ".local_manifests/p1-custom/sheet.csv"
    assert labels["preview_dir"] == ".local_manifests/p1-custom/previews"
    assert labels["manual_validation_sheet_md"] == ".local_manifests/phase-4.4p1-pixiv-manual-validation-sheet.md"
    assert labels["full_local_paths_public"] is False


def test_sample_selection_keeps_exact_ids_private(db):
    _media(db, 1, filename="100729533_p0.jpg")
    _media(db, 2, filename="100729533_p1.jpg")
    _media(db, 3, filename="prefix-200000001_p0.jpg")
    _media(db, 4, filename="200000002_p0-20260514232809.jpg")
    public, private = p0.audit_pixiv_source_priors(db, approved_ids=[])
    media_by_id = {int(row.id): row for row in db.query(Media).all()}

    sample_summary, selected = p1.select_p1_sample(private, media_by_id, sample_size=3)

    assert sample_summary["selected_count"] == 3
    assert sample_summary["exact_pixiv_ids_public"] is False
    assert "100729533" not in json.dumps(sample_summary, ensure_ascii=False)
    assert "100729533" in json.dumps(selected, ensure_ascii=False)


def test_sample_selection_fallback_stays_anime_only(db):
    _media(db, 1, filename="100729533_p0.jpg", content_class=ContentClassEnum.anime)
    _media(db, 2, filename="200000001_p0.jpg", content_class=ContentClassEnum.unknown)
    _media(db, 3, filename="200000002_p0.jpg", content_class=ContentClassEnum.non_anime)
    public, private = p0.audit_pixiv_source_priors(db, approved_ids=[])
    media_by_id = {int(row.id): row for row in db.query(Media).all()}

    sample_summary, selected = p1.select_p1_sample(private, media_by_id, sample_size=3)

    assert sample_summary["anime_only"] is True
    assert sample_summary["selected_count"] == 1
    assert sample_summary["requested_sample_size"] == 3
    assert sample_summary["insufficient_anime_candidates"] is True
    assert sample_summary["non_anime_candidates_excluded"] == 2
    assert [item["content_class"] for item in selected] == ["anime"]


def test_public_report_excludes_exact_pixiv_ids_and_local_paths(db):
    _media(db, 1, filename="100729533_p0.jpg")
    extraction_summary, private = p0.audit_pixiv_source_priors(db, approved_ids=[])
    sample_summary = {
        "selected_count": 1,
        "selection_strategy": "test",
        "category_counts": {},
        "page_case_distribution": {"p0": 1},
        "exact_pixiv_ids_public": False,
    }
    network_policy = p1.build_network_policy(1, 2.0, 10.0)
    aggregate = {
        "pixiv_page_probe": {
            "requests_attempted": 0,
            "http_status_distribution": {},
            "final_url_host_distribution": {},
            "blocked_count": 0,
            "stopped_early": False,
            "public_exact_urls": False,
        },
        "metadata_availability": {"metadata_richness_distribution": {}, "field_availability_counts": {}},
        "preview_availability": {"preview_status_distribution": {}},
        "correspondence_verification": {"result_distribution": {}, "threshold_policy_version": "test"},
    }
    summary = p1.build_public_summary(
        generated_at="2026-05-31T00:00:00+00:00",
        identity={"db_identity_result": "development_blombooru_confirmed"},
        artifact_labels={"reference_details_json": ".local_manifests/x.json"},
        extraction_summary=extraction_summary,
        sample_summary=sample_summary,
        network_policy=network_policy,
        aggregate=aggregate,
        booru_policy=p1.booru_lookup_policy_result(),
        reviewer_carry_forward=[],
    )

    p1.assert_public_payload_safe(summary, private_markers=private["distinct_pixiv_work_ids"])
    assert "100729533" not in json.dumps(summary, ensure_ascii=False)


def test_page_probe_headers_use_no_cookies_or_referer():
    headers = p1.build_safe_headers(accept="text/html")

    assert "Cookie" not in headers
    assert "Referer" not in headers
    assert "VIOLET-P1-PixivPublicProbe" in headers["User-Agent"]


@pytest.mark.parametrize(
    ("status", "body", "expected_reason"),
    [
        (403, "", "http_403"),
        (429, "", "http_429"),
        (200, "please login to continue", "login_captcha_consent_or_antibot_marker"),
        (200, "captcha required", "login_captcha_consent_or_antibot_marker"),
    ],
)
def test_blocked_page_detection(status, body, expected_reason):
    blocked, reason = p1.detect_blocked_page(status, body, content_type="text/html")

    assert blocked is True
    assert reason == expected_reason


def test_login_text_does_not_block_when_public_metadata_exists():
    body = '<meta property="og:title" content="Public work"> consent login captcha'
    blocked, reason = p1.detect_blocked_page(200, body, content_type="text/html")

    assert blocked is False
    assert reason is None


def test_metadata_parser_handles_og_jsonld_and_preload_fixture():
    html_fixture = """
    <html><head>
      <meta property="og:title" content="Sample Pixiv Work">
      <meta property="og:description" content="Sample caption">
      <meta property="og:url" content="https://www.pixiv.net/artworks/100729533">
      <meta property="og:image" content="https://i.pximg.net/c/540x540_70/img-master/img/2023/01/01/00/00/00/100729533_p0_master1200.jpg">
      <script type="application/ld+json">{"@type":"ImageObject","name":"LD Title","image":"https://example.test/preview.jpg"}</script>
      <script id="meta-preload-data" type="application/json">{"illust":{"100729533":{"title":"Preload Title","userName":"Artist","userId":"42","pageCount":2,"tags":{"tags":[{"tag":"tag_a"},{"tag":"tag_b"}]},"urls":{"small":"https://example.test/small.jpg"}}}}</script>
    </head></html>
    """
    metadata = p1.parse_public_metadata(html_fixture)

    assert metadata["title"] == "Sample Pixiv Work"
    assert metadata["artist_user_name"] == "Artist"
    assert metadata["artist_user_id"] == "42"
    assert metadata["page_count"] == 2
    assert metadata["tags"] == ["tag_a", "tag_b"]
    assert metadata["preview_image_candidates"]
    assert "preview_image_candidates" in metadata["metadata_fields_found"]


def test_metadata_parser_handles_meta_tag_preload_payload_and_redacts_public_report():
    payload = json.dumps(
        {
            "illust": {
                "100729533": {
                    "title": "Meta Preload Title",
                    "userName": "Meta Artist",
                    "userId": "4242",
                    "pageCount": 3,
                    "tags": {"tags": [{"tag": "meta_tag_a"}, {"tag": "meta_tag_b"}]},
                    "urls": {"small": "https://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg"},
                }
            }
        }
    )
    html_fixture = f'<html><head><meta id="meta-preload-data" content="{p1.html.escape(payload, quote=True)}"></head></html>'

    metadata = p1.parse_public_metadata(html_fixture)

    assert metadata["preload_payload_found"] is True
    assert metadata["preload_data_found"] is True
    assert metadata["artist_user_name"] == "Meta Artist"
    assert metadata["artist_user_id"] == "4242"
    assert metadata["page_count"] == 3
    assert metadata["tags"] == ["meta_tag_a", "meta_tag_b"]
    assert p1.classify_metadata_richness(metadata, blocked=False) == "rich_structured_metadata"
    public = {"metadata_richness": p1.classify_metadata_richness(metadata, blocked=False)}
    p1.assert_public_payload_safe(public, private_markers=["100729533"])


def test_metadata_parser_malformed_meta_preload_fails_safely():
    metadata = p1.parse_public_metadata('<meta name="preload-data" content="{not-json">')

    assert metadata["preload_payload_found"] is True
    assert metadata["preload_data_found"] is False
    assert metadata["preload_parse_error_count"] == 1


def test_preview_fetch_blocks_403_without_bypass(tmp_path):
    def fake_get(*_args, **_kwargs):
        return p1.HttpResult(
            url="https://i.pximg.net/example.jpg",
            final_url="https://i.pximg.net/example.jpg",
            status=403,
            content_type="text/html",
            content_length_header=None,
            body=b"forbidden",
            error="http_error_403",
        )

    result = p1.fetch_preview_image(
        "https://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg",
        output_dir=tmp_path,
        index=1,
        timeout_seconds=1,
        http_get=fake_get,
    )

    assert result["status"] == "preview_fetch_blocked"
    assert result["reason"] == "http_403"
    assert not list(tmp_path.iterdir())


def test_preview_fetch_accepts_allowed_pixiv_image_host(tmp_path):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return p1.HttpResult(
            url=url,
            final_url=url,
            status=200,
            content_type="image/jpeg",
            content_length_header=None,
            body=_jpeg_bytes(),
        )

    result = p1.fetch_preview_image(
        "https://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg",
        output_dir=tmp_path,
        index=1,
        timeout_seconds=1,
        http_get=fake_get,
    )

    assert result["status"] == "reference_preview_fetched"
    assert result["host_policy_status"] == "allowed_pixiv_image_host"
    assert result["preview_url_host"] == "i.pximg.net"
    assert calls[0][1]["allow_redirects"] is False
    assert Path(result["image_path"]).exists()


def test_preview_fetch_rejects_unexpected_host_before_fetch(tmp_path):
    def fake_get(*_args, **_kwargs):
        raise AssertionError("third-party preview URL should not be fetched")

    result = p1.fetch_preview_image(
        "https://example.test/not-pixiv.jpg",
        output_dir=tmp_path,
        index=1,
        timeout_seconds=1,
        http_get=fake_get,
    )

    assert result["status"] == "preview_fetch_blocked_unexpected_host"
    assert result["reason"] == "initial_url_unexpected_host"
    assert result["preview_url_host"] == "example.test"
    assert not list(tmp_path.iterdir())


def test_preview_fetch_rejects_non_https_allowlisted_host_before_fetch(tmp_path):
    def fake_get(*_args, **_kwargs):
        raise AssertionError("non-HTTPS preview URL should not be fetched")

    for url in (
        "http://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg",
        "ftp://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg",
        "//i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg",
    ):
        result = p1.fetch_preview_image(
            url,
            output_dir=tmp_path,
            index=1,
            timeout_seconds=1,
            http_get=fake_get,
        )

        assert result["status"] == "preview_fetch_blocked_unexpected_host"
        assert result["reason"] == "initial_url_non_https"
        assert result["host_policy_status"] == "blocked_non_https"
    assert not list(tmp_path.iterdir())


def test_preview_fetch_rejects_redirect_to_unexpected_host_before_second_fetch(tmp_path):
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return p1.HttpResult(
            url=url,
            final_url="https://example.test/redirected.jpg",
            status=302,
            content_type="text/html",
            content_length_header=None,
            body=b"",
            error="http_error_302",
            redirect_location="https://example.test/redirected.jpg",
        )

    result = p1.fetch_preview_image(
        "https://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg",
        output_dir=tmp_path,
        index=1,
        timeout_seconds=1,
        http_get=fake_get,
    )

    assert calls == ["https://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg"]
    assert result["status"] == "preview_fetch_blocked_unexpected_host"
    assert result["reason"] == "redirect_url_unexpected_host"
    assert result["final_url_host"] == "example.test"
    assert not list(tmp_path.iterdir())


def test_preview_fetch_rejects_redirect_to_non_https_allowlisted_host(tmp_path):
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return p1.HttpResult(
            url=url,
            final_url="http://i.pximg.net/redirected.jpg",
            status=302,
            content_type="text/html",
            content_length_header=None,
            body=b"",
            error="http_error_302",
            redirect_location="http://i.pximg.net/redirected.jpg",
        )

    result = p1.fetch_preview_image(
        "https://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg",
        output_dir=tmp_path,
        index=1,
        timeout_seconds=1,
        http_get=fake_get,
    )

    assert len(calls) == 1
    assert result["status"] == "preview_fetch_blocked_unexpected_host"
    assert result["reason"] == "redirect_url_non_https"
    assert result["host_policy_status"] == "blocked_non_https"
    assert not list(tmp_path.iterdir())


def test_preview_candidates_skip_unexpected_and_try_later_allowed_host(tmp_path):
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return p1.HttpResult(
            url=url,
            final_url=url,
            status=200,
            content_type="image/jpeg",
            content_length_header=None,
            body=_jpeg_bytes(),
        )

    result = p1.fetch_preview_from_candidates(
        [
            "https://example.test/static.jpg",
            "https://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg",
        ],
        output_dir=tmp_path,
        index=1,
        timeout_seconds=1,
        http_get=fake_get,
    )

    assert calls == ["https://i.pximg.net/c/540x540_70/img-master/img/example_p0_master1200.jpg"]
    assert result["status"] == "reference_preview_fetched"
    assert result["preview_candidates_total"] == 2
    assert result["preview_candidates_skipped_unexpected_host"] == 1
    assert result["preview_candidates_attempted_allowed"] == 1


def test_preview_host_policy_aggregation_counts_blocked_host():
    aggregate = p1.aggregate_public_results(
        [
            {
                "media_id": 1,
                "network_attempted": True,
                "http_status": 200,
                "final_url": "https://www.pixiv.net/artworks/123456",
                "metadata_richness": "preview_only",
                "preview_result": {
                    "status": "preview_fetch_blocked_unexpected_host",
                    "host_policy_status": "blocked_unexpected_host",
                },
            }
        ],
        [{"media_id": 1, "status": "preview_fetch_blocked"}],
    )

    assert aggregate["pixiv_page_probe"]["requests_attempted"] == 1
    assert aggregate["preview_availability"]["preview_status_distribution"]["preview_fetch_blocked_unexpected_host"] == 1
    assert aggregate["preview_availability"]["preview_candidate_host_policy_distribution"]["blocked_unexpected_host"] == 1


def test_network_error_counts_as_attempted_request(tmp_path):
    sample = [
        {
            "media_id": 1,
            "matches": [{"source_field": "stored_filename", "pixiv_work_id": "100729533", "page_index": 0}],
            "local_media": {"thumbnail_path": "", "path": ""},
        }
    ]

    def fake_page_get(*_args, **_kwargs):
        return p1.HttpResult(
            url="https://www.pixiv.net/artworks/100729533",
            final_url="https://www.pixiv.net/artworks/100729533",
            status=None,
            content_type=None,
            content_length_header=None,
            body=b"",
            error="url_error:TimeoutError",
        )

    page_results, corr = p1.probe_pixiv_pages(
        sample,
        timeout_seconds=1,
        delay_seconds=0,
        preview_dir=tmp_path,
        storage_root=tmp_path,
        page_http_get=fake_page_get,
    )
    aggregate = p1.aggregate_public_results(page_results, corr)

    assert page_results[0]["network_attempted"] is True
    assert page_results[0]["blocked_reason"] == "network_error"
    assert aggregate["pixiv_page_probe"]["requests_attempted"] == 1
    assert aggregate["pixiv_page_probe"]["network_attempts_including_failures"] == 1
    assert aggregate["pixiv_page_probe"]["http_status_distribution"] == {}
    assert aggregate["pixiv_page_probe"]["status_none_count"] == 1
    assert aggregate["pixiv_page_probe"]["network_error_distribution"]["url_error:TimeoutError"] == 1


def test_page_redirect_to_unknown_host_blocked_before_follow(tmp_path):
    calls = []

    def fake_page_get(url, **kwargs):
        calls.append((url, kwargs))
        return p1.HttpResult(
            url=url,
            final_url="https://example.test/not-pixiv",
            status=302,
            content_type="text/html",
            content_length_header=None,
            body=b"",
            error="http_error_302",
            redirect_location="https://example.test/not-pixiv",
        )

    sample = [
        {
            "media_id": 1,
            "matches": [{"source_field": "stored_filename", "pixiv_work_id": "100729533", "page_index": 0}],
            "local_media": {"thumbnail_path": "", "path": ""},
        }
    ]
    page_results, corr = p1.probe_pixiv_pages(
        sample,
        timeout_seconds=1,
        delay_seconds=0,
        preview_dir=tmp_path,
        storage_root=tmp_path,
        page_http_get=fake_page_get,
    )

    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False
    assert page_results[0]["blocked"] is True
    assert page_results[0]["blocked_reason"] == "redirect_blocked_unexpected_host"
    assert page_results[0]["page_redirect_policy_status"] == "blocked"
    assert corr[0]["status"] == "pixiv_page_blocked"


def test_safe_pixiv_page_redirect_is_followed_with_no_redirect_policy(tmp_path):
    calls = []

    def fake_page_get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return p1.HttpResult(
                url=url,
                final_url="https://www.pixiv.net/en/artworks/100729533",
                status=302,
                content_type="text/html",
                content_length_header=None,
                body=b"",
                error="http_error_302",
                redirect_location="https://www.pixiv.net/en/artworks/100729533",
            )
        return _html_result(url)

    sample = [
        {
            "media_id": 1,
            "matches": [{"source_field": "stored_filename", "pixiv_work_id": "100729533", "page_index": 0}],
            "local_media": {"thumbnail_path": "", "path": ""},
        }
    ]
    page_results, _corr = p1.probe_pixiv_pages(
        sample,
        timeout_seconds=1,
        delay_seconds=0,
        preview_dir=tmp_path,
        storage_root=tmp_path,
        page_http_get=fake_page_get,
    )
    aggregate = p1.aggregate_public_results(page_results, [])

    assert [call[0] for call in calls] == [
        "https://www.pixiv.net/artworks/100729533",
        "https://www.pixiv.net/en/artworks/100729533",
    ]
    assert all(call[1]["allow_redirects"] is False for call in calls)
    assert page_results[0]["blocked"] is False
    assert page_results[0]["page_redirect_policy_status"] == "followed_safe_redirect"
    assert page_results[0]["network_attempt_count"] == 2
    assert aggregate["pixiv_page_probe"]["network_attempts_including_failures"] == 2


def test_transient_network_error_records_item_and_continues_sample(tmp_path):
    calls = []

    def fake_page_get(url, **_kwargs):
        calls.append(url)
        if len(calls) == 1:
            return p1.HttpResult(
                url=url,
                final_url=url,
                status=None,
                content_type=None,
                content_length_header=None,
                body=b"",
                error="url_error:TimeoutError",
            )
        return _html_result(url)

    sample = [
        {
            "media_id": 1,
            "matches": [{"source_field": "stored_filename", "pixiv_work_id": "100729533", "page_index": 0}],
            "local_media": {"thumbnail_path": "", "path": ""},
        },
        {
            "media_id": 2,
            "matches": [{"source_field": "stored_filename", "pixiv_work_id": "200000001", "page_index": 0}],
            "local_media": {"thumbnail_path": "", "path": ""},
        },
    ]
    page_results, corr = p1.probe_pixiv_pages(
        sample,
        timeout_seconds=1,
        delay_seconds=0,
        preview_dir=tmp_path,
        storage_root=tmp_path,
        page_http_get=fake_page_get,
    )
    aggregate = p1.aggregate_public_results(page_results, corr)

    assert len(calls) == 2
    assert page_results[0]["blocked_reason"] == "network_error"
    assert page_results[1].get("status") != "not_attempted_after_stop"
    assert aggregate["pixiv_page_probe"]["requests_attempted"] == 2
    assert aggregate["pixiv_page_probe"]["stopped_early"] is False


def test_image_correspondence_accepts_similar_and_rejects_mismatch():
    local = p1.build_image_signature_from_image(Image.new("RGB", (80, 60), color=(120, 80, 40)))
    similar = p1.build_image_signature_from_image(Image.new("RGB", (80, 60), color=(122, 82, 42)))
    mismatch = p1.build_image_signature_from_image(Image.new("RGB", (60, 120), color=(250, 250, 250)))

    assert p1.compare_image_signatures(local, similar)["auto_verification_status"] == "auto_verified_high_confidence"
    assert p1.compare_image_signatures(local, mismatch)["auto_verification_status"] == "auto_rejected_mismatch"


def test_read_only_guard_blocks_write_sql():
    engine = create_engine("sqlite://")
    p1.install_read_only_guard(engine)
    with engine.connect() as conn:
        with pytest.raises(p1.ReadOnlyViolation):
            conn.execute(text("INSERT INTO example VALUES (1)"))
    engine.dispose()


def test_summary_json_public_safety_blocks_exact_artwork_url():
    with pytest.raises(p1.PrivacyBlocked):
        p1.assert_public_payload_safe({"url": "https://www.pixiv.net/artworks/100729533"})


def test_optional_booru_lookup_is_blocked_by_default_and_with_reserved_flag():
    default = p1.booru_lookup_policy_result()
    enabled = p1.booru_lookup_policy_result(enabled=True)

    assert default["status"] == "no_upload_booru_lookup_policy_blocked"
    assert default["requests_attempted"] == 0
    assert enabled["status"] == "no_upload_booru_lookup_policy_blocked"
    assert enabled["requests_attempted"] == 0


def test_manual_validation_sheet_is_private_and_public_summary_is_redacted(tmp_path):
    local_rel = Path("media/thumbnails/1.jpg")
    local_abs = tmp_path / local_rel
    local_abs.parent.mkdir(parents=True)
    local_abs.write_bytes(_jpeg_bytes(color=(10, 20, 30)))
    preview_path = tmp_path / "preview.jpg"
    preview_path.write_bytes(_jpeg_bytes(color=(40, 50, 60)))
    selected = [
        {
            "media_id": 1,
            "local_media": {
                "filename": "100729533_p0.jpg",
                "filename_basename": "100729533_p0.jpg",
                "thumbnail_path": str(local_rel).replace("\\", "/"),
                "path": "",
            },
        }
    ]
    page_results = [
        {
            "media_id": 1,
            "pixiv_work_id": "100729533",
            "page_index": 0,
            "url": "https://www.pixiv.net/artworks/100729533",
            "metadata_richness": "rich_structured_metadata",
            "metadata": {"title": "Private Title", "artist_user_name": "Artist", "tags": ["tag_a"], "page_count": 1},
            "preview_result": {
                "status": "reference_preview_fetched",
                "image_path": str(preview_path),
                "preview_url_host": "i.pximg.net",
                "final_url_host": "i.pximg.net",
            },
        }
    ]
    correspondence = [
        {
            "media_id": 1,
            "status": "auto_rejected_mismatch",
            "scores": {"aspect_ratio_delta": 0.25, "ahash_distance": 8, "dhash_distance": 9, "average_color_distance": 40},
        }
    ]

    rows = p1.build_manual_validation_rows(selected, page_results, correspondence, storage_root=tmp_path)
    sheet = p1.build_manual_validation_sheet_md(rows)

    assert rows[0]["media_id"] == 1
    assert rows[0]["pixiv_work_id"] == "100729533"
    assert rows[0]["user_visual_match"] == ""
    assert rows[0]["user_notes"] == ""
    assert rows[0]["likely_mismatch_reason_bucket"] == "preview_crop_or_thumbnail_variant"
    assert "100729533" in sheet
    assert str(local_abs) in sheet

    args = p1.build_arg_parser().parse_args([])
    paths = p1.resolve_output_paths(args)
    public_summary = p1.manual_validation_public_summary(rows, paths)
    p1.assert_public_payload_safe(public_summary, private_markers=["100729533", str(local_abs)])
    assert public_summary["items_needing_manual_validation"] == 1
    assert public_summary["contact_sheet_html"].startswith(".local_manifests/")
