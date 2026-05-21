"""Windows Cloud Files metadata helpers.

These helpers intentionally inspect metadata only.  They never open file
contents and therefore do not trigger Cloud Files hydration by themselves.
"""

from __future__ import annotations

import errno
import multiprocessing
import os
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


IS_WINDOWS = platform.system() == "Windows"

FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_HIDDEN = 0x00000002
FILE_ATTRIBUTE_SYSTEM = 0x00000004
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_ARCHIVE = 0x00000020
FILE_ATTRIBUTE_SPARSE_FILE = 0x00000200
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_COMPRESSED = 0x00000800
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_NOT_CONTENT_INDEXED = 0x00002000
FILE_ATTRIBUTE_ENCRYPTED = 0x00004000
FILE_ATTRIBUTE_INTEGRITY_STREAM = 0x00008000
FILE_ATTRIBUTE_VIRTUAL = 0x00010000
FILE_ATTRIBUTE_NO_SCRUB_DATA = 0x00020000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_PINNED = 0x00080000
FILE_ATTRIBUTE_UNPINNED = 0x00100000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000

ERROR_CLOUD_FILE_NETWORK_UNAVAILABLE = 388

CLOUD_ATTRIBUTE_MASK = (
    FILE_ATTRIBUTE_OFFLINE
    | FILE_ATTRIBUTE_RECALL_ON_OPEN
    | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)

ATTRIBUTE_FLAGS: tuple[tuple[str, int], ...] = (
    ("readonly", FILE_ATTRIBUTE_READONLY),
    ("hidden", FILE_ATTRIBUTE_HIDDEN),
    ("system", FILE_ATTRIBUTE_SYSTEM),
    ("directory", FILE_ATTRIBUTE_DIRECTORY),
    ("archive", FILE_ATTRIBUTE_ARCHIVE),
    ("sparse_file", FILE_ATTRIBUTE_SPARSE_FILE),
    ("reparse_point", FILE_ATTRIBUTE_REPARSE_POINT),
    ("compressed", FILE_ATTRIBUTE_COMPRESSED),
    ("offline", FILE_ATTRIBUTE_OFFLINE),
    ("not_content_indexed", FILE_ATTRIBUTE_NOT_CONTENT_INDEXED),
    ("encrypted", FILE_ATTRIBUTE_ENCRYPTED),
    ("integrity_stream", FILE_ATTRIBUTE_INTEGRITY_STREAM),
    ("virtual", FILE_ATTRIBUTE_VIRTUAL),
    ("no_scrub_data", FILE_ATTRIBUTE_NO_SCRUB_DATA),
    ("recall_on_open", FILE_ATTRIBUTE_RECALL_ON_OPEN),
    ("pinned", FILE_ATTRIBUTE_PINNED),
    ("unpinned", FILE_ATTRIBUTE_UNPINNED),
    ("recall_on_data_access", FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS),
)


