#!/usr/bin/env python3
"""Phase 3.8d-I3 recovery planning for partial staging and cloud hydration.

By default this script is metadata/report-only. It inspects the preserved
partial Phase 3.8d staging target, produces privacy-safe cleanup planning
reports, and documents the controlled read-probe/hydration and same-bucket
backfill policies required before Phase 3.8d execute can be retried.

Cleanup execution is available only through the reviewed I4a path and requires
``--execute-cleanup`` plus the exact confirmation phrase after the same
manifest/filesystem proof passes. Read-probe/hydration remains out of scope.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from app.services.classification_first_workflow import (  # noqa: E402
    find_privacy_leaks,
    sanitize_public_obj,
)

from audit_cloud_availability import (  # noqa: E402
    CLEANUP_CONFIRM_PHRASE,
    plan_same_bucket_backfill,
    read_manifest,
)


DEFAULT_MANIFEST = REPO_ROOT / ".local_manifests" / "phase-3.8c-medium-candidate-manifest.csv"
DEFAULT_RECOVERY_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i3-recovery-plan.md"
DEFAULT_CLEANUP_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i3-partial-staging-cleanup-dry-run.md"
DEFAULT_CLEANUP_REPORT_JSON = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i3-partial-staging-cleanup-dry-run-summary.json"
DEFAULT_LOCAL_DETAILS_JSON = REPO_ROOT / ".local_manifests" / "phase-3.8d-i3-recovery-local-details.json"
DEFAULT_STAGING_LOG = REPO_ROOT / ".local_manifests" / "phase-3.8d-staging-copy.log"
DEFAULT_EXPECTED_FILE_COUNT = 97
DEFAULT_EXPECTED_TOTAL_BYTES = 340_159_586
DEFAULT_SELECTED_TOTAL = 1000
DEFAULT_FAILED_ROW_ID = 98
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
COPY_RUN_HEADING_RE = re.compile(r"^\s*===\s*Executing Copy\s*===\s*$", re.IGNORECASE)
TARGET_RE = re.compile(r"^\s*Target:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
EXPECTED_FILES_RE = re.compile(r"^\s*Expected files:\s*(?P<value>\d+)\s*$", re.IGNORECASE)
FILES_COPIED_RE = re.compile(r"^\s*Files copied:\s*(?P<value>\d+)\s*$", re.IGNORECASE)
BYTES_COPIED_RE = re.compile(
    r"^\s*Bytes copied:\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT]?B)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProtectedRoot:
    label: str
    path: Path


@dataclass(frozen=True)
class ProtectedRootCheck:
    label: str
    exists: bool
    is_directory: bool
    can_resolve: bool
    overlaps_target: bool
    resolved_path: Path | None


@dataclass(frozen=True)
class StagingLogEntry:
    target: Path | None
    expected_files: int | None
    files_copied: int | None
    bytes_copied: int | None
    bytes_copied_tolerance: int | None


@dataclass(frozen=True)
class StagingLogMatch:
    log_present: bool
    log_matches: bool
    target_exact_match: bool
    expected_count_matches: bool
    files_copied_matches: bool | None
    bytes_copied_matches: bool | None
    files_copied_present: bool
    bytes_copied_present: bool
    matching_entry_found: bool
    entry_count: int
    relative_target_handling: str


@dataclass(frozen=True)
class ExpectedStagingFile:
    relative_key: str
    relative_label: str
    size_bytes: int | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_inside_or_same(child: Path, parent: Path) -> bool:
    try:
        resolved_child = child.resolve()
        resolved_parent = parent.resolve()
        return resolved_child == resolved_parent or resolved_child.is_relative_to(resolved_parent)
    except (OSError, RuntimeError, ValueError):
        return False


def _safe_public_bool(value: bool) -> bool:
    return bool(value)


def parse_protected_root(raw: str) -> ProtectedRoot:
    """Parse LABEL=PATH protected root arguments without exposing paths publicly."""
    if "=" not in raw:
        raise argparse.ArgumentTypeError("protected roots must use LABEL=PATH")
    label, path_text = raw.split("=", 1)
    label = label.strip()
    if not label:
        raise argparse.ArgumentTypeError("protected root label must not be blank")
    if not label.replace("_", "").replace("-", "").isalnum():
        raise argparse.ArgumentTypeError("protected root label must be alphanumeric plus _ or -")
    path_text = path_text.strip()
    if not path_text:
        raise argparse.ArgumentTypeError("protected root path must not be blank")
    return ProtectedRoot(label=label, path=Path(path_text))


def _iter_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted([path for path in root.rglob("*") if path.is_file()], key=lambda path: str(path).lower())


def _iter_entries(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(root.rglob("*"), key=lambda path: str(path).lower())


def _entry_has_reparse_point(path: Path) -> bool:
    try:
        attrs = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def _find_escape_risk_entries(root: Path) -> list[Path]:
    risks = []
    for entry in _iter_entries(root):
        if entry.is_symlink() or _entry_has_reparse_point(entry):
            risks.append(entry)
    return risks


def _safe_entry_labels(paths: Sequence[Path]) -> list[str]:
    return [
        f"staging_entry_{index:04d}{path.suffix.lower() or '.unknown'}"
        for index, path in enumerate(paths[:10], start=1)
    ]


def _normalize_path(path: Path, *, base: Path | None = None) -> Path | None:
    if not path.is_absolute():
        if base is None:
            return None
        path = base / path
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return path


def _path_key(path: Path, *, base: Path | None = None) -> str | None:
    normalized = _normalize_path(path, base=base)
    if normalized is None:
        return None
    return os.path.normcase(str(normalized))


def _relative_key(path: Path) -> str:
    return os.path.normcase(path.as_posix())


def _relative_path_from_target(target_root: Path, path: Path) -> Path | None:
    normalized_target = _normalize_path(target_root)
    if normalized_target is None:
        return None
    normalized_path = _normalize_path(path, base=target_root)
    if normalized_path is None:
        return None
    try:
        return normalized_path.relative_to(normalized_target)
    except ValueError:
        return None


def _decode_text_file(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _parse_log_bytes(value: str, unit: str | None) -> tuple[int, int | None]:
    number = float(value)
    normalized_unit = (unit or "").upper()
    multipliers = {
        "": 1,
        "B": 1,
        "KB": 1024,
        "MB": 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
        "TB": 1024 * 1024 * 1024 * 1024,
    }
    multiplier = multipliers.get(normalized_unit, 1)
    bytes_value = int(round(number * multiplier))
    if normalized_unit in {"", "B"}:
        tolerance = 0
    elif "." in value:
        decimal_places = len(value.split(".", 1)[1])
        tolerance = int(round(0.5 * (10 ** -decimal_places) * multiplier)) + 1
    else:
        # Unit values without decimal precision are too ambiguous for cleanup
        # identity proof. Fail closed rather than accepting a wide tolerance.
        tolerance = None
    return bytes_value, tolerance


def _parse_staging_log_entries(text: str) -> list[StagingLogEntry]:
    entries: list[StagingLogEntry] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        if COPY_RUN_HEADING_RE.match(line):
            if current is not None:
                entries.append(
                    StagingLogEntry(
                        target=current.get("target"),
                        expected_files=current.get("expected_files"),
                        files_copied=current.get("files_copied"),
                        bytes_copied=current.get("bytes_copied"),
                        bytes_copied_tolerance=current.get("bytes_copied_tolerance"),
                    )
                )
            current = {}
            continue
        if current is None:
            continue
        if match := TARGET_RE.match(line):
            current["target"] = Path(match.group("value").strip())
            continue
        if match := EXPECTED_FILES_RE.match(line):
            current["expected_files"] = int(match.group("value"))
            continue
        if match := FILES_COPIED_RE.match(line):
            current["files_copied"] = int(match.group("value"))
            continue
        if match := BYTES_COPIED_RE.match(line):
            bytes_value, tolerance = _parse_log_bytes(match.group("value"), match.group("unit"))
            current["bytes_copied"] = bytes_value
            current["bytes_copied_tolerance"] = tolerance
            continue
    if current is not None:
        entries.append(
            StagingLogEntry(
                target=current.get("target"),
                expected_files=current.get("expected_files"),
                files_copied=current.get("files_copied"),
                bytes_copied=current.get("bytes_copied"),
                bytes_copied_tolerance=current.get("bytes_copied_tolerance"),
            )
        )
    return entries


def _staging_log_matches_target(
    log_path: Path | None,
    target_root: Path,
    *,
    expected_copy_count: int,
    expected_file_count: int,
    expected_total_bytes: int,
    relative_target_bases: Sequence[Path],
) -> StagingLogMatch:
    if log_path is None or not log_path.is_file():
        return StagingLogMatch(False, False, False, False, None, None, False, False, False, 0, "not_applicable")
    text = _decode_text_file(log_path)
    if text is None:
        return StagingLogMatch(False, False, False, False, None, None, False, False, False, 0, "not_applicable")
    entries = _parse_staging_log_entries(text)
    target_key = _path_key(target_root)
    if target_key is None:
        return StagingLogMatch(True, False, False, False, None, None, False, False, False, len(entries), "target_unresolvable")
    target_exact_match = False
    expected_count_matches = False
    files_copied_matches: bool | None = None
    bytes_copied_matches: bool | None = None
    files_copied_present = False
    bytes_copied_present = False
    matching_entry_found = False
    relative_target_seen = False
    ambiguous_relative_target_seen = False
    for entry in entries:
        if entry.target is None:
            continue
        if entry.target.is_absolute():
            entry_target_key = _path_key(entry.target)
        else:
            relative_target_seen = True
            entry_target_keys = [
                key for base in relative_target_bases if (key := _path_key(entry.target, base=base)) is not None
            ]
            ambiguous_relative_target_seen = ambiguous_relative_target_seen or not entry_target_keys
            entry_target_key = entry_target_keys[0] if entry_target_keys else None
        entry_target_matches = (
            entry_target_key == target_key
            if entry.target.is_absolute()
            else target_key in entry_target_keys
        )
        target_exact_match = target_exact_match or entry_target_matches
        if not entry_target_matches:
            continue
        entry_expected_matches = entry.expected_files == expected_copy_count
        expected_count_matches = expected_count_matches or entry_expected_matches
        if not entry_expected_matches:
            continue
        files_copied_present = entry.files_copied is not None
        bytes_copied_present = entry.bytes_copied is not None
        entry_files_copied_matches = entry.files_copied is not None and entry.files_copied == expected_file_count
        entry_bytes_copied_matches = (
            entry.bytes_copied is not None
            and entry.bytes_copied_tolerance is not None
            and abs(entry.bytes_copied - expected_total_bytes) <= entry.bytes_copied_tolerance
        )
        files_copied_matches = entry_files_copied_matches
        bytes_copied_matches = entry_bytes_copied_matches
        matching_entry_found = entry_files_copied_matches and entry_bytes_copied_matches
        if not matching_entry_found:
            continue
        return StagingLogMatch(
            True,
            True,
            True,
            True,
            files_copied_matches,
            bytes_copied_matches,
            files_copied_present,
            bytes_copied_present,
            True,
            len(entries),
            "stable_base" if relative_target_seen else "absolute_target",
        )
    relative_target_handling = "absolute_target"
    if ambiguous_relative_target_seen:
        relative_target_handling = "ambiguous_relative_target_failed_closed"
    elif relative_target_seen:
        relative_target_handling = "stable_base"
    return StagingLogMatch(
        True,
        False,
        target_exact_match,
        expected_count_matches,
        files_copied_matches,
        bytes_copied_matches,
        files_copied_present,
        bytes_copied_present,
        matching_entry_found,
        len(entries),
        relative_target_handling,
    )


def _validate_protected_root(root: ProtectedRoot, target_root: Path) -> ProtectedRootCheck:
    exists = root.path.exists()
    is_directory = root.path.is_dir()
    resolved_path: Path | None
    try:
        resolved_path = root.path.resolve(strict=True)
        can_resolve = True
    except (OSError, RuntimeError, ValueError):
        resolved_path = None
        can_resolve = False
    overlaps = (
        can_resolve
        and is_directory
        and resolved_path is not None
        and (_is_inside_or_same(target_root, resolved_path) or _is_inside_or_same(resolved_path, target_root))
    )
    return ProtectedRootCheck(
        label=root.label,
        exists=exists,
        is_directory=is_directory,
        can_resolve=can_resolve,
        overlaps_target=overlaps,
        resolved_path=resolved_path,
    )


def _is_manifest_copy_row(row: Mapping[str, str]) -> bool:
    return (row.get("selection_reason") or "").strip() in {"new_candidate", "existing_tier500"} and not (
        row.get("exclusion_reason") or ""
    ).strip()


def _parse_manifest_size(row: Mapping[str, str]) -> int | None:
    raw = (row.get("size_bytes") or "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _expected_partial_files(
    rows: Sequence[dict[str, str]] | None,
    expected_file_count: int,
    *,
    target_root: Path,
) -> tuple[dict[str, ExpectedStagingFile] | None, int, int, int]:
    if rows is None:
        return None, 0, 0, 0
    expected: dict[str, ExpectedStagingFile] = {}
    duplicate_count = 0
    invalid_target_count = 0
    for row in rows:
        if not _is_manifest_copy_row(row):
            continue
        target = (row.get("proposed_target_path") or "").strip()
        if not target:
            invalid_target_count += 1
            continue
        relative_path = _relative_path_from_target(target_root, Path(target))
        if relative_path is None:
            invalid_target_count += 1
            continue
        relative_key = _relative_key(relative_path)
        if relative_key in expected:
            duplicate_count += 1
            continue
        expected[relative_key] = ExpectedStagingFile(
            relative_key=relative_key,
            relative_label=relative_path.as_posix(),
            size_bytes=_parse_manifest_size(row),
        )
        if len(expected) >= expected_file_count:
            break
    return expected, duplicate_count, invalid_target_count, len(expected)


def build_cleanup_dry_run(
    *,
    target_root: Path,
    expected_staging_root: Path | None,
    protected_roots: Sequence[ProtectedRoot],
    expected_file_count: int,
    expected_total_bytes: int,
    expected_copy_count: int,
    execute_cleanup_requested: bool,
    confirm_cleanup: str,
    staging_log: Path | None,
    expected_manifest_rows: Sequence[dict[str, str]] | None = None,
    allow_target_equals_expected_root: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a public cleanup dry-run plan plus local full-path evidence."""
    target_exists = target_root.exists()
    target_is_dir = target_root.is_dir()
    files = _iter_files(target_root)
    escape_risk_entries = _find_escape_risk_entries(target_root)
    actual_files: dict[str, int] = {}
    actual_unkeyed_files = 0
    duplicate_actual_keys = 0
    for path in files:
        relative_path = _relative_path_from_target(target_root, path)
        if relative_path is None:
            actual_unkeyed_files += 1
            continue
        relative_key = _relative_key(relative_path)
        if relative_key in actual_files:
            duplicate_actual_keys += 1
            continue
        try:
            actual_files[relative_key] = path.stat().st_size
        except OSError:
            actual_files[relative_key] = -1
    expected_files, duplicate_expected_keys, invalid_manifest_targets, expected_manifest_count = _expected_partial_files(
        expected_manifest_rows,
        expected_file_count,
        target_root=target_root,
    )
    actual_file_keys = set(actual_files)
    expected_file_keys = set(expected_files or {})
    unexpected_keys = actual_file_keys - expected_file_keys if expected_files is not None else set()
    missing_expected_keys = expected_file_keys - actual_file_keys if expected_files is not None else set()
    size_mismatch_keys = (
        {
            key
            for key in actual_file_keys & expected_file_keys
            if expected_files
            and expected_files[key].size_bytes is not None
            and actual_files.get(key) != expected_files[key].size_bytes
        }
        if expected_files is not None
        else set()
    )
    expected_manifest_sizes_available = (
        sum(1 for item in expected_files.values() if item.size_bytes is not None) if expected_files is not None else 0
    )
    expected_manifest_total_bytes = (
        sum(item.size_bytes for item in expected_files.values() if item.size_bytes is not None)
        if expected_files is not None
        else None
    )
    manifest_filesystem_check_available = expected_files is not None
    manifest_filesystem_check_passed = (
        manifest_filesystem_check_available
        and expected_manifest_count == expected_file_count
        and not duplicate_expected_keys
        and not invalid_manifest_targets
        and not duplicate_actual_keys
        and not actual_unkeyed_files
        and not unexpected_keys
        and not missing_expected_keys
        and not size_mismatch_keys
    )
    total_bytes = 0
    stat_errors = 0
    ext_counts: Counter[str] = Counter()
    local_file_samples: list[str] = []
    for path in files:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            stat_errors += 1
        ext_counts[path.suffix.lower()] += 1
        if len(local_file_samples) < 10:
            local_file_samples.append(str(path))

    sample_safe_labels = [
        f"staging_file_{index:04d}{path.suffix.lower() or '.unknown'}"
        for index, path in enumerate(files[:10], start=1)
    ]

    protected_checks = []
    protected_root_validation = []
    unsafe_reasons: list[str] = []
    for root in protected_roots:
        check = _validate_protected_root(root, target_root)
        protected_checks.append(
            {
                "protected_label": root.label,
                "path_exists": check.exists,
                "is_directory": check.is_directory,
                "can_resolve": check.can_resolve,
                "valid_existing_directory": check.exists and check.is_directory and check.can_resolve,
                "overlaps_target": check.overlaps_target,
            }
        )
        protected_root_validation.append(check)
        if check.overlaps_target:
            unsafe_reasons.append(f"target overlaps protected root: {root.label}")

    labels = {root.label for root in protected_roots}
    required_labels_all_present = bool(labels & {"source_root", "icloud_source_root"}) and "repo_root" in labels and "app_storage_root" in labels
    relative_target_bases = []
    if expected_staging_root is not None:
        relative_target_bases.extend([expected_staging_root, expected_staging_root.parent])
    log_match = _staging_log_matches_target(
        staging_log,
        target_root,
        expected_copy_count=expected_copy_count,
        expected_file_count=expected_file_count,
        expected_total_bytes=expected_total_bytes,
        relative_target_bases=relative_target_bases,
    )
    file_count_matches = len(files) == expected_file_count
    expected_bytes_for_identity = (
        expected_manifest_total_bytes
        if expected_manifest_total_bytes is not None and expected_manifest_sizes_available == expected_file_count
        else expected_total_bytes
    )
    byte_count_matches = total_bytes == expected_bytes_for_identity
    expected_staging_root_explicit = expected_staging_root is not None
    expected_staging_root_exists = expected_staging_root.exists() if expected_staging_root is not None else False
    expected_staging_root_is_directory = expected_staging_root.is_dir() if expected_staging_root is not None else False
    target_under_expected_staging_root = (
        _is_inside_or_same(target_root, expected_staging_root) if expected_staging_root is not None else False
    )
    target_equals_expected_staging_root = False
    if expected_staging_root is not None:
        target_key = _path_key(target_root)
        expected_root_key = _path_key(expected_staging_root)
        target_equals_expected_staging_root = target_key is not None and target_key == expected_root_key
    target_location_allowed = (
        expected_staging_root_is_directory
        and target_under_expected_staging_root
        and (not target_equals_expected_staging_root or allow_target_equals_expected_root)
    )
    invalid_protected_labels = [
        check.label
        for check in protected_root_validation
        if not (check.exists and check.is_directory and check.can_resolve)
    ]
    missing_required_protected_labels = []
    if not labels & {"source_root", "icloud_source_root"}:
        missing_required_protected_labels.append("source_root_or_icloud_source_root")
    if "repo_root" not in labels:
        missing_required_protected_labels.append("repo_root")
    if "app_storage_root" not in labels:
        missing_required_protected_labels.append("app_storage_root")
    protected_roots_valid = required_labels_all_present and not invalid_protected_labels
    source_checks = [check for check in protected_checks if check["protected_label"] in {"source_root", "icloud_source_root"}]
    repo_checks = [check for check in protected_checks if check["protected_label"] == "repo_root"]
    app_storage_checks = [
        check
        for check in protected_checks
        if check["protected_label"] in {"app_storage_root", "app_media_root", "app_originals_root", "app_thumbnails_root"}
    ]
    source_check_valid = bool(source_checks) and all(check["valid_existing_directory"] for check in source_checks)
    repo_check_valid = bool(repo_checks) and all(check["valid_existing_directory"] for check in repo_checks)
    app_storage_check_valid = bool(app_storage_checks) and all(check["valid_existing_directory"] for check in app_storage_checks)
    target_is_dedicated = (
        target_exists
        and target_is_dir
        and expected_staging_root_explicit
        and target_location_allowed
        and file_count_matches
        and byte_count_matches
        and manifest_filesystem_check_passed
        and protected_roots_valid
        and not escape_risk_entries
        and not unsafe_reasons
    )
    if not log_match.log_present:
        staging_log_diagnostic_status = "unavailable"
    elif log_match.log_matches and manifest_filesystem_check_passed:
        staging_log_diagnostic_status = "matches_manifest_filesystem"
    elif log_match.log_matches:
        staging_log_diagnostic_status = "matches_but_manifest_filesystem_failed"
    else:
        staging_log_diagnostic_status = "mismatch_diagnostic_only"

    public = {
        "phase": "3.8d-I3",
        "mode": "partial_staging_cleanup_dry_run",
        "target_safe_label": "phase_3_8d_partial_staging",
        "target_exists": _safe_public_bool(target_exists),
        "target_is_directory": _safe_public_bool(target_is_dir),
        "target_is_dedicated_phase38d_target": _safe_public_bool(target_is_dedicated),
        "expected_staging_root_explicit": _safe_public_bool(expected_staging_root_explicit),
        "expected_staging_root_exists": _safe_public_bool(expected_staging_root_exists),
        "expected_staging_root_is_directory": _safe_public_bool(expected_staging_root_is_directory),
        "target_under_expected_staging_root": _safe_public_bool(target_under_expected_staging_root),
        "target_equals_expected_staging_root": _safe_public_bool(target_equals_expected_staging_root),
        "target_equals_expected_staging_root_allowed": _safe_public_bool(allow_target_equals_expected_root),
        "dedicated_target_evidence": {
            "authorization_basis": "manifest_filesystem_proof",
            "staging_copy_log_present": log_match.log_present,
            "staging_log_authorization_role": "not_used_for_cleanup_authorization",
            "staging_log_diagnostic_status": staging_log_diagnostic_status,
            "staging_copy_log_matches_target": log_match.log_matches,
            "staging_copy_log_target_exact_match": log_match.target_exact_match,
            "staging_copy_log_expected_count_correlated": log_match.expected_count_matches,
            "staging_copy_log_files_copied_present": log_match.files_copied_present,
            "staging_copy_log_bytes_copied_present": log_match.bytes_copied_present,
            "staging_copy_log_files_copied_matches": log_match.files_copied_matches,
            "staging_copy_log_bytes_copied_matches": log_match.bytes_copied_matches,
            "staging_copy_log_matching_entry_found": log_match.matching_entry_found,
            "staging_copy_log_entry_count": log_match.entry_count,
            "relative_target_handling": log_match.relative_target_handling,
            "expected_file_count_matches": file_count_matches,
            "expected_total_bytes_matches": byte_count_matches,
            "expected_total_bytes_source": "manifest" if expected_manifest_total_bytes is not None and expected_manifest_sizes_available == expected_file_count else "input",
            "manifest_filesystem_check_available": manifest_filesystem_check_available,
            "manifest_filesystem_check_passed": manifest_filesystem_check_passed,
            "expected_manifest_file_count": expected_manifest_count,
            "expected_manifest_size_available_count": expected_manifest_sizes_available,
            "expected_manifest_total_bytes": expected_manifest_total_bytes,
            "unexpected_files_check_available": manifest_filesystem_check_available,
            "unexpected_files_check_passed": manifest_filesystem_check_passed and not unexpected_keys and not missing_expected_keys,
            "unexpected_file_count": len(unexpected_keys),
            "missing_expected_file_count": len(missing_expected_keys),
            "size_mismatch_file_count": len(size_mismatch_keys),
            "duplicate_expected_target_count": duplicate_expected_keys,
            "invalid_manifest_target_count": invalid_manifest_targets,
            "duplicate_actual_target_count": duplicate_actual_keys,
            "actual_unkeyed_file_count": actual_unkeyed_files,
            "escape_hazard_entry_count": len(escape_risk_entries),
            "escape_hazard_safe_labels": _safe_entry_labels(escape_risk_entries),
        },
        "protected_root_checks": protected_checks,
        "target_is_not_source_icloud": source_check_valid and not any(check["overlaps_target"] for check in source_checks),
        "target_is_not_repo": repo_check_valid and not any(check["overlaps_target"] for check in repo_checks),
        "target_is_not_app_managed_storage": app_storage_check_valid
        and not any(check["overlaps_target"] for check in app_storage_checks),
        "required_protected_labels_present": {
            "source_or_icloud": bool(labels & {"source_root", "icloud_source_root"}),
            "repo_root": "repo_root" in labels,
            "app_storage_root": "app_storage_root" in labels,
            "app_media_root_if_provided": "app_media_root" in labels,
        },
        "invalid_protected_root_labels": invalid_protected_labels,
        "missing_required_protected_labels": missing_required_protected_labels,
        "file_count": len(files),
        "actual_copied_file_count": len(files),
        "expected_partial_file_count": expected_file_count,
        "requested_expected_copy_count": expected_copy_count,
        "expected_file_count": expected_file_count,
        "total_bytes": total_bytes,
        "expected_total_bytes": expected_bytes_for_identity,
        "input_expected_total_bytes": expected_total_bytes,
        "stat_errors": stat_errors,
        "extension_distribution": dict(sorted(ext_counts.items())),
        "sample_safe_labels": sample_safe_labels,
        "deletion_plan": {
            "dry_run_only": True,
            "actual_delete_performed": False,
            "would_delete_only_under_target_root": target_exists and target_is_dir,
            "would_delete_file_count": len(files),
            "would_delete_bytes": total_bytes,
            "execute_requested": execute_cleanup_requested,
            "execute_allowed": False,
            "confirmation_phrase_required": CLEANUP_CONFIRM_PHRASE,
            "confirmation_phrase_valid": confirm_cleanup == CLEANUP_CONFIRM_PHRASE,
            "separate_user_approval_required": True,
        },
        "unsafe_reasons": unsafe_reasons,
    }

    identity_mismatch_reasons = []
    if not public["target_exists"]:
        identity_mismatch_reasons.append("target_missing")
    if not public["target_is_directory"]:
        identity_mismatch_reasons.append("target_not_directory")
    if not public["expected_staging_root_explicit"]:
        identity_mismatch_reasons.append("expected_staging_root_missing")
    if public["expected_staging_root_explicit"] and not public["expected_staging_root_is_directory"]:
        identity_mismatch_reasons.append("expected_staging_root_not_directory")
    if not public["target_under_expected_staging_root"]:
        identity_mismatch_reasons.append("target_not_under_expected_staging_root")
    if public["target_equals_expected_staging_root"] and not public["target_equals_expected_staging_root_allowed"]:
        identity_mismatch_reasons.append("target_equals_expected_staging_root_not_allowed")
    if not file_count_matches:
        identity_mismatch_reasons.append("expected_partial_file_count_mismatch")
    if not byte_count_matches:
        identity_mismatch_reasons.append("expected_total_bytes_mismatch")
    if not manifest_filesystem_check_available:
        identity_mismatch_reasons.append("manifest_filesystem_proof_missing")
    elif not manifest_filesystem_check_passed:
        identity_mismatch_reasons.append("unexpected_or_missing_staging_files")
    if size_mismatch_keys:
        identity_mismatch_reasons.append("manifest_size_mismatch")
    if duplicate_expected_keys:
        identity_mismatch_reasons.append("duplicate_manifest_targets")
    if invalid_manifest_targets:
        identity_mismatch_reasons.append("invalid_manifest_targets")
    if duplicate_actual_keys or actual_unkeyed_files:
        identity_mismatch_reasons.append("actual_target_scan_unreliable")
    if escape_risk_entries:
        identity_mismatch_reasons.append("target_contains_symlink_or_reparse_point")
    public["identity_mismatch_reasons"] = identity_mismatch_reasons

    if unsafe_reasons:
        public["status"] = "blocked_unsafe_target"
    elif invalid_protected_labels or missing_required_protected_labels:
        public["status"] = "blocked_invalid_protected_root"
    elif not expected_staging_root_explicit:
        public["status"] = "blocked_missing_expected_staging_root"
    elif identity_mismatch_reasons:
        public["status"] = "blocked_identity_mismatch"
    else:
        public["status"] = "dry_run_passed"
    public["deletion_plan"]["execute_allowed"] = (
        execute_cleanup_requested
        and public["deletion_plan"]["confirmation_phrase_valid"]
        and public["status"] == "dry_run_passed"
    )
    public["deletion_plan"]["dry_run_only"] = not execute_cleanup_requested

    local = {
        "target_root": str(target_root),
        "expected_staging_root": str(expected_staging_root) if expected_staging_root else None,
        "protected_roots": [{"label": root.label, "path": str(root.path)} for root in protected_roots],
        "local_file_samples": local_file_samples,
        "staging_log": str(staging_log) if staging_log else None,
        "unexpected_file_keys": sorted(unexpected_keys)[:20],
        "missing_expected_file_keys": sorted(missing_expected_keys)[:20],
        "size_mismatch_file_keys": sorted(size_mismatch_keys)[:20],
        "escape_hazard_paths": [str(path) for path in escape_risk_entries[:20]],
    }
    return public, local


