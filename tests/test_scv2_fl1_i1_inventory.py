from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.fl1_i1_inventory import (
    InventoryBudgets,
    InventoryDisposition,
    InventoryError,
    InventoryRunner,
    PrivateManifest,
    PrivateRunLedger,
    RunStatus,
    _item_identity,
    _process_identity_for_pid,
)
from scripts.fl1_i1_operation_gateway import (
    CloudAvailability,
    OperationGatewayError,
    OperationKind,
    OperationLedger,
    SyntheticAttributeAdapter,
)
from scripts.fl1_i1_restart_harness import run_controlled_restart_harness
from scripts.phase_contracts import ContractRepositoryContext, check_phase_contract
from scripts.phase_contracts.fl1_i1_contract import derive_canonical_public_projection
from tests.fl1_i1_helpers import I1Fixture, make_i1_fixture, run_cli, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source_state(source: Path) -> dict[str, tuple[int, int, str]]:
    state: dict[str, tuple[int, int, str]] = {}
    for path in sorted(source.rglob("*")):
        metadata = os.stat(path, follow_symlinks=False)
        digest = "directory"
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        state[path.relative_to(source).as_posix()] = (
            metadata.st_size,
            metadata.st_mtime_ns,
            digest,
        )
    return state


def _run_direct(
    fixture: I1Fixture,
    *,
    adapter: SyntheticAttributeAdapter | None = None,
    budgets: InventoryBudgets | None = None,
    stop_after_items: int | None = None,
) -> InventoryRunner:
    runner = InventoryRunner(
        context=fixture.context(),
        budgets=budgets or fixture.budgets,
        evidence_root=fixture.evidence,
        attribute_adapter=adapter
        or SyntheticAttributeAdapter(
            observations={
                "e.webp": CloudAvailability.RECALL_RISK,
                "g.gif": CloudAvailability.UNKNOWN,
            }
        ),
    )
    runner.run(stop_after_items=stop_after_items)
    return runner


def _cross_process_complete(fixture: I1Fixture) -> tuple[dict, dict, Path]:
    first_payload, second_payload, run_dir = run_controlled_restart_harness(
        project_root=PROJECT_ROOT,
        repo_root=fixture.repo,
        private_root_config=fixture.private_config,
        source_root=fixture.source,
        source_scope_id="pytest-temporary-fixture",
        evidence_root=fixture.evidence,
        budgets_config=fixture.budgets_config,
        synthetic_attributes=fixture.synthetic_attributes,
        stop_after_items=2,
    )
    assert first_payload["status"] == RunStatus.CONTROLLED_STOP.value
    assert first_payload["unresolved"] == 5
    assert second_payload["status"] == RunStatus.COMPLETE.value
    assert second_payload["unresolved"] == 0
    return first_payload, second_payload, run_dir


