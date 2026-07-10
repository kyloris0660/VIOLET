"""Freshness guard for the post-PR #133 SCV2-R2 handoff and roadmap state."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_current_handoff_is_post_pr133_and_points_to_r2() -> None:
    text = _read("docs/current-handoff.md")
    summary = json.loads(
        _read("docs/reports/phase-4.5-scv2-r2-constraint-aware-graph-remediation-summary.json")
    )
    final_working_db = summary["environment_isolation"]["working_db"]

    assert "Last updated for SCV2-R2 after PR #133" in text
    assert "Current phase | `SCV2-R2: Constraint-Aware SourceConcept Graph Remediation`" in text
    assert "4a44d5809c9ec567bf59474cc3e20df62a0e97de" in text
    assert "blombooru_r1r_restored_test_20260618` (preserved)" in text
    assert final_working_db == "blombooru_scv2_r2_review4_test_20260710"
    assert final_working_db in text
    assert "target_met_constraint_aware_r2" in text
    assert "2,284 genuinely new/missing pairs" in text
    assert "blocked_llm_approval_required" in text
    assert "no downstream route approved" in text


def test_roadmap_keeps_r2_after_a1r_and_blocks_route_promotion() -> None:
    roadmap = _read("docs/roadmap/current-mainline-roadmap.md")
    post_s2 = _read("docs/roadmap/post-s2-production-roadmap.md")
    project = _read("docs/project-roadmap.md")

    assert "5. `R1R: Full SourceConcept Pipeline Replay / Remediation`" in roadmap
    assert "6. `SCV2-A1R: Route Audit Rerun After R1R`" in roadmap
    assert "7. `SCV2-R2: Constraint-Aware SourceConcept Graph Remediation`" in roadmap
    assert "R1R evidence DB is read-only input" in roadmap
    assert "A1R: Route Audit Rerun After R1R" in post_s2
    assert "R2, PX1-B, Provider-2, scale-up, Entity bridge, SourceConcept truth promotion" in project
    assert "remain blocked until R1R and A1R produce valid route evidence" in project
    assert "route_approved=true" not in roadmap
    assert "PX1-B approved" not in roadmap
    assert "Provider-2 approved" not in roadmap
    assert "Entity bridge approved" not in roadmap
