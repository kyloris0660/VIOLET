from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.fl1_i2_validation_receipt import (
    ReceiptError,
    create_same_head_receipt,
)
from scripts.trusted_git import resolve_trusted_git_executable, trusted_git_environment


def _bindings() -> dict[str, str]:
    return {name: str(index) * 64 for index, name in enumerate(("config", "policy", "manifest", "ledger", "worker"), start=1)}


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


def test_same_head_receipt_binds_all_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    receipt = create_same_head_receipt(
        repo_root=repo,
        evidence_root=evidence,
        output_name="receipt.json",
        run_id="synthetic-run",
        bindings=_bindings(),
        command=(sys.executable, "-c", "raise SystemExit(0)"),
    )
    assert receipt.positive and receipt.same_head_tree and receipt.clean_before_after
    assert receipt.to_public_dict()["private_bindings_redacted"] is True


def test_head_or_tree_drift_never_issues_positive_receipt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; Path('tracked.txt').write_text('drift\\n', encoding='utf-8')",
    )
    with pytest.raises(ReceiptError, match="positive_not_issued"):
        create_same_head_receipt(
            repo_root=repo,
            evidence_root=evidence,
            output_name="receipt.json",
            run_id="synthetic-run",
            bindings=_bindings(),
            command=command,
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
