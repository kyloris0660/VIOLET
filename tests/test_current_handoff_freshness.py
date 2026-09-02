"""Fail-closed freshness tests for the active SCV2-PX2 projection."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

import pytest

from scripts import check_documentation_state as documentation_state


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "docs" / "state" / "current-phase.json"


def _state() -> dict[str, object]:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def test_current_phase_schema_identity_and_boundary_are_exact() -> None:
    state = _state()
    documentation_state.validate_state(state)
    assert state["schema_version"] == "violet.current-phase.v2"
    assert state["phase_id"] == "SCV2-PX2"
    assert state["branch"] == documentation_state.SCV2_PX2_BRANCH
    assert state["accepted_mainline_base"] == documentation_state.SCV2_PX2_ACCEPTED_MAIN
    assert state["accepted_mainline_tree"] == documentation_state.SCV2_PX2_ACCEPTED_MAIN_TREE
    assert state["safe_to_merge"] is False
    assert state["route_approved"] is False
    ready = state["current_status"] == documentation_state.SCV2_PX2_READY_STATUS
    assert state["target_met"] is ready
    assert state["manual_acceptance_status"] == (
        "pending_scv2_px2_owner_merge_audit"
        if ready
        else "px2_synthetic_implementation_in_progress"
    )


def test_live_git_binds_pr147_merge_and_px2_implementation_evidence() -> None:
    state = _state()
    documentation_state.validate_git_ancestry(state, root=ROOT)
    assert documentation_state._trusted_git_value(
        ROOT, "rev-parse", f"{documentation_state.SCV2_PX2_PR147_ACCEPTED_HEAD}^{{tree}}"
    ) == state["previous_phase_final_tree"]
    assert documentation_state._trusted_git_value(
        ROOT, "rev-parse", f"{documentation_state.SCV2_PX2_ACCEPTED_MAIN}^{{tree}}"
    ) == state["accepted_mainline_tree"]
    assert tuple(
        documentation_state._trusted_git_value(
            ROOT, "show", "-s", "--format=%P", documentation_state.SCV2_PX2_ACCEPTED_MAIN
        ).split()
    ) == documentation_state.SCV2_PX2_PR147_MERGE_PARENTS
    assert documentation_state._trusted_git_value(
        ROOT, "rev-parse", f"{state['implementation_evidence_head']}^{{tree}}"
    ) == state["implementation_evidence_tree"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("branch", "codex/wrong"),
        ("accepted_mainline_base", "f" * 40),
        ("accepted_mainline_tree", "f" * 40),
        ("previous_phase_merge_commit", "f" * 40),
        ("previous_phase_final_head", "f" * 40),
        ("previous_phase_status", "pending_merge"),
    ],
)
def test_baseline_identity_mutation_fails_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state[field] = value
    with pytest.raises(documentation_state.DocumentationStateError, match="identity_or_baseline"):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("safe_to_merge", True),
        ("route_approved", True),
        ("next_phase_started", True),
        ("planning_authorized", False),
        ("planning_completed", False),
        ("planning_approved", False),
        ("manual_acceptance_status", "accepted"),
    ],
)
def test_status_or_owner_authority_mutation_fails_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state[field] = value
    with pytest.raises(documentation_state.DocumentationStateError, match="status_fields_conflict"):
        documentation_state.validate_state(state)


def test_target_met_and_contract_flags_track_ready_status() -> None:
    state = copy.deepcopy(_state())
    state["target_met"] = not bool(state["target_met"])
    with pytest.raises(documentation_state.DocumentationStateError, match="status_fields_conflict"):
        documentation_state.validate_state(state)

    state = copy.deepcopy(_state())
    state["pipeline_contract"]["deterministic_replay_verified"] = not bool(
        state["pipeline_contract"]["deterministic_replay_verified"]
    )
    with pytest.raises(documentation_state.DocumentationStateError, match="contract_projection"):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract_id", "caller_positive_contract"),
        ("public_schema", "caller.schema"),
        ("machine_verifiable_ci", True),
        ("owner_authority_machine_verifiable", True),
    ],
)
def test_contract_projection_mutation_fails_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state["pipeline_contract"][field] = value
    with pytest.raises(documentation_state.DocumentationStateError, match="contract_projection"):
        documentation_state.validate_state(state)


def test_authority_and_protected_evidence_mutations_fail_closed() -> None:
    state = copy.deepcopy(_state())
    state["authorities"]["px2_merge_authorized"] = True
    with pytest.raises(documentation_state.DocumentationStateError, match="authority_map"):
        documentation_state.validate_state(state)

    state = copy.deepcopy(_state())
    state["protected_evidence"]["px1_merged"] = False
    with pytest.raises(documentation_state.DocumentationStateError, match="protected_evidence"):
        documentation_state.validate_state(state)

    state = copy.deepcopy(_state())
    state["protected_evidence"]["provider_network_activity"] = 1
    with pytest.raises(documentation_state.DocumentationStateError, match="protected_evidence"):
        documentation_state.validate_state(state)


def test_fixed_px1_px2_px3_route_cannot_expand_or_start_px3() -> None:
    state = copy.deepcopy(_state())
    state["upcoming_route"].append(
        {"phase_id": "SCV2-PX4", "scope": "not authorized", "started": False}
    )
    with pytest.raises(documentation_state.DocumentationStateError, match="fixed_route"):
        documentation_state.validate_state(state)

    state = copy.deepcopy(_state())
    state["upcoming_route"][2]["started"] = True
    with pytest.raises(documentation_state.DocumentationStateError, match="fixed_route"):
        documentation_state.validate_state(state)


def test_handoff_is_exact_generated_projection() -> None:
    state = _state()
    actual = (ROOT / "docs" / "current-handoff.md").read_text(encoding="utf-8")
    assert actual == documentation_state.render_handoff(state)
    assert "SCV2-PX2" in actual
    assert "PX1 Merge Projection" in actual
    assert "Deferred Debt And Exact Due Gates" in actual
    assert "378" not in actual
    assert state["protected_evidence"]["primary_user_untracked_name_digest_before"] not in actual


def test_active_markers_and_contract_commands_are_consistent() -> None:
    state = _state()
    documentation_state.validate_roadmaps(state, root=ROOT)
    for relative in (
        "docs/project-roadmap.md",
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/phase-contracts.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert text.count("<!-- CURRENT_PHASE: SCV2-PX2 -->") == 1
    contract = (ROOT / "docs" / "phase-contracts.md").read_text(encoding="utf-8")
    assert "run_scv2_px2_pixiv_metadata_clustering.py" in contract
    assert "--px2-evidence" in contract
    assert "phase-4.5-PX1 is historical" in contract


def test_conflicting_current_marker_fails_closed(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    (docs / "roadmap").mkdir(parents=True)
    for relative in (
        Path("project-roadmap.md"),
        Path("roadmap/current-mainline-roadmap.md"),
        Path("phase-contracts.md"),
    ):
        source = ROOT / "docs" / relative
        target = docs / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    target = docs / "project-roadmap.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "<!-- CURRENT_PHASE: SCV2-PX2 -->",
            "<!-- CURRENT_PHASE: SCV2-PX1 -->",
        ),
        encoding="utf-8",
    )
    with pytest.raises(documentation_state.DocumentationStateError, match="current_phase_conflict"):
        documentation_state.validate_roadmaps(_state(), root=tmp_path)


def test_public_state_rejects_nul_and_private_path() -> None:
    state = copy.deepcopy(_state())
    state["route_scope"] = "synthetic\x00scope"
    with pytest.raises(documentation_state.DocumentationStateError, match="redaction"):
        documentation_state.validate_state(state)

    state = copy.deepcopy(_state())
    state["route_scope"] = "C:\\private\\fixture"
    with pytest.raises(documentation_state.DocumentationStateError, match="redaction"):
        documentation_state.validate_state(state)


def test_documentation_checker_returns_current_phase_result() -> None:
    result = documentation_state.check_documentation_state(root=ROOT)
    assert result["passed"] is True
    assert result["phase_id"] == "SCV2-PX2"
    assert result["current_status"] == _state()["current_status"]
