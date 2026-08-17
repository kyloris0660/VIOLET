from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.fl1_i2_validation_receipt import (
    ReceiptError,
    canonical_focused_test_command,
    create_same_head_receipt,
    validate_canonical_focused_command,
)
from scripts.trusted_git import resolve_trusted_git_executable, trusted_git_environment


def _bindings() -> dict[str, str]:
    return {name: str(index) * 64 for index, name in enumerate(("config", "policy", "manifest", "ledger", "worker"), start=1)}


def test_canonical_pytest_command_disables_repository_bytecode() -> None:
    command = canonical_focused_test_command(Path(sys.executable))
    assert command[1:6] == ("-B", "-I", "-s", "-m", "pytest")


def _init_repo(path: Path) -> None:
    git = resolve_trusted_git_executable(repo_root=path)
    environment = trusted_git_environment()
    subprocess.run([git.path, "init", "-q", path], check=True, env=environment)
    subprocess.run([git.path, "-C", path, "config", "user.email", "synthetic@example.invalid"], check=True, env=environment)
    subprocess.run([git.path, "-C", path, "config", "user.name", "Synthetic"], check=True, env=environment)
    subprocess.run([git.path, "-C", path, "config", "core.autocrlf", "false"], check=True, env=environment)
    (path / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run([git.path, "-C", path, "add", "tracked.txt"], check=True, env=environment)
    subprocess.run([git.path, "-C", path, "commit", "-qm", "baseline"], check=True, env=environment)


def test_same_head_receipt_binds_all_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setattr(
        "scripts.fl1_i2_validation_receipt._run_validation_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=b"ok", stderr=b""),
    )
    receipt = create_same_head_receipt(
        repo_root=repo,
        evidence_root=evidence,
        output_name="receipt.json",
        run_id="synthetic-run",
        bindings=_bindings(),
        command=canonical_focused_test_command(Path(sys.executable)),
    )
    assert receipt.positive and receipt.same_head_tree and receipt.clean_before_after
    assert receipt.to_public_dict()["private_bindings_redacted"] is True


def test_head_or_tree_drift_never_issues_positive_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    def drift(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        (repo / "tracked.txt").write_text("drift\n", encoding="utf-8")
        return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("scripts.fl1_i2_validation_receipt._run_validation_command", drift)
    with pytest.raises(ReceiptError, match="positive_not_issued"):
        create_same_head_receipt(
            repo_root=repo,
            evidence_root=evidence,
            output_name="receipt.json",
            run_id="synthetic-run",
            bindings=_bindings(),
            command=canonical_focused_test_command(Path(sys.executable)),
        )


def test_caller_cannot_supply_positive_or_head_fields(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        create_same_head_receipt(  # type: ignore[call-arg]
            repo_root=tmp_path,
            evidence_root=tmp_path,
            output_name="receipt.json",
            run_id="run",
            bindings=_bindings(),
            command=(sys.executable, "-c", "pass"),
            positive=True,
            git_head="0" * 40,
        )


@pytest.mark.parametrize(
    "command",
    [
        ("true",),
        ("echo", "tests/test_scv2_fl1_i2_contract.py"),
        (*canonical_focused_test_command(Path(sys.executable)), "--collect-only"),
        (*canonical_focused_test_command(Path(sys.executable)), "-k", "nothing"),
        (*canonical_focused_test_command(Path(sys.executable)), "--ignore", "tests"),
        (sys.executable, "-I", "-s", "-m", "pytest", "-q", "stuffed-tests/test_scv2_fl1_i2_contract.py"),
    ],
)
def test_noncanonical_or_deselected_commands_cannot_issue_proof(command: tuple[str, ...]) -> None:
    with pytest.raises(ReceiptError, match="command_not_canonical|python_invalid"):
        validate_canonical_focused_command(command, Path(sys.executable))
