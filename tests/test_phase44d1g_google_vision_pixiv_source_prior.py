"""Focused tests for the Phase 4.4-D1G runner."""

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
from scripts import run_phase44d1g_google_vision_pixiv_source_prior as d1g  # noqa: E402


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
    filename: str | None = None,
    path: str | None = None,
    thumbnail_path: str | None = None,
    source: str | None = None,
    content_class=ContentClassEnum.anime,
    file_type=FileTypeEnum.image,
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


@pytest.mark.parametrize(
    ("text", "work_id", "page"),
    [
        ("100729533_p0.jpg", "100729533", 0),
        ("100729533_p1.jpg", "100729533", 1),
        ("144735627_p0-20260514232809.jpg", "144735627", 0),
        ("144735627_p0(1)-20260514232809.jpg", "144735627", 0),
        ("prefix-144735627_p0.jpg", "144735627", 0),
    ],
)
def test_pixiv_filename_regex_accepts_required_examples(text, work_id, page):
    matches = d1g.extract_pixiv_filename_prior_from_text(text)
    assert matches
    assert matches[0]["pixiv_work_id"] == work_id
    assert matches[0]["page_index"] == page


@pytest.mark.parametrize(
    "text",
    [
        "100729533-x_p0.jpg",
        "100729533-p0.jpg",
        "abc_p0.jpg",
        "12345_p0.jpg",
        "100729533_p.jpg",
        "100729533__p0.jpg",
    ],
)
def test_pixiv_filename_regex_rejects_absent_or_broken_token(text):
    assert d1g.extract_pixiv_filename_prior_from_text(text) == []


def test_sample_gate_rejects_ids_outside_approved_set():
    with pytest.raises(d1g.SampleGateError, match="outside approved"):
        d1g.parse_media_ids([2690, 999999])


def test_sample_gate_requires_exact_approved_set():
    with pytest.raises(d1g.SampleGateError, match="exact approved"):
        d1g.parse_media_ids([2690])


def test_derived_input_uses_safe_name_and_strips_private_filename(db, tmp_path):
    media_id = d1g.APPROVED_SAMPLE_IDS[0]
    private_filename = "100729533_p0_private_title.jpg"
    media = _media(db, media_id, filename=private_filename)
    _make_storage_for_media(tmp_path, media_id)
    derived = d1g.generate_derived_input(media, storage_root=tmp_path, output_dir=tmp_path / "derived")
    assert derived.safe_filename == f"phase44d1g_m{media_id}_derived.jpg"
    assert private_filename not in derived.safe_filename
    assert derived.width <= d1g.MAX_DERIVED_DIMENSION
    assert derived.height <= d1g.MAX_DERIVED_DIMENSION
    assert derived.path.name == derived.safe_filename


def test_public_sample_gate_excludes_filename_path_and_source_label(db, tmp_path):
    media_id = d1g.APPROVED_SAMPLE_IDS[0]
    private_filename = "144735627_p0_private.jpg"
    private_source = "private_source_label"
    _media(db, media_id, filename=private_filename, source=private_source)
    _make_storage_for_media(tmp_path, media_id)
    sample_gate, _ = d1g.build_sample_gate(db, media_ids=[media_id], storage_root=tmp_path)
    payload = json.dumps({key: value for key, value in sample_gate.items() if key != "private_sample_details"})
    assert str(tmp_path) not in payload
    assert "media/original" not in payload
    assert "media/thumbnails" not in payload
    assert private_filename not in payload
    assert private_source not in payload


def test_pixiv_audit_keeps_exact_ids_in_private_details_only(db):
    _media(db, 1, filename="100729533_p0.jpg", content_class=ContentClassEnum.anime)
    _media(db, 2, filename="plain_name.jpg", content_class=ContentClassEnum.non_anime)
    public, private = d1g.audit_pixiv_source_priors(db, approved_ids=[1, 2])
    assert public["media_with_pixiv_like_filename_token"] == 1
    assert public["distinct_candidate_pixiv_work_ids"] == 1
    assert "100729533" not in json.dumps(public, ensure_ascii=False)
    assert "100729533" in json.dumps(private, ensure_ascii=False)


def test_read_only_guard_blocks_write_sql():
    engine = create_engine("sqlite://")
    d1g.install_read_only_guard(engine)
    with engine.connect() as conn:
        with pytest.raises(d1g.ReadOnlyViolation):
            conn.execute(text("INSERT INTO example VALUES (1)"))
    engine.dispose()


def test_public_report_safety_allows_redacted_credential_booleans_and_blocks_local_path():
    d1g.assert_public_report_safe(
        {
            "google_application_credentials_set": False,
            "credential_contents_printed": False,
            "adc_token_printed": False,
        }
    )
    with pytest.raises(d1g.PrivacyBlocked):
        d1g.assert_public_report_safe({"leak": "C:\\Users\\person\\secret.json"})
