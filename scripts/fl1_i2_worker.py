"""Terminable child-worker controller for all potentially blocking I2 work."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.app.services.source_ingestion_gate import SourceIngestionGate, SourceKind
from backend.app.services.source_safety import (
    FileChangeIdentity,
    FileObjectIdentity,
    SourceSafetyPolicy,
)
from scripts.fl1_i2_source_backends import (
    EnumerationBudget,
    SourceBackendError,
    current_handle_backend,
)
from scripts.fl1_i2_media_validation import MediaValidationError


class WorkerError(RuntimeError):
    def __init__(self, code: str, *, bytes_consumed: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.bytes_consumed = bytes_consumed


class WorkerOperation(str, Enum):
    LIST_DIRECTORY = "list_directory"
    COMBINED_CONTENT = "combined_content"
    SYNTHETIC_BLOCK = "synthetic_block"


class WorkerStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WorkerResult:
    status: WorkerStatus
    safe_code: str
    payload: Mapping[str, Any] | None
    started_persisted: bool
    exit_confirmed: bool
    elapsed_ms: int
    bytes_consumed: int = 0


def _member_payload(member: Any) -> dict[str, Any]:
    return {
        "name": member.name,
        "object_identity": member.object_identity.to_private_dict(),
        "change_identity": member.change_identity.to_private_dict(),
        "attributes": member.attributes,
        "reparse_tag": member.reparse_tag,
    }


def _object_identity(payload: Mapping[str, Any]) -> FileObjectIdentity:
    try:
        return FileObjectIdentity(
            str(payload["platform"]),
            str(payload["volume_id"]),
            str(payload["file_id"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerError("worker_expected_object_identity_invalid") from exc


def _change_identity(payload: Mapping[str, Any]) -> FileChangeIdentity:
    try:
        return FileChangeIdentity(
            int(payload["change_time_ns"]),
            int(payload["write_time_ns"]),
            int(payload["size"]),
            int(payload["allocation_size"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerError("worker_expected_change_identity_invalid") from exc


def _enumeration_budget(payload: Mapping[str, Any]) -> EnumerationBudget:
    try:
        raw = payload["enumeration_budget"]
        if not isinstance(raw, Mapping):
            raise TypeError
        return EnumerationBudget(
            max_entries=int(raw["max_entries"]),
            max_pages=int(raw["max_pages"]),
            max_metadata_bytes=int(raw["max_metadata_bytes"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerError("worker_enumeration_budget_invalid") from exc


def _select_member(
    backend: Any,
    directory: Any,
    name: str,
    expected_object: FileObjectIdentity,
    expected_change: FileChangeIdentity,
    budget: EnumerationBudget,
) -> Any:
    matches = [
        member
        for member in backend.enumerate_directory(directory, budget=budget)
        if member.name == name
    ]
    if len(matches) != 1:
        raise WorkerError("source_member_membership_changed")
    member = matches[0]
    if member.object_identity != expected_object:
        raise WorkerError("source_member_object_identity_drift")
    if member.change_identity != expected_change:
        raise WorkerError("source_member_change_identity_drift")
    return member


def _gate_observation(observation: Any, policy: SourceSafetyPolicy) -> None:
    decision = SourceIngestionGate.decide_observation(
        source_kind=SourceKind.PATH_SOURCE,
        observation=observation,
        policy=policy,
    )
    if decision.blocked:
        raise WorkerError(decision.reason)


class _DigestingChunks:
    def __init__(self, chunks: Any, max_bytes: int) -> None:
        self._chunks = iter(chunks)
        self.max_bytes = max_bytes
        self.byte_count = 0
        self.digest = hashlib.sha256()

    def __iter__(self) -> "_DigestingChunks":
        return self

    def __next__(self) -> bytes:
        chunk = next(self._chunks)
        self.byte_count += len(chunk)
        if self.byte_count > self.max_bytes:
            raise WorkerError(
                "worker_byte_budget_exceeded",
                bytes_consumed=self.byte_count,
            )
        self.digest.update(chunk)
        return chunk


def _execute(operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if operation == WorkerOperation.SYNTHETIC_BLOCK.value:
        multiprocessing.Event().wait(3600)
        raise AssertionError("unreachable")
    root = Path(str(payload["root"]))
    backend = current_handle_backend()
    policy = SourceSafetyPolicy.from_trusted_config(payload["policy"])
    enumeration_budget = _enumeration_budget(payload)
    with backend.open_directory(root) as directory:
        if operation == WorkerOperation.LIST_DIRECTORY.value:
            before = backend.observe(directory)
            members = backend.enumerate_directory(directory, budget=enumeration_budget)
            after = backend.observe(directory)
            if before.object_identity != after.object_identity or before.change_identity != after.change_identity:
                raise WorkerError("source_directory_identity_drift")
            return {
                "directory_observation": before.to_private_dict(),
                "members": [_member_payload(member) for member in members],
            }
        if operation != WorkerOperation.COMBINED_CONTENT.value:
            raise WorkerError("worker_operation_invalid")
        expected_root = payload["expected_root_observation"]
        if not isinstance(expected_root, Mapping):
            raise WorkerError("worker_expected_root_observation_invalid")
        if directory.observation.object_identity != _object_identity(expected_root["object_identity"]):
            raise WorkerError("source_root_object_identity_drift")
        if directory.observation.change_identity != _change_identity(expected_root["change_identity"]):
            raise WorkerError("source_root_change_identity_drift")
        name = str(payload["member_name"])
        expected_object = _object_identity(payload["expected_member_object_identity"])
        expected_change = _change_identity(payload["expected_member_change_identity"])
        member = _select_member(
            backend,
            directory,
            name,
            expected_object,
            expected_change,
            enumeration_budget,
        )
        with backend.open_child(directory, member) as child:
            opened = child.observation
            _gate_observation(opened, policy)
            before = backend.observe(child)
            _gate_observation(before, policy)
            if opened != before:
                raise WorkerError("source_object_changed_before_read")
            max_bytes = int(payload["max_bytes"])
            if max_bytes <= 0:
                raise WorkerError("worker_byte_budget_invalid")
            if max_bytes != max(1, expected_change.size):
                raise WorkerError("worker_byte_reservation_mismatch")
            from scripts.fl1_i2_media_validation import validate_media_stream

            stream = _DigestingChunks(
                backend.read_chunks(
                    child,
                    chunk_size=64 * 1024,
                    max_bytes=max_bytes,
                ),
                max_bytes,
            )
            try:
                media = validate_media_stream(
                    stream,
                    max_bytes=max_bytes,
                    max_depth=int(payload["max_depth"]),
                    deadline_monotonic=float(payload["parser_deadline_monotonic"]),
                )
            except (MediaValidationError, SourceBackendError, WorkerError) as exc:
                code = exc.code if isinstance(exc, (SourceBackendError, WorkerError)) else str(exc)
                raise WorkerError(code, bytes_consumed=stream.byte_count) from exc
            after = backend.observe(child)
            _gate_observation(after, policy)
            if before != after:
                raise WorkerError("source_object_changed_during_operation")
            return {
                "byte_count": stream.byte_count,
                "content_fingerprint": stream.digest.hexdigest(),
                "media": media.to_private_dict(),
                "object_identity": before.object_identity.to_private_dict(),
                "change_identity": before.change_identity.to_private_dict(),
                "policy_version": policy.policy_version,
                "policy_fingerprint": hashlib.sha256(
                    json.dumps(
                        policy.to_fingerprint_payload(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "opened_observation": opened.to_private_dict(),
                "pre_read_observation": before.to_private_dict(),
                "post_read_observation": after.to_private_dict(),
            }


def _worker_main(connection: Any, operation: str, payload: Mapping[str, Any]) -> None:
    try:
        connection.send(("READY", None))
        message, _data = connection.recv()
        if message != "GO":
            connection.send(("FAILED", {"safe_code": "worker_go_protocol_invalid"}))
            return
        result = _execute(operation, payload)
        connection.send(("COMPLETED", result))
    except (SourceBackendError, WorkerError, MediaValidationError, ValueError, KeyError) as exc:
        connection.send(
            (
                "FAILED",
                {
                    "safe_code": str(exc),
                    "bytes_consumed": getattr(exc, "bytes_consumed", 0),
                },
            )
        )
    except BaseException:
        # Traceback remains inside the child and is never projected publicly.
        traceback.format_exc()
        try:
            connection.send(("FAILED", {"safe_code": "worker_unknown_failure"}))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class WorkerController:
    def __init__(self, *, ready_timeout_seconds: float = 3.0, exit_timeout_seconds: float = 3.0) -> None:
        if ready_timeout_seconds <= 0 or exit_timeout_seconds <= 0:
            raise ValueError("worker_controller_timeout_invalid")
        self.ready_timeout_seconds = ready_timeout_seconds
        self.exit_timeout_seconds = exit_timeout_seconds

    def run(
        self,
        operation: WorkerOperation,
        payload: Mapping[str, Any],
        *,
        deadline_seconds: float,
        persist_started: Callable[[], None],
    ) -> WorkerResult:
        if deadline_seconds <= 0:
            raise WorkerError("worker_deadline_invalid")
        started_at = time.monotonic()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(target=_worker_main, args=(child, operation.value, dict(payload)))
        process.start()
        child.close()
        started_persisted = False
        try:
            if not parent.poll(min(self.ready_timeout_seconds, deadline_seconds)):
                return self._terminate(process, started_at, started_persisted, "worker_ready_timeout")
            message, _payload = parent.recv()
            if message != "READY":
                return self._terminate(process, started_at, started_persisted, "worker_ready_protocol_invalid")
            persist_started()
            started_persisted = True
            parent.send(("GO", None))
            remaining = deadline_seconds - (time.monotonic() - started_at)
            if remaining <= 0 or not parent.poll(remaining):
                return self._terminate(process, started_at, started_persisted, "worker_deadline_exceeded")
            message, result = parent.recv()
            process.join(self.exit_timeout_seconds)
            if process.is_alive():
                return self._terminate(process, started_at, started_persisted, "worker_exit_unconfirmed")
            if message == "COMPLETED":
                return WorkerResult(
                    WorkerStatus.COMPLETED,
                    "worker_completed",
                    result,
                    started_persisted,
                    True,
                    int((time.monotonic() - started_at) * 1000),
                    int(result.get("byte_count", 0)),
                )
            return WorkerResult(
                WorkerStatus.FAILED,
                str(result.get("safe_code", "worker_failed")),
                None,
                started_persisted,
                True,
                int((time.monotonic() - started_at) * 1000),
                int(result.get("bytes_consumed", 0)),
            )
        except (EOFError, OSError, WorkerError):
            return self._terminate(process, started_at, started_persisted, "worker_channel_failed")
        except BaseException:
            self._terminate(process, started_at, started_persisted, "worker_parent_interrupted")
            raise
        finally:
            parent.close()

    def _terminate(self, process: multiprocessing.Process, started_at: float, started_persisted: bool, code: str) -> WorkerResult:
        if process.is_alive():
            process.terminate()
        process.join(self.exit_timeout_seconds)
        if process.is_alive():
            process.kill()
            process.join(self.exit_timeout_seconds)
        confirmed = not process.is_alive()
        return WorkerResult(
            WorkerStatus.INTERRUPTED if confirmed else WorkerStatus.BLOCKED,
            code if confirmed else "worker_termination_unconfirmed",
            None,
            started_persisted,
            confirmed,
            int((time.monotonic() - started_at) * 1000),
            0,
        )