def _build_delete_targets(
    *,
    target_root: Path,
    expected_manifest_rows: Sequence[dict[str, str]],
    expected_file_count: int,
) -> tuple[list[tuple[Path, int]], list[str]]:
    errors: list[str] = []
    expected_files, duplicate_count, invalid_count, expected_count = _expected_partial_files(
        expected_manifest_rows,
        expected_file_count,
        target_root=target_root,
    )
    if expected_files is None:
        return [], ["manifest_filesystem_proof_missing"]
    if duplicate_count:
        errors.append("duplicate_manifest_targets")
    if invalid_count:
        errors.append("invalid_manifest_targets")
    if expected_count != expected_file_count:
        errors.append("expected_manifest_file_count_mismatch")

    delete_targets: list[tuple[Path, int]] = []
    for expected in expected_files.values():
        relative_path = Path(expected.relative_label)
        candidate = target_root / relative_path
        normalized = _normalize_path(candidate)
        if normalized is None or not _is_inside_or_same(normalized, target_root):
            errors.append("delete_target_outside_staging_root")
            continue
        if normalized == _normalize_path(target_root):
            errors.append("delete_target_is_staging_root")
            continue
        if normalized.is_symlink() or _entry_has_reparse_point(normalized):
            errors.append("delete_target_symlink_or_reparse_point")
            continue
        if not normalized.is_file():
            errors.append("delete_target_not_regular_file")
            continue
        try:
            size = normalized.stat().st_size
        except OSError:
            errors.append("delete_target_stat_failed")
            continue
        if expected.size_bytes is not None and size != expected.size_bytes:
            errors.append("delete_target_size_mismatch")
            continue
        delete_targets.append((normalized, size))
    return delete_targets, sorted(set(errors))


