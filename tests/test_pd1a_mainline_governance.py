"""Focused tests for PD1-A roadmap persistence."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_in_order(text: str, needles: list[str]) -> None:
    cursor = -1
    for needle in needles:
        position = text.find(needle)
        assert position > cursor, f"{needle!r} is missing or out of order"
        cursor = position


def test_current_mainline_roadmap_persists_accepted_sequence() -> None:
    text = _read("docs/roadmap/current-mainline-roadmap.md")

    assert "PR #113" in text
    assert "17ec3326bc00e4d025bbe4297fad1157b9cda2ff" in text
    _assert_in_order(
        text,
        [
            "PD1-A",
            "S2G-1",
            "S2G-2/3",
            "R1R",
            "Pixiv/source metadata strategy polish",
            "S3A",
            "S3B",
            "S2F0",
        ],
    )
    assert "S2G-1 GPU AI tagging capability probe and benchmark" in text
    assert "Production / development separation" in text
    assert "What Is Intentionally Not Next" in text


def test_post_s2_roadmap_matches_current_mainline_sequence() -> None:
    text = _read("docs/roadmap/post-s2-production-roadmap.md")

    _assert_in_order(
        text,
        [
            "PD1-A",
            "S2G-1",
            "S2G-2/3",
            "R1R",
            "Pixiv/source metadata strategy polish",
            "S3A",
            "S3B",
            "S2F0",
        ],
    )
    assert "Recommended immediate next phase after PD1-A: `S2G-1`" in text
    assert "Do not introduce new providers before Pixiv/source metadata is settled" in text


def test_handoff_points_to_current_mainline_roadmap() -> None:
    text = _read("docs/current-handoff.md")

    assert "docs/roadmap/current-mainline-roadmap.md" in text
    assert "PD1-A" in text
    assert "S2G-1 GPU AI tagging capability probe and benchmark" in text
