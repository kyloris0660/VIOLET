from __future__ import annotations

from pathlib import Path
import binascii
import zlib

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
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")


def _payload(root: Path, source: Path, *, max_bytes: int | None = None) -> dict[str, object]:
    backend = current_handle_backend()
    with backend.open_directory(root) as directory:
        member = next(item for item in backend.enumerate_directory(directory) if item.name == source.name)
        return {
            "root": str(root),
            "expected_root_observation": directory.observation.to_private_dict(),
            "ancestor_members": [],
            "member": {
                "name": member.name,
                "member_type": "file",
                "object_identity": member.object_identity.to_private_dict(),
                "change_identity": member.change_identity.to_private_dict(),
                "attributes": member.attributes,
                "reparse_tag": member.reparse_tag,
                "link_count": member.link_count,
            },
            "max_bytes": max_bytes if max_bytes is not None else max(1, member.change_identity.size),
            "max_depth": 100,
            "parser_deadline_monotonic": __import__("time").monotonic() + 5,
            "policy": _policy(),
            "enumeration_budget": {"max_entries": 100, "max_pages": 100, "max_metadata_bytes": 100000, "max_directories": 20, "max_depth": 10},
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


def test_terminated_worker_reports_shared_run_wide_byte_progress() -> None:
    result = WorkerController().run(
        WorkerOperation.SYNTHETIC_BLOCK,
        {"simulated_bytes_consumed": 37},
        deadline_seconds=0.2,
        persist_started=lambda: None,
    )
    assert result.status is WorkerStatus.INTERRUPTED
    assert result.bytes_consumed == 37


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
    payload["member"]["object_identity"] = {"platform": "windows", "volume_id": "1", "file_id": "1"}  # type: ignore[index]
    with pytest.raises(Exception, match="source_child_identity_mismatch"):
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


def test_content_operation_does_not_reenumerate_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "direct-open"
    root.mkdir()
    source = root / "fixture.png"
    source.write_bytes(_png())
    payload = _payload(root, source)
    backend_type = type(current_handle_backend())

    def forbidden_enumeration(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("content operation must use the manifest chain")

    monkeypatch.setattr(backend_type, "enumerate_directory", forbidden_enumeration)
    result = _execute(WorkerOperation.COMBINED_CONTENT.value, payload)
    assert result["media"]["valid"] is True


def test_recursive_enumeration_uses_one_run_wide_directory_budget(tmp_path: Path) -> None:
    root = tmp_path / "recursive-budget"
    (root / "one" / "two").mkdir(parents=True)
    with pytest.raises(Exception, match="directory_budget"):
        _execute(
            WorkerOperation.LIST_DIRECTORY.value,
            {
                "root": str(root),
                "policy": _policy(),
                "enumeration_budget": {
                    "max_entries": 100,
                    "max_pages": 100,
                    "max_metadata_bytes": 100000,
                    "max_directories": 1,
                    "max_depth": 10,
                },
            },
        )
