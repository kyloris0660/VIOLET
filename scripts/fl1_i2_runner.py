"""Synthetic-only SCV2-FL1-I2 pre-real hardening evidence runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# Direct-script invocation must establish the repository root before importing
# any repository module. Module invocation reaches the same canonical code path.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, os.fspath(REPOSITORY_ROOT))

from scripts.fl1_i1_operation_gateway import load_private_json
from scripts.fl1_i2_cli import public_error_envelope, render_public_json
from scripts.fl1_i2_confinement import (
    ConfinementError,
    bind_directory,
    lexically_confine_config_path,
    os_local_temp_root,
    verify_synthetic_roots,
)
from scripts.fl1_i2_evidence import (
    EvidenceError,
    EvidenceStore,
    FailureBudget,
    FixedCutManifest,
    ItemDisposition,
    ManifestDirectory,
    ManifestMember,
    OperationLedger,
    OperationState,
    canonical_fingerprint,
)
from scripts.fl1_i2_worker import WorkerController, WorkerOperation, WorkerResult, WorkerStatus


CONTRACT_ID = "scv2_fl1_i2_pre_real_hardening_contract_v1"
PUBLIC_SCHEMA = "violet.scv2-fl1-i2-public-summary.v1"
CONFIG_SCHEMA = "violet.scv2-fl1-i2-synthetic-run-config.v3"
MARKER_NAME = ".violet-synthetic-fixture.json"
ELIGIBLE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"})
CANONICAL_POLICY = {
    "policy_version": "scv2-fl1-i2-source-safety.v1",
    "allowed_source_kinds": ["path_source"],
    "require_known_attributes": True,
    "require_no_follow": True,
    "require_identity_bound": True,
    "reject_reparse_points": True,
    "reject_multiple_links": True,
    "reject_recall_risk": True,
}
CANONICAL_ENUMERATION_BUDGET = {
    "max_entries": 8192,
    "max_pages": 2048,
    "max_metadata_bytes": 8 * 1024 * 1024,
    "max_directories": 1024,
    "max_depth": 64,
}
EXPECTED_FALSE_AUTHORITIES = {
    "real_source": False,
    "database": False,
    "app_storage": False,
    "import": False,
    "classification_or_tagging": False,
    "provider_or_llm": False,
    "media_download": False,
    "stable_replay": False,
    "production": False,
}


class RunnerError(RuntimeError):
    def __init__(self, code: str, *, public_code: str = "validation_failed") -> None:
        super().__init__(code)
        self.code = code
        self.public_code = public_code


@dataclass(frozen=True)
class RunDeadline:
    """One invocation-local monotonic view of the persisted run deadline."""

    deadline_monotonic: float

    @classmethod
    def for_new_run(cls, budget: FailureBudget, *, monotonic_start: float) -> "RunDeadline":
        return cls(monotonic_start + budget.max_run_seconds)

    @classmethod
    def for_resume(cls, ledger: OperationLedger, budget: FailureBudget) -> "RunDeadline":
        elapsed_wall = max(0, time.time_ns() - ledger.run_started_at_ns) / 1_000_000_000
        remaining = max(0.0, budget.max_run_seconds - elapsed_wall)
        return cls(time.monotonic() + remaining)

    def remaining_seconds(self) -> float:
        return self.deadline_monotonic - time.monotonic()

    def require_admission(self) -> float:
        remaining = self.remaining_seconds()
        if remaining <= 0:
            raise RunnerError("run_wide_deadline_exceeded")
        return remaining

    def worker_timeout(self, maximum: float) -> float:
        return min(maximum, self.require_admission())


def _begin_operation_before_deadline(
    ledger: OperationLedger,
    deadline: RunDeadline,
    **kwargs: Any,
) -> str:
    deadline.require_admission()
    return ledger.begin(**kwargs)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise RunnerError("cli_arguments_invalid")


def _within(candidate: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((os.fspath(candidate), os.fspath(root))) == os.fspath(root)
    except ValueError:
        return False


def _validate_temp_root(path: Path) -> Path:
    try:
        bound = bind_directory(path)
        temp_root = os_local_temp_root()
    except ConfinementError as exc:
        raise RunnerError("synthetic_temp_root_invalid") from exc
    if bound.path == temp_root.path or not _within(bound.path, temp_root.path):
        raise RunnerError("synthetic_temp_root_invalid")
    return bound.path


def create_synthetic_run_config(
    *,
    source_root: Path,
    evidence_root: Path,
    run_id: str | None = None,
    budget: FailureBudget | None = None,
) -> Path:
    try:
        roots = verify_synthetic_roots(source_root, evidence_root)
    except ConfinementError as exc:
        raise RunnerError("synthetic_roots_invalid") from exc
    source = roots.source_root.path
    evidence = roots.evidence_root.path
    identifier = run_id or uuid.uuid4().hex
    marker = source / MARKER_NAME
    if marker.exists() or marker.is_symlink():
        raise RunnerError("synthetic_fixture_marker_already_exists")
    active_budget = budget or FailureBudget(3, 100, 8 * 1024 * 1024, 10)
    marker_payload = {
        "schema_version": "violet.scv2-fl1-i2-synthetic-fixture-marker.v1",
        "run_id": identifier,
        "mode": "synthetic_new_temp_fixture",
        "created_at_ns": time.time_ns(),
    }
    EvidenceStore(source).write(MARKER_NAME, marker_payload)
    marker_size = (source / MARKER_NAME).stat(follow_symlinks=False).st_size
    if marker_size > active_budget.max_synthetic_marker_bytes:
        raise RunnerError("synthetic_marker_budget_exhausted")
    config = {
        "schema_version": CONFIG_SCHEMA,
        "run_id": identifier,
        "mode": "synthetic_new_temp_fixture",
        "source_root": os.fspath(source),
        "evidence_root": os.fspath(evidence),
        "confinement": {
            "task_root": os.fspath(roots.task_root.path),
            "task_root_identity": roots.task_root.identity_fingerprint,
            "source_root_identity": roots.source_root.identity_fingerprint,
            "evidence_root_identity": roots.evidence_root.identity_fingerprint,
            "synthetic_marker_bytes": marker_size,
            "synthetic_marker_budget_scope": "separate_from_evidence_root_disk_budget",
        },
        "policy": dict(CANONICAL_POLICY),
        "enumeration_budget": dict(CANONICAL_ENUMERATION_BUDGET),
        "budget": active_budget.to_dict(),
        "authorities": dict(EXPECTED_FALSE_AUTHORITIES),
    }
    EvidenceStore(evidence).write("private-run-config.json", config)
    return evidence / "private-run-config.json"


def _load_config(path: Path) -> tuple[dict[str, Any], Path, Path, FailureBudget]:
    try:
        config_path = lexically_confine_config_path(path)
    except ConfinementError as exc:
        raise RunnerError("synthetic_run_config_location_invalid") from exc
    payload = load_private_json(config_path)
    expected = {
        "schema_version",
        "run_id",
        "mode",
        "source_root",
        "evidence_root",
        "confinement",
        "policy",
        "enumeration_budget",
        "budget",
        "authorities",
    }
    if set(payload) != expected or payload.get("schema_version") != CONFIG_SCHEMA or payload.get("mode") != "synthetic_new_temp_fixture":
        raise RunnerError("synthetic_run_config_invalid")
    try:
        roots = verify_synthetic_roots(
            Path(str(payload["source_root"])), Path(str(payload["evidence_root"]))
        )
    except ConfinementError as exc:
        raise RunnerError("synthetic_roots_invalid") from exc
    source = roots.source_root.path
    evidence = roots.evidence_root.path
    confinement = payload.get("confinement")
    if (
        not isinstance(confinement, Mapping)
        or type(confinement.get("synthetic_marker_bytes")) is not int
        or confinement["synthetic_marker_bytes"] < 0
    ):
        raise RunnerError("synthetic_confinement_binding_invalid")
    expected_confinement = {
        "task_root": os.fspath(roots.task_root.path),
        "task_root_identity": roots.task_root.identity_fingerprint,
        "source_root_identity": roots.source_root.identity_fingerprint,
        "evidence_root_identity": roots.evidence_root.identity_fingerprint,
        "synthetic_marker_bytes": confinement["synthetic_marker_bytes"],
        "synthetic_marker_budget_scope": "separate_from_evidence_root_disk_budget",
    }
    if payload.get("confinement") != expected_confinement:
        raise RunnerError("synthetic_confinement_binding_invalid")
    try:
        config_bound = config_path
        if config_bound.parent != evidence or config_bound.name != "private-run-config.json":
            raise RunnerError("synthetic_run_config_location_invalid")
    except (OSError, ValueError) as exc:
        raise RunnerError("synthetic_run_config_location_invalid") from exc
    if config_bound != evidence / "private-run-config.json":
        raise RunnerError("synthetic_run_config_location_invalid")
    marker_path = source / MARKER_NAME
    marker = EvidenceStore(source).read(MARKER_NAME)
    marker_keys = {"schema_version", "run_id", "mode", "created_at_ns"}
    if (
        set(marker) != marker_keys
        or marker.get("schema_version") != "violet.scv2-fl1-i2-synthetic-fixture-marker.v1"
        or marker.get("run_id") != payload["run_id"]
        or marker.get("mode") != "synthetic_new_temp_fixture"
        or type(marker.get("created_at_ns")) is not int
        or marker["created_at_ns"] <= 0
    ):
        raise RunnerError("synthetic_fixture_marker_invalid")
    authorities = payload["authorities"]
    if (
        not isinstance(authorities, Mapping)
        or set(authorities) != set(EXPECTED_FALSE_AUTHORITIES)
        or any(type(authorities[key]) is not bool or authorities[key] is not False for key in EXPECTED_FALSE_AUTHORITIES)
    ):
        raise RunnerError("synthetic_run_authority_escalation")
    if payload["policy"] != CANONICAL_POLICY or payload["enumeration_budget"] != CANONICAL_ENUMERATION_BUDGET:
        raise RunnerError("synthetic_run_policy_drift")
    try:
        budget = FailureBudget(**payload["budget"])
    except (TypeError, EvidenceError) as exc:
        raise RunnerError("synthetic_run_budget_invalid") from exc
    marker_size = marker_path.stat(follow_symlinks=False).st_size
    if (
        marker_size != payload["confinement"]["synthetic_marker_bytes"]
        or marker_size > budget.max_synthetic_marker_bytes
    ):
        raise RunnerError("synthetic_marker_budget_exhausted")
    return payload, source, evidence, budget


def _checkpoint(injector: Callable[[str], None] | None, name: str) -> None:
    if injector is not None:
        injector(name)


def _persist_ledger(
    store: EvidenceStore,
    ledger: OperationLedger,
    *,
    crash_injector: Callable[[str], None] | None,
    boundary: str,
) -> None:
    store.write("private-operation-ledger.json", ledger.to_private_dict())
    _checkpoint(crash_injector, boundary)


def _persist_and_verify_ledger(
    store: EvidenceStore,
    ledger: OperationLedger,
    *,
    crash_injector: Callable[[str], None] | None,
    boundary: str,
) -> None:
    _persist_ledger(store, ledger, crash_injector=crash_injector, boundary=boundary)
    rebuilt = OperationLedger.from_private_dict(store.read("private-operation-ledger.json"))
    if rebuilt.to_private_dict() != ledger.to_private_dict():
        raise RunnerError("operation_closure_conflict")


def _persist_worker_projection(
    store: EvidenceStore,
    ledger: OperationLedger,
    *,
    crash_injector: Callable[[str], None] | None,
) -> dict[str, Any]:
    projection = ledger.to_worker_projection()
    store.write("private-worker-results.json", projection)
    _checkpoint(crash_injector, "after_worker_projection")
    return projection


def _validate_or_rebuild_projection(store: EvidenceStore, ledger: OperationLedger, evidence: Path) -> dict[str, Any]:
    expected = ledger.to_worker_projection()
    path = evidence / "private-worker-results.json"
    if not path.exists() and not path.is_symlink():
        store.write("private-worker-results.json", expected)
        return expected
    observed = store.read("private-worker-results.json")
    if observed != expected:
        observed_records = observed.get("records") if isinstance(observed, Mapping) else None
        expected_records = expected["records"]
        expected_by_id = {record["operation_id"]: record for record in expected_records}
        if (
            observed.get("schema_version") == expected["schema_version"]
            and observed.get("run_id") == expected["run_id"]
            and isinstance(observed_records, list)
            and len({record.get("operation_id") for record in observed_records if isinstance(record, Mapping)}) == len(observed_records)
            and all(
                isinstance(record, Mapping)
                and expected_by_id.get(record.get("operation_id")) == record
                for record in observed_records
            )
        ):
            store.write("private-worker-results.json", expected)
        else:
            raise RunnerError("operation_closure_conflict")
    return expected


def _terminal_state(result: WorkerResult) -> OperationState:
    if result.status is WorkerStatus.COMPLETED:
        return OperationState.COMPLETED
    if result.status is WorkerStatus.FAILED:
        return OperationState.FAILED
    return OperationState.INTERRUPTED


def _commit_worker_result(
    store: EvidenceStore,
    ledger: OperationLedger,
    operation_id: str,
    result: WorkerResult,
    *,
    crash_injector: Callable[[str], None] | None,
) -> None:
    authoritative = OperationLedger.from_private_dict(
        store.read("private-operation-ledger.json")
    )
    if authoritative.to_private_dict() != ledger.to_private_dict():
        raise RunnerError("operation_closure_conflict")
    current = authoritative.state(operation_id)
    if result.started_persisted:
        if current is not OperationState.STARTED:
            raise RunnerError("operation_closure_conflict")
        terminal = _terminal_state(result)
        consumed = result.bytes_consumed
    else:
        if current is not OperationState.INTENT or result.status not in {
            WorkerStatus.INTERRUPTED,
            WorkerStatus.BLOCKED,
        }:
            raise RunnerError("operation_closure_conflict")
        terminal = OperationState.RECOVERED
        consumed = 0
    controller_payload = {
        "result": dict(result.payload) if result.payload is not None else None,
        "started_persisted": result.started_persisted,
        "exit_confirmed": result.exit_confirmed,
        "elapsed_ms": result.elapsed_ms,
    }
    ledger.commit_terminal(
        operation_id,
        terminal,
        result.safe_code,
        bytes_consumed=consumed,
        payload=controller_payload,
    )
    _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_terminal_ledger_commit")
    _persist_worker_projection(store, ledger, crash_injector=crash_injector)
    if not result.exit_confirmed:
        raise RunnerError("worker_termination_unconfirmed", public_code="worker_termination_unconfirmed")


def _manifest_from_private(raw: Mapping[str, Any]) -> FixedCutManifest:
    try:
        return FixedCutManifest.build(
            run_id=str(raw["run_id"]),
            source_scope_fingerprint=str(raw["source_scope_fingerprint"]),
            directory_observation=raw["directory_observation"],
            directories=tuple(
                ManifestDirectory(
                    tuple(item["component_chain"]),
                    item["observation"],
                    item["parent_object_identity"],
                    item["parent_change_identity"],
                )
                for item in raw["directories"]
            ),
            members=tuple(
                ManifestMember(
                    str(item["item_id"]),
                    str(item["private_name"]),
                    item["object_identity"],
                    item["change_identity"],
                    tuple(item["component_chain"]),
                    item["parent_object_identity"],
                    item["parent_change_identity"],
                    int(item["attributes"]),
                    int(item["reparse_tag"]),
                    int(item["link_count"]),
                )
                for item in raw["members"]
            ),
            snapshot_fingerprint=str(raw["snapshot_fingerprint"]),
        )
    except (KeyError, TypeError, ValueError, EvidenceError) as exc:
        raise RunnerError("synthetic_resume_manifest_invalid") from exc


def _snapshot_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"directory_observation", "directories", "members", "enumeration_usage"}
    if set(payload) != expected:
        raise RunnerError("synthetic_snapshot_invalid")
    if not isinstance(payload["directories"], list) or not isinstance(payload["members"], list):
        raise RunnerError("synthetic_snapshot_invalid")
    return {
        "directory_observation": payload["directory_observation"],
        "directories": payload["directories"],
        "members": payload["members"],
    }


def _remaining_enumeration_budget(config: Mapping[str, Any], ledger: OperationLedger) -> dict[str, int]:
    counters = ledger.run_counters()
    configured = config["enumeration_budget"]
    remaining = {
        "max_entries": int(configured["max_entries"]) - counters["entries_discovered"],
        "max_pages": int(configured["max_pages"]) - counters["enumeration_pages"],
        "max_metadata_bytes": int(configured["max_metadata_bytes"]) - counters["metadata_bytes"],
        "max_directories": int(configured["max_directories"]) - counters["directories_discovered"],
        "max_depth": int(configured["max_depth"]),
    }
    if min(remaining.values()) <= 0:
        raise RunnerError("run_wide_enumeration_budget_exhausted")
    return remaining


def _validate_run_counters(ledger: OperationLedger, budget: FailureBudget) -> dict[str, int]:
    counters = ledger.run_counters()
    limits = {
        "directories_discovered": budget.max_directories,
        "entries_discovered": budget.max_entries,
        "enumeration_pages": budget.max_enumeration_pages,
        "metadata_observations": budget.max_metadata_observations,
        "metadata_bytes": budget.max_metadata_bytes,
        "content_opens": budget.max_content_opens,
        "content_bytes": budget.max_bytes,
        "decoded_structure_bytes": budget.max_decoded_structure_bytes,
        "hash_operations": budget.max_hash_operations,
        "structure_validations": budget.max_structure_validations,
        "total_operations": budget.max_operations,
        "failed_operations": budget.max_failures,
        "retry_operations": budget.max_retries,
        "maximum_concurrent_workers": budget.max_concurrent_workers,
        "external_cost_usd": 0,
    }
    if any(counters[key] > maximum for key, maximum in limits.items()):
        raise RunnerError("run_wide_budget_exhausted")
    if counters["interrupted_operations"] > budget.max_failures:
        raise RunnerError("run_wide_budget_exhausted")
    if time.time_ns() - ledger.run_started_at_ns > int(budget.max_run_seconds * 1_000_000_000):
        raise RunnerError("run_wide_deadline_exceeded")
    return counters


def _canonical_evidence_bytes(*payloads: Mapping[str, Any]) -> int:
    return sum(
        len(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
        for payload in payloads
    )


def _persisted_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def finalize_synthetic_evidence_bundle(evidence_root: Path) -> dict[str, Any]:
    """Finalize the receipt-bound public summary and exact evidence-byte fixed point."""

    store = EvidenceStore(evidence_root)
    config = store.read("private-run-config.json")
    try:
        budget = FailureBudget(**config["budget"])
    except (KeyError, TypeError, EvidenceError) as exc:
        raise RunnerError("synthetic_run_budget_invalid") from exc
    summary = store.read("public-summary.json")
    receipt = store.read("local-validation-receipt.json")
    manifest_payload = store.read("private-manifest.json")
    ledger_payload = store.read("private-operation-ledger.json")
    worker_payload = store.read("private-worker-results.json")
    required = (
        "private-run-config.json",
        "private-manifest.json",
        "private-operation-ledger.json",
        "private-worker-results.json",
        "local-validation-receipt.json",
    )
    base_bytes = sum(
        len(store.read_bytes(name, max_bytes=budget.max_evidence_bytes + 1))
        for name in required
    )
    bindings = summary.get("evidence_bindings")
    if not isinstance(bindings, dict):
        raise RunnerError("synthetic_public_summary_invalid")
    bindings.update(
        {
            "config": canonical_fingerprint(config),
            "policy": canonical_fingerprint(config["policy"]),
            "manifest": manifest_payload["manifest_fingerprint"],
            "ledger": canonical_fingerprint(ledger_payload),
            "worker": canonical_fingerprint(worker_payload),
            "receipt": canonical_fingerprint(receipt),
        }
    )
    summary["status"] = "synthetic_implementation_evidence_complete"
    run_budget = summary.get("run_budget")
    if not isinstance(run_budget, dict):
        raise RunnerError("synthetic_public_summary_invalid")
    run_budget["synthetic_marker_bytes"] = config["confinement"][
        "synthetic_marker_bytes"
    ]
    run_budget["synthetic_marker_budget_scope"] = config["confinement"][
        "synthetic_marker_budget_scope"
    ]
    previous = -1
    for _ in range(16):
        total = base_bytes + len(_persisted_json_bytes(summary))
        run_budget["evidence_disk_bytes"] = total
        if total == previous:
            break
        previous = total
    else:
        raise RunnerError("evidence_disk_fixed_point_unavailable")
    if total > budget.max_evidence_bytes:
        raise RunnerError("run_wide_evidence_disk_budget_exhausted")
    store.write("public-summary.json", summary)
    observed = base_bytes + len(
        store.read_bytes(
            "public-summary.json", max_bytes=budget.max_evidence_bytes + 1
        )
    )
    if observed != total:
        raise RunnerError("evidence_disk_fixed_point_mismatch")
    return summary


def _worker_member_payload(member: ManifestMember) -> dict[str, Any]:
    return {
        "name": member.private_name,
        "member_type": "file",
        "object_identity": dict(member.object_identity),
        "change_identity": dict(member.change_identity),
        "attributes": member.attributes,
        "reparse_tag": member.reparse_tag,
        "link_count": member.link_count,
    }


def _worker_ancestor_payloads(manifest: FixedCutManifest, member: ManifestMember) -> list[dict[str, Any]]:
    by_chain = {directory.component_chain: directory for directory in manifest.directories}
    payloads: list[dict[str, Any]] = []
    for length in range(1, len(member.component_chain)):
        chain = member.component_chain[:length]
        directory = by_chain.get(chain)
        if directory is None:
            raise RunnerError("synthetic_manifest_ancestor_missing")
        observation = directory.observation
        payloads.append(
            {
                "name": chain[-1],
                "member_type": "directory",
                "object_identity": observation["object_identity"],
                "change_identity": observation["change_identity"],
                "attributes": 0,
                "reparse_tag": observation["reparse_tag"],
                "link_count": observation["link_count"],
            }
        )
    return payloads


def _disposition_from_result(record: Mapping[str, Any]) -> ItemDisposition:
    status = record.get("status")
    if status == OperationState.INTERRUPTED.value:
        return ItemDisposition.INTERRUPTED
    if status != OperationState.COMPLETED.value:
        return ItemDisposition.FAILED
    envelope = record.get("payload")
    result = envelope.get("result") if isinstance(envelope, Mapping) else None
    media = result.get("media") if isinstance(result, Mapping) else None
    if not isinstance(media, Mapping):
        return ItemDisposition.FAILED
    if media.get("valid") is True and media.get("disposition") == "structure_valid":
        return ItemDisposition.CONTENT_VERIFIED
    if media.get("disposition") == "corrupt_media":
        return ItemDisposition.CORRUPT_MEDIA
    if media.get("disposition") == "unsupported":
        return ItemDisposition.UNSUPPORTED
    return ItemDisposition.FAILED


def run_synthetic_hardening(
    config_path: Path,
    *,
    crash_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    config, source, evidence, budget = _load_config(config_path)
    run_id = str(config["run_id"])
    store = EvidenceStore(evidence)
    config_fingerprint = canonical_fingerprint(config)
    policy_fingerprint = canonical_fingerprint(config["policy"])
    budget_fingerprint = canonical_fingerprint(budget.to_dict())
    controller = WorkerController()
    ledger_path = evidence / "private-operation-ledger.json"
    manifest_path = evidence / "private-manifest.json"
    workers_path = evidence / "private-worker-results.json"
    new_run_monotonic_start: float | None = None

    if workers_path.exists() and not ledger_path.exists():
        raise RunnerError("operation_closure_conflict")
    if ledger_path.exists() or ledger_path.is_symlink():
        ledger = OperationLedger.from_private_dict(store.read("private-operation-ledger.json"))
        if ledger.run_id != run_id or ledger.budget_fingerprint != budget_fingerprint:
            raise RunnerError("synthetic_resume_ledger_context_drift")
        if ledger.recover_residuals():
            _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_recovery_ledger_commit")
        _validate_or_rebuild_projection(store, ledger, evidence)
    else:
        new_run_monotonic_start = time.monotonic()
        ledger = OperationLedger(
            run_id,
            "pending_manifest",
            budget_fingerprint,
            run_started_at_ns=time.time_ns(),
        )

    deadline = (
        RunDeadline.for_new_run(budget, monotonic_start=new_run_monotonic_start)
        if new_run_monotonic_start is not None
        else RunDeadline.for_resume(ledger, budget)
    )

    if manifest_path.exists() or manifest_path.is_symlink():
        raw_manifest = store.read("private-manifest.json")
        manifest = _manifest_from_private(raw_manifest)
        if manifest.to_private_dict() != raw_manifest or manifest.run_id != run_id:
            raise RunnerError("synthetic_resume_manifest_drift")
        if ledger.manifest_fingerprint == "pending_manifest":
            listing_records = [
                record
                for record in ledger.committed_results.values()
                if record.get("kind") == WorkerOperation.LIST_DIRECTORY.value
                and record.get("status") == OperationState.COMPLETED.value
            ]
            if len(listing_records) != 1:
                raise RunnerError("operation_closure_conflict")
            ledger.manifest_fingerprint = manifest.manifest_fingerprint
            _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_manifest_ledger_binding")
        elif ledger.manifest_fingerprint != manifest.manifest_fingerprint:
            raise RunnerError("synthetic_resume_manifest_drift")
    else:
        prior_listing = [
            record
            for record in ledger.committed_results.values()
            if record.get("kind") == WorkerOperation.LIST_DIRECTORY.value
            and record.get("status") == OperationState.COMPLETED.value
        ]
        if prior_listing:
            if len(prior_listing) != 1:
                raise RunnerError("operation_closure_conflict")
            listing_record = prior_listing[0]
            listing_envelope = listing_record.get("payload")
            listing_payload = listing_envelope.get("result") if isinstance(listing_envelope, Mapping) else None
        else:
            discovery = _begin_operation_before_deadline(
                ledger,
                deadline,
                item_id="directory-membership",
                kind=WorkerOperation.LIST_DIRECTORY.value,
                attempt=1 + sum(
                    record.get("kind") == WorkerOperation.LIST_DIRECTORY.value
                    for record in ledger.committed_results.values()
                ),
                budget=budget,
                bytes_reserved=0,
            )
            _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_intent_ledger_commit")

            def persist_discovery_started() -> None:
                ledger.mark_started(discovery)
                _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_started_ledger_commit")

            listing_timeout = deadline.worker_timeout(budget.worker_deadline_seconds)
            listing = controller.run(
                WorkerOperation.LIST_DIRECTORY,
                {
                    "root": os.fspath(source),
                    "policy": config["policy"],
                    "enumeration_budget": config["enumeration_budget"],
                },
                deadline_seconds=listing_timeout,
                persist_started=persist_discovery_started,
            )
            _commit_worker_result(store, ledger, discovery, listing, crash_injector=crash_injector)
            if listing.status is not WorkerStatus.COMPLETED or listing.payload is None:
                raise RunnerError("synthetic_directory_listing_failed", public_code="worker_interrupted")
            listing_payload = listing.payload
        if not isinstance(listing_payload, Mapping):
            raise RunnerError("operation_closure_conflict")
        snapshot_identity = _snapshot_identity(listing_payload)
        directory_observation = listing_payload["directory_observation"]
        scope_fingerprint = canonical_fingerprint(directory_observation)
        manifest_directories = [
            ManifestDirectory(
                tuple(raw["component_chain"]),
                raw["observation"],
                raw["parent_object_identity"],
                raw["parent_change_identity"],
            )
            for raw in listing_payload["directories"]
        ]
        manifest_members: list[ManifestMember] = []
        for raw in listing_payload["members"]:
            name = str(raw["name"])
            if Path(name).suffix.casefold() not in ELIGIBLE_SUFFIXES:
                continue
            component_chain = tuple(str(value) for value in raw["component_chain"])
            item_id = canonical_fingerprint(
                {
                    "scope": scope_fingerprint,
                    "component_chain": component_chain,
                    "object_identity": raw["object_identity"],
                    "change_identity": raw["change_identity"],
                }
            )
            manifest_members.append(
                ManifestMember(
                    item_id,
                    name,
                    raw["object_identity"],
                    raw["change_identity"],
                    component_chain,
                    raw["parent_object_identity"],
                    raw["parent_change_identity"],
                    int(raw["attributes"]),
                    int(raw["reparse_tag"]),
                    int(raw["link_count"]),
                )
            )
        manifest = FixedCutManifest.build(
            run_id=run_id,
            source_scope_fingerprint=scope_fingerprint,
            directory_observation=directory_observation,
            members=manifest_members,
            directories=manifest_directories,
            snapshot_fingerprint=canonical_fingerprint(snapshot_identity),
        )
        ledger.manifest_fingerprint = manifest.manifest_fingerprint
        store.write("private-manifest.json", manifest.to_private_dict())
        _checkpoint(crash_injector, "after_manifest_commit")
        _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_manifest_ledger_binding")

    for member in manifest.members:
        prior = [
            record
            for record in ledger.committed_results.values()
            if record.get("item_id") == member.item_id and record.get("kind") == WorkerOperation.COMBINED_CONTENT.value
        ]
        executed_prior = [
            record
            for record in prior
            if record.get("status") != OperationState.RECOVERED.value
        ]
        if len(executed_prior) > 1:
            raise RunnerError("operation_closure_conflict")
        if executed_prior:
            ledger.set_disposition(
                member.item_id, _disposition_from_result(executed_prior[0])
            )
            _persist_and_verify_ledger(
                store,
                ledger,
                crash_injector=crash_injector,
                boundary="after_reconstructed_disposition_ledger_commit",
            )
            continue
        expected_size = int(member.change_identity["size"])
        reservation = max(1, expected_size)
        decoded_remaining = (
            budget.max_decoded_structure_bytes
            - ledger.run_counters()["decoded_structure_bytes"]
        )
        if decoded_remaining <= 0:
            raise RunnerError("run_wide_decoded_structure_budget_exhausted")
        operation_id = _begin_operation_before_deadline(
            ledger,
            deadline,
            item_id=member.item_id,
            kind=WorkerOperation.COMBINED_CONTENT.value,
            attempt=1 + len(prior),
            budget=budget,
            bytes_reserved=reservation,
        )
        _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_intent_ledger_commit")

        def persist_started(identifier: str = operation_id) -> None:
            ledger.mark_started(identifier)
            _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_started_ledger_commit")

        content_timeout = deadline.worker_timeout(budget.worker_deadline_seconds)
        result = controller.run(
            WorkerOperation.COMBINED_CONTENT,
            {
                "root": os.fspath(source),
                "expected_root_observation": manifest.directory_observation,
                "ancestor_members": _worker_ancestor_payloads(manifest, member),
                "member": _worker_member_payload(member),
                "max_bytes": reservation,
                "max_decoded_structure_bytes": decoded_remaining,
                "max_depth": 1024,
                "parser_deadline_monotonic": time.monotonic() + content_timeout,
                "policy": config["policy"],
                "enumeration_budget": config["enumeration_budget"],
            },
            deadline_seconds=content_timeout,
            persist_started=persist_started,
        )
        _commit_worker_result(store, ledger, operation_id, result, crash_injector=crash_injector)
        ledger.set_disposition(member.item_id, _disposition_from_result(ledger.committed_results[operation_id]))
        _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_disposition_ledger_commit")

    if set(ledger.item_dispositions) != {member.item_id for member in manifest.members}:
        raise RunnerError("operation_closure_conflict")

    prior_snapshots = [
        record
        for record in ledger.committed_results.values()
        if record.get("kind") == WorkerOperation.FINAL_SNAPSHOT.value
    ]
    if len(prior_snapshots) > 1:
        raise RunnerError("operation_closure_conflict")
    if prior_snapshots:
        final_record = prior_snapshots[0]
        envelope = final_record.get("payload")
        final_payload = envelope.get("result") if isinstance(envelope, Mapping) else None
    else:
        snapshot_operation = _begin_operation_before_deadline(
            ledger,
            deadline,
            item_id="final-directory-snapshot",
            kind=WorkerOperation.FINAL_SNAPSHOT.value,
            attempt=1,
            budget=budget,
            bytes_reserved=0,
        )
        _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_intent_ledger_commit")

        def persist_snapshot_started(identifier: str = snapshot_operation) -> None:
            ledger.mark_started(identifier)
            _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_started_ledger_commit")

        snapshot_timeout = deadline.worker_timeout(budget.worker_deadline_seconds)
        final_result = controller.run(
            WorkerOperation.FINAL_SNAPSHOT,
            {
                "root": os.fspath(source),
                "policy": config["policy"],
                "enumeration_budget": _remaining_enumeration_budget(config, ledger),
            },
            deadline_seconds=snapshot_timeout,
            persist_started=persist_snapshot_started,
        )
        _commit_worker_result(store, ledger, snapshot_operation, final_result, crash_injector=crash_injector)
        if final_result.status is not WorkerStatus.COMPLETED or final_result.payload is None:
            raise RunnerError("synthetic_final_snapshot_failed", public_code="worker_interrupted")
        final_payload = final_result.payload
    if not isinstance(final_payload, Mapping):
        raise RunnerError("operation_closure_conflict")
    if canonical_fingerprint(_snapshot_identity(final_payload)) != manifest.snapshot_fingerprint:
        raise RunnerError("synthetic_final_snapshot_drift")
    worker_payload = _validate_or_rebuild_projection(store, ledger, evidence)
    ledger_payload = ledger.to_private_dict()
    run_counters = _validate_run_counters(ledger, budget)
    evidence_bytes = sum(
        len(store.read_bytes(name, max_bytes=budget.max_evidence_bytes + 1))
        for name in (
            "private-run-config.json",
            "private-manifest.json",
            "private-operation-ledger.json",
            "private-worker-results.json",
        )
    )
    if evidence_bytes > budget.max_evidence_bytes:
        raise RunnerError("run_wide_evidence_disk_budget_exhausted")
    counts = {
        disposition.value: sum(value is disposition for value in ledger.item_dispositions.values())
        for disposition in ItemDisposition
    }
    public_summary = {
        "schema_version": PUBLIC_SCHEMA,
        "contract_id": CONTRACT_ID,
        "status": "synthetic_implementation_evidence_pending_receipt",
        "run_token": hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24],
        "item_counts": {"manifest": len(manifest.members), **counts},
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
            "synthetic_marker_bytes": config["confinement"]["synthetic_marker_bytes"],
            "synthetic_marker_budget_scope": config["confinement"][
                "synthetic_marker_budget_scope"
            ],
            "counters": run_counters,
        },
        "evidence_bindings": {
            "config": config_fingerprint,
            "policy": policy_fingerprint,
            "manifest": manifest.manifest_fingerprint,
            "ledger": canonical_fingerprint(ledger_payload),
            "worker": canonical_fingerprint(worker_payload),
        },
        "authorities": config["authorities"],
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
    store.write("public-summary.json", public_summary)
    _checkpoint(crash_injector, "after_public_summary_commit")
    return public_summary


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description=__doc__, add_help=True)
    parser.add_argument("--config", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        summary = run_synthetic_hardening(Path(args.config))
        print(render_public_json(summary))
        return 0
    except BaseException as exc:
        print(render_public_json(public_error_envelope(exc)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
