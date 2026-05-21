#!/usr/bin/env python3
"""Validate and execute a candidate manifest for Tier-1000 pilot staging.

Phase 3.3b — dry-run validation AND controlled copy execution.
Reads a CSV manifest produced by generate_candidate_manifest.py,
validates that the proposed staging operation is safe, and optionally
copies files to the target directory.

Hard rules:
  - NEVER modifies the source or existing directories.
  - NEVER modifies any database.
  - NEVER overwrites existing target files.
  - --execute requires --confirm-copy-tier1000 safety flag.
  - Partial failure: stops on first error, reports progress.

Usage (dry-run):
    python scripts/stage_pilot_files.py \
        --manifest ".local_manifests/phase-3.3a.1-candidate-manifest.csv" \
        --target-root "E:\\VioletPilotData_1000" \
        --dry-run

Usage (execute):
    python scripts/stage_pilot_files.py \
        --manifest ".local_manifests/phase-3.3a.1-candidate-manifest.csv" \
        --target-root "E:\\VioletPilotData_1000" \
        --source-root "C:\\Users\\...\\iCloud Photos\\Photos" \
        --existing-root "E:\\VioletPilotData" \
        --execute --confirm-copy-tier1000
"""
import argparse
import csv
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.utils.cloud_files import (  # noqa: E402
    classify_cloud_file_state,
    classify_file_access_error,
)

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

KNOWN_SELECTION_REASONS = {"existing_tier500", "new_candidate"}
KNOWN_EXCLUSION_CODES = {
    "stat_error",
    "placeholder",
    "hidden",
    "already_imported_prior_manifest",
    "duplicate_prior_manifest_key",
    "not_selected_temporal_stratified",
    "timestamp_unknown_over_cap",
}
KNOWN_EXCLUSION_PREFIXES = ("unsupported_format:",)

# Minimum expected CSV fields for a non-truncated row
_REQUIRED_FIELDS = {
    "source_path", "proposed_target_path", "extension",
    "size_bytes", "selection_reason", "exclusion_reason",
}


def _row_has_required_values(row: dict) -> bool:
    """Check whether a CSV row has non-None values for all required fields.

    csv.DictReader always populates header-defined keys even for short rows,
    filling missing trailing cells with None.  A key-only check
    (``_REQUIRED_FIELDS.issubset(row.keys())``) therefore never detects
    truncated rows.  This helper inspects **values**: a field is considered
    missing if the key is absent OR the value is None.

    Empty string is NOT treated as missing here — existing schema validators
    handle blank source/target/extension separately.
    """
    for field in _REQUIRED_FIELDS:
        if field not in row or row[field] is None:
            return False
    return True


def _clean_field(row: dict, key: str, default: str = "") -> str:
    """Safely get a string field from a CSV row, handling None values."""
    val = row.get(key)
    if val is None:
        return default
    return str(val).strip()


def _is_known_exclusion(reason: str) -> bool:
    """Check if an exclusion_reason is a recognized value."""
    if reason in KNOWN_EXCLUSION_CODES:
        return True
    for prefix in KNOWN_EXCLUSION_PREFIXES:
        if reason.startswith(prefix):
            return True
    return False


