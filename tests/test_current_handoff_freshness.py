"""Semantic guards for the machine-readable current-phase documentation state."""

from __future__ import annotations

import json
import shutil
import copy
from pathlib import Path

import pytest

from scripts import check_documentation_state as documentation_state


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "docs" / "state" / "current-phase.json"
HANDOFF_PATH = ROOT / "docs" / "current-handoff.md"
INCIDENT_PATH = (
    ROOT
    / "docs"
    / "reports"
    / "phase-4.5-scv2-sv1b-replay-trusted-provenance-checkpoint.md"
)
ADR_PATH = ROOT / "docs" / "decisions" / "ADR-0001-stable-replay-evidence-v2.md"


def _state() -> dict[str, object]:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _copy_docs_root(tmp_path: Path) -> Path:
    copied_root = tmp_path / "repo"
    shutil.copytree(ROOT / "docs", copied_root / "docs")
    return copied_root


def test_current_phase_schema_and_status_fields_are_consistent() -> None:
    state = _state()

    documentation_state.validate_state(state)
    assert state["schema_version"] == "violet.current-phase.v1"
    assert state["phase_id"] == "SCV2-SV1B"
    assert state["repository"] == "kyloris0660/VIOLET"
    assert state["pr_number"] == 139
    assert state["draft"] is True
    assert state["target_met"] is False
    assert state["safe_to_merge"] is False
    assert state["route_approved"] is False
    assert state["manual_acceptance_status"] in {
        "not_started_replay_recovery",
        "pending_user",
    }
    assert state["next_phase_started"] is False
    assert state["active_blocker"]["code"]
    assert state["active_blocker"]["scope"]
    assert state["active_blocker"]["resolution"]
    assert state["current_replay_strategy"]["fresh_replay_database_creation_limit"] == 1
    assert state["current_replay_strategy"]["external_call_budget"] == 0
    assert state["public_state_boundary"] == (
        "public_safe_governance_only_no_private_proof_payloads_or_paths"
    )
    protected = state["protected_evidence"]
    assert protected["canonical_phase_acquired_membership_count"] == 7271
    assert protected[
        "canonical_phase_acquired_membership_fingerprint"
    ] == "47390e3cc2dd43af484d6d6c92ef8cbb86c3cf8984304b64c86f9d97eb641bd1"
    assert protected["canonical_phase_acquired_missing_count"] == 0
    assert protected["canonical_phase_acquired_unsupported_count"] == 0
    assert protected["superseded_candidate_provenance_membership_count"] == 7257
    assert protected["production_library_consumed_or_modified"] is False
    assert any(
        "binding v4" in operation
        for operation in state["authorized_operations"]
    )
    assert not any(
        "binding v3" in operation
        for operation in state["authorized_operations"]
    )


def test_handoff_is_exact_generated_projection_and_stays_small() -> None:
    state = _state()
    rendered = documentation_state.render_handoff(state)

    assert HANDOFF_PATH.read_text(encoding="utf-8") == rendered
    assert 40 <= len(rendered.splitlines()) <= 60
    assert "this file is not the fact source" in rendered
    assert state["current_status"] in rendered
    assert state["next_required_checkpoint"] in rendered
    assert all(
        operation in rendered
        for operation in state["authorized_operations"]
    )
    assert "binding v2" not in rendered
    assert "binding v4" in rendered


def test_handoff_writer_atomically_renders_current_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "current-handoff.md"
    state = _state()

    documentation_state.write_handoff(state, path=target)

    assert target.read_text(encoding="utf-8") == (
        documentation_state.render_handoff(state)
    )
    assert not target.with_suffix(".md.tmp").exists()


def test_current_phase_links_report_adr_and_contract_are_consistent() -> None:
    state = _state()
    documentation_state.check_documentation_state()
    incident = INCIDENT_PATH.read_text(encoding="utf-8")
    adr = ADR_PATH.read_text(encoding="utf-8")
    contract = (ROOT / "docs" / "phase-contracts.md").read_text(encoding="utf-8")

    for link in state["durable_links"]:
        assert (ROOT / link["path"]).is_file()
    blocker = state["active_blocker"]["code"]
    assert blocker in incident
    assert blocker in contract
    assert blocker in HANDOFF_PATH.read_text(encoding="utf-8")
    assert "schema-aware" in incident
    assert "schema-aware" in adr
    assert "fresh isolated Replay" in incident
    assert "development numeric row ids" in adr.lower()
    assert "Primary export -> fresh import -> Replay re-export" in adr
    top = "\n".join(incident.splitlines()[:20])
    assert (
        f"AUTHORITATIVE_CURRENT_STATUS: {state['current_status']}" in top
    )
    assert (
        "AUTHORITATIVE_MANUAL_ACCEPTANCE_STATUS: "
        f"{state['manual_acceptance_status']}" in top
    )


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


def test_state_change_without_handoff_regeneration_fails_closed(tmp_path: Path) -> None:
    copied_root = _copy_docs_root(tmp_path)
    state_path = copied_root / "docs" / "state" / "current-phase.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["updated_at"] = "2099-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="generated_handoff_drift",
    ):
        documentation_state.check_documentation_state(root=copied_root)


