#!/usr/bin/env python3
"""Phase 3.8d-I6 staging copy retry with the I5c backfilled manifest.

This operational runner validates the local I5c backfilled manifest, proves the
staging target is empty and disjoint from protected roots, runs the existing
staging copy dry-run gate, and only executes the copy when that gate passes.
It never imports into DB or runs downstream jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import stat
import sys
import time
from collections import Counter
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
from app.utils.cloud_files import (  # noqa: E402
    classify_cloud_file_state,
    classify_file_access_error,
)
from audit_cloud_availability import read_manifest, row_size, safe_row_label  # noqa: E402
from run_phase38d_i5_hydration_audit import COPY_SELECTION_REASONS  # noqa: E402
import stage_pilot_files as stage_pilot  # noqa: E402


DEFAULT_MANIFEST = (
    REPO_ROOT / ".local_manifests" / "phase-3.8d-i5c-backfilled-selected-manifest.csv"
)
DEFAULT_DEFERRED_LEDGER = (
    REPO_ROOT / ".local_manifests" / "phase-3.8d-i5c-deferred-cloud-recovery-ledger.json"
)
DEFAULT_I5C_SUMMARY = (
    REPO_ROOT / "docs" / "reports" / "phase-3.8d-i5c-backfill-application-summary.json"
)
DEFAULT_I3_LOCAL_DETAILS = (
    REPO_ROOT / ".local_manifests" / "phase-3.8d-i3-recovery-local-details.json"
)
DEFAULT_REPORT_JSON = (
    REPO_ROOT / "docs" / "reports" / "phase-3.8d-i6-staging-copy-retry-summary.json"
)
DEFAULT_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i6-staging-copy-retry.md"
DEFAULT_LOCAL_DETAILS = (
    REPO_ROOT / ".local_manifests" / "phase-3.8d-i6-staging-copy-retry-details.json"
)
DEFAULT_LOCAL_LOG = REPO_ROOT / ".local_manifests" / "phase-3.8d-i6-staging-copy-retry.log"
DEFAULT_ITEM_LEDGER = REPO_ROOT / ".local_manifests" / "phase-3.8d-i6-staging-copy-item-ledger.json"

FAILED_ROWS = {98, 881}
REPLACEMENT_ROWS = {1029, 1041}
WINDOWS_REPARSE_POINT = 0x00000400
CLOUD_AWARE_CONFIRM_PHRASE = "COPY_PHASE38D_BACKFILLED_STAGING_WITH_CLOUD_RECALL"
DEFAULT_FAILURE_BUDGET = {
    "max_item_failures": 20,
    "max_failure_rate": 0.05,
    "max_consecutive_failures": 10,
    "max_same_reason_failures": 20,
}
ITEM_LEVEL_DRY_RUN_FIELDS = {
    "source_files_missing",
    "unsupported_extensions",
    "cloud_risk_files",
}
EXPECTED_BUCKET_DISTRIBUTION = {
    "b01": 63,
    "b02": 63,
    "b03": 63,
    "b04": 63,
    "b05": 63,
    "b06": 63,
    "b07": 63,
    "b08": 63,
    "b09": 62,
    "b10": 62,
    "b11": 62,
    "b12": 62,
    "b13": 62,
    "b14": 62,
    "b15": 62,
    "b16": 62,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(payload), encoding="utf-8")


def append_log(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line.rstrip() + "\n")


def row_id(row: Mapping[str, str]) -> int:
    try:
        return int(row.get("row_id") or 0)
    except (TypeError, ValueError):
        return 0


def is_selected_copy_row(row: Mapping[str, str]) -> bool:
    return (row.get("selection_reason") or "").strip() in COPY_SELECTION_REASONS and not (
        row.get("exclusion_reason") or ""
    ).strip()


def selected_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    return sorted([row for row in rows if is_selected_copy_row(row)], key=row_id)


def _path_key(path: str | Path) -> str:
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        resolved = Path(path)
    return os.path.normcase(str(resolved))


def _is_under_or_equal(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_strictly_under(child: Path, parent: Path) -> bool:
    return _path_key(child) != _path_key(parent) and _is_under_or_equal(child, parent)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _counter_dict(values: Sequence[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _single_target_root(rows: Sequence[dict[str, str]]) -> tuple[Path | None, list[str]]:
    roots: set[str] = set()
    errors: list[str] = []
    for row in selected_rows(rows):
        target = (row.get("proposed_target_path") or "").strip()
        if not target:
            errors.append(f"selected_row_{row_id(row)}_missing_target_path")
            continue
        target_path = Path(target)
        if not target_path.is_absolute():
            errors.append(f"selected_row_{row_id(row)}_target_path_not_absolute")
            continue
        roots.add(str(target_path.parent))
    if errors:
        return None, errors
    if not roots:
        return None, ["no_selected_target_paths"]
    if len(roots) != 1:
        return None, ["ambiguous_selected_target_roots"]
    return Path(next(iter(roots))), []


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def load_i3_context(path: Path) -> dict[str, Any]:
    data = load_json(path)
    cleanup = data.get("cleanup") if isinstance(data, dict) else {}
    if not isinstance(cleanup, dict):
        cleanup = {}
    protected = cleanup.get("protected_roots") or []
    if not isinstance(protected, list):
        protected = []
    return {
        "expected_staging_root": cleanup.get("expected_staging_root"),
        "protected_roots": [
            {"label": item.get("label"), "path": item.get("path")}
            for item in protected
            if isinstance(item, dict) and item.get("label") and item.get("path")
        ],
    }


def parse_protected_root(value: str) -> dict[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--protected-root must use label=path")
    label, path = value.split("=", 1)
    label = label.strip()
    path = path.strip()
    if not label or not path:
        raise argparse.ArgumentTypeError("--protected-root requires non-empty label and path")
    return {"label": label, "path": path}


def _resolve_directory(path: Path) -> tuple[Path | None, str | None]:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        return None, f"resolve_failed_{type(exc).__name__}"
    if not resolved.exists():
        return None, "missing"
    if not resolved.is_dir():
        return None, "not_directory"
    return resolved, None


def _entry_is_reparse(stat_result: os.stat_result) -> bool:
    attrs = getattr(stat_result, "st_file_attributes", 0)
    return bool(attrs & WINDOWS_REPARSE_POINT)


def root_escape_hazards(path: Path) -> list[str]:
    hazards: list[str] = []
    try:
        stat_result = path.lstat()
    except OSError as exc:
        return [f"target_root_lstat_failed_{type(exc).__name__}"]
    if path.is_symlink():
        hazards.append("target_root_symlink")
    if _entry_is_reparse(stat_result):
        hazards.append("target_root_reparse_point")
    return hazards


def scan_tree(root: Path) -> dict[str, Any]:
    files: dict[str, int] = {}
    hazards: list[dict[str, Any]] = []
    total_bytes = 0
    if not root.exists():
        return {
            "exists": False,
            "file_count": 0,
            "total_bytes": 0,
            "files": files,
            "hazards": hazards,
        }
    for current, dirs, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for dirname in dirs:
            dpath = current_path / dirname
            try:
                dstat = dpath.lstat()
            except OSError as exc:
                hazards.append({"safe_label": f"tree_entry_{len(hazards) + 1:04d}", "reason": f"stat_failed_{type(exc).__name__}"})
                continue
            if dpath.is_symlink():
                hazards.append({"safe_label": f"tree_entry_{len(hazards) + 1:04d}", "reason": "symlink_directory"})
                continue
            if _entry_is_reparse(dstat):
                hazards.append({"safe_label": f"tree_entry_{len(hazards) + 1:04d}", "reason": "reparse_directory"})
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for filename in filenames:
            fpath = current_path / filename
            try:
                fstat = fpath.lstat()
            except OSError as exc:
                hazards.append({"safe_label": f"tree_entry_{len(hazards) + 1:04d}", "reason": f"stat_failed_{type(exc).__name__}"})
                continue
            rel = fpath.relative_to(root).as_posix()
            if fpath.is_symlink():
                hazards.append({"safe_label": rel, "reason": "symlink_file"})
                continue
            if _entry_is_reparse(fstat):
                hazards.append({"safe_label": rel, "reason": "reparse_file"})
                continue
            if not stat.S_ISREG(fstat.st_mode):
                hazards.append({"safe_label": rel, "reason": "non_regular_file"})
                continue
            if getattr(fstat, "st_nlink", 1) > 1:
                hazards.append({"safe_label": rel, "reason": "hardlink_file"})
                continue
            files[rel] = int(fstat.st_size)
            total_bytes += int(fstat.st_size)
    return {
        "exists": root.is_dir(),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "hazards": hazards,
    }


def expected_file_map(rows: Sequence[dict[str, str]], target_root: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    expected: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        resolved_root = target_root.resolve()
    except (OSError, RuntimeError) as exc:
        return {}, [f"target_root_resolve_failed_{type(exc).__name__}"]
    for row in selected_rows(rows):
        target = (row.get("proposed_target_path") or "").strip()
        if not target:
            errors.append(f"row_{row_id(row)}_missing_target")
            continue
        try:
            target_path = Path(target).resolve()
            rel = target_path.relative_to(resolved_root).as_posix()
        except (OSError, RuntimeError, ValueError):
            errors.append(f"row_{row_id(row)}_target_escape")
            continue
        if rel in expected:
            errors.append(f"duplicate_expected_target_{rel}")
            continue
        expected[rel] = {
            "row_id": row_id(row),
            "safe_label": safe_row_label(row, prefix="source"),
            "size_bytes": row_size(row),
            "extension": (row.get("extension") or "").lower(),
            "bucket": row.get("temporal_bucket") or "unknown",
        }
    return expected, errors


def validate_backfilled_manifest(
    rows: Sequence[dict[str, str]],
    *,
    target_root: Path,
    expected_selected_total: int,
    i5c_summary: Mapping[str, Any],
    deferred_ledger: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    active = selected_rows(rows)
    active_ids = {row_id(row) for row in active}
    errors: list[str] = []
    bucket_distribution = _counter_dict([(row.get("temporal_bucket") or "unknown") for row in active])
    extension_distribution = _counter_dict([(row.get("extension") or "").lower() for row in active])
    total_bytes = sum(row_size(row) for row in active)
    duplicate_sources = [path for path, count in Counter(_path_key(row.get("source_path", "")) for row in active).items() if path and count > 1]
    duplicate_targets = [
        path for path, count in Counter(_path_key(row.get("proposed_target_path", "")) for row in active).items() if path and count > 1
    ]
    duplicate_keys = [key for key, count in Counter((row.get("duplicate_key") or "").strip() for row in active).items() if key and count > 1]
    expected_targets, target_errors = expected_file_map(rows, target_root)
    errors.extend(target_errors)
    if len(active) != expected_selected_total:
        errors.append(f"selected_total_expected_{expected_selected_total}_actual_{len(active)}")
    for failed_id in FAILED_ROWS:
        if failed_id in active_ids:
            errors.append(f"failed_row_{failed_id}_still_active_selected")
    for replacement_id in REPLACEMENT_ROWS:
        if replacement_id not in active_ids:
            errors.append(f"replacement_row_{replacement_id}_not_active_selected")
    if duplicate_sources:
        errors.append(f"duplicate_source_path_count_{len(duplicate_sources)}")
    if duplicate_targets:
        errors.append(f"duplicate_target_path_count_{len(duplicate_targets)}")
    if duplicate_keys:
        errors.append(f"duplicate_detectable_hash_key_count_{len(duplicate_keys)}")
    if len(expected_targets) != len(active):
        errors.append("expected_target_map_count_mismatch")
    summary_after = i5c_summary.get("bucket_distribution_after") if isinstance(i5c_summary, dict) else None
    expected_distribution = summary_after if isinstance(summary_after, dict) and summary_after else EXPECTED_BUCKET_DISTRIBUTION
    if bucket_distribution != expected_distribution:
        errors.append("bucket_distribution_mismatch")
    ledger_rows = deferred_ledger.get("rows", []) if isinstance(deferred_ledger, dict) else []
    ledger_ids = {int(item.get("row_id")) for item in ledger_rows if isinstance(item, dict) and str(item.get("row_id", "")).isdigit()}
    if not FAILED_ROWS.issubset(ledger_ids):
        errors.append("deferred_ledger_missing_failed_rows")
    validation = {
        "status": "passed" if not errors else "failed",
        "selected_total": len(active),
        "expected_selected_total": expected_selected_total,
        "failed_rows_absent": sorted(FAILED_ROWS - active_ids) == sorted(FAILED_ROWS),
        "replacement_rows_present": sorted(REPLACEMENT_ROWS & active_ids),
        "bucket_distribution": bucket_distribution,
        "bucket_distribution_unchanged_from_i5c": bucket_distribution == expected_distribution,
        "extension_distribution": extension_distribution,
        "expected_total_bytes": total_bytes,
        "duplicate_source_count": len(duplicate_sources),
        "duplicate_target_path_count": len(duplicate_targets),
        "duplicate_detectable_hash_key_count": len(duplicate_keys),
        "deferred_ledger_rows": sorted(ledger_ids & FAILED_ROWS),
        "errors": errors,
    }
    return validation, errors


def validate_target_safety(
    target_root: Path,
    *,
    expected_staging_root: Path | None,
    protected_roots: Sequence[Mapping[str, str]],
) -> tuple[dict[str, Any], list[str], list[Path]]:
    errors: list[str] = []
    resolved_protected: list[Path] = []
    protected_public: list[dict[str, Any]] = []
    target_resolved, target_error = _resolve_directory(target_root)
    if target_error:
        errors.append(f"target_root_{target_error}")
    else:
        for hazard in root_escape_hazards(target_root):
            errors.append(hazard)
    expected_resolved = None
    if expected_staging_root is None:
        errors.append("missing_expected_staging_root")
    else:
        expected_resolved, expected_error = _resolve_directory(expected_staging_root)
        if expected_error:
            errors.append(f"expected_staging_root_{expected_error}")
    if target_resolved and expected_resolved:
        if not _is_under_or_equal(target_resolved, expected_resolved):
            errors.append("target_not_under_expected_staging_root")
    required_labels = {"source_root", "repo_root", "app_storage_root"}
    labels_seen: set[str] = set()
    for item in protected_roots:
        label = str(item.get("label") or "").strip()
        raw_path = str(item.get("path") or "").strip()
        if not label or not raw_path:
            continue
        labels_seen.add(label)
        resolved, reason = _resolve_directory(Path(raw_path))
        ok = reason is None
        protected_public.append({"label": label, "valid_directory": ok, "error_reason": reason})
        if not ok:
            errors.append(f"protected_root_{label}_{reason}")
        else:
            assert resolved is not None
            resolved_protected.append(resolved)
    missing_required = sorted(required_labels - labels_seen)
    for label in missing_required:
        errors.append(f"missing_protected_root_{label}")
    if target_resolved:
        for protected in resolved_protected:
            if _path_key(target_resolved) == _path_key(protected):
                errors.append("target_protected_root_exact_overlap")
            elif _is_strictly_under(target_resolved, protected):
                errors.append("target_inside_protected_root")
            elif _is_strictly_under(protected, target_resolved):
                errors.append("protected_root_inside_target")
    tree = scan_tree(target_root)
    if tree["hazards"]:
        errors.append("target_tree_hazard_detected")
    if tree["file_count"] != 0:
        errors.append("staging_target_not_empty")
    check = {
        "status": "passed" if not errors else "failed",
        "target_label": "phase_3_8d_i6_backfilled_staging_target",
        "target_exists": bool(target_root.exists()),
        "target_is_directory": bool(target_root.is_dir()),
        "target_root_hazard_count": sum(
            1 for error in errors if error.startswith("target_root_symlink") or error.startswith("target_root_reparse")
        ),
        "expected_staging_root_provided": expected_staging_root is not None,
        "target_under_expected_staging_root": bool(
            target_resolved and expected_resolved and _is_under_or_equal(target_resolved, expected_resolved)
        ),
        "target_equals_expected_staging_root": bool(
            target_resolved and expected_resolved and _path_key(target_resolved) == _path_key(expected_resolved)
        ),
        "protected_roots": protected_public,
        "protected_root_overlap": any(
            error in {"target_protected_root_exact_overlap", "target_inside_protected_root", "protected_root_inside_target"}
            for error in errors
        ),
        "file_count": tree["file_count"],
        "total_bytes": tree["total_bytes"],
        "hazard_count": len(tree["hazards"]),
        "errors": errors,
    }
    return check, errors, resolved_protected


def summarize_dry_run(
    result: Mapping[str, Any],
    *,
    expected_copy_count: int,
    rows: Sequence[dict[str, str]],
    allow_item_level_failures: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    active_ids = {row_id(row) for row in selected_rows(rows)}
    copy_count = _safe_int(result.get("existing_tier500_rows")) + _safe_int(result.get("new_candidate_rows"))
    errors: list[str] = []
    if copy_count != expected_copy_count:
        errors.append(f"copy_count_expected_{expected_copy_count}_actual_{copy_count}")
    required_zero_fields = (
        "target_root_escapes",
        "target_filename_collisions",
        "source_root_violations",
        "target_existing_files",
        "blank_source_paths",
        "blank_target_paths",
        "blank_extensions",
        "invalid_selection_reasons",
        "invalid_exclusion_reasons",
        "extension_mismatches",
        "suffix_missing",
        "truncated_rows",
    )
    for field in required_zero_fields:
        if _safe_int(result.get(field)) != 0:
            errors.append(f"{field}_nonzero")
    item_level_counts = {
        "source_files_missing": _safe_int(result.get("source_files_missing")),
        "unsupported_extensions": _safe_int(result.get("unsupported_extensions")),
        "cloud_risk_files": _safe_int(result.get("cloud_risk_files")),
    }
    if not allow_item_level_failures:
        for field, count in item_level_counts.items():
            if count:
                errors.append("cloud_availability_files_nonzero" if field == "cloud_risk_files" else f"{field}_nonzero")
    if FAILED_ROWS & active_ids:
        errors.append("failed_rows_present_in_dry_run_rows")
    if not REPLACEMENT_ROWS.issubset(active_ids):
        errors.append("replacement_rows_missing_in_dry_run_rows")
    status = "passed"
    if errors:
        status = "failed"
    elif allow_item_level_failures and any(item_level_counts.values()):
        status = "passed_with_item_level_risks"
    summary = {
        "status": status,
        "stage_pilot_valid": bool(result.get("valid")),
        "expected_copy_rows": expected_copy_count,
        "copy_rows": copy_count,
        "existing_tier500_rows": _safe_int(result.get("existing_tier500_rows")),
        "new_candidate_rows": _safe_int(result.get("new_candidate_rows")),
        "source_files_missing": _safe_int(result.get("source_files_missing")),
        "unsupported_extensions": _safe_int(result.get("unsupported_extensions")),
        "target_root_escapes": _safe_int(result.get("target_root_escapes")),
        "target_collisions": _safe_int(result.get("target_filename_collisions")),
        "duplicate_target_paths": _safe_int(result.get("target_filename_collisions")),
        "source_root_violations": _safe_int(result.get("source_root_violations")),
        "target_existing_files": _safe_int(result.get("target_existing_files")),
        "cloud_risk_files": _safe_int(result.get("cloud_risk_files")),
        "cloud_availability_reason_counts": result.get("cloud_risk_by_reason") or {},
        "item_level_risk_counts": item_level_counts,
        "item_level_failures_allowed": allow_item_level_failures,
        "total_copy_bytes": _safe_int(result.get("total_copy_bytes")),
        "rows_98_881_absent": not bool(FAILED_ROWS & active_ids),
        "rows_1029_1041_present": sorted(REPLACEMENT_ROWS & active_ids),
        "errors": errors,
        "stage_pilot_error_count": len(result.get("errors") or []),
    }
    return summary, errors


def audit_staged_files(
    rows: Sequence[dict[str, str]],
    *,
    target_root: Path,
    expected_copy_count: int,
    item_ledger: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected, expected_errors = expected_file_map(rows, target_root)
    all_expected = dict(expected)
    if item_ledger is not None:
        staged_row_ids = {int(item.get("row_id")) for item in item_ledger if item.get("status") == "staged"}
        failed_row_ids = {int(item.get("row_id")) for item in item_ledger if item.get("status") != "staged"}
        expected = {rel: item for rel, item in expected.items() if int(item["row_id"]) in staged_row_ids}
    else:
        staged_row_ids = {int(item["row_id"]) for item in expected.values()}
        failed_row_ids = set()
    actual = scan_tree(target_root)
    actual_files: dict[str, int] = actual["files"]
    expected_keys = set(expected)
    actual_keys = set(actual_files)
    missing = sorted(expected_keys - actual_keys)
    unexpected = sorted(actual_keys - expected_keys)
    size_mismatches = sorted(
        rel
        for rel in expected_keys & actual_keys
        if int(expected[rel]["size_bytes"]) != int(actual_files[rel])
    )
    expected_bytes = sum(int(item["size_bytes"]) for item in expected.values())
    errors: list[str] = list(expected_errors)
    if actual["hazards"]:
        errors.append("post_copy_tree_hazard_detected")
    if actual["file_count"] != len(expected):
        errors.append("post_copy_file_count_mismatch")
    if actual["total_bytes"] != expected_bytes:
        errors.append("post_copy_total_bytes_mismatch")
    if missing:
        errors.append("post_copy_missing_files")
    if unexpected:
        errors.append("post_copy_unexpected_files")
    if size_mismatches:
        errors.append("post_copy_size_mismatch")
    replacement_staged = sorted(REPLACEMENT_ROWS & staged_row_ids)
    failed_original_staged = sorted(
        int(item["row_id"])
        for rel, item in all_expected.items()
        if int(item["row_id"]) in FAILED_ROWS and rel in actual_keys
    )
    public = {
        "status": "passed" if not errors else "failed",
        "target_label": "phase_3_8d_i6_backfilled_staging_target",
        "expected_selected_total": expected_copy_count,
        "expected_success_file_count": len(expected),
        "actual_file_count": actual["file_count"],
        "expected_total_bytes": expected_bytes,
        "actual_total_bytes": actual["total_bytes"],
        "known_failed_item_count": len(failed_row_ids),
        "missing_due_to_known_failed_item_count": len(failed_row_ids),
        "missing_file_count": len(missing),
        "unexpected_file_count": len(unexpected),
        "size_mismatch_count": len(size_mismatches),
        "hazard_count": len(actual["hazards"]),
        "rows_98_881_staged": failed_original_staged,
        "rows_1029_1041_staged": replacement_staged,
        "errors": errors,
    }
    local = {
        "expected_files": expected,
        "actual_files": actual_files,
        "missing_files": missing,
        "unexpected_files": unexpected,
        "size_mismatches": size_mismatches,
        "hazards": actual["hazards"],
    }
    return public, local


def cloud_state_summary(path: Path) -> dict[str, Any]:
    state = classify_cloud_file_state(path)
    return {
        "exists": state.exists,
        "is_file": state.is_file,
        "likely_cloud_placeholder": state.likely_cloud_placeholder,
        "offline": state.offline,
        "recall_on_open": state.recall_on_open,
        "recall_on_data_access": state.recall_on_data_access,
        "reparse_point": state.reparse_point,
        "sparse_file": state.sparse_file,
        "error_code": state.error_code,
    }


def item_status_from_reason(reason: str) -> str:
    mapping = {
        "cloud_hydration_failed": "failed_cloud_hydration",
        "cloud_network_unavailable": "failed_cloud_hydration",
        "cloud_provider_timeout": "failed_timeout",
        "cloud_recall_on_open": "failed_cloud_hydration",
        "cloud_recall_on_data_access": "failed_cloud_hydration",
        "cloud_offline": "failed_cloud_hydration",
        "read_timeout": "failed_timeout",
        "permission_denied": "failed_permission",
        "source_missing": "failed_missing_source",
        "unsupported_extension": "skipped_unsupported",
        "size_mismatch": "failed_size_mismatch",
    }
    return mapping.get(reason, "failed_generic_read")


def failure_budget_exceeded(
    *,
    item_failure_count: int,
    total_selected: int,
    consecutive_failures: int,
    reason_counts: Mapping[str, int],
    budget: Mapping[str, Any],
) -> bool:
    max_item_failures = int(budget["max_item_failures"])
    max_failure_rate = float(budget["max_failure_rate"])
    max_consecutive_failures = int(budget["max_consecutive_failures"])
    max_same_reason_failures = int(budget["max_same_reason_failures"])
    if item_failure_count > max_item_failures:
        return True
    if total_selected > 0 and (item_failure_count / total_selected) > max_failure_rate:
        return True
    if consecutive_failures > max_consecutive_failures:
        return True
    return any(count > max_same_reason_failures for count in reason_counts.values())


def _blank_ledger_item(row: Mapping[str, str], target_root: Path) -> dict[str, Any]:
    rid = row_id(row)
    target = Path((row.get("proposed_target_path") or ""))
    try:
        target_label = target.resolve().relative_to(target_root.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError):
        target_label = f"target_row_{rid:04d}"
    return {
        "row_id": rid,
        "safe_label": safe_row_label(row, prefix="source"),
        "bucket": row.get("temporal_bucket") or "unknown",
        "extension": (row.get("extension") or "").lower(),
        "expected_size": row_size(row),
        "source_cloud_state_summary": {},
        "target_safe_label": target_label,
        "status": "deferred",
        "reason": None,
        "bytes_copied": 0,
        "staging_target_exists": False,
        "eligible_for_db_import": False,
    }


def copy_with_item_failure_budget(
    rows: Sequence[dict[str, str]],
    *,
    target_root: Path,
    approved_source_roots: Sequence[Path],
    budget: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.monotonic()
    selected = selected_rows(rows)
    total_selected = len(selected)
    try:
        resolved_root = target_root.resolve()
        resolved_sources = [root.resolve() for root in approved_source_roots]
    except (OSError, RuntimeError) as exc:
        return (
            {
                "attempted": True,
                "status": "structural_failure",
                "structural_failure": True,
                "structural_reason": f"root_resolve_failed_{type(exc).__name__}",
                "attempted_count": 0,
                "staged_success_count": 0,
                "item_failure_count": 0,
                "failure_rate": 0.0,
                "copied_bytes": 0,
                "budget_exceeded": False,
            },
            [],
        )

    ledger: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    consecutive_failures = 0
    max_consecutive_observed = 0
    staged_success_count = 0
    copied_bytes = 0
    item_failure_count = 0
    attempted_count = 0
    budget_exceeded = False
    structural_failure = False
    structural_reason: str | None = None

    for row in selected:
        attempted_count += 1
        item = _blank_ledger_item(row, target_root)
        source = Path(row.get("source_path") or "")
        target = Path(row.get("proposed_target_path") or "")
        item["source_cloud_state_summary"] = cloud_state_summary(source)
        expected_size = row_size(row)
        ext = (row.get("extension") or "").lower()

        def mark_failed(reason: str, *, bytes_copied: int = 0) -> None:
            nonlocal item_failure_count, consecutive_failures, max_consecutive_observed
            item["status"] = item_status_from_reason(reason)
            item["reason"] = reason
            item["bytes_copied"] = bytes_copied
            item["staging_target_exists"] = target.exists()
            item["eligible_for_db_import"] = False
            item_failure_count += 1
            reason_counts[reason] += 1
            consecutive_failures += 1
            max_consecutive_observed = max(max_consecutive_observed, consecutive_failures)

        if ext not in stage_pilot.SUPPORTED_EXTENSIONS:
            mark_failed("unsupported_extension")
            ledger.append(item)
        elif not source.exists():
            mark_failed("source_missing")
            ledger.append(item)
        elif not source.is_file():
            mark_failed("unreadable_source")
            ledger.append(item)
        else:
            try:
                resolved_source = source.resolve()
                if not any(_is_under_or_equal(resolved_source, root) for root in resolved_sources):
                    structural_failure = True
                    structural_reason = "source_outside_approved_roots"
                    item["status"] = "deferred"
                    item["reason"] = structural_reason
                    ledger.append(item)
                    break
                resolved_target = target.resolve()
                resolved_target.relative_to(resolved_root)
            except (OSError, RuntimeError, ValueError):
                structural_failure = True
                structural_reason = "target_escape_or_resolve_failed"
                item["status"] = "deferred"
                item["reason"] = structural_reason
                ledger.append(item)
                break
            if target.exists():
                structural_failure = True
                structural_reason = "target_already_exists"
                item["status"] = "deferred"
                item["reason"] = structural_reason
                ledger.append(item)
                break
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(target))
                copied = int(target.stat().st_size)
                if expected_size and copied != expected_size:
                    mark_failed("size_mismatch", bytes_copied=copied)
                else:
                    item["status"] = "staged"
                    item["reason"] = None
                    item["bytes_copied"] = copied
                    item["staging_target_exists"] = target.exists()
                    item["eligible_for_db_import"] = True
                    staged_success_count += 1
                    copied_bytes += copied
                    consecutive_failures = 0
            except (OSError, shutil.SameFileError) as exc:
                state = classify_cloud_file_state(source)
                mark_failed(classify_file_access_error(exc, state))
            ledger.append(item)

        if item["status"] != "staged" and not structural_failure:
            budget_exceeded = failure_budget_exceeded(
                item_failure_count=item_failure_count,
                total_selected=total_selected,
                consecutive_failures=consecutive_failures,
                reason_counts=reason_counts,
                budget=budget,
            )
            if budget_exceeded:
                break

    for row in selected[attempted_count:]:
        skipped = _blank_ledger_item(row, target_root)
        skipped["status"] = "deferred"
        skipped["reason"] = "not_attempted_after_budget_or_structural_stop"
        ledger.append(skipped)

    failure_rate = item_failure_count / total_selected if total_selected else 0.0
    if structural_failure:
        status = "structural_failure"
    elif budget_exceeded:
        status = "blocked_failure_budget_exceeded"
    elif item_failure_count:
        status = "completed_with_item_failures"
    else:
        status = "staging_copy_passed"
    public_failures = [
        {
            "row_id": item["row_id"],
            "safe_label": item["safe_label"],
            "bucket": item["bucket"],
            "extension": item["extension"],
            "reason": item["reason"],
            "status": item["status"],
        }
        for item in ledger
        if item["status"] != "staged" and item["reason"]
    ]
    return (
        {
            "attempted": True,
            "status": status,
            "duration_seconds": round(time.monotonic() - started, 3),
            "attempted_count": attempted_count,
            "staged_success_count": staged_success_count,
            "item_failure_count": item_failure_count,
            "failure_rate": round(failure_rate, 6),
            "max_consecutive_failures_observed": max_consecutive_observed,
            "failure_reason_distribution": dict(sorted(reason_counts.items())),
            "failed_rows": public_failures,
            "budget_exceeded": budget_exceeded,
            "structural_failure": structural_failure,
            "structural_reason": structural_reason,
            "copied_files": staged_success_count,
            "copied_bytes": copied_bytes,
            "failed": item_failure_count,
            "failed_reason_code": public_failures[0]["reason"] if public_failures else None,
            "failed_safe_label": public_failures[0]["safe_label"] if public_failures else None,
        },
        ledger,
    )


def collect_db_counts() -> dict[str, Any]:
    try:
        from sqlalchemy import func
        from app import database
        from app.models import AITagJob, ClassificationJob, Media, TagTranslationJob, blombooru_media_tags

        database.init_engine()
        if database.SessionLocal is None:
            return {"available": False, "error": "database_not_initialized"}
        db = database.SessionLocal()
        try:
            return {
                "available": True,
                "media": int(db.query(func.count(Media.id)).scalar() or 0),
                "media_tags": int(db.query(func.count()).select_from(blombooru_media_tags).scalar() or 0),
                "ai_jobs": int(db.query(func.count(AITagJob.id)).scalar() or 0),
                "classification_jobs": int(db.query(func.count(ClassificationJob.id)).scalar() or 0),
                "translation_jobs": int(db.query(func.count(TagTranslationJob.id)).scalar() or 0),
            }
        finally:
            db.close()
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}


def db_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("media", "media_tags", "ai_jobs", "classification_jobs", "translation_jobs")
    return {
        "available": bool(before.get("available")) and bool(after.get("available")),
        "before": {field: before.get(field) for field in fields},
        "after": {field: after.get(field) for field in fields},
        "delta": {field: _safe_int(after.get(field)) - _safe_int(before.get(field)) for field in fields},
        "unchanged": bool(before.get("available"))
        and bool(after.get("available"))
        and all(_safe_int(after.get(field)) - _safe_int(before.get(field)) == 0 for field in fields),
    }


def run_staging_copy_retry(
    *,
    manifest_path: Path,
    deferred_ledger_path: Path,
    i5c_summary_path: Path,
    i3_local_details_path: Path,
    target_root: Path | None,
    expected_staging_root: Path | None,
    protected_roots: Sequence[Mapping[str, str]],
    expected_selected_total: int,
    execute: bool,
    confirm_copy_tier1000: bool,
    allow_cloud_recall_copy: bool = False,
    confirm_cloud_aware_copy: str | None = None,
    failure_budget: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    started = time.monotonic()
    failure_budget = dict(DEFAULT_FAILURE_BUDGET | dict(failure_budget or {}))
    db_before = collect_db_counts()
    status = "copy_pending" if execute else "dry_run_passed"
    copy_result_public = {
        "attempted": False,
        "status": "not_run_dry_run_mode",
        "attempted_count": 0,
        "staged_success_count": 0,
        "item_failure_count": 0,
        "failure_rate": 0.0,
        "max_consecutive_failures_observed": 0,
        "failure_reason_distribution": {},
        "failed_rows": [],
        "budget_exceeded": False,
        "copied_files": 0,
        "copied_bytes": 0,
        "failed": 0,
        "failed_reason_code": None,
        "failed_safe_label": None,
    }
    copy_result_local: dict[str, Any] = {}
    item_ledger: list[dict[str, Any]] = []
    post_copy_public = {
        "status": "not_run",
        "expected_selected_total": expected_selected_total,
        "expected_success_file_count": None,
        "actual_file_count": 0,
        "expected_total_bytes": None,
        "actual_total_bytes": 0,
        "known_failed_item_count": 0,
        "missing_due_to_known_failed_item_count": 0,
        "missing_file_count": None,
        "unexpected_file_count": None,
        "size_mismatch_count": None,
        "rows_98_881_staged": [],
        "rows_1029_1041_staged": [],
    }
    post_copy_local: dict[str, Any] = {}
    rows: list[dict[str, str]] = []
    local_context = load_i3_context(i3_local_details_path)
    if expected_staging_root is None and local_context.get("expected_staging_root"):
        expected_staging_root = Path(str(local_context["expected_staging_root"]))
    effective_protected = list(protected_roots) or list(local_context.get("protected_roots") or [])
    i5c_summary = load_json(i5c_summary_path)
    deferred_ledger = load_json(deferred_ledger_path)
    setup_errors: list[str] = []
    if not manifest_path.is_file():
        setup_errors.append("blocked_missing_backfilled_manifest")
    if not deferred_ledger_path.is_file():
        setup_errors.append("blocked_missing_deferred_ledger")
    if not setup_errors:
        rows = read_manifest(manifest_path)
    derived_target, target_errors = _single_target_root(rows) if rows else (None, [])
    setup_errors.extend(target_errors)
    if target_root is None:
        target_root = derived_target
    if target_root is None:
        setup_errors.append("target_root_not_available")
        target_root = Path(".")
    manifest_validation, manifest_errors = validate_backfilled_manifest(
        rows,
        target_root=target_root,
        expected_selected_total=expected_selected_total,
        i5c_summary=i5c_summary,
        deferred_ledger=deferred_ledger,
    ) if rows else (
        {
            "status": "failed",
            "selected_total": 0,
            "expected_selected_total": expected_selected_total,
            "failed_rows_absent": False,
            "replacement_rows_present": [],
            "bucket_distribution": {},
            "bucket_distribution_unchanged_from_i5c": False,
            "extension_distribution": {},
            "expected_total_bytes": 0,
            "duplicate_source_count": 0,
            "duplicate_target_path_count": 0,
            "duplicate_detectable_hash_key_count": 0,
            "deferred_ledger_rows": [],
            "errors": ["manifest_not_loaded"],
        },
        ["manifest_not_loaded"],
    )
    target_check, target_errors, resolved_protected = validate_target_safety(
        target_root,
        expected_staging_root=expected_staging_root,
        protected_roots=effective_protected,
    )
    source_roots = [
        Path(str(item["path"]))
        for item in effective_protected
        if str(item.get("label")) in {"source_root", "existing_root"}
    ]
    if not source_roots:
        target_errors.append("missing_approved_source_root")
        target_check["errors"] = [*target_check.get("errors", []), "missing_approved_source_root"]
        target_check["status"] = "failed"
    dry_run_raw: dict[str, Any] = {}
    dry_run_public: dict[str, Any] = {
        "status": "not_run",
        "stage_pilot_valid": False,
        "expected_copy_rows": expected_selected_total,
        "copy_rows": 0,
        "existing_tier500_rows": 0,
        "new_candidate_rows": 0,
        "source_files_missing": 0,
        "unsupported_extensions": 0,
        "target_root_escapes": 0,
        "target_collisions": 0,
        "duplicate_target_paths": 0,
        "source_root_violations": 0,
        "target_existing_files": 0,
        "cloud_risk_files": 0,
        "cloud_availability_reason_counts": {},
        "item_level_risk_counts": {},
        "item_level_failures_allowed": allow_cloud_recall_copy,
        "total_copy_bytes": 0,
        "rows_98_881_absent": False,
        "rows_1029_1041_present": [],
        "errors": [],
        "stage_pilot_error_count": 0,
    }
    dry_run_errors: list[str] = []
    if setup_errors or manifest_errors or target_errors:
        status = "blocked_preflight_failed"
        dry_run_errors = []
    else:
        dry_run_raw = stage_pilot.validate_manifest(
            manifest_path,
            target_root,
            approved_source_roots=source_roots,
            rows=rows,
        )
        dry_run_public, dry_run_errors = summarize_dry_run(
            dry_run_raw,
            expected_copy_count=expected_selected_total,
            rows=rows,
            allow_item_level_failures=allow_cloud_recall_copy,
        )
        if dry_run_errors:
            status = "blocked_dry_run_failed"
        elif execute:
            status = "copy_ready"
    if execute and not confirm_copy_tier1000:
        status = "blocked_missing_copy_confirmation"
    if execute and allow_cloud_recall_copy and confirm_cloud_aware_copy != CLOUD_AWARE_CONFIRM_PHRASE:
        status = "blocked_missing_cloud_aware_copy_confirmation"
    if execute and status == "copy_ready":
        copy_result_public, item_ledger = copy_with_item_failure_budget(
            rows,
            target_root=target_root,
            approved_source_roots=source_roots,
            budget=failure_budget,
        )
        copy_result_local = {"item_ledger": item_ledger}
        if copy_result_public["status"] == "structural_failure":
            status = "blocked_structural_copy_failure"
        elif copy_result_public["status"] == "blocked_failure_budget_exceeded":
            status = "blocked_failure_budget_exceeded"
        else:
            post_copy_public, post_copy_local = audit_staged_files(
                rows,
                target_root=target_root,
                expected_copy_count=expected_selected_total,
                item_ledger=item_ledger,
            )
            if copy_result_public["status"] == "staging_copy_passed" and post_copy_public["status"] == "passed":
                status = "staging_copy_passed"
            elif copy_result_public["status"] == "completed_with_item_failures" and post_copy_public["status"] == "passed":
                post_copy_public["status"] = "completed_with_item_failures"
                status = "completed_with_item_failures"
            else:
                status = "blocked_post_copy_audit_failed"
    elif execute and status != "copy_ready":
        copy_result_public["status"] = "not_run_blocked_before_copy"
    elif not execute and not dry_run_errors and not setup_errors and not manifest_errors and not target_errors:
        status = "dry_run_passed"
    if not execute:
        if status == "dry_run_passed":
            copy_result_public["status"] = "not_requested_after_dry_run"
        elif status == "blocked_dry_run_failed":
            copy_result_public["status"] = "not_run_dry_run_failed"
        elif status.startswith("blocked_"):
            copy_result_public["status"] = "not_run_blocked_before_copy"
    db_after = collect_db_counts()
    db_no_mutation = db_delta(db_before, db_after)
    db_import_eligibility = {
        "eligible_for_full_1000_import_planning": status == "staging_copy_passed"
        and copy_result_public.get("staged_success_count") == expected_selected_total
        and post_copy_public.get("status") == "passed",
        "eligible_for_partial_import_planning": status == "completed_with_item_failures",
        "full_import_blocked_reason": None,
    }
    if not db_import_eligibility["eligible_for_full_1000_import_planning"]:
        if copy_result_public.get("staged_success_count", 0) < expected_selected_total:
            db_import_eligibility["full_import_blocked_reason"] = "staged_success_count_less_than_selected_total"
        elif status.startswith("blocked_"):
            db_import_eligibility["full_import_blocked_reason"] = status
        else:
            db_import_eligibility["full_import_blocked_reason"] = "post_copy_audit_not_full_pass"
    success = status in {"staging_copy_passed", "completed_with_item_failures"} or (
        not execute and status == "dry_run_passed"
    )
    public_report = {
        "phase": "3.8d-I6",
        "mode": "staging_copy_retry_with_backfilled_manifest",
        "created_at": utc_now(),
        "status": status,
        "success": success,
        "duration_seconds": round(time.monotonic() - started, 3),
        "setup": {
            "backfilled_manifest_present": manifest_path.is_file(),
            "deferred_ledger_present": deferred_ledger_path.is_file(),
            "i5c_summary_present": i5c_summary_path.is_file(),
            "i3_local_context_present": i3_local_details_path.is_file(),
            "errors": setup_errors,
        },
        "manifest_validation": manifest_validation,
        "pre_copy_target_check": target_check,
        "cloud_aware_copy_policy": {
            "enabled": allow_cloud_recall_copy,
            "confirmation_required": allow_cloud_recall_copy,
            "confirmation_phrase_accepted": bool(
                allow_cloud_recall_copy and confirm_cloud_aware_copy == CLOUD_AWARE_CONFIRM_PHRASE
            ),
            "cloud_recall_rows_are_metadata_level_not_proven_failures": True,
        },
        "failure_budget": failure_budget,
        "dry_run": dry_run_public,
        "actual_staging_copy": copy_result_public,
        "post_copy_audit": post_copy_public,
        "db_import_eligibility": db_import_eligibility,
        "db_no_mutation": db_no_mutation,
        "source_icloud_statement": {
            "source_content_read_for_staging_copy_only": bool(copy_result_public.get("attempted")),
            "source_write_mutation": False,
            "provider_side_hydration_cache_may_have_occurred": bool(copy_result_public.get("attempted")),
        },
        "app_managed_storage_statement": {
            "mutation": False,
            "statement": "No app-managed storage write path was invoked; staging copy target is disjoint from app storage labels.",
        },
        "safety": {
            "db_import": False,
            "db_mutation": False,
            "classification": False,
            "ai_tagging": False,
            "localization": False,
            "entity_resolver": False,
            "similarity": False,
            "cleanup_delete": False,
            "source_icloud_write_mutation": False,
            "app_managed_storage_mutation": False,
            "full_phase38d_execute": False,
            "push_main": False,
            "merge": False,
        },
        "local_artifacts": {
            "details": DEFAULT_LOCAL_DETAILS.name,
            "log": DEFAULT_LOCAL_LOG.name,
            "item_ledger": DEFAULT_ITEM_LEDGER.name,
            "must_remain_untracked": True,
        },
        "next_step": (
            "Proceed to separately approved DB import planning."
            if status == "staging_copy_passed"
            else (
                "Stop: item failures stayed within budget; user/ChatGPT must approve backfill or partial import planning."
                if status == "completed_with_item_failures"
                else "Stop: staging copy retry did not complete; do not import DB or run downstream jobs."
            )
        ),
    }
    public = sanitize_public_obj(public_report)
    leaks = find_privacy_leaks(public)
    public["privacy"] = {
        "paths_redacted": True,
        "safe_labels_only": True,
        "leaks": leaks,
        "passed": not leaks,
    }
    if leaks:
        public["status"] = "blocked_privacy_leak"
        public["success"] = False
        status = "blocked_privacy_leak"
    local_details = {
        "created_at": utc_now(),
        "manifest_path": str(manifest_path),
        "target_root": str(target_root),
        "expected_staging_root": str(expected_staging_root) if expected_staging_root else None,
        "protected_roots": list(effective_protected),
        "source_roots": [str(path) for path in source_roots],
        "dry_run_raw": dry_run_raw,
        "copy_result_raw": copy_result_local,
        "item_ledger": item_ledger,
        "post_copy_audit_local": post_copy_local,
        "db_before": db_before,
        "db_after": db_after,
    }
    exit_code = 0 if public["success"] else 1
    return public, local_details, exit_code


def render_markdown(report: Mapping[str, Any]) -> str:
    setup = report.get("setup") or {}
    manifest = report.get("manifest_validation") or {}
    target = report.get("pre_copy_target_check") or {}
    policy = report.get("cloud_aware_copy_policy") or {}
    budget = report.get("failure_budget") or {}
    dry_run = report.get("dry_run") or {}
    copy = report.get("actual_staging_copy") or {}
    post = report.get("post_copy_audit") or {}
    eligibility = report.get("db_import_eligibility") or {}
    db = report.get("db_no_mutation") or {}
    source = report.get("source_icloud_statement") or {}
    app_storage = report.get("app_managed_storage_statement") or {}
    safety = report.get("safety") or {}
    privacy = report.get("privacy") or {}
    lines = [
        "# Phase 3.8d-I6 Staging Copy Retry",
        "",
        "## Summary",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Success: `{report.get('success')}`",
        f"- Backfilled manifest present: `{setup.get('backfilled_manifest_present')}`",
        f"- Deferred ledger present: `{setup.get('deferred_ledger_present')}`",
        f"- Setup errors: `{json.dumps(setup.get('errors') or [], ensure_ascii=False)}`",
        f"- Duration seconds: `{report.get('duration_seconds')}`",
        "",
        "## Manifest Validation",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Selected total: `{manifest.get('selected_total')}`",
        f"- Expected selected total: `{manifest.get('expected_selected_total')}`",
        f"- Failed rows absent: `{manifest.get('failed_rows_absent')}`",
        f"- Replacement rows present: `{manifest.get('replacement_rows_present')}`",
        f"- Bucket distribution unchanged from I5c: `{manifest.get('bucket_distribution_unchanged_from_i5c')}`",
        f"- Extension distribution: `{json.dumps(manifest.get('extension_distribution') or {}, sort_keys=True)}`",
        f"- Expected total bytes: `{manifest.get('expected_total_bytes')}`",
        f"- Duplicate source paths: `{manifest.get('duplicate_source_count')}`",
        f"- Duplicate target paths: `{manifest.get('duplicate_target_path_count')}`",
        f"- Deferred ledger rows: `{manifest.get('deferred_ledger_rows')}`",
        f"- Errors: `{json.dumps(manifest.get('errors') or [], ensure_ascii=False)}`",
        "",
        "## Pre-copy Target Check",
        "",
        f"- Status: `{target.get('status')}`",
        f"- Target label: `{target.get('target_label')}`",
        f"- Target exists: `{target.get('target_exists')}`",
        f"- Target is directory: `{target.get('target_is_directory')}`",
        f"- Target under expected staging root: `{target.get('target_under_expected_staging_root')}`",
        f"- Target equals expected staging root: `{target.get('target_equals_expected_staging_root')}`",
        f"- File count before copy: `{target.get('file_count')}`",
        f"- Bytes before copy: `{target.get('total_bytes')}`",
        f"- Hazard count: `{target.get('hazard_count')}`",
        f"- Target root hazard count: `{target.get('target_root_hazard_count')}`",
        f"- Errors: `{json.dumps(target.get('errors') or [], ensure_ascii=False)}`",
        "",
        "## Cloud-aware Copy Policy",
        "",
        f"- Enabled: `{policy.get('enabled')}`",
        f"- Confirmation phrase accepted: `{policy.get('confirmation_phrase_accepted')}`",
        f"- Recall-risk rows are metadata-level cloud-backed rows, not proven failures: `{policy.get('cloud_recall_rows_are_metadata_level_not_proven_failures')}`",
        f"- Failure budget: `{json.dumps(budget, sort_keys=True)}`",
        "",
        "## Dry-run",
        "",
        f"- Status: `{dry_run.get('status')}`",
        f"- Stage pilot valid: `{dry_run.get('stage_pilot_valid')}`",
        f"- Expected copy rows: `{dry_run.get('expected_copy_rows')}`",
        f"- Copy rows: `{dry_run.get('copy_rows')}`",
        f"- Source files missing: `{dry_run.get('source_files_missing')}`",
        f"- Unsupported extensions: `{dry_run.get('unsupported_extensions')}`",
        f"- Target escapes: `{dry_run.get('target_root_escapes')}`",
        f"- Target collisions: `{dry_run.get('target_collisions')}`",
        f"- Cloud risk files: `{dry_run.get('cloud_risk_files')}`",
        f"- Item-level failures allowed: `{dry_run.get('item_level_failures_allowed')}`",
        f"- Item-level risk counts: `{json.dumps(dry_run.get('item_level_risk_counts') or {}, sort_keys=True)}`",
        f"- Cloud risk by reason: `{json.dumps(dry_run.get('cloud_availability_reason_counts') or {}, sort_keys=True)}`",
        f"- Rows 98/881 absent: `{dry_run.get('rows_98_881_absent')}`",
        f"- Rows 1029/1041 present: `{dry_run.get('rows_1029_1041_present')}`",
        f"- Errors: `{json.dumps(dry_run.get('errors') or [], ensure_ascii=False)}`",
        "",
        "## Actual Staging Copy",
        "",
        f"- Attempted: `{copy.get('attempted')}`",
        f"- Status: `{copy.get('status')}`",
        f"- Attempted count: `{copy.get('attempted_count')}`",
        f"- Staged success count: `{copy.get('staged_success_count')}`",
        f"- Item failure count: `{copy.get('item_failure_count')}`",
        f"- Failure rate: `{copy.get('failure_rate')}`",
        f"- Failure budget exceeded: `{copy.get('budget_exceeded')}`",
        f"- Max consecutive failures observed: `{copy.get('max_consecutive_failures_observed')}`",
        f"- Failure reason distribution: `{json.dumps(copy.get('failure_reason_distribution') or {}, sort_keys=True)}`",
        f"- Copied files: `{copy.get('copied_files')}`",
        f"- Copied bytes: `{copy.get('copied_bytes')}`",
        f"- Failed rows: `{json.dumps(copy.get('failed_rows') or [], ensure_ascii=False)}`",
        "",
        "## Post-copy Audit",
        "",
        f"- Status: `{post.get('status')}`",
        f"- Expected selected total: `{post.get('expected_selected_total')}`",
        f"- Expected success file count: `{post.get('expected_success_file_count')}`",
        f"- Actual file count: `{post.get('actual_file_count')}`",
        f"- Expected total bytes: `{post.get('expected_total_bytes')}`",
        f"- Actual total bytes: `{post.get('actual_total_bytes')}`",
        f"- Known failed item count: `{post.get('known_failed_item_count')}`",
        f"- Missing due to known failed items: `{post.get('missing_due_to_known_failed_item_count')}`",
        f"- Missing file count: `{post.get('missing_file_count')}`",
        f"- Unexpected file count: `{post.get('unexpected_file_count')}`",
        f"- Size mismatch count: `{post.get('size_mismatch_count')}`",
        f"- Rows 98/881 staged: `{post.get('rows_98_881_staged')}`",
        f"- Rows 1029/1041 staged: `{post.get('rows_1029_1041_staged')}`",
        f"- Errors: `{json.dumps(post.get('errors') or [], ensure_ascii=False)}`",
        "",
        "## DB Import Eligibility",
        "",
        f"- Eligible for full 1000 import planning: `{eligibility.get('eligible_for_full_1000_import_planning')}`",
        f"- Eligible for partial import planning: `{eligibility.get('eligible_for_partial_import_planning')}`",
        f"- Full import blocked reason: `{eligibility.get('full_import_blocked_reason')}`",
        "",
        "## DB No-mutation Proof",
        "",
        f"- Available: `{db.get('available')}`",
        f"- Before: `{json.dumps(db.get('before') or {}, sort_keys=True)}`",
        f"- After: `{json.dumps(db.get('after') or {}, sort_keys=True)}`",
        f"- Delta: `{json.dumps(db.get('delta') or {}, sort_keys=True)}`",
        f"- Unchanged: `{db.get('unchanged')}`",
        "",
        "## Safety",
        "",
        f"- Source/iCloud write mutation: `{source.get('source_write_mutation')}`",
        f"- Source content read for staging copy only: `{source.get('source_content_read_for_staging_copy_only')}`",
        f"- Provider-side hydration/cache may have occurred: `{source.get('provider_side_hydration_cache_may_have_occurred')}`",
        f"- App-managed storage mutation: `{app_storage.get('mutation')}`",
        f"- DB import: `{safety.get('db_import')}`",
        f"- Classification: `{safety.get('classification')}`",
        f"- AI tagging: `{safety.get('ai_tagging')}`",
        f"- Localization: `{safety.get('localization')}`",
        f"- Entity Resolver: `{safety.get('entity_resolver')}`",
        f"- Similarity: `{safety.get('similarity')}`",
        f"- Cleanup/delete: `{safety.get('cleanup_delete')}`",
        f"- Push main: `{safety.get('push_main')}`",
        f"- Merge: `{safety.get('merge')}`",
        "",
        "## Privacy",
        "",
        f"- Passed: `{privacy.get('passed')}`",
        f"- Leaks: `{json.dumps(privacy.get('leaks') or [], ensure_ascii=False)}`",
        "",
        "## Next Step",
        "",
        str(report.get("next_step") or ""),
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Phase 3.8d-I6 staging copy retry.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--deferred-ledger", type=Path, default=DEFAULT_DEFERRED_LEDGER)
    parser.add_argument("--i5c-summary", type=Path, default=DEFAULT_I5C_SUMMARY)
    parser.add_argument("--i3-local-details", type=Path, default=DEFAULT_I3_LOCAL_DETAILS)
    parser.add_argument("--target-root", type=Path, default=None)
    parser.add_argument("--expected-staging-root", type=Path, default=None)
    parser.add_argument("--protected-root", action="append", type=parse_protected_root, default=[])
    parser.add_argument("--expected-selected-total", type=int, default=1000)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-copy-tier1000", action="store_true")
    parser.add_argument("--allow-cloud-recall-copy", action="store_true")
    parser.add_argument("--confirm-cloud-aware-copy", default=None)
    parser.add_argument("--max-item-failures", type=int, default=DEFAULT_FAILURE_BUDGET["max_item_failures"])
    parser.add_argument("--max-failure-rate", type=float, default=DEFAULT_FAILURE_BUDGET["max_failure_rate"])
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=DEFAULT_FAILURE_BUDGET["max_consecutive_failures"],
    )
    parser.add_argument(
        "--max-same-reason-failures",
        type=int,
        default=DEFAULT_FAILURE_BUDGET["max_same_reason_failures"],
    )
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--local-details-json", type=Path, default=DEFAULT_LOCAL_DETAILS)
    parser.add_argument("--local-log", type=Path, default=DEFAULT_LOCAL_LOG)
    parser.add_argument("--item-ledger-json", type=Path, default=DEFAULT_ITEM_LEDGER)
    args = parser.parse_args(argv)

    failure_budget = {
        "max_item_failures": args.max_item_failures,
        "max_failure_rate": args.max_failure_rate,
        "max_consecutive_failures": args.max_consecutive_failures,
        "max_same_reason_failures": args.max_same_reason_failures,
    }
    report, local_details, exit_code = run_staging_copy_retry(
        manifest_path=args.manifest,
        deferred_ledger_path=args.deferred_ledger,
        i5c_summary_path=args.i5c_summary,
        i3_local_details_path=args.i3_local_details,
        target_root=args.target_root,
        expected_staging_root=args.expected_staging_root,
        protected_roots=args.protected_root,
        expected_selected_total=args.expected_selected_total,
        execute=args.execute,
        confirm_copy_tier1000=args.confirm_copy_tier1000,
        allow_cloud_recall_copy=args.allow_cloud_recall_copy,
        confirm_cloud_aware_copy=args.confirm_cloud_aware_copy,
        failure_budget=failure_budget,
    )
    write_json(args.report_json, report)
    write_markdown(args.report_md, report)
    write_json(args.local_details_json, local_details)
    write_json(
        args.item_ledger_json,
        {
            "created_at": utc_now(),
            "phase": "3.8d-I6",
            "status": report["status"],
            "rows": local_details.get("item_ledger", []),
        },
    )
    append_log(
        args.local_log,
        [
            f"{utc_now()} phase=3.8d-I6 status={report['status']} success={report['success']}",
            f"actual_copy_status={report['actual_staging_copy']['status']} staged={report['actual_staging_copy']['staged_success_count']} failed={report['actual_staging_copy']['item_failure_count']} bytes={report['actual_staging_copy']['copied_bytes']}",
            f"post_copy_status={report['post_copy_audit']['status']}",
        ],
    )
    print(json.dumps({"status": report["status"], "success": report["success"]}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
