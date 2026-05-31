"""Focused tests for Phase 4.4-P1 Pixiv live reference pilot."""

from __future__ import annotations

import json
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
