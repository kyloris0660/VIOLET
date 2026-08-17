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


def _object(identifier: str) -> dict[str, str]:
    return {"platform": "synthetic", "volume_id": "volume", "file_id": identifier}


def _change(size: int = 10) -> dict[str, int]:
    return {"change_time_ns": 1, "write_time_ns": 1, "size": size, "allocation_size": size}


def _directory() -> dict[str, object]:
    return {"object_identity": _object("root"), "change_identity": _change(0)}


def test_fixed_cut_manifest_is_deterministic_and_rejects_duplicates() -> None:
    members = [ManifestMember("b", "b.jpg", _object("2"), _change()), ManifestMember("a", "a.jpg", _object("1"), _change())]
    first = FixedCutManifest.build(run_id="run", source_scope_fingerprint="scope", directory_observation=_directory(), members=members)
    second = FixedCutManifest.build(run_id="run", source_scope_fingerprint="scope", directory_observation=_directory(), members=reversed(members))
    assert first.manifest_fingerprint == second.manifest_fingerprint
    assert [item.item_id for item in first.members] == ["a", "b"]
    with pytest.raises(EvidenceError, match="manifest_duplicate_item_id"):
        FixedCutManifest.build(run_id="run", source_scope_fingerprint="scope", directory_observation=_directory(), members=[members[0], members[0]])


def test_failure_admission_uses_strict_less_than_maximum() -> None:
    budget = _budget(2)
    ledger = OperationLedger("run", "manifest", canonical_fingerprint(budget.to_dict()))
    for index in range(2):
        operation = ledger.begin(item_id=f"item-{index}", kind="combined_content", attempt=1, budget=budget)
        ledger.mark_started(operation)
        ledger.commit_terminal(operation, OperationState.FAILED, "synthetic_failure", bytes_consumed=0, payload=None)
    assert ledger.failure_count == budget.max_failures
    assert not ledger.can_admit(budget)
    with pytest.raises(EvidenceError, match="operation_admission_budget_exhausted"):
        ledger.begin(item_id="never-started", kind="combined_content", attempt=1, budget=budget)


def test_residual_intent_and_started_get_distinct_terminal_closure() -> None:
    ledger = OperationLedger("run", "manifest", "budget")
    intent = ledger.begin(item_id="intent", kind="list_directory", attempt=1, budget=_budget())
    started = ledger.begin(item_id="started", kind="combined_content", attempt=1, budget=_budget(), bytes_reserved=10)
    ledger.mark_started(started)
    assert set(ledger.recover_residuals()) == {intent, started}
    assert ledger.state(intent) is OperationState.RECOVERED
    assert ledger.state(started) is OperationState.INTERRUPTED
    ledger.validate()
    with pytest.raises(EvidenceError, match="operation_terminal_transition_invalid"):
        ledger.commit_terminal(started, OperationState.FAILED, "second_terminal", bytes_consumed=0, payload=None)


def test_operation_terminal_state_and_item_disposition_are_separate() -> None:
    ledger = OperationLedger("run", "manifest", "budget")
    operation = ledger.begin(item_id="corrupt", kind="combined_content", attempt=1, budget=_budget(), bytes_reserved=10)
    ledger.mark_started(operation)
    ledger.commit_terminal(operation, OperationState.COMPLETED, "parser_completed", bytes_consumed=10, payload={"valid": False})
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
    operation = ledger.begin(item_id="item", kind="combined_content", attempt=1, budget=_budget(), bytes_reserved=10)
    ledger.mark_started(operation)
    ledger.commit_terminal(operation, OperationState.INTERRUPTED, "timeout", bytes_consumed=4, payload=None)
    with pytest.raises(EvidenceError, match="operation_id_reused"):
        ledger.begin(item_id="item", kind="combined_content", attempt=2, budget=_budget(), operation_id=operation)
    retry = ledger.begin(item_id="item", kind="combined_content", attempt=2, budget=_budget())
    assert retry != operation


def test_run_wide_bytes_reconcile_partial_failure_and_resume() -> None:
    budget = FailureBudget(3, 10, 10, 5)
    ledger = OperationLedger("run", "manifest", canonical_fingerprint(budget.to_dict()))
    first = ledger.begin(item_id="one", kind="combined_content", attempt=1, budget=budget, bytes_reserved=6)
    ledger.mark_started(first)
    ledger.commit_terminal(first, OperationState.FAILED, "partial", bytes_consumed=4, payload=None)
    second = ledger.begin(item_id="two", kind="combined_content", attempt=1, budget=budget, bytes_reserved=6)
    ledger.mark_started(second)
    ledger.commit_terminal(second, OperationState.COMPLETED, "ok", bytes_consumed=6, payload={"ok": True})
    assert ledger.consumed_bytes == budget.max_bytes
    assert not ledger.can_admit(budget)
    rebuilt = OperationLedger.from_private_dict(ledger.to_private_dict())
    assert rebuilt.consumed_bytes == budget.max_bytes
    assert rebuilt.to_worker_projection() == ledger.to_worker_projection()


def test_terminal_and_result_fingerprint_conflicts_fail_closed() -> None:
    ledger = OperationLedger("run", "manifest", "budget")
    operation = ledger.begin(item_id="item", kind="combined_content", attempt=1, budget=_budget(), bytes_reserved=2)
    ledger.mark_started(operation)
    ledger.commit_terminal(operation, OperationState.COMPLETED, "ok", bytes_consumed=2, payload={"ok": True})
    payload = ledger.to_private_dict()
    payload["committed_results"][operation]["bytes_consumed"] = 1
    with pytest.raises(EvidenceError, match="operation_closure_conflict"):
        OperationLedger.from_private_dict(payload)
