"""Synthetic-only tests for the SCV2-FL1-P1 safety and ledger foundation."""

from __future__ import annotations

import copy
import json
import subprocess
import uuid
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
    ImplementationEvidenceMode,
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
    REQUIRED_FAILURE_BUDGET_SCENARIOS,
    REQUIRED_RECONCILIATION_SCENARIOS,
    ReconciliationScenarioObservation,
    StableInventoryItem,
    SyntheticScenarioObservation,
    build_contract_summary,
    build_failure_budget_scenario_matrix,
    build_reconciliation_scenario_matrix,
    collect_implementation_evidence,
    failure_budget_scenario_bundle_to_dict,
    reconciliation_scenario_bundle_to_dict,
    validate_isolation,
    verify_implementation_evidence_repository,
)
from scripts.check_documentation_state import validate_git_ancestry
from scripts.phase_contracts import (
    ContractRepositoryContext,
    check_phase_contract,
    get_contract,
)
from scripts.phase_contracts.contract_checks import scan_public_payload


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_HEAD = "a" * 40
SYNTHETIC_TREE = "b" * 40
SYNTHETIC_BASE = "c" * 40


def _digest(payload: object) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _refresh_implementation_digest(payload: dict[str, object]) -> None:
    payload["evidence_digest"] = _digest(
        {
            key: value
            for key, value in payload.items()
            if key != "evidence_digest"
        }
    )


