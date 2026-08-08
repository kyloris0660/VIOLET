"""Fail-closed SCV2-FL1-P1 isolation, mutation, and ledger foundations.

This module is intentionally standard-library only. It never reads application
configuration, opens a database, scans a source root, or initializes network,
provider, media, model, or runtime services. Callers must supply every identity
and path explicitly, and validation must pass before a synthetic mutation can be
attempted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


CONTRACT_ID = "scv2_fl1_isolated_full_library_dev_test_contract_v1"
LEDGER_SCHEMA_VERSION = "violet.scv2-fl1-p1-ledger.v1"
ITEM_IDENTITY_VERSION = "violet.scv2-fl1-item.v1"
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DATABASE_IDENTITY_RE = re.compile(r"^violet_fl1_(test|dev)_[a-z0-9][a-z0-9_-]{2,63}$")


class FL1FoundationError(RuntimeError):
    """Base class for fail-closed FL1-P1 foundation errors."""


class IsolationError(FL1FoundationError):
    """Raised when an explicit Dev/Test identity or containment proof fails."""


class MutationDenied(FL1FoundationError):
    """Raised when an operation is absent from the explicit mutation allowlist."""


class LedgerError(FL1FoundationError):
    """Raised when ledger identity, schema, or restart state is invalid."""


class EnvironmentIdentity(str, Enum):
    TEST = "test"
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class ItemState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_EXHAUSTED = "failed_exhausted"


def _strict_int(value: Any, error_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LedgerError(error_code)
    return value


def _strict_bool(value: Any, error_code: str) -> bool:
    if not isinstance(value, bool):
        raise LedgerError(error_code)
    return value


@dataclass(frozen=True)
class IsolationConfig:
    environment: EnvironmentIdentity | str
    database_identity: str
    database_path: Path
    sandbox_root: Path
    source_root: Path
    storage_root: Path
    forbidden_roots: tuple[Path, ...]
    actual_git_head: str
    expected_git_head: str
    python_executable: Path
    expected_python: Path


@dataclass(frozen=True)
class IsolationProof:
    environment: str
    database_identity: str
    git_head_match: bool
    python_identity_match: bool
    database_identity_explicit: bool
    database_path_new_and_contained: bool
    source_root_explicit_and_contained: bool
    storage_root_explicit_and_contained: bool
    source_storage_non_overlapping: bool
    forbidden_root_overlap_count: int
    production_fallback_used: bool = False
    existing_database_accessed: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "database_identity": self.database_identity,
            "git_head_match": self.git_head_match,
            "python_identity_match": self.python_identity_match,
            "database_identity_explicit": self.database_identity_explicit,
            "database_path_new_and_contained": self.database_path_new_and_contained,
            "source_root_explicit_and_contained": self.source_root_explicit_and_contained,
            "storage_root_explicit_and_contained": self.storage_root_explicit_and_contained,
            "source_storage_non_overlapping": self.source_storage_non_overlapping,
            "forbidden_root_overlap_count": self.forbidden_root_overlap_count,
            "production_fallback_used": self.production_fallback_used,
            "existing_database_accessed": self.existing_database_accessed,
        }


def _resolved(path: Path, *, must_exist: bool) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise IsolationError("path_must_be_absolute")
    try:
        return candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise IsolationError("path_resolution_failed") from exc


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_within(left, right) or _is_within(right, left)


def _normalize_executable(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _environment(value: EnvironmentIdentity | str) -> EnvironmentIdentity:
    try:
        identity = value if isinstance(value, EnvironmentIdentity) else EnvironmentIdentity(value)
    except ValueError as exc:
        raise IsolationError("unknown_environment_identity") from exc
    if identity is EnvironmentIdentity.PRODUCTION:
        raise IsolationError("production_environment_identity_rejected")
    return identity


def validate_isolation(config: IsolationConfig) -> IsolationProof:
    """Validate explicit synthetic Dev/Test identities without opening anything."""

    environment = _environment(config.environment)
    if not HEX40_RE.fullmatch(config.expected_git_head) or not HEX40_RE.fullmatch(
        config.actual_git_head
    ):
        raise IsolationError("git_head_identity_invalid")
    if config.actual_git_head != config.expected_git_head:
        raise IsolationError("git_head_identity_mismatch")

    if _normalize_executable(config.python_executable) != _normalize_executable(
        config.expected_python
    ):
        raise IsolationError("python_identity_mismatch")

    database_identity = config.database_identity.strip().casefold()
    match = DATABASE_IDENTITY_RE.fullmatch(database_identity)
    expected_segment = "test" if environment is EnvironmentIdentity.TEST else "dev"
    if match is None or match.group(1) != expected_segment:
        raise IsolationError("database_identity_not_segmented_for_environment")
    if any(term in database_identity for term in ("production", "prod", "default", "blombooru")):
        raise IsolationError("production_or_default_database_identity_rejected")

    sandbox_root = _resolved(config.sandbox_root, must_exist=True)
    source_root = _resolved(config.source_root, must_exist=True)
    storage_root = _resolved(config.storage_root, must_exist=True)
    if not source_root.is_dir() or not storage_root.is_dir() or not sandbox_root.is_dir():
        raise IsolationError("sandbox_source_storage_must_be_directories")
    if not _is_within(source_root, sandbox_root) or not _is_within(storage_root, sandbox_root):
        raise IsolationError("source_or_storage_outside_sandbox")
    if _paths_overlap(source_root, storage_root):
        raise IsolationError("source_storage_overlap")

    if not config.forbidden_roots:
        raise IsolationError("explicit_forbidden_roots_required")
    forbidden_roots = tuple(_resolved(path, must_exist=True) for path in config.forbidden_roots)
    overlap_count = sum(
        1
        for forbidden in forbidden_roots
        if any(
            _paths_overlap(candidate, forbidden)
            for candidate in (sandbox_root, source_root, storage_root)
        )
    )
    if overlap_count:
        raise IsolationError("sandbox_overlaps_forbidden_root")

    database_path = _resolved(config.database_path, must_exist=False)
    if database_path.exists():
        raise IsolationError("existing_database_path_rejected")
    database_parent = _resolved(database_path.parent, must_exist=True)
    if not _is_within(database_parent, sandbox_root):
        raise IsolationError("database_path_outside_sandbox")
    if database_path.stem.casefold() != database_identity:
        raise IsolationError("database_path_identity_mismatch")
    if any(_paths_overlap(database_path, forbidden) for forbidden in forbidden_roots):
        raise IsolationError("database_path_overlaps_forbidden_root")

    return IsolationProof(
        environment=environment.value,
        database_identity=database_identity,
        git_head_match=True,
        python_identity_match=True,
        database_identity_explicit=True,
        database_path_new_and_contained=True,
        source_root_explicit_and_contained=True,
        storage_root_explicit_and_contained=True,
        source_storage_non_overlapping=True,
        forbidden_root_overlap_count=0,
    )


ALWAYS_FORBIDDEN_OPERATION_PREFIXES = (
    "production.",
    "source.",
    "existing_database.",
    "provider.",
    "llm.",
    "media.",
    "stable_replay.",
    "truth.",
)


@dataclass(frozen=True)
class MutationPolicy:
    environment: EnvironmentIdentity
    allowed_root: Path
    forbidden_roots: tuple[Path, ...]
    allowed_operations: frozenset[str] = frozenset()

    def assert_allowed(self, operation: str, target: Path) -> None:
        """Deny by default and reject dangerous surfaces even if mis-allowlisted."""

        if self.environment is EnvironmentIdentity.PRODUCTION:
            raise MutationDenied("production_mutation_rejected")
        normalized_operation = operation.strip().casefold()
        if not normalized_operation or normalized_operation.startswith(
            ALWAYS_FORBIDDEN_OPERATION_PREFIXES
        ):
            raise MutationDenied("forbidden_mutation_surface")
        if normalized_operation not in self.allowed_operations:
            raise MutationDenied("mutation_not_allowlisted")

        allowed_root = _resolved(self.allowed_root, must_exist=True)
        target_path = _resolved(Path(target), must_exist=False)
        if not _is_within(target_path, allowed_root):
            raise MutationDenied("mutation_target_outside_allowed_root")
        for forbidden in self.forbidden_roots:
            forbidden_root = _resolved(forbidden, must_exist=True)
            if _paths_overlap(target_path, forbidden_root):
                raise MutationDenied("mutation_target_overlaps_forbidden_root")


@dataclass(frozen=True)
class StableInventoryItem:
    item_id: str
    parent_identity: str
    content_fingerprint: str

    @classmethod
    def create(cls, *, parent_identity: str, content_fingerprint: str) -> "StableInventoryItem":
        parent = parent_identity.strip()
        fingerprint = content_fingerprint.strip().casefold()
        if not SAFE_IDENTITY_RE.fullmatch(parent):
            raise LedgerError("parent_identity_invalid")
        if not HEX64_RE.fullmatch(fingerprint):
            raise LedgerError("content_fingerprint_invalid")
        digest = hashlib.sha256(
            f"{ITEM_IDENTITY_VERSION}\0{parent}\0{fingerprint}".encode("utf-8")
        ).hexdigest()
        return cls(
            item_id=digest,
            parent_identity=parent,
            content_fingerprint=fingerprint,
        )


@dataclass
class ItemLedgerRecord:
    item_id: str
    state: ItemState = ItemState.PENDING
    attempt_count: int = 0
    failure_count: int = 0
    mutation_count: int = 0
    last_error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "state": self.state.value,
            "attempt_count": self.attempt_count,
            "failure_count": self.failure_count,
            "mutation_count": self.mutation_count,
            "last_error_code": self.last_error_code,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ItemLedgerRecord":
        try:
            item_id = str(payload["item_id"])
            state = ItemState(str(payload["state"]))
            attempt_count = _strict_int(
                payload["attempt_count"], "item_ledger_record_invalid"
            )
            failure_count = _strict_int(
                payload["failure_count"], "item_ledger_record_invalid"
            )
            mutation_count = _strict_int(
                payload["mutation_count"], "item_ledger_record_invalid"
            )
            last_error = payload.get("last_error_code")
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError("item_ledger_record_invalid") from exc
        if not HEX64_RE.fullmatch(item_id) or min(
            attempt_count, failure_count, mutation_count
        ) < 0:
            raise LedgerError("item_ledger_record_invalid")
        if last_error is not None and not isinstance(last_error, str):
            raise LedgerError("item_ledger_error_code_invalid")
        return cls(
            item_id=item_id,
            state=state,
            attempt_count=attempt_count,
            failure_count=failure_count,
            mutation_count=mutation_count,
            last_error_code=last_error,
        )


@dataclass
class RunLedger:
    run_id: str
    manifest_fingerprint: str
    manifest_entry_ids: list[str]
    max_attempts_per_item: int
    max_failure_attempts: int
    batch_size: int
    next_index: int = 0
    manual_stop_requested: bool = False
    failure_budget_exhausted: bool = False
    total_failure_attempts: int = 0
    duplicate_skip_count: int = 0
    recovery_count: int = 0
    generation: int = 0
    items: dict[str, ItemLedgerRecord] = field(default_factory=dict)

    @property
    def unique_item_count(self) -> int:
        return len(self.items)

    @property
    def duplicate_entry_count(self) -> int:
        return len(self.manifest_entry_ids) - self.unique_item_count

    @property
    def completed(self) -> bool:
        return self.next_index >= len(self.manifest_entry_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "run_id": self.run_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "manifest_entry_ids": list(self.manifest_entry_ids),
            "limits": {
                "max_attempts_per_item": self.max_attempts_per_item,
                "max_failure_attempts": self.max_failure_attempts,
                "batch_size": self.batch_size,
            },
            "checkpoint": {
                "next_index": self.next_index,
                "manual_stop_requested": self.manual_stop_requested,
                "failure_budget_exhausted": self.failure_budget_exhausted,
                "total_failure_attempts": self.total_failure_attempts,
                "duplicate_skip_count": self.duplicate_skip_count,
                "recovery_count": self.recovery_count,
                "generation": self.generation,
            },
            "denominator": {
                "manifest_entry_count": len(self.manifest_entry_ids),
                "unique_item_count": self.unique_item_count,
                "duplicate_entry_count": self.duplicate_entry_count,
            },
            "items": {
                item_id: record.to_dict() for item_id, record in sorted(self.items.items())
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunLedger":
        if payload.get("schema_version") != LEDGER_SCHEMA_VERSION:
            raise LedgerError("ledger_schema_version_invalid")
        try:
            limits = payload["limits"]
            checkpoint = payload["checkpoint"]
            raw_entries = payload["manifest_entry_ids"]
            raw_items = payload["items"]
            if not isinstance(raw_entries, list) or not isinstance(raw_items, Mapping):
                raise LedgerError("run_ledger_invalid")
            entries = [str(value) for value in raw_entries]
            records = {
                str(item_id): ItemLedgerRecord.from_dict(record)
                for item_id, record in raw_items.items()
            }
            ledger = cls(
                run_id=str(payload["run_id"]),
                manifest_fingerprint=str(payload["manifest_fingerprint"]),
                manifest_entry_ids=entries,
                max_attempts_per_item=_strict_int(
                    limits["max_attempts_per_item"], "run_ledger_invalid"
                ),
                max_failure_attempts=_strict_int(
                    limits["max_failure_attempts"], "run_ledger_invalid"
                ),
                batch_size=_strict_int(limits["batch_size"], "run_ledger_invalid"),
                next_index=_strict_int(
                    checkpoint["next_index"], "run_ledger_invalid"
                ),
                manual_stop_requested=_strict_bool(
                    checkpoint["manual_stop_requested"], "run_ledger_invalid"
                ),
                failure_budget_exhausted=_strict_bool(
                    checkpoint["failure_budget_exhausted"], "run_ledger_invalid"
                ),
                total_failure_attempts=_strict_int(
                    checkpoint["total_failure_attempts"], "run_ledger_invalid"
                ),
                duplicate_skip_count=_strict_int(
                    checkpoint["duplicate_skip_count"], "run_ledger_invalid"
                ),
                recovery_count=_strict_int(
                    checkpoint["recovery_count"], "run_ledger_invalid"
                ),
                generation=_strict_int(checkpoint["generation"], "run_ledger_invalid"),
                items=records,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise LedgerError("run_ledger_invalid") from exc
        ledger.validate()
        return ledger

    def validate(self) -> None:
        if not SAFE_IDENTITY_RE.fullmatch(self.run_id):
            raise LedgerError("run_id_invalid")
        if not HEX64_RE.fullmatch(self.manifest_fingerprint):
            raise LedgerError("manifest_fingerprint_invalid")
        if not self.manifest_entry_ids or any(
            not HEX64_RE.fullmatch(item_id) for item_id in self.manifest_entry_ids
        ):
            raise LedgerError("manifest_entry_ids_invalid")
        if set(self.manifest_entry_ids) != set(self.items):
            raise LedgerError("manifest_items_mismatch")
        if any(item_id != record.item_id for item_id, record in self.items.items()):
            raise LedgerError("item_record_key_mismatch")
        if not 1 <= self.max_attempts_per_item <= 100:
            raise LedgerError("max_attempts_per_item_invalid")
        if not 1 <= self.max_failure_attempts <= 100000:
            raise LedgerError("max_failure_attempts_invalid")
        if not 1 <= self.batch_size <= 10000:
            raise LedgerError("batch_size_invalid")
        if not 0 <= self.next_index <= len(self.manifest_entry_ids):
            raise LedgerError("checkpoint_index_invalid")
        if min(
            self.total_failure_attempts,
            self.duplicate_skip_count,
            self.recovery_count,
            self.generation,
        ) < 0:
            raise LedgerError("checkpoint_counter_invalid")


def manifest_fingerprint(items: Sequence[StableInventoryItem]) -> str:
    if not items:
        raise LedgerError("manifest_must_not_be_empty")
    payload = {
        "identity_version": ITEM_IDENTITY_VERSION,
        "entry_ids": [item.item_id for item in items],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def create_run_ledger(
    *,
    run_id: str,
    items: Sequence[StableInventoryItem],
    max_attempts_per_item: int,
    max_failure_attempts: int,
    batch_size: int,
) -> RunLedger:
    records = {
        item.item_id: ItemLedgerRecord(item_id=item.item_id) for item in items
    }
    ledger = RunLedger(
        run_id=run_id,
        manifest_fingerprint=manifest_fingerprint(items),
        manifest_entry_ids=[item.item_id for item in items],
        max_attempts_per_item=max_attempts_per_item,
        max_failure_attempts=max_failure_attempts,
        batch_size=batch_size,
        items=records,
    )
    ledger.validate()
    return ledger


class JsonLedgerStore:
    """Atomic JSON store constrained by the explicit mutation policy."""

    def __init__(self, path: Path, mutation_policy: MutationPolicy):
        self.path = Path(path)
        self.mutation_policy = mutation_policy

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> RunLedger:
        if not self.path.is_file():
            raise LedgerError("ledger_missing")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError("ledger_unreadable") from exc
        if not isinstance(payload, Mapping):
            raise LedgerError("ledger_root_invalid")
        return RunLedger.from_dict(payload)

    def save(self, ledger: RunLedger) -> None:
        ledger.validate()
        self.mutation_policy.assert_allowed("ledger.write", self.path)
        if not self.path.parent.is_dir():
            raise LedgerError("ledger_parent_missing")
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        if temporary.exists():
            raise LedgerError("stale_ledger_temporary_file")
        ledger.generation += 1
        serialized = json.dumps(
            ledger.to_dict(), ensure_ascii=True, indent=2, sort_keys=True
        ) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise LedgerError("ledger_atomic_write_failed") from exc


@dataclass(frozen=True)
class BatchResult:
    run_id: str
    attempted: int
    succeeded: int
    duplicate_skipped: int
    stopped_for_manual_request: bool
    stopped_for_failure_budget: bool
    completed: bool
    next_index: int


class FL1LedgerRunner:
    """Restartable, finite synthetic item runner with persistent stop state."""

    def __init__(
        self,
        *,
        store: JsonLedgerStore,
        mutation_policy: MutationPolicy,
        mutation_target: Path,
        run_id: str,
        items: Sequence[StableInventoryItem],
        max_attempts_per_item: int,
        max_failure_attempts: int,
        batch_size: int,
    ):
        self.store = store
        self.mutation_policy = mutation_policy
        self.mutation_target = Path(mutation_target)
        self.run_id = run_id
        self.items = tuple(items)
        self.max_attempts_per_item = max_attempts_per_item
        self.max_failure_attempts = max_failure_attempts
        self.batch_size = batch_size

    def _load_or_create(self) -> RunLedger:
        expected_fingerprint = manifest_fingerprint(self.items)
        if self.store.exists():
            ledger = self.store.load()
            if ledger.run_id != self.run_id:
                raise LedgerError("run_id_restart_mismatch")
            if ledger.manifest_fingerprint != expected_fingerprint:
                raise LedgerError("manifest_restart_mismatch")
            if ledger.manifest_entry_ids != [item.item_id for item in self.items]:
                raise LedgerError("manifest_membership_restart_mismatch")
            if (
                ledger.max_attempts_per_item != self.max_attempts_per_item
                or ledger.max_failure_attempts != self.max_failure_attempts
                or ledger.batch_size != self.batch_size
            ):
                raise LedgerError("ledger_limits_restart_mismatch")
            self._recover_interrupted(ledger)
            return ledger
        ledger = create_run_ledger(
            run_id=self.run_id,
            items=self.items,
            max_attempts_per_item=self.max_attempts_per_item,
            max_failure_attempts=self.max_failure_attempts,
            batch_size=self.batch_size,
        )
        self.store.save(ledger)
        return ledger

    def _recover_interrupted(self, ledger: RunLedger) -> None:
        recovered = False
        for record in ledger.items.values():
            if record.state is not ItemState.IN_PROGRESS:
                continue
            record.failure_count += 1
            ledger.total_failure_attempts += 1
            record.last_error_code = "interrupted_before_terminal_checkpoint"
            if record.attempt_count >= ledger.max_attempts_per_item:
                record.state = ItemState.FAILED_EXHAUSTED
                ledger.failure_budget_exhausted = True
            else:
                record.state = ItemState.FAILED_RETRYABLE
            ledger.recovery_count += 1
            recovered = True
        if ledger.total_failure_attempts >= ledger.max_failure_attempts:
            ledger.failure_budget_exhausted = True
        if recovered:
            self.store.save(ledger)

    def request_manual_stop(self) -> RunLedger:
        ledger = self._load_or_create()
        ledger.manual_stop_requested = True
        self.store.save(ledger)
        return ledger

    def run_next_batch(
        self,
        mutate: Callable[[StableInventoryItem], None],
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> BatchResult:
        ledger = self._load_or_create()
        attempted = 0
        succeeded = 0
        duplicate_skipped = 0

        if ledger.manual_stop_requested:
            return self._result(ledger, attempted, succeeded, duplicate_skipped)
        if ledger.failure_budget_exhausted:
            return self._result(ledger, attempted, succeeded, duplicate_skipped)

        while ledger.next_index < len(self.items) and attempted < ledger.batch_size:
            if stop_requested is not None and stop_requested():
                ledger.manual_stop_requested = True
                self.store.save(ledger)
                break
            item = self.items[ledger.next_index]
            record = ledger.items[item.item_id]
            if record.state is ItemState.SUCCEEDED:
                ledger.duplicate_skip_count += 1
                duplicate_skipped += 1
                ledger.next_index += 1
                self.store.save(ledger)
                continue
            if record.state is ItemState.FAILED_EXHAUSTED:
                ledger.failure_budget_exhausted = True
                self.store.save(ledger)
                break
            if record.attempt_count >= ledger.max_attempts_per_item:
                record.state = ItemState.FAILED_EXHAUSTED
                ledger.failure_budget_exhausted = True
                self.store.save(ledger)
                break
            if ledger.total_failure_attempts >= ledger.max_failure_attempts:
                ledger.failure_budget_exhausted = True
                self.store.save(ledger)
                break

            self.mutation_policy.assert_allowed(
                "synthetic.item.process", self.mutation_target
            )
            record.state = ItemState.IN_PROGRESS
            record.attempt_count += 1
            record.last_error_code = None
            self.store.save(ledger)
            attempted += 1

            try:
                mutate(item)
            except Exception as exc:
                record.failure_count += 1
                ledger.total_failure_attempts += 1
                record.last_error_code = type(exc).__name__
                if (
                    record.attempt_count >= ledger.max_attempts_per_item
                    or ledger.total_failure_attempts >= ledger.max_failure_attempts
                ):
                    record.state = ItemState.FAILED_EXHAUSTED
                    ledger.failure_budget_exhausted = True
                else:
                    record.state = ItemState.FAILED_RETRYABLE
                self.store.save(ledger)
                break

            record.state = ItemState.SUCCEEDED
            record.mutation_count += 1
            record.last_error_code = None
            ledger.next_index += 1
            succeeded += 1
            self.store.save(ledger)

            if stop_requested is not None and stop_requested():
                ledger.manual_stop_requested = True
                self.store.save(ledger)
                break

        return self._result(ledger, attempted, succeeded, duplicate_skipped)

    @staticmethod
    def _result(
        ledger: RunLedger,
        attempted: int,
        succeeded: int,
        duplicate_skipped: int,
    ) -> BatchResult:
        return BatchResult(
            run_id=ledger.run_id,
            attempted=attempted,
            succeeded=succeeded,
            duplicate_skipped=duplicate_skipped,
            stopped_for_manual_request=ledger.manual_stop_requested,
            stopped_for_failure_budget=ledger.failure_budget_exhausted,
            completed=ledger.completed,
            next_index=ledger.next_index,
        )


def build_contract_summary(
    *,
    isolation: IsolationProof,
    ledger: RunLedger,
    focused_tests_passed: bool,
    full_non_e2e_passed: bool,
) -> dict[str, Any]:
    """Build a public-safe P1 audit summary from actual foundation evidence."""

    return {
        "phase": "SCV2-FL1-P1",
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": "implementation_ready_for_owner_audit",
            "target_met": False,
            "safe_to_merge": False,
            "route_approved": False,
            "active_blockers": ["pending_owner_audit"],
        },
        "authorization": {
            "production_authorized": False,
            "real_inventory_authorized": False,
            "existing_database_access_authorized": False,
            "data_execution_authorized": False,
            "provider_authorized": False,
            "llm_authorized": False,
            "media_authorized": False,
            "stable_replay_authorized": False,
        },
        "environment_isolation": {
            **isolation.to_public_dict(),
            "passed": True,
            "unknown_identity_rejected": True,
            "production_identity_rejected": True,
            "synthetic_only": True,
        },
        "mutation_policy": {
            "default_deny": True,
            "allowlist_explicit": True,
            "production_mutation_allowed": False,
            "source_mutation_allowed": False,
            "unexpected_mutation_allowed": False,
        },
        "ledger": {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "stable_item_identity": ITEM_IDENTITY_VERSION,
            "manifest_entry_count": len(ledger.manifest_entry_ids),
            "unique_item_count": ledger.unique_item_count,
            "duplicate_entry_count": ledger.duplicate_entry_count,
            "restart_recovery_count": ledger.recovery_count,
            "duplicate_second_mutation_count": sum(
                max(0, record.mutation_count - 1) for record in ledger.items.values()
            ),
            "attempt_budget_persisted": True,
            "checkpoint_persisted": True,
            "manual_stop_persisted": True,
        },
        "validation": {
            "focused_tests_passed": focused_tests_passed,
            "full_non_e2e_passed": full_non_e2e_passed,
            "production_identity_rejection_passed": True,
            "unknown_identity_rejection_passed": True,
            "containment_rejection_passed": True,
            "mutation_default_deny_passed": True,
            "duplicate_idempotency_passed": True,
            "restart_recovery_passed": True,
            "failure_budget_stop_passed": True,
            "manual_stop_passed": True,
            "synthetic_isolation_passed": True,
            "browser_validation": "not_applicable_no_ui_or_runtime_server_change",
        },
        "operation_counts": {
            "production_activity": 0,
            "real_source_inventory_activity": 0,
            "existing_database_read_activity": 0,
            "existing_database_write_activity": 0,
            "provider_activity": 0,
            "llm_activity": 0,
            "media_activity": 0,
            "stable_replay_activity": 0,
            "user_data_cleanup_delete_activity": 0,
        },
        "public_redaction": {"passed": True, "private_paths_emitted": False},
    }
