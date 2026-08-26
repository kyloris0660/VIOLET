"""Synthetic-only Windows feasibility checkpoint for SCV2-FL1-I2.

Every live object in this module is created under pytest's OS temporary root.
The tests deliberately exercise the native ABI before any production source
backend is implemented.  A failure is a phase blocker; there is no path-based
enumeration fallback.
"""

from __future__ import annotations

import ctypes
import multiprocessing
import os
import subprocess
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows feasibility only")

FILE_LIST_DIRECTORY = 0x0001
FILE_READ_ATTRIBUTES = 0x0080
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ID_INFO_CLASS = 0x12
FILE_ID_EXTD_DIRECTORY_INFO_CLASS = 0x13
FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS = 0x14
FILE_BASIC_INFO_CLASS = 0x0
FILE_STANDARD_INFO_CLASS = 0x1
FILE_ATTRIBUTE_TAG_INFO_CLASS = 0x9
ERROR_NO_MORE_FILES = 18
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

OBJ_CASE_INSENSITIVE = 0x40
OBJ_DONT_REPARSE = 0x1000
FILE_OPEN = 0x1
FILE_SYNCHRONOUS_IO_NONALERT = 0x20
FILE_NON_DIRECTORY_FILE = 0x40
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_OPEN_NO_RECALL = 0x00400000

# This flag constrains recall caused by the open itself.  It is intentionally
# not named or modeled as a guarantee about a later content read.
NO_RECALL_OPEN_ONLY_SEMANTICS = True


class FILE_ID_128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]


class FILE_ID_INFO(ctypes.Structure):
    _fields_ = [
        ("VolumeSerialNumber", ctypes.c_ulonglong),
        ("FileId", FILE_ID_128),
    ]


class FILE_BASIC_INFO(ctypes.Structure):
    _fields_ = [
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
    ]


class FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = [
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    ]


class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [
        ("FileAttributes", wintypes.DWORD),
        ("ReparseTag", wintypes.DWORD),
    ]


class FILE_ID_EXTD_DIR_INFO(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("FileIndex", wintypes.ULONG),
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("AllocationSize", ctypes.c_longlong),
        ("FileAttributes", wintypes.ULONG),
        ("FileNameLength", wintypes.ULONG),
        ("EaSize", wintypes.ULONG),
        ("ReparsePointTag", wintypes.ULONG),
        ("FileId", FILE_ID_128),
        ("FileName", wintypes.WCHAR * 1),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", wintypes.LPVOID),
        ("SecurityQualityOfService", wintypes.LPVOID),
    ]


class IO_STATUS_BLOCK_UNION(ctypes.Union):
    _fields_ = [("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID)]


class IO_STATUS_BLOCK(ctypes.Structure):
    _anonymous_ = ("Result",)
    _fields_ = [
        ("Result", IO_STATUS_BLOCK_UNION),
        ("Information", ctypes.c_size_t),
    ]


class UNICODE_STRING32(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint16),
        ("MaximumLength", ctypes.c_uint16),
        ("Buffer", ctypes.c_uint32),
    ]


class UNICODE_STRING64(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint16),
        ("MaximumLength", ctypes.c_uint16),
        ("Buffer", ctypes.c_uint64),
    ]


class OBJECT_ATTRIBUTES32(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint32),
        ("RootDirectory", ctypes.c_uint32),
        ("ObjectName", ctypes.c_uint32),
        ("Attributes", ctypes.c_uint32),
        ("SecurityDescriptor", ctypes.c_uint32),
        ("SecurityQualityOfService", ctypes.c_uint32),
    ]


class OBJECT_ATTRIBUTES64(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint32),
        ("_Padding", ctypes.c_uint32),
        ("RootDirectory", ctypes.c_uint64),
        ("ObjectName", ctypes.c_uint64),
        ("Attributes", ctypes.c_uint32),
        ("_Padding2", ctypes.c_uint32),
        ("SecurityDescriptor", ctypes.c_uint64),
        ("SecurityQualityOfService", ctypes.c_uint64),
    ]


@dataclass(frozen=True)
class DirectoryRecord:
    name: str
    file_id: bytes
    attributes: int
    reparse_tag: int


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
ntdll = ctypes.WinDLL("ntdll") if os.name == "nt" else None

