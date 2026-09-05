"""Executable evidence and coordinated-mutation coverage for SCV2-PX3."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from app.services.pixiv_metadata_projection_service import canonical_fingerprint
from app.services.pixiv_product_integration_service import (
    run_repository_synthetic_pixiv_product_integration,
    write_pixiv_product_evidence,
)
from scripts.check_phase_contract import build_parser
from scripts.phase_contracts import ContractRepositoryContext, check_phase_contract, get_contract
from scripts.phase_contracts import scv2_px3_contract
from scripts.phase_contracts.scv2_px3_contract import (
    Scv2Px3EvidencePaths,
    load_px3_evidence_artifacts,
)
from scripts.scv2_px3_validation_receipt import (
    EVIDENCE_ARTIFACT_NAMES,
    RECEIPT_NAME,
    canonical_focused_test_command,
    canonical_json_bytes,
    evidence_bindings,
    execution_environment_policy_fingerprint,
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
    command = list(canonical_focused_test_command(Path(sys.executable)))
    receipt = {
        "schema_version": "violet.scv2-px3-local-validation-receipt.v1",
        "contract_id": "scv2_px3_pixiv_product_integration_contract_v1",
        "git_head": "a" * 40,
        "git_tree": "b" * 40,
        "trusted_git_fingerprint": "c" * 64,
        "python_executable_fingerprint": "d" * 64,
        "approved_python_runtime_fingerprint": "e" * 64,
        "execution_environment_policy_fingerprint": execution_environment_policy_fingerprint(),
        "execution_environment_fingerprint": "1" * 64,
        "validation_temp_root_identity_fingerprint": "2" * 64,
        "evidence_bindings": evidence_bindings(payloads),
        "command_argv": command,
        "command_fingerprint": canonical_fingerprint(command),
        "exit_code": 0,
        "stdout_fingerprint": "3" * 64,
        "stderr_fingerprint": "4" * 64,
        "same_head_tree": True,
        "clean_before_after": True,
        "positive": True,
        "trust_level": "local_operator_receipt",
        "machine_verifiable_ci": False,
        "owner_authority_machine_verifiable": False,
    }
    _write(root / RECEIPT_NAME, receipt)


@pytest.fixture(scope="module")
def base_evidence(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("scv2-px3-contract-base")
    runtime = tmp_path_factory.mktemp("scv2-px3-contract-runtime")
    previous = {key: os.environ.get(key) for key in (
        "VIOLET_SKIP_DOTENV", "VIOLET_ENV", "POSTGRES_DB", "TEST_DATABASE_URL",
        "VIOLET_STORAGE_ROOT", "VIOLET_TEST_STORAGE_ROOT",
    )}
    os.environ.update({
        "VIOLET_SKIP_DOTENV": "1",
        "VIOLET_ENV": "test",
        "POSTGRES_DB": "scv2_px3_contract_temp",
        "TEST_DATABASE_URL": "",
        "VIOLET_STORAGE_ROOT": os.fspath(runtime),
        "VIOLET_TEST_STORAGE_ROOT": os.fspath(runtime),
    })
    try:
        px1, px2, px3 = run_repository_synthetic_pixiv_product_integration(workspace=root)
        write_pixiv_product_evidence(root, px1_summary=px1, px2_result=px2, px3_result=px3)
        _write_receipt(root)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return root


@pytest.fixture(autouse=True)
def repository_proof(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("VIOLET_SKIP_DOTENV", "1")
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("POSTGRES_DB", "scv2_px3_contract_temp")
    monkeypatch.setenv("TEST_DATABASE_URL", "")
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(runtime))
    monkeypatch.setenv("VIOLET_TEST_STORAGE_ROOT", str(runtime))
    monkeypatch.setattr(
        scv2_px3_contract, "repository_identity_snapshot", lambda *_args, **_kwargs: _repository()
    )
    monkeypatch.setattr(
        scv2_px3_contract,
        "validate_px3_evidence_carry_forward",
        lambda *_args, **_kwargs: {"changed_paths": [], "docs_only": True},
    )


def _evidence(tmp_path: Path, base: Path) -> tuple[dict[str, object], Scv2Px3EvidencePaths]:
    root = tmp_path / "evidence"
    shutil.copytree(base, root)
    summary = json.loads((root / "public-summary.json").read_text(encoding="utf-8"))
    return summary, Scv2Px3EvidencePaths(root)


def _context(paths: Scv2Px3EvidencePaths) -> ContractRepositoryContext:
    return ContractRepositoryContext(
        repo_root=paths.root,
        expected_python=Path(sys.executable),
        scv2_px3_evidence=paths,
    )


def _rewrite_public(root: Path, summary: dict[str, object]) -> None:
    unsigned = dict(summary)
    unsigned.pop("canonical_fingerprint", None)
    summary["canonical_fingerprint"] = canonical_fingerprint(unsigned)
    _write(root / "public-summary.json", summary)
    _write_receipt(root)


def test_contract_registered_and_independently_rebuilds_full_chain(
    tmp_path: Path, base_evidence: Path
) -> None:
    summary, paths = _evidence(tmp_path, base_evidence)
    contract = get_contract("scv2_px3_pixiv_product_integration_contract_v1")
    assert contract.custom_checks == ("scv2_px3_pixiv_product_integration",)
    assert "--px3-evidence" in build_parser().format_help()
    result = check_phase_contract(contract.contract_id, summary, repository_context=_context(paths))
    assert result.passed, result.to_dict()
    assert result.details["scv2_px3_projection"] == {
        "cluster_count": 20,
        "candidate_disposition_count": 59,
        "ambiguity_record_count": 29,
        "product_result_fingerprint": summary["product_result_fingerprint"],
        "canonical_fingerprint": summary["canonical_fingerprint"],
    }
    assert result.target_met_claimed is True
    assert result.safe_to_merge_claimed is False


def test_fixed_loader_requires_canonical_exact_member_set(
    tmp_path: Path, base_evidence: Path
) -> None:
    _summary, paths = _evidence(tmp_path, base_evidence)
    assert load_px3_evidence_artifacts(paths)["public-summary.json"]["counts"]["cluster_count"] == 20
    (paths.root / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(Exception, match="fixed_member_set_invalid"):
        load_px3_evidence_artifacts(paths)


def test_coordinated_product_and_all_fingerprint_mutation_still_fails(
    tmp_path: Path, base_evidence: Path
) -> None:
    summary, paths = _evidence(tmp_path, base_evidence)
    forged = copy.deepcopy(summary)
    forged["clusters"][0]["primary_display_name"] = "Coordinated forgery"
    business = {
        "scope_key": forged["scope_key"],
        "source_mode": forged["source_mode"],
        "px1_input_fingerprint": forged["px1_input_fingerprint"],
        "px2_business_projection_fingerprint": forged["px2_business_projection_fingerprint"],
        "resolver_version": forged["resolver_version"],
        "context_policy_version": forged["context_policy_version"],
        "candidate_policy_version": forged["candidate_policy_version"],
        "product_policy_version": forged["product_policy_version"],
        "clusters": forged["clusters"],
        "candidate_dispositions": forged["candidate_dispositions"],
        "ambiguity_records": forged["ambiguity_records"],
    }
    forged["product_result_fingerprint"] = canonical_fingerprint(business)
    forged["run_key"] = f"scv2-px3:{forged['product_result_fingerprint'][:32]}"
    _rewrite_public(paths.root, forged)
    result = check_phase_contract(
        "scv2_px3_pixiv_product_integration_contract_v1",
        forged,
        repository_context=_context(paths),
    )
    assert not result.passed
    assert any("independent_replay_projection_mismatch" in error.code for error in result.errors)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("authorities", {"production_authorized": True}, "authority_map_invalid"),
        ("invariants", {"deferred_union_violation_count": 1}, "invariants_invalid"),
        ("operation_receipt", {"provider_network_activity": 1}, "operation_receipt_invalid"),
    ],
)
def test_boundary_mutations_fail_closed(
    tmp_path: Path,
    base_evidence: Path,
    field: str,
    value: dict[str, object],
    error: str,
) -> None:
    summary, paths = _evidence(tmp_path, base_evidence)
    forged = copy.deepcopy(summary)
    forged[field].update(value)
    if field in {"operation_receipt"}:
        _write(paths.root / "operation-receipt.json", forged[field])
    _rewrite_public(paths.root, forged)
    result = check_phase_contract(
        "scv2_px3_pixiv_product_integration_contract_v1",
        forged,
        repository_context=_context(paths),
    )
    assert not result.passed
    assert any(error in finding.code for finding in result.errors), result.to_dict()


def test_coordinated_binding_search_acceptance_rollback_claims_are_rederived(tmp_path, base_evidence):
    summary, paths = _evidence(tmp_path, base_evidence)
    forged = copy.deepcopy(summary)
    persistence = forged['persistence_proof']
    binding = persistence['media_binding_proof']
    outcome = binding['outcomes'][0]
    outcome.update(persisted_binding_count=0, accepted_plan_mismatch_rejected_count=0,
                   rollback_immediately_revoked_search_and_detail=False)
    outcome['actual_search_results']['AsterHistorical'] = []
    binding['canonical_fingerprint'] = canonical_fingerprint({k: v for k, v in binding.items() if k != 'canonical_fingerprint'})
    persistence['canonical_fingerprint'] = canonical_fingerprint({k: v for k, v in persistence.items() if k != 'canonical_fingerprint'})
    _write(paths.root / 'product-persistence-proof.json', persistence)
    _rewrite_public(paths.root, forged)
    result = check_phase_contract('scv2_px3_pixiv_product_integration_contract_v1', forged,
                                   repository_context=_context(paths))
    assert not result.passed
    assert any('independent_replay_projection_mismatch' in error.code for error in result.errors)


def test_missing_private_context_fails_closed() -> None:
    result = check_phase_contract(
        "scv2_px3_pixiv_product_integration_contract_v1",
        {"contract_id": "scv2_px3_pixiv_product_integration_contract_v1"},
    )
    assert not result.passed
    assert "px3_private_evidence_required" in {error.code for error in result.errors}
