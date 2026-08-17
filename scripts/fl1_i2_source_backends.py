"""Handle-bound source backends for SCV2-FL1-I2 synthetic validation.

There is intentionally no path-enumeration fallback.  A backend that cannot
enumerate from the verified directory handle fails closed.
"""

from __future__ import annotations

import ctypes
import os
import stat
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from backend.app.services.source_safety import (
    CloudAvailability,
    FileChangeIdentity,
    FileObjectIdentity,
    HandleObservation,
)


class SourceBackendError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DirectoryMember:
    name: str
    object_identity: FileObjectIdentity
    attributes: int
    reparse_tag: int


@dataclass
class OpenHandle:
    raw_handle: int
    observation: HandleObservation
    backend: "BaseHandleBackend"

    def close(self) -> None:
        if self.raw_handle >= 0:
            self.backend.close_handle(self.raw_handle)
            self.raw_handle = -1

    def __enter__(self) -> "OpenHandle":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class BaseHandleBackend:
    def open_directory(self, path: Path) -> OpenHandle:
        raise NotImplementedError

    def enumerate_directory(self, directory: OpenHandle) -> tuple[DirectoryMember, ...]:
        raise NotImplementedError

    def open_child(self, directory: OpenHandle, member: DirectoryMember) -> OpenHandle:
        raise NotImplementedError

    def observe(self, handle: OpenHandle) -> HandleObservation:
        raise NotImplementedError

    def close_handle(self, raw_handle: int) -> None:
        raise NotImplementedError

    def read_chunks(self, handle: OpenHandle, *, chunk_size: int, max_bytes: int) -> Iterator[bytes]:
        raise NotImplementedError


def _validate_member_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise SourceBackendError("source_member_name_invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise SourceBackendError("source_member_name_invalid")


class PosixHandleBackend(BaseHandleBackend):
    """POSIX backend using fd-scandir and parent-relative no-follow open."""

    def _require_posix(self) -> None:
        if os.name == "nt" or not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
            raise SourceBackendError("posix_handle_capability_unsupported")

    @staticmethod
    def _identity(metadata: os.stat_result) -> FileObjectIdentity:
        return FileObjectIdentity("posix", f"{metadata.st_dev:x}", f"{metadata.st_ino:x}")

    @classmethod
    def _observation(cls, fd: int) -> HandleObservation:
        metadata = os.fstat(fd)
        return HandleObservation(
            object_identity=cls._identity(metadata),
            change_identity=FileChangeIdentity(
                change_time_ns=metadata.st_ctime_ns,
                write_time_ns=metadata.st_mtime_ns,
                size=metadata.st_size,
                allocation_size=getattr(metadata, "st_blocks", 0) * 512,
            ),
            cloud_availability=CloudAvailability.AVAILABLE,
            attributes_known=True,
            is_directory=stat.S_ISDIR(metadata.st_mode),
            reparse_point=False,
            reparse_tag=0,
            link_count=metadata.st_nlink,
            no_follow=True,
            identity_bound=True,
            no_recall_open_only=False,
        )

    def open_directory(self, path: Path) -> OpenHandle:
        self._require_posix()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except (OSError, TypeError) as exc:
            raise SourceBackendError("source_directory_open_failed") from exc
        try:
            observation = self._observation(fd)
            if not observation.is_directory:
                raise SourceBackendError("source_directory_not_directory")
            return OpenHandle(fd, observation, self)
        except Exception:
            os.close(fd)
            raise

    def enumerate_directory(self, directory: OpenHandle) -> tuple[DirectoryMember, ...]:
        before = self.observe(directory)
        try:
            iterator = os.scandir(directory.raw_handle)
        except (OSError, TypeError) as exc:
            raise SourceBackendError("same_handle_enumeration_unsupported") from exc
        records: list[DirectoryMember] = []
        try:
            with iterator:
                for entry in iterator:
                    _validate_member_name(entry.name)
                    metadata = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(metadata.st_mode):
                        raise SourceBackendError("source_reparse_point_rejected")
                    records.append(DirectoryMember(entry.name, self._identity(metadata), metadata.st_mode, 0))
        except OSError as exc:
            raise SourceBackendError("same_handle_enumeration_failed") from exc
        after = self.observe(directory)
        if before.object_identity != after.object_identity:
            raise SourceBackendError("source_directory_identity_drift")
        identities = [member.object_identity for member in records]
        if len(set(identities)) != len(identities):
            raise SourceBackendError("source_alias_identity_rejected")
        return tuple(records)

    def open_child(self, directory: OpenHandle, member: DirectoryMember) -> OpenHandle:
        _validate_member_name(member.name)
        try:
            fd = os.open(member.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory.raw_handle)
        except OSError as exc:
            raise SourceBackendError("source_child_open_failed") from exc
        try:
            observation = self._observation(fd)
            if observation.object_identity != member.object_identity:
                raise SourceBackendError("source_child_identity_mismatch")
            if observation.reparse_point:
                raise SourceBackendError("source_reparse_point_rejected")
            if observation.link_count != 1:
                raise SourceBackendError("source_hard_link_rejected")
            return OpenHandle(fd, observation, self)
        except Exception:
            os.close(fd)
            raise

    def observe(self, handle: OpenHandle) -> HandleObservation:
        return self._observation(handle.raw_handle)

    def close_handle(self, raw_handle: int) -> None:
        os.close(raw_handle)

    def read_chunks(self, handle: OpenHandle, *, chunk_size: int, max_bytes: int) -> Iterator[bytes]:
        total = 0
        while total < max_bytes:
            chunk = os.read(handle.raw_handle, min(chunk_size, max_bytes - total))
            if not chunk:
                return
            total += len(chunk)
            yield chunk


# Windows declarations are kept explicit and covered by the ABI checkpoint.
FILE_LIST_DIRECTORY = 0x0001
FILE_READ_DATA = 0x0001
FILE_READ_ATTRIBUTES = 0x0080
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
FILE_ID_INFO_CLASS = 0x12
FILE_ID_EXTD_DIRECTORY_INFO_CLASS = 0x13
FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS = 0x14
FILE_BASIC_INFO_CLASS = 0
FILE_STANDARD_INFO_CLASS = 1
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
ERROR_NO_MORE_FILES = 18
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
OBJ_CASE_INSENSITIVE = 0x40
OBJ_DONT_REPARSE = 0x1000
FILE_OPEN = 1
FILE_SYNCHRONOUS_IO_NONALERT = 0x20
FILE_NON_DIRECTORY_FILE = 0x40
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_OPEN_NO_RECALL = 0x00400000


class FILE_ID_128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]


