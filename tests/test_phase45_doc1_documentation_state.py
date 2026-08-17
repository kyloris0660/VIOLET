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


def test_current_handoff_is_current_route_focused() -> None:
    handoff = read_text(ROOT / "docs" / "current-handoff.md")
    state = load_state()
    assert handoff == render_handoff(state)
    assert 55 <= len(handoff.splitlines()) <= 115
    assert "docs/state/current-phase.json" in handoff
    assert state["phase_id"] in handoff
    assert "PR pending creation" in handoff or f"PR #{state['pr_number']}" in handoff
    assert state["active_blocker"]["code"] in handoff
    assert "projected external cost: `$0`" in handoff
    assert "17 findings" in handoff


def test_fl1_i2_state_authorizes_only_synthetic_implementation() -> None:
    state = load_state()
    validate_state(state)
    validate_roadmaps(state)
    assert state["phase_id"] == "SCV2-FL1-I2"
    assert state["current_status"] == "fl1_i2_pr146_bounded_correction_ready_for_owner_reaudit"
    assert state["planning_authorized"] is True
    assert state["planning_completed"] is True
    assert state["planning_approved"] is True
    assert state["target_met"] is False
    assert state["safe_to_merge"] is False
    assert state["route_approved"] is False
    assert state["planning_boundary"]["implementation_authorized"] is True
    assert state["planning_boundary"]["implementation_started"] is True
    assert state["planning_boundary"]["implementation_completed"] is True
    assert state["planning_boundary"]["synthetic_ephemeral_test_fixture_authorized"] is True
    assert state["planning_boundary"]["real_inventory_started"] is False
    assert state["planning_boundary"]["real_source_inventory_authorized"] is False
    assert state["protected_evidence"]["fl1_i2_superseded_evidence_head"] == (
        "78ccbdc69ee1bf0f51c297435b56e2be868b54e9"
    )
    assert state["protected_evidence"]["fl1_i2_bounded_correction_authorized"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_met", True),
        ("safe_to_merge", True),
        ("route_approved", True),
        ("planning_approved", False),
        ("manual_acceptance_status", "pending_fl1_i2_plan_owner_reaudit"),
    ],
)
def test_owner_accepted_state_mutation_fails_closed(field: str, value: object) -> None:
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
    with pytest.raises(DocumentationStateError, match="fl1_i2_boundary_invalid"):
        validate_state(state)


def test_public_current_phase_and_updated_icloud_doc_are_redacted() -> None:
    serialized = json.dumps(load_state(), ensure_ascii=False)
    icloud = read_text(ROOT / "docs" / "icloud-safe-ingestion.md")
    assert "C:\\Users\\" not in serialized
    assert "C:\\Users\\kyloris\\Pictures" not in icloud
    assert "<private-source-root>" in icloud
    assert "canonically integrated with that runtime scanner" in icloud
    assert "Nothing here claims I2 source-safety" in icloud
    assert "runtime integration is complete" in icloud


def test_current_contract_names_review_gates_and_claim_boundary() -> None:
    contract = read_text(ROOT / "docs" / "phase-contracts.md")
    assert "Terminal review `4897012517`" in contract
    assert "17 historical" in contract
    assert "14 gates" in contract
    assert "not tamper-resistant" in contract
    assert "machine_verifiable_ci=false" in contract
    assert "closed_in_fl1_i2_synthetic_implementation_evidence" in json.dumps(
        load_state(), ensure_ascii=False
    )
    assert "scv2_fl1_i2_pre_real_hardening_contract_v1" in contract
    assert "FileIdExtdDirectory" in contract
    assert "acb12c1db258fdef1d4f063b053d422e0d887abf" in contract
    assert "4907783329" in contract
    for gate in (
        "REAL_OPERATION_GATEWAY_GATE",
        "VALIDATION_RECEIPT_GATE",
        "OWNER_AUTHORITY_GATE",
        "POSIX_LEDGER_DURABILITY_GATE",
        "STABLE_REPLAY_GATE",
    ):
        assert gate in contract
