"""Shared fail-closed Git boundary for V.I.O.L.E.T. evidence tooling.

The module deliberately does not consult ``PATH`` or caller-provided Git and
Program Files environment variables.  Every repository command is bound to an
explicit repository root and work tree, runs without caller Git controls, and
first proves that Git observes the same work tree the caller named.
"""

from __future__ import annotations

import hashlib
import json
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
        ".pth",
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
        ".cfg",
        ".ini",
        ".toml",
        ".yaml",
        ".yml",
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
        "conftest.py",
        "pytest.ini",
        "sitecustomize.py",
        "usercustomize.py",
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
    object_identity_fingerprint: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "identity_source": "os_backed_bounded_system_location",
            "executable_fingerprint": self.fingerprint,
            "object_identity_fingerprint": self.object_identity_fingerprint,
            "absolute_path_emitted": False,
            "repo_local": False,
            "symlink_or_reparse": False,
        }


@dataclass(frozen=True)
class ApprovedPythonRuntime:
    executable: Path
    venv_root: Path
    executable_fingerprint: str
    executable_identity_fingerprint: str
    venv_root_identity_fingerprint: str
    pyvenv_cfg_fingerprint: str
    execution_manifest_fingerprint: str


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
    ordinary_ignored_count: int = 0
    behavior_ignored_count: int = 0
    uncertain_ignored_count: int = 0


