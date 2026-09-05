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
    SCV2_PX3_CLOSURE_READY_STATUS,
    SCV2_PX3_MERGED_STATUS,
    SCV2_PX3_RESTORE_PROGRESS,
    SCV2_PX3_RESTORE_VERIFIED,
    load_state,
    render_handoff,
    validate_roadmaps,
    validate_state,
)


ROOT = Path(__file__).resolve().parents[1]


def _state() -> dict[str, object]:
    # Keep the accepted PX3 restrictions under regression; they are historical.
    from scripts.check_documentation_state import _trusted_git_value
    return json.loads(_trusted_git_value(ROOT, 'show',
        '26a6fc8d30ba2b2eae69f55a8e7c33d5a4b9cdd3:docs/state/current-phase.json'))


def test_current_handoff_is_exact_a1_projection() -> None:
    state = load_state(ROOT / 'docs/state/current-phase.json')
    handoff = (ROOT / 'docs/current-handoff.md').read_text(encoding='utf-8')
    assert handoff == render_handoff(state)
    assert 55 <= len(handoff.splitlines()) <= 115
    assert state['phase_id'] == 'PRODUCTION-PIXIV-A1'
    assert 'PR #150 已合并' in handoff


def test_a1_state_and_active_docs_validate() -> None:
    state = load_state(ROOT / 'docs/state/current-phase.json')
    validate_state(state)
    validate_roadmaps(state)
    assert state['previous_phase_pr_number'] == 150
    assert state['safe_to_merge'] is False
    assert state['route_approved'] is False
    assert state['next_phase_started'] is False


@pytest.mark.parametrize('field', ['merge','provider_network','llm','truth_mutation','original_file_mutation'])
def test_a1_authority_cannot_expand(field):
    state = load_state(ROOT / 'docs/state/current-phase.json')
    state['authorities'][field] = True
    with pytest.raises(DocumentationStateError, match='forbidden'):
        validate_state(state)


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
    state["protected_evidence"][field] = (not state['protected_evidence'][field]) if isinstance(value, bool) else value
    if field == 'existing_db_or_app_storage_activity':
        state['protected_evidence'][field] = _state()['protected_evidence'][field] + 1
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
    state["authorities"][field] = not state['authorities'][field]
    with pytest.raises(DocumentationStateError, match="authority_map"):
        validate_state(state)


def test_due_gate_set_cannot_be_omitted() -> None:
    state = copy.deepcopy(_state())
    assert {item["id"] for item in state["deferred_debt"]} == (
        SCV2_PX1_REQUIRED_DEFERRED_GATES | ({'SCV2_PX3_MULTIWORKER_APPLY_GATE'} if state['current_status'] in {SCV2_PX3_CLOSURE_READY_STATUS, SCV2_PX3_MERGED_STATUS, SCV2_PX3_RESTORE_PROGRESS, SCV2_PX3_RESTORE_VERIFIED} else set())
        | ({'SCV2_PX3_METADATA_REFRESH_BINDING_GATE', 'SCV2_PX3_POLICY_VERSION_CAPTURE_GATE'} if state.get('restored_canary') else set())
    )
    state["deferred_debt"].pop()
    with pytest.raises(DocumentationStateError, match="deferred_due_gate_set"):
        validate_state(state)


@pytest.mark.parametrize('field', [
    'media_binding_contract_passed', 'accepted_plan_exact_match_passed',
    'actual_search_and_detail_passed', 'rollback_ownership_and_cache_passed',
    'synthetic_edge_browser_passed', 'backup_restore_before_normal_startup_stop_recorded',
])
def test_final_product_closure_cannot_omit_independent_gate(field):
    state = copy.deepcopy(_state())
    state.pop('restored_canary', None)
    state['branch'] = 'codex/scv2-px3-pixiv-product-integration'
    state['pr_number'] = 149
    state.update(current_status=SCV2_PX3_CLOSURE_READY_STATUS, target_met=True, safe_to_merge=True,
                 manual_acceptance_status='owner_accepted_final_bounded_product_closure',
                 px3_target_met=True, px3_owner_accepted=True, px3_merged=False,
                 three_phase_implementation_route_completed=False,
                 conditional_expected_head_merge_authorized=True)
    state['authorities']['px3_merge_authorized'] = True
    for key in ('controlled_canary_authorized', 'real_pixiv_network_execution_authorized',
                'provider_credentials_authorized', 'existing_database_or_app_storage_access_authorized',
                'real_source_or_icloud_access_authorized', 'user_data_import_authorized',
                'production_authorized', 'full_library_import_authorized'):
        state[key] = False
    state['closure_verification'] = {key: True for key in (
        'media_binding_contract_passed', 'accepted_plan_exact_match_passed',
        'actual_search_and_detail_passed', 'rollback_ownership_and_cache_passed',
        'synthetic_edge_browser_passed', 'backup_restore_before_normal_startup_stop_recorded')}
    state['closure_verification'][field] = False
    with pytest.raises(DocumentationStateError, match='closure_evidence_missing'):
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


def test_restored_copy_authority_cannot_expand_to_original_writes():
    state = copy.deepcopy(_state())
    if not state.get('restored_canary'):
        pytest.skip('Historical PX3 state')
    state['restored_canary']['original_database_write_authorized'] = True
    with pytest.raises(DocumentationStateError, match='restored_canary_authority_map'):
        validate_state(state)


def test_restored_verified_checkpoint_requires_rollback_evidence():
    state = copy.deepcopy(_state())
    if not state.get('restored_canary'):
        pytest.skip('Historical PX3 state')
    state['current_status'] = SCV2_PX3_RESTORE_VERIFIED
    state['restored_canary']['rollback_baseline_verified'] = False
    with pytest.raises(DocumentationStateError, match='restored_canary_evidence_missing:rollback'):
        validate_state(state)
