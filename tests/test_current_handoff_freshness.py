"""Freshness guards for the PR #136 SCV2-ML1 canonical closeout state."""

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


def test_ml1_closeout_handoff_roadmaps_report_and_contract_are_consistent() -> None:
    handoff = _read("docs/current-handoff.md")
    roadmap = _read("docs/roadmap/current-mainline-roadmap.md")
    project = _read("docs/project-roadmap.md")
    policy = _read("docs/pixiv-metadata-ingestion-and-promotion-policy.md")
    contracts = _read("docs/phase-contracts.md")
    report = _read("docs/reports/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure.md")
    wrapper = json.loads(
        _read("docs/reports/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure-summary.json")
    )
    summary = wrapper["evidence_summary"]

    for text in (handoff, roadmap, project, report):
        assert "partial_ml1_pixiv_metadata_foundation_complete" in text
        assert "safe_to_merge=true" in text or "safe_to_merge: `True`" in text
        assert "route_approved=true" in text or "route_approved: `True`" in text

    canonical = "\n".join((handoff, roadmap, project, policy, contracts, report))
    assert "deferred_nonblocking_source_page_mismatch" in canonical
    assert "source_page_mismatch_deferred_nonblocking_v1" in canonical
    assert "SCV2-ML2" in canonical
    assert "No provider call has occurred yet" not in canonical
    assert "safe_to_merge=false" not in canonical
    assert "route_approved=false" not in canonical

    contract = summary["pipeline_contract"]
    accounting = summary["pixiv_accounting"]
    route = summary["route_authorization"]
    assert contract["status"] == "partial_ml1_pixiv_metadata_foundation_complete"
    assert contract["claims"] == {
        "target_met": False,
        "route_approved": True,
        "safe_to_merge": True,
    }
    assert contract["active_blockers"] == []
    assert accounting["candidate_distinct_work_count"] == 2235
    assert accounting["metadata_present_complete_work_count"] == 2155
    assert accounting["terminal_remote_unavailable_work_count"] == 66
    assert accounting["deferred_nonblocking_source_page_mismatch_work_count"] == 14
    assert accounting["work_accounting_equality_holds"] is True
    assert route["route_approved_scope"] == "SCV2-ML2_next_phase_only"
    assert route["production_authorized"] is False
