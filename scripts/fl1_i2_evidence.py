"""Fixed-cut manifest, budgets, ledger, and crash recovery for FL1-I2."""

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


MANIFEST_SCHEMA = "violet.scv2-fl1-i2-private-manifest.v1"
LEDGER_SCHEMA = "violet.scv2-fl1-i2-private-operation-ledger.v1"


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
    INTERRUPTED = "interrupted"
    DEFERRED = "deferred"
    FAILED = "failed"


def canonical_fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ManifestMember:
    item_id: str
    private_name: str
    object_identity: Mapping[str, str]

    def to_private_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "private_name": self.private_name, "object_identity": dict(self.object_identity)}


@dataclass(frozen=True)
class FixedCutManifest:
    run_id: str
    source_scope_fingerprint: str
    members: tuple[ManifestMember, ...]
    manifest_fingerprint: str

    @classmethod
    def build(cls, *, run_id: str, source_scope_fingerprint: str, members: Sequence[ManifestMember]) -> "FixedCutManifest":
        if not run_id or not source_scope_fingerprint:
            raise EvidenceError("manifest_identity_invalid")
        ordered = tuple(sorted(members, key=lambda item: item.item_id))
        if len({item.item_id for item in ordered}) != len(ordered):
            raise EvidenceError("manifest_duplicate_item_id")
        core = {
            "schema_version": MANIFEST_SCHEMA,
            "run_id": run_id,
            "source_scope_fingerprint": source_scope_fingerprint,
            "members": [item.to_private_dict() for item in ordered],
        }
        return cls(run_id, source_scope_fingerprint, ordered, canonical_fingerprint(core))

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA,
            "run_id": self.run_id,
            "source_scope_fingerprint": self.source_scope_fingerprint,
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
    attempt: int
    state: OperationState
    timestamp_ns: int
    safe_code: str

    def to_dict(self) -> dict[str, Any]:
        return {"operation_id": self.operation_id, "item_id": self.item_id, "attempt": self.attempt, "state": self.state.value, "timestamp_ns": self.timestamp_ns, "safe_code": self.safe_code}


@dataclass
class OperationLedger:
    run_id: str
    manifest_fingerprint: str
    budget_fingerprint: str
    events: list[OperationEvent] = field(default_factory=list)
    item_dispositions: dict[str, ItemDisposition] = field(default_factory=dict)

    def _events(self, operation_id: str) -> list[OperationEvent]:
        return [event for event in self.events if event.operation_id == operation_id]

    def state(self, operation_id: str) -> OperationState | None:
        events = self._events(operation_id)
        return events[-1].state if events else None

    @property
    def failure_count(self) -> int:
        return sum(event.state is OperationState.FAILED for event in self.events)

    @property
    def operation_count(self) -> int:
        return sum(event.state is OperationState.INTENT for event in self.events)

    def can_admit(self, budget: FailureBudget) -> bool:
        return self.failure_count < budget.max_failures and self.operation_count < budget.max_operations

    def begin(self, *, item_id: str, attempt: int, budget: FailureBudget, operation_id: str | None = None) -> str:
        if not self.can_admit(budget):
            raise EvidenceError("operation_admission_budget_exhausted")
        identifier = operation_id or uuid.uuid4().hex
        if self._events(identifier):
            raise EvidenceError("operation_id_reused")
        self.events.append(OperationEvent(identifier, item_id, attempt, OperationState.INTENT, time.time_ns(), "operation_intent_persisted"))
        return identifier

    def mark_started(self, operation_id: str) -> None:
        previous = self.state(operation_id)
        if previous is not OperationState.INTENT:
            raise EvidenceError("operation_started_transition_invalid")
        first = self._events(operation_id)[0]
        self.events.append(OperationEvent(operation_id, first.item_id, first.attempt, OperationState.STARTED, time.time_ns(), "worker_started_persisted"))

    def close(self, operation_id: str, state: OperationState, safe_code: str) -> None:
        if state not in TERMINAL_STATES:
            raise EvidenceError("operation_terminal_state_invalid")
        previous = self.state(operation_id)
        allowed = {
            OperationState.INTENT: {OperationState.RECOVERED},
            OperationState.STARTED: {OperationState.COMPLETED, OperationState.INTERRUPTED, OperationState.FAILED},
        }
        if previous not in allowed or state not in allowed[previous]:
            raise EvidenceError("operation_terminal_transition_invalid")
        first = self._events(operation_id)[0]
        self.events.append(OperationEvent(operation_id, first.item_id, first.attempt, state, time.time_ns(), safe_code))

    def set_disposition(self, item_id: str, disposition: ItemDisposition) -> None:
        self.item_dispositions[item_id] = disposition

    def recover_residuals(self) -> tuple[str, ...]:
        recovered: list[str] = []
        for operation_id in dict.fromkeys(event.operation_id for event in self.events):
            state = self.state(operation_id)
            if state is OperationState.INTENT:
                self.close(operation_id, OperationState.RECOVERED, "residual_intent_recovered_without_execution")
                recovered.append(operation_id)
            elif state is OperationState.STARTED:
                self.close(operation_id, OperationState.INTERRUPTED, "residual_started_interrupted")
                recovered.append(operation_id)
        return tuple(recovered)

    def validate(self) -> None:
        for operation_id in dict.fromkeys(event.operation_id for event in self.events):
            events = self._events(operation_id)
            if not events or events[0].state is not OperationState.INTENT:
                raise EvidenceError("operation_intent_missing")
            terminal = [event for event in events if event.state in TERMINAL_STATES]
            if len(terminal) > 1:
                raise EvidenceError("operation_terminal_closure_not_unique")
            if terminal and events[-1] is not terminal[0]:
                raise EvidenceError("operation_event_after_terminal")

    def to_private_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": LEDGER_SCHEMA,
            "run_id": self.run_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "budget_fingerprint": self.budget_fingerprint,
            "events": [event.to_dict() for event in self.events],
            "item_dispositions": {key: value.value for key, value in sorted(self.item_dispositions.items())},
        }

    @classmethod
    def from_private_dict(cls, payload: Mapping[str, Any]) -> "OperationLedger":
        if payload.get("schema_version") != LEDGER_SCHEMA:
            raise EvidenceError("operation_ledger_schema_invalid")
        ledger = cls(str(payload["run_id"]), str(payload["manifest_fingerprint"]), str(payload["budget_fingerprint"]))
        try:
            ledger.events = [OperationEvent(str(item["operation_id"]), str(item["item_id"]), int(item["attempt"]), OperationState(item["state"]), int(item["timestamp_ns"]), str(item["safe_code"])) for item in payload["events"]]
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
