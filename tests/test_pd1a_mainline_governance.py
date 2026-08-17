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

    assert "PRs #132-#139" in text
    _assert_in_order(
        text,
        [
            "1. R1R through SCV2-SV1B merged in PRs #132-#139",
            "2. SCV2-FL1 planning merged in PR #140",
            "3. SCV2-FL1-P1 merged in PR #141",
            "4. SCV2-FL1-P1-R1 was owner-accepted",
            "5. SCV2-FL1-I1 was owner-accepted",
            "6. SCV2-FL1-I2 planning was owner-accepted",
        ],
    )
    _assert_split_s2g_not_active(text)
    assert "SCV2-FL1-I2: Real-source Read-only Inventory Hardening and Canary Readiness" in text
    assert "PR #144 terminal review `4897012517`" in text
    assert "machine_verifiable_ci=false" in text
    state = json.loads(_read("docs/state/current-phase.json"))
    assert state["current_status"] in text
    assert "route_approved=false" in text
    assert "planning_approved=true" in text
    assert "safe_to_merge=false" in text
    assert "production" in text.casefold()
    assert "Stop Boundary" in text
    assert "pending_fl1_i2_bounded_followup_review_and_owner_reaudit" in text
    assert "scv2_fl1_i2_pre_real_hardening_contract_v1" in text
    assert "FL1_I3_REAL_SOURCE_SCOPE_GATE" in text
    assert "acb12c1db258fdef1d4f063b053d422e0d887abf" in text
    assert "data-plane network operations remain zero" in text


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
    assert "PR pending creation" in text or "PR #" in text
    assert "All 17 findings remain historical audit records" in text
    for forbidden_term in ("provider", "Pixiv", "gallery-dl", "LLM"):
        assert forbidden_term in text
    assert "Current phase | `S3A-M2-R" not in text
    _assert_split_s2g_not_active(text)
