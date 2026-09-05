"""PX3 canary entrypoints stay precise and current execution stays blocked."""

from __future__ import annotations

import json

import pytest

from scripts.plan_scv2_px3_controlled_canary import GATES, build_plan, main


@pytest.mark.parametrize("gate", sorted(GATES))
def test_each_owner_gate_has_exact_entrypoint_and_stop_conditions(gate: str) -> None:
    plan = build_plan(gate=gate, canary_percent=3, work_limit=2)
    assert plan["gate"] == GATES[gate]
    assert plan["status"] == "blocked_current_authority_false"
    assert plan["current_execution_authorized"] is False
    assert plan["required_future_authorities"]
    assert plan["entrypoint"]
    assert "private_value_or_credential_exposure" in plan["stop_conditions"]
    assert plan["production_authorized"] is False
    assert plan["full_library_import_authorized"] is False
    assert len(plan["canonical_fingerprint"]) == 64


@pytest.mark.parametrize(
    ("percentage", "work_limit", "error"),
    [
        (0, 1, "percentage_invalid"),
        (6, 1, "percentage_invalid"),
        (False, 1, "percentage_invalid"),
        ("1", 1, "percentage_invalid"),
        (1, 0, "work_limit_invalid"),
        (1, 6, "work_limit_invalid"),
        (1, False, "work_limit_invalid"),
        (1, "1", "work_limit_invalid"),
    ],
)
def test_canary_bounds_fail_closed(
    percentage: object,
    work_limit: object,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        build_plan(gate="import-canary", canary_percent=percentage, work_limit=work_limit)


def test_execute_flag_only_emits_plan_and_returns_blocked(capsys) -> None:
    exit_code = main(["--gate", "provider-smoke", "--execute"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert payload["current_execution_authorized"] is False
    assert payload["status"] == "blocked_current_authority_false"


def test_provider_template_cannot_execute_an_unbounded_ingestion_command():
    plan = build_plan(gate='provider-smoke', canary_percent=1, work_limit=2)
    assert plan['executable_entrypoint_available'] is False
    assert plan['entrypoint'].startswith('BLOCKED:')


def test_apply_without_actual_dry_run_and_restore_overwrite_are_not_emitted():
    for gate in ('existing-db-canary', 'import-canary'):
        plan = build_plan(gate=gate, canary_percent=1, work_limit=1)
        assert plan['apply_entrypoint'] is None
        assert not plan.get('apply_request_ready')
    restore = build_plan(gate='backup-restore', canary_percent=1, work_limit=1)
    assert '--clean' not in restore['restore_entrypoint']
    assert '--single-transaction' in restore['restore_entrypoint']
