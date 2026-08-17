"""Shared fail-closed Git boundary for V.I.O.L.E.T. evidence tooling.

The module deliberately does not consult ``PATH`` or caller-provided Git and
Program Files environment variables.  Every repository command is bound to an
explicit repository root and work tree, runs without caller Git controls, and
first proves that Git observes the same work tree the caller named.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterable, Mapping, Sequence


WindowsGitLocationProvider = Callable[
    [], tuple[tuple[PurePath, ...], tuple[PurePath, ...]]
]
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_BEHAVIOR_SUFFIXES = frozenset(
    {
        ".bat",
        ".bash",
        ".cjs",
        ".cmd",
        ".com",
        ".dll",
        ".dylib",
        ".exe",
        ".fish",
        ".jar",
        ".js",
        ".jsx",
        ".mjs",
        ".node",
        ".ps1",
        ".py",
        ".pyc",
        ".pyd",
        ".pyi",
        ".pyw",
        ".sh",
        ".so",
        ".ts",
        ".tsx",
        ".wasm",
        ".zsh",
    }
)
_BEHAVIOR_NAMES = frozenset(
    {
        ".env",
        ".gitattributes",
        ".gitmodules",
        "bun.lock",
        "bun.lockb",
        "cargo.toml",
        "compose.yaml",
        "compose.yml",
        "deno.json",
        "deno.jsonc",
        "docker-compose.yaml",
        "docker-compose.yml",
        "dockerfile",
        "gemfile",
        "justfile",
        "makefile",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pyproject.toml",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
        "tox.ini",
        "uv.lock",
        "yarn.lock",
    }
)
_ORDINARY_ARTIFACT_SUFFIXES = frozenset(
    {
        ".7z",
        ".avif",
        ".bmp",
        ".csv",
        ".doc",
        ".docx",
        ".gif",
        ".heic",
        ".jpeg",
        ".jpg",
        ".log",
        ".md",
        ".mov",
        ".mp3",
        ".mp4",
        ".ods",
        ".odt",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".rar",
        ".rtf",
        ".svg",
        ".tar",
        ".tif",
        ".tiff",
        ".tsv",
        ".txt",
        ".wav",
        ".webm",
        ".webp",
        ".xls",
        ".xlsx",
        ".zip",
    }
)


class TrustedGitError(RuntimeError):
    """Raised when Git identity, output, or worktree evidence is untrusted."""


@dataclass(frozen=True)
class TrustedGitExecutable:
    path: Path
    fingerprint: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "identity_source": "os_backed_bounded_system_location",
            "executable_fingerprint": self.fingerprint,
            "absolute_path_emitted": False,
            "repo_local": False,
            "symlink_or_reparse": False,
        }


@dataclass(frozen=True)
class WorktreeEntry:
    record_type: str
    path: str
    original_path: str | None = None


@dataclass(frozen=True)
class WorktreeDriftSummary:
    tracked_count: int
    ordinary_untracked_count: int
    behavior_untracked_count: int
    uncertain_untracked_count: int


def trusted_git_environment(
    inherited: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Scrub every caller Git control key, case-insensitively."""

    source = os.environ if inherited is None else inherited
    scrubbed = {
        key: value
        for key, value in source.items()
        if not key.casefold().startswith("git_")
    }
    scrubbed.update(
        {
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return scrubbed


def _casefold_deduplicated_paths(paths: Iterable[PurePath]) -> tuple[PurePath, ...]:
    unique: list[PurePath] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)


def _read_windows_registry_path(
    registry: Any,
    *,
    key_path: str,
    value_name: str,
    view: int,
) -> PureWindowsPath | None:
    try:
        handle = registry.OpenKey(
            registry.HKEY_LOCAL_MACHINE,
            key_path,
            0,
            registry.KEY_READ | view,
        )
    except (OSError, TypeError, ValueError):
        return None
    try:
        value, _value_type = registry.QueryValueEx(handle, value_name)
    except (OSError, TypeError, ValueError):
        return None
    finally:
        try:
            registry.CloseKey(handle)
        except OSError:
            pass
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    candidate = PureWindowsPath(normalized)
    if not normalized or not candidate.is_absolute():
        return None
    return candidate


def windows_system_git_roots(
    *, registry: Any | None = None
) -> tuple[tuple[PurePath, ...], tuple[PurePath, ...]]:
    """Read bounded Git and Program Files roots from explicit HKLM views."""

    if registry is None:
        try:
            import winreg as registry
        except ImportError:
            return (), ()
    views: list[int] = []
    for view in (
        getattr(registry, "KEY_WOW64_64KEY", 0),
        getattr(registry, "KEY_WOW64_32KEY", 0),
    ):
        if view not in views:
            views.append(view)
    git_roots: list[PurePath] = []
    program_files_roots: list[PurePath] = []
    for view in views:
        install_root = _read_windows_registry_path(
            registry,
            key_path=r"SOFTWARE\GitForWindows",
            value_name="InstallPath",
            view=view,
        )
        if install_root is not None:
            git_roots.append(install_root)
        for value_name in ("ProgramFilesDir", "ProgramFilesDir (x86)"):
            root = _read_windows_registry_path(
                registry,
                key_path=r"SOFTWARE\Microsoft\Windows\CurrentVersion",
                value_name=value_name,
                view=view,
            )
            if root is not None:
                program_files_roots.append(root)
    return (
        _casefold_deduplicated_paths(git_roots),
        _casefold_deduplicated_paths(program_files_roots),
    )


def windows_trusted_git_candidates(
    *,
    git_install_roots: Iterable[PurePath],
    program_files_roots: Iterable[PurePath],
) -> tuple[PurePath, ...]:
    install_roots = _casefold_deduplicated_paths(
        (
            *git_install_roots,
            *(root / "Git" for root in program_files_roots),
        )
    )
    return _casefold_deduplicated_paths(
        candidate
        for root in install_roots
        for candidate in (root / "cmd" / "git.exe", root / "bin" / "git.exe")
    )


def trusted_git_candidates(
    *,
    platform_name: str | None = None,
    windows_location_provider: WindowsGitLocationProvider | None = None,
) -> tuple[PurePath, ...]:
    effective_platform = os.name if platform_name is None else platform_name
    if effective_platform == "nt":
        provider = windows_location_provider or windows_system_git_roots
        git_roots, program_files_roots = provider()
        return windows_trusted_git_candidates(
            git_install_roots=git_roots,
            program_files_roots=program_files_roots,
        )
    return (
        Path("/usr/bin/git"),
        Path("/usr/local/bin/git"),
        Path("/opt/homebrew/bin/git"),
    )


def _lexically_within(path: Path, root: Path) -> bool:
    candidate = os.path.normcase(os.path.abspath(os.fspath(path)))
    boundary = os.path.normcase(os.path.abspath(os.fspath(root)))
    try:
        return os.path.commonpath((candidate, boundary)) == boundary
    except ValueError:
        return False


def resolve_trusted_git_executable(
    *,
    repo_root: Path | None = None,
    excluded_roots: Sequence[Path] = (),
    platform_name: str | None = None,
    windows_location_provider: WindowsGitLocationProvider | None = None,
    path_value: str | None = None,
) -> TrustedGitExecutable:
    """Resolve a bounded system Git; ``path_value`` is ignored for compatibility."""

    del path_value
    excluded = tuple(Path(value) for value in excluded_roots)
    if repo_root is not None:
        excluded = (*excluded, Path(repo_root))
    for candidate in trusted_git_candidates(
        platform_name=platform_name,
        windows_location_provider=windows_location_provider,
    ):
        try:
            resolved = Path(candidate).resolve(strict=True)
            metadata = os.lstat(resolved)
        except OSError:
            continue
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or (
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        if any(_lexically_within(resolved, root) for root in excluded):
            continue
        try:
            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except OSError:
            continue
        return TrustedGitExecutable(path=resolved, fingerprint=digest)
    raise TrustedGitError("trusted_git_executable_unavailable")


def _canonical_root(root: Path) -> Path:
    try:
        resolved = Path(root).resolve(strict=True)
    except OSError as exc:
        raise TrustedGitError("trusted_git_repository_root_invalid") from exc
    if not resolved.is_dir():
        raise TrustedGitError("trusted_git_repository_root_invalid")
    return resolved


def _command_prefix(git: TrustedGitExecutable, root: Path) -> list[str]:
    return [
        os.fspath(git.path),
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.useBuiltinFSMonitor=false",
        "-C",
        os.fspath(root),
        f"--work-tree={os.fspath(root)}",
    ]


def _invoke(
    git: TrustedGitExecutable,
    root: Path,
    arguments: Sequence[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [*_command_prefix(git, root), *arguments],
            cwd=root,
            env=trusted_git_environment(),
            capture_output=True,
            text=False,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrustedGitError(
            f"trusted_git_invocation_failed:{type(exc).__name__}"
        ) from exc


def _decode_utf8(raw: bytes, code: str) -> str:
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise TrustedGitError(code) from exc


def _same_path(left: Path, right: Path) -> bool:
    left_text = os.path.normcase(os.path.abspath(os.fspath(left)))
    right_text = os.path.normcase(os.path.abspath(os.fspath(right)))
    return left_text == right_text


def _verify_worktree(
    git: TrustedGitExecutable, root: Path, *, timeout: float
) -> None:
    configured_worktree = _invoke(
        git,
        root,
        ("config", "--local", "--get-all", "core.worktree"),
        timeout=timeout,
    )
    if configured_worktree.returncode == 0 and configured_worktree.stdout.strip():
        raise TrustedGitError("trusted_git_local_core_worktree_rejected")
    if configured_worktree.returncode not in {0, 1}:
        raise TrustedGitError("trusted_git_worktree_identity_failed")
    proof = _invoke(
        git,
        root,
        ("rev-parse", "--show-toplevel", "--is-inside-work-tree"),
        timeout=timeout,
    )
    if proof.returncode != 0:
        raise TrustedGitError("trusted_git_worktree_identity_failed")
    lines = _decode_utf8(
        proof.stdout, "trusted_git_worktree_identity_unreadable"
    ).splitlines()
    if len(lines) != 2 or lines[1].strip() != "true":
        raise TrustedGitError("trusted_git_worktree_identity_failed")
    try:
        observed = Path(lines[0].strip()).resolve(strict=True)
    except OSError as exc:
        raise TrustedGitError("trusted_git_worktree_identity_failed") from exc
    if not _same_path(observed, root):
        raise TrustedGitError("trusted_git_worktree_identity_mismatch")


def run_trusted_git_bytes(
    repo_root: Path,
    arguments: Sequence[str],
    *,
    git: TrustedGitExecutable | None = None,
    timeout: float = 15,
) -> subprocess.CompletedProcess[bytes]:
    root = _canonical_root(repo_root)
    executable = git or resolve_trusted_git_executable(repo_root=root)
    _verify_worktree(executable, root, timeout=timeout)
    return _invoke(executable, root, arguments, timeout=timeout)


def run_trusted_git_text(
    repo_root: Path,
    arguments: Sequence[str],
    *,
    git: TrustedGitExecutable | None = None,
    timeout: float = 15,
) -> subprocess.CompletedProcess[str]:
    completed = run_trusted_git_bytes(
        repo_root, arguments, git=git, timeout=timeout
    )
    stdout = _decode_utf8(completed.stdout, "trusted_git_output_unreadable")
    stderr = _decode_utf8(completed.stderr, "trusted_git_output_unreadable")
    return subprocess.CompletedProcess(
        completed.args, completed.returncode, stdout=stdout, stderr=stderr
    )


def validate_git_path(raw_path: str) -> str:
    """Validate one verbatim ``-z`` Git path without separator rewriting."""

    if not raw_path or "\\" in raw_path or "\x00" in raw_path:
        raise TrustedGitError("trusted_git_path_invalid")
    if raw_path.startswith("/") or _WINDOWS_DRIVE.match(raw_path):
        raise TrustedGitError("trusted_git_path_invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_path):
        raise TrustedGitError("trusted_git_path_invalid")
    parts = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise TrustedGitError("trusted_git_path_invalid")
    if PurePosixPath(raw_path).as_posix() != raw_path:
        raise TrustedGitError("trusted_git_path_invalid")
    return raw_path


def decode_git_z_paths(raw: bytes) -> tuple[str, ...]:
    if not raw.endswith(b"\0") and raw:
        raise TrustedGitError("trusted_git_z_output_invalid")
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    paths: list[str] = []
    for record in records:
        path = _decode_utf8(record, "trusted_git_path_undecodable")
        paths.append(validate_git_path(path))
    return tuple(paths)


def parse_porcelain_v2_z(raw: bytes) -> tuple[WorktreeEntry, ...]:
    """Parse raw porcelain-v2 records, rejecting unknown or malformed output."""

    if raw and not raw.endswith(b"\0"):
        raise TrustedGitError("trusted_git_status_unparseable")
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    entries: list[WorktreeEntry] = []
    index = 0
    while index < len(records):
        record = records[index]
        if record.startswith((b"? ", b"! ")):
            record_type = chr(record[0])
            path_raw = record[2:]
            original_raw = None
        elif record.startswith(b"1 "):
            fields = record.split(b" ", 8)
            if len(fields) != 9:
                raise TrustedGitError("trusted_git_status_unparseable")
            record_type = "1"
            path_raw = fields[8]
            original_raw = None
        elif record.startswith(b"2 "):
            fields = record.split(b" ", 9)
            if len(fields) != 10 or index + 1 >= len(records):
                raise TrustedGitError("trusted_git_status_unparseable")
            record_type = "2"
            path_raw = fields[9]
            index += 1
            original_raw = records[index]
        elif record.startswith(b"u "):
            fields = record.split(b" ", 10)
            if len(fields) != 11:
                raise TrustedGitError("trusted_git_status_unparseable")
            record_type = "u"
            path_raw = fields[10]
            original_raw = None
        else:
            raise TrustedGitError("trusted_git_status_unparseable")
        path = validate_git_path(
            _decode_utf8(path_raw, "trusted_git_path_undecodable")
        )
        original = None
        if original_raw is not None:
            original = validate_git_path(
                _decode_utf8(original_raw, "trusted_git_path_undecodable")
            )
        entries.append(WorktreeEntry(record_type, path, original))
        index += 1
    return tuple(entries)


def _classify_untracked(repo_root: Path, path: str) -> str:
    parts = path.split("/")
    candidate = repo_root.joinpath(*parts)
    if not _lexically_within(candidate, repo_root):
        return "uncertain"
    current = repo_root
    try:
        for part in parts[:-1]:
            current /= part
            component = os.lstat(current)
            component_attributes = getattr(component, "st_file_attributes", 0)
            if stat.S_ISLNK(component.st_mode) or (
                component_attributes
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                return "behavior"
            if not stat.S_ISDIR(component.st_mode):
                return "uncertain"
        metadata = os.lstat(candidate)
    except OSError:
        return "uncertain"
    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or (
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        return "behavior"
    if not stat.S_ISREG(metadata.st_mode):
        return "uncertain"
    name = PurePosixPath(path).name.casefold()
    suffix = PurePosixPath(path).suffix.casefold()
    if metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        return "behavior"
    if name in _BEHAVIOR_NAMES or name.startswith("requirements-"):
        return "behavior"
    if suffix in _BEHAVIOR_SUFFIXES:
        return "behavior"
    if suffix in _ORDINARY_ARTIFACT_SUFFIXES:
        return "ordinary"
    return "uncertain"


def inspect_worktree_drift(
    git: TrustedGitExecutable, repo_root: Path
) -> WorktreeDriftSummary:
    root = _canonical_root(repo_root)
    completed = run_trusted_git_bytes(
        root,
        ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
        git=git,
    )
    if completed.returncode != 0:
        raise TrustedGitError("trusted_git_status_failed")
    tracked = ordinary = behavior = uncertain = 0
    for entry in parse_porcelain_v2_z(completed.stdout):
        if entry.record_type == "!":
            continue
        if entry.record_type != "?":
            tracked += 1
            continue
        classification = _classify_untracked(root, entry.path)
        if classification == "ordinary":
            ordinary += 1
        elif classification == "behavior":
            behavior += 1
        else:
            uncertain += 1
    return WorktreeDriftSummary(tracked, ordinary, behavior, uncertain)


def assert_trusted_worktree_clean(
    git: TrustedGitExecutable, repo_root: Path
) -> WorktreeDriftSummary:
    summary = inspect_worktree_drift(git, repo_root)
    if summary.tracked_count:
        raise TrustedGitError("evidence_worktree_tracked_drift")
    if summary.behavior_untracked_count:
        raise TrustedGitError(
            "evidence_worktree_behavior_affecting_untracked:"
            f"{summary.behavior_untracked_count}"
        )
    if summary.uncertain_untracked_count:
        raise TrustedGitError(
            "evidence_worktree_identity_or_type_uncertain:"
            f"{summary.uncertain_untracked_count}"
        )
    return summary