def execute_verified_cleanup(
    *,
    target_root: Path,
    expected_manifest_rows: Sequence[dict[str, str]],
    cleanup_dry_run: Mapping[str, Any],
    expected_file_count: int,
    expected_total_bytes: int,
    execute_cleanup_requested: bool,
    confirm_cleanup: str,
) -> dict[str, Any]:
    """Delete only verified expected staging files after a passing dry-run proof."""
    result: dict[str, Any] = {
        "mode": "controlled_partial_staging_cleanup",
        "requested": execute_cleanup_requested,
        "confirmation_phrase_required": CLEANUP_CONFIRM_PHRASE,
        "confirmation_phrase_valid": confirm_cleanup == CLEANUP_CONFIRM_PHRASE,
        "pre_cleanup_status": cleanup_dry_run.get("status"),
        "actual_delete_performed": False,
        "deleted_file_count": 0,
        "deleted_bytes": 0,
        "post_cleanup_file_count": None,
        "post_cleanup_total_bytes": None,
        "errors": [],
        "status": "not_requested",
    }
    if not execute_cleanup_requested:
        return result
    if confirm_cleanup != CLEANUP_CONFIRM_PHRASE:
        result["status"] = "blocked_bad_confirmation"
        result["errors"] = ["confirmation_phrase_mismatch"]
        return result
    if cleanup_dry_run.get("status") != "dry_run_passed":
        result["status"] = "blocked_dry_run_not_passed"
        result["errors"] = list(cleanup_dry_run.get("identity_mismatch_reasons") or []) or [
            str(cleanup_dry_run.get("status"))
        ]
        return result
    evidence = cleanup_dry_run.get("dedicated_target_evidence") or {}
    if evidence.get("escape_hazard_entry_count"):
        result["status"] = "blocked_escape_risk"
        result["errors"] = ["target_contains_symlink_or_reparse_point"]
        return result

    escape_risk_entries = _find_escape_risk_entries(target_root)
    if escape_risk_entries:
        result["status"] = "blocked_escape_risk"
        result["errors"] = ["target_contains_symlink_or_reparse_point"]
        return result

    delete_targets, target_errors = _build_delete_targets(
        target_root=target_root,
        expected_manifest_rows=expected_manifest_rows,
        expected_file_count=expected_file_count,
    )
    if target_errors:
        result["status"] = "blocked_delete_target_validation_failed"
        result["errors"] = target_errors
        return result
    pre_delete_bytes = sum(size for _path, size in delete_targets)
    if len(delete_targets) != expected_file_count:
        result["status"] = "blocked_delete_target_count_mismatch"
        result["errors"] = ["delete_target_count_mismatch"]
        return result
    if pre_delete_bytes != expected_total_bytes:
        result["status"] = "blocked_delete_target_bytes_mismatch"
        result["errors"] = ["delete_target_bytes_mismatch"]
        return result

    deleted_count = 0
    deleted_bytes = 0
    for path, size in delete_targets:
        try:
            path.unlink()
        except OSError as exc:
            result["status"] = "partial_cleanup_failed"
            result["actual_delete_performed"] = deleted_count > 0
            result["deleted_file_count"] = deleted_count
            result["deleted_bytes"] = deleted_bytes
            result["errors"] = [f"delete_failed_after_{deleted_count}_files: {exc.__class__.__name__}"]
            remaining_files = _iter_files(target_root)
            result["post_cleanup_file_count"] = len(remaining_files)
            result["post_cleanup_total_bytes"] = sum(path.stat().st_size for path in remaining_files if path.exists())
            return result
        deleted_count += 1
        deleted_bytes += size

    remaining_files = _iter_files(target_root)
    remaining_bytes = sum(path.stat().st_size for path in remaining_files if path.exists())
    result["actual_delete_performed"] = True
    result["deleted_file_count"] = deleted_count
    result["deleted_bytes"] = deleted_bytes
    result["post_cleanup_file_count"] = len(remaining_files)
    result["post_cleanup_total_bytes"] = remaining_bytes
    if deleted_count == expected_file_count and deleted_bytes == expected_total_bytes and not remaining_files:
        result["status"] = "cleanup_passed"
    else:
        result["status"] = "post_cleanup_verification_failed"
        result["errors"] = ["post_cleanup_target_not_empty_or_count_mismatch"]
    return result


