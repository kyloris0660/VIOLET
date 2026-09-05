"""Unit coverage for the SCV2-PX3 same-HEAD receipt."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from scripts.scv2_px3_validation_receipt import (
    EVIDENCE_ARTIFACT_NAMES,
    PX3_DOCS_ONLY_CARRY_FORWARD_ALLOWLIST,
    Px3ValidationReceiptError,
    canonical_fingerprint,
    canonical_focused_test_command,
    canonical_px3_validation_environment,
    execution_environment_policy_fingerprint,
    validate_receipt_payload,
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


def _bindings() -> dict[str, str]:
    return {
        name: f"{index:x}" * 64
        for index, name in enumerate(EVIDENCE_ARTIFACT_NAMES, start=1)
    }


def _receipt() -> dict[str, object]:
    command = list(canonical_focused_test_command(Path(sys.executable)))
    return {
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
        "evidence_bindings": _bindings(),
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


def test_canonical_command_covers_full_px1_px2_px3_compatibility() -> None:
    command = canonical_focused_test_command(Path(sys.executable))
    assert command[1:9] == (
        "-B", "-I", "-s", "-m", "pytest", "-p", "no:cacheprovider", "-q"
    )
    for test in (
        "tests/test_scv2_px1_pixiv_metadata_consolidation.py",
        "tests/test_scv2_px2_pixiv_metadata_clustering.py",
        "tests/test_phase45_sc1_source_concept_resolver.py",
        "tests/test_scv2_px3_pixiv_product_integration.py",
        "tests/test_scv2_px3_pixiv_product_api.py",
        "tests/test_scv2_px3_contract.py",
    ):
        assert test in command


def test_validation_environment_is_task_owned_and_all_product_flags_are_off(
    tmp_path: Path,
) -> None:
    environment = canonical_px3_validation_environment(
        {"PATH": "synthetic"}, validation_temp_root=tmp_path
    )
    assert environment["VIOLET_ENV"] == "test"
    assert environment["TEST_DATABASE_URL"] == f"sqlite:///{(tmp_path / 'px3-validation.sqlite3').as_posix()}"
    assert Path(environment["VIOLET_STORAGE_ROOT"]).parent == tmp_path
    assert environment["SCV2_PX3_PRODUCT_INTEGRATION_ENABLED"] == "0"
    assert environment["SCV2_PX3_PRODUCT_APPLY_ENABLED"] == "0"
    assert environment["SCV2_PX3_SYNTHETIC_UI_ENABLED"] == "0"


def test_receipt_validates_exact_repository_command_and_bindings() -> None:
    validate_receipt_payload(
        _receipt(),
        approved_python=Path(sys.executable),
        expected_repository=_repository(),
        expected_bindings=_bindings(),
    )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("exit_code", 1, "not_positive"),
        ("command_fingerprint", "f" * 64, "command_fingerprint_mismatch"),
        ("git_tree", "f" * 40, "repository_binding_mismatch"),
        ("positive", False, "not_positive"),
    ],
)
def test_receipt_mutations_fail_closed(field: str, value: object, error: str) -> None:
    receipt = _receipt()
    receipt[field] = value
    with pytest.raises(Px3ValidationReceiptError, match=error):
        validate_receipt_payload(
            receipt,
            approved_python=Path(sys.executable),
            expected_repository=_repository(),
            expected_bindings=_bindings(),
        )


def test_docs_only_carry_forward_is_exactly_six_governance_paths() -> None:
    assert PX3_DOCS_ONLY_CARRY_FORWARD_ALLOWLIST == {
        "docs/current-handoff.md",
        "docs/phase-contracts.md",
        "docs/project-roadmap.md",
        "docs/roadmap/current-mainline-roadmap.md",
        "docs/state/current-phase.json",
        "docs/development/scv2-px3-controlled-canary.md",
    }
