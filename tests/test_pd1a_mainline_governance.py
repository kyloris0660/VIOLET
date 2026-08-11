"""Focused tests for current mainline roadmap persistence."""

from __future__ import annotations

import json
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


def test_current_mainline_roadmap_persists_accepted_sequence_and_fl1_boundary() -> None:
    text = _read("docs/roadmap/current-mainline-roadmap.md")

    assert "PR #135" in text
    assert "PR #133" in text
    assert "9ce1128be643c0eaa998ccdff8890d76196ce7db" in text
    _assert_in_order(
        text,
        [
            "1. R1R merged in PR #132",
            "2. SCV2-A1R merged in PR #133",
            "3. SCV2-R2 merged in PR #134",
            "4. SCV2-R2R merged in PR #135",
            "5. SCV2-ML1 merged in PR #136",
            "6. SCV2-ML2 merged in PR #137",
            "7. SCV2-SV1-A merged in PR #138",
            "8. SCV2-SV1B merged in PR #139",
            "9. SCV2-FL1 planning merged in PR #140",
            "10. SCV2-FL1-P1 physically merged in PR #141",
            "11. SCV2-FL1-P1-R1 was owner-accepted",
        ],
    )
    _assert_split_s2g_not_active(text)
    assert "SCV2-FL1-I1: Read-only Inventory" in text
    state = json.loads(_read("docs/state/current-phase.json"))
    assert state["current_status"] in text
    assert "route_approved=false" in text
    assert "production" in text.casefold()
    assert "Stop Boundary" in text


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

    assert "roadmap/current-mainline-roadmap.md" in text
    assert "SCV2-FL1" in text
    assert "Draft PR" in text
    for forbidden_term in ("provider", "Pixiv", "gallery-dl", "LLM"):
        assert forbidden_term in text
    assert "Current phase | `S3A-M2-R" not in text
    _assert_split_s2g_not_active(text)
