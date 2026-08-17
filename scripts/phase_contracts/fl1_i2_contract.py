"""Executable evidence contract for SCV2-FL1-I2 synthetic hardening."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.fl1_i2_evidence import (
    EvidenceStore,
    FailureBudget,
    FixedCutManifest,
    ItemDisposition,
    ManifestMember,
    OperationLedger,
    OperationState,
    TERMINAL_STATES,
    canonical_fingerprint,
)
from scripts.fl1_i2_runner import (
    CANONICAL_ENUMERATION_BUDGET,
    CANONICAL_POLICY,
    CONFIG_SCHEMA,
    CONTRACT_ID,
    ELIGIBLE_SUFFIXES,
    PUBLIC_SCHEMA,
)
from scripts.fl1_i2_validation_receipt import (
    CANONICAL_FOCUSED_TESTS,
    SameHeadValidationReceipt,
    validate_canonical_focused_command,
)
from scripts.fl1_i2_worker import WorkerOperation
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
REQUIRED_FOCUSED_TESTS = CANONICAL_FOCUSED_TESTS


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
            ManifestMember(
                str(item["item_id"]),
                str(item["private_name"]),
                item["object_identity"],
                item["change_identity"],
            )
            for item in payload["members"]
        )
        rebuilt = FixedCutManifest.build(
            run_id=str(payload["run_id"]),
            source_scope_fingerprint=str(payload["source_scope_fingerprint"]),
            directory_observation=payload["directory_observation"],
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
        result = run_trusted_git_text(
            repo_root,
            ("merge-base", "--is-ancestor", ancestor, "HEAD"),
            git=git,
        )
        if result.returncode:
            raise FL1I2ContractError("fl1_i2_planning_ancestry_invalid")
    return value("rev-parse", "HEAD^{commit}"), value("rev-parse", "HEAD^{tree}"), git.fingerprint


def _result_envelope(record: Mapping[str, Any]) -> Mapping[str, Any]:
    envelope = record.get("payload")
    if not isinstance(envelope, Mapping):
        raise FL1I2ContractError("fl1_i2_worker_result_missing")
    if envelope.get("started_persisted") is not True or envelope.get("exit_confirmed") is not True:
        raise FL1I2ContractError("fl1_i2_worker_closure_incomplete")
    result = envelope.get("result")
    if not isinstance(result, Mapping):
        raise FL1I2ContractError("fl1_i2_worker_result_missing")
    return result


def _validate_listing(manifest: FixedCutManifest, record: Mapping[str, Any]) -> None:
    if record.get("status") != OperationState.COMPLETED.value:
        raise FL1I2ContractError("fl1_i2_listing_incomplete")
    result = _result_envelope(record)
    if result.get("directory_observation") != manifest.directory_observation:
        raise FL1I2ContractError("fl1_i2_directory_observation_mismatch")
    raw_members = result.get("members")
    if not isinstance(raw_members, list):
        raise FL1I2ContractError("fl1_i2_listing_members_invalid")
    expected_members: list[dict[str, Any]] = []
    for raw in raw_members:
        if not isinstance(raw, Mapping):
            raise FL1I2ContractError("fl1_i2_listing_members_invalid")
        name = str(raw.get("name", ""))
        if Path(name).suffix.casefold() not in ELIGIBLE_SUFFIXES:
            continue
        item_id = canonical_fingerprint(
            {
                "scope": manifest.source_scope_fingerprint,
                "name": name,
                "object_identity": raw.get("object_identity"),
                "change_identity": raw.get("change_identity"),
            }
        )
        expected_members.append(
            {
                "item_id": item_id,
                "private_name": name,
                "object_identity": raw.get("object_identity"),
                "change_identity": raw.get("change_identity"),
            }
        )
    if sorted(expected_members, key=lambda item: item["item_id"]) != [member.to_private_dict() for member in manifest.members]:
        raise FL1I2ContractError("fl1_i2_manifest_listing_mapping_invalid")


def _expected_disposition(media: Mapping[str, Any]) -> ItemDisposition:
    if media.get("valid") is True and media.get("disposition") == "structure_valid":
        return ItemDisposition.CONTENT_VERIFIED
    if media.get("valid") is False and media.get("disposition") == "corrupt_media":
        return ItemDisposition.CORRUPT_MEDIA
    if media.get("valid") is False and media.get("disposition") == "unsupported":
        return ItemDisposition.UNSUPPORTED
    raise FL1I2ContractError("fl1_i2_media_disposition_invalid")


def _validate_member_result(
    *,
    member: ManifestMember,
    record: Mapping[str, Any],
    policy_fingerprint: str,
    disposition: ItemDisposition,
) -> None:
    if record.get("status") != OperationState.COMPLETED.value:
        raise FL1I2ContractError("fl1_i2_member_operation_incomplete")
    result = _result_envelope(record)
    observations = (
        result.get("opened_observation"),
        result.get("pre_read_observation"),
        result.get("post_read_observation"),
    )
    if any(not isinstance(value, Mapping) for value in observations) or len({canonical_fingerprint(value) for value in observations}) != 1:
        raise FL1I2ContractError("fl1_i2_handle_observation_drift")
    observation = observations[0]
    if observation.get("object_identity") != member.object_identity or observation.get("change_identity") != member.change_identity:
        raise FL1I2ContractError("fl1_i2_manifest_identity_binding_invalid")
    if result.get("object_identity") != member.object_identity or result.get("change_identity") != member.change_identity:
        raise FL1I2ContractError("fl1_i2_manifest_identity_binding_invalid")
    if result.get("policy_fingerprint") != policy_fingerprint or result.get("policy_version") != CANONICAL_POLICY["policy_version"]:
        raise FL1I2ContractError("fl1_i2_policy_result_binding_invalid")
    expected_bytes = int(member.change_identity["size"])
    if (
        result.get("byte_count") != expected_bytes
        or result.get("byte_count") != record.get("bytes_consumed")
        or record.get("bytes_reserved") != max(1, expected_bytes)
    ):
        raise FL1I2ContractError("fl1_i2_member_bytes_binding_invalid")
    content_fingerprint = result.get("content_fingerprint")
    if not isinstance(content_fingerprint, str) or len(content_fingerprint) != 64 or any(value not in "0123456789abcdef" for value in content_fingerprint):
        raise FL1I2ContractError("fl1_i2_content_fingerprint_invalid")
    media = result.get("media")
    if not isinstance(media, Mapping) or media.get("bytes_examined") != expected_bytes:
        raise FL1I2ContractError("fl1_i2_media_result_binding_invalid")
    if _expected_disposition(media) is not disposition:
        raise FL1I2ContractError("fl1_i2_disposition_result_mismatch")


def _validate_run_budget(ledger: OperationLedger, budget: FailureBudget) -> None:
    consumed = 0
    active: dict[str, int] = {}
    for event in ledger.events:
        if event.state is OperationState.INTENT:
            if consumed >= budget.max_bytes or event.bytes_reserved > budget.max_bytes - consumed - sum(active.values()):
                raise FL1I2ContractError("fl1_i2_run_byte_budget_invalid")
            active[event.operation_id] = event.bytes_reserved
        elif event.state in TERMINAL_STATES:
            reserved = active.pop(event.operation_id, None)
            if reserved is None or event.bytes_consumed > reserved:
                raise FL1I2ContractError("fl1_i2_run_byte_budget_invalid")
            consumed += event.bytes_consumed
    if active or consumed != ledger.consumed_bytes or consumed > budget.max_bytes:
        raise FL1I2ContractError("fl1_i2_run_byte_budget_invalid")


def _derive_gate_closure(
    *,
    config: Mapping[str, Any],
    manifest: FixedCutManifest,
    ledger: OperationLedger,
    workers: Mapping[str, Any],
    receipt: SameHeadValidationReceipt,
) -> dict[str, bool]:
    validate_canonical_focused_command(receipt.command_argv, Path(sys.executable))
    python_path = Path(sys.executable).resolve(strict=True)
    if receipt.python_executable_fingerprint != hashlib.sha256(python_path.read_bytes()).hexdigest():
        raise FL1I2ContractError("fl1_i2_python_identity_mismatch")
    expected_workers = ledger.to_worker_projection()
    if dict(workers) != expected_workers:
        raise FL1I2ContractError("fl1_i2_worker_ledger_reconciliation_failed")
    records = workers.get("records")
    if not isinstance(records, list):
        raise FL1I2ContractError("fl1_i2_worker_evidence_missing")
    listing = [record for record in records if record.get("kind") == WorkerOperation.LIST_DIRECTORY.value]
    content = [record for record in records if record.get("kind") == WorkerOperation.COMBINED_CONTENT.value]
    if len(listing) != 1 or len(content) != len(manifest.members) or len(records) != 1 + len(manifest.members):
        raise FL1I2ContractError("fl1_i2_operation_mapping_incomplete")
    _validate_listing(manifest, listing[0])
    content_by_item: dict[str, Mapping[str, Any]] = {}
    for record in content:
        item_id = str(record.get("item_id", ""))
        if item_id in content_by_item:
            raise FL1I2ContractError("fl1_i2_operation_mapping_conflict")
        content_by_item[item_id] = record
    if set(content_by_item) != {member.item_id for member in manifest.members}:
        raise FL1I2ContractError("fl1_i2_operation_mapping_incomplete")
    if set(ledger.item_dispositions) != set(content_by_item):
        raise FL1I2ContractError("fl1_i2_disposition_mapping_incomplete")
    policy_fingerprint = canonical_fingerprint(config["policy"])
    for member in manifest.members:
        _validate_member_result(
            member=member,
            record=content_by_item[member.item_id],
            policy_fingerprint=policy_fingerprint,
            disposition=ledger.item_dispositions[member.item_id],
        )
    budget = FailureBudget(**config["budget"])
    _validate_run_budget(ledger, budget)
    if ledger.failure_count or any(event.state in {OperationState.INTERRUPTED, OperationState.RECOVERED, OperationState.FAILED} for event in ledger.events):
        raise FL1I2ContractError("fl1_i2_operation_errors_present")
    return {str(finding): True for finding in GATE_FINDINGS}


def derive_canonical_public_projection(
    *,
    repo_root: Path,
    evidence_paths: FL1I2EvidencePaths,
) -> tuple[dict[str, Any], dict[str, bool]]:
    evidence = _load(evidence_paths)
    config = evidence["config"]
    if config.get("schema_version") != CONFIG_SCHEMA or config.get("mode") != "synthetic_new_temp_fixture":
        raise FL1I2ContractError("fl1_i2_config_invalid")
    if config.get("policy") != CANONICAL_POLICY or config.get("enumeration_budget") != CANONICAL_ENUMERATION_BUDGET:
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
    gates = _derive_gate_closure(
        config=config,
        manifest=manifest,
        ledger=ledger,
        workers=workers,
        receipt=receipt,
    )
    item_counts = {"manifest": len(manifest.members)}
    for disposition in ItemDisposition:
        item_counts[disposition.value] = sum(value is disposition for value in ledger.item_dispositions.values())
    budget = FailureBudget(**config["budget"])
    summary = {
        "schema_version": PUBLIC_SCHEMA,
        "contract_id": CONTRACT_ID,
        "status": "synthetic_implementation_evidence_complete",
        "run_token": hashlib.sha256(str(config["run_id"]).encode("utf-8")).hexdigest()[:24],
        "item_counts": item_counts,
        "operation_counts": {
            "total": ledger.operation_count,
            "completed": sum(event.state is OperationState.COMPLETED for event in ledger.events),
            "failed": ledger.failure_count,
            "interrupted": sum(event.state is OperationState.INTERRUPTED for event in ledger.events),
            "recovered": sum(event.state is OperationState.RECOVERED for event in ledger.events),
        },
        "run_budget": {
            "maximum_bytes": budget.max_bytes,
            "consumed_bytes": ledger.consumed_bytes,
            "remaining_bytes": ledger.remaining_bytes(budget),
        },
        "evidence_bindings": bindings,
        "authorities": dict(authorities),
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "machine_verifiable_ci": False,
        "trust_level": "local_operator_evidence",
        "public_redaction": {
            "passed": True,
            "paths_redacted": True,
            "filenames_redacted": True,
            "object_ids_redacted": True,
            "content_hashes_redacted": True,
        },
    }
    return summary, gates


def check_fl1_i2_contract(
    contract: Any,
    summary: Mapping[str, Any],
    result: Any,
    *,
    repository_context: Any,
) -> None:
    del contract
    evidence_paths = getattr(repository_context, "fl1_i2_evidence", None) if repository_context is not None else None
    if not isinstance(evidence_paths, FL1I2EvidencePaths):
        result.fail("fl1_i2_private_evidence_required", "Exact confined FL1-I2 private evidence is required.")
        return
    try:
        expected, gates = derive_canonical_public_projection(
            repo_root=repository_context.repo_root,
            evidence_paths=evidence_paths,
        )
    except Exception as exc:
        result.fail(str(exc), "FL1-I2 evidence re-derivation failed.")
        return
    if dict(summary) != expected:
        result.fail("fl1_i2_public_projection_mismatch", "Public summary is not the canonical private-evidence projection.")
    result.details["fl1_i2_gate_closure"] = gates
    result.details["authority_boundary"] = "local_operator_evidence_not_ci_or_owner_acceptance"
