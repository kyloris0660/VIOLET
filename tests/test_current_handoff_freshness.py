"""Fail-closed freshness tests for the final SCV2-PX3 projection."""

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
    return json.loads(documentation_state._trusted_git_value(ROOT, 'show', '26a6fc8d30ba2b2eae69f55a8e7c33d5a4b9cdd3:docs/state/current-phase.json'))


def test_current_phase_schema_identity_and_boundary_are_exact() -> None:
    state = _state()
    documentation_state.validate_state(state)
    assert state["schema_version"] == "violet.current-phase.v2"
    assert state["phase_id"] == "SCV2-PX3"
    restored = bool(state.get('restored_canary'))
    assert state["branch"] == (documentation_state.SCV2_PX3_RESTORE_BRANCH if restored else documentation_state.SCV2_PX3_BRANCH)
    assert state["accepted_mainline_base"] == documentation_state.SCV2_PX3_ACCEPTED_MAIN
    assert state["accepted_mainline_tree"] == documentation_state.SCV2_PX3_ACCEPTED_MAIN_TREE
    assert state["safe_to_merge"] is (state.get('restored_canary', {}).get('followup_merge_authorized', False) or state['current_status'] == documentation_state.SCV2_PX3_CLOSURE_READY_STATUS)
    assert state["route_approved"] is False
    ready = state["current_status"] != documentation_state.SCV2_PX3_IN_PROGRESS_STATUS
    assert state["target_met"] is ready
    assert state["manual_acceptance_status"] == (
        'owner_accepted_final_bounded_product_closure' if restored or state['current_status'] in {
            documentation_state.SCV2_PX3_CLOSURE_READY_STATUS, documentation_state.SCV2_PX3_MERGED_STATUS}
        else ('pending_scv2_px3_owner_acceptance_and_controlled_canary' if ready else 'px3_product_integration_in_progress')
    )


def test_live_git_binds_pr148_merge_and_px3_implementation_evidence() -> None:
    state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    documentation_state.validate_git_ancestry(state, root=ROOT)
    assert documentation_state._trusted_git_value(ROOT,'merge-base',state['accepted_mainline_base'],'HEAD') == state['accepted_mainline_base']
    assert state['previous_phase_pr_number'] == 150


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
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="identity_or_baseline",
    ):
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
def test_status_or_owner_authority_mutation_fails_closed(
    field: str, value: object
) -> None:
    state = copy.deepcopy(_state())
    state[field] = not state[field] if isinstance(value, bool) else value
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="status_fields_conflict",
    ):
        documentation_state.validate_state(state)


def test_target_met_and_contract_flags_track_ready_status() -> None:
    state = copy.deepcopy(_state())
    state["target_met"] = not bool(state["target_met"])
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="status_fields_conflict",
    ):
        documentation_state.validate_state(state)

    state = copy.deepcopy(_state())
    state["pipeline_contract"]["product_persistence_verified"] = not bool(
        state["pipeline_contract"]["product_persistence_verified"]
    )
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="contract_projection",
    ):
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
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="contract_projection",
    ):
        documentation_state.validate_state(state)


def test_authority_and_protected_evidence_mutations_fail_closed() -> None:
    state = copy.deepcopy(_state())
    state["authorities"]["px3_merge_authorized"] = True
    with pytest.raises(documentation_state.DocumentationStateError, match="authority_map"):
        documentation_state.validate_state(state)

    state = copy.deepcopy(_state())
    state["protected_evidence"]["px2_merged"] = False
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="protected_evidence",
    ):
        documentation_state.validate_state(state)

    state = copy.deepcopy(_state())
    state["protected_evidence"]["provider_network_activity"] = 1
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="protected_evidence",
    ):
        documentation_state.validate_state(state)


def test_fixed_route_cannot_expand_or_unstart_px3() -> None:
    state = copy.deepcopy(_state())
    state["upcoming_route"].append(
        {"phase_id": "SCV2-PX4", "scope": "not authorized", "started": False}
    )
    with pytest.raises(documentation_state.DocumentationStateError, match="fixed_route"):
        documentation_state.validate_state(state)

    state = copy.deepcopy(_state())
    state["upcoming_route"][2]["started"] = False
    with pytest.raises(documentation_state.DocumentationStateError, match="fixed_route"):
        documentation_state.validate_state(state)


def test_handoff_is_exact_generated_projection() -> None:
    state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    actual = (ROOT/'docs/current-handoff.md').read_text(encoding='utf-8')
    assert actual == documentation_state.render_handoff(state)
    assert 'PRODUCTION-PIXIV-A1' in actual
    assert '项目负责人复审' in actual
    assert '原图、人工标签、相册、确认实体保留' in actual


def test_active_markers_and_contract_commands_are_consistent() -> None:
    state=json.loads(STATE_PATH.read_text(encoding='utf-8'))
    documentation_state.validate_roadmaps(state,root=ROOT)
    for relative in ('docs/project-roadmap.md','docs/roadmap/current-mainline-roadmap.md','docs/phase-contracts.md'):
        assert (ROOT/relative).read_text(encoding='utf-8').count('<!-- CURRENT_PHASE: PRODUCTION-PIXIV-A1 -->') == 1
    assert 'production_pixiv_a1_v1' in (ROOT/'docs/phase-contracts.md').read_text(encoding='utf-8')


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
            "<!-- CURRENT_PHASE: PRODUCTION-PIXIV-A1 -->",
            "<!-- CURRENT_PHASE: SCV2-PX2 -->",
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="a1_roadmap_marker",
    ):
        documentation_state.validate_roadmaps(json.loads(STATE_PATH.read_text(encoding='utf-8')), root=tmp_path)


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
    result=documentation_state.check_documentation_state(root=ROOT)
    assert result['passed'] is True
    assert result['phase_id']=='PRODUCTION-PIXIV-A1'
    assert result['current_status']==json.loads(STATE_PATH.read_text(encoding='utf-8'))['current_status']
