"""Current documentation-state tests for the final SCV2-PX3 route."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.check_documentation_state import (
    DocumentationStateError,
    SCV2_PX1_REQUIRED_DEFERRED_GATES,
    SCV2_PX3_IN_PROGRESS_STATUS,
    SCV2_PX3_READY_STATUS,
    load_state,
    render_handoff,
    validate_roadmaps,
    validate_state,
)


ROOT = Path(__file__).resolve().parents[1]


def _state() -> dict[str, object]:
    return load_state(ROOT / "docs" / "state" / "current-phase.json")


def test_current_handoff_is_exact_px3_projection() -> None:
    state = _state()
    handoff = (ROOT / "docs" / "current-handoff.md").read_text(encoding="utf-8")
    assert handoff == render_handoff(state)
    assert 55 <= len(handoff.splitlines()) <= 180
    assert "PX2 Merge Projection" in handoff
    assert "SCV2_PX2_MERGED" in handoff
    assert "SCV2-PX3" in handoff
    assert state["active_blocker"]["code"] in handoff
    assert "Historical phase-4.5-PX1 is historical compatibility evidence" in handoff
    assert "px3_owner_accepted=false" in handoff
    assert "px3_merge_authorized=false" in handoff


def test_px3_state_and_active_docs_validate() -> None:
    state = _state()
    validate_state(state)
    validate_roadmaps(state)
    assert state["phase_id"] == "SCV2-PX3"
    assert state["previous_phase"] == "SCV2-PX2"
    assert state["previous_phase_status"] == (
        "owner_accepted_pr148_merged_with_exact_tree_preserved"
    )
    assert state["current_status"] in {
        SCV2_PX3_IN_PROGRESS_STATUS,
        SCV2_PX3_READY_STATUS,
    }
    ready = state["current_status"] == SCV2_PX3_READY_STATUS
    assert state["target_met"] is ready
    assert state["safe_to_merge"] is False
    assert state["route_approved"] is False
    assert state["next_phase_started"] is False


def test_pr148_merge_identity_is_exact() -> None:
    protected = _state()["protected_evidence"]
    assert protected["pr148_accepted_head"] == (
        "bf8055af61c3a5d32155701ed7110db692047dba"
    )
    assert protected["pr148_accepted_tree"] == (
        "507a223a9156ff2f9944524303419e85891812fa"
    )
    assert protected["pr148_merge_commit"] == (
        "421e2989d274e2dc4492d5bccc10720dcfbbaa4f"
    )
    assert protected["pr148_merge_tree"] == protected["pr148_accepted_tree"]
    assert protected["pr148_merge_parents"] == [
        "5a8efdaf954ab95bd82f95464af31a7fd0873e5e",
        "bf8055af61c3a5d32155701ed7110db692047dba",
    ]
    assert protected["pr148_merged"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pr148_merged", False),
        ("pr148_accepted_head", "f" * 40),
        ("px2_owner_accepted", False),
        ("px2_merged", False),
        ("px3_started", False),
        ("px3_owner_accepted", True),
        ("px3_safe_to_merge", True),
        ("px3_merge_authorized", True),
        ("provider_network_activity", 1),
        ("existing_db_or_app_storage_activity", 1),
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
        "px3_merge_authorized",
        "real_pixiv_network_execution_authorized",
        "provider_credentials_authorized",
        "real_source_or_icloud_access_authorized",
        "existing_database_or_app_storage_mutation_authorized",
        "user_data_import_authorized",
        "production_authorized",
        "full_library_import_authorized",
    ],
)
def test_forbidden_authority_mutation_fails_closed(field: str) -> None:
    state = copy.deepcopy(_state())
    state["authorities"][field] = True
    with pytest.raises(DocumentationStateError, match="authority_map"):
        validate_state(state)


def test_due_gate_set_cannot_be_omitted() -> None:
    state = copy.deepcopy(_state())
    assert {item["id"] for item in state["deferred_debt"]} == (
        SCV2_PX1_REQUIRED_DEFERRED_GATES
    )
    state["deferred_debt"].pop()
    with pytest.raises(DocumentationStateError, match="deferred_due_gate_set"):
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
