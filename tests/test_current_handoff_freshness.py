from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from scripts import check_documentation_state as documentation_state


ROOT = Path(__file__).resolve().parents[1]


def _state() -> dict[str, object]:
    return json.loads(
        (ROOT / "docs" / "state" / "current-phase.json").read_text(
            encoding="utf-8"
        )
    )


def _copy_docs_root(tmp_path: Path) -> Path:
    copied = tmp_path / "repo"
    shutil.copytree(ROOT / "docs", copied / "docs")
    (copied / "AGENTS.md").write_text(
        (ROOT / "AGENTS.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return copied


def test_current_phase_schema_and_fl1_i1_boundary_are_consistent() -> None:
    state = _state()

    documentation_state.validate_state(state)
    assert state["schema_version"] == "violet.current-phase.v2"
    assert state["phase_id"] == "SCV2-FL1-I1"
    assert state["branch"] == documentation_state.FL1_I1_BRANCH
    assert state["accepted_mainline_base"] == documentation_state.FL1_I1_ACCEPTED_MAIN
    assert state["current_status"] in {
        "fl1_i1_read_only_inventory_implementation_in_progress",
        "fl1_i1_synthetic_implementation_ready_for_owner_audit",
        "fl1_i1_first_review_bounded_remediation_in_progress",
        "fl1_i1_bounded_remediation_ready_for_owner_audit",
    }
    assert state["target_met"] is False
    assert state["safe_to_merge"] is False
    assert state["route_approved"] is False
    assert state["next_phase_started"] is True
    boundary = state["planning_boundary"]
    assert boundary["implementation_authorized"] is True
    assert boundary["synthetic_ephemeral_test_fixture_authorized"] is True
    assert boundary["real_inventory_started"] is False
    assert boundary["real_source_inventory_authorized"] is False
    assert boundary["database_access_authorized"] is False
    assert boundary["app_storage_write_authorized"] is False
    assert boundary["provider_or_llm_authorized"] is False
    assert boundary["production_authorized"] is False
    audit_ready = state["current_status"] in {
        "fl1_i1_synthetic_implementation_ready_for_owner_audit",
        "fl1_i1_bounded_remediation_ready_for_owner_audit",
    }
    assert boundary["implementation_completed"] is audit_ready
    assert state["protected_evidence"][
        "fl1_i1_implementation_evidence_frozen"
    ] is audit_ready


def test_prior_pr143_acceptance_and_five_adjudications_are_exact() -> None:
    state = _state()
    prior = state["prior_phase_acceptance"]
    upstream = state["upstream_pr_state"]

    assert prior == {
        "phase_id": "SCV2-FL1-P1-R1",
        "status": "owner_accepted_and_merge_commit_merged",
        "merge_commit": documentation_state.FL1_I1_ACCEPTED_MAIN,
        "final_pr_head": documentation_state.FL1_I1_PR143_HEAD,
        "implementation_evidence_head": documentation_state.FL1_I1_P1_R1_EVIDENCE,
        "final_review_id": 4890771735,
        "final_review_submitted_at": "2026-08-09T07:24:25Z",
        "owner_adjudication_count": 5,
        "owner_decision_source": "direct_human_github_pr_body_and_merge_commit",
        "automated_positive_authority": False,
    }
    assert upstream["owner_closeout_active_unresolved_thread_count"] == 10
    assert upstream["owner_closeout_outdated_unresolved_thread_count"] == 7
    decisions = "\n".join(
        decision["decision"] for decision in state["owner_decisions"]
    )
    for concept in (
        "protected roots",
        "merge-commit mitigation",
        "restart invocation provenance",
        "runtime Git HEAD",
        "validation receipts",
    ):
        assert concept.casefold() in decisions.casefold()


def test_pr144_single_bounded_remediation_authority_is_exact() -> None:
    state = _state()
    protected = state["protected_evidence"]
    review = next(
        decision
        for decision in state["owner_decisions"]
        if decision["id"] == "owner_authorized_pr144_first_review_bounded_remediation_20260810"
    )
    assert state["current_status"] in {
        "fl1_i1_first_review_bounded_remediation_in_progress",
        "fl1_i1_bounded_remediation_ready_for_owner_audit",
    }
    assert state["manual_acceptance_status"] == "pending_i1_bounded_remediation_owner_audit"
    assert protected["bounded_remediation_round"] == "1_of_1"
    assert review["review_id"] == 4891695875
    assert review["reviewed_head"] == "b65c7b84adfe45b92f85dfb72d60920bd1fb0ad3"
    assert review["finding_count"] == 18
    assert review["p1_count"] == 15
    assert review["p2_count"] == 3
    assert len(review["finding_adjudications"]) == 18
    assert all(
        finding["decision"] == "must_fix_current_i1"
        for finding in review["finding_adjudications"]
    )


def test_remote_sync_policy_is_canonical_and_self_heal_is_not_contract_proof() -> None:
    state = _state()
    protected = state["protected_evidence"]
    assert protected["preflight_remote_sync"] == "self_healed_by_fast_forward"
    assert protected["preflight_remote_sync_is_contract_proof"] is False

    for relative in (
        "AGENTS.md",
        "docs/development/agent-runbook.md",
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/project-roadmap.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "fast-forward" in text
        assert "local-only" in text
        assert "reset" in text
        assert "untracked" in text


def test_pr142_archaeology_matrix_is_bounded_and_rejects_known_gaps() -> None:
    plan = (
        ROOT
        / "docs"
        / "plans"
        / "phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md"
    ).read_text(encoding="utf-8")
    assert "PR #142 bounded carry-forward matrix" in plan
    for concept in (
        "Deterministic tree traversal",
        "Caller-supplied actual Git HEAD or Python executable",
        "Caller-supplied `forbidden_roots` completeness",
        "Synthetic disposition override as Cloud Files proof",
        "Copied before/after snapshot as restart proof",
        "In-memory manifest and caller test booleans",
    ):
        assert concept in plan
    assert "No PR #142 commit" in plan
    assert "cherry-picked" in plan


def test_handoff_is_exact_generated_projection() -> None:
    state = _state()
    handoff = (ROOT / "docs" / "current-handoff.md").read_text(encoding="utf-8")
    assert handoff == documentation_state.render_handoff(state)
    assert 40 <= len(handoff.splitlines()) <= 65
    assert "SCV2-FL1-I1" in handoff
    assert "real source inventory is not authorized or started" in handoff
    assert "self_healed_by_fast_forward" in handoff


def test_doc_gov_split_and_active_markers_are_consistent() -> None:
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
    documentation_state.validate_roadmaps(_state())


def test_conflicting_current_roadmap_phase_fails_closed(tmp_path: Path) -> None:
    copied_root = _copy_docs_root(tmp_path)
    roadmap = copied_root / "docs" / "project-roadmap.md"
    roadmap.write_text(
        roadmap.read_text(encoding="utf-8").replace(
            "<!-- CURRENT_PHASE: SCV2-FL1-I1 -->",
            "<!-- CURRENT_PHASE: SCV2-FL1-P1-R1 -->",
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="current_phase_conflict",
    ):
        documentation_state.validate_roadmaps(_state(), root=copied_root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("draft", False),
        ("current_status", "fl1_p1_r1_implementation_ready_for_owner_audit"),
        ("target_met", True),
        ("safe_to_merge", True),
        ("route_approved", True),
        ("manual_acceptance_status", "owner_accepted"),
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
        ("implementation_authorized", False),
        ("owner_acceptance_valid", True),
        ("merge_authorized", True),
        ("fl1_i1_route_authorized", False),
        ("fl1_i1_implementation_started", False),
        ("real_inventory_started", True),
        ("real_source_inventory_authorized", True),
        ("source_root_access_authorized", True),
        ("database_access_authorized", True),
        ("app_storage_write_authorized", True),
        ("provider_or_llm_authorized", True),
        ("media_authorized", True),
        ("production_authorized", True),
        ("projected_external_cost_usd", 0.01),
    ],
)
def test_fl1_i1_execution_boundary_fails_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state["planning_boundary"][field] = value
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i1_boundary_invalid",
    ):
        documentation_state.validate_state(state)


def test_implementation_completed_must_track_current_status() -> None:
    state = copy.deepcopy(_state())
    current = state["planning_boundary"]["implementation_completed"]
    state["planning_boundary"]["implementation_completed"] = not current
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i1_boundary_invalid",
    ):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    "field",
    [
        "real_source_inventory_operation_count",
        "existing_database_read_operation_count",
        "existing_database_write_operation_count",
        "app_storage_write_operation_count",
        "import_operation_count",
        "provider_operation_count",
        "llm_operation_count",
        "media_or_thumbnail_operation_count",
        "network_operation_count",
        "stable_replay_operation_count",
        "production_operation_count",
    ],
)
def test_fl1_i1_public_non_action_counts_cannot_claim_activity(field: str) -> None:
    state = copy.deepcopy(_state())
    state["protected_evidence"][field] = 1
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i1_protected_evidence_invalid",
    ):
        documentation_state.validate_state(state)


