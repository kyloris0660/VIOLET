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
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


CONTRACT_ID = "scv2_fl1_isolated_full_library_dev_test_contract_v1"
LEDGER_SCHEMA_VERSION = "violet.scv2-fl1-p1-ledger.v2"
ITEM_IDENTITY_VERSION = "violet.scv2-fl1-item.v1"
LOGICAL_TARGET_IDENTITY_VERSION = "violet.scv2-fl1-logical-target.v1"
OPERATION_EVIDENCE_SCHEMA_VERSION = "violet.scv2-fl1-p1-operation-evidence.v2"
IMPLEMENTATION_EVIDENCE_SCHEMA_VERSION = (
    "violet.scv2-fl1-p1-implementation-evidence.v2"
)
SCENARIO_MATRIX_SCHEMA_VERSION = "violet.scv2-fl1-p1-scenario-matrix.v1"
MUTATION_ATTRIBUTION_SCHEMA_VERSION = (
    "violet.scv2-fl1-p1-mutation-attribution.v1"
)
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DATABASE_IDENTITY_RE = re.compile(r"^violet_fl1_(test|dev)_[a-z0-9][a-z0-9_-]{2,63}$")
SAFE_REPO_PATH_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")

REQUIRED_EXECUTED_STAGES: tuple[str, ...] = (
    "environment_isolation_preflight",
    "mutation_default_deny",
    "stable_inventory_identity",
    "restartable_item_ledger",
    "interrupted_mutation_reconciliation",
    "failure_budget_and_manual_stop",
    "forbidden_operation_evidence",
)

REQUIRED_FAILURE_BUDGET_SCENARIOS: tuple[str, ...] = (
    "normal_success",
    "manual_stop_restart",
    "per_item_exhaustion",
    "global_budget_exhaustion",
    "restart_counter_reason_consistency",
)

# Executable code, tests, and executable contracts may not drift after the
# implementation evidence boundary.  Only these exact governance projections
# may be committed between that boundary and the final reviewed head.
POST_IMPLEMENTATION_GOVERNANCE_PATH_ALLOWLIST = frozenset(
    {
        "docs/current-handoff.md",
        "docs/phase-contracts.md",
        "docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md",
        "docs/project-roadmap.md",
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/state/current-phase.json",
        "docs/test-workflow.md",
    }
)


class FL1FoundationError(RuntimeError):
    """Base class for fail-closed FL1-P1 foundation errors."""


class IsolationError(FL1FoundationError):
    """Raised when an explicit Dev/Test identity or containment proof fails."""


class MutationDenied(FL1FoundationError):
    """Raised when an operation is absent from the explicit mutation allowlist."""


class LedgerError(FL1FoundationError):
    """Raised when ledger identity, schema, or restart state is invalid."""


class MutationNotCommittedError(FL1FoundationError):
    """Explicit mutator proof that an invoked attempt made no side effect."""

    def __init__(self, error_code: str = "mutation_not_committed"):
        if not SAFE_IDENTITY_RE.fullmatch(error_code):
            raise ValueError("mutation_not_committed_error_code_invalid")
        super().__init__(error_code)
        self.error_code = error_code


class EnvironmentIdentity(str, Enum):
    TEST = "test"
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class ItemState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    OUTCOME_UNKNOWN = "outcome_unknown"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_EXHAUSTED = "failed_exhausted"
    DUPLICATE = "duplicate"


class InterruptedMutationOutcome(str, Enum):
    """Caller-derived reconciliation result for an interrupted mutation."""

    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    UNKNOWN = "unknown"


class ReconciliationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"


class ItemTerminalReason(str, Enum):
    ITEM_ATTEMPT_BUDGET_EXHAUSTED = "item_attempt_budget_exhausted"
    DUPLICATE_LOGICAL_TARGET = "duplicate_logical_target"


class GlobalStopReason(str, Enum):
    FAILURE_BUDGET_EXHAUSTED = "global_failure_budget_exhausted"


class OperationKind(str, Enum):
    SYNTHETIC_MUTATION_INVOCATION = "synthetic_mutation_invocation"
    PRODUCTION_ACTIVITY = "production_activity"
    REAL_SOURCE_INVENTORY_ACTIVITY = "real_source_inventory_activity"
    EXISTING_DATABASE_READ_ACTIVITY = "existing_database_read_activity"
    EXISTING_DATABASE_WRITE_ACTIVITY = "existing_database_write_activity"
    PROVIDER_ACTIVITY = "provider_activity"
    LLM_ACTIVITY = "llm_activity"
    MEDIA_ACTIVITY = "media_activity"
    STABLE_REPLAY_ACTIVITY = "stable_replay_activity"
    USER_DATA_CLEANUP_DELETE_ACTIVITY = "user_data_cleanup_delete_activity"


FORBIDDEN_OPERATION_KINDS: tuple[OperationKind, ...] = tuple(
    kind
    for kind in OperationKind
    if kind is not OperationKind.SYNTHETIC_MUTATION_INVOCATION
)


class AuthorizationKind(str, Enum):
    MERGE = "merge"
    NEXT_PHASE_ROUTE = "next_phase_route"


class ImplementationEvidenceMode(str, Enum):
    PR_AUDIT = "pr_audit"
    SQUASH_CARRY_FORWARD = "squash_carry_forward"


