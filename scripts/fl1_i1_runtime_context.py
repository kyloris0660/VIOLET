"""Trusted runtime and protected-root context for SCV2-FL1-I1.

Trust bootstrap is deliberately ordered.  The Git executable and the current
process' repository-venv identity are established before a candidate worktree
becomes a subprocess cwd.  A pending-owner root payload is rejected from its
lexical JSON values without observing any configured root.
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
from pathlib import Path, PurePath
from typing import Any, Iterable, Mapping, Sequence


RUNTIME_CONTEXT_SCHEMA_VERSION = "violet.scv2-fl1-i1-runtime-context.v2"
PROTECTED_ROOT_REGISTRY_SCHEMA_VERSION = "violet.scv2-fl1-i1-protected-root-registry.v2"
PRIVATE_ROOT_CONFIG_SCHEMA_VERSION = "violet.scv2-fl1-i1-private-roots.v1"
SOURCE_SCOPE_SCHEMA_VERSION = "violet.scv2-fl1-i1-source-scope.v2"
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


def _normcase_lexical(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _lexically_within(path: Path, root: Path) -> bool:
    candidate = _normcase_lexical(path)
    boundary = _normcase_lexical(root)
    try:
        return os.path.commonpath((candidate, boundary)) == boundary
    except ValueError:
        return False


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _overlaps(left: Path, right: Path) -> bool:
    return _is_within(left, right) or _is_within(right, left)


def _is_reparse_or_symlink(path: Path, *, code: str) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RuntimeContextError(code) from exc
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _reject_component_escape(path: Path, *, code: str) -> None:
    if not path.is_absolute():
        raise RuntimeContextError(code)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if _is_reparse_or_symlink(current, code=code):
            raise RuntimeContextError(code)


def _canonical_directory(path: Path, code: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise RuntimeContextError(code)
    _reject_component_escape(candidate, code=code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeContextError(code) from exc
    if not resolved.is_dir() or resolved != candidate:
        raise RuntimeContextError(code)
    return resolved


def _hmac_hex(key: bytes, purpose: str, value: str) -> str:
    return hmac.new(key, f"{purpose}\0{value}".encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class TrustedGitExecutable:
    path: Path
    fingerprint: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "identity_source": "absolute_path_search_before_candidate_cwd",
            "executable_fingerprint": self.fingerprint,
            "absolute_path_emitted": False,
            "repo_local": False,
            "symlink_or_reparse": False,
        }


def _candidate_git_names() -> tuple[str, ...]:
    return ("git.exe",) if os.name == "nt" else ("git",)


def _trusted_git_install_roots() -> tuple[Path, ...]:
    if os.name == "nt":
        roots: list[Path] = []
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            value = os.environ.get(variable)
            if value and Path(value).is_absolute():
                roots.append(Path(value) / "Git")
        return tuple(roots)
    return tuple(
        Path(value)
        for value in ("/usr/bin", "/usr/local/bin", "/opt/homebrew/bin", "/opt/local/bin")
    )


def resolve_trusted_git_executable(
    *, excluded_roots: Sequence[Path] = (), path_value: str | None = None
) -> TrustedGitExecutable:
    """Resolve Git without consulting cwd or a repo-local executable.

    Empty/relative PATH entries are ignored.  Exclusion is lexical and occurs
    before any candidate lstat, so a forbidden repo/source root is not probed.
    """

    raw_path = os.environ.get("PATH", "") if path_value is None else path_value
    excluded = tuple(Path(value) for value in excluded_roots)
    trusted_install_roots = _trusted_git_install_roots()
    for raw_entry in raw_path.split(os.pathsep):
        if not raw_entry or raw_entry == ".":
            continue
        directory = Path(raw_entry)
        if not directory.is_absolute():
            continue
        if any(_lexically_within(directory, root) for root in excluded):
            continue
        for name in _candidate_git_names():
            candidate = Path(os.path.abspath(os.fspath(directory / name)))
            if not any(_lexically_within(candidate, root) for root in trusted_install_roots):
                continue
            if any(_lexically_within(candidate, root) for root in excluded):
                continue
            try:
                metadata = os.lstat(candidate)
            except OSError:
                continue
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                continue
            if not stat.S_ISREG(metadata.st_mode) or not candidate.is_absolute():
                continue
            try:
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except OSError:
                continue
            return TrustedGitExecutable(path=candidate, fingerprint=digest)
    raise RuntimeContextError("trusted_git_executable_unavailable")


def run_trusted_git(
    git: TrustedGitExecutable,
    repo_root: Path,
    *arguments: str,
) -> str:
    completed = subprocess.run(
        [os.fspath(git.path), *arguments],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeContextError("trusted_git_identity_failed")
    try:
        return completed.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeContextError("trusted_git_identity_unreadable") from exc


def assert_trusted_worktree_clean(git: TrustedGitExecutable, repo_root: Path) -> None:
    status = run_trusted_git(
        git,
        repo_root,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
    )
    if status:
        raise RuntimeContextError("evidence_worktree_tracked_drift")


def _normalize_executable(path: Path) -> str:
    # Do not resolve POSIX venv shims into a shared base interpreter.
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def derive_repository_python_identity(
    *,
    caller_expected_python: Path | None = None,
    policy_repo_root: Path | None = None,
) -> Mapping[str, Any]:
    """Derive expected identity from the active venv, never from caller input."""

    actual = Path(sys.executable)
    prefix = Path(sys.prefix)
    base_prefix = Path(getattr(sys, "base_prefix", sys.prefix))
    if prefix == base_prefix or prefix.name.casefold() not in {"venv", ".venv"}:
        raise RuntimeContextError("repository_venv_python_required")
    if policy_repo_root is not None:
        candidates = {
            _normalize_executable(Path(policy_repo_root) / "venv"),
            _normalize_executable(Path(policy_repo_root) / ".venv"),
        }
        if _normalize_executable(prefix) not in candidates:
            raise RuntimeContextError("repository_venv_policy_mismatch")
    if not _lexically_within(actual, prefix):
        raise RuntimeContextError("python_prefix_executable_mismatch")
    # Windows venv executables must not traverse a reparse point.  On POSIX the
    # final venv/bin/python shim is normally a symlink and is intentionally
    # compared lexically, while its parent chain remains no-follow.
    parent = actual.parent
    _reject_component_escape(parent, code="python_venv_parent_untrusted")
    try:
        metadata = os.lstat(actual)
    except OSError as exc:
        raise RuntimeContextError("python_executable_unreadable") from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    if os.name == "nt" and (
        stat.S_ISLNK(metadata.st_mode)
        or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise RuntimeContextError("python_executable_symlink_or_reparse")
    if caller_expected_python is not None and _normalize_executable(caller_expected_python) != _normalize_executable(actual):
        raise RuntimeContextError("python_identity_mismatch")
    fingerprint = hashlib.sha256(
        f"{_normalize_executable(actual)}\0{_normalize_executable(prefix)}".encode("utf-8")
    ).hexdigest()
    return {
        "actual_source": "current_process_sys_executable",
        "expected_source": "current_process_repository_venv_policy",
        "match": True,
        "is_venv": True,
        "sys_prefix_consistent": True,
        "identity_fingerprint": fingerprint,
        "absolute_path_emitted": False,
    }


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
        # This check intentionally precedes roots extraction, Path creation,
        # resolve/lstat/stat, and all attribute observation.
        if trust_class is not RegistryTrustClass.TEMPORARY_FIXTURE_ONLY:
            raise RuntimeContextError("real_source_owner_authority_not_available")
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
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise RuntimeContextError("protected_root_value_invalid")
            roots[role] = _canonical_directory(Path(raw), "protected_root_symlink_or_reparse_rejected")
        registry = cls(roots, bytes.fromhex(raw_key), trust_class)
        registry.validate()
        return registry

    @classmethod
    def load_private_config(cls, path: Path) -> "ProtectedRootRegistry":
        config_path = Path(path)
        if not config_path.is_absolute():
            raise RuntimeContextError("private_root_config_path_invalid")
        try:
            metadata = os.lstat(config_path)
            if stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise RuntimeContextError("private_root_config_path_invalid")
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except RuntimeContextError:
            raise
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
    git_executable: TrustedGitExecutable
    roots: ProtectedRootRegistry
    source_scope: TrustedSourceScope
    worktree_clean: bool

    @property
    def context_fingerprint(self) -> str:
        payload = {
            "schema_version": RUNTIME_CONTEXT_SCHEMA_VERSION,
            "actual_git_head": self.actual_git_head,
            "branch": self.branch,
            "python_identity_fingerprint": self.python_identity["identity_fingerprint"],
            "git_executable_fingerprint": self.git_executable.fingerprint,
            "root_registry_fingerprint": self.roots.fingerprint,
            "source_scope_fingerprint": self.source_scope.scope_fingerprint,
            "worktree_clean": self.worktree_clean,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_CONTEXT_SCHEMA_VERSION,
            "actual_git_head": self.actual_git_head,
            "branch": self.branch,
            "repository_identity_source": "trusted_absolute_git_rev_parse",
            "repository_path_emitted": False,
            "git_identity": self.git_executable.to_public_dict(),
            "python_identity": dict(self.python_identity),
            "protected_roots": self.roots.to_public_dict(),
            "source_scope": self.source_scope.to_public_dict(),
            "worktree_clean": self.worktree_clean,
            "context_fingerprint": self.context_fingerprint,
        }


def _derive_source_scope(
    *, registry: ProtectedRootRegistry, source_root: Path, mode: SourceMode | str, scope_id: str
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
    sandbox = registry.roots["synthetic_test_sandbox_root"]
    # Pure lexical containment is mandatory before the source root is touched.
    if not _lexically_within(raw_root, sandbox) or _normcase_lexical(raw_root) == _normcase_lexical(sandbox):
        raise RuntimeContextError("source_scope_not_temporary_fixture")
    root = _canonical_directory(raw_root, "source_scope_symlink_or_reparse_rejected")
    if not _is_within(root, sandbox) or root == sandbox:
        raise RuntimeContextError("source_scope_not_temporary_fixture")
    for role, protected in registry.roots.items():
        if role != "synthetic_test_sandbox_root" and _overlaps(root, protected):
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
    private_root_config: Path,
    source_root: Path,
    source_mode: SourceMode | str,
    source_scope_id: str,
    expected_python: Path | None = None,
) -> TrustedRuntimeContext:
    requested_lexical = Path(os.path.abspath(os.fspath(repo_root)))
    if not Path(repo_root).is_absolute():
        raise RuntimeContextError("trusted_repo_root_invalid")
    config_path = Path(private_root_config)
    if not config_path.is_absolute():
        raise RuntimeContextError("private_root_config_path_invalid")
    try:
        config_metadata = os.lstat(config_path)
        if stat.S_ISLNK(config_metadata.st_mode) or (
            getattr(config_metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise RuntimeContextError("private_root_config_path_invalid")
        lexical_payload = json.loads(config_path.read_text(encoding="utf-8"))
    except RuntimeContextError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeContextError("private_root_config_unreadable") from exc
    if not isinstance(lexical_payload, Mapping):
        raise RuntimeContextError("private_root_config_invalid")
    try:
        lexical_trust = RegistryTrustClass(lexical_payload.get("trust_class"))
    except (TypeError, ValueError) as exc:
        raise RuntimeContextError("private_root_config_trust_class_invalid") from exc
    if lexical_trust is not RegistryTrustClass.TEMPORARY_FIXTURE_ONLY:
        raise RuntimeContextError("real_source_owner_authority_not_available")
    lexical_roots_payload = lexical_payload.get("roots")
    lexical_exclusions: list[Path] = [requested_lexical, Path(source_root)]
    if isinstance(lexical_roots_payload, Mapping):
        lexical_exclusions.extend(
            Path(value)
            for value in lexical_roots_payload.values()
            if isinstance(value, str) and Path(value).is_absolute()
        )
    git = resolve_trusted_git_executable(excluded_roots=tuple(lexical_exclusions))
    _reject_component_escape(requested_lexical, code="trusted_repo_root_invalid")
    actual_root_text = run_trusted_git(git, requested_lexical, "rev-parse", "--show-toplevel")
    requested_root = _canonical_directory(requested_lexical, "trusted_repo_root_invalid")
    actual_root = _canonical_directory(Path(actual_root_text), "trusted_repo_root_invalid")
    if actual_root != requested_root:
        raise RuntimeContextError("trusted_repo_root_mismatch")
    actual_head = run_trusted_git(git, actual_root, "rev-parse", "HEAD^{commit}")
    if not HEX40_RE.fullmatch(actual_head):
        raise RuntimeContextError("trusted_git_head_invalid")
    branch = run_trusted_git(git, actual_root, "branch", "--show-current")
    if not branch:
        raise RuntimeContextError("trusted_git_branch_detached")
    assert_trusted_worktree_clean(git, actual_root)
    code_root = Path(__file__).resolve().parents[1]
    common_git = Path(run_trusted_git(git, code_root, "rev-parse", "--git-common-dir"))
    if not common_git.is_absolute():
        common_git = code_root / common_git
    common_git = common_git.resolve(strict=True)
    if common_git.name != ".git":
        raise RuntimeContextError("repository_venv_policy_root_invalid")
    python_identity = derive_repository_python_identity(
        caller_expected_python=expected_python,
        policy_repo_root=common_git.parent,
    )
    registry = ProtectedRootRegistry.load_private_config(Path(private_root_config))
    if registry.roots["repository_worktree_root"] != actual_root:
        raise RuntimeContextError("protected_repository_root_mismatch")
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
        git_executable=git,
        roots=registry,
        source_scope=source_scope,
        worktree_clean=True,
    )


def private_config_payload_for_temporary_roots(
    roots: Mapping[str, Path], *, private_derivation_key: bytes
) -> dict[str, Any]:
    if len(private_derivation_key) != 32:
        raise RuntimeContextError("private_derivation_key_invalid")
    return {
        "schema_version": PRIVATE_ROOT_CONFIG_SCHEMA_VERSION,
        "trust_class": RegistryTrustClass.TEMPORARY_FIXTURE_ONLY.value,
        "private_derivation_key": private_derivation_key.hex(),
        "roots": {role: os.fspath(roots[role]) for role in roots},
    }


def actual_python_executable() -> Path:
    return Path(sys.executable)
