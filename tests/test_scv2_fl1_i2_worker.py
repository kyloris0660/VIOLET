from __future__ import annotations

from pathlib import Path

from scripts.fl1_i2_worker import WorkerController, WorkerOperation, WorkerStatus


def _policy() -> dict[str, object]:
    return {
        "policy_version": "scv2-fl1-i2-source-safety.v1",
        "allowed_source_kinds": ["path_source"],
        "require_known_attributes": True,
        "require_no_follow": True,
        "require_identity_bound": True,
        "reject_reparse_points": True,
        "reject_multiple_links": True,
        "reject_recall_risk": True,
    }


def test_ready_started_go_orders_persistence_before_source_operation(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "fixture.png"
    source.write_bytes(b"synthetic")
    persisted: list[str] = []
    result = WorkerController().run(
        WorkerOperation.HASH_FILE,
        {"root": str(root), "member_name": source.name, "max_bytes": 100, "policy": _policy()},
        deadline_seconds=5,
        persist_started=lambda: persisted.append("STARTED"),
    )
    assert persisted == ["STARTED"]
    assert result.status is WorkerStatus.COMPLETED
    assert result.payload and result.payload["byte_count"] == 9


def test_blocking_worker_is_terminated_and_confirmed() -> None:
    result = WorkerController().run(
        WorkerOperation.SYNTHETIC_BLOCK,
        {},
        deadline_seconds=0.2,
        persist_started=lambda: None,
    )
    assert result.status is WorkerStatus.INTERRUPTED
    assert result.safe_code == "worker_deadline_exceeded"
    assert result.started_persisted
    assert result.exit_confirmed


def test_budget_overflow_fails_without_private_value_projection(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "fixture.jpg"
    source.write_bytes(b"synthetic-over-budget")
    result = WorkerController().run(
        WorkerOperation.HASH_FILE,
        {"root": str(root), "member_name": source.name, "max_bytes": 3, "policy": _policy()},
        deadline_seconds=5,
        persist_started=lambda: None,
    )
    assert result.status is WorkerStatus.FAILED
    assert result.safe_code == "worker_byte_budget_exceeded"
    assert result.payload is None


def test_unconfirmed_termination_blocks_entire_run() -> None:
    class UnkillableProcess:
        def is_alive(self) -> bool:
            return True

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def join(self, _timeout: float) -> None:
            pass

    result = WorkerController(exit_timeout_seconds=0.01)._terminate(  # type: ignore[arg-type]
        UnkillableProcess(),
        0.0,
        True,
        "worker_deadline_exceeded",
    )
    assert result.status is WorkerStatus.BLOCKED
    assert result.safe_code == "worker_termination_unconfirmed"
    assert not result.exit_confirmed