def _refresh_stage_matrix_digests(stage: dict[str, object]) -> None:
    matrix = stage["evidence"]
    assert isinstance(matrix, dict)
    rows = matrix["scenarios"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        row["evidence_digest"] = _digest(
            {
                key: value
                for key, value in row.items()
                if key not in {"status", "evidence_digest"}
            }
        )
    matrix["fingerprint"] = _digest(
        {
            "schema_version": matrix["schema_version"],
            "scenarios": rows,
        }
    )
    stage["evidence_digest"] = _digest(
        {"stage": stage["stage"], "evidence": matrix}
    )


def _implementation_evidence() -> ImplementationEvidence:
    return ImplementationEvidence.create(
        approved_base_commit=SYNTHETIC_BASE,
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


def _failure_scenario_evidence(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    root = tmp_path / "failure-scenario-matrix"
    observations: dict[str, SyntheticScenarioObservation] = {}

    normal_items = [_item(201), _item(202)]
    normal, normal_store = _runner(
        root / "normal", normal_items, run_id="scenario-normal-success"
    )
    normal.run_next_batch(lambda _current: None)
    normal_before = normal_store.load()
    normal_restart, _ = _runner(
        root / "normal", normal_items, run_id="scenario-normal-success"
    )
    normal_restart.run_next_batch(lambda _current: None)
    observations["normal_success"] = SyntheticScenarioObservation(
        normal_before, normal_store.load()
    )

    manual_items = [_item(203), _item(204)]
    manual, manual_store = _runner(
        root / "manual", manual_items, run_id="scenario-manual-stop"
    )
    manual_calls: list[str] = []
    manual.run_next_batch(
        lambda current: manual_calls.append(current.item_id),
        stop_requested=lambda: len(manual_calls) >= 1,
    )
    manual_before = manual_store.load()
    manual_restart, _ = _runner(
        root / "manual", manual_items, run_id="scenario-manual-stop"
    )
    manual_restart.run_next_batch(lambda _current: None)
    observations["manual_stop_restart"] = SyntheticScenarioObservation(
        manual_before, manual_store.load()
    )

    per_item_items = [_item(205), _item(206)]
    per_item, per_item_store = _runner(
        root / "per-item",
        per_item_items,
        run_id="scenario-per-item-exhaustion",
        max_attempts_per_item=1,
        max_failure_attempts=10,
    )
    per_item.run_next_batch(
        lambda _current: (_ for _ in ()).throw(
            MutationNotCommittedError("scenario_item_failure")
        )
    )
    per_item.run_next_batch(lambda _current: None)
    per_item_before = per_item_store.load()
    per_item_restart, _ = _runner(
        root / "per-item",
        per_item_items,
        run_id="scenario-per-item-exhaustion",
        max_attempts_per_item=1,
        max_failure_attempts=10,
    )
    per_item_restart.run_next_batch(lambda _current: None)
    observations["per_item_exhaustion"] = SyntheticScenarioObservation(
        per_item_before, per_item_store.load()
    )

    global_items = [_item(207), _item(208), _item(209)]
    global_runner, global_store = _runner(
        root / "global",
        global_items,
        run_id="scenario-global-exhaustion",
        max_attempts_per_item=1,
        max_failure_attempts=2,
    )
    fail = lambda _current: (_ for _ in ()).throw(  # noqa: E731
        MutationNotCommittedError("scenario_global_failure")
    )
    global_runner.run_next_batch(fail)
    global_runner.run_next_batch(fail)
    global_before = global_store.load()
    global_restart, _ = _runner(
        root / "global",
        global_items,
        run_id="scenario-global-exhaustion",
        max_attempts_per_item=1,
        max_failure_attempts=2,
    )
    global_restart.run_next_batch(lambda _current: None)
    observations["global_budget_exhaustion"] = SyntheticScenarioObservation(
        global_before, global_store.load()
    )

    restart_items = [_item(210), _item(211)]
    restart_runner, restart_store = _runner(
        root / "restart",
        restart_items,
        run_id="scenario-restart-consistency",
        max_attempts_per_item=1,
        max_failure_attempts=10,
    )
    restart_runner.run_next_batch(
        lambda _current: (_ for _ in ()).throw(
            MutationNotCommittedError("scenario_restart_failure")
        )
    )
    restart_runner.run_next_batch(lambda _current: None)
    restart_before = restart_store.load()
    restarted, _ = _runner(
        root / "restart",
        restart_items,
        run_id="scenario-restart-consistency",
        max_attempts_per_item=1,
        max_failure_attempts=10,
    )
    restarted.run_next_batch(lambda _current: None)
    observations["restart_counter_reason_consistency"] = (
        SyntheticScenarioObservation(restart_before, restart_store.load())
    )

    matrix = build_failure_budget_scenario_matrix(observations)
    assert [row["scenario"] for row in matrix["scenarios"]] == list(
        REQUIRED_FAILURE_BUDGET_SCENARIOS
    )
    return matrix, failure_budget_scenario_bundle_to_dict(observations)


def _failure_scenario_matrix(tmp_path: Path) -> dict[str, object]:
    return _failure_scenario_evidence(tmp_path)[0]


def _reconciliation_scenario_evidence(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    observations: dict[str, ReconciliationScenarioObservation] = {}
    bundle_id = uuid.uuid4().hex
    outcomes = {
        "committed": InterruptedMutationOutcome.COMMITTED,
        "unknown": InterruptedMutationOutcome.UNKNOWN,
        "not_committed": InterruptedMutationOutcome.NOT_COMMITTED,
    }
    for offset, scenario in enumerate(REQUIRED_RECONCILIATION_SCENARIOS):
        root = tmp_path / f"reconciliation-{bundle_id}-{scenario}"
        item = _item(300 + offset)
        run_id = f"scenario-reconciliation-{bundle_id}-{scenario}"
        runner, store = _runner(
            root,
            [item],
            run_id=run_id,
            max_attempts_per_item=3,
            max_failure_attempts=5,
        )
        with pytest.raises(
            LedgerError, match="mutation_outcome_reconciliation_required"
        ):
            runner.run_next_batch(
                lambda _current: (_ for _ in ()).throw(
                    RuntimeError("synthetic_post_invocation_interruption")
                )
            )
        interrupted = store.load()

        blocked_runner, _ = _runner(
            root,
            [item],
            run_id=run_id,
            max_attempts_per_item=3,
            max_failure_attempts=5,
        )
        with pytest.raises(
            LedgerError, match="interrupted_mutation_reconciliation_required"
        ):
            blocked_runner.run_next_batch(lambda _current: None)
        blocked_restart = store.load()

        reconciliation_runner, _ = _runner(
            root,
            [item],
            run_id=run_id,
            max_attempts_per_item=3,
            max_failure_attempts=5,
        )
        private_ledger = reconciliation_runner._load_or_create()
        if scenario == "unknown":
            with pytest.raises(
                LedgerError, match="interrupted_mutation_outcome_unknown"
            ):
                reconciliation_runner._recover_interrupted(
                    private_ledger,
                    lambda _current: outcomes[scenario],
                )
        else:
            reconciliation_runner._recover_interrupted(
                private_ledger,
                lambda _current: outcomes[scenario],
            )
        reconciliation_result = store.load()

        post_runner, _ = _runner(
            root,
            [item],
            run_id=run_id,
            max_attempts_per_item=3,
            max_failure_attempts=5,
        )
        if scenario == "unknown":
            with pytest.raises(
                LedgerError, match="interrupted_mutation_reconciliation_required"
            ):
                post_runner.run_next_batch(lambda _current: None)
        else:
            post_runner.run_next_batch(lambda _current: None)
        post_reconciliation = store.load()

        final_runner, _ = _runner(
            root,
            [item],
            run_id=run_id,
            max_attempts_per_item=3,
            max_failure_attempts=5,
        )
        if scenario == "unknown":
            with pytest.raises(
                LedgerError, match="interrupted_mutation_reconciliation_required"
            ):
                final_runner.run_next_batch(lambda _current: None)
        else:
            final_runner.run_next_batch(lambda _current: None)
        final_restart = store.load()

        observations[scenario] = ReconciliationScenarioObservation(
            interrupted=interrupted,
            blocked_restart=blocked_restart,
            reconciliation_result=reconciliation_result,
            post_reconciliation=post_reconciliation,
            final_restart=final_restart,
        )

    matrix = build_reconciliation_scenario_matrix(observations)
    return matrix, reconciliation_scenario_bundle_to_dict(observations)


def _reconciliation_scenario_matrix(tmp_path: Path) -> dict[str, object]:
    return _reconciliation_scenario_evidence(tmp_path)[0]


def _trusted_scenario_evidence(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    failure_matrix, failure_bundle = _failure_scenario_evidence(
        tmp_path / "failure-budget-proof"
    )
    reconciliation_matrix, reconciliation_bundle = (
        _reconciliation_scenario_evidence(tmp_path / "reconciliation-proof")
    )
    return (
        failure_matrix,
        failure_bundle,
        reconciliation_matrix,
        reconciliation_bundle,
    )


def _git_repository_evidence(
    tmp_path: Path,
) -> tuple[Path, str, str, str, ImplementationEvidence]:
    repo = tmp_path / "evidence-repo"
    repo.mkdir(parents=True)

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
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "scripts/runtime.py")
    git("commit", "-m", "implementation")
    implementation = git("rev-parse", "HEAD")
    (repo / "docs").mkdir()
    (repo / "docs" / "current-handoff.md").write_text(
        "pending audit\n", encoding="utf-8"
    )
    git("add", "docs/current-handoff.md")
    git("commit", "-m", "governance")
    final = git("rev-parse", "HEAD")
    evidence = collect_implementation_evidence(
        repo_root=repo,
        approved_base_commit=base,
        implementation_commit=implementation,
    )
    return repo, base, implementation, final, evidence


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


@pytest.mark.parametrize("mode", ["missing", "other_item", "exchanged"])
def test_private_invocations_must_reconcile_to_each_item_after_fingerprint_refresh(
    tmp_path: Path, mode: str
) -> None:
    items = [_item(212), _item(213)]
    runner, store = _runner(
        tmp_path,
        items,
        max_attempts_per_item=3,
        max_failure_attempts=10,
    )
    runner.run_next_batch(
        lambda _current: (_ for _ in ()).throw(
            MutationNotCommittedError("first_attempt_not_committed")
        )
    )
    runner.run_next_batch(lambda _current: None)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    events = payload["operation_evidence"]["events"]
    assert [event["item_id"] for event in events] == [
        items[0].item_id,
        items[0].item_id,
        items[1].item_id,
    ]
    if mode == "missing":
        events[0]["item_id"] = None
        expected_error = "synthetic_mutation_invocation_item_id_required"
    elif mode == "other_item":
        events[0]["item_id"] = items[1].item_id
        expected_error = "mutation_invocation_item_attribution_mismatch"
    else:
        events[0]["item_id"] = items[1].item_id
        events[1]["item_id"] = items[1].item_id
        events[2]["item_id"] = items[0].item_id
        expected_error = "mutation_invocation_item_attribution_mismatch"
    payload["operation_evidence"]["fingerprint"] = _digest(
        {
            "schema_version": payload["operation_evidence"]["schema_version"],
            "events": events,
        }
    )
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LedgerError, match=expected_error):
        store.load()


def test_item_attribution_proof_survives_restart(tmp_path: Path) -> None:
    items = [_item(214), _item(215)]
    runner, store = _runner(tmp_path, items)
    runner.run_next_batch(lambda _current: None)
    before = store.load().mutation_attribution_proof
    restarted, _ = _runner(tmp_path, items)
    restarted.run_next_batch(lambda _current: None)
    after = store.load().mutation_attribution_proof

    assert before == after
    assert before["invocation_count"] == 2
    assert all(
        row["attempt_count"] == row["invocation_count"]
        for row in before["rows"]
    )


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
    repo, _base, _implementation, _final, evidence = _git_repository_evidence(
        tmp_path
    )
    failure_matrix, failure_bundle, reconciliation_matrix, reconciliation_bundle = (
        _trusted_scenario_evidence(tmp_path / "trusted-scenarios")
    )

    summary = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=evidence,
        failure_budget_scenario_matrix=failure_matrix,
        reconciliation_scenario_matrix=reconciliation_matrix,
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
    assert summary["public_redaction"] == {
        "passed": True,
        "private_paths_emitted": False,
        "finding_count": 0,
    }
    assert scan_public_payload(summary) == []

    contract = get_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1"
    )
    missing_context = check_phase_contract(contract.contract_id, summary)
    assert contract.phase_kind == "scv2_fl1_p1_isolation_safety_ledger_foundation"
    assert missing_context.passed is False
    assert {
        "fl1_p1_repository_context_required",
        "fl1_p1_runtime_ledger_context_required",
        "fl1_p1_failure_budget_scenario_context_required",
        "fl1_p1_reconciliation_scenario_context_required",
    }.issubset({finding.code for finding in missing_context.errors})
    complete = check_phase_contract(
        contract.contract_id,
        summary,
        repository_context=ContractRepositoryContext(
            repo_root=repo,
            runtime_ledger=store.load(),
            failure_budget_scenario_bundle=failure_bundle,
            reconciliation_scenario_bundle=reconciliation_bundle,
        ),
    )
    assert complete.passed is True


def test_owner_acceptance_is_bound_but_does_not_authorize_merge_or_route(
    tmp_path: Path,
) -> None:
    proof = validate_isolation(_config(tmp_path))
    runner, store = _runner(tmp_path, [_item(120)])
    runner.run_next_batch(lambda _current: None)
    repo, _base, _implementation, _final, evidence = _git_repository_evidence(
        tmp_path
    )
    acceptance = _owner_acceptance(evidence)
    failure_matrix, failure_bundle, reconciliation_matrix, reconciliation_bundle = (
        _trusted_scenario_evidence(tmp_path / "trusted-scenarios")
    )

    accepted = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=evidence,
        failure_budget_scenario_matrix=failure_matrix,
        reconciliation_scenario_matrix=reconciliation_matrix,
        focused_tests_passed=True,
        full_non_e2e_passed=True,
        owner_acceptance=acceptance,
    )
    missing_context = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", accepted
    )
    assert missing_context.passed is False
    assert "fl1_p1_repository_context_required" in {
        finding.code for finding in missing_context.errors
    }
    accepted_result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        accepted,
        repository_context=ContractRepositoryContext(
            repo_root=repo,
            runtime_ledger=store.load(),
            failure_budget_scenario_bundle=failure_bundle,
            reconciliation_scenario_bundle=reconciliation_bundle,
        ),
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
        failure_budget_scenario_matrix=failure_matrix,
        reconciliation_scenario_matrix=reconciliation_matrix,
        focused_tests_passed=True,
        full_non_e2e_passed=True,
        owner_acceptance=acceptance,
        merge_authorization=merge,
    )
    assert merge_ready["pipeline_contract"]["safe_to_merge"] is True
    assert merge_ready["pipeline_contract"]["route_approved"] is False


