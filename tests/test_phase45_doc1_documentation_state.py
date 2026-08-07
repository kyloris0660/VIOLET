"""Documentation-state guards for Phase 4.5-DOC1-R1."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ACTIVE_DOCS = [
    ROOT / "README.md",
    ROOT / "docs" / "current-handoff.md",
    ROOT / "docs" / "project-roadmap.md",
    ROOT / "docs" / "test-workflow.md",
]

SUMMARY_JSON = (
    ROOT
    / "docs"
    / "reports"
    / "phase-4.5-doc1-post-sc2-documentation-consolidation-summary.json"
)

STALE_SC2_PHRASES = [
    "Phase 4.5-SC2 is planned",
    "Phase 4.5-SC2 implementation is prepared",
    "after Phase 4.5-SC2 implementation",
    "pending_precommit_validation",
]

REQUIRED_SUMMARY_FIELDS = {
    "phase",
    "title",
    "branch",
    "generated_at",
    "docs_updated",
    "docs_restructured",
    "line_counts_before_after",
    "readme_rewrite",
    "handoff_slimming",
    "roadmap_restructure",
    "test_workflow_restructure",
    "guard_debt_classification",
    "executable_guards_added",
    "deferred_guards",
    "recommended_next_phase",
    "validation",
    "safety",
    "artifact_lifecycle",
}

REQUIRED_GUARD_KEYS = {
    "source_concept_not_entity_truth",
    "no_truth_path_writes_sc1_sc2_read_paths",
    "redaction_local_paths_filenames_secrets",
    "visible_status_gate",
    "search_cache_invalidation",
    "alias_expansion_symmetry",
    "needs_review_explicit_alias_expansion",
    "f6_global_q_chip_behavior",
    "promotion_preview_disabled_noop",
    "sc2_no_provider_llm_source_enrichment",
    "scv1_coverage_inventory_alias_gap_search_symmetry",
    "entity_bridge_preview_confirmation_audit_rollback_write_guards",
    "provider_gallery_dl_llm_broad_run_ledger_budget_gates",
    "phase39_ledger_prerequisite",
    "docs_only_validation_policy",
    "agent_no_merge_no_push_main_policy",
}

ALLOWED_CLASSIFICATIONS = {
    "executable_now",
    "add_in_doc1_r1",
    "must_add_in_scv1",
    "must_add_before_sc3_or_entity_bridge",
    "must_add_before_provider_or_full_library_run",
    "documented_only_acceptable",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_active_docs_do_not_reintroduce_stale_sc2_planning_language() -> None:
    combined = "\n".join(read_text(path) for path in ACTIVE_DOCS)

    for phrase in STALE_SC2_PHRASES:
        assert phrase not in combined

    assert "Phase 4.5-SCV1" in combined
    assert "SourceConcept" in combined


def test_readme_is_public_entrypoint_not_phase_changelog() -> None:
    readme = read_text(ROOT / "README.md")

    for heading in [
        "## Key Features",
        "## Current Status",
        "## Architecture Overview",
        "## Quick Start",
        "## Safety And Privacy Model",
        "## Documentation Map",
    ]:
        assert heading in readme

    assert readme.count("PR #") <= 1
    assert "SourceConcept" in readme
    assert "not Entity truth" in readme
    assert "docs/current-handoff.md" in readme
    assert "docs/project-roadmap.md" in readme
    assert "docs/test-workflow.md" in readme
    assert "docs/manual-validation.md" in readme


def test_current_handoff_is_slim_and_current_route_focused() -> None:
    handoff = read_text(ROOT / "docs" / "current-handoff.md")
    state = json.loads(
        (ROOT / "docs" / "state" / "current-phase.json").read_text(
            encoding="utf-8"
        )
    )
    line_count = len(handoff.splitlines())

    assert 40 <= line_count <= 60
    assert "docs/state/current-phase.json" in handoff
    assert "SCV2-FL1" in handoff
    assert "Draft PR" in handoff
    assert state["planning_boundary"]["planning_only"] is True
    assert state["active_blocker"]["code"] in handoff
    assert "projected external cost: `$0`" in handoff
    assert "provider, Pixiv, gallery-dl, Provider-2, LLM" in handoff
    assert "Phase 4.4-B0" not in handoff
    assert "Phase 4.4-D1G" not in handoff


def test_doc1_summary_has_required_schema_and_guard_classifications() -> None:
    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))

    assert REQUIRED_SUMMARY_FIELDS.issubset(summary)
    assert summary["phase"] == "4.5-DOC1-R1"
    assert summary["recommended_next_phase"]["phase"] == "4.5-SCV1"
    assert summary["safety"]["runtime_ui_behavior_changed"] is False
    assert summary["safety"]["db_write"] is False

    guards = summary["guard_debt_classification"]
    by_key = {item["key"]: item for item in guards}
    assert REQUIRED_GUARD_KEYS.issubset(by_key)

    for item in guards:
        classification = item["classification"]
        assert classification in ALLOWED_CLASSIFICATIONS
        if classification not in {"executable_now", "add_in_doc1_r1"}:
            assert item.get("why_not_now")
            assert item.get("future_phase")
            assert item.get("future_guard_shape")


def test_docs_only_validation_policy_is_explicit() -> None:
    workflow = read_text(ROOT / "docs" / "test-workflow.md")

    assert "Docs-only governance, handoff, roadmap, report, or README updates" in workflow
    assert "No pytest/E2E/browser/server required unless code, tests, runtime, or UI changed." in workflow
    assert "tests/test_phase45_doc1_documentation_state.py" in workflow
