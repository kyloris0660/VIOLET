"""Freshness guard for the post-PR #129 handoff and roadmap state."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_current_handoff_is_post_pr129_and_points_to_r1r() -> None:
    text = _read("docs/current-handoff.md")

    assert "Last updated for R1R-P0 after PR #129" in text
    assert "Current phase | `R1R-P0: Current Handoff and Roadmap Slim Refresh`" in text
    assert "Next technical phase | `R1R: Full SourceConcept Pipeline Replay / Remediation`" in text
    assert "S3A-M2-R / PR #129 is merged and closed as operator-ready" in text
    assert "285e76d3eaa76f02acaa9dccf2b7fc91761ca428" in text
    assert "ef9b4447e48221ece00924afed78101640ed56e9" in text
    assert "Issue #130 tracks deferred S3A-M2-R PR-R2/manual-sync hardening debt" in text
    assert "R1R execution must not use or write production DB" in text
    assert "R1R must use dev/test/restored-snapshot DB only" in text
    assert "Current phase | `S3A-M2-R" not in text
    assert "S3A-M2-R: Manual Sync Stabilization" not in text


def test_roadmap_keeps_a1r_after_r1r_and_blocks_route_promotion() -> None:
    roadmap = _read("docs/roadmap/current-mainline-roadmap.md")
    post_s2 = _read("docs/roadmap/post-s2-production-roadmap.md")
    project = _read("docs/project-roadmap.md")

    assert "5. `R1R: Full SourceConcept Pipeline Replay / Remediation`" in roadmap
    assert "6. `A1R: Route Audit Rerun After R1R`" in roadmap
    assert "R1R must use dev/test/restored-snapshot DB only" in roadmap
    assert "A1R: Route Audit Rerun After R1R" in post_s2
    assert "R2, PX1-B, Provider-2, scale-up, Entity bridge, SourceConcept truth promotion" in project
    assert "remain blocked until R1R and A1R produce valid route evidence" in project
    assert "route_approved=true" not in roadmap
    assert "R2 approved" not in roadmap
    assert "PX1-B approved" not in roadmap
    assert "Provider-2 approved" not in roadmap
    assert "Entity bridge approved" not in roadmap
