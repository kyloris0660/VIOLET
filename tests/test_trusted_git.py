from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import check_documentation_state as documentation_state
from scripts.trusted_git import (
    TrustedGitError,
    assert_trusted_worktree_clean,
    inspect_worktree_drift,
    parse_porcelain_v2_z,
    resolve_trusted_git_executable,
    run_trusted_git_text,
    trusted_git_environment,
    validate_git_path,
)
from tests.fl1_i1_helpers import make_i1_fixture


def _bootstrap_git(repo: Path, *arguments: str) -> str:
    git = resolve_trusted_git_executable(excluded_roots=(repo,))
    completed = subprocess.run(
        [os.fspath(git.path), "-C", os.fspath(repo), *arguments],
        cwd=repo,
        env=trusted_git_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=15,
    )
    return completed.stdout.strip()


def _new_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _bootstrap_git(repo, "init", "--initial-branch=main")
    _bootstrap_git(repo, "config", "user.email", "trusted-git@example.invalid")
    _bootstrap_git(repo, "config", "user.name", "Trusted Git Test")
    _bootstrap_git(repo, "config", "core.autocrlf", "false")
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _bootstrap_git(repo, "add", "README.md")
    _bootstrap_git(repo, "commit", "-m", "baseline")
    return repo


def _commit(repo: Path, message: str, *paths: str) -> str:
    _bootstrap_git(repo, "add", "--", *paths)
    _bootstrap_git(repo, "commit", "-m", message)
    return _bootstrap_git(repo, "rev-parse", "HEAD")


def test_shared_environment_scrubs_all_mixed_case_git_controls() -> None:
    environment = trusted_git_environment(
        {
            "Path": "ordinary",
            "gIt_DiR": "hostile",
            "Git_Work_Tree": "hostile",
            "GIT_CONFIG_COUNT": "1",
            "git_replace_ref_base": "refs/hostile/",
        }
    )
    assert environment["Path"] == "ordinary"
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    allowed = {
        "git_no_replace_objects",
        "git_config_nosystem",
        "git_config_global",
        "git_config_system",
        "git_optional_locks",
        "git_terminal_prompt",
    }
    assert not {
        key.casefold()
        for key in environment
        if key.casefold().startswith("git_")
    } - allowed


@pytest.mark.parametrize(
    "path",
    [
        r"docs\current-handoff.md",
        "/absolute",
        "C:/absolute",
        ".",
        "../escape",
        "a/../escape",
        "a//b",
        "a/./b",
        "control\npath",
    ],
)
def test_verbatim_git_path_rejects_ambiguous_or_escaping_values(path: str) -> None:
    with pytest.raises(TrustedGitError, match="trusted_git_path_invalid"):
        validate_git_path(path)


def test_posix_literal_backslash_cannot_collide_with_forward_slash_allowlist() -> None:
    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_governance_projection_path_invalid",
    ):
        documentation_state._validate_fl1_i2_projection_paths(
            [r"docs\current-handoff.md"]
        )
    documentation_state._validate_fl1_i2_projection_paths(
        ["docs/current-handoff.md"]
    )


def test_porcelain_v2_z_parser_preserves_raw_paths_and_rejects_truncation() -> None:
    entries = parse_porcelain_v2_z(b"? report.md\0? image.jpg\0")
    assert [(entry.record_type, entry.path) for entry in entries] == [
        ("?", "report.md"),
        ("?", "image.jpg"),
    ]
    with pytest.raises(TrustedGitError, match="trusted_git_status_unparseable"):
        parse_porcelain_v2_z(b"? report.md")


def test_ordinary_untracked_artifacts_are_retained_but_behavior_files_block(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path, populate=False)
    git = resolve_trusted_git_executable(excluded_roots=(fixture.repo,))
    (fixture.repo / "operator-report.md").write_text("report\n", encoding="utf-8")
    (fixture.repo / "fixture-image.jpg").write_bytes(b"synthetic")

    summary = assert_trusted_worktree_clean(git, fixture.repo)
    assert summary.ordinary_untracked_count == 2
    assert summary.behavior_untracked_count == 0
    assert summary.uncertain_untracked_count == 0

    (fixture.repo / "importable.py").write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(
        TrustedGitError, match="evidence_worktree_behavior_affecting_untracked:1"
    ):
        assert_trusted_worktree_clean(git, fixture.repo)


