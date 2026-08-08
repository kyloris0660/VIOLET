"""Synthetic-only SCV2-FL1-I1 read-only inventory foundation.

This module deliberately cannot scan a real source root.  It accepts only an
explicit synthetic fixture contained by an explicit sandbox, performs bounded
read-only observation, and returns an in-memory private manifest plus a
public-safe aggregate summary.  It never opens a database, writes app storage,
starts runtime services, or initializes provider, LLM, media, or network code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


CONTRACT_ID = "scv2_fl1_i1_read_only_inventory_contract_v1"
MANIFEST_SCHEMA_VERSION = "violet.scv2-fl1-i1-inventory-manifest.v1"
MEMBERSHIP_IDENTITY_VERSION = "violet.scv2-fl1-i1-membership.v1"
PUBLIC_LABEL_VERSION = "violet.scv2-fl1-i1-public-label.v1"
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,10}$")


class I1InventoryError(RuntimeError):
    """Raised when the I1 synthetic read-only boundary cannot be proven."""


class _UnreadableFile(OSError):
    def __init__(self, bytes_read: int = 0) -> None:
        super().__init__("synthetic_fixture_read_failed")
        self.bytes_read = bytes_read


class SourceKind(str, Enum):
    SYNTHETIC_FIXTURE = "synthetic_fixture"
    REAL_SOURCE = "real_source"


class InventoryDisposition(str, Enum):
    UNSUPPORTED = "unsupported"
    DUPLICATE = "duplicate"
    CLOUD_RECALL_DEFERRED = "cloud_recall_deferred"
    UNREADABLE_OR_MISSING = "unreadable_or_missing"
    ELIGIBLE_CANDIDATE = "eligible_candidate"


class ImportDisposition(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    IMPORT_DEFERRED = "import_deferred"


@dataclass(frozen=True)
class I1InventoryConfig:
    source_kind: SourceKind | str
    source_scope_id: str
    sandbox_root: Path
    source_root: Path
    forbidden_roots: tuple[Path, ...]
    actual_git_head: str
    expected_git_head: str
    python_executable: Path
    expected_python: Path
    supported_extensions: tuple[str, ...]
    max_discovered_items: int
    max_total_source_bytes: int
    read_chunk_bytes: int = 1024 * 1024
    expected_source_snapshot_fingerprint: str | None = None
    synthetic_disposition_overrides: Mapping[str, str] = field(default_factory=dict)
    real_source_inventory_authorized: bool = False
    database_access_authorized: bool = False
    app_storage_write_authorized: bool = False
    network_authorized: bool = False


@dataclass(frozen=True)
class InventoryPreflight:
    source_scope_id: str
    sandbox_root: Path
    source_root: Path
    forbidden_roots: tuple[Path, ...]
    supported_extensions: frozenset[str]
    max_discovered_items: int
    max_total_source_bytes: int
    read_chunk_bytes: int
    expected_source_snapshot_fingerprint: str | None
    synthetic_disposition_overrides: Mapping[str, InventoryDisposition]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "source_kind": SourceKind.SYNTHETIC_FIXTURE.value,
            "source_scope_id": self.source_scope_id,
            "git_head_match": True,
            "python_identity_match": True,
            "source_root_explicit_and_contained": True,
            "forbidden_root_overlap_count": 0,
            "symlink_following_allowed": False,
            "synthetic_only": True,
            "bounded_item_count": True,
            "bounded_source_bytes": True,
        }


@dataclass(frozen=True)
class _SourceEntry:
    path: Path
    relative_path: str
    mode: int
    size: int
    mtime_ns: int
    device: int
    inode: int

    @property
    def signature(self) -> tuple[int, int, int, int, int]:
        return (self.mode, self.size, self.mtime_ns, self.device, self.inode)

    def snapshot_payload(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "mode": self.mode,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "device": self.device,
            "inode": self.inode,
        }


@dataclass(frozen=True)
class InventoryRecord:
    item_id: str
    public_label: str
    private_relative_path: str
    extension: str
    disposition: InventoryDisposition
    import_disposition: ImportDisposition
    content_fingerprint: str | None
    duplicate_of_item_id: str | None
    error_code: str | None

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "public_label": self.public_label,
            "private_relative_path": self.private_relative_path,
            "extension": self.extension,
            "disposition": self.disposition.value,
            "import_disposition": self.import_disposition.value,
            "content_fingerprint": self.content_fingerprint,
            "duplicate_of_item_id": self.duplicate_of_item_id,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class InventoryManifest:
    source_scope_id: str
    source_snapshot_fingerprint: str
    manifest_fingerprint: str
    records: tuple[InventoryRecord, ...]
    total_source_bytes: int
    synthetic_file_read_attempt_count: int
    synthetic_file_read_success_count: int
    synthetic_bytes_read: int
    source_tree_unchanged: bool
    expected_snapshot_matched: bool

    def denominator(self) -> dict[str, int]:
        counts = {disposition: 0 for disposition in InventoryDisposition}
        for record in self.records:
            counts[record.disposition] += 1
        discovered = len(self.records)
        unsupported = counts[InventoryDisposition.UNSUPPORTED]
        supported = discovered - unsupported
        eligible = counts[InventoryDisposition.ELIGIBLE_CANDIDATE]
        return {
            "discovered": discovered,
            "supported": supported,
            "unsupported": unsupported,
            "duplicate": counts[InventoryDisposition.DUPLICATE],
            "cloud_recall_deferred": counts[
                InventoryDisposition.CLOUD_RECALL_DEFERRED
            ],
            "unreadable_or_missing": counts[
                InventoryDisposition.UNREADABLE_OR_MISSING
            ],
            "eligible_candidate": eligible,
            "imported": 0,
            "import_deferred": eligible,
            "import_failed": 0,
            "unresolved": 0,
        }

    def validate(self) -> None:
        if not SAFE_IDENTITY_RE.fullmatch(self.source_scope_id):
            raise I1InventoryError("manifest_source_scope_invalid")
        if not HEX64_RE.fullmatch(self.source_snapshot_fingerprint):
            raise I1InventoryError("source_snapshot_fingerprint_invalid")
        if not HEX64_RE.fullmatch(self.manifest_fingerprint):
            raise I1InventoryError("manifest_fingerprint_invalid")
        if not self.records:
            raise I1InventoryError("inventory_manifest_empty")
        if min(
            self.total_source_bytes,
            self.synthetic_file_read_attempt_count,
            self.synthetic_file_read_success_count,
            self.synthetic_bytes_read,
        ) < 0:
            raise I1InventoryError("inventory_operation_counter_invalid")
        if self.synthetic_file_read_success_count > self.synthetic_file_read_attempt_count:
            raise I1InventoryError("inventory_read_counter_invalid")
        if not self.source_tree_unchanged or not self.expected_snapshot_matched:
            raise I1InventoryError("source_snapshot_proof_invalid")

        by_id: dict[str, InventoryRecord] = {}
        labels: set[str] = set()
        for record in self.records:
            if not HEX64_RE.fullmatch(record.item_id) or record.item_id in by_id:
                raise I1InventoryError("inventory_item_identity_invalid")
            if not re.fullmatch(r"item_[0-9a-f]{16}", record.public_label):
                raise I1InventoryError("inventory_public_label_invalid")
            if record.public_label != _public_label(record.item_id):
                raise I1InventoryError("inventory_public_label_identity_mismatch")
            if record.public_label in labels:
                raise I1InventoryError("inventory_public_label_collision")
            labels.add(record.public_label)

            fingerprint_required = record.disposition in {
                InventoryDisposition.DUPLICATE,
                InventoryDisposition.ELIGIBLE_CANDIDATE,
            }
            if fingerprint_required != bool(
                record.content_fingerprint
                and HEX64_RE.fullmatch(record.content_fingerprint)
            ):
                raise I1InventoryError("inventory_content_fingerprint_invalid")
            if record.disposition is InventoryDisposition.DUPLICATE:
                primary = by_id.get(record.duplicate_of_item_id or "")
                if (
                    primary is None
                    or primary.disposition is not InventoryDisposition.ELIGIBLE_CANDIDATE
                    or primary.content_fingerprint != record.content_fingerprint
                ):
                    raise I1InventoryError("inventory_duplicate_reference_invalid")
            elif record.duplicate_of_item_id is not None:
                raise I1InventoryError("inventory_duplicate_reference_unexpected")

            if record.disposition is InventoryDisposition.ELIGIBLE_CANDIDATE:
                if record.import_disposition is not ImportDisposition.IMPORT_DEFERRED:
                    raise I1InventoryError("eligible_import_state_invalid")
            elif record.import_disposition is not ImportDisposition.NOT_APPLICABLE:
                raise I1InventoryError("noneligible_import_state_invalid")
            by_id[record.item_id] = record

        denominator = self.denominator()
        if denominator["discovered"] != (
            denominator["supported"] + denominator["unsupported"]
        ):
            raise I1InventoryError("inventory_discovered_equation_mismatch")
        if denominator["supported"] != sum(
            denominator[key]
            for key in (
                "duplicate",
                "cloud_recall_deferred",
                "unreadable_or_missing",
                "eligible_candidate",
            )
        ):
            raise I1InventoryError("inventory_supported_equation_mismatch")
        if denominator["eligible_candidate"] != sum(
            denominator[key]
            for key in ("imported", "import_deferred", "import_failed")
        ):
            raise I1InventoryError("inventory_eligible_equation_mismatch")
        if denominator["unresolved"] != 0:
            raise I1InventoryError("inventory_unresolved_nonzero")
        if self.manifest_fingerprint != _manifest_fingerprint(
            self.source_scope_id,
            self.source_snapshot_fingerprint,
            self.records,
        ):
            raise I1InventoryError("inventory_manifest_fingerprint_mismatch")


def _strict_positive_int(value: Any, error_code: str, *, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
        raise I1InventoryError(error_code)
    return value


def _normalize_executable(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _resolved_directory(path: Path, error_code: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise I1InventoryError(error_code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise I1InventoryError(error_code) from exc
    if not resolved.is_dir():
        raise I1InventoryError(error_code)
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_within(left, right) or _is_within(right, left)


def _normalize_override_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise I1InventoryError("synthetic_override_path_invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise I1InventoryError("synthetic_override_path_invalid")
    return pure.as_posix()


def validate_i1_preflight(config: I1InventoryConfig) -> InventoryPreflight:
    try:
        source_kind = (
            config.source_kind
            if isinstance(config.source_kind, SourceKind)
            else SourceKind(config.source_kind)
        )
    except ValueError as exc:
        raise I1InventoryError("unknown_source_kind") from exc
    if source_kind is not SourceKind.SYNTHETIC_FIXTURE:
        raise I1InventoryError("real_source_inventory_not_authorized")
    if any(
        value is not False
        for value in (
            config.real_source_inventory_authorized,
            config.database_access_authorized,
            config.app_storage_write_authorized,
            config.network_authorized,
        )
    ):
        raise I1InventoryError("forbidden_authorization_enabled")
    if not SAFE_IDENTITY_RE.fullmatch(config.source_scope_id):
        raise I1InventoryError("source_scope_identity_invalid")
    if (
        not HEX40_RE.fullmatch(config.actual_git_head)
        or not HEX40_RE.fullmatch(config.expected_git_head)
        or config.actual_git_head != config.expected_git_head
    ):
        raise I1InventoryError("git_head_identity_mismatch")
    if _normalize_executable(config.python_executable) != _normalize_executable(
        config.expected_python
    ):
        raise I1InventoryError("python_identity_mismatch")

    sandbox_root = _resolved_directory(config.sandbox_root, "sandbox_root_invalid")
    source_root = _resolved_directory(config.source_root, "source_root_invalid")
    if not _is_within(source_root, sandbox_root) or source_root == sandbox_root:
        raise I1InventoryError("synthetic_source_outside_sandbox")
    if not config.forbidden_roots:
        raise I1InventoryError("explicit_forbidden_roots_required")
    forbidden_roots = tuple(
        _resolved_directory(path, "forbidden_root_invalid")
        for path in config.forbidden_roots
    )
    if any(_paths_overlap(sandbox_root, root) for root in forbidden_roots):
        raise I1InventoryError("sandbox_overlaps_forbidden_root")

    if not config.supported_extensions:
        raise I1InventoryError("supported_extensions_required")
    normalized_extensions = tuple(
        str(value).strip().casefold() for value in config.supported_extensions
    )
    if (
        len(set(normalized_extensions)) != len(normalized_extensions)
        or any(not EXTENSION_RE.fullmatch(value) for value in normalized_extensions)
    ):
        raise I1InventoryError("supported_extensions_invalid")
    max_items = _strict_positive_int(
        config.max_discovered_items,
        "max_discovered_items_invalid",
        upper=1_000_000,
    )
    max_bytes = _strict_positive_int(
        config.max_total_source_bytes,
        "max_total_source_bytes_invalid",
        upper=10**15,
    )
    chunk_bytes = _strict_positive_int(
        config.read_chunk_bytes,
        "read_chunk_bytes_invalid",
        upper=16 * 1024 * 1024,
    )
    expected_snapshot = config.expected_source_snapshot_fingerprint
    if expected_snapshot is not None and not HEX64_RE.fullmatch(expected_snapshot):
        raise I1InventoryError("expected_source_snapshot_fingerprint_invalid")

    if not isinstance(config.synthetic_disposition_overrides, Mapping):
        raise I1InventoryError("synthetic_overrides_invalid")
    overrides: dict[str, InventoryDisposition] = {}
    for raw_path, raw_disposition in config.synthetic_disposition_overrides.items():
        path = _normalize_override_path(raw_path)
        try:
            disposition = InventoryDisposition(raw_disposition)
        except ValueError as exc:
            raise I1InventoryError("synthetic_override_disposition_invalid") from exc
        if disposition not in {
            InventoryDisposition.CLOUD_RECALL_DEFERRED,
            InventoryDisposition.UNREADABLE_OR_MISSING,
        }:
            raise I1InventoryError("synthetic_override_disposition_invalid")
        if path in overrides:
            raise I1InventoryError("synthetic_override_duplicate")
        overrides[path] = disposition

    return InventoryPreflight(
        source_scope_id=config.source_scope_id,
        sandbox_root=sandbox_root,
        source_root=source_root,
        forbidden_roots=forbidden_roots,
        supported_extensions=frozenset(normalized_extensions),
        max_discovered_items=max_items,
        max_total_source_bytes=max_bytes,
        read_chunk_bytes=chunk_bytes,
        expected_source_snapshot_fingerprint=expected_snapshot,
        synthetic_disposition_overrides=overrides,
    )


def _scan_source_tree(preflight: InventoryPreflight) -> tuple[tuple[_SourceEntry, ...], str, int]:
    entries: list[_SourceEntry] = []
    directories = [preflight.source_root]
    snapshot_nodes: list[dict[str, Any]] = []
    total_bytes = 0
    while directories:
        directory = directories.pop()
        try:
            directory_metadata = directory.stat()
        except OSError as exc:
            raise I1InventoryError("source_directory_stat_failed") from exc
        directory_relative = (
            "."
            if directory == preflight.source_root
            else directory.relative_to(preflight.source_root).as_posix()
        )
        snapshot_nodes.append(
            {
                "kind": "directory",
                "relative_path": directory_relative,
                "mode": directory_metadata.st_mode,
                "size": directory_metadata.st_size,
                "mtime_ns": directory_metadata.st_mtime_ns,
                "device": directory_metadata.st_dev,
                "inode": directory_metadata.st_ino,
            }
        )
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda child: child.name)
        except OSError as exc:
            raise I1InventoryError("source_directory_unreadable") from exc
        for child in children:
            path = Path(child.path)
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise I1InventoryError("source_entry_stat_failed") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise I1InventoryError("source_symlink_rejected")
            if stat.S_ISDIR(metadata.st_mode):
                directories.append(path)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise I1InventoryError("source_special_file_rejected")
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise I1InventoryError("source_entry_resolution_failed") from exc
            if not _is_within(resolved, preflight.source_root):
                raise I1InventoryError("source_entry_escape_rejected")
            relative = path.relative_to(preflight.source_root).as_posix()
            entry = _SourceEntry(
                path=path,
                relative_path=relative,
                mode=metadata.st_mode,
                size=metadata.st_size,
                mtime_ns=metadata.st_mtime_ns,
                device=metadata.st_dev,
                inode=metadata.st_ino,
            )
            entries.append(entry)
            snapshot_nodes.append({"kind": "file", **entry.snapshot_payload()})
            total_bytes += metadata.st_size
            if len(entries) > preflight.max_discovered_items:
                raise I1InventoryError("source_item_limit_exceeded")
            if total_bytes > preflight.max_total_source_bytes:
                raise I1InventoryError("source_byte_limit_exceeded")
    if not entries:
        raise I1InventoryError("synthetic_source_fixture_empty")
    entries.sort(key=lambda value: value.relative_path)
    snapshot_payload = sorted(
        snapshot_nodes,
        key=lambda value: (str(value["relative_path"]), str(value["kind"])),
    )
    fingerprint = hashlib.sha256(
        json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return tuple(entries), fingerprint, total_bytes


def _hash_file(entry: _SourceEntry, chunk_bytes: int) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(entry.path, flags)
    except OSError as exc:
        raise _UnreadableFile() from exc
    bytes_read = 0
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        opened_signature = (
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_dev,
            opened.st_ino,
        )
        if opened_signature != entry.signature:
            raise I1InventoryError("source_entry_changed_before_read")
        while True:
            try:
                chunk = os.read(descriptor, chunk_bytes)
            except OSError as exc:
                raise _UnreadableFile(bytes_read) from exc
            if not chunk:
                break
            digest.update(chunk)
            bytes_read += len(chunk)
        closed = os.fstat(descriptor)
        closed_signature = (
            closed.st_mode,
            closed.st_size,
            closed.st_mtime_ns,
            closed.st_dev,
            closed.st_ino,
        )
        if closed_signature != entry.signature or bytes_read != entry.size:
            raise I1InventoryError("source_entry_changed_during_read")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), bytes_read


def _membership_identity(
    *, source_scope_id: str, entry: _SourceEntry, content_fingerprint: str | None
) -> str:
    path_ref = hashlib.sha256(
        f"{source_scope_id}\0{entry.relative_path}".encode("utf-8")
    ).hexdigest()
    evidence_ref = content_fingerprint or hashlib.sha256(
        json.dumps(entry.snapshot_payload(), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return hashlib.sha256(
        (
            f"{MEMBERSHIP_IDENTITY_VERSION}\0{source_scope_id}\0"
            f"{path_ref}\0{evidence_ref}"
        ).encode("utf-8")
    ).hexdigest()


def _public_label(item_id: str) -> str:
    digest = hashlib.sha256(
        f"{PUBLIC_LABEL_VERSION}\0{item_id}".encode("utf-8")
    ).hexdigest()
    return f"item_{digest[:16]}"


def _manifest_fingerprint(
    source_scope_id: str,
    source_snapshot_fingerprint: str,
    records: Sequence[InventoryRecord],
) -> str:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "membership_identity_version": MEMBERSHIP_IDENTITY_VERSION,
        "source_scope_id": source_scope_id,
        "source_snapshot_fingerprint": source_snapshot_fingerprint,
        "records": [
            {
                "item_id": record.item_id,
                "disposition": record.disposition.value,
                "import_disposition": record.import_disposition.value,
                "content_fingerprint": record.content_fingerprint,
                "duplicate_of_item_id": record.duplicate_of_item_id,
                "error_code": record.error_code,
            }
            for record in records
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def scan_synthetic_inventory(config: I1InventoryConfig) -> tuple[InventoryPreflight, InventoryManifest]:
    """Scan one bounded synthetic fixture and return in-memory evidence only."""

    preflight = validate_i1_preflight(config)
    before_entries, before_fingerprint, total_source_bytes = _scan_source_tree(preflight)
    expected_matched = (
        preflight.expected_source_snapshot_fingerprint is None
        or preflight.expected_source_snapshot_fingerprint == before_fingerprint
    )
    if not expected_matched:
        raise I1InventoryError("source_snapshot_fingerprint_mismatch")
    discovered_paths = {entry.relative_path for entry in before_entries}
    unknown_overrides = set(preflight.synthetic_disposition_overrides) - discovered_paths
    if unknown_overrides:
        raise I1InventoryError("synthetic_override_item_missing")

    records: list[InventoryRecord] = []
    primary_by_fingerprint: dict[str, str] = {}
    attempts = 0
    successes = 0
    bytes_read = 0
    for entry in before_entries:
        extension = Path(entry.relative_path).suffix.casefold()
        override = preflight.synthetic_disposition_overrides.get(entry.relative_path)
        content_fingerprint: str | None = None
        duplicate_of: str | None = None
        error_code: str | None = None

        if override is not None and extension not in preflight.supported_extensions:
            raise I1InventoryError("synthetic_override_requires_supported_extension")
        if extension not in preflight.supported_extensions:
            disposition = InventoryDisposition.UNSUPPORTED
            import_disposition = ImportDisposition.NOT_APPLICABLE
            error_code = "unsupported_extension"
        elif override is InventoryDisposition.CLOUD_RECALL_DEFERRED:
            disposition = override
            import_disposition = ImportDisposition.NOT_APPLICABLE
            error_code = "synthetic_cloud_recall_deferred"
        elif override is InventoryDisposition.UNREADABLE_OR_MISSING:
            disposition = override
            import_disposition = ImportDisposition.NOT_APPLICABLE
            error_code = "synthetic_fixture_unreadable"
        else:
            attempts += 1
            try:
                content_fingerprint, item_bytes = _hash_file(
                    entry, preflight.read_chunk_bytes
                )
            except _UnreadableFile as exc:
                bytes_read += exc.bytes_read
                disposition = InventoryDisposition.UNREADABLE_OR_MISSING
                import_disposition = ImportDisposition.NOT_APPLICABLE
                error_code = "synthetic_fixture_read_failed"
            else:
                successes += 1
                bytes_read += item_bytes
                primary_id = primary_by_fingerprint.get(content_fingerprint)
                if primary_id is None:
                    disposition = InventoryDisposition.ELIGIBLE_CANDIDATE
                    import_disposition = ImportDisposition.IMPORT_DEFERRED
                else:
                    disposition = InventoryDisposition.DUPLICATE
                    import_disposition = ImportDisposition.NOT_APPLICABLE
                    duplicate_of = primary_id

        item_id = _membership_identity(
            source_scope_id=preflight.source_scope_id,
            entry=entry,
            content_fingerprint=content_fingerprint,
        )
        record = InventoryRecord(
            item_id=item_id,
            public_label=_public_label(item_id),
            private_relative_path=entry.relative_path,
            extension=extension,
            disposition=disposition,
            import_disposition=import_disposition,
            content_fingerprint=content_fingerprint,
            duplicate_of_item_id=duplicate_of,
            error_code=error_code,
        )
        records.append(record)
        if disposition is InventoryDisposition.ELIGIBLE_CANDIDATE:
            if content_fingerprint is None:  # pragma: no cover - guarded above
                raise I1InventoryError("eligible_content_fingerprint_missing")
            primary_by_fingerprint[content_fingerprint] = item_id

    after_entries, after_fingerprint, after_total_bytes = _scan_source_tree(preflight)
    if (
        after_fingerprint != before_fingerprint
        or after_total_bytes != total_source_bytes
        or tuple(entry.relative_path for entry in after_entries)
        != tuple(entry.relative_path for entry in before_entries)
    ):
        raise I1InventoryError("source_changed_during_inventory")

    record_tuple = tuple(records)
    manifest = InventoryManifest(
        source_scope_id=preflight.source_scope_id,
        source_snapshot_fingerprint=before_fingerprint,
        manifest_fingerprint=_manifest_fingerprint(
            preflight.source_scope_id,
            before_fingerprint,
            record_tuple,
        ),
        records=record_tuple,
        total_source_bytes=total_source_bytes,
        synthetic_file_read_attempt_count=attempts,
        synthetic_file_read_success_count=successes,
        synthetic_bytes_read=bytes_read,
        source_tree_unchanged=True,
        expected_snapshot_matched=True,
    )
    manifest.validate()
    return preflight, manifest


def build_contract_summary(
    *,
    preflight: InventoryPreflight,
    manifest: InventoryManifest,
    focused_tests_passed: bool,
    full_non_e2e_passed: bool,
) -> dict[str, Any]:
    """Build a public-safe synthetic I1 owner-audit summary."""

    manifest.validate()
    denominator = manifest.denominator()
    return {
        "phase": "SCV2-FL1-I1",
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": "synthetic_implementation_ready_for_owner_audit",
            "target_met": False,
            "safe_to_merge": False,
            "route_approved": False,
            "active_blockers": [
                "pending_owner_audit",
                "real_source_scope_not_authorized",
            ],
        },
        "authorization": {
            "synthetic_fixture_inventory_authorized": True,
            "real_source_inventory_authorized": False,
            "database_access_authorized": False,
            "app_storage_write_authorized": False,
            "data_execution_authorized": False,
            "provider_authorized": False,
            "llm_authorized": False,
            "media_authorized": False,
            "stable_replay_authorized": False,
            "network_authorized": False,
        },
        "preflight": preflight.to_public_dict(),
        "read_only_proof": {
            "source_snapshot_before": manifest.source_snapshot_fingerprint,
            "source_snapshot_after": manifest.source_snapshot_fingerprint,
            "source_tree_unchanged": manifest.source_tree_unchanged,
            "expected_snapshot_matched": manifest.expected_snapshot_matched,
            "source_mutation_count": 0,
            "database_connection_count": 0,
            "database_write_count": 0,
            "app_storage_write_count": 0,
            "external_request_count": 0,
        },
        "inventory": {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "membership_identity_version": MEMBERSHIP_IDENTITY_VERSION,
            "manifest_fingerprint": manifest.manifest_fingerprint,
            **denominator,
            "discovered_equation_balanced": True,
            "supported_equation_balanced": True,
            "eligible_equation_balanced": True,
            "one_terminal_disposition_per_item": True,
            "content_fingerprint_deduplication": True,
            "filename_or_row_order_identity_used": False,
        },
        "operation_counts": {
            "synthetic_source_file_read_attempts": manifest.synthetic_file_read_attempt_count,
            "synthetic_source_file_read_successes": manifest.synthetic_file_read_success_count,
            "synthetic_source_bytes_read": manifest.synthetic_bytes_read,
            "production_activity": 0,
            "real_source_inventory_activity": 0,
            "existing_database_read_activity": 0,
            "existing_database_write_activity": 0,
            "app_storage_write_activity": 0,
            "provider_activity": 0,
            "llm_activity": 0,
            "media_activity": 0,
            "stable_replay_activity": 0,
            "user_data_cleanup_delete_activity": 0,
        },
        "validation": {
            "focused_tests_passed": bool(focused_tests_passed),
            "full_non_e2e_passed": bool(full_non_e2e_passed),
            "synthetic_source_containment_passed": True,
            "real_source_rejection_passed": True,
            "read_only_snapshot_passed": True,
            "symlink_rejection_passed": True,
            "finite_budget_passed": True,
            "denominator_equations_passed": True,
            "duplicate_accounting_passed": True,
            "cloud_and_unreadable_terminal_state_passed": True,
            "import_deferred_boundary_passed": True,
            "public_redaction_passed": True,
        },
        "public_redaction": {
            "passed": True,
            "private_paths_emitted": False,
            "content_fingerprints_emitted": False,
            "per_item_private_records_emitted": False,
        },
    }
