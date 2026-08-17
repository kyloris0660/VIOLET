from __future__ import annotations

import binascii
import json
from pathlib import Path

import pytest

from scripts.fl1_i2_evidence import (
    EvidenceStore,
    FailureBudget,
    OperationLedger,
    canonical_fingerprint,
)
from scripts.fl1_i2_runner import create_synthetic_run_config, run_synthetic_hardening


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + (binascii.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")

    ihdr = b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"x") + chunk(b"IEND", b"")


def test_synthetic_runner_builds_fixed_manifest_and_reconciled_ledgers(tmp_path: Path) -> None:
    source = tmp_path / "source"
    evidence = tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    (source / "valid.png").write_bytes(_png())
    (source / "corrupt.jpg").write_bytes(b"\xff\xd8truncated")
    config = create_synthetic_run_config(source_root=source, evidence_root=evidence, run_id="synthetic-run", budget=FailureBudget(3, 20, 1024, 5))
    summary = run_synthetic_hardening(config)
    assert summary["item_counts"]["manifest"] == 2
    assert summary["item_counts"]["content_verified"] == 1
    assert summary["item_counts"]["corrupt_media"] == 1
    assert summary["operation_counts"]["total"] == 3
    assert summary["authorities"]["real_source"] is False
    rendered = json.dumps(summary)
    assert "valid.png" not in rendered and "corrupt.jpg" not in rendered
    ledger = json.loads((evidence / "private-operation-ledger.json").read_text(encoding="utf-8"))
    operation_ids = {event["operation_id"] for event in ledger["events"]}
    assert all(sum(event["state"] in {"completed", "failed", "interrupted", "recovered"} for event in ledger["events"] if event["operation_id"] == identifier) == 1 for identifier in operation_ids)
    assert len(ledger["committed_results"]) == len(operation_ids)
    content = [record for record in ledger["committed_results"].values() if record["kind"] == "combined_content"]
    assert len(content) == 2
    assert all(record["bytes_consumed"] == record["payload"]["result"]["byte_count"] for record in content)


def test_runner_rejects_non_temp_or_authority_escalated_config(tmp_path: Path) -> None:
    source = tmp_path / "source"
    evidence = tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    config = create_synthetic_run_config(source_root=source, evidence_root=evidence)
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["authorities"]["database"] = True
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Exception, match="synthetic_run_authority_escalation"):
        run_synthetic_hardening(config)


def test_runner_rejects_weakened_policy_even_when_shape_is_valid(tmp_path: Path) -> None:
    source = tmp_path / "source"
    evidence = tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    config = create_synthetic_run_config(source_root=source, evidence_root=evidence)
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["policy"]["reject_multiple_links"] = False
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Exception, match="synthetic_run_policy_drift"):
        run_synthetic_hardening(config)


def test_fixture_marker_cannot_be_reused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    evidence = tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    create_synthetic_run_config(source_root=source, evidence_root=evidence)
    with pytest.raises(Exception, match="marker_already_exists"):
        create_synthetic_run_config(source_root=source, evidence_root=evidence)


def test_resume_keeps_fixed_manifest_and_does_not_repeat_completed_operations(tmp_path: Path) -> None:
    source = tmp_path / "source"
    evidence = tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    (source / "first.png").write_bytes(_png())
    config = create_synthetic_run_config(source_root=source, evidence_root=evidence, run_id="resume-run")
    first = run_synthetic_hardening(config)
    (source / "delta.png").write_bytes(_png())
    second = run_synthetic_hardening(config)
    assert second == first
    assert second["item_counts"]["manifest"] == 1


def test_residual_intent_is_recovered_before_new_operation_id(tmp_path: Path) -> None:
    source = tmp_path / "source"
    evidence = tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    (source / "fixture.png").write_bytes(_png())
    budget = FailureBudget(3, 20, 1024, 5)
    config = create_synthetic_run_config(source_root=source, evidence_root=evidence, run_id="crash-run", budget=budget)
    ledger = OperationLedger("crash-run", "pending_manifest", canonical_fingerprint(budget.to_dict()))
    abandoned = ledger.begin(item_id="directory-membership", kind="list_directory", attempt=1, budget=budget)
    EvidenceStore(evidence).write("private-operation-ledger.json", ledger.to_private_dict())
    summary = run_synthetic_hardening(config)
    persisted = json.loads((evidence / "private-operation-ledger.json").read_text(encoding="utf-8"))
    states = [event["state"] for event in persisted["events"] if event["operation_id"] == abandoned]
    assert states == ["intent", "recovered"]
    assert summary["operation_counts"]["recovered"] == 1


