"""Executable evidence contract for SCV2-FL1-I2 synthetic hardening."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.fl1_i2_evidence import (
    EvidenceStore,
    EvidenceError,
    FailureBudget,
    FixedCutManifest,
    ItemDisposition,
    ManifestDirectory,
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
    EXPECTED_FALSE_AUTHORITIES,
    PUBLIC_SCHEMA,
)
from scripts.fl1_i2_validation_receipt import (
    CANONICAL_FOCUSED_TESTS,
    SameHeadValidationReceipt,
    execution_environment_policy_fingerprint,
    validate_canonical_focused_command,
)
from scripts.fl1_i2_worker import WorkerOperation
from backend.app.services.source_ingestion_gate import SourceIngestionGate, SourceKind
from backend.app.services.source_safety import HandleObservation, SourceSafetyPolicy
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
        expected = {
            "schema_version", "run_id", "source_scope_fingerprint", "directory_observation",
            "directories", "members", "snapshot_fingerprint", "manifest_fingerprint",
        }
        if set(payload) != expected or not isinstance(payload["directories"], list) or not isinstance(payload["members"], list):
            raise TypeError
        directories_list: list[ManifestDirectory] = []
        for item in payload["directories"]:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"component_chain", "observation", "parent_object_identity", "parent_change_identity"}
                or not isinstance(item["component_chain"], list)
                or not all(type(value) is str and value for value in item["component_chain"])
            ):
                raise TypeError
            observation = HandleObservation.from_private_dict(item["observation"])
            if not observation.is_directory or observation.reparse_point or not observation.no_follow or not observation.identity_bound:
                raise TypeError
            if item["parent_object_identity"] is not None:
                from backend.app.services.source_safety import FileChangeIdentity, FileObjectIdentity
                FileObjectIdentity.from_private_dict(item["parent_object_identity"])
                FileChangeIdentity.from_private_dict(item["parent_change_identity"])
            directories_list.append(ManifestDirectory(tuple(item["component_chain"]), item["observation"], item["parent_object_identity"], item["parent_change_identity"]))
        directories = tuple(directories_list)
        members_list: list[ManifestMember] = []
        from backend.app.services.source_safety import FileChangeIdentity, FileObjectIdentity
        for item in payload["members"]:
            expected_member_keys = {
                "item_id", "private_name", "object_identity", "change_identity", "component_chain",
                "parent_object_identity", "parent_change_identity", "attributes", "reparse_tag", "link_count",
            }
            if not isinstance(item, Mapping) or set(item) != expected_member_keys or not isinstance(item["component_chain"], list):
                raise TypeError
            FileObjectIdentity.from_private_dict(item["object_identity"])
            FileChangeIdentity.from_private_dict(item["change_identity"])
            FileObjectIdentity.from_private_dict(item["parent_object_identity"])
            FileChangeIdentity.from_private_dict(item["parent_change_identity"])
            if type(item["attributes"]) is not int or type(item["reparse_tag"]) is not int or type(item["link_count"]) is not int:
                raise TypeError
            members_list.append(ManifestMember(
                item["item_id"], item["private_name"], item["object_identity"], item["change_identity"],
                tuple(item["component_chain"]), item["parent_object_identity"], item["parent_change_identity"],
                item["attributes"], item["reparse_tag"], item["link_count"],
            ))
        members = tuple(members_list)
        rebuilt = FixedCutManifest.build(
            run_id=str(payload["run_id"]),
            source_scope_fingerprint=str(payload["source_scope_fingerprint"]),
            directory_observation=payload["directory_observation"],
            members=members,
            directories=directories,
            snapshot_fingerprint=str(payload["snapshot_fingerprint"]),
        )
    except (KeyError, TypeError, ValueError, EvidenceError) as exc:
        raise FL1I2ContractError("fl1_i2_manifest_invalid") from exc
    if rebuilt.to_private_dict() != dict(payload):
        raise FL1I2ContractError("fl1_i2_manifest_fingerprint_mismatch")
    by_chain = {directory.component_chain: directory for directory in rebuilt.directories}
    for directory in rebuilt.directories:
        if directory.component_chain == ():
            if directory.parent_object_identity is not None or directory.parent_change_identity is not None:
                raise FL1I2ContractError("fl1_i2_manifest_parent_binding_invalid")
            continue
        parent = by_chain.get(directory.component_chain[:-1])
        if parent is None or directory.parent_object_identity != parent.observation["object_identity"] or directory.parent_change_identity != parent.observation["change_identity"]:
            raise FL1I2ContractError("fl1_i2_manifest_parent_binding_invalid")
    for member in rebuilt.members:
        parent = by_chain.get(member.component_chain[:-1])
        if parent is None or member.parent_object_identity != parent.observation["object_identity"] or member.parent_change_identity != parent.observation["change_identity"]:
            raise FL1I2ContractError("fl1_i2_manifest_parent_binding_invalid")
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


def _snapshot_identity(result: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"directory_observation", "directories", "members", "enumeration_usage"}
    if set(result) != expected or not isinstance(result["directories"], list) or not isinstance(result["members"], list):
        raise FL1I2ContractError("fl1_i2_snapshot_invalid")
    usage = result["enumeration_usage"]
    usage_keys = {"directories", "entries", "pages", "metadata_bytes", "metadata_observations"}
    if not isinstance(usage, Mapping) or set(usage) != usage_keys or any(type(usage[key]) is not int or usage[key] < 0 for key in usage_keys):
        raise FL1I2ContractError("fl1_i2_snapshot_usage_invalid")
    return {
        "directory_observation": result["directory_observation"],
        "directories": result["directories"],
        "members": result["members"],
    }


def _validate_listing(manifest: FixedCutManifest, record: Mapping[str, Any], *, final: bool = False) -> None:
    if record.get("status") != OperationState.COMPLETED.value:
        raise FL1I2ContractError("fl1_i2_listing_incomplete")
    result = _result_envelope(record)
    identity = _snapshot_identity(result)
    if canonical_fingerprint(identity) != manifest.snapshot_fingerprint:
        raise FL1I2ContractError("fl1_i2_final_snapshot_drift" if final else "fl1_i2_initial_snapshot_mismatch")
    if result.get("directory_observation") != manifest.directory_observation:
        raise FL1I2ContractError("fl1_i2_directory_observation_mismatch")
    if final:
        return
    raw_directories = result.get("directories")
    if not isinstance(raw_directories, list) or sorted(raw_directories, key=lambda item: item["component_chain"]) != [
        directory.to_private_dict() for directory in manifest.directories
    ]:
        raise FL1I2ContractError("fl1_i2_manifest_directory_mapping_invalid")
    raw_members = result.get("members")
    if not isinstance(raw_members, list):
        raise FL1I2ContractError("fl1_i2_listing_members_invalid")
    expected_members: list[dict[str, Any]] = []
    for raw in raw_members:
        if not isinstance(raw, Mapping):
            raise FL1I2ContractError("fl1_i2_listing_members_invalid")
        expected_raw_keys = {
            "name", "member_type", "object_identity", "change_identity", "attributes", "reparse_tag",
            "link_count", "component_chain", "parent_object_identity", "parent_change_identity",
        }
        if set(raw) != expected_raw_keys or raw.get("member_type") != "file":
            raise FL1I2ContractError("fl1_i2_listing_members_invalid")
        name = str(raw.get("name", ""))
        if Path(name).suffix.casefold() not in ELIGIBLE_SUFFIXES:
            continue
        item_id = canonical_fingerprint(
            {
                "scope": manifest.source_scope_fingerprint,
                "component_chain": raw.get("component_chain"),
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
                "component_chain": raw.get("component_chain"),
                "parent_object_identity": raw.get("parent_object_identity"),
                "parent_change_identity": raw.get("parent_change_identity"),
                "attributes": raw.get("attributes"),
                "reparse_tag": raw.get("reparse_tag"),
                "link_count": raw.get("link_count"),
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
    policy: SourceSafetyPolicy,
    policy_fingerprint: str,
    worker_deadline_seconds: float,
    disposition: ItemDisposition,
) -> None:
    if record.get("status") != OperationState.COMPLETED.value:
        raise FL1I2ContractError("fl1_i2_member_operation_incomplete")
    result = _result_envelope(record)
    envelope = record.get("payload")
    if not isinstance(envelope, Mapping) or set(envelope) != {"result", "started_persisted", "exit_confirmed", "elapsed_ms"}:
        raise FL1I2ContractError("fl1_i2_worker_envelope_invalid")
    if type(envelope["elapsed_ms"]) is not int or envelope["elapsed_ms"] < 0 or envelope["elapsed_ms"] > worker_deadline_seconds * 1000:
        raise FL1I2ContractError("fl1_i2_worker_deadline_invalid")
    raw_observations = (
        result.get("opened_observation"),
        result.get("pre_read_observation"),
        result.get("post_read_observation"),
    )
    if any(not isinstance(value, Mapping) for value in raw_observations):
        raise FL1I2ContractError("fl1_i2_handle_observation_invalid")
    try:
        observations = tuple(HandleObservation.from_private_dict(value) for value in raw_observations)
    except ValueError as exc:
        raise FL1I2ContractError("fl1_i2_handle_observation_invalid") from exc
    if len(set(observations)) != 1:
        raise FL1I2ContractError("fl1_i2_handle_observation_drift")
    observation = observations[0]
    if observation.is_directory or observation.object_identity.to_private_dict() != member.object_identity or observation.change_identity.to_private_dict() != member.change_identity:
        raise FL1I2ContractError("fl1_i2_manifest_identity_binding_invalid")
    for observed in observations:
        decision = SourceIngestionGate.decide_observation(
            source_kind=SourceKind.PATH_SOURCE,
            observation=observed,
            policy=policy,
        )
        if not decision.allowed:
            raise FL1I2ContractError("fl1_i2_policy_observation_rejected")
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


def _validate_run_budget(
    ledger: OperationLedger,
    budget: FailureBudget,
    *,
    enumeration_budget: Mapping[str, Any],
    evidence_bytes: int,
) -> dict[str, int]:
    if ledger.budget_fingerprint != canonical_fingerprint(budget.to_dict()):
        raise FL1I2ContractError("fl1_i2_budget_fingerprint_mismatch")
    if ledger.operation_count > budget.max_operations:
        raise FL1I2ContractError("fl1_i2_operation_budget_invalid")
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
    counters = ledger.run_counters()
    limits = {
        "directories_discovered": budget.max_directories,
        "entries_discovered": budget.max_entries,
        "metadata_observations": budget.max_metadata_observations,
        "metadata_bytes": budget.max_metadata_bytes,
        "content_opens": budget.max_content_opens,
        "content_bytes": budget.max_bytes,
        "hash_operations": budget.max_hash_operations,
        "structure_validations": budget.max_structure_validations,
        "total_operations": budget.max_operations,
        "failed_operations": budget.max_failures,
        "interrupted_operations": budget.max_failures,
        "retry_operations": budget.max_retries,
        "maximum_concurrent_workers": budget.max_concurrent_workers,
        "external_cost_usd": 0,
    }
    if any(counters[key] > maximum for key, maximum in limits.items()):
        raise FL1I2ContractError("fl1_i2_run_counter_budget_invalid")
    pages = 0
    for record in ledger.committed_results.values():
        envelope = record.get("payload")
        result = envelope.get("result") if isinstance(envelope, Mapping) else None
        if record.get("kind") in {WorkerOperation.LIST_DIRECTORY.value, WorkerOperation.FINAL_SNAPSHOT.value} and isinstance(result, Mapping):
            usage = result.get("enumeration_usage")
            if not isinstance(usage, Mapping):
                raise FL1I2ContractError("fl1_i2_snapshot_usage_invalid")
            pages += int(usage.get("pages", -1))
    if (
        counters["entries_discovered"] > enumeration_budget["max_entries"]
        or counters["directories_discovered"] > enumeration_budget["max_directories"]
        or counters["metadata_bytes"] > enumeration_budget["max_metadata_bytes"]
        or pages > enumeration_budget["max_pages"]
    ):
        raise FL1I2ContractError("fl1_i2_enumeration_budget_invalid")
    timestamps = [event.timestamp_ns for event in ledger.events]
    if timestamps and max(timestamps) - ledger.run_started_at_ns > int(budget.max_run_seconds * 1_000_000_000):
        raise FL1I2ContractError("fl1_i2_run_deadline_invalid")
    if evidence_bytes < 0 or evidence_bytes > budget.max_evidence_bytes:
        raise FL1I2ContractError("fl1_i2_evidence_disk_budget_invalid")
    return counters


def _derive_gate_closure(
    *,
    config: Mapping[str, Any],
    manifest: FixedCutManifest,
    ledger: OperationLedger,
    workers: Mapping[str, Any],
    receipt: SameHeadValidationReceipt,
    evidence_bytes: int,
) -> dict[str, bool]:
    validate_canonical_focused_command(receipt.command_argv, Path(sys.executable))
    python_path = Path(sys.executable).resolve(strict=True)
    if receipt.python_executable_fingerprint != hashlib.sha256(python_path.read_bytes()).hexdigest():
        raise FL1I2ContractError("fl1_i2_python_identity_mismatch")
    if receipt.execution_environment_policy_fingerprint != execution_environment_policy_fingerprint():
        raise FL1I2ContractError("fl1_i2_execution_environment_policy_invalid")
    expected_workers = ledger.to_worker_projection()
    if dict(workers) != expected_workers:
        raise FL1I2ContractError("fl1_i2_worker_ledger_reconciliation_failed")
    records = workers.get("records")
    if not isinstance(records, list):
        raise FL1I2ContractError("fl1_i2_worker_evidence_missing")
    budget = FailureBudget(**config["budget"])
    for record in records:
        if not isinstance(record, Mapping):
            raise FL1I2ContractError("fl1_i2_worker_evidence_invalid")
        envelope = record.get("payload")
        if not isinstance(envelope, Mapping) or set(envelope) != {"result", "started_persisted", "exit_confirmed", "elapsed_ms"}:
            raise FL1I2ContractError("fl1_i2_worker_envelope_invalid")
        if type(envelope["elapsed_ms"]) is not int or not 0 <= envelope["elapsed_ms"] <= budget.worker_deadline_seconds * 1000:
            raise FL1I2ContractError("fl1_i2_worker_deadline_invalid")
    listing = [record for record in records if record.get("kind") == WorkerOperation.LIST_DIRECTORY.value]
    final_snapshot = [record for record in records if record.get("kind") == WorkerOperation.FINAL_SNAPSHOT.value]
    content = [record for record in records if record.get("kind") == WorkerOperation.COMBINED_CONTENT.value]
    if len(listing) != 1 or len(final_snapshot) != 1 or len(content) != len(manifest.members) or len(records) != 2 + len(manifest.members):
        raise FL1I2ContractError("fl1_i2_operation_mapping_incomplete")
    _validate_listing(manifest, listing[0])
    _validate_listing(manifest, final_snapshot[0], final=True)
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
    try:
        policy = SourceSafetyPolicy.from_trusted_config(config["policy"])
    except ValueError as exc:
        raise FL1I2ContractError("fl1_i2_policy_drift") from exc
    for member in manifest.members:
        _validate_member_result(
            member=member,
            record=content_by_item[member.item_id],
            policy=policy,
            policy_fingerprint=policy_fingerprint,
            worker_deadline_seconds=budget.worker_deadline_seconds,
            disposition=ledger.item_dispositions[member.item_id],
        )
    counters = _validate_run_budget(
        ledger,
        budget,
        enumeration_budget=config["enumeration_budget"],
        evidence_bytes=evidence_bytes,
    )
    if counters["content_opens"] != len(manifest.members) or counters["hash_operations"] != len(manifest.members) or counters["structure_validations"] != len(manifest.members):
        raise FL1I2ContractError("fl1_i2_content_operation_counter_invalid")
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
    config_keys = {
        "schema_version", "run_id", "mode", "source_root", "evidence_root", "policy",
        "enumeration_budget", "budget", "authorities",
    }
    if set(config) != config_keys or config.get("schema_version") != CONFIG_SCHEMA or config.get("mode") != "synthetic_new_temp_fixture":
        raise FL1I2ContractError("fl1_i2_config_invalid")
    if any(type(config[key]) is not str or not config[key] for key in ("run_id", "source_root", "evidence_root")):
        raise FL1I2ContractError("fl1_i2_config_invalid")
    if config.get("policy") != CANONICAL_POLICY or config.get("enumeration_budget") != CANONICAL_ENUMERATION_BUDGET:
        raise FL1I2ContractError("fl1_i2_policy_drift")
    authorities = config.get("authorities")
    if (
        not isinstance(authorities, Mapping)
        or set(authorities) != set(EXPECTED_FALSE_AUTHORITIES)
        or any(type(authorities[key]) is not bool or authorities[key] is not False for key in EXPECTED_FALSE_AUTHORITIES)
    ):
        raise FL1I2ContractError("fl1_i2_authority_escalation")
    try:
        budget = FailureBudget(**config["budget"])
    except (TypeError, ValueError, EvidenceError) as exc:
        raise FL1I2ContractError("fl1_i2_budget_invalid") from exc
    if config["budget"] != budget.to_dict():
        raise FL1I2ContractError("fl1_i2_budget_invalid")
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
        evidence_bytes=sum(
            len(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
            for payload in (config, evidence["manifest"], evidence["ledger"], workers)
        ),
    )
    item_counts = {"manifest": len(manifest.members)}
    for disposition in ItemDisposition:
        item_counts[disposition.value] = sum(value is disposition for value in ledger.item_dispositions.values())
    run_counters = ledger.run_counters()
    evidence_bytes = sum(
        len(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
        for payload in (config, evidence["manifest"], evidence["ledger"], workers)
    )
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
            "evidence_disk_bytes": evidence_bytes,
            "counters": run_counters,
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
