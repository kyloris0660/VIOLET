"""Layered read-only operation gateway and private evidence ledger for FL1-I1."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from scripts.fl1_i1_runtime_context import TrustedRuntimeContext


OPERATION_LEDGER_SCHEMA_VERSION = "violet.scv2-fl1-i1-operation-ledger.v1"
OPERATION_RECORD_SCHEMA_VERSION = "violet.scv2-fl1-i1-operation-record.v1"
CLOUD_OBSERVATION_SCHEMA_VERSION = "violet.scv2-fl1-i1-cloud-observation.v1"


class OperationGatewayError(RuntimeError):
    """Raised when a source operation cannot be safely attributed."""


class OperationKind(str, Enum):
    SOURCE_DIRECTORY_LIST = "source_directory_list"
    SOURCE_ENTRY_METADATA = "source_entry_metadata"
    SOURCE_CLOUD_ATTRIBUTE_OBSERVATION = "source_cloud_attribute_observation"
    SOURCE_FILE_READ = "source_file_read"
    SOURCE_FILE_HASH = "source_file_hash"


class OperationStatus(str, Enum):
    INTENT = "intent"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEFERRED = "deferred"


class CloudAvailability(str, Enum):
    AVAILABLE = "available"
    RECALL_RISK = "recall_risk"
    REPARSE_POINT = "reparse_point"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CloudAttributeObservation:
    availability: CloudAvailability
    attributes_known: bool
    reparse_point: bool
    offline: bool
    recall_on_open: bool
    recall_on_data_access: bool
    platform: str
    synthetic_observation: bool = False

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CLOUD_OBSERVATION_SCHEMA_VERSION,
            "availability": self.availability.value,
            "attributes_known": self.attributes_known,
            "reparse_point": self.reparse_point,
            "offline": self.offline,
            "recall_on_open": self.recall_on_open,
            "recall_on_data_access": self.recall_on_data_access,
            "platform": self.platform,
            "synthetic_observation": self.synthetic_observation,
        }


class AttributeAdapter(Protocol):
    def observe(self, path: Path) -> CloudAttributeObservation: ...


class WindowsCloudAttributeAdapter:
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_ATTRIBUTE_OFFLINE = 0x00001000
    FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
    INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

    def observe(self, path: Path) -> CloudAttributeObservation:
        if os.name != "nt":
            return CloudAttributeObservation(
                availability=CloudAvailability.UNKNOWN,
                attributes_known=False,
                reparse_point=False,
                offline=False,
                recall_on_open=False,
                recall_on_data_access=False,
                platform=os.name,
            )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_attributes = kernel32.GetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        attributes = int(get_attributes(os.fspath(path)))
        if attributes == self.INVALID_FILE_ATTRIBUTES:
            return CloudAttributeObservation(
                availability=CloudAvailability.UNKNOWN,
                attributes_known=False,
                reparse_point=False,
                offline=False,
                recall_on_open=False,
                recall_on_data_access=False,
                platform="windows",
            )
        reparse = bool(attributes & self.FILE_ATTRIBUTE_REPARSE_POINT)
        offline = bool(attributes & self.FILE_ATTRIBUTE_OFFLINE)
        recall_open = bool(attributes & self.FILE_ATTRIBUTE_RECALL_ON_OPEN)
        recall_data = bool(attributes & self.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
        if reparse:
            availability = CloudAvailability.REPARSE_POINT
        elif offline or recall_open or recall_data:
            availability = CloudAvailability.RECALL_RISK
        else:
            availability = CloudAvailability.AVAILABLE
        return CloudAttributeObservation(
            availability=availability,
            attributes_known=True,
            reparse_point=reparse,
            offline=offline,
            recall_on_open=recall_open,
            recall_on_data_access=recall_data,
            platform="windows",
        )


@dataclass(frozen=True)
class SyntheticAttributeAdapter:
    observations: Mapping[str, CloudAvailability | str]
    default: CloudAvailability = CloudAvailability.AVAILABLE

    def observe(self, path: Path) -> CloudAttributeObservation:
        raw = self.observations.get(path.name, self.default)
        try:
            availability = (
                raw if isinstance(raw, CloudAvailability) else CloudAvailability(raw)
            )
        except (TypeError, ValueError) as exc:
            raise OperationGatewayError("synthetic_cloud_observation_invalid") from exc
        return CloudAttributeObservation(
            availability=availability,
            attributes_known=availability is not CloudAvailability.UNKNOWN,
            reparse_point=availability is CloudAvailability.REPARSE_POINT,
            offline=availability is CloudAvailability.RECALL_RISK,
            recall_on_open=availability is CloudAvailability.RECALL_RISK,
            recall_on_data_access=False,
            platform="synthetic",
            synthetic_observation=True,
        )


@dataclass
class OperationRecord:
    operation_id: str
    sequence: int
    run_id: str
    invocation_id: str
    item_id: str | None
    attempt: int
    actual_git_head: str
    source_scope_fingerprint: str
    kind: OperationKind
    status: OperationStatus
    intent_timestamp_ns: int
    terminal_timestamp_ns: int | None = None
    safe_reason_code: str | None = None
    byte_count: int = 0
    observation: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPERATION_RECORD_SCHEMA_VERSION,
            "operation_id": self.operation_id,
            "sequence": self.sequence,
            "run_id": self.run_id,
            "invocation_id": self.invocation_id,
            "item_id": self.item_id,
            "attempt": self.attempt,
            "actual_git_head": self.actual_git_head,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "kind": self.kind.value,
            "status": self.status.value,
            "intent_timestamp_ns": self.intent_timestamp_ns,
            "terminal_timestamp_ns": self.terminal_timestamp_ns,
            "safe_reason_code": self.safe_reason_code,
            "byte_count": self.byte_count,
            "observation": dict(self.observation) if self.observation else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OperationRecord":
        if payload.get("schema_version") != OPERATION_RECORD_SCHEMA_VERSION:
            raise OperationGatewayError("operation_record_schema_invalid")
        try:
            record = cls(
                operation_id=str(payload["operation_id"]),
                sequence=int(payload["sequence"]),
                run_id=str(payload["run_id"]),
                invocation_id=str(payload["invocation_id"]),
                item_id=(str(payload["item_id"]) if payload.get("item_id") else None),
                attempt=int(payload["attempt"]),
                actual_git_head=str(payload["actual_git_head"]),
                source_scope_fingerprint=str(payload["source_scope_fingerprint"]),
                kind=OperationKind(payload["kind"]),
                status=OperationStatus(payload["status"]),
                intent_timestamp_ns=int(payload["intent_timestamp_ns"]),
                terminal_timestamp_ns=(
                    int(payload["terminal_timestamp_ns"])
                    if payload.get("terminal_timestamp_ns") is not None
                    else None
                ),
                safe_reason_code=(
                    str(payload["safe_reason_code"])
                    if payload.get("safe_reason_code") is not None
                    else None
                ),
                byte_count=int(payload.get("byte_count", 0)),
                observation=(
                    dict(payload["observation"])
                    if isinstance(payload.get("observation"), Mapping)
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationGatewayError("operation_record_invalid") from exc
        record.validate()
        return record

    def validate(self) -> None:
        try:
            uuid.UUID(self.operation_id)
            uuid.UUID(self.run_id)
            uuid.UUID(self.invocation_id)
        except (ValueError, AttributeError) as exc:
            raise OperationGatewayError("operation_identity_invalid") from exc
        if self.sequence < 1 or self.attempt < 0 or self.byte_count < 0:
            raise OperationGatewayError("operation_counter_invalid")
        if self.status is OperationStatus.INTENT:
            if self.terminal_timestamp_ns is not None:
                raise OperationGatewayError("operation_intent_terminal_timestamp")
        elif self.terminal_timestamp_ns is None:
            raise OperationGatewayError("operation_terminal_timestamp_missing")


@dataclass
class OperationLedger:
    run_id: str
    actual_git_head: str
    source_scope_fingerprint: str
    records: list[OperationRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        records = [record.to_dict() for record in self.records]
        return {
            "schema_version": OPERATION_LEDGER_SCHEMA_VERSION,
            "run_id": self.run_id,
            "actual_git_head": self.actual_git_head,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "records": records,
            "ledger_fingerprint": _fingerprint_records(records),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OperationLedger":
        if payload.get("schema_version") != OPERATION_LEDGER_SCHEMA_VERSION:
            raise OperationGatewayError("operation_ledger_schema_invalid")
        records_payload = payload.get("records")
        if not isinstance(records_payload, Sequence) or isinstance(
            records_payload, (str, bytes)
        ):
            raise OperationGatewayError("operation_ledger_records_invalid")
        ledger = cls(
            run_id=str(payload.get("run_id", "")),
            actual_git_head=str(payload.get("actual_git_head", "")),
            source_scope_fingerprint=str(payload.get("source_scope_fingerprint", "")),
            records=[
                OperationRecord.from_dict(record)
                for record in records_payload
                if isinstance(record, Mapping)
            ],
        )
        if len(ledger.records) != len(records_payload):
            raise OperationGatewayError("operation_ledger_record_invalid")
        ledger.validate()
        expected = _fingerprint_records([record.to_dict() for record in ledger.records])
        if payload.get("ledger_fingerprint") != expected:
            raise OperationGatewayError("operation_ledger_fingerprint_mismatch")
        return ledger

    def validate(self) -> None:
        try:
            uuid.UUID(self.run_id)
        except ValueError as exc:
            raise OperationGatewayError("operation_ledger_run_id_invalid") from exc
        for index, record in enumerate(self.records, start=1):
            record.validate()
            if (
                record.sequence != index
                or record.run_id != self.run_id
                or record.actual_git_head != self.actual_git_head
                or record.source_scope_fingerprint != self.source_scope_fingerprint
            ):
                raise OperationGatewayError("operation_ledger_binding_invalid")
        by_id = {record.operation_id: record for record in self.records}
        if len(by_id) != len(self.records):
            raise OperationGatewayError("operation_ledger_duplicate_id")

    @property
    def counts(self) -> dict[str, int]:
        return {
            kind.value: sum(
                record.kind is kind and record.status is OperationStatus.SUCCEEDED
                for record in self.records
            )
            for kind in OperationKind
        }


def _fingerprint_records(records: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one task-owned private JSON file atomically."""

    target = Path(path)
    if not target.is_absolute() or not target.parent.is_dir() or target.is_symlink():
        raise OperationGatewayError("private_artifact_target_invalid")
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if os.name != "nt":
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise OperationGatewayError("private_artifact_atomic_write_failed") from exc


