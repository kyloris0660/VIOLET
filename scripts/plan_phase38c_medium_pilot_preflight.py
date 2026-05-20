#!/usr/bin/env python3
"""Phase 3.8c medium pilot candidate preflight and dry-run planner.

This phase is read-only against source, staging, storage, and DB state.  It
generates a local full-path candidate manifest plus privacy-safe public reports
for a future guarded execute phase.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, TextIO


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.classification_first_workflow import (  # noqa: E402
    DEFAULT_SOURCE_LABEL,
    NULL_POLICY_HARD_FAIL,
    WorkflowScope,
    build_dry_run_report,
    collect_mutation_snapshot,
    compare_mutation_snapshots,
    find_privacy_leaks,
    sanitize_public_obj,
    utc_now,
    workflow_stage_contracts,
    write_json_report,
)


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
PLACEHOLDER_SIZE_THRESHOLD = 1024
DEFAULT_PREVIOUS_MANIFEST = REPO_ROOT / ".local_manifests" / "phase-3.3a.1-candidate-manifest.csv"
DEFAULT_CANDIDATE_MANIFEST = REPO_ROOT / ".local_manifests" / "phase-3.8c-medium-candidate-manifest.csv"
DEFAULT_REPORT_JSON = REPO_ROOT / "docs" / "reports" / "phase-3.8c-medium-pilot-preflight-summary.json"
DEFAULT_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8c-medium-pilot-preflight.md"
DEFAULT_FUTURE_SOURCE_LABEL = "violet:medium1000:phase3.8d"
EXECUTE_REJECTION = "Phase 3.8c supports candidate preflight dry-run only; execute is deferred to Phase 3.8d."


@dataclass
class CandidateEntry:
    path: Path
    relative_key: str
    filename: str
    extension: str
    size_bytes: int
    mtime_epoch: float | None
    timestamp_source: str
    temporal_bucket: str = "timestamp_unknown"
    selection_reason: str = ""
    exclusion_reason: str = ""
    duplicate_key: str = ""


@dataclass(frozen=True)
class TreeSnapshot:
    exists: bool
    file_count: int
    total_bytes: int
    stat_errors: int


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative integer")
    return parsed


def _positive_int(value: str) -> int:
    parsed = _non_negative_int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _path_key(path: Path) -> str:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        resolved = path
    return os.path.normcase(str(resolved))


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def validate_output_paths(paths: Sequence[Path], protected_roots: Sequence[Path]) -> None:
    resolved_outputs: list[Path] = []
    for path in paths:
        try:
            resolved_outputs.append(path.resolve())
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Cannot resolve output path: {exc}") from exc

    if len({os.path.normcase(str(path)) for path in resolved_outputs}) != len(resolved_outputs):
        raise ValueError("output paths must be distinct")

    for output in resolved_outputs:
        for root in protected_roots:
            if _is_inside(output, root):
                raise ValueError("output/report paths must not be written inside source or staging roots")


def snapshot_tree(root: Path) -> TreeSnapshot:
    if not root.exists():
        return TreeSnapshot(exists=False, file_count=0, total_bytes=0, stat_errors=0)
    if not root.is_dir():
        return TreeSnapshot(exists=False, file_count=0, total_bytes=0, stat_errors=1)

    file_count = 0
    total_bytes = 0
    stat_errors = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(dirnames, key=str.lower)
        for filename in sorted(filenames, key=str.lower):
            path = Path(dirpath) / filename
            try:
                stat = path.stat()
            except OSError:
                stat_errors += 1
                continue
            file_count += 1
            total_bytes += int(stat.st_size)
    return TreeSnapshot(exists=True, file_count=file_count, total_bytes=total_bytes, stat_errors=stat_errors)


def _safe_iso_from_epoch(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _is_placeholder(path: Path, size_bytes: int) -> bool:
    if size_bytes == 0:
        return True
    if path.suffix.lower() == ".icloud" or path.name.startswith("."):
        return True
    return size_bytes < PLACEHOLDER_SIZE_THRESHOLD


def load_prior_manifest_index(previous_manifest: Path) -> tuple[set[str], set[tuple[str, int]]]:
    if not previous_manifest.is_file():
        raise ValueError(f"previous manifest not found: {previous_manifest}")

    prior_paths: set[str] = set()
    prior_duplicate_keys: set[tuple[str, int]] = set()
    with previous_manifest.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            selection = (row.get("selection_reason") or "").strip()
            source_path = (row.get("source_path") or "").strip()
            if selection not in {"existing_tier500", "new_candidate"} or not source_path:
                continue
            size_text = (row.get("size_bytes") or "0").strip()
            try:
                size_bytes = int(size_text)
            except ValueError:
                size_bytes = 0
            path = Path(source_path)
            prior_paths.add(_path_key(path))
            prior_duplicate_keys.add((path.name.lower(), size_bytes))
    return prior_paths, prior_duplicate_keys


def _scan_source_entries(
    source_root: Path,
    *,
    prior_paths: set[str],
    prior_duplicate_keys: set[tuple[str, int]],
) -> tuple[list[CandidateEntry], list[CandidateEntry], Counter[str]]:
    candidates: list[CandidateEntry] = []
    excluded: list[CandidateEntry] = []
    exclusion_counts: Counter[str] = Counter()

    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = sorted([name for name in dirnames if not name.startswith(".")], key=str.lower)
        for filename in sorted(filenames, key=str.lower):
            path = Path(dirpath) / filename
            try:
                relative_key = str(path.relative_to(source_root)).replace("\\", "/")
            except ValueError:
                relative_key = path.name
            extension = path.suffix.lower()
            size_bytes = 0
            mtime_epoch: float | None = None
            timestamp_source = "filesystem_mtime"
            stat_error = False
            try:
                stat = path.stat()
                size_bytes = int(stat.st_size)
                if stat.st_mtime > 0:
                    mtime_epoch = float(stat.st_mtime)
                else:
                    timestamp_source = "timestamp_unknown"
            except OSError:
                stat_error = True
                timestamp_source = "timestamp_unknown"

            entry = CandidateEntry(
                path=path,
                relative_key=relative_key,
                filename=filename,
                extension=extension,
                size_bytes=size_bytes,
                mtime_epoch=mtime_epoch,
                timestamp_source=timestamp_source,
            )

            reason = ""
            if stat_error:
                reason = "stat_error"
            elif filename.startswith("."):
                reason = "hidden"
            elif extension not in SUPPORTED_EXTENSIONS:
                reason = f"unsupported_format:{extension or '<none>'}"
            elif _is_placeholder(path, size_bytes):
                reason = "placeholder"
            elif _path_key(path) in prior_paths:
                reason = "already_imported_prior_manifest"
            elif (filename.lower(), size_bytes) in prior_duplicate_keys:
                reason = "duplicate_prior_manifest_key"

            if reason:
                entry.exclusion_reason = reason
                entry.duplicate_key = reason if reason.startswith("duplicate") else ""
                excluded.append(entry)
                exclusion_counts[reason] += 1
            else:
                candidates.append(entry)

    return candidates, excluded, exclusion_counts


def build_temporal_buckets(candidates: Sequence[CandidateEntry], bucket_count: int) -> list[dict[str, Any]]:
    known = sorted(
        [entry for entry in candidates if entry.mtime_epoch is not None],
        key=lambda entry: (entry.mtime_epoch or 0, entry.relative_key.lower()),
    )
    if not known:
        return []

    actual_count = min(max(1, bucket_count), len(known))
    buckets: list[dict[str, Any]] = []
    for index in range(actual_count):
        start = math.floor(index * len(known) / actual_count)
        end = math.floor((index + 1) * len(known) / actual_count)
        entries = known[start:end]
        if not entries:
            continue
        label = f"b{index + 1:02d}"
        for entry in entries:
            entry.temporal_bucket = label
        buckets.append(
            {
                "label": label,
                "candidate_count": len(entries),
                "start_time_utc": _safe_iso_from_epoch(entries[0].mtime_epoch),
                "end_time_utc": _safe_iso_from_epoch(entries[-1].mtime_epoch),
                "entries": entries,
            }
        )
    return buckets


def _allocate_evenly(bucket_sizes: Mapping[str, int], target_count: int) -> dict[str, int]:
    labels = list(bucket_sizes)
    if target_count <= 0 or not labels:
        return {label: 0 for label in labels}
    base = target_count // len(labels)
    remainder = target_count % len(labels)
    allocations = {
        label: min(bucket_sizes[label], base + (1 if index < remainder else 0))
        for index, label in enumerate(labels)
    }
    remaining = target_count - sum(allocations.values())
    while remaining > 0:
        candidates = [
            (bucket_sizes[label] - allocations[label], label)
            for label in labels
            if bucket_sizes[label] > allocations[label]
        ]
        if not candidates:
            break
        candidates.sort(key=lambda item: (-item[0], item[1]))
        _, label = candidates[0]
        allocations[label] += 1
        remaining -= 1
    return allocations


def select_temporal_stratified_candidates(
    candidates: Sequence[CandidateEntry],
    *,
    planned_new_count: int,
    bucket_count: int,
    seed: int,
    timestamp_unknown_cap: int,
) -> tuple[list[CandidateEntry], dict[str, Any]]:
    unknown = sorted(
        [entry for entry in candidates if entry.mtime_epoch is None],
        key=lambda entry: entry.relative_key.lower(),
    )
    buckets = build_temporal_buckets(candidates, bucket_count)
    rng = random.Random(seed)

    unknown_selected_count = min(len(unknown), timestamp_unknown_cap, planned_new_count)
    selected_unknown = rng.sample(unknown, unknown_selected_count) if unknown_selected_count else []
    for entry in selected_unknown:
        entry.selection_reason = "new_candidate"
        entry.temporal_bucket = "timestamp_unknown"

    known_target = max(0, planned_new_count - len(selected_unknown))
    bucket_sizes = {bucket["label"]: bucket["candidate_count"] for bucket in buckets}
    allocations = _allocate_evenly(bucket_sizes, known_target)

    selected_known: list[CandidateEntry] = []
    for bucket in buckets:
        entries = list(bucket["entries"])
        count = allocations.get(bucket["label"], 0)
        chosen = entries if count >= len(entries) else rng.sample(entries, count)
        for entry in chosen:
            entry.selection_reason = "new_candidate"
        selected_known.extend(chosen)

    selected = sorted(
        [*selected_known, *selected_unknown],
        key=lambda entry: (entry.temporal_bucket, entry.mtime_epoch or 0, entry.relative_key.lower()),
    )

    selected_by_bucket = Counter(entry.temporal_bucket for entry in selected)
    candidate_by_bucket = Counter({bucket["label"]: bucket["candidate_count"] for bucket in buckets})
    if unknown:
        candidate_by_bucket["timestamp_unknown"] = len(unknown)

    bucket_public = [
        {
            "label": bucket["label"],
            "candidate_count": bucket["candidate_count"],
            "selected_count": selected_by_bucket.get(bucket["label"], 0),
            "start_time_utc": bucket["start_time_utc"],
            "end_time_utc": bucket["end_time_utc"],
        }
        for bucket in buckets
    ]
    if unknown:
        bucket_public.append(
            {
                "label": "timestamp_unknown",
                "candidate_count": len(unknown),
                "selected_count": selected_by_bucket.get("timestamp_unknown", 0),
                "start_time_utc": None,
                "end_time_utc": None,
            }
        )

    known_selected_buckets = [
        label for label in selected_by_bucket if label != "timestamp_unknown" and selected_by_bucket[label] > 0
    ]
    temporal_diversity_passed = bool(buckets) and (
        len(known_selected_buckets) == len(buckets)
        if known_target >= len(buckets)
        else len(known_selected_buckets) >= min(2, len(buckets))
    )

    summary = {
        "strategy": "filesystem_mtime_quantile_stratified",
        "seed": seed,
        "planned_new_count": planned_new_count,
        "temporal_bucket_count": len(buckets),
        "candidate_count_by_bucket": dict(sorted(candidate_by_bucket.items())),
        "selected_count_by_bucket": dict(sorted(selected_by_bucket.items())),
        "timestamp_unknown_count": len(unknown),
        "timestamp_unknown_selected": len(selected_unknown),
        "timestamp_unknown_cap": timestamp_unknown_cap,
        "bucket_details": bucket_public,
        "temporal_diversity_check": {
            "passed": temporal_diversity_passed,
            "known_selected_bucket_count": len(known_selected_buckets),
            "known_bucket_count": len(buckets),
            "not_directory_order": True,
            "not_newest_or_oldest_only": temporal_diversity_passed,
        },
    }
    return selected, summary


def _unique_target_path(target_root: Path, filename: str, source_path: Path, used_names: set[str]) -> Path:
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = filename
    if candidate.lower() not in used_names:
        used_names.add(candidate.lower())
        return target_root / candidate

    salt = 0
    while True:
        digest = hashlib.sha256(f"{source_path}|{filename}|{salt}".encode("utf-8")).hexdigest()[:8]
        candidate = f"{stem}__{digest}{suffix}"
        if candidate.lower() not in used_names:
            used_names.add(candidate.lower())
            return target_root / candidate
        salt += 1


def _manifest_row(
    row_id: int,
    entry: CandidateEntry,
    *,
    target_path: Path | None,
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "source_path": str(entry.path),
        "proposed_target_path": str(target_path or ""),
        "extension": entry.extension,
        "size_bytes": entry.size_bytes,
        "selection_reason": entry.selection_reason,
        "duplicate_key": entry.duplicate_key,
        "exclusion_reason": entry.exclusion_reason,
        "placeholder_flag": str(entry.exclusion_reason == "placeholder"),
        "stat_error": str(entry.exclusion_reason == "stat_error"),
        "temporal_bucket": entry.temporal_bucket,
        "timestamp_source": entry.timestamp_source,
        "modified_time_utc": _safe_iso_from_epoch(entry.mtime_epoch) or "",
    }


def build_manifest_rows(
    *,
    selected: Sequence[CandidateEntry],
    candidates: Sequence[CandidateEntry],
    excluded: Sequence[CandidateEntry],
    target_root: Path,
) -> list[dict[str, Any]]:
    selected_keys = {_path_key(entry.path) for entry in selected}
    rows: list[dict[str, Any]] = []
    used_names: set[str] = set()
    row_id = 0

    for entry in selected:
        row_id += 1
        target_path = _unique_target_path(target_root, entry.filename, entry.path, used_names)
        rows.append(_manifest_row(row_id, entry, target_path=target_path))

    for entry in candidates:
        if _path_key(entry.path) in selected_keys:
            continue
        row_id += 1
        entry.exclusion_reason = "not_selected_temporal_stratified"
        rows.append(_manifest_row(row_id, entry, target_path=None))

    for entry in excluded:
        row_id += 1
        rows.append(_manifest_row(row_id, entry, target_path=None))

    return rows


def write_candidate_manifest(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "row_id",
        "source_path",
        "proposed_target_path",
        "extension",
        "size_bytes",
        "selection_reason",
        "duplicate_key",
        "exclusion_reason",
        "placeholder_flag",
        "stat_error",
        "temporal_bucket",
        "timestamp_source",
        "modified_time_utc",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _counter_from(entries: Iterable[CandidateEntry], attr: str) -> dict[str, int]:
    return dict(sorted(Counter(getattr(entry, attr) for entry in entries).items()))


def build_candidate_selection(
    *,
    source_root: Path,
    previous_manifest: Path,
    target_root: Path,
    planned_new_count: int,
    temporal_buckets: int,
    seed: int,
    timestamp_unknown_cap: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prior_paths, prior_duplicate_keys = load_prior_manifest_index(previous_manifest)
    candidates, excluded, exclusion_counts = _scan_source_entries(
        source_root,
        prior_paths=prior_paths,
        prior_duplicate_keys=prior_duplicate_keys,
    )
    selected, temporal_summary = select_temporal_stratified_candidates(
        candidates,
        planned_new_count=planned_new_count,
        bucket_count=temporal_buckets,
        seed=seed,
        timestamp_unknown_cap=timestamp_unknown_cap,
    )
    rows = build_manifest_rows(selected=selected, candidates=candidates, excluded=excluded, target_root=target_root)
    unselected_count = len(candidates) - len(selected)
    full_exclusion_counts = Counter(exclusion_counts)
    if unselected_count:
        full_exclusion_counts["not_selected_temporal_stratified"] += unselected_count

    selected_bytes = sum(entry.size_bytes for entry in selected)
    summary = {
        "source_labels": {
            "source_root_label": "icloud_photos_source",
            "previous_manifest_label": "phase_3_3a_1_local_manifest",
            "future_staging_target_label": "phase_3_8d_medium_staging_target",
            "paths_redacted": True,
        },
        "candidate_total": len(candidates),
        "selected_total": len(selected),
        "excluded_total": sum(full_exclusion_counts.values()),
        "source_inventory_total": len(candidates) + len(excluded),
        "approximate_byte_estimate": selected_bytes,
        "extension_distribution": _counter_from(selected, "extension"),
        "candidate_extension_distribution": _counter_from(candidates, "extension"),
        "exclusion_reason_counts": dict(sorted(full_exclusion_counts.items())),
        "duplicate_detection": {
            "prior_manifest_copy_paths_indexed": len(prior_paths),
            "prior_manifest_filename_size_keys_indexed": len(prior_duplicate_keys),
            "exact_hash_duplicate_scan_performed": False,
            "exact_hash_duplicate_scan_note": "Skipped to keep Phase 3.8c read-only and cheap; prior manifest path plus filename/size duplicate keys were excluded.",
        },
        **temporal_summary,
    }
    return rows, summary


def _storage_file_counts(settings: Any) -> dict[str, TreeSnapshot]:
    original_dir = Path(getattr(settings, "ORIGINAL_DIR", REPO_ROOT / "media" / "original"))
    thumbnail_dir = Path(getattr(settings, "THUMBNAIL_DIR", REPO_ROOT / "media" / "thumbnails"))
    return {
        "original": snapshot_tree(original_dir),
        "thumbnail": snapshot_tree(thumbnail_dir),
    }


def _tree_delta(before: TreeSnapshot, after: TreeSnapshot) -> dict[str, int | bool]:
    return {
        "exists_before": before.exists,
        "exists_after": after.exists,
        "file_count_delta": after.file_count - before.file_count,
        "total_bytes_delta": after.total_bytes - before.total_bytes,
        "stat_errors_delta": after.stat_errors - before.stat_errors,
    }


def _public_tree(snapshot: TreeSnapshot) -> dict[str, int | bool]:
    return asdict(snapshot)


def _replace_phase_label(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("Phase 3.8b", "Phase 3.8c")
    if isinstance(value, list):
        return [_replace_phase_label(item) for item in value]
    if isinstance(value, dict):
        return {key: _replace_phase_label(item) for key, item in value.items()}
    return value


def _relative_display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return "<outside_repo_artifact>"


def build_phase38c_report(
    *,
    candidate_summary: Mapping[str, Any],
    workflow_report: Mapping[str, Any],
    db_before: Any,
    db_after: Any,
    storage_before: Mapping[str, TreeSnapshot],
    storage_after: Mapping[str, TreeSnapshot],
    source_before: TreeSnapshot,
    source_after: TreeSnapshot,
    staging_before: TreeSnapshot,
    staging_after: TreeSnapshot,
    candidate_manifest: Path,
    future_source_label: str,
    expected_selected_count: int | None,
    strict: bool,
) -> dict[str, Any]:
    db_delta = compare_mutation_snapshots(db_before, db_after)
    storage_delta = {
        key: _tree_delta(storage_before[key], storage_after[key])
        for key in sorted(storage_before)
    }
    source_delta = _tree_delta(source_before, source_after)
    staging_delta = _tree_delta(staging_before, staging_after)

    failures: list[str] = []
    if any(value != 0 for value in db_delta.values()):
        failures.append(f"DB mutation detected: {db_delta}")
    for label, delta in storage_delta.items():
        if delta["file_count_delta"] != 0 or delta["total_bytes_delta"] != 0:
            failures.append(f"storage {label} mutation detected")
    if source_delta["file_count_delta"] != 0 or source_delta["total_bytes_delta"] != 0:
        failures.append("source mutation detected")
    if staging_delta["file_count_delta"] != 0 or staging_delta["total_bytes_delta"] != 0:
        failures.append("staging mutation detected")
    if not candidate_summary["temporal_diversity_check"]["passed"]:
        failures.append("temporal diversity check failed")
    if expected_selected_count is not None and candidate_summary["selected_total"] != expected_selected_count:
        message = (
            f"expected_selected_count={expected_selected_count}, "
            f"found={candidate_summary['selected_total']}"
        )
        if strict:
            failures.append(message)

    planned_total_after_execute = (
        int(workflow_report["counts"]["target_media_count"]) + int(candidate_summary["selected_total"])
    )

    stage_contracts = _replace_phase_label([asdict(stage) for stage in workflow_stage_contracts()])
    stage_plans = {
        "candidate_manifest": {
            "selected_total": candidate_summary["selected_total"],
            "candidate_total": candidate_summary["candidate_total"],
            "artifact_label": _relative_display(candidate_manifest),
        },
        "staging_copy_plan": {
            "mode": "dry_run_plan_only",
            "copy_rows": candidate_summary["selected_total"],
            "approximate_bytes": candidate_summary["approximate_byte_estimate"],
        },
        "db_import_plan": {
            "mode": "future_phase_3_8d_only",
            "expected_import_attempts": candidate_summary["selected_total"],
            "expected_total_after_execute": planned_total_after_execute,
        },
        "classification_plan": {
            "mode": "future_phase_3_8d_only",
            "must_run_before_ai": True,
            "expected_new_rows_to_classify": candidate_summary["selected_total"],
        },
        "eligible_selection_plan": {
            "eligible_policy": "anime + unknown",
            "ineligible_policy": "non_anime + illustration + unclassified",
            "null_content_class_policy": NULL_POLICY_HARD_FAIL,
        },
        "ai_tagging_plan": {
            "mode": "future_phase_3_8d_only",
            "scope": "eligible classified media only",
        },
        "localization_plan": {
            "mode": "future_phase_3_8d_only",
            "scope": "eligible-derived general/meta tags only",
            "proper_nouns": "deferred",
        },
        "post_run_validation_plan": {
            "endpoint_sweeps": workflow_report["validation_contract"]["endpoint_sweeps"],
            "smoke_checks": workflow_report["validation_contract"]["smoke_checks"],
        },
    }

    report: dict[str, Any] = {
        "phase": "3.8c",
        "mode": "dry_run",
        "success": not failures,
        "status": "passed" if not failures else "failed_contract",
        "started_at": workflow_report["started_at"],
        "finished_at": utc_now(),
        "scope": {
            "current_source_label": workflow_report.get("scope", {}).get("source_label", DEFAULT_SOURCE_LABEL),
            "future_source_label": future_source_label,
            "dry_run": True,
            "strict": strict,
            "planned_new_candidate_count": candidate_summary["planned_new_count"],
            "selected_new_candidate_count": candidate_summary["selected_total"],
            "planned_total_after_execute": planned_total_after_execute,
            "execute_phase": "3.8d",
        },
        "identity": workflow_report["identity"],
        "current_db_baseline": {
            "media_count": workflow_report["counts"]["target_media_count"],
            "phase_3_5_source_label_count": workflow_report["counts"]["target_media_count"],
            "eligible_count": workflow_report["counts"]["eligible_media_count"],
            "ineligible_count": workflow_report["counts"]["ineligible_media_count"],
            "content_class_distribution": workflow_report["counts"]["content_class_distribution"],
            "legacy_ineligible_ai_associations": workflow_report["legacy_contamination"][
                "ineligible_ai_associations"
            ],
        },
        "candidate_selection": dict(candidate_summary),
        "formal_dry_run_workflow": {
            "workflow_order": workflow_report["workflow_order"],
            "stage_contracts": stage_contracts,
            "stage_plans": stage_plans,
            "hard_gates": [
                "source read-only",
                "local manifest remains untracked",
                "temporal diversity check",
                "no-mutation proof",
                "privacy scan",
                "execute remains rejected",
                "Phase 3.8d explicit approval before writes",
            ],
            "stop_conditions": [
                "candidate source cannot be safely identified",
                "temporal diversity collapses into one narrow bucket",
                "expected counts cannot be reconciled",
                "privacy scan fails",
                "any DB/storage/source/staging mutation is detected",
                "execute is needed to continue",
            ],
            "artifacts_required_in_execute_phase": [
                "DB backup",
                "staging copy manifest",
                "pre-import audit summary",
                "DB import summary",
                "classification summary",
                "AI tagging eligible-only summary",
                "localization eligible-derived general/meta summary",
                "post-run validation summary",
                "browser/API smoke summary",
            ],
        },
        "expected_count_deltas": {
            "media": candidate_summary["selected_total"],
            "media_tags": "future_execute_only_unknown_until_ai_dry_run",
            "ai_jobs": "future_execute_only",
            "classification_jobs": "future_execute_only",
            "translation_jobs": "future_execute_only",
        },
        "no_mutation_proof": {
            "db": {
                "before": asdict(db_before),
                "after": asdict(db_after),
                "delta": db_delta,
            },
            "storage": {
                key: {
                    "before": _public_tree(storage_before[key]),
                    "after": _public_tree(storage_after[key]),
                    "delta": storage_delta[key],
                }
                for key in sorted(storage_before)
            },
            "source": {
                "before": _public_tree(source_before),
                "after": _public_tree(source_after),
                "delta": source_delta,
            },
            "staging": {
                "before": _public_tree(staging_before),
                "after": _public_tree(staging_after),
                "delta": staging_delta,
            },
            "passed": not any(
                [
                    any(value != 0 for value in db_delta.values()),
                    any(
                        delta["file_count_delta"] != 0 or delta["total_bytes_delta"] != 0
                        for delta in storage_delta.values()
                    ),
                    source_delta["file_count_delta"] != 0,
                    source_delta["total_bytes_delta"] != 0,
                    staging_delta["file_count_delta"] != 0,
                    staging_delta["total_bytes_delta"] != 0,
                ]
            ),
        },
        "execute_policy": {
            "execute_supported_in_phase": False,
            "execute_rejection_message": EXECUTE_REJECTION,
            "real_import_copy_classification_ai_localization": "forbidden_in_phase_3_8c",
        },
        "local_artifacts": {
            "candidate_manifest": _relative_display(candidate_manifest),
            "full_paths_in_public_reports": False,
            "must_remain_untracked": True,
        },
        "contract_failures": failures,
        "warnings": workflow_report.get("warnings", []),
    }

    safe_report = sanitize_public_obj(report)
    leaks = find_privacy_leaks(safe_report)
    safe_report["privacy"] = {
        "paths_redacted": True,
        "secret_values_redacted": True,
        "leaks": leaks,
        "passed": not leaks,
    }
    if leaks:
        safe_report["success"] = False
        safe_report["status"] = "failed_privacy"
        safe_report["contract_failures"].append(f"privacy leaks detected: {leaks}")
    return safe_report


def render_markdown_report(report: Mapping[str, Any]) -> str:
    candidate = report["candidate_selection"]
    baseline = report["current_db_baseline"]
    mutation = report["no_mutation_proof"]
    lines = [
        "# Phase 3.8c Medium Pilot Preflight Dry-run",
        "",
        "## Summary",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Status: `{report['status']}`",
        f"- Success: `{report['success']}`",
        f"- Current source label: `{report['scope']['current_source_label']}`",
        f"- Future source label: `{report['scope']['future_source_label']}`",
        f"- Planned new candidates: `{report['scope']['planned_new_candidate_count']}`",
        f"- Selected new candidates: `{report['scope']['selected_new_candidate_count']}`",
        f"- Planned total after execute: `{report['scope']['planned_total_after_execute']}`",
        f"- Repo branch: `{report['identity']['repo']['branch']}`",
        f"- Python: `{report['identity']['python']['executable_label']}` `{report['identity']['python']['version']}`",
        f"- DB: `{report['identity']['database']['violet_env']}` / `{report['identity']['database']['db_name']}`",
        "",
        "## Current Baseline",
        "",
        f"- Current DB media count: `{baseline['media_count']}`",
        f"- Phase 3.5 source label count: `{baseline['phase_3_5_source_label_count']}`",
        f"- Eligible media: `{baseline['eligible_count']}`",
        f"- Ineligible media: `{baseline['ineligible_count']}`",
        f"- Content class distribution: `{json.dumps(baseline['content_class_distribution'], sort_keys=True)}`",
        f"- Legacy ineligible AI associations: `{baseline['legacy_ineligible_ai_associations']}`",
        "",
        "## Candidate Selection",
        "",
        f"- Candidate total: `{candidate['candidate_total']}`",
        f"- Selected total: `{candidate['selected_total']}`",
        f"- Excluded total: `{candidate['excluded_total']}`",
        f"- Temporal bucket count: `{candidate['temporal_bucket_count']}`",
        f"- Timestamp unknown count: `{candidate['timestamp_unknown_count']}`",
        f"- Timestamp unknown selected: `{candidate['timestamp_unknown_selected']}`",
        f"- Approximate byte estimate: `{candidate['approximate_byte_estimate']}`",
        f"- Extension distribution: `{json.dumps(candidate['extension_distribution'], sort_keys=True)}`",
        f"- Exclusion reason counts: `{json.dumps(candidate['exclusion_reason_counts'], sort_keys=True)}`",
        f"- Temporal diversity passed: `{candidate['temporal_diversity_check']['passed']}`",
        "",
        "## Temporal Buckets",
        "",
        "| bucket | candidates | selected | start UTC | end UTC |",
        "|---|---:|---:|---|---|",
    ]
    for bucket in candidate["bucket_details"]:
        lines.append(
            f"| `{bucket['label']}` | `{bucket['candidate_count']}` | `{bucket['selected_count']}` | "
            f"`{bucket['start_time_utc']}` | `{bucket['end_time_utc']}` |"
        )
    lines.extend(
        [
            "",
            "## Formal Dry-run Workflow",
            "",
            "| # | stage | status | mutation risk |",
            "|---:|---|---|---|",
        ]
    )
    for stage in report["formal_dry_run_workflow"]["stage_contracts"]:
        lines.append(
            f"| {stage['order']} | {stage['name']} | {stage['implementation_status']} | {stage['mutation_risk']} |"
        )
    lines.extend(
        [
            "",
            "## No-mutation Proof",
            "",
            f"- DB delta: `{json.dumps(mutation['db']['delta'], sort_keys=True)}`",
            f"- Storage original delta: `{json.dumps(mutation['storage']['original']['delta'], sort_keys=True)}`",
            f"- Storage thumbnail delta: `{json.dumps(mutation['storage']['thumbnail']['delta'], sort_keys=True)}`",
            f"- Source delta: `{json.dumps(mutation['source']['delta'], sort_keys=True)}`",
            f"- Staging delta: `{json.dumps(mutation['staging']['delta'], sort_keys=True)}`",
            f"- Passed: `{mutation['passed']}`",
            "",
            "## Privacy",
            "",
            f"- Passed: `{report['privacy']['passed']}`",
            f"- Leaks: `{json.dumps(report['privacy']['leaks'])}`",
            f"- Local full-path manifest: `{report['local_artifacts']['candidate_manifest']}`",
            f"- Full paths in public reports: `{report['local_artifacts']['full_paths_in_public_reports']}`",
            "",
            "## Contract Failures",
            "",
        ]
    )
    if report["contract_failures"]:
        lines.extend(f"- `{failure}`" for failure in report["contract_failures"])
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Safety Confirmation",
            "",
            "- Dry-run only.",
            "- No real import/copy/staging mutation.",
            "- No DB mutation.",
            "- No classification, AI tagging, localization, Entity Resolver, cleanup, delete, reset, drop, or truncate.",
            "- Source files remain read-only.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_markdown_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(report), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--execute", action="store_true", help="Rejected in Phase 3.8c.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--previous-manifest", type=Path, default=DEFAULT_PREVIOUS_MANIFEST)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--planned-new-count", type=_positive_int, default=1000)
    parser.add_argument("--temporal-buckets", type=_positive_int, default=16)
    parser.add_argument("--timestamp-unknown-cap", type=_non_negative_int, default=25)
    parser.add_argument("--seed", type=int, default=3803)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE_MANIFEST)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--future-source-label", default=DEFAULT_FUTURE_SOURCE_LABEL)
    parser.add_argument("--current-source-label", default=DEFAULT_SOURCE_LABEL)
    parser.add_argument("--expected-current-media-count", type=_non_negative_int, default=995)
    parser.add_argument("--expected-eligible-count", type=_non_negative_int, default=969)
    parser.add_argument("--expected-ineligible-count", type=_non_negative_int, default=26)
    parser.add_argument("--expected-selected-count", type=_non_negative_int)
    parser.add_argument("--strict", action="store_true")
    return parser


def _load_app_context() -> tuple[Callable[[], Any], Any]:
    from app import database as database_mod
    from app.config import settings

    if database_mod.SessionLocal is None:
        database_mod.init_engine()
    if database_mod.SessionLocal is None:
        raise RuntimeError("Database is not initialized")
    return database_mod.SessionLocal, settings


def _print_summary(report: Mapping[str, Any], out: TextIO) -> None:
    candidate = report["candidate_selection"]
    print("Phase 3.8c medium pilot preflight dry-run", file=out)
    print(f"mode={report['mode']} status={report['status']} success={report['success']}", file=out)
    print(
        f"selected={candidate['selected_total']} candidates={candidate['candidate_total']} "
        f"buckets={candidate['temporal_bucket_count']} unknown={candidate['timestamp_unknown_count']}",
        file=out,
    )
    print(
        f"planned_total_after_execute={report['scope']['planned_total_after_execute']} "
        f"temporal_diversity={candidate['temporal_diversity_check']['passed']}",
        file=out,
    )
    print(f"no_mutation={report['no_mutation_proof']['passed']} privacy={report['privacy']['passed']}", file=out)
    print(f"candidate_manifest={report['local_artifacts']['candidate_manifest']}", file=out)
    if report["contract_failures"]:
        print(f"contract_failures={report['contract_failures']}", file=out)


def main(
    argv: list[str] | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
    settings_obj: Any | None = None,
    repo_root: Path = REPO_ROOT,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = out or sys.stdout
    err = err or sys.stderr

    if args.execute:
        print(EXECUTE_REJECTION, file=err)
        return 2
    if not args.source_root.is_dir():
        print("ERROR: source root does not exist or is not a directory", file=err)
        return 1
    if not args.previous_manifest.is_file():
        print("ERROR: previous manifest does not exist", file=err)
        return 1

    try:
        validate_output_paths(
            [args.candidate_manifest, args.report_json, args.report_md],
            [args.source_root, args.target_root],
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=err)
        return 1

    if session_factory is None or settings_obj is None:
        loaded_factory, loaded_settings = _load_app_context()
        session_factory = session_factory or loaded_factory
        settings_obj = settings_obj or loaded_settings

    db = session_factory()
    try:
        db_before = collect_mutation_snapshot(db)
        storage_before = _storage_file_counts(settings_obj)
        source_before = snapshot_tree(args.source_root)
        staging_before = snapshot_tree(args.target_root)

        rows, candidate_summary = build_candidate_selection(
            source_root=args.source_root,
            previous_manifest=args.previous_manifest,
            target_root=args.target_root,
            planned_new_count=args.planned_new_count,
            temporal_buckets=args.temporal_buckets,
            seed=args.seed,
            timestamp_unknown_cap=args.timestamp_unknown_cap,
        )
        write_candidate_manifest(args.candidate_manifest, rows)

        db_after = collect_mutation_snapshot(db)
        storage_after = _storage_file_counts(settings_obj)
        source_after = snapshot_tree(args.source_root)
        staging_after = snapshot_tree(args.target_root)

        workflow_scope = WorkflowScope(
            source_label=args.current_source_label,
            expected_current_media_count=args.expected_current_media_count,
            expected_eligible_count=args.expected_eligible_count,
            expected_ineligible_count=args.expected_ineligible_count,
            strict=args.strict,
            dry_run=True,
            null_content_class_policy=NULL_POLICY_HARD_FAIL,
        )
        workflow_report = build_dry_run_report(
            db,
            workflow_scope,
            repo_root=repo_root,
            settings=settings_obj,
            before_snapshot=db_before,
            after_snapshot=db_after,
        )
        report = build_phase38c_report(
            candidate_summary=candidate_summary,
            workflow_report=workflow_report,
            db_before=db_before,
            db_after=db_after,
            storage_before=storage_before,
            storage_after=storage_after,
            source_before=source_before,
            source_after=source_after,
            staging_before=staging_before,
            staging_after=staging_after,
            candidate_manifest=args.candidate_manifest,
            future_source_label=args.future_source_label,
            expected_selected_count=args.expected_selected_count,
            strict=args.strict,
        )
    finally:
        db.close()

    write_json_report(args.report_json, report)
    write_markdown_report(args.report_md, report)
    _print_summary(report, out)
    print(f"report_json={_relative_display(args.report_json)}", file=out)
    print(f"report_md={_relative_display(args.report_md)}", file=out)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
