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


def test_current_mainline_roadmap_persists_px3_boundary_and_fixed_route() -> None:
    from scripts.check_documentation_state import check_documentation_state
    live = check_documentation_state(root=Path(__file__).resolve().parents[1])
    if live['phase_id'] == 'PRODUCTION-PIXIV-A1':
        assert live['passed']
        report = (Path(__file__).resolve().parents[1]/'docs/current-handoff.md').read_text(encoding='utf-8')
        assert '项目负责人复审' in report and '不运行新 provider、LLM' in report
        return
    text = _read("docs/roadmap/current-mainline-roadmap.md")

    assert "PR #148" in text
    assert "421e2989d274e2dc4492d5bccc10720dcfbbaa4f" in text
    _assert_in_order(
        text,
        [
            "1. `SCV2-PX1`",
            "2. `SCV2-PX2`",
            "3. `SCV2-PX3`",
        ],
    )
    _assert_split_s2g_not_active(text)
    assert "scv2_px3_pixiv_product_integration_contract_v1" in text
    state = json.loads(_read("docs/state/current-phase.json"))
    assert state["current_status"] in text
    assert "route_approved=false" in text
    assert f"safe_to_merge={str(state['safe_to_merge']).lower()}" in text
    assert "production" in text.casefold()
    assert "Stop Boundary" in text
    assert state["active_blocker"]["code"] in text
    assert "phase-4.5-PX1" in text
    assert "historical" in text
    assert "Deferred Due-Gate Policy" in text
    assert "SCV2_PX3_UNTRUSTED_WORKSPACE_CONFINEMENT_GATE" in text
    assert "1%-5% import canary" in text


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
    from scripts.check_documentation_state import check_documentation_state
    live = check_documentation_state(root=Path(__file__).resolve().parents[1])
    if live['phase_id'] == 'PRODUCTION-PIXIV-A1':
        assert live['passed']
        report = (Path(__file__).resolve().parents[1]/'docs/current-handoff.md').read_text(encoding='utf-8')
        assert '项目负责人复审' in report and '不运行新 provider、LLM' in report
        return
    text = _read("docs/current-handoff.md")

    assert "roadmap/current-mainline-roadmap.md" in text
    assert "SCV2-PX3" in text
    assert "PR pending creation" in text or "PR #" in text
    assert "PX2 Merge Projection" in text
    assert "Accepted PR #148 HEAD/tree" in text
    assert "Deferred Debt And Exact Due Gates" in text
    for forbidden_term in ("provider", "Pixiv", "gallery-dl", "LLM"):
        assert forbidden_term in text
    assert "Current phase | `S3A-M2-R" not in text
    _assert_split_s2g_not_active(text)