def controlled_read_probe_policy() -> dict[str, Any]:
    return {
        "default_enabled": False,
        "approval_required_before_run": True,
        "may_trigger_provider_hydration": True,
        "mode": "opt_in_read_probe_before_retry",
        "metadata_only_audit_remains_default": True,
        "prefix_read_bytes": 1,
        "per_file_timeout_seconds": 10,
        "retry_count": 0,
        "retry_policy": "bounded_only; no infinite retry",
        "structured_error_reasons": [
            "cloud_offline",
            "cloud_recall_on_open",
            "cloud_recall_on_data_access",
            "cloud_network_unavailable",
            "cloud_hydration_failed",
            "source_missing",
            "permission_denied",
            "generic_copy_failed",
        ],
        "provider_network_unavailable_handling": "stop affected file after bounded attempts and report structurally",
        "post_failure_rule": "no DB import after failed hydration/read-probe/staging",
        "local_details": "full paths and per-file errors stay in ignored local artifacts",
        "public_report": "safe labels and aggregate counts only",
        "cfhydrateplaceholder": {
            "status": "future_enhancement_only",
            "requires_explicit_user_or_chatgpt_approval": True,
            "not_implemented_in_phase_3_8d_i3": True,
        },
    }


def build_backfill_policy(
    *,
    manifest_rows: Sequence[dict[str, str]],
    failed_row_id: int,
    selected_total: int,
) -> dict[str, Any]:
    dry_run_plan = plan_same_bucket_backfill(manifest_rows, [failed_row_id])
    return {
        "mode": "same_bucket_backfill_policy",
        "dry_run_only": True,
        "actual_manifest_replacement_performed": False,
        "approval_required_before_manifest_change": True,
        "eligible_only_after_bounded_hydration_failure": True,
        "same_bucket_first": True,
        "preserve_selected_total": selected_total,
        "preserve_temporal_diversity": True,
        "failed_cloud_candidates_remain_reported": True,
        "dry_run_plan": dry_run_plan,
    }


