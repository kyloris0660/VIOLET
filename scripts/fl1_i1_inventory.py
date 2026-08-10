"""Persistent, bounded, synthetic-first SCV2-FL1-I1 inventory scanner."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fl1_i1_operation_gateway import (
    AttributeAdapter,
    CloudAvailability,
    OperationGateway,
    OperationGatewayError,
    OperationLedger,
    OperationKind,
    OperationLedgerStore,
    OperationStatus,
    SyntheticAttributeAdapter,
    TaskOwnedArtifactStore,
    WindowsCloudAttributeAdapter,
    attribute_adapter_identity,
    load_private_json,
)
from scripts.fl1_i1_runtime_context import (
    SourceMode,
    TrustedRuntimeContext,
    build_trusted_runtime_context,
)


CONTRACT_ID = "scv2_fl1_i1_read_only_inventory_contract_v1"
BUDGET_SCHEMA_VERSION = "violet.scv2-fl1-i1-inventory-budgets.v1"
MANIFEST_SCHEMA_VERSION = "violet.scv2-fl1-i1-private-manifest.v1"
RUN_LEDGER_SCHEMA_VERSION = "violet.scv2-fl1-i1-run-ledger.v1"
CHECKPOINT_SCHEMA_VERSION = "violet.scv2-fl1-i1-inventory-checkpoint.v1"
INVOKATION_SCHEMA_VERSION = "violet.scv2-fl1-i1-invocation.v1"
MEMBERSHIP_IDENTITY_VERSION = "violet.scv2-fl1-i1-membership.v1"
CONTENT_IDENTITY_VERSION = "sha256"
PUBLIC_LABEL_VERSION = "violet.scv2-fl1-i1-private-keyed-label.v1"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")


class InventoryError(RuntimeError):
    """Raised for structural inventory failures and private evidence tamper."""


class InventoryDisposition(str, Enum):
    UNSUPPORTED = "unsupported"
    DUPLICATE = "duplicate"
    CLOUD_RECALL_DEFERRED = "cloud_recall_deferred"
    UNREADABLE_OR_MISSING = "unreadable_or_missing"
    ELIGIBLE_CANDIDATE = "eligible_candidate"


class RunStatus(str, Enum):
    DISCOVERING = "discovering"
    RUNNING = "running"
    CONTROLLED_STOP = "controlled_stop"
    BUDGET_STOP = "budget_stop"
    BLOCKED_INCOMPLETE = "blocked_incomplete"
    COMPLETE = "complete"


@dataclass(frozen=True)
class InventoryBudgets:
    max_discovered_items: int
    max_directory_entries: int
    max_total_observed_bytes: int
    max_per_file_hash_bytes: int
    max_total_hashed_bytes: int
    read_chunk_size: int
    per_item_timeout_seconds: float
    max_unreadable_failures: int
    max_consecutive_failures: int
    max_same_reason_failures: int
    batch_size: int
    traversal_timeout_seconds: float = 30.0
    max_item_attempts: int = 3

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InventoryBudgets":
        if payload.get("schema_version") != BUDGET_SCHEMA_VERSION:
            raise InventoryError("budget_schema_invalid")
        try:
            budgets = cls(
                max_discovered_items=int(payload["max_discovered_items"]),
                max_directory_entries=int(payload["max_directory_entries"]),
                max_total_observed_bytes=int(payload["max_total_observed_bytes"]),
                max_per_file_hash_bytes=int(payload["max_per_file_hash_bytes"]),
                max_total_hashed_bytes=int(payload["max_total_hashed_bytes"]),
                read_chunk_size=int(payload["read_chunk_size"]),
                per_item_timeout_seconds=float(payload["per_item_timeout_seconds"]),
                max_unreadable_failures=int(payload["max_unreadable_failures"]),
                max_consecutive_failures=int(payload["max_consecutive_failures"]),
                max_same_reason_failures=int(payload["max_same_reason_failures"]),
                batch_size=int(payload["batch_size"]),
                traversal_timeout_seconds=float(
                    payload.get("traversal_timeout_seconds", 30.0)
                ),
                max_item_attempts=int(payload.get("max_item_attempts", 3)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InventoryError("budget_payload_invalid") from exc
        budgets.validate()
        return budgets

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BUDGET_SCHEMA_VERSION,
            "max_discovered_items": self.max_discovered_items,
            "max_directory_entries": self.max_directory_entries,
            "max_total_observed_bytes": self.max_total_observed_bytes,
            "max_per_file_hash_bytes": self.max_per_file_hash_bytes,
            "max_total_hashed_bytes": self.max_total_hashed_bytes,
            "read_chunk_size": self.read_chunk_size,
            "per_item_timeout_seconds": self.per_item_timeout_seconds,
            "max_unreadable_failures": self.max_unreadable_failures,
            "max_consecutive_failures": self.max_consecutive_failures,
            "max_same_reason_failures": self.max_same_reason_failures,
            "batch_size": self.batch_size,
            "traversal_timeout_seconds": self.traversal_timeout_seconds,
            "max_item_attempts": self.max_item_attempts,
        }

    def validate(self) -> None:
        integer_fields = (
            self.max_discovered_items,
            self.max_directory_entries,
            self.max_total_observed_bytes,
            self.max_per_file_hash_bytes,
            self.max_total_hashed_bytes,
            self.read_chunk_size,
            self.max_unreadable_failures,
            self.max_consecutive_failures,
            self.max_same_reason_failures,
            self.batch_size,
            self.max_item_attempts,
        )
        if any(isinstance(value, bool) or value <= 0 for value in integer_fields):
            raise InventoryError("budget_value_invalid")
        if not 0 < self.per_item_timeout_seconds <= 3600:
            raise InventoryError("budget_timeout_invalid")
        if not 0 < self.traversal_timeout_seconds <= 3600:
            raise InventoryError("budget_traversal_timeout_invalid")
        if self.read_chunk_size > 16 * 1024 * 1024:
            raise InventoryError("budget_chunk_size_invalid")
        if self.max_per_file_hash_bytes > self.max_total_hashed_bytes:
            raise InventoryError("budget_hash_relationship_invalid")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())


@dataclass
class PrivateInventoryItem:
    item_id: str
    private_relative_path: str
    private_path_token: str
    private_public_label: str
    extension: str
    signature: tuple[int, int, int, int, int]
    observed_size: int
    disposition: InventoryDisposition | None = None
    reason_code: str | None = None
    content_fingerprint: str | None = None
    duplicate_of_item_id: str | None = None
    attempt_count: int = 0
    terminal_invocation_id: str | None = None

    @property
    def terminal(self) -> bool:
        return self.disposition is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "private_relative_path": self.private_relative_path,
            "private_path_token": self.private_path_token,
            "private_public_label": self.private_public_label,
            "extension": self.extension,
            "signature": list(self.signature),
            "observed_size": self.observed_size,
            "disposition": self.disposition.value if self.disposition else None,
            "reason_code": self.reason_code,
            "content_fingerprint": self.content_fingerprint,
            "duplicate_of_item_id": self.duplicate_of_item_id,
            "attempt_count": self.attempt_count,
            "terminal_invocation_id": self.terminal_invocation_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PrivateInventoryItem":
        try:
            signature_payload = payload["signature"]
            if not isinstance(signature_payload, Sequence) or len(signature_payload) != 5:
                raise ValueError
            disposition = (
                InventoryDisposition(payload["disposition"])
                if payload.get("disposition") is not None
                else None
            )
            item = cls(
                item_id=str(payload["item_id"]),
                private_relative_path=str(payload["private_relative_path"]),
                private_path_token=str(payload["private_path_token"]),
                private_public_label=str(payload["private_public_label"]),
                extension=str(payload["extension"]),
                signature=tuple(int(value) for value in signature_payload),  # type: ignore[arg-type]
                observed_size=int(payload["observed_size"]),
                disposition=disposition,
                reason_code=(str(payload["reason_code"]) if payload.get("reason_code") else None),
                content_fingerprint=(
                    str(payload["content_fingerprint"])
                    if payload.get("content_fingerprint")
                    else None
                ),
                duplicate_of_item_id=(
                    str(payload["duplicate_of_item_id"])
                    if payload.get("duplicate_of_item_id")
                    else None
                ),
                attempt_count=int(payload.get("attempt_count", 0)),
                terminal_invocation_id=(
                    str(payload["terminal_invocation_id"])
                    if payload.get("terminal_invocation_id")
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InventoryError("private_manifest_item_invalid") from exc
        item.validate()
        return item

    def validate(self) -> None:
        if not HEX64_RE.fullmatch(self.item_id):
            raise InventoryError("item_identity_invalid")
        if not HEX64_RE.fullmatch(self.private_path_token):
            raise InventoryError("private_path_token_invalid")
        if not re.fullmatch(r"item_[0-9a-f]{24}", self.private_public_label):
            raise InventoryError("private_public_label_invalid")
        if self.observed_size < 0 or self.attempt_count < 0:
            raise InventoryError("item_counter_invalid")
        if self.reason_code is not None and not SAFE_REASON_RE.fullmatch(self.reason_code):
            raise InventoryError("item_reason_invalid")
        if self.content_fingerprint is not None and not HEX64_RE.fullmatch(
            self.content_fingerprint
        ):
            raise InventoryError("content_fingerprint_invalid")
        if self.disposition in {
            InventoryDisposition.ELIGIBLE_CANDIDATE,
            InventoryDisposition.DUPLICATE,
        } and self.content_fingerprint is None:
            raise InventoryError("terminal_content_identity_missing")
        if self.disposition is InventoryDisposition.DUPLICATE:
            if not self.duplicate_of_item_id:
                raise InventoryError("duplicate_primary_missing")
        elif self.duplicate_of_item_id is not None:
            raise InventoryError("duplicate_primary_unexpected")


@dataclass
class InvocationEvidence:
    invocation_id: str
    pid: int
    process_start_observation: str
    started_at_ns: int
    parent_checkpoint_fingerprint: str | None
    ended_at_ns: int | None = None
    end_checkpoint_fingerprint: str | None = None

    def to_dict(self, *, include_end_checkpoint: bool = True) -> dict[str, Any]:
        return {
            "schema_version": INVOKATION_SCHEMA_VERSION,
            "invocation_id": self.invocation_id,
            "pid": self.pid,
            "process_start_observation": self.process_start_observation,
            "started_at_ns": self.started_at_ns,
            "parent_checkpoint_fingerprint": self.parent_checkpoint_fingerprint,
            "ended_at_ns": self.ended_at_ns,
            "end_checkpoint_fingerprint": (
                self.end_checkpoint_fingerprint if include_end_checkpoint else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvocationEvidence":
        if payload.get("schema_version") != INVOKATION_SCHEMA_VERSION:
            raise InventoryError("invocation_schema_invalid")
        try:
            invocation = cls(
                invocation_id=str(payload["invocation_id"]),
                pid=int(payload["pid"]),
                process_start_observation=str(payload["process_start_observation"]),
                started_at_ns=int(payload["started_at_ns"]),
                parent_checkpoint_fingerprint=(
                    str(payload["parent_checkpoint_fingerprint"])
                    if payload.get("parent_checkpoint_fingerprint")
                    else None
                ),
                ended_at_ns=(int(payload["ended_at_ns"]) if payload.get("ended_at_ns") else None),
                end_checkpoint_fingerprint=(
                    str(payload["end_checkpoint_fingerprint"])
                    if payload.get("end_checkpoint_fingerprint")
                    else None
                ),
            )
            uuid.UUID(invocation.invocation_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise InventoryError("invocation_evidence_invalid") from exc
        if invocation.pid <= 0 or invocation.started_at_ns <= 0:
            raise InventoryError("invocation_process_evidence_invalid")
        if not invocation.process_start_observation:
            raise InventoryError("invocation_process_evidence_invalid")
        if invocation.ended_at_ns is not None and invocation.ended_at_ns < invocation.started_at_ns:
            raise InventoryError("invocation_timestamp_invalid")
        for fingerprint in (
            invocation.parent_checkpoint_fingerprint,
            invocation.end_checkpoint_fingerprint,
        ):
            if fingerprint is not None and not HEX64_RE.fullmatch(fingerprint):
                raise InventoryError("invocation_checkpoint_invalid")
        return invocation


@dataclass
class PrivateManifest:
    manifest_id: str
    run_id: str
    actual_git_head: str
    source_scope_fingerprint: str
    source_snapshot_fingerprint: str
    keyed_label_secret: str
    items: list[PrivateInventoryItem]
    discovery_directories: list[dict[str, Any]] = field(default_factory=list)
    final_directories: list[dict[str, Any]] = field(default_factory=list)
    manifest_fingerprint: str = ""

    @property
    def discovery_directory_tokens(self) -> list[str]:
        return [str(value["path_token"]) for value in self.discovery_directories]

    @property
    def final_directory_tokens(self) -> list[str]:
        return [str(value["path_token"]) for value in self.final_directories]

    def denominator(self) -> dict[str, int]:
        counts = {disposition: 0 for disposition in InventoryDisposition}
        unresolved = 0
        for item in self.items:
            if item.disposition is None:
                unresolved += 1
            else:
                counts[item.disposition] += 1
        discovered = len(self.items)
        unsupported = counts[InventoryDisposition.UNSUPPORTED]
        supported = sum(
            counts[disposition]
            for disposition in (
                InventoryDisposition.DUPLICATE,
                InventoryDisposition.CLOUD_RECALL_DEFERRED,
                InventoryDisposition.UNREADABLE_OR_MISSING,
                InventoryDisposition.ELIGIBLE_CANDIDATE,
            )
        )
        eligible = counts[InventoryDisposition.ELIGIBLE_CANDIDATE]
        return {
            "discovered": discovered,
            "supported": supported,
            "unsupported": unsupported,
            "duplicate": counts[InventoryDisposition.DUPLICATE],
            "cloud_recall_deferred": counts[
                InventoryDisposition.CLOUD_RECALL_DEFERRED
            ],
            "unreadable_or_missing": counts[
                InventoryDisposition.UNREADABLE_OR_MISSING
            ],
            "eligible_candidate": eligible,
            "imported": 0,
            "import_deferred": eligible,
            "import_failed": 0,
            "unresolved": unresolved,
        }

    def _payload(self, *, include_fingerprint: bool) -> dict[str, Any]:
        payload = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "manifest_id": self.manifest_id,
            "run_id": self.run_id,
            "actual_git_head": self.actual_git_head,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "source_snapshot_fingerprint": self.source_snapshot_fingerprint,
            "membership_identity_version": MEMBERSHIP_IDENTITY_VERSION,
            "content_identity_version": CONTENT_IDENTITY_VERSION,
            "public_label_version": PUBLIC_LABEL_VERSION,
            "keyed_label_secret": self.keyed_label_secret,
            "items": [item.to_dict() for item in self.items],
            "discovery_directories": list(self.discovery_directories),
            "final_directories": list(self.final_directories),
            "denominator": self.denominator(),
        }
        if include_fingerprint:
            payload["manifest_fingerprint"] = self.manifest_fingerprint
        return payload

    def refresh_fingerprint(self) -> None:
        self.manifest_fingerprint = _fingerprint(
            self._payload(include_fingerprint=False)
        )

    def to_dict(self) -> dict[str, Any]:
        self.refresh_fingerprint()
        return self._payload(include_fingerprint=True)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PrivateManifest":
        if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise InventoryError("manifest_schema_invalid")
        items_payload = payload.get("items")
        if not isinstance(items_payload, list):
            raise InventoryError("manifest_items_invalid")
        manifest = cls(
            manifest_id=str(payload.get("manifest_id", "")),
            run_id=str(payload.get("run_id", "")),
            actual_git_head=str(payload.get("actual_git_head", "")),
            source_scope_fingerprint=str(payload.get("source_scope_fingerprint", "")),
            source_snapshot_fingerprint=str(payload.get("source_snapshot_fingerprint", "")),
            keyed_label_secret=str(payload.get("keyed_label_secret", "")),
            items=[PrivateInventoryItem.from_dict(item) for item in items_payload],
            discovery_directories=[
                dict(value)
                for value in payload.get("discovery_directories", [])
                if isinstance(value, Mapping)
            ],
            final_directories=[
                dict(value)
                for value in payload.get("final_directories", [])
                if isinstance(value, Mapping)
            ],
            manifest_fingerprint=str(payload.get("manifest_fingerprint", "")),
        )
        manifest.validate()
        expected = _fingerprint(manifest._payload(include_fingerprint=False))
        if manifest.manifest_fingerprint != expected:
            raise InventoryError("manifest_fingerprint_mismatch")
        if payload.get("denominator") != manifest.denominator():
            raise InventoryError("manifest_denominator_tamper")
        return manifest

    def validate(self) -> None:
        try:
            uuid.UUID(self.manifest_id)
            uuid.UUID(self.run_id)
        except ValueError as exc:
            raise InventoryError("manifest_identity_invalid") from exc
        if not HEX64_RE.fullmatch(self.source_scope_fingerprint) or not HEX64_RE.fullmatch(
            self.source_snapshot_fingerprint
        ):
            raise InventoryError("manifest_scope_or_snapshot_invalid")
        if not HEX64_RE.fullmatch(self.keyed_label_secret):
            raise InventoryError("manifest_keyed_label_secret_invalid")
        for directories in (self.discovery_directories, self.final_directories):
            tokens = [value.get("path_token") for value in directories]
            if len(set(tokens)) != len(tokens) or any(
                not isinstance(value, Mapping)
                or set(value) != {"relative_path", "path_token", "signature"}
                or not isinstance(value.get("relative_path"), str)
                or not isinstance(value.get("path_token"), str)
                or not HEX64_RE.fullmatch(str(value.get("path_token")))
                or not isinstance(value.get("signature"), list)
                or len(value.get("signature", [])) != 5
                for value in directories
            ):
                raise InventoryError("manifest_directory_token_invalid")
        by_id: dict[str, PrivateInventoryItem] = {}
        labels: set[str] = set()
        for item in self.items:
            item.validate()
            if item.item_id in by_id or item.private_public_label in labels:
                raise InventoryError("manifest_item_identity_duplicate")
            if item.disposition is InventoryDisposition.DUPLICATE:
                primary = by_id.get(item.duplicate_of_item_id or "")
                if (
                    primary is None
                    or primary.disposition is not InventoryDisposition.ELIGIBLE_CANDIDATE
                    or primary.content_fingerprint != item.content_fingerprint
                ):
                    raise InventoryError("duplicate_primary_invalid")
            by_id[item.item_id] = item
            labels.add(item.private_public_label)
        denominator = self.denominator()
        if denominator["discovered"] != (
            denominator["supported"]
            + denominator["unsupported"]
            + denominator["unresolved"]
        ):
            raise InventoryError("discovered_equation_mismatch")
        if denominator["supported"] != sum(
            denominator[key]
            for key in (
                "duplicate",
                "cloud_recall_deferred",
                "unreadable_or_missing",
                "eligible_candidate",
            )
        ):
            raise InventoryError("supported_equation_mismatch")
        if denominator["eligible_candidate"] != sum(
            denominator[key]
            for key in ("imported", "import_deferred", "import_failed")
        ):
            raise InventoryError("eligible_equation_mismatch")

    def validate_derivations(self, context: TrustedRuntimeContext) -> None:
        """Rebuild all editable membership identities from trusted secrets."""

        try:
            secret = bytes.fromhex(self.keyed_label_secret)
        except ValueError as exc:
            raise InventoryError("manifest_keyed_label_secret_invalid") from exc
        expected_secret = hmac.new(
            context.roots.private_derivation_key,
            f"manifest-label\0{self.run_id}\0{self.manifest_id}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(secret, expected_secret):
            raise InventoryError("manifest_label_secret_derivation_mismatch")
        for item in self.items:
            relative = item.private_relative_path
            pure = Path(relative)
            if (
                not relative
                or pure.is_absolute()
                or "\\" in relative
                or any(part in {"", ".", ".."} for part in pure.parts)
                or pure.as_posix() != relative
            ):
                raise InventoryError("manifest_relative_path_noncanonical")
            expected_item, expected_token = _item_identity(context, relative)
            if item.private_path_token != expected_token or item.item_id != expected_item:
                raise InventoryError("manifest_membership_derivation_mismatch")
            if item.private_public_label != _private_public_label(secret, expected_item):
                raise InventoryError("manifest_private_label_derivation_mismatch")
            if item.extension != Path(relative).suffix.casefold():
                raise InventoryError("manifest_extension_derivation_mismatch")
            if item.observed_size != item.signature[1] or item.signature[1] < 0:
                raise InventoryError("manifest_signature_size_mismatch")
        for directories in (self.discovery_directories, self.final_directories):
            for directory in directories:
                relative = str(directory["relative_path"])
                if relative != "." and (
                    Path(relative).is_absolute()
                    or "\\" in relative
                    or any(part in {"", ".", ".."} for part in Path(relative).parts)
                    or Path(relative).as_posix() != relative
                ):
                    raise InventoryError("manifest_directory_path_noncanonical")
                if directory["path_token"] != _path_token(context, relative):
                    raise InventoryError("manifest_directory_token_derivation_mismatch")


@dataclass
class PrivateRunLedger:
    run_id: str
    manifest_id: str
    actual_git_head: str
    branch: str
    runtime_context_fingerprint: str
    source_scope_fingerprint: str
    root_registry_fingerprint: str
    budget_fingerprint: str
    policy_fingerprint: str
    status: RunStatus
    invocations: list[InvocationEvidence]
    total_hashed_bytes: int = 0
    total_unreadable_failures: int = 0
    consecutive_failures: int = 0
    same_reason_failures: dict[str, int] = field(default_factory=dict)
    source_mutation_count: int = 0
    stop_reason: str | None = None
    checkpoint_fingerprint: str = ""

    def _payload(self, *, include_checkpoint: bool) -> dict[str, Any]:
        payload = {
            "schema_version": RUN_LEDGER_SCHEMA_VERSION,
            "run_id": self.run_id,
            "manifest_id": self.manifest_id,
            "actual_git_head": self.actual_git_head,
            "branch": self.branch,
            "runtime_context_fingerprint": self.runtime_context_fingerprint,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "root_registry_fingerprint": self.root_registry_fingerprint,
            "budget_fingerprint": self.budget_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "status": self.status.value,
            "invocations": [
                invocation.to_dict(include_end_checkpoint=False)
                for invocation in self.invocations
            ],
            "total_hashed_bytes": self.total_hashed_bytes,
            "total_unreadable_failures": self.total_unreadable_failures,
            "consecutive_failures": self.consecutive_failures,
            "same_reason_failures": dict(sorted(self.same_reason_failures.items())),
            "source_mutation_count": self.source_mutation_count,
            "stop_reason": self.stop_reason,
        }
        if include_checkpoint:
            payload["checkpoint_fingerprint"] = self.checkpoint_fingerprint
        return payload

    def refresh_checkpoint(self) -> None:
        self.checkpoint_fingerprint = _fingerprint(
            self._payload(include_checkpoint=False)
        )

    def to_dict(self) -> dict[str, Any]:
        self.refresh_checkpoint()
        payload = self._payload(include_checkpoint=True)
        payload["invocations"] = [invocation.to_dict() for invocation in self.invocations]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PrivateRunLedger":
        if payload.get("schema_version") != RUN_LEDGER_SCHEMA_VERSION:
            raise InventoryError("run_ledger_schema_invalid")
        invocations_payload = payload.get("invocations")
        if not isinstance(invocations_payload, list):
            raise InventoryError("run_invocations_invalid")
        try:
            ledger = cls(
                run_id=str(payload["run_id"]),
                manifest_id=str(payload["manifest_id"]),
                actual_git_head=str(payload["actual_git_head"]),
                branch=str(payload["branch"]),
                runtime_context_fingerprint=str(payload["runtime_context_fingerprint"]),
                source_scope_fingerprint=str(payload["source_scope_fingerprint"]),
                root_registry_fingerprint=str(payload["root_registry_fingerprint"]),
                budget_fingerprint=str(payload["budget_fingerprint"]),
                policy_fingerprint=str(payload["policy_fingerprint"]),
                status=RunStatus(payload["status"]),
                invocations=[InvocationEvidence.from_dict(value) for value in invocations_payload],
                total_hashed_bytes=int(payload.get("total_hashed_bytes", 0)),
                total_unreadable_failures=int(payload.get("total_unreadable_failures", 0)),
                consecutive_failures=int(payload.get("consecutive_failures", 0)),
                same_reason_failures={
                    str(key): int(value)
                    for key, value in dict(payload.get("same_reason_failures", {})).items()
                },
                source_mutation_count=int(payload.get("source_mutation_count", 0)),
                stop_reason=(str(payload["stop_reason"]) if payload.get("stop_reason") else None),
                checkpoint_fingerprint=str(payload.get("checkpoint_fingerprint", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InventoryError("run_ledger_invalid") from exc
        ledger.validate()
        expected = _fingerprint(ledger._payload(include_checkpoint=False))
        if ledger.checkpoint_fingerprint != expected:
            raise InventoryError("run_checkpoint_fingerprint_mismatch")
        return ledger

    def validate(self) -> None:
        try:
            uuid.UUID(self.run_id)
            uuid.UUID(self.manifest_id)
        except ValueError as exc:
            raise InventoryError("run_identity_invalid") from exc
        fingerprints = (
            self.runtime_context_fingerprint,
            self.source_scope_fingerprint,
            self.root_registry_fingerprint,
            self.budget_fingerprint,
            self.policy_fingerprint,
        )
        if any(not HEX64_RE.fullmatch(value) for value in fingerprints):
            raise InventoryError("run_binding_fingerprint_invalid")
        if min(
            self.total_hashed_bytes,
            self.total_unreadable_failures,
            self.consecutive_failures,
            self.source_mutation_count,
        ) < 0:
            raise InventoryError("run_counter_invalid")
        ids: set[str] = set()
        for index, invocation in enumerate(self.invocations):
            if invocation.invocation_id in ids:
                raise InventoryError("duplicate_invocation_id")
            ids.add(invocation.invocation_id)
            if index == 0:
                if invocation.parent_checkpoint_fingerprint is not None:
                    raise InventoryError("initial_invocation_parent_unexpected")
            else:
                previous = self.invocations[index - 1]
                if (
                    invocation.parent_checkpoint_fingerprint
                    != previous.end_checkpoint_fingerprint
                    or invocation.pid == previous.pid
                    or invocation.process_start_observation
                    == previous.process_start_observation
                ):
                    raise InventoryError("restart_invocation_provenance_invalid")


def _fingerprint(payload: Mapping[str, Any] | Sequence[Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _process_start_observation() -> str:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            kernel32.GetCurrentProcess(),
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if ok:
            value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return f"windows_filetime:{value}"
    proc_stat = Path(f"/proc/{os.getpid()}/stat")
    if proc_stat.is_file():
        try:
            return f"proc_start_ticks:{proc_stat.read_text(encoding='ascii').split()[21]}"
        except (OSError, IndexError):
            pass
    return f"process_observation_fallback:{os.getpid()}:{time.monotonic_ns()}"


def _process_identity_for_pid(pid: int) -> tuple[bool, str | None]:
    """Return (definitely_alive, start identity); access uncertainty is live."""

    if pid <= 0:
        return False, None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: no such process
                return False, None
            return True, None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return True, None
            value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return True, f"windows_filetime:{value}"
        finally:
            kernel32.CloseHandle(handle)
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        return True, f"proc_start_ticks:{proc_stat.read_text(encoding='ascii').split()[21]}"
    except FileNotFoundError:
        return False, None
    except (OSError, IndexError):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, None
        except (PermissionError, OSError):
            return True, None
        return True, None


def _path_token(context: TrustedRuntimeContext, relative_path: str) -> str:
    return hmac.new(
        context.roots.private_derivation_key,
        f"path\0{context.source_scope.scope_fingerprint}\0{relative_path}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _item_identity(context: TrustedRuntimeContext, relative_path: str) -> tuple[str, str]:
    token = _path_token(context, relative_path)
    item_id = hashlib.sha256(
        f"{MEMBERSHIP_IDENTITY_VERSION}\0{context.source_scope.scope_fingerprint}\0{token}".encode(
            "ascii"
        )
    ).hexdigest()
    return item_id, token


def _private_public_label(secret: bytes, item_id: str) -> str:
    return "item_" + hmac.new(
        secret,
        f"{PUBLIC_LABEL_VERSION}\0{item_id}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()[:24]


def _signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_dev,
        metadata.st_ino,
    )


def _bounded_media_header_valid(
    extension: str, header: bytes, tail: bytes, total_size: int
) -> bool:
    """Bounded container/header screening, not pixel-level decode validation."""

    if total_size <= 0:
        return False
    if extension in {".jpg", ".jpeg"}:
        return total_size >= 4 and header.startswith(b"\xff\xd8\xff") and tail.endswith(b"\xff\xd9")
    if extension == ".png":
        return total_size >= 20 and header.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in tail
    if extension == ".gif":
        return total_size >= 14 and header[:6] in {b"GIF87a", b"GIF89a"} and tail.endswith(b";")
    if extension == ".webp":
        if total_size < 12 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
            return False
        return int.from_bytes(header[4:8], "little") + 8 == total_size
    if extension == ".avif":
        return total_size >= 12 and header[4:8] == b"ftyp" and b"avif" in header[:64]
    return False


class InventoryRunner:
    SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"})

    def __init__(
        self,
        *,
        context: TrustedRuntimeContext,
        budgets: InventoryBudgets,
        evidence_root: Path,
        attribute_adapter: AttributeAdapter,
        run_id: str | None = None,
        resume: bool = False,
        expected_parent_checkpoint: str | None = None,
        after_operation_intent: Any | None = None,
    ) -> None:
        budgets.validate()
        self.context = context
        self.budgets = budgets
        self.attribute_adapter = attribute_adapter
        evidence = Path(evidence_root).resolve(strict=True)
        if evidence != context.roots.roots["phase_evidence_output_root"]:
            raise InventoryError("evidence_root_registry_mismatch")
        self.artifacts = TaskOwnedArtifactStore(evidence)
        self.run_id = run_id or str(uuid.uuid4())
        try:
            uuid.UUID(self.run_id)
        except ValueError as exc:
            raise InventoryError("run_id_invalid") from exc
        self.run_dir = evidence / self.run_id
        self.manifest_path = self.run_dir / "private-manifest.json"
        self.run_path = self.run_dir / "private-run-ledger.json"
        self.operation_path = self.run_dir / "private-operation-ledger.json"
        self.checkpoint_path = self.run_dir / "private-inventory-checkpoint.json"
        self.lock_path = self.run_dir / "runner.lock"
        adapter_identity = attribute_adapter_identity(attribute_adapter)
        self.policy_fingerprint = _fingerprint(
            {
                "contract_id": CONTRACT_ID,
                "supported_extensions": sorted(self.SUPPORTED_EXTENSIONS),
                "cloud_unknown_policy": "defer_without_content_open",
                "real_source": False,
                "imported": 0,
                "network": 0,
                "attribute_adapter": adapter_identity,
                "header_validation": "bounded_magic_and_container_markers_v1",
            }
        )
        if resume and not self.run_dir.is_dir():
            raise InventoryError("resume_run_missing")
        if not resume:
            if self.run_dir.exists():
                raise InventoryError("duplicate_runner_or_run_id")
            try:
                os.mkdir(self.run_dir, 0o700)
            except OSError as exc:
                raise InventoryError("run_directory_create_failed") from exc
        self._lock_fd: int | None = None
        self._lock_fd = self._acquire_lock()
        try:
            if resume:
                self.manifest, self.ledger, checkpoint_operations = self._load_checkpoint()
                if expected_parent_checkpoint != self.ledger.checkpoint_fingerprint:
                    raise InventoryError("resume_parent_checkpoint_mismatch")
                if not self.ledger.invocations:
                    raise InventoryError("resume_parent_invocation_missing")
                previous = self.ledger.invocations[-1]
                if previous.pid == os.getpid():
                    raise InventoryError("same_process_restart_rejected")
                if previous.ended_at_ns is None:
                    alive, identity = _process_identity_for_pid(previous.pid)
                    if alive and (identity is None or identity == previous.process_start_observation):
                        raise InventoryError("resume_parent_invocation_still_live")
                self._validate_bindings()
            else:
                checkpoint_operations = None
                manifest_id = str(uuid.uuid4())
                secret = hmac.new(
                    context.roots.private_derivation_key,
                    f"manifest-label\0{self.run_id}\0{manifest_id}".encode("ascii"),
                    hashlib.sha256,
                ).digest()
                self.manifest = PrivateManifest(
                    manifest_id=manifest_id,
                    run_id=self.run_id,
                    actual_git_head=context.actual_git_head,
                    source_scope_fingerprint=context.source_scope.scope_fingerprint,
                    source_snapshot_fingerprint="0" * 64,
                    keyed_label_secret=secret.hex(),
                    items=[],
                )
                self.ledger = PrivateRunLedger(
                    run_id=self.run_id,
                    manifest_id=manifest_id,
                    actual_git_head=context.actual_git_head,
                    branch=context.branch,
                    runtime_context_fingerprint=context.context_fingerprint,
                    source_scope_fingerprint=context.source_scope.scope_fingerprint,
                    root_registry_fingerprint=context.roots.fingerprint,
                    budget_fingerprint=budgets.fingerprint,
                    policy_fingerprint=self.policy_fingerprint,
                    status=RunStatus.DISCOVERING,
                    invocations=[],
                )
            parent = self.ledger.checkpoint_fingerprint if resume else None
            self.invocation = InvocationEvidence(
                invocation_id=str(uuid.uuid4()),
                pid=os.getpid(),
                process_start_observation=_process_start_observation(),
                started_at_ns=time.time_ns(),
                parent_checkpoint_fingerprint=parent,
            )
            self.ledger.invocations.append(self.invocation)
            if checkpoint_operations is not None:
                OperationLedgerStore(
                    self.operation_path, artifact_store=self.artifacts
                ).save(checkpoint_operations)
            self.gateway = OperationGateway(
                context=context,
                store=OperationLedgerStore(
                    self.operation_path, artifact_store=self.artifacts
                ),
                run_id=self.run_id,
                invocation_id=self.invocation.invocation_id,
                attribute_adapter=attribute_adapter,
                on_persist=self._operation_persisted,
                after_intent=after_operation_intent,
            )
            if resume:
                previous = self.ledger.invocations[-2]
                if previous.ended_at_ns is None:
                    self.gateway.reconcile_interrupted_intents(
                        dead_invocation_id=previous.invocation_id
                    )
                    previous.ended_at_ns = time.time_ns()
                    previous.end_checkpoint_fingerprint = parent
            self._reconcile_counters_from_operations()
            self._persist_all()
        except Exception:
            if self._lock_fd is not None:
                self._release_lock(self._lock_fd)
                self._lock_fd = None
            raise

    def _validate_bindings(self) -> None:
        self.manifest.validate_derivations(self.context)
        expected = {
            "manifest_run": self.manifest.run_id == self.run_id,
            "ledger_run": self.ledger.run_id == self.run_id,
            "manifest_id": self.manifest.manifest_id == self.ledger.manifest_id,
            "git": self.ledger.actual_git_head == self.context.actual_git_head,
            "manifest_git": self.manifest.actual_git_head == self.context.actual_git_head,
            "branch": self.ledger.branch == self.context.branch,
            "context": self.ledger.runtime_context_fingerprint == self.context.context_fingerprint,
            "scope": self.ledger.source_scope_fingerprint
            == self.context.source_scope.scope_fingerprint,
            "manifest_scope": self.manifest.source_scope_fingerprint
            == self.context.source_scope.scope_fingerprint,
            "roots": self.ledger.root_registry_fingerprint == self.context.roots.fingerprint,
            "budgets": self.ledger.budget_fingerprint == self.budgets.fingerprint,
            "policy": self.ledger.policy_fingerprint == self.policy_fingerprint,
        }
        if not all(expected.values()):
            raise InventoryError("resume_context_head_scope_budget_policy_drift")

    def _save_manifest(self) -> None:
        self.manifest.validate()
        self.manifest.validate_derivations(self.context)
        self._save_checkpoint(self.gateway.ledger if hasattr(self, "gateway") else None)
        self.artifacts.atomic_write_json(self.manifest_path, self.manifest.to_dict())

    def _save_run(self) -> None:
        self.ledger.validate()
        self._save_checkpoint(self.gateway.ledger if hasattr(self, "gateway") else None)
        self.artifacts.atomic_write_json(self.run_path, self.ledger.to_dict())

    def _checkpoint_payload(self, operations: OperationLedger | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "manifest": self.manifest.to_dict(),
            "run_ledger": self.ledger.to_dict(),
            "operation_ledger": operations.to_dict() if operations is not None else None,
        }
        payload["checkpoint_bundle_fingerprint"] = _fingerprint(payload)
        return payload

    def _save_checkpoint(self, operations: OperationLedger | None) -> None:
        if not hasattr(self, "manifest") or not hasattr(self, "ledger"):
            return
        self.artifacts.atomic_write_json(
            self.checkpoint_path, self._checkpoint_payload(operations)
        )

    def _load_checkpoint(self) -> tuple[PrivateManifest, PrivateRunLedger, OperationLedger]:
        payload = load_private_json(self.checkpoint_path)
        if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise InventoryError("inventory_checkpoint_schema_invalid")
        claimed = payload.get("checkpoint_bundle_fingerprint")
        canonical = dict(payload)
        canonical.pop("checkpoint_bundle_fingerprint", None)
        if claimed != _fingerprint(canonical):
            raise InventoryError("inventory_checkpoint_fingerprint_mismatch")
        manifest_payload = payload.get("manifest")
        ledger_payload = payload.get("run_ledger")
        operation_payload = payload.get("operation_ledger")
        if not all(isinstance(value, Mapping) for value in (manifest_payload, ledger_payload, operation_payload)):
            raise InventoryError("inventory_checkpoint_payload_invalid")
        manifest = PrivateManifest.from_dict(manifest_payload)  # type: ignore[arg-type]
        ledger = PrivateRunLedger.from_dict(ledger_payload)  # type: ignore[arg-type]
        operations = OperationLedger.from_dict(operation_payload)  # type: ignore[arg-type]
        # The write-ahead operation projection may be one record newer than the
        # checkpoint if a process died between the two atomic replaces.
        if self.operation_path.exists():
            projected = OperationLedgerStore(
                self.operation_path, artifact_store=self.artifacts
            ).load()
            if len(projected.records) >= len(operations.records):
                operations = projected
        return manifest, ledger, operations

    def _operation_persisted(self, operations: OperationLedger) -> None:
        self._save_checkpoint(operations)

    def _persist_all(self) -> None:
        operations = self.gateway.ledger if hasattr(self, "gateway") else None
        self._save_checkpoint(operations)
        self.artifacts.atomic_write_json(self.manifest_path, self.manifest.to_dict())
        self.artifacts.atomic_write_json(self.run_path, self.ledger.to_dict())
        if operations is not None:
            OperationLedgerStore(
                self.operation_path, artifact_store=self.artifacts
            ).save(operations)

    def _reconcile_counters_from_operations(self) -> None:
        if not hasattr(self, "gateway"):
            return
        charged = sum(
            record.byte_count
            for record in self.gateway.ledger.records
            if record.kind.value == "source_file_read"
            and record.status is not OperationStatus.INTENT
        )
        self.ledger.total_hashed_bytes = max(self.ledger.total_hashed_bytes, charged)
        failed_attempt_reasons: dict[tuple[str, int], str] = {}
        for record in self.gateway.ledger.records:
            if (
                record.kind is OperationKind.SOURCE_FILE_READ
                and record.item_id is not None
                and record.status in {OperationStatus.FAILED, OperationStatus.INTERRUPTED}
            ):
                failed_attempt_reasons[(record.item_id, record.attempt)] = (
                    record.safe_reason_code or "source_read_failed"
                )
        for item in self.manifest.items:
            if item.disposition is InventoryDisposition.UNREADABLE_OR_MISSING:
                failed_attempt_reasons.setdefault(
                    (item.item_id, item.attempt_count),
                    item.reason_code or "source_read_failed",
                )
        conservative_failures = len(failed_attempt_reasons)
        self.ledger.total_unreadable_failures = max(
            self.ledger.total_unreadable_failures, conservative_failures
        )
        if conservative_failures:
            self.ledger.consecutive_failures = max(
                self.ledger.consecutive_failures, conservative_failures
            )
        reason_counts: dict[str, int] = {}
        for reason in failed_attempt_reasons.values():
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for reason, count in reason_counts.items():
            self.ledger.same_reason_failures[reason] = max(
                self.ledger.same_reason_failures.get(reason, 0), count
            )

    def _acquire_lock(self) -> int:
        if self.lock_path.exists() or self.lock_path.is_symlink():
            try:
                payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
                pid = int(payload["pid"])
                identity = str(payload["process_start_observation"])
                if str(payload["run_id"]) != self.run_id:
                    raise InventoryError("runner_lock_run_identity_mismatch")
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise InventoryError("runner_lock_unreadable") from exc
            if pid == os.getpid():
                raise InventoryError("duplicate_runner_lock_present")
            alive, live_identity = _process_identity_for_pid(pid)
            if alive and (live_identity is None or live_identity == identity):
                raise InventoryError("duplicate_runner_lock_present")
            try:
                self.lock_path.unlink()
            except OSError as exc:
                raise InventoryError("stale_runner_lock_recovery_failed") from exc
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            payload = json.dumps(
                {
                    "schema_version": "violet.scv2-fl1-i1-runner-lock.v1",
                    "run_id": self.run_id,
                    "pid": os.getpid(),
                    "process_start_observation": _process_start_observation(),
                },
                sort_keys=True,
            ).encode("ascii")
            os.write(descriptor, payload)
            os.fsync(descriptor)
            if os.name != "nt":
                metadata = os.fstat(descriptor)
                if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
                    raise InventoryError("runner_lock_mode_or_owner_invalid")
            return descriptor
        except InventoryError:
            raise
        except OSError as exc:
            raise InventoryError("duplicate_runner_lock_present") from exc

    def _release_lock(self, descriptor: int) -> None:
        os.close(descriptor)
        try:
            self.lock_path.unlink()
        except OSError as exc:
            raise InventoryError("runner_lock_release_failed") from exc

    def _discover(self) -> str:
        source = self.context.source_scope.root
        stack = [source]
        snapshot: list[dict[str, Any]] = []
        items: list[PrivateInventoryItem] = []
        total_entries = 0
        total_observed_bytes = 0
        deadline = time.monotonic() + self.budgets.traversal_timeout_seconds
        secret = bytes.fromhex(self.manifest.keyed_label_secret)
        while stack:
            if time.monotonic() > deadline:
                raise InventoryError("source_traversal_deadline_exceeded")
            directory = stack.pop()
            relative_directory = (
                "." if directory == source else directory.relative_to(source).as_posix()
            )
            directory_metadata = self.gateway.stat_entry(directory)
            if not stat.S_ISDIR(directory_metadata.st_mode):
                raise InventoryError("source_directory_type_changed")
            snapshot.append(
                {
                    "kind": "directory",
                    "relative_path": relative_directory,
                    "path_token": _path_token(self.context, relative_directory),
                    "signature": list(_signature(directory_metadata)),
                }
            )
            children = self.gateway.list_directory(
                directory,
                target_token=_path_token(self.context, relative_directory),
                max_entries=self.budgets.max_directory_entries - total_entries,
                deadline_monotonic=deadline,
            )
            total_entries += len(children)
            if total_entries > self.budgets.max_directory_entries:
                raise InventoryError("directory_entry_budget_exceeded")
            child_directories: list[Path] = []
            for child in children:
                path = Path(child.path)
                relative = path.relative_to(source).as_posix()
                candidate_item_id, candidate_token = _item_identity(
                    self.context, relative
                )
                metadata = self.gateway.stat_entry(path, item_id=candidate_item_id)
                attributes = getattr(metadata, "st_file_attributes", 0)
                if stat.S_ISLNK(metadata.st_mode):
                    raise InventoryError("source_symlink_or_reparse_rejected")
                if stat.S_ISDIR(metadata.st_mode):
                    if attributes & 0x400:
                        raise InventoryError("source_directory_reparse_rejected")
                    child_directories.append(path)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise InventoryError("source_special_file_rejected")
                item = PrivateInventoryItem(
                    item_id=candidate_item_id,
                    private_relative_path=relative,
                    private_path_token=candidate_token,
                    private_public_label=_private_public_label(
                        secret, candidate_item_id
                    ),
                    extension=path.suffix.casefold(),
                    signature=_signature(metadata),
                    observed_size=metadata.st_size,
                )
                items.append(item)
                total_observed_bytes += metadata.st_size
                snapshot.append(
                    {
                        "kind": "file",
                        "path_token": candidate_token,
                        "signature": list(item.signature),
                    }
                )
                if len(items) > self.budgets.max_discovered_items:
                    raise InventoryError("discovered_item_budget_exceeded")
                if total_observed_bytes > self.budgets.max_total_observed_bytes:
                    raise InventoryError("observed_byte_budget_exceeded")
            stack.extend(reversed(sorted(child_directories, key=lambda path: path.name)))
        if not items:
            raise InventoryError("source_fixture_empty")
        items.sort(key=lambda item: item.private_relative_path)
        self.manifest.items = items
        self.manifest.discovery_directories = sorted(
            [
                {
                    "relative_path": value["relative_path"],
                    "path_token": value["path_token"],
                    "signature": value["signature"],
                }
                for value in snapshot
                if value["kind"] == "directory"
            ],
            key=lambda value: value["path_token"],
        )
        self.manifest.source_snapshot_fingerprint = _fingerprint(
            sorted(snapshot, key=lambda value: (value["path_token"], value["kind"]))
        )
        self.manifest.validate()
        self._save_manifest()
        return self.manifest.source_snapshot_fingerprint

    def _snapshot_only(self) -> str:
        source = self.context.source_scope.root
        stack = [source]
        snapshot: list[dict[str, Any]] = []
        total_entries = 0
        total_items = 0
        total_observed_bytes = 0
        deadline = time.monotonic() + self.budgets.traversal_timeout_seconds
        while stack:
            if time.monotonic() > deadline:
                raise InventoryError("source_traversal_deadline_exceeded")
            directory = stack.pop()
            relative_directory = (
                "." if directory == source else directory.relative_to(source).as_posix()
            )
            directory_metadata = self.gateway.stat_entry(directory)
            if not stat.S_ISDIR(directory_metadata.st_mode):
                raise InventoryError("source_directory_type_changed")
            snapshot.append(
                {
                    "kind": "directory",
                    "relative_path": relative_directory,
                    "path_token": _path_token(self.context, relative_directory),
                    "signature": list(_signature(directory_metadata)),
                }
            )
            children = self.gateway.list_directory(
                directory,
                target_token=_path_token(self.context, relative_directory),
                max_entries=self.budgets.max_directory_entries - total_entries,
                deadline_monotonic=deadline,
            )
            total_entries += len(children)
            if total_entries > self.budgets.max_directory_entries:
                raise InventoryError("directory_entry_budget_exceeded")
            child_directories: list[Path] = []
            for child in children:
                path = Path(child.path)
                relative = path.relative_to(source).as_posix()
                candidate_item_id, candidate_token = _item_identity(
                    self.context, relative
                )
                metadata = self.gateway.stat_entry(path, item_id=candidate_item_id)
                attributes = getattr(metadata, "st_file_attributes", 0)
                if stat.S_ISLNK(metadata.st_mode):
                    raise InventoryError("source_symlink_or_reparse_rejected")
                if stat.S_ISDIR(metadata.st_mode):
                    if attributes & 0x400:
                        raise InventoryError("source_directory_reparse_rejected")
                    child_directories.append(path)
                elif stat.S_ISREG(metadata.st_mode):
                    total_items += 1
                    total_observed_bytes += metadata.st_size
                    if total_items > self.budgets.max_discovered_items:
                        raise InventoryError("discovered_item_budget_exceeded")
                    if total_observed_bytes > self.budgets.max_total_observed_bytes:
                        raise InventoryError("observed_byte_budget_exceeded")
                    snapshot.append(
                        {
                            "kind": "file",
                            "path_token": candidate_token,
                            "signature": list(_signature(metadata)),
                        }
                    )
                else:
                    raise InventoryError("source_special_file_rejected")
            stack.extend(reversed(sorted(child_directories, key=lambda path: path.name)))
        self.manifest.final_directories = sorted(
            [
                {
                    "relative_path": value["relative_path"],
                    "path_token": value["path_token"],
                    "signature": value["signature"],
                }
                for value in snapshot
                if value["kind"] == "directory"
            ],
            key=lambda value: value["path_token"],
        )
        return _fingerprint(
            sorted(snapshot, key=lambda value: (value["path_token"], value["kind"]))
        )

    def _record_failure(self, reason: str) -> None:
        self.ledger.total_unreadable_failures += 1
        self.ledger.consecutive_failures += 1
        self.ledger.same_reason_failures[reason] = (
            self.ledger.same_reason_failures.get(reason, 0) + 1
        )
        if (
            self.ledger.total_unreadable_failures
            > self.budgets.max_unreadable_failures
            or self.ledger.consecutive_failures
            > self.budgets.max_consecutive_failures
            or self.ledger.same_reason_failures[reason]
            > self.budgets.max_same_reason_failures
        ):
            self.ledger.status = RunStatus.BUDGET_STOP
            self.ledger.stop_reason = "failure_budget_exceeded"

    def _terminal(
        self,
        item: PrivateInventoryItem,
        disposition: InventoryDisposition,
        reason: str,
        *,
        content_fingerprint: str | None = None,
        duplicate_of: str | None = None,
    ) -> None:
        item.disposition = disposition
        item.reason_code = reason
        item.content_fingerprint = content_fingerprint
        item.duplicate_of_item_id = duplicate_of
        item.terminal_invocation_id = self.invocation.invocation_id
        item.validate()
        self._save_manifest()
        self._save_run()

    def run(self, *, stop_after_items: int | None = None) -> PrivateRunLedger:
        if stop_after_items is not None and stop_after_items <= 0:
            raise InventoryError("controlled_stop_limit_invalid")
        processed_this_invocation = 0
        structural_error: Exception | None = None
        try:
            if not self.manifest.items:
                self._discover()
            self.ledger.status = RunStatus.RUNNING
            self.ledger.stop_reason = None
            if (
                self.ledger.total_unreadable_failures
                > self.budgets.max_unreadable_failures
                or self.ledger.consecutive_failures
                > self.budgets.max_consecutive_failures
                or any(
                    value > self.budgets.max_same_reason_failures
                    for value in self.ledger.same_reason_failures.values()
                )
            ):
                self.ledger.status = RunStatus.BUDGET_STOP
                self.ledger.stop_reason = "failure_budget_exceeded"
            self._save_run()
            primary_by_content: dict[str, str] = {}
            for existing in self.manifest.items:
                if (
                    existing.disposition is InventoryDisposition.ELIGIBLE_CANDIDATE
                    and existing.content_fingerprint
                ):
                    primary_by_content.setdefault(
                        existing.content_fingerprint, existing.item_id
                    )
            for item in self.manifest.items:
                if item.terminal:
                    continue
                if stop_after_items is not None and processed_this_invocation >= stop_after_items:
                    self.ledger.status = RunStatus.CONTROLLED_STOP
                    self.ledger.stop_reason = "manual_stop_between_items"
                    break
                if processed_this_invocation >= self.budgets.batch_size:
                    self.ledger.status = RunStatus.CONTROLLED_STOP
                    self.ledger.stop_reason = "batch_boundary_stop"
                    break
                if self.ledger.status is RunStatus.BUDGET_STOP:
                    break
                processed_this_invocation += 1
                if item.attempt_count >= self.budgets.max_item_attempts:
                    self._record_failure("max_item_attempts_exceeded")
                    self._terminal(
                        item,
                        InventoryDisposition.UNREADABLE_OR_MISSING,
                        "max_item_attempts_exceeded",
                    )
                    continue
                item.attempt_count += 1
                self._save_manifest()
                path = self.context.source_scope.root / Path(item.private_relative_path)
                if item.extension == ".icloud":
                    self.gateway.defer_placeholder_by_policy(
                        path,
                        item_id=item.item_id,
                        attempt=item.attempt_count,
                    )
                    self.ledger.consecutive_failures = 0
                    self._terminal(
                        item,
                        InventoryDisposition.CLOUD_RECALL_DEFERRED,
                        "icloud_placeholder_deferred",
                    )
                    continue
                if item.extension not in self.SUPPORTED_EXTENSIONS:
                    self.ledger.consecutive_failures = 0
                    self._terminal(
                        item,
                        InventoryDisposition.UNSUPPORTED,
                        "unsupported_extension",
                    )
                    continue
                observation = self.gateway.observe_attributes(
                    path,
                    item_id=item.item_id,
                    attempt=item.attempt_count,
                )
                if observation.availability in {
                    CloudAvailability.UNKNOWN,
                    CloudAvailability.RECALL_RISK,
                }:
                    self.ledger.consecutive_failures = 0
                    self._terminal(
                        item,
                        InventoryDisposition.CLOUD_RECALL_DEFERRED,
                        (
                            "cloud_attribute_unknown"
                            if observation.availability is CloudAvailability.UNKNOWN
                            else "cloud_recall_risk"
                        ),
                    )
                    continue
                if observation.availability is CloudAvailability.REPARSE_POINT:
                    raise InventoryError("source_reparse_observation_rejected")
                if item.observed_size > self.budgets.max_per_file_hash_bytes:
                    self._record_failure("per_file_hash_budget_exceeded")
                    self._terminal(
                        item,
                        InventoryDisposition.UNREADABLE_OR_MISSING,
                        "per_file_hash_budget_exceeded",
                    )
                    continue
                if (
                    self.ledger.total_hashed_bytes + item.observed_size
                    > self.budgets.max_total_hashed_bytes
                ):
                    self.ledger.status = RunStatus.BUDGET_STOP
                    self.ledger.stop_reason = "total_hash_budget_exceeded"
                    break
                try:
                    remaining = self.budgets.max_total_hashed_bytes - self.ledger.total_hashed_bytes
                    if remaining <= 0:
                        self.ledger.status = RunStatus.BUDGET_STOP
                        self.ledger.stop_reason = "total_hash_budget_exceeded"
                        break
                    content, byte_count = self.gateway.hash_file(
                        path,
                        item_id=item.item_id,
                        attempt=item.attempt_count,
                        expected_signature=item.signature,
                        chunk_size=self.budgets.read_chunk_size,
                        timeout_seconds=self.budgets.per_item_timeout_seconds,
                        max_bytes=min(
                            self.budgets.max_per_file_hash_bytes,
                            remaining,
                        ),
                    )
                except OperationGatewayError as exc:
                    reason = str(exc)
                    self.ledger.total_hashed_bytes += exc.byte_count
                    if reason in {
                        "source_entry_changed_before_read",
                        "source_entry_changed_during_read",
                        "source_path_escape",
                        "source_special_file_rejected",
                    }:
                        raise InventoryError(reason) from exc
                    self._record_failure(reason)
                    self._terminal(
                        item,
                        InventoryDisposition.UNREADABLE_OR_MISSING,
                        reason if SAFE_REASON_RE.fullmatch(reason) else "source_read_failed",
                    )
                    continue
                self.ledger.total_hashed_bytes += byte_count
                self.ledger.consecutive_failures = 0
                header, tail = self.gateway.last_content_probe
                if not _bounded_media_header_valid(
                    item.extension, header, tail, byte_count
                ):
                    self._record_failure("corrupt_or_invalid_media")
                    self._terminal(
                        item,
                        InventoryDisposition.UNREADABLE_OR_MISSING,
                        "corrupt_or_invalid_media",
                    )
                    continue
                primary = primary_by_content.get(content)
                if primary is None:
                    primary_by_content[content] = item.item_id
                    self._terminal(
                        item,
                        InventoryDisposition.ELIGIBLE_CANDIDATE,
                        "eligible_import_deferred",
                        content_fingerprint=content,
                    )
                else:
                    self._terminal(
                        item,
                        InventoryDisposition.DUPLICATE,
                        "exact_content_duplicate",
                        content_fingerprint=content,
                        duplicate_of=primary,
                    )

            unresolved = self.manifest.denominator()["unresolved"]
            if self.ledger.status is RunStatus.RUNNING and unresolved == 0:
                after = self._snapshot_only()
                if after != self.manifest.source_snapshot_fingerprint:
                    self.ledger.source_mutation_count += 1
                    raise InventoryError("source_tree_changed_during_inventory")
                self.ledger.status = RunStatus.COMPLETE
                self.ledger.stop_reason = None
            elif self.ledger.status is RunStatus.RUNNING:
                self.ledger.status = RunStatus.CONTROLLED_STOP
                self.ledger.stop_reason = "incomplete_batch"
        except Exception as exc:
            structural_error = (
                InventoryError(str(exc))
                if isinstance(exc, OperationGatewayError)
                else exc
            )
            self.ledger.status = RunStatus.BLOCKED_INCOMPLETE
            self.ledger.stop_reason = (
                str(structural_error)
                if isinstance(structural_error, InventoryError)
                else "unexpected_inventory_error"
            )
        finally:
            try:
                self.invocation.ended_at_ns = time.time_ns()
                self._save_manifest()
                self._save_run()
                self.invocation.end_checkpoint_fingerprint = self.ledger.checkpoint_fingerprint
                self._save_run()
            finally:
                if self._lock_fd is not None:
                    self._release_lock(self._lock_fd)
                    self._lock_fd = None
        if structural_error is not None:
            if isinstance(structural_error, InventoryError):
                raise structural_error
            raise InventoryError("unexpected_inventory_error") from structural_error
        return self.ledger


def load_private_artifacts(run_dir: Path) -> tuple[PrivateRunLedger, PrivateManifest, dict[str, Any]]:
    root = Path(run_dir)
    ledger = PrivateRunLedger.from_dict(load_private_json(root / "private-run-ledger.json"))
    manifest = PrivateManifest.from_dict(load_private_json(root / "private-manifest.json"))
    operations = load_private_json(root / "private-operation-ledger.json")
    if ledger.run_id != manifest.run_id:
        raise InventoryError("private_artifact_run_binding_mismatch")
    return ledger, manifest, operations


def _load_budgets(path: Path) -> InventoryBudgets:
    return InventoryBudgets.from_dict(load_private_json(path))


def _adapter_from_args(args: argparse.Namespace) -> AttributeAdapter:
    if args.attribute_adapter == "windows":
        if args.synthetic_attributes:
            raise InventoryError("windows_adapter_synthetic_config_forbidden")
        return WindowsCloudAttributeAdapter()
    observations: dict[str, str] = {}
    default = CloudAvailability.AVAILABLE
    if args.synthetic_attributes:
        payload = load_private_json(Path(args.synthetic_attributes))
        if not set(payload).issubset({"observations", "default"}):
            raise InventoryError("synthetic_attribute_payload_unknown_field")
        raw = payload.get("observations", {})
        if not isinstance(raw, Mapping):
            raise InventoryError("synthetic_attribute_payload_invalid")
        observations = {str(key): str(value) for key, value in raw.items()}
        try:
            default = CloudAvailability(payload.get("default", "available"))
            for value in observations.values():
                CloudAvailability(value)
        except (TypeError, ValueError) as exc:
            raise InventoryError("synthetic_attribute_payload_invalid") from exc
    return SyntheticAttributeAdapter(observations=observations, default=default)


def _command_scan(args: argparse.Namespace) -> int:
    context = build_trusted_runtime_context(
        repo_root=Path(args.repo_root),
        expected_python=(Path(args.expected_python) if args.expected_python else None),
        private_root_config=Path(args.private_root_config),
        source_root=Path(args.source_root),
        source_mode=args.source_mode,
        source_scope_id=args.source_scope_id,
    )
    budgets = _load_budgets(Path(args.budgets_config))
    after_intent = None
    if args.test_abrupt_exit_after_read_intent:
        if args.source_mode != SourceMode.SYNTHETIC_FIXTURE.value:
            raise InventoryError("test_interrupt_hook_requires_synthetic_fixture")
        signal_path = Path(args.test_intent_signal_output)
        signal_store = TaskOwnedArtifactStore(Path(args.evidence_root))

        def abrupt_after_intent(record: Any) -> None:
            if record.kind is OperationKind.SOURCE_FILE_READ:
                signal_store.atomic_write_json(
                    signal_path,
                    {
                        "run_id": record.run_id,
                        "invocation_id": record.invocation_id,
                        "operation_id": record.operation_id,
                        "intent_persisted": True,
                    },
                )
                os._exit(91)

        after_intent = abrupt_after_intent
    runner = InventoryRunner(
        context=context,
        budgets=budgets,
        evidence_root=Path(args.evidence_root),
        attribute_adapter=_adapter_from_args(args),
        run_id=args.resume_run_id or args.new_run_id,
        resume=args.resume_run_id is not None,
        expected_parent_checkpoint=args.parent_checkpoint,
        after_operation_intent=after_intent,
    )
    ledger = runner.run(stop_after_items=args.stop_after_items)
    print(
        json.dumps(
            {
                "run_id": ledger.run_id,
                "invocation_id": runner.invocation.invocation_id,
                "pid": runner.invocation.pid,
                "process_start_observation": runner.invocation.process_start_observation,
                "status": ledger.status.value,
                "checkpoint_fingerprint": ledger.checkpoint_fingerprint,
                "unresolved": runner.manifest.denominator()["unresolved"],
                "private_paths_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="scan one temporary fixture")
    scan.add_argument("--repo-root", required=True)
    scan.add_argument("--expected-python")
    scan.add_argument("--private-root-config", required=True)
    scan.add_argument("--source-root", required=True)
    scan.add_argument("--source-mode", choices=[mode.value for mode in SourceMode], required=True)
    scan.add_argument("--source-scope-id", required=True)
    scan.add_argument("--evidence-root", required=True)
    scan.add_argument("--budgets-config", required=True)
    scan.add_argument("--attribute-adapter", choices=("synthetic", "windows"), default="synthetic")
    scan.add_argument("--synthetic-attributes")
    scan.add_argument("--stop-after-items", type=int)
    scan.add_argument("--resume-run-id")
    scan.add_argument("--new-run-id", help=argparse.SUPPRESS)
    scan.add_argument("--parent-checkpoint")
    scan.add_argument(
        "--test-abrupt-exit-after-read-intent",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    scan.add_argument("--test-intent-signal-output", help=argparse.SUPPRESS)
    scan.set_defaults(handler=_command_scan)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (InventoryError, OperationGatewayError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
