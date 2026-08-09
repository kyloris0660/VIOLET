"""Trusted runtime and protected-root context for SCV2-FL1-I1.

The module never discovers or touches a real source root by default.  Current
callers can build only temporary-fixture scopes.  A future real-source caller
must provide a private registry and a separately implemented owner-authority
gateway; a boolean or caller-supplied "actual" identity is intentionally not
accepted here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from scripts.fl1_p1_foundation import (
    LedgerError,
    assert_evidence_worktree_clean,
    derive_python_identity,
)


RUNTIME_CONTEXT_SCHEMA_VERSION = "violet.scv2-fl1-i1-runtime-context.v1"
PROTECTED_ROOT_REGISTRY_SCHEMA_VERSION = (
    "violet.scv2-fl1-i1-protected-root-registry.v1"
)
PRIVATE_ROOT_CONFIG_SCHEMA_VERSION = "violet.scv2-fl1-i1-private-roots.v1"
SOURCE_SCOPE_SCHEMA_VERSION = "violet.scv2-fl1-i1-source-scope.v1"
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

REQUIRED_PROTECTED_ROOT_ROLES: tuple[str, ...] = (
    "production_source_root",
    "production_icloud_root",
    "production_app_storage_root",
    "accepted_evidence_storage_root",
    "repository_worktree_root",
    "phase_evidence_output_root",
    "synthetic_test_sandbox_root",
)


class RuntimeContextError(RuntimeError):
    """Raised when actual runtime or private root identity is not trustworthy."""


class SourceMode(str, Enum):
    SYNTHETIC_FIXTURE = "synthetic_fixture"
    AUTHORIZED_READ_ONLY_SOURCE = "authorized_read_only_source"


class RegistryTrustClass(str, Enum):
    TEMPORARY_FIXTURE_ONLY = "temporary_fixture_only"
    PRIVATE_RUNTIME_PENDING_OWNER = "private_runtime_pending_owner"


def _canonical(path: Path, error_code: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise RuntimeContextError(error_code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeContextError(error_code) from exc
    if not resolved.is_dir():
        raise RuntimeContextError(error_code)
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _overlaps(left: Path, right: Path) -> bool:
    return _is_within(left, right) or _is_within(right, left)


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RuntimeContextError("protected_root_lstat_failed") from exc
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _reject_component_escape(path: Path) -> None:
    """Reject symlink/reparse components instead of silently trusting resolve()."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if _is_reparse_or_symlink(current):
            raise RuntimeContextError("protected_root_symlink_or_reparse_rejected")


