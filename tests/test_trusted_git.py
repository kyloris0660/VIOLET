from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
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
    verify_approved_python_runtime,
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


def test_slow_ignored_inventory_keeps_bounded_budget_and_rejects_behavior(tmp_path, monkeypatch):
    from scripts import trusted_git
    repo = _new_repo(tmp_path)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    _commit(repo, "ignore retained evidence", ".gitignore")
    ignored = repo / "ignored"
    ignored.mkdir()
    (ignored / "importable.py").write_text("synthetic\n", encoding="utf-8")
    actual = trusted_git.run_trusted_git_bytes
    observed = []

    def simulated_slow_inventory(root, arguments, **kwargs):
        observed.append((tuple(arguments), kwargs.get("timeout", 15)))
        if "--ignored" in arguments and kwargs.get("timeout", 15) < 20:
            raise TrustedGitError("trusted_git_invocation_failed:TimeoutExpired")
        return actual(root, arguments, **kwargs)

    monkeypatch.setattr(trusted_git, "run_trusted_git_bytes", simulated_slow_inventory)
    git = resolve_trusted_git_executable(excluded_roots=(repo,))
    with pytest.raises(TrustedGitError, match="behavior_affecting_ignored:1"):
        assert_trusted_worktree_clean(git, repo)
    assert [timeout for args, timeout in observed if "--ignored" in args] == [60]
    assert all(timeout == 15 for args, timeout in observed if "--ignored" not in args)


def test_ignored_inventory_timeout_still_blocks_candidate(tmp_path, monkeypatch):
    from scripts import trusted_git
    repo = _new_repo(tmp_path)
    candidate = _bootstrap_git(repo, "rev-parse", "HEAD")
    actual = trusted_git.run_trusted_git_bytes
    observed = []

    def expired_inventory(root, arguments, **kwargs):
        if "--ignored" in arguments:
            observed.append(kwargs.get("timeout"))
            raise TrustedGitError("trusted_git_invocation_failed:TimeoutExpired")
        return actual(root, arguments, **kwargs)

    monkeypatch.setattr(trusted_git, "run_trusted_git_bytes", expired_inventory)
    assert trusted_git.candidate_behavior_carry_forward(repo, candidate) is False
    assert observed == [60]


