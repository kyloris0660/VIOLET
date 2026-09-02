"""Executable evidence contract for deterministic synthetic SCV2-PX2."""

from __future__ import annotations

from collections import Counter
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
from scripts.scv2_px2_validation_receipt import (
    EVIDENCE_ARTIFACT_NAMES,
    RECEIPT_NAME,
    evidence_bindings,
    validate_px2_evidence_carry_forward,
    validate_receipt_payload,
)

from .contract_types import ContractCheckResult, PhaseContract


JSON_ARTIFACT_BUDGETS = {
    "px1-consumer-summary.json": 12 * 1024 * 1024,
    "candidate-dispositions.json": 4 * 1024 * 1024,
    "ambiguous-ledger.json": 2 * 1024 * 1024,
    "persistence-proof.json": 2 * 1024 * 1024,
    "operation-receipt.json": 256 * 1024,
    "public-summary.json": 16 * 1024 * 1024,
    RECEIPT_NAME: 256 * 1024,
}
DATABASE_NAMES = (
    "px1-first.sqlite3",
    "px1-reversed.sqlite3",
    "px2-source-concepts-1.sqlite3",
    "px2-source-concepts-2.sqlite3",
)
ALLOWED_EVIDENCE_MEMBERS = (
    set(EVIDENCE_ARTIFACT_NAMES) | {RECEIPT_NAME, *DATABASE_NAMES}
)
WINDOWS_REPARSE_POINT = 0x400
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_SOURCE_STATES = {
    "complete": 8,
    "conflict": 1,
    "page_mismatch": 1,
    "retryable": 1,
    "terminal": 1,
    "unsupported": 2,
}
EXPECTED_INVARIANTS = {
    "all_input_bundles_accounted": True,
    "all_candidate_pairs_accounted": True,
    "unexplained_signal_loss": 0,
    "multi_stable_creator_id_component_count": 0,
    "name_only_artist_union_count": 0,
    "cannot_link_union_violation_count": 0,
    "deferred_union_violation_count": 0,
    "cross_role_union_violation_count": 0,
    "deterministic_replay": True,
    "temporary_persistence_idempotent": True,
    "existing_db_or_app_storage_activity": 0,
    "provider_network_activity": 0,
    "llm_activity": 0,
    "production_activity": 0,
}


class Scv2Px2ContractError(RuntimeError):
    pass