def _strict_int(value: Any, error_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LedgerError(error_code)
    return value


def _strict_bool(value: Any, error_code: str) -> bool:
    if not isinstance(value, bool):
        raise LedgerError(error_code)
    return value


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class OperationEvent:
    """Category-only persisted execution evidence; never carries local paths."""

    sequence: int
    kind: OperationKind
    item_id: str | None = None

    def validate(self) -> None:
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise LedgerError("operation_event_sequence_invalid")
        if not isinstance(self.kind, OperationKind):
            raise LedgerError("operation_event_kind_invalid")
        if self.item_id is not None and not HEX64_RE.fullmatch(self.item_id):
            raise LedgerError("operation_event_item_id_invalid")
        if (
            self.kind is OperationKind.SYNTHETIC_MUTATION_INVOCATION
            and self.item_id is None
        ):
            raise LedgerError("synthetic_mutation_invocation_item_id_required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "sequence": self.sequence,
            "kind": self.kind.value,
            "item_id": self.item_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OperationEvent":
        try:
            event = cls(
                sequence=_strict_int(
                    payload["sequence"], "operation_event_sequence_invalid"
                ),
                kind=OperationKind(str(payload["kind"])),
                item_id=(
                    str(payload["item_id"])
                    if payload.get("item_id") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError("operation_event_invalid") from exc
        event.validate()
        return event


@dataclass(frozen=True)
class ImplementationEvidence:
    """Immutable Git evidence boundary plus exact governance-only carry-forward."""

    mode: ImplementationEvidenceMode
    approved_base_commit: str
    implementation_commit: str
    implementation_tree: str
    final_commit: str
    final_tree: str
    carry_forward_commit: str | None
    carry_forward_tree: str | None
    post_implementation_changed_paths: tuple[str, ...]
    evidence_digest: str

    @classmethod
    def create(
        cls,
        *,
        mode: ImplementationEvidenceMode = ImplementationEvidenceMode.PR_AUDIT,
        approved_base_commit: str,
        implementation_commit: str,
        implementation_tree: str,
        final_commit: str,
        final_tree: str,
        carry_forward_commit: str | None = None,
        carry_forward_tree: str | None = None,
        post_implementation_changed_paths: Sequence[str],
    ) -> "ImplementationEvidence":
        changed_paths = tuple(sorted(set(post_implementation_changed_paths)))
        payload = {
            "schema_version": IMPLEMENTATION_EVIDENCE_SCHEMA_VERSION,
            "mode": mode.value,
            "approved_base_commit": approved_base_commit,
            "implementation_commit": implementation_commit,
            "implementation_tree": implementation_tree,
            "final_commit": final_commit,
            "final_tree": final_tree,
            "carry_forward_commit": carry_forward_commit,
            "carry_forward_tree": carry_forward_tree,
            "post_implementation_changed_paths": list(changed_paths),
        }
        evidence = cls(
            mode=mode,
            approved_base_commit=approved_base_commit,
            implementation_commit=implementation_commit,
            implementation_tree=implementation_tree,
            final_commit=final_commit,
            final_tree=final_tree,
            carry_forward_commit=carry_forward_commit,
            carry_forward_tree=carry_forward_tree,
            post_implementation_changed_paths=changed_paths,
            evidence_digest=_canonical_digest(payload),
        )
        evidence.validate()
        return evidence

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": IMPLEMENTATION_EVIDENCE_SCHEMA_VERSION,
            "mode": self.mode.value,
            "approved_base_commit": self.approved_base_commit,
            "implementation_commit": self.implementation_commit,
            "implementation_tree": self.implementation_tree,
            "final_commit": self.final_commit,
            "final_tree": self.final_tree,
            "carry_forward_commit": self.carry_forward_commit,
            "carry_forward_tree": self.carry_forward_tree,
            "post_implementation_changed_paths": list(
                self.post_implementation_changed_paths
            ),
        }

    def validate(self) -> None:
        if not isinstance(self.mode, ImplementationEvidenceMode):
            raise LedgerError("implementation_evidence_mode_invalid")
        for value in (
            self.approved_base_commit,
            self.implementation_commit,
            self.implementation_tree,
            self.final_commit,
            self.final_tree,
        ):
            if not HEX40_RE.fullmatch(value):
                raise LedgerError("implementation_evidence_git_identity_invalid")
        if self.mode is ImplementationEvidenceMode.PR_AUDIT:
            if self.carry_forward_commit is not None or self.carry_forward_tree is not None:
                raise LedgerError("implementation_evidence_pr_carry_forward_forbidden")
        elif not (
            isinstance(self.carry_forward_commit, str)
            and HEX40_RE.fullmatch(self.carry_forward_commit)
            and isinstance(self.carry_forward_tree, str)
            and HEX40_RE.fullmatch(self.carry_forward_tree)
            and self.carry_forward_tree == self.final_tree
        ):
            raise LedgerError("implementation_evidence_squash_binding_invalid")
        paths = self.post_implementation_changed_paths
        if paths != tuple(sorted(set(paths))) or any(
            not SAFE_REPO_PATH_RE.fullmatch(path) for path in paths
        ):
            raise LedgerError("implementation_evidence_changed_paths_invalid")
        if any(
            path not in POST_IMPLEMENTATION_GOVERNANCE_PATH_ALLOWLIST
            for path in paths
        ):
            raise LedgerError("implementation_evidence_executable_drift")
        if self.final_commit == self.implementation_commit:
            if paths or self.final_tree != self.implementation_tree:
                raise LedgerError("implementation_evidence_same_commit_mismatch")
        elif not paths:
            # A different commit with an identical tree is acceptable, but it
            # must bind the same immutable implementation tree.
            if self.final_tree != self.implementation_tree:
                raise LedgerError("implementation_evidence_unaccounted_tree_drift")
        if not HEX64_RE.fullmatch(self.evidence_digest) or (
            self.evidence_digest != _canonical_digest(self._payload())
        ):
            raise LedgerError("implementation_evidence_digest_mismatch")

    def to_public_dict(self) -> dict[str, Any]:
        self.validate()
        return {**self._payload(), "evidence_digest": self.evidence_digest}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ImplementationEvidence":
        try:
            evidence = cls(
                mode=ImplementationEvidenceMode(str(payload["mode"])),
                approved_base_commit=str(payload["approved_base_commit"]),
                implementation_commit=str(payload["implementation_commit"]),
                implementation_tree=str(payload["implementation_tree"]),
                final_commit=str(payload["final_commit"]),
                final_tree=str(payload["final_tree"]),
                carry_forward_commit=(
                    str(payload["carry_forward_commit"])
                    if payload.get("carry_forward_commit") is not None
                    else None
                ),
                carry_forward_tree=(
                    str(payload["carry_forward_tree"])
                    if payload.get("carry_forward_tree") is not None
                    else None
                ),
                post_implementation_changed_paths=tuple(
                    str(path)
                    for path in payload["post_implementation_changed_paths"]
                ),
                evidence_digest=str(payload["evidence_digest"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError("implementation_evidence_invalid") from exc
        evidence.validate()
        return evidence


@dataclass(frozen=True)
class OwnerAcceptanceEvidence:
    """Owner acceptance bound to the implementation and final reviewed Git state."""

    identity: str
    implementation_commit: str
    implementation_tree: str
    implementation_digest: str
    reviewed_final_commit: str
    reviewed_final_tree: str

    def validate_against(self, evidence: ImplementationEvidence) -> None:
        evidence.validate()
        if not SAFE_IDENTITY_RE.fullmatch(self.identity):
            raise LedgerError("owner_acceptance_identity_invalid")
        if any(
            not HEX40_RE.fullmatch(value)
            for value in (
                self.implementation_commit,
                self.implementation_tree,
                self.reviewed_final_commit,
                self.reviewed_final_tree,
            )
        ) or not HEX64_RE.fullmatch(self.implementation_digest):
            raise LedgerError("owner_acceptance_git_binding_invalid")
        if (
            self.implementation_commit != evidence.implementation_commit
            or self.implementation_tree != evidence.implementation_tree
            or self.implementation_digest != evidence.evidence_digest
            or self.reviewed_final_commit != evidence.final_commit
            or self.reviewed_final_tree != evidence.final_tree
        ):
            raise LedgerError("owner_acceptance_evidence_mismatch")

    def to_public_dict(self, evidence: ImplementationEvidence) -> dict[str, str]:
        self.validate_against(evidence)
        return {
            "identity": self.identity,
            "implementation_commit": self.implementation_commit,
            "implementation_tree": self.implementation_tree,
            "implementation_digest": self.implementation_digest,
            "reviewed_final_commit": self.reviewed_final_commit,
            "reviewed_final_tree": self.reviewed_final_tree,
        }


@dataclass(frozen=True)
class BoundAuthorizationEvidence:
    """Merge and next-route authorization are distinct from acceptance."""

    kind: AuthorizationKind
    identity: str
    owner_acceptance_identity: str
    reviewed_final_commit: str
    reviewed_final_tree: str
    route_scope: str | None = None

    def validate_against(
        self,
        implementation: ImplementationEvidence,
        acceptance: OwnerAcceptanceEvidence,
    ) -> None:
        if not isinstance(self.kind, AuthorizationKind):
            raise LedgerError("bound_authorization_kind_invalid")
        if not SAFE_IDENTITY_RE.fullmatch(self.identity):
            raise LedgerError("bound_authorization_identity_invalid")
        acceptance.validate_against(implementation)
        if (
            self.owner_acceptance_identity != acceptance.identity
            or self.reviewed_final_commit != implementation.final_commit
            or self.reviewed_final_tree != implementation.final_tree
        ):
            raise LedgerError("bound_authorization_evidence_mismatch")
        if self.kind is AuthorizationKind.MERGE and self.route_scope is not None:
            raise LedgerError("merge_authorization_route_scope_forbidden")
        if self.kind is AuthorizationKind.NEXT_PHASE_ROUTE:
            if not self.route_scope or not SAFE_IDENTITY_RE.fullmatch(self.route_scope):
                raise LedgerError("route_authorization_scope_invalid")

    def to_public_dict(
        self,
        implementation: ImplementationEvidence,
        acceptance: OwnerAcceptanceEvidence,
    ) -> dict[str, Any]:
        self.validate_against(implementation, acceptance)
        return {
            "kind": self.kind.value,
            "identity": self.identity,
            "owner_acceptance_identity": self.owner_acceptance_identity,
            "reviewed_final_commit": self.reviewed_final_commit,
            "reviewed_final_tree": self.reviewed_final_tree,
            "route_scope": self.route_scope,
        }


def _git(
    repo_root: Path, *arguments: str, allow_failure: bool = False
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 and not allow_failure:
        raise LedgerError("implementation_evidence_git_command_failed")
    return completed


def collect_implementation_evidence(
    *,
    repo_root: Path,
    approved_base_commit: str,
    implementation_commit: str,
    final_commit: str = "HEAD",
    mode: ImplementationEvidenceMode = ImplementationEvidenceMode.PR_AUDIT,
    carry_forward_commit: str = "HEAD",
) -> ImplementationEvidence:
    """Collect topology-bound evidence from Git; caller-supplied paths are refused."""

    root = Path(repo_root).resolve(strict=True)
    top_level = _git(root, "rev-parse", "--show-toplevel").stdout.decode(
        "utf-8", errors="strict"
    ).strip()
    if Path(top_level).resolve(strict=True) != root:
        raise LedgerError("implementation_evidence_repo_root_mismatch")

    head = _git(root, "rev-parse", "HEAD^{commit}").stdout.decode(
        "ascii", errors="strict"
    ).strip()
    base = _git(
        root, "rev-parse", f"{approved_base_commit}^{{commit}}"
    ).stdout.decode("ascii", errors="strict").strip()
    implementation = _git(
        root, "rev-parse", f"{implementation_commit}^{{commit}}"
    ).stdout.decode("ascii", errors="strict").strip()
    final = _git(
        root, "rev-parse", f"{final_commit}^{{commit}}"
    ).stdout.decode("ascii", errors="strict").strip()
    if _git(
        root,
        "merge-base",
        "--is-ancestor",
        base,
        implementation,
        allow_failure=True,
    ).returncode != 0:
        raise LedgerError("implementation_evidence_base_not_ancestor")
    if _git(
        root,
        "merge-base",
        "--is-ancestor",
        implementation,
        final,
        allow_failure=True,
    ).returncode != 0:
        raise LedgerError("implementation_evidence_not_ancestor_of_final")

    carry: str | None = None
    carry_tree: str | None = None
    if mode is ImplementationEvidenceMode.PR_AUDIT:
        if final != head:
            raise LedgerError("implementation_evidence_final_not_current_head")
    elif mode is ImplementationEvidenceMode.SQUASH_CARRY_FORWARD:
        carry = _git(
            root, "rev-parse", f"{carry_forward_commit}^{{commit}}"
        ).stdout.decode("ascii", errors="strict").strip()
        if carry != head:
            raise LedgerError("implementation_evidence_carry_forward_not_current_head")
        parent_row = _git(root, "rev-list", "--parents", "-n", "1", carry).stdout.decode(
            "ascii", errors="strict"
        ).strip().split()
        if len(parent_row) != 2 or parent_row[1] != base:
            raise LedgerError("implementation_evidence_squash_parent_invalid")
        carry_tree = _git(root, "rev-parse", f"{carry}^{{tree}}").stdout.decode(
            "ascii", errors="strict"
        ).strip()
    else:  # pragma: no cover - Enum guard for future modes
        raise LedgerError("implementation_evidence_mode_invalid")
    implementation_tree = _git(
        root, "rev-parse", f"{implementation}^{{tree}}"
    ).stdout.decode("ascii", errors="strict").strip()
    final_tree = _git(root, "rev-parse", f"{final}^{{tree}}").stdout.decode(
        "ascii", errors="strict"
    ).strip()
    if carry_tree is not None and carry_tree != final_tree:
        raise LedgerError("implementation_evidence_squash_tree_mismatch")
    raw_paths = _git(
        root,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        f"{implementation}..{final}",
    ).stdout
    changed_paths = tuple(
        path.decode("utf-8", errors="strict").replace("\\", "/")
        for path in raw_paths.split(b"\0")
        if path
    )
    return ImplementationEvidence.create(
        mode=mode,
        approved_base_commit=base,
        implementation_commit=implementation,
        implementation_tree=implementation_tree,
        final_commit=final,
        final_tree=final_tree,
        carry_forward_commit=carry,
        carry_forward_tree=carry_tree,
        post_implementation_changed_paths=changed_paths,
    )


def verify_implementation_evidence_repository(
    *, repo_root: Path, evidence: ImplementationEvidence
) -> None:
    """Recollect an evidence claim from the repository and require exact equality."""

    evidence.validate()
    recollected = collect_implementation_evidence(
        repo_root=repo_root,
        approved_base_commit=evidence.approved_base_commit,
        implementation_commit=evidence.implementation_commit,
        final_commit=evidence.final_commit,
        mode=evidence.mode,
        carry_forward_commit=evidence.carry_forward_commit or "HEAD",
    )
    if recollected != evidence:
        raise LedgerError("implementation_evidence_repository_mismatch")


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
    database_source_storage_pairwise_disjoint: bool
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
            "database_source_storage_pairwise_disjoint": (
                self.database_source_storage_pairwise_disjoint
            ),
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
    # Do not collapse a venv launcher symlink to its base interpreter.  On
    # POSIX, realpath(.venv/bin/python) commonly equals the system Python and
    # would let a non-venv process satisfy an exact interpreter check.
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _environment(value: EnvironmentIdentity | str) -> EnvironmentIdentity:
    normalized = value
    if isinstance(value, str):
        normalized = value.strip().casefold()
    try:
        identity = EnvironmentIdentity(normalized)
    except (TypeError, ValueError) as exc:
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
    database_parent = _resolved(database_path.parent, must_exist=True)
    if not _is_within(database_parent, sandbox_root):
        raise IsolationError("database_path_outside_sandbox")
    if any(
        _paths_overlap(left, right)
        for left, right in (
            (database_parent, source_root),
            (database_parent, storage_root),
            (database_path, source_root),
            (database_path, storage_root),
        )
    ):
        raise IsolationError("database_source_storage_overlap")
    if database_path.exists():
        raise IsolationError("existing_database_path_rejected")
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
        database_source_storage_pairwise_disjoint=True,
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
    environment: EnvironmentIdentity | str
    allowed_root: Path
    forbidden_roots: tuple[Path, ...]
    allowed_operations: frozenset[str] = frozenset()

    def assert_target_contained(self, target: Path) -> None:
        """Reject reads or writes outside the explicitly isolated storage root."""

        allowed_root = _resolved(self.allowed_root, must_exist=True)
        target_path = _resolved(Path(target), must_exist=False)
        if not _is_within(target_path, allowed_root):
            raise MutationDenied("mutation_target_outside_allowed_root")
        for forbidden in self.forbidden_roots:
            forbidden_root = _resolved(forbidden, must_exist=True)
            if _paths_overlap(target_path, forbidden_root):
                raise MutationDenied("mutation_target_overlaps_forbidden_root")

    def assert_allowed(self, operation: str, target: Path) -> None:
        """Deny by default and reject dangerous surfaces even if mis-allowlisted."""

        try:
            environment = _environment(self.environment)
        except IsolationError as exc:
            if str(exc) == "production_environment_identity_rejected":
                raise MutationDenied("production_mutation_rejected") from exc
            raise MutationDenied("unknown_environment_identity") from exc
        if environment is EnvironmentIdentity.PRODUCTION:  # pragma: no cover
            raise MutationDenied("production_mutation_rejected")
        normalized_operation = operation.strip().casefold()
        if not normalized_operation or normalized_operation.startswith(
            ALWAYS_FORBIDDEN_OPERATION_PREFIXES
        ):
            raise MutationDenied("forbidden_mutation_surface")
        if normalized_operation not in self.allowed_operations:
            raise MutationDenied("mutation_not_allowlisted")

        self.assert_target_contained(target)


@dataclass(frozen=True)
class StableInventoryItem:
    item_id: str
    logical_target_id: str
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
        logical_target_id = hashlib.sha256(
            f"{LOGICAL_TARGET_IDENTITY_VERSION}\0{fingerprint}".encode("utf-8")
        ).hexdigest()
        return cls(
            item_id=digest,
            logical_target_id=logical_target_id,
            parent_identity=parent,
            content_fingerprint=fingerprint,
        )

    def validate(self) -> None:
        expected = self.create(
            parent_identity=self.parent_identity,
            content_fingerprint=self.content_fingerprint,
        )
        if self.item_id != expected.item_id:
            raise LedgerError("item_identity_mismatch")
        if self.logical_target_id != expected.logical_target_id:
            raise LedgerError("logical_target_identity_mismatch")


@dataclass
class ItemLedgerRecord:
    item_id: str
    logical_target_id: str
    content_fingerprint: str
    state: ItemState = ItemState.PENDING
    attempt_count: int = 0
    failure_count: int = 0
    mutation_count: int = 0
    last_error_code: str | None = None
    duplicate_of_item_id: str | None = None
    reconciliation_status: ReconciliationStatus = ReconciliationStatus.NOT_REQUIRED
    reconciliation_count: int = 0
    terminal_reason: ItemTerminalReason | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "logical_target_id": self.logical_target_id,
            "content_fingerprint": self.content_fingerprint,
            "state": self.state.value,
            "attempt_count": self.attempt_count,
            "failure_count": self.failure_count,
            "mutation_count": self.mutation_count,
            "last_error_code": self.last_error_code,
            "duplicate_of_item_id": self.duplicate_of_item_id,
            "reconciliation_status": self.reconciliation_status.value,
            "reconciliation_count": self.reconciliation_count,
            "terminal_reason": (
                self.terminal_reason.value if self.terminal_reason is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ItemLedgerRecord":
        try:
            item_id = str(payload["item_id"])
            logical_target_id = str(payload["logical_target_id"])
            content_fingerprint = str(payload["content_fingerprint"])
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
            reconciliation_status = ReconciliationStatus(
                str(payload["reconciliation_status"])
            )
            reconciliation_count = _strict_int(
                payload["reconciliation_count"], "item_ledger_record_invalid"
            )
            raw_terminal_reason = payload.get("terminal_reason")
            terminal_reason = (
                ItemTerminalReason(str(raw_terminal_reason))
                if raw_terminal_reason is not None
                else None
            )
            last_error = payload.get("last_error_code")
            duplicate_of = payload.get("duplicate_of_item_id")
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError("item_ledger_record_invalid") from exc
        if (
            not HEX64_RE.fullmatch(item_id)
            or not HEX64_RE.fullmatch(logical_target_id)
            or not HEX64_RE.fullmatch(content_fingerprint)
        ):
            raise LedgerError("item_ledger_record_invalid")
        if min(
            attempt_count, failure_count, mutation_count, reconciliation_count
        ) < 0:
            raise LedgerError("item_ledger_record_invalid")
        if last_error is not None and not isinstance(last_error, str):
            raise LedgerError("item_ledger_error_code_invalid")
        if duplicate_of is not None and (
            not isinstance(duplicate_of, str) or not HEX64_RE.fullmatch(duplicate_of)
        ):
            raise LedgerError("item_ledger_duplicate_reference_invalid")
        return cls(
            item_id=item_id,
            logical_target_id=logical_target_id,
            content_fingerprint=content_fingerprint,
            state=state,
            attempt_count=attempt_count,
            failure_count=failure_count,
            mutation_count=mutation_count,
            last_error_code=last_error,
            duplicate_of_item_id=duplicate_of,
            reconciliation_status=reconciliation_status,
            reconciliation_count=reconciliation_count,
            terminal_reason=terminal_reason,
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
    global_stop_reason: GlobalStopReason | None = None
    total_failure_attempts: int = 0
    duplicate_skip_count: int = 0
    recovery_count: int = 0
    generation: int = 0
    items: dict[str, ItemLedgerRecord] = field(default_factory=dict)
    operation_events: list[OperationEvent] = field(default_factory=list)

    @property
    def source_item_count(self) -> int:
        return len(self.items)

    @property
    def unique_item_count(self) -> int:
        return len({record.logical_target_id for record in self.items.values()})

    @property
    def duplicate_entry_count(self) -> int:
        return len(self.manifest_entry_ids) - self.unique_item_count

    @property
    def content_duplicate_item_count(self) -> int:
        return self.source_item_count - self.unique_item_count

    @property
    def repeated_manifest_entry_count(self) -> int:
        return len(self.manifest_entry_ids) - self.source_item_count

    @property
    def duplicate_second_mutation_count(self) -> int:
        mutations_by_logical_target: dict[str, int] = {}
        for record in self.items.values():
            mutations_by_logical_target[record.logical_target_id] = (
                mutations_by_logical_target.get(record.logical_target_id, 0)
                + record.mutation_count
            )
        return sum(
            max(0, mutation_count - 1)
            for mutation_count in mutations_by_logical_target.values()
        )

    @property
    def operation_counts(self) -> dict[str, int]:
        return {
            kind.value: sum(event.kind is kind for event in self.operation_events)
            for kind in FORBIDDEN_OPERATION_KINDS
        }

    @property
    def operation_evidence_fingerprint(self) -> str:
        return _canonical_digest(
            {
                "schema_version": OPERATION_EVIDENCE_SCHEMA_VERSION,
                "events": [event.to_dict() for event in self.operation_events],
            }
        )

    @property
    def private_execution_fingerprint(self) -> str:
        """Bind public proofs to the complete validated private ledger state."""

        return _canonical_digest(
            {
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
                    "global_stop_reason": (
                        self.global_stop_reason.value
                        if self.global_stop_reason is not None
                        else None
                    ),
                    "total_failure_attempts": self.total_failure_attempts,
                    "duplicate_skip_count": self.duplicate_skip_count,
                    "recovery_count": self.recovery_count,
                    "generation": self.generation,
                },
                "items": {
                    item_id: record.to_dict()
                    for item_id, record in sorted(self.items.items())
                },
                "operation_events": [
                    event.to_dict() for event in self.operation_events
                ],
            }
        )

    def _public_item_token(self, item_id: str) -> str:
        return hashlib.sha256(
            (
                f"{MUTATION_ATTRIBUTION_SCHEMA_VERSION}\0"
                f"{self.run_id}\0{item_id}"
            ).encode("utf-8")
        ).hexdigest()

    @property
    def public_operation_events(self) -> list[dict[str, Any]]:
        return [
            {
                "sequence": event.sequence,
                "kind": event.kind.value,
                "item_token": (
                    self._public_item_token(event.item_id)
                    if event.item_id is not None
                    else None
                ),
            }
            for event in self.operation_events
        ]

    @property
    def mutation_attribution_proof(self) -> dict[str, Any]:
        invocations_by_item = {item_id: 0 for item_id in self.items}
        for event in self.operation_events:
            if event.kind is OperationKind.SYNTHETIC_MUTATION_INVOCATION:
                assert event.item_id is not None
                invocations_by_item[event.item_id] += 1
        rows = [
            {
                "item_token": self._public_item_token(item_id),
                "attempt_count": self.items[item_id].attempt_count,
                "invocation_count": invocations_by_item[item_id],
            }
            for item_id in sorted(self.items)
        ]
        payload = {
            "schema_version": MUTATION_ATTRIBUTION_SCHEMA_VERSION,
            "private_execution_fingerprint": self.private_execution_fingerprint,
            "rows": rows,
        }
        return {
            **payload,
            "item_count": len(rows),
            "invocation_count": sum(
                row["invocation_count"] for row in rows
            ),
            "fingerprint": _canonical_digest(payload),
        }

    @property
    def reconciliation_required_count(self) -> int:
        return sum(
            record.reconciliation_status is ReconciliationStatus.REQUIRED
            for record in self.items.values()
        )

    @property
    def per_item_exhausted_count(self) -> int:
        return sum(
            record.terminal_reason
            is ItemTerminalReason.ITEM_ATTEMPT_BUDGET_EXHAUSTED
            for record in self.items.values()
        )

    def record_operation(
        self, kind: OperationKind, *, item_id: str | None = None
    ) -> None:
        event = OperationEvent(
            sequence=len(self.operation_events) + 1,
            kind=kind,
            item_id=item_id,
        )
        event.validate()
        self.operation_events.append(event)

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
                "global_stop_reason": (
                    self.global_stop_reason.value
                    if self.global_stop_reason is not None
                    else None
                ),
                "total_failure_attempts": self.total_failure_attempts,
                "duplicate_skip_count": self.duplicate_skip_count,
                "recovery_count": self.recovery_count,
                "generation": self.generation,
            },
            "denominator": {
                "manifest_entry_count": len(self.manifest_entry_ids),
                "source_item_count": self.source_item_count,
                "unique_item_count": self.unique_item_count,
                "duplicate_entry_count": self.duplicate_entry_count,
                "content_duplicate_item_count": self.content_duplicate_item_count,
                "repeated_manifest_entry_count": self.repeated_manifest_entry_count,
            },
            "items": {
                item_id: record.to_dict() for item_id, record in sorted(self.items.items())
            },
            "operation_evidence": {
                "schema_version": OPERATION_EVIDENCE_SCHEMA_VERSION,
                "events": [event.to_dict() for event in self.operation_events],
                "event_count": len(self.operation_events),
                "fingerprint": self.operation_evidence_fingerprint,
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
            raw_operation_evidence = payload["operation_evidence"]
            if (
                not isinstance(raw_entries, list)
                or not isinstance(raw_items, Mapping)
                or not isinstance(raw_operation_evidence, Mapping)
                or raw_operation_evidence.get("schema_version")
                != OPERATION_EVIDENCE_SCHEMA_VERSION
                or not isinstance(raw_operation_evidence.get("events"), list)
            ):
                raise LedgerError("run_ledger_invalid")
            entries = [str(value) for value in raw_entries]
            records = {
                str(item_id): ItemLedgerRecord.from_dict(record)
                for item_id, record in raw_items.items()
            }
            operation_events = [
                OperationEvent.from_dict(event)
                for event in raw_operation_evidence["events"]
            ]
            raw_global_stop_reason = checkpoint.get("global_stop_reason")
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
                global_stop_reason=(
                    GlobalStopReason(str(raw_global_stop_reason))
                    if raw_global_stop_reason is not None
                    else None
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
                operation_events=operation_events,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise LedgerError("run_ledger_invalid") from exc
        ledger.validate()
        operation_evidence = payload["operation_evidence"]
        if (
            operation_evidence.get("event_count") != len(ledger.operation_events)
            or operation_evidence.get("fingerprint")
            != ledger.operation_evidence_fingerprint
        ):
            raise LedgerError("operation_evidence_integrity_invalid")
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
        if self.failure_budget_exhausted != (
            self.total_failure_attempts >= self.max_failure_attempts
        ):
            raise LedgerError("failure_budget_state_invalid")
        expected_global_reason = (
            GlobalStopReason.FAILURE_BUDGET_EXHAUSTED
            if self.failure_budget_exhausted
            else None
        )
        if self.global_stop_reason is not expected_global_reason:
            raise LedgerError("global_stop_reason_invalid")
        if self.total_failure_attempts != sum(
            record.failure_count for record in self.items.values()
        ):
            raise LedgerError("failure_attempt_accounting_invalid")
        if not 0 <= self.duplicate_skip_count <= self.duplicate_entry_count:
            raise LedgerError("duplicate_skip_accounting_invalid")
        if [event.sequence for event in self.operation_events] != list(
            range(1, len(self.operation_events) + 1)
        ):
            raise LedgerError("operation_event_sequence_invalid")
        for event in self.operation_events:
            event.validate()
            if event.item_id is not None and event.item_id not in self.items:
                raise LedgerError("operation_event_item_missing")
        invocations_by_item = {item_id: 0 for item_id in self.items}
        for event in self.operation_events:
            if event.kind is OperationKind.SYNTHETIC_MUTATION_INVOCATION:
                if event.item_id is None:
                    raise LedgerError(
                        "synthetic_mutation_invocation_item_id_required"
                    )
                invocations_by_item[event.item_id] += 1
        if any(
            invocations_by_item[item_id] != record.attempt_count
            for item_id, record in self.items.items()
        ):
            raise LedgerError("mutation_invocation_item_attribution_mismatch")
        if self.duplicate_second_mutation_count:
            raise LedgerError("logical_target_multiple_mutations")

        terminal_states = {
            ItemState.SUCCEEDED,
            ItemState.FAILED_EXHAUSTED,
            ItemState.DUPLICATE,
        }
        for entry_id in self.manifest_entry_ids[: self.next_index]:
            if self.items[entry_id].state not in terminal_states:
                raise LedgerError("checkpoint_skips_nonterminal_item")

        in_progress_ids: list[str] = []
        for item_id, record in self.items.items():
            expected_logical_target_id = hashlib.sha256(
                (
                    f"{LOGICAL_TARGET_IDENTITY_VERSION}\0"
                    f"{record.content_fingerprint}"
                ).encode("utf-8")
            ).hexdigest()
            if record.logical_target_id != expected_logical_target_id:
                raise LedgerError("logical_target_fingerprint_mismatch")
            if record.failure_count > record.attempt_count or record.mutation_count > 1:
                raise LedgerError("item_attempt_accounting_invalid")
            if record.reconciliation_count > record.attempt_count:
                raise LedgerError("item_reconciliation_accounting_invalid")
            if record.state is ItemState.PENDING:
                valid = (
                    record.attempt_count == 0
                    and record.failure_count == 0
                    and record.mutation_count == 0
                    and record.last_error_code is None
                    and record.duplicate_of_item_id is None
                    and record.reconciliation_status
                    is ReconciliationStatus.NOT_REQUIRED
                    and record.reconciliation_count == 0
                    and record.terminal_reason is None
                )
            elif record.state is ItemState.IN_PROGRESS:
                in_progress_ids.append(item_id)
                valid = (
                    record.attempt_count >= 1
                    and record.mutation_count == 0
                    and record.duplicate_of_item_id is None
                    and record.reconciliation_status
                    is ReconciliationStatus.REQUIRED
                    and record.terminal_reason is None
                )
            elif record.state is ItemState.OUTCOME_UNKNOWN:
                in_progress_ids.append(item_id)
                valid = (
                    record.attempt_count >= 1
                    and record.mutation_count == 0
                    and record.duplicate_of_item_id is None
                    and record.reconciliation_status
                    is ReconciliationStatus.REQUIRED
                    and record.last_error_code is not None
                    and record.terminal_reason is None
                )
            elif record.state is ItemState.SUCCEEDED:
                valid = (
                    record.attempt_count >= 1
                    and record.mutation_count == 1
                    and record.last_error_code is None
                    and record.duplicate_of_item_id is None
                    and record.reconciliation_status
                    is ReconciliationStatus.COMMITTED
                    and record.terminal_reason is None
                )
            elif record.state is ItemState.FAILED_RETRYABLE:
                valid = (
                    record.attempt_count >= 1
                    and record.failure_count >= 1
                    and record.attempt_count < self.max_attempts_per_item
                    and record.mutation_count == 0
                    and record.last_error_code is not None
                    and record.duplicate_of_item_id is None
                    and record.reconciliation_status
                    is ReconciliationStatus.NOT_COMMITTED
                    and record.terminal_reason is None
                )
            elif record.state is ItemState.FAILED_EXHAUSTED:
                valid = (
                    record.attempt_count >= 1
                    and record.failure_count >= 1
                    and record.mutation_count == 0
                    and record.last_error_code is not None
                    and record.duplicate_of_item_id is None
                    and record.reconciliation_status
                    is ReconciliationStatus.NOT_COMMITTED
                    and record.terminal_reason
                    is ItemTerminalReason.ITEM_ATTEMPT_BUDGET_EXHAUSTED
                )
            elif record.state is ItemState.DUPLICATE:
                duplicate_of = record.duplicate_of_item_id
                primary = self.items.get(duplicate_of or "")
                valid = (
                    record.attempt_count == 0
                    and record.failure_count == 0
                    and record.mutation_count == 0
                    and record.last_error_code is None
                    and duplicate_of is not None
                    and duplicate_of != item_id
                    and primary is not None
                    and primary.logical_target_id == record.logical_target_id
                    and primary.state
                    in {ItemState.SUCCEEDED, ItemState.FAILED_EXHAUSTED}
                    and record.reconciliation_status
                    is ReconciliationStatus.NOT_REQUIRED
                    and record.reconciliation_count == 0
                    and record.terminal_reason
                    is ItemTerminalReason.DUPLICATE_LOGICAL_TARGET
                )
            else:  # pragma: no cover - exhaustive guard for future enum additions
                valid = False
            if not valid:
                raise LedgerError("item_state_accounting_invalid")

        if len(in_progress_ids) > 1:
            raise LedgerError("multiple_in_progress_items")
        if in_progress_ids:
            if self.next_index >= len(self.manifest_entry_ids):
                raise LedgerError("in_progress_item_after_completion")
            if self.manifest_entry_ids[self.next_index] != in_progress_ids[0]:
                raise LedgerError("in_progress_item_checkpoint_mismatch")


def manifest_fingerprint(items: Sequence[StableInventoryItem]) -> str:
    if not items:
        raise LedgerError("manifest_must_not_be_empty")
    for item in items:
        item.validate()
    payload = {
        "identity_version": ITEM_IDENTITY_VERSION,
        "logical_target_identity_version": LOGICAL_TARGET_IDENTITY_VERSION,
        "entry_ids": [item.item_id for item in items],
        "logical_target_ids": [item.logical_target_id for item in items],
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
        item.item_id: ItemLedgerRecord(
            item_id=item.item_id,
            logical_target_id=item.logical_target_id,
            content_fingerprint=item.content_fingerprint,
        )
        for item in items
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
        self.mutation_policy.assert_target_contained(self.path)

    def exists(self) -> bool:
        self.mutation_policy.assert_target_contained(self.path)
        return self.path.is_file()

    def load(self) -> RunLedger:
        self.mutation_policy.assert_target_contained(self.path)
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
        expected_generation = ledger.generation
        temporary_owned = False
        try:
            handle = temporary.open("x", encoding="utf-8", newline="\n")
            temporary_owned = True
            with handle:
                if self.path.exists():
                    current = self.load()
                    if current.generation != expected_generation:
                        raise LedgerError("ledger_generation_conflict")
                elif expected_generation != 0:
                    raise LedgerError("ledger_generation_conflict")
                ledger.generation = expected_generation + 1
                serialized = json.dumps(
                    ledger.to_dict(), ensure_ascii=True, indent=2, sort_keys=True
                ) + "\n"
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except LedgerError:
            ledger.generation = expected_generation
            if temporary_owned:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        except OSError as exc:
            ledger.generation = expected_generation
            if temporary_owned:
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
        manifest_fingerprint(self.items)
        self._items_by_id: dict[str, StableInventoryItem] = {}
        self._primary_by_logical_target: dict[str, StableInventoryItem] = {}
        for item in self.items:
            existing = self._items_by_id.setdefault(item.item_id, item)
            if existing != item:
                raise LedgerError("duplicate_item_id_payload_mismatch")
            self._primary_by_logical_target.setdefault(item.logical_target_id, item)

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
            if any(
                ledger.items[item.item_id].logical_target_id != item.logical_target_id
                or ledger.items[item.item_id].content_fingerprint
                != item.content_fingerprint
                for item in self.items
            ):
                raise LedgerError("manifest_item_identity_restart_mismatch")
            if (
                ledger.max_attempts_per_item != self.max_attempts_per_item
                or ledger.max_failure_attempts != self.max_failure_attempts
                or ledger.batch_size != self.batch_size
            ):
                raise LedgerError("ledger_limits_restart_mismatch")
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

    def _recover_interrupted(
        self,
        ledger: RunLedger,
        reconcile_interrupted: Callable[
            [StableInventoryItem], InterruptedMutationOutcome | str
        ]
        | None,
    ) -> None:
        interrupted = [
            record
            for record in ledger.items.values()
            if record.state in {ItemState.IN_PROGRESS, ItemState.OUTCOME_UNKNOWN}
        ]
        if not interrupted:
            return
        if reconcile_interrupted is None:
            for record in interrupted:
                record.state = ItemState.OUTCOME_UNKNOWN
                record.reconciliation_status = ReconciliationStatus.REQUIRED
                record.last_error_code = (
                    record.last_error_code
                    or "interrupted_mutation_outcome_unknown"
                )
            self.store.save(ledger)
            raise LedgerError("interrupted_mutation_reconciliation_required")

        for record in ledger.items.values():
            if record.state not in {ItemState.IN_PROGRESS, ItemState.OUTCOME_UNKNOWN}:
                continue
            item = self._items_by_id.get(record.item_id)
            if item is None:
                raise LedgerError("interrupted_item_missing_from_manifest")
            try:
                outcome = InterruptedMutationOutcome(reconcile_interrupted(item))
            except (TypeError, ValueError) as exc:
                record.state = ItemState.OUTCOME_UNKNOWN
                record.reconciliation_status = ReconciliationStatus.REQUIRED
                record.last_error_code = "reconciliation_result_invalid"
                self.store.save(ledger)
                raise LedgerError(
                    "interrupted_mutation_reconciliation_invalid"
                ) from exc
            except Exception as exc:
                record.state = ItemState.OUTCOME_UNKNOWN
                record.reconciliation_status = ReconciliationStatus.REQUIRED
                record.last_error_code = "reconciliation_callback_failed"
                self.store.save(ledger)
                raise LedgerError("interrupted_mutation_reconciliation_failed") from exc
            if outcome is InterruptedMutationOutcome.UNKNOWN:
                record.state = ItemState.OUTCOME_UNKNOWN
                record.reconciliation_status = ReconciliationStatus.REQUIRED
                record.last_error_code = "reconciliation_outcome_unknown"
                self.store.save(ledger)
                raise LedgerError("interrupted_mutation_outcome_unknown")
            if outcome is InterruptedMutationOutcome.COMMITTED:
                record.state = ItemState.SUCCEEDED
                record.mutation_count = 1
                record.last_error_code = None
                record.reconciliation_status = ReconciliationStatus.COMMITTED
                record.reconciliation_count += 1
                record.terminal_reason = None
                if (
                    ledger.next_index >= len(ledger.manifest_entry_ids)
                    or ledger.manifest_entry_ids[ledger.next_index] != record.item_id
                ):
                    raise LedgerError("interrupted_item_checkpoint_mismatch")
                ledger.next_index += 1
            else:
                record.failure_count += 1
                ledger.total_failure_attempts += 1
                record.last_error_code = "interrupted_before_terminal_checkpoint"
                record.reconciliation_status = ReconciliationStatus.NOT_COMMITTED
                record.reconciliation_count += 1
                if record.attempt_count >= ledger.max_attempts_per_item:
                    record.state = ItemState.FAILED_EXHAUSTED
                    record.terminal_reason = (
                        ItemTerminalReason.ITEM_ATTEMPT_BUDGET_EXHAUSTED
                    )
                else:
                    record.state = ItemState.FAILED_RETRYABLE
                    record.terminal_reason = None
            ledger.recovery_count += 1
        if ledger.total_failure_attempts >= ledger.max_failure_attempts:
            ledger.failure_budget_exhausted = True
            ledger.global_stop_reason = GlobalStopReason.FAILURE_BUDGET_EXHAUSTED
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
        reconcile_interrupted: Callable[
            [StableInventoryItem], InterruptedMutationOutcome | str
        ]
        | None = None,
    ) -> BatchResult:
        ledger = self._load_or_create()
        self._recover_interrupted(ledger, reconcile_interrupted)
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
            primary_item = self._primary_by_logical_target[item.logical_target_id]
            if item.item_id != primary_item.item_id:
                primary_record = ledger.items[primary_item.item_id]
                if primary_record.state not in {
                    ItemState.SUCCEEDED,
                    ItemState.FAILED_EXHAUSTED,
                }:
                    raise LedgerError("content_duplicate_primary_not_terminal")
                if record.state is ItemState.PENDING:
                    record.state = ItemState.DUPLICATE
                    record.duplicate_of_item_id = primary_item.item_id
                    record.terminal_reason = (
                        ItemTerminalReason.DUPLICATE_LOGICAL_TARGET
                    )
                elif record.state is not ItemState.DUPLICATE:
                    raise LedgerError("content_duplicate_state_invalid")
                ledger.duplicate_skip_count += 1
                duplicate_skipped += 1
                ledger.next_index += 1
                self.store.save(ledger)
                continue
            if record.state in {ItemState.SUCCEEDED, ItemState.DUPLICATE}:
                ledger.duplicate_skip_count += 1
                duplicate_skipped += 1
                ledger.next_index += 1
                self.store.save(ledger)
                continue
            if record.state is ItemState.FAILED_EXHAUSTED:
                if item.item_id in ledger.manifest_entry_ids[: ledger.next_index]:
                    ledger.duplicate_skip_count += 1
                    duplicate_skipped += 1
                ledger.next_index += 1
                self.store.save(ledger)
                continue
            if record.attempt_count >= ledger.max_attempts_per_item:
                record.state = ItemState.FAILED_EXHAUSTED
                record.last_error_code = (
                    record.last_error_code or "attempt_budget_exhausted"
                )
                record.reconciliation_status = ReconciliationStatus.NOT_COMMITTED
                record.terminal_reason = (
                    ItemTerminalReason.ITEM_ATTEMPT_BUDGET_EXHAUSTED
                )
                ledger.next_index += 1
                self.store.save(ledger)
                continue
            if ledger.total_failure_attempts >= ledger.max_failure_attempts:
                ledger.failure_budget_exhausted = True
                ledger.global_stop_reason = GlobalStopReason.FAILURE_BUDGET_EXHAUSTED
                self.store.save(ledger)
                break

            self.mutation_policy.assert_allowed(
                "synthetic.item.process", self.mutation_target
            )
            record.state = ItemState.IN_PROGRESS
            record.attempt_count += 1
            record.last_error_code = None
            record.reconciliation_status = ReconciliationStatus.REQUIRED
            record.terminal_reason = None
            ledger.record_operation(
                OperationKind.SYNTHETIC_MUTATION_INVOCATION,
                item_id=item.item_id,
            )
            self.store.save(ledger)
            attempted += 1

            try:
                mutate(item)
            except MutationNotCommittedError as exc:
                record.failure_count += 1
                ledger.total_failure_attempts += 1
                record.last_error_code = exc.error_code
                record.reconciliation_status = ReconciliationStatus.NOT_COMMITTED
                if record.attempt_count >= ledger.max_attempts_per_item:
                    record.state = ItemState.FAILED_EXHAUSTED
                    record.terminal_reason = (
                        ItemTerminalReason.ITEM_ATTEMPT_BUDGET_EXHAUSTED
                    )
                    ledger.next_index += 1
                else:
                    record.state = ItemState.FAILED_RETRYABLE
                    record.terminal_reason = None
                if ledger.total_failure_attempts >= ledger.max_failure_attempts:
                    ledger.failure_budget_exhausted = True
                    ledger.global_stop_reason = (
                        GlobalStopReason.FAILURE_BUDGET_EXHAUSTED
                    )
                self.store.save(ledger)
                break
            except Exception as exc:
                record.state = ItemState.OUTCOME_UNKNOWN
                record.last_error_code = type(exc).__name__
                record.reconciliation_status = ReconciliationStatus.REQUIRED
                record.terminal_reason = None
                self.store.save(ledger)
                raise LedgerError(
                    "mutation_outcome_reconciliation_required"
                ) from exc

            record.state = ItemState.SUCCEEDED
            record.mutation_count += 1
            record.last_error_code = None
            record.reconciliation_status = ReconciliationStatus.COMMITTED
            record.terminal_reason = None
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


@dataclass(frozen=True)
class SyntheticScenarioObservation:
    """Private before/after ledger snapshots for one restart-bound scenario."""

    before_restart: RunLedger
    after_restart: RunLedger


def _scenario_assertions(
    scenario: str, before: RunLedger, after: RunLedger
) -> dict[str, bool]:
    before.validate()
    after.validate()
    shared = {
        "same_run_identity": before.run_id == after.run_id,
        "same_manifest_identity": (
            before.manifest_fingerprint == after.manifest_fingerprint
        ),
    }
    if scenario == "normal_success":
        return {
            **shared,
            "completed": after.completed,
            "all_items_terminal_success_or_duplicate": all(
                record.state in {ItemState.SUCCEEDED, ItemState.DUPLICATE}
                for record in after.items.values()
            ),
            "manual_stop_not_requested": not after.manual_stop_requested,
            "global_budget_not_exhausted": not after.failure_budget_exhausted,
        }
    if scenario == "manual_stop_restart":
        return {
            **shared,
            "manual_stop_persisted": (
                before.manual_stop_requested and after.manual_stop_requested
            ),
            "restart_blocked_next_mutation": (
                before.operation_evidence_fingerprint
                == after.operation_evidence_fingerprint
                and before.next_index == after.next_index
            ),
            "pending_item_preserved": any(
                record.state is ItemState.PENDING
                for record in after.items.values()
            ),
        }
    if scenario == "per_item_exhaustion":
        exhausted_indexes = [
            index
            for index, item_id in enumerate(after.manifest_entry_ids)
            if after.items[item_id].terminal_reason
            is ItemTerminalReason.ITEM_ATTEMPT_BUDGET_EXHAUSTED
        ]
        later_succeeded = any(
            after.items[item_id].state is ItemState.SUCCEEDED
            for index, item_id in enumerate(after.manifest_entry_ids)
            if exhausted_indexes and index > min(exhausted_indexes)
        )
        return {
            **shared,
            "single_item_exhausted": bool(exhausted_indexes),
            "global_budget_not_poisoned": (
                not after.failure_budget_exhausted
                and after.global_stop_reason is None
            ),
            "later_item_processed": later_succeeded,
            "terminal_reason_persisted": all(
                before.items[item_id].terminal_reason
                == after.items[item_id].terminal_reason
                for item_id in before.items
            ),
        }
    if scenario == "global_budget_exhaustion":
        return {
            **shared,
            "global_budget_exhausted": after.failure_budget_exhausted,
            "global_reason_exact": (
                after.global_stop_reason
                is GlobalStopReason.FAILURE_BUDGET_EXHAUSTED
            ),
            "later_item_not_executed": any(
                record.state is ItemState.PENDING
                for record in after.items.values()
            ),
            "restart_blocked_next_mutation": (
                before.operation_evidence_fingerprint
                == after.operation_evidence_fingerprint
            ),
        }
    if scenario == "restart_counter_reason_consistency":
        return {
            **shared,
            "global_counters_persisted": (
                before.total_failure_attempts
                == after.total_failure_attempts
                and before.failure_budget_exhausted
                == after.failure_budget_exhausted
                and before.global_stop_reason == after.global_stop_reason
            ),
            "item_counters_and_reasons_persisted": all(
                (
                    before.items[item_id].attempt_count,
                    before.items[item_id].failure_count,
                    before.items[item_id].terminal_reason,
                )
                == (
                    after.items[item_id].attempt_count,
                    after.items[item_id].failure_count,
                    after.items[item_id].terminal_reason,
                )
                for item_id in before.items
            ),
            "operation_attribution_persisted": (
                before.operation_evidence_fingerprint
                == after.operation_evidence_fingerprint
            ),
        }
    raise LedgerError("failure_budget_scenario_name_invalid")


def build_failure_budget_scenario_matrix(
    observations: Mapping[str, SyntheticScenarioObservation],
) -> dict[str, Any]:
    """Derive the required scenario matrix from independent private ledgers."""

    if set(observations) != set(REQUIRED_FAILURE_BUDGET_SCENARIOS):
        raise LedgerError("failure_budget_scenario_membership_invalid")
    rows: list[dict[str, Any]] = []
    run_ids: list[str] = []
    for scenario in REQUIRED_FAILURE_BUDGET_SCENARIOS:
        observation = observations[scenario]
        if not isinstance(observation, SyntheticScenarioObservation):
            raise LedgerError("failure_budget_scenario_observation_invalid")
        before = observation.before_restart
        after = observation.after_restart
        assertions = _scenario_assertions(scenario, before, after)
        if not all(assertions.values()):
            raise LedgerError(f"failure_budget_scenario_failed:{scenario}")
        run_ids.append(after.run_id)
        row_payload = {
            "scenario": scenario,
            "run_id": after.run_id,
            "ledger_fingerprint": after.private_execution_fingerprint,
            "restart_evidence_fingerprint": _canonical_digest(
                {
                    "before": before.private_execution_fingerprint,
                    "after": after.private_execution_fingerprint,
                }
            ),
            "assertions": assertions,
        }
        rows.append(
            {
                **row_payload,
                "status": "completed",
                "evidence_digest": _canonical_digest(row_payload),
            }
        )
    if len(set(run_ids)) != len(run_ids):
        raise LedgerError("failure_budget_scenario_run_identity_reused")
    if len({row["ledger_fingerprint"] for row in rows}) != len(rows):
        raise LedgerError("failure_budget_scenario_ledger_fingerprint_reused")
    payload = {
        "schema_version": SCENARIO_MATRIX_SCHEMA_VERSION,
        "scenarios": rows,
    }
    return {
        **payload,
        "scenario_count": len(rows),
        "fingerprint": _canonical_digest(payload),
    }


def validate_failure_budget_scenario_matrix(
    matrix: Mapping[str, Any],
) -> None:
    """Validate the public matrix shape and every independently bound assertion."""

    rows = matrix.get("scenarios") if isinstance(matrix, Mapping) else None
    if not (
        matrix.get("schema_version") == SCENARIO_MATRIX_SCHEMA_VERSION
        and isinstance(rows, list)
        and [row.get("scenario") for row in rows]
        == list(REQUIRED_FAILURE_BUDGET_SCENARIOS)
        and matrix.get("scenario_count") == len(REQUIRED_FAILURE_BUDGET_SCENARIOS)
    ):
        raise LedgerError("failure_budget_scenario_matrix_invalid")
    run_ids: list[str] = []
    ledger_fingerprints: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise LedgerError("failure_budget_scenario_matrix_invalid")
        assertions = row.get("assertions")
        row_payload = {
            "scenario": row.get("scenario"),
            "run_id": row.get("run_id"),
            "ledger_fingerprint": row.get("ledger_fingerprint"),
            "restart_evidence_fingerprint": row.get(
                "restart_evidence_fingerprint"
            ),
            "assertions": assertions,
        }
        if not (
            row.get("status") == "completed"
            and isinstance(row.get("run_id"), str)
            and SAFE_IDENTITY_RE.fullmatch(str(row.get("run_id")))
            and isinstance(row.get("ledger_fingerprint"), str)
            and HEX64_RE.fullmatch(str(row.get("ledger_fingerprint")))
            and isinstance(row.get("restart_evidence_fingerprint"), str)
            and HEX64_RE.fullmatch(str(row.get("restart_evidence_fingerprint")))
            and isinstance(assertions, Mapping)
            and bool(assertions)
            and all(value is True for value in assertions.values())
            and row.get("evidence_digest") == _canonical_digest(row_payload)
        ):
            raise LedgerError("failure_budget_scenario_matrix_invalid")
        run_ids.append(str(row["run_id"]))
        ledger_fingerprints.append(str(row["ledger_fingerprint"]))
    payload = {
        "schema_version": SCENARIO_MATRIX_SCHEMA_VERSION,
        "scenarios": rows,
    }
    if (
        len(set(run_ids)) != len(run_ids)
        or len(set(ledger_fingerprints)) != len(ledger_fingerprints)
        or matrix.get("fingerprint") != _canonical_digest(payload)
    ):
        raise LedgerError("failure_budget_scenario_matrix_invalid")


def build_contract_summary(
    *,
    isolation: IsolationProof,
    ledger: RunLedger,
    implementation_evidence: ImplementationEvidence,
    failure_budget_scenario_matrix: Mapping[str, Any],
    focused_tests_passed: bool,
    full_non_e2e_passed: bool,
    owner_acceptance: OwnerAcceptanceEvidence | None = None,
    merge_authorization: BoundAuthorizationEvidence | None = None,
    next_phase_route_authorization: BoundAuthorizationEvidence | None = None,
) -> dict[str, Any]:
    """Build a public-safe P1 audit summary from actual foundation evidence."""

    ledger.validate()
    implementation_evidence.validate()
    validate_failure_budget_scenario_matrix(failure_budget_scenario_matrix)
    scenario_rows = failure_budget_scenario_matrix["scenarios"]
    failure_scenario_matrix_completed = (
        failure_budget_scenario_matrix["scenario_count"]
        == len(REQUIRED_FAILURE_BUDGET_SCENARIOS)
        and all(
            row["status"] == "completed"
            and all(assertion is True for assertion in row["assertions"].values())
            for row in scenario_rows
        )
    )
    if not isinstance(focused_tests_passed, bool) or not isinstance(
        full_non_e2e_passed, bool
    ):
        raise LedgerError("validation_result_must_be_boolean")

    owner_acceptance_payload: dict[str, Any] | None = None
    if owner_acceptance is not None:
        owner_acceptance_payload = owner_acceptance.to_public_dict(
            implementation_evidence
        )
    if merge_authorization is not None:
        if owner_acceptance is None:
            raise LedgerError("merge_authorization_requires_owner_acceptance")
        if merge_authorization.kind is not AuthorizationKind.MERGE:
            raise LedgerError("merge_authorization_kind_invalid")
        merge_authorization_payload = merge_authorization.to_public_dict(
            implementation_evidence, owner_acceptance
        )
    else:
        merge_authorization_payload = None
    if next_phase_route_authorization is not None:
        if owner_acceptance is None or merge_authorization is None:
            raise LedgerError("route_authorization_requires_acceptance_and_merge")
        if (
            next_phase_route_authorization.kind
            is not AuthorizationKind.NEXT_PHASE_ROUTE
        ):
            raise LedgerError("route_authorization_kind_invalid")
        route_authorization_payload = next_phase_route_authorization.to_public_dict(
            implementation_evidence, owner_acceptance
        )
    else:
        route_authorization_payload = None

    owner_accepted = owner_acceptance_payload is not None
    merge_authorized = merge_authorization_payload is not None
    route_authorized = route_authorization_payload is not None
    if not owner_accepted:
        status = "implementation_ready_for_owner_audit"
        blockers = ["pending_owner_audit"]
    elif not merge_authorized:
        status = "owner_accepted_pending_merge_authorization"
        blockers = ["pending_merge_authorization"]
    else:
        status = "owner_accepted_for_merge"
        blockers = []

    operation_evidence = {
        "schema_version": OPERATION_EVIDENCE_SCHEMA_VERSION,
        "events": ledger.public_operation_events,
        "event_count": len(ledger.operation_events),
        "fingerprint": _canonical_digest(
            {
                "schema_version": OPERATION_EVIDENCE_SCHEMA_VERSION,
                "events": ledger.public_operation_events,
            }
        ),
        "private_execution_fingerprint": ledger.private_execution_fingerprint,
        "mutation_attribution": ledger.mutation_attribution_proof,
        "ledger_generation": ledger.generation,
        "run_id": ledger.run_id,
    }
    operation_counts = ledger.operation_counts
    isolation_payload = isolation.to_public_dict()
    stage_inputs: dict[str, tuple[bool, Mapping[str, Any]]] = {
        "environment_isolation_preflight": (
            all(
                isolation_payload.get(key) is True
                for key in (
                    "git_head_match",
                    "python_identity_match",
                    "database_identity_explicit",
                    "database_path_new_and_contained",
                    "source_root_explicit_and_contained",
                    "storage_root_explicit_and_contained",
                    "source_storage_non_overlapping",
                    "database_source_storage_pairwise_disjoint",
                )
            )
            and isolation.environment in {"test", "development"}
            and isolation.forbidden_root_overlap_count == 0
            and isolation.production_fallback_used is False
            and isolation.existing_database_accessed is False,
            isolation_payload,
        ),
        "mutation_default_deny": (
            all(count == 0 for count in operation_counts.values()),
            {
                "forbidden_operation_counts": operation_counts,
                "synthetic_invocation_count": sum(
                    event.kind is OperationKind.SYNTHETIC_MUTATION_INVOCATION
                    for event in ledger.operation_events
                ),
            },
        ),
        "stable_inventory_identity": (
            ledger.duplicate_second_mutation_count == 0,
            {
                "manifest_fingerprint": ledger.manifest_fingerprint,
                "source_item_count": ledger.source_item_count,
                "unique_item_count": ledger.unique_item_count,
                "duplicate_second_mutation_count": (
                    ledger.duplicate_second_mutation_count
                ),
            },
        ),
        "restartable_item_ledger": (
            ledger.generation >= 1,
            {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "generation": ledger.generation,
                "next_index": ledger.next_index,
            },
        ),
        "interrupted_mutation_reconciliation": (
            ledger.reconciliation_required_count == 0,
            {
                "reconciliation_required_count": (
                    ledger.reconciliation_required_count
                ),
                "recovery_count": ledger.recovery_count,
            },
        ),
        "failure_budget_and_manual_stop": (
            failure_scenario_matrix_completed,
            dict(failure_budget_scenario_matrix),
        ),
        "forbidden_operation_evidence": (
            all(count == 0 for count in operation_counts.values()),
            operation_evidence,
        ),
    }
    stage_evidence = []
    for stage_name in REQUIRED_EXECUTED_STAGES:
        completed, payload = stage_inputs[stage_name]
        stage_evidence.append(
            {
                "stage": stage_name,
                "status": "completed" if completed else "failed",
                "evidence": dict(payload),
                "evidence_digest": _canonical_digest(
                    {"stage": stage_name, "evidence": payload}
                ),
            }
        )
    executed_stages = [
        row["stage"] for row in stage_evidence if row["status"] == "completed"
    ]
    missing_required_stages = [
        stage for stage in REQUIRED_EXECUTED_STAGES if stage not in executed_stages
    ]

    return {
        "phase": "SCV2-FL1-P1",
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "target_met": owner_accepted,
            "safe_to_merge": merge_authorized,
            "route_approved": route_authorized,
            "active_blockers": blockers,
            "owner_acceptance_evidence": owner_acceptance_payload,
            "merge_authorization_evidence": merge_authorization_payload,
            "next_phase_route_authorization_evidence": (
                route_authorization_payload
            ),
        },
        "implementation_evidence": implementation_evidence.to_public_dict(),
        "executed_stages": executed_stages,
        "missing_required_stages": missing_required_stages,
        "stage_evidence": stage_evidence,
        "authorization": {
            "p1_r1_implementation_authorized": True,
            "owner_audit_completed": owner_accepted,
            "owner_acceptance_valid": owner_accepted,
            "merge_authorized": merge_authorized,
            "next_phase_route_authorized": route_authorized,
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
            **isolation_payload,
            "passed": (
                "environment_isolation_preflight" in executed_stages
            ),
            "unknown_identity_rejected": True,
            "production_identity_rejected": True,
            "synthetic_only": True,
        },
        "mutation_policy": {
            "default_deny": True,
            "allowlist_explicit": True,
            "ledger_read_contained": True,
            "production_mutation_allowed": False,
            "source_mutation_allowed": False,
            "unexpected_mutation_allowed": False,
        },
        "ledger": {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "stable_item_identity": ITEM_IDENTITY_VERSION,
            "logical_target_identity": LOGICAL_TARGET_IDENTITY_VERSION,
            "manifest_entry_count": len(ledger.manifest_entry_ids),
            "source_item_count": ledger.source_item_count,
            "unique_item_count": ledger.unique_item_count,
            "duplicate_entry_count": ledger.duplicate_entry_count,
            "content_duplicate_item_count": ledger.content_duplicate_item_count,
            "repeated_manifest_entry_count": ledger.repeated_manifest_entry_count,
            "restart_recovery_count": ledger.recovery_count,
            "duplicate_second_mutation_count": (
                ledger.duplicate_second_mutation_count
            ),
            "operation_evidence_fingerprint": (
                operation_evidence["fingerprint"]
            ),
            "private_execution_fingerprint": ledger.private_execution_fingerprint,
            "mutation_attribution_fingerprint": (
                ledger.mutation_attribution_proof["fingerprint"]
            ),
            "operation_event_count": len(ledger.operation_events),
            "reconciliation_required_count": (
                ledger.reconciliation_required_count
            ),
            "per_item_exhausted_count": ledger.per_item_exhausted_count,
            "global_failure_attempt_count": ledger.total_failure_attempts,
            "global_stop_reason": (
                ledger.global_stop_reason.value
                if ledger.global_stop_reason is not None
                else None
            ),
            "attempt_budget_persisted": True,
            "checkpoint_persisted": True,
            "manual_stop_persisted": True,
            "interrupted_mutation_reconciliation_required": True,
            "generation_conflict_rejected": True,
        },
        "validation": {
            "focused_tests_passed": focused_tests_passed,
            "full_non_e2e_passed": full_non_e2e_passed,
            "browser_validation": "not_applicable_no_ui_or_runtime_server_change",
        },
        "operation_evidence": operation_evidence,
        "operation_counts": operation_counts,
        "public_redaction": {"passed": True, "private_paths_emitted": False},
    }
