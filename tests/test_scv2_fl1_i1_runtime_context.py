from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

import pytest
import scripts.fl1_i1_runtime_context as runtime_module

from scripts.fl1_i1_runtime_context import (
    REQUIRED_PROTECTED_ROOT_ROLES,
    ProtectedRootRegistry,
    RuntimeContextError,
    SourceMode,
    build_trusted_runtime_context,
    derive_repository_python_identity,
    resolve_trusted_git_executable,
)
from tests.fl1_i1_helpers import make_i1_fixture, write_json


def test_trusted_context_derives_actual_git_python_and_safe_public_projection(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    context = fixture.context()

    assert context.actual_git_head
    assert context.python_identity["actual_source"] == "current_process_sys_executable"
    assert context.python_identity["match"] is True
    assert context.roots.to_public_dict()["required_roles"] == list(REQUIRED_PROTECTED_ROOT_ROLES)
    assert context.source_scope.real_source is False
    serialized = json.dumps(context.to_public_dict(), sort_keys=True)
    assert os.fspath(fixture.root) not in serialized
    assert os.fspath(fixture.repo) not in serialized
    assert os.fspath(Path(sys.executable).parent) not in serialized


def test_caller_cannot_supply_actual_git_or_python_identity() -> None:
    parameters = inspect.signature(build_trusted_runtime_context).parameters
    assert "actual_git_head" not in parameters
    assert "python_executable" not in parameters
    assert "owner_authorized" not in parameters


def test_wrong_python_identity_fails_closed(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    with pytest.raises(RuntimeContextError, match="python_identity_mismatch"):
        build_trusted_runtime_context(
            repo_root=fixture.repo,
            expected_python=fixture.root / "not-the-current-python.exe",
            private_root_config=fixture.private_config,
            source_root=fixture.source,
            source_mode=SourceMode.SYNTHETIC_FIXTURE,
            source_scope_id="pytest-temporary-fixture",
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("missing", "protected_root_required_role_missing"),
        ("unknown", "protected_root_unknown_role"),
        ("duplicate", "protected_root_duplicate_or_alias"),
        ("overlap", "protected_root_overlap"),
    ],
)
def test_protected_root_registry_rejects_incomplete_ambiguous_or_overlapping_roles(
    tmp_path: Path, mutation: str, error: str
) -> None:
    fixture = make_i1_fixture(tmp_path)
    payload = json.loads(fixture.private_config.read_text(encoding="utf-8"))
    roots = payload["roots"]
    if mutation == "missing":
        roots.pop("production_source_root")
    elif mutation == "unknown":
        roots["caller_forbidden_root"] = roots["production_source_root"]
    elif mutation == "duplicate":
        roots["production_source_root"] = roots["production_icloud_root"]
    else:
        roots["production_source_root"] = os.fspath(
            fixture.roots["production_icloud_root"] / "nested"
        )
        Path(roots["production_source_root"]).mkdir()
    write_json(fixture.private_config, payload)

    with pytest.raises(RuntimeContextError, match=error):
        ProtectedRootRegistry.load_private_config(fixture.private_config)


def test_source_and_evidence_overlap_with_protected_roles_fails(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    with pytest.raises(RuntimeContextError, match="source_scope_not_temporary_fixture"):
        build_trusted_runtime_context(
            repo_root=fixture.repo,
            expected_python=Path(sys.executable),
            private_root_config=fixture.private_config,
            source_root=fixture.roots["production_source_root"],
            source_mode=SourceMode.AUTHORIZED_READ_ONLY_SOURCE,
            source_scope_id="caller-claims-owner-authority",
        )


def test_authorized_read_only_source_code_path_remains_temporary_fixture_only(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    context = fixture.context(source_mode=SourceMode.AUTHORIZED_READ_ONLY_SOURCE.value)
    assert context.source_scope.mode is SourceMode.AUTHORIZED_READ_ONLY_SOURCE
    assert context.source_scope.authorization_class == "temporary_fixture_only"
    assert context.source_scope.real_source is False


def test_symlink_root_component_is_rejected(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    link = fixture.root / "linked-production-root"
    try:
        link.symlink_to(fixture.roots["production_source_root"], target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    payload = json.loads(fixture.private_config.read_text(encoding="utf-8"))
    payload["roots"]["production_source_root"] = os.fspath(link)
    write_json(fixture.private_config, payload)
    with pytest.raises(RuntimeContextError, match="protected_root_symlink_or_reparse_rejected"):
        ProtectedRootRegistry.load_private_config(fixture.private_config)


def test_dirty_or_head_drift_fails_before_context_is_usable(tmp_path: Path) -> None:
    fixture = make_i1_fixture(tmp_path)
    (fixture.repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(RuntimeContextError, match="evidence_worktree_tracked_drift"):
        fixture.context()


def test_repo_local_fake_git_executable_is_never_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_i1_fixture(tmp_path)
    fake = fixture.repo / ("git.exe" if os.name == "nt" else "git")
    fake.write_bytes(b"not trusted git")
    monkeypatch.setenv("PATH", os.fspath(fixture.repo) + os.pathsep + os.environ["PATH"])
    trusted = resolve_trusted_git_executable(excluded_roots=(fixture.repo,))
    assert trusted.path != fake
    assert fixture.repo not in trusted.path.parents
    with pytest.raises(
        RuntimeContextError, match="evidence_worktree_behavior_affecting_untracked:1"
    ):
        fixture.context()


def test_system_python_cannot_self_approve_expected_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module.sys, "prefix", runtime_module.sys.base_prefix)
    with pytest.raises(RuntimeContextError, match="repository_venv_python_required"):
        derive_repository_python_identity(
            caller_expected_python=Path(runtime_module.sys.executable)
        )


def test_pending_owner_payload_rejects_before_any_configured_root_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_root_text = os.fspath(tmp_path / "never-observe-real-root")
    payload = {
        "schema_version": "violet.scv2-fl1-i1-private-roots.v1",
        "trust_class": "private_runtime_pending_owner",
        "private_derivation_key": "11" * 32,
        "roots": {role: real_root_text for role in REQUIRED_PROTECTED_ROOT_ROLES},
    }
    observed: list[str] = []
    original_lstat = runtime_module.os.lstat

    def recording_lstat(path):
        if real_root_text in os.fspath(path):
            observed.append(os.fspath(path))
        return original_lstat(path)

    monkeypatch.setattr(runtime_module.os, "lstat", recording_lstat)
    with pytest.raises(RuntimeContextError, match="real_source_owner_authority_not_available"):
        ProtectedRootRegistry.from_private_payload(payload)
    assert observed == []


def test_outside_sandbox_source_is_rejected_lexically_without_touching_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_i1_fixture(tmp_path)
    outside = tmp_path / "never-observe-outside-source"
    observed: list[str] = []
    original_lstat = runtime_module.os.lstat

    def recording_lstat(path):
        if os.fspath(path).startswith(os.fspath(outside)):
            observed.append(os.fspath(path))
            raise AssertionError("outside source was observed")
        return original_lstat(path)

    monkeypatch.setattr(runtime_module.os, "lstat", recording_lstat)
    with pytest.raises(RuntimeContextError, match="source_scope_not_temporary_fixture"):
        build_trusted_runtime_context(
            repo_root=fixture.repo,
            expected_python=Path(sys.executable),
            private_root_config=fixture.private_config,
            source_root=outside,
            source_mode=SourceMode.SYNTHETIC_FIXTURE,
            source_scope_id="outside-must-not-be-touched",
        )
    assert observed == []
