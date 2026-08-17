"""Same-HEAD local validation receipt for SCV2-FL1-I2."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.fl1_i2_evidence import EvidenceStore
from scripts.trusted_git import (
    assert_trusted_worktree_clean,
    resolve_trusted_git_executable,
    run_trusted_git_text,
)


RECEIPT_SCHEMA = "violet.scv2-fl1-i2-local-validation-receipt.v1"


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
    config_fingerprint: str
    policy_fingerprint: str
    manifest_fingerprint: str
    ledger_fingerprint: str
    worker_fingerprint: str
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


def _fingerprint(payload: Any) -> str:
    if isinstance(payload, bytes):
        encoded = payload
    else:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def trusted_git_snapshot(repo_root: Path) -> tuple[GitSnapshot, str]:
    git = resolve_trusted_git_executable(repo_root=repo_root)
    head = run_trusted_git_text(repo_root, ("rev-parse", "HEAD^{commit}"), git=git)
    tree = run_trusted_git_text(repo_root, ("rev-parse", "HEAD^{tree}"), git=git)
    if head.returncode or tree.returncode:
        raise ReceiptError("validation_receipt_git_snapshot_failed")
    return GitSnapshot(head.stdout.strip(), tree.stdout.strip()), git.fingerprint


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
    if not command:
        raise ReceiptError("validation_receipt_command_missing")
    before, git_fingerprint = trusted_git_snapshot(repo_root)
    git = resolve_trusted_git_executable(repo_root=repo_root)
    try:
        assert_trusted_worktree_clean(git, repo_root)
        clean_before = True
    except Exception:
        clean_before = False
    started = time.time_ns()
    completed = subprocess.run(tuple(command), cwd=repo_root, capture_output=True, check=False, timeout=900)
    ended = time.time_ns()
    after, after_git_fingerprint = trusted_git_snapshot(repo_root)
    try:
        assert_trusted_worktree_clean(git, repo_root)
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
        config_fingerprint=bindings["config"],
        policy_fingerprint=bindings["policy"],
        manifest_fingerprint=bindings["manifest"],
        ledger_fingerprint=bindings["ledger"],
        worker_fingerprint=bindings["worker"],
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
