"""Executable evidence and mutation coverage for SCV2-PX2."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from app.services.pixiv_metadata_clustering_service import (
    run_synthetic_pixiv_metadata_clustering,
    write_pixiv_clustering_evidence,
)
from app.services.pixiv_metadata_projection_service import (
    canonical_fingerprint,
    canonical_json_bytes,
)
from app.services.pixiv_metadata_vertical_slice_service import (
    repository_synthetic_pixiv_fixture,
    run_synthetic_pixiv_vertical_slice,
)
from scripts.check_phase_contract import build_parser
from scripts.phase_contracts import ContractRepositoryContext, check_phase_contract, get_contract
from scripts.phase_contracts import scv2_px2_contract
from scripts.phase_contracts.scv2_px2_contract import (
    Scv2Px2EvidencePaths,
    load_px2_evidence_artifacts,
)
from scripts.scv2_px2_validation_receipt import (
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


@pytest.fixture(scope="module")
def base_evidence(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("scv2-px2-contract-base")
    runtime = tmp_path_factory.mktemp("scv2-px2-contract-runtime")
    keys = (
        "VIOLET_SKIP_DOTENV",
        "VIOLET_ENV",
        "POSTGRES_DB",
        "TEST_DATABASE_URL",
        "VIOLET_STORAGE_ROOT",
        "VIOLET_TEST_STORAGE_ROOT",
    )
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(
        {
            "VIOLET_SKIP_DOTENV": "1",
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "scv2_px2_contract_temp",
            "TEST_DATABASE_URL": "",
            "VIOLET_STORAGE_ROOT": os.fspath(runtime),
            "VIOLET_TEST_STORAGE_ROOT": os.fspath(runtime),
        }
    )
    try:
        px1 = run_synthetic_pixiv_vertical_slice(
            workspace=root,
            fixture=repository_synthetic_pixiv_fixture(),
        )
        summary = run_synthetic_pixiv_metadata_clustering(
            workspace=root,
            px1_summary=px1,
        )
        write_pixiv_clustering_evidence(root, px1_summary=px1, result=summary)
        _write_receipt(root)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return root


@pytest.fixture(autouse=True)
def repository_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime-storage"
    runtime.mkdir()
    monkeypatch.setenv("VIOLET_SKIP_DOTENV", "1")
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("POSTGRES_DB", "scv2_px2_contract_temp")
    monkeypatch.setenv("TEST_DATABASE_URL", "")
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(runtime))
    monkeypatch.setenv("VIOLET_TEST_STORAGE_ROOT", str(runtime))
    monkeypatch.setattr(
        scv2_px2_contract,
        "repository_identity_snapshot",
        lambda *_args, **_kwargs: _repository(),
    )
    monkeypatch.setattr(
        scv2_px2_contract,
        "validate_px2_evidence_carry_forward",
        lambda *_args, **_kwargs: {"changed_paths": [], "docs_only": True},
    )


def _evidence(
    tmp_path: Path, base_evidence: Path
) -> tuple[dict[str, object], Scv2Px2EvidencePaths]:
    root = tmp_path / "px2-evidence"
    shutil.copytree(base_evidence, root)
    summary = json.loads((root / "public-summary.json").read_text(encoding="utf-8"))
    return summary, Scv2Px2EvidencePaths(root)


def _context(paths: Scv2Px2EvidencePaths) -> ContractRepositoryContext:
    return ContractRepositoryContext(
        repo_root=paths.root,
        expected_python=Path(sys.executable),
        scv2_px2_evidence=paths,
    )


def _rewrite_public(root: Path, summary: dict[str, object]) -> None:
    unsigned = dict(summary)
    unsigned.pop("canonical_fingerprint", None)
    summary["canonical_fingerprint"] = canonical_fingerprint(unsigned)
    _write(root / "public-summary.json", summary)
    _write_receipt(root)


def test_contract_registered_and_rebuilds_real_px1_resolver_and_persistence(
    tmp_path: Path, base_evidence: Path
) -> None:
    summary, paths = _evidence(tmp_path, base_evidence)
    contract = get_contract("scv2_px2_deterministic_pixiv_clustering_contract_v1")
    assert contract.custom_checks == ("scv2_px2_deterministic_pixiv_clustering",)
    assert "--px2-evidence" in build_parser().format_help()
    result = check_phase_contract(
        contract.contract_id,
        summary,
        repository_context=_context(paths),
    )
    assert result.passed, result.to_dict()
    details = result.details["scv2_px2_projection"]
    assert details["aggregate_count"] == 14
    assert details["signal_count"] == 40
    assert details["cluster_count"] == 20
    assert details["candidate_counts"] == {
        "cannot_link": 4,
        "deferred_nonblocking": 3,
        "must_link": 52,
    }
    assert result.target_met_claimed is True
    assert result.safe_to_merge_claimed is False
    assert result.route_approved is False


def test_fixed_evidence_loader_requires_exact_canonical_member_set(
    tmp_path: Path, base_evidence: Path
) -> None:
    _summary, paths = _evidence(tmp_path, base_evidence)
    loaded = load_px2_evidence_artifacts(paths)
    assert loaded["public-summary.json"]["cluster_count"] == 20
    (paths.root / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(Exception, match="fixed_member_set_invalid"):
        load_px2_evidence_artifacts(paths)


@pytest.mark.parametrize(
    "artifact",
    [
        "px1-consumer-summary.json",
        "candidate-dispositions.json",
        "ambiguous-ledger.json",
        "persistence-proof.json",
        "operation-receipt.json",
        "public-summary.json",
        RECEIPT_NAME,
    ],
)
def test_noncanonical_artifact_mutation_fails_closed(
    tmp_path: Path, base_evidence: Path, artifact: str
) -> None:
    summary, paths = _evidence(tmp_path, base_evidence)
    target = paths.root / artifact
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload.append({"tamper": True})
    else:
        payload["tamper"] = True
    target.write_text(json.dumps(payload), encoding="utf-8")
    result = check_phase_contract(
        "scv2_px2_deterministic_pixiv_clustering_contract_v1",
        summary,
        repository_context=_context(paths),
    )
    assert not result.passed


@pytest.mark.parametrize(
    ("mutation", "error_fragment"),
    [
        ("authority", "authority_projection_invalid"),
        ("candidate", "must_link_union_invalid"),
        ("ledger", "ambiguous_ledger_invalid"),
        ("persistence", "persistence_proof_invalid"),
        ("invariant", "invariants_invalid"),
        ("row_identity", "public_projection_forbidden_field"),
    ],
)
def test_positive_claim_mutations_cannot_survive_reprojection(
    tmp_path: Path,
    base_evidence: Path,
    mutation: str,
    error_fragment: str,
) -> None:
    summary, paths = _evidence(tmp_path, base_evidence)
    forged = copy.deepcopy(summary)
    if mutation == "authority":
        forged["authorities"]["real_provider_authorized"] = True
    elif mutation == "candidate":
        forged["candidate_dispositions"][0]["union_decision"] = False
        _write(paths.root / "candidate-dispositions.json", forged["candidate_dispositions"])
    elif mutation == "ledger":
        forged["ambiguous_ledger"]["record_count"] += 1
        ledger_unsigned = dict(forged["ambiguous_ledger"])
        ledger_unsigned.pop("canonical_fingerprint")
        forged["ambiguous_ledger"]["canonical_fingerprint"] = canonical_fingerprint(
            ledger_unsigned
        )
        _write(paths.root / "ambiguous-ledger.json", forged["ambiguous_ledger"])
    elif mutation == "persistence":
        forged["persistence_proof"]["temporary_persistence_idempotent"] = False
        persistence_unsigned = dict(forged["persistence_proof"])
        persistence_unsigned.pop("canonical_fingerprint")
        forged["persistence_proof"]["canonical_fingerprint"] = canonical_fingerprint(
            persistence_unsigned
        )
        _write(paths.root / "persistence-proof.json", forged["persistence_proof"])
    elif mutation == "invariant":
        forged["invariants"]["cannot_link_union_violation_count"] = 1
    elif mutation == "row_identity":
        forged["clusters"][0]["database_row_id"] = 123
    _rewrite_public(paths.root, forged)
    result = check_phase_contract(
        "scv2_px2_deterministic_pixiv_clustering_contract_v1",
        forged,
        repository_context=_context(paths),
    )
    assert not result.passed
    assert any(error_fragment in error.code for error in result.errors), result.to_dict()


def test_px1_input_mutation_is_rejected_by_independent_reconstruction(
    tmp_path: Path, base_evidence: Path
) -> None:
    summary, paths = _evidence(tmp_path, base_evidence)
    px1 = json.loads(
        (paths.root / "px1-consumer-summary.json").read_text(encoding="utf-8")
    )
    px1["px2_consumer_contract"]["aggregate_artifact_fingerprint"] = "0" * 64
    unsigned = dict(px1)
    unsigned.pop("canonical_fingerprint")
    px1["canonical_fingerprint"] = canonical_fingerprint(unsigned)
    _write(paths.root / "px1-consumer-summary.json", px1)
    _write_receipt(paths.root)
    result = check_phase_contract(
        "scv2_px2_deterministic_pixiv_clustering_contract_v1",
        summary,
        repository_context=_context(paths),
    )
    assert not result.passed


def test_missing_private_context_and_python_fail_closed() -> None:
    result = check_phase_contract(
        "scv2_px2_deterministic_pixiv_clustering_contract_v1",
        {"contract_id": "scv2_px2_deterministic_pixiv_clustering_contract_v1"},
    )
    assert not result.passed
    assert "px2_private_evidence_required" in {error.code for error in result.errors}