MAX_STATUS_BYTES = 4 * 1024 * 1024
MAX_STATUS_ENTRIES = 4096
MAX_PYVENV_CFG_BYTES = 64 * 1024
MAX_RUNTIME_ENVIRONMENT_ENTRIES = 200_000
_RUNTIME_CONTROL_SUFFIXES = frozenset({".pth", ".pyd", ".dll", ".so", ".dylib"})
_RUNTIME_CONTROL_NAMES = frozenset(
    {"sitecustomize.py", "usercustomize.py", "pytest.ini", "pyproject.toml"}
)
_ORDINARY_IGNORED_DIRECTORIES = frozenset(
    {
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
_PRIVATE_ARTIFACT_PREFIX = ".local_manifests/scv2-fl1-i2-private"
_PRIVATE_ARTIFACT_SUFFIXES = frozenset({".json", ".log", ".md", ".txt"})


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


def _identity_fingerprint(metadata: os.stat_result) -> str:
    identity = (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )
    return hashlib.sha256(repr(identity).encode("ascii")).hexdigest()


def _lexical_absolute(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise TrustedGitError("trusted_git_executable_lexical_path_invalid")
    return Path(os.path.abspath(os.fspath(candidate)))


def _assert_no_alias_components(path: Path) -> tuple[Path, os.stat_result]:
    """Inspect the lexical chain before resolving any component."""

    lexical = _lexical_absolute(path)
    anchor = Path(lexical.anchor)
    current = anchor
    try:
        anchor_metadata = os.lstat(anchor)
        anchor_attributes = getattr(anchor_metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(anchor_metadata.st_mode) or (
            anchor_attributes
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise TrustedGitError("trusted_git_executable_alias_rejected")
        metadata = anchor_metadata
        for component in lexical.parts[1:]:
            current /= component
            metadata = os.lstat(current)
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                attributes
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise TrustedGitError("trusted_git_executable_alias_rejected")
    except TrustedGitError:
        raise
    except OSError as exc:
        raise TrustedGitError("trusted_git_executable_unavailable") from exc
    return lexical, metadata


def _bind_executable(path: Path) -> tuple[str, str]:
    lexical, lexical_metadata = _assert_no_alias_components(path)
    if not stat.S_ISREG(lexical_metadata.st_mode):
        raise TrustedGitError("trusted_git_executable_unavailable")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(lexical, flags)
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != lexical_metadata.st_dev
            or opened.st_ino != lexical_metadata.st_ino
        ):
            raise TrustedGitError("trusted_git_executable_identity_drift")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _identity_fingerprint(opened) != _identity_fingerprint(after):
            raise TrustedGitError("trusted_git_executable_identity_drift")
        return digest.hexdigest(), _identity_fingerprint(after)
    except TrustedGitError:
        raise
    except OSError as exc:
        raise TrustedGitError("trusted_git_executable_unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_regular_nofollow(path: Path, *, max_bytes: int) -> tuple[bytes, str]:
    lexical, lexical_metadata = _assert_no_alias_components(path)
    if not stat.S_ISREG(lexical_metadata.st_mode):
        raise TrustedGitError("approved_python_runtime_invalid")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(lexical, flags)
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != lexical_metadata.st_dev
            or opened.st_ino != lexical_metadata.st_ino
        ):
            raise TrustedGitError("approved_python_runtime_identity_drift")
        data = bytearray()
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise TrustedGitError("approved_python_runtime_manifest_too_large")
        after = os.fstat(descriptor)
        if _identity_fingerprint(opened) != _identity_fingerprint(after):
            raise TrustedGitError("approved_python_runtime_identity_drift")
        return bytes(data), _identity_fingerprint(after)
    except TrustedGitError:
        raise
    except OSError as exc:
        raise TrustedGitError("approved_python_runtime_invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def verify_approved_python_runtime(
    python_executable: Path,
    *,
    repo_root: Path,
    timeout: float = 15,
) -> ApprovedPythonRuntime:
    """Bind the exact venv used by canonical pytest without trusting env redirects."""

    executable_lexical, _ = _assert_no_alias_components(Path(python_executable))
    executable = executable_lexical.resolve(strict=True)
    venv_root_lexical = executable_lexical.parent.parent
    venv_root_lexical, venv_metadata = _assert_no_alias_components(venv_root_lexical)
    if not stat.S_ISDIR(venv_metadata.st_mode):
        raise TrustedGitError("approved_python_runtime_invalid")
    venv_root = venv_root_lexical.resolve(strict=True)
    config_bytes, _ = _read_regular_nofollow(
        venv_root_lexical / "pyvenv.cfg", max_bytes=MAX_PYVENV_CFG_BYTES
    )
    executable_digest, executable_identity = _bind_executable(executable_lexical)
    probe = (
        "import json,sys;print(json.dumps({"
        "'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
        "'path':sys.path},sort_keys=True,separators=(',',':')))"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.casefold().startswith("python")
        and not key.casefold().startswith("pytest_")
        and not key.casefold().startswith("coverage")
        and not key.casefold().startswith("cov_core")
    }
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    try:
        completed = subprocess.run(
            [os.fspath(executable), "-B", "-I", "-s", "-c", probe],
            cwd=_canonical_root(repo_root),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrustedGitError("approved_python_runtime_probe_failed") from exc
    if completed.returncode != 0 or completed.stderr:
        raise TrustedGitError("approved_python_runtime_probe_failed")
    try:
        observed = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TrustedGitError("approved_python_runtime_probe_invalid") from exc
    if set(observed) != {"base_prefix", "executable", "path", "prefix"}:
        raise TrustedGitError("approved_python_runtime_probe_invalid")
    if not isinstance(observed["path"], list) or not all(
        isinstance(item, str) and item for item in observed["path"]
    ):
        raise TrustedGitError("approved_python_runtime_probe_invalid")
    try:
        observed_executable = Path(observed["executable"]).resolve(strict=True)
        observed_prefix = Path(observed["prefix"]).resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise TrustedGitError("approved_python_runtime_probe_invalid") from exc
    if not _same_path(observed_executable, executable) or not _same_path(
        observed_prefix, venv_root
    ):
        raise TrustedGitError("approved_python_runtime_identity_mismatch")
    repo = _canonical_root(repo_root)
    for entry in observed["path"]:
        entry_path = Path(entry)
        if entry_path.is_absolute() and _lexically_within(entry_path, repo):
            if not _lexically_within(entry_path, venv_root):
                raise TrustedGitError("approved_python_runtime_repo_redirect")
    environment_files: list[dict[str, Any]] = []
    scanned = 0
    for current_root, directory_names, file_names in os.walk(
        venv_root, topdown=True, followlinks=False
    ):
        directory_names.sort(key=str.casefold)
        file_names.sort(key=str.casefold)
        current = Path(current_root)
        for name in (*directory_names, *file_names):
            scanned += 1
            if scanned > MAX_RUNTIME_ENVIRONMENT_ENTRIES:
                raise TrustedGitError("approved_python_runtime_manifest_too_large")
            child = current / name
            try:
                metadata = os.lstat(child)
            except OSError as exc:
                raise TrustedGitError("approved_python_runtime_invalid") from exc
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                attributes
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise TrustedGitError("approved_python_runtime_alias_rejected")
        for name in file_names:
            child = current / name
            suffix = child.suffix.casefold()
            if suffix not in _RUNTIME_CONTROL_SUFFIXES and name.casefold() not in _RUNTIME_CONTROL_NAMES:
                continue
            metadata = os.lstat(child)
            if suffix == ".pth" or name.casefold() in _RUNTIME_CONTROL_NAMES:
                data, identity = _read_regular_nofollow(
                    child, max_bytes=1024 * 1024
                )
                content_fingerprint = hashlib.sha256(data).hexdigest()
            else:
                identity = _identity_fingerprint(metadata)
                content_fingerprint = "identity_bound_native_module"
            environment_files.append(
                {
                    "relative_path": child.relative_to(venv_root).as_posix(),
                    "identity": identity,
                    "content_fingerprint": content_fingerprint,
                }
            )
    manifest = {
        "probe": observed,
        "python_argv": ["-B", "-I", "-s"],
        "forced_environment": {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        },
        "pyvenv_cfg_fingerprint": hashlib.sha256(config_bytes).hexdigest(),
        "execution_control_files": environment_files,
    }
    return ApprovedPythonRuntime(
        executable=executable,
        venv_root=venv_root,
        executable_fingerprint=executable_digest,
        executable_identity_fingerprint=executable_identity,
        venv_root_identity_fingerprint=_identity_fingerprint(venv_metadata),
        pyvenv_cfg_fingerprint=hashlib.sha256(config_bytes).hexdigest(),
        execution_manifest_fingerprint=hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )


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
            lexical, metadata = _assert_no_alias_components(Path(candidate))
            resolved = lexical.resolve(strict=True)
        except (OSError, TrustedGitError):
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
            digest, identity = _bind_executable(lexical)
        except TrustedGitError:
            continue
        return TrustedGitExecutable(
            path=resolved,
            fingerprint=digest,
            object_identity_fingerprint=identity,
        )
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
    before_digest, before_identity = _bind_executable(git.path)
    if (
        before_digest != git.fingerprint
        or before_identity != git.object_identity_fingerprint
    ):
        raise TrustedGitError("trusted_git_executable_identity_drift")
    try:
        completed = subprocess.run(
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
    after_digest, after_identity = _bind_executable(git.path)
    if (
        after_digest != git.fingerprint
        or after_identity != git.object_identity_fingerprint
    ):
        raise TrustedGitError("trusted_git_executable_identity_drift")
    return completed


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


def _explicit_ordinary_ignored(path: str, metadata: os.stat_result) -> bool:
    normalized = path.rstrip("/")
    parts = PurePosixPath(normalized).parts
    if stat.S_ISDIR(metadata.st_mode):
        return normalized in _ORDINARY_IGNORED_DIRECTORIES or normalized == _PRIVATE_ARTIFACT_PREFIX
    if not stat.S_ISREG(metadata.st_mode):
        return False
    if parts and parts[0] in _ORDINARY_IGNORED_DIRECTORIES:
        return PurePosixPath(normalized).suffix.casefold() not in _BEHAVIOR_SUFFIXES
    if normalized.startswith(_PRIVATE_ARTIFACT_PREFIX + "/"):
        return PurePosixPath(normalized).suffix.casefold() in _PRIVATE_ARTIFACT_SUFFIXES
    return normalized in {".coverage"}


def _classify_untracked(repo_root: Path, path: str, *, ignored: bool = False) -> str:
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
    if ignored and _explicit_ordinary_ignored(path, metadata):
        return "ordinary"
    if not stat.S_ISREG(metadata.st_mode):
        return "uncertain"
    name = PurePosixPath(path).name.casefold()
    suffix = PurePosixPath(path).suffix.casefold()
    if metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        return "behavior"
    if name in _BEHAVIOR_NAMES or name.startswith("requirements-") or name.startswith(".env."):
        return "behavior"
    if suffix in _BEHAVIOR_SUFFIXES:
        return "behavior"
    if suffix in _ORDINARY_ARTIFACT_SUFFIXES:
        return "ordinary"
    return "uncertain"


def candidate_behavior_carry_forward(repo_root: Path, candidate: str) -> bool:
    """Local launcher and A1 evidence share the same candidate drift boundary."""
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo_root), *args],
            text=True, encoding='utf-8', stderr=subprocess.DEVNULL, timeout=10).strip()
    try:
        if not re.fullmatch('[0-9a-f]{40}', candidate):
            return False
        if git('rev-parse', candidate+'^{commit}') != candidate or git('merge-base',candidate,'HEAD') != candidate:
            return False
        changed = git('diff', '--name-only', '-z', candidate).split('\0')
        for path in filter(None, changed):
            if path == 'AGENTS.md' or (path.startswith('docs/') and path.endswith('.md')):
                continue
            if path not in {'docs/state/current-phase.json', 'docs/reports/production-pixiv-a1-summary.json',
                            'docs/reports/production-import-recovery-summary.json'}:
                return False
        for path in filter(None, git('ls-files','--others','--exclude-standard','-z').split('\0')):
            if _classify_untracked(repo_root, path) != 'ordinary':
                return False
            # An otherwise ordinary artifact explicitly loaded by application
            # code is behavior input, regardless of its docs/ placement.
            referenced = subprocess.run(['git','-C',str(repo_root),'grep','-l','-F',path,'--',
                'backend','frontend','scripts','run.py'], capture_output=True, timeout=10)
            if referenced.returncode != 1:
                return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def inspect_worktree_drift(
    git: TrustedGitExecutable,
    repo_root: Path,
    *,
    approved_python_runtime: ApprovedPythonRuntime | None = None,
) -> WorktreeDriftSummary:
    root = _canonical_root(repo_root)
    completed = run_trusted_git_bytes(
        root,
        ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
        git=git,
    )
    if completed.returncode != 0:
        raise TrustedGitError("trusted_git_status_failed")
    ignored_arguments: list[str] = [
        "ls-files",
        "-z",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--",
        ".",
    ]
    if approved_python_runtime is not None and _lexically_within(
        approved_python_runtime.venv_root, root
    ):
        current_runtime = verify_approved_python_runtime(
            approved_python_runtime.executable, repo_root=root
        )
        if current_runtime != approved_python_runtime:
            raise TrustedGitError("approved_python_runtime_identity_drift")
        relative = approved_python_runtime.venv_root.relative_to(root).as_posix()
        validate_git_path(relative)
        ignored_arguments.extend(
            (f":(exclude,top){relative}", f":(exclude,top){relative}/**")
        )
    ignored = run_trusted_git_bytes(
        root,
        tuple(ignored_arguments),
        git=git,
    )
    if ignored.returncode != 0:
        raise TrustedGitError("trusted_git_ignored_status_failed")
    if len(completed.stdout) + len(ignored.stdout) > MAX_STATUS_BYTES:
        raise TrustedGitError("trusted_git_status_budget_exceeded")
    entries = parse_porcelain_v2_z(completed.stdout)
    ignored_paths = decode_git_z_paths(ignored.stdout)
    if len(entries) + len(ignored_paths) > MAX_STATUS_ENTRIES:
        raise TrustedGitError("trusted_git_status_budget_exceeded")
    tracked = ordinary = behavior = uncertain = 0
    ordinary_ignored = behavior_ignored = uncertain_ignored = 0
    for entry in entries:
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
    for path in ignored_paths:
        classification = _classify_untracked(root, path, ignored=True)
        if classification == "ordinary":
            ordinary_ignored += 1
        elif classification == "behavior":
            behavior_ignored += 1
        else:
            uncertain_ignored += 1
    return WorktreeDriftSummary(
        tracked,
        ordinary,
        behavior,
        uncertain,
        ordinary_ignored,
        behavior_ignored,
        uncertain_ignored,
    )


def assert_trusted_worktree_clean(
    git: TrustedGitExecutable,
    repo_root: Path,
    *,
    approved_python_runtime: ApprovedPythonRuntime | None = None,
) -> WorktreeDriftSummary:
    summary = inspect_worktree_drift(
        git,
        repo_root,
        approved_python_runtime=approved_python_runtime,
    )
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
    if summary.behavior_ignored_count:
        raise TrustedGitError(
            "evidence_worktree_behavior_affecting_ignored:"
            f"{summary.behavior_ignored_count}"
        )
    if summary.uncertain_ignored_count:
        raise TrustedGitError(
            "evidence_worktree_ignored_identity_or_type_uncertain:"
            f"{summary.uncertain_ignored_count}"
        )
    return summary
