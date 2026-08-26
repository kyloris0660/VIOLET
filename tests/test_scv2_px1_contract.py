"""Executable evidence and mutation coverage for SCV2-PX1."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.pixiv_metadata_projection_service import (
    canonical_fingerprint,
    canonical_json_bytes,
)
from app.services.pixiv_metadata_vertical_slice_service import (
    repository_synthetic_pixiv_fixture,
    run_synthetic_pixiv_vertical_slice,
    write_synthetic_vertical_slice_evidence,
)
from scripts.check_phase_contract import build_parser
from scripts.phase_contracts import ContractRepositoryContext, check_phase_contract, get_contract
from scripts.phase_contracts import scv2_px1_contract
from scripts.phase_contracts.scv2_px1_contract import (
    Scv2Px1ContractError,
    Scv2Px1EvidencePaths,
    load_px1_evidence_artifacts,
)
from scripts.scv2_px1_validation_receipt import (
    EVIDENCE_ARTIFACT_NAMES,
    RECEIPT_NAME,
    build_receipt_payload,
    canonical_focused_test_command,
    evidence_bindings,
)


def _repository() -> dict[str, str | bool]:
    return {
        "git_head": "a" * 40,
        "git_tree": "b" * 40,
        "trusted_git_fingerprint": "c" * 64,
        "python_executable_fingerprint": "d" * 64,
        "approved_python_runtime_fingerprint": "e" * 64,
        "worktree_clean": True,
    }


def _write(path: Path, payload: object) -> None:
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


def _write_receipt(root: Path) -> None:
    payloads = {
        name: json.loads((root / name).read_text(encoding="utf-8"))
        for name in EVIDENCE_ARTIFACT_NAMES
    }
    receipt = build_receipt_payload(
        repository_before=_repository(),
        repository_after=_repository(),
        approved_python=Path(sys.executable),
        command=canonical_focused_test_command(Path(sys.executable)),
        command_result=subprocess.CompletedProcess(
            args=(), returncode=0, stdout=b"synthetic passed", stderr=b""
        ),
        validation_environment_fingerprint="1" * 64,
        validation_temp_root_identity_fingerprint="2" * 64,
        bindings=evidence_bindings(payloads),
    )
    _write(root / RECEIPT_NAME, receipt)


def _evidence(tmp_path: Path) -> tuple[dict[str, object], Scv2Px1EvidencePaths]:
    root = tmp_path / "px1-evidence"
    fixture = repository_synthetic_pixiv_fixture()
    summary = run_synthetic_pixiv_vertical_slice(workspace=root, fixture=fixture)
    write_synthetic_vertical_slice_evidence(root, fixture=fixture, summary=summary)
    _write_receipt(root)
    return summary, Scv2Px1EvidencePaths(root)


@pytest.fixture(autouse=True)
def repository_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = tmp_path / "runtime-storage"
    storage.mkdir()
    monkeypatch.setenv("VIOLET_SKIP_DOTENV", "1")
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(
        scv2_px1_contract,
        "repository_identity_snapshot",
        lambda *_args, **_kwargs: _repository(),
    )


def _context(paths: Scv2Px1EvidencePaths) -> ContractRepositoryContext:
    return ContractRepositoryContext(
        repo_root=paths.root,
        expected_python=Path(sys.executable),
        scv2_px1_evidence=paths,
    )


def test_contract_registered_and_rebuilds_aggregate_signal_and_receipt(
    tmp_path: Path,
) -> None:
    summary, paths = _evidence(tmp_path)
    contract = get_contract("scv2_px1_pixiv_metadata_consolidation_contract_v1")
    assert contract.custom_checks == ("scv2_px1_pixiv_metadata_consolidation",)
    assert all("python -B" in command for command in contract.required_validation_commands)
    assert contract.required_validation_commands[-1] == (
        "python -B -m pytest tests/ --ignore tests/e2e"
    )
    result = check_phase_contract(
        contract.contract_id,
        summary,
        repository_context=_context(paths),
    )
    assert result.passed, result.to_dict()
    assert result.details["scv2_px1_projection"]["aggregate_count"] == 9
    assert result.details["scv2_px1_repository_binding"]["git_head"] == "a" * 40
    assert result.target_met_claimed is True
    assert result.safe_to_merge_claimed is False
    assert result.route_approved is False


def test_caller_positive_flag_cannot_replace_fixed_evidence(tmp_path: Path) -> None:
    summary, paths = _evidence(tmp_path)
    forged = {**summary, "safe_to_merge": True}
    result = check_phase_contract(
        "scv2_px1_pixiv_metadata_consolidation_contract_v1",
        forged,
        repository_context=_context(paths),
    )
    assert not result.passed
    assert any("caller_summary_evidence_mismatch" in error.code for error in result.errors)


@pytest.mark.parametrize(
    "artifact",
    [
        "synthetic-fixture.json",
        "aggregates.json",
        "signal-bundles.json",
        "operation-receipt.json",
        "public-summary.json",
        RECEIPT_NAME,
    ],
)
def test_private_artifact_noncanonical_mutation_fails_closed(
    tmp_path: Path,
    artifact: str,
) -> None:
    summary, paths = _evidence(tmp_path)
    target = paths.root / artifact
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload.append({"contract_test_tamper": True})
    else:
        payload["contract_test_tamper"] = True
    target.write_text(json.dumps(payload), encoding="utf-8")
    result = check_phase_contract(
        "scv2_px1_pixiv_metadata_consolidation_contract_v1",
        summary,
        repository_context=_context(paths),
    )
    assert not result.passed


def test_authority_escalation_cannot_survive_reprojection(tmp_path: Path) -> None:
    summary, paths = _evidence(tmp_path)
    forged = copy.deepcopy(summary)
    forged["authorities"]["real_source_inventory_authorized"] = True
    unsigned = dict(forged)
    unsigned.pop("canonical_fingerprint")
    forged["canonical_fingerprint"] = canonical_fingerprint(unsigned)
    _write(paths.root / "public-summary.json", forged)
    _write_receipt(paths.root)
    result = check_phase_contract(
        "scv2_px1_pixiv_metadata_consolidation_contract_v1",
        forged,
        repository_context=_context(paths),
    )
    assert not result.passed
    assert any("authority_map_invalid" in error.code for error in result.errors)


def test_replay_false_positive_cannot_survive_independent_execution(tmp_path: Path) -> None:
    summary, paths = _evidence(tmp_path)
    forged = copy.deepcopy(summary)
    forged["canonical_projection_fingerprint"] = "0" * 64
    forged["replay_projection_fingerprint"] = "0" * 64
    forged["reversed_input_projection_fingerprint"] = "0" * 64
    unsigned = dict(forged)
    unsigned.pop("canonical_fingerprint")
    forged["canonical_fingerprint"] = canonical_fingerprint(unsigned)
    _write(paths.root / "public-summary.json", forged)
    _write_receipt(paths.root)
    result = check_phase_contract(
        "scv2_px1_pixiv_metadata_consolidation_contract_v1",
        forged,
        repository_context=_context(paths),
    )
    assert not result.passed
    assert any("independent_replay_projection_mismatch" in error.code for error in result.errors)


def test_missing_private_context_and_python_fail_closed() -> None:
    result = check_phase_contract(
        "scv2_px1_pixiv_metadata_consolidation_contract_v1",
        {"contract_id": "scv2_px1_pixiv_metadata_consolidation_contract_v1"},
    )
    assert not result.passed
    assert "px1_private_evidence_required" in {error.code for error in result.errors}


def test_contract_cli_exposes_only_fixed_px1_evidence_root() -> None:
    args = build_parser().parse_args(
        [
            "--contract",
            "scv2_px1_pixiv_metadata_consolidation_contract_v1",
            "--summary",
            "public-summary.json",
            "--repo-root",
            "repo",
            "--expected-python",
            sys.executable,
            "--px1-evidence",
            "private-root",
        ]
    )
    assert args.px1_evidence == "private-root"
    assert not hasattr(args, "px1_passed")


def test_contract_rejects_unconfined_root_before_member_access() -> None:
    with pytest.raises(Scv2Px1ContractError, match="not_task_temp"):
        load_px1_evidence_artifacts(Scv2Px1EvidencePaths(Path.cwd()))
