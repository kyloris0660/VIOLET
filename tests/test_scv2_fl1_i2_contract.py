from __future__ import annotations

import binascii
import hashlib
import json
import sys
import zlib
from pathlib import Path

import pytest

from scripts.fl1_i2_evidence import EvidenceStore, FailureBudget, canonical_fingerprint
from scripts.fl1_i2_runner import create_synthetic_run_config, run_synthetic_hardening
from scripts.fl1_i2_validation_receipt import (
    SameHeadValidationReceipt,
    canonical_focused_test_command,
    execution_environment_policy_fingerprint,
)
from scripts.phase_contracts import ContractRepositoryContext, check_phase_contract, get_contract
from scripts.phase_contracts import fl1_i2_contract
from scripts.phase_contracts.fl1_i2_contract import FL1I2EvidencePaths, REQUIRED_FOCUSED_TESTS
from scripts.phase_contracts.fl1_i2_contract import FL1I2ContractError, derive_canonical_public_projection
from scripts.check_phase_contract import build_parser


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + (binascii.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")

    ihdr = b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")


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
        execution_environment_policy_fingerprint=execution_environment_policy_fingerprint(),
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


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _refresh_ledger_worker_and_receipt(paths: FL1I2EvidencePaths) -> None:
    ledger_path = paths.root / "private-operation-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    for operation_id, record in ledger["committed_results"].items():
        base = {key: value for key, value in record.items() if key != "result_fingerprint"}
        fingerprint = canonical_fingerprint(base)
        record["result_fingerprint"] = fingerprint
        terminal = next(
            event
            for event in ledger["events"]
            if event["operation_id"] == operation_id and event["state"] in {"completed", "failed", "interrupted", "recovered"}
        )
        terminal["result_fingerprint"] = fingerprint
    _write(ledger_path, ledger)
    workers = {
        "schema_version": "violet.scv2-fl1-i2-private-worker-results.v3",
        "run_id": ledger["run_id"],
        "records": [ledger["committed_results"][key] for key in sorted(ledger["committed_results"])],
    }
    _write(paths.root / "private-worker-results.json", workers)
    config = json.loads((paths.root / "private-run-config.json").read_text(encoding="utf-8"))
    manifest = json.loads((paths.root / "private-manifest.json").read_text(encoding="utf-8"))
    receipt_path = paths.root / "local-validation-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["config_fingerprint"] = canonical_fingerprint(config)
    receipt["policy_fingerprint"] = canonical_fingerprint(config["policy"])
    receipt["manifest_fingerprint"] = manifest["manifest_fingerprint"]
    receipt["ledger_fingerprint"] = canonical_fingerprint(ledger)
    receipt["worker_fingerprint"] = canonical_fingerprint(workers)
    _write(receipt_path, receipt)


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


@pytest.mark.parametrize(
    "authorities",
    [
        {},
        {"real_source": False},
        {
            "real_source": False,
            "database": False,
            "app_storage": False,
            "import": False,
            "classification_or_tagging": False,
            "provider_or_llm": False,
            "media_download": False,
            "stable_replay": False,
            "production": False,
            "extra": False,
        },
        {
            "real_source": 0,
            "database": False,
            "app_storage": False,
            "import": False,
            "classification_or_tagging": False,
            "provider_or_llm": False,
            "media_download": False,
            "stable_replay": False,
            "production": False,
        },
    ],
)
def test_authority_map_requires_exact_keys_and_exact_false_booleans(
    tmp_path: Path,
    repository_proof: None,
    authorities: dict[str, object],
) -> None:
    _summary, paths = _evidence(tmp_path)
    config_path = paths.root / "private-run-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["authorities"] = authorities
    _write(config_path, config)
    with pytest.raises(FL1I2ContractError, match="authority_escalation"):
        derive_canonical_public_projection(repo_root=tmp_path, evidence_paths=paths)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cloud_availability", "recall_risk"),
        ("no_follow", False),
        ("identity_bound", False),
        ("reparse_point", True),
        ("link_count", 2),
    ],
)
def test_contract_reapplies_policy_to_each_recorded_observation(
    tmp_path: Path,
    repository_proof: None,
    field: str,
    value: object,
) -> None:
    _summary, paths = _evidence(tmp_path)
    ledger_path = paths.root / "private-operation-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    content = next(record for record in ledger["committed_results"].values() if record["kind"] == "combined_content")
    result = content["payload"]["result"]
    for key in ("opened_observation", "pre_read_observation", "post_read_observation"):
        result[key][field] = value
    _write(ledger_path, ledger)
    _refresh_ledger_worker_and_receipt(paths)
    with pytest.raises(FL1I2ContractError, match="policy_observation_rejected"):
        derive_canonical_public_projection(repo_root=tmp_path, evidence_paths=paths)


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong_type"])
def test_handle_observation_reconstruction_requires_exact_schema(
    tmp_path: Path,
    repository_proof: None,
    mutation: str,
) -> None:
    _summary, paths = _evidence(tmp_path)
    ledger_path = paths.root / "private-operation-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    content = next(record for record in ledger["committed_results"].values() if record["kind"] == "combined_content")
    observation = content["payload"]["result"]["opened_observation"]
    if mutation == "missing":
        observation.pop("attributes_known")
    elif mutation == "extra":
        observation["extra"] = False
    else:
        observation["no_follow"] = 1
    _write(ledger_path, ledger)
    _refresh_ledger_worker_and_receipt(paths)
    with pytest.raises(FL1I2ContractError, match="handle_observation_invalid"):
        derive_canonical_public_projection(repo_root=tmp_path, evidence_paths=paths)


