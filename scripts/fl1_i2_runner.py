"""Synthetic-only SCV2-FL1-I2 pre-real hardening evidence runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.fl1_i1_operation_gateway import load_private_json
from scripts.fl1_i2_cli import public_error_envelope, render_public_json
from scripts.fl1_i2_evidence import (
    EvidenceError,
    EvidenceStore,
    FailureBudget,
    FixedCutManifest,
    ItemDisposition,
    ManifestMember,
    OperationLedger,
    OperationState,
    canonical_fingerprint,
)
from scripts.fl1_i2_worker import WorkerController, WorkerOperation, WorkerResult, WorkerStatus


CONTRACT_ID = "scv2_fl1_i2_pre_real_hardening_contract_v1"
PUBLIC_SCHEMA = "violet.scv2-fl1-i2-public-summary.v1"
CONFIG_SCHEMA = "violet.scv2-fl1-i2-synthetic-run-config.v1"
MARKER_NAME = ".violet-synthetic-fixture.json"
ELIGIBLE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"})


class RunnerError(RuntimeError):
    def __init__(self, code: str, *, public_code: str = "validation_failed") -> None:
        super().__init__(code)
        self.code = code
        self.public_code = public_code


def _within(candidate: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((os.fspath(candidate), os.fspath(root))) == os.fspath(root)
    except ValueError:
        return False


def _validate_temp_root(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    except OSError as exc:
        raise RunnerError("synthetic_temp_root_invalid") from exc
    if not resolved.is_dir() or resolved == temp_root or not _within(resolved, temp_root):
        raise RunnerError("synthetic_temp_root_invalid")
    current = resolved
    while current != temp_root:
        metadata = os.lstat(current)
        if os.path.islink(current) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise RunnerError("synthetic_temp_root_alias_rejected")
        current = current.parent
    return resolved


def create_synthetic_run_config(
    *,
    source_root: Path,
    evidence_root: Path,
    run_id: str | None = None,
    budget: FailureBudget | None = None,
) -> Path:
    """Initialize task-owned config/marker for a newly created temp fixture."""

    source = _validate_temp_root(source_root)
    evidence = _validate_temp_root(evidence_root)
    if source == evidence or _within(source, evidence) or _within(evidence, source):
        raise RunnerError("synthetic_roots_overlap")
    identifier = run_id or uuid.uuid4().hex
    marker = source / MARKER_NAME
    if marker.exists() or marker.is_symlink():
        raise RunnerError("synthetic_fixture_marker_already_exists")
    active_budget = budget or FailureBudget(3, 100, 8 * 1024 * 1024, 10)
    policy = {
        "policy_version": "scv2-fl1-i2-source-safety.v1",
        "allowed_source_kinds": ["path_source"],
        "require_known_attributes": True,
        "require_no_follow": True,
        "require_identity_bound": True,
        "reject_reparse_points": True,
        "reject_multiple_links": True,
        "reject_recall_risk": True,
    }
    marker_payload = {
        "schema_version": "violet.scv2-fl1-i2-synthetic-fixture-marker.v1",
        "run_id": identifier,
        "mode": "synthetic_new_temp_fixture",
        "created_at_ns": time.time_ns(),
    }
    EvidenceStore(source).write(MARKER_NAME, marker_payload)
    config = {
        "schema_version": CONFIG_SCHEMA,
        "run_id": identifier,
        "mode": "synthetic_new_temp_fixture",
        "source_root": os.fspath(source),
        "evidence_root": os.fspath(evidence),
        "policy": policy,
        "budget": active_budget.to_dict(),
        "authorities": {
            "real_source": False,
            "database": False,
            "app_storage": False,
            "import": False,
            "classification_or_tagging": False,
            "provider_or_llm": False,
            "media_download": False,
            "stable_replay": False,
            "production": False,
        },
    }
    EvidenceStore(evidence).write("private-run-config.json", config)
    return evidence / "private-run-config.json"


def _load_config(path: Path) -> tuple[dict[str, Any], Path, Path, FailureBudget]:
    payload = load_private_json(path)
    expected = {"schema_version", "run_id", "mode", "source_root", "evidence_root", "policy", "budget", "authorities"}
    if set(payload) != expected or payload.get("schema_version") != CONFIG_SCHEMA or payload.get("mode") != "synthetic_new_temp_fixture":
        raise RunnerError("synthetic_run_config_invalid")
    source = _validate_temp_root(Path(str(payload["source_root"])))
    evidence = _validate_temp_root(Path(str(payload["evidence_root"])))
    if path.resolve(strict=True) != (evidence / "private-run-config.json"):
        raise RunnerError("synthetic_run_config_location_invalid")
    marker = load_private_json(source / MARKER_NAME)
    if marker.get("run_id") != payload["run_id"] or marker.get("mode") != "synthetic_new_temp_fixture":
        raise RunnerError("synthetic_fixture_marker_invalid")
    expected_authorities = {
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
    if payload["authorities"] != expected_authorities:
        raise RunnerError("synthetic_run_authority_escalation")
    try:
        budget = FailureBudget(**payload["budget"])
    except (TypeError, EvidenceError) as exc:
        raise RunnerError("synthetic_run_budget_invalid") from exc
    return payload, source, evidence, budget


def _worker_private(result: WorkerResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "safe_code": result.safe_code,
        "payload": dict(result.payload) if result.payload is not None else None,
        "started_persisted": result.started_persisted,
        "exit_confirmed": result.exit_confirmed,
        "elapsed_ms": result.elapsed_ms,
    }


def _close_from_worker(ledger: OperationLedger, operation_id: str, result: WorkerResult) -> None:
    if result.status is WorkerStatus.COMPLETED:
        ledger.close(operation_id, OperationState.COMPLETED, result.safe_code)
    elif result.status is WorkerStatus.FAILED:
        ledger.close(operation_id, OperationState.FAILED, result.safe_code)
    else:
        ledger.close(operation_id, OperationState.INTERRUPTED, result.safe_code)
    if not result.exit_confirmed:
        raise RunnerError("worker_termination_unconfirmed", public_code="worker_termination_unconfirmed")


def run_synthetic_hardening(config_path: Path) -> dict[str, Any]:
    config, source, evidence, budget = _load_config(config_path)
    run_id = str(config["run_id"])
    store = EvidenceStore(evidence)
    config_fingerprint = canonical_fingerprint(config)
    policy_fingerprint = canonical_fingerprint(config["policy"])
    budget_fingerprint = canonical_fingerprint(budget.to_dict())
    ledger = OperationLedger(run_id, "pending_manifest", budget_fingerprint)
    controller = WorkerController()
    worker_records: list[dict[str, Any]] = []

    discovery = ledger.begin(item_id="directory-membership", attempt=1, budget=budget)
    store.write("private-operation-ledger.json", ledger.to_private_dict())

    def persist_discovery_started() -> None:
        ledger.mark_started(discovery)
        store.write("private-operation-ledger.json", ledger.to_private_dict())

    listing = controller.run(
        WorkerOperation.LIST_DIRECTORY,
        {"root": os.fspath(source), "policy": config["policy"]},
        deadline_seconds=budget.worker_deadline_seconds,
        persist_started=persist_discovery_started,
    )
    _close_from_worker(ledger, discovery, listing)
    worker_records.append({"operation_id": discovery, "kind": "list_directory", **_worker_private(listing)})
    if listing.status is not WorkerStatus.COMPLETED or listing.payload is None:
        store.write("private-operation-ledger.json", ledger.to_private_dict())
        raise RunnerError("synthetic_directory_listing_failed", public_code="worker_interrupted")
    directory_identity = listing.payload["directory_observation"]["object_identity"]
    scope_fingerprint = canonical_fingerprint(directory_identity)
    manifest_members: list[ManifestMember] = []
    for raw in listing.payload["members"]:
        name = str(raw["name"])
        if Path(name).suffix.casefold() not in ELIGIBLE_SUFFIXES:
            continue
        item_id = canonical_fingerprint({"scope": scope_fingerprint, "name": name, "identity": raw["object_identity"]})
        manifest_members.append(ManifestMember(item_id, name, raw["object_identity"]))
    manifest = FixedCutManifest.build(run_id=run_id, source_scope_fingerprint=scope_fingerprint, members=manifest_members)
    ledger.manifest_fingerprint = manifest.manifest_fingerprint
    store.write("private-manifest.json", manifest.to_private_dict())
    store.write("private-operation-ledger.json", ledger.to_private_dict())

    for member in manifest.members:
        item_failed = False
        for operation_kind in (WorkerOperation.HASH_FILE, WorkerOperation.VALIDATE_MEDIA):
            operation_id = ledger.begin(item_id=member.item_id, attempt=1, budget=budget)
            store.write("private-operation-ledger.json", ledger.to_private_dict())

            def persist_started(identifier: str = operation_id) -> None:
                ledger.mark_started(identifier)
                store.write("private-operation-ledger.json", ledger.to_private_dict())

            payload: dict[str, Any] = {
                "root": os.fspath(source),
                "member_name": member.private_name,
                "max_bytes": budget.max_bytes,
                "policy": config["policy"],
            }
            if operation_kind is WorkerOperation.VALIDATE_MEDIA:
                payload.update({"max_depth": 1024, "parser_deadline_monotonic": time.monotonic() + budget.worker_deadline_seconds})
            result = controller.run(operation_kind, payload, deadline_seconds=budget.worker_deadline_seconds, persist_started=persist_started)
            _close_from_worker(ledger, operation_id, result)
            worker_records.append({"operation_id": operation_id, "kind": operation_kind.value, **_worker_private(result)})
            store.write("private-operation-ledger.json", ledger.to_private_dict())
            if result.status is not WorkerStatus.COMPLETED:
                disposition = ItemDisposition.INTERRUPTED if result.status in {WorkerStatus.INTERRUPTED, WorkerStatus.BLOCKED} else ItemDisposition.FAILED
                ledger.set_disposition(member.item_id, disposition)
                item_failed = True
                break
            if operation_kind is WorkerOperation.VALIDATE_MEDIA and result.payload and not result.payload.get("valid"):
                ledger.set_disposition(member.item_id, ItemDisposition.CORRUPT_MEDIA)
                item_failed = True
        if not item_failed:
            ledger.set_disposition(member.item_id, ItemDisposition.CONTENT_VERIFIED)
        store.write("private-operation-ledger.json", ledger.to_private_dict())

    worker_payload = {"schema_version": "violet.scv2-fl1-i2-private-worker-results.v1", "run_id": run_id, "records": worker_records}
    store.write("private-worker-results.json", worker_payload)
    ledger_payload = ledger.to_private_dict()
    counts = {disposition.value: sum(value is disposition for value in ledger.item_dispositions.values()) for disposition in ItemDisposition}
    public_summary = {
        "schema_version": PUBLIC_SCHEMA,
        "contract_id": CONTRACT_ID,
        "status": "synthetic_implementation_evidence_complete",
        "run_token": hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24],
        "item_counts": {"manifest": len(manifest.members), **counts},
        "operation_counts": {
            "total": ledger.operation_count,
            "completed": sum(event.state is OperationState.COMPLETED for event in ledger.events),
            "failed": ledger.failure_count,
            "interrupted": sum(event.state is OperationState.INTERRUPTED for event in ledger.events),
            "recovered": sum(event.state is OperationState.RECOVERED for event in ledger.events),
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
        "public_redaction": {"passed": True, "paths_redacted": True, "filenames_redacted": True, "object_ids_redacted": True, "content_hashes_redacted": True},
    }
    store.write("public-summary.json", public_summary)
    return public_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_synthetic_hardening(Path(args.config))
        print(render_public_json(summary))
        return 0
    except BaseException as exc:
        print(render_public_json(public_error_envelope(exc)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
