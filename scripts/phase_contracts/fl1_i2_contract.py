"""Executable evidence contract for SCV2-FL1-I2 synthetic hardening."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.fl1_i2_evidence import (
    EvidenceStore,
    FailureBudget,
    FixedCutManifest,
    ManifestMember,
    OperationLedger,
    OperationState,
    canonical_fingerprint,
)
from scripts.fl1_i2_runner import CANONICAL_POLICY, CONFIG_SCHEMA, CONTRACT_ID, PUBLIC_SCHEMA
from scripts.fl1_i2_validation_receipt import SameHeadValidationReceipt
from scripts.trusted_git import (
    assert_trusted_worktree_clean,
    resolve_trusted_git_executable,
    run_trusted_git_text,
)


ACCEPTED_PLANNING_HEAD = "acb12c1db258fdef1d4f063b053d422e0d887abf"
ACCEPTED_PLANNING_TREE = "fc573c7646ad5edf10c32c7712de7f27ab058a2a"
PLANNING_PROJECTION_HEAD = "7275bceff9152ea5f823186691e6b91ee2ca1e11"
PLANNING_MERGE_COMMIT = "1913bd27517efc1a6007a202fc9650de4f20fab4"

GATE_FINDINGS = (1, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 16, 17)
REQUIRED_FOCUSED_TESTS = (
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


class FL1I2ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class FL1I2EvidencePaths:
    root: Path

    @property
    def config(self) -> str:
        return "private-run-config.json"

    @property
    def manifest(self) -> str:
        return "private-manifest.json"

    @property
    def ledger(self) -> str:
        return "private-operation-ledger.json"

    @property
    def workers(self) -> str:
        return "private-worker-results.json"

    @property
    def receipt(self) -> str:
        return "local-validation-receipt.json"


def _load(paths: FL1I2EvidencePaths) -> dict[str, Any]:
    store = EvidenceStore(paths.root)
    return {
        "config": store.read(paths.config),
        "manifest": store.read(paths.manifest),
        "ledger": store.read(paths.ledger),
        "workers": store.read(paths.workers),
        "receipt": store.read(paths.receipt),
    }


def _manifest(payload: Mapping[str, Any]) -> FixedCutManifest:
    try:
        members = tuple(
            ManifestMember(str(item["item_id"]), str(item["private_name"]), item["object_identity"])
            for item in payload["members"]
        )
        rebuilt = FixedCutManifest.build(
            run_id=str(payload["run_id"]),
            source_scope_fingerprint=str(payload["source_scope_fingerprint"]),
            members=members,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FL1I2ContractError("fl1_i2_manifest_invalid") from exc
    if rebuilt.to_private_dict() != dict(payload):
        raise FL1I2ContractError("fl1_i2_manifest_fingerprint_mismatch")
    return rebuilt


def _repository_snapshot(repo_root: Path) -> tuple[str, str, str]:
    git = resolve_trusted_git_executable(repo_root=repo_root)
    assert_trusted_worktree_clean(git, repo_root)

    def value(*arguments: str) -> str:
        result = run_trusted_git_text(repo_root, arguments, git=git)
        if result.returncode:
            raise FL1I2ContractError("fl1_i2_repository_proof_failed")
        return result.stdout.strip()

    if value("rev-parse", f"{ACCEPTED_PLANNING_HEAD}^{{tree}}") != ACCEPTED_PLANNING_TREE:
        raise FL1I2ContractError("fl1_i2_accepted_planning_tree_mismatch")
    for ancestor in (ACCEPTED_PLANNING_HEAD, PLANNING_PROJECTION_HEAD, PLANNING_MERGE_COMMIT):
        result = run_trusted_git_text(repo_root, ("merge-base", "--is-ancestor", ancestor, "HEAD"), git=git)
        if result.returncode:
            raise FL1I2ContractError("fl1_i2_planning_ancestry_invalid")
    return value("rev-parse", "HEAD^{commit}"), value("rev-parse", "HEAD^{tree}"), git.fingerprint


def _required_code_proof(repo_root: Path) -> None:
    required_markers = {
        "scripts/check_documentation_state.py": "scripts.trusted_git",
        "scripts/fl1_i1_runtime_context.py": "scripts.trusted_git",
        "scripts/fl1_i2_validation_receipt.py": "scripts.trusted_git",
        "scripts/phase_contracts/fl1_i2_contract.py": "scripts.trusted_git",
        "scripts/fl1_i2_source_backends.py": "FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS",
        "scripts/fl1_i2_worker.py": "persist_started()",
        "scripts/fl1_i2_evidence.py": "residual_intent_recovered_without_execution",
        "scripts/fl1_i2_media_validation.py": "media_validation_byte_budget_exceeded",
        "scripts/fl1_i2_cli.py": "correlation_token",
    }
    git = resolve_trusted_git_executable(repo_root=repo_root)
    for path, marker in required_markers.items():
        result = run_trusted_git_text(repo_root, ("show", f"HEAD:{path}"), git=git)
        if result.returncode or marker not in result.stdout:
            raise FL1I2ContractError("fl1_i2_required_code_proof_missing")


def _derive_gate_closure(
    *,
    repo_root: Path,
    config: Mapping[str, Any],
    manifest: FixedCutManifest,
    ledger: OperationLedger,
    workers: Mapping[str, Any],
    receipt: SameHeadValidationReceipt,
) -> dict[str, bool]:
    _required_code_proof(repo_root)
    command_text = "\n".join(receipt.command_argv)
    if any(test not in command_text for test in REQUIRED_FOCUSED_TESTS):
        raise FL1I2ContractError("fl1_i2_focused_validation_incomplete")
    records = workers.get("records")
    if not isinstance(records, list) or not records:
        raise FL1I2ContractError("fl1_i2_worker_evidence_missing")
    if any(not item.get("started_persisted") or not item.get("exit_confirmed") for item in records):
        raise FL1I2ContractError("fl1_i2_worker_closure_incomplete")
    operation_ids = {event.operation_id for event in ledger.events}
    recorded_worker_ids = {str(item.get("operation_id")) for item in records}
    if not recorded_worker_ids <= operation_ids:
        raise FL1I2ContractError("fl1_i2_worker_ledger_reconciliation_failed")
    for missing in operation_ids - recorded_worker_ids:
        if ledger.state(missing) not in {OperationState.RECOVERED, OperationState.INTERRUPTED}:
            raise FL1I2ContractError("fl1_i2_worker_ledger_reconciliation_failed")
    if any(sum(event.state in {OperationState.COMPLETED, OperationState.FAILED, OperationState.INTERRUPTED, OperationState.RECOVERED} for event in ledger.events if event.operation_id == identifier) != 1 for identifier in operation_ids):
        raise FL1I2ContractError("fl1_i2_terminal_closure_invalid")

    # Re-derive the exact failure maximum and recovery boundary; no caller flag
    # participates in either result.
    budget = FailureBudget(**config["budget"])
    scenario = OperationLedger("contract-scenario", "manifest", canonical_fingerprint(budget.to_dict()))
    if budget.max_failures:
        for index in range(budget.max_failures):
            identifier = scenario.begin(item_id=f"failure-{index}", attempt=1, budget=budget)
            scenario.mark_started(identifier)
            scenario.close(identifier, OperationState.FAILED, "synthetic_contract_failure")
        if scenario.can_admit(budget):
            raise FL1I2ContractError("fl1_i2_failure_maximum_off_by_one")
    recovery = OperationLedger("contract-recovery", "manifest", "budget")
    intent = recovery.begin(item_id="intent", attempt=1, budget=FailureBudget(2, 3, 10, 1))
    started = recovery.begin(item_id="started", attempt=1, budget=FailureBudget(2, 3, 10, 1))
    recovery.mark_started(started)
    recovery.recover_residuals()
    if recovery.state(intent) is not OperationState.RECOVERED or recovery.state(started) is not OperationState.INTERRUPTED:
        raise FL1I2ContractError("fl1_i2_recovery_derivation_failed")

    completed_payloads = [item.get("payload") for item in records if item.get("status") == "completed" and isinstance(item.get("payload"), Mapping)]
    opened = [payload for payload in completed_payloads if "object_identity" in payload]
    if manifest.members and not opened:
        raise FL1I2ContractError("fl1_i2_handle_identity_evidence_missing")
    return {str(finding): True for finding in GATE_FINDINGS}


def derive_canonical_public_projection(*, repo_root: Path, evidence_paths: FL1I2EvidencePaths) -> tuple[dict[str, Any], dict[str, bool]]:
    evidence = _load(evidence_paths)
    config = evidence["config"]
    if config.get("schema_version") != CONFIG_SCHEMA or config.get("mode") != "synthetic_new_temp_fixture":
        raise FL1I2ContractError("fl1_i2_config_invalid")
    if config.get("policy") != CANONICAL_POLICY:
        raise FL1I2ContractError("fl1_i2_policy_drift")
    authorities = config.get("authorities")
    if not isinstance(authorities, Mapping) or any(value is not False for value in authorities.values()):
        raise FL1I2ContractError("fl1_i2_authority_escalation")
    manifest = _manifest(evidence["manifest"])
    ledger = OperationLedger.from_private_dict(evidence["ledger"])
    if ledger.run_id != config["run_id"] or ledger.manifest_fingerprint != manifest.manifest_fingerprint:
        raise FL1I2ContractError("fl1_i2_run_binding_mismatch")
    workers = evidence["workers"]
    if workers.get("run_id") != config["run_id"]:
        raise FL1I2ContractError("fl1_i2_worker_run_binding_mismatch")
    receipt = SameHeadValidationReceipt.from_private_dict(evidence["receipt"])
    bindings = {
        "config": canonical_fingerprint(config),
        "policy": canonical_fingerprint(config["policy"]),
        "manifest": manifest.manifest_fingerprint,
        "ledger": canonical_fingerprint(evidence["ledger"]),
        "worker": canonical_fingerprint(workers),
    }
    if receipt.run_id != config["run_id"] or any(getattr(receipt, f"{name}_fingerprint") != value for name, value in bindings.items()):
        raise FL1I2ContractError("fl1_i2_receipt_binding_mismatch")
    head, tree, git_fingerprint = _repository_snapshot(repo_root)
    if not receipt.positive or (receipt.git_head, receipt.git_tree, receipt.trusted_git_fingerprint) != (head, tree, git_fingerprint):
        raise FL1I2ContractError("fl1_i2_same_head_receipt_invalid")
    gates = _derive_gate_closure(repo_root=repo_root, config=config, manifest=manifest, ledger=ledger, workers=workers, receipt=receipt)
    dispositions = ledger.item_dispositions
    item_counts = {"manifest": len(manifest.members)}
    for name in ("pending", "content_verified", "corrupt_media", "interrupted", "deferred", "failed"):
        item_counts[name] = sum(value.value == name for value in dispositions.values())
    summary = {
        "schema_version": PUBLIC_SCHEMA,
        "contract_id": CONTRACT_ID,
        "status": "synthetic_implementation_evidence_complete",
        "run_token": __import__("hashlib").sha256(str(config["run_id"]).encode("utf-8")).hexdigest()[:24],
        "item_counts": item_counts,
        "operation_counts": {
            "total": ledger.operation_count,
            "completed": sum(event.state is OperationState.COMPLETED for event in ledger.events),
            "failed": ledger.failure_count,
            "interrupted": sum(event.state is OperationState.INTERRUPTED for event in ledger.events),
            "recovered": sum(event.state is OperationState.RECOVERED for event in ledger.events),
        },
        "evidence_bindings": bindings,
        "authorities": dict(authorities),
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "machine_verifiable_ci": False,
        "trust_level": "local_operator_evidence",
        "public_redaction": {"passed": True, "paths_redacted": True, "filenames_redacted": True, "object_ids_redacted": True, "content_hashes_redacted": True},
    }
    return summary, gates


def check_fl1_i2_contract(contract: Any, summary: Mapping[str, Any], result: Any, *, repository_context: Any) -> None:
    evidence_paths = getattr(repository_context, "fl1_i2_evidence", None) if repository_context is not None else None
    if not isinstance(evidence_paths, FL1I2EvidencePaths):
        result.fail("fl1_i2_private_evidence_required", "Exact confined FL1-I2 private evidence is required.")
        return
    try:
        expected, gates = derive_canonical_public_projection(repo_root=repository_context.repo_root, evidence_paths=evidence_paths)
    except Exception as exc:
        result.fail(str(exc), "FL1-I2 evidence re-derivation failed.")
        return
    if dict(summary) != expected:
        result.fail("fl1_i2_public_projection_mismatch", "Public summary is not the canonical private-evidence projection.")
    result.details["fl1_i2_gate_closure"] = gates
    result.details["authority_boundary"] = "local_operator_evidence_not_ci_or_owner_acceptance"
