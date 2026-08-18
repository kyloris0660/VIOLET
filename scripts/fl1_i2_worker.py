"""Terminable child-worker controller for all potentially blocking I2 work."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import time
import traceback
from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.app.services.source_ingestion_gate import SourceIngestionGate, SourceKind
from backend.app.services.source_safety import (
    FileChangeIdentity,
    FileObjectIdentity,
    HandleObservation,
    SourceSafetyPolicy,
)
from scripts.fl1_i2_source_backends import (
    DirectoryMember,
    EnumerationBudget,
    EnumerationUsage,
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
    FINAL_SNAPSHOT = "final_snapshot"
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
        "member_type": member.member_type,
        "object_identity": member.object_identity.to_private_dict(),
        "change_identity": member.change_identity.to_private_dict(),
        "attributes": member.attributes,
        "reparse_tag": member.reparse_tag,
        "link_count": member.link_count,
    }


def _object_identity(payload: Mapping[str, Any]) -> FileObjectIdentity:
    try:
        return FileObjectIdentity.from_private_dict(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerError("worker_expected_object_identity_invalid") from exc


def _change_identity(payload: Mapping[str, Any]) -> FileChangeIdentity:
    try:
        return FileChangeIdentity.from_private_dict(payload)
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
            max_directories=int(raw["max_directories"]),
            max_depth=int(raw["max_depth"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerError("worker_enumeration_budget_invalid") from exc


def _gate_observation(observation: Any, policy: SourceSafetyPolicy) -> None:
    decision = SourceIngestionGate.decide_observation(
        source_kind=SourceKind.PATH_SOURCE,
        observation=observation,
        policy=policy,
    )
    if decision.blocked:
        raise WorkerError(decision.reason)


def _directory_observation_allowed(observation: HandleObservation) -> None:
    if (
        not observation.is_directory
        or not observation.attributes_known
        or not observation.no_follow
        or not observation.identity_bound
        or observation.reparse_point
        or observation.cloud_availability.value != "available"
    ):
        raise WorkerError("source_directory_observation_rejected")


def _recursive_snapshot(
    backend: Any,
    root: Any,
    *,
    budget: EnumerationBudget,
    policy: SourceSafetyPolicy,
) -> dict[str, Any]:
    usage = EnumerationUsage()
    directory_records: list[dict[str, Any]] = []
    file_records: list[dict[str, Any]] = []
    seen_objects: set[FileObjectIdentity] = set()
    seen_paths: set[tuple[str, ...]] = set()

    def visit(directory: Any, chain: tuple[str, ...], depth: int, parent: HandleObservation | None) -> None:
        before = backend.observe(directory)
        _directory_observation_allowed(before)
        if before.object_identity in seen_objects or chain in seen_paths:
            raise WorkerError("source_alias_identity_rejected")
        seen_objects.add(before.object_identity)
        seen_paths.add(chain)
        directory_record = {
            "component_chain": list(chain),
            "parent_object_identity": parent.object_identity.to_private_dict() if parent else None,
            "parent_change_identity": parent.change_identity.to_private_dict() if parent else None,
            "observation": before.to_private_dict(),
            "page_count": 0,
            "entry_count": 0,
            "metadata_observations": 0,
            "metadata_bytes": 0,
            "pages": [],
        }
        directory_records.append(directory_record)
        usage_before = usage.to_private_dict()
        members = backend.enumerate_directory(
            directory,
            budget=budget,
            usage=usage,
            depth=depth,
        )
        usage_after = usage.to_private_dict()
        directory_record.update(
            {
                "page_count": usage_after["pages"] - usage_before["pages"],
                "entry_count": usage_after["entries"] - usage_before["entries"],
                "metadata_observations": (
                    usage_after["metadata_observations"]
                    - usage_before["metadata_observations"]
                ),
                "metadata_bytes": (
                    usage_after["metadata_bytes"] - usage_before["metadata_bytes"]
                ),
                "pages": usage_after["page_records"][usage_before["pages"] :],
            }
        )
        for member in sorted(members, key=lambda value: (value.name.casefold(), value.name)):
            child_chain = (*chain, member.name)
            if member.object_identity in seen_objects or child_chain in seen_paths:
                raise WorkerError("source_alias_identity_rejected")
            if member.member_type == "directory":
                with backend.open_discovered_directory(directory, member) as child_directory:
                    visit(child_directory, child_chain, depth + 1, before)
                continue
            if member.member_type != "file":
                raise WorkerError("source_member_type_invalid")
            with backend.open_file_child(directory, member) as child:
                observed = child.observation
                _gate_observation(observed, policy)
                if observed.object_identity in seen_objects:
                    raise WorkerError("source_alias_identity_rejected")
                seen_objects.add(observed.object_identity)
                seen_paths.add(child_chain)
                file_records.append(
                    {
                        **_member_payload(
                            DirectoryMember(
                                member.name,
                                "file",
                                observed.object_identity,
                                observed.change_identity,
                                member.attributes,
                                member.reparse_tag,
                                observed.link_count,
                            )
                        ),
                        "component_chain": list(child_chain),
                        "parent_object_identity": before.object_identity.to_private_dict(),
                        "parent_change_identity": before.change_identity.to_private_dict(),
                    }
                )
        after = backend.observe(directory)
        if before.object_identity != after.object_identity or before.change_identity != after.change_identity:
            raise WorkerError("source_directory_identity_drift")

    visit(root, (), 0, None)
    return {
        "directory_observation": root.observation.to_private_dict(),
        "directories": directory_records,
        "members": file_records,
        "enumeration_usage": usage.to_private_dict(),
    }


def _manifest_member(payload: Mapping[str, Any], *, member_type: str) -> DirectoryMember:
    expected = {
        "name",
        "member_type",
        "object_identity",
        "change_identity",
        "attributes",
        "reparse_tag",
        "link_count",
    }
    if set(payload) != expected or payload.get("member_type") != member_type:
        raise WorkerError("worker_manifest_member_invalid")
    if type(payload["name"]) is not str or type(payload["attributes"]) is not int:
        raise WorkerError("worker_manifest_member_invalid")
    if type(payload["reparse_tag"]) is not int or type(payload["link_count"]) is not int:
        raise WorkerError("worker_manifest_member_invalid")
    return DirectoryMember(
        payload["name"],
        member_type,
        _object_identity(payload["object_identity"]),
        _change_identity(payload["change_identity"]),
        payload["attributes"],
        payload["reparse_tag"],
        payload["link_count"],
    )


class _DigestingChunks:
    def __init__(self, chunks: Any, max_bytes: int, progress_counter: Any | None = None) -> None:
        self._chunks = iter(chunks)
        self.max_bytes = max_bytes
        self.byte_count = 0
        self.digest = hashlib.sha256()
        self._progress_counter = progress_counter

    def __iter__(self) -> "_DigestingChunks":
        return self

    def __next__(self) -> bytes:
        chunk = next(self._chunks)
        self.byte_count += len(chunk)
        if self._progress_counter is not None:
            with self._progress_counter.get_lock():
                self._progress_counter.value = self.byte_count
        if self.byte_count > self.max_bytes:
            raise WorkerError(
                "worker_byte_budget_exceeded",
                bytes_consumed=self.byte_count,
            )
        self.digest.update(chunk)
        return chunk


def _execute(
    operation: str,
    payload: Mapping[str, Any],
    progress_counter: Any | None = None,
) -> dict[str, Any]:
    if operation == WorkerOperation.SYNTHETIC_BLOCK.value:
        simulated = int(payload.get("simulated_bytes_consumed", 0))
        if progress_counter is not None and simulated:
            with progress_counter.get_lock():
                progress_counter.value = simulated
        multiprocessing.Event().wait(3600)
        raise AssertionError("unreachable")
    root = Path(str(payload["root"]))
    backend = current_handle_backend()
    policy = SourceSafetyPolicy.from_trusted_config(payload["policy"])
    enumeration_budget = _enumeration_budget(payload)
    with backend.open_directory(root) as directory:
        if operation in {WorkerOperation.LIST_DIRECTORY.value, WorkerOperation.FINAL_SNAPSHOT.value}:
            return _recursive_snapshot(
                backend,
                directory,
                budget=enumeration_budget,
                policy=policy,
            )
        if operation != WorkerOperation.COMBINED_CONTENT.value:
            raise WorkerError("worker_operation_invalid")
        expected_root = payload["expected_root_observation"]
        if not isinstance(expected_root, Mapping):
            raise WorkerError("worker_expected_root_observation_invalid")
        try:
            expected_root_observation = HandleObservation.from_private_dict(expected_root)
        except ValueError as exc:
            raise WorkerError("worker_expected_root_observation_invalid") from exc
        _directory_observation_allowed(directory.observation)
        if directory.observation.object_identity != expected_root_observation.object_identity:
            raise WorkerError("source_root_object_identity_drift")
        if directory.observation.change_identity != expected_root_observation.change_identity:
            raise WorkerError("source_root_change_identity_drift")
        ancestors = payload.get("ancestor_members")
        leaf_payload = payload.get("member")
        if not isinstance(ancestors, list) or not isinstance(leaf_payload, Mapping):
            raise WorkerError("worker_manifest_chain_invalid")
        with ExitStack() as stack:
            parent = directory
            for raw_ancestor in ancestors:
                if not isinstance(raw_ancestor, Mapping):
                    raise WorkerError("worker_manifest_chain_invalid")
                ancestor = _manifest_member(raw_ancestor, member_type="directory")
                parent = stack.enter_context(backend.open_directory_child(parent, ancestor))
                _directory_observation_allowed(parent.observation)
            member = _manifest_member(leaf_payload, member_type="file")
            child = stack.enter_context(backend.open_file_child(parent, member))
            opened = child.observation
            _gate_observation(opened, policy)
            before = backend.observe(child)
            _gate_observation(before, policy)
            if opened != before:
                raise WorkerError("source_object_changed_before_read")
            max_bytes = int(payload["max_bytes"])
            if max_bytes <= 0:
                raise WorkerError("worker_byte_budget_invalid")
            expected_change = member.change_identity
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
                progress_counter,
            )
            try:
                media = validate_media_stream(
                    stream,
                    max_bytes=max_bytes,
                    max_depth=int(payload["max_depth"]),
                    deadline_monotonic=float(payload["parser_deadline_monotonic"]),
                    max_decoded_structure_bytes=int(
                        payload.get("max_decoded_structure_bytes", 64 * 1024 * 1024)
                    ),
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


def _worker_main(
    connection: Any,
    operation: str,
    payload: Mapping[str, Any],
    progress_counter: Any,
) -> None:
    try:
        connection.send(("READY", None))
        message, _data = connection.recv()
        if message != "GO":
            connection.send(("FAILED", {"safe_code": "worker_go_protocol_invalid"}))
            return
        result = _execute(operation, payload, progress_counter)
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
        progress_counter = context.Value("Q", 0, lock=True)
        process = context.Process(
            target=_worker_main,
            args=(child, operation.value, dict(payload), progress_counter),
        )
        process.start()
        child.close()
        started_persisted = False
        try:
            if not parent.poll(min(self.ready_timeout_seconds, deadline_seconds)):
                return self._terminate(
                    process,
                    started_at,
                    started_persisted,
                    "worker_ready_timeout",
                    progress_counter,
                )
            message, _payload = parent.recv()
            if message != "READY":
                return self._terminate(
                    process,
                    started_at,
                    started_persisted,
                    "worker_ready_protocol_invalid",
                    progress_counter,
                )
            persist_started()
            started_persisted = True
            parent.send(("GO", None))
            remaining = deadline_seconds - (time.monotonic() - started_at)
            if remaining <= 0 or not parent.poll(remaining):
                return self._terminate(
                    process,
                    started_at,
                    started_persisted,
                    "worker_deadline_exceeded",
                    progress_counter,
                )
            message, result = parent.recv()
            process.join(self.exit_timeout_seconds)
            if process.is_alive():
                return self._terminate(
                    process,
                    started_at,
                    started_persisted,
                    "worker_exit_unconfirmed",
                    progress_counter,
                )
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
            return self._terminate(
                process,
                started_at,
                started_persisted,
                "worker_channel_failed",
                progress_counter,
            )
        except BaseException:
            self._terminate(
                process,
                started_at,
                started_persisted,
                "worker_parent_interrupted",
                progress_counter,
            )
            raise
        finally:
            parent.close()

    def _terminate(
        self,
        process: multiprocessing.Process,
        started_at: float,
        started_persisted: bool,
        code: str,
        progress_counter: Any | None = None,
    ) -> WorkerResult:
        if process.is_alive():
            process.terminate()
        process.join(self.exit_timeout_seconds)
        if process.is_alive():
            process.kill()
            process.join(self.exit_timeout_seconds)
        confirmed = not process.is_alive()
        consumed = 0
        if progress_counter is not None:
            with progress_counter.get_lock():
                consumed = int(progress_counter.value)
        return WorkerResult(
            WorkerStatus.INTERRUPTED if confirmed else WorkerStatus.BLOCKED,
            code if confirmed else "worker_termination_unconfirmed",
            None,
            started_persisted,
            confirmed,
            int((time.monotonic() - started_at) * 1000),
            consumed,
        )
