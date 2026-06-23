"""Focused tests for PD1-A-R1 roadmap persistence."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_in_order(text: str, needles: list[str]) -> None:
    cursor = -1
    for needle in needles:
        position = text.find(needle, cursor + 1)
        assert position > cursor, f"{needle!r} is missing or out of order"
        cursor = position


def _assert_split_s2g_not_active(text: str) -> None:
    assert "2. `S2G-1`" not in text
    assert "3. `S2G-2/3`" not in text
    assert "S2G-1 GPU AI tagging capability probe and benchmark" not in text
    assert "Recommended immediate next phase after PD1-A: `S2G-1`" not in text


def test_current_mainline_roadmap_persists_accepted_sequence() -> None:
    text = _read("docs/roadmap/current-mainline-roadmap.md")

    assert "PR #113" in text
    assert "17ec3326bc00e4d025bbe4297fad1157b9cda2ff" in text
    assert "PR #122" in text
    assert "aece424df2814ef0d840f9fe472a9d19478d2020" in text
    _assert_in_order(
        text,
        [
            "PD1-A-R1",
            "S2G: GPU / AI Tagging Execution Foundation",
            "R1R",
            "A1R",
            "Pixiv/source metadata strategy polish",
            "S3A",
            "S3B",
            "S2F0",
        ],
    )
    _assert_split_s2g_not_active(text)
    assert "Single consolidated phase" in text
    assert "Production / Development Separation" in text
    assert "What Is Intentionally Not Next" in text


def test_post_s2_roadmap_matches_current_mainline_sequence() -> None:
    text = _read("docs/roadmap/post-s2-production-roadmap.md")

    _assert_in_order(
        text,
        [
            "PD1-A-R1",
            "S2G: GPU / AI Tagging Execution Foundation",
            "R1R",
            "A1R",
            "Pixiv/source metadata strategy polish",
            "S3A",
            "S3B",
            "S2F0",
        ],
    )
    _assert_split_s2g_not_active(text)
    assert "Do not introduce new providers before Pixiv/source metadata is settled" in text


def test_handoff_points_to_current_mainline_roadmap() -> None:
    text = _read("docs/current-handoff.md")

    assert "docs/roadmap/current-mainline-roadmap.md" in text
    assert "PD1-A-R1" in text
    assert "S2G: GPU / AI Tagging Execution Foundation" in text
    _assert_split_s2g_not_active(text)
