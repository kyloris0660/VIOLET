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
    load_private_bytes,
    load_private_json,
)


MANIFEST_SCHEMA = "violet.scv2-fl1-i2-private-manifest.v3"
LEDGER_SCHEMA = "violet.scv2-fl1-i2-private-operation-ledger.v3"
WORKER_RESULTS_SCHEMA = "violet.scv2-fl1-i2-private-worker-results.v3"


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


def derive_snapshot_usage(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild enumeration counters from bound directory/page/member evidence."""

    if set(snapshot) != {
        "directory_observation",
        "directories",
        "members",
        "enumeration_usage",
    }:
        raise EvidenceError("snapshot_schema_invalid")
    directories = snapshot["directories"]
    members = snapshot["members"]
    claimed = snapshot["enumeration_usage"]
    if not isinstance(directories, list) or not isinstance(members, list):
        raise EvidenceError("snapshot_schema_invalid")
    if not isinstance(claimed, Mapping):
        raise EvidenceError("snapshot_usage_invalid")
    directory_keys = {
        "component_chain",
        "parent_object_identity",
        "parent_change_identity",
        "observation",
        "page_count",
        "entry_count",
        "metadata_observations",
        "metadata_bytes",
        "pages",
    }
    member_keys = {
        "name",
        "member_type",
        "object_identity",
        "change_identity",
        "attributes",
        "reparse_tag",
        "link_count",
        "component_chain",
        "parent_object_identity",
        "parent_change_identity",
    }
    by_chain: dict[tuple[str, ...], Mapping[str, Any]] = {}
    for raw in directories:
        if not isinstance(raw, Mapping) or set(raw) != directory_keys:
            raise EvidenceError("snapshot_directory_schema_invalid")
        chain_raw = raw["component_chain"]
        if not isinstance(chain_raw, list) or not all(
            type(value) is str and value for value in chain_raw
        ):
            if chain_raw != []:
                raise EvidenceError("snapshot_directory_chain_invalid")
        chain = tuple(chain_raw)
        if chain in by_chain:
            raise EvidenceError("snapshot_directory_duplicate")
        by_chain[chain] = raw
    if () not in by_chain or len(by_chain) != len(directories):
        raise EvidenceError("snapshot_root_directory_invalid")
    member_by_parent: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for raw in members:
        if not isinstance(raw, Mapping) or set(raw) != member_keys:
            raise EvidenceError("snapshot_member_schema_invalid")
        chain_raw = raw["component_chain"]
        if (
            not isinstance(chain_raw, list)
            or not chain_raw
            or not all(type(value) is str and value for value in chain_raw)
            or raw.get("member_type") != "file"
            or raw.get("name") != chain_raw[-1]
        ):
            raise EvidenceError("snapshot_member_chain_invalid")
        parent = tuple(chain_raw[:-1])
        if parent not in by_chain:
            raise EvidenceError("snapshot_member_parent_missing")
        member_by_parent.setdefault(parent, []).append(raw)
    root_identity = by_chain[()]["observation"]
    try:
        platform = root_identity["object_identity"]["platform"]
    except (KeyError, TypeError) as exc:
        raise EvidenceError("snapshot_root_observation_invalid") from exc
    if platform not in {"posix", "windows"}:
        raise EvidenceError("snapshot_platform_invalid")
    if platform == "windows":
        from scripts.fl1_i2_source_backends import FILE_ID_EXTD_DIR_INFO

        windows_record_prefix = int(FILE_ID_EXTD_DIR_INFO.FileName.offset)
    page_records: list[dict[str, int]] = []
    total_entries = total_metadata_bytes = total_metadata_observations = 0
    for chain, raw in sorted(by_chain.items(), key=lambda item: item[0]):
        children = [
            child_chain[-1]
            for child_chain in by_chain
            if len(child_chain) == len(chain) + 1 and child_chain[:-1] == chain
        ]
        names = children + [str(item["name"]) for item in member_by_parent.get(chain, ())]
        entry_count = len(names)
        if platform == "posix":
            metadata_bytes = sum(len(name.encode("utf-8", errors="strict")) + 128 for name in names)
        else:
            metadata_bytes = sum(
                windows_record_prefix + len(name.encode("utf-16-le", errors="strict"))
                for name in names
            )
        pages = raw["pages"]
        if not isinstance(pages, list) or not pages:
            raise EvidenceError("snapshot_page_evidence_invalid")
        if any(
            not isinstance(page, Mapping)
            or set(page) != {"page_index", "entry_count", "metadata_bytes"}
            or any(type(page[key]) is not int or page[key] < 0 for key in page)
            for page in pages
        ):
            raise EvidenceError("snapshot_page_evidence_invalid")
        if (
            sum(page["entry_count"] for page in pages) != entry_count
            or sum(page["metadata_bytes"] for page in pages) != metadata_bytes
            or raw["page_count"] != len(pages)
            or raw["entry_count"] != entry_count
            or raw["metadata_observations"] != entry_count + 1
            or raw["metadata_bytes"] != metadata_bytes
        ):
            raise EvidenceError("snapshot_usage_mismatch")
        page_records.extend(dict(page) for page in pages)
        total_entries += entry_count
        total_metadata_bytes += metadata_bytes
        total_metadata_observations += entry_count + 1
    expected_page_indexes = list(range(1, len(page_records) + 1))
    if [page["page_index"] for page in page_records] != expected_page_indexes:
        raise EvidenceError("snapshot_page_sequence_invalid")
    rebuilt = {
        "directories": len(directories),
        "entries": total_entries,
        "pages": len(page_records),
        "metadata_bytes": total_metadata_bytes,
        "metadata_observations": total_metadata_observations,
        "page_records": page_records,
    }
    if dict(claimed) != rebuilt:
        raise EvidenceError("snapshot_usage_mismatch")
    return rebuilt


@dataclass(frozen=True)
class ManifestMember:
    item_id: str
    private_name: str
    object_identity: Mapping[str, str]
    change_identity: Mapping[str, int]
    component_chain: tuple[str, ...] = ()
    parent_object_identity: Mapping[str, str] | None = None
    parent_change_identity: Mapping[str, int] | None = None
    attributes: int = 0
    reparse_tag: int = 0
    link_count: int = 1

    def __post_init__(self) -> None:
        if not self.component_chain:
            object.__setattr__(self, "component_chain", (self.private_name,))
        if self.component_chain[-1] != self.private_name or any(not value for value in self.component_chain):
            raise EvidenceError("manifest_component_chain_invalid")
        if self.link_count != 1 or self.reparse_tag != 0:
            raise EvidenceError("manifest_member_safety_invalid")

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "private_name": self.private_name,
            "object_identity": dict(self.object_identity),
            "change_identity": dict(self.change_identity),
            "component_chain": list(self.component_chain),
            "parent_object_identity": dict(self.parent_object_identity) if self.parent_object_identity is not None else None,
            "parent_change_identity": dict(self.parent_change_identity) if self.parent_change_identity is not None else None,
            "attributes": self.attributes,
            "reparse_tag": self.reparse_tag,
            "link_count": self.link_count,
        }


@dataclass(frozen=True)
class ManifestDirectory:
    component_chain: tuple[str, ...]
    observation: Mapping[str, Any]
    parent_object_identity: Mapping[str, str] | None
    parent_change_identity: Mapping[str, int] | None

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "component_chain": list(self.component_chain),
            "observation": dict(self.observation),
            "parent_object_identity": dict(self.parent_object_identity) if self.parent_object_identity is not None else None,
            "parent_change_identity": dict(self.parent_change_identity) if self.parent_change_identity is not None else None,
        }


@dataclass(frozen=True)
class FixedCutManifest:
    run_id: str
    source_scope_fingerprint: str
    directory_observation: Mapping[str, Any]
    directories: tuple[ManifestDirectory, ...]
    members: tuple[ManifestMember, ...]
    snapshot_fingerprint: str
    manifest_fingerprint: str

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        source_scope_fingerprint: str,
        directory_observation: Mapping[str, Any],
        members: Sequence[ManifestMember],
        directories: Sequence[ManifestDirectory] = (),
        snapshot_fingerprint: str | None = None,
    ) -> "FixedCutManifest":
        if not run_id or not source_scope_fingerprint or not directory_observation:
            raise EvidenceError("manifest_identity_invalid")
        ordered = tuple(sorted(members, key=lambda item: item.item_id))
        ordered_directories = tuple(sorted(directories, key=lambda item: item.component_chain))
        if ordered_directories:
            if ordered_directories[0].component_chain != () or ordered_directories[0].observation != directory_observation:
                raise EvidenceError("manifest_root_directory_invalid")
            if len({item.component_chain for item in ordered_directories}) != len(ordered_directories):
                raise EvidenceError("manifest_duplicate_directory_path")
        if len({item.item_id for item in ordered}) != len(ordered):
            raise EvidenceError("manifest_duplicate_item_id")
        if len({item.private_name for item in ordered}) != len(ordered):
            if len({item.component_chain for item in ordered}) != len(ordered):
                raise EvidenceError("manifest_duplicate_private_name")
        if len({canonical_fingerprint(item.object_identity) for item in ordered}) != len(ordered):
            raise EvidenceError("manifest_duplicate_object_identity")
        all_identities = [canonical_fingerprint(item.object_identity) for item in ordered]
        all_identities.extend(
            canonical_fingerprint(item.observation.get("object_identity")) for item in ordered_directories
        )
        if len(set(all_identities)) != len(all_identities):
            raise EvidenceError("manifest_duplicate_object_identity")
        resolved_snapshot = snapshot_fingerprint or canonical_fingerprint(
            {
                "directory_observation": directory_observation,
                "directories": [item.to_private_dict() for item in ordered_directories],
                "members": [item.to_private_dict() for item in ordered],
            }
        )
        if len(resolved_snapshot) != 64 or any(value not in "0123456789abcdef" for value in resolved_snapshot):
            raise EvidenceError("manifest_snapshot_fingerprint_invalid")
        core = {
            "schema_version": MANIFEST_SCHEMA,
            "run_id": run_id,
            "source_scope_fingerprint": source_scope_fingerprint,
            "directory_observation": dict(directory_observation),
            "directories": [item.to_private_dict() for item in ordered_directories],
            "members": [item.to_private_dict() for item in ordered],
            "snapshot_fingerprint": resolved_snapshot,
        }
        return cls(
            run_id,
            source_scope_fingerprint,
            dict(directory_observation),
            ordered_directories,
            ordered,
            core["snapshot_fingerprint"],
            canonical_fingerprint(core),
        )

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA,
            "run_id": self.run_id,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "directory_observation": dict(self.directory_observation),
            "directories": [item.to_private_dict() for item in self.directories],
            "members": [item.to_private_dict() for item in self.members],
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
        }


@dataclass(frozen=True)
class FailureBudget:
    max_failures: int
    max_operations: int
    max_bytes: int
    worker_deadline_seconds: float
    max_directories: int = 1024
    max_entries: int = 8192
    max_enumeration_pages: int = 2048
    max_metadata_observations: int = 16384
    max_metadata_bytes: int = 8 * 1024 * 1024
    max_content_opens: int = 4096
    max_hash_operations: int = 4096
    max_structure_validations: int = 4096
    max_retries: int = 3
    max_concurrent_workers: int = 1
    max_run_seconds: float = 900.0
    max_evidence_bytes: int = 64 * 1024 * 1024
    max_decoded_structure_bytes: int = 64 * 1024 * 1024
    max_synthetic_marker_bytes: int = 16 * 1024
    max_external_cost_usd: int = 0

    def __post_init__(self) -> None:
        integer_fields = (
            self.max_failures,
            self.max_operations,
            self.max_bytes,
            self.max_directories,
            self.max_entries,
            self.max_enumeration_pages,
            self.max_metadata_observations,
            self.max_metadata_bytes,
            self.max_content_opens,
            self.max_hash_operations,
            self.max_structure_validations,
            self.max_retries,
            self.max_concurrent_workers,
            self.max_evidence_bytes,
            self.max_decoded_structure_bytes,
            self.max_synthetic_marker_bytes,
            self.max_external_cost_usd,
        )
        if any(type(value) is not int for value in integer_fields) or any(
            type(value) not in {int, float}
            for value in (self.worker_deadline_seconds, self.max_run_seconds)
        ):
            raise EvidenceError("budget_invalid")
        positive = (
            self.max_operations,
            self.max_bytes,
            self.worker_deadline_seconds,
            self.max_directories,
            self.max_entries,
            self.max_enumeration_pages,
            self.max_metadata_observations,
            self.max_metadata_bytes,
            self.max_content_opens,
            self.max_hash_operations,
            self.max_structure_validations,
            self.max_concurrent_workers,
            self.max_run_seconds,
            self.max_evidence_bytes,
            self.max_decoded_structure_bytes,
            self.max_synthetic_marker_bytes,
        )
        if self.max_failures < 0 or self.max_retries < 0 or min(positive) <= 0 or self.max_external_cost_usd != 0:
            raise EvidenceError("budget_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_failures": self.max_failures,
            "max_operations": self.max_operations,
            "max_bytes": self.max_bytes,
            "worker_deadline_seconds": self.worker_deadline_seconds,
            "max_directories": self.max_directories,
            "max_entries": self.max_entries,
            "max_enumeration_pages": self.max_enumeration_pages,
            "max_metadata_observations": self.max_metadata_observations,
            "max_metadata_bytes": self.max_metadata_bytes,
            "max_content_opens": self.max_content_opens,
            "max_hash_operations": self.max_hash_operations,
            "max_structure_validations": self.max_structure_validations,
            "max_retries": self.max_retries,
            "max_concurrent_workers": self.max_concurrent_workers,
            "max_run_seconds": self.max_run_seconds,
            "max_evidence_bytes": self.max_evidence_bytes,
            "max_decoded_structure_bytes": self.max_decoded_structure_bytes,
            "max_synthetic_marker_bytes": self.max_synthetic_marker_bytes,
            "max_external_cost_usd": self.max_external_cost_usd,
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
    run_started_at_ns: int = field(default_factory=time.time_ns)

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
            if not operation_id or not events or events[0].state is not OperationState.INTENT:
                raise EvidenceError("operation_intent_missing")
            first = events[0]
            if not first.item_id or not first.kind or first.attempt <= 0 or first.bytes_reserved < 0:
                raise EvidenceError("operation_reserved_bytes_invalid")
            if any(
                event.item_id != first.item_id
                or event.kind != first.kind
                or event.attempt != first.attempt
                or event.bytes_reserved != first.bytes_reserved
                for event in events
            ):
                raise EvidenceError("operation_closure_conflict")
            sequence = tuple(event.state for event in events)
            valid_sequences = {
                (OperationState.INTENT,),
                (OperationState.INTENT, OperationState.STARTED),
                (OperationState.INTENT, OperationState.RECOVERED),
                (OperationState.INTENT, OperationState.STARTED, OperationState.COMPLETED),
                (OperationState.INTENT, OperationState.STARTED, OperationState.FAILED),
                (OperationState.INTENT, OperationState.STARTED, OperationState.INTERRUPTED),
            }
            if sequence not in valid_sequences:
                raise EvidenceError("operation_state_sequence_invalid")
            if any(events[index].timestamp_ns > events[index + 1].timestamp_ns for index in range(len(events) - 1)):
                raise EvidenceError("operation_event_order_invalid")
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

    def run_counters(self) -> dict[str, int]:
        counters = {
            "directories_discovered": 0,
            "entries_discovered": 0,
            "enumeration_pages": 0,
            "metadata_observations": 0,
            "metadata_bytes": 0,
            "content_opens": 0,
            "content_bytes": self.consumed_bytes,
            "decoded_structure_bytes": 0,
            "hash_operations": 0,
            "structure_validations": 0,
            "total_operations": self.operation_count,
            "failed_operations": sum(event.state is OperationState.FAILED for event in self.events),
            "interrupted_operations": sum(event.state is OperationState.INTERRUPTED for event in self.events),
            "retry_operations": sum(event.state is OperationState.INTENT and event.attempt > 1 for event in self.events),
            "maximum_concurrent_workers": 0,
            "external_cost_usd": 0,
        }
        active = 0
        for event in self.events:
            if event.state is OperationState.STARTED:
                active += 1
                counters["maximum_concurrent_workers"] = max(counters["maximum_concurrent_workers"], active)
            elif event.state in {OperationState.COMPLETED, OperationState.FAILED, OperationState.INTERRUPTED}:
                active -= 1
                if active < 0:
                    raise EvidenceError("operation_concurrency_invalid")
        if active:
            raise EvidenceError("operation_concurrency_invalid")
        for record in self.committed_results.values():
            envelope = record.get("payload")
            result = envelope.get("result") if isinstance(envelope, Mapping) else None
            if not isinstance(result, Mapping):
                continue
            if record.get("kind") in {"list_directory", "final_snapshot"}:
                usage = derive_snapshot_usage(result)
                counters["directories_discovered"] += usage["directories"]
                counters["entries_discovered"] += usage["entries"]
                counters["enumeration_pages"] += usage["pages"]
                counters["metadata_observations"] += usage["metadata_observations"]
                counters["metadata_bytes"] += usage["metadata_bytes"]
            elif record.get("kind") == "combined_content" and record.get("status") == "completed":
                counters["content_opens"] += 1
                counters["hash_operations"] += 1
                counters["structure_validations"] += 1
                media = result.get("media")
                if isinstance(media, Mapping):
                    decoded = media.get("decoded_structure_bytes", 0)
                    if type(decoded) is not int or decoded < 0:
                        raise EvidenceError("operation_counter_invalid")
                    counters["decoded_structure_bytes"] += decoded
        return counters

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
            "run_started_at_ns": self.run_started_at_ns,
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
            "run_started_at_ns",
        }
        if set(payload) != expected:
            raise EvidenceError("operation_ledger_invalid")
        if type(payload["run_started_at_ns"]) is not int or payload["run_started_at_ns"] <= 0:
            raise EvidenceError("operation_ledger_invalid")
        ledger = cls(
            str(payload["run_id"]),
            str(payload["manifest_fingerprint"]),
            str(payload["budget_fingerprint"]),
            run_started_at_ns=payload["run_started_at_ns"],
        )
        try:
            event_keys = {
                "operation_id", "item_id", "kind", "attempt", "state", "timestamp_ns",
                "safe_code", "bytes_reserved", "bytes_consumed", "result_fingerprint",
            }
            ledger.events = []
            for item in payload["events"]:
                if not isinstance(item, Mapping) or set(item) != event_keys:
                    raise TypeError
                if any(type(item[key]) is not int for key in ("attempt", "timestamp_ns", "bytes_reserved", "bytes_consumed")):
                    raise TypeError
                if any(type(item[key]) is not str or not item[key] for key in ("operation_id", "item_id", "kind", "safe_code")):
                    raise TypeError
                if item["result_fingerprint"] is not None and type(item["result_fingerprint"]) is not str:
                    raise TypeError
                ledger.events.append(OperationEvent(
                    item["operation_id"], item["item_id"], item["kind"], item["attempt"],
                    OperationState(item["state"]), item["timestamp_ns"], item["safe_code"],
                    item["bytes_reserved"], item["bytes_consumed"], item["result_fingerprint"],
                ))
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

    def read_bytes(self, relative_path: str, *, max_bytes: int) -> bytes:
        target = self.store.target(Path(relative_path))
        try:
            return load_private_bytes(target, max_bytes=max_bytes)
        except OperationGatewayError as exc:
            raise EvidenceError(str(exc)) from exc