def build_row98_recovery_semantics(backfill_policy: Mapping[str, Any]) -> dict[str, Any]:
    replacement = None
    replacements = backfill_policy.get("dry_run_plan", {}).get("replacements") or []
    if replacements:
        replacement = replacements[0]
    return {
        "question": "Can this handle the original row 98 failure?",
        "current_i3_status": {
            "row_98_abandoned": False,
            "row_98_retried": False,
            "read_probe_or_hydration_executed": False,
            "actual_backfill_manifest_replacement_applied": False,
            "cleanup_performed": False,
            "proves_row_98_can_be_hydrated_or_copied": False,
        },
        "planned_recovery_path": [
            "If the user approves cleanup, clean only the dedicated partial staging target after dry-run plus confirmation.",
            "If the user approves controlled read-probe/hydration, test row 98 and other recall-risk rows with bounded prefix read, timeout, and retry.",
            "If row 98 read-probe/hydration succeeds, keep original row 98 in the selected set and rerun staging copy from a clean target.",
            "If row 98 fails after bounded hydrate/read-probe, use the same-bucket backfill candidate or equivalent while preserving selected_total=1000 and temporal bucket distribution.",
            "DB import remains forbidden until staging copy and post-copy audit fully pass.",
        ],
        "formal_policy": {
            "manual_download_is_formal_solution": False,
            "skip_only_is_acceptable": False,
            "backfill_is_primary_strategy": False,
            "backfill_is_last_resort_after_bounded_recovery_failure": True,
            "cloud_placeholder_is_not_permanent_failure": True,
        },
        "dry_run_backfill_candidate": replacement,
        "terminal_failure_requires": [
            "controlled hydrate/read-probe explicitly approved",
            "bounded attempts made",
            "structured failure reason captured",
            "file not partially copied or imported",
            "same-bucket replacement/backfill decision reported and approved",
        ],
        "structured_unrecovered_reasons": [
            "cloud_network_unavailable",
            "cloud_hydration_failed",
            "cloud_provider_unavailable",
            "source_missing",
            "permission_denied",
            "cloud_file_unrecoverable",
        ],
        "future_public_report_counts": [
            "attempted_recovery_count",
            "recovered_count",
            "unrecovered_count",
            "backfilled_count",
            "unresolved_count",
            "reason_distribution",
        ],
    }