def load_private_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationGatewayError("private_artifact_unreadable") from exc
    if not isinstance(payload, dict):
        raise OperationGatewayError("private_artifact_invalid")
    return payload


class OperationLedgerStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def save(self, ledger: OperationLedger) -> None:
        ledger.validate()
        atomic_write_json(self.path, ledger.to_dict())

    def load(self) -> OperationLedger:
        return OperationLedger.from_dict(load_private_json(self.path))


def _open_readonly_nofollow(path: Path) -> int:
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        return os.open(path, flags)

    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        os.fspath(path),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,  # OPEN_EXISTING
        0x00200000 | 0x08000000,  # OPEN_REPARSE_POINT | SEQUENTIAL_SCAN
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    try:
        return msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
    except Exception:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise


class OperationGateway:
    def __init__(
        self,
        *,
        context: TrustedRuntimeContext,
        store: OperationLedgerStore,
        run_id: str,
        invocation_id: str,
        attribute_adapter: AttributeAdapter,
    ) -> None:
        self.context = context
        self.store = store
        self.run_id = run_id
        self.invocation_id = invocation_id
        self.attribute_adapter = attribute_adapter
        if store.path.exists():
            self.ledger = store.load()
            if (
                self.ledger.run_id != run_id
                or self.ledger.actual_git_head != context.actual_git_head
                or self.ledger.source_scope_fingerprint
                != context.source_scope.scope_fingerprint
            ):
                raise OperationGatewayError("operation_ledger_context_drift")
        else:
            self.ledger = OperationLedger(
                run_id=run_id,
                actual_git_head=context.actual_git_head,
                source_scope_fingerprint=context.source_scope.scope_fingerprint,
            )
            store.save(self.ledger)

    def _intent(
        self,
        kind: OperationKind,
        *,
        item_id: str | None,
        attempt: int,
    ) -> OperationRecord:
        record = OperationRecord(
            operation_id=str(uuid.uuid4()),
            sequence=len(self.ledger.records) + 1,
            run_id=self.run_id,
            invocation_id=self.invocation_id,
            item_id=item_id,
            attempt=attempt,
            actual_git_head=self.context.actual_git_head,
            source_scope_fingerprint=self.context.source_scope.scope_fingerprint,
            kind=kind,
            status=OperationStatus.INTENT,
            intent_timestamp_ns=time.time_ns(),
        )
        self.ledger.records.append(record)
        self.store.save(self.ledger)
        return record

    def _terminal(
        self,
        record: OperationRecord,
        status: OperationStatus,
        reason: str,
        *,
        byte_count: int = 0,
        observation: Mapping[str, Any] | None = None,
    ) -> None:
        record.status = status
        record.terminal_timestamp_ns = time.time_ns()
        record.safe_reason_code = reason
        record.byte_count = byte_count
        record.observation = observation
        self.store.save(self.ledger)

    def _assert_contained(self, path: Path) -> Path:
        candidate = Path(os.path.abspath(Path(path)))
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise OperationGatewayError("source_path_unresolved") from exc
        root = self.context.source_scope.root
        if resolved != root and root not in resolved.parents:
            raise OperationGatewayError("source_path_escape")
        if resolved != candidate:
            raise OperationGatewayError("source_symlink_or_reparse_rejected")
        return resolved

    def _assert_metadata_target(self, path: Path) -> Path:
        """Validate a lexical target while leaving its final component no-follow."""

        candidate = Path(os.path.abspath(Path(path)))
        root = self.context.source_scope.root
        if candidate != root and root not in candidate.parents:
            raise OperationGatewayError("source_path_escape")
        parent = candidate if candidate == root else candidate.parent
        try:
            resolved_parent = parent.resolve(strict=True)
        except OSError as exc:
            raise OperationGatewayError("source_path_unresolved") from exc
        if resolved_parent != parent or (
            resolved_parent != root and root not in resolved_parent.parents
        ):
            raise OperationGatewayError("source_symlink_or_reparse_escape")
        return candidate

    def list_directory(self, path: Path) -> list[os.DirEntry[str]]:
        intent = self._intent(
            OperationKind.SOURCE_DIRECTORY_LIST, item_id=None, attempt=0
        )
        try:
            resolved = self._assert_contained(path)
            with os.scandir(resolved) as iterator:
                entries = sorted(list(iterator), key=lambda entry: entry.name)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, OperationGatewayError) else "directory_list_failed"
            self._terminal(intent, OperationStatus.FAILED, reason)
            if isinstance(exc, OperationGatewayError):
                raise
            raise OperationGatewayError("directory_list_failed") from exc
        self._terminal(intent, OperationStatus.SUCCEEDED, "directory_list_succeeded")
        return entries

    def stat_entry(
        self, path: Path, *, item_id: str | None = None, attempt: int = 0
    ) -> os.stat_result:
        candidate = Path(path)
        intent = self._intent(
            OperationKind.SOURCE_ENTRY_METADATA,
            item_id=item_id,
            attempt=attempt,
        )
        try:
            candidate = self._assert_metadata_target(candidate)
            metadata = os.stat(candidate, follow_symlinks=False)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, OperationGatewayError) else "metadata_failed"
            self._terminal(intent, OperationStatus.FAILED, reason)
            if isinstance(exc, OperationGatewayError):
                raise
            raise OperationGatewayError("metadata_failed") from exc
        self._terminal(intent, OperationStatus.SUCCEEDED, "metadata_succeeded")
        return metadata

    def observe_attributes(
        self, path: Path, *, item_id: str, attempt: int
    ) -> CloudAttributeObservation:
        intent = self._intent(
            OperationKind.SOURCE_CLOUD_ATTRIBUTE_OBSERVATION,
            item_id=item_id,
            attempt=attempt,
        )
        try:
            resolved = self._assert_contained(path)
            observation = self.attribute_adapter.observe(resolved)
        except Exception as exc:
            self._terminal(intent, OperationStatus.FAILED, "attribute_observation_failed")
            raise OperationGatewayError("attribute_observation_failed") from exc
        status = (
            OperationStatus.SUCCEEDED
            if observation.availability is CloudAvailability.AVAILABLE
            else OperationStatus.DEFERRED
        )
        self._terminal(
            intent,
            status,
            f"cloud_{observation.availability.value}",
            observation=observation.to_private_dict(),
        )
        return observation

    def hash_file(
        self,
        path: Path,
        *,
        item_id: str,
        attempt: int,
        expected_signature: tuple[int, int, int, int, int],
        chunk_size: int,
        timeout_seconds: float,
        max_bytes: int,
    ) -> tuple[str, int]:
        read_intent = self._intent(
            OperationKind.SOURCE_FILE_READ, item_id=item_id, attempt=attempt
        )
        hash_intent = self._intent(
            OperationKind.SOURCE_FILE_HASH, item_id=item_id, attempt=attempt
        )
        descriptor: int | None = None
        total = 0
        digest = hashlib.sha256()
        deadline = time.monotonic() + timeout_seconds
        try:
            resolved = self._assert_contained(path)
            descriptor = _open_readonly_nofollow(resolved)
            opened = os.fstat(descriptor)
            opened_signature = (
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_dev,
                opened.st_ino,
            )
            if opened_signature != expected_signature:
                raise OperationGatewayError("source_entry_changed_before_read")
            if not stat.S_ISREG(opened.st_mode):
                raise OperationGatewayError("source_special_file_rejected")
            while True:
                if time.monotonic() > deadline:
                    raise OperationGatewayError("source_read_timeout")
                chunk = os.read(descriptor, chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise OperationGatewayError("source_file_hash_budget_exceeded")
                digest.update(chunk)
            closed = os.fstat(descriptor)
            closed_signature = (
                closed.st_mode,
                closed.st_size,
                closed.st_mtime_ns,
                closed.st_dev,
                closed.st_ino,
            )
            if closed_signature != expected_signature or total != opened.st_size:
                raise OperationGatewayError("source_entry_changed_during_read")
        except Exception as exc:
            reason = (
                str(exc)
                if isinstance(exc, OperationGatewayError)
                else "source_file_read_failed"
            )
            self._terminal(
                read_intent, OperationStatus.FAILED, reason, byte_count=total
            )
            self._terminal(
                hash_intent, OperationStatus.FAILED, reason, byte_count=total
            )
            if isinstance(exc, OperationGatewayError):
                raise
            raise OperationGatewayError("source_file_read_failed") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._terminal(
            read_intent,
            OperationStatus.SUCCEEDED,
            "source_file_read_succeeded",
            byte_count=total,
        )
        self._terminal(
            hash_intent,
            OperationStatus.SUCCEEDED,
            "source_file_hash_succeeded",
            byte_count=total,
        )
        return digest.hexdigest(), total
