"""Semantic guards for the machine-readable current-phase documentation state."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from scripts import check_documentation_state as documentation_state


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "docs" / "state" / "current-phase.json"
HANDOFF_PATH = ROOT / "docs" / "current-handoff.md"


def _state() -> dict[str, object]:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _copy_docs_root(tmp_path: Path) -> Path:
    copied_root = tmp_path / "repo"
    shutil.copytree(ROOT / "docs", copied_root / "docs")
    return copied_root


def test_current_phase_schema_and_fl1_i1_boundary_are_consistent() -> None:
    state = _state()

    documentation_state.validate_state(state)
    assert state["schema_version"] == "violet.current-phase.v2"
    assert state["phase_id"] == "SCV2-FL1-I1"
    assert state["repository"] == "kyloris0660/VIOLET"
    assert state["draft"] is True
    assert state["pr_number"] is None or state["pr_number"] > 0
    assert state["current_status"] == documentation_state.FL1_STATUS
    assert state["target_met"] is False
    assert state["safe_to_merge"] is False
    assert state["route_approved"] is False
    assert state["route_scope"] == documentation_state.FL1_ROUTE_SCOPE
    assert state["planning_approved"] is True
    assert state["approved_planning_head"] == documentation_state.FL1_APPROVED_PLANNING_HEAD
    assert state["manual_acceptance_status"] == documentation_state.FL1_MANUAL_STATUS
    assert state["next_phase_started"] is True
    assert state["active_blocker"]["code"] == documentation_state.FL1_BLOCKER
    assert state["planning_boundary"] == {
        "planning_only": False,
        "implementation_authorized": True,
        "implementation_scope": documentation_state.FL1_ROUTE_SCOPE,
        "completed_implementation_scope": documentation_state.FL1_COMPLETED_SCOPE,
        "implementation_completed": True,
        "owner_audit_pending": True,
        "data_execution_authorized": False,
        "production_authorized": False,
        "database_access_authorized": False,
        "database_data_execution_authorized": False,
        "source_root_access_authorized": False,
        "synthetic_source_fixture_access_authorized": True,
        "real_source_inventory_authorized": False,
        "provider_or_llm_authorized": False,
        "provider_authorized": False,
        "llm_authorized": False,
        "media_or_thumbnail_download_authorized": False,
        "media_authorized": False,
        "classification_or_tagging_execution_authorized": False,
        "stable_replay_authorized": False,
        "synthetic_ephemeral_test_fixture_authorized": True,
        "projected_external_cost_usd": 0,
    }


def test_prior_fl1_p1_acceptance_is_bound_to_exact_merge_and_tree() -> None:
    prior = _state()["prior_phase_acceptance"]

    assert prior["phase_id"] == "SCV2-FL1-P1"
    assert prior["merge_commit"] == documentation_state.FL1_P1_MERGE_COMMIT
    assert prior["accepted_tree"] == documentation_state.FL1_P1_ACCEPTED_TREE
    assert prior["final_pr_head"] == documentation_state.FL1_P1_FINAL_HEAD
    assert prior["implementation_evidence_head"] == documentation_state.FL1_P1_FINAL_HEAD
    assert prior["reviewed_and_validated_head"] == documentation_state.FL1_P1_FINAL_HEAD
    assert prior["reviewed_and_validated_tree"] == documentation_state.FL1_P1_ACCEPTED_TREE
    assert prior["merge_tree_matches_final_pr_tree"] is True
    assert prior["review_observation_seconds_at_least"] >= 300
    assert prior["premerge_review_count"] == 0
    assert prior["premerge_unresolved_thread_count"] == 0
    assert prior["premerge_failing_or_pending_check_count"] == 0
    assert prior["terminal_review_count"] == 1
    assert prior["terminal_unresolved_thread_count"] == 8
    assert prior["terminal_failing_or_pending_check_count"] == 0
    assert (
        prior["late_review_submitted_at"]
        == documentation_state.FL1_P1_LATE_REVIEW_SUBMITTED_AT
    )
    assert prior["late_review_arrived_after_merge"] is True
    assert (
        prior["late_review_carry_forward_head"]
        == documentation_state.FL1_I1_IMPLEMENTATION_HEAD
    )
    assert prior["late_review_threads_replied_or_resolved"] is False


def test_handoff_is_exact_generated_projection_and_stays_small() -> None:
    state = _state()
    rendered = documentation_state.render_handoff(state)

    assert HANDOFF_PATH.read_text(encoding="utf-8") == rendered
    assert 40 <= len(rendered.splitlines()) <= 60
    assert "this file is not the fact source" in rendered
    assert "SCV2-FL1" in rendered
    assert state["current_status"] in rendered
    assert state["next_required_checkpoint"] in rendered
    assert "implementation/data/production authorization: `true/false/false`" in rendered
    assert all(operation in rendered for operation in state["authorized_operations"])


def test_handoff_writer_atomically_renders_current_state(tmp_path: Path) -> None:
    target = tmp_path / "current-handoff.md"
    state = _state()

    documentation_state.write_handoff(state, path=target)

    assert target.read_text(encoding="utf-8") == documentation_state.render_handoff(state)
    assert not target.with_suffix(".md.tmp").exists()


def test_current_phase_durable_links_exist_and_are_public_docs() -> None:
    state = _state()
    documentation_state.check_documentation_state()

    paths = {link["path"] for link in state["durable_links"]}
    assert "docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md" in paths
    assert "docs/governance/doc-gov-02-closeout.md" in paths
    assert "docs/roadmap/archive/project-roadmap-through-scv2-sv1b.md" in paths
    assert "docs/development/agent-runbook.md" in paths
    assert all((ROOT / path).is_file() for path in paths)


def test_each_active_roadmap_declares_exactly_one_current_phase() -> None:
    state = _state()
    marker = f"<!-- CURRENT_PHASE: {state['phase_id']} -->"

    for path in (
        ROOT / "docs" / "roadmap" / "current-mainline-roadmap.md",
        ROOT / "docs" / "project-roadmap.md",
        ROOT / "docs" / "phase-contracts.md",
    ):
        assert path.read_text(encoding="utf-8").count(marker) == 1
    documentation_state.validate_roadmaps(state)


def test_doc_gov_02_separates_active_entrypoints_from_history() -> None:
    project = (ROOT / "docs" / "project-roadmap.md").read_text(encoding="utf-8")
    archive = (
        ROOT / "docs" / "roadmap" / "archive" / "project-roadmap-through-scv2-sv1b.md"
    ).read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "development" / "agent-runbook.md").read_text(
        encoding="utf-8"
    )

    assert len(project.splitlines()) < 150
    assert len(agents.splitlines()) < 150
    assert len(archive.splitlines()) > 1000
    assert len(runbook.splitlines()) > 500
    assert "CURRENT_PHASE: SCV2-FL1-I1" in project
    assert "CURRENT_PHASE: SCV2-SV1B" in archive
    assert "current next technical phase" not in project.casefold()
    assert "docs/development/agent-runbook.md" in agents


def test_fl1_plan_covers_required_dev_test_contract_and_stop_points() -> None:
    plan = (
        ROOT
        / "docs"
        / "plans"
        / "phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md"
    ).read_text(encoding="utf-8")

    for heading in (
        "## 2. Goals",
        "## 3. Non-Goals",
        "## 4. Isolation Design",
        "## 5. Full-Library Inventory Denominator",
        "## 6. Duplicate, Unsupported, And Cloud-Recall Policy",
        "## 7. Batch Import And Recovery",
        "## 8. Classification And Local AI Tagging",
        "## 9. SV1-A / SV1-B Evidence Reuse",
        "## 10. Metadata, Localization, And Graph Extension Route",
        "## 11. Provider And External Request Boundary",
        "## 12. Mutation Allowlist And Forbidden Tables",
        "## 13. Failure Budget And Fail-Closed Conditions",
        "## 14. Manual Acceptance And Stop Points",
        "## 15. Executable Contract And Tests",
        "## 16. Proposed PR Split",
        "## 18. Approval Boundary",
    ):
        assert heading in plan
    assert "VIOLET_ENV=test" in plan
    assert "projected cost: zero" in plan
    assert "waiver_inherited_by_fl1" not in plan
    assert "No implementation PR may silently include execution authority" in plan
    assert "production" in plan.casefold()


def test_state_change_without_handoff_regeneration_fails_closed(tmp_path: Path) -> None:
    copied_root = _copy_docs_root(tmp_path)
    state_path = copied_root / "docs" / "state" / "current-phase.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["updated_at"] = "2099-01-01T00:00:00+00:00"
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(documentation_state.DocumentationStateError, match="generated_handoff_drift"):
        documentation_state.check_documentation_state(root=copied_root)


def test_conflicting_current_roadmap_phase_fails_closed(tmp_path: Path) -> None:
    copied_root = _copy_docs_root(tmp_path)
    roadmap = copied_root / "docs" / "project-roadmap.md"
    roadmap.write_text(
        roadmap.read_text(encoding="utf-8").replace(
            "<!-- CURRENT_PHASE: SCV2-FL1-I1 -->",
            "<!-- CURRENT_PHASE: SCV2-FL1 -->",
        ),
        encoding="utf-8",
    )

    with pytest.raises(documentation_state.DocumentationStateError, match="current_phase_conflict"):
        documentation_state.check_documentation_state(root=copied_root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("draft", False),
        ("current_status", "fl1_p1_owner_accepted_for_merge"),
        ("target_met", True),
        ("safe_to_merge", True),
        ("route_approved", True),
        ("manual_acceptance_status", "owner_accepted_fl1_p1_foundation"),
        ("next_phase_started", False),
    ],
)
def test_fl1_i1_status_conflicts_fail_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state[field] = value

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i1_status_fields_conflict",
    ):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("planning_only", True),
        ("implementation_authorized", False),
        ("completed_implementation_scope", "unknown"),
        ("owner_audit_pending", False),
        ("data_execution_authorized", True),
        ("production_authorized", True),
        ("database_access_authorized", True),
        ("source_root_access_authorized", True),
        ("synthetic_source_fixture_access_authorized", False),
        ("provider_or_llm_authorized", True),
        ("media_or_thumbnail_download_authorized", True),
        ("projected_external_cost_usd", 0.01),
    ],
)
def test_fl1_execution_boundary_fails_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state["planning_boundary"][field] = value

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i1_boundary_invalid",
    ):
        documentation_state.validate_state(state)


def test_fl1_authorized_operations_cannot_grant_execution() -> None:
    state = copy.deepcopy(_state())
    state["authorized_operations"].append("database creation for FL1")

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i1_authorizes_data_execution",
    ):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    "field",
    [
        "database_operation_count",
        "existing_database_read_operation_count",
        "existing_database_write_operation_count",
        "app_storage_write_operation_count",
        "real_source_inventory_operation_count",
        "provider_operation_count",
        "llm_operation_count",
        "media_or_thumbnail_operation_count",
        "stable_replay_operation_count",
    ],
)
def test_fl1_operation_counts_must_stay_zero(field: str) -> None:
    state = copy.deepcopy(_state())
    state["protected_evidence"][field] = 1

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_operation_counts_nonzero",
    ):
        documentation_state.validate_state(state)


def test_prior_fl1_p1_merge_identity_cannot_drift() -> None:
    state = copy.deepcopy(_state())
    state["prior_phase_acceptance"]["accepted_tree"] = "f" * 40

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_prior_phase_acceptance_invalid",
    ):
        documentation_state.validate_state(state)


@pytest.mark.parametrize("field", ["accepted_mainline_base", "implementation_evidence_head"])
def test_current_phase_git_heads_must_be_ancestors(field: str) -> None:
    state = copy.deepcopy(_state())
    state[field] = "f" * 40

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match=f"{field}_not_ancestor_of_head",
    ):
        documentation_state.validate_git_ancestry(state)