class Scv2Px2EvidencePaths:
    """Caller path kept unresolved until local-temp confinement succeeds."""

    def __init__(self, root: Path):
        self.root = Path(root)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_fingerprint(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _lexically_confined_root(path: Path) -> Path:
    if not path.is_absolute():
        raise Scv2Px2ContractError("px2_evidence_root_not_absolute")
    lexical = Path(os.path.abspath(os.fspath(path)))
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        relative = lexical.relative_to(temp_root)
    except ValueError as exc:
        raise Scv2Px2ContractError("px2_evidence_root_not_task_temp") from exc
    cursor = temp_root
    for component in relative.parts:
        cursor /= component
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise Scv2Px2ContractError("px2_evidence_root_unavailable") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise Scv2Px2ContractError("px2_evidence_root_alias_or_type_invalid")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(temp_root)
    except (OSError, ValueError) as exc:
        raise Scv2Px2ContractError("px2_evidence_root_resolution_invalid") from exc
    if resolved != lexical:
        raise Scv2Px2ContractError("px2_evidence_root_alias_or_type_invalid")
    return resolved


def _read_bounded_canonical_json(root: Path, name: str) -> Any:
    if name not in JSON_ARTIFACT_BUDGETS or Path(name).name != name:
        raise Scv2Px2ContractError("px2_evidence_name_invalid")
    target = root / name
    try:
        metadata = os.lstat(target)
    except OSError as exc:
        raise Scv2Px2ContractError(f"px2_evidence_missing:{name}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
        or metadata.st_size > JSON_ARTIFACT_BUDGETS[name]
    ):
        raise Scv2Px2ContractError(f"px2_evidence_type_or_budget_invalid:{name}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size != metadata.st_size
                or (metadata.st_ino and opened.st_ino != metadata.st_ino)
                or (metadata.st_dev and opened.st_dev != metadata.st_dev)
            ):
                raise Scv2Px2ContractError(
                    f"px2_evidence_identity_drift:{name}"
                )
            raw = b""
            while len(raw) <= JSON_ARTIFACT_BUDGETS[name]:
                chunk = os.read(
                    descriptor,
                    min(
                        64 * 1024,
                        JSON_ARTIFACT_BUDGETS[name] + 1 - len(raw),
                    ),
                )
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise Scv2Px2ContractError(f"px2_evidence_read_failed:{name}") from exc
    if (
        len(raw) > JSON_ARTIFACT_BUDGETS[name]
        or b"\x00" in raw
        or raw.startswith(b"\xef\xbb\xbf")
    ):
        raise Scv2Px2ContractError(
            f"px2_evidence_encoding_or_budget_invalid:{name}"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Scv2Px2ContractError(f"px2_evidence_json_invalid:{name}") from exc
    if raw != canonical_json_bytes(payload) + b"\n":
        raise Scv2Px2ContractError(f"px2_evidence_not_canonical:{name}")
    return payload


def load_px2_evidence_artifacts(
    paths: Scv2Px2EvidencePaths,
    *,
    require_receipt: bool = True,
) -> dict[str, Any]:
    root = _lexically_confined_root(paths.root)
    try:
        member_names = {entry.name for entry in os.scandir(root)}
    except OSError as exc:
        raise Scv2Px2ContractError("px2_evidence_directory_unreadable") from exc
    expected = set(ALLOWED_EVIDENCE_MEMBERS)
    if not require_receipt:
        expected.remove(RECEIPT_NAME)
    if member_names != expected:
        raise Scv2Px2ContractError("px2_evidence_fixed_member_set_invalid")
    names = list(EVIDENCE_ARTIFACT_NAMES)
    if require_receipt:
        names.append(RECEIPT_NAME)
    payloads = {name: _read_bounded_canonical_json(root, name) for name in names}
    for database_name in DATABASE_NAMES:
        try:
            metadata = os.lstat(root / database_name)
        except OSError as exc:
            raise Scv2Px2ContractError("px2_task_database_missing") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
            or metadata.st_size > 64 * 1024 * 1024
        ):
            raise Scv2Px2ContractError("px2_task_database_type_invalid")
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
    with tempfile.TemporaryDirectory(
        prefix="violet-scv2-px2-contract-storage-"
    ) as runtime_storage:
        os.environ.update(
            {
                "VIOLET_SKIP_DOTENV": "1",
                "VIOLET_ENV": "test",
                "POSTGRES_DB": "scv2_px2_task_temp",
                "TEST_DATABASE_URL": "",
                "VIOLET_STORAGE_ROOT": runtime_storage,
                "VIOLET_TEST_STORAGE_ROOT": runtime_storage,
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


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Scv2Px2ContractError(f"px2_{label}_invalid")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise Scv2Px2ContractError(f"px2_{label}_invalid")
    return value


def _assert_no_database_row_identity(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in {"database_row_id", "db_row_id", "row_id"}:
                raise Scv2Px2ContractError(
                    f"px2_public_projection_forbidden_field:{key}"
                )
            _assert_no_database_row_identity(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_database_row_identity(nested)


def _validate_public_projection(summary: Mapping[str, Any]) -> dict[str, Any]:
    from backend.app.services.pixiv_metadata_clustering_service import (
        PX2_AMBIGUOUS_LEDGER_SCHEMA,
        PX2_AUTHORITY_MAP,
        PX2_CANDIDATE_POLICY_VERSION,
        PX2_CLUSTER_RESULT_SCHEMA,
        PX2_CONTEXT_POLICY_VERSION,
        PX2_CONTRACT_ID,
        PX2_EXECUTED_STAGES,
        PX2_OPERATION_RECEIPT_SCHEMA,
        PX2_PERSISTENCE_PROOF_SCHEMA,
    )
    from backend.app.services.pixiv_metadata_projection_service import (
        PIXIV_AGGREGATE_SCHEMA,
        PIXIV_SIGNAL_BUNDLE_SCHEMA,
        assert_public_safe_projection,
    )
    from backend.app.services.pixiv_metadata_vertical_slice_service import (
        PX2_CONSUMER_CONTRACT_SCHEMA,
    )
    from backend.app.services.source_concept_resolver_service import RESOLVER_VERSION

    if (
        summary.get("schema_version") != PX2_CLUSTER_RESULT_SCHEMA
        or summary.get("contract_id") != PX2_CONTRACT_ID
        or summary.get("status") != "implementation_ready_for_owner_merge_audit"
        or tuple(summary.get("executed_stages", ())) != PX2_EXECUTED_STAGES
    ):
        raise Scv2Px2ContractError("px2_public_identity_or_stage_invalid")
    unsigned = dict(summary)
    supplied = unsigned.pop("canonical_fingerprint", None)
    if not isinstance(supplied, str) or supplied != canonical_fingerprint(unsigned):
        raise Scv2Px2ContractError("px2_public_fingerprint_invalid")
    assert_public_safe_projection(summary)
    _assert_no_database_row_identity(summary)

    inputs = _require_mapping(summary.get("px1_inputs"), "px1_inputs")
    if (
        inputs.get("aggregate_schema") != PIXIV_AGGREGATE_SCHEMA
        or inputs.get("signal_bundle_schema") != PIXIV_SIGNAL_BUNDLE_SCHEMA
        or inputs.get("consumer_contract_schema") != PX2_CONSUMER_CONTRACT_SCHEMA
        or inputs.get("aggregate_count") != 14
        or inputs.get("signal_bundle_count") != 14
        or inputs.get("signal_count") != 40
        or inputs.get("source_state_counts") != EXPECTED_SOURCE_STATES
        or inputs.get("canonical_json_round_trip_stable") is not True
    ):
        raise Scv2Px2ContractError("px2_px1_consumer_projection_invalid")
    for name in (
        "aggregate_artifact_fingerprint",
        "signal_bundle_artifact_fingerprint",
        "consumer_input_fingerprint",
    ):
        if not isinstance(inputs.get(name), str) or not HEX64_RE.fullmatch(inputs[name]):
            raise Scv2Px2ContractError("px2_px1_input_fingerprint_invalid")

    resolver = _require_mapping(summary.get("resolver"), "resolver")
    if resolver != {
        "resolver_version": RESOLVER_VERSION,
        "context_policy_version": PX2_CONTEXT_POLICY_VERSION,
        "candidate_policy_version": PX2_CANDIDATE_POLICY_VERSION,
        "llm_enabled": False,
        "llm_judgment_count": 0,
    }:
        raise Scv2Px2ContractError("px2_resolver_projection_invalid")

    candidates = _require_list(
        summary.get("candidate_dispositions"), "candidate_dispositions"
    )
    pair_keys = [row.get("pair_key") for row in candidates if isinstance(row, Mapping)]
    if len(pair_keys) != len(candidates) or pair_keys != sorted(set(pair_keys)):
        raise Scv2Px2ContractError("px2_candidate_pair_identity_invalid")
    disposition_counts = Counter(row.get("disposition") for row in candidates)
    if set(disposition_counts) != {"must_link", "cannot_link", "deferred_nonblocking"}:
        raise Scv2Px2ContractError("px2_candidate_disposition_domain_invalid")
    for row in candidates:
        if (
            not isinstance(row, Mapping)
            or row.get("candidate_policy_version") != PX2_CANDIDATE_POLICY_VERSION
            or row.get("left_signal_key") >= row.get("right_signal_key")
            or not isinstance(row.get("evidence_refs"), list)
            or not row.get("evidence_refs")
        ):
            raise Scv2Px2ContractError("px2_candidate_record_invalid")
        if row.get("disposition") == "must_link":
            if row.get("union_decision") is not True or row.get(
                "same_resolved_component"
            ) is not True:
                raise Scv2Px2ContractError("px2_must_link_union_invalid")
        elif row.get("union_decision") is not False or row.get(
            "same_resolved_component"
        ) is not False:
            raise Scv2Px2ContractError("px2_nonunion_disposition_invalid")
    accounting = _require_mapping(
        summary.get("candidate_accounting"), "candidate_accounting"
    )
    expected_counts = {
        "must_link_count": disposition_counts["must_link"],
        "cannot_link_count": disposition_counts["cannot_link"],
        "deferred_nonblocking_count": disposition_counts["deferred_nonblocking"],
    }
    if (
        accounting.get("total_candidate_pairs") != len(candidates)
        or any(accounting.get(key) != value for key, value in expected_counts.items())
        or accounting.get("candidate_disposition_coverage") != 1.0
        or accounting.get("accounting_equality_passed") is not True
        or any(
            accounting.get(key) != 0
            for key in (
                "duplicate_disposition_count",
                "extra_disposition_count",
                "silently_dropped_pair_count",
                "unaccounted_pair_count",
            )
        )
    ):
        raise Scv2Px2ContractError("px2_candidate_accounting_invalid")

    clusters = _require_list(summary.get("clusters"), "clusters")
    cluster_keys = [row.get("cluster_key") for row in clusters if isinstance(row, Mapping)]
    if (
        len(cluster_keys) != len(clusters)
        or cluster_keys != sorted(set(cluster_keys))
        or summary.get("cluster_count") != len(clusters)
    ):
        raise Scv2Px2ContractError("px2_cluster_identity_invalid")
    member_keys = [
        key
        for cluster in clusters
        for key in _require_list(cluster.get("member_signal_keys"), "cluster_members")
    ]
    diagnostics = _require_mapping(summary.get("diagnostics"), "diagnostics")
    rejected_signal_count = diagnostics.get("rejected_signal_count")
    if (
        not isinstance(rejected_signal_count, int)
        or rejected_signal_count < 0
        or len(member_keys) != len(set(member_keys))
        or len(member_keys) + rejected_signal_count != inputs.get("signal_count")
    ):
        raise Scv2Px2ContractError("px2_cluster_signal_accounting_invalid")

    ledger = _require_mapping(summary.get("ambiguous_ledger"), "ambiguous_ledger")
    unsigned_ledger = dict(ledger)
    ledger_fingerprint = unsigned_ledger.pop("canonical_fingerprint", None)
    ledger_counts = _require_mapping(ledger.get("counts"), "ledger_counts")
    if (
        ledger.get("schema_version") != PX2_AMBIGUOUS_LEDGER_SCHEMA
        or ledger_fingerprint != canonical_fingerprint(unsigned_ledger)
        or ledger.get("record_count") != sum(ledger_counts.values())
        or ledger.get("blocking") is not False
        or ledger.get("human_review_required") is not False
        or ledger.get("identity_union_allowed") is not False
        or ledger_counts.get("deferred_candidate_pair_count")
        != disposition_counts["deferred_nonblocking"]
    ):
        raise Scv2Px2ContractError("px2_ambiguous_ledger_invalid")

    persistence = _require_mapping(
        summary.get("persistence_proof"), "persistence_proof"
    )
    unsigned_persistence = dict(persistence)
    persistence_fingerprint = unsigned_persistence.pop("canonical_fingerprint", None)
    if (
        persistence.get("schema_version") != PX2_PERSISTENCE_PROOF_SCHEMA
        or persistence_fingerprint != canonical_fingerprint(unsigned_persistence)
        or persistence.get("temporary_database_count") != 2
        or persistence.get("apply_count_per_database") != 2
        or persistence.get("temporary_persistence_idempotent") is not True
        or persistence.get("scope_counts_equal_across_databases") is not True
        or persistence.get("stale_foreign_scope_preserved") is not True
        or persistence.get("only_sourceconcept_owned_temporary_tables_written")
        is not True
        or persistence.get("ambiguous_ledger_persisted_in_existing_resolution_run")
        is not True
        or persistence.get("database_row_id_variation_observed") is not True
        or persistence.get("business_fingerprint_excludes_database_row_id") is not True
        or persistence.get("existing_database_or_app_storage_activity") != 0
        or persistence.get("migration_activity") != 0
    ):
        raise Scv2Px2ContractError("px2_persistence_proof_invalid")
    database_runs = _require_list(persistence.get("databases"), "persistence_databases")
    if len(database_runs) != 2 or any(
        row.get("replay_row_delta_count") != 0
        or row.get("non_sourceconcept_row_delta_count") != 0
        or row.get("forbidden_truth_table_write_count") != 0
        or row.get("stale_scope_violation_count") != 0
        or row.get("scope_counts_stable") is not True
        for row in database_runs
    ):
        raise Scv2Px2ContractError("px2_persistence_database_replay_invalid")

    matrix = _require_list(summary.get("acceptance_matrix"), "acceptance_matrix")
    if (
        summary.get("acceptance_matrix_passed") is not True
        or len(matrix) < 13
        or len({row.get("scenario") for row in matrix}) != len(matrix)
        or any(row.get("passed") is not True for row in matrix)
    ):
        raise Scv2Px2ContractError("px2_acceptance_matrix_invalid")
    if summary.get("invariants") != EXPECTED_INVARIANTS:
        raise Scv2Px2ContractError("px2_invariants_invalid")

    operation = _require_mapping(summary.get("operation_receipt"), "operation_receipt")
    if (
        operation.get("schema_version") != PX2_OPERATION_RECEIPT_SCHEMA
        or operation.get("fixture_source")
        != "repository_owned_px1_synthetic_contract"
        or operation.get("task_owned_temporary_workspace_enforced") is not True
        or operation.get("px1_input_generation_temporary_database_count") != 2
        or operation.get("source_concept_temporary_database_count") != 2
        or any(
            value != 0
            for key, value in operation.items()
            if key.endswith("_activity")
        )
    ):
        raise Scv2Px2ContractError("px2_operation_receipt_invalid")
    if summary.get("authorities") != PX2_AUTHORITY_MAP:
        raise Scv2Px2ContractError("px2_authority_projection_invalid")
    if any(
        summary.get(key) is not value
        for key, value in {
            "deterministic_clustering_verified": True,
            "persistable_cluster_result_verified": True,
            "px1_owner_accepted": True,
            "px1_merged": True,
            "px2_started": True,
            "px2_implementation_completed": True,
            "px2_target_met": True,
            "target_met": True,
            "px2_owner_accepted": False,
            "px2_safe_to_merge": False,
            "px2_merge_authorized": False,
            "px3_started": False,
            "existing_database_authorized": False,
            "real_provider_authorized": False,
            "real_source_authorized": False,
            "full_import_authorized": False,
            "production_authorized": False,
        }.items()
    ):
        raise Scv2Px2ContractError("px2_route_boundary_invalid")
    return {
        "aggregate_count": inputs["aggregate_count"],
        "signal_count": inputs["signal_count"],
        "cluster_count": len(clusters),
        "candidate_counts": dict(sorted(disposition_counts.items())),
        "ambiguous_record_count": ledger["record_count"],
        "public_fingerprint": supplied,
        "business_projection_fingerprint": summary.get(
            "business_projection_fingerprint"
        ),
    }


def check_scv2_px2_contract(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    repository_context: Any,
) -> None:
    if repository_context is None or repository_context.scv2_px2_evidence is None:
        result.fail(
            "px2_private_evidence_required",
            "SCV2-PX2 requires a confined fixed-name evidence bundle.",
        )
        return
    if repository_context.expected_python is None:
        result.fail(
            "px2_expected_python_required",
            "SCV2-PX2 requires an explicitly approved repository Python.",
        )
        return
    try:
        approved_python = repository_context.expected_python.resolve(strict=True)
        if Path(sys.executable).resolve(strict=True) != approved_python:
            raise Scv2Px2ContractError("px2_checker_python_identity_mismatch")
        evidence = load_px2_evidence_artifacts(repository_context.scv2_px2_evidence)
        evidence_summary = evidence["public-summary.json"]
        if dict(summary) != evidence_summary:
            raise Scv2Px2ContractError("px2_caller_summary_evidence_mismatch")
        if evidence["candidate-dispositions.json"] != summary.get(
            "candidate_dispositions"
        ):
            raise Scv2Px2ContractError("px2_candidate_artifact_mismatch")
        if evidence["ambiguous-ledger.json"] != summary.get("ambiguous_ledger"):
            raise Scv2Px2ContractError("px2_ledger_artifact_mismatch")
        if evidence["persistence-proof.json"] != summary.get("persistence_proof"):
            raise Scv2Px2ContractError("px2_persistence_artifact_mismatch")
        if evidence["operation-receipt.json"] != summary.get("operation_receipt"):
            raise Scv2Px2ContractError("px2_operation_receipt_artifact_mismatch")
        with _task_runtime_environment():
            projection_details = _validate_public_projection(summary)
            from backend.app.services.pixiv_metadata_clustering_service import (
                run_synthetic_pixiv_metadata_clustering,
            )

            with tempfile.TemporaryDirectory(
                prefix="violet-scv2-px2-contract-"
            ) as workspace:
                regenerated = run_synthetic_pixiv_metadata_clustering(
                    workspace=Path(workspace),
                    px1_summary=_require_mapping(
                        evidence["px1-consumer-summary.json"], "px1_evidence"
                    ),
                )
        if regenerated != dict(summary):
            raise Scv2Px2ContractError("px2_independent_replay_projection_mismatch")

        repository = repository_identity_snapshot(
            repository_context.repo_root,
            python_executable=approved_python,
            require_clean=True,
        )
        bindings = evidence_bindings(
            {name: evidence[name] for name in EVIDENCE_ARTIFACT_NAMES}
        )
        receipt = _require_mapping(evidence[RECEIPT_NAME], "validation_receipt")
        evidence_head = str(receipt.get("git_head", ""))
        evidence_tree = str(receipt.get("git_tree", ""))
        carry_forward = validate_px2_evidence_carry_forward(
            repository_context.repo_root,
            evidence_head=evidence_head,
            evidence_tree=evidence_tree,
        )
        receipt_repository = dict(repository)
        receipt_repository.update(
            {"git_head": evidence_head, "git_tree": evidence_tree}
        )
        validate_receipt_payload(
            receipt,
            approved_python=approved_python,
            expected_repository=receipt_repository,
            expected_bindings=bindings,
        )
    except Exception as exc:
        result.fail(str(exc), "SCV2-PX2 evidence re-derivation failed.")
        return
    result.details["scv2_px2_projection"] = projection_details
    result.details["scv2_px2_repository_binding"] = {
        "git_head": repository["git_head"],
        "git_tree": repository["git_tree"],
        "implementation_evidence_head": evidence_head,
        "implementation_evidence_tree": evidence_tree,
        "docs_only_carry_forward_paths": carry_forward["changed_paths"],
        "trusted_git_fingerprint": repository["trusted_git_fingerprint"],
        "approved_python_runtime_fingerprint": repository[
            "approved_python_runtime_fingerprint"
        ],
        "clean": True,
    }
    result.details["scv2_px2_evidence_bindings"] = bindings
    result.details["authority_boundary"] = (
        "synthetic_local_operator_evidence_only_px2_owner_acceptance_and_merge_remain_false"
    )