@dataclass(frozen=True)
class CloudFileState:
    path: str
    supported_platform: bool
    exists: bool
    is_file: bool
    attributes_raw: int | None = None
    attributes_hex: str | None = None
    attribute_names: tuple[str, ...] = ()
    offline: bool = False
    reparse_point: bool = False
    recall_on_open: bool = False
    recall_on_data_access: bool = False
    pinned: bool = False
    unpinned: bool = False
    sparse_file: bool = False
    likely_cloud_placeholder: bool = False
    error_code: int | None = None
    error_message: str | None = None

    def to_dict(self, *, include_path: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_path:
            data.pop("path", None)
        return data


def _format_windows_error(code: int) -> str:
    try:
        import ctypes

        return ctypes.FormatError(code).strip()
    except Exception:
        return os.strerror(code) if code else "unknown Windows error"


def _get_windows_attributes_raw(path: Path) -> tuple[int | None, int | None, str | None]:
    """Return ``(attributes, error_code, error_message)`` for a Windows path."""

    if not IS_WINDOWS:
        return None, None, None
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetFileAttributesW.restype = ctypes.c_uint32
        raw = kernel32.GetFileAttributesW(str(path))
        if raw == 0xFFFFFFFF:
            code = ctypes.get_last_error()
            return None, int(code), _format_windows_error(int(code))
        return int(raw), None, None
    except Exception as exc:
        return None, getattr(exc, "winerror", None), str(exc)


def _flags_from_raw(raw: int | None) -> dict[str, bool]:
    if raw is None:
        return {name: False for name, _bit in ATTRIBUTE_FLAGS}
    return {name: bool(raw & bit) for name, bit in ATTRIBUTE_FLAGS}


def get_cloud_file_attributes(path: str | Path) -> dict[str, Any]:
    """Return metadata-only Cloud Files attributes for *path*.

    On non-Windows platforms this returns ``supported_platform=False`` and
    conservative no-risk flags.
    """

    return classify_cloud_file_state(path).to_dict(include_path=True)


def classify_cloud_file_state(path: str | Path) -> CloudFileState:
    file_path = Path(path)
    if not IS_WINDOWS:
        try:
            exists = file_path.exists()
            is_file = file_path.is_file()
        except OSError as exc:
            return CloudFileState(
                path=str(file_path),
                supported_platform=False,
                exists=False,
                is_file=False,
                error_code=getattr(exc, "errno", None),
                error_message=str(exc),
            )
        return CloudFileState(
            path=str(file_path),
            supported_platform=False,
            exists=exists,
            is_file=is_file,
        )

    raw, error_code, error_message = _get_windows_attributes_raw(file_path)
    if raw is None:
        return CloudFileState(
            path=str(file_path),
            supported_platform=True,
            exists=False,
            is_file=False,
            error_code=error_code,
            error_message=error_message,
        )

    flags = _flags_from_raw(raw)
    exists = True
    is_file = not flags["directory"]
    likely_cloud = bool(
        flags["offline"]
        or flags["recall_on_open"]
        or flags["recall_on_data_access"]
        or (flags["reparse_point"] and (flags["offline"] or flags["sparse_file"] or flags["unpinned"]))
    )
    names = tuple(name for name, bit in ATTRIBUTE_FLAGS if raw & bit)
    return CloudFileState(
        path=str(file_path),
        supported_platform=True,
        exists=exists,
        is_file=is_file,
        attributes_raw=raw,
        attributes_hex=f"0x{raw:08x}",
        attribute_names=names,
        offline=flags["offline"],
        reparse_point=flags["reparse_point"],
        recall_on_open=flags["recall_on_open"],
        recall_on_data_access=flags["recall_on_data_access"],
        pinned=flags["pinned"],
        unpinned=flags["unpinned"],
        sparse_file=flags["sparse_file"],
        likely_cloud_placeholder=likely_cloud,
    )


def is_likely_cloud_placeholder(path: str | Path) -> bool:
    return classify_cloud_file_state(path).likely_cloud_placeholder


def classify_file_access_error(error: BaseException, cloud_state: CloudFileState | None = None) -> str:
    """Map a file access/copy exception to a structured ingestion reason."""

    winerror = getattr(error, "winerror", None)
    err_no = getattr(error, "errno", None)
    if winerror == ERROR_CLOUD_FILE_NETWORK_UNAVAILABLE:
        return "cloud_network_unavailable"
    if winerror in (2, 3) or err_no == errno.ENOENT:
        return "source_missing"
    if winerror == 5 or err_no in (errno.EACCES, errno.EPERM):
        return "permission_denied"
    if cloud_state:
        if cloud_state.offline:
            return "cloud_offline"
        if cloud_state.recall_on_open:
            return "cloud_recall_on_open"
        if cloud_state.recall_on_data_access:
            return "cloud_recall_on_data_access"
        if cloud_state.likely_cloud_placeholder:
            return "cloud_hydration_failed"
    return "generic_copy_failed"


def _read_prefix_worker(path: str, max_bytes: int, conn: Any) -> None:
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes)
        conn.send({"ok": True, "bytes_read": len(data), "error_reason": None, "error_message": None})
    except Exception as exc:
        conn.send(
            {
                "ok": False,
                "bytes_read": 0,
                "error_reason": classify_file_access_error(exc),
                "error_message": str(exc),
            }
        )
    finally:
        conn.close()


def _read_full_worker(path: str, chunk_size: int, conn: Any) -> None:
    bytes_read = 0
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                bytes_read += len(chunk)
        conn.send(
            {
                "ok": True,
                "bytes_read": bytes_read,
                "error_reason": None,
                "error_message": None,
                "winerror": None,
                "errno": None,
            }
        )
    except Exception as exc:
        conn.send(
            {
                "ok": False,
                "bytes_read": bytes_read,
                "error_reason": classify_file_access_error(exc),
                "error_message": str(exc),
                "winerror": getattr(exc, "winerror", None),
                "errno": getattr(exc, "errno", None),
            }
        )
    finally:
        conn.close()


