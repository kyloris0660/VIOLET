"""Focused tests for current mainline roadmap persistence."""

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


def test_current_mainline_roadmap_persists_post_pr133_r2_sequence() -> None:
    text = _read("docs/roadmap/current-mainline-roadmap.md")

    assert "PR #129" in text
    assert "PR #133" in text
    assert "4a44d5809c9ec567bf59474cc3e20df62a0e97de" in text
    assert "operator-ready" in text
    assert "Issue #130" in text
    _assert_in_order(
        text,
        [
            "1. `S2G-M1",
            "2. `S3A-M1",
            "3. `S3A-M2",
            "4. `S3A-M2-R",
            "5. `R1R",
            "6. `SCV2-A1R",
            "7. `SCV2-R2",
            "8. `Pixiv/source metadata strategy polish`",
            "9. `S3B",
            "10. `S2F0",
        ],
    )
    _assert_split_s2g_not_active(text)
    assert "R1R evidence DB is read-only input" in text
    assert "Production / Development Separation" in text
    assert "What Is Intentionally Not Next" in text


def test_post_s2_roadmap_matches_current_mainline_sequence() -> None:
    text = _read("docs/roadmap/post-s2-production-roadmap.md")

    assert "PR #129" in text
    assert "Issue #130" in text
    _assert_in_order(
        text,
        [
            "1. `S2G-M1",
            "2. `S3A-M1",
            "3. `S3A-M2",
            "4. `S3A-M2-R",
            "5. `R1R",
            "6. `A1R",
            "7. `Pixiv/source metadata strategy polish`",
            "8. `S3B",
            "9. `S2F0",
        ],
    )
    _assert_split_s2g_not_active(text)
    assert "R1R must use dev/test/restored-snapshot DB only" in text
    assert "Do not introduce new providers before Pixiv/source metadata is settled" not in text
    assert "Make the Pixiv/source" in text
    assert "metadata route reliable before adding providers" in text


def test_handoff_points_to_current_mainline_roadmap() -> None:
    text = _read("docs/current-handoff.md")

    assert "docs/roadmap/current-mainline-roadmap.md" in text
    assert "SCV2-R2: Constraint-Aware SourceConcept Graph Remediation" in text
    assert "target_met_constraint_aware_r2" in text
    assert "no downstream route approved" in text
    assert "Current phase | `S3A-M2-R" not in text
    _assert_split_s2g_not_active(text)