def test_contract_cli_uses_repository_and_private_ledger_context(
    tmp_path: Path,
) -> None:
    proof = validate_isolation(_config(tmp_path))
    runner, store = _runner(tmp_path, [_item(219)])
    runner.run_next_batch(lambda _current: None)
    repo, _base, _implementation, _final, evidence = _git_repository_evidence(
        tmp_path
    )
    failure_matrix, failure_bundle, reconciliation_matrix, reconciliation_bundle = (
        _trusted_scenario_evidence(tmp_path / "trusted-scenarios")
    )
    summary = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=evidence,
        failure_budget_scenario_matrix=failure_matrix,
        reconciliation_scenario_matrix=reconciliation_matrix,
        focused_tests_passed=True,
        full_non_e2e_passed=True,
        owner_acceptance=_owner_acceptance(evidence),
    )
    summary_path = tmp_path / "contract-summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    failure_bundle_path = tmp_path / "failure-budget-private.json"
    failure_bundle_path.write_text(json.dumps(failure_bundle), encoding="utf-8")
    reconciliation_bundle_path = tmp_path / "reconciliation-private.json"
    reconciliation_bundle_path.write_text(
        json.dumps(reconciliation_bundle), encoding="utf-8"
    )

    base_args = [
        sys.executable,
        str(ROOT / "scripts" / "check_phase_contract.py"),
        "--contract",
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        "--summary",
        str(summary_path),
    ]
    incomplete_args = (
        [],
        ["--repo-root", str(repo)],
        [
            "--repo-root",
            str(repo),
            "--runtime-ledger",
            str(store.path),
        ],
    )
    for extra_args in incomplete_args:
        incomplete = subprocess.run(
            [*base_args, *extra_args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert incomplete.returncode == 1
        assert json.loads(incomplete.stdout)["passed"] is False

    completed = subprocess.run(
        [
            *base_args,
            "--repo-root",
            str(repo),
            "--runtime-ledger",
            str(store.path),
            "--failure-budget-scenarios",
            str(failure_bundle_path),
            "--reconciliation-scenarios",
            str(reconciliation_bundle_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["passed"] is True


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
        failure_budget_scenario_matrix=_failure_scenario_matrix(tmp_path),
        reconciliation_scenario_matrix=_reconciliation_scenario_matrix(tmp_path),
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
            failure_budget_scenario_matrix=_failure_scenario_matrix(tmp_path),
            reconciliation_scenario_matrix=_reconciliation_scenario_matrix(tmp_path),
            focused_tests_passed=True,
            full_non_e2e_passed=True,
            owner_acceptance=wrong,
        )

    summary = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=evidence,
        failure_budget_scenario_matrix=_failure_scenario_matrix(tmp_path),
        reconciliation_scenario_matrix=_reconciliation_scenario_matrix(tmp_path),
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
            approved_base_commit="e" * 40,
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
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "base")
    base_commit = git("rev-parse", "HEAD")
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
        approved_base_commit=base_commit,
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
            approved_base_commit=base_commit,
            implementation_commit=implementation_commit,
        )


def test_squash_carry_forward_uses_parent_and_reviewed_tree_not_branch_ancestry(
    tmp_path: Path,
) -> None:
    repo, base, implementation, final, _pr_evidence = _git_repository_evidence(
        tmp_path
    )

    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    git("branch", "reviewed-feature", final)
    git("checkout", "-b", "squash-main", base)
    git("merge", "--squash", "reviewed-feature")
    git("commit", "-m", "squash carry-forward")
    squash = git("rev-parse", "HEAD")

    evidence = collect_implementation_evidence(
        repo_root=repo,
        approved_base_commit=base,
        implementation_commit=implementation,
        final_commit=final,
        mode=ImplementationEvidenceMode.SQUASH_CARRY_FORWARD,
    )
    verify_implementation_evidence_repository(repo_root=repo, evidence=evidence)

    assert evidence.carry_forward_commit == squash
    assert evidence.carry_forward_tree == evidence.final_tree
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", implementation, squash],
        cwd=repo,
        check=False,
    ).returncode != 0
    validate_git_ancestry(
        {
            "accepted_mainline_base": base,
            "implementation_evidence_head": implementation,
        },
        root=repo,
    )


def test_squash_carry_forward_rejects_wrong_tree_base_old_final_and_drift(
    tmp_path: Path,
) -> None:
    repo, base, implementation, final, _pr_evidence = _git_repository_evidence(
        tmp_path
    )

    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    git("branch", "reviewed-feature", final)
    git("checkout", "-b", "squash-main", base)
    git("merge", "--squash", "reviewed-feature")
    git("commit", "-m", "correct squash")

    with pytest.raises(LedgerError, match="squash_parent_invalid"):
        collect_implementation_evidence(
            repo_root=repo,
            approved_base_commit=implementation,
            implementation_commit=implementation,
            final_commit=final,
            mode=ImplementationEvidenceMode.SQUASH_CARRY_FORWARD,
        )
    with pytest.raises(LedgerError, match="squash_tree_mismatch"):
        collect_implementation_evidence(
            repo_root=repo,
            approved_base_commit=base,
            implementation_commit=implementation,
            final_commit=implementation,
            mode=ImplementationEvidenceMode.SQUASH_CARRY_FORWARD,
        )

    git("checkout", "-B", "wrong-tree", base)
    git("merge", "--squash", "reviewed-feature")
    (repo / "README.md").write_text("wrong tree\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "wrong squash tree")
    with pytest.raises(LedgerError, match="squash_tree_mismatch"):
        collect_implementation_evidence(
            repo_root=repo,
            approved_base_commit=base,
            implementation_commit=implementation,
            final_commit=final,
            mode=ImplementationEvidenceMode.SQUASH_CARRY_FORWARD,
        )

    git("checkout", "reviewed-feature")
    (repo / "scripts" / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    git("add", "scripts/runtime.py")
    git("commit", "-m", "executable drift")
    drifted_final = git("rev-parse", "HEAD")
    git("checkout", "-B", "drift-squash", base)
    git("merge", "--squash", "reviewed-feature")
    git("commit", "-m", "drifted squash")
    with pytest.raises(LedgerError, match="implementation_evidence_executable_drift"):
        collect_implementation_evidence(
            repo_root=repo,
            approved_base_commit=base,
            implementation_commit=implementation,
            final_commit=drifted_final,
            mode=ImplementationEvidenceMode.SQUASH_CARRY_FORWARD,
        )


def test_contract_checker_verifies_real_repository_objects_and_current_head(
    tmp_path: Path,
) -> None:
    repo, _base, implementation, _final, evidence = _git_repository_evidence(
        tmp_path
    )
    summary, store, failure_bundle, reconciliation_bundle = (
        _passing_summary_with_bundles(tmp_path / "summary")
    )
    summary["implementation_evidence"] = evidence.to_public_dict()
    context = ContractRepositoryContext(
        repo_root=repo,
        runtime_ledger=store.load(),
        failure_budget_scenario_bundle=failure_bundle,
        reconciliation_scenario_bundle=reconciliation_bundle,
    )

    assert check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        summary,
        repository_context=context,
    ).passed is True

    forged_cases: list[dict[str, object]] = []
    nonexistent = copy.deepcopy(summary["implementation_evidence"])
    nonexistent["implementation_commit"] = "d" * 40
    _refresh_implementation_digest(nonexistent)
    forged_cases.append(nonexistent)

    wrong_tree = copy.deepcopy(summary["implementation_evidence"])
    wrong_tree["final_tree"] = "e" * 40
    _refresh_implementation_digest(wrong_tree)
    forged_cases.append(wrong_tree)

    stale = copy.deepcopy(summary["implementation_evidence"])
    implementation_tree = subprocess.run(
        ["git", "rev-parse", f"{implementation}^{{tree}}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    stale.update(
        {
            "final_commit": implementation,
            "final_tree": implementation_tree,
            "post_implementation_changed_paths": [],
        }
    )
    _refresh_implementation_digest(stale)
    forged_cases.append(stale)

    for forged in forged_cases:
        candidate = copy.deepcopy(summary)
        candidate["implementation_evidence"] = forged
        result = check_phase_contract(
            "scv2_fl1_isolated_full_library_dev_test_contract_v1",
            candidate,
            repository_context=context,
        )
        assert result.passed is False
        assert "fl1_p1_repository_evidence_invalid" in {
            finding.code for finding in result.errors
        }


def test_contract_checker_accepts_repository_verified_squash_carry_forward(
    tmp_path: Path,
) -> None:
    repo, base, implementation, final, _evidence = _git_repository_evidence(
        tmp_path
    )

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("branch", "reviewed-feature", final)
    git("checkout", "-b", "squash-main", base)
    git("merge", "--squash", "reviewed-feature")
    git("commit", "-m", "squash")
    evidence = collect_implementation_evidence(
        repo_root=repo,
        approved_base_commit=base,
        implementation_commit=implementation,
        final_commit=final,
        mode=ImplementationEvidenceMode.SQUASH_CARRY_FORWARD,
    )
    summary, store, failure_bundle, reconciliation_bundle = (
        _passing_summary_with_bundles(tmp_path / "summary")
    )
    summary["implementation_evidence"] = evidence.to_public_dict()

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        summary,
        repository_context=ContractRepositoryContext(
            repo_root=repo,
            runtime_ledger=store.load(),
            failure_budget_scenario_bundle=failure_bundle,
            reconciliation_scenario_bundle=reconciliation_bundle,
        ),
    )
    assert result.passed is True


def _passing_summary_with_bundles(
    tmp_path: Path,
    item_index: int = 123,
) -> tuple[dict, JsonLedgerStore, dict[str, object], dict[str, object]]:
    proof = validate_isolation(_config(tmp_path))
    runner, store = _runner(tmp_path, [_item(item_index)])
    runner.run_next_batch(lambda _current: None)
    failure_matrix, failure_bundle, reconciliation_matrix, reconciliation_bundle = (
        _trusted_scenario_evidence(tmp_path / "trusted-scenarios")
    )
    return (
        build_contract_summary(
            isolation=proof,
            ledger=store.load(),
            implementation_evidence=_implementation_evidence(),
            failure_budget_scenario_matrix=failure_matrix,
            reconciliation_scenario_matrix=reconciliation_matrix,
            focused_tests_passed=True,
            full_non_e2e_passed=True,
        ),
        store,
        failure_bundle,
        reconciliation_bundle,
    )


def _passing_summary(
    tmp_path: Path, item_index: int = 123
) -> tuple[dict, JsonLedgerStore]:
    summary, store, _failure_bundle, _reconciliation_bundle = (
        _passing_summary_with_bundles(tmp_path, item_index)
    )
    return summary, store


def _trusted_audit_ready_case(
    tmp_path: Path,
) -> tuple[dict, ContractRepositoryContext, JsonLedgerStore]:
    runtime_root = tmp_path / "main-runtime"
    proof = validate_isolation(_config(runtime_root))
    runner, store = _runner(runtime_root, [_item(400)])
    runner.run_next_batch(lambda _current: None)
    repo, _base, _implementation, _final, evidence = _git_repository_evidence(
        tmp_path / "repository"
    )
    failure_matrix, failure_bundle, reconciliation_matrix, reconciliation_bundle = (
        _trusted_scenario_evidence(tmp_path / "trusted-scenarios")
    )
    summary = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=evidence,
        failure_budget_scenario_matrix=failure_matrix,
        reconciliation_scenario_matrix=reconciliation_matrix,
        focused_tests_passed=True,
        full_non_e2e_passed=True,
    )
    context = ContractRepositoryContext(
        repo_root=repo,
        runtime_ledger=store.load(),
        failure_budget_scenario_bundle=failure_bundle,
        reconciliation_scenario_bundle=reconciliation_bundle,
    )
    return summary, context, store


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
        failure_budget_scenario_matrix=_failure_scenario_matrix(tmp_path),
        reconciliation_scenario_matrix=_reconciliation_scenario_matrix(tmp_path),
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


def test_public_attribution_rejects_equal_total_swapped_between_items(
    tmp_path: Path,
) -> None:
    items = [_item(216), _item(217)]
    proof = validate_isolation(_config(tmp_path))
    runner, store = _runner(
        tmp_path,
        items,
        max_attempts_per_item=3,
        max_failure_attempts=10,
    )
    runner.run_next_batch(
        lambda _current: (_ for _ in ()).throw(
            MutationNotCommittedError("first_attempt_not_committed")
        )
    )
    runner.run_next_batch(lambda _current: None)
    summary = build_contract_summary(
        isolation=proof,
        ledger=store.load(),
        implementation_evidence=_implementation_evidence(),
        failure_budget_scenario_matrix=_failure_scenario_matrix(tmp_path),
        reconciliation_scenario_matrix=_reconciliation_scenario_matrix(tmp_path),
        focused_tests_passed=True,
        full_non_e2e_passed=True,
    )
    attribution = summary["operation_evidence"]["mutation_attribution"]
    rows = attribution["rows"]
    assert sorted(row["invocation_count"] for row in rows) == [1, 2]
    rows[0]["attempt_count"], rows[1]["attempt_count"] = (
        rows[1]["attempt_count"],
        rows[0]["attempt_count"],
    )
    rows[0]["invocation_count"], rows[1]["invocation_count"] = (
        rows[1]["invocation_count"],
        rows[0]["invocation_count"],
    )
    attribution_payload = {
        "schema_version": attribution["schema_version"],
        "private_execution_fingerprint": attribution[
            "private_execution_fingerprint"
        ],
        "rows": rows,
    }
    attribution["fingerprint"] = _digest(attribution_payload)
    summary["ledger"]["mutation_attribution_fingerprint"] = attribution[
        "fingerprint"
    ]
    assert attribution["invocation_count"] == 3

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", summary
    )
    assert result.passed is False
    assert "fl1_p1_operation_evidence_invalid" in {
        finding.code for finding in result.errors
    }