def test_exact_verified_repo_venv_is_excluded_before_ignored_budget(
    tmp_path: Path,
) -> None:
    repo = _new_repo(tmp_path)
    (repo / ".gitignore").write_text("venv/\nextra-venv/\n", encoding="utf-8")
    _commit(repo, "ignore synthetic venvs", ".gitignore")
    subprocess.run(
        [sys.executable, "-I", "-s", "-m", "venv", "--without-pip", repo / "venv"],
        check=True,
        timeout=60,
    )
    for index in range(MAX_STATUS_ENTRIES := 4200):
        (repo / "venv" / f"synthetic-{index}.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
    runtime = verify_approved_python_runtime(
        repo / "venv" / "Scripts" / "python.exe",
        repo_root=repo,
    )
    git = resolve_trusted_git_executable(excluded_roots=(repo,))
    summary = assert_trusted_worktree_clean(
        git,
        repo,
        approved_python_runtime=runtime,
    )
    assert summary.behavior_ignored_count == 0
    assert MAX_STATUS_ENTRIES > 4096

    extra = repo / "extra-venv"
    extra.mkdir()
    (extra / "redirect.pth").write_text("synthetic\n", encoding="utf-8")
    with pytest.raises(TrustedGitError, match="behavior_affecting_ignored"):
        assert_trusted_worktree_clean(
            git,
            repo,
            approved_python_runtime=runtime,
        )


def test_verified_venv_control_file_drift_fails_closed(tmp_path: Path) -> None:
    repo = _new_repo(tmp_path)
    (repo / ".gitignore").write_text("venv/\n", encoding="utf-8")
    _commit(repo, "ignore synthetic venv", ".gitignore")
    subprocess.run(
        [sys.executable, "-I", "-s", "-m", "venv", "--without-pip", repo / "venv"],
        check=True,
        timeout=60,
    )
    runtime = verify_approved_python_runtime(
        repo / "venv" / "Scripts" / "python.exe",
        repo_root=repo,
    )
    site_packages = repo / "venv" / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    (site_packages / "hostile.pth").write_text("synthetic\n", encoding="utf-8")
    git = resolve_trusted_git_executable(excluded_roots=(repo,))
    with pytest.raises(TrustedGitError, match="identity_drift"):
        assert_trusted_worktree_clean(
            git,
            repo,
            approved_python_runtime=runtime,
        )


def test_trusted_git_candidate_and_parent_aliases_are_rejected(tmp_path: Path) -> None:
    real = resolve_trusted_git_executable(excluded_roots=(tmp_path,)).path
    install = tmp_path / "git-install"
    command = install / "cmd"
    command.mkdir(parents=True)
    try:
        (command / "git.exe").symlink_to(real)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(TrustedGitError, match="trusted_git_executable_unavailable"):
        resolve_trusted_git_executable(
            platform_name="nt",
            windows_location_provider=lambda: ((install,), ()),
        )

    (command / "git.exe").unlink()
    command.rmdir()
    real_parent = tmp_path / "real-command"
    real_parent.mkdir()
    (real_parent / "git.exe").write_bytes(b"synthetic")
    try:
        command.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation unavailable")
    with pytest.raises(TrustedGitError, match="trusted_git_executable_unavailable"):
        resolve_trusted_git_executable(
            platform_name="nt",
            windows_location_provider=lambda: ((install,), ()),
        )


def test_trusted_git_replacement_between_binding_and_invocation_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import trusted_git as trusted_git_module

    repo = _new_repo(tmp_path)
    git = resolve_trusted_git_executable(excluded_roots=(repo,))
    original = trusted_git_module._bind_executable
    calls = 0

    def drift(path: Path) -> tuple[str, str]:
        nonlocal calls
        calls += 1
        digest, identity = original(path)
        if calls == 2:
            return digest, "0" * 64
        return digest, identity

    monkeypatch.setattr(trusted_git_module, "_bind_executable", drift)
    with pytest.raises(TrustedGitError, match="identity_drift"):
        run_trusted_git_text(repo, ("status", "--porcelain=v2"), git=git)


@pytest.mark.parametrize("target_kind", ["parent", "candidate"])
def test_trusted_git_reparse_attribute_is_rejected_before_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    from scripts import trusted_git as trusted_git_module

    parent = tmp_path / "candidate"
    parent.mkdir()
    candidate = parent / "git.exe"
    candidate.write_bytes(b"synthetic")
    target = parent if target_kind == "parent" else candidate
    original = os.lstat

    def lstat(path: object) -> object:
        observed = original(path)
        if os.path.normcase(os.path.abspath(os.fspath(path))) == os.path.normcase(
            os.path.abspath(os.fspath(target))
        ):
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=0x400,
            )
        return observed

    monkeypatch.setattr(trusted_git_module.os, "lstat", lstat)
    with pytest.raises(TrustedGitError, match="alias_rejected"):
        trusted_git_module._assert_no_alias_components(candidate)


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



def _launcher_metadata_fixture(tmp_path, monkeypatch):
    import json
    from scripts import trusted_git as boundary
    from scripts import violet_production_control as real_control
    import scripts
    repo = _new_repo(tmp_path)
    (repo / 'scripts').mkdir()
    controller = repo / 'scripts/violet_production_control.py'
    controller.write_text('# Source-bound controller fixture\n', encoding='utf-8')
    (repo / 'run.py').write_text('# Source-bound command fixture\n', encoding='utf-8')
    (repo / '.gitignore').write_text('.local_manifests/\n', encoding='utf-8')
    candidate = _commit(repo, 'launcher fixture', 'scripts/violet_production_control.py', 'run.py', '.gitignore')
    directory = repo / '.local_manifests/production_launcher'
    directory.mkdir(parents=True)
    profile = directory / 'production-profile.json'
    profile.write_text(json.dumps({'profile_id': 'production-default', 'candidate_head': candidate}), encoding='utf-8')
    config = SimpleNamespace(config_source='production_profile', profile_exists=True, profile_errors=[], repo_root=repo,
                             expected_python=Path(sys.executable), env={'VIOLET_ENV': 'production'}, port=8123, url='http://127.0.0.1:8123')
    command = [sys.executable, str(repo / 'run.py')]
    verified = {'value': True, 'calls': 0}
    def verify(state, observed):
        verified['calls'] += 1
        assert observed is config
        return verified['value'], ([] if verified['value'] else ['pid_identity_mismatch'])
    fake = SimpleNamespace(__file__=str(controller), resolve_config=lambda **kwargs: config,
                           START_LOCK_TTL_SECONDS=real_control.START_LOCK_TTL_SECONDS,
                           production_command=lambda observed: command, verify_managed_process=verify)
    monkeypatch.setattr(scripts, 'violet_production_control', fake)
    monkeypatch.setitem(sys.modules, 'scripts.violet_production_control', fake)
    monkeypatch.setattr(sys, 'argv', [str(controller), 'start', '--profile', 'production-default', '--json'])
    spec = {'private_file': 'production_launcher/production-profile.json',
            'previous_full_sha256': __import__('hashlib').sha256(profile.read_bytes()).hexdigest()}
    return SimpleNamespace(repo=repo, directory=directory, candidate=candidate, boundary=boundary,
                           real_control=real_control, config=config, command=command, verified=verified,
                           registry={'candidate_profile': spec}, spec=spec, controller=controller)


