"""Synthetic-only SCV2-FL1-I2 pre-real hardening evidence runner."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# Direct-script invocation must establish the repository root before importing
# any repository module. Module invocation reaches the same canonical code path.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, os.fspath(REPOSITORY_ROOT))

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
CONFIG_SCHEMA = "violet.scv2-fl1-i2-synthetic-run-config.v2"
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
    "max_entries": 4096,
    "max_pages": 1024,
    "max_metadata_bytes": 4 * 1024 * 1024,
}


class RunnerError(RuntimeError):
    def __init__(self, code: str, *, public_code: str = "validation_failed") -> None:
        super().__init__(code)
        self.code = code
        self.public_code = public_code


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
    source = _validate_temp_root(source_root)
    evidence = _validate_temp_root(evidence_root)
    if source == evidence or _within(source, evidence) or _within(evidence, source):
        raise RunnerError("synthetic_roots_overlap")
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
    config = {
        "schema_version": CONFIG_SCHEMA,
        "run_id": identifier,
        "mode": "synthetic_new_temp_fixture",
        "source_root": os.fspath(source),
        "evidence_root": os.fspath(evidence),
        "policy": dict(CANONICAL_POLICY),
        "enumeration_budget": dict(CANONICAL_ENUMERATION_BUDGET),
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
    expected = {
        "schema_version",
        "run_id",
        "mode",
        "source_root",
        "evidence_root",
        "policy",
        "enumeration_budget",
        "budget",
        "authorities",
    }
    if set(payload) != expected or payload.get("schema_version") != CONFIG_SCHEMA or payload.get("mode") != "synthetic_new_temp_fixture":
        raise RunnerError("synthetic_run_config_invalid")
    source = _validate_temp_root(Path(str(payload["source_root"])))
    evidence = _validate_temp_root(Path(str(payload["evidence_root"])))
    if path.resolve(strict=True) != evidence / "private-run-config.json":
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
    if payload["policy"] != CANONICAL_POLICY or payload["enumeration_budget"] != CANONICAL_ENUMERATION_BUDGET:
        raise RunnerError("synthetic_run_policy_drift")
    try:
        budget = FailureBudget(**payload["budget"])
    except (TypeError, EvidenceError) as exc:
        raise RunnerError("synthetic_run_budget_invalid") from exc
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
    first = ledger._events(operation_id)[0]
    consumed = result.bytes_consumed
    if result.status in {WorkerStatus.INTERRUPTED, WorkerStatus.BLOCKED} and consumed == 0:
        consumed = first.bytes_reserved
    controller_payload = {
        "result": dict(result.payload) if result.payload is not None else None,
        "started_persisted": result.started_persisted,
        "exit_confirmed": result.exit_confirmed,
        "elapsed_ms": result.elapsed_ms,
    }
    ledger.commit_terminal(
        operation_id,
        _terminal_state(result),
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
            members=tuple(
                ManifestMember(
                    str(item["item_id"]),
                    str(item["private_name"]),
                    item["object_identity"],
                    item["change_identity"],
                )
                for item in raw["members"]
            ),
        )
    except (KeyError, TypeError, ValueError, EvidenceError) as exc:
        raise RunnerError("synthetic_resume_manifest_invalid") from exc


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
        ledger = OperationLedger(run_id, "pending_manifest", budget_fingerprint)

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
            discovery = ledger.begin(
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

            listing = controller.run(
                WorkerOperation.LIST_DIRECTORY,
                {
                    "root": os.fspath(source),
                    "policy": config["policy"],
                    "enumeration_budget": config["enumeration_budget"],
                },
                deadline_seconds=budget.worker_deadline_seconds,
                persist_started=persist_discovery_started,
            )
            _commit_worker_result(store, ledger, discovery, listing, crash_injector=crash_injector)
            if listing.status is not WorkerStatus.COMPLETED or listing.payload is None:
                raise RunnerError("synthetic_directory_listing_failed", public_code="worker_interrupted")
            listing_payload = listing.payload
        if not isinstance(listing_payload, Mapping):
            raise RunnerError("operation_closure_conflict")
        directory_observation = listing_payload["directory_observation"]
        scope_fingerprint = canonical_fingerprint(directory_observation)
        manifest_members: list[ManifestMember] = []
        for raw in listing_payload["members"]:
            name = str(raw["name"])
            if Path(name).suffix.casefold() not in ELIGIBLE_SUFFIXES:
                continue
            item_id = canonical_fingerprint(
                {
                    "scope": scope_fingerprint,
                    "name": name,
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
                )
            )
        manifest = FixedCutManifest.build(
            run_id=run_id,
            source_scope_fingerprint=scope_fingerprint,
            directory_observation=directory_observation,
            members=manifest_members,
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
        if len(prior) > 1:
            raise RunnerError("operation_closure_conflict")
        if prior:
            ledger.set_disposition(member.item_id, _disposition_from_result(prior[0]))
            continue
        expected_size = int(member.change_identity["size"])
        reservation = max(1, expected_size)
        operation_id = ledger.begin(
            item_id=member.item_id,
            kind=WorkerOperation.COMBINED_CONTENT.value,
            attempt=1,
            budget=budget,
            bytes_reserved=reservation,
        )
        _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_intent_ledger_commit")

        def persist_started(identifier: str = operation_id) -> None:
            ledger.mark_started(identifier)
            _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_started_ledger_commit")

        result = controller.run(
            WorkerOperation.COMBINED_CONTENT,
            {
                "root": os.fspath(source),
                "member_name": member.private_name,
                "expected_root_observation": manifest.directory_observation,
                "expected_member_object_identity": member.object_identity,
                "expected_member_change_identity": member.change_identity,
                "max_bytes": reservation,
                "max_depth": 1024,
                "parser_deadline_monotonic": time.monotonic() + budget.worker_deadline_seconds,
                "policy": config["policy"],
                "enumeration_budget": config["enumeration_budget"],
            },
            deadline_seconds=budget.worker_deadline_seconds,
            persist_started=persist_started,
        )
        _commit_worker_result(store, ledger, operation_id, result, crash_injector=crash_injector)
        ledger.set_disposition(member.item_id, _disposition_from_result(ledger.committed_results[operation_id]))
        _persist_ledger(store, ledger, crash_injector=crash_injector, boundary="after_disposition_ledger_commit")

    if set(ledger.item_dispositions) != {member.item_id for member in manifest.members}:
        raise RunnerError("operation_closure_conflict")
    worker_payload = _validate_or_rebuild_projection(store, ledger, evidence)
    ledger_payload = ledger.to_private_dict()
    counts = {
        disposition.value: sum(value is disposition for value in ledger.item_dispositions.values())
        for disposition in ItemDisposition
    }
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
        "run_budget": {
            "maximum_bytes": budget.max_bytes,
            "consumed_bytes": ledger.consumed_bytes,
            "remaining_bytes": ledger.remaining_bytes(budget),
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
