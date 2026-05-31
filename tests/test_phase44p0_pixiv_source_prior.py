"""Focused tests for the Phase 4.4-P0 Pixiv source-prior runner."""

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
    matches = p0.extract_pixiv_filename_prior_from_text(text)
    assert matches
    assert matches[0]["pixiv_work_id"] == work_id
    assert matches[0]["page_index"] == page


@pytest.mark.parametrize(
    "text",
    [
        "144735627-x_p0.jpg",
        "144735627_px.jpg",
        "144735627p0.jpg",
        "abc_p0.jpg",
        "123_p0.jpg",
    ],
)
def test_pixiv_filename_regex_rejects_absent_or_broken_token(text):
    assert p0.extract_pixiv_filename_prior_from_text(text) == []


def test_uppercase_variant_is_reported_but_not_accepted():
    assert p0.extract_pixiv_filename_prior_from_text("144735627_P0.jpg") == []
    variants = p0.detect_pixiv_filename_variants("144735627_P0.jpg")
    assert variants
    assert variants[0]["reason"] == "uppercase_page_marker_possible_variant"


def test_audit_reports_public_aggregates_and_keeps_exact_ids_private(db):
    _media(db, 1, filename="100729533_p0.jpg", content_class=ContentClassEnum.anime)
    _media(db, 2, filename="prefix-100729533_p1.jpg", content_class=ContentClassEnum.anime)
    _media(db, 3, filename="144735627_P0.jpg", content_class=ContentClassEnum.non_anime)
    _media(db, 4, filename="plain_name.jpg", content_class=ContentClassEnum.unknown)

    public, private = p0.audit_pixiv_source_priors(db, approved_ids=[1, 2, 3, 4])

    assert public["total_media_inspected"] == 4
    assert public["media_with_one_or_more_pixiv_like_tokens"] == 2
    assert public["distinct_candidate_pixiv_work_ids"] == 1
    assert public["duplicate_work_id_count"] == 1
    assert public["page_index_distribution"] == {"0": 1, "1": 1}
    assert public["invalid_or_variant_token_count"] == 1
    assert "100729533" not in json.dumps(public, ensure_ascii=False)
    assert "100729533" in json.dumps(private, ensure_ascii=False)


def test_sample_selection_covers_special_cases(db):
    _media(db, 1, filename="100729533_p0.jpg")
    _media(db, 2, filename="144735627_p0-20260514232809.jpg")
    _media(db, 3, filename="144735627_p0(1)-20260514232809.jpg")
    _media(db, 4, filename="prefix-200000001_p0.jpg")
    _media(db, 5, filename="200000002_p1.jpg")
    _media(db, 6, filename="200000001_p0-again.jpg")
    public, private = p0.audit_pixiv_source_priors(db, approved_ids=[])

    sample_summary, selected = p0.select_feasibility_sample(private, max_items=6)
    categories = sample_summary["category_counts"]

    assert public["media_with_one_or_more_pixiv_like_tokens"] == 6
    assert categories["simple_exact_token_basename"] >= 1
    assert categories["suffix_timestamp_case"] >= 1
    assert categories["duplicate_marker_case"] >= 1
    assert categories["prefixed_token"] >= 1
    assert categories["non_p0_page"] >= 1
    assert categories["duplicate_work_id_case"] >= 1
    assert len(selected) <= 6


def test_read_only_guard_blocks_write_sql():
    engine = create_engine("sqlite://")
    p0.install_read_only_guard(engine)
    with engine.connect() as conn:
        with pytest.raises(p0.ReadOnlyViolation):
            conn.execute(text("INSERT INTO example VALUES (1)"))
    engine.dispose()


def test_public_payload_safety_blocks_private_marker_and_local_path():
    p0.assert_public_payload_safe({"ok": "aggregate only"}, private_markers=["100729533"])
    with pytest.raises(p0.PrivacyBlocked):
        p0.assert_public_payload_safe({"leak": "100729533"}, private_markers=["100729533"])
    with pytest.raises(p0.PrivacyBlocked):
        p0.assert_public_payload_safe({"leak": "C:\\Users\\person\\private.jpg"})


def test_reference_policy_blocks_live_lookup():
    policy = p0.reference_lookup_policy_result()
    assert policy["status"] == "reference_lookup_policy_blocked"
    assert policy["requests_attempted"] == 0
    assert policy["live_reference_lookup_allowed"] is False


def test_image_signature_comparison_accepts_same_image_and_rejects_different_image():
    local = p0.build_image_signature(Image.new("RGB", (80, 60), color=(120, 80, 40)))
    same = p0.build_image_signature(Image.new("RGB", (80, 60), color=(122, 82, 42)))
    different = p0.build_image_signature(Image.new("RGB", (60, 120), color=(250, 250, 250)))

    accepted = p0.compare_image_signatures(local, same)
    rejected = p0.compare_image_signatures(local, different)

    assert accepted["auto_verification_status"] == "auto_verified_high_confidence"
    assert rejected["auto_verification_status"] == "auto_rejected_mismatch"


def test_public_summary_excludes_exact_pixiv_ids(db):
    _media(db, 1, filename="100729533_p0.jpg")
    extraction_summary, private = p0.audit_pixiv_source_priors(db, approved_ids=[])
    sample_summary, selected = p0.select_feasibility_sample(private, max_items=1)
    lookup_policy = p0.reference_lookup_policy_result()
    verification_summary = p0.build_public_verification_summary(len(selected), lookup_policy)
    summary = p0.build_public_summary(
        generated_at="2026-05-31T00:00:00+00:00",
        identity={"db_identity_result": "development_blombooru_confirmed"},
        extraction_summary=extraction_summary,
        sample_summary=sample_summary,
        lookup_policy=lookup_policy,
        verification_summary=verification_summary,
    )

    p0.assert_public_payload_safe(summary, private_markers=private["distinct_pixiv_work_ids"])
    assert "100729533" not in json.dumps(summary, ensure_ascii=False)
