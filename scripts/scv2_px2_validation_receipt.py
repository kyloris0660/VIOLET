"""Same-HEAD local validation receipt for the synthetic SCV2-PX2 route."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

from scripts.fl1_i2_confinement import (
    ConfinementError,
    create_owned_validation_temp_root,
    remove_owned_validation_temp_root,
)
from scripts.fl1_i2_validation_receipt import canonical_validation_environment
from scripts.scv2_px1_validation_receipt import repository_identity_snapshot
from scripts.trusted_git import (
    decode_git_z_paths,
    resolve_trusted_git_executable,
    run_trusted_git_bytes,
    run_trusted_git_text,
)


RECEIPT_SCHEMA = "violet.scv2-px2-local-validation-receipt.v1"
RECEIPT_NAME = "local-validation-receipt.json"
PX2_CONTRACT_ID = "scv2_px2_deterministic_pixiv_clustering_contract_v1"
EVIDENCE_ARTIFACT_NAMES = (
    "px1-consumer-summary.json",
    "candidate-dispositions.json",
    "ambiguous-ledger.json",
    "persistence-proof.json",
    "operation-receipt.json",
    "public-summary.json",
)
CANONICAL_FOCUSED_TESTS = (
    "tests/test_phase_contracts.py",
    "tests/test_scv2_px1_pixiv_metadata_consolidation.py",
    "tests/test_phase45_sc1_source_concept_resolver.py",
    "tests/test_scv2_px2_pixiv_metadata_clustering.py",
    "tests/test_scv2_px2_validation_receipt.py",
    "tests/test_scv2_px2_contract.py",
    "tests/test_phase45_doc1_documentation_state.py",
    "tests/test_pd1a_mainline_governance.py",
)
PX2_DOCS_ONLY_CARRY_FORWARD_ALLOWLIST = frozenset(
    {
        "docs/current-handoff.md",
        "docs/phase-contracts.md",
        "docs/project-roadmap.md",
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/state/current-phase.json",
    }
)


class Px2ValidationReceiptError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def execution_environment_policy_fingerprint() -> str:
    return canonical_fingerprint(
        {
            "schema_version": "violet.scv2-px2-validation-environment-policy.v1",
            "dotenv_skipped": True,
            "violet_environment": "test",
            "database_policy": "validation_temp_root/px2-validation.sqlite3",
            "storage_policy": "validation_temp_root/runtime-storage",
            "provider_network": False,
            "existing_database_or_app_storage": False,
            "llm_or_external_model": False,
            "server_browser_e2e": False,
            "production": False,
        }
    )


def canonical_px2_validation_environment(
    base_environment: Mapping[str, str],
    *,
    validation_temp_root: Path,
) -> dict[str, str]:
    environment = dict(base_environment)
    runtime_storage = validation_temp_root / "runtime-storage"
    validation_database = validation_temp_root / "px2-validation.sqlite3"
    environment.update(
        {
            "VIOLET_SKIP_DOTENV": "1",
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "scv2_px2_task_temp",
            "TEST_DATABASE_URL": f"sqlite:///{validation_database.as_posix()}",
            "VIOLET_STORAGE_ROOT": os.fspath(runtime_storage),
            "VIOLET_TEST_STORAGE_ROOT": os.fspath(runtime_storage),
        }
    )
    return environment


def canonical_focused_test_command(python_executable: Path) -> tuple[str, ...]:
    try:
        approved = python_executable.resolve(strict=True)
    except OSError as exc:
        raise Px2ValidationReceiptError("px2_receipt_python_invalid") from exc
    if not approved.is_file():
        raise Px2ValidationReceiptError("px2_receipt_python_invalid")
    return (
        os.fspath(approved),
        "-B",
        "-I",
        "-s",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        *CANONICAL_FOCUSED_TESTS,
    )


def validate_canonical_focused_command(
    command: Sequence[str], approved_python: Path
) -> None:
    if tuple(command) != canonical_focused_test_command(approved_python):
        raise Px2ValidationReceiptError("px2_receipt_command_not_canonical")


def evidence_bindings(payloads: Mapping[str, Any]) -> dict[str, str]:
    if set(payloads) != set(EVIDENCE_ARTIFACT_NAMES):
        raise Px2ValidationReceiptError("px2_receipt_evidence_set_invalid")
    return {
        name: canonical_fingerprint(payloads[name])
        for name in sorted(EVIDENCE_ARTIFACT_NAMES)
    }


def _git_value(repo_root: Path, *arguments: str) -> str:
    git = resolve_trusted_git_executable(repo_root=repo_root)
    result = run_trusted_git_text(repo_root, arguments, git=git)
    if result.returncode:
        raise Px2ValidationReceiptError("px2_receipt_git_snapshot_failed")
    return result.stdout.strip()


def _git_z_paths(repo_root: Path, *arguments: str) -> tuple[str, ...]:
    git = resolve_trusted_git_executable(repo_root=repo_root)
    result = run_trusted_git_bytes(repo_root, arguments, git=git)
    if result.returncode:
        raise Px2ValidationReceiptError("px2_evidence_carry_forward_git_failed")
    try:
        return decode_git_z_paths(result.stdout)
    except Exception as exc:
        raise Px2ValidationReceiptError(
            "px2_evidence_carry_forward_git_output_invalid"
        ) from exc


def validate_px2_evidence_carry_forward(
    repo_root: Path,
    *,
    evidence_head: str,
    evidence_tree: str,
    current_ref: str = "HEAD",
    include_worktree: bool = True,
) -> dict[str, Any]:
    """Allow only the five governance projections after tested PX2 code."""

    root = repo_root.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{40}", str(evidence_head)) or not re.fullmatch(
        r"[0-9a-f]{40}", str(evidence_tree)
    ):
        raise Px2ValidationReceiptError("px2_evidence_identity_invalid")
    if _git_value(root, "rev-parse", f"{evidence_head}^{{tree}}") != evidence_tree:
        raise Px2ValidationReceiptError("px2_evidence_tree_mismatch")
    current_head = _git_value(root, "rev-parse", f"{current_ref}^{{commit}}")
    current_tree = _git_value(root, "rev-parse", f"{current_ref}^{{tree}}")
    git = resolve_trusted_git_executable(repo_root=root)
    ancestor = run_trusted_git_text(
        root,
        ("merge-base", "--is-ancestor", evidence_head, current_head),
        git=git,
    )
    if ancestor.returncode:
        raise Px2ValidationReceiptError("px2_evidence_not_ancestor")
    changed = set(
        _git_z_paths(
            root,
            "diff",
            "--name-only",
            "-z",
            f"{evidence_head}..{current_head}",
            "--",
        )
    )
    if include_worktree:
        if current_ref != "HEAD":
            raise Px2ValidationReceiptError("px2_evidence_worktree_requires_head_ref")
        changed.update(_git_z_paths(root, "diff", "--name-only", "-z"))
        changed.update(_git_z_paths(root, "diff", "--cached", "--name-only", "-z"))
        changed.update(
            _git_z_paths(root, "ls-files", "--others", "--exclude-standard", "-z")
        )
    disallowed = sorted(changed - PX2_DOCS_ONLY_CARRY_FORWARD_ALLOWLIST)
    if disallowed:
        raise Px2ValidationReceiptError(
            "px2_evidence_behavioral_carry_forward_invalid:" + ",".join(disallowed)
        )
    return {
        "evidence_head": evidence_head,
        "evidence_tree": evidence_tree,
        "current_head": current_head,
        "current_tree": current_tree,
        "changed_paths": sorted(changed),
        "docs_only": True,
    }


def build_receipt_payload(
    *,
    repository_before: Mapping[str, str | bool],
    repository_after: Mapping[str, str | bool],
    approved_python: Path,
    command: Sequence[str],
    command_result: subprocess.CompletedProcess[bytes],
    validation_environment_fingerprint: str,
    validation_temp_root_identity_fingerprint: str,
    bindings: Mapping[str, str],
) -> dict[str, Any]:
    validate_canonical_focused_command(command, approved_python)
    same_head_tree = all(
        repository_before.get(key) == repository_after.get(key)
        for key in ("git_head", "git_tree", "trusted_git_fingerprint")
    )
    clean_before_after = (
        repository_before.get("worktree_clean") is True
        and repository_after.get("worktree_clean") is True
    )
    positive = command_result.returncode == 0 and same_head_tree and clean_before_after
    return {
        "schema_version": RECEIPT_SCHEMA,
        "contract_id": PX2_CONTRACT_ID,
        "git_head": str(repository_before["git_head"]),
        "git_tree": str(repository_before["git_tree"]),
        "trusted_git_fingerprint": str(repository_before["trusted_git_fingerprint"]),
        "python_executable_fingerprint": str(
            repository_before["python_executable_fingerprint"]
        ),
        "approved_python_runtime_fingerprint": str(
            repository_before["approved_python_runtime_fingerprint"]
        ),
        "execution_environment_policy_fingerprint": (
            execution_environment_policy_fingerprint()
        ),
        "execution_environment_fingerprint": validation_environment_fingerprint,
        "validation_temp_root_identity_fingerprint": (
            validation_temp_root_identity_fingerprint
        ),
        "evidence_bindings": dict(sorted(bindings.items())),
        "command_argv": list(command),
        "command_fingerprint": canonical_fingerprint(list(command)),
        "exit_code": command_result.returncode,
        "stdout_fingerprint": _sha256_bytes(command_result.stdout),
        "stderr_fingerprint": _sha256_bytes(command_result.stderr),
        "same_head_tree": same_head_tree,
        "clean_before_after": clean_before_after,
        "positive": positive,
        "trust_level": "local_operator_receipt",
        "machine_verifiable_ci": False,
        "owner_authority_machine_verifiable": False,
    }


def validate_receipt_payload(
    payload: Mapping[str, Any],
    *,
    approved_python: Path,
    expected_repository: Mapping[str, str | bool],
    expected_bindings: Mapping[str, str],
) -> None:
    expected_keys = {
        "schema_version",
        "contract_id",
        "git_head",
        "git_tree",
        "trusted_git_fingerprint",
        "python_executable_fingerprint",
        "approved_python_runtime_fingerprint",
        "execution_environment_policy_fingerprint",
        "execution_environment_fingerprint",
        "validation_temp_root_identity_fingerprint",
        "evidence_bindings",
        "command_argv",
        "command_fingerprint",
        "exit_code",
        "stdout_fingerprint",
        "stderr_fingerprint",
        "same_head_tree",
        "clean_before_after",
        "positive",
        "trust_level",
        "machine_verifiable_ci",
        "owner_authority_machine_verifiable",
    }
    if set(payload) != expected_keys:
        raise Px2ValidationReceiptError("px2_receipt_schema_invalid")
    if (
        payload.get("schema_version") != RECEIPT_SCHEMA
        or payload.get("contract_id") != PX2_CONTRACT_ID
    ):
        raise Px2ValidationReceiptError("px2_receipt_schema_invalid")
    command = payload.get("command_argv")
    if not isinstance(command, list) or not all(
        isinstance(value, str) and value for value in command
    ):
        raise Px2ValidationReceiptError("px2_receipt_command_invalid")
    validate_canonical_focused_command(command, approved_python)
    if payload.get("command_fingerprint") != canonical_fingerprint(command):
        raise Px2ValidationReceiptError("px2_receipt_command_fingerprint_mismatch")
    if payload.get("execution_environment_policy_fingerprint") != (
        execution_environment_policy_fingerprint()
    ):
        raise Px2ValidationReceiptError("px2_receipt_environment_policy_mismatch")
    if payload.get("evidence_bindings") != dict(sorted(expected_bindings.items())):
        raise Px2ValidationReceiptError("px2_receipt_evidence_binding_mismatch")
    repository_fields = (
        "git_head",
        "git_tree",
        "trusted_git_fingerprint",
        "python_executable_fingerprint",
        "approved_python_runtime_fingerprint",
    )
    if any(
        payload.get(name) != expected_repository.get(name)
        for name in repository_fields
    ):
        raise Px2ValidationReceiptError("px2_receipt_repository_binding_mismatch")
    if (
        payload.get("exit_code") != 0
        or payload.get("same_head_tree") is not True
        or payload.get("clean_before_after") is not True
        or payload.get("positive") is not True
        or payload.get("trust_level") != "local_operator_receipt"
        or payload.get("machine_verifiable_ci") is not False
        or payload.get("owner_authority_machine_verifiable") is not False
    ):
        raise Px2ValidationReceiptError("px2_receipt_not_positive")
    for field in (
        "trusted_git_fingerprint",
        "python_executable_fingerprint",
        "approved_python_runtime_fingerprint",
        "execution_environment_policy_fingerprint",
        "execution_environment_fingerprint",
        "validation_temp_root_identity_fingerprint",
        "command_fingerprint",
        "stdout_fingerprint",
        "stderr_fingerprint",
    ):
        value = payload.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise Px2ValidationReceiptError("px2_receipt_fingerprint_invalid")


def create_same_head_validation_receipt(
    *,
    repo_root: Path,
    evidence_root: Path,
    evidence_payloads: Mapping[str, Any],
) -> dict[str, Any]:
    root = repo_root.resolve(strict=True)
    approved_python = Path(sys.executable).resolve(strict=True)
    receipt_path = evidence_root / RECEIPT_NAME
    if receipt_path.exists():
        raise Px2ValidationReceiptError("px2_receipt_already_exists")
    bindings = evidence_bindings(evidence_payloads)
    before = repository_identity_snapshot(
        root, python_executable=approved_python, require_clean=True
    )
    git = resolve_trusted_git_executable(repo_root=root)
    try:
        validation_temp = create_owned_validation_temp_root()
    except ConfinementError as exc:
        raise Px2ValidationReceiptError("px2_receipt_temp_root_unavailable") from exc
    environment = canonical_px2_validation_environment(
        canonical_validation_environment(
            trusted_git_executable=git.path,
            validation_temp_root=validation_temp.path,
        ),
        validation_temp_root=validation_temp.path,
    )
    command = canonical_focused_test_command(approved_python)
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            check=False,
            timeout=900,
        )
    finally:
        try:
            remove_owned_validation_temp_root(validation_temp)
        except ConfinementError as exc:
            raise Px2ValidationReceiptError("px2_receipt_temp_cleanup_failed") from exc
    after = repository_identity_snapshot(
        root, python_executable=approved_python, require_clean=True
    )
    payload = build_receipt_payload(
        repository_before=before,
        repository_after=after,
        approved_python=approved_python,
        command=command,
        command_result=completed,
        validation_environment_fingerprint=canonical_fingerprint(
            dict(sorted(environment.items()))
        ),
        validation_temp_root_identity_fingerprint=validation_temp.identity_fingerprint,
        bindings=bindings,
    )
    if payload["positive"] is not True:
        raise Px2ValidationReceiptError("px2_receipt_positive_not_issued")
    try:
        descriptor = os.open(
            receipt_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(payload) + b"\n")
    except OSError as exc:
        raise Px2ValidationReceiptError("px2_receipt_write_failed") from exc
    return payload