def test_trusted_private_ledger_rejects_recomputed_public_attribution_forgery(
    tmp_path: Path,
) -> None:
    repo, _base, _implementation, _final, evidence = _git_repository_evidence(
        tmp_path
    )
    summary, store, failure_bundle, reconciliation_bundle = (
        _passing_summary_with_bundles(tmp_path / "summary")
    )
    summary["implementation_evidence"] = evidence.to_public_dict()
    attribution = summary["operation_evidence"]["mutation_attribution"]
    attribution["rows"][0]["item_identity_digest"] = "f" * 64
    attribution_payload = {
        "schema_version": attribution["schema_version"],
        "private_execution_fingerprint": attribution[
            "private_execution_fingerprint"
        ],
        "rows": attribution["rows"],
    }
    attribution["fingerprint"] = _digest(attribution_payload)
    summary["ledger"]["mutation_attribution_fingerprint"] = attribution[
        "fingerprint"
    ]

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        summary,
        repository_context=ContractRepositoryContext(
            repo_root=repo,
            runtime_ledger=store.load(),
            failure_budget_scenario_bundle=failure_bundle,
            reconciliation_scenario_bundle=reconciliation_bundle,
        ),
    )
    assert result.passed is False
    assert "fl1_p1_runtime_ledger_evidence_mismatch" in {
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


@pytest.mark.parametrize(
    "mode", ["missing_scenario", "failed_assertion", "reused_run", "failed_status"]
)
def test_failure_budget_stage_requires_complete_independent_scenario_matrix(
    tmp_path: Path, mode: str
) -> None:
    summary, _ = _passing_summary(tmp_path, 218)
    stage = next(
        row
        for row in summary["stage_evidence"]
        if row["stage"] == "failure_budget_and_manual_stop"
    )
    matrix = stage["evidence"]
    if mode == "missing_scenario":
        matrix["scenarios"].pop()
        matrix["scenario_count"] = len(matrix["scenarios"])
    elif mode == "failed_assertion":
        row = matrix["scenarios"][1]
        assertion = next(iter(row["assertions"]))
        row["assertions"][assertion] = False
        row["evidence_digest"] = _digest(
            {
                key: value
                for key, value in row.items()
                if key not in {"status", "evidence_digest"}
            }
        )
    elif mode == "reused_run":
        row = matrix["scenarios"][1]
        row["run_id"] = matrix["scenarios"][0]["run_id"]
        row["evidence_digest"] = _digest(
            {
                key: value
                for key, value in row.items()
                if key not in {"status", "evidence_digest"}
            }
        )
    else:
        matrix["scenarios"][1]["status"] = "failed"
    matrix["fingerprint"] = _digest(
        {
            "schema_version": matrix["schema_version"],
            "scenarios": matrix["scenarios"],
        }
    )
    stage["evidence_digest"] = _digest(
        {"stage": stage["stage"], "evidence": stage["evidence"]}
    )

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", summary
    )
    assert result.passed is False
    assert "fl1_p1_required_stage_evidence_invalid" in {
        finding.code for finding in result.errors
    }


