"""Fixed-cut manifest, run-wide budgets, and crash-consistent I2 evidence."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.fl1_i1_operation_gateway import (
    OperationGatewayError,
    TaskOwnedArtifactStore,
    load_private_json,
)


MANIFEST_SCHEMA = "violet.scv2-fl1-i2-private-manifest.v2"
LEDGER_SCHEMA = "violet.scv2-fl1-i2-private-operation-ledger.v2"
WORKER_RESULTS_SCHEMA = "violet.scv2-fl1-i2-private-worker-results.v2"


class EvidenceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OperationState(str, Enum):
    INTENT = "intent"
    STARTED = "started"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    RECOVERED = "recovered"


TERMINAL_STATES = {
    OperationState.COMPLETED,
    OperationState.INTERRUPTED,
    OperationState.FAILED,
    OperationState.RECOVERED,
}


class ItemDisposition(str, Enum):
    PENDING = "pending"
    CONTENT_VERIFIED = "content_verified"
    CORRUPT_MEDIA = "corrupt_media"
    UNSUPPORTED = "unsupported"
    INTERRUPTED = "interrupted"
    DEFERRED = "deferred"
    FAILED = "failed"


def canonical_fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ManifestMember:
    item_id: str
    private_name: str
    object_identity: Mapping[str, str]
    change_identity: Mapping[str, int]

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "private_name": self.private_name,
            "object_identity": dict(self.object_identity),
            "change_identity": dict(self.change_identity),
        }


@dataclass(frozen=True)
class FixedCutManifest:
    run_id: str
    source_scope_fingerprint: str
    directory_observation: Mapping[str, Any]
    members: tuple[ManifestMember, ...]
    manifest_fingerprint: str

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        source_scope_fingerprint: str,
        directory_observation: Mapping[str, Any],
        members: Sequence[ManifestMember],
    ) -> "FixedCutManifest":
        if not run_id or not source_scope_fingerprint or not directory_observation:
            raise EvidenceError("manifest_identity_invalid")
        ordered = tuple(sorted(members, key=lambda item: item.item_id))
        if len({item.item_id for item in ordered}) != len(ordered):
            raise EvidenceError("manifest_duplicate_item_id")
        if len({item.private_name for item in ordered}) != len(ordered):
            raise EvidenceError("manifest_duplicate_private_name")
        if len({canonical_fingerprint(item.object_identity) for item in ordered}) != len(ordered):
            raise EvidenceError("manifest_duplicate_object_identity")
        core = {
            "schema_version": MANIFEST_SCHEMA,
            "run_id": run_id,
            "source_scope_fingerprint": source_scope_fingerprint,
            "directory_observation": dict(directory_observation),
            "members": [item.to_private_dict() for item in ordered],
        }
        return cls(
            run_id,
            source_scope_fingerprint,
            dict(directory_observation),
            ordered,
            canonical_fingerprint(core),
        )

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA,
            "run_id": self.run_id,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "directory_observation": dict(self.directory_observation),
            "members": [item.to_private_dict() for item in self.members],
            "manifest_fingerprint": self.manifest_fingerprint,
        }


@dataclass(frozen=True)
class FailureBudget:
    max_failures: int
    max_operations: int
    max_bytes: int
    worker_deadline_seconds: float

    def __post_init__(self) -> None:
        if self.max_failures < 0 or self.max_operations <= 0 or self.max_bytes <= 0 or self.worker_deadline_seconds <= 0:
            raise EvidenceError("budget_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_failures": self.max_failures,
            "max_operations": self.max_operations,
            "max_bytes": self.max_bytes,
            "worker_deadline_seconds": self.worker_deadline_seconds,
        }


@dataclass
class OperationEvent:
    operation_id: str
    item_id: str
    kind: str
    attempt: int
    state: OperationState
    timestamp_ns: int
    safe_code: str
    bytes_reserved: int
    bytes_consumed: int
    result_fingerprint: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "item_id": self.item_id,
            "kind": self.kind,
            "attempt": self.attempt,
            "state": self.state.value,
            "timestamp_ns": self.timestamp_ns,
            "safe_code": self.safe_code,
            "bytes_reserved": self.bytes_reserved,
            "bytes_consumed": self.bytes_consumed,
            "result_fingerprint": self.result_fingerprint,
        }


@dataclass
class OperationLedger:
    run_id: str
    manifest_fingerprint: str
    budget_fingerprint: str
    events: list[OperationEvent] = field(default_factory=list)
    committed_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    item_dispositions: dict[str, ItemDisposition] = field(default_factory=dict)

    def _events(self, operation_id: str) -> list[OperationEvent]:
        return [event for event in self.events if event.operation_id == operation_id]

    def state(self, operation_id: str) -> OperationState | None:
        events = self._events(operation_id)
        return events[-1].state if events else None

    @property
    def failure_count(self) -> int:
        return sum(event.state in {OperationState.FAILED, OperationState.INTERRUPTED} for event in self.events)

    @property
    def operation_count(self) -> int:
        return sum(event.state is OperationState.INTENT for event in self.events)

    @property
    def consumed_bytes(self) -> int:
        return sum(event.bytes_consumed for event in self.events if event.state in TERMINAL_STATES)

    @property
    def active_reserved_bytes(self) -> int:
        reserved = 0
        for operation_id in dict.fromkeys(event.operation_id for event in self.events):
            if self.state(operation_id) in {OperationState.INTENT, OperationState.STARTED}:
                reserved += self._events(operation_id)[0].bytes_reserved
        return reserved

    def remaining_bytes(self, budget: FailureBudget) -> int:
        return budget.max_bytes - self.consumed_bytes - self.active_reserved_bytes

    def can_admit(self, budget: FailureBudget, *, bytes_reserved: int = 0) -> bool:
        if bytes_reserved < 0:
            return False
        return (
            self.failure_count < budget.max_failures
            and self.operation_count < budget.max_operations
            and self.consumed_bytes < budget.max_bytes
            and bytes_reserved <= self.remaining_bytes(budget)
        )

    def begin(
        self,
        *,
        item_id: str,
        kind: str,
        attempt: int,
        budget: FailureBudget,
        bytes_reserved: int = 0,
        operation_id: str | None = None,
    ) -> str:
        if not item_id or not kind or attempt <= 0:
            raise EvidenceError("operation_identity_invalid")
        if not self.can_admit(budget, bytes_reserved=bytes_reserved):
            raise EvidenceError("operation_admission_budget_exhausted")
        identifier = operation_id or uuid.uuid4().hex
        if self._events(identifier) or identifier in self.committed_results:
            raise EvidenceError("operation_id_reused")
        self.events.append(
            OperationEvent(
                identifier,
                item_id,
                kind,
                attempt,
                OperationState.INTENT,
                time.time_ns(),
                "operation_intent_persisted",
                bytes_reserved,
                0,
                None,
            )
        )
        return identifier

    def mark_started(self, operation_id: str) -> None:
        previous = self.state(operation_id)
        if previous is not OperationState.INTENT:
            raise EvidenceError("operation_started_transition_invalid")
        first = self._events(operation_id)[0]
        self.events.append(
            OperationEvent(
                operation_id,
                first.item_id,
                first.kind,
                first.attempt,
                OperationState.STARTED,
                time.time_ns(),
                "worker_started_persisted",
                first.bytes_reserved,
                0,
                None,
            )
        )

    def commit_terminal(
        self,
        operation_id: str,
        state: OperationState,
        safe_code: str,
        *,
        bytes_consumed: int,
        payload: Mapping[str, Any] | None,
    ) -> str:
        if state not in TERMINAL_STATES:
            raise EvidenceError("operation_terminal_state_invalid")
        previous = self.state(operation_id)
        allowed = {
            OperationState.INTENT: {OperationState.RECOVERED},
            OperationState.STARTED: {
                OperationState.COMPLETED,
                OperationState.INTERRUPTED,
                OperationState.FAILED,
            },
        }
        if previous not in allowed or state not in allowed[previous]:
            raise EvidenceError("operation_terminal_transition_invalid")
        first = self._events(operation_id)[0]
        if bytes_consumed < 0 or bytes_consumed > first.bytes_reserved:
            raise EvidenceError("operation_consumed_bytes_invalid")
        result = {
            "operation_id": operation_id,
            "item_id": first.item_id,
            "kind": first.kind,
            "attempt": first.attempt,
            "status": state.value,
            "safe_code": safe_code,
            "bytes_reserved": first.bytes_reserved,
            "bytes_consumed": bytes_consumed,
            "payload": dict(payload) if payload is not None else None,
        }
        fingerprint = canonical_fingerprint(result)
        if operation_id in self.committed_results:
            raise EvidenceError("operation_closure_conflict")
        self.committed_results[operation_id] = {**result, "result_fingerprint": fingerprint}
        self.events.append(
            OperationEvent(
                operation_id,
                first.item_id,
                first.kind,
                first.attempt,
                state,
                time.time_ns(),
                safe_code,
                first.bytes_reserved,
                bytes_consumed,
                fingerprint,
            )
        )
        return fingerprint

    def set_disposition(self, item_id: str, disposition: ItemDisposition) -> None:
        previous = self.item_dispositions.get(item_id)
        if previous is not None and previous is not disposition:
            raise EvidenceError("item_disposition_conflict")
        self.item_dispositions[item_id] = disposition

    def recover_residuals(self) -> tuple[str, ...]:
        recovered: list[str] = []
        for operation_id in dict.fromkeys(event.operation_id for event in self.events):
            current = self.state(operation_id)
            first = self._events(operation_id)[0]
            if current is OperationState.INTENT:
                self.commit_terminal(
                    operation_id,
                    OperationState.RECOVERED,
                    "residual_intent_recovered_without_execution",
                    bytes_consumed=0,
                    payload=None,
                )
                recovered.append(operation_id)
            elif current is OperationState.STARTED:
                self.commit_terminal(
                    operation_id,
                    OperationState.INTERRUPTED,
                    "residual_started_interrupted",
                    bytes_consumed=first.bytes_reserved,
                    payload=None,
                )
                recovered.append(operation_id)
        return tuple(recovered)

    def validate(self) -> None:
        operation_ids = tuple(dict.fromkeys(event.operation_id for event in self.events))
        if set(self.committed_results) - set(operation_ids):
            raise EvidenceError("operation_closure_conflict")
        for operation_id in operation_ids:
            events = self._events(operation_id)
            if not events or events[0].state is not OperationState.INTENT:
                raise EvidenceError("operation_intent_missing")
            first = events[0]
            if first.bytes_reserved < 0:
                raise EvidenceError("operation_reserved_bytes_invalid")
            if any(
                event.item_id != first.item_id
                or event.kind != first.kind
                or event.attempt != first.attempt
                or event.bytes_reserved != first.bytes_reserved
                for event in events
            ):
                raise EvidenceError("operation_closure_conflict")
            terminal = [event for event in events if event.state in TERMINAL_STATES]
            if len(terminal) > 1 or (terminal and events[-1] is not terminal[0]):
                raise EvidenceError("operation_terminal_closure_not_unique")
            committed = self.committed_results.get(operation_id)
            if not terminal:
                if committed is not None:
                    raise EvidenceError("operation_closure_conflict")
                continue
            if committed is None:
                raise EvidenceError("operation_closure_conflict")
            terminal_event = terminal[0]
            base = {key: value for key, value in committed.items() if key != "result_fingerprint"}
            fingerprint = canonical_fingerprint(base)
            if (
                committed.get("result_fingerprint") != fingerprint
                or terminal_event.result_fingerprint != fingerprint
                or committed.get("status") != terminal_event.state.value
                or committed.get("safe_code") != terminal_event.safe_code
                or committed.get("bytes_consumed") != terminal_event.bytes_consumed
                or committed.get("bytes_reserved") != terminal_event.bytes_reserved
            ):
                raise EvidenceError("operation_closure_conflict")

    def to_worker_projection(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": WORKER_RESULTS_SCHEMA,
            "run_id": self.run_id,
            "records": [self.committed_results[key] for key in sorted(self.committed_results)],
        }

    def to_private_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": LEDGER_SCHEMA,
            "run_id": self.run_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "budget_fingerprint": self.budget_fingerprint,
            "events": [event.to_dict() for event in self.events],
            "committed_results": {key: self.committed_results[key] for key in sorted(self.committed_results)},
            "item_dispositions": {key: value.value for key, value in sorted(self.item_dispositions.items())},
        }

    @classmethod
    def from_private_dict(cls, payload: Mapping[str, Any]) -> "OperationLedger":
        if payload.get("schema_version") != LEDGER_SCHEMA:
            raise EvidenceError("operation_ledger_schema_invalid")
        expected = {
            "schema_version",
            "run_id",
            "manifest_fingerprint",
            "budget_fingerprint",
            "events",
            "committed_results",
            "item_dispositions",
        }
        if set(payload) != expected:
            raise EvidenceError("operation_ledger_invalid")
        ledger = cls(str(payload["run_id"]), str(payload["manifest_fingerprint"]), str(payload["budget_fingerprint"]))
        try:
            ledger.events = [
                OperationEvent(
                    str(item["operation_id"]),
                    str(item["item_id"]),
                    str(item["kind"]),
                    int(item["attempt"]),
                    OperationState(item["state"]),
                    int(item["timestamp_ns"]),
                    str(item["safe_code"]),
                    int(item["bytes_reserved"]),
                    int(item["bytes_consumed"]),
                    item["result_fingerprint"],
                )
                for item in payload["events"]
            ]
            ledger.committed_results = {str(key): dict(value) for key, value in payload["committed_results"].items()}
            ledger.item_dispositions = {str(key): ItemDisposition(value) for key, value in payload["item_dispositions"].items()}
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceError("operation_ledger_invalid") from exc
        ledger.validate()
        return ledger


class EvidenceStore:
    def __init__(self, root: Path) -> None:
        self.store = TaskOwnedArtifactStore(root)

    def write(self, relative_path: str, payload: Mapping[str, Any]) -> None:
        self.store.atomic_write_json(Path(relative_path), payload)

    def read(self, relative_path: str) -> dict[str, Any]:
        target = self.store.target(Path(relative_path))
        try:
            return load_private_json(target)
        except OperationGatewayError as exc:
            raise EvidenceError(str(exc)) from exc
