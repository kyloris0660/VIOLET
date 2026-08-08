from __future__ import annotations

import copy
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import fl1_i1_inventory as inventory
from scripts.fl1_i1_inventory import (
    CONTRACT_ID,
    I1InventoryConfig,
    I1InventoryError,
    ImportDisposition,
    InventoryDisposition,
    SourceKind,
    build_contract_summary,
    scan_synthetic_inventory,
    validate_i1_preflight,
)
from scripts.phase_contracts.contract_checks import check_phase_contract
from scripts.phase_contracts.contract_registry import (
    CONTRACTS,
    REQUIRED_CONTRACT_IDS,
)


HEAD = "a" * 40


def _fixture_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    sandbox = tmp_path / "sandbox"
    source = sandbox / "source"
    forbidden = tmp_path / "forbidden"
    source.mkdir(parents=True)
    forbidden.mkdir()
    return sandbox, source, forbidden


def _config(
    tmp_path: Path,
    *,
    overrides: dict[str, str] | None = None,
    expected_snapshot: str | None = None,
    source_kind: SourceKind | str = SourceKind.SYNTHETIC_FIXTURE,
) -> tuple[I1InventoryConfig, Path, Path]:
    sandbox, source, forbidden = _fixture_roots(tmp_path)
    config = I1InventoryConfig(
        source_kind=source_kind,
        source_scope_id="synthetic_scope_v1",
        sandbox_root=sandbox.resolve(),
        source_root=source.resolve(),
        forbidden_roots=(forbidden.resolve(),),
        actual_git_head=HEAD,
        expected_git_head=HEAD,
        python_executable=Path(os.sys.executable),
        expected_python=Path(os.sys.executable),
        supported_extensions=(".jpg", ".jpeg", ".png", ".webp"),
        max_discovered_items=100,
        max_total_source_bytes=1024 * 1024,
        read_chunk_bytes=4,
        expected_source_snapshot_fingerprint=expected_snapshot,
        synthetic_disposition_overrides=overrides or {},
    )
    return config, source, forbidden


def _populate_inventory(source: Path) -> None:
    (source / "nested").mkdir()
    (source / "a.jpg").write_bytes(b"same-content")
    (source / "nested" / "b.png").write_bytes(b"same-content")
    (source / "c.txt").write_text("unsupported", encoding="utf-8")
    (source / "d.jpeg").write_bytes(b"unique-content")
    (source / "cloud.webp").write_bytes(b"cloud-placeholder")
    (source / "unreadable.jpg").write_bytes(b"unreadable-placeholder")


def test_synthetic_inventory_balances_denominator_and_defers_import(tmp_path: Path) -> None:
    config, source, _ = _config(
        tmp_path,
        overrides={
            "cloud.webp": InventoryDisposition.CLOUD_RECALL_DEFERRED.value,
            "unreadable.jpg": InventoryDisposition.UNREADABLE_OR_MISSING.value,
        },
    )
    _populate_inventory(source)

    preflight, manifest = scan_synthetic_inventory(config)

    assert preflight.source_root == source.resolve()
    assert manifest.source_tree_unchanged is True
    assert manifest.denominator() == {
        "discovered": 6,
        "supported": 5,
        "unsupported": 1,
        "duplicate": 1,
        "cloud_recall_deferred": 1,
        "unreadable_or_missing": 1,
        "eligible_candidate": 2,
        "imported": 0,
        "import_deferred": 2,
        "import_failed": 0,
        "unresolved": 0,
    }
    eligible = [
        record
        for record in manifest.records
        if record.disposition is InventoryDisposition.ELIGIBLE_CANDIDATE
    ]
    duplicate = next(
        record
        for record in manifest.records
        if record.disposition is InventoryDisposition.DUPLICATE
    )
    assert all(
        record.import_disposition is ImportDisposition.IMPORT_DEFERRED
        for record in eligible
    )
    assert duplicate.duplicate_of_item_id in {record.item_id for record in eligible}
    assert manifest.synthetic_file_read_attempt_count == 3
    assert manifest.synthetic_file_read_success_count == 3


def test_manifest_and_public_labels_are_stable_across_restart(tmp_path: Path) -> None:
    config, source, _ = _config(tmp_path)
    (source / "one.jpg").write_bytes(b"one")
    (source / "two.jpg").write_bytes(b"two")

    _, first = scan_synthetic_inventory(config)
    bound = replace(
        config,
        expected_source_snapshot_fingerprint=first.source_snapshot_fingerprint,
    )
    _, second = scan_synthetic_inventory(bound)

    assert first.manifest_fingerprint == second.manifest_fingerprint
    assert [record.item_id for record in first.records] == [
        record.item_id for record in second.records
    ]
    assert [record.public_label for record in first.records] == [
        record.public_label for record in second.records
    ]


