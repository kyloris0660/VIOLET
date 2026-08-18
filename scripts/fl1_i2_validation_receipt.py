"""Same-HEAD local validation receipt for SCV2-FL1-I2."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.fl1_i2_evidence import EvidenceStore
from scripts.fl1_i2_confinement import (
    ConfinementError,
    create_owned_validation_temp_root,
    remove_owned_validation_temp_root,
)
from scripts.trusted_git import (
    assert_trusted_worktree_clean,
    resolve_trusted_git_executable,
    run_trusted_git_text,
    verify_approved_python_runtime,
)


RECEIPT_SCHEMA = "violet.scv2-fl1-i2-local-validation-receipt.v4"
CANONICAL_FOCUSED_TESTS = (
    "tests/test_trusted_git.py",
    "tests/test_scv2_fl1_i2_source_policy.py",
    "tests/test_scv2_fl1_i2_source_backends.py",
    "tests/test_scv2_fl1_i2_windows_feasibility.py",
    "tests/test_scv2_fl1_i2_worker.py",
    "tests/test_scv2_fl1_i2_evidence.py",
    "tests/test_scv2_fl1_i2_media_cli.py",
    "tests/test_scv2_fl1_i2_runner.py",
    "tests/test_scv2_fl1_i2_validation_receipt.py",
    "tests/test_scv2_fl1_i2_contract.py",
)
SYSTEM_ENVIRONMENT_ALLOWLIST = (
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "USERPROFILE",
    "HOME",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "LANG",
    "LC_ALL",
)
FORCED_VALIDATION_ENVIRONMENT = {
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}
CONTROL_ENVIRONMENT_PREFIXES = ("pytest_", "coverage", "cov_core")
CONTROL_ENVIRONMENT_NAMES = {
    "pythonpath",
    "pythonhome",
    "pythonstartup",
    "pythonbreakpoint",
    "pythoninspect",
    "pythonwarnings",
    "pythonpycacheprefix",
    "pythonplatlibdir",
    "pythonuserbase",
    "sitecustomize",
}
NON_CONTROL_PYTEST_ENVIRONMENT = {"pytest_current_test", "pytest_version"}


class ReceiptError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitSnapshot:
    head: str
    tree: str


@dataclass(frozen=True)
class SameHeadValidationReceipt:
    run_id: str
    git_head: str
    git_tree: str
    trusted_git_fingerprint: str
    python_executable_fingerprint: str
    approved_python_runtime_fingerprint: str
    execution_environment_policy_fingerprint: str
    execution_environment_fingerprint: str
    validation_temp_root_identity_fingerprint: str
    config_fingerprint: str
    policy_fingerprint: str
    manifest_fingerprint: str
    ledger_fingerprint: str
    worker_fingerprint: str
    command_argv: tuple[str, ...]
    command_fingerprint: str
    exit_code: int
    stdout_fingerprint: str
    stderr_fingerprint: str
    started_at_ns: int
    ended_at_ns: int
    same_head_tree: bool
    clean_before_after: bool
    positive: bool
    trust_level: str = "local_operator_receipt"
    machine_verifiable_ci: bool = False
    owner_authority_machine_verifiable: bool = False

    def to_private_dict(self) -> dict[str, Any]:
        return {"schema_version": RECEIPT_SCHEMA, **self.__dict__}

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RECEIPT_SCHEMA,
            "git_head": self.git_head,
            "git_tree": self.git_tree,
            "trusted_git_fingerprint": self.trusted_git_fingerprint,
            "python_executable_fingerprint": self.python_executable_fingerprint,
            "approved_python_runtime_fingerprint": self.approved_python_runtime_fingerprint,
            "execution_environment_policy_fingerprint": self.execution_environment_policy_fingerprint,
            "execution_environment_fingerprint": self.execution_environment_fingerprint,
            "validation_temp_root_identity_fingerprint": self.validation_temp_root_identity_fingerprint,
            "command_fingerprint": self.command_fingerprint,
            "exit_code": self.exit_code,
            "same_head_tree": self.same_head_tree,
            "clean_before_after": self.clean_before_after,
            "positive": self.positive,
            "trust_level": self.trust_level,
            "machine_verifiable_ci": False,
            "owner_authority_machine_verifiable": False,
            "private_bindings_redacted": True,
        }

    @classmethod
    def from_private_dict(cls, payload: Mapping[str, Any]) -> "SameHeadValidationReceipt":
        expected = {field.name for field in fields(cls)} | {"schema_version"}
        if set(payload) != expected or payload.get("schema_version") != RECEIPT_SCHEMA:
            raise ReceiptError("validation_receipt_schema_invalid")
        try:
            command = payload["command_argv"]
            if not isinstance(command, list) or not all(isinstance(value, str) and value for value in command):
                raise TypeError
            values = dict(payload)
            values.pop("schema_version")
            values["command_argv"] = tuple(command)
            receipt = cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise ReceiptError("validation_receipt_invalid") from exc
        if receipt.command_fingerprint != _fingerprint(list(receipt.command_argv)):
            raise ReceiptError("validation_receipt_command_fingerprint_mismatch")
        if receipt.execution_environment_policy_fingerprint != execution_environment_policy_fingerprint():
            raise ReceiptError("validation_receipt_environment_policy_mismatch")
        validate_canonical_focused_command(receipt.command_argv, Path(receipt.command_argv[0]))
        if receipt.positive != (receipt.exit_code == 0 and receipt.same_head_tree and receipt.clean_before_after):
            raise ReceiptError("validation_receipt_positive_invalid")
        if receipt.trust_level != "local_operator_receipt" or receipt.machine_verifiable_ci or receipt.owner_authority_machine_verifiable:
            raise ReceiptError("validation_receipt_authority_invalid")
        for value in (
            receipt.trusted_git_fingerprint,
            receipt.python_executable_fingerprint,
            receipt.approved_python_runtime_fingerprint,
            receipt.execution_environment_policy_fingerprint,
            receipt.execution_environment_fingerprint,
            receipt.validation_temp_root_identity_fingerprint,
            receipt.config_fingerprint,
            receipt.policy_fingerprint,
            receipt.manifest_fingerprint,
            receipt.ledger_fingerprint,
            receipt.worker_fingerprint,
            receipt.command_fingerprint,
            receipt.stdout_fingerprint,
            receipt.stderr_fingerprint,
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ReceiptError("validation_receipt_fingerprint_invalid")
        return receipt


def execution_environment_policy_payload() -> dict[str, Any]:
    return {
        "schema_version": "violet.scv2-fl1-i2-validation-environment-policy.v1",
        "system_allowlist": list(SYSTEM_ENVIRONMENT_ALLOWLIST),
        "forced": dict(sorted(FORCED_VALIDATION_ENVIRONMENT.items())),
        "control_prefixes": list(CONTROL_ENVIRONMENT_PREFIXES),
        "control_names": sorted(CONTROL_ENVIRONMENT_NAMES),
        "plugin_autoload": False,
        "path_policy": "trusted_git_parent_plus_os_system_directory_only",
        "python_isolated_argv": ["-B", "-I", "-s", "-m", "pytest"],
        "temporary_directory_policy": "verified_os_local_exclusive_task_root",
    }


def execution_environment_policy_fingerprint() -> str:
    return _fingerprint(execution_environment_policy_payload())


def canonical_validation_environment(
    caller: Mapping[str, str] | None = None,
    *,
    trusted_git_executable: Path | None = None,
    validation_temp_root: Path | None = None,
) -> dict[str, str]:
    source = dict(os.environ if caller is None else caller)
    casefolded = {key.casefold(): (key, value) for key, value in source.items()}
    forced_casefolded = {key.casefold(): value for key, value in FORCED_VALIDATION_ENVIRONMENT.items()}
    hostile: list[str] = []
    for folded, (_key, value) in casefolded.items():
        if folded in NON_CONTROL_PYTEST_ENVIRONMENT:
            continue
        if folded in forced_casefolded and value == forced_casefolded[folded]:
            continue
        if folded in CONTROL_ENVIRONMENT_NAMES or any(folded.startswith(prefix) for prefix in CONTROL_ENVIRONMENT_PREFIXES):
            hostile.append(folded)
        elif folded.startswith("python") and folded not in forced_casefolded:
            hostile.append(folded)
    if hostile:
        raise ReceiptError("validation_receipt_hostile_environment")
    environment: dict[str, str] = {}
    for canonical in SYSTEM_ENVIRONMENT_ALLOWLIST:
        found = casefolded.get(canonical.casefold())
        if found is not None:
            environment[canonical] = found[1]
    if trusted_git_executable is not None:
        try:
            trusted_parent = trusted_git_executable.resolve(strict=True).parent
        except OSError as exc:
            raise ReceiptError("validation_receipt_trusted_git_invalid") from exc
        path_roots = [os.fspath(trusted_parent)]
        system_root = environment.get("SystemRoot") or environment.get("WINDIR")
        if os.name == "nt" and system_root:
            path_roots.append(os.fspath(Path(system_root) / "System32"))
        environment["PATH"] = os.pathsep.join(path_roots)
    if validation_temp_root is not None:
        exact_temp = os.fspath(validation_temp_root)
        environment.update({"TEMP": exact_temp, "TMP": exact_temp, "TMPDIR": exact_temp})
    environment.update(FORCED_VALIDATION_ENVIRONMENT)
    return environment


def _fingerprint(payload: Any) -> str:
    if isinstance(payload, bytes):
        encoded = payload
    else:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        common = Path(os.path.commonpath((left, right)))
    except ValueError:
        return False
    return common in {left, right}


def canonical_focused_test_command(python_executable: Path) -> tuple[str, ...]:
    try:
        approved = python_executable.resolve(strict=True)
    except OSError as exc:
        raise ReceiptError("validation_receipt_python_invalid") from exc
    if not approved.is_file():
        raise ReceiptError("validation_receipt_python_invalid")
    return (
        os.fspath(approved),
        "-B",
        "-I",
        "-s",
        "-m",
        "pytest",
        "-q",
        *CANONICAL_FOCUSED_TESTS,
    )


def validate_canonical_focused_command(
    command: Sequence[str],
    approved_python: Path,
) -> None:
    expected = canonical_focused_test_command(approved_python)
    if tuple(command) != expected:
        raise ReceiptError("validation_receipt_command_not_canonical")


def _python_fingerprint(python_executable: Path) -> str:
    try:
        return hashlib.sha256(python_executable.resolve(strict=True).read_bytes()).hexdigest()
    except OSError as exc:
        raise ReceiptError("validation_receipt_python_invalid") from exc


def trusted_git_snapshot(repo_root: Path) -> tuple[GitSnapshot, str]:
    git = resolve_trusted_git_executable(repo_root=repo_root)
    head = run_trusted_git_text(repo_root, ("rev-parse", "HEAD^{commit}"), git=git)
    tree = run_trusted_git_text(repo_root, ("rev-parse", "HEAD^{tree}"), git=git)
    if head.returncode or tree.returncode:
        raise ReceiptError("validation_receipt_git_snapshot_failed")
    return GitSnapshot(head.stdout.strip(), tree.stdout.strip()), git.fingerprint


def _run_validation_command(
    command: Sequence[str],
    *,
    repo_root: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        tuple(command),
        cwd=repo_root,
        env=environment,
        capture_output=True,
        check=False,
        timeout=900,
    )


def create_same_head_receipt(
    *,
    repo_root: Path,
    evidence_root: Path,
    output_name: str,
    run_id: str,
    bindings: Mapping[str, str],
    command: Sequence[str],
) -> SameHeadValidationReceipt:
    required = {"config", "policy", "manifest", "ledger", "worker"}
    if set(bindings) != required or any(len(value) != 64 for value in bindings.values()):
        raise ReceiptError("validation_receipt_bindings_invalid")
    approved_python = Path(sys.executable).resolve(strict=True)
    runtime = verify_approved_python_runtime(
        approved_python,
        repo_root=repo_root,
    )
    validate_canonical_focused_command(command, approved_python)
    before, git_fingerprint = trusted_git_snapshot(repo_root)
    git = resolve_trusted_git_executable(repo_root=repo_root)
    try:
        assert_trusted_worktree_clean(
            git,
            repo_root,
            approved_python_runtime=runtime,
        )
        clean_before = True
    except Exception:
        clean_before = False
    try:
        validation_temp = create_owned_validation_temp_root()
    except ConfinementError as exc:
        raise ReceiptError("validation_receipt_temp_root_unavailable") from exc
    if any(
        _paths_overlap(validation_temp.path, boundary)
        for boundary in (
            Path(repo_root).resolve(strict=True),
            Path(evidence_root).resolve(strict=True),
        )
    ):
        raise ReceiptError("validation_receipt_temp_root_overlap")
    started = time.time_ns()
    environment = canonical_validation_environment(
        trusted_git_executable=git.path,
        validation_temp_root=validation_temp.path,
    )
    try:
        completed = _run_validation_command(
            command,
            repo_root=repo_root,
            environment=environment,
        )
    finally:
        try:
            remove_owned_validation_temp_root(validation_temp)
        except ConfinementError as exc:
            raise ReceiptError("validation_receipt_temp_cleanup_failed") from exc
    ended = time.time_ns()
    after, after_git_fingerprint = trusted_git_snapshot(repo_root)
    try:
        assert_trusted_worktree_clean(
            git,
            repo_root,
            approved_python_runtime=runtime,
        )
        clean_after = True
    except Exception:
        clean_after = False
    same = before == after and git_fingerprint == after_git_fingerprint
    positive = completed.returncode == 0 and same and clean_before and clean_after
    receipt = SameHeadValidationReceipt(
        run_id=run_id,
        git_head=before.head,
        git_tree=before.tree,
        trusted_git_fingerprint=git_fingerprint,
        python_executable_fingerprint=_python_fingerprint(approved_python),
        approved_python_runtime_fingerprint=runtime.execution_manifest_fingerprint,
        execution_environment_policy_fingerprint=execution_environment_policy_fingerprint(),
        execution_environment_fingerprint=_fingerprint(dict(sorted(environment.items()))),
        validation_temp_root_identity_fingerprint=validation_temp.identity_fingerprint,
        config_fingerprint=bindings["config"],
        policy_fingerprint=bindings["policy"],
        manifest_fingerprint=bindings["manifest"],
        ledger_fingerprint=bindings["ledger"],
        worker_fingerprint=bindings["worker"],
        command_argv=tuple(command),
        command_fingerprint=_fingerprint(list(command)),
        exit_code=completed.returncode,
        stdout_fingerprint=_fingerprint(completed.stdout),
        stderr_fingerprint=_fingerprint(completed.stderr),
        started_at_ns=started,
        ended_at_ns=ended,
        same_head_tree=same,
        clean_before_after=clean_before and clean_after,
        positive=positive,
    )
    EvidenceStore(evidence_root).write(output_name, receipt.to_private_dict())
    if not positive:
        raise ReceiptError("validation_receipt_positive_not_issued")
    return receipt