def _hmac_hex(key: bytes, purpose: str, value: str) -> str:
    return hmac.new(
        key,
        f"{purpose}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class ProtectedRootRegistry:
    roots: Mapping[str, Path]
    private_derivation_key: bytes
    trust_class: RegistryTrustClass

    @classmethod
    def from_private_payload(cls, payload: Mapping[str, Any]) -> "ProtectedRootRegistry":
        if payload.get("schema_version") != PRIVATE_ROOT_CONFIG_SCHEMA_VERSION:
            raise RuntimeContextError("private_root_config_schema_invalid")
        try:
            trust_class = RegistryTrustClass(payload.get("trust_class"))
        except (TypeError, ValueError) as exc:
            raise RuntimeContextError("private_root_config_trust_class_invalid") from exc
        roots_payload = payload.get("roots")
        if not isinstance(roots_payload, Mapping):
            raise RuntimeContextError("protected_root_registry_invalid")
        keys = set(roots_payload)
        expected = set(REQUIRED_PROTECTED_ROOT_ROLES)
        if keys != expected:
            if expected - keys:
                raise RuntimeContextError("protected_root_required_role_missing")
            raise RuntimeContextError("protected_root_unknown_role")
        raw_key = payload.get("private_derivation_key")
        if not isinstance(raw_key, str) or not HEX64_RE.fullmatch(raw_key):
            raise RuntimeContextError("private_derivation_key_invalid")
        roots: dict[str, Path] = {}
        for role in REQUIRED_PROTECTED_ROOT_ROLES:
            raw = roots_payload[role]
            if not isinstance(raw, str):
                raise RuntimeContextError("protected_root_value_invalid")
            candidate = Path(raw)
            if not candidate.is_absolute():
                raise RuntimeContextError("protected_root_value_invalid")
            _reject_component_escape(candidate)
            roots[role] = _canonical(candidate, "protected_root_value_invalid")
        registry = cls(
            roots=roots,
            private_derivation_key=bytes.fromhex(raw_key),
            trust_class=trust_class,
        )
        registry.validate()
        return registry

    @classmethod
    def load_private_config(cls, path: Path) -> "ProtectedRootRegistry":
        config_path = Path(path)
        if not config_path.is_absolute() or config_path.is_symlink():
            raise RuntimeContextError("private_root_config_path_invalid")
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeContextError("private_root_config_unreadable") from exc
        if not isinstance(payload, Mapping):
            raise RuntimeContextError("private_root_config_invalid")
        return cls.from_private_payload(payload)

    def validate(self) -> None:
        if set(self.roots) != set(REQUIRED_PROTECTED_ROOT_ROLES):
            raise RuntimeContextError("protected_root_registry_incomplete")
        values = list(self.roots.values())
        if len(set(values)) != len(values):
            raise RuntimeContextError("protected_root_duplicate_or_alias")
        for index, left in enumerate(values):
            for right in values[index + 1 :]:
                if _overlaps(left, right):
                    raise RuntimeContextError("protected_root_overlap")
        if len(self.private_derivation_key) != 32:
            raise RuntimeContextError("private_derivation_key_invalid")

    @property
    def fingerprint(self) -> str:
        self.validate()
        parts = [
            f"{role}:{_hmac_hex(self.private_derivation_key, role, os.fspath(self.roots[role]))}"
            for role in REQUIRED_PROTECTED_ROOT_ROLES
        ]
        return hashlib.sha256("\n".join(parts).encode("ascii")).hexdigest()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROTECTED_ROOT_REGISTRY_SCHEMA_VERSION,
            "required_roles": list(REQUIRED_PROTECTED_ROOT_ROLES),
            "resolved_role_count": len(self.roots),
            "complete": True,
            "duplicate_or_alias_count": 0,
            "overlap_count": 0,
            "registry_fingerprint": self.fingerprint,
            "path_values_emitted": False,
            "trust_class": self.trust_class.value,
        }


@dataclass(frozen=True)
class TrustedSourceScope:
    mode: SourceMode
    root: Path
    scope_id: str
    scope_fingerprint: str
    authorization_class: str
    real_source: bool

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SOURCE_SCOPE_SCHEMA_VERSION,
            "mode": self.mode.value,
            "scope_id": self.scope_id,
            "scope_fingerprint": self.scope_fingerprint,
            "authorization_class": self.authorization_class,
            "real_source": self.real_source,
            "path_emitted": False,
        }


@dataclass(frozen=True)
class TrustedRuntimeContext:
    repo_root: Path
    actual_git_head: str
    branch: str
    python_identity: Mapping[str, Any]
    roots: ProtectedRootRegistry
    source_scope: TrustedSourceScope
    worktree_clean: bool

    @property
    def context_fingerprint(self) -> str:
        payload = {
            "schema_version": RUNTIME_CONTEXT_SCHEMA_VERSION,
            "actual_git_head": self.actual_git_head,
            "branch": self.branch,
            "python_identity_fingerprint": self.python_identity[
                "identity_fingerprint"
            ],
            "root_registry_fingerprint": self.roots.fingerprint,
            "source_scope_fingerprint": self.source_scope.scope_fingerprint,
            "worktree_clean": self.worktree_clean,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_CONTEXT_SCHEMA_VERSION,
            "actual_git_head": self.actual_git_head,
            "branch": self.branch,
            "repository_identity_source": "git_rev_parse_trusted_worktree",
            "repository_path_emitted": False,
            "python_identity": dict(self.python_identity),
            "protected_roots": self.roots.to_public_dict(),
            "source_scope": self.source_scope.to_public_dict(),
            "worktree_clean": self.worktree_clean,
            "context_fingerprint": self.context_fingerprint,
        }


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeContextError("trusted_git_identity_failed")
    try:
        return completed.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeContextError("trusted_git_identity_unreadable") from exc