def test_remote_sync_classification_cannot_be_promoted_to_contract_proof() -> None:
    state = copy.deepcopy(_state())
    state["protected_evidence"]["preflight_remote_sync_is_contract_proof"] = True
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i1_protected_evidence_invalid",
    ):
        documentation_state.validate_state(state)


def test_in_progress_evidence_is_not_falsely_frozen() -> None:
    state = copy.deepcopy(_state())
    state["current_status"] = "fl1_i1_read_only_inventory_implementation_in_progress"
    state["manual_acceptance_status"] = "pending_i1_implementation_owner_audit"
    state["active_blocker"]["code"] = "fl1_i1_implementation_in_progress"
    state["planning_boundary"]["implementation_completed"] = False
    state["protected_evidence"]["fl1_i1_implementation_evidence_frozen"] = False
    state["implementation_evidence_head"] = "f" * 40
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i1_in_progress_evidence_must_equal_base",
    ):
        documentation_state.validate_state(state)


def test_audit_ready_state_requires_distinct_frozen_evidence() -> None:
    state = copy.deepcopy(_state())
    state["current_status"] = "fl1_i1_synthetic_implementation_ready_for_owner_audit"
    state["manual_acceptance_status"] = (
        "pending_i1_synthetic_implementation_owner_audit"
    )
    state["active_blocker"]["code"] = (
        "pending_i1_synthetic_implementation_owner_audit_and_real_source_scope"
    )
    state["planning_boundary"]["implementation_completed"] = True
    state["protected_evidence"]["fl1_i1_implementation_evidence_frozen"] = True
    state["implementation_evidence_head"] = documentation_state.FL1_I1_ACCEPTED_MAIN
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i1_implementation_evidence_not_frozen",
    ):
        documentation_state.validate_state(state)

    state["implementation_evidence_head"] = "f" * 40
    documentation_state.validate_state(state)


def test_public_state_rejects_private_path() -> None:
    state = copy.deepcopy(_state())
    state["owner_decisions"].append(
        {"id": "unsafe", "decision": "private C:\\Users\\person\\source"}
    )
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="public_state_redaction_failure",
    ):
        documentation_state.validate_state(state)


def test_required_use_before_gates_are_present() -> None:
    debts = {entry["id"]: entry for entry in _state()["deferred_debt"]}
    assert set(debts) == {
        "REAL_OPERATION_GATEWAY_GATE",
        "VALIDATION_RECEIPT_GATE",
        "OWNER_AUTHORITY_GATE",
        "POSIX_LEDGER_DURABILITY_GATE",
        "STABLE_REPLAY_GATE",
    }
    assert "real source" in debts["REAL_OPERATION_GATEWAY_GATE"]["due_before"]
    assert "local_operator_receipt" in " ".join(
        debts["VALIDATION_RECEIPT_GATE"]["requirements"]
    )