def test_candidate_drift_accepts_real_own_start_lock_and_keeps_unknown_ignored_files_closed(tmp_path, monkeypatch):
    fixture = _launcher_metadata_fixture(tmp_path, monkeypatch)
    lock = fixture.directory / 'violet-production-launcher-start.lock'
    status = fixture.real_control._acquire_start_lock(lock)
    assert status.acquired
    try:
        git = resolve_trusted_git_executable(repo_root=fixture.repo)
        summary = inspect_worktree_drift(git, fixture.repo, approved_artifacts=fixture.registry, approved_candidate=fixture.candidate)
        assert summary.ordinary_ignored_count == 2
        assert summary.behavior_ignored_count == summary.uncertain_ignored_count == 0
        (fixture.directory / 'hook.py').write_text('raise RuntimeError("must never execute")\n', encoding='utf-8')
        (fixture.directory / 'unknown.json').write_text('{}', encoding='utf-8')
        (fixture.directory / '.violet-production-launcher-state.json.123.456.tmp').write_text('{}', encoding='utf-8')
        summary = inspect_worktree_drift(git, fixture.repo, approved_artifacts=fixture.registry, approved_candidate=fixture.candidate)
        assert summary.behavior_ignored_count == 1 and summary.uncertain_ignored_count == 2
    finally:
        fixture.real_control._release_start_lock(lock)