def test_contract_independently_enforces_operation_count_budget(tmp_path: Path, repository_proof: None) -> None:
    _summary, paths = _evidence(tmp_path)
    config_path = paths.root / "private-run-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["budget"]["max_operations"] = 1
    _write(config_path, config)
    ledger_path = paths.root / "private-operation-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["budget_fingerprint"] = canonical_fingerprint(config["budget"])
    _write(ledger_path, ledger)
    _refresh_ledger_worker_and_receipt(paths)
    with pytest.raises(FL1I2ContractError, match="operation_budget_invalid"):
        derive_canonical_public_projection(repo_root=tmp_path, evidence_paths=paths)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_operations", True),
        ("max_evidence_bytes", 1),
        ("max_run_seconds", 0.000001),
        ("max_content_opens", 0),
        ("max_hash_operations", 0),
        ("max_structure_validations", 0),
    ],
)
def test_contract_rebuilds_budget_and_runtime_boundaries_from_disk(
    tmp_path: Path,
    repository_proof: None,
    field: str,
    value: object,
) -> None:
    _summary, paths = _evidence(tmp_path)
    config_path = paths.root / "private-run-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["budget"][field] = value
    _write(config_path, config)
    if field != "max_operations" or value is not True:
        ledger_path = paths.root / "private-operation-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["budget_fingerprint"] = canonical_fingerprint(config["budget"])
        _write(ledger_path, ledger)
        _refresh_ledger_worker_and_receipt(paths)
    with pytest.raises(Exception, match="budget|deadline"):
        derive_canonical_public_projection(repo_root=tmp_path, evidence_paths=paths)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_config_requires_exact_top_level_schema(tmp_path: Path, repository_proof: None, mutation: str) -> None:
    _summary, paths = _evidence(tmp_path)
    config_path = paths.root / "private-run-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        config.pop("enumeration_budget")
    else:
        config["extra"] = False
    _write(config_path, config)
    with pytest.raises(FL1I2ContractError, match="config_invalid"):
        derive_canonical_public_projection(repo_root=tmp_path, evidence_paths=paths)


def test_duplicate_started_event_fails_state_machine_reconstruction(tmp_path: Path, repository_proof: None) -> None:
    _summary, paths = _evidence(tmp_path)
    ledger_path = paths.root / "private-operation-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    started = next(event for event in ledger["events"] if event["state"] == "started")
    index = ledger["events"].index(started)
    ledger["events"].insert(index + 1, dict(started))
    _write(ledger_path, ledger)
    with pytest.raises(Exception, match="state_sequence_invalid"):
        derive_canonical_public_projection(repo_root=tmp_path, evidence_paths=paths)


def test_receipt_environment_policy_fingerprint_is_independently_verified(tmp_path: Path, repository_proof: None) -> None:
    _summary, paths = _evidence(tmp_path)
    receipt_path = paths.root / "local-validation-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["execution_environment_policy_fingerprint"] = "0" * 64
    _write(receipt_path, receipt)
    with pytest.raises(Exception, match="environment_policy_mismatch"):
        derive_canonical_public_projection(repo_root=tmp_path, evidence_paths=paths)