@pytest.mark.parametrize(
    "boundary",
    [
        "after_intent_ledger_commit",
        "after_started_ledger_commit",
        "after_terminal_ledger_commit",
        "after_worker_projection",
        "after_manifest_commit",
        "after_manifest_ledger_binding",
        "after_disposition_ledger_commit",
        "after_public_summary_commit",
    ],
)
def test_every_persistence_boundary_resumes_or_fails_closed_deterministically(tmp_path: Path, boundary: str) -> None:
    source = tmp_path / f"source-{boundary}"
    evidence = tmp_path / f"evidence-{boundary}"
    source.mkdir()
    evidence.mkdir()
    (source / "fixture.png").write_bytes(_png())
    config = create_synthetic_run_config(source_root=source, evidence_root=evidence, run_id=f"crash-{boundary}")
    injected = False

    def crash(name: str) -> None:
        nonlocal injected
        if name == boundary and not injected:
            injected = True
            raise RuntimeError("synthetic_crash")

    with pytest.raises(RuntimeError, match="synthetic_crash"):
        run_synthetic_hardening(config, crash_injector=crash)
    assert injected
    summary = run_synthetic_hardening(config)
    assert summary["item_counts"]["manifest"] == 1
    ledger = json.loads((evidence / "private-operation-ledger.json").read_text(encoding="utf-8"))
    workers = json.loads((evidence / "private-worker-results.json").read_text(encoding="utf-8"))
    assert workers["records"] == [ledger["committed_results"][key] for key in sorted(ledger["committed_results"])]


def test_run_wide_byte_budget_exact_boundary_blocks_next_started(tmp_path: Path) -> None:
    source = tmp_path / "source-budget"
    evidence = tmp_path / "evidence-budget"
    source.mkdir()
    evidence.mkdir()
    payload = _png()
    (source / "one.png").write_bytes(payload)
    (source / "two.png").write_bytes(payload)
    (source / "three.png").write_bytes(payload)
    config = create_synthetic_run_config(
        source_root=source,
        evidence_root=evidence,
        run_id="exact-budget",
        budget=FailureBudget(3, 20, len(payload) * 2, 5),
    )
    with pytest.raises(Exception, match="operation_admission_budget_exhausted"):
        run_synthetic_hardening(config)
    ledger = json.loads((evidence / "private-operation-ledger.json").read_text(encoding="utf-8"))
    content_started = [
        event
        for event in ledger["events"]
        if event["kind"] == "combined_content" and event["state"] == "started"
    ]
    assert len(content_started) == 2
    assert sum(event["bytes_consumed"] for event in ledger["events"] if event["state"] in {"completed", "failed", "interrupted", "recovered"}) == len(payload) * 2


def test_orphan_or_conflicting_worker_projection_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source-conflict"
    evidence = tmp_path / "evidence-conflict"
    source.mkdir()
    evidence.mkdir()
    (source / "fixture.png").write_bytes(_png())
    config = create_synthetic_run_config(source_root=source, evidence_root=evidence, run_id="conflict")
    run_synthetic_hardening(config)
    workers = json.loads((evidence / "private-worker-results.json").read_text(encoding="utf-8"))
    workers["records"][0]["safe_code"] = "forged"
    (evidence / "private-worker-results.json").write_text(json.dumps(workers), encoding="utf-8")
    with pytest.raises(Exception, match="operation_closure_conflict"):
        run_synthetic_hardening(config)


def test_terminal_ledger_ahead_of_existing_projection_rebuilds_without_rerun(tmp_path: Path) -> None:
    source = tmp_path / "source-terminal-gap"
    evidence = tmp_path / "evidence-terminal-gap"
    source.mkdir()
    evidence.mkdir()
    (source / "fixture.png").write_bytes(_png())
    config = create_synthetic_run_config(source_root=source, evidence_root=evidence, run_id="terminal-gap")
    terminal_count = 0

    def crash(name: str) -> None:
        nonlocal terminal_count
        if name == "after_terminal_ledger_commit":
            terminal_count += 1
            if terminal_count == 2:
                raise RuntimeError("synthetic_content_terminal_crash")

    with pytest.raises(RuntimeError, match="content_terminal_crash"):
        run_synthetic_hardening(config, crash_injector=crash)
    before = json.loads((evidence / "private-operation-ledger.json").read_text(encoding="utf-8"))
    content_ids = [key for key, record in before["committed_results"].items() if record["kind"] == "combined_content"]
    assert len(content_ids) == 1
    summary = run_synthetic_hardening(config)
    after = json.loads((evidence / "private-operation-ledger.json").read_text(encoding="utf-8"))
    assert set(after["committed_results"]) == set(before["committed_results"])
    assert summary["item_counts"]["content_verified"] == 1
