from __future__ import annotations

import os
import inspect
from pathlib import Path

import pytest

from scripts.fl1_i2_source_backends import (
    EnumerationBudget,
    PosixHandleBackend,
    SourceBackendError,
    WindowsHandleBackend,
    current_handle_backend,
    _validated_next_entry_offset,
)


def test_current_backend_same_handle_identity_and_change_detection(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-source"
    root.mkdir()
    source = root / "member.jpg"
    source.write_bytes(b"synthetic")
    backend = current_handle_backend()
    with backend.open_directory(root) as directory:
        members = backend.enumerate_directory(directory)
        member = next(item for item in members if item.name == source.name)
        with backend.open_child(directory, member) as child:
            assert child.observation.object_identity == member.object_identity
            assert child.observation.change_identity == member.change_identity
            before = backend.observe(child)
            source.write_bytes(b"synthetic-changed")
            after = backend.observe(child)
            assert before.object_identity == after.object_identity
            assert before.change_identity != after.change_identity


def test_current_backend_rejects_hard_link(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-links"
    root.mkdir()
    source = root / "one.png"
    source.write_bytes(b"synthetic")
    os.link(source, root / "two.png")
    backend = current_handle_backend()
    with backend.open_directory(root) as directory:
        with pytest.raises(SourceBackendError, match="source_alias_identity_rejected"):
            backend.enumerate_directory(directory)


def test_recursive_backend_rejects_nested_hard_link(tmp_path: Path) -> None:
    root = tmp_path / "nested-links"
    nested = root / "nested"
    nested.mkdir(parents=True)
    source = nested / "one.png"
    source.write_bytes(b"synthetic")
    os.link(source, nested / "two.png")
    backend = current_handle_backend()
    with backend.open_directory(root) as directory:
        nested_member = next(member for member in backend.enumerate_directory(directory) if member.name == "nested")
        with backend.open_discovered_directory(directory, nested_member) as child:
            with pytest.raises(SourceBackendError, match="source_alias_identity_rejected"):
                backend.enumerate_directory(child)


def test_literal_backslash_member_name_is_never_treated_as_separator() -> None:
    from scripts.fl1_i2_source_backends import _validate_member_name

    with pytest.raises(SourceBackendError, match="source_member_name_invalid"):
        _validate_member_name(r"alias\\escape.jpg")


def test_non_current_backend_fails_closed() -> None:
    if os.name == "nt":
        with pytest.raises(SourceBackendError, match="posix_handle_capability_unsupported"):
            PosixHandleBackend().open_directory(Path("synthetic"))
    else:
        with pytest.raises(SourceBackendError, match="windows_handle_capability_unsupported"):
            WindowsHandleBackend()


def test_enumeration_entry_budget_fails_before_unbounded_growth(tmp_path: Path) -> None:
    root = tmp_path / "budget"
    root.mkdir()
    for index in range(3):
        (root / f"{index}.jpg").write_bytes(b"synthetic")
    backend = current_handle_backend()
    with backend.open_directory(root) as directory:
        with pytest.raises(SourceBackendError, match="entry_budget"):
            backend.enumerate_directory(
                directory,
                budget=EnumerationBudget(max_entries=2, max_pages=100, max_metadata_bytes=10000),
            )


def test_directory_member_opens_relative_to_verified_parent(tmp_path: Path) -> None:
    root = tmp_path / "nested"
    root.mkdir()
    nested = root / "child"
    nested.mkdir()
    backend = current_handle_backend()
    with backend.open_directory(root) as directory:
        member = next(item for item in backend.enumerate_directory(directory) if item.name == "child")
        assert member.member_type == "directory"
        with backend.open_discovered_directory(directory, member) as child:
            assert child.observation.is_directory
            assert child.observation.object_identity == member.object_identity


def test_windows_directory_record_offsets_require_alignment_progress_and_bounds() -> None:
    assert _validated_next_entry_offset(current_offset=0, next_entry_offset=96, file_name_length=2, buffer_size=4096) == 96
    assert _validated_next_entry_offset(current_offset=0, next_entry_offset=0, file_name_length=2, buffer_size=4096) is None
    for offset in (95, 88, 4096):
        with pytest.raises(SourceBackendError, match="out_of_bounds"):
            _validated_next_entry_offset(current_offset=0, next_entry_offset=offset, file_name_length=2, buffer_size=4096)


def test_windows_production_root_has_no_path_based_createfile_fallback() -> None:
    source = inspect.getsource(WindowsHandleBackend.open_directory)
    assert "_nt_open" in source
    assert "CreateFileW" not in inspect.getsource(WindowsHandleBackend)


@pytest.mark.skipif(os.name == "nt", reason="POSIX live temporary test")
def test_posix_symlink_is_rejected_during_same_handle_enumeration(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target"
    target.write_bytes(b"synthetic")
    (root / "alias").symlink_to(target)
    backend = PosixHandleBackend()
    with backend.open_directory(root) as directory:
        with pytest.raises(SourceBackendError, match="source_reparse_point_rejected"):
            backend.enumerate_directory(directory)


@pytest.mark.skipif(os.name == "nt", reason="POSIX component-walk test")
def test_posix_root_intermediate_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(SourceBackendError, match="source_directory_open_failed"):
        PosixHandleBackend().open_directory(alias)
