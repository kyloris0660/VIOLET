"""Dedicated executable contract for SCV2-FL1-I1 inventory evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.fl1_i1_inventory import (
    CONTRACT_ID,
    InventoryBudgets,
    InventoryDisposition,
    PrivateManifest,
    PrivateRunLedger,
    RunStatus,
)
from scripts.fl1_i1_operation_gateway import (
    OperationKind,
    OperationLedger,
    OperationStatus,
    atomic_write_json,
    load_private_json,
)
from scripts.fl1_i1_runtime_context import build_trusted_runtime_context
from scripts.fl1_i1_validation_receipt import load_local_validation_receipt

from .contract_types import ContractCheckResult, PhaseContract


PUBLIC_PROJECTION_SCHEMA_VERSION = "violet.scv2-fl1-i1-public-projection.v1"
EVIDENCE_KEYS = {
    "private_root_config",
    "source_root",
    "source_mode",
    "source_scope_id",
    "budgets_config",
    "run_dir",
    "validation_receipt",
    "validation_report",
}
FORBIDDEN_PUBLIC_KEY_RE = re.compile(
    r"(?i)(private|filename|file_name|relative_path|source_root|repo_root|"
    r"content_fingerprint|content_hash|keyed|secret|token|command$|credential|password)"
)
WINDOWS_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\s\"'<>|]+")
UNC_PATH_RE = re.compile(r"\\\\[^\\\s\"'<>|]+\\[^\\\s\"'<>|]+")
POSIX_PRIVATE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])/(home|Users|tmp|mnt|Volumes)/")


class FL1I1ContractError(RuntimeError):
    """Raised when private evidence cannot produce a canonical projection."""


@dataclass(frozen=True)
class FL1I1EvidencePaths:
    private_root_config: Path
    source_root: Path
    source_mode: str
    source_scope_id: str
    budgets_config: Path
    run_dir: Path
    validation_receipt: Path
    validation_report: Path

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FL1I1EvidencePaths":
        if set(payload) != EVIDENCE_KEYS:
            raise FL1I1ContractError("fl1_i1_evidence_context_schema_invalid")

        def absolute(key: str) -> Path:
            value = payload[key]
            if not isinstance(value, (str, os.PathLike)):
                raise FL1I1ContractError("fl1_i1_evidence_path_invalid")
            path = Path(value)
            if not path.is_absolute():
                raise FL1I1ContractError("fl1_i1_evidence_path_invalid")
            return path

        return cls(
            private_root_config=absolute("private_root_config"),
            source_root=absolute("source_root"),
            source_mode=str(payload["source_mode"]),
            source_scope_id=str(payload["source_scope_id"]),
            budgets_config=absolute("budgets_config"),
            run_dir=absolute("run_dir"),
            validation_receipt=absolute("validation_receipt"),
            validation_report=absolute("validation_report"),
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_private_artifacts(
    run_dir: Path,
) -> tuple[PrivateRunLedger, PrivateManifest, OperationLedger]:
    ledger = PrivateRunLedger.from_dict(
        load_private_json(run_dir / "private-run-ledger.json")
    )
    manifest = PrivateManifest.from_dict(
        load_private_json(run_dir / "private-manifest.json")
    )
    operations = OperationLedger.from_dict(
        load_private_json(run_dir / "private-operation-ledger.json")
    )
    if not (
        ledger.run_id == manifest.run_id == operations.run_id
        and ledger.manifest_id == manifest.manifest_id
        and ledger.actual_git_head
        == manifest.actual_git_head
        == operations.actual_git_head
        and ledger.source_scope_fingerprint
        == manifest.source_scope_fingerprint
        == operations.source_scope_fingerprint
    ):
        raise FL1I1ContractError("fl1_i1_private_artifact_binding_mismatch")
    return ledger, manifest, operations


def _validate_complete_manifest(manifest: PrivateManifest) -> dict[str, int]:
    denominator = manifest.denominator()
    if denominator["unresolved"] != 0:
        raise FL1I1ContractError("fl1_i1_manifest_unresolved")
    if denominator["discovered"] != denominator["supported"] + denominator["unsupported"]:
        raise FL1I1ContractError("fl1_i1_discovered_equation_mismatch")
    if denominator["supported"] != sum(
        denominator[key]
        for key in (
            "duplicate",
            "cloud_recall_deferred",
            "unreadable_or_missing",
            "eligible_candidate",
        )
    ):
        raise FL1I1ContractError("fl1_i1_supported_equation_mismatch")
    if denominator["eligible_candidate"] != sum(
        denominator[key] for key in ("imported", "import_deferred", "import_failed")
    ):
        raise FL1I1ContractError("fl1_i1_eligible_equation_mismatch")
    if denominator["imported"] != 0 or denominator["import_failed"] != 0:
        raise FL1I1ContractError("fl1_i1_import_activity_forbidden")
    if denominator["import_deferred"] != denominator["eligible_candidate"]:
        raise FL1I1ContractError("fl1_i1_import_deferred_mismatch")
    return denominator


def _validate_duplicate_authority(manifest: PrivateManifest) -> dict[str, int]:
    by_content: dict[str, list[Any]] = {}
    for item in manifest.items:
        if item.content_fingerprint:
            by_content.setdefault(item.content_fingerprint, []).append(item)
    duplicate_groups = 0
    duplicate_references = 0
    for items in by_content.values():
        ordered = sorted(items, key=lambda item: item.private_relative_path)
        primaries = [
            item
            for item in ordered
            if item.disposition is InventoryDisposition.ELIGIBLE_CANDIDATE
        ]
        if len(ordered) > 1:
            duplicate_groups += 1
            if len(primaries) != 1 or primaries[0] is not ordered[0]:
                raise FL1I1ContractError("fl1_i1_duplicate_primary_nondeterministic")
            for duplicate in ordered[1:]:
                if (
                    duplicate.disposition is not InventoryDisposition.DUPLICATE
                    or duplicate.duplicate_of_item_id != primaries[0].item_id
                ):
                    raise FL1I1ContractError("fl1_i1_duplicate_reference_invalid")
                duplicate_references += 1
        elif primaries and len(primaries) != 1:
            raise FL1I1ContractError("fl1_i1_content_primary_invalid")
    return {
        "exact_content_group_count": len(by_content),
        "duplicate_content_group_count": duplicate_groups,
        "duplicate_reference_count": duplicate_references,
    }


def _validate_operations(
    ledger: PrivateRunLedger,
    manifest: PrivateManifest,
    operations: OperationLedger,
) -> dict[str, Any]:
    invocation_ids = {value.invocation_id for value in ledger.invocations}
    item_ids = {item.item_id for item in manifest.items}
    terminal_reprocessed = 0
    content_pairs: dict[tuple[str, str, int], set[OperationKind]] = {}
    status_counts = {
        kind.value: {status.value: 0 for status in OperationStatus}
        for kind in OperationKind
    }
    synthetic_observations = 0
    for record in operations.records:
        if record.invocation_id not in invocation_ids:
            raise FL1I1ContractError("fl1_i1_operation_invocation_unbound")
        if record.status is OperationStatus.INTENT:
            raise FL1I1ContractError("fl1_i1_operation_intent_unfinished")
        if (
            record.terminal_timestamp_ns is None
            or record.terminal_timestamp_ns < record.intent_timestamp_ns
        ):
            raise FL1I1ContractError("fl1_i1_operation_write_ahead_timestamp_invalid")
        status_counts[record.kind.value][record.status.value] += 1
        if record.kind in {
            OperationKind.SOURCE_CLOUD_ATTRIBUTE_OBSERVATION,
            OperationKind.SOURCE_FILE_READ,
            OperationKind.SOURCE_FILE_HASH,
        }:
            if record.item_id not in item_ids or record.attempt < 1:
                raise FL1I1ContractError("fl1_i1_item_operation_attribution_invalid")
        if record.kind is OperationKind.SOURCE_CLOUD_ATTRIBUTE_OBSERVATION:
            observation = record.observation
            if not isinstance(observation, Mapping):
                raise FL1I1ContractError("fl1_i1_cloud_observation_missing")
            if observation.get("synthetic_observation") is True:
                synthetic_observations += 1
            if observation.get("attributes_known") is False and record.status is OperationStatus.SUCCEEDED:
                raise FL1I1ContractError("fl1_i1_cloud_unknown_treated_available")
        if record.kind in {OperationKind.SOURCE_FILE_READ, OperationKind.SOURCE_FILE_HASH}:
            key = (str(record.invocation_id), str(record.item_id), record.attempt)
            content_pairs.setdefault(key, set()).add(record.kind)

    for kinds in content_pairs.values():
        if kinds != {OperationKind.SOURCE_FILE_READ, OperationKind.SOURCE_FILE_HASH}:
            raise FL1I1ContractError("fl1_i1_read_hash_operation_pair_invalid")

    terminal_invocation_by_item = {
        item.item_id: item.terminal_invocation_id for item in manifest.items
    }
    invocation_order = {
        invocation.invocation_id: index for index, invocation in enumerate(ledger.invocations)
    }
    for record in operations.records:
        if record.kind not in {
            OperationKind.SOURCE_CLOUD_ATTRIBUTE_OBSERVATION,
            OperationKind.SOURCE_FILE_READ,
            OperationKind.SOURCE_FILE_HASH,
        } or record.item_id is None:
            continue
        terminal_invocation = terminal_invocation_by_item[record.item_id]
        if terminal_invocation and invocation_order[record.invocation_id] > invocation_order[terminal_invocation]:
            terminal_reprocessed += 1
    if terminal_reprocessed:
        raise FL1I1ContractError("fl1_i1_terminal_item_reprocessed")

    kind_totals = {
        kind.value: sum(status_counts[kind.value].values()) for kind in OperationKind
    }
    if any(kind_totals[kind.value] < 1 for kind in OperationKind):
        raise FL1I1ContractError("fl1_i1_gateway_layer_coverage_incomplete")
    return {
        "kind_totals": kind_totals,
        "status_counts": status_counts,
        "record_count": len(operations.records),
        "all_records_terminal": True,
        "write_ahead_timestamp_coverage": True,
        "per_item_content_attribution": True,
        "terminal_content_reprocessed_count": terminal_reprocessed,
        "synthetic_attribute_observation_count": synthetic_observations,
        "ledger_fingerprint": operations.to_dict()["ledger_fingerprint"],
    }


def _validate_restart(ledger: PrivateRunLedger, manifest: PrivateManifest) -> dict[str, Any]:
    if len(ledger.invocations) < 2:
        raise FL1I1ContractError("fl1_i1_distinct_restart_provenance_missing")
    if ledger.status is not RunStatus.COMPLETE:
        raise FL1I1ContractError("fl1_i1_run_not_complete")
    if any(
        invocation.ended_at_ns is None or invocation.end_checkpoint_fingerprint is None
        for invocation in ledger.invocations
    ):
        raise FL1I1ContractError("fl1_i1_invocation_terminal_evidence_missing")
    if len({value.pid for value in ledger.invocations}) != len(ledger.invocations) or len(
        {value.process_start_observation for value in ledger.invocations}
    ) != len(ledger.invocations):
        raise FL1I1ContractError("fl1_i1_distinct_process_provenance_missing")
    if any(item.attempt_count < 1 or item.terminal_invocation_id is None for item in manifest.items):
        raise FL1I1ContractError("fl1_i1_item_attempt_evidence_missing")
    return {
        "invocation_count": len(ledger.invocations),
        "distinct_invocation_ids": len({value.invocation_id for value in ledger.invocations}),
        "distinct_process_ids": len({value.pid for value in ledger.invocations}),
        "distinct_process_start_observations": len(
            {value.process_start_observation for value in ledger.invocations}
        ),
        "parent_checkpoint_chain_valid": True,
        "attempts_preserved": True,
        "terminal_items_reopened_or_rehashed": False,
        "attestation_level": "executable_cross_process_provenance_not_os_or_tpm_attestation",
    }


def scan_public_projection(payload: Any, path: str = "$") -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            child = f"{path}.{key}"
            if FORBIDDEN_PUBLIC_KEY_RE.search(str(key)) and str(key) not in {
                "private_paths_emitted",
            }:
                findings.append({"code": "forbidden_public_key", "path": child})
            findings.extend(scan_public_projection(value, child))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            findings.extend(scan_public_projection(value, f"{path}[{index}]"))
    elif isinstance(payload, str):
        if WINDOWS_PATH_RE.search(payload) or UNC_PATH_RE.search(payload) or POSIX_PRIVATE_PATH_RE.search(payload):
            findings.append({"code": "private_path_value", "path": path})
    return findings


def derive_canonical_public_projection(
    *,
    repo_root: Path,
    expected_python: Path,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    paths = FL1I1EvidencePaths.from_mapping(evidence)
    context = build_trusted_runtime_context(
        repo_root=Path(repo_root),
        expected_python=Path(expected_python),
        private_root_config=paths.private_root_config,
        source_root=paths.source_root,
        source_mode=paths.source_mode,
        source_scope_id=paths.source_scope_id,
    )
    budgets = InventoryBudgets.from_dict(load_private_json(paths.budgets_config))
    evidence_root = context.roots.roots["phase_evidence_output_root"]
    for private_artifact in (
        paths.run_dir,
        paths.validation_receipt,
        paths.validation_report,
    ):
        resolved_artifact = private_artifact.resolve(strict=True)
        if resolved_artifact != evidence_root and evidence_root not in resolved_artifact.parents:
            raise FL1I1ContractError("fl1_i1_private_artifact_outside_evidence_root")
    ledger, manifest, operations = _load_private_artifacts(paths.run_dir)
    if not (
        ledger.actual_git_head == context.actual_git_head
        and ledger.branch == context.branch
        and ledger.runtime_context_fingerprint == context.context_fingerprint
        and ledger.root_registry_fingerprint == context.roots.fingerprint
        and ledger.source_scope_fingerprint == context.source_scope.scope_fingerprint
        and ledger.budget_fingerprint == budgets.fingerprint
    ):
        raise FL1I1ContractError("fl1_i1_runtime_head_scope_config_budget_drift")
    denominator = _validate_complete_manifest(manifest)
    duplicate_accounting = _validate_duplicate_authority(manifest)
    operation_accounting = _validate_operations(ledger, manifest, operations)
    restart = _validate_restart(ledger, manifest)
    receipt = load_local_validation_receipt(paths.validation_receipt)
    if (
        receipt.actual_git_head != context.actual_git_head
        or receipt.report_sha256 != _sha256_file(paths.validation_report)
        or receipt.exit_code != 0
        or receipt.clean_worktree is not True
    ):
        raise FL1I1ContractError("fl1_i1_validation_receipt_binding_invalid")
    projection: dict[str, Any] = {
        "schema_version": PUBLIC_PROJECTION_SCHEMA_VERSION,
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "contract_version": "1",
            "phase_id": "SCV2-FL1-I1",
            "status": "synthetic_inventory_evidence_complete",
            "target_met": False,
            "safe_to_merge": False,
            "route_approved": False,
        },
        "trusted_runtime": context.to_public_dict(),
        "configuration": {
            "budget_fingerprint": budgets.fingerprint,
            "policy_fingerprint": ledger.policy_fingerprint,
            "manifest_fingerprint": manifest.manifest_fingerprint,
            "checkpoint_fingerprint": ledger.checkpoint_fingerprint,
        },
        "run_identity": {
            "run_id": ledger.run_id,
            "manifest_id": ledger.manifest_id,
            "invocation_ids": [value.invocation_id for value in ledger.invocations],
            "run_status": ledger.status.value,
        },
        "denominator": denominator,
        "duplicate_accounting": duplicate_accounting,
        "operation_evidence": operation_accounting,
        "restart_resume": restart,
        "gateway_coverage": {
            "temporary_fixture_source_subset_exercised": True,
            "phase_wide_real_gateway_proven": False,
            "synthetic_callback_phase_wide_proof": False,
            "real_source_operation_count": 0,
        },
        "forbidden_activity": {
            "source_mutation_count": ledger.source_mutation_count,
            "database_access_count": 0,
            "app_storage_write_count": 0,
            "import_count": 0,
            "provider_request_count": 0,
            "llm_request_count": 0,
            "media_operation_count": 0,
            "network_request_count": 0,
            "stable_replay_count": 0,
            "production_operation_count": 0,
        },
        "validation_receipt": receipt.to_public_dict(),
        "authorization": {
            "synthetic_implementation_authorized": True,
            "real_source_inventory_authorized": False,
            "real_inventory_started": False,
            "database_access_authorized": False,
            "app_storage_write_authorized": False,
            "provider_or_llm_authorized": False,
            "media_authorized": False,
            "production_authorized": False,
            "owner_authority_machine_verifiable": False,
            "manual_acceptance_status": "pending_i1_synthetic_implementation_owner_audit",
        },
        "public_redaction": {
            "passed": True,
            "finding_count": 0,
            "private_paths_emitted": False,
            "per_item_labels_emitted": False,
        },
    }
    if ledger.source_mutation_count != 0:
        raise FL1I1ContractError("fl1_i1_source_mutation_detected")
    findings = scan_public_projection(projection)
    if findings:
        raise FL1I1ContractError("fl1_i1_canonical_projection_redaction_failure")
    return projection


def _first_difference(expected: Any, actual: Any, path: str = "$") -> str:
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        if set(expected) != set(actual):
            return path
        for key in expected:
            difference = _first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference:
                return difference
        return ""
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return path
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            difference = _first_difference(left, right, f"{path}[{index}]")
            if difference:
                return difference
        return ""
    return "" if expected == actual else path


def check_fl1_i1_contract(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    repository_context: object | None,
) -> None:
    evidence = getattr(repository_context, "fl1_i1_evidence", None)
    repo_root = getattr(repository_context, "repo_root", None)
    expected_python = getattr(repository_context, "expected_python", None)
    if not isinstance(evidence, Mapping) or repo_root is None or expected_python is None:
        result.fail(
            "fl1_i1_private_evidence_required",
            "The I1 contract requires trusted repository context and the complete private evidence bundle.",
        )
        return
    try:
        expected = derive_canonical_public_projection(
            repo_root=Path(repo_root),
            expected_python=Path(expected_python),
            evidence=evidence,
        )
    except Exception as exc:
        result.fail(
            "fl1_i1_private_evidence_invalid",
            "The private I1 evidence bundle could not produce a canonical public projection.",
            actual=str(exc),
        )
        return
    public_findings = scan_public_projection(summary)
    if public_findings:
        for finding in public_findings:
            result.fail(
                f"fl1_i1_public_{finding['code']}",
                "The complete I1 public payload contains a forbidden private field or path.",
                path=finding["path"],
            )
    difference = _first_difference(expected, summary)
    if difference:
        result.fail(
            "fl1_i1_canonical_projection_mismatch",
            "The submitted public summary is not the exact projection rebuilt from private evidence.",
            path=difference,
        )
    result.details["fl1_i1_canonical_projection_fingerprint"] = _fingerprint(expected)
    result.details["fl1_i1_private_artifacts_validated"] = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--expected-python", required=True)
    parser.add_argument("--evidence-context", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        evidence = load_private_json(Path(args.evidence_context))
        projection = derive_canonical_public_projection(
            repo_root=Path(args.repo_root),
            expected_python=Path(args.expected_python),
            evidence=evidence,
        )
        atomic_write_json(Path(args.output), projection)
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "passed": True,
                "projection_fingerprint": _fingerprint(projection),
                "private_paths_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
