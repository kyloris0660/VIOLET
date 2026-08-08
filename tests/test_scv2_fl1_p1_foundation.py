"""Synthetic-only tests for the SCV2-FL1-P1 safety and ledger foundation."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.fl1_p1_foundation import (
    EnvironmentIdentity,
    FL1LedgerRunner,
    InterruptedMutationOutcome,
    IsolationConfig,
    IsolationError,
    ItemState,
    JsonLedgerStore,
    LedgerError,
    MutationDenied,
    MutationPolicy,
    StableInventoryItem,
    build_contract_summary,
    validate_isolation,
)
from scripts.phase_contracts import check_phase_contract, get_contract


SYNTHETIC_HEAD = "a" * 40


def _layout(tmp_path: Path) -> dict[str, Path]:
    sandbox = tmp_path / "fl1-sandbox"
    source = sandbox / "source"
    storage = sandbox / "storage"
    database_dir = sandbox / "database"
    forbidden = tmp_path / "production-denylist"
    for path in (source, storage, database_dir, forbidden):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "sandbox": sandbox,
        "source": source,
        "storage": storage,
        "database_dir": database_dir,
        "forbidden": forbidden,
    }


def _config(tmp_path: Path, **overrides: object) -> IsolationConfig:
    paths = _layout(tmp_path)
    config = IsolationConfig(
        environment=EnvironmentIdentity.TEST,
        database_identity="violet_fl1_test_synthetic",
        database_path=(paths["database_dir"] / "violet_fl1_test_synthetic.db"),
        sandbox_root=paths["sandbox"],
        source_root=paths["source"],
        storage_root=paths["storage"],
        forbidden_roots=(paths["forbidden"],),
        actual_git_head=SYNTHETIC_HEAD,
        expected_git_head=SYNTHETIC_HEAD,
        python_executable=Path(sys.executable),
        expected_python=Path(sys.executable),
    )
    return replace(config, **overrides)


def _item(index: int, *, parent: str = "synthetic-parent") -> StableInventoryItem:
    return StableInventoryItem.create(
        parent_identity=parent,
        content_fingerprint=f"{index:064x}",
    )


def _policy(
    storage_root: Path,
    forbidden_root: Path,
    *,
    operations: frozenset[str] = frozenset(
        {"ledger.write", "synthetic.item.process"}
    ),
) -> MutationPolicy:
    return MutationPolicy(
        environment=EnvironmentIdentity.TEST,
        allowed_root=storage_root,
        forbidden_roots=(forbidden_root,),
        allowed_operations=operations,
    )


def _runner(
    tmp_path: Path,
    items: list[StableInventoryItem],
    *,
    run_id: str = "synthetic-run",
    max_attempts_per_item: int = 3,
    max_failure_attempts: int = 5,
    batch_size: int = 10,
    operations: frozenset[str] = frozenset(
        {"ledger.write", "synthetic.item.process"}
    ),
) -> tuple[FL1LedgerRunner, JsonLedgerStore]:
    paths = _layout(tmp_path)
    policy = _policy(paths["storage"], paths["forbidden"], operations=operations)
    store = JsonLedgerStore(paths["storage"] / "ledger.json", policy)
    runner = FL1LedgerRunner(
        store=store,
        mutation_policy=policy,
        mutation_target=paths["storage"] / "synthetic-output",
        run_id=run_id,
        items=items,
        max_attempts_per_item=max_attempts_per_item,
        max_failure_attempts=max_failure_attempts,
        batch_size=batch_size,
    )
    return runner, store


def test_explicit_test_identity_and_synthetic_paths_pass(tmp_path: Path) -> None:
    proof = validate_isolation(_config(tmp_path))

    assert proof.environment == "test"
    assert proof.database_identity == "violet_fl1_test_synthetic"
    assert proof.database_path_new_and_contained is True
    assert proof.source_storage_non_overlapping is True
    assert proof.production_fallback_used is False
    assert proof.existing_database_accessed is False


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX venv launchers use symlinks"
)
def test_python_identity_does_not_collapse_a_venv_symlink_to_base_python(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "venv-python"
    launcher.symlink_to(Path(sys.executable))

    with pytest.raises(IsolationError, match="python_identity_mismatch"):
        validate_isolation(
            _config(
                tmp_path,
                python_executable=Path(sys.executable),
                expected_python=launcher,
            )
        )


@pytest.mark.parametrize(
    ("identity", "error"),
    [
        (EnvironmentIdentity.PRODUCTION, "production_environment_identity_rejected"),
        ("unknown", "unknown_environment_identity"),
    ],
)
def test_production_and_unknown_identity_are_rejected(
    tmp_path: Path, identity: EnvironmentIdentity | str, error: str
) -> None:
    with pytest.raises(IsolationError, match=error):
        validate_isolation(_config(tmp_path, environment=identity))


def test_database_identity_must_match_test_or_dev_segment(tmp_path: Path) -> None:
    with pytest.raises(
        IsolationError, match="database_identity_not_segmented_for_environment"
    ):
        validate_isolation(
            _config(tmp_path, database_identity="violet_fl1_dev_synthetic")
        )


def test_source_or_storage_outside_sandbox_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside-source"
    outside.mkdir()

    with pytest.raises(IsolationError, match="source_or_storage_outside_sandbox"):
        validate_isolation(_config(tmp_path, source_root=outside))


def test_source_storage_overlap_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with pytest.raises(IsolationError, match="source_storage_overlap"):
        validate_isolation(config=replace(config, storage_root=config.source_root))


def test_production_or_existing_database_path_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.database_path.write_text("synthetic placeholder", encoding="utf-8")

    with pytest.raises(IsolationError, match="existing_database_path_rejected"):
        validate_isolation(config)


def test_explicit_config_does_not_fall_back_to_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIOLET_ENV", "production")
    monkeypatch.setenv("POSTGRES_DB", "blombooru")
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(tmp_path / "unapproved"))

    proof = validate_isolation(_config(tmp_path))

    assert proof.environment == "test"
    assert proof.production_fallback_used is False


def test_mutation_policy_is_default_deny_and_source_is_always_forbidden(
    tmp_path: Path,
) -> None:
    paths = _layout(tmp_path)
    target = paths["storage"] / "synthetic-output"
    deny = _policy(paths["storage"], paths["forbidden"], operations=frozenset())

    with pytest.raises(MutationDenied, match="mutation_not_allowlisted"):
        deny.assert_allowed("synthetic.item.process", target)

    source_misconfigured_as_allowed = _policy(
        paths["storage"],
        paths["forbidden"],
        operations=frozenset({"source.write"}),
    )
    with pytest.raises(MutationDenied, match="forbidden_mutation_surface"):
        source_misconfigured_as_allowed.assert_allowed("source.write", target)


def test_mutation_target_must_stay_inside_allowlisted_storage(tmp_path: Path) -> None:
    paths = _layout(tmp_path)
    policy = _policy(paths["storage"], paths["forbidden"])

    with pytest.raises(MutationDenied, match="mutation_target_outside_allowed_root"):
        policy.assert_allowed("synthetic.item.process", paths["sandbox"] / "escape")


def test_ledger_store_cannot_read_from_outside_allowlisted_storage(
    tmp_path: Path,
) -> None:
    paths = _layout(tmp_path)
    policy = _policy(paths["storage"], paths["forbidden"])

    with pytest.raises(MutationDenied, match="mutation_target_outside_allowed_root"):
        JsonLedgerStore(tmp_path / "outside" / "ledger.json", policy)


def test_duplicate_item_does_not_trigger_second_mutation(tmp_path: Path) -> None:
    item = _item(1)
    runner, store = _runner(tmp_path, [item, item])
    calls: list[str] = []

    result = runner.run_next_batch(lambda current: calls.append(current.item_id))
    ledger = store.load()

    assert calls == [item.item_id]
    assert result.completed is True
    assert result.duplicate_skipped == 1
    assert ledger.duplicate_entry_count == 1
    assert ledger.items[item.item_id].mutation_count == 1


def test_same_content_under_different_parents_uses_one_logical_mutation(
    tmp_path: Path,
) -> None:
    first = _item(101, parent="parent-a")
    duplicate = _item(101, parent="parent-b")
    runner, store = _runner(tmp_path, [first, duplicate])
    calls: list[str] = []

    result = runner.run_next_batch(lambda current: calls.append(current.item_id))
    ledger = store.load()

    assert calls == [first.item_id]
    assert result.completed is True
    assert result.duplicate_skipped == 1
    assert ledger.source_item_count == 2
    assert ledger.unique_item_count == 1
    assert ledger.duplicate_entry_count == 1
    assert ledger.content_duplicate_item_count == 1
    assert ledger.items[duplicate.item_id].state is ItemState.DUPLICATE
    assert ledger.items[duplicate.item_id].duplicate_of_item_id == first.item_id


def test_restart_recovers_interrupted_item_without_resetting_attempts(
    tmp_path: Path,
) -> None:
    item = _item(2)
    first_runner, store = _runner(tmp_path, [item])

    class AbruptInterruption(BaseException):
        pass

    with pytest.raises(AbruptInterruption):
        first_runner.run_next_batch(
            lambda _current: (_ for _ in ()).throw(AbruptInterruption())
        )

    interrupted = store.load()
    assert interrupted.items[item.item_id].state is ItemState.IN_PROGRESS
    assert interrupted.items[item.item_id].attempt_count == 1

    restarted_runner, _ = _runner(tmp_path, [item])
    result = restarted_runner.run_next_batch(
        lambda _current: None,
        reconcile_interrupted=lambda _current: (
            InterruptedMutationOutcome.NOT_COMMITTED
        ),
    )
    recovered = store.load()

    assert result.completed is True
    assert recovered.recovery_count == 1
    assert recovered.items[item.item_id].attempt_count == 2
    assert recovered.items[item.item_id].failure_count == 1
    assert recovered.items[item.item_id].mutation_count == 1


def test_interrupted_post_effect_mutation_requires_reconciliation_and_is_not_replayed(
    tmp_path: Path,
) -> None:
    item = _item(102)
    first_runner, store = _runner(tmp_path, [item])
    effects: list[str] = []

    class AbruptInterruption(BaseException):
        pass

    def mutate_then_interrupt(current: StableInventoryItem) -> None:
        effects.append(current.item_id)
        raise AbruptInterruption()

    with pytest.raises(AbruptInterruption):
        first_runner.run_next_batch(mutate_then_interrupt)

    restarted, _ = _runner(tmp_path, [item])
    with pytest.raises(LedgerError, match="interrupted_mutation_reconciliation_required"):
        restarted.run_next_batch(lambda current: effects.append(current.item_id))
    assert effects == [item.item_id]

    result = restarted.run_next_batch(
        lambda current: effects.append(current.item_id),
        reconcile_interrupted=lambda _current: InterruptedMutationOutcome.COMMITTED,
    )
    recovered = store.load()

    assert result.completed is True
    assert effects == [item.item_id]
    assert recovered.items[item.item_id].state is ItemState.SUCCEEDED
    assert recovered.items[item.item_id].attempt_count == 1
    assert recovered.items[item.item_id].mutation_count == 1


def test_attempt_and_failure_budget_stop_future_mutations(tmp_path: Path) -> None:
    item = _item(3)
    runner, store = _runner(
        tmp_path,
        [item],
        max_attempts_per_item=2,
        max_failure_attempts=2,
    )
    calls = 0

    def fail(_current: StableInventoryItem) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("synthetic_failure")

    first = runner.run_next_batch(fail)
    second = runner.run_next_batch(fail)
    third = runner.run_next_batch(fail)
    ledger = store.load()

    assert first.stopped_for_failure_budget is False
    assert second.stopped_for_failure_budget is True
    assert third.attempted == 0
    assert calls == 2
    assert ledger.total_failure_attempts == 2
    assert ledger.items[item.item_id].attempt_count == 2
    assert ledger.items[item.item_id].state is ItemState.FAILED_EXHAUSTED


def test_per_item_exhaustion_does_not_consume_the_global_run_budget(
    tmp_path: Path,
) -> None:
    failed_item = _item(103)
    later_item = _item(104)
    runner, store = _runner(
        tmp_path,
        [failed_item, later_item],
        max_attempts_per_item=1,
        max_failure_attempts=10,
    )

    first = runner.run_next_batch(
        lambda _current: (_ for _ in ()).throw(RuntimeError("synthetic_failure"))
    )
    calls: list[str] = []
    second = runner.run_next_batch(lambda current: calls.append(current.item_id))
    ledger = store.load()

    assert first.stopped_for_failure_budget is False
    assert second.completed is True
    assert calls == [later_item.item_id]
    assert ledger.failure_budget_exhausted is False
    assert ledger.items[failed_item.item_id].state is ItemState.FAILED_EXHAUSTED
    assert ledger.items[later_item.item_id].state is ItemState.SUCCEEDED


def test_stale_writer_cannot_overwrite_a_newer_ledger_generation(
    tmp_path: Path,
) -> None:
    item = _item(105)
    runner, store = _runner(tmp_path, [item])
    runner.run_next_batch(lambda _current: None)
    first = store.load()
    stale = store.load()

    first.manual_stop_requested = True
    store.save(first)
    stale.manual_stop_requested = True

    with pytest.raises(LedgerError, match="ledger_generation_conflict"):
        store.save(stale)


def test_valid_json_with_inconsistent_failure_accounting_fails_closed(
    tmp_path: Path,
) -> None:
    item = _item(106)
    runner, store = _runner(tmp_path, [item])
    runner.run_next_batch(lambda _current: None)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["checkpoint"]["total_failure_attempts"] = 1
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LedgerError, match="failure_attempt_accounting_invalid"):
        store.load()


def test_valid_json_with_mismatched_logical_target_fails_closed(
    tmp_path: Path,
) -> None:
    item = _item(107)
    runner, store = _runner(tmp_path, [item])
    runner.run_next_batch(lambda _current: None)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["items"][item.item_id]["logical_target_id"] = "f" * 64
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LedgerError, match="logical_target_fingerprint_mismatch"):
        store.load()


def test_manual_stop_is_persisted_and_prevents_next_batch(tmp_path: Path) -> None:
    items = [_item(4), _item(5)]
    runner, store = _runner(tmp_path, items, batch_size=2)
    calls: list[str] = []

    first = runner.run_next_batch(
        lambda current: calls.append(current.item_id),
        stop_requested=lambda: len(calls) >= 1,
    )
    restarted, _ = _runner(tmp_path, items, batch_size=2)
    second = restarted.run_next_batch(lambda current: calls.append(current.item_id))
    ledger = store.load()

    assert first.stopped_for_manual_request is True
    assert second.attempted == 0
    assert calls == [items[0].item_id]
    assert ledger.manual_stop_requested is True
    assert ledger.next_index == 1


def test_manifest_or_limit_drift_fails_closed_on_restart(tmp_path: Path) -> None:
    first, _ = _runner(tmp_path, [_item(6)])
    first.run_next_batch(lambda _current: None)
    drifted, _ = _runner(tmp_path, [_item(7)])

    with pytest.raises(LedgerError, match="manifest_restart_mismatch"):
        drifted.run_next_batch(lambda _current: None)


def test_contract_summary_is_public_safe_and_owner_audit_blocked(tmp_path: Path) -> None:
    config = _config(tmp_path)
    proof = validate_isolation(config)
    item = _item(8)
    runner, store = _runner(tmp_path, [item])
    runner.run_next_batch(lambda _current: None)

    summary = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        focused_tests_passed=True,
        full_non_e2e_passed=True,
    )

    assert summary["pipeline_contract"] == {
        "contract_id": "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        "status": "implementation_ready_for_owner_audit",
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "active_blockers": ["pending_owner_audit"],
        "owner_acceptance_identity": None,
    }
    assert all(value == 0 for value in summary["operation_counts"].values())
    assert summary["environment_isolation"]["synthetic_only"] is True
    assert "source_root" not in summary["environment_isolation"]

    contract = get_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1"
    )
    result = check_phase_contract(contract.contract_id, summary)
    assert contract.phase_kind == "scv2_fl1_p1_isolation_safety_ledger_foundation"
    assert result.passed is True

    accepted = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        focused_tests_passed=True,
        full_non_e2e_passed=True,
        owner_acceptance_identity="owner_accepted_fl1_p1_20260808",
    )
    accepted_result = check_phase_contract(contract.contract_id, accepted)
    assert accepted_result.passed is True
    assert accepted["pipeline_contract"] == {
        "contract_id": "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        "status": "owner_accepted_for_merge",
        "target_met": True,
        "safe_to_merge": True,
        "route_approved": True,
        "active_blockers": [],
        "owner_acceptance_identity": "owner_accepted_fl1_p1_20260808",
    }

    invalid_authorization = copy.deepcopy(summary)
    invalid_authorization["authorization"]["production_authorized"] = True
    failed = check_phase_contract(contract.contract_id, invalid_authorization)
    assert failed.passed is False
    assert "fl1_p1_authorization_boundary_invalid" in {
        finding.code for finding in failed.errors
    }

    invalid_ledger = copy.deepcopy(summary)
    invalid_ledger["ledger"]["duplicate_second_mutation_count"] = 1
    failed = check_phase_contract(contract.contract_id, invalid_ledger)
    assert failed.passed is False
    assert "fl1_p1_ledger_invalid" in {finding.code for finding in failed.errors}

    invalid_operation_count = copy.deepcopy(summary)
    invalid_operation_count["operation_counts"]["provider_activity"] = 1
    failed = check_phase_contract(contract.contract_id, invalid_operation_count)
    assert failed.passed is False
    assert "fl1_p1_forbidden_activity" in {
        finding.code for finding in failed.errors
    }
