"""Unit coverage for the SCV2-PX2 same-HEAD receipt."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from scripts.scv2_px2_validation_receipt import (
    EVIDENCE_ARTIFACT_NAMES,
    Px2ValidationReceiptError,
    build_receipt_payload,
    canonical_focused_test_command,
    canonical_px2_validation_environment,
    validate_canonical_focused_command,
    validate_px2_evidence_carry_forward,
    validate_receipt_payload,
)
from scripts.trusted_git import resolve_trusted_git_executable, trusted_git_environment


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


def test_canonical_command_covers_px1_resolver_px2_and_contract_suites() -> None:
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
    assert "tests/test_phase45_sc1_source_concept_resolver.py" in command
    assert "tests/test_scv2_px2_pixiv_metadata_clustering.py" in command
    assert "tests/test_scv2_px2_contract.py" in command


def test_validation_environment_uses_task_owned_sqlite_identity(tmp_path: Path) -> None:
    base = {"PATH": "synthetic-path"}
    environment = canonical_px2_validation_environment(
        base,
        validation_temp_root=tmp_path,
    )
    assert base == {"PATH": "synthetic-path"}
    assert environment["VIOLET_ENV"] == "test"
    assert environment["POSTGRES_DB"] == "scv2_px2_task_temp"
    assert environment["TEST_DATABASE_URL"] == (
        f"sqlite:///{(tmp_path / 'px2-validation.sqlite3').as_posix()}"
    )
    assert Path(environment["VIOLET_STORAGE_ROOT"]).parent == tmp_path
    assert environment["VIOLET_TEST_STORAGE_ROOT"] == environment[
        "VIOLET_STORAGE_ROOT"
    ]


def test_receipt_validates_exact_repository_command_and_bindings() -> None:
    validate_receipt_payload(
        _receipt(),
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
    with pytest.raises(Px2ValidationReceiptError, match="command_not_canonical"):
        validate_canonical_focused_command(command, Path(sys.executable))


def test_failed_command_never_becomes_positive() -> None:
    receipt = _receipt(exit_code=1)
    assert receipt["positive"] is False
    with pytest.raises(Px2ValidationReceiptError, match="not_positive"):
        validate_receipt_payload(
            receipt,
            approved_python=Path(sys.executable),
            expected_repository=_repository(),
            expected_bindings=_bindings(),
        )


def test_receipt_binding_mutation_fails_closed() -> None:
    changed = dict(_bindings())
    changed["public-summary.json"] = "f" * 64
    with pytest.raises(Px2ValidationReceiptError, match="evidence_binding_mismatch"):
        validate_receipt_payload(
            _receipt(),
            approved_python=Path(sys.executable),
            expected_repository=_repository(),
            expected_bindings=changed,
        )


def test_docs_only_carry_forward_rejects_behavior_mutation(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    git = resolve_trusted_git_executable(repo_root=repository_root)
    repo = tmp_path / "px2-carry-forward"
    repo.mkdir()
    environment = trusted_git_environment()

    def run(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        completed = subprocess.run(
            [str(git.path), *arguments],
            cwd=repo,
            env=environment,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
        return completed

    run("init")
    behavior = repo / "backend" / "app" / "services" / "pixiv_metadata_clustering_service.py"
    behavior.parent.mkdir(parents=True)
    behavior.write_text('PX2_VERSION = "v1"\n', encoding="utf-8")
    run("add", "--", behavior.relative_to(repo).as_posix())
    run(
        "-c",
        "user.name=SCV2 PX2 Synthetic",
        "-c",
        "user.email=scv2-px2-synthetic.invalid",
        "commit",
        "-m",
        "synthetic evidence head",
    )
    evidence_head = run("rev-parse", "HEAD^{commit}").stdout.decode().strip()
    evidence_tree = run("rev-parse", "HEAD^{tree}").stdout.decode().strip()

    handoff = repo / "docs" / "current-handoff.md"
    handoff.parent.mkdir(parents=True)
    handoff.write_text("Synthetic docs-only projection.\n", encoding="utf-8")
    run("add", "--", handoff.relative_to(repo).as_posix())
    run(
        "-c",
        "user.name=SCV2 PX2 Synthetic",
        "-c",
        "user.email=scv2-px2-synthetic.invalid",
        "commit",
        "-m",
        "synthetic docs projection",
    )
    result = validate_px2_evidence_carry_forward(
        repo,
        evidence_head=evidence_head,
        evidence_tree=evidence_tree,
    )
    assert result["changed_paths"] == ["docs/current-handoff.md"]

    behavior.write_text('PX2_VERSION = "v2"\n', encoding="utf-8")
    run("add", "--", behavior.relative_to(repo).as_posix())
    run(
        "-c",
        "user.name=SCV2 PX2 Synthetic",
        "-c",
        "user.email=scv2-px2-synthetic.invalid",
        "commit",
        "-m",
        "synthetic behavior mutation",
    )
    with pytest.raises(
        Px2ValidationReceiptError,
        match="px2_evidence_behavioral_carry_forward_invalid",
    ):
        validate_px2_evidence_carry_forward(
            repo,
            evidence_head=evidence_head,
            evidence_tree=evidence_tree,
        )