def test_untracked_unknown_type_fails_closed_without_path_disclosure(
    tmp_path: Path,
) -> None:
    fixture = make_i1_fixture(tmp_path, populate=False)
    git = resolve_trusted_git_executable(excluded_roots=(fixture.repo,))
    (fixture.repo / "opaque.unknown").write_bytes(b"synthetic")
    summary = inspect_worktree_drift(git, fixture.repo)
    assert summary.uncertain_untracked_count == 1
    with pytest.raises(
        TrustedGitError, match="evidence_worktree_identity_or_type_uncertain:1"
    ) as caught:
        assert_trusted_worktree_clean(git, fixture.repo)
    assert "opaque" not in str(caught.value)


def test_ignored_behavior_files_are_bounded_and_fail_closed_without_path_disclosure(tmp_path: Path) -> None:
    repo = _new_repo(tmp_path)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    _commit(repo, "ignore synthetic directory", ".gitignore")
    ignored = repo / "ignored"
    ignored.mkdir()
    for name in (".env", ".env.local", "pytest.ini", "conftest.py", "redirect.pth", "sitecustomize.py", "importable.py"):
        (ignored / name).write_text("synthetic\n", encoding="utf-8")
    git = resolve_trusted_git_executable(excluded_roots=(repo,))
    summary = inspect_worktree_drift(git, repo)
    assert summary.behavior_ignored_count == 7
    with pytest.raises(TrustedGitError, match="behavior_affecting_ignored:7") as caught:
        assert_trusted_worktree_clean(git, repo)
    assert ".env.local" not in str(caught.value)


def test_explicit_ordinary_ignored_cache_and_private_receipts_can_remain(tmp_path: Path) -> None:
    repo = _new_repo(tmp_path)
    (repo / ".gitignore").write_text(".pytest_cache/\n.local_manifests/\n", encoding="utf-8")
    _commit(repo, "ignore task artifacts", ".gitignore")
    cache = repo / ".pytest_cache"
    cache.mkdir()
    (cache / "state.txt").write_text("synthetic\n", encoding="utf-8")
    private = repo / ".local_manifests" / "scv2-fl1-i2-private"
    private.mkdir(parents=True)
    (private / "receipt.json").write_text("{}\n", encoding="utf-8")
    git = resolve_trusted_git_executable(excluded_roots=(repo,))
    summary = assert_trusted_worktree_clean(git, repo)
    assert summary.ordinary_ignored_count == 2
    assert summary.behavior_ignored_count == 0
    assert summary.uncertain_ignored_count == 0


def test_ignored_enumeration_budget_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _new_repo(tmp_path)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    _commit(repo, "ignore synthetic directory", ".gitignore")
    ignored = repo / "ignored"
    ignored.mkdir()
    (ignored / "one.txt").write_text("one\n", encoding="utf-8")
    (ignored / "two.txt").write_text("two\n", encoding="utf-8")
    monkeypatch.setattr("scripts.trusted_git.MAX_STATUS_ENTRIES", 1)
    git = resolve_trusted_git_executable(excluded_roots=(repo,))
    with pytest.raises(TrustedGitError, match="status_budget_exceeded"):
        inspect_worktree_drift(git, repo)


def test_hostile_local_core_worktree_fails_closed_even_with_explicit_pin(
    tmp_path: Path,
) -> None:
    repo = _new_repo(tmp_path)
    hostile = tmp_path / "hostile-worktree"
    hostile.mkdir()
    _bootstrap_git(repo, "config", "core.worktree", os.fspath(hostile))
    git = resolve_trusted_git_executable(excluded_roots=(repo, hostile))

    with pytest.raises(
        TrustedGitError, match="trusted_git_local_core_worktree_rejected"
    ):
        run_trusted_git_text(repo, ("rev-parse", "--show-toplevel"), git=git)