class FILE_ID_INFO(ctypes.Structure):
    _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", FILE_ID_128)]


class FILE_BASIC_INFO(ctypes.Structure):
    _fields_ = [("CreationTime", ctypes.c_longlong), ("LastAccessTime", ctypes.c_longlong), ("LastWriteTime", ctypes.c_longlong), ("ChangeTime", ctypes.c_longlong), ("FileAttributes", wintypes.DWORD)]


class FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = [("AllocationSize", ctypes.c_longlong), ("EndOfFile", ctypes.c_longlong), ("NumberOfLinks", wintypes.DWORD), ("DeletePending", ctypes.c_ubyte), ("Directory", ctypes.c_ubyte)]


class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


class FILE_ID_EXTD_DIR_INFO(ctypes.Structure):
    _fields_ = [("NextEntryOffset", wintypes.ULONG), ("FileIndex", wintypes.ULONG), ("CreationTime", ctypes.c_longlong), ("LastAccessTime", ctypes.c_longlong), ("LastWriteTime", ctypes.c_longlong), ("ChangeTime", ctypes.c_longlong), ("EndOfFile", ctypes.c_longlong), ("AllocationSize", ctypes.c_longlong), ("FileAttributes", wintypes.ULONG), ("FileNameLength", wintypes.ULONG), ("EaSize", wintypes.ULONG), ("ReparsePointTag", wintypes.ULONG), ("FileId", FILE_ID_128), ("FileName", wintypes.WCHAR * 1)]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE), ("ObjectName", ctypes.POINTER(UNICODE_STRING)), ("Attributes", wintypes.ULONG), ("SecurityDescriptor", wintypes.LPVOID), ("SecurityQualityOfService", wintypes.LPVOID)]


