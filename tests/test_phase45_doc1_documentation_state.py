"""Current documentation-state tests for the SCV2-PX1 route."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.check_documentation_state import (
    DocumentationStateError,
    SCV2_PX1_IN_PROGRESS_STATUS,
    SCV2_PX1_READY_STATUS,
    SCV2_PX1_FINAL_REVIEW_DUE_GATES,
    SCV2_PX1_REQUIRED_DEFERRED_GATES,
    load_state,
    render_handoff,
    validate_roadmaps,
    validate_state,
)


ROOT = Path(__file__).resolve().parents[1]


def _state() -> dict[str, object]:
    return load_state(ROOT / "docs" / "state" / "current-phase.json")


def test_current_handoff_is_exact_px1_projection() -> None:
    state = _state()
    handoff = (ROOT / "docs" / "current-handoff.md").read_text(encoding="utf-8")
    assert handoff == render_handoff(state)
    assert 55 <= len(handoff.splitlines()) <= 180
    assert "SCV2-PX1" in handoff
    assert "SCV2-PX2" in handoff
    assert "SCV2-PX3" in handoff
    assert state["active_blocker"]["code"] in handoff
    assert "phase-4.5-PX1` orchestration remains historical" in handoff
    assert "owner_accepted=false" in handoff
    assert "safe_to_merge=false" in handoff
    assert "merge_authorized=false" in handoff


def test_px1_state_and_active_docs_validate() -> None:
    state = _state()
    validate_state(state)
    validate_roadmaps(state)
    assert state["phase_id"] == "SCV2-PX1"
    assert state["previous_phase"] == "SCV2-FL1-I2"
    assert state["previous_phase_status"] == (
        "owner_adjudicated_pr146_merged_with_deferred_due_gates_preserved"
    )
    assert state["current_status"] in {
        SCV2_PX1_IN_PROGRESS_STATUS,
        SCV2_PX1_READY_STATUS,
    }
    assert state["target_met"] is (
        state["current_status"] == SCV2_PX1_READY_STATUS
    )
    assert state["safe_to_merge"] is False
    assert state["route_approved"] is False
    assert state["next_phase_started"] is False


def test_pr146_merge_and_final_review_debt_are_exact() -> None:
    protected = _state()["protected_evidence"]
    assert protected["pr146_accepted_head"] == (
        "914d746c3548241a99333393daa88caefd8b2337"
    )
    assert protected["pr146_accepted_tree"] == (
        "9f7bfc76d0d405e2d5081bc8cd8d38d54e090b71"
    )
    assert protected["pr146_merge_commit"] == (
        "8a825bcdd12f76d1c2c396b7039bd9e326cd63dc"
    )
    assert protected["pr146_merge_tree"] == protected["pr146_accepted_tree"]
    assert protected["pr146_merged"] is True
    assert protected["pr146_final_review_id"] == 5031131564
    assert protected["pr146_final_review_finding_count"] == 10
    assert protected["pr146_final_review_resolved_count"] == 0
    assert protected["pr146_final_review_outdated_count"] == 0
    assert {
        item["thread_id"]: item["due_gate"]
        for item in protected["pr146_final_review_deferred_findings"]
    } == SCV2_PX1_FINAL_REVIEW_DUE_GATES


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pr146_merged", False),
        ("pr146_final_review_id", 0),
        ("pr146_final_review_finding_count", 9),
        ("pr146_final_review_resolved_count", 1),
        ("machine_verifiable_ci", True),
        ("owner_accepted", True),
        ("safe_to_merge", True),
        ("merge_authorized", True),
        ("external_data_plane_network_operation_count", 1),
        ("existing_database_access_operation_count", 1),
    ],
)
def test_protected_evidence_mutation_fails_closed(field: str, value: object) -> None:
    state = copy.deepcopy(_state())
    state["protected_evidence"][field] = value
    with pytest.raises(DocumentationStateError, match="protected_evidence"):
        validate_state(state)


@pytest.mark.parametrize(
    "field",
    [
        "merge_authorized",
        "real_source_inventory_authorized",
        "source_root_or_icloud_access_authorized",
        "existing_database_access_authorized",
        "app_storage_write_authorized",
        "real_pixiv_or_gallery_dl_network_execution_authorized",
        "provider_credentials_authorized",
        "media_or_thumbnail_download_authorized",
        "import_authorized",
        "classification_or_tagging_execution_on_user_data_authorized",
        "llm_or_external_model_authorized",
        "server_browser_or_e2e_authorized",
        "production_authorized",
        "full_library_import_authorized",
    ],
)
def test_forbidden_authority_mutation_fails_closed(field: str) -> None:
    state = copy.deepcopy(_state())
    state["authorities"][field] = True
    with pytest.raises(DocumentationStateError, match="authority_map"):
        validate_state(state)


def test_due_gate_set_cannot_be_omitted_or_relabelled() -> None:
    state = copy.deepcopy(_state())
    assert {item["id"] for item in state["deferred_debt"]} == (
        SCV2_PX1_REQUIRED_DEFERRED_GATES
    )
    state["deferred_debt"].pop()
    with pytest.raises(DocumentationStateError, match="deferred_due_gate_set"):
        validate_state(state)

    state = copy.deepcopy(_state())
    state["protected_evidence"]["pr146_final_review_deferred_findings"][0][
        "due_gate"
    ] = "PX1_FALSE_CLOSURE"
    with pytest.raises(DocumentationStateError, match="due_gate_map"):
        validate_state(state)


def test_public_state_rejects_private_path_and_secret() -> None:
    for value in (
        "C:\\private\\source",
        "Authorization: Bearer synthetic-secret",
    ):
        state = copy.deepcopy(_state())
        state["route_scope"] = value
        with pytest.raises(DocumentationStateError, match="redaction"):
            validate_state(state)


def test_tracked_current_json_is_public_safe() -> None:
    text = (ROOT / "docs" / "state" / "current-phase.json").read_text(
        encoding="utf-8"
    )
    json.loads(text)
    assert "C:\\" not in text
    assert "cookie=" not in text.casefold()
    assert "bearer " not in text.casefold()
    assert "raw provider response" not in text.casefold()