def test_failure_budget_public_true_assertion_cannot_replace_private_evidence(
    tmp_path: Path,
) -> None:
    summary, context, _store = _trusted_audit_ready_case(tmp_path)
    stage = next(
        row
        for row in summary["stage_evidence"]
        if row["stage"] == "failure_budget_and_manual_stop"
    )
    stage["evidence"]["scenarios"][0]["assertions"] = {"passed": True}
    _refresh_stage_matrix_digests(stage)

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        summary,
        repository_context=context,
    )
    assert result.passed is False
    assert {
        "fl1_p1_failure_budget_scenario_context_required",
        "fl1_p1_required_stage_evidence_invalid",
    }.issubset({finding.code for finding in result.errors})


def test_audit_ready_requires_all_protected_contexts(tmp_path: Path) -> None:
    summary, context, _store = _trusted_audit_ready_case(tmp_path)
    cases = (
        ContractRepositoryContext(repo_root=context.repo_root),
        ContractRepositoryContext(
            repo_root=context.repo_root,
            runtime_ledger=context.runtime_ledger,
        ),
        replace(context, failure_budget_scenario_bundle=None),
        replace(context, reconciliation_scenario_bundle=None),
    )
    expected_codes = (
        {
            "fl1_p1_runtime_ledger_context_required",
            "fl1_p1_failure_budget_scenario_context_required",
            "fl1_p1_reconciliation_scenario_context_required",
        },
        {
            "fl1_p1_failure_budget_scenario_context_required",
            "fl1_p1_reconciliation_scenario_context_required",
        },
        {"fl1_p1_failure_budget_scenario_context_required"},
        {"fl1_p1_reconciliation_scenario_context_required"},
    )
    for candidate, expected in zip(cases, expected_codes, strict=True):
        result = check_phase_contract(
            "scv2_fl1_isolated_full_library_dev_test_contract_v1",
            summary,
            repository_context=candidate,
        )
        assert result.passed is False
        assert expected.issubset({finding.code for finding in result.errors})