def resume_vs_cleanup_recommendation() -> dict[str, Any]:
    return {
        "options": {
            "cleanup_plus_rerun": {
                "summary": "Delete only the dedicated partial staging target after explicit approval, then rerun staging from an empty target after hydration/backfill gates pass.",
                "advantages": [
                    "simpler because only 97 files were copied",
                    "no downstream DB/import/classification state exists",
                    "avoids overwrite/resume edge cases",
                ],
                "requirements": [
                    "separate explicit cleanup approval",
                    "dry-run delete report reviewed",
                    "controlled read-probe/hydration policy approved and passed",
                    "same-bucket backfill approved for bounded hydrate failures",
                ],
            },
            "resume_partial_staging": {
                "summary": "Keep the 97 files and resume from first missing/failed row after verifying copied files.",
                "advantages": ["preserves already copied files"],
                "requirements": [
                    "verify already-copied files by size/hash",
                    "refuse overwrite",
                    "resume from first missing or failed row",
                    "final post-copy audit must pass at exactly 1000 staged files",
                ],
            },
        },
        "recommended": "cleanup_plus_rerun",
        "reason": "Only 97 files were copied and no DB/downstream state exists, so empty-target rerun is the lower-complexity recovery path after explicit cleanup approval.",
        "phase_3_8d_execute_status": "blocked_until_cleanup_and_read_probe_hydration_backfill_approval",
    }