def test_snapshot_binding_rejects_changed_fixture(tmp_path: Path) -> None:
    config, source, _ = _config(tmp_path)
    item = source / "one.jpg"
    item.write_bytes(b"one")
    _, first = scan_synthetic_inventory(config)
    item.write_bytes(b"changed")

    with pytest.raises(I1InventoryError, match="source_snapshot_fingerprint_mismatch"):
        scan_synthetic_inventory(
            replace(
                config,
                expected_source_snapshot_fingerprint=first.source_snapshot_fingerprint,
            )
        )


def test_real_unknown_and_forbidden_authorizations_fail_closed(tmp_path: Path) -> None:
    real_config, source, _ = _config(tmp_path, source_kind=SourceKind.REAL_SOURCE)
    (source / "one.jpg").write_bytes(b"one")
    with pytest.raises(I1InventoryError, match="real_source_inventory_not_authorized"):
        validate_i1_preflight(real_config)

    with pytest.raises(I1InventoryError, match="unknown_source_kind"):
        validate_i1_preflight(replace(real_config, source_kind="unknown"))

    for field in (
        "real_source_inventory_authorized",
        "database_access_authorized",
        "app_storage_write_authorized",
        "network_authorized",
    ):
        with pytest.raises(I1InventoryError, match="forbidden_authorization_enabled"):
            validate_i1_preflight(
                replace(real_config, source_kind=SourceKind.SYNTHETIC_FIXTURE, **{field: True})
            )


def test_identity_and_containment_fail_closed(tmp_path: Path) -> None:
    config, source, forbidden = _config(tmp_path)
    (source / "one.jpg").write_bytes(b"one")

    invalid_configs = (
        (replace(config, actual_git_head="b" * 40), "git_head_identity_mismatch"),
        (
            replace(config, expected_python=tmp_path / "different-python"),
            "python_identity_mismatch",
        ),
        (
            replace(config, source_root=forbidden.resolve()),
            "synthetic_source_outside_sandbox",
        ),
        (
            replace(config, forbidden_roots=(config.sandbox_root,)),
            "sandbox_overlaps_forbidden_root",
        ),
    )
    for invalid, error in invalid_configs:
        with pytest.raises(I1InventoryError, match=error):
            validate_i1_preflight(invalid)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("supported_extensions", (), "supported_extensions_required"),
        ("supported_extensions", ("jpg",), "supported_extensions_invalid"),
        ("supported_extensions", (".jpg", ".JPG"), "supported_extensions_invalid"),
        ("max_discovered_items", 0, "max_discovered_items_invalid"),
        ("max_total_source_bytes", 0, "max_total_source_bytes_invalid"),
        ("read_chunk_bytes", 0, "read_chunk_bytes_invalid"),
        (
            "expected_source_snapshot_fingerprint",
            "invalid",
            "expected_source_snapshot_fingerprint_invalid",
        ),
    ],
)
def test_preflight_schema_and_limits_are_strict(
    tmp_path: Path, field: str, value: object, error: str
) -> None:
    config, source, _ = _config(tmp_path)
    (source / "one.jpg").write_bytes(b"one")
    with pytest.raises(I1InventoryError, match=error):
        validate_i1_preflight(replace(config, **{field: value}))


def test_item_and_byte_limits_stop_before_manifest_claim(tmp_path: Path) -> None:
    config, source, _ = _config(tmp_path)
    (source / "one.jpg").write_bytes(b"1234")
    (source / "two.jpg").write_bytes(b"5678")

    with pytest.raises(I1InventoryError, match="source_item_limit_exceeded"):
        scan_synthetic_inventory(replace(config, max_discovered_items=1))
    with pytest.raises(I1InventoryError, match="source_byte_limit_exceeded"):
        scan_synthetic_inventory(replace(config, max_total_source_bytes=7))


def test_empty_symlink_and_special_source_entries_are_rejected(tmp_path: Path) -> None:
    empty_config, _, _ = _config(tmp_path / "empty")
    with pytest.raises(I1InventoryError, match="synthetic_source_fixture_empty"):
        scan_synthetic_inventory(empty_config)

    symlink_config, symlink_source, _ = _config(tmp_path / "symlink")
    target = symlink_source / "target.jpg"
    target.write_bytes(b"target")
    try:
        (symlink_source / "link.jpg").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(I1InventoryError, match="source_symlink_rejected"):
        scan_synthetic_inventory(symlink_config)

    if hasattr(os, "mkfifo"):
        special_config, special_source, _ = _config(tmp_path / "special")
        os.mkfifo(special_source / "pipe.jpg")
        with pytest.raises(I1InventoryError, match="source_special_file_rejected"):
            scan_synthetic_inventory(special_config)