def test_blocked_diagnostic_without_protected_stage_claim_needs_no_repo_context(
    tmp_path: Path,
) -> None:
    summary, _context, _store = _trusted_audit_ready_case(tmp_path)
    summary["pipeline_contract"]["status"] = "blocked_fl1_p1_foundation"
    summary["pipeline_contract"]["active_blockers"] = ["focused_tests_failed"]
    summary["validation"]["focused_tests_passed"] = False

    protected = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", summary
    )
    assert "fl1_p1_repository_context_required" in {
        finding.code for finding in protected.errors
    }

    summary["executed_stages"] = []
    summary["missing_required_stages"] = list(REQUIRED_EXECUTED_STAGES)
    summary["stage_evidence"] = []
    diagnostic = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1", summary
    )
    assert diagnostic.passed is False
    assert "fl1_p1_required_stage_evidence_invalid" in {
        finding.code for finding in diagnostic.errors
    }
    assert "fl1_p1_repository_context_required" not in {
        finding.code for finding in diagnostic.errors
    }


@pytest.mark.parametrize(
    "damage",
    ["swapped_scenarios", "swapped_before_after", "reused_run", "private_drift"],
)
def test_failure_budget_private_bundle_tampering_fails_closed(
    tmp_path: Path,
    damage: str,
) -> None:
    summary, context, _store = _trusted_audit_ready_case(tmp_path)
    bundle = copy.deepcopy(context.failure_budget_scenario_bundle)
    assert isinstance(bundle, dict)
    scenarios = bundle["scenarios"]
    assert isinstance(scenarios, dict)
    first, second = REQUIRED_FAILURE_BUDGET_SCENARIOS[:2]
    if damage == "swapped_scenarios":
        scenarios[first], scenarios[second] = scenarios[second], scenarios[first]
    elif damage == "swapped_before_after":
        row = scenarios[first]
        row["before_restart"], row["after_restart"] = (
            row["after_restart"],
            row["before_restart"],
        )
    elif damage == "reused_run":
        reused_run = scenarios[first]["after_restart"]["ledger"]["run_id"]
        scenarios[second]["before_restart"]["ledger"]["run_id"] = reused_run
        scenarios[second]["after_restart"]["ledger"]["run_id"] = reused_run
    else:
        scenarios[first]["after_restart"]["ledger"]["checkpoint"][
            "generation"
        ] += 1

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        summary,
        repository_context=replace(
            context,
            failure_budget_scenario_bundle=bundle,
        ),
    )
    assert result.passed is False
    assert "fl1_p1_failure_budget_scenario_context_required" in {
        finding.code for finding in result.errors
    }


