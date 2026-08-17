from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.fl1_i2_evidence import (
    EvidenceError,
    EvidenceStore,
    FailureBudget,
    FixedCutManifest,
    ItemDisposition,
    ManifestMember,
    OperationLedger,
    OperationState,
    canonical_fingerprint,
)


def _budget(max_failures: int = 2) -> FailureBudget:
    return FailureBudget(max_failures, 10, 1000, 5)


def test_fixed_cut_manifest_is_deterministic_and_rejects_duplicates() -> None:
    members = [ManifestMember("b", "b.jpg", {"file_id": "2"}), ManifestMember("a", "a.jpg", {"file_id": "1"})]
    first = FixedCutManifest.build(run_id="run", source_scope_fingerprint="scope", members=members)
    second = FixedCutManifest.build(run_id="run", source_scope_fingerprint="scope", members=reversed(members))
    assert first.manifest_fingerprint == second.manifest_fingerprint
    assert [item.item_id for item in first.members] == ["a", "b"]
    with pytest.raises(EvidenceError, match="manifest_duplicate_item_id"):
        FixedCutManifest.build(run_id="run", source_scope_fingerprint="scope", members=[members[0], members[0]])


def test_failure_admission_uses_strict_less_than_maximum() -> None:
    budget = _budget(2)
    ledger = OperationLedger("run", "manifest", canonical_fingerprint(budget.to_dict()))
    for index in range(2):
        operation = ledger.begin(item_id=f"item-{index}", attempt=1, budget=budget)
        ledger.mark_started(operation)
        ledger.close(operation, OperationState.FAILED, "synthetic_failure")
    assert ledger.failure_count == budget.max_failures
    assert not ledger.can_admit(budget)
    with pytest.raises(EvidenceError, match="operation_admission_budget_exhausted"):
        ledger.begin(item_id="never-started", attempt=1, budget=budget)


def test_residual_intent_and_started_get_distinct_terminal_closure() -> None:
    ledger = OperationLedger("run", "manifest", "budget")
    intent = ledger.begin(item_id="intent", attempt=1, budget=_budget())
    started = ledger.begin(item_id="started", attempt=1, budget=_budget())
    ledger.mark_started(started)
    assert set(ledger.recover_residuals()) == {intent, started}
    assert ledger.state(intent) is OperationState.RECOVERED
    assert ledger.state(started) is OperationState.INTERRUPTED
    ledger.validate()
    with pytest.raises(EvidenceError, match="operation_terminal_transition_invalid"):
        ledger.close(started, OperationState.FAILED, "second_terminal")


def test_operation_terminal_state_and_item_disposition_are_separate() -> None:
    ledger = OperationLedger("run", "manifest", "budget")
    operation = ledger.begin(item_id="corrupt", attempt=1, budget=_budget())
    ledger.mark_started(operation)
    ledger.close(operation, OperationState.COMPLETED, "parser_completed")
    ledger.set_disposition("corrupt", ItemDisposition.CORRUPT_MEDIA)
    assert ledger.state(operation) is OperationState.COMPLETED
    assert ledger.item_dispositions["corrupt"] is ItemDisposition.CORRUPT_MEDIA


def test_private_store_reads_nofollow_and_rejects_alias(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    store = EvidenceStore(root)
    store.write("ledger.json", {"safe": True})
    assert store.read("ledger.json") == {"safe": True}
    alias = root / "alias.json"
    try:
        alias.symlink_to(root / "ledger.json")
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises((EvidenceError, Exception), match="private_artifact"):
        store.read("alias.json")


def test_retry_requires_new_operation_id() -> None:
    ledger = OperationLedger("run", "manifest", "budget")
    operation = ledger.begin(item_id="item", attempt=1, budget=_budget())
    ledger.mark_started(operation)
    ledger.close(operation, OperationState.INTERRUPTED, "timeout")
    with pytest.raises(EvidenceError, match="operation_id_reused"):
        ledger.begin(item_id="item", attempt=2, budget=_budget(), operation_id=operation)
    retry = ledger.begin(item_id="item", attempt=2, budget=_budget())
    assert retry != operation
