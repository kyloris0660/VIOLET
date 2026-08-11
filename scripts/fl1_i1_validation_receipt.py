"""Execute one local validation command and emit a confined operator receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fl1_i1_operation_gateway import TaskOwnedArtifactStore, load_private_json
from scripts.fl1_i1_runtime_context import (
    RuntimeContextError,
    SourceMode,
    assert_trusted_worktree_clean,
    build_trusted_runtime_context,
    run_trusted_git,
)


VALIDATION_RECEIPT_SCHEMA_VERSION = "violet.scv2-fl1-i1-local-validation-receipt.v2"
VALIDATION_REPORT_SCHEMA_VERSION = "violet.scv2-fl1-i1-local-validation-report.v1"
TRUST_LEVEL = "local_operator_receipt"


class ValidationReceiptError(RuntimeError):
    pass


def _fingerprint_argv(argv: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(list(argv), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class LocalValidationReceipt:
    actual_git_head: str
    trusted_git_fingerprint: str
    python_identity_fingerprint: str
    command_argv: tuple[str, ...]
    command_fingerprint: str
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str
    report_sha256: str
    started_at_ns: int
    ended_at_ns: int
    clean_worktree: bool
    trust_level: str = TRUST_LEVEL
    machine_verifiable_ci: bool = False
    owner_authority_machine_verifiable: bool = False

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VALIDATION_RECEIPT_SCHEMA_VERSION,
            "actual_git_head": self.actual_git_head,
            "trusted_git_fingerprint": self.trusted_git_fingerprint,
            "python_identity_fingerprint": self.python_identity_fingerprint,
            "command_argv": list(self.command_argv),
            "command_fingerprint": self.command_fingerprint,
            "exit_code": self.exit_code,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "report_sha256": self.report_sha256,
            "started_at_ns": self.started_at_ns,
            "ended_at_ns": self.ended_at_ns,
            "clean_worktree": self.clean_worktree,
            "trust_level": self.trust_level,
            "machine_verifiable_ci": self.machine_verifiable_ci,
            "owner_authority_machine_verifiable": self.owner_authority_machine_verifiable,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VALIDATION_RECEIPT_SCHEMA_VERSION,
            "actual_git_head": self.actual_git_head,
            "trusted_git_fingerprint": self.trusted_git_fingerprint,
            "python_identity_fingerprint": self.python_identity_fingerprint,
            "command_fingerprint": self.command_fingerprint,
            "exit_code": self.exit_code,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "report_sha256": self.report_sha256,
            "started_at_ns": self.started_at_ns,
            "ended_at_ns": self.ended_at_ns,
            "clean_worktree": self.clean_worktree,
            "trust_level": self.trust_level,
            "machine_verifiable_ci": self.machine_verifiable_ci,
            "owner_authority_machine_verifiable": self.owner_authority_machine_verifiable,
            "command_emitted": False,
        }

    def validate(self) -> None:
        if (
            self.trust_level != TRUST_LEVEL
            or self.machine_verifiable_ci is not False
            or self.owner_authority_machine_verifiable is not False
        ):
            raise ValidationReceiptError("validation_receipt_authority_escalation")
        if not self.command_argv or any(not value for value in self.command_argv):
            raise ValidationReceiptError("validation_receipt_command_missing")
        if self.command_fingerprint != _fingerprint_argv(self.command_argv):
            raise ValidationReceiptError("validation_receipt_command_fingerprint_mismatch")
        if not isinstance(self.exit_code, int) or isinstance(self.exit_code, bool):
            raise ValidationReceiptError("validation_receipt_exit_code_invalid")
        if self.started_at_ns <= 0 or self.ended_at_ns < self.started_at_ns:
            raise ValidationReceiptError("validation_receipt_timestamp_invalid")
        for value, length in (
            (self.actual_git_head, 40),
            (self.trusted_git_fingerprint, 64),
            (self.python_identity_fingerprint, 64),
            (self.command_fingerprint, 64),
            (self.stdout_sha256, 64),
            (self.stderr_sha256, 64),
            (self.report_sha256, 64),
        ):
            if len(value) != length or any(char not in "0123456789abcdef" for char in value):
                raise ValidationReceiptError("validation_receipt_fingerprint_invalid")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LocalValidationReceipt":
        expected_keys = {
            "schema_version",
            "actual_git_head",
            "trusted_git_fingerprint",
            "python_identity_fingerprint",
            "command_argv",
            "command_fingerprint",
            "exit_code",
            "stdout_sha256",
            "stderr_sha256",
            "report_sha256",
            "started_at_ns",
            "ended_at_ns",
            "clean_worktree",
            "trust_level",
            "machine_verifiable_ci",
            "owner_authority_machine_verifiable",
        }
        if set(payload) != expected_keys or payload.get("schema_version") != VALIDATION_RECEIPT_SCHEMA_VERSION:
            raise ValidationReceiptError("validation_receipt_schema_invalid")
        try:
            argv = payload["command_argv"]
            if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
                raise TypeError
            receipt = cls(
                actual_git_head=str(payload["actual_git_head"]),
                trusted_git_fingerprint=str(payload["trusted_git_fingerprint"]),
                python_identity_fingerprint=str(payload["python_identity_fingerprint"]),
                command_argv=tuple(argv),
                command_fingerprint=str(payload["command_fingerprint"]),
                exit_code=int(payload["exit_code"]),
                stdout_sha256=str(payload["stdout_sha256"]),
                stderr_sha256=str(payload["stderr_sha256"]),
                report_sha256=str(payload["report_sha256"]),
                started_at_ns=int(payload["started_at_ns"]),
                ended_at_ns=int(payload["ended_at_ns"]),
                clean_worktree=payload["clean_worktree"],
                trust_level=str(payload["trust_level"]),
                machine_verifiable_ci=payload["machine_verifiable_ci"],
                owner_authority_machine_verifiable=payload[
                    "owner_authority_machine_verifiable"
                ],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationReceiptError("validation_receipt_invalid") from exc
        if not isinstance(receipt.clean_worktree, bool):
            raise ValidationReceiptError("validation_receipt_clean_state_invalid")
        receipt.validate()
        return receipt


def create_local_validation_receipt(
    *,
    repo_root: Path,
    private_root_config: Path,
    source_root: Path,
    source_scope_id: str,
    command_argv: Sequence[str],
    report_path: Path,
    output_path: Path,
) -> LocalValidationReceipt:
    """Personally execute the command; no caller-supplied result is accepted."""

    context = build_trusted_runtime_context(
        repo_root=repo_root,
        private_root_config=private_root_config,
        source_root=source_root,
        source_mode=SourceMode.SYNTHETIC_FIXTURE,
        source_scope_id=source_scope_id,
    )
    git = context.git_executable
    actual_root = context.repo_root
    python_identity = context.python_identity
    argv = tuple(str(value) for value in command_argv)
    if not argv:
        raise ValidationReceiptError("validation_receipt_command_missing")
    if os.path.normcase(os.path.abspath(argv[0])) != os.path.normcase(
        os.path.abspath(sys.executable)
    ):
        raise ValidationReceiptError("validation_receipt_command_python_untrusted")
    try:
        assert_trusted_worktree_clean(git, actual_root)
        clean_before = True
    except RuntimeContextError:
        clean_before = False
    started = time.time_ns()
    completed = subprocess.run(
        list(argv),
        cwd=actual_root,
        capture_output=True,
        check=False,
    )
    ended = time.time_ns()
    try:
        assert_trusted_worktree_clean(git, actual_root)
        clean_after = True
    except RuntimeContextError:
        clean_after = False
    store = TaskOwnedArtifactStore(
        context.roots.roots["phase_evidence_output_root"]
    )
    command_fingerprint = _fingerprint_argv(argv)
    report = {
        "schema_version": VALIDATION_REPORT_SCHEMA_VERSION,
        "command_fingerprint": command_fingerprint,
        "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
        "started_at_ns": started,
        "ended_at_ns": ended,
    }
    store.atomic_write_json(Path(report_path), report)
    receipt = LocalValidationReceipt(
        actual_git_head=run_trusted_git(git, actual_root, "rev-parse", "HEAD^{commit}"),
        trusted_git_fingerprint=git.fingerprint,
        python_identity_fingerprint=str(python_identity["identity_fingerprint"]),
        command_argv=argv,
        command_fingerprint=command_fingerprint,
        exit_code=completed.returncode,
        stdout_sha256=report["stdout_sha256"],
        stderr_sha256=report["stderr_sha256"],
        report_sha256=_sha256_file(Path(report_path)),
        started_at_ns=started,
        ended_at_ns=ended,
        clean_worktree=clean_before and clean_after,
    )
    receipt.validate()
    store.atomic_write_json(Path(output_path), receipt.to_private_dict())
    return receipt


def load_local_validation_receipt(path: Path) -> LocalValidationReceipt:
    return LocalValidationReceipt.from_dict(load_private_json(Path(path)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--private-root-config", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-scope-id", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    try:
        receipt = create_local_validation_receipt(
            repo_root=Path(args.repo_root),
            private_root_config=Path(args.private_root_config),
            source_root=Path(args.source_root),
            source_scope_id=args.source_scope_id,
            command_argv=command,
            report_path=Path(args.report),
            output_path=Path(args.output),
        )
    except (OSError, UnicodeError, RuntimeContextError, ValidationReceiptError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({
        "passed": receipt.exit_code == 0 and receipt.clean_worktree,
        "actual_git_head": receipt.actual_git_head,
        "exit_code": receipt.exit_code,
        "trust_level": receipt.trust_level,
        "machine_verifiable_ci": False,
        "owner_authority_machine_verifiable": False,
        "clean_worktree": receipt.clean_worktree,
        "private_paths_emitted": False,
    }, sort_keys=True))
    return 0 if receipt.exit_code == 0 and receipt.clean_worktree else 1


if __name__ == "__main__":
    raise SystemExit(main())