def test_hostile_environment_and_injected_fsmonitor_cannot_redirect_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _new_repo(tmp_path)
    marker = tmp_path / "fsmonitor-ran"
    monitor = tmp_path / "monitor.cmd"
    monitor.write_text(f"@echo off\r\necho ran>\"{marker}\"\r\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", os.fspath(tmp_path / "not-a-repo"))
    monkeypatch.setenv("gIt_WoRk_TrEe", os.fspath(tmp_path / "hostile"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", os.fspath(monitor))

    completed = run_trusted_git_text(
        repo, ("status", "--porcelain=v2", "--untracked-files=no")
    )

    assert completed.returncode == 0
    assert not marker.exists()


def test_frozen_projection_history_checks_each_parent_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _new_repo(tmp_path)
    accepted = _bootstrap_git(repo, "rev-parse", "HEAD")
    (repo / "backend").mkdir()
    (repo / "backend" / "forbidden.py").write_text("bad = True\n", encoding="utf-8")
    _commit(repo, "forbidden intermediate", "backend/forbidden.py")
    (repo / "backend" / "forbidden.py").unlink()
    _bootstrap_git(repo, "add", "-u", "--", "backend/forbidden.py")
    _bootstrap_git(repo, "commit", "-m", "restore endpoint")
    projection = _bootstrap_git(repo, "rev-parse", "HEAD")
    projection_tree = _bootstrap_git(repo, "rev-parse", "HEAD^{tree}")
    monkeypatch.setattr(documentation_state, "FL1_I2_APPROVED_PLANNING_HEAD", accepted)
    monkeypatch.setattr(documentation_state, "FL1_I2_PLANNING_PROJECTION_HEAD", projection)
    monkeypatch.setattr(documentation_state, "FL1_I2_PLANNING_PROJECTION_TREE", projection_tree)

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_governance_projection_path_invalid",
    ):
        documentation_state._validate_fl1_i2_projection_history(root=repo)


def test_frozen_projection_history_allows_governance_only_parent_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _new_repo(tmp_path)
    accepted = _bootstrap_git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("projection\n", encoding="utf-8")
    projection = _commit(repo, "governance projection", "README.md")
    projection_tree = _bootstrap_git(repo, "rev-parse", "HEAD^{tree}")
    monkeypatch.setattr(documentation_state, "FL1_I2_APPROVED_PLANNING_HEAD", accepted)
    monkeypatch.setattr(documentation_state, "FL1_I2_PLANNING_PROJECTION_HEAD", projection)
    monkeypatch.setattr(documentation_state, "FL1_I2_PLANNING_PROJECTION_TREE", projection_tree)

    documentation_state._validate_fl1_i2_projection_history(root=repo)


def test_frozen_projection_history_detects_forbidden_merge_side_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _new_repo(tmp_path)
    accepted = _bootstrap_git(repo, "rev-parse", "HEAD")
    _bootstrap_git(repo, "checkout", "-b", "side")
    (repo / "backend").mkdir()
    (repo / "backend" / "side.py").write_text("bad = True\n", encoding="utf-8")
    _commit(repo, "forbidden side", "backend/side.py")
    (repo / "backend" / "side.py").unlink()
    _bootstrap_git(repo, "add", "-u", "--", "backend/side.py")
    _bootstrap_git(repo, "commit", "-m", "restore side endpoint")
    _bootstrap_git(repo, "checkout", "main")
    _bootstrap_git(repo, "merge", "--no-ff", "side", "-m", "projection merge")
    projection = _bootstrap_git(repo, "rev-parse", "HEAD")
    projection_tree = _bootstrap_git(repo, "rev-parse", "HEAD^{tree}")
    monkeypatch.setattr(documentation_state, "FL1_I2_APPROVED_PLANNING_HEAD", accepted)
    monkeypatch.setattr(documentation_state, "FL1_I2_PLANNING_PROJECTION_HEAD", projection)
    monkeypatch.setattr(documentation_state, "FL1_I2_PLANNING_PROJECTION_TREE", projection_tree)

    with pytest.raises(
        documentation_state.DocumentationStateError,
        match="fl1_i2_governance_projection_path_invalid",
    ):
        documentation_state._validate_fl1_i2_projection_history(root=repo)
