from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.fl1_i2_source_backends import (
    PosixHandleBackend,
    SourceBackendError,
    WindowsHandleBackend,
    current_handle_backend,
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
