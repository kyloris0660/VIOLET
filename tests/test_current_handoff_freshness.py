"""Freshness guards for the post-PR #136 SCV2-ML2 canonical state."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MERGED_R2R_SHA = "5bbbb8ff13b140ea77a839757603714bfdd87181"
MERGED_ML1_SHA = "f6cae3483f4cf75974746a4cc82222f28e399b96"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_current_handoff_is_post_pr136_and_points_to_ml2() -> None:
    text = _read("docs/current-handoff.md")
    summary = json.loads(
        _read("docs/reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure-summary.json")
    )

    assert "PR #136" in text
    assert MERGED_ML1_SHA in text
    assert "SCV2-ML2: Multilingual Identity Candidate Closure" in text
    assert summary["environment_isolation"]["working_database"] in text
    assert "606 = 12 already materialized + 594 new + 0 cannot-link + 0 deferred" in text
    assert "Search-result union is not identity union" in text
    assert "route_approved=false" in text
    assert "provider" in text and "does not authorize" in text


def test_roadmap_supersedes_sr1_and_keeps_ml2_boundary() -> None:
    roadmap = _read("docs/roadmap/current-mainline-roadmap.md")
    project = _read("docs/project-roadmap.md")

    assert MERGED_R2R_SHA in roadmap
    assert MERGED_ML1_SHA in roadmap
    assert "former `SCV2-SR1" in roadmap
    assert "superseded" in roadmap
    assert "SCV2-ML1: Multilingual Alias and Source-Metadata Closure" in roadmap
    assert "SCV2-ML2: Multilingual Identity Candidate Closure" in roadmap
    assert "media-level AND intersection" in roadmap
    assert "SCV2-ML2" in project
    assert "PX1-B broad acquisition" in roadmap
    assert "Entity bridge" in roadmap


def test_ml2_closeout_handoff_roadmaps_report_and_contract_are_consistent() -> None:
    handoff = _read("docs/current-handoff.md")
    roadmap = _read("docs/roadmap/current-mainline-roadmap.md")
    project = _read("docs/project-roadmap.md")
    policy = _read("docs/source-concept-autonomous-resolution-policy.md")
    contracts = _read("docs/phase-contracts.md")
    report = _read("docs/reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure.md")
    summary = json.loads(
        _read("docs/reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure-summary.json")
    )

    for text in (handoff, roadmap, project, report):
        assert "target_met_multilingual_identity_candidate_closure" in text
        assert "safe_to_merge=true" in text or "safe_to_merge: `True`" in text
        assert "route_approved=false" in text or "route_approved: `False`" in text

    canonical = "\n".join((handoff, roadmap, project, policy, contracts, report))
    assert "ml2_multilingual_identity_candidate_closure_contract_v1" in canonical
    assert "stable_creator_id" in canonical
    assert "1214" in canonical
    assert "safe_to_merge=false" not in canonical

    contract = summary["pipeline_contract"]
    accounting = summary["family_accounting"]
    route = summary["route_decision"]
    assert contract["status"] == "target_met_multilingual_identity_candidate_closure"
    assert contract["target_met"] is True
    assert contract["route_approved"] is False
    assert contract["safe_to_merge"] is True
    assert contract["active_blockers"] == []
    assert accounting["identity_eligible_family_count"] == 606
    assert accounting["already_materialized_family_count"] == 12
    assert accounting["newly_materialized_family_count"] == 594
    assert accounting["accounting_equality_passed"] is True
    assert route["route_approved"] is False
    assert route["next_phase_started"] is False