def read_probe_prefix(
    path: str | Path,
    *,
    max_bytes: int = 1,
    timeout_seconds: int = 10,
    retries: int = 0,
) -> dict[str, Any]:
    """Opt-in read probe that may trigger provider-side hydration.

    This is deliberately separate from metadata helpers and must only be used
    behind an explicit CLI/API flag.
    """

    attempts: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    for attempt in range(retries + 1):
        parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
        proc = multiprocessing.Process(
            target=_read_prefix_worker,
            args=(str(Path(path)), max_bytes, child_conn),
            daemon=True,
        )
        proc.start()
        child_conn.close()
        proc.join(timeout_seconds)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
            result = {
                "attempt": attempt + 1,
                "ok": False,
                "bytes_read": 0,
                "error_reason": "read_probe_timeout",
                "error_message": f"read probe timed out after {timeout_seconds}s",
            }
        elif parent_conn.poll(timeout=1):
            result = parent_conn.recv()
            result["attempt"] = attempt + 1
        else:
            result = {
                "attempt": attempt + 1,
                "ok": False,
                "bytes_read": 0,
                "error_reason": "read_probe_no_result",
                "error_message": f"subprocess exited with code {proc.exitcode} and no result",
            }
        parent_conn.close()
        attempts.append(result)
        final = result
        if result.get("ok"):
            break

    return {
        "read_probe": True,
        "max_bytes": max_bytes,
        "timeout_seconds": timeout_seconds,
        "retries": retries,
        "attempts": attempts,
        "ok": bool(final and final.get("ok")),
        "bytes_read": int(final.get("bytes_read", 0) if final else 0),
        "error_reason": final.get("error_reason") if final else None,
        "error_message": final.get("error_message") if final else None,
    }


def read_verify_full_content(
    path: str | Path,
    *,
    expected_size: int | None = None,
    timeout_seconds: int = 60,
    retries: int = 1,
    chunk_size: int = 4 * 1024 * 1024,
) -> dict[str, Any]:
    """Opt-in full-content read verification.

    The function streams the entire file in a subprocess so callers can bound
    per-file read time.  It writes nothing.  On Cloud Files sources this may
    trigger provider-side hydration, so callers must keep it behind an
    explicit approval gate.
    """

    if chunk_size <= 0:
        return {
            "full_read": True,
            "expected_size": expected_size,
            "chunk_size": chunk_size,
            "timeout_seconds": timeout_seconds,
            "retries": retries,
            "attempts": [],
            "ok": False,
            "bytes_read": 0,
            "bytes_read_total": 0,
            "duration_seconds": 0.0,
            "error_reason": "invalid_chunk_size",
            "error_message": "chunk_size must be greater than 0",
        }

    attempts: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    for attempt in range(retries + 1):
        started = time.monotonic()
        parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
        proc = multiprocessing.Process(
            target=_read_full_worker,
            args=(str(Path(path)), chunk_size, child_conn),
            daemon=True,
        )
        proc.start()
        child_conn.close()
        proc.join(timeout_seconds)
        duration = time.monotonic() - started
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
            result = {
                "attempt": attempt + 1,
                "ok": False,
                "bytes_read": 0,
                "duration_seconds": duration,
                "error_reason": "read_timeout",
                "error_message": f"full read timed out after {timeout_seconds}s",
                "winerror": None,
                "errno": None,
            }
        elif parent_conn.poll(timeout=1):
            try:
                result = parent_conn.recv()
                result["attempt"] = attempt + 1
                result["duration_seconds"] = duration
            except EOFError:
                result = {
                    "attempt": attempt + 1,
                    "ok": False,
                    "bytes_read": 0,
                    "duration_seconds": duration,
                    "error_reason": "read_worker_eof",
                    "error_message": f"subprocess exited with code {proc.exitcode} before sending a result",
                    "winerror": None,
                    "errno": None,
                }
        else:
            result = {
                "attempt": attempt + 1,
                "ok": False,
                "bytes_read": 0,
                "duration_seconds": duration,
                "error_reason": "read_no_result",
                "error_message": f"subprocess exited with code {proc.exitcode} and no result",
                "winerror": None,
                "errno": None,
            }
        parent_conn.close()

        if result.get("ok") and expected_size is not None and int(result.get("bytes_read", 0)) != expected_size:
            result = {
                **result,
                "ok": False,
                "error_reason": "size_mismatch",
                "error_message": (
                    f"full read size mismatch: read {int(result.get('bytes_read', 0))} "
                    f"bytes, expected {expected_size}"
                ),
            }

        attempts.append(result)
        final = result
        if result.get("ok"):
            break

    total_attempt_bytes = sum(int(attempt.get("bytes_read", 0) or 0) for attempt in attempts)
    total_duration = sum(float(attempt.get("duration_seconds", 0.0) or 0.0) for attempt in attempts)
    return {
        "full_read": True,
        "expected_size": expected_size,
        "chunk_size": chunk_size,
        "timeout_seconds": timeout_seconds,
        "retries": retries,
        "attempts": attempts,
        "ok": bool(final and final.get("ok")),
        "bytes_read": int(final.get("bytes_read", 0) if final else 0),
        "bytes_read_total": total_attempt_bytes,
        "duration_seconds": total_duration,
        "error_reason": final.get("error_reason") if final else None,
        "error_message": final.get("error_message") if final else None,
    }
