from __future__ import annotations

import binascii
import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts.fl1_i2_evidence import EvidenceStore, FailureBudget, canonical_fingerprint
from scripts.fl1_i2_runner import create_synthetic_run_config, run_synthetic_hardening
from scripts.fl1_i2_validation_receipt import SameHeadValidationReceipt, canonical_focused_test_command
from scripts.phase_contracts import ContractRepositoryContext, check_phase_contract, get_contract
from scripts.phase_contracts import fl1_i2_contract
from scripts.phase_contracts.fl1_i2_contract import FL1I2EvidencePaths, REQUIRED_FOCUSED_TESTS
from scripts.check_phase_contract import build_parser


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + (binascii.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")

    ihdr = b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"x") + chunk(b"IEND", b"")


def _command_fingerprint(command: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(list(command), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _evidence(tmp_path: Path) -> tuple[dict[str, object], FL1I2EvidencePaths]:
    source = tmp_path / "source"
    root = tmp_path / "evidence"
    source.mkdir()
    root.mkdir()
    (source / "fixture.png").write_bytes(_png())
    config_path = create_synthetic_run_config(source_root=source, evidence_root=root, run_id="contract-run", budget=FailureBudget(2, 10, 1024, 5))
    summary = run_synthetic_hardening(config_path)
    command = canonical_focused_test_command(Path(sys.executable))
    bindings = summary["evidence_bindings"]
    receipt = SameHeadValidationReceipt(
        run_id="contract-run",
        git_head="a" * 40,
        git_tree="b" * 40,
        trusted_git_fingerprint="c" * 64,
        python_executable_fingerprint=hashlib.sha256(Path(sys.executable).resolve(strict=True).read_bytes()).hexdigest(),
        config_fingerprint=bindings["config"],
        policy_fingerprint=bindings["policy"],
        manifest_fingerprint=bindings["manifest"],
        ledger_fingerprint=bindings["ledger"],
        worker_fingerprint=bindings["worker"],
        command_argv=command,
        command_fingerprint=_command_fingerprint(command),
        exit_code=0,
        stdout_fingerprint="d" * 64,
        stderr_fingerprint="e" * 64,
        started_at_ns=1,
        ended_at_ns=2,
        same_head_tree=True,
        clean_before_after=True,
        positive=True,
    )
    EvidenceStore(root).write("local-validation-receipt.json", receipt.to_private_dict())
    return summary, FL1I2EvidencePaths(root)


@pytest.fixture
def repository_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fl1_i2_contract, "_repository_snapshot", lambda _root: ("a" * 40, "b" * 40, "c" * 64))


def test_contract_registered_and_rebuilds_all_fourteen_gates(tmp_path: Path, repository_proof: None) -> None:
    summary, paths = _evidence(tmp_path)
    contract = get_contract("scv2_fl1_i2_pre_real_hardening_contract_v1")
    assert contract.custom_checks == ("scv2_fl1_i2_pre_real_hardening",)
    result = check_phase_contract(contract.contract_id, summary, repository_context=ContractRepositoryContext(repo_root=tmp_path, fl1_i2_evidence=paths))
    assert result.passed, result.to_dict()
    assert result.details["fl1_i2_gate_closure"] == {str(value): True for value in fl1_i2_contract.GATE_FINDINGS}


def test_caller_positive_flag_cannot_replace_private_projection(tmp_path: Path, repository_proof: None) -> None:
    summary, paths = _evidence(tmp_path)
    forged = {**summary, "passed": True}
    result = check_phase_contract("scv2_fl1_i2_pre_real_hardening_contract_v1", forged, repository_context=ContractRepositoryContext(repo_root=tmp_path, fl1_i2_evidence=paths))
    assert not result.passed
    assert "fl1_i2_public_projection_mismatch" in {error.code for error in result.errors}


@pytest.mark.parametrize("artifact", ["private-run-config.json", "private-manifest.json", "private-operation-ledger.json", "private-worker-results.json", "local-validation-receipt.json"])
def test_private_artifact_mutation_fails_closed(tmp_path: Path, repository_proof: None, artifact: str) -> None:
    summary, paths = _evidence(tmp_path)
    target = paths.root / artifact
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["contract_test_tamper"] = True
    target.write_text(json.dumps(payload), encoding="utf-8")
    result = check_phase_contract("scv2_fl1_i2_pre_real_hardening_contract_v1", summary, repository_context=ContractRepositoryContext(repo_root=tmp_path, fl1_i2_evidence=paths))
    assert not result.passed


def test_positive_real_source_authority_fails_closed_before_projection(tmp_path: Path, repository_proof: None) -> None:
    summary, paths = _evidence(tmp_path)
    target = paths.root / "private-run-config.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["authorities"]["real_source"] = True
    target.write_text(json.dumps(payload), encoding="utf-8")
    result = check_phase_contract("scv2_fl1_i2_pre_real_hardening_contract_v1", summary, repository_context=ContractRepositoryContext(repo_root=tmp_path, fl1_i2_evidence=paths))
    assert not result.passed
    assert any("authority_escalation" in error.code for error in result.errors)


@pytest.mark.parametrize(
    ("artifact", "mutate"),
    [
        ("private-worker-results.json", lambda payload: payload["records"].pop()),
        ("private-worker-results.json", lambda payload: payload["records"].append(dict(payload["records"][-1]))),
        ("private-worker-results.json", lambda payload: payload["records"][-1]["payload"]["result"].update({"byte_count": 0})),
        ("private-worker-results.json", lambda payload: payload["records"][-1]["payload"]["result"].update({"policy_fingerprint": "0" * 64})),
        ("private-operation-ledger.json", lambda payload: payload["item_dispositions"].update({next(iter(payload["item_dispositions"])): "failed"})),
        ("private-operation-ledger.json", lambda payload: payload["committed_results"][next(iter(payload["committed_results"]))].update({"result_fingerprint": "0" * 64})),
    ],
)
def test_missing_extra_duplicate_or_conflicting_member_evidence_fails_closed(
    tmp_path: Path,
    repository_proof: None,
    artifact: str,
    mutate: object,
) -> None:
    summary, paths = _evidence(tmp_path)
    target = paths.root / artifact
    payload = json.loads(target.read_text(encoding="utf-8"))
    mutate(payload)  # type: ignore[operator]
    target.write_text(json.dumps(payload), encoding="utf-8")
    result = check_phase_contract(
        "scv2_fl1_i2_pre_real_hardening_contract_v1",
        summary,
        repository_context=ContractRepositoryContext(repo_root=tmp_path, fl1_i2_evidence=paths),
    )
    assert not result.passed


def test_missing_private_context_fails_closed() -> None:
    result = check_phase_contract("scv2_fl1_i2_pre_real_hardening_contract_v1", {"contract_id": "scv2_fl1_i2_pre_real_hardening_contract_v1"})
    assert not result.passed
    assert "fl1_i2_private_evidence_required" in {error.code for error in result.errors}


def test_contract_cli_exposes_only_fixed_root_i2_evidence_option() -> None:
    args = build_parser().parse_args(
        [
            "--contract",
            "scv2_fl1_i2_pre_real_hardening_contract_v1",
            "--summary",
            "public-summary.json",
            "--repo-root",
            "repo",
            "--fl1-i2-evidence",
            "private-root",
        ]
    )
    assert args.fl1_i2_evidence == "private-root"
    assert not hasattr(args, "fl1_i2_passed")
