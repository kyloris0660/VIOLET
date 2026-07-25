"""Semantic guards for the machine-readable current-phase documentation state."""

from __future__ import annotations

import json
import shutil
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


def test_handoff_is_exact_generated_projection_and_stays_small() -> None:
    state = _state()
    rendered = documentation_state.render_handoff(state)

    assert HANDOFF_PATH.read_text(encoding="utf-8") == rendered
    assert 40 <= len(rendered.splitlines()) <= 60
    assert "this file is not the fact source" in rendered
    assert state["current_status"] in rendered
    assert state["next_required_checkpoint"] in rendered


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
    state["current_status"] = "changed_without_handoff_regeneration"
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
