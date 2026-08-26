"""Canonical local temporary-directory confinement for synthetic FL1-I2 work."""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable


class ConfinementError(RuntimeError):
    pass


@dataclass(frozen=True)
class BoundDirectory:
    path: Path
    identity_fingerprint: str


@dataclass(frozen=True)
class SyntheticRoots:
    task_root: BoundDirectory
    source_root: BoundDirectory
    evidence_root: BoundDirectory


@dataclass(frozen=True)
class LexicalSyntheticRoots:
    task_root: Path
    source_root: Path
    evidence_root: Path


def _identity(metadata: os.stat_result) -> str:
    return hashlib.sha256(
        repr(
            (
                int(metadata.st_dev),
                int(metadata.st_ino),
                int(metadata.st_mode),
            )
        ).encode("ascii")
    ).hexdigest()


def _within(candidate: Path, root: Path) -> bool:
    try:
        return os.path.commonpath(
            (os.path.normcase(os.fspath(candidate)), os.path.normcase(os.fspath(root)))
        ) == os.path.normcase(os.fspath(root))
    except ValueError:
        return False


def _lexical_absolute_local_path(path: Path) -> Path:
    """Validate an absolute local path without observing the target path."""

    raw = os.fspath(path)
    if (
        not raw
        or "\x00" in raw
        or any(ord(value) < 32 for value in raw)
        or raw.startswith(("\\\\", "//"))
    ):
        raise ConfinementError("synthetic_path_lexical_invalid")
    if os.name == "nt":
        windows = PureWindowsPath(raw)
        if not windows.is_absolute() or windows.drive.startswith(("\\\\", "//")):
            raise ConfinementError("synthetic_path_lexical_invalid")
        parts = windows.parts[1:]
    else:
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise ConfinementError("synthetic_path_lexical_invalid")
        parts = candidate.parts[1:]
    if any(value in {".", ".."} for value in parts):
        raise ConfinementError("synthetic_path_lexical_invalid")
    return Path(os.path.abspath(raw))


def lexically_confine_evidence_root(
    evidence_root: Path,
    *,
    temp_provider: Callable[[], Path] | None = None,
) -> Path:
    """Prove an evidence root is below the trusted local temp root, without statting it."""

    local_temp = os_local_temp_root(provider=temp_provider)
    lexical = _lexical_absolute_local_path(evidence_root)
    if lexical == local_temp.path or not _within(lexical, local_temp.path):
        raise ConfinementError("synthetic_evidence_root_lexical_invalid")
    return lexical


def lexically_confine_config_path(
    config_path: Path,
    *,
    expected_basename: str = "private-run-config.json",
    temp_provider: Callable[[], Path] | None = None,
) -> Path:
    """Validate the CLI config location before any caller path is opened."""

    lexical = _lexical_absolute_local_path(config_path)
    if lexical.name != expected_basename:
        raise ConfinementError("synthetic_config_lexical_invalid")
    evidence = lexically_confine_evidence_root(
        lexical.parent, temp_provider=temp_provider
    )
    if lexical.parent != evidence:
        raise ConfinementError("synthetic_config_lexical_invalid")
    return lexical


def lexically_confine_synthetic_roots(
    source_root: Path,
    evidence_root: Path,
    *,
    temp_provider: Callable[[], Path] | None = None,
) -> LexicalSyntheticRoots:
    """Check confinement and overlap before touching either caller root."""

    local_temp = os_local_temp_root(provider=temp_provider)
    source = _lexical_absolute_local_path(source_root)
    evidence = _lexical_absolute_local_path(evidence_root)
    if (
        source == local_temp.path
        or evidence == local_temp.path
        or not _within(source, local_temp.path)
        or not _within(evidence, local_temp.path)
    ):
        raise ConfinementError("synthetic_roots_lexical_invalid")
    try:
        task = Path(os.path.commonpath((source, evidence)))
    except ValueError as exc:
        raise ConfinementError("synthetic_roots_overlap_or_escape") from exc
    if task == local_temp.path or not _within(task, local_temp.path):
        raise ConfinementError("synthetic_task_root_invalid")
    if source == evidence or _within(source, evidence) or _within(evidence, source):
        raise ConfinementError("synthetic_roots_overlap_or_escape")
    return LexicalSyntheticRoots(task, source, evidence)


