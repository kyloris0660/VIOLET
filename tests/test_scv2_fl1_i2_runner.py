from __future__ import annotations

import binascii
import json
from pathlib import Path

import pytest

from scripts.fl1_i2_evidence import FailureBudget
from scripts.fl1_i2_runner import create_synthetic_run_config, run_synthetic_hardening


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + (binascii.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", b"\x00" * 13) + chunk(b"IEND", b"")


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
    assert summary["operation_counts"]["total"] == 5
    assert summary["authorities"]["real_source"] is False
    rendered = json.dumps(summary)
    assert "valid.png" not in rendered and "corrupt.jpg" not in rendered
    ledger = json.loads((evidence / "private-operation-ledger.json").read_text(encoding="utf-8"))
    operation_ids = {event["operation_id"] for event in ledger["events"]}
    assert all(sum(event["state"] in {"completed", "failed", "interrupted", "recovered"} for event in ledger["events"] if event["operation_id"] == identifier) == 1 for identifier in operation_ids)


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


def test_fixture_marker_cannot_be_reused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    evidence = tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    create_synthetic_run_config(source_root=source, evidence_root=evidence)
    with pytest.raises(Exception, match="marker_already_exists"):
        create_synthetic_run_config(source_root=source, evidence_root=evidence)
