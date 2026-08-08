"""Synthetic-only tests for the SCV2-FL1-P1 safety and ledger foundation."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.fl1_p1_foundation import (
    AuthorizationKind,
    BoundAuthorizationEvidence,
    EnvironmentIdentity,
    FL1LedgerRunner,
    GlobalStopReason,
    ImplementationEvidence,
    InterruptedMutationOutcome,
    IsolationConfig,
    IsolationError,
    ItemTerminalReason,
    ItemState,
    JsonLedgerStore,
    LedgerError,
    MutationDenied,
    MutationNotCommittedError,
    MutationPolicy,
    OperationKind,
    OwnerAcceptanceEvidence,
    ReconciliationStatus,
    REQUIRED_EXECUTED_STAGES,
    StableInventoryItem,
    build_contract_summary,
    collect_implementation_evidence,
    validate_isolation,
)
from scripts.phase_contracts import check_phase_contract, get_contract


SYNTHETIC_HEAD = "a" * 40
SYNTHETIC_TREE = "b" * 40


def _implementation_evidence() -> ImplementationEvidence:
    return ImplementationEvidence.create(
        implementation_commit=SYNTHETIC_HEAD,
        implementation_tree=SYNTHETIC_TREE,
        final_commit=SYNTHETIC_HEAD,
        final_tree=SYNTHETIC_TREE,
        post_implementation_changed_paths=(),
    )


def _owner_acceptance(
    evidence: ImplementationEvidence,
) -> OwnerAcceptanceEvidence:
    return OwnerAcceptanceEvidence(
        identity="owner_acceptance_synthetic",
        implementation_commit=evidence.implementation_commit,
        implementation_tree=evidence.implementation_tree,
        implementation_digest=evidence.evidence_digest,
        reviewed_final_commit=evidence.final_commit,
        reviewed_final_tree=evidence.final_tree,
    )


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
        ("production", "production_environment_identity_rejected"),
        (" PRODUCTION ", "production_environment_identity_rejected"),
        ("unknown", "unknown_environment_identity"),
        (" UNKNOWN ", "unknown_environment_identity"),
        ("fallback", "unknown_environment_identity"),
        ("", "unknown_environment_identity"),
        (None, "unknown_environment_identity"),
        ("staging", "unknown_environment_identity"),
    ],
)
def test_production_and_unknown_identity_are_rejected(
    tmp_path: Path, identity: EnvironmentIdentity | str | None, error: str
) -> None:
    with pytest.raises(IsolationError, match=error):
        validate_isolation(_config(tmp_path, environment=identity))


@pytest.mark.parametrize(
    "identity", [EnvironmentIdentity.TEST, "test", " TEST ", "development"]
)
def test_structured_and_string_synthetic_identity_have_the_same_semantics(
    tmp_path: Path, identity: EnvironmentIdentity | str
) -> None:
    config = _config(tmp_path, environment=identity)
    identity_text = identity.value if isinstance(identity, EnvironmentIdentity) else identity
    expected = "development" if identity_text.strip().casefold() in {
        "development",
        "dev",
    } else "test"
    if expected == "development":
        config = replace(
            config,
            database_identity="violet_fl1_dev_synthetic",
            database_path=config.database_path.with_name(
                "violet_fl1_dev_synthetic.db"
            ),
        )

    proof = validate_isolation(config)
    policy = MutationPolicy(
        environment=identity,
        allowed_root=config.storage_root,
        forbidden_roots=config.forbidden_roots,
        allowed_operations=frozenset({"synthetic.item.process"}),
    )
    policy.assert_allowed(
        "synthetic.item.process", config.storage_root / "synthetic-output"
    )

    assert proof.environment == expected


@pytest.mark.parametrize(
    "identity",
    ["production", " PRODUCTION ", "unknown", "fallback", "", "staging"],
)
def test_mutation_policy_rejects_every_non_synthetic_string_identity(
    tmp_path: Path, identity: str
) -> None:
    paths = _layout(tmp_path)
    policy = MutationPolicy(
        environment=identity,
        allowed_root=paths["storage"],
        forbidden_roots=(paths["forbidden"],),
        allowed_operations=frozenset({"synthetic.item.process"}),
    )

    with pytest.raises(MutationDenied):
        policy.assert_allowed(
            "synthetic.item.process", paths["storage"] / "synthetic-output"
        )


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


@pytest.mark.parametrize("relationship", ["equal_source", "inside_source", "inside_storage"])
def test_database_path_must_be_disjoint_from_source_and_storage(
    tmp_path: Path, relationship: str
) -> None:
    config = _config(tmp_path)
    if relationship == "equal_source":
        database_path = config.source_root
    elif relationship == "inside_source":
        database_path = config.source_root / "nested" / "synthetic.db"
    else:
        database_path = config.storage_root / "nested" / "synthetic.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with pytest.raises(IsolationError, match="database_source_storage_overlap"):
        validate_isolation(replace(config, database_path=database_path))


def test_source_cannot_be_nested_inside_database_root(tmp_path: Path) -> None:
    config = _config(tmp_path)
    database_root = config.sandbox_root / "database-root"
    source = database_root / "source"
    source.mkdir(parents=True)

    with pytest.raises(IsolationError, match="database_source_storage_overlap"):
        validate_isolation(
            replace(
                config,
                database_path=database_root / "synthetic.db",
                source_root=source,
            )
        )


def test_resolved_path_alias_cannot_hide_database_overlap(tmp_path: Path) -> None:
    config = _config(tmp_path)
    aliased = config.source_root / "child" / ".." / "synthetic.db"

    with pytest.raises(IsolationError, match="database_source_storage_overlap"):
        validate_isolation(replace(config, database_path=aliased))


def test_symlink_alias_cannot_hide_database_overlap(tmp_path: Path) -> None:
    config = _config(tmp_path)
    alias = config.sandbox_root / "source-alias"
    try:
        alias.symlink_to(config.source_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this host")

    with pytest.raises(IsolationError, match="database_source_storage_overlap"):
        validate_isolation(
            replace(config, database_path=alias / "violet_fl1_test_synthetic.db")
        )


def test_sibling_synthetic_roots_are_pairwise_disjoint(tmp_path: Path) -> None:
    proof = validate_isolation(_config(tmp_path))

    assert proof.database_source_storage_pairwise_disjoint is True


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

    restarted, _ = _runner(tmp_path, [first, duplicate])
    restarted_result = restarted.run_next_batch(
        lambda current: calls.append(current.item_id)
    )
    assert restarted_result.attempted == 0
    assert calls == [first.item_id]
    assert store.load().items[duplicate.item_id].terminal_reason is (
        ItemTerminalReason.DUPLICATE_LOGICAL_TARGET
    )


def test_distinct_logical_targets_each_mutate_once(tmp_path: Path) -> None:
    first = _item(108, parent="parent-a")
    second = _item(109, parent="parent-a")
    runner, store = _runner(tmp_path, [first, second])
    calls: list[str] = []

    result = runner.run_next_batch(lambda current: calls.append(current.item_id))

    assert result.completed is True
    assert calls == [first.item_id, second.item_id]
    assert store.load().duplicate_second_mutation_count == 0


def test_ledger_rejects_two_mutations_for_one_logical_target(
    tmp_path: Path,
) -> None:
    first = _item(110, parent="parent-a")
    duplicate = _item(110, parent="parent-b")
    runner, store = _runner(tmp_path, [first, duplicate])
    runner.run_next_batch(lambda _current: None)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    duplicate_record = payload["items"][duplicate.item_id]
    duplicate_record.update(
        {
            "state": "succeeded",
            "attempt_count": 1,
            "mutation_count": 1,
            "duplicate_of_item_id": None,
            "reconciliation_status": "committed",
            "terminal_reason": None,
        }
    )
    payload["operation_evidence"]["events"].append(
        {
            "sequence": 2,
            "kind": "synthetic_mutation_invocation",
            "item_id": duplicate.item_id,
        }
    )
    payload["operation_evidence"]["event_count"] = 2
    payload["operation_evidence"]["fingerprint"] = "0" * 64
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LedgerError, match="logical_target_multiple_mutations"):
        store.load()


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


def test_ordinary_exception_after_side_effect_becomes_outcome_unknown(
    tmp_path: Path,
) -> None:
    item = _item(111)
    runner, store = _runner(tmp_path, [item])
    effects: list[str] = []

    def mutate_then_raise(current: StableInventoryItem) -> None:
        effects.append(current.item_id)
        raise RuntimeError("post_effect_failure")

    with pytest.raises(LedgerError, match="mutation_outcome_reconciliation_required"):
        runner.run_next_batch(mutate_then_raise)

    unknown = store.load().items[item.item_id]
    assert unknown.state is ItemState.OUTCOME_UNKNOWN
    assert unknown.reconciliation_status is ReconciliationStatus.REQUIRED
    assert unknown.mutation_count == 0
    assert effects == [item.item_id]

    restarted, _ = _runner(tmp_path, [item])
    with pytest.raises(LedgerError, match="reconciliation_required"):
        restarted.run_next_batch(lambda current: effects.append(current.item_id))
    assert effects == [item.item_id]


def test_unknown_reconciliation_remains_fail_closed_across_restart(
    tmp_path: Path,
) -> None:
    item = _item(112)
    runner, store = _runner(tmp_path, [item])

    with pytest.raises(LedgerError, match="mutation_outcome_reconciliation_required"):
        runner.run_next_batch(
            lambda _current: (_ for _ in ()).throw(RuntimeError("unknown"))
        )

    restarted, _ = _runner(tmp_path, [item])
    with pytest.raises(LedgerError, match="interrupted_mutation_outcome_unknown"):
        restarted.run_next_batch(
            lambda _current: pytest.fail("must not replay"),
            reconcile_interrupted=lambda _current: InterruptedMutationOutcome.UNKNOWN,
        )
    persisted = store.load().items[item.item_id]
    assert persisted.state is ItemState.OUTCOME_UNKNOWN
    assert persisted.reconciliation_status is ReconciliationStatus.REQUIRED
    assert persisted.attempt_count == 1


def test_only_not_committed_reconciliation_allows_budgeted_retry(
    tmp_path: Path,
) -> None:
    item = _item(113)
    runner, store = _runner(tmp_path, [item], max_attempts_per_item=2)
    effects: list[str] = []

    with pytest.raises(LedgerError, match="mutation_outcome_reconciliation_required"):
        runner.run_next_batch(
            lambda current: (
                effects.append(current.item_id),
                (_ for _ in ()).throw(RuntimeError("unknown")),
            )
        )

    restarted, _ = _runner(tmp_path, [item], max_attempts_per_item=2)
    result = restarted.run_next_batch(
        lambda current: effects.append(current.item_id),
        reconcile_interrupted=lambda _current: InterruptedMutationOutcome.NOT_COMMITTED,
    )
    ledger = store.load()

    assert result.completed is True
    assert effects == [item.item_id, item.item_id]
    assert ledger.items[item.item_id].attempt_count == 2
    assert ledger.items[item.item_id].mutation_count == 1


def test_pre_invocation_policy_failure_creates_no_attempt_or_event(
    tmp_path: Path,
) -> None:
    item = _item(114)
    runner, store = _runner(tmp_path, [item], operations=frozenset({"ledger.write"}))

    with pytest.raises(MutationDenied, match="mutation_not_allowlisted"):
        runner.run_next_batch(lambda _current: None)
    ledger = store.load()

    assert ledger.items[item.item_id].attempt_count == 0
    assert ledger.operation_events == []


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
        raise MutationNotCommittedError("synthetic_failure")

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
        lambda _current: (_ for _ in ()).throw(
            MutationNotCommittedError("synthetic_failure")
        )
    )
    calls: list[str] = []
    second = runner.run_next_batch(lambda current: calls.append(current.item_id))
    ledger = store.load()

    assert first.stopped_for_failure_budget is False
    assert second.completed is True
    assert calls == [later_item.item_id]
    assert ledger.failure_budget_exhausted is False
    assert ledger.global_stop_reason is None
    assert ledger.items[failed_item.item_id].state is ItemState.FAILED_EXHAUSTED
    assert ledger.items[failed_item.item_id].terminal_reason is (
        ItemTerminalReason.ITEM_ATTEMPT_BUDGET_EXHAUSTED
    )
    assert ledger.items[later_item.item_id].state is ItemState.SUCCEEDED


def test_per_item_exhaustion_survives_restart_without_poisoning_later_item(
    tmp_path: Path,
) -> None:
    failed_item = _item(115)
    later_item = _item(116)
    first, store = _runner(
        tmp_path,
        [failed_item, later_item],
        max_attempts_per_item=1,
        max_failure_attempts=10,
    )
    first.run_next_batch(
        lambda _current: (_ for _ in ()).throw(
            MutationNotCommittedError("not_committed")
        )
    )

    restarted, _ = _runner(
        tmp_path,
        [failed_item, later_item],
        max_attempts_per_item=1,
        max_failure_attempts=10,
    )
    calls: list[str] = []
    result = restarted.run_next_batch(lambda current: calls.append(current.item_id))
    ledger = store.load()

    assert result.completed is True
    assert calls == [later_item.item_id]
    assert ledger.global_stop_reason is None
    assert ledger.items[failed_item.item_id].terminal_reason is (
        ItemTerminalReason.ITEM_ATTEMPT_BUDGET_EXHAUSTED
    )


def test_global_failure_budget_is_distinct_and_stops_later_items(
    tmp_path: Path,
) -> None:
    items = [_item(117), _item(118), _item(119)]
    runner, store = _runner(
        tmp_path,
        items,
        max_attempts_per_item=1,
        max_failure_attempts=2,
    )
    calls: list[str] = []

    def fail_not_committed(current: StableInventoryItem) -> None:
        calls.append(current.item_id)
        raise MutationNotCommittedError("not_committed")

    runner.run_next_batch(fail_not_committed)
    second = runner.run_next_batch(fail_not_committed)
    third = runner.run_next_batch(fail_not_committed)
    ledger = store.load()

    assert second.stopped_for_failure_budget is True
    assert third.attempted == 0
    assert calls == [items[0].item_id, items[1].item_id]
    assert ledger.failure_budget_exhausted is True
    assert ledger.global_stop_reason is GlobalStopReason.FAILURE_BUDGET_EXHAUSTED
    assert ledger.items[items[0].item_id].state is ItemState.FAILED_EXHAUSTED
    assert ledger.items[items[1].item_id].state is ItemState.FAILED_EXHAUSTED
    assert ledger.items[items[2].item_id].state is ItemState.PENDING


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
        implementation_evidence=_implementation_evidence(),
        focused_tests_passed=True,
        full_non_e2e_passed=True,
    )

    pipeline = summary["pipeline_contract"]
    assert pipeline["status"] == "implementation_ready_for_owner_audit"
    assert pipeline["target_met"] is False
    assert pipeline["safe_to_merge"] is False
    assert pipeline["route_approved"] is False
    assert pipeline["active_blockers"] == ["pending_owner_audit"]
    assert pipeline["owner_acceptance_evidence"] is None
    assert all(value == 0 for value in summary["operation_counts"].values())
    assert summary["environment_isolation"]["synthetic_only"] is True
    assert "source_root" not in summary["environment_isolation"]
    assert summary["executed_stages"] == list(REQUIRED_EXECUTED_STAGES)
    assert summary["missing_required_stages"] == []

    contract = get_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1"
    )
    result = check_phase_contract(contract.contract_id, summary)
    assert contract.phase_kind == "scv2_fl1_p1_isolation_safety_ledger_foundation"
    assert result.passed is True


def test_owner_acceptance_is_bound_but_does_not_authorize_merge_or_route(
    tmp_path: Path,
) -> None:
    proof = validate_isolation(_config(tmp_path))
    runner, store = _runner(tmp_path, [_item(120)])
    runner.run_next_batch(lambda _current: None)
    evidence = _implementation_evidence()
    acceptance = _owner_acceptance(evidence)

    accepted = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=evidence,
        focused_tests_passed=True,
        full_non_e2e_passed=True,
        owner_acceptance=acceptance,
    )
    accepted_result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", accepted
    )
    assert accepted_result.passed is True
    pipeline = accepted["pipeline_contract"]
    assert pipeline["status"] == "owner_accepted_pending_merge_authorization"
    assert pipeline["target_met"] is True
    assert pipeline["safe_to_merge"] is False
    assert pipeline["route_approved"] is False
    assert pipeline["active_blockers"] == ["pending_merge_authorization"]

    merge = BoundAuthorizationEvidence(
        kind=AuthorizationKind.MERGE,
        identity="owner_merge_authorization",
        owner_acceptance_identity=acceptance.identity,
        reviewed_final_commit=evidence.final_commit,
        reviewed_final_tree=evidence.final_tree,
    )
    merge_ready = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=evidence,
        focused_tests_passed=True,
        full_non_e2e_passed=True,
        owner_acceptance=acceptance,
        merge_authorization=merge,
    )
    assert merge_ready["pipeline_contract"]["safe_to_merge"] is True
    assert merge_ready["pipeline_contract"]["route_approved"] is False


@pytest.mark.parametrize(
    "mutator",
    [
        lambda summary: summary["pipeline_contract"].update({"accepted": True}),
        lambda summary: summary["pipeline_contract"].update({"target_met": True}),
        lambda summary: summary["pipeline_contract"].update(
            {"owner_acceptance_identity": "legacy_boolean_acceptance"}
        ),
    ],
)
def test_unbound_or_boolean_acceptance_fails_closed(
    tmp_path: Path, mutator: object
) -> None:
    proof = validate_isolation(_config(tmp_path))
    runner, store = _runner(tmp_path, [_item(121)])
    runner.run_next_batch(lambda _current: None)
    summary = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=_implementation_evidence(),
        focused_tests_passed=True,
        full_non_e2e_passed=True,
    )
    mutator(summary)  # type: ignore[operator]

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", summary
    )

    assert result.passed is False
    assert "fl1_p1_claim_invalid" in {finding.code for finding in result.errors}


def test_wrong_or_stale_owner_acceptance_binding_is_rejected(
    tmp_path: Path,
) -> None:
    proof = validate_isolation(_config(tmp_path))
    runner, store = _runner(tmp_path, [_item(122)])
    runner.run_next_batch(lambda _current: None)
    evidence = _implementation_evidence()
    wrong = replace(_owner_acceptance(evidence), implementation_commit="c" * 40)

    with pytest.raises(LedgerError, match="owner_acceptance_evidence_mismatch"):
        build_contract_summary(
            isolation=proof,
            ledger=store.load(),
            implementation_evidence=evidence,
            focused_tests_passed=True,
            full_non_e2e_passed=True,
            owner_acceptance=wrong,
        )

    summary = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=evidence,
        focused_tests_passed=True,
        full_non_e2e_passed=True,
        owner_acceptance=_owner_acceptance(evidence),
    )
    summary["implementation_evidence"]["evidence_digest"] = "0" * 64
    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", summary
    )
    assert result.passed is False
    assert {finding.code for finding in result.errors} >= {
        "fl1_p1_implementation_evidence_invalid",
        "fl1_p1_claim_invalid",
    }


def test_implementation_evidence_rejects_executable_drift() -> None:
    with pytest.raises(LedgerError, match="implementation_evidence_executable_drift"):
        ImplementationEvidence.create(
            implementation_commit="a" * 40,
            implementation_tree="b" * 40,
            final_commit="c" * 40,
            final_tree="d" * 40,
            post_implementation_changed_paths=("scripts/runtime.py",),
        )


def test_git_collected_evidence_allows_only_exact_governance_paths(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    git("init")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "user.name", "Synthetic Test")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "scripts/runtime.py")
    git("commit", "-m", "implementation")
    implementation_commit = git("rev-parse", "HEAD")

    (repo / "docs").mkdir()
    (repo / "docs" / "current-handoff.md").write_text("pending audit\n", encoding="utf-8")
    git("add", "docs/current-handoff.md")
    git("commit", "-m", "governance")
    governance = collect_implementation_evidence(
        repo_root=repo,
        implementation_commit=implementation_commit,
    )
    assert governance.post_implementation_changed_paths == (
        "docs/current-handoff.md",
    )

    (repo / "scripts" / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    git("add", "scripts/runtime.py")
    git("commit", "-m", "executable drift")
    with pytest.raises(LedgerError, match="implementation_evidence_executable_drift"):
        collect_implementation_evidence(
            repo_root=repo,
            implementation_commit=implementation_commit,
        )


def _passing_summary(tmp_path: Path, item_index: int = 123) -> tuple[dict, JsonLedgerStore]:
    proof = validate_isolation(_config(tmp_path))
    runner, store = _runner(tmp_path, [_item(item_index)])
    runner.run_next_batch(lambda _current: None)
    return (
        build_contract_summary(
            isolation=proof,
            ledger=store.load(),
            implementation_evidence=_implementation_evidence(),
            focused_tests_passed=True,
            full_non_e2e_passed=True,
        ),
        store,
    )


def test_forbidden_operation_counts_are_derived_from_persisted_events(
    tmp_path: Path,
) -> None:
    summary, store = _passing_summary(tmp_path)
    assert all(value == 0 for value in summary["operation_counts"].values())

    ledger = store.load()
    ledger.record_operation(OperationKind.PROVIDER_ACTIVITY)
    store.save(ledger)
    restarted = store.load()
    injected = build_contract_summary(
        isolation=validate_isolation(_config(tmp_path)),
        ledger=restarted,
        implementation_evidence=_implementation_evidence(),
        focused_tests_passed=True,
        full_non_e2e_passed=True,
    )
    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", injected
    )

    assert injected["operation_counts"]["provider_activity"] == 1
    assert store.load().operation_counts["provider_activity"] == 1
    assert result.passed is False
    assert "fl1_p1_forbidden_activity" in {finding.code for finding in result.errors}


def test_operation_summary_cannot_disagree_with_event_ledger(tmp_path: Path) -> None:
    summary, _ = _passing_summary(tmp_path, 124)
    summary["operation_evidence"]["events"].append(
        {"sequence": 2, "kind": "provider_activity", "item_id": None}
    )
    summary["operation_evidence"]["event_count"] = 2

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", summary
    )

    assert result.passed is False
    assert "fl1_p1_operation_evidence_invalid" in {
        finding.code for finding in result.errors
    }


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_operation_ledger_fails_closed(
    tmp_path: Path, damage: str
) -> None:
    _, store = _passing_summary(tmp_path, 125)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    if damage == "missing":
        payload.pop("operation_evidence")
    else:
        payload["operation_evidence"]["fingerprint"] = "0" * 64
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LedgerError):
        store.load()


@pytest.mark.parametrize(
    "mode", ["missing", "claimed_pass", "failed", "spelling", "digest"]
)
def test_required_stage_evidence_is_enforced(
    tmp_path: Path, mode: str
) -> None:
    summary, _ = _passing_summary(tmp_path, 126)
    if mode == "missing":
        summary["stage_evidence"].pop()
        summary["executed_stages"].pop()
        summary["missing_required_stages"] = [REQUIRED_EXECUTED_STAGES[-1]]
    elif mode == "claimed_pass":
        summary["stage_evidence"].pop()
    elif mode == "failed":
        summary["stage_evidence"][0]["status"] = "failed"
    elif mode == "spelling":
        summary["stage_evidence"][0]["stage"] += "_typo"
    else:
        summary["stage_evidence"][0]["evidence_digest"] = "0" * 64

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", summary
    )

    assert result.passed is False
    assert "fl1_p1_required_stage_evidence_invalid" in {
        finding.code for finding in result.errors
    }


def test_other_contract_invariants_remain_fail_closed(tmp_path: Path) -> None:
    summary, _ = _passing_summary(tmp_path, 127)

    invalid_authorization = copy.deepcopy(summary)
    invalid_authorization["authorization"]["production_authorized"] = True
    failed = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        invalid_authorization,
    )
    assert failed.passed is False
    assert "fl1_p1_authorization_boundary_invalid" in {
        finding.code for finding in failed.errors
    }

    invalid_ledger = copy.deepcopy(summary)
    invalid_ledger["ledger"]["duplicate_second_mutation_count"] = 1
    failed = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", invalid_ledger
    )
    assert failed.passed is False
    assert "fl1_p1_ledger_invalid" in {finding.code for finding in failed.errors}

    invalid_operation_count = copy.deepcopy(summary)
    invalid_operation_count["operation_counts"]["provider_activity"] = 1
    failed = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        invalid_operation_count,
    )
    assert failed.passed is False
    assert "fl1_p1_forbidden_activity" in {
        finding.code for finding in failed.errors
    }
