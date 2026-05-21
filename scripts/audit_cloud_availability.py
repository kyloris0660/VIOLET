#!/usr/bin/env python3
"""Metadata-only Cloud Files availability audit for manifest ingestion.

Default mode never opens or reads source file contents.  The optional
``--read-probe`` mode is explicit because it may trigger Cloud Files hydration.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.classification_first_workflow import (  # noqa: E402
    find_privacy_leaks,
    sanitize_public_obj,
)
from app.services.source_ingestion_gate import SourceIngestionGate  # noqa: E402
from app.utils.cloud_files import (  # noqa: E402
    CloudFileState,
    read_probe_prefix,
)


DEFAULT_MANIFEST = REPO_ROOT / ".local_manifests" / "phase-3.8c-medium-candidate-manifest.csv"
DEFAULT_LOCAL_DETAILS = REPO_ROOT / ".local_manifests" / "phase-3.8d-cloud-availability-audit-details.json"
DEFAULT_REPORT_JSON = REPO_ROOT / "docs" / "reports" / "phase-3.8d-cloud-availability-audit-summary.json"
DEFAULT_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8d-cloud-availability-audit.md"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
CLEANUP_CONFIRM_PHRASE = "DELETE_PHASE38D_PARTIAL_STAGING"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def is_selected_copy_row(row: Mapping[str, str]) -> bool:
    return (row.get("selection_reason") or "").strip() == "new_candidate" and not (
        row.get("exclusion_reason") or ""
    ).strip()


def is_backfill_candidate_row(row: Mapping[str, str]) -> bool:
    return (row.get("exclusion_reason") or "").strip() == "not_selected_temporal_stratified"


def safe_row_label(row: Mapping[str, Any], *, prefix: str = "source") -> str:
    ext = str(row.get("extension") or Path(str(row.get("source_path") or "")).suffix or "").lower()
    row_id = int(row.get("row_id") or 0)
    return f"{prefix}_row_{row_id:04d}{ext}"


def path_key(path: str | Path) -> str:
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        resolved = Path(path)
    return os.path.normcase(str(resolved))


def row_size(row: Mapping[str, str]) -> int:
    try:
        return int((row.get("size_bytes") or "0").strip())
    except ValueError:
        return 0


def safe_state_dict(state: CloudFileState) -> dict[str, Any]:
    data = state.to_dict(include_path=False)
    return data


def evaluate_source_gate(path: Path, safe_label: str):
    return SourceIngestionGate.evaluate_path_source(
        path,
        safe_label=safe_label,
        hydration_policy_enabled=False,
    )


def selected_records(
    rows: Sequence[dict[str, str]],
    *,
    target_root: Path | None = None,
    failure_row_id: int | None = None,
    read_probe: bool = False,
    read_probe_limit: int = 10,
    read_probe_bytes: int = 1,
    read_probe_timeout: int = 10,
    read_probe_retries: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    local_records: list[dict[str, Any]] = []
    probe_count = 0
    for row in rows:
        if not is_selected_copy_row(row):
            continue
        source_path = Path(row["source_path"])
        target_path = Path(row.get("proposed_target_path") or "")
        if target_root is not None and not target_path.is_absolute():
            target_path = target_root / target_path
        safe_label = safe_row_label(row)
        gate = evaluate_source_gate(source_path, safe_label)
        state = gate.cloud_state or CloudFileState(
            path=str(source_path),
            supported_platform=False,
            exists=False,
            is_file=False,
            error_message="source ingestion gate returned no cloud state",
        )
        stat_error = None
        stat_size = None
        try:
            stat_size = source_path.stat().st_size
        except OSError as exc:
            stat_error = str(exc)
        target_exists = target_path.exists() if str(target_path) else False
        row_id = int(row["row_id"])
        probe_result = None
        if read_probe and probe_count < read_probe_limit:
            probe_count += 1
            probe_result = read_probe_prefix(
                source_path,
                max_bytes=read_probe_bytes,
                timeout_seconds=read_probe_timeout,
                retries=read_probe_retries,
            )
        public = {
            "row_id": row_id,
            "source_safe_label": safe_label,
            "target_safe_label": safe_row_label(row, prefix="target"),
            "extension": (row.get("extension") or "").lower(),
            "bucket": row.get("temporal_bucket") or "unknown",
            "expected_size": row_size(row),
            "stat_size": stat_size,
            "stat_error": bool(stat_error),
            "exists": state.exists,
            "is_file": state.is_file,
            "cloud_state": safe_state_dict(state),
            "source_ingestion_gate": gate.to_public_dict(),
            "likely_cloud_placeholder": state.likely_cloud_placeholder,
            "target_already_copied": target_exists,
            "prior_copy_status": (
                "failed_row"
                if failure_row_id is not None and row_id == failure_row_id
                else (
                    "before_failed_row"
                    if failure_row_id is not None and row_id < failure_row_id
                    else ("after_failed_row_or_unattempted" if failure_row_id else "unknown")
                )
            ),
            "read_probe": probe_result,
        }
        records.append(public)
        local_records.append({**public, "source_path": str(source_path), "target_path": str(target_path), "stat_error_message": stat_error})
    return records, local_records


def summarize_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_bucket = Counter(str(record["bucket"]) for record in records)
    by_ext = Counter(str(record["extension"]) for record in records)
    risky = [record for record in records if record.get("likely_cloud_placeholder")]
    risky_by_bucket = Counter(str(record["bucket"]) for record in risky)
    risky_by_ext = Counter(str(record["extension"]) for record in risky)
    already_copied = [record for record in records if record.get("target_already_copied")]
    summary = {
        "selected_total": len(records),
        "exists": sum(1 for record in records if record.get("exists")),
        "missing": sum(1 for record in records if not record.get("exists")),
        "stat_errors": sum(1 for record in records if record.get("stat_error")),
        "offline_count": sum(1 for record in records if record["cloud_state"].get("offline")),
        "reparse_point_count": sum(1 for record in records if record["cloud_state"].get("reparse_point")),
        "recall_on_open_count": sum(1 for record in records if record["cloud_state"].get("recall_on_open")),
        "recall_on_data_access_count": sum(1 for record in records if record["cloud_state"].get("recall_on_data_access")),
        "pinned_count": sum(1 for record in records if record["cloud_state"].get("pinned")),
        "unpinned_count": sum(1 for record in records if record["cloud_state"].get("unpinned")),
        "sparse_file_count": sum(1 for record in records if record["cloud_state"].get("sparse_file")),
        "likely_cloud_placeholder_count": len(risky),
        "selected_count_by_bucket": dict(sorted(by_bucket.items())),
        "selected_count_by_extension": dict(sorted(by_ext.items())),
        "risky_count_by_bucket": dict(sorted(risky_by_bucket.items())),
        "risky_count_by_extension": dict(sorted(risky_by_ext.items())),
        "already_copied_in_partial_staging": len(already_copied),
        "not_yet_copied": len(records) - len(already_copied),
        "already_copied_by_bucket": dict(sorted(Counter(str(record["bucket"]) for record in already_copied).items())),
        "already_copied_by_extension": dict(sorted(Counter(str(record["extension"]) for record in already_copied).items())),
        "first_risky_examples": [
            {
                "row_id": int(record["row_id"]),
                "source_safe_label": record["source_safe_label"],
                "target_safe_label": record["target_safe_label"],
                "extension": record["extension"],
                "bucket": record["bucket"],
                "expected_size": record["expected_size"],
                "cloud_state": record["cloud_state"],
                "target_already_copied": record["target_already_copied"],
                "prior_copy_status": record["prior_copy_status"],
            }
            for record in risky[:20]
        ],
    }
    summary["copy_gate"] = {
        "status": "blocked_requires_hydration_policy" if summary["likely_cloud_placeholder_count"] > 0 else "passed",
        "reason": (
            "Selected set contains cloud-backed files. Direct copy is forbidden until controlled hydration/read-probe/backfill policy passes."
            if summary["likely_cloud_placeholder_count"] > 0
            else "No cloud placeholder risk detected by metadata-only audit."
        ),
    }
    return summary


def plan_same_bucket_backfill(
    rows: Sequence[dict[str, str]],
    failed_row_ids: Iterable[int],
) -> dict[str, Any]:
    failed_ids = [int(row_id) for row_id in failed_row_ids]
    selected_by_id = {int(row["row_id"]): row for row in rows if is_selected_copy_row(row)}
    pool_by_bucket: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if is_backfill_candidate_row(row) and (row.get("extension") or "").lower() in SUPPORTED_EXTENSIONS:
            pool_by_bucket.setdefault(row.get("temporal_bucket") or "unknown", []).append(row)
    for bucket_rows in pool_by_bucket.values():
        bucket_rows.sort(key=lambda item: (Path(item.get("source_path") or "").name.lower(), int(item.get("row_id") or 0)))

    replacements: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    used: set[int] = set()
    for failed_id in failed_ids:
        failed = selected_by_id.get(failed_id)
        if failed is None:
            unresolved.append({"failed_row_id": failed_id, "reason": "failed_row_not_selected"})
            continue
        bucket = failed.get("temporal_bucket") or "unknown"
        candidate = next((row for row in pool_by_bucket.get(bucket, []) if int(row["row_id"]) not in used), None)
        if candidate is None:
            unresolved.append({"failed_row_id": int(failed_id), "bucket": bucket, "reason": "no_same_bucket_candidate"})
            continue
        used.add(int(candidate["row_id"]))
        replacements.append(
            {
                "failed_row_id": int(failed_id),
                "failed_safe_label": safe_row_label(failed),
                "replacement_row_id": int(candidate["row_id"]),
                "replacement_safe_label": safe_row_label(candidate, prefix="replacement"),
                "bucket": bucket,
                "reason": "same_bucket_backfill_after_bounded_hydration_failure",
            }
        )
    return {
        "mode": "dry_run_only",
        "selected_total_preserved": True,
        "policy": "same temporal bucket replacement when possible; never silently drop failed rows",
        "requested_failures": len(failed_ids),
        "replacement_count": len(replacements),
        "unresolved_count": len(unresolved),
        "replacements": replacements,
        "unresolved": unresolved,
    }


def _is_inside_or_same(child: Path, parent: Path) -> bool:
    try:
        resolved_child = child.resolve()
        resolved_parent = parent.resolve()
        return resolved_child == resolved_parent or resolved_child.is_relative_to(resolved_parent)
    except (OSError, RuntimeError, ValueError):
        return False


def build_cleanup_dry_run_plan(
    target_root: Path,
    *,
    protected_roots: Sequence[Path],
    execute: bool = False,
    confirm_phrase: str = "",
) -> dict[str, Any]:
    unsafe = []
    for root in protected_roots:
        if _is_inside_or_same(target_root, root) or _is_inside_or_same(root, target_root):
            unsafe.append("target overlaps protected root")
    files = list(target_root.rglob("*")) if target_root.is_dir() else []
    file_items = [path for path in files if path.is_file()]
    total_bytes = 0
    ext_counts = Counter()
    for path in file_items:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
        ext_counts[path.suffix.lower()] += 1
    would_be_eligible_after_separate_approval = execute and confirm_phrase == CLEANUP_CONFIRM_PHRASE and not unsafe
    return {
        "mode": "dry_run_cleanup_plan",
        "execute_requested": execute,
        "execute_allowed": False,
        "would_be_eligible_after_separate_approval": would_be_eligible_after_separate_approval,
        "actual_delete_performed": False,
        "confirm_phrase_required": CLEANUP_CONFIRM_PHRASE,
        "confirm_phrase_valid": confirm_phrase == CLEANUP_CONFIRM_PHRASE,
        "target_exists": target_root.exists(),
        "target_is_directory": target_root.is_dir(),
        "target_safe_label": "phase_3_8d_partial_staging",
        "unsafe_reasons": unsafe,
        "file_count": len(file_items),
        "total_bytes": total_bytes,
        "extension_distribution": dict(sorted(ext_counts.items())),
        "note": "Phase 3.8d-I1 documents cleanup only; actual deletion requires separate approval.",
    }


def build_report(
    *,
    manifest_path: Path,
    target_root: Path | None,
    local_details_path: Path | None,
    records: Sequence[dict[str, Any]],
    summary: dict[str, Any],
    read_probe_enabled: bool,
    backfill_plan: dict[str, Any] | None,
    cleanup_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    local_details_display = "local_cloud_availability_details" if local_details_path else None
    report = {
        "phase": "3.8d-I1",
        "mode": "cloud_availability_metadata_audit",
        "created_at": utc_now(),
        "success": True,
        "manifest_label": manifest_path.name,
        "target_root_label": "phase_3_8d_partial_staging" if target_root else None,
        "local_details_artifact": local_details_display,
        "read_probe": {
            "enabled": read_probe_enabled,
            "warning": "read_probe may trigger Cloud Files hydration and is opt-in only",
        },
        "summary": summary,
        "backfill_plan": backfill_plan,
        "cleanup_plan": cleanup_plan,
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


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Phase 3.8d-I1 Cloud Availability Audit",
        "",
        "## Summary",
        "",
        f"- Status: `{'passed' if report['success'] else 'failed'}`",
        f"- Mode: `{report['mode']}`",
        f"- Manifest label: `{report['manifest_label']}`",
        f"- Target label: `{report.get('target_root_label')}`",
        f"- Read probe enabled: `{report['read_probe']['enabled']}`",
        f"- Selected total: `{summary['selected_total']}`",
        f"- Already copied in partial staging: `{summary['already_copied_in_partial_staging']}`",
        f"- Not yet copied: `{summary['not_yet_copied']}`",
        f"- Likely cloud placeholder count: `{summary['likely_cloud_placeholder_count']}`",
        f"- Copy gate: `{summary['copy_gate']['status']}`",
        "",
        "## Cloud Attribute Counts",
        "",
        f"- Exists: `{summary['exists']}`",
        f"- Missing: `{summary['missing']}`",
        f"- Stat errors: `{summary['stat_errors']}`",
        f"- Offline: `{summary['offline_count']}`",
        f"- Reparse point: `{summary['reparse_point_count']}`",
        f"- Recall on open: `{summary['recall_on_open_count']}`",
        f"- Recall on data access: `{summary['recall_on_data_access_count']}`",
        f"- Pinned: `{summary['pinned_count']}`",
        f"- Unpinned: `{summary['unpinned_count']}`",
        f"- Sparse file: `{summary['sparse_file_count']}`",
        "",
        "## Distributions",
        "",
        f"- Selected by bucket: `{json.dumps(summary['selected_count_by_bucket'], sort_keys=True)}`",
        f"- Risky by bucket: `{json.dumps(summary['risky_count_by_bucket'], sort_keys=True)}`",
        f"- Selected by extension: `{json.dumps(summary['selected_count_by_extension'], sort_keys=True)}`",
        f"- Risky by extension: `{json.dumps(summary['risky_count_by_extension'], sort_keys=True)}`",
        "",
        "## First Risky Examples",
        "",
    ]
    for item in summary["first_risky_examples"][:10]:
        lines.append(
            f"- `{item['source_safe_label']}` bucket `{item['bucket']}` extension `{item['extension']}` "
            f"state `{json.dumps(item['cloud_state'], sort_keys=True)}`"
        )
    if not summary["first_risky_examples"]:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Gate Rule",
            "",
            "If `likely_cloud_placeholder_count > 0`, direct staging copy is blocked until a controlled hydrate/read-probe/backfill policy is explicitly enabled and passes.",
            "",
            "## Privacy",
            "",
            f"- Passed: `{report['privacy']['passed']}`",
            f"- Leaks: `{json.dumps(report['privacy']['leaks'])}`",
            f"- Local details artifact: `{report['local_details_artifact']}`",
            "",
            "## Safety",
            "",
            "- Metadata-only by default.",
            "- No content read unless `--read-probe` is explicitly provided.",
            "- No source/iCloud mutation.",
            "- No staging cleanup/delete.",
            "- No DB import.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(payload), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--target-root", type=Path, default=None)
    parser.add_argument("--failure-row-id", type=_positive_int, default=None)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--local-details-json", type=Path, default=DEFAULT_LOCAL_DETAILS)
    parser.add_argument("--read-probe", action="store_true", help="Opt-in: may trigger Cloud Files hydration")
    parser.add_argument("--read-probe-limit", type=_non_negative_int, default=10)
    parser.add_argument("--read-probe-bytes", type=_positive_int, default=1)
    parser.add_argument("--read-probe-timeout", type=_positive_int, default=10)
    parser.add_argument("--read-probe-retries", type=_non_negative_int, default=0)
    parser.add_argument("--plan-backfill-for-row", action="append", type=_positive_int, default=[])
    parser.add_argument("--cleanup-plan", action="store_true")
    parser.add_argument("--execute-cleanup", action="store_true")
    parser.add_argument("--confirm-cleanup", default="")
    parser.add_argument("--protected-root", action="append", type=Path, default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.manifest.is_file():
        parser.error(f"manifest not found: {args.manifest}")
    if args.execute_cleanup and args.confirm_cleanup != CLEANUP_CONFIRM_PHRASE:
        parser.error(f"--execute-cleanup requires --confirm-cleanup {CLEANUP_CONFIRM_PHRASE}")

    rows = read_manifest(args.manifest)
    records, local_records = selected_records(
        rows,
        target_root=args.target_root,
        failure_row_id=args.failure_row_id,
        read_probe=args.read_probe,
        read_probe_limit=args.read_probe_limit,
        read_probe_bytes=args.read_probe_bytes,
        read_probe_timeout=args.read_probe_timeout,
        read_probe_retries=args.read_probe_retries,
    )
    summary = summarize_records(records)
    backfill_plan = (
        plan_same_bucket_backfill(rows, args.plan_backfill_for_row)
        if args.plan_backfill_for_row
        else None
    )
    cleanup_plan = None
    if args.cleanup_plan:
        if args.target_root is None:
            parser.error("--cleanup-plan requires --target-root")
        cleanup_plan = build_cleanup_dry_run_plan(
            args.target_root,
            protected_roots=args.protected_root,
            execute=args.execute_cleanup,
            confirm_phrase=args.confirm_cleanup,
        )

    report = build_report(
        manifest_path=args.manifest,
        target_root=args.target_root,
        local_details_path=args.local_details_json,
        records=records,
        summary=summary,
        read_probe_enabled=args.read_probe,
        backfill_plan=backfill_plan,
        cleanup_plan=cleanup_plan,
    )
    write_json(args.report_json, report)
    write_markdown(args.report_md, report)
    write_json(args.local_details_json, {"records": local_records})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