if os.name == "nt":
    kernel32.GetSystemDirectoryW.argtypes = (wintypes.LPWSTR, wintypes.UINT)
    kernel32.GetSystemDirectoryW.restype = wintypes.UINT
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateSymbolicLinkW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    )
    kernel32.CreateSymbolicLinkW.restype = wintypes.BOOLEAN
    ntdll.NtCreateFile.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(OBJECT_ATTRIBUTES),
        ctypes.POINTER(IO_STATUS_BLOCK),
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    )
    ntdll.NtCreateFile.restype = wintypes.LONG


def _close(handle: int | None) -> None:
    if handle not in {None, 0, INVALID_HANDLE_VALUE}:
        assert kernel32.CloseHandle(handle)


def _open_directory(path: Path) -> int:
    handle = kernel32.CreateFileW(
        os.fspath(path),
        FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateFileW directory failed")
    return handle


def _query(handle: int, information_class: int, structure: type[ctypes.Structure]):
    value = structure()
    if not kernel32.GetFileInformationByHandleEx(
        handle, information_class, ctypes.byref(value), ctypes.sizeof(value)
    ):
        raise OSError(ctypes.get_last_error(), "GetFileInformationByHandleEx failed")
    return value


def _file_id(handle: int) -> tuple[int, bytes]:
    info = _query(handle, FILE_ID_INFO_CLASS, FILE_ID_INFO)
    return info.VolumeSerialNumber, bytes(info.FileId.Identifier)


def _parse_directory_buffer(buffer: ctypes.Array[ctypes.c_char]) -> tuple[DirectoryRecord, ...]:
    records: list[DirectoryRecord] = []
    base = ctypes.addressof(buffer)
    offset = 0
    while True:
        if offset + FILE_ID_EXTD_DIR_INFO.FileName.offset > ctypes.sizeof(buffer):
            raise AssertionError("directory record header escaped buffer")
        record = FILE_ID_EXTD_DIR_INFO.from_address(base + offset)
        name_end = offset + FILE_ID_EXTD_DIR_INFO.FileName.offset + record.FileNameLength
        if record.FileNameLength % 2 or name_end > ctypes.sizeof(buffer):
            raise AssertionError("directory record name escaped buffer")
        name = ctypes.string_at(
            base + offset + FILE_ID_EXTD_DIR_INFO.FileName.offset,
            record.FileNameLength,
        ).decode("utf-16-le", errors="strict")
        records.append(
            DirectoryRecord(
                name=name,
                file_id=bytes(record.FileId.Identifier),
                attributes=record.FileAttributes,
                reparse_tag=record.ReparsePointTag,
            )
        )
        if record.NextEntryOffset == 0:
            break
        if record.NextEntryOffset < FILE_ID_EXTD_DIR_INFO.FileName.offset:
            raise AssertionError("directory record offset is not forward-bounded")
        offset += record.NextEntryOffset
        if offset >= ctypes.sizeof(buffer):
            raise AssertionError("directory record offset escaped buffer")
    return tuple(records)


def _enumerate_page(handle: int, *, restart: bool) -> tuple[DirectoryRecord, ...] | None:
    buffer = ctypes.create_string_buffer(512)
    information_class = (
        FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS
        if restart
        else FILE_ID_EXTD_DIRECTORY_INFO_CLASS
    )
    ctypes.set_last_error(0)
    if not kernel32.GetFileInformationByHandleEx(
        handle, information_class, buffer, ctypes.sizeof(buffer)
    ):
        error = ctypes.get_last_error()
        if error == ERROR_NO_MORE_FILES:
            return None
        raise OSError(error, "same-handle directory enumeration failed")
    return _parse_directory_buffer(buffer)


def _open_relative(parent_handle: int, name: str) -> tuple[int | None, int]:
    buffer = ctypes.create_unicode_buffer(name)
    encoded_length = len(name.encode("utf-16-le"))
    unicode_name = UNICODE_STRING(
        Length=encoded_length,
        MaximumLength=encoded_length + 2,
        Buffer=ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = OBJECT_ATTRIBUTES(
        Length=ctypes.sizeof(OBJECT_ATTRIBUTES),
        RootDirectory=parent_handle,
        ObjectName=ctypes.pointer(unicode_name),
        Attributes=OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE,
        SecurityDescriptor=None,
        SecurityQualityOfService=None,
    )
    io_status = IO_STATUS_BLOCK()
    child = wintypes.HANDLE()
    status = int(
        ntdll.NtCreateFile(
            ctypes.byref(child),
            FILE_READ_ATTRIBUTES | SYNCHRONIZE,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            FILE_OPEN,
            FILE_SYNCHRONOUS_IO_NONALERT
            | FILE_NON_DIRECTORY_FILE
            | FILE_OPEN_REPARSE_POINT
            | FILE_OPEN_NO_RECALL,
            None,
            0,
        )
    )
    # NT_SUCCESS is true for all non-negative signed NTSTATUS values.
    if status < 0:
        return None, status
    return child.value, status


def _create_directory_junction(link: Path, target: Path) -> None:
    system_directory = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetSystemDirectoryW(system_directory, len(system_directory))
    if not 0 < length < len(system_directory):
        raise AssertionError("windows_same_handle_feasibility_blocked:system_directory")
    cmd = Path(system_directory.value) / "cmd.exe"
    completed = subprocess.run(
        [
            os.fspath(cmd),
            "/d",
            "/c",
            "mklink",
            "/J",
            os.fspath(link),
            os.fspath(target),
        ],
        capture_output=True,
        check=False,
        timeout=5,
    )
    if completed.returncode != 0 or not link.is_dir():
        raise AssertionError(
            "windows_same_handle_feasibility_blocked:junction_unavailable"
        )


def _blocking_worker(ready: multiprocessing.synchronize.Event) -> None:
    ready.set()
    multiprocessing.Event().wait(3600)


def test_windows_ctypes_abi_sizes_offsets_and_alignment_are_explicit() -> None:
    assert ctypes.sizeof(FILE_ID_128) == 16
    assert ctypes.sizeof(FILE_ID_INFO) == 24
    assert FILE_ID_INFO.FileId.offset == 8
    assert ctypes.sizeof(FILE_BASIC_INFO) == 40
    assert FILE_BASIC_INFO.FileAttributes.offset == 32
    assert FILE_ID_EXTD_DIR_INFO.FileId.offset == 72
    assert FILE_ID_EXTD_DIR_INFO.FileName.offset == 88
    assert ctypes.alignment(FILE_ID_EXTD_DIR_INFO) == 8

    assert ctypes.sizeof(UNICODE_STRING32) == 8
    assert UNICODE_STRING32.Buffer.offset == 4
    assert ctypes.sizeof(OBJECT_ATTRIBUTES32) == 24
    assert OBJECT_ATTRIBUTES32.Attributes.offset == 12
    assert ctypes.sizeof(UNICODE_STRING64) == 16
    assert UNICODE_STRING64.Buffer.offset == 8
    assert ctypes.sizeof(OBJECT_ATTRIBUTES64) == 48
    assert OBJECT_ATTRIBUTES64.Attributes.offset == 24

    expected_pointer_size = ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(UNICODE_STRING) == (16 if expected_pointer_size == 8 else 8)
    assert ctypes.sizeof(OBJECT_ATTRIBUTES) == (48 if expected_pointer_size == 8 else 24)
    assert ctypes.sizeof(IO_STATUS_BLOCK) == (16 if expected_pointer_size == 8 else 8)


def test_same_handle_enumeration_paginates_restarts_and_binds_file_ids(
    tmp_path: Path,
) -> None:
    root = tmp_path / "same-handle"
    root.mkdir()
    for index in range(24):
        (root / f"member-{index:02d}-synthetic.txt").write_bytes(b"synthetic")
    handle = _open_directory(root)
    child_handle: int | None = None
    try:
        pages: list[tuple[DirectoryRecord, ...]] = []
        page = _enumerate_page(handle, restart=True)
        while page is not None:
            pages.append(page)
            page = _enumerate_page(handle, restart=False)
        assert len(pages) > 1
        names = {record.name for page_records in pages for record in page_records}
        expected = {f"member-{index:02d}-synthetic.txt" for index in range(24)}
        assert expected <= names
        assert all(any(record.file_id) for page_records in pages for record in page_records)

        restarted = _enumerate_page(handle, restart=True)
        assert restarted is not None
        assert restarted == pages[0]

        selected = next(
            record
            for page_records in pages
            for record in page_records
            if record.name in expected
        )
        child_handle, status = _open_relative(handle, selected.name)
        assert status >= 0 and child_handle is not None
        _volume, opened_id = _file_id(child_handle)
        assert opened_id == selected.file_id
        assert NO_RECALL_OPEN_ONLY_SEMANTICS is True
    finally:
        _close(child_handle)
        _close(handle)


def test_parent_relative_open_change_hardlink_and_reparse_are_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "relative-open"
    root.mkdir()
    original = root / "original.txt"
    original.write_bytes(b"synthetic")
    hardlink = root / "hardlink.txt"
    os.link(original, hardlink)
    symlink = root / "symlink.txt"
    symlink_created = bool(
        kernel32.CreateSymbolicLinkW(
            os.fspath(symlink),
            os.fspath(original),
            0x2,  # SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE
        )
    )
    junction_target = root / "junction-target"
    junction_target.mkdir()
    (junction_target / "inside.txt").write_bytes(b"synthetic")
    junction = root / "junction"
    _create_directory_junction(junction, junction_target)

    parent = _open_directory(root)
    original_handle: int | None = None
    hardlink_handle: int | None = None
    symlink_handle: int | None = None
    try:
        original_handle, status = _open_relative(parent, original.name)
        assert status >= 0 and original_handle is not None
        hardlink_handle, status = _open_relative(parent, hardlink.name)
        assert status >= 0 and hardlink_handle is not None

        original_id = _file_id(original_handle)
        assert original_id[1] != bytes(16)
        assert _file_id(hardlink_handle) == original_id
        standard = _query(original_handle, FILE_STANDARD_INFO_CLASS, FILE_STANDARD_INFO)
        assert standard.NumberOfLinks >= 2

        before_basic = _query(original_handle, FILE_BASIC_INFO_CLASS, FILE_BASIC_INFO)
        before_standard = _query(original_handle, FILE_STANDARD_INFO_CLASS, FILE_STANDARD_INFO)
        renamed = root / "renamed.txt"
        original.rename(renamed)
        with renamed.open("ab") as stream:
            stream.write(b"-changed")
            stream.flush()
            os.fsync(stream.fileno())
        after_basic = _query(original_handle, FILE_BASIC_INFO_CLASS, FILE_BASIC_INFO)
        after_standard = _query(original_handle, FILE_STANDARD_INFO_CLASS, FILE_STANDARD_INFO)
        assert _file_id(original_handle) == original_id
        assert (
            after_basic.ChangeTime,
            after_basic.LastWriteTime,
            after_standard.EndOfFile,
        ) != (
            before_basic.ChangeTime,
            before_basic.LastWriteTime,
            before_standard.EndOfFile,
        )

        junction_child, junction_status = _open_relative(
            parent, r"junction\inside.txt"
        )
        assert junction_child is None
        assert junction_status < 0
        enumerated: list[DirectoryRecord] = []
        page = _enumerate_page(parent, restart=True)
        while page is not None:
            enumerated.extend(page)
            page = _enumerate_page(parent, restart=False)
        junction_record = next(record for record in enumerated if record.name == "junction")
        assert junction_record.attributes & FILE_ATTRIBUTE_REPARSE_POINT
        assert junction_record.reparse_tag != 0

        if symlink_created:
            symlink_handle, symlink_status = _open_relative(parent, symlink.name)
            assert symlink_status >= 0 and symlink_handle is not None
            tag = _query(
                symlink_handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, FILE_ATTRIBUTE_TAG_INFO
            )
            assert tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT
            assert tag.ReparseTag != 0
    finally:
        _close(symlink_handle)
        _close(hardlink_handle)
        _close(original_handle)
        _close(parent)


def test_worker_termination_is_confirmed_within_bounded_wait() -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    worker = context.Process(target=_blocking_worker, args=(ready,))
    worker.start()
    try:
        assert ready.wait(3)
        worker.terminate()
        worker.join(3)
        assert not worker.is_alive()
        assert worker.exitcode is not None
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(3)
