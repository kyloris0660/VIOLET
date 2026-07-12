"""Freshness guard for the post-PR #135 SCV2-ML1 handoff and roadmap state."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MERGED_R2R_SHA = "5bbbb8ff13b140ea77a839757603714bfdd87181"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_current_handoff_is_post_pr135_and_points_to_ml1() -> None:
    text = _read("docs/current-handoff.md")
    summary = json.loads(_read("docs/reports/phase-4.5-scv2-r2r-autonomous-recall-search-closure-summary.json"))

    assert "PR #135" in text
    assert MERGED_R2R_SHA in text
    assert "SCV2-ML1: Multilingual Alias and Source-Metadata Closure" in text
    assert summary["environment_isolation"]["working_db"] in text
    assert "3319 = 1522 must_link + 1791 cannot_link + 6 deferred_nonblocking" in text
    assert "Search-result union is not identity union" in text
    assert "metadata acquisition" in text and "remain unauthorized" in text


def test_roadmap_supersedes_sr1_and_keeps_ml1_boundary() -> None:
    roadmap = _read("docs/roadmap/current-mainline-roadmap.md")
    project = _read("docs/project-roadmap.md")

    assert MERGED_R2R_SHA in roadmap
    assert "former `SCV2-SR1" in roadmap
    assert "superseded" in roadmap
    assert "SCV2-ML1: Multilingual Alias and Source-Metadata Closure" in roadmap
    assert "media-level AND intersection" in roadmap
    assert "SCV2-ML1" in project
    assert "PX1-B broad acquisition" in roadmap
    assert "Entity bridge" in roadmap
