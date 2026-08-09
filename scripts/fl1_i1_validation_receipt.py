"""Local-operator validation receipts for SCV2-FL1-I1.

Receipts bind a command result to the actual repository HEAD and report bytes.
They are deliberately not CI or owner authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fl1_i1_operation_gateway import atomic_write_json, load_private_json
from scripts.fl1_p1_foundation import LedgerError, assert_evidence_worktree_clean


VALIDATION_RECEIPT_SCHEMA_VERSION = "violet.scv2-fl1-i1-local-validation-receipt.v1"
TRUST_LEVEL = "local_operator_receipt"


class ValidationReceiptError(RuntimeError):
    """Raised when local validation evidence is malformed or drifts."""


def _git(repo_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValidationReceiptError("validation_receipt_git_identity_failed")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class LocalValidationReceipt:
    actual_git_head: str
    command: str
    command_fingerprint: str
    exit_code: int
    report_sha256: str
    started_at_ns: int
    ended_at_ns: int
    clean_worktree: bool
    trust_level: str = TRUST_LEVEL
    machine_verifiable_ci: bool = False

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VALIDATION_RECEIPT_SCHEMA_VERSION,
            "actual_git_head": self.actual_git_head,
            "command": self.command,
            "command_fingerprint": self.command_fingerprint,
            "exit_code": self.exit_code,
            "report_sha256": self.report_sha256,
            "started_at_ns": self.started_at_ns,
            "ended_at_ns": self.ended_at_ns,
            "clean_worktree": self.clean_worktree,
            "trust_level": self.trust_level,
            "machine_verifiable_ci": self.machine_verifiable_ci,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VALIDATION_RECEIPT_SCHEMA_VERSION,
            "actual_git_head": self.actual_git_head,
            "command_fingerprint": self.command_fingerprint,
            "exit_code": self.exit_code,
            "report_sha256": self.report_sha256,
            "started_at_ns": self.started_at_ns,
            "ended_at_ns": self.ended_at_ns,
            "clean_worktree": self.clean_worktree,
            "trust_level": self.trust_level,
            "machine_verifiable_ci": self.machine_verifiable_ci,
            "command_emitted": False,
        }

    def validate(self) -> None:
        if self.trust_level != TRUST_LEVEL or self.machine_verifiable_ci is not False:
            raise ValidationReceiptError("validation_receipt_authority_escalation")
        if not isinstance(self.exit_code, int) or isinstance(self.exit_code, bool):
            raise ValidationReceiptError("validation_receipt_exit_code_invalid")
        if self.started_at_ns <= 0 or self.ended_at_ns < self.started_at_ns:
            raise ValidationReceiptError("validation_receipt_timestamp_invalid")
        if not self.command:
            raise ValidationReceiptError("validation_receipt_command_missing")
        expected_command = hashlib.sha256(self.command.encode("utf-8")).hexdigest()
        if self.command_fingerprint != expected_command:
            raise ValidationReceiptError("validation_receipt_command_fingerprint_mismatch")
        if len(self.actual_git_head) != 40 or any(
            char not in "0123456789abcdef" for char in self.actual_git_head
        ):
            raise ValidationReceiptError("validation_receipt_fingerprint_invalid")
        for value in (self.report_sha256, self.command_fingerprint):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValidationReceiptError("validation_receipt_fingerprint_invalid")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LocalValidationReceipt":
        if set(payload) != {
            "schema_version",
            "actual_git_head",
            "command",
            "command_fingerprint",
            "exit_code",
            "report_sha256",
            "started_at_ns",
            "ended_at_ns",
            "clean_worktree",
            "trust_level",
            "machine_verifiable_ci",
        } or payload.get("schema_version") != VALIDATION_RECEIPT_SCHEMA_VERSION:
            raise ValidationReceiptError("validation_receipt_schema_invalid")
        try:
            receipt = cls(
                actual_git_head=str(payload["actual_git_head"]),
                command=str(payload["command"]),
                command_fingerprint=str(payload["command_fingerprint"]),
                exit_code=int(payload["exit_code"]),
                report_sha256=str(payload["report_sha256"]),
                started_at_ns=int(payload["started_at_ns"]),
                ended_at_ns=int(payload["ended_at_ns"]),
                clean_worktree=payload["clean_worktree"],
                trust_level=str(payload["trust_level"]),
                machine_verifiable_ci=payload["machine_verifiable_ci"],
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
    command: str,
    exit_code: int,
    report_path: Path,
    started_at_ns: int,
    output_path: Path,
) -> LocalValidationReceipt:
    """Create one private receipt from actual Git and report bytes."""

    root = Path(repo_root).resolve(strict=True)
    actual_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if actual_root != root:
        raise ValidationReceiptError("validation_receipt_repo_root_mismatch")
    head = _git(root, "rev-parse", "HEAD^{commit}")
    try:
        assert_evidence_worktree_clean(root)
        clean_worktree = True
    except LedgerError:
        clean_worktree = False
    receipt = LocalValidationReceipt(
        actual_git_head=head,
        command=command,
        command_fingerprint=hashlib.sha256(command.encode("utf-8")).hexdigest(),
        exit_code=exit_code,
        report_sha256=_sha256_file(Path(report_path)),
        started_at_ns=started_at_ns,
        ended_at_ns=time.time_ns(),
        clean_worktree=clean_worktree,
    )
    receipt.validate()
    atomic_write_json(Path(output_path), receipt.to_private_dict())
    return receipt


def load_local_validation_receipt(path: Path) -> LocalValidationReceipt:
    return LocalValidationReceipt.from_dict(load_private_json(Path(path)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--started-at-ns", type=int, required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = create_local_validation_receipt(
            repo_root=Path(args.repo_root),
            command=args.command,
            exit_code=args.exit_code,
            report_path=Path(args.report),
            started_at_ns=args.started_at_ns,
            output_path=Path(args.output),
        )
    except (OSError, UnicodeError, ValidationReceiptError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "passed": True,
                "actual_git_head": receipt.actual_git_head,
                "trust_level": receipt.trust_level,
                "machine_verifiable_ci": receipt.machine_verifiable_ci,
                "clean_worktree": receipt.clean_worktree,
                "private_paths_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
