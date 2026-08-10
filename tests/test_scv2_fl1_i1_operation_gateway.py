from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path

import pytest

import scripts.fl1_i1_operation_gateway as gateway_module
from scripts.fl1_i1_operation_gateway import (
    CloudAvailability,
    OperationGateway,
    OperationGatewayError,
    OperationKind,
    OperationLedger,
    OperationLedgerStore,
    OperationStatus,
    SyntheticAttributeAdapter,
    TaskOwnedArtifactStore,
    WindowsCloudAttributeAdapter,
)
from tests.fl1_i1_helpers import make_i1_fixture


class RecordingStore(OperationLedgerStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path, artifact_store=TaskOwnedArtifactStore(path.parent))
        self.snapshots: list[list[str]] = []

    def save(self, ledger: OperationLedger) -> None:
        self.snapshots.append([record.status.value for record in ledger.records])
        super().save(ledger)


def _gateway(tmp_path: Path, *, adapter=None):
    fixture = make_i1_fixture(tmp_path)
    context = fixture.context()
    store = RecordingStore(fixture.evidence / "operation-ledger.json")
    gateway = OperationGateway(
        context=context,
        store=store,
        run_id=str(uuid.uuid4()),
        invocation_id=str(uuid.uuid4()),
        attribute_adapter=adapter or SyntheticAttributeAdapter(observations={}),
    )
    return fixture, gateway, store


def test_each_gateway_entry_persists_intent_before_terminal_result(tmp_path: Path) -> None:
    fixture, gateway, store = _gateway(tmp_path)
    target = fixture.source / "a.jpg"
    metadata = gateway.stat_entry(target, item_id="a" * 64, attempt=1)
    gateway.list_directory(fixture.source, target_token="1" * 64)
    gateway.observe_attributes(target, item_id="a" * 64, attempt=1)
    gateway.hash_file(
        target,
        item_id="a" * 64,
        attempt=1,
        expected_signature=(
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_dev,
            metadata.st_ino,
        ),
        chunk_size=4,
        timeout_seconds=2,
        max_bytes=1024,
    )
    assert any(snapshot and snapshot[-1] == "intent" for snapshot in store.snapshots)
    assert all(record.status is not OperationStatus.INTENT for record in gateway.ledger.records)
    assert {record.kind for record in gateway.ledger.records} == set(OperationKind)
    for record in gateway.ledger.records:
        assert record.terminal_timestamp_ns is not None
        assert record.terminal_timestamp_ns >= record.intent_timestamp_ns


def test_outside_scope_attempt_is_write_ahead_logged_and_rejected(tmp_path: Path) -> None:
    fixture, gateway, _ = _gateway(tmp_path)
    with pytest.raises(OperationGatewayError, match="source_path_escape"):
        gateway.stat_entry(
            fixture.roots["production_source_root"],
            item_id="a" * 64,
            attempt=1,
        )
    record = gateway.ledger.records[-1]
    assert record.kind is OperationKind.SOURCE_ENTRY_METADATA
    assert record.status is OperationStatus.FAILED
    assert record.safe_reason_code == "source_path_escape"


def test_cloud_recall_and_unknown_observations_are_deferred(tmp_path: Path) -> None:
    adapter = SyntheticAttributeAdapter(
        observations={"a.jpg": CloudAvailability.RECALL_RISK, "b.jpg": CloudAvailability.UNKNOWN}
    )
    fixture, gateway, _ = _gateway(tmp_path, adapter=adapter)
    first = gateway.observe_attributes(
        fixture.source / "a.jpg", item_id="a" * 64, attempt=1
    )
    second = gateway.observe_attributes(
        fixture.source / "b.jpg", item_id="b" * 64, attempt=1
    )
    assert first.availability is CloudAvailability.RECALL_RISK
    assert second.attributes_known is False
    assert [record.status for record in gateway.ledger.records] == [
        OperationStatus.DEFERRED,
        OperationStatus.DEFERRED,
    ]
    assert not any(
        record.kind in {OperationKind.SOURCE_FILE_READ, OperationKind.SOURCE_FILE_HASH}
        for record in gateway.ledger.records
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows API-level observation")
def test_windows_cloud_adapter_observes_real_temporary_file_without_opening_content(tmp_path: Path) -> None:
    target = tmp_path / "windows-attribute-probe.tmp"
    target.write_bytes(b"temporary fixture")
    observation = WindowsCloudAttributeAdapter().observe(target)
    assert observation.platform == "windows"
    assert observation.attributes_known is True
    assert observation.reparse_point is False
    assert observation.availability is CloudAvailability.AVAILABLE


def test_metadata_is_no_follow_and_symlink_never_reaches_hash(tmp_path: Path) -> None:
    fixture, gateway, _ = _gateway(tmp_path)
    link = fixture.source / "linked.jpg"
    try:
        link.symlink_to(fixture.source / "a.jpg")
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    metadata = gateway.stat_entry(link, item_id="c" * 64, attempt=1)
    assert stat.S_ISLNK(metadata.st_mode)
    with pytest.raises(OperationGatewayError, match="source_symlink_or_reparse_rejected"):
        gateway.hash_file(
            link,
            item_id="c" * 64,
            attempt=1,
            expected_signature=(
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_dev,
                metadata.st_ino,
            ),
            chunk_size=4,
            timeout_seconds=1,
            max_bytes=1024,
        )
    assert gateway.ledger.records[-1].status is OperationStatus.FAILED


def test_operation_ledger_tamper_fails_even_if_json_is_well_formed(tmp_path: Path) -> None:
    fixture, gateway, store = _gateway(tmp_path)
    gateway.list_directory(fixture.source, target_token="1" * 64)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["records"][0]["actual_git_head"] = "0" * 40
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OperationGatewayError):
        store.load()