def test_plain_success_counts_cannot_complete_reconciliation_stage(
    tmp_path: Path,
) -> None:
    summary, context, _store = _trusted_audit_ready_case(tmp_path)
    stage = next(
        row
        for row in summary["stage_evidence"]
        if row["stage"] == "interrupted_mutation_reconciliation"
    )
    stage["evidence"] = {
        "reconciliation_required_count": 0,
        "recovery_count": 0,
    }
    stage["status"] = "completed"
    stage["evidence_digest"] = _digest(
        {"stage": stage["stage"], "evidence": stage["evidence"]}
    )

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        summary,
        repository_context=context,
    )
    assert result.passed is False
    assert {
        "fl1_p1_reconciliation_scenario_context_required",
        "fl1_p1_required_stage_evidence_invalid",
    }.issubset({finding.code for finding in result.errors})


@pytest.mark.parametrize(
    "damage",
    ["missing_restart", "second_invocation", "unknown_marked_succeeded"],
)
def test_reconciliation_private_bundle_tampering_fails_closed(
    tmp_path: Path,
    damage: str,
) -> None:
    summary, context, _store = _trusted_audit_ready_case(tmp_path)
    bundle = copy.deepcopy(context.reconciliation_scenario_bundle)
    assert isinstance(bundle, dict)
    scenarios = bundle["scenarios"]
    assert isinstance(scenarios, dict)
    if damage == "missing_restart":
        scenarios["committed"].pop("blocked_restart")
    elif damage == "second_invocation":
        scenarios["not_committed"]["blocked_restart"] = copy.deepcopy(
            scenarios["not_committed"]["post_reconciliation"]
        )
    else:
        unknown_run_id = scenarios["unknown"]["interrupted"]["run_id"]
        scenarios["unknown"] = copy.deepcopy(scenarios["committed"])
        for snapshot in scenarios["unknown"].values():
            snapshot["run_id"] = unknown_run_id

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        summary,
        repository_context=replace(
            context,
            reconciliation_scenario_bundle=bundle,
        ),
    )
    assert result.passed is False
    assert "fl1_p1_reconciliation_scenario_context_required" in {
        finding.code for finding in result.errors
    }