def build_recovery_report(
    *,
    cleanup_dry_run: Mapping[str, Any],
    backfill_policy: Mapping[str, Any],
    local_details_artifact: str,
    cleanup_execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cleanup_execution = cleanup_execution or {"status": "not_requested", "actual_delete_performed": False}
    report = {
        "phase": "3.8d-I3",
        "mode": "recovery_cleanup_hydration_backfill_plan",
        "created_at": utc_now(),
        "success": cleanup_dry_run.get("status") == "dry_run_passed"
        and cleanup_execution.get("status") in {"not_requested", "cleanup_passed"},
        "incident_context": {
            "phase_3_8d_execute_status": "blocked",
            "partial_staging_preserved": True,
            "db_import_ran": False,
            "classification_ran": False,
            "ai_tagging_ran": False,
            "localization_ran": False,
            "selected_manifest_label": "phase-3.8c-medium-candidate-manifest.csv",
            "known_cloud_recall_risk_count": 613,
        },
        "cleanup_dry_run": cleanup_dry_run,
        "cleanup_execution": cleanup_execution,
        "controlled_read_probe_hydration_policy": controlled_read_probe_policy(),
        "same_bucket_backfill_policy": backfill_policy,
        "row_98_recovery_semantics": build_row98_recovery_semantics(backfill_policy),
        "resume_vs_cleanup_rerun": resume_vs_cleanup_recommendation(),
        "local_artifacts": {
            "local_details_artifact": local_details_artifact,
            "full_paths_committed": False,
        },
        "safety_confirmation": {
            "actual_cleanup_delete_performed": bool(cleanup_execution.get("actual_delete_performed")),
            "staging_copy_rerun": False,
            "read_probe_or_hydration_executed": False,
            "source_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "db_import": False,
            "classification": False,
            "ai_tagging": False,
            "localization": False,
            "entity_resolver": False,
            "similarity": False,
        },
        "privacy": {
            "paths_redacted": True,
            "safe_labels_only": True,
        },
    }
    safe = sanitize_public_obj(report)
    leaks = find_privacy_leaks(safe)
    safe["privacy"]["leaks"] = leaks
    safe["privacy"]["passed"] = not leaks
    if leaks:
        safe["success"] = False
    return safe


def render_cleanup_markdown(
    report: Mapping[str, Any],
    cleanup_execution: Mapping[str, Any] | None = None,
) -> str:
    deletion = report["deletion_plan"]
    evidence = report["dedicated_target_evidence"]
    lines = [
        "# Phase 3.8d-I3 Partial Staging Cleanup Dry-run",
        "",
        "## Summary",
        "",
        f"- Status: `{report['status']}`",
        f"- Target label: `{report['target_safe_label']}`",
        f"- Target exists: `{report['target_exists']}`",
        f"- Target is directory: `{report['target_is_directory']}`",
        f"- Expected staging root explicit: `{report['expected_staging_root_explicit']}`",
        f"- Expected staging root is directory: `{report['expected_staging_root_is_directory']}`",
        f"- Target under expected staging root: `{report['target_under_expected_staging_root']}`",
        f"- Target equals expected staging root: `{report['target_equals_expected_staging_root']}`",
        f"- Target equals expected staging root allowed: `{report['target_equals_expected_staging_root_allowed']}`",
        f"- Dedicated Phase 3.8d target: `{report['target_is_dedicated_phase38d_target']}`",
        f"- Actual copied file count: `{report['actual_copied_file_count']}`",
        f"- Expected partial file count: `{report['expected_partial_file_count']}`",
        f"- Requested expected copy count: `{report['requested_expected_copy_count']}`",
        f"- Total bytes: `{report['total_bytes']}`",
        f"- Expected total bytes: `{report['expected_total_bytes']}`",
        "",
        "## Dedicated Target Evidence",
        "",
        f"- Cleanup proof basis: `{evidence['authorization_basis']}`",
        f"- Manifest/filesystem check available: `{evidence['manifest_filesystem_check_available']}`",
        f"- Manifest/filesystem check passed: `{evidence['manifest_filesystem_check_passed']}`",
        f"- Expected manifest file count: `{evidence['expected_manifest_file_count']}`",
        f"- Expected manifest size available count: `{evidence['expected_manifest_size_available_count']}`",
        f"- Expected manifest total bytes: `{evidence['expected_manifest_total_bytes']}`",
        f"- Expected total bytes source: `{evidence['expected_total_bytes_source']}`",
        f"- Staging copy log present: `{evidence['staging_copy_log_present']}`",
        f"- Staging log authorization role: `{evidence['staging_log_authorization_role']}`",
        f"- Staging log diagnostic status: `{evidence['staging_log_diagnostic_status']}`",
        f"- Staging copy log target/count/copy diagnostic match: `{evidence['staging_copy_log_matches_target']}`",
        f"- Staging copy log target exact match: `{evidence['staging_copy_log_target_exact_match']}`",
        f"- Staging copy log expected count correlated: `{evidence['staging_copy_log_expected_count_correlated']}`",
        f"- Staging copy log files copied present: `{evidence['staging_copy_log_files_copied_present']}`",
        f"- Staging copy log bytes copied present: `{evidence['staging_copy_log_bytes_copied_present']}`",
        f"- Staging copy log files copied matches: `{evidence['staging_copy_log_files_copied_matches']}`",
        f"- Staging copy log bytes copied matches: `{evidence['staging_copy_log_bytes_copied_matches']}`",
        f"- Staging copy log matching entry found: `{evidence['staging_copy_log_matching_entry_found']}`",
        f"- Staging copy log entry count: `{evidence['staging_copy_log_entry_count']}`",
        f"- Relative target handling: `{evidence['relative_target_handling']}`",
        f"- Expected partial file count matches: `{evidence['expected_file_count_matches']}`",
        f"- Expected total bytes matches: `{evidence['expected_total_bytes_matches']}`",
        f"- Unexpected files check available: `{evidence['unexpected_files_check_available']}`",
        f"- Unexpected files check passed: `{evidence['unexpected_files_check_passed']}`",
        f"- Unexpected file count: `{evidence['unexpected_file_count']}`",
        f"- Missing expected file count: `{evidence['missing_expected_file_count']}`",
        f"- Size mismatch file count: `{evidence['size_mismatch_file_count']}`",
        f"- Duplicate expected target count: `{evidence['duplicate_expected_target_count']}`",
        f"- Invalid manifest target count: `{evidence['invalid_manifest_target_count']}`",
        f"- Symlink/reparse escape hazard count: `{evidence['escape_hazard_entry_count']}`",
        f"- Identity mismatch reasons: `{json.dumps(report['identity_mismatch_reasons'], ensure_ascii=False)}`",
        "",
        "## Safety Proof",
        "",
        f"- Not source/iCloud: `{report['target_is_not_source_icloud']}`",
        f"- Not repo: `{report['target_is_not_repo']}`",
        f"- Not app-managed storage: `{report['target_is_not_app_managed_storage']}`",
        f"- Invalid protected root labels: `{json.dumps(report['invalid_protected_root_labels'], ensure_ascii=False)}`",
        f"- Missing required protected labels: `{json.dumps(report['missing_required_protected_labels'], ensure_ascii=False)}`",
        f"- Unsafe reasons: `{json.dumps(report['unsafe_reasons'], ensure_ascii=False)}`",
        "",
        "Cleanup authorization is based on explicit target/root inputs, valid protected roots, target/protected-root disjointness, manifest-derived expected staging files, and an actual filesystem scan of the target. Staging logs are diagnostic only and are not used for cleanup authorization. Actual cleanup still requires separate user/ChatGPT approval.",
        "",
        "## Extension Distribution",
        "",
    ]
    for ext, count in report["extension_distribution"].items():
        lines.append(f"- `{ext or '<none>'}`: `{count}`")
    lines.extend(
        [
            "",
            "## Sample Safe Labels",
            "",
        ]
    )
    for label in report["sample_safe_labels"]:
        lines.append(f"- `{label}`")
    lines.extend(
        [
            "",
            "## Deletion Plan",
            "",
            f"- Dry-run only: `{deletion['dry_run_only']}`",
            f"- Actual delete performed: `{deletion['actual_delete_performed']}`",
            f"- Would delete only under target root: `{deletion['would_delete_only_under_target_root']}`",
            f"- Would delete file count: `{deletion['would_delete_file_count']}`",
            f"- Would delete bytes: `{deletion['would_delete_bytes']}`",
            f"- Execute requested: `{deletion['execute_requested']}`",
            f"- Execute allowed: `{deletion['execute_allowed']}`",
            f"- Confirmation phrase required: `{deletion['confirmation_phrase_required']}`",
            f"- Separate user approval required: `{deletion['separate_user_approval_required']}`",
            "",
            "## Privacy",
            "",
            "- This report uses only safe labels.",
            "- Full local paths remain only in ignored local artifacts.",
        ]
    )
    if cleanup_execution is not None:
        lines.extend(
            [
                "",
                "## Cleanup Execution",
                "",
                f"- Requested: `{cleanup_execution['requested']}`",
                f"- Status: `{cleanup_execution['status']}`",
                f"- Actual delete performed: `{cleanup_execution['actual_delete_performed']}`",
                f"- Deleted file count: `{cleanup_execution['deleted_file_count']}`",
                f"- Deleted bytes: `{cleanup_execution['deleted_bytes']}`",
                f"- Post-cleanup file count: `{cleanup_execution['post_cleanup_file_count']}`",
                f"- Post-cleanup total bytes: `{cleanup_execution['post_cleanup_total_bytes']}`",
                f"- Errors: `{json.dumps(cleanup_execution['errors'], ensure_ascii=False)}`",
            ]
        )
    return "\n".join(lines) + "\n"


def render_recovery_markdown(report: Mapping[str, Any]) -> str:
    cleanup = report["cleanup_dry_run"]
    probe = report["controlled_read_probe_hydration_policy"]
    backfill = report["same_bucket_backfill_policy"]
    row98 = report["row_98_recovery_semantics"]
    recommendation = report["resume_vs_cleanup_rerun"]
    lines = [
        "# Phase 3.8d-I3 Recovery Plan",
        "",
        "## Incident State",
        "",
        f"- Phase 3.8d execute status: `{report['incident_context']['phase_3_8d_execute_status']}`",
        f"- Partial staging preserved: `{report['incident_context']['partial_staging_preserved']}`",
        f"- Known Cloud Files recall-risk count: `{report['incident_context']['known_cloud_recall_risk_count']}`",
        f"- Cleanup dry-run status: `{cleanup['status']}`",
        "",
        "## Cleanup Dry-run",
        "",
        f"- Target label: `{cleanup['target_safe_label']}`",
        f"- File count: `{cleanup['file_count']}`",
        f"- Total bytes: `{cleanup['total_bytes']}`",
        f"- Expected staging root explicit: `{cleanup['expected_staging_root_explicit']}`",
        f"- Expected staging root is directory: `{cleanup['expected_staging_root_is_directory']}`",
        f"- Target under expected staging root: `{cleanup['target_under_expected_staging_root']}`",
        f"- Target equals expected staging root: `{cleanup['target_equals_expected_staging_root']}`",
        f"- Target equals expected staging root allowed: `{cleanup['target_equals_expected_staging_root_allowed']}`",
        f"- Protected roots valid: `{not cleanup['invalid_protected_root_labels'] and not cleanup['missing_required_protected_labels']}`",
        f"- Cleanup authorization basis: `{cleanup['dedicated_target_evidence']['authorization_basis']}`",
        f"- Manifest/filesystem proof passed: `{cleanup['dedicated_target_evidence']['manifest_filesystem_check_passed']}`",
        f"- Staging log authorization role: `{cleanup['dedicated_target_evidence']['staging_log_authorization_role']}`",
        f"- Staging log diagnostic status: `{cleanup['dedicated_target_evidence']['staging_log_diagnostic_status']}`",
        f"- Actual delete performed: `{cleanup['deletion_plan']['actual_delete_performed']}`",
        f"- Confirmation phrase required: `{cleanup['deletion_plan']['confirmation_phrase_required']}`",
        "",
        "Cleanup authorization is based on manifest/filesystem proof, not human staging logs. Staging logs are diagnostic only. Actual cleanup still requires separate user/ChatGPT approval.",
        "",
        "## Controlled Read-probe / Hydration Policy",
        "",
        f"- Default enabled: `{probe['default_enabled']}`",
        f"- Approval required before run: `{probe['approval_required_before_run']}`",
        f"- May trigger provider hydration: `{probe['may_trigger_provider_hydration']}`",
        f"- Prefix read bytes: `{probe['prefix_read_bytes']}`",
        f"- Per-file timeout seconds: `{probe['per_file_timeout_seconds']}`",
        f"- Retry count: `{probe['retry_count']}`",
        f"- CfHydratePlaceholder status: `{probe['cfhydrateplaceholder']['status']}`",
        "",
        "## Same-bucket Backfill Policy",
        "",
        f"- Dry-run only: `{backfill['dry_run_only']}`",
        f"- Actual manifest replacement performed: `{backfill['actual_manifest_replacement_performed']}`",
        f"- Same-bucket first: `{backfill['same_bucket_first']}`",
        f"- Preserve selected total: `{backfill['preserve_selected_total']}`",
        f"- Dry-run replacement count: `{backfill['dry_run_plan']['replacement_count']}`",
        f"- Dry-run unresolved count: `{backfill['dry_run_plan']['unresolved_count']}`",
        "",
        "### Dry-run Replacements",
        "",
    ]
    replacements = backfill["dry_run_plan"].get("replacements") or []
    if replacements:
        for item in replacements:
            lines.append(
                f"- Failed `{item['failed_safe_label']}` -> replacement `{item['replacement_safe_label']}` "
                f"in bucket `{item['bucket']}`"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Can this handle the original row 98 failure?",
            "",
            "Short answer: I3 defines the recovery path for row 98, but it has not yet proven that row 98 can be hydrated or copied.",
            "Row 98 is not abandoned; it remains the preferred original candidate unless bounded recovery fails after explicit approval.",
            "",
            "Current I3 status:",
            f"- row 98 abandoned: `{row98['current_i3_status']['row_98_abandoned']}`",
            f"- row 98 retried: `{row98['current_i3_status']['row_98_retried']}`",
            f"- read-probe/hydration executed: `{row98['current_i3_status']['read_probe_or_hydration_executed']}`",
            f"- actual backfill manifest replacement applied: `{row98['current_i3_status']['actual_backfill_manifest_replacement_applied']}`",
            f"- cleanup performed: `{row98['current_i3_status']['cleanup_performed']}`",
            f"- proves row 98 can be hydrated/copied: `{row98['current_i3_status']['proves_row_98_can_be_hydrated_or_copied']}`",
            "",
            "Planned recovery path:",
        ]
    )
    for index, step in enumerate(row98["planned_recovery_path"], start=1):
        lines.append(f"{index}. {step}")
    lines.extend(
        [
            "",
            "Policy notes:",
            f"- Manual download is the formal solution: `{row98['formal_policy']['manual_download_is_formal_solution']}`",
            f"- Skip-only is acceptable: `{row98['formal_policy']['skip_only_is_acceptable']}`",
            f"- Backfill is primary strategy: `{row98['formal_policy']['backfill_is_primary_strategy']}`",
            f"- Backfill is last resort after bounded recovery failure: `{row98['formal_policy']['backfill_is_last_resort_after_bounded_recovery_failure']}`",
            f"- Cloud placeholder is permanent failure: `{not row98['formal_policy']['cloud_placeholder_is_not_permanent_failure']}`",
            "",
            "Manual download is not the formal solution because V.I.O.L.E.T. must handle iCloud-backed libraries at scale through deterministic availability gates. Skip-only is not acceptable because cloud placeholder status means the file needs a controlled availability workflow, not silent abandonment. Backfill is only a fallback after bounded read-probe/hydration failure so the original cloud-backed item remains usable whenever the provider can make it readable.",
            "",
            "Dry-run row 98 backfill candidate:",
        ]
    )
    candidate = row98.get("dry_run_backfill_candidate")
    if candidate:
        lines.append(
            f"- `{candidate['failed_safe_label']}` -> `{candidate['replacement_safe_label']}` "
            f"in bucket `{candidate['bucket']}`"
        )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Resume vs Cleanup + Rerun",
            "",
            f"- Recommended: `{recommendation['recommended']}`",
            f"- Reason: {recommendation['reason']}",
            "",
            "## Safety",
            "",
        ]
    )
    for key, value in report["safety_confirmation"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Privacy",
            "",
            f"- Passed: `{report['privacy']['passed']}`",
            f"- Leaks: `{json.dumps(report['privacy']['leaks'], ensure_ascii=False)}`",
            f"- Local details artifact: `{report['local_artifacts']['local_details_artifact']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--expected-staging-root", type=Path, default=None)
    parser.add_argument("--protected-root", type=parse_protected_root, action="append", default=[])
    parser.add_argument("--staging-log", type=Path, default=DEFAULT_STAGING_LOG)
    parser.add_argument("--expected-file-count", type=int, default=DEFAULT_EXPECTED_FILE_COUNT)
    parser.add_argument("--expected-total-bytes", type=int, default=DEFAULT_EXPECTED_TOTAL_BYTES)
    parser.add_argument("--selected-total", type=int, default=DEFAULT_SELECTED_TOTAL)
    parser.add_argument("--failed-row-id", type=int, default=DEFAULT_FAILED_ROW_ID)
    parser.add_argument(
        "--allow-target-equals-expected-staging-root",
        action="store_true",
        help="Explicitly allow the dedicated staging target itself to be used as the expected staging root.",
    )
    parser.add_argument("--execute-cleanup", action="store_true")
    parser.add_argument("--confirm-cleanup", default="")
    parser.add_argument("--recovery-report-md", type=Path, default=DEFAULT_RECOVERY_REPORT_MD)
    parser.add_argument("--cleanup-report-md", type=Path, default=DEFAULT_CLEANUP_REPORT_MD)
    parser.add_argument("--cleanup-report-json", type=Path, default=DEFAULT_CLEANUP_REPORT_JSON)
    parser.add_argument("--local-details-json", type=Path, default=DEFAULT_LOCAL_DETAILS_JSON)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.manifest.is_file():
        parser.error(f"manifest not found: {args.manifest}")

    manifest_rows = read_manifest(args.manifest)
    cleanup_dry_run, local_cleanup_details = build_cleanup_dry_run(
        target_root=args.target_root,
        expected_staging_root=args.expected_staging_root,
        protected_roots=args.protected_root,
        expected_file_count=args.expected_file_count,
        expected_total_bytes=args.expected_total_bytes,
        expected_copy_count=args.selected_total,
        expected_manifest_rows=manifest_rows,
        execute_cleanup_requested=args.execute_cleanup,
        confirm_cleanup=args.confirm_cleanup,
        staging_log=args.staging_log,
        allow_target_equals_expected_root=args.allow_target_equals_expected_staging_root,
    )
    cleanup_execution = execute_verified_cleanup(
        target_root=args.target_root,
        expected_manifest_rows=manifest_rows,
        cleanup_dry_run=cleanup_dry_run,
        expected_file_count=args.expected_file_count,
        expected_total_bytes=cleanup_dry_run.get("expected_total_bytes", args.expected_total_bytes),
        execute_cleanup_requested=args.execute_cleanup,
        confirm_cleanup=args.confirm_cleanup,
    )
    backfill_policy = build_backfill_policy(
        manifest_rows=manifest_rows,
        failed_row_id=args.failed_row_id,
        selected_total=args.selected_total,
    )
    recovery_report = build_recovery_report(
        cleanup_dry_run=cleanup_dry_run,
        backfill_policy=backfill_policy,
        local_details_artifact=args.local_details_json.name,
        cleanup_execution=cleanup_execution,
    )
    execution_success = cleanup_execution["status"] in {"not_requested", "cleanup_passed"}

    cleanup_report = sanitize_public_obj(
        {
            "phase": "3.8d-I3",
            "created_at": recovery_report["created_at"],
            "success": cleanup_dry_run.get("status") == "dry_run_passed" and execution_success,
            "cleanup_dry_run": cleanup_dry_run,
            "cleanup_execution": cleanup_execution,
            "privacy": {"paths_redacted": True, "safe_labels_only": True},
        }
    )
    leaks = find_privacy_leaks(cleanup_report)
    cleanup_report["privacy"]["leaks"] = leaks
    cleanup_report["privacy"]["passed"] = not leaks
    if leaks:
        cleanup_report["success"] = False

    write_json(args.cleanup_report_json, cleanup_report)
    write_text(args.cleanup_report_md, render_cleanup_markdown(cleanup_dry_run, cleanup_execution))
    write_text(args.recovery_report_md, render_recovery_markdown(recovery_report))
    write_json(
        args.local_details_json,
        {
            "created_at": recovery_report["created_at"],
            "cleanup": local_cleanup_details,
            "cleanup_execution": cleanup_execution,
            "manifest": str(args.manifest),
            "target_root": str(args.target_root),
        },
    )
    print(json.dumps(recovery_report, ensure_ascii=False, indent=2))
    return 0 if recovery_report.get("success") and cleanup_report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
