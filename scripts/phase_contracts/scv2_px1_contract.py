"""Executable evidence contract for the SCV2-PX1 offline Pixiv vertical slice."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Iterator, Mapping
from scripts.scv2_px1_validation_receipt import (
    EVIDENCE_ARTIFACT_NAMES,
    RECEIPT_NAME,
    evidence_bindings,
    repository_identity_snapshot,
    validate_px1_evidence_carry_forward,
    validate_receipt_payload,
)

from .contract_types import ContractCheckResult, PhaseContract


PIXIV_AGGREGATE_SCHEMA = "violet.scv2-px1-pixiv-work-page-aggregate.v1"
PIXIV_SIGNAL_BUNDLE_SCHEMA = "violet.scv2-px1-source-concept-signal-bundle.v1"
PIXIV_PUBLIC_SUMMARY_SCHEMA = "violet.scv2-px1-pixiv-metadata-summary.v1"
SYNTHETIC_FIXTURE_SCHEMA = "violet.scv2-px1-synthetic-pixiv-fixture.v1"
VERTICAL_SLICE_RECEIPT_SCHEMA = "violet.scv2-px1-offline-operation-receipt.v2"
PX1_CONTRACT_ID = "scv2_px1_pixiv_metadata_consolidation_contract_v1"
PX2_CONSUMER_CONTRACT_SCHEMA = "violet.scv2-px1-px2-consumer-contract.v1"
PX1_EXECUTED_STAGES = (
    "synthetic_fixture_creation",
    "canonical_pixiv_normalization",
    "source_metadata_persistence",
    "canonical_work_page_aggregate",
    "source_concept_signal_projection",
    "deterministic_replay",
    "public_safe_summary",
)
PX1_AUTHORITY_MAP = {
    "px1_implementation_authorized": True,
    "repository_read_authorized": True,
    "synthetic_fixture_execution_authorized": True,
    "task_owned_temporary_database_authorized": True,
    "isolated_worktree_authorized": True,
    "branch_commit_push_authorized": True,
    "one_normal_pull_request_authorized": True,
    "documentation_state_transition_authorized": True,
    "merge_authorized": False,
    "real_source_inventory_authorized": False,
    "source_root_or_icloud_access_authorized": False,
    "existing_database_access_authorized": False,
    "app_storage_write_authorized": False,
    "real_pixiv_or_gallery_dl_network_execution_authorized": False,
    "provider_credentials_authorized": False,
    "media_or_thumbnail_download_authorized": False,
    "import_authorized": False,
    "classification_or_tagging_execution_on_user_data_authorized": False,
    "llm_or_external_model_authorized": False,
    "server_browser_or_e2e_authorized": False,
    "production_authorized": False,
    "full_library_import_authorized": False,
}
_WINDOWS_PATH = re.compile(r"(?i)(?:^|[\s\"'=:(\[])(?:[a-z]:[\\/]|\\\\)")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?:^|[\s\"'=:(\[])/(?!/)")
_FILE_URI = re.compile(r"(?i)\bfile:(?://|\\\\)")
_SECRET_MARKER = re.compile(
    r"(?i)(?:authorization\s*[:=]|set-cookie\s*[:=]|cookie\s*[:=]|"
    r"bearer\s+\S+|api[_-]?key\s*[:=]|client[_-]?secret\s*[:=]|"
    r"refresh[_-]?token\s*[:=]|access[_-]?token\s*[:=]|"
    r"password\s*[:=]|credential(?:s)?\s*[:=]|secret\s*[:=])"
)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def assert_public_safe_projection(value: Any) -> None:
    serialized = canonical_json_bytes(value).decode("utf-8")
    lowered = serialized.casefold()
    forbidden_keys = (
        '"raw_metadata_json"',
        '"raw_provider_payload"',
        '"raw"',
        '"source_url"',
        '"local_path"',
        '"filename"',
        '"credential"',
        '"credentials"',
        '"authorization"',
        '"client_secret"',
        '"password"',
        '"cookie"',
        '"access_token"',
        '"refresh_token"',
    )
    if any(key in lowered for key in forbidden_keys):
        raise Scv2Px1ContractError("px1_public_projection_forbidden_field")
    if (
        "\x00" in serialized
        or "\\u0000" in lowered
        or _WINDOWS_PATH.search(serialized)
        or _POSIX_ABSOLUTE_PATH.search(serialized)
        or _FILE_URI.search(serialized)
        or _SECRET_MARKER.search(serialized)
    ):
        raise Scv2Px1ContractError("px1_public_projection_private_text")


EXPECTED_DISPOSITIONS = {
    "complete": 8,
    "conflict": 1,
    "page_mismatch": 1,
    "retryable": 1,
    "terminal": 1,
    "unsupported": 2,
}
ALLOWED_EVIDENCE_MEMBERS = set(EVIDENCE_ARTIFACT_NAMES) | {
    RECEIPT_NAME,
    "px1-first.sqlite3",
    "px1-reversed.sqlite3",
}
ARTIFACT_BUDGETS = {
    "synthetic-fixture.json": 512 * 1024,
    "aggregates.json": 4 * 1024 * 1024,
    "signal-bundles.json": 4 * 1024 * 1024,
    "operation-receipt.json": 256 * 1024,
    "public-summary.json": 8 * 1024 * 1024,
    RECEIPT_NAME: 256 * 1024,
}
WINDOWS_REPARSE_POINT = 0x400


class Scv2Px1ContractError(RuntimeError):
    pass


class Scv2Px1EvidencePaths:
    """A caller-provided path that remains unresolved until lexical confinement."""

    def __init__(self, root: Path):
        self.root = Path(root)


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
        prefix="violet-scv2-px1-contract-storage-"
    ) as runtime_storage:
        os.environ.update(
            {
                "VIOLET_SKIP_DOTENV": "1",
                "VIOLET_ENV": "test",
                "POSTGRES_DB": "scv2_px1_task_temp",
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


def _lexically_confined_root(path: Path) -> Path:
    if not path.is_absolute():
        raise Scv2Px1ContractError("px1_evidence_root_not_absolute")
    lexical = Path(os.path.abspath(os.fspath(path)))
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        lexical.relative_to(temp_root)
    except ValueError as exc:
        raise Scv2Px1ContractError("px1_evidence_root_not_task_temp") from exc
    try:
        relative = lexical.relative_to(temp_root)
    except OSError as exc:
        raise Scv2Px1ContractError("px1_evidence_root_unavailable") from exc
    cursor = temp_root
    for component in relative.parts:
        cursor = cursor / component
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise Scv2Px1ContractError("px1_evidence_root_unavailable") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise Scv2Px1ContractError("px1_evidence_root_alias_or_type_invalid")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(temp_root)
    except (OSError, ValueError) as exc:
        raise Scv2Px1ContractError("px1_evidence_root_resolution_invalid") from exc
    if resolved != lexical:
        raise Scv2Px1ContractError("px1_evidence_root_alias_or_type_invalid")
    return resolved


def _read_bounded_canonical_json(root: Path, name: str) -> Any:
    if name not in ARTIFACT_BUDGETS or Path(name).name != name:
        raise Scv2Px1ContractError("px1_evidence_name_invalid")
    target = root / name
    try:
        metadata = os.lstat(target)
    except OSError as exc:
        raise Scv2Px1ContractError(f"px1_evidence_missing:{name}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
        or metadata.st_size > ARTIFACT_BUDGETS[name]
    ):
        raise Scv2Px1ContractError(f"px1_evidence_type_or_budget_invalid:{name}")
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
                raise Scv2Px1ContractError(f"px1_evidence_identity_drift:{name}")
            raw = b""
            while len(raw) <= ARTIFACT_BUDGETS[name]:
                chunk = os.read(descriptor, min(64 * 1024, ARTIFACT_BUDGETS[name] + 1 - len(raw)))
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise Scv2Px1ContractError(f"px1_evidence_read_failed:{name}") from exc
    if len(raw) > ARTIFACT_BUDGETS[name] or b"\x00" in raw or raw.startswith(b"\xef\xbb\xbf"):
        raise Scv2Px1ContractError(f"px1_evidence_encoding_or_budget_invalid:{name}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Scv2Px1ContractError(f"px1_evidence_json_invalid:{name}") from exc
    if raw != canonical_json_bytes(payload) + b"\n":
        raise Scv2Px1ContractError(f"px1_evidence_not_canonical:{name}")
    return payload


def load_px1_evidence_artifacts(
    paths: Scv2Px1EvidencePaths,
    *,
    require_receipt: bool = True,
) -> dict[str, Any]:
    root = _lexically_confined_root(paths.root)
    try:
        member_names = {entry.name for entry in os.scandir(root)}
    except OSError as exc:
        raise Scv2Px1ContractError("px1_evidence_directory_unreadable") from exc
    expected_members = set(ALLOWED_EVIDENCE_MEMBERS)
    if not require_receipt:
        expected_members.remove(RECEIPT_NAME)
    if member_names != expected_members:
        raise Scv2Px1ContractError("px1_evidence_fixed_member_set_invalid")
    json_names = list(EVIDENCE_ARTIFACT_NAMES)
    if require_receipt:
        json_names.append(RECEIPT_NAME)
    payloads = {
        name: _read_bounded_canonical_json(root, name)
        for name in json_names
    }
    for database_name in ("px1-first.sqlite3", "px1-reversed.sqlite3"):
        try:
            metadata = os.lstat(root / database_name)
        except OSError as exc:
            raise Scv2Px1ContractError("px1_task_database_missing") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
            or metadata.st_size > 64 * 1024 * 1024
        ):
            raise Scv2Px1ContractError("px1_task_database_type_invalid")
    payloads["_root"] = root
    return payloads


def _validate_embedded_fingerprint(payload: Mapping[str, Any], *, label: str) -> None:
    supplied = payload.get("canonical_fingerprint")
    unsigned = dict(payload)
    unsigned.pop("canonical_fingerprint", None)
    if supplied != canonical_fingerprint(unsigned):
        raise Scv2Px1ContractError(f"px1_{label}_fingerprint_invalid")


def _load_current_px1_evidence_binding(repo_root: Path) -> tuple[str, str]:
    state_path = repo_root / "docs" / "state" / "current-phase.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Scv2Px1ContractError("px1_current_state_unavailable") from exc
    if state.get("phase_id") != "SCV2-PX1":
        raise Scv2Px1ContractError("px1_current_state_phase_invalid")
    head = str(state.get("implementation_evidence_head") or "")
    tree = str(state.get("implementation_evidence_tree") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", head) or not re.fullmatch(
        r"[0-9a-f]{40}", tree
    ):
        raise Scv2Px1ContractError("px1_current_state_evidence_invalid")
    return head, tree


def _validate_public_projection(summary: Mapping[str, Any]) -> dict[str, Any]:
    if summary.get("schema_version") != PIXIV_PUBLIC_SUMMARY_SCHEMA:
        raise Scv2Px1ContractError("px1_public_schema_invalid")
    if summary.get("contract_id") != PX1_CONTRACT_ID:
        raise Scv2Px1ContractError("px1_contract_id_invalid")
    _validate_embedded_fingerprint(summary, label="summary")
    assert_public_safe_projection(summary)
    if summary.get("status") != "implementation_ready_for_owner_audit":
        raise Scv2Px1ContractError("px1_status_invalid")
    if summary.get("executed_stages") != list(PX1_EXECUTED_STAGES):
        raise Scv2Px1ContractError("px1_stages_invalid")
    if summary.get("authorities") != PX1_AUTHORITY_MAP:
        raise Scv2Px1ContractError("px1_authority_map_invalid")
    required_true = (
        "synthetic_vertical_slice_verified",
        "deterministic_replay",
        "input_order_stable",
        "px1_implementation_completed",
        "px1_target_met",
        "px2_consumer_contract_frozen",
        "target_met",
    )
    required_false = (
        "cluster_materialization_performed",
        "entity_truth_promoted",
        "owner_accepted",
        "safe_to_merge",
        "route_approved",
        "merge_authorized",
        "px2_started",
        "real_provider_authorized",
        "real_source_authorized",
        "full_import_authorized",
        "production_authorized",
    )
    if any(summary.get(field) is not True for field in required_true):
        raise Scv2Px1ContractError("px1_positive_claim_invalid")
    if any(summary.get(field) is not False for field in required_false):
        raise Scv2Px1ContractError("px1_negative_authority_invalid")
    if not (
        summary.get("canonical_projection_fingerprint")
        == summary.get("replay_projection_fingerprint")
        == summary.get("reversed_input_projection_fingerprint")
    ):
        raise Scv2Px1ContractError("px1_replay_fingerprint_mismatch")

    aggregates = summary.get("aggregates")
    bundles = summary.get("signal_bundles")
    if not isinstance(aggregates, list) or not isinstance(bundles, list):
        raise Scv2Px1ContractError("px1_projection_shape_invalid")
    if len(aggregates) != 14 or len(bundles) != len(aggregates):
        raise Scv2Px1ContractError("px1_projection_count_invalid")
    consumer = summary.get("px2_consumer_contract")
    if not isinstance(consumer, Mapping) or consumer != {
        "schema_version": PX2_CONSUMER_CONTRACT_SCHEMA,
        "aggregate_schema_version": PIXIV_AGGREGATE_SCHEMA,
        "signal_bundle_schema_version": PIXIV_SIGNAL_BUNDLE_SCHEMA,
        "aggregate_artifact_fingerprint": canonical_fingerprint(aggregates),
        "signal_bundle_artifact_fingerprint": canonical_fingerprint(bundles),
        "canonical_json_round_trip_stable": True,
        "database_row_identity_excluded": True,
        "runtime_order_identity_excluded": True,
        "wall_clock_identity_excluded": True,
        "cluster_materialization_performed": False,
    }:
        raise Scv2Px1ContractError("px1_px2_consumer_contract_invalid")
    if (
        canonical_fingerprint(json.loads(canonical_json_bytes(aggregates)))
        != canonical_fingerprint(aggregates)
        or canonical_fingerprint(json.loads(canonical_json_bytes(bundles)))
        != canonical_fingerprint(bundles)
    ):
        raise Scv2Px1ContractError("px1_px2_canonical_round_trip_invalid")
    consumer_serialized = canonical_json_bytes(
        {"aggregates": aggregates, "signal_bundles": bundles}
    ).decode("utf-8").casefold()
    if any(
        forbidden in consumer_serialized
        for forbidden in (
            '"media_id"',
            '"provider_record_key"',
            '"source_metadata_record_id"',
            '"retrieved_at"',
            '"created_at"',
            '"updated_at"',
        )
    ):
        raise Scv2Px1ContractError("px1_px2_row_or_time_identity_present")
    aggregate_keys: list[str] = []
    aggregates_by_key: dict[str, Mapping[str, Any]] = {}
    for aggregate in aggregates:
        if not isinstance(aggregate, Mapping) or aggregate.get("schema_version") != PIXIV_AGGREGATE_SCHEMA:
            raise Scv2Px1ContractError("px1_aggregate_schema_invalid")
        _validate_embedded_fingerprint(aggregate, label="aggregate")
        aggregate_key = str(aggregate.get("stable_work_page_key"))
        aggregate_keys.append(aggregate_key)
        aggregates_by_key[aggregate_key] = aggregate
        if not isinstance(aggregate.get("provenance"), Mapping):
            raise Scv2Px1ContractError("px1_aggregate_provenance_missing")
        if not isinstance(aggregate.get("conflict_reasons"), list) or not isinstance(
            aggregate.get("deferred_reasons"), list
        ):
            raise Scv2Px1ContractError("px1_aggregate_uncertainty_missing")
    if aggregate_keys != sorted(set(aggregate_keys)):
        raise Scv2Px1ContractError("px1_aggregate_logical_keys_invalid")
    derived_dispositions = dict(
        sorted(Counter(str(item.get("disposition")) for item in aggregates).items())
    )
    if derived_dispositions != EXPECTED_DISPOSITIONS or summary.get("disposition_counts") != derived_dispositions:
        raise Scv2Px1ContractError("px1_disposition_accounting_invalid")
    if not any(
        "legacy_unknown" in (item.get("provenance") or {}).get(
            "normalizer_versions", []
        )
        and "legacy_unknown_provenance" in item.get("deferred_reasons", [])
        for item in aggregates
    ):
        raise Scv2Px1ContractError("px1_legacy_provenance_projection_missing")
    if not any(
        (item.get("page_count_or_known_scope") or {}).get("page_count") == 3
        and (item.get("page_count_or_known_scope") or {}).get(
            "known_page_indexes"
        )
        == [0, 1, 2]
        for item in aggregates
    ):
        raise Scv2Px1ContractError("px1_three_page_projection_missing")
    database_counts = summary.get("database_counts")
    if not isinstance(database_counts, Mapping) or (
        database_counts.get("source_metadata_records") != 16
        or database_counts.get("source_metadata_records") <= len(aggregates)
    ):
        raise Scv2Px1ContractError("px1_mixed_database_accounting_invalid")

    all_logical_keys: set[str] = set()
    signal_count = 0
    for bundle in bundles:
        if not isinstance(bundle, Mapping) or bundle.get("schema_version") != PIXIV_SIGNAL_BUNDLE_SCHEMA:
            raise Scv2Px1ContractError("px1_signal_bundle_schema_invalid")
        _validate_embedded_fingerprint(bundle, label="signal_bundle")
        logical_keys = bundle.get("logical_keys")
        signals = bundle.get("signals")
        if not isinstance(logical_keys, list) or not isinstance(signals, list):
            raise Scv2Px1ContractError("px1_signal_bundle_shape_invalid")
        derived_keys = sorted(str(item.get("signal_key")) for item in signals)
        if logical_keys != derived_keys or bundle.get("signal_count") != len(signals):
            raise Scv2Px1ContractError("px1_signal_logical_keys_invalid")
        if all_logical_keys.intersection(derived_keys):
            raise Scv2Px1ContractError("px1_cross_context_signal_union")
        all_logical_keys.update(derived_keys)
        signal_count += len(signals)
        aggregate = aggregates_by_key.get(str(bundle.get("stable_work_page_key")))
        if aggregate is None or bundle.get("aggregate_fingerprint") != aggregate.get(
            "canonical_fingerprint"
        ):
            raise Scv2Px1ContractError("px1_signal_aggregate_binding_invalid")
        expected_source_state = {
            "disposition": aggregate.get("disposition"),
            "conflict_reasons": aggregate.get("conflict_reasons"),
            "deferred_reasons": aggregate.get("deferred_reasons"),
            "metadata_completeness": aggregate.get("metadata_completeness"),
            "provenance": aggregate.get("provenance"),
        }
        if bundle.get("source_state") != expected_source_state:
            raise Scv2Px1ContractError("px1_signal_source_state_invalid")
        creator = aggregate.get("creator")
        creator = creator if isinstance(creator, Mapping) else {}
        creator_id = creator.get("provider_creator_id")
        conflicting_creator_ids = creator.get("conflicting_provider_creator_ids") or []
        anchors = {
            str(signal.get("identity_anchor"))
            for signal in signals
            if signal.get("identity_anchor")
        }
        if creator_id and not conflicting_creator_ids and (
            f"provider-account:pixiv:{creator_id}" not in anchors
        ):
            raise Scv2Px1ContractError("px1_valid_creator_anchor_missing")
        if (not creator_id or conflicting_creator_ids) and anchors:
            raise Scv2Px1ContractError("px1_invalid_or_conflicting_creator_anchor_present")
        if (
            bundle.get("name_only_identity_anchor_count") != 0
            or bundle.get("cross_context_union_count") != 0
            or bundle.get("cluster_materialization_performed") is not False
            or bundle.get("entity_truth_promoted") is not False
        ):
            raise Scv2Px1ContractError("px1_signal_authority_invalid")

    receipt = summary.get("operation_receipt")
    if not isinstance(receipt, Mapping) or receipt.get("schema_version") != VERTICAL_SLICE_RECEIPT_SCHEMA:
        raise Scv2Px1ContractError("px1_operation_receipt_schema_invalid")
    zero_fields = (
        "existing_database_read_count",
        "existing_database_write_count",
        "existing_app_storage_access_count",
        "provider_network_activity_count",
        "media_network_activity_count",
        "subprocess_activity_count",
        "credential_access_count",
        "source_root_access_count",
        "entity_truth_write_count",
        "source_concept_materialization_count",
        "media_tag_write_count",
    )
    if any(receipt.get(field) != 0 for field in zero_fields):
        raise Scv2Px1ContractError("px1_operation_receipt_nonzero_forbidden_activity")
    if (
        receipt.get("fixture_source") != "repository_owned_new_synthetic_only"
        or receipt.get("temporary_workspace_enforced") is not True
        or receipt.get("task_owned_temporary_database_count") != 2
        or receipt.get("task_owned_temporary_runtime_storage_root_count") != 1
    ):
        raise Scv2Px1ContractError("px1_operation_receipt_scope_invalid")
    return {
        "aggregate_count": len(aggregates),
        "signal_count": signal_count,
        "aggregate_logical_keys": aggregate_keys,
        "signal_logical_keys": sorted(all_logical_keys),
        "disposition_counts": derived_dispositions,
    }


def check_scv2_px1_contract(
    contract: PhaseContract,
    summary: Mapping[str, Any],
    result: ContractCheckResult,
    *,
    repository_context: Any,
) -> None:
    if repository_context is None or repository_context.scv2_px1_evidence is None:
        result.fail(
            "px1_private_evidence_required",
            "SCV2-PX1 requires a confined fixed-name evidence bundle.",
        )
        return
    if repository_context.expected_python is None:
        result.fail(
            "px1_expected_python_required",
            "SCV2-PX1 requires an explicitly approved repository Python.",
        )
        return
    try:
        approved_python = repository_context.expected_python.resolve(strict=True)
        if Path(sys.executable).resolve(strict=True) != approved_python:
            raise Scv2Px1ContractError("px1_checker_python_identity_mismatch")
        evidence = load_px1_evidence_artifacts(repository_context.scv2_px1_evidence)
        fixture = evidence["synthetic-fixture.json"]
        evidence_summary = evidence["public-summary.json"]
        if (
            not isinstance(fixture, Mapping)
            or fixture.get("schema_version") != SYNTHETIC_FIXTURE_SCHEMA
            or fixture.get("fixture_origin") != "repository_owned_new_synthetic_only"
        ):
            raise Scv2Px1ContractError("px1_fixture_authority_invalid")
        if dict(summary) != evidence_summary:
            raise Scv2Px1ContractError("px1_caller_summary_evidence_mismatch")
        if evidence["aggregates.json"] != summary.get("aggregates"):
            raise Scv2Px1ContractError("px1_aggregate_artifact_mismatch")
        if evidence["signal-bundles.json"] != summary.get("signal_bundles"):
            raise Scv2Px1ContractError("px1_signal_artifact_mismatch")
        if evidence["operation-receipt.json"] != summary.get("operation_receipt"):
            raise Scv2Px1ContractError("px1_operation_receipt_artifact_mismatch")
        projection_details = _validate_public_projection(summary)

        with _task_runtime_environment():
            from backend.app.services.pixiv_metadata_vertical_slice_service import (
                repository_synthetic_pixiv_fixture,
                run_synthetic_pixiv_vertical_slice,
            )

            if fixture != repository_synthetic_pixiv_fixture():
                raise Scv2Px1ContractError("px1_fixture_not_repository_canonical")
            with tempfile.TemporaryDirectory(
                prefix="violet-scv2-px1-contract-"
            ) as workspace:
                regenerated = run_synthetic_pixiv_vertical_slice(
                    workspace=Path(workspace),
                    fixture=fixture,
                )
        if regenerated != dict(summary):
            raise Scv2Px1ContractError("px1_independent_replay_projection_mismatch")

        repository = repository_identity_snapshot(
            repository_context.repo_root,
            python_executable=approved_python,
            require_clean=True,
        )
        bound_payloads = {
            name: evidence[name] for name in EVIDENCE_ARTIFACT_NAMES
        }
        bindings = evidence_bindings(bound_payloads)
        receipt = evidence[RECEIPT_NAME]
        if not isinstance(receipt, Mapping):
            raise Scv2Px1ContractError("px1_validation_receipt_invalid")
        evidence_head, evidence_tree = _load_current_px1_evidence_binding(
            repository_context.repo_root
        )
        if (
            receipt.get("git_head") != evidence_head
            or receipt.get("git_tree") != evidence_tree
        ):
            raise Scv2Px1ContractError("px1_receipt_current_state_binding_mismatch")
        carry_forward = validate_px1_evidence_carry_forward(
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
        result.fail(str(exc), "SCV2-PX1 evidence re-derivation failed.")
        return
    result.details["scv2_px1_projection"] = projection_details
    result.details["scv2_px1_repository_binding"] = {
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
    result.details["scv2_px1_evidence_bindings"] = bindings
    result.details["authority_boundary"] = (
        "synthetic_local_operator_evidence_only_owner_acceptance_and_merge_remain_false"
    )
