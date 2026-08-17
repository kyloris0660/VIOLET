"""Layered read-only operation gateway and private evidence ledger for FL1-I1."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
import time
import uuid
from types import MappingProxyType
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from scripts.fl1_i1_runtime_context import TrustedRuntimeContext
from backend.app.services.source_safety import CloudAvailability


OPERATION_LEDGER_SCHEMA_VERSION = "violet.scv2-fl1-i1-operation-ledger.v1"
OPERATION_RECORD_SCHEMA_VERSION = "violet.scv2-fl1-i1-operation-record.v1"
CLOUD_OBSERVATION_SCHEMA_VERSION = "violet.scv2-fl1-i1-cloud-observation.v1"


class OperationGatewayError(RuntimeError):
    """Raised when a source operation cannot be safely attributed."""

    def __init__(self, code: str, *, byte_count: int = 0) -> None:
        super().__init__(code)
        self.byte_count = byte_count


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
    INTERRUPTED = "interrupted"


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


def attribute_adapter_identity(adapter: AttributeAdapter) -> Mapping[str, Any]:
    if isinstance(adapter, WindowsCloudAttributeAdapter):
        payload: dict[str, Any] = {
            "adapter_type": "windows_cloud_files_attributes_v1",
            "adapter_class": f"{type(adapter).__module__}.{type(adapter).__qualname__}",
            "configuration": {},
        }
    elif isinstance(adapter, SyntheticAttributeAdapter):
        payload = {
            "adapter_type": "synthetic_attribute_observation_v1",
            "adapter_class": f"{type(adapter).__module__}.{type(adapter).__qualname__}",
            "configuration": {
                "observations": {
                    str(key): (
                        value.value if isinstance(value, CloudAvailability) else str(value)
                    )
                    for key, value in sorted(adapter.observations.items())
                },
                "default": adapter.default.value,
            },
        }
    else:
        raise OperationGatewayError("attribute_adapter_type_untrusted")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "adapter_type": payload["adapter_type"],
        "configuration_fingerprint": hashlib.sha256(canonical).hexdigest(),
    }


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

    def __post_init__(self) -> None:
        normalized: dict[str, CloudAvailability] = {}
        try:
            for key, value in self.observations.items():
                normalized[str(key)] = (
                    value
                    if isinstance(value, CloudAvailability)
                    else CloudAvailability(value)
                )
        except (TypeError, ValueError) as exc:
            raise OperationGatewayError("synthetic_cloud_observation_invalid") from exc
        object.__setattr__(self, "observations", MappingProxyType(normalized))
        try:
            normalized_default = (
                self.default
                if isinstance(self.default, CloudAvailability)
                else CloudAvailability(self.default)
            )
        except (TypeError, ValueError) as exc:
            raise OperationGatewayError("synthetic_cloud_observation_invalid") from exc
        object.__setattr__(self, "default", normalized_default)

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
    reserved_byte_count: int = 0
    target_token: str | None = None
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
            "reserved_byte_count": self.reserved_byte_count,
            "target_token": self.target_token,
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
                reserved_byte_count=int(payload.get("reserved_byte_count", 0)),
                target_token=(
                    str(payload["target_token"])
                    if payload.get("target_token") is not None
                    else None
                ),
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
        if (
            self.sequence < 1
            or self.attempt < 0
            or self.byte_count < 0
            or self.reserved_byte_count < 0
        ):
            raise OperationGatewayError("operation_counter_invalid")
        if self.kind is OperationKind.SOURCE_DIRECTORY_LIST and not self.target_token:
            raise OperationGatewayError("directory_listing_target_identity_missing")
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


def _path_has_reparse_or_symlink(path: Path) -> bool:
    metadata = os.lstat(path)
    return bool(
        stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


class TaskOwnedArtifactStore:
    """Constrain every I1 artifact write to one trusted evidence root."""

    def __init__(self, root: Path) -> None:
        candidate = Path(root)
        if not candidate.is_absolute():
            raise OperationGatewayError("private_artifact_root_invalid")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise OperationGatewayError("private_artifact_root_invalid") from exc
        if resolved != candidate or not resolved.is_dir() or _path_has_reparse_or_symlink(resolved):
            raise OperationGatewayError("private_artifact_root_invalid")
        self.root = resolved

    def target(self, path: Path) -> Path:
        raw = Path(path)
        if any(part == ".." for part in raw.parts):
            raise OperationGatewayError("private_artifact_target_escape")
        target = raw if raw.is_absolute() else self.root / raw
        target = Path(os.path.abspath(os.fspath(target)))
        try:
            if os.path.commonpath((os.fspath(target), os.fspath(self.root))) != os.fspath(self.root):
                raise OperationGatewayError("private_artifact_target_escape")
        except ValueError as exc:
            raise OperationGatewayError("private_artifact_target_escape") from exc
        if target == self.root:
            raise OperationGatewayError("private_artifact_target_invalid")
        current = self.root
        relative_parts = target.relative_to(self.root).parts
        for part in relative_parts[:-1]:
            current = current / part
            try:
                if not current.is_dir() or _path_has_reparse_or_symlink(current):
                    raise OperationGatewayError("private_artifact_parent_untrusted")
            except OSError as exc:
                raise OperationGatewayError("private_artifact_parent_untrusted") from exc
        if target.exists() or target.is_symlink():
            try:
                if _path_has_reparse_or_symlink(target) or not target.is_file():
                    raise OperationGatewayError("private_artifact_target_untrusted")
            except OSError as exc:
                raise OperationGatewayError("private_artifact_target_untrusted") from exc
        return target

    def atomic_write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        target = self.target(path)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        descriptor: int | None = None
        try:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
            descriptor = os.open(temporary, flags, 0o600)
            if os.name != "nt":
                metadata = os.fstat(descriptor)
                if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
                    raise OperationGatewayError("private_artifact_mode_or_owner_invalid")
            data = (
                json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            offset = 0
            while offset < len(data):
                offset += os.write(descriptor, data[offset:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, target)
            if os.name != "nt":
                metadata = os.stat(target, follow_symlinks=False)
                if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
                    raise OperationGatewayError("private_artifact_mode_or_owner_invalid")
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OperationGatewayError:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise OperationGatewayError("private_artifact_atomic_write_failed") from exc


def atomic_write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    artifact_root: Path | None = None,
) -> None:
    """Compatibility adapter; callers must supply the trusted evidence root."""

    if artifact_root is None:
        raise OperationGatewayError("private_artifact_root_required")
    TaskOwnedArtifactStore(artifact_root).atomic_write_json(Path(path), payload)


def load_private_json(path: Path) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        target = Path(path)
        descriptor = _open_readonly_nofollow(target)
        opened = os.fstat(descriptor)
        named = os.stat(target, follow_symlinks=False)
        if (
            stat.S_ISLNK(named.st_mode)
            or getattr(named, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or not stat.S_ISREG(opened.st_mode)
        ):
            raise OperationGatewayError("private_artifact_target_untrusted")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = None
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationGatewayError("private_artifact_unreadable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise OperationGatewayError("private_artifact_invalid")
    return payload


class OperationLedgerStore:
    def __init__(self, path: Path, *, artifact_store: TaskOwnedArtifactStore) -> None:
        self.path = Path(path)
        self.artifact_store = artifact_store

    def save(self, ledger: OperationLedger) -> None:
        ledger.validate()
        self.artifact_store.atomic_write_json(self.path, ledger.to_dict())

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
        on_persist: Callable[[OperationLedger], None] | None = None,
        after_intent: Callable[[OperationRecord], None] | None = None,
    ) -> None:
        self.context = context
        self.store = store
        self.run_id = run_id
        self.invocation_id = invocation_id
        self.attribute_adapter = attribute_adapter
        self.on_persist = on_persist
        self.after_intent = after_intent
        self.last_content_probe: tuple[bytes, bytes] = (b"", b"")
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

    def _save(self) -> None:
        self.store.save(self.ledger)
        if self.on_persist is not None:
            self.on_persist(self.ledger)

    def _intent(
        self,
        kind: OperationKind,
        *,
        item_id: str | None,
        attempt: int,
        reserved_byte_count: int = 0,
        target_token: str | None = None,
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
            reserved_byte_count=reserved_byte_count,
            target_token=target_token,
        )
        self.ledger.records.append(record)
        self._save()
        if self.after_intent is not None:
            self.after_intent(record)
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
        self._save()

    def reconcile_interrupted_intents(self, *, dead_invocation_id: str) -> int:
        reconciled = 0
        for record in self.ledger.records:
            if record.invocation_id == dead_invocation_id and record.status is OperationStatus.INTENT:
                record.status = OperationStatus.INTERRUPTED
                record.terminal_timestamp_ns = time.time_ns()
                record.safe_reason_code = "prior_invocation_interrupted"
                record.byte_count = record.reserved_byte_count
                reconciled += 1
        if reconciled:
            self._save()
        return reconciled

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

    def list_directory(
        self,
        path: Path,
        *,
        target_token: str,
        max_entries: int | None = None,
        deadline_monotonic: float | None = None,
    ) -> list[os.DirEntry[str]]:
        intent = self._intent(
            OperationKind.SOURCE_DIRECTORY_LIST,
            item_id=None,
            attempt=0,
            target_token=target_token,
        )
        try:
            resolved = self._assert_contained(path)
            with os.scandir(resolved) as iterator:
                entries = []
                for entry in iterator:
                    if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
                        raise OperationGatewayError("source_traversal_deadline_exceeded")
                    entries.append(entry)
                    if max_entries is not None and len(entries) > max_entries:
                        raise OperationGatewayError("directory_entry_budget_exceeded")
                entries.sort(key=lambda entry: entry.name)
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
            # Do not resolve/query/open the final component before the Cloud
            # Files attribute decision.  Only its already-verified parent is
            # resolved here.
            candidate = self._assert_metadata_target(path)
            observation = self.attribute_adapter.observe(candidate)
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

    def defer_placeholder_by_policy(
        self, path: Path, *, item_id: str, attempt: int
    ) -> None:
        intent = self._intent(
            OperationKind.SOURCE_CLOUD_ATTRIBUTE_OBSERVATION,
            item_id=item_id,
            attempt=attempt,
        )
        try:
            self._assert_metadata_target(path)
        except OperationGatewayError as exc:
            self._terminal(intent, OperationStatus.FAILED, str(exc))
            raise
        self._terminal(
            intent,
            OperationStatus.DEFERRED,
            "icloud_placeholder_deferred",
            observation={
                "schema_version": CLOUD_OBSERVATION_SCHEMA_VERSION,
                "availability": CloudAvailability.RECALL_RISK.value,
                "attributes_known": False,
                "reparse_point": False,
                "offline": True,
                "recall_on_open": True,
                "recall_on_data_access": True,
                "platform": "lexical_placeholder_policy",
                "synthetic_observation": True,
            },
        )

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
            OperationKind.SOURCE_FILE_READ,
            item_id=item_id,
            attempt=attempt,
            reserved_byte_count=max_bytes,
        )
        hash_intent = self._intent(
            OperationKind.SOURCE_FILE_HASH,
            item_id=item_id,
            attempt=attempt,
            reserved_byte_count=max_bytes,
        )
        descriptor: int | None = None
        total = 0
        digest = hashlib.sha256()
        header = bytearray()
        tail = bytearray()
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
                if total >= opened.st_size:
                    break
                remaining = max_bytes - total
                if remaining <= 0:
                    raise OperationGatewayError(
                        "source_file_hash_budget_exceeded", byte_count=total
                    )
                chunk = os.read(descriptor, min(chunk_size, remaining))
                if not chunk:
                    break
                total += len(chunk)
                if len(header) < 64:
                    header.extend(chunk[: 64 - len(header)])
                tail.extend(chunk)
                if len(tail) > 64:
                    del tail[:-64]
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
                exc.byte_count = max(exc.byte_count, total)
                raise
            raise OperationGatewayError("source_file_read_failed", byte_count=total) from exc
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
            observation={"content_fingerprint": digest.hexdigest()},
        )
        self.last_content_probe = (bytes(header), bytes(tail))
        return digest.hexdigest(), total
