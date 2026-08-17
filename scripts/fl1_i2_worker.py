"""Terminable child-worker controller for all potentially blocking I2 work."""

from __future__ import annotations

import hashlib
import multiprocessing
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.app.services.source_ingestion_gate import SourceIngestionGate, SourceKind
from backend.app.services.source_safety import SourceSafetyPolicy
from scripts.fl1_i2_source_backends import SourceBackendError, current_handle_backend


class WorkerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WorkerOperation(str, Enum):
    LIST_DIRECTORY = "list_directory"
    HASH_FILE = "hash_file"
    VALIDATE_MEDIA = "validate_media"
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


def _member_payload(member: Any) -> dict[str, Any]:
    return {
        "name": member.name,
        "object_identity": member.object_identity.to_private_dict(),
        "attributes": member.attributes,
        "reparse_tag": member.reparse_tag,
    }


def _select_member(backend: Any, directory: Any, name: str) -> Any:
    matches = [member for member in backend.enumerate_directory(directory) if member.name == name]
    if len(matches) != 1:
        raise WorkerError("source_member_membership_changed")
    return matches[0]


def _execute(operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if operation == WorkerOperation.SYNTHETIC_BLOCK.value:
        multiprocessing.Event().wait(3600)
        raise AssertionError("unreachable")
    root = Path(str(payload["root"]))
    backend = current_handle_backend()
    policy = SourceSafetyPolicy.from_trusted_config(payload["policy"])
    with backend.open_directory(root) as directory:
        if operation == WorkerOperation.LIST_DIRECTORY.value:
            before = backend.observe(directory)
            members = backend.enumerate_directory(directory)
            after = backend.observe(directory)
            if before.object_identity != after.object_identity:
                raise WorkerError("source_directory_identity_drift")
            return {
                "directory_observation": before.to_private_dict(),
                "members": [_member_payload(member) for member in members],
            }
        name = str(payload["member_name"])
        member = _select_member(backend, directory, name)
        with backend.open_child(directory, member) as child:
            decision = SourceIngestionGate.decide_observation(
                source_kind=SourceKind.PATH_SOURCE,
                observation=child.observation,
                policy=policy,
            )
            if decision.blocked:
                raise WorkerError(decision.reason)
            before = backend.observe(child)
            max_bytes = int(payload["max_bytes"])
            if max_bytes <= 0:
                raise WorkerError("worker_byte_budget_invalid")
            if operation == WorkerOperation.HASH_FILE.value:
                digest = hashlib.sha256()
                byte_count = 0
                for chunk in backend.read_chunks(child, chunk_size=64 * 1024, max_bytes=max_bytes + 1):
                    byte_count += len(chunk)
                    if byte_count > max_bytes:
                        raise WorkerError("worker_byte_budget_exceeded")
                    digest.update(chunk)
                result = {"byte_count": byte_count, "content_fingerprint": digest.hexdigest()}
            elif operation == WorkerOperation.VALIDATE_MEDIA.value:
                from scripts.fl1_i2_media_validation import validate_media_stream

                result = validate_media_stream(
                    backend.read_chunks(child, chunk_size=64 * 1024, max_bytes=max_bytes + 1),
                    max_bytes=max_bytes,
                    max_depth=int(payload["max_depth"]),
                    deadline_monotonic=float(payload["parser_deadline_monotonic"]),
                ).to_private_dict()
            else:
                raise WorkerError("worker_operation_invalid")
            after = backend.observe(child)
            if before.object_identity != after.object_identity or before.change_identity != after.change_identity:
                raise WorkerError("source_object_changed_during_operation")
            return {
                **result,
                "object_identity": before.object_identity.to_private_dict(),
                "change_identity": before.change_identity.to_private_dict(),
                "policy_version": policy.policy_version,
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
    except (SourceBackendError, WorkerError, ValueError, KeyError) as exc:
        connection.send(("FAILED", {"safe_code": str(exc)}))
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
                return WorkerResult(WorkerStatus.COMPLETED, "worker_completed", result, started_persisted, True, int((time.monotonic() - started_at) * 1000))
            return WorkerResult(WorkerStatus.FAILED, str(result.get("safe_code", "worker_failed")), None, started_persisted, True, int((time.monotonic() - started_at) * 1000))
        except (EOFError, OSError, WorkerError):
            return self._terminate(process, started_at, started_persisted, "worker_channel_failed")
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
        )