def test_conflicting_current_roadmap_phase_fails_closed(tmp_path: Path) -> None:
    copied_root = _copy_docs_root(tmp_path)
    roadmap = copied_root / "docs" / "project-roadmap.md"
    text = roadmap.read_text(encoding="utf-8")
    roadmap.write_text(
        text.replace(
            "<!-- CURRENT_PHASE: SCV2-SV1B -->",
            "<!-- CURRENT_PHASE: SCV2-FL1 -->",
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="current_phase_marker_count",
    ):
        documentation_state.check_documentation_state(root=copied_root)


def test_historical_reports_remain_historical_and_outside_generated_state() -> None:
    state = _state()
    historical = (
        ROOT
        / "docs"
        / "reports"
        / "phase-4.5-scv2-ml2-multilingual-identity-candidate-closure-summary.json"
    )
    payload = json.loads(historical.read_text(encoding="utf-8"))

    assert payload["pipeline_contract"]["status"] == (
        "target_met_multilingual_identity_candidate_closure"
    )
    assert payload["pipeline_contract"]["safe_to_merge"] is True
    assert historical.relative_to(ROOT).as_posix() not in {
        link["path"] for link in state["durable_links"]
    }


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("current_status", "blocked_sv1b_replay", "pending_user_status_fields_conflict"),
        ("target_met", True, "pending_user_status_fields_conflict"),
        ("safe_to_merge", True, "pending_user_status_fields_conflict"),
        ("route_approved", True, "pending_user_status_fields_conflict"),
        ("next_phase_started", True, "sv1b_cannot_start_next_phase"),
    ],
)
def test_pending_user_five_field_combinations_fail_closed(
    field: str,
    value: object,
    error: str,
) -> None:
    state = copy.deepcopy(_state())
    state[field] = value

    with pytest.raises(documentation_state.DocumentationStateError, match=error):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    "authorization",
    [
        "create another Replay database",
        "import accepted evidence into Replay",
        "derive fresh Replay graph",
    ],
)
def test_pending_user_cannot_authorize_completed_database_operations(
    authorization: str,
) -> None:
    state = copy.deepcopy(_state())
    state["authorized_operations"].append(authorization)

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="pending_user_database_operation_authorized",
    ):
        documentation_state.validate_state(state)


def test_completed_future_command_in_blocker_resolution_fails_closed() -> None:
    state = copy.deepcopy(_state())
    state["active_blocker"]["resolution"] = (
        "Commit this final public state before owner acceptance."
    )

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="blocker_resolution_contains_completed_future_command",
    ):
        documentation_state.validate_state(state)


def test_pending_user_requires_single_active_binding_v4_authorization() -> None:
    state = copy.deepcopy(_state())
    state["authorized_operations"][-1] = (
        "exactly one versioned non-overwriting audit-closeout final binding v3"
    )

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="pending_user_active_binding_authorization_invalid",
    ):
        documentation_state.validate_state(state)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("canonical_phase_acquired_membership_count", 7257),
        ("canonical_phase_acquired_missing_count", 1),
        ("canonical_phase_acquired_unsupported_count", 1),
        ("canonical_phase_acquired_membership_fingerprint", "0" * 63),
        ("production_library_consumed_or_modified", True),
    ),
)
def test_sv1b_canonical_membership_public_state_fails_closed(
    field: str,
    value: object,
) -> None:
    state = copy.deepcopy(_state())
    state["protected_evidence"][field] = value

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="sv1b_canonical_phase_membership_state_invalid",
    ):
        documentation_state.validate_state(state)


def test_linked_incident_must_declare_authoritative_current_state_at_top(
    tmp_path: Path,
) -> None:
    copied_root = _copy_docs_root(tmp_path)
    incident = (
        copied_root
        / "docs"
        / "reports"
        / "phase-4.5-scv2-sv1b-replay-trusted-provenance-checkpoint.md"
    )
    incident.write_text(
        incident.read_text(encoding="utf-8").replace(
            "AUTHORITATIVE_CURRENT_STATUS: "
            "automated_sv1b_candidate_ready_manual_acceptance_pending",
            "AUTHORITATIVE_CURRENT_STATUS: historical_blocker",
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="incident_authoritative_state_mismatch",
    ):
        documentation_state.check_documentation_state(root=copied_root)


def test_historical_incident_status_requires_superseded_marker(
    tmp_path: Path,
) -> None:
    copied_root = _copy_docs_root(tmp_path)
    incident = (
        copied_root
        / "docs"
        / "reports"
        / "phase-4.5-scv2-sv1b-replay-trusted-provenance-checkpoint.md"
    )
    incident.write_text(
        incident.read_text(encoding="utf-8").replace(
            "<!-- HISTORICAL_STATUSES_BELOW: historical_superseded -->",
            "",
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="incident_historical_status_marker_missing",
    ):
        documentation_state.check_documentation_state(root=copied_root)


def test_historical_checkpoint_summary_cannot_masquerade_as_current(
    tmp_path: Path,
) -> None:
    copied_root = _copy_docs_root(tmp_path)
    summary_path = (
        copied_root
        / "docs"
        / "reports"
        / "phase-4.5-scv2-sv1b-replay-trusted-provenance-checkpoint-summary.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["record_role"] = "authoritative_current_state"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="incident_summary_role_not_historical",
    ):
        documentation_state.check_documentation_state(root=copied_root)


@pytest.mark.parametrize(
    "field",
    ["accepted_mainline_base", "implementation_evidence_head"],
)
def test_current_phase_git_heads_must_be_ancestors(field: str) -> None:
    state = copy.deepcopy(_state())
    state[field] = "f" * 40

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match=f"{field}_not_ancestor_of_head",
    ):
        documentation_state.validate_git_ancestry(state)