@pytest.mark.parametrize('counterexample', ['other_pid', 'readonly_caller', 'stale', 'future', 'duplicate_key', 'unapproved_profile'])
def test_candidate_launcher_lock_cannot_authorize_other_process_or_untrusted_metadata(tmp_path, monkeypatch, counterexample):
    import json
    from datetime import datetime, timedelta, timezone
    fixture = _launcher_metadata_fixture(tmp_path, monkeypatch)
    lock = fixture.directory / 'violet-production-launcher-start.lock'
    assert fixture.real_control._acquire_start_lock(lock).acquired
    try:
        payload = json.loads(lock.read_bytes())
        if counterexample == 'other_pid': payload['pid'] += 1
        if counterexample == 'readonly_caller': monkeypatch.setattr(sys, 'argv', [str(fixture.controller), 'preflight'])
        if counterexample == 'stale': payload['created_at'] = (datetime.now(timezone.utc) - timedelta(seconds=fixture.real_control.START_LOCK_TTL_SECONDS+10)).isoformat()
        if counterexample == 'future': payload['created_at'] = (datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
        if counterexample == 'unapproved_profile': (fixture.directory / 'production-profile.json').write_text(json.dumps({'profile_id':'production-default','candidate_head':'0'*40}), encoding='utf-8')
        lock.write_text(json.dumps(payload), encoding='utf-8')
        if counterexample == 'duplicate_key': lock.write_text(json.dumps(payload)[:-1]+',"pid":'+str(os.getpid())+'}', encoding='utf-8')
        assert not fixture.boundary._verified_launcher_runtime_file(fixture.repo, str(lock.relative_to(fixture.repo)).replace('\\','/'), fixture.spec, fixture.candidate)
    finally:
        fixture.real_control._release_start_lock(lock)


@pytest.mark.parametrize('counterexample', [None, 'foreign_command', 'dead_or_reused_pid', 'unsafe_mode', 'extra_key', 'invalid_creation_time'])
def test_candidate_launcher_state_requires_exact_command_and_verified_live_identity(tmp_path, monkeypatch, counterexample):
    import json, time
    from datetime import datetime, timezone
    fixture = _launcher_metadata_fixture(tmp_path, monkeypatch)
    state = fixture.directory / 'violet-production-launcher-state.json'
    payload = {'state_version':fixture.real_control.STATE_VERSION, 'app_name':fixture.real_control.APP_NAME,
               'started_by':'violet_production_launcher', 'pid':os.getpid(), 'pid_create_time':time.time(),
               'start_time':datetime.now(timezone.utc).isoformat(), 'command':fixture.command,
               'repo_root':str(fixture.repo), 'port':8123, 'url':fixture.config.url, 'env':'production', 'debug':False, 'startup_safe_mode':True}
    if counterexample == 'foreign_command': payload['command'] = [sys.executable, str(fixture.repo/'foreign.py')]
    if counterexample == 'dead_or_reused_pid': fixture.verified['value'] = False
    if counterexample == 'unsafe_mode': payload['startup_safe_mode'] = False
    if counterexample == 'extra_key': payload['extra'] = 'unexpected control'
    if counterexample == 'invalid_creation_time': payload['pid_create_time'] = None
    fixture.real_control._write_state(payload, state)
    observed = fixture.boundary._verified_launcher_runtime_file(fixture.repo, str(state.relative_to(fixture.repo)).replace('\\','/'), fixture.spec, fixture.candidate)
    assert observed is (counterexample is None)
    if counterexample in {None, 'dead_or_reused_pid'}: assert fixture.verified['calls'] == 1


def test_candidate_launcher_runtime_metadata_cannot_follow_a_link(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    fixture = _launcher_metadata_fixture(tmp_path, monkeypatch)
    outside = tmp_path / 'outside-lock.json'
    outside.write_text(json.dumps({'pid':os.getpid(),'created_at':datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
    lock = fixture.directory / 'violet-production-launcher-start.lock'
    try: lock.symlink_to(outside)
    except (OSError, NotImplementedError): pytest.skip('Symlink creation unavailable on this host')
    assert not fixture.boundary._verified_launcher_runtime_file(fixture.repo, str(lock.relative_to(fixture.repo)).replace('\\','/'), fixture.spec, fixture.candidate)


def test_candidate_carry_rejects_committed_behavior_hidden_by_path_git(tmp_path, monkeypatch):
    from scripts.trusted_git import candidate_behavior_carry_forward
    repo = _new_repo(tmp_path)
    (repo / 'backend').mkdir()
    source = repo / 'backend/hook.py'
    source.write_text('value = 1\n', encoding='utf-8')
    candidate = _commit(repo, 'original backend', 'backend/hook.py')
    source.write_text('value = 2\n', encoding='utf-8')
    _commit(repo, 'changed behavior', 'backend/hook.py')
    assert _bootstrap_git(repo, 'diff', '--name-only', candidate) == 'backend/hook.py'
    drift = inspect_worktree_drift(resolve_trusted_git_executable(repo_root=repo), repo)
    assert not any((drift.behavior_untracked_count, drift.uncertain_untracked_count,
                    drift.behavior_ignored_count, drift.uncertain_ignored_count))
    real = subprocess.check_output
    def path_wrapper(argv, *args, **kwargs):
        if argv[0] == 'git' and 'diff' in argv:
            return '' if kwargs.get('text') else b''
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'check_output', path_wrapper)
    assert candidate_behavior_carry_forward(repo, candidate) is False


def test_candidate_carry_preserves_docs_without_path_or_inherited_git_controls(tmp_path, monkeypatch):
    from scripts.trusted_git import candidate_behavior_carry_forward
    repo = _new_repo(tmp_path)
    candidate = _bootstrap_git(repo, 'rev-parse', 'HEAD')
    (repo / 'docs').mkdir()
    (repo / 'docs/note.md').write_text('documentation\n', encoding='utf-8')
    _commit(repo, 'docs only', 'docs/note.md')
    (repo / 'docs/evidence.txt').write_text('ordinary artifact\n', encoding='utf-8')
    monkeypatch.setenv('GIT_DIR', str(tmp_path / 'foreign.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(tmp_path / 'foreign'))
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'alias.diff')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', '!echo hidden')
    real = subprocess.run
    calls = []
    def forbid_path(argv, *args, **kwargs):
        assert argv[0] != 'git', 'candidate Git consulted PATH'
        calls.append((argv, kwargs.get('env', {})))
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', forbid_path)
    assert candidate_behavior_carry_forward(repo, candidate) is True
    assert calls and len({str(argv[0]).casefold() for argv, _ in calls}) == 1
    assert all('GIT_DIR' not in env and 'GIT_WORK_TREE' not in env
               and 'GIT_CONFIG_COUNT' not in env for _, env in calls)


def test_candidate_carry_rejects_referenced_artifact_despite_path_grep_wrapper(tmp_path, monkeypatch):
    from scripts.trusted_git import candidate_behavior_carry_forward
    repo = _new_repo(tmp_path)
    (repo / 'backend').mkdir()
    (repo / 'backend/hook.py').write_text('input_path = "docs/evidence.txt"\n', encoding='utf-8')
    candidate = _commit(repo, 'referenced input', 'backend/hook.py')
    (repo / 'docs').mkdir()
    (repo / 'docs/evidence.txt').write_text('loaded input\n', encoding='utf-8')
    real = subprocess.run
    def path_wrapper(argv, *args, **kwargs):
        if argv[0] == 'git' and 'grep' in argv:
            return subprocess.CompletedProcess(argv, 1, stdout=b'', stderr=b'')
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', path_wrapper)
    assert candidate_behavior_carry_forward(repo, candidate) is False