def bind_directory(path: Path) -> BoundDirectory:
    """Bind an absolute directory after checking every lexical component."""

    candidate = Path(path)
    if not candidate.is_absolute():
        raise ConfinementError("local_temp_root_invalid")
    lexical = Path(os.path.abspath(os.fspath(candidate)))
    current = Path(lexical.anchor)
    try:
        metadata = os.lstat(current)
        for component in lexical.parts[1:]:
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                attributes
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise ConfinementError("local_temp_alias_rejected")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ConfinementError("local_temp_root_invalid")
            current /= component
            metadata = os.lstat(current)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or (
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise ConfinementError("local_temp_alias_rejected")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ConfinementError("local_temp_root_invalid")
        resolved = lexical.resolve(strict=True)
    except ConfinementError:
        raise
    except OSError as exc:
        raise ConfinementError("local_temp_root_invalid") from exc
    if os.path.normcase(os.fspath(resolved)) != os.path.normcase(os.fspath(lexical)):
        raise ConfinementError("local_temp_alias_rejected")
    return BoundDirectory(resolved, _identity(metadata))


def _windows_local_appdata() -> Path:
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    # FOLDERID_LocalAppData = F1B32785-6FBA-4FCF-9D55-7B8E7F157091
    folder = GUID(
        0xF1B32785,
        0x6FBA,
        0x4FCF,
        (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
    )
    pointer = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    result = shell32.SHGetKnownFolderPath(
        ctypes.byref(folder), 0x00004000, None, ctypes.byref(pointer)
    )
    if result != 0 or not pointer.value:
        raise ConfinementError("os_local_temp_provider_unavailable")
    try:
        return Path(pointer.value) / "Temp"
    finally:
        ole32.CoTaskMemFree(pointer)


def os_local_temp_root(
    *, provider: Callable[[], Path] | None = None
) -> BoundDirectory:
    if provider is not None:
        candidate = provider()
    elif os.name == "nt":
        candidate = _windows_local_appdata()
    elif sys.platform == "darwin":
        candidate = Path("/private/tmp")
    else:
        candidate = Path("/tmp")
    bound = bind_directory(candidate)
    if os.name == "nt":
        text = os.fspath(bound.path)
        if text.startswith(("\\\\", "//")):
            raise ConfinementError("os_local_temp_network_rejected")
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(bound.path.anchor)
        if drive_type in {0, 1, 4}:
            raise ConfinementError("os_local_temp_network_rejected")
    return bound


def verify_synthetic_roots(
    source_root: Path,
    evidence_root: Path,
    *,
    temp_provider: Callable[[], Path] | None = None,
) -> SyntheticRoots:
    lexical = lexically_confine_synthetic_roots(
        source_root, evidence_root, temp_provider=temp_provider
    )
    source = bind_directory(lexical.source_root)
    evidence = bind_directory(lexical.evidence_root)
    task = bind_directory(lexical.task_root)
    if source.path == evidence.path or _within(source.path, evidence.path) or _within(
        evidence.path, source.path
    ):
        raise ConfinementError("synthetic_roots_overlap_or_escape")
    if source.identity_fingerprint == evidence.identity_fingerprint:
        raise ConfinementError("synthetic_roots_alias")
    if not _within(source.path, task.path) or not _within(evidence.path, task.path):
        raise ConfinementError("synthetic_roots_overlap_or_escape")
    return SyntheticRoots(task, source, evidence)


def create_owned_validation_temp_root(
    *,
    temp_provider: Callable[[], Path] | None = None,
) -> BoundDirectory:
    base = os_local_temp_root(provider=temp_provider)
    candidate = base.path / f"violet-fl1-i2-validation-{uuid.uuid4().hex}"
    try:
        os.mkdir(candidate, 0o700)
    except OSError as exc:
        raise ConfinementError("validation_temp_create_failed") from exc
    return bind_directory(candidate)


def remove_owned_validation_temp_root(bound: BoundDirectory) -> None:
    current = bind_directory(bound.path)
    if current != bound:
        raise ConfinementError("validation_temp_identity_drift")

    def remove_contents(directory: Path) -> None:
        try:
            entries = tuple(os.scandir(directory))
        except OSError as exc:
            raise ConfinementError("validation_temp_cleanup_failed") from exc
        for entry in entries:
            try:
                child = directory / entry.name
                metadata = os.lstat(child)
                attributes = getattr(metadata, "st_file_attributes", 0)
                is_alias = stat.S_ISLNK(metadata.st_mode) or bool(
                    attributes
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
                if is_alias:
                    if os.name == "nt" and stat.S_ISDIR(metadata.st_mode):
                        os.rmdir(child)
                    else:
                        os.unlink(child)
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    remove_contents(child)
                    os.rmdir(child)
                elif stat.S_ISREG(metadata.st_mode):
                    try:
                        os.unlink(child)
                    except PermissionError:
                        if os.name != "nt":
                            raise
                        current = os.lstat(child)
                        current_attributes = getattr(
                            current, "st_file_attributes", 0
                        )
                        if (
                            not stat.S_ISREG(current.st_mode)
                            or stat.S_ISLNK(current.st_mode)
                            or current_attributes
                            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                            or (current.st_dev, current.st_ino)
                            != (metadata.st_dev, metadata.st_ino)
                        ):
                            raise ConfinementError(
                                "validation_temp_cleanup_identity_drift"
                            )
                        os.chmod(child, current.st_mode | stat.S_IWRITE)
                        writable = os.lstat(child)
                        if (
                            not stat.S_ISREG(writable.st_mode)
                            or stat.S_ISLNK(writable.st_mode)
                            or getattr(writable, "st_file_attributes", 0)
                            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                            or (writable.st_dev, writable.st_ino)
                            != (metadata.st_dev, metadata.st_ino)
                        ):
                            raise ConfinementError(
                                "validation_temp_cleanup_identity_drift"
                            )
                        os.unlink(child)
                else:
                    raise ConfinementError("validation_temp_cleanup_type_rejected")
            except ConfinementError:
                raise
            except OSError as exc:
                raise ConfinementError("validation_temp_cleanup_failed") from exc
    try:
        remove_contents(bound.path)
        if bind_directory(bound.path) != bound:
            raise ConfinementError("validation_temp_identity_drift")
        os.rmdir(bound.path)
    except ConfinementError:
        raise
    except OSError as exc:
        raise ConfinementError("validation_temp_cleanup_failed") from exc
