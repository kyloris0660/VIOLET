"""Executable evidence contract for the final synthetic SCV2-PX3 route."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Iterator, Mapping

from scripts.scv2_px1_validation_receipt import repository_identity_snapshot
from scripts.scv2_px3_validation_receipt import (
    EVIDENCE_ARTIFACT_NAMES,
    RECEIPT_NAME,
    canonical_fingerprint,
    canonical_json_bytes,
    evidence_bindings,
    validate_px3_evidence_carry_forward,
    validate_receipt_payload,
)

from .contract_types import ContractCheckResult, PhaseContract


JSON_ARTIFACT_BUDGETS = {
    "px1-summary.json": 16 * 1024 * 1024,
    "px2-summary.json": 16 * 1024 * 1024,
    "product-persistence-proof.json": 2 * 1024 * 1024,
    "operation-receipt.json": 256 * 1024,
    "public-summary.json": 20 * 1024 * 1024,
    RECEIPT_NAME: 256 * 1024,
}
DATABASE_NAMES = (
    "px1-first.sqlite3",
    "px1-reversed.sqlite3",
    "px2-source-concepts-1.sqlite3",
    "px2-source-concepts-2.sqlite3",
    "px3-product-integration.sqlite3",
)
WINDOWS_REPARSE_POINT = 0x400
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")


class Scv2Px3ContractError(RuntimeError):
    pass


class Scv2Px3EvidencePaths:
    def __init__(self, root: Path):
        self.root = Path(root)


def _lexically_confined_root(path: Path) -> Path:
    if not path.is_absolute():
        raise Scv2Px3ContractError("px3_evidence_root_not_absolute")
    lexical = Path(os.path.abspath(os.fspath(path)))
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        relative = lexical.relative_to(temp_root)
    except ValueError as exc:
        raise Scv2Px3ContractError("px3_evidence_root_not_task_temp") from exc
    cursor = temp_root
    for component in relative.parts:
        cursor /= component
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise Scv2Px3ContractError("px3_evidence_root_unavailable") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise Scv2Px3ContractError("px3_evidence_root_alias_or_type_invalid")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(temp_root)
    except (OSError, ValueError) as exc:
        raise Scv2Px3ContractError("px3_evidence_root_resolution_invalid") from exc
    if resolved != lexical:
        raise Scv2Px3ContractError("px3_evidence_root_alias_or_type_invalid")
    return resolved


def _read_bounded_canonical_json(root: Path, name: str) -> Any:
    if name not in JSON_ARTIFACT_BUDGETS or Path(name).name != name:
        raise Scv2Px3ContractError("px3_evidence_name_invalid")
    target = root / name
    try:
        metadata = os.lstat(target)
    except OSError as exc:
        raise Scv2Px3ContractError(f"px3_evidence_missing:{name}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
        or metadata.st_size > JSON_ARTIFACT_BUDGETS[name]
    ):
        raise Scv2Px3ContractError(f"px3_evidence_type_or_budget_invalid:{name}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
        try:
            opened = os.fstat(descriptor)
            if opened.st_size != metadata.st_size or (
                metadata.st_ino and opened.st_ino != metadata.st_ino
            ):
                raise Scv2Px3ContractError(f"px3_evidence_identity_drift:{name}")
            raw = os.read(descriptor, JSON_ARTIFACT_BUDGETS[name] + 1)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise Scv2Px3ContractError(f"px3_evidence_read_failed:{name}") from exc
    if len(raw) > JSON_ARTIFACT_BUDGETS[name] or b"\x00" in raw or raw.startswith(b"\xef\xbb\xbf"):
        raise Scv2Px3ContractError(f"px3_evidence_encoding_or_budget_invalid:{name}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Scv2Px3ContractError(f"px3_evidence_json_invalid:{name}") from exc
    if raw != canonical_json_bytes(payload) + b"\n":
        raise Scv2Px3ContractError(f"px3_evidence_not_canonical:{name}")
    return payload


def load_px3_evidence_artifacts(
    paths: Scv2Px3EvidencePaths, *, require_receipt: bool = True
) -> dict[str, Any]:
    root = _lexically_confined_root(paths.root)
    names = set(EVIDENCE_ARTIFACT_NAMES) | set(DATABASE_NAMES)
    if require_receipt:
        names.add(RECEIPT_NAME)
    try:
        actual = {entry.name for entry in os.scandir(root)}
    except OSError as exc:
        raise Scv2Px3ContractError("px3_evidence_directory_unreadable") from exc
    if actual != names:
        raise Scv2Px3ContractError("px3_evidence_fixed_member_set_invalid")
    json_names = list(EVIDENCE_ARTIFACT_NAMES)
    if require_receipt:
        json_names.append(RECEIPT_NAME)
    payloads = {name: _read_bounded_canonical_json(root, name) for name in json_names}
    for name in DATABASE_NAMES:
        metadata = os.lstat(root / name)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
            or metadata.st_size > 64 * 1024 * 1024
        ):
            raise Scv2Px3ContractError("px3_task_database_type_invalid")
    payloads["_root"] = root
    return payloads


@contextmanager
def _task_runtime_environment() -> Iterator[None]:
    keys = (
        "VIOLET_SKIP_DOTENV",
        "VIOLET_ENV",
        "POSTGRES_DB",
        "TEST_DATABASE_URL",
        "VIOLET_STORAGE_ROOT",
        "VIOLET_TEST_STORAGE_ROOT",
    )
    previous = {key: os.environ.get(key) for key in keys}
    with tempfile.TemporaryDirectory(prefix="violet-scv2-px3-contract-storage-") as storage:
        os.environ.update(
            {
                "VIOLET_SKIP_DOTENV": "1",
                "VIOLET_ENV": "test",
                "POSTGRES_DB": "scv2_px3_task_temp",
                "TEST_DATABASE_URL": "",
                "VIOLET_STORAGE_ROOT": storage,
                "VIOLET_TEST_STORAGE_ROOT": storage,
            }
        )
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Scv2Px3ContractError(f"px3_{label}_invalid")
    return value


def _validate_public_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    from backend.app.services.pixiv_metadata_clustering_service import (
        PX2_CANDIDATE_POLICY_VERSION,
        PX2_CONTEXT_POLICY_VERSION,
    )
    from backend.app.services.pixiv_metadata_projection_service import (
        assert_public_safe_projection,
    )
    from backend.app.services.pixiv_product_integration_service import (
        PX3_AUTHORITY_MAP,
        PX3_CONTRACT_ID,
        PX3_EXECUTED_STAGES,
        PX3_OPERATION_RECEIPT_SCHEMA,
        PX3_PERSISTENCE_SCHEMA,
        PX3_POLICY_VERSION,
        PX3_PUBLIC_SCHEMA,
    )
    from backend.app.services.source_concept_resolver_service import RESOLVER_VERSION

    unsigned = dict(summary)
    supplied = unsigned.pop("canonical_fingerprint", None)
    if supplied != canonical_fingerprint(unsigned):
        raise Scv2Px3ContractError("px3_public_fingerprint_invalid")
    if (
        summary.get("schema_version") != PX3_PUBLIC_SCHEMA
        or summary.get("contract_id") != PX3_CONTRACT_ID
        or summary.get("status")
        != "implementation_ready_for_owner_acceptance_and_controlled_canary"
        or tuple(summary.get("executed_stages", ())) != PX3_EXECUTED_STAGES
        or summary.get("resolver_version") != RESOLVER_VERSION
        or summary.get("context_policy_version") != PX2_CONTEXT_POLICY_VERSION
        or summary.get("candidate_policy_version") != PX2_CANDIDATE_POLICY_VERSION
        or summary.get("product_policy_version") != PX3_POLICY_VERSION
    ):
        raise Scv2Px3ContractError("px3_public_identity_invalid")
    assert_public_safe_projection(summary)
    for fingerprint in (
        "px1_input_fingerprint",
        "px2_business_projection_fingerprint",
        "product_result_fingerprint",
    ):
        if HEX64_RE.fullmatch(str(summary.get(fingerprint, ""))) is None:
            raise Scv2Px3ContractError("px3_input_or_product_fingerprint_invalid")
    counts = _mapping(summary.get("counts"), "counts")
    expected_counts = {
        "cluster_count": 20,
        "member_signal_count": 34,
        "candidate_disposition_count": 59,
        "ambiguity_record_count": 29,
        "must_link_count": 52,
        "cannot_link_count": 4,
        "deferred_nonblocking_count": 3,
    }
    if dict(counts) != expected_counts:
        raise Scv2Px3ContractError("px3_counts_invalid")
    clusters = summary.get("clusters")
    candidates = summary.get("candidate_dispositions")
    ambiguities = summary.get("ambiguity_records")
    if not all(isinstance(rows, list) for rows in (clusters, candidates, ambiguities)):
        raise Scv2Px3ContractError("px3_product_projection_invalid")
    if (
        len(clusters) != counts["cluster_count"]
        or len(candidates) != counts["candidate_disposition_count"]
        or len(ambiguities) != counts["ambiguity_record_count"]
        or [row["cluster_key"] for row in clusters] != sorted({row["cluster_key"] for row in clusters})
        or [row["pair_key"] for row in candidates] != sorted({row["pair_key"] for row in candidates})
        or [row["record_key"] for row in ambiguities] != sorted({row["record_key"] for row in ambiguities})
    ):
        raise Scv2Px3ContractError("px3_product_accounting_invalid")
    business = {
        "scope_key": summary.get("scope_key"),
        "source_mode": summary.get("source_mode"),
        "px1_input_fingerprint": summary.get("px1_input_fingerprint"),
        "px2_business_projection_fingerprint": summary.get("px2_business_projection_fingerprint"),
        "resolver_version": summary.get("resolver_version"),
        "context_policy_version": summary.get("context_policy_version"),
        "candidate_policy_version": summary.get("candidate_policy_version"),
        "product_policy_version": summary.get("product_policy_version"),
        "clusters": clusters,
        "candidate_dispositions": candidates,
        "ambiguity_records": ambiguities,
    }
    if canonical_fingerprint(business) != summary.get("product_result_fingerprint"):
        raise Scv2Px3ContractError("px3_product_result_fingerprint_invalid")
    if summary.get("run_key") != f"scv2-px3:{summary['product_result_fingerprint'][:32]}":
        raise Scv2Px3ContractError("px3_run_key_invalid")
    invariants = _mapping(summary.get("invariants"), "invariants")
    if invariants != {
        "all_input_bundles_accounted": True,
        "all_candidate_pairs_accounted": True,
        "unexplained_signal_loss": 0,
        "cannot_link_union_violation_count": 0,
        "deferred_union_violation_count": 0,
        "product_projection_complete": True,
    }:
        raise Scv2Px3ContractError("px3_invariants_invalid")
    proof = _mapping(summary.get("persistence_proof"), "persistence_proof")
    proof_unsigned = dict(proof)
    proof_fingerprint = proof_unsigned.pop("canonical_fingerprint", None)
    if (
        proof.get("schema_version") != PX3_PERSISTENCE_SCHEMA
        or proof_fingerprint != canonical_fingerprint(proof_unsigned)
        or proof.get("temporary_database_count") != 1
        or proof.get("dry_run_product_row_delta_count") != 0
        or proof.get("replay_row_delta_count") != 0
        or proof.get("product_run_count") != 1
        or proof.get("active_product_run_count") != 1
        or proof.get("temporary_persistence_idempotent") is not True
        or proof.get("query_projection_complete") is not True
        or proof.get("rollback_succeeded") is not True
        or proof.get("rollback_idempotent") is not True
        or proof.get("forbidden_truth_table_write_count") != 0
    ):
        raise Scv2Px3ContractError("px3_persistence_proof_invalid")
    operation = _mapping(summary.get("operation_receipt"), "operation_receipt")
    if (
        operation.get("schema_version") != PX3_OPERATION_RECEIPT_SCHEMA
        or operation.get("receipt_scope") != "repository_owned_cli_invocation"
        or operation.get("px1_input_generation_temporary_database_count") != 2
        or operation.get("px2_proof_temporary_database_count") != 2
        or operation.get("px3_product_temporary_database_count") != 1
        or any(value != 0 for key, value in operation.items() if key.endswith("_activity"))
    ):
        raise Scv2Px3ContractError("px3_operation_receipt_invalid")
    if summary.get("authorities") != PX3_AUTHORITY_MAP:
        raise Scv2Px3ContractError("px3_authority_map_invalid")
    required_route = {
        "px1_owner_accepted": True,
        "px1_merged": True,
        "px2_owner_accepted": True,
        "px2_merged": True,
        "px3_started": True,
        "px3_implementation_completed": True,
        "px3_target_met": True,
        "target_met": True,
        "px3_owner_accepted": False,
        "px3_safe_to_merge": False,
        "px3_merge_authorized": False,
    }
    if any(summary.get(key) is not value for key, value in required_route.items()):
        raise Scv2Px3ContractError("px3_route_boundary_invalid")
    return {
        "cluster_count": counts["cluster_count"],
        "candidate_disposition_count": counts["candidate_disposition_count"],
        "ambiguity_record_count": counts["ambiguity_record_count"],
        "product_result_fingerprint": summary["product_result_fingerprint"],
        "canonical_fingerprint": supplied,
    }


def check_scv2_px3_contract(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    repository_context: Any,
) -> None:
    if repository_context is None or repository_context.scv2_px3_evidence is None:
        result.fail("px3_private_evidence_required", "SCV2-PX3 requires confined fixed-name evidence.")
        return
    if repository_context.expected_python is None:
        result.fail("px3_expected_python_required", "SCV2-PX3 requires approved repository Python.")
        return
    try:
        approved_python = repository_context.expected_python.resolve(strict=True)
        if Path(sys.executable).resolve(strict=True) != approved_python:
            raise Scv2Px3ContractError("px3_checker_python_identity_mismatch")
        evidence = load_px3_evidence_artifacts(repository_context.scv2_px3_evidence)
        if dict(summary) != evidence["public-summary.json"]:
            raise Scv2Px3ContractError("px3_caller_summary_evidence_mismatch")
        if evidence["product-persistence-proof.json"] != summary.get("persistence_proof"):
            raise Scv2Px3ContractError("px3_persistence_artifact_mismatch")
        if evidence["operation-receipt.json"] != summary.get("operation_receipt"):
            raise Scv2Px3ContractError("px3_operation_artifact_mismatch")
        with _task_runtime_environment():
            details = _validate_public_summary(summary)
            from backend.app.services.pixiv_product_integration_service import (
                run_repository_synthetic_pixiv_product_integration,
            )
            with tempfile.TemporaryDirectory(prefix="violet-scv2-px3-contract-") as workspace:
                regenerated_px1, regenerated_px2, regenerated_px3 = (
                    run_repository_synthetic_pixiv_product_integration(workspace=Path(workspace))
                )
        if regenerated_px1 != evidence["px1-summary.json"]:
            raise Scv2Px3ContractError("px3_repository_px1_projection_mismatch")
        if regenerated_px2 != evidence["px2-summary.json"]:
            raise Scv2Px3ContractError("px3_repository_px2_projection_mismatch")
        if regenerated_px3 != dict(summary):
            raise Scv2Px3ContractError("px3_independent_replay_projection_mismatch")
        repository = repository_identity_snapshot(
            repository_context.repo_root,
            python_executable=approved_python,
            require_clean=True,
        )
        bindings = evidence_bindings({name: evidence[name] for name in EVIDENCE_ARTIFACT_NAMES})
        receipt = _mapping(evidence[RECEIPT_NAME], "validation_receipt")
        evidence_head = str(receipt.get("git_head", ""))
        evidence_tree = str(receipt.get("git_tree", ""))
        carry_forward = validate_px3_evidence_carry_forward(
            repository_context.repo_root,
            evidence_head=evidence_head,
            evidence_tree=evidence_tree,
        )
        receipt_repository = dict(repository)
        receipt_repository.update({"git_head": evidence_head, "git_tree": evidence_tree})
        validate_receipt_payload(
            receipt,
            approved_python=approved_python,
            expected_repository=receipt_repository,
            expected_bindings=bindings,
        )
    except Exception as exc:
        result.fail(str(exc), "SCV2-PX3 evidence re-derivation failed.")
        return
    result.details["scv2_px3_projection"] = details
    result.details["scv2_px3_repository_binding"] = {
        "git_head": repository["git_head"],
        "git_tree": repository["git_tree"],
        "implementation_evidence_head": evidence_head,
        "implementation_evidence_tree": evidence_tree,
        "docs_only_carry_forward_paths": carry_forward["changed_paths"],
        "clean": True,
    }
    result.details["scv2_px3_evidence_bindings"] = bindings
    result.details["authority_boundary"] = (
        "synthetic_local_operator_evidence_only_owner_acceptance_controlled_canary_and_merge_remain_false"
    )
