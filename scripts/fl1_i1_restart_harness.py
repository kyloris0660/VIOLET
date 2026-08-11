"""Independent parent harness for controlled FL1-I1 stop/resume evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fl1_i1_inventory import _process_identity_for_pid, _process_start_observation
from scripts.fl1_i1_operation_gateway import TaskOwnedArtifactStore
from scripts.fl1_i1_runtime_context import SourceMode, build_trusted_runtime_context


HARNESS_SCHEMA_VERSION = "violet.scv2-fl1-i1-restart-parent-harness.v1"


class RestartHarnessError(RuntimeError):
    pass


def _fingerprint_argv(argv: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(list(argv), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _child(argv: Sequence[str], *, cwd: Path, parent_checkpoint: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.time_ns()
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _alive, process_start = _process_identity_for_pid(process.pid)
    stdout, stderr = process.communicate()
    ended = time.time_ns()
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestartHarnessError("restart_child_output_invalid") from exc
    if process.returncode != 0 or not isinstance(payload, Mapping):
        raise RestartHarnessError("restart_child_failed")
    child_receipt = {
        "launcher_pid": process.pid,
        "launcher_process_start_observation": process_start,
        "child_pid": payload.get("pid"),
        "child_process_start_observation": payload.get("process_start_observation"),
        "argv_fingerprint": _fingerprint_argv(argv),
        "started_at_ns": started,
        "ended_at_ns": ended,
        "exit_code": process.returncode,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "parent_checkpoint_fingerprint": parent_checkpoint,
        "run_id": payload.get("run_id"),
        "invocation_id": payload.get("invocation_id"),
        "child_checkpoint_fingerprint": payload.get("checkpoint_fingerprint"),
    }
    return dict(payload), child_receipt


def run_controlled_restart_harness(
    *,
    project_root: Path,
    repo_root: Path,
    private_root_config: Path,
    source_root: Path,
    source_scope_id: str,
    evidence_root: Path,
    budgets_config: Path,
    synthetic_attributes: Path,
    stop_after_items: int = 2,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    context = build_trusted_runtime_context(
        repo_root=repo_root,
        private_root_config=private_root_config,
        source_root=source_root,
        source_mode=SourceMode.SYNTHETIC_FIXTURE,
        source_scope_id=source_scope_id,
    )
    if Path(evidence_root).resolve(strict=True) != context.roots.roots["phase_evidence_output_root"]:
        raise RestartHarnessError("restart_harness_evidence_root_mismatch")
    base = [
        os.fspath(Path(sys.executable)),
        os.fspath(Path(project_root) / "scripts" / "fl1_i1_inventory.py"),
        "scan",
        "--repo-root", os.fspath(repo_root),
        "--private-root-config", os.fspath(private_root_config),
        "--source-root", os.fspath(source_root),
        "--source-mode", SourceMode.SYNTHETIC_FIXTURE.value,
        "--source-scope-id", source_scope_id,
        "--evidence-root", os.fspath(evidence_root),
        "--budgets-config", os.fspath(budgets_config),
        "--attribute-adapter", "synthetic",
        "--synthetic-attributes", os.fspath(synthetic_attributes),
    ]
    first, first_receipt = _child(
        [*base, "--stop-after-items", str(stop_after_items)],
        cwd=Path(project_root),
        parent_checkpoint=None,
    )
    if first.get("status") != "controlled_stop":
        raise RestartHarnessError("restart_first_child_not_controlled_stop")
    second_argv = [
        *base,
        "--resume-run-id", str(first["run_id"]),
        "--parent-checkpoint", str(first["checkpoint_fingerprint"]),
    ]
    second, second_receipt = _child(
        second_argv,
        cwd=Path(project_root),
        parent_checkpoint=str(first["checkpoint_fingerprint"]),
    )
    if second.get("status") != "complete" or second.get("run_id") != first.get("run_id"):
        raise RestartHarnessError("restart_second_child_not_complete")
    run_dir = Path(evidence_root) / str(first["run_id"])
    checkpoint = run_dir / "private-inventory-checkpoint.json"
    receipt: dict[str, Any] = {
        "schema_version": HARNESS_SCHEMA_VERSION,
        "run_id": first["run_id"],
        "actual_git_head": context.actual_git_head,
        "parent_pid": os.getpid(),
        "parent_process_start_observation": _process_start_observation(),
        "created_at_ns": time.time_ns(),
        "children": [first_receipt, second_receipt],
        "final_checkpoint_artifact_sha256": _sha256(checkpoint),
        "attestation_level": "local_parent_child_executable_provenance_not_os_tpm_or_ci",
    }
    receipt["receipt_fingerprint"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    TaskOwnedArtifactStore(Path(evidence_root)).atomic_write_json(
        run_dir / "restart-parent-harness-receipt.json", receipt
    )
    return first, second, run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--private-root-config", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-scope-id", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--budgets-config", required=True)
    parser.add_argument("--synthetic-attributes", required=True)
    parser.add_argument("--stop-after-items", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        first, second, _ = run_controlled_restart_harness(
            project_root=Path(args.project_root),
            repo_root=Path(args.repo_root),
            private_root_config=Path(args.private_root_config),
            source_root=Path(args.source_root),
            source_scope_id=args.source_scope_id,
            evidence_root=Path(args.evidence_root),
            budgets_config=Path(args.budgets_config),
            synthetic_attributes=Path(args.synthetic_attributes),
            stop_after_items=args.stop_after_items,
        )
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({
        "passed": True,
        "run_id": second["run_id"],
        "first_invocation_id": first["invocation_id"],
        "second_invocation_id": second["invocation_id"],
        "attestation_level": "local_parent_child_executable_provenance_not_os_tpm_or_ci",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
