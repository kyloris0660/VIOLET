from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.check_documentation_state import (
    DocumentationStateError,
    load_state,
    render_handoff,
    validate_roadmaps,
    validate_state,
)


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_current_handoff_is_slim_and_current_route_focused() -> None:
    handoff = read_text(ROOT / "docs" / "current-handoff.md")
    state = load_state()
    assert handoff == render_handoff(state)
    assert 40 <= len(handoff.splitlines()) <= 65
    assert "docs/state/current-phase.json" in handoff
    assert state["phase_id"] in handoff
    assert "Draft PR" in handoff
    assert state["active_blocker"]["code"] in handoff
    assert "projected external cost: `$0`" in handoff
    for forbidden_term in ("provider", "Pixiv", "gallery-dl", "LLM"):
        assert forbidden_term in handoff


def test_fl1_i1_state_stops_before_real_inventory() -> None:
    state = load_state()
    validate_state(state)
    validate_roadmaps(state)
    assert state["phase_id"] == "SCV2-FL1-I1"
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
    assert state["planning_boundary"]["real_inventory_started"] is False
    assert state["planning_boundary"]["real_source_inventory_authorized"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_met", True),
        ("safe_to_merge", True),
        ("route_approved", True),
        ("manual_acceptance_status", "owner_accepted_fl1_i1"),
    ],
)
def test_unapproved_positive_claim_fails_closed(field: str, value: object) -> None:
    state = copy.deepcopy(load_state())
    state[field] = value
    with pytest.raises(DocumentationStateError):
        validate_state(state)


@pytest.mark.parametrize(
    "field",
    [
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
def test_forbidden_execution_authority_fails_closed(field: str) -> None:
    state = copy.deepcopy(load_state())
    state["planning_boundary"][field] = True
    with pytest.raises(DocumentationStateError, match="fl1_i1_boundary_invalid"):
        validate_state(state)


def test_sync_operator_classification_cannot_be_promoted() -> None:
    state = copy.deepcopy(load_state())
    state["protected_evidence"]["preflight_remote_sync_is_contract_proof"] = True
    with pytest.raises(
        DocumentationStateError, match="fl1_i1_protected_evidence_invalid"
    ):
        validate_state(state)


def test_public_current_phase_does_not_contain_private_absolute_path() -> None:
    serialized = json.dumps(load_state(), ensure_ascii=False)
    assert "C:\\Users\\" not in serialized
    assert ".local_manifests" not in serialized


def test_current_documents_name_all_five_use_before_gates() -> None:
    contract = read_text(ROOT / "docs" / "phase-contracts.md")
    for gate in (
        "REAL_OPERATION_GATEWAY_GATE",
        "VALIDATION_RECEIPT_GATE",
        "OWNER_AUTHORITY_GATE",
        "POSIX_LEDGER_DURABILITY_GATE",
        "STABLE_REPLAY_GATE",
    ):
        assert gate in contract


def test_pr142_is_read_only_archaeology_not_authority() -> None:
    plan = read_text(
        ROOT
        / "docs"
        / "plans"
        / "phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md"
    )
    roadmap = read_text(ROOT / "docs" / "roadmap" / "current-mainline-roadmap.md")
    assert "PR #142 bounded carry-forward matrix" in plan
    assert "No PR #142 commit" in plan
    assert "closed, unmerged" in roadmap
    assert "non-authoritative" in roadmap
