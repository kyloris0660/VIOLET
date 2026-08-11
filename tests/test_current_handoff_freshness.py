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


def test_current_phase_schema_and_i2_planning_boundary_are_exact() -> None:
    state = _state()

    documentation_state.validate_state(state)
    assert state["schema_version"] == "violet.current-phase.v2"
    assert state["phase_id"] == "SCV2-FL1-I2"
    assert state["branch"] == documentation_state.FL1_I2_BRANCH
    assert state["accepted_mainline_base"] == documentation_state.FL1_I2_ACCEPTED_MAIN
    assert state["current_status"] == documentation_state.FL1_I2_STATUS
    assert state["planning_authorized"] is True
    assert state["planning_completed"] is True
    assert state["planning_approved"] is False
    assert state["target_met"] is False
    assert state["safe_to_merge"] is False
    assert state["route_approved"] is False
    boundary = state["planning_boundary"]
    assert boundary["planning_only"] is True
    assert boundary["implementation_authorized"] is False
    assert boundary["implementation_started"] is False
    assert boundary["real_inventory_started"] is False
    assert boundary["real_source_inventory_authorized"] is False
    assert boundary["database_access_authorized"] is False
    assert boundary["app_storage_write_authorized"] is False
    assert boundary["provider_or_llm_authorized"] is False
    assert boundary["production_authorized"] is False


def test_pr144_acceptance_merge_and_terminal_review_are_exact() -> None:
    state = _state()
    prior = state["prior_phase_acceptance"]
    upstream = state["upstream_pr_state"]

    assert state["previous_phase"] == "SCV2-FL1-I1"
    assert state["previous_phase_status"] == "owner_accepted_and_merge_commit_merged"
    assert state["previous_phase_accepted_scope"] == (
        "synthetic_and_new_temporary_fixture_foundation_only"
    )
    assert state["previous_phase_real_inventory_target_met"] is False
    assert prior["merge_commit"] == documentation_state.FL1_I2_ACCEPTED_MAIN
    assert prior["final_head"] == documentation_state.FL1_I2_PREVIOUS_FINAL_HEAD
    assert prior["final_tree"] == documentation_state.FL1_I2_PREVIOUS_FINAL_TREE
    assert prior["implementation_evidence_head"] == documentation_state.FL1_I2_PREVIOUS_EVIDENCE
    assert prior["implementation_evidence_tree"] == documentation_state.FL1_I2_PREVIOUS_EVIDENCE_TREE
    assert upstream["terminal_review_id"] == documentation_state.FL1_I2_TERMINAL_REVIEW_ID
    assert upstream["terminal_review_finding_count"] == 17
    assert upstream["terminal_review_p1_count"] == 13
    assert upstream["terminal_review_p2_count"] == 4
    assert upstream["terminal_review_resolved_count"] == 0
    assert upstream["terminal_review_outdated_count"] == 0
    assert upstream["github_checks"] == 0


def test_live_git_objects_bind_frozen_i1_commit_and_tree_evidence() -> None:
    documentation_state.validate_git_ancestry(_state())


def test_terminal_review_use_before_register_is_complete() -> None:
    findings = _state()["terminal_review_findings"]
    assert [finding["number"] for finding in findings] == list(range(1, 18))
    assert sum(finding["severity"] == "P1" for finding in findings) == 13
    assert sum(finding["severity"] == "P2" for finding in findings) == 4
    classifications = [finding["classification"] for finding in findings]
    assert classifications.count("closed_in_current_governance_pr") == 2
    assert classifications.count("must_close_before_i2_implementation") == 14
    assert classifications.count(
        "claim_boundary_local_evidence_not_tamper_resistant_attestation"
    ) == 1


def test_handoff_is_exact_generated_projection() -> None:
    state = _state()
    handoff = (ROOT / "docs" / "current-handoff.md").read_text(encoding="utf-8")
    assert handoff == documentation_state.render_handoff(state)
    assert 55 <= len(handoff.splitlines()) <= 115
    assert "SCV2-FL1-I2" in handoff
    assert "All 17 findings remain historical audit records" in handoff
    assert "machine_verifiable_ci=false" in handoff
    assert documentation_state.FL1_I2_BLOCKER in handoff


def test_active_markers_and_remote_sync_policy_are_consistent() -> None:
    state = _state()
    documentation_state.validate_roadmaps(state)
    for relative in (
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/project-roadmap.md",
        "docs/phase-contracts.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert text.count("<!-- CURRENT_PHASE: SCV2-FL1-I2 -->") == 1
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


def test_conflicting_current_roadmap_phase_fails_closed(tmp_path: Path) -> None:
    copied_root = _copy_docs_root(tmp_path)
    roadmap = copied_root / "docs" / "project-roadmap.md"
    roadmap.write_text(
        roadmap.read_text(encoding="utf-8").replace(
            "<!-- CURRENT_PHASE: SCV2-FL1-I2 -->",
            "<!-- CURRENT_PHASE: SCV2-FL1-I1 -->",
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
        ("current_status", "fl1_i2_implementation_in_progress"),
        ("planning_approved", True),
        ("target_met", True),
        ("safe_to_merge", True),
        ("route_approved", True),
        ("manual_acceptance_status", "owner_accepted"),
    ],
)
def test_i2_positive_status_claims_fail_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state[field] = value
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_status_fields_conflict",
    ):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    "field",
    [
        "implementation_authorized",
        "implementation_started",
        "owner_acceptance_valid",
        "merge_authorized",
        "real_inventory_started",
        "real_source_inventory_authorized",
        "source_root_access_authorized",
        "database_access_authorized",
        "app_storage_write_authorized",
        "import_authorized",
        "classification_or_tagging_execution_authorized",
        "provider_or_llm_authorized",
        "media_authorized",
        "stable_replay_authorized",
        "production_authorized",
    ],
)
def test_i2_execution_authority_fails_closed(field: str) -> None:
    state = copy.deepcopy(_state())
    state["planning_boundary"][field] = True
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_boundary_invalid",
    ):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("previous_phase_implementation_evidence_head", "f" * 40),
        ("previous_phase_implementation_evidence_tree", "f" * 40),
        ("machine_verifiable_ci", True),
        ("github_checks_observed", 1),
        ("ci_authority", True),
        ("preflight_remote_sync_is_contract_proof", True),
    ],
)
def test_frozen_evidence_and_ci_boundary_fail_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state["protected_evidence"][field] = value
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_protected_evidence_invalid",
    ):
        documentation_state.validate_state(state)


def test_terminal_finding_tamper_fails_closed() -> None:
    state = copy.deepcopy(_state())
    state["terminal_review_findings"][0]["classification"] = "closed"
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_terminal_review_findings_invalid",
    ):
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


def test_plan_contains_canonical_architecture_threat_model_and_full_route() -> None:
    plan = (
        ROOT
        / "docs"
        / "plans"
        / "phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md"
    ).read_text(encoding="utf-8")
    for concept in (
        "Canonical Architecture Convergence",
        "backend/app/utils/cloud_files.py",
        "backend/app/services/source_ingestion_gate.py",
        "legacy `scan_and_import(dry_run=True)`",
        "OS or kernel compromise",
        "SCV2-FL1-I2 - Pre-Real Hardening",
        "SCV2-FL1-I3 - Bounded Real-Source Inventory Canary",
        "SCV2-FL1-I4 - Full-Library Read-Only Inventory",
        "SCV2-FL1-E1 - Isolated Import Rehearsal",
        "SCV2-FL1-E2 - Local Classification And AI Tagging",
        "SCV2-FL1-V1 - Product And Owner Validation",
    ):
        assert concept in plan
