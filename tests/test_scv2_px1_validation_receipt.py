"""Unit coverage for the SCV2-PX1 same-HEAD receipt."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from scripts.scv2_px1_validation_receipt import (
    EVIDENCE_ARTIFACT_NAMES,
    Px1ValidationReceiptError,
    build_receipt_payload,
    canonical_focused_test_command,
    validate_canonical_focused_command,
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


def _receipt(*, exit_code: int = 0) -> dict[str, object]:
    python = Path(sys.executable)
    return build_receipt_payload(
        repository_before=_repository(),
        repository_after=_repository(),
        approved_python=python,
        command=canonical_focused_test_command(python),
        command_result=subprocess.CompletedProcess(
            args=(), returncode=exit_code, stdout=b"synthetic passed", stderr=b""
        ),
        validation_environment_fingerprint="1" * 64,
        validation_temp_root_identity_fingerprint="2" * 64,
        bindings=_bindings(),
    )


def test_canonical_command_covers_px1_and_compatibility_suites() -> None:
    command = canonical_focused_test_command(Path(sys.executable))
    assert command[1:9] == (
        "-B",
        "-I",
        "-s",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
    )
    assert "tests/test_scv2_px1_pixiv_metadata_consolidation.py" in command
    assert "tests/test_pixiv_metadata_ingestion_service.py" in command
    assert "tests/test_phase45_sc1_source_concept_resolver.py" in command


def test_receipt_validates_exact_repository_command_and_evidence_bindings() -> None:
    receipt = _receipt()
    validate_receipt_payload(
        receipt,
        approved_python=Path(sys.executable),
        expected_repository=_repository(),
        expected_bindings=_bindings(),
    )


@pytest.mark.parametrize(
    "command",
    [
        (sys.executable, "-m", "pytest"),
        (*canonical_focused_test_command(Path(sys.executable)), "--collect-only"),
        (*canonical_focused_test_command(Path(sys.executable))[:-1],),
    ],
)
def test_noncanonical_command_cannot_issue_receipt(command: tuple[str, ...]) -> None:
    with pytest.raises(Px1ValidationReceiptError, match="command_not_canonical"):
        validate_canonical_focused_command(command, Path(sys.executable))


def test_failed_command_never_becomes_positive() -> None:
    receipt = _receipt(exit_code=1)
    assert receipt["positive"] is False
    with pytest.raises(Px1ValidationReceiptError, match="not_positive"):
        validate_receipt_payload(
            receipt,
            approved_python=Path(sys.executable),
            expected_repository=_repository(),
            expected_bindings=_bindings(),
        )


def test_receipt_binding_mutation_fails_closed() -> None:
    receipt = _receipt()
    changed = dict(_bindings())
    changed["public-summary.json"] = "f" * 64
    with pytest.raises(Px1ValidationReceiptError, match="evidence_binding_mismatch"):
        validate_receipt_payload(
            receipt,
            approved_python=Path(sys.executable),
            expected_repository=_repository(),
            expected_bindings=changed,
        )
