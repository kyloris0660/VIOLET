"""Same-HEAD local validation receipt for SCV2-PX1.

The receipt extends the repository's existing trusted-Git and confined
validation-temp machinery.  It records fingerprints only in public-facing
bindings; the fixed private receipt remains below an operator-provided local
temporary evidence directory.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

from scripts.fl1_i2_confinement import (
    ConfinementError,
    create_owned_validation_temp_root,
    remove_owned_validation_temp_root,
)
from scripts.fl1_i2_validation_receipt import (
    canonical_validation_environment,
    execution_environment_policy_fingerprint as base_environment_policy_fingerprint,
)
from scripts.trusted_git import (
    assert_trusted_worktree_clean,
    resolve_trusted_git_executable,
    run_trusted_git_text,
    verify_approved_python_runtime,
)


RECEIPT_SCHEMA = "violet.scv2-px1-local-validation-receipt.v1"
RECEIPT_NAME = "local-validation-receipt.json"
PX1_CONTRACT_ID = "scv2_px1_pixiv_metadata_consolidation_contract_v1"
EVIDENCE_ARTIFACT_NAMES = (
    "synthetic-fixture.json",
    "aggregates.json",
    "signal-bundles.json",
    "operation-receipt.json",
    "public-summary.json",
)
CANONICAL_FOCUSED_TESTS = (
    "tests/test_pixiv_metadata_ingestion_service.py",
    "tests/test_phase44p2r_f3_pixiv_metadata_normalization.py",
    "tests/test_phase45_px1_pixiv_metadata_dedup_dry_run.py",
    "tests/test_phase45_sc1_source_concept_resolver.py",
    "tests/test_scv2_px1_pixiv_metadata_consolidation.py",
    "tests/test_scv2_px1_validation_receipt.py",
    "tests/test_scv2_px1_contract.py",
)
MAX_RECEIPT_BYTES = 256 * 1024


class Px1ValidationReceiptError(RuntimeError):
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


def execution_environment_policy_fingerprint() -> str:
    return canonical_fingerprint(
        {
            "schema_version": "violet.scv2-px1-validation-environment-policy.v1",
            "base_i2_environment_policy_fingerprint": (
                base_environment_policy_fingerprint()
            ),
            "dotenv_skipped": True,
            "violet_environment": "test",
            "test_database_name": "scv2_px1_task_temp",
            "storage_root_policy": "validation_temp_root/runtime-storage",
            "existing_app_storage_access": False,
            "dynamic_loader_policy_due_gate": (
                "FL1_I2_DYNAMIC_LOADER_ENVIRONMENT_POLICY"
            ),
        }
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.resolve(strict=True).read_bytes())
    except OSError as exc:
        raise Px1ValidationReceiptError("px1_receipt_python_invalid") from exc


def canonical_focused_test_command(python_executable: Path) -> tuple[str, ...]:
    try:
        approved = python_executable.resolve(strict=True)
    except OSError as exc:
        raise Px1ValidationReceiptError("px1_receipt_python_invalid") from exc
    if not approved.is_file():
        raise Px1ValidationReceiptError("px1_receipt_python_invalid")
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
    command: Sequence[str],
    approved_python: Path,
) -> None:
    if tuple(command) != canonical_focused_test_command(approved_python):
        raise Px1ValidationReceiptError("px1_receipt_command_not_canonical")


def _git_value(repo_root: Path, *arguments: str) -> str:
    git = resolve_trusted_git_executable(repo_root=repo_root)
    result = run_trusted_git_text(repo_root, arguments, git=git)
    if result.returncode:
        raise Px1ValidationReceiptError("px1_receipt_git_snapshot_failed")
    return result.stdout.strip()


def repository_identity_snapshot(
    repo_root: Path,
    *,
    python_executable: Path | None = None,
    require_clean: bool = True,
) -> dict[str, str | bool]:
    root = repo_root.resolve(strict=True)
    approved_python = (python_executable or Path(sys.executable)).resolve(strict=True)
    runtime = verify_approved_python_runtime(approved_python, repo_root=root)
    git = resolve_trusted_git_executable(repo_root=root)
    if require_clean:
        assert_trusted_worktree_clean(
            git,
            root,
            approved_python_runtime=runtime,
        )
    return {
        "git_head": _git_value(root, "rev-parse", "HEAD^{commit}"),
        "git_tree": _git_value(root, "rev-parse", "HEAD^{tree}"),
        "trusted_git_fingerprint": git.fingerprint,
        "python_executable_fingerprint": _sha256_file(approved_python),
        "approved_python_runtime_fingerprint": runtime.execution_manifest_fingerprint,
        "worktree_clean": True,
    }


def evidence_bindings(payloads: Mapping[str, Any]) -> dict[str, str]:
    if set(payloads) != set(EVIDENCE_ARTIFACT_NAMES):
        raise Px1ValidationReceiptError("px1_receipt_evidence_set_invalid")
    return {
        name: canonical_fingerprint(payloads[name])
        for name in sorted(EVIDENCE_ARTIFACT_NAMES)
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
    if set(bindings) != set(EVIDENCE_ARTIFACT_NAMES):
        raise Px1ValidationReceiptError("px1_receipt_evidence_set_invalid")
    same_head_tree = (
        repository_before.get("git_head") == repository_after.get("git_head")
        and repository_before.get("git_tree") == repository_after.get("git_tree")
        and repository_before.get("trusted_git_fingerprint")
        == repository_after.get("trusted_git_fingerprint")
    )
    clean_before_after = (
        repository_before.get("worktree_clean") is True
        and repository_after.get("worktree_clean") is True
    )
    positive = command_result.returncode == 0 and same_head_tree and clean_before_after
    return {
        "schema_version": RECEIPT_SCHEMA,
        "contract_id": PX1_CONTRACT_ID,
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
        raise Px1ValidationReceiptError("px1_receipt_schema_invalid")
    if (
        payload.get("schema_version") != RECEIPT_SCHEMA
        or payload.get("contract_id") != PX1_CONTRACT_ID
    ):
        raise Px1ValidationReceiptError("px1_receipt_schema_invalid")
    command = payload.get("command_argv")
    if not isinstance(command, list) or not all(
        isinstance(value, str) and value for value in command
    ):
        raise Px1ValidationReceiptError("px1_receipt_command_invalid")
    validate_canonical_focused_command(command, approved_python)
    if payload.get("command_fingerprint") != canonical_fingerprint(command):
        raise Px1ValidationReceiptError("px1_receipt_command_fingerprint_mismatch")
    if payload.get("execution_environment_policy_fingerprint") != (
        execution_environment_policy_fingerprint()
    ):
        raise Px1ValidationReceiptError("px1_receipt_environment_policy_mismatch")
    if payload.get("evidence_bindings") != dict(sorted(expected_bindings.items())):
        raise Px1ValidationReceiptError("px1_receipt_evidence_binding_mismatch")
    repository_fields = (
        "git_head",
        "git_tree",
        "trusted_git_fingerprint",
        "python_executable_fingerprint",
        "approved_python_runtime_fingerprint",
    )
    if any(payload.get(name) != expected_repository.get(name) for name in repository_fields):
        raise Px1ValidationReceiptError("px1_receipt_repository_binding_mismatch")
    if (
        payload.get("exit_code") != 0
        or payload.get("same_head_tree") is not True
        or payload.get("clean_before_after") is not True
        or payload.get("positive") is not True
        or payload.get("trust_level") != "local_operator_receipt"
        or payload.get("machine_verifiable_ci") is not False
        or payload.get("owner_authority_machine_verifiable") is not False
    ):
        raise Px1ValidationReceiptError("px1_receipt_not_positive")
    fingerprint_fields = (
        "trusted_git_fingerprint",
        "python_executable_fingerprint",
        "approved_python_runtime_fingerprint",
        "execution_environment_policy_fingerprint",
        "execution_environment_fingerprint",
        "validation_temp_root_identity_fingerprint",
        "command_fingerprint",
        "stdout_fingerprint",
        "stderr_fingerprint",
    )
    for field in fingerprint_fields:
        value = payload.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise Px1ValidationReceiptError("px1_receipt_fingerprint_invalid")


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
        raise Px1ValidationReceiptError("px1_receipt_already_exists")
    bindings = evidence_bindings(evidence_payloads)
    before = repository_identity_snapshot(
        root,
        python_executable=approved_python,
        require_clean=True,
    )
    git = resolve_trusted_git_executable(repo_root=root)
    try:
        validation_temp = create_owned_validation_temp_root()
    except ConfinementError as exc:
        raise Px1ValidationReceiptError("px1_receipt_temp_root_unavailable") from exc
    environment = canonical_validation_environment(
        trusted_git_executable=git.path,
        validation_temp_root=validation_temp.path,
    )
    runtime_storage = validation_temp.path / "runtime-storage"
    environment.update(
        {
            "VIOLET_SKIP_DOTENV": "1",
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "scv2_px1_task_temp",
            "TEST_DATABASE_URL": "",
            "VIOLET_STORAGE_ROOT": os.fspath(runtime_storage),
            "VIOLET_TEST_STORAGE_ROOT": os.fspath(runtime_storage),
        }
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
            raise Px1ValidationReceiptError("px1_receipt_temp_cleanup_failed") from exc
    after = repository_identity_snapshot(
        root,
        python_executable=approved_python,
        require_clean=True,
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
        raise Px1ValidationReceiptError("px1_receipt_positive_not_issued")
    try:
        descriptor = os.open(
            receipt_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(payload) + b"\n")
    except OSError as exc:
        raise Px1ValidationReceiptError("px1_receipt_write_failed") from exc
    return payload