def _derive_source_scope(
    *,
    registry: ProtectedRootRegistry,
    source_root: Path,
    mode: SourceMode | str,
    scope_id: str,
) -> TrustedSourceScope:
    if not SAFE_ID_RE.fullmatch(scope_id):
        raise RuntimeContextError("source_scope_id_invalid")
    try:
        normalized_mode = mode if isinstance(mode, SourceMode) else SourceMode(mode)
    except (TypeError, ValueError) as exc:
        raise RuntimeContextError("source_mode_invalid") from exc
    raw_root = Path(source_root)
    if not raw_root.is_absolute():
        raise RuntimeContextError("source_scope_root_invalid")
    _reject_component_escape(raw_root)
    root = _canonical(raw_root, "source_scope_root_invalid")
    sandbox = registry.roots["synthetic_test_sandbox_root"]
    if not _is_within(root, sandbox) or root == sandbox:
        raise RuntimeContextError("source_scope_not_temporary_fixture")
    for role, protected in registry.roots.items():
        if role == "synthetic_test_sandbox_root":
            continue
        if _overlaps(root, protected):
            raise RuntimeContextError("source_scope_overlaps_protected_root")
    relative = root.relative_to(sandbox).as_posix()
    fingerprint = _hmac_hex(
        registry.private_derivation_key,
        f"{SOURCE_SCOPE_SCHEMA_VERSION}:{normalized_mode.value}:{scope_id}",
        relative,
    )
    return TrustedSourceScope(
        mode=normalized_mode,
        root=root,
        scope_id=scope_id,
        scope_fingerprint=fingerprint,
        authorization_class="temporary_fixture_only",
        real_source=False,
    )


def build_trusted_runtime_context(
    *,
    repo_root: Path,
    expected_python: Path,
    private_root_config: Path,
    source_root: Path,
    source_mode: SourceMode | str,
    source_scope_id: str,
) -> TrustedRuntimeContext:
    """Build actual identity from the process/repository and a private registry."""

    requested_root = _canonical(Path(repo_root), "trusted_repo_root_invalid")
    actual_root = _canonical(
        Path(_git(requested_root, "rev-parse", "--show-toplevel")),
        "trusted_repo_root_invalid",
    )
    if actual_root != requested_root:
        raise RuntimeContextError("trusted_repo_root_mismatch")
    actual_head = _git(actual_root, "rev-parse", "HEAD^{commit}")
    if not HEX40_RE.fullmatch(actual_head):
        raise RuntimeContextError("trusted_git_head_invalid")
    branch = _git(actual_root, "branch", "--show-current")
    if not branch:
        raise RuntimeContextError("trusted_git_branch_detached")
    try:
        assert_evidence_worktree_clean(actual_root)
    except LedgerError as exc:
        raise RuntimeContextError(str(exc)) from exc
    python_identity = derive_python_identity(Path(expected_python))
    if python_identity.get("match") is not True:
        raise RuntimeContextError("python_identity_mismatch")
    registry = ProtectedRootRegistry.load_private_config(Path(private_root_config))
    if registry.roots["repository_worktree_root"] != actual_root:
        raise RuntimeContextError("protected_repository_root_mismatch")
    if registry.trust_class is not RegistryTrustClass.TEMPORARY_FIXTURE_ONLY:
        raise RuntimeContextError("real_source_owner_authority_not_available")
    source_scope = _derive_source_scope(
        registry=registry,
        source_root=source_root,
        mode=source_mode,
        scope_id=source_scope_id,
    )
    return TrustedRuntimeContext(
        repo_root=actual_root,
        actual_git_head=actual_head,
        branch=branch,
        python_identity=python_identity,
        roots=registry,
        source_scope=source_scope,
        worktree_clean=True,
    )


def private_config_payload_for_temporary_roots(
    roots: Mapping[str, Path], *, private_derivation_key: bytes
) -> dict[str, Any]:
    """Test helper that emits a private payload, never a public artifact."""

    if len(private_derivation_key) != 32:
        raise RuntimeContextError("private_derivation_key_invalid")
    return {
        "schema_version": PRIVATE_ROOT_CONFIG_SCHEMA_VERSION,
        "trust_class": RegistryTrustClass.TEMPORARY_FIXTURE_ONLY.value,
        "private_derivation_key": private_derivation_key.hex(),
        "roots": {role: os.fspath(roots[role]) for role in roots},
    }


def actual_python_executable() -> Path:
    """Expose the only accepted actual Python source for callers and tests."""

    return Path(sys.executable)
