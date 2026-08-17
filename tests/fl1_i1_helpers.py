"""Temporary-only helpers for SCV2-FL1-I1 tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.fl1_i1_inventory import BUDGET_SCHEMA_VERSION, InventoryBudgets
from scripts.fl1_i1_runtime_context import (
    REQUIRED_PROTECTED_ROOT_ROLES,
    SourceMode,
    build_trusted_runtime_context,
    private_config_payload_for_temporary_roots,
)


@dataclass(frozen=True)
class I1Fixture:
    root: Path
    repo: Path
    source: Path
    evidence: Path
    sandbox: Path
    roots: dict[str, Path]
    private_config: Path
    budgets_config: Path
    synthetic_attributes: Path
    budgets: InventoryBudgets

    def context(self, *, source_mode: str = SourceMode.SYNTHETIC_FIXTURE.value):
        return build_trusted_runtime_context(
            repo_root=self.repo,
            expected_python=Path(sys.executable),
            private_root_config=self.private_config,
            source_root=self.source,
            source_mode=source_mode,
            source_scope_id="pytest-temporary-fixture",
        )

    def scanner_args(self) -> list[str]:
        return [
            "--repo-root",
            os.fspath(self.repo),
            "--expected-python",
            os.fspath(Path(sys.executable)),
            "--private-root-config",
            os.fspath(self.private_config),
            "--source-root",
            os.fspath(self.source),
            "--source-mode",
            SourceMode.SYNTHETIC_FIXTURE.value,
            "--source-scope-id",
            "pytest-temporary-fixture",
            "--evidence-root",
            os.fspath(self.evidence),
            "--budgets-config",
            os.fspath(self.budgets_config),
            "--attribute-adapter",
            "synthetic",
            "--synthetic-attributes",
            os.fspath(self.synthetic_attributes),
        ]

    def contract_evidence(
        self,
        *,
        run_id: str,
        receipt: Path,
        report: Path,
    ) -> dict[str, Any]:
        return {
            "private_root_config": os.fspath(self.private_config),
            "source_root": os.fspath(self.source),
            "source_mode": SourceMode.SYNTHETIC_FIXTURE.value,
            "source_scope_id": "pytest-temporary-fixture",
            "budgets_config": os.fspath(self.budgets_config),
            "run_dir": os.fspath(self.evidence / run_id),
            "validation_receipt": os.fspath(receipt),
            "validation_report": os.fspath(report),
        }


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def git_head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def make_i1_fixture(tmp_path: Path, *, populate: bool = True) -> I1Fixture:
    root = tmp_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "trusted-repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "fl1-i1-tests@example.invalid")
    _git(repo, "config", "user.name", "FL1 I1 Tests")
    # Evidence Git disables caller global config.  Pin checkout normalization in
    # the synthetic repository so a host-level core.autocrlf value cannot make
    # the freshly committed fixture appear dirty under that trusted view.
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "tracked.txt").write_text("trusted repository\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "test baseline")

    role_parent = root / "roles"
    role_parent.mkdir()
    roots = {
        "production_source_root": role_parent / "production-source",
        "production_icloud_root": role_parent / "production-icloud",
        "production_app_storage_root": role_parent / "production-app-storage",
        "accepted_evidence_storage_root": role_parent / "accepted-evidence",
        "repository_worktree_root": repo,
        "phase_evidence_output_root": role_parent / "phase-evidence",
        "synthetic_test_sandbox_root": role_parent / "synthetic-sandbox",
    }
    assert set(roots) == set(REQUIRED_PROTECTED_ROOT_ROLES)
    for role, path in roots.items():
        if role != "repository_worktree_root":
            path.mkdir()
    sandbox = roots["synthetic_test_sandbox_root"]
    source = sandbox / "source-fixture"
    source.mkdir()
    evidence = roots["phase_evidence_output_root"]

    if populate:
        jpeg = b"\xff\xd8\xff\xe0" + b"bounded synthetic jpeg" + b"\xff\xd9"
        png = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\x0dIHDR"
            + b"bounded-synthetic-png"
            + b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        (source / "a.jpg").write_bytes(jpeg)
        (source / "b.jpg").write_bytes(jpeg)
        (source / "c.png").write_bytes(png)
        (source / "d.txt").write_text("unsupported", encoding="utf-8")
        (source / "e.webp").write_bytes(b"recall risk")
        (source / "f.icloud").write_text("placeholder", encoding="utf-8")
        (source / "g.gif").write_bytes(b"unknown attributes")

    private_config = root / "private-roots.json"
    write_json(
        private_config,
        private_config_payload_for_temporary_roots(
            roots,
            private_derivation_key=bytes.fromhex("11" * 32),
        ),
    )
    budgets = InventoryBudgets(
        max_discovered_items=100,
        max_directory_entries=200,
        max_total_observed_bytes=10 * 1024 * 1024,
        max_per_file_hash_bytes=1024 * 1024,
        max_total_hashed_bytes=10 * 1024 * 1024,
        read_chunk_size=4,
        per_item_timeout_seconds=5.0,
        max_unreadable_failures=5,
        max_consecutive_failures=3,
        max_same_reason_failures=3,
        batch_size=100,
    )
    budgets_config = root / "budgets.json"
    write_json(budgets_config, budgets.to_dict())
    assert budgets.to_dict()["schema_version"] == BUDGET_SCHEMA_VERSION
    attributes = root / "synthetic-attributes.json"
    write_json(
        attributes,
        {"observations": {"e.webp": "recall_risk", "g.gif": "unknown"}},
    )
    return I1Fixture(
        root=root,
        repo=repo,
        source=source,
        evidence=evidence,
        sandbox=sandbox,
        roots=roots,
        private_config=private_config,
        budgets_config=budgets_config,
        synthetic_attributes=attributes,
        budgets=budgets,
    )


def run_cli(project_root: Path, fixture: I1Fixture, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            os.fspath(project_root / "scripts" / "fl1_i1_inventory.py"),
            "scan",
            *fixture.scanner_args(),
            *extra,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
