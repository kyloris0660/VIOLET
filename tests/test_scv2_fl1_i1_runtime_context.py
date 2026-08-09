from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

import pytest

from scripts.fl1_i1_runtime_context import (
    REQUIRED_PROTECTED_ROOT_ROLES,
    ProtectedRootRegistry,
    RuntimeContextError,
    SourceMode,
    build_trusted_runtime_context,
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