class IO_STATUS_BLOCK_UNION(ctypes.Union):
    _fields_ = [("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID)]


class IO_STATUS_BLOCK(ctypes.Structure):
    _anonymous_ = ("Result",)
    _fields_ = [("Result", IO_STATUS_BLOCK_UNION), ("Information", ctypes.c_size_t)]


class WindowsHandleBackend(BaseHandleBackend):
    BUFFER_SIZE = 4096

    def __init__(self) -> None:
        if os.name != "nt":
            raise SourceBackendError("windows_handle_capability_unsupported")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll")
        self._configure()

    def _configure(self) -> None:
        self.kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.GetFileInformationByHandleEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        self.kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.ReadFile.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
        self.kernel32.ReadFile.restype = wintypes.BOOL
        self.ntdll.NtCreateFile.argtypes = (ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(OBJECT_ATTRIBUTES), ctypes.POINTER(IO_STATUS_BLOCK), ctypes.POINTER(ctypes.c_longlong), wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG)
        self.ntdll.NtCreateFile.restype = wintypes.LONG

    def _query(self, handle: int, information_class: int, structure: type[ctypes.Structure]) -> ctypes.Structure:
        value = structure()
        if not self.kernel32.GetFileInformationByHandleEx(handle, information_class, ctypes.byref(value), ctypes.sizeof(value)):
            raise SourceBackendError("source_handle_observation_failed")
        return value

    def _observation(self, handle: int) -> HandleObservation:
        identity = self._query(handle, FILE_ID_INFO_CLASS, FILE_ID_INFO)
        basic = self._query(handle, FILE_BASIC_INFO_CLASS, FILE_BASIC_INFO)
        standard = self._query(handle, FILE_STANDARD_INFO_CLASS, FILE_STANDARD_INFO)
        tag = self._query(handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, FILE_ATTRIBUTE_TAG_INFO)
        file_id = bytes(identity.FileId.Identifier).hex()
        if not any(identity.FileId.Identifier):
            raise SourceBackendError("source_file_identity_zero")
        attributes = int(basic.FileAttributes)
        reparse = bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)
        recall = bool(attributes & (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS))
        availability = CloudAvailability.REPARSE_POINT if reparse else (CloudAvailability.RECALL_RISK if recall else CloudAvailability.AVAILABLE)
        return HandleObservation(
            object_identity=FileObjectIdentity("windows", f"{int(identity.VolumeSerialNumber):016x}", file_id),
            change_identity=FileChangeIdentity(int(basic.ChangeTime), int(basic.LastWriteTime), int(standard.EndOfFile), int(standard.AllocationSize)),
            cloud_availability=availability,
            attributes_known=True,
            is_directory=bool(standard.Directory),
            reparse_point=reparse,
            reparse_tag=int(tag.ReparseTag),
            link_count=int(standard.NumberOfLinks),
            no_follow=True,
            identity_bound=True,
            no_recall_open_only=True,
        )

    def open_directory(self, path: Path) -> OpenHandle:
        handle = self.kernel32.CreateFileW(os.fspath(path), FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None)
        if handle == INVALID_HANDLE_VALUE:
            raise SourceBackendError("source_directory_open_failed")
        try:
            observation = self._observation(handle)
            if not observation.is_directory or observation.reparse_point:
                raise SourceBackendError("source_directory_rejected")
            return OpenHandle(int(handle), observation, self)
        except Exception:
            self.close_handle(int(handle))
            raise

    def _page(self, handle: int, restart: bool) -> tuple[DirectoryMember, ...] | None:
        buffer = ctypes.create_string_buffer(self.BUFFER_SIZE)
        info_class = FILE_ID_EXTD_DIRECTORY_RESTART_INFO_CLASS if restart else FILE_ID_EXTD_DIRECTORY_INFO_CLASS
        ctypes.set_last_error(0)
        if not self.kernel32.GetFileInformationByHandleEx(handle, info_class, buffer, ctypes.sizeof(buffer)):
            if ctypes.get_last_error() == ERROR_NO_MORE_FILES:
                return None
            raise SourceBackendError("same_handle_enumeration_failed")
        records: list[DirectoryMember] = []
        base = ctypes.addressof(buffer)
        offset = 0
        while True:
            if offset + FILE_ID_EXTD_DIR_INFO.FileName.offset > ctypes.sizeof(buffer):
                raise SourceBackendError("same_handle_record_out_of_bounds")
            record = FILE_ID_EXTD_DIR_INFO.from_address(base + offset)
            end = offset + FILE_ID_EXTD_DIR_INFO.FileName.offset + record.FileNameLength
            if record.FileNameLength % 2 or end > ctypes.sizeof(buffer):
                raise SourceBackendError("same_handle_record_out_of_bounds")
            try:
                name = ctypes.string_at(base + offset + FILE_ID_EXTD_DIR_INFO.FileName.offset, record.FileNameLength).decode("utf-16-le", errors="strict")
            except UnicodeDecodeError as exc:
                raise SourceBackendError("source_member_name_invalid") from exc
            # NT directory queries expose the structural ``.``/``..`` entries.
            # They are never admitted as members or passed to relative open.
            if name not in {".", ".."}:
                _validate_member_name(name)
                records.append(DirectoryMember(name, FileObjectIdentity("windows", "pending-volume", bytes(record.FileId.Identifier).hex()), int(record.FileAttributes), int(record.ReparsePointTag)))
            if record.NextEntryOffset == 0:
                break
            if record.NextEntryOffset < FILE_ID_EXTD_DIR_INFO.FileName.offset:
                raise SourceBackendError("same_handle_record_out_of_bounds")
            offset += record.NextEntryOffset
            if offset >= ctypes.sizeof(buffer):
                raise SourceBackendError("same_handle_record_out_of_bounds")
        return tuple(records)

    def enumerate_directory(self, directory: OpenHandle) -> tuple[DirectoryMember, ...]:
        before = self.observe(directory)
        members: list[DirectoryMember] = []
        page = self._page(directory.raw_handle, True)
        while page is not None:
            for member in page:
                members.append(DirectoryMember(member.name, FileObjectIdentity("windows", before.object_identity.volume_id, member.object_identity.file_id), member.attributes, member.reparse_tag))
            page = self._page(directory.raw_handle, False)
        after = self.observe(directory)
        if before.object_identity != after.object_identity:
            raise SourceBackendError("source_directory_identity_drift")
        if any(member.attributes & FILE_ATTRIBUTE_REPARSE_POINT or member.reparse_tag for member in members):
            raise SourceBackendError("source_reparse_point_rejected")
        identities = [member.object_identity for member in members]
        if len(set(identities)) != len(identities):
            raise SourceBackendError("source_alias_identity_rejected")
        if len({member.name.casefold() for member in members}) != len(members):
            raise SourceBackendError("source_name_collision_rejected")
        return tuple(members)

    def open_child(self, directory: OpenHandle, member: DirectoryMember) -> OpenHandle:
        _validate_member_name(member.name)
        name_buffer = ctypes.create_unicode_buffer(member.name)
        encoded_length = len(member.name.encode("utf-16-le"))
        unicode_name = UNICODE_STRING(encoded_length, encoded_length + 2, ctypes.cast(name_buffer, wintypes.LPWSTR))
        attributes = OBJECT_ATTRIBUTES(ctypes.sizeof(OBJECT_ATTRIBUTES), directory.raw_handle, ctypes.pointer(unicode_name), OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE, None, None)
        io_status = IO_STATUS_BLOCK()
        child = wintypes.HANDLE()
        status = int(self.ntdll.NtCreateFile(ctypes.byref(child), FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE, ctypes.byref(attributes), ctypes.byref(io_status), None, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, FILE_OPEN, FILE_SYNCHRONOUS_IO_NONALERT | FILE_NON_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT | FILE_OPEN_NO_RECALL, None, 0))
        if status < 0 or not child.value:
            raise SourceBackendError("source_child_open_failed")
        handle = int(child.value)
        try:
            observation = self._observation(handle)
            if observation.object_identity != member.object_identity:
                raise SourceBackendError("source_child_identity_mismatch")
            if observation.reparse_point:
                raise SourceBackendError("source_reparse_point_rejected")
            if observation.link_count != 1:
                raise SourceBackendError("source_hard_link_rejected")
            return OpenHandle(handle, observation, self)
        except Exception:
            self.close_handle(handle)
            raise

    def observe(self, handle: OpenHandle) -> HandleObservation:
        return self._observation(handle.raw_handle)

    def close_handle(self, raw_handle: int) -> None:
        if not self.kernel32.CloseHandle(raw_handle):
            raise SourceBackendError("source_handle_close_failed")

    def read_chunks(self, handle: OpenHandle, *, chunk_size: int, max_bytes: int) -> Iterator[bytes]:
        total = 0
        while total < max_bytes:
            requested = min(chunk_size, max_bytes - total)
            buffer = ctypes.create_string_buffer(requested)
            read = wintypes.DWORD()
            if not self.kernel32.ReadFile(handle.raw_handle, buffer, requested, ctypes.byref(read), None):
                raise SourceBackendError("source_handle_read_failed")
            if read.value == 0:
                return
            total += int(read.value)
            yield bytes(buffer.raw[: read.value])


def current_handle_backend() -> BaseHandleBackend:
    return WindowsHandleBackend() if os.name == "nt" else PosixHandleBackend()