def test_reconciliation_matrix_proves_all_three_outcomes(tmp_path: Path) -> None:
    summary, context, _store = _trusted_audit_ready_case(tmp_path)
    stage = next(
        row
        for row in summary["stage_evidence"]
        if row["stage"] == "interrupted_mutation_reconciliation"
    )
    assert [row["scenario"] for row in stage["evidence"]["scenarios"]] == list(
        REQUIRED_RECONCILIATION_SCENARIOS
    )
    assert check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        summary,
        repository_context=context,
    ).passed is True


@pytest.mark.parametrize(
    ("field", "leaked_value"),
    [
        ("api_key", "private-api-credential"),
        ("database_url", "postgresql://user:secret@localhost/private"),
        ("windows_path", r"C:\\Users\\private-user\\source.txt"),
        ("posix_path", "/home/private-user/source.txt"),
        ("raw_filename", "PRIVATE_SOURCE_001.JPG"),
        ("source_content_fingerprint", "f" * 64),
        ("access_token", "ghp_privateToken1234567890"),
    ],
)
def test_fl1_contract_recursively_scans_unknown_public_fields_without_echo(
    tmp_path: Path,
    field: str,
    leaked_value: str,
) -> None:
    summary, context, _store = _trusted_audit_ready_case(tmp_path)
    summary["debug"] = {field: leaked_value}
    summary["public_redaction"] = {
        "passed": True,
        "private_paths_emitted": False,
        "finding_count": 0,
    }
    _refresh_implementation_digest(summary["implementation_evidence"])
    for stage in summary["stage_evidence"]:
        if isinstance(stage.get("evidence"), dict) and isinstance(
            stage["evidence"].get("scenarios"), list
        ):
            _refresh_stage_matrix_digests(stage)

    result = check_phase_contract(
        "scv2_fl1_isolated_full_library_dev_test_contract_v1",
        summary,
        repository_context=context,
    )
    assert result.passed is False
    codes = {finding.code for finding in result.errors}
    assert "fl1_p1_public_redaction_invalid" in codes
    assert any(code.startswith("fl1_p1_public_redaction_") for code in codes)
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    assert leaked_value not in serialized


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