def test_override_paths_and_dispositions_are_bounded(tmp_path: Path) -> None:
    config, source, _ = _config(tmp_path)
    (source / "one.jpg").write_bytes(b"one")
    invalid = (
        ({"../one.jpg": "cloud_recall_deferred"}, "synthetic_override_path_invalid"),
        ({"one.jpg": "eligible_candidate"}, "synthetic_override_disposition_invalid"),
        ({"missing.jpg": "cloud_recall_deferred"}, "synthetic_override_item_missing"),
    )
    for overrides, error in invalid:
        with pytest.raises(I1InventoryError, match=error):
            scan_synthetic_inventory(
                replace(config, synthetic_disposition_overrides=overrides)
            )

    (source / "unsupported.txt").write_text("unsupported", encoding="utf-8")
    with pytest.raises(
        I1InventoryError, match="synthetic_override_requires_supported_extension"
    ):
        scan_synthetic_inventory(
            replace(
                config,
                synthetic_disposition_overrides={
                    "unsupported.txt": "cloud_recall_deferred"
                },
            )
        )


def test_actual_read_failure_is_terminal_and_not_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, source, _ = _config(tmp_path)
    (source / "one.jpg").write_bytes(b"one")
    original_open = inventory.os.open

    def fail_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path).name == "one.jpg":
            raise PermissionError("synthetic failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(inventory.os, "open", fail_open)
    _, manifest = scan_synthetic_inventory(config)

    assert manifest.denominator()["unreadable_or_missing"] == 1
    assert manifest.denominator()["unresolved"] == 0
    assert manifest.synthetic_file_read_attempt_count == 1
    assert manifest.synthetic_file_read_success_count == 0


def test_source_change_during_read_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, source, _ = _config(tmp_path)
    item = source / "one.jpg"
    item.write_bytes(b"one")
    original_hash = inventory._hash_file

    def mutate_after_hash(entry: object, chunk_bytes: int) -> tuple[str, int]:
        result = original_hash(entry, chunk_bytes)
        item.write_bytes(b"mutated")
        return result

    monkeypatch.setattr(inventory, "_hash_file", mutate_after_hash)
    with pytest.raises(I1InventoryError, match="source_changed_during_inventory"):
        scan_synthetic_inventory(config)


def test_scanner_opens_files_read_only_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, source, _ = _config(tmp_path)
    (source / "one.jpg").write_bytes(b"one")
    before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    original_open = inventory.os.open
    observed_flags: list[int] = []

    def observe_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        observed_flags.append(flags)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(inventory.os, "open", observe_open)
    scan_synthetic_inventory(config)
    after = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }

    assert before == after
    assert observed_flags
    assert all(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) == 0 for flags in observed_flags)


def test_manifest_validation_rejects_private_accounting_tamper(tmp_path: Path) -> None:
    config, source, _ = _config(tmp_path)
    (source / "one.jpg").write_bytes(b"one")
    _, manifest = scan_synthetic_inventory(config)
    record = manifest.records[0]

    with pytest.raises(I1InventoryError, match="eligible_import_state_invalid"):
        replace(
            manifest,
            records=(
                replace(record, import_disposition=ImportDisposition.NOT_APPLICABLE),
            ),
        ).validate()
    with pytest.raises(I1InventoryError, match="inventory_manifest_fingerprint_mismatch"):
        replace(manifest, manifest_fingerprint="0" * 64).validate()


def test_public_contract_summary_contains_only_safe_aggregate_evidence(tmp_path: Path) -> None:
    config, source, _ = _config(tmp_path)
    private_name = "private-person-name.jpg"
    (source / private_name).write_bytes(b"private-content")
    preflight, manifest = scan_synthetic_inventory(config)

    summary = build_contract_summary(
        preflight=preflight,
        manifest=manifest,
        focused_tests_passed=True,
        full_non_e2e_passed=True,
    )
    serialized = json.dumps(summary, sort_keys=True)
    assert private_name not in serialized
    assert str(source) not in serialized
    assert manifest.records[0].content_fingerprint not in serialized
    assert summary["pipeline_contract"] == {
        "contract_id": CONTRACT_ID,
        "status": "synthetic_implementation_ready_for_owner_audit",
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "active_blockers": [
            "pending_owner_audit",
            "real_source_scope_not_authorized",
        ],
    }
    result = check_phase_contract(CONTRACT_ID, summary)
    assert result.passed is True

    unsafe = copy.deepcopy(summary)
    unsafe["authorization"]["real_source_inventory_authorized"] = True
    failed = check_phase_contract(CONTRACT_ID, unsafe)
    assert failed.passed is False
    assert "fl1_i1_authorization_boundary_invalid" in {
        failure.code for failure in failed.errors
    }


def test_i1_contract_is_registered_and_required() -> None:
    assert CONTRACT_ID in CONTRACTS
    assert CONTRACT_ID in REQUIRED_CONTRACT_IDS
    contract = CONTRACTS[CONTRACT_ID]
    assert contract.phase_kind == "scv2_fl1_i1_read_only_inventory_foundation"
    assert contract.custom_checks == ("scv2_fl1_i1_inventory",)
