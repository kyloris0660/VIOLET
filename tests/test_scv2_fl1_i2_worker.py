from __future__ import annotations

from pathlib import Path
import binascii

import pytest

from scripts.fl1_i2_source_backends import current_handle_backend
from scripts.fl1_i2_worker import WorkerController, WorkerOperation, WorkerStatus, _execute


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


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + (binascii.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")

    ihdr = b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"x") + chunk(b"IEND", b"")


def _payload(root: Path, source: Path, *, max_bytes: int | None = None) -> dict[str, object]:
    backend = current_handle_backend()
    with backend.open_directory(root) as directory:
        member = next(item for item in backend.enumerate_directory(directory) if item.name == source.name)
        return {
            "root": str(root),
            "member_name": source.name,
            "expected_root_observation": directory.observation.to_private_dict(),
            "expected_member_object_identity": member.object_identity.to_private_dict(),
            "expected_member_change_identity": member.change_identity.to_private_dict(),
            "max_bytes": max_bytes if max_bytes is not None else max(1, member.change_identity.size),
            "max_depth": 100,
            "parser_deadline_monotonic": __import__("time").monotonic() + 5,
            "policy": _policy(),
            "enumeration_budget": {"max_entries": 100, "max_pages": 100, "max_metadata_bytes": 100000},
        }


def test_ready_started_go_orders_persistence_before_source_operation(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "fixture.png"
    source.write_bytes(_png())
    persisted: list[str] = []
    result = WorkerController().run(
        WorkerOperation.COMBINED_CONTENT,
        _payload(root, source),
        deadline_seconds=5,
        persist_started=lambda: persisted.append("STARTED"),
    )
    assert persisted == ["STARTED"]
    assert result.status is WorkerStatus.COMPLETED
    assert result.payload and result.payload["byte_count"] == len(_png())
    assert result.payload["media"]["valid"] is True


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
    source.write_bytes(_png())
    result = WorkerController().run(
        WorkerOperation.COMBINED_CONTENT,
        _payload(root, source, max_bytes=3),
        deadline_seconds=5,
        persist_started=lambda: None,
    )
    assert result.status is WorkerStatus.FAILED
    assert result.safe_code == "worker_byte_reservation_mismatch"
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


def test_combined_operation_rejects_manifest_identity_drift(tmp_path: Path) -> None:
    root = tmp_path / "identity"
    root.mkdir()
    source = root / "fixture.png"
    source.write_bytes(_png())
    payload = _payload(root, source)
    payload["expected_member_object_identity"] = {"platform": "windows", "volume_id": "1", "file_id": "1"}
    with pytest.raises(Exception, match="source_member_object_identity_drift"):
        _execute(WorkerOperation.COMBINED_CONTENT.value, payload)


def test_opened_pre_and_post_observations_are_all_policy_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "gates"
    root.mkdir()
    source = root / "fixture.png"
    source.write_bytes(_png())
    from scripts import fl1_i2_worker

    original = fl1_i2_worker.SourceIngestionGate.decide_observation
    observed: list[object] = []

    def decide(**kwargs: object):
        observed.append(kwargs["observation"])
        return original(**kwargs)

    monkeypatch.setattr(fl1_i2_worker.SourceIngestionGate, "decide_observation", decide)
    result = _execute(WorkerOperation.COMBINED_CONTENT.value, _payload(root, source))
    assert result["media"]["valid"] is True
    assert len(observed) == 3