def _create_validation_receipt(fixture: I1Fixture) -> tuple[Path, Path]:
    report = fixture.evidence / "focused-validation-report.json"
    receipt = fixture.evidence / "local-validation-receipt.json"
    completed = subprocess.run(
        [
            sys.executable,
            os.fspath(PROJECT_ROOT / "scripts" / "fl1_i1_validation_receipt.py"),
            "--repo-root", os.fspath(fixture.repo),
            "--private-root-config", os.fspath(fixture.private_config),
            "--source-root", os.fspath(fixture.source),
            "--source-scope-id", "pytest-temporary-fixture",
            "--report", os.fspath(report),
            "--output", os.fspath(receipt),
            "--", sys.executable, "-c", "print('synthetic-focused')",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return receipt, report


def test_synthetic_inventory_denominator_duplicate_cloud_and_read_only_source(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    before = _source_state(fixture.source)
    runner = _run_direct(fixture)
    after = _source_state(fixture.source)
    denominator = runner.manifest.denominator()

    assert runner.ledger.status is RunStatus.COMPLETE
    assert denominator == {
        "discovered": 7,
        "supported": 6,
        "unsupported": 1,
        "duplicate": 1,
        "cloud_recall_deferred": 3,
        "unreadable_or_missing": 0,
        "eligible_candidate": 2,
        "imported": 0,
        "import_deferred": 2,
        "import_failed": 0,
        "unresolved": 0,
    }
    assert before == after
    assert runner.ledger.source_mutation_count == 0
    duplicate = next(
        item for item in runner.manifest.items if item.disposition is InventoryDisposition.DUPLICATE
    )
    primary = next(item for item in runner.manifest.items if item.item_id == duplicate.duplicate_of_item_id)
    assert duplicate.content_fingerprint == primary.content_fingerprint
    operations = OperationLedger.from_dict(
        json.loads(runner.operation_path.read_text(encoding="utf-8"))
    )
    deferred_items = {
        item.item_id
        for item in runner.manifest.items
        if item.disposition is InventoryDisposition.CLOUD_RECALL_DEFERRED
    }
    assert not any(
        record.item_id in deferred_items
        and record.kind in {OperationKind.SOURCE_FILE_READ, OperationKind.SOURCE_FILE_HASH}
        for record in operations.records
    )


def test_manual_stop_does_not_start_next_item_and_same_process_resume_fails(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    runner = _run_direct(fixture, stop_after_items=1)
    assert runner.ledger.status is RunStatus.CONTROLLED_STOP
    assert [item.attempt_count for item in runner.manifest.items] == [1, 0, 0, 0, 0, 0, 0]
    checkpoint = runner.ledger.checkpoint_fingerprint
    with pytest.raises(InventoryError, match="same_process_restart_rejected"):
        InventoryRunner(
            context=fixture.context(),
            budgets=fixture.budgets,
            evidence_root=fixture.evidence,
            attribute_adapter=SyntheticAttributeAdapter(observations={}),
            run_id=runner.run_id,
            resume=True,
            expected_parent_checkpoint=checkpoint,
        )


def test_distinct_python_process_resume_preserves_attempts_and_skips_terminal_content(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    first, second, run_dir = _cross_process_complete(fixture)
    ledger = PrivateRunLedger.from_dict(
        json.loads((run_dir / "private-run-ledger.json").read_text(encoding="utf-8"))
    )
    manifest = PrivateManifest.from_dict(
        json.loads((run_dir / "private-manifest.json").read_text(encoding="utf-8"))
    )
    operations = OperationLedger.from_dict(
        json.loads((run_dir / "private-operation-ledger.json").read_text(encoding="utf-8"))
    )
    assert first["invocation_id"] != second["invocation_id"]
    assert len(ledger.invocations) == 2
    assert ledger.invocations[0].pid != ledger.invocations[1].pid
    assert ledger.invocations[1].parent_checkpoint_fingerprint == first["checkpoint_fingerprint"]
    assert all(item.attempt_count == 1 for item in manifest.items)
    first_terminal_ids = {
        item.item_id
        for item in manifest.items
        if item.terminal_invocation_id == first["invocation_id"]
    }
    assert len(first_terminal_ids) == 2
    assert not any(
        record.invocation_id == second["invocation_id"]
        and record.item_id in first_terminal_ids
        and record.kind
        in {
            OperationKind.SOURCE_CLOUD_ATTRIBUTE_OBSERVATION,
            OperationKind.SOURCE_FILE_READ,
            OperationKind.SOURCE_FILE_HASH,
        }
        for record in operations.records
    )


def test_abrupt_exit_after_intent_is_terminalized_and_retried_by_new_process(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path)
    run_id = str(uuid.uuid4())
    signal = fixture.evidence / "abrupt-intent-signal.json"
    process = subprocess.run(
        [
            sys.executable,
            os.fspath(PROJECT_ROOT / "scripts" / "fl1_i1_inventory.py"),
            "scan",
            *fixture.scanner_args(),
            "--new-run-id",
            run_id,
            "--test-abrupt-exit-after-read-intent",
            "--test-intent-signal-output",
            os.fspath(signal),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 91
    assert json.loads(signal.read_text(encoding="utf-8"))["intent_persisted"] is True
    lock_payload = json.loads(
        (fixture.evidence / run_id / "runner.lock").read_text(encoding="utf-8")
    )
    death_deadline = time.monotonic() + 5
    while True:
        alive, identity = _process_identity_for_pid(int(lock_payload["pid"]))
        if not alive or identity != lock_payload["process_start_observation"]:
            break
        if time.monotonic() > death_deadline:
            pytest.fail("abrupt child remained live after parent observed exit")
        time.sleep(0.05)
    checkpoint_payload = json.loads(
        (fixture.evidence / run_id / "private-inventory-checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    parent_checkpoint = checkpoint_payload["run_ledger"]["checkpoint_fingerprint"]
    resumed = run_cli(
        PROJECT_ROOT,
        fixture,
        "--resume-run-id",
        run_id,
        "--parent-checkpoint",
        parent_checkpoint,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert json.loads(resumed.stdout)["status"] == RunStatus.COMPLETE.value
    operations = OperationLedger.from_dict(
        json.loads(
            (fixture.evidence / run_id / "private-operation-ledger.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert not any(record.status.value == "intent" for record in operations.records)
    assert any(record.status.value == "interrupted" for record in operations.records)
    manifest = PrivateManifest.from_dict(
        json.loads(
            (fixture.evidence / run_id / "private-manifest.json").read_text(
                encoding="utf-8"
            )
        )
    )
    first = next(item for item in manifest.items if item.private_relative_path == "a.jpg")
    assert first.attempt_count == 2


def test_wrong_parent_checkpoint_fails_without_second_scan(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    first = run_cli(PROJECT_ROOT, fixture, "--stop-after-items", "1")
    payload = json.loads(first.stdout)
    second = run_cli(
        PROJECT_ROOT,
        fixture,
        "--resume-run-id",
        payload["run_id"],
        "--parent-checkpoint",
        "0" * 64,
    )
    assert second.returncode == 1
    assert json.loads(second.stdout)["error"] == "resume_parent_checkpoint_mismatch"


def test_resume_rejects_budget_or_scope_configuration_drift(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    first = run_cli(PROJECT_ROOT, fixture, "--stop-after-items", "1")
    payload = json.loads(first.stdout)
    drifted = replace(fixture.budgets, batch_size=99)
    write_json(fixture.budgets_config, drifted.to_dict())
    second = run_cli(
        PROJECT_ROOT,
        fixture,
        "--resume-run-id",
        payload["run_id"],
        "--parent-checkpoint",
        payload["checkpoint_fingerprint"],
    )
    assert second.returncode == 1
    assert json.loads(second.stdout)["error"] == "resume_context_head_scope_budget_policy_drift"


def test_duplicate_runner_lock_fails_before_another_item(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    runner = InventoryRunner(
        context=fixture.context(),
        budgets=fixture.budgets,
        evidence_root=fixture.evidence,
        attribute_adapter=SyntheticAttributeAdapter(observations={}),
    )
    before = {
        path.name: path.read_bytes()
        for path in runner.run_dir.iterdir()
        if path.is_file()
    }
    with pytest.raises(InventoryError, match="duplicate_runner_lock_present"):
        InventoryRunner(
            context=fixture.context(),
            budgets=fixture.budgets,
            evidence_root=fixture.evidence,
            attribute_adapter=SyntheticAttributeAdapter(observations={}),
            run_id=runner.run_id,
            resume=True,
            expected_parent_checkpoint=runner.ledger.checkpoint_fingerprint,
        )
    after = {
        path.name: path.read_bytes()
        for path in runner.run_dir.iterdir()
        if path.is_file()
    }
    assert after == before
    runner.run(stop_after_items=1)


def test_copied_or_same_invocation_snapshot_cannot_prove_restart(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    first = run_cli(PROJECT_ROOT, fixture, "--stop-after-items", "1")
    payload = json.loads(first.stdout)
    run_path = fixture.evidence / payload["run_id"] / "private-run-ledger.json"
    raw = json.loads(run_path.read_text(encoding="utf-8"))
    copied = dict(raw["invocations"][0])
    copied["parent_checkpoint_fingerprint"] = raw["checkpoint_fingerprint"]
    raw["invocations"].append(copied)
    with pytest.raises(InventoryError, match="duplicate_invocation_id"):
        PrivateRunLedger.from_dict(raw)


def test_file_and_tree_race_fail_closed_with_incomplete_ledger(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)

    class MutatingAdapter(SyntheticAttributeAdapter):
        mutated = False

        def observe(self, path: Path):
            if not self.mutated:
                self.mutated = True
                (path.parent / "added-during-scan.jpg").write_bytes(b"race")
            return super().observe(path)

    runner = InventoryRunner(
        context=fixture.context(),
        budgets=fixture.budgets,
        evidence_root=fixture.evidence,
        attribute_adapter=MutatingAdapter(observations={}),
    )
    with pytest.raises(InventoryError, match="source_tree_changed_during_inventory"):
        runner.run()
    stored = PrivateRunLedger.from_dict(
        json.loads(runner.run_path.read_text(encoding="utf-8"))
    )
    assert stored.status is RunStatus.BLOCKED_INCOMPLETE
    assert stored.source_mutation_count == 1


def test_signature_change_between_discovery_and_open_is_structural_failure(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)

    class ReplacingAdapter(SyntheticAttributeAdapter):
        changed = False

        def observe(self, path: Path):
            if not self.changed:
                self.changed = True
                path.write_bytes(path.read_bytes() + b"changed")
            return super().observe(path)

    runner = InventoryRunner(
        context=fixture.context(),
        budgets=fixture.budgets,
        evidence_root=fixture.evidence,
        attribute_adapter=ReplacingAdapter(observations={}),
    )
    with pytest.raises(InventoryError, match="source_entry_changed_before_read"):
        runner.run()
    assert runner.ledger.status is RunStatus.BLOCKED_INCOMPLETE


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("max_discovered_items", 3, "discovered_item_budget_exceeded"),
        ("max_directory_entries", 3, "directory_entry_budget_exceeded"),
        ("max_total_observed_bytes", 3, "observed_byte_budget_exceeded"),
    ],
)
def test_discovery_budgets_stop_and_preserve_forensic_run(
    tmp_path: Path, field: str, value: int, error: str
) -> None:
    fixture = make_i1_fixture(tmp_path)
    budgets = replace(fixture.budgets, **{field: value})
    runner = InventoryRunner(
        context=fixture.context(),
        budgets=budgets,
        evidence_root=fixture.evidence,
        attribute_adapter=SyntheticAttributeAdapter(observations={}),
    )
    with pytest.raises(InventoryError, match=error):
        runner.run()
    stored = PrivateRunLedger.from_dict(
        json.loads(runner.run_path.read_text(encoding="utf-8"))
    )
    assert stored.status is RunStatus.BLOCKED_INCOMPLETE
    assert runner.manifest_path.is_file()


def test_hash_and_failure_budgets_stop_before_next_item(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    budgets = replace(fixture.budgets, max_total_hashed_bytes=40, max_per_file_hash_bytes=40)
    runner = _run_direct(fixture, budgets=budgets)
    assert runner.ledger.status is RunStatus.BUDGET_STOP
    assert runner.ledger.stop_reason == "total_hash_budget_exceeded"
    assert runner.manifest.denominator()["unresolved"] > 0

    fixture2 = make_i1_fixture(tmp_path / "failure-case")
    runner2 = InventoryRunner(
        context=fixture2.context(),
        budgets=replace(
            fixture2.budgets,
            max_unreadable_failures=1,
            max_consecutive_failures=1,
            max_same_reason_failures=1,
        ),
        evidence_root=fixture2.evidence,
        attribute_adapter=SyntheticAttributeAdapter(observations={}),
    )

    def fail_hash(*args, **kwargs):
        raise OperationGatewayError("source_read_timeout")

    runner2.gateway.hash_file = fail_hash  # type: ignore[method-assign]
    runner2.run()
    assert runner2.ledger.status is RunStatus.BUDGET_STOP
    assert runner2.ledger.total_unreadable_failures == 2
    assert [item.attempt_count for item in runner2.manifest.items[:3]] == [1, 1, 0]


def test_manifest_denominator_duplicate_and_unresolved_tamper_fail(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    runner = _run_direct(fixture)
    payload = json.loads(runner.manifest_path.read_text(encoding="utf-8"))
    payload["denominator"]["discovered"] += 1
    with pytest.raises(InventoryError, match="manifest_denominator_tamper"):
        PrivateManifest.from_dict(payload)

    payload = json.loads(runner.manifest_path.read_text(encoding="utf-8"))
    duplicate = next(item for item in payload["items"] if item["disposition"] == "duplicate")
    duplicate["duplicate_of_item_id"] = "f" * 64
    payload["manifest_fingerprint"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in payload.items() if key != "manifest_fingerprint"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(InventoryError, match="duplicate_primary_invalid"):
        PrivateManifest.from_dict(payload)

    item = runner.manifest.items[0]
    item.disposition = None
    item.reason_code = None
    item.content_fingerprint = None
    item.terminal_invocation_id = None
    assert runner.manifest.denominator()["unresolved"] == 1


def test_membership_rename_and_content_identity_are_distinct(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    context = fixture.context()
    original_id, _ = _item_identity(context, "a.jpg")
    renamed_id, _ = _item_identity(context, "renamed.jpg")
    content_before = hashlib.sha256((fixture.source / "a.jpg").read_bytes()).hexdigest()
    content_after = hashlib.sha256((fixture.source / "b.jpg").read_bytes()).hexdigest()
    assert original_id != renamed_id
    assert content_before == content_after


def test_manifest_item_swap_with_recomputed_editable_fingerprint_fails_derivation(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path)
    runner = _run_direct(fixture)
    payload = json.loads(runner.manifest_path.read_text(encoding="utf-8"))
    left, right = payload["items"][0], payload["items"][1]
    for field in ("private_relative_path", "signature", "observed_size", "extension"):
        left[field], right[field] = right[field], left[field]
    payload["manifest_fingerprint"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in payload.items() if key != "manifest_fingerprint"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    loaded = PrivateManifest.from_dict(payload)
    with pytest.raises(InventoryError, match="manifest_membership_derivation_mismatch"):
        loaded.validate_derivations(fixture.context())


def test_attribute_adapter_configuration_drift_fails_resume(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    first = run_cli(PROJECT_ROOT, fixture, "--stop-after-items", "1")
    assert first.returncode == 0
    payload = json.loads(first.stdout)
    write_json(
        fixture.synthetic_attributes,
        {"observations": {"e.webp": "available", "g.gif": "unknown"}},
    )
    resumed = run_cli(
        PROJECT_ROOT,
        fixture,
        "--resume-run-id",
        payload["run_id"],
        "--parent-checkpoint",
        payload["checkpoint_fingerprint"],
    )
    assert resumed.returncode == 1
    assert json.loads(resumed.stdout)["error"] == "resume_context_head_scope_budget_policy_drift"


def test_synthetic_attribute_unknown_configuration_field_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path)
    write_json(
        fixture.synthetic_attributes,
        {"observations": {}, "debug_override": "available"},
    )
    completed = run_cli(PROJECT_ROOT, fixture)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["error"] == "synthetic_attribute_payload_unknown_field"


def test_nested_directory_listings_close_against_both_snapshots(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    nested = fixture.source / "nested" / "deeper"
    nested.mkdir(parents=True)
    nested.joinpath("valid.jpg").write_bytes(
        b"\xff\xd8\xff\xe0nested-valid\xff\xd9"
    )
    runner = _run_direct(fixture)
    assert len(runner.manifest.discovery_directory_tokens) == 3
    assert runner.manifest.discovery_directory_tokens == runner.manifest.final_directory_tokens
    operations = OperationLedger.from_dict(
        json.loads(runner.operation_path.read_text(encoding="utf-8"))
    )
    listing_tokens = [
        record.target_token
        for record in operations.records
        if record.kind is OperationKind.SOURCE_DIRECTORY_LIST
    ]
    for token in runner.manifest.discovery_directory_tokens:
        assert listing_tokens.count(token) == 2


def test_final_snapshot_expansion_stops_inside_entry_budget(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)

    class ExpandingAdapter(SyntheticAttributeAdapter):
        expanded = False

        def observe(self, path: Path):
            if not self.expanded:
                self.expanded = True
                for index in range(40):
                    (path.parent / f"expansion-{index:03d}.txt").write_text(
                        "bounded", encoding="utf-8"
                    )
            return super().observe(path)

    runner = InventoryRunner(
        context=fixture.context(),
        budgets=replace(fixture.budgets, max_directory_entries=12),
        evidence_root=fixture.evidence,
        attribute_adapter=ExpandingAdapter(
            observations={
                "e.webp": CloudAvailability.RECALL_RISK,
                "g.gif": CloudAvailability.UNKNOWN,
            }
        ),
    )
    with pytest.raises(InventoryError, match="directory_entry_budget_exceeded"):
        runner.run()
    assert runner.ledger.status is RunStatus.BLOCKED_INCOMPLETE


def test_valid_duplicate_cloud_unsupported_and_invalid_media_share_exact_denominator(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path, populate=False)
    valid = b"\xff\xd8\xff\xe0valid-fixture\xff\xd9"
    (fixture.source / "a.jpg").write_bytes(valid)
    (fixture.source / "b.jpg").write_bytes(valid)
    (fixture.source / "c.jpg").write_text("plain text with image suffix", encoding="utf-8")
    (fixture.source / "d.png").write_bytes(b"")
    (fixture.source / "e.gif").write_bytes(b"GIF89a-truncated")
    (fixture.source / "f.webp").write_bytes(b"recall-risk-placeholder")
    (fixture.source / "g.txt").write_text("unsupported", encoding="utf-8")
    runner = _run_direct(
        fixture,
        adapter=SyntheticAttributeAdapter(
            observations={"f.webp": CloudAvailability.RECALL_RISK}
        ),
    )
    denominator = runner.manifest.denominator()
    assert denominator == {
        "discovered": 7,
        "supported": 6,
        "unsupported": 1,
        "duplicate": 1,
        "cloud_recall_deferred": 1,
        "unreadable_or_missing": 3,
        "eligible_candidate": 1,
        "imported": 0,
        "import_deferred": 1,
        "import_failed": 0,
        "unresolved": 0,
    }
    assert sum(
        item.reason_code == "corrupt_or_invalid_media" for item in runner.manifest.items
    ) == 3


@pytest.mark.parametrize("projection_name", ["private-manifest.json", "private-run-ledger.json"])
def test_checkpoint_recovers_stale_split_projection(
    tmp_path: Path, projection_name: str
) -> None:
    fixture = make_i1_fixture(tmp_path)
    first = run_cli(PROJECT_ROOT, fixture, "--stop-after-items", "1")
    payload = json.loads(first.stdout)
    projection = fixture.evidence / payload["run_id"] / projection_name
    projection.write_text("{}\n", encoding="utf-8")
    resumed = run_cli(
        PROJECT_ROOT,
        fixture,
        "--resume-run-id", payload["run_id"],
        "--parent-checkpoint", payload["checkpoint_fingerprint"],
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert json.loads(resumed.stdout)["status"] == RunStatus.COMPLETE.value


def test_i1_modules_have_no_database_network_provider_media_or_application_route_imports() -> None:
    forbidden_roots = {
        "backend",
        "requests",
        "httpx",
        "socket",
        "sqlalchemy",
        "psycopg",
        "gallery_dl",
        "openai",
    }
    modules = (
        PROJECT_ROOT / "scripts" / "fl1_i1_runtime_context.py",
        PROJECT_ROOT / "scripts" / "fl1_i1_operation_gateway.py",
        PROJECT_ROOT / "scripts" / "fl1_i1_inventory.py",
        PROJECT_ROOT / "scripts" / "fl1_i1_validation_receipt.py",
        PROJECT_ROOT / "scripts" / "phase_contracts" / "fl1_i1_contract.py",
    )
    imported: set[str] = set()
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
    assert imported.isdisjoint(forbidden_roots)


def test_contract_rebuilds_projection_and_rejects_counts_paths_unknown_fields_and_receipt_escalation(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path)
    _, completed, _ = _cross_process_complete(fixture)
    receipt_path, report = _create_validation_receipt(fixture)
    evidence = fixture.contract_evidence(
        run_id=completed["run_id"], receipt=receipt_path, report=report
    )
    summary = derive_canonical_public_projection(
        repo_root=fixture.repo,
        expected_python=Path(sys.executable),
        evidence=evidence,
    )
    context = ContractRepositoryContext(
        repo_root=fixture.repo,
        expected_python=Path(sys.executable),
        fl1_i1_evidence=evidence,
    )
    result = check_phase_contract(
        "scv2_fl1_i1_read_only_inventory_contract_v1",
        summary,
        repository_context=context,
    )
    assert result.passed, result.to_dict()
    assert summary["pipeline_contract"]["target_met"] is False
    assert summary["validation_receipt"]["machine_verifiable_ci"] is False
    assert os.fspath(fixture.root) not in json.dumps(summary)

    summary_path = fixture.root / "public-summary.json"
    evidence_path = fixture.root / "private-evidence-context.json"
    write_json(summary_path, summary)
    write_json(evidence_path, evidence)
    generated_summary_path = fixture.evidence / "generated-public-summary.json"
    derive_cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.phase_contracts.fl1_i1_contract",
            "--repo-root",
            os.fspath(fixture.repo),
            "--expected-python",
            os.fspath(Path(sys.executable)),
            "--evidence-context",
            os.fspath(evidence_path),
            "--output",
            os.fspath(generated_summary_path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert derive_cli.returncode == 0, derive_cli.stdout + derive_cli.stderr
    assert json.loads(generated_summary_path.read_text(encoding="utf-8")) == summary
    cli = subprocess.run(
        [
            sys.executable,
            os.fspath(PROJECT_ROOT / "scripts" / "check_phase_contract.py"),
            "--contract",
            "scv2_fl1_i1_read_only_inventory_contract_v1",
            "--summary",
            os.fspath(summary_path),
            "--repo-root",
            os.fspath(fixture.repo),
            "--expected-python",
            os.fspath(Path(sys.executable)),
            "--fl1-i1-evidence",
            os.fspath(evidence_path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert json.loads(cli.stdout)["passed"] is True

    count_tamper = json.loads(json.dumps(summary))
    count_tamper["denominator"]["discovered"] += 1
    assert not check_phase_contract(
        "scv2_fl1_i1_read_only_inventory_contract_v1",
        count_tamper,
        repository_context=context,
    ).passed

    unknown = json.loads(json.dumps(summary))
    unknown["debug"] = {"source_root": os.fspath(fixture.source)}
    unknown_result = check_phase_contract(
        "scv2_fl1_i1_read_only_inventory_contract_v1",
        unknown,
        repository_context=context,
    )
    assert not unknown_result.passed
    assert any("public" in finding.code for finding in unknown_result.errors)

    caller_claims = json.loads(json.dumps(summary))
    caller_claims["focused_tests_passed"] = True
    caller_claims["full_tests_passed"] = True
    assert not check_phase_contract(
        "scv2_fl1_i1_read_only_inventory_contract_v1",
        caller_claims,
        repository_context=context,
    ).passed

    content_leak = json.loads(json.dumps(summary))
    content_leak["debug_content_fingerprint"] = "a" * 64
    assert not check_phase_contract(
        "scv2_fl1_i1_read_only_inventory_contract_v1",
        content_leak,
        repository_context=context,
    ).passed

    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_payload["machine_verifiable_ci"] = True
    write_json(receipt_path, receipt_payload)
    escalated = check_phase_contract(
        "scv2_fl1_i1_read_only_inventory_contract_v1",
        summary,
        repository_context=context,
    )
    assert not escalated.passed
    assert any(finding.code == "fl1_i1_private_evidence_invalid" for finding in escalated.errors)


def test_old_head_manifest_cannot_be_combined_with_new_repo_context(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    first = run_cli(PROJECT_ROOT, fixture, "--stop-after-items", "1")
    payload = json.loads(first.stdout)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "new trusted head"],
        cwd=fixture.repo,
        check=True,
        capture_output=True,
    )
    resumed = run_cli(
        PROJECT_ROOT,
        fixture,
        "--resume-run-id",
        payload["run_id"],
        "--parent-checkpoint",
        payload["checkpoint_fingerprint"],
    )
    assert resumed.returncode == 1
    assert json.loads(resumed.stdout)["error"] == "resume_context_head_scope_budget_policy_drift"


def test_contract_rejects_one_item_read_evidence_removal_even_after_refingerprint(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path)
    _, completed, run_dir = _cross_process_complete(fixture)
    receipt, report = _create_validation_receipt(fixture)
    evidence = fixture.contract_evidence(
        run_id=completed["run_id"], receipt=receipt, report=report
    )
    operations_path = run_dir / "private-operation-ledger.json"
    operations = json.loads(operations_path.read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "private-manifest.json").read_text(encoding="utf-8"))
    eligible = next(
        item for item in manifest["items"] if item["disposition"] == "eligible_candidate"
    )
    operations["records"] = [
        record
        for record in operations["records"]
        if not (
            record["item_id"] == eligible["item_id"]
            and record["kind"] in {"source_file_read", "source_file_hash"}
        )
    ]
    for sequence, record in enumerate(operations["records"], start=1):
        record["sequence"] = sequence
    operations["ledger_fingerprint"] = hashlib.sha256(
        json.dumps(
            operations["records"], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    write_json(operations_path, operations)
    with pytest.raises(
        Exception,
        match="hashed_byte_counter_mismatch|eligible_read_hash_evidence_missing",
    ):
        derive_canonical_public_projection(
            repo_root=fixture.repo,
            expected_python=Path(sys.executable),
            evidence=evidence,
        )


def test_single_process_restart_claim_without_parent_harness_receipt_fails(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path)
    _, completed, run_dir = _cross_process_complete(fixture)
    receipt, report = _create_validation_receipt(fixture)
    harness = run_dir / "restart-parent-harness-receipt.json"
    harness.write_text("{}\n", encoding="utf-8")
    with pytest.raises(Exception, match="restart_parent_harness_missing"):
        derive_canonical_public_projection(
            repo_root=fixture.repo,
            expected_python=Path(sys.executable),
            evidence=fixture.contract_evidence(
                run_id=completed["run_id"], receipt=receipt, report=report
            ),
        )


def test_projection_and_receipt_output_escape_cannot_overwrite_source_fixture(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path)
    _, completed, _ = _cross_process_complete(fixture)
    receipt, report = _create_validation_receipt(fixture)
    evidence = fixture.contract_evidence(
        run_id=completed["run_id"], receipt=receipt, report=report
    )
    evidence_path = fixture.root / "private-evidence-context.json"
    write_json(evidence_path, evidence)
    source_target = fixture.source / "a.jpg"
    before = source_target.read_bytes()
    projection = subprocess.run(
        [
            sys.executable,
            "-m", "scripts.phase_contracts.fl1_i1_contract",
            "--repo-root", os.fspath(fixture.repo),
            "--expected-python", os.fspath(Path(sys.executable)),
            "--evidence-context", os.fspath(evidence_path),
            "--output", os.fspath(source_target),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert projection.returncode == 1
    assert source_target.read_bytes() == before

    receipt_escape = subprocess.run(
        [
            sys.executable,
            os.fspath(PROJECT_ROOT / "scripts" / "fl1_i1_validation_receipt.py"),
            "--repo-root", os.fspath(fixture.repo),
            "--private-root-config", os.fspath(fixture.private_config),
            "--source-root", os.fspath(fixture.source),
            "--source-scope-id", "pytest-temporary-fixture",
            "--report", os.fspath(source_target),
            "--output", os.fspath(source_target),
            "--", sys.executable, "-c", "print('bounded')",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert receipt_escape.returncode == 1
    assert source_target.read_bytes() == before


def test_validation_receipt_has_no_caller_supplied_pass_or_exit_code(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path)
    output = fixture.evidence / "failing-receipt.json"
    report = fixture.evidence / "failing-report.json"
    failing = subprocess.run(
        [
            sys.executable,
            os.fspath(PROJECT_ROOT / "scripts" / "fl1_i1_validation_receipt.py"),
            "--repo-root", os.fspath(fixture.repo),
            "--private-root-config", os.fspath(fixture.private_config),
            "--source-root", os.fspath(fixture.source),
            "--source-scope-id", "pytest-temporary-fixture",
            "--report", os.fspath(report),
            "--output", os.fspath(output),
            "--", sys.executable, "-c", "raise SystemExit(7)",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failing.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["exit_code"] == 7
    assert payload["machine_verifiable_ci"] is False
    assert payload["owner_authority_machine_verifiable"] is False

    caller_fill = subprocess.run(
        [
            sys.executable,
            os.fspath(PROJECT_ROOT / "scripts" / "fl1_i1_validation_receipt.py"),
            "--repo-root", os.fspath(fixture.repo),
            "--exit-code", "0",
            "--command", "true",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert caller_fill.returncode != 0