def validate_manifest(
    manifest_path: Path,
    target_root: Path,
    *,
    approved_source_roots: list[Path] | None = None,
    rows: list[dict] | None = None,
) -> dict:
    """Validate a staging manifest (dry-run).

    Parameters
    ----------
    manifest_path : Path
        CSV manifest from generate_candidate_manifest.py.
    target_root : Path
        Proposed staging directory.
    approved_source_roots : list[Path] | None
        If provided, every non-excluded source_path must resolve under
        one of these roots. None skips the check (dry-run compat).
    rows : list[dict] | None
        Pre-read CSV rows.  If provided, the manifest file is NOT re-read;
        these rows are validated directly.  Use with ``read_manifest_rows()``
        to avoid TOCTOU gaps.

    Returns a dict with validation results.
    """
    result = {
        "manifest_path": str(manifest_path),
        "target_root": str(target_root),
        "total_rows": 0,
        "existing_tier500_rows": 0,
        "new_candidate_rows": 0,
        "excluded_rows": 0,
        "source_files_exist": 0,
        "source_files_missing": 0,
        "source_files_missing_paths": [],
        "target_filename_collisions": 0,
        "target_collision_paths": [],
        "target_root_escapes": 0,
        "target_root_escape_paths": [],
        "unsupported_extensions": 0,
        "blank_source_paths": 0,
        "blank_target_paths": 0,
        "blank_extensions": 0,
        "invalid_selection_reasons": 0,
        "invalid_exclusion_reasons": 0,
        "extension_mismatches": 0,
        "suffix_missing": 0,
        "target_existing_files": 0,
        "target_existing_paths": [],
        "cloud_risk_files": 0,
        "cloud_risk_paths": [],
        "cloud_risk_by_reason": {},
        "source_root_violations": 0,
        "source_root_violation_paths": [],
        "truncated_rows": 0,
        "total_copy_bytes": 0,
        "target_root_exists": target_root.is_dir(),
        "target_root_is_local": True,
        "errors": [],
        "warnings": [],
        "valid": True,
    }

    if not manifest_path.is_file() and rows is None:
        result["errors"].append(f"Manifest file not found: {manifest_path}")
        result["valid"] = False
        return result

    if rows is not None:
        # Use pre-read rows (avoids TOCTOU gap in execute path)
        pass
    else:
        rows_read, read_err = read_manifest_rows(manifest_path)
        if read_err is not None:
            result["errors"].append(read_err)
            result["valid"] = False
            return result
        rows = rows_read

    result["total_rows"] = len(rows)

    try:
        resolved_root = target_root.resolve()
    except (RuntimeError, OSError) as exc:
        result["errors"].append(f"Cannot resolve target_root: {exc}")
        result["valid"] = False
        return result

    # Resolve approved source roots once
    resolved_approved: list[Path] | None = None
    if approved_source_roots is not None:
        resolved_approved = []
        for sr in approved_source_roots:
            try:
                resolved_approved.append(sr.resolve())
            except (RuntimeError, OSError) as exc:
                result["errors"].append(f"Cannot resolve approved source root {sr}: {exc}")
                result["valid"] = False
                return result

    # Collision detection by full resolved target path (case-insensitive on Windows)
    seen_targets: dict[str, str] = {}

    for row in rows:
        # Truncated row detection (value-level: None means missing cell)
        if not _row_has_required_values(row):
            result["truncated_rows"] += 1
            continue

        # 2A: Normalize all string fields
        selection = _clean_field(row, "selection_reason")
        exclusion = _clean_field(row, "exclusion_reason")
        source_path = _clean_field(row, "source_path")
        proposed_target = _clean_field(row, "proposed_target_path")
        ext = _clean_field(row, "extension").lower()
        size_str = _clean_field(row, "size_bytes", "0")

        try:
            size = int(size_str)
        except (ValueError, TypeError):
            size = 0

        # 2C: Exclusion reason validation
        if exclusion:
            if not _is_known_exclusion(exclusion):
                result["invalid_exclusion_reasons"] += 1
            else:
                result["excluded_rows"] += 1
                continue
        # Note: whitespace-only exclusion was already stripped to "", so falls through

        # --- Non-excluded (copy) row processing ---

        # 2B: Selection reason validation
        if selection == "existing_tier500":
            result["existing_tier500_rows"] += 1
        elif selection == "new_candidate":
            result["new_candidate_rows"] += 1

        if selection not in KNOWN_SELECTION_REASONS:
            result["invalid_selection_reasons"] += 1

        # Reject non-excluded copy rows with blank source_path
        if not source_path:
            result["blank_source_paths"] += 1

        # Reject non-excluded copy rows with blank proposed_target_path
        if not proposed_target:
            result["blank_target_paths"] += 1

        # Reject non-excluded copy rows with blank extension
        if not ext:
            result["blank_extensions"] += 1

        # Validate source file exists — missing source is an ERROR for copy rows
        source_suffix = ""
        if source_path:
            sp = Path(source_path)
            if sp.is_file():
                result["source_files_exist"] += 1
                source_suffix = sp.suffix.lower()
                cloud_state = classify_cloud_file_state(sp)
                if cloud_state.likely_cloud_placeholder:
                    result["cloud_risk_files"] += 1
                    reasons = []
                    if cloud_state.offline:
                        reasons.append("cloud_offline")
                    if cloud_state.recall_on_open:
                        reasons.append("cloud_recall_on_open")
                    if cloud_state.recall_on_data_access:
                        reasons.append("cloud_recall_on_data_access")
                    if cloud_state.reparse_point:
                        reasons.append("cloud_reparse_point")
                    if not reasons:
                        reasons.append("cloud_hydration_risk")
                    for reason in reasons:
                        result["cloud_risk_by_reason"][reason] = result["cloud_risk_by_reason"].get(reason, 0) + 1
                    if len(result["cloud_risk_paths"]) < 10:
                        result["cloud_risk_paths"].append(source_path)
            else:
                result["source_files_missing"] += 1
                if len(result["source_files_missing_paths"]) < 10:
                    result["source_files_missing_paths"].append(source_path)
                # Derive suffix even for missing source (for consistency check)
                source_suffix = sp.suffix.lower()

            # Approved source root check
            if resolved_approved is not None:
                try:
                    resolved_sp = sp.resolve()
                    in_approved = any(
                        _is_under(resolved_sp, root) for root in resolved_approved
                    )
                    if not in_approved:
                        result["source_root_violations"] += 1
                        if len(result["source_root_violation_paths"]) < 10:
                            result["source_root_violation_paths"].append(source_path)
                except (RuntimeError, OSError):
                    result["source_root_violations"] += 1
                    if len(result["source_root_violation_paths"]) < 10:
                        result["source_root_violation_paths"].append(source_path)

        # Validate extension from CSV field
        if ext and ext not in SUPPORTED_EXTENSIONS:
            result["unsupported_extensions"] += 1

        # 2F: Suffix consistency — all three must agree and be supported
        target_suffix = ""
        if proposed_target:
            target_suffix = Path(proposed_target).suffix.lower()

        if source_path:
            # Source suffix must be non-empty and supported
            if not source_suffix:
                result["suffix_missing"] += 1
            elif source_suffix not in SUPPORTED_EXTENSIONS:
                result["extension_mismatches"] += 1

            # CSV extension must match source suffix
            if ext and source_suffix and ext != source_suffix:
                result["extension_mismatches"] += 1

        if proposed_target:
            # Target suffix must be non-empty and supported
            if not target_suffix:
                result["suffix_missing"] += 1
            elif target_suffix not in SUPPORTED_EXTENSIONS:
                result["extension_mismatches"] += 1

            # Target suffix must equal source suffix
            if source_suffix and target_suffix and target_suffix != source_suffix:
                result["extension_mismatches"] += 1

        # Check target path escapes target_root
        if proposed_target:
            try:
                resolved_target = Path(proposed_target).resolve()
                resolved_target.relative_to(resolved_root)
            except (ValueError, RuntimeError, OSError):
                result["target_root_escapes"] += 1
                if len(result["target_root_escape_paths"]) < 10:
                    result["target_root_escape_paths"].append(proposed_target)
        else:
            # Empty/blank target on a copy row — counts as escape
            if not proposed_target:
                # Already counted in blank_target_paths; also count as escape
                result["target_root_escapes"] += 1

        # 2H: Collision by full resolved target path (case-insensitive)
        if proposed_target:
            try:
                collision_key = str(Path(proposed_target).resolve()).lower()
            except (OSError, ValueError, RuntimeError):
                collision_key = proposed_target.lower()
            if collision_key in seen_targets:
                result["target_filename_collisions"] += 1
                if len(result["target_collision_paths"]) < 10:
                    result["target_collision_paths"].append(proposed_target)
            else:
                seen_targets[collision_key] = proposed_target

        # 2I: Check if proposed target already exists on disk
        if proposed_target:
            try:
                resolved_target = Path(proposed_target).resolve()
                if resolved_target.exists():
                    result["target_existing_files"] += 1
                    if len(result["target_existing_paths"]) < 10:
                        result["target_existing_paths"].append(proposed_target)
            except (OSError, ValueError, RuntimeError):
                pass  # resolve/exists failure — already caught by escape check

        result["total_copy_bytes"] += size

    # Validation checks — errors that set valid=False
    if result["source_files_missing"] > 0:
        result["errors"].append(
            f"{result['source_files_missing']} source files not found"
        )
        result["valid"] = False

    if result["target_root_escapes"] > 0:
        result["errors"].append(
            f"{result['target_root_escapes']} target paths escape target_root"
        )
        result["valid"] = False

    if result["blank_source_paths"] > 0:
        result["errors"].append(
            f"{result['blank_source_paths']} non-excluded rows have blank source_path"
        )
        result["valid"] = False

    if result["blank_target_paths"] > 0:
        result["errors"].append(
            f"{result['blank_target_paths']} non-excluded rows have blank proposed_target_path"
        )
        result["valid"] = False

    if result["blank_extensions"] > 0:
        result["errors"].append(
            f"{result['blank_extensions']} non-excluded rows have blank extension"
        )
        result["valid"] = False

    if result["target_filename_collisions"] > 0:
        result["errors"].append(
            f"{result['target_filename_collisions']} target path collisions detected"
        )
        result["valid"] = False

    if result["unsupported_extensions"] > 0:
        result["errors"].append(
            f"{result['unsupported_extensions']} rows have unsupported extensions"
        )
        result["valid"] = False

    if result["invalid_selection_reasons"] > 0:
        result["errors"].append(
            f"{result['invalid_selection_reasons']} non-excluded rows have invalid selection_reason"
        )
        result["valid"] = False

    if result["invalid_exclusion_reasons"] > 0:
        result["errors"].append(
            f"{result['invalid_exclusion_reasons']} rows have invalid exclusion_reason"
        )
        result["valid"] = False

    if result["extension_mismatches"] > 0:
        result["errors"].append(
            f"{result['extension_mismatches']} rows have extension mismatches"
        )
        result["valid"] = False

    if result["suffix_missing"] > 0:
        result["errors"].append(
            f"{result['suffix_missing']} copy rows have missing file suffix"
        )
        result["valid"] = False

    if result["target_existing_files"] > 0:
        result["errors"].append(
            f"{result['target_existing_files']} proposed target files already exist on disk"
        )
        result["valid"] = False

    if result["cloud_risk_files"] > 0:
        result["errors"].append(
            f"{result['cloud_risk_files']} copy rows have cloud availability risk; "
            "run cloud availability/hydration gate before execute"
        )
        result["valid"] = False

    if result["source_root_violations"] > 0:
        result["errors"].append(
            f"{result['source_root_violations']} source paths outside approved roots"
        )
        result["valid"] = False

    if result["truncated_rows"] > 0:
        result["errors"].append(
            f"{result['truncated_rows']} truncated rows (missing required fields)"
        )
        result["valid"] = False

    copy_count = result["existing_tier500_rows"] + result["new_candidate_rows"]
    if copy_count == 0:
        result["warnings"].append("No files to copy (manifest may be empty or all excluded)")

    return result