def test_hash_detects_file_signature_change_before_read(tmp_path: Path) -> None:
    fixture, gateway, _ = _gateway(tmp_path)
    target = fixture.source / "a.jpg"
    metadata = os.stat(target, follow_symlinks=False)
    expected = (
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_dev,
        metadata.st_ino,
    )
    target.write_bytes(b"changed after discovery")
    with pytest.raises(OperationGatewayError, match="source_entry_changed_before_read"):
        gateway.hash_file(
            target,
            item_id="d" * 64,
            attempt=1,
            expected_signature=expected,
            chunk_size=4,
            timeout_seconds=2,
            max_bytes=1024,
        )
    assert gateway.ledger.records[-2].status is OperationStatus.FAILED
    assert gateway.ledger.records[-1].status is OperationStatus.FAILED


def test_hash_detects_mutation_during_chunked_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture, gateway, _ = _gateway(tmp_path)
    target = fixture.source / "a.jpg"
    metadata = os.stat(target, follow_symlinks=False)
    expected = (
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_dev,
        metadata.st_ino,
    )
    original_read = gateway_module.os.read
    mutated = False

    def mutating_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, size)
        if chunk and not mutated:
            mutated = True
            with target.open("ab") as handle:
                handle.write(b"race")
        return chunk

    monkeypatch.setattr(gateway_module.os, "read", mutating_read)
    with pytest.raises(OperationGatewayError, match="source_entry_changed_during_read"):
        gateway.hash_file(
            target,
            item_id="e" * 64,
            attempt=1,
            expected_signature=expected,
            chunk_size=4,
            timeout_seconds=2,
            max_bytes=1024,
        )


def test_per_item_deadline_stops_before_content_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture, gateway, _ = _gateway(tmp_path)
    target = fixture.source / "a.jpg"
    metadata = os.stat(target, follow_symlinks=False)
    expected = (
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_dev,
        metadata.st_ino,
    )
    values = iter((0.0, 2.0))
    monkeypatch.setattr(gateway_module.time, "monotonic", lambda: next(values))
    with pytest.raises(OperationGatewayError, match="source_read_timeout"):
        gateway.hash_file(
            target,
            item_id="f" * 64,
            attempt=1,
            expected_signature=expected,
            chunk_size=4,
            timeout_seconds=1,
            max_bytes=1024,
        )


def test_recall_risk_is_observed_before_final_component_resolve_or_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, gateway, _ = _gateway(
        tmp_path,
        adapter=SyntheticAttributeAdapter(
            observations={"a.jpg": CloudAvailability.RECALL_RISK}
        ),
    )
    target = fixture.source / "a.jpg"
    original_resolve = Path.resolve
    final_resolves: list[Path] = []

    def recording_resolve(self: Path, *args, **kwargs):
        if self == target:
            final_resolves.append(self)
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", recording_resolve)
    monkeypatch.setattr(
        gateway_module,
        "_open_readonly_nofollow",
        lambda _path: (_ for _ in ()).throw(AssertionError("content open attempted")),
    )
    observation = gateway.observe_attributes(target, item_id="a" * 64, attempt=1)
    assert observation.availability is CloudAvailability.RECALL_RISK
    assert final_resolves == []


def test_task_owned_writer_rejects_source_target_without_modifying_bytes(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path)
    target = fixture.source / "a.jpg"
    before = target.read_bytes()
    store = TaskOwnedArtifactStore(fixture.evidence)
    with pytest.raises(OperationGatewayError, match="private_artifact_target_escape"):
        store.atomic_write_json(target, {"forbidden": True})
    assert target.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX private mode/owner assertion")
def test_task_owned_private_artifact_is_explicit_owner_0600(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    target = fixture.evidence / "private-mode.json"
    TaskOwnedArtifactStore(fixture.evidence).atomic_write_json(target, {"ok": True})
    metadata = os.stat(target, follow_symlinks=False)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_uid == os.getuid()


def test_growing_file_never_reads_beyond_reserved_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, gateway, _ = _gateway(tmp_path)
    target = fixture.source / "a.jpg"
    metadata = os.stat(target, follow_symlinks=False)
    expected = (
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_dev,
        metadata.st_ino,
    )
    original_read = gateway_module.os.read
    actual_read = 0
    mutated = False

    def growing_read(descriptor: int, size: int) -> bytes:
        nonlocal actual_read, mutated
        chunk = original_read(descriptor, size)
        actual_read += len(chunk)
        if chunk and not mutated:
            mutated = True
            with target.open("ab") as handle:
                handle.write(b"growth-beyond-original-budget")
        return chunk

    monkeypatch.setattr(gateway_module.os, "read", growing_read)
    with pytest.raises(OperationGatewayError, match="source_entry_changed_during_read"):
        gateway.hash_file(
            target,
            item_id="9" * 64,
            attempt=1,
            expected_signature=expected,
            chunk_size=4,
            timeout_seconds=2,
            max_bytes=metadata.st_size,
        )
    assert actual_read <= metadata.st_size
    read_record = next(
        record
        for record in gateway.ledger.records
        if record.kind is OperationKind.SOURCE_FILE_READ
    )
    assert read_record.byte_count <= metadata.st_size