def _is_under(child: Path, parent: Path) -> bool:
    """Check if child path is under parent path (resolved, case-insensitive on Windows)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _ensure_target_root_disjoint(
    target_root: Path,
    protected_roots: list[Path],
) -> tuple[bool, str | None]:
    """Check that target_root does not overlap with any protected root.

    Rejects if:
    - target_root == protected_root (same directory)
    - target_root is inside a protected_root
    - a protected_root is inside target_root

    All paths must already be resolved.

    Returns (ok, error_message).  ok=True means disjoint (safe).
    """
    for pr in protected_roots:
        # Exact match (case-insensitive on Windows)
        if str(target_root).lower() == str(pr).lower():
            return False, (
                f"target_root is the same as protected root: "
                f"{target_root} == {pr}"
            )
        # target inside protected
        if _is_under(target_root, pr):
            return False, (
                f"target_root is inside protected root: "
                f"{target_root} inside {pr}"
            )
        # protected inside target
        if _is_under(pr, target_root):
            return False, (
                f"protected root is inside target_root: "
                f"{pr} inside {target_root}"
            )
    return True, None


def read_manifest_rows(manifest_path: Path) -> tuple[list[dict], str | None]:
    """Read all rows from a CSV manifest file.

    Returns (rows, error_message).  On success error_message is None.
    On failure rows is [] and error_message describes the problem.
    """
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        return rows, None
    except (OSError, csv.Error) as exc:
        return [], f"Failed to read manifest {manifest_path}: {exc}"


def execute_copy(
    manifest_path: Path,
    target_root: Path,
    *,
    approved_source_roots: list[Path],
    rows: list[dict] | None = None,
) -> dict:
    """Execute the staging copy from a validated manifest.

    Copies files listed in the manifest to target_root. Stops on first error.
    NEVER overwrites existing files. NEVER copies from unapproved source roots.

    Parameters
    ----------
    manifest_path : Path
        CSV manifest (already validated by validate_manifest).
    target_root : Path
        Staging directory (will be created if it doesn't exist).
    approved_source_roots : list[Path]
        Every source_path must resolve under one of these.
    rows : list[dict] | None
        Pre-read CSV rows.  If provided, the manifest file is NOT re-read;
        these rows are used directly.  Use with ``read_manifest_rows()``
        to avoid TOCTOU gaps.

    Returns a dict with copy results.
    """
    copy_result = {
        "total_rows": 0,
        "copied": 0,
        "skipped_excluded": 0,
        "skipped_truncated": 0,
        "failed": 0,
        "failed_path": None,
        "failed_reason": None,
        "failed_reason_code": None,
        "failed_cloud_state": None,
        "total_bytes_copied": 0,
        "errors": [],
    }

    # Resolve approved roots
    resolved_approved = []
    for sr in approved_source_roots:
        try:
            resolved_approved.append(sr.resolve())
        except (RuntimeError, OSError) as exc:
            copy_result["errors"].append(f"Cannot resolve approved root {sr}: {exc}")
            copy_result["failed"] = 1
            copy_result["failed_reason"] = f"Cannot resolve approved root: {exc}"
            return copy_result

    # §3: Wrap target_root.resolve() in try/except
    try:
        resolved_root = target_root.resolve()
    except (RuntimeError, OSError) as exc:
        msg = f"Cannot resolve target_root {target_root}: {exc}"
        copy_result["errors"].append(msg)
        copy_result["failed"] = 1
        copy_result["failed_reason"] = msg
        return copy_result

    # §1: target_root must be disjoint from all protected (source/existing) roots
    disjoint_ok, disjoint_msg = _ensure_target_root_disjoint(
        resolved_root, resolved_approved
    )
    if not disjoint_ok:
        copy_result["errors"].append(disjoint_msg)
        copy_result["failed"] = 1
        copy_result["failed_reason"] = disjoint_msg
        return copy_result

    # Guard: target_root must not be an existing non-directory (e.g. a file)
    if target_root.exists() and not target_root.is_dir():
        msg = f"target_root exists but is not a directory: {target_root}"
        copy_result["errors"].append(msg)
        copy_result["failed"] = 1
        copy_result["failed_reason"] = msg
        return copy_result

    # Create target directory
    try:
        target_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"Cannot create target directory {target_root}: {exc}"
        copy_result["errors"].append(msg)
        copy_result["failed"] = 1
        copy_result["failed_reason"] = msg
        return copy_result

    # §2+§4: Use pre-read rows if provided, otherwise read with structured error handling
    if rows is not None:
        pass  # use caller-provided rows
    else:
        rows_read, read_err = read_manifest_rows(manifest_path)
        if read_err is not None:
            copy_result["errors"].append(read_err)
            copy_result["failed"] = 1
            copy_result["failed_reason"] = read_err
            return copy_result
        rows = rows_read

    copy_result["total_rows"] = len(rows)

    for row in rows:
        # Skip truncated rows (value-level: None means missing cell)
        if not _row_has_required_values(row):
            copy_result["skipped_truncated"] += 1
            continue

        exclusion = _clean_field(row, "exclusion_reason")
        if exclusion and _is_known_exclusion(exclusion):
            copy_result["skipped_excluded"] += 1
            continue

        source_path = _clean_field(row, "source_path")
        proposed_target = _clean_field(row, "proposed_target_path")

        if not source_path or not proposed_target:
            copy_result["failed"] += 1
            copy_result["failed_path"] = source_path or "(blank)"
            copy_result["failed_reason"] = "Blank source or target path"
            copy_result["errors"].append(f"Blank path: source={source_path!r} target={proposed_target!r}")
            return copy_result

        sp = Path(source_path)
        tp = Path(proposed_target)

        # Source must exist
        if not sp.is_file():
            cloud_state = classify_cloud_file_state(sp)
            copy_result["failed"] += 1
            copy_result["failed_path"] = source_path
            copy_result["failed_reason_code"] = "source_missing"
            copy_result["failed_reason"] = "Source file not found"
            copy_result["failed_cloud_state"] = cloud_state.to_dict()
            copy_result["errors"].append(f"Source not found: {source_path}")
            return copy_result

        # Source must be under an approved root
        try:
            resolved_sp = sp.resolve()
            in_approved = any(_is_under(resolved_sp, root) for root in resolved_approved)
        except (RuntimeError, OSError):
            in_approved = False

        if not in_approved:
            copy_result["failed"] += 1
            copy_result["failed_path"] = source_path
            copy_result["failed_reason_code"] = "permission_denied"
            copy_result["failed_reason"] = "Source outside approved roots"
            copy_result["errors"].append(f"Source outside approved roots: {source_path}")
            return copy_result

        # Target must be inside target_root
        try:
            resolved_tp = tp.resolve()
            resolved_tp.relative_to(resolved_root)
        except (ValueError, RuntimeError, OSError):
            copy_result["failed"] += 1
            copy_result["failed_path"] = proposed_target
            copy_result["failed_reason"] = "Target escapes target_root"
            copy_result["errors"].append(f"Target escapes root: {proposed_target}")
            return copy_result

        # NEVER overwrite
        if tp.exists():
            copy_result["failed"] += 1
            copy_result["failed_path"] = proposed_target
            copy_result["failed_reason_code"] = "target_exists"
            copy_result["failed_reason"] = "Target already exists (refuse overwrite)"
            copy_result["errors"].append(f"Target exists: {proposed_target}")
            return copy_result

        # Create parent directories
        try:
            tp.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            copy_result["failed"] += 1
            copy_result["failed_path"] = proposed_target
            copy_result["failed_reason"] = f"Cannot create parent directory {tp.parent}: {exc}"
            copy_result["errors"].append(
                f"mkdir error for {proposed_target}: {exc}"
            )
            return copy_result

        # Copy with metadata preservation
        try:
            shutil.copy2(str(sp), str(tp))
        except (OSError, shutil.SameFileError) as exc:
            cloud_state = classify_cloud_file_state(sp)
            reason_code = classify_file_access_error(exc, cloud_state)
            copy_result["failed"] += 1
            copy_result["failed_path"] = source_path
            copy_result["failed_reason_code"] = reason_code
            copy_result["failed_reason"] = f"Copy failed ({reason_code}): {exc}"
            copy_result["failed_cloud_state"] = cloud_state.to_dict()
            copy_result["errors"].append(f"Copy error ({reason_code}): {source_path} -> {proposed_target}: {exc}")
            return copy_result

        try:
            copy_result["total_bytes_copied"] += tp.stat().st_size
        except OSError:
            pass

        copy_result["copied"] += 1

    return copy_result


def post_copy_audit(target_root: Path) -> dict:
    """Post-copy verification of the staged directory.

    Counts files, total bytes, extension distribution, and samples files.

    Parameters
    ----------
    target_root : Path
        The staging directory to audit.

    Returns a dict with audit results.
    """
    audit = {
        "target_root": str(target_root),
        "exists": target_root.is_dir(),
        "total_files": 0,
        "total_bytes": 0,
        "extension_counts": {},
        "unexpected_extensions": [],
        "sample_files": [],
    }

    if not target_root.is_dir():
        return audit

    ext_counter: Counter = Counter()
    all_files: list[Path] = []

    for root, _dirs, files in os.walk(target_root):
        for fname in files:
            fpath = Path(root) / fname
            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0
            ext = fpath.suffix.lower()
            ext_counter[ext] += 1
            audit["total_files"] += 1
            audit["total_bytes"] += size
            all_files.append(fpath)

            if ext not in SUPPORTED_EXTENSIONS:
                audit["unexpected_extensions"].append(str(fpath))

    audit["extension_counts"] = dict(ext_counter)

    # Sample up to 20 files
    import random as _rng
    sample_count = min(20, len(all_files))
    if sample_count > 0:
        sampled = _rng.Random(42).sample(all_files, sample_count)
        audit["sample_files"] = [str(p) for p in sorted(sampled)]

    return audit


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.2f} GB"


def main():
    parser = argparse.ArgumentParser(
        description="Validate and execute a staging manifest for Tier-1000 pilot")
    parser.add_argument("--manifest", type=str, required=True,
                        help="Path to CSV manifest from generate_candidate_manifest.py")
    parser.add_argument("--target-root", type=str, required=True,
                        help="Proposed staging directory path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Dry-run mode: validate only, do not copy")
    parser.add_argument("--execute", action="store_true",
                        help="Execute mode: validate then copy files")
    parser.add_argument("--confirm-copy-tier1000", action="store_true",
                        help="Safety confirmation flag for --execute mode")
    parser.add_argument("--source-root", type=str, default=None,
                        help="Source root (e.g. iCloud Photos) for source-root validation")
    parser.add_argument("--existing-root", type=str, default=None,
                        help="Existing dataset root for source-root validation")
    args = parser.parse_args()

    # --- CLI safety gates ---

    if args.execute and args.dry_run:
        print("ERROR: --execute and --dry-run are mutually exclusive.", file=sys.stderr)
        sys.exit(2)

    if not args.dry_run and not args.execute:
        print("ERROR: Either --dry-run or --execute is required.", file=sys.stderr)
        sys.exit(2)

    if args.execute and not args.confirm_copy_tier1000:
        print("ERROR: --execute requires --confirm-copy-tier1000 safety flag.", file=sys.stderr)
        sys.exit(2)

    if args.execute and not args.source_root:
        print("ERROR: --execute requires --source-root for source-root validation.", file=sys.stderr)
        sys.exit(2)

    if args.execute and not args.existing_root:
        print("ERROR: --execute requires --existing-root for source-root validation.", file=sys.stderr)
        sys.exit(2)

    manifest_path = Path(args.manifest)
    target_root = Path(args.target_root)

    # Build approved source roots list
    approved_source_roots = None
    if args.source_root or args.existing_root:
        approved_source_roots = []
        if args.source_root:
            approved_source_roots.append(Path(args.source_root))
        if args.existing_root:
            approved_source_roots.append(Path(args.existing_root))

    mode_label = "execute (copy)" if args.execute else "dry-run (validation only)"
    print(f"Manifest:    {manifest_path}")
    print(f"Target root: {target_root}")
    print(f"Mode:        {mode_label}")
    if args.source_root:
        print(f"Source root: {args.source_root}")
    if args.existing_root:
        print(f"Existing root: {args.existing_root}")
    print()

    # --- Phase 0: Read manifest once (TOCTOU prevention) ---
    manifest_rows, read_err = read_manifest_rows(manifest_path)
    if read_err is not None:
        print(f"ERROR: {read_err}", file=sys.stderr)
        sys.exit(1)

    # --- Phase 1: Validation ---
    result = validate_manifest(
        manifest_path, target_root,
        approved_source_roots=approved_source_roots,
        rows=manifest_rows,
    )

    print("=== Staging Validation ===")
    print(f"  Total manifest rows:       {result['total_rows']}")
    print(f"  Existing (Tier-500):       {result['existing_tier500_rows']}")
    print(f"  New candidates:            {result['new_candidate_rows']}")
    print(f"  Excluded:                  {result['excluded_rows']}")
    print(f"  Truncated rows:            {result['truncated_rows']}")
    print(f"  Source files found:        {result['source_files_exist']}")
    print(f"  Source files missing:      {result['source_files_missing']}")
    print(f"  Target collisions:         {result['target_filename_collisions']}")
    print(f"  Target-root escapes:       {result['target_root_escapes']}")
    print(f"  Unsupported extensions:    {result['unsupported_extensions']}")
    print(f"  Blank source paths:        {result['blank_source_paths']}")
    print(f"  Blank target paths:        {result['blank_target_paths']}")
    print(f"  Blank extensions:          {result['blank_extensions']}")
    print(f"  Suffix missing:            {result['suffix_missing']}")
    print(f"  Extension mismatches:      {result['extension_mismatches']}")
    print(f"  Source root violations:    {result['source_root_violations']}")
    print(f"  Target files exist on disk: {result['target_existing_files']}")
    print(f"  Cloud availability risks:  {result['cloud_risk_files']}")
    print(f"  Total copy size:           {_fmt_bytes(result['total_copy_bytes'])}")
    print(f"  Target root exists:        {result['target_root_exists']}")
    print()

    copy_count = result["existing_tier500_rows"] + result["new_candidate_rows"]
    print(f"  Files to copy (total):     {copy_count}")

    if result["warnings"]:
        print()
        print("  Warnings:")
        for w in result["warnings"]:
            print(f"    - {w}")

    if result["errors"]:
        print()
        print("  Errors:")
        for e in result["errors"]:
            print(f"    - {e}")

    print()
    if result["valid"]:
        print("  RESULT: VALID — manifest is safe to stage")
    else:
        print("  RESULT: INVALID — fix errors before proceeding")

    if args.dry_run:
        print()
        print("  [DRY-RUN] No files were copied.")
        sys.exit(0 if result["valid"] else 1)

    # --- Phase 2: Execute copy ---
    if not result["valid"]:
        print()
        print("  [EXECUTE] Validation FAILED — aborting copy.")
        sys.exit(1)

    print()
    print("=" * 50)
    print("=== Executing Copy ===")
    print(f"  Target: {target_root}")
    print(f"  Expected files: {copy_count}")
    print()

    copy_res = execute_copy(
        manifest_path, target_root,
        approved_source_roots=[Path(args.source_root), Path(args.existing_root)],
        rows=manifest_rows,
    )

    print(f"  Rows processed: {copy_res['total_rows']}")
    print(f"  Files copied:   {copy_res['copied']}")
    print(f"  Excluded:       {copy_res['skipped_excluded']}")
    print(f"  Truncated:      {copy_res['skipped_truncated']}")
    print(f"  Failed:         {copy_res['failed']}")
    if copy_res.get("failed_reason_code"):
        print(f"  Failure code:   {copy_res['failed_reason_code']}")
    print(f"  Bytes copied:   {_fmt_bytes(copy_res['total_bytes_copied'])}")

    if copy_res["errors"]:
        print()
        print("  Copy errors:")
        for e in copy_res["errors"]:
            print(f"    - {e}")

    if copy_res["failed"] > 0:
        print()
        print(f"  COPY FAILED at: {copy_res['failed_path']}")
        print(f"  Reason: {copy_res['failed_reason']}")
        print(f"  Files copied before failure: {copy_res['copied']}")
        sys.exit(3)

    # Post-copy integrity checks (before audit)
    if copy_res["copied"] != copy_count:
        print()
        print(f"  ERROR: copied {copy_res['copied']} files, expected {copy_count}")
        sys.exit(3)

    if copy_res["skipped_truncated"] > 0:
        print()
        print(f"  ERROR: {copy_res['skipped_truncated']} truncated rows encountered during execute")
        sys.exit(3)

    # --- Phase 3: Post-copy audit ---
    print()
    print("=" * 50)
    print("=== Post-Copy Audit ===")

    audit = post_copy_audit(target_root)

    print(f"  Directory exists: {audit['exists']}")
    print(f"  Total files:      {audit['total_files']}")
    print(f"  Total bytes:      {_fmt_bytes(audit['total_bytes'])}")
    print(f"  Extensions:")
    for ext, count in sorted(audit["extension_counts"].items()):
        print(f"    {ext}: {count}")

    if audit["unexpected_extensions"]:
        print(f"  Unexpected extensions: {len(audit['unexpected_extensions'])}")
        for p in audit["unexpected_extensions"][:5]:
            print(f"    - {p}")

    print(f"  Sample files ({len(audit['sample_files'])}):")
    for p in audit["sample_files"]:
        print(f"    - {p}")

    # Summary — hard-fail on any audit discrepancy
    print()
    print("=" * 50)

    audit_ok = True

    if audit["total_files"] != copy_count:
        print(f"  ERROR: Expected {copy_count} files, found {audit['total_files']}")
        audit_ok = False

    if audit["unexpected_extensions"]:
        print(f"  ERROR: {len(audit['unexpected_extensions'])} files with unexpected extensions")
        audit_ok = False

    if audit_ok:
        print(f"  SUCCESS: {audit['total_files']} files staged, {_fmt_bytes(audit['total_bytes'])}")
        sys.exit(0)
    else:
        print()
        print("  Post-copy audit FAILED.")
        sys.exit(4)


if __name__ == "__main__":
    main()
