#!/usr/bin/env python3
"""Phase 3.8d-I3 recovery planning for partial staging and cloud hydration.

This script is intentionally dry-run-only. It inspects the preserved partial
Phase 3.8d staging target, produces privacy-safe cleanup planning reports, and
documents the controlled read-probe/hydration and same-bucket backfill policies
required before Phase 3.8d execute can be retried.
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
    is_selected_copy_row,
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
EXPECTED_FILES_RE = re.compile(r"Expected files:\s*(\d+)")


@dataclass(frozen=True)
class ProtectedRoot:
    label: str
    path: Path


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


def _path_key(path: Path) -> str:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        resolved = path
    return os.path.normcase(str(resolved))


def _staging_log_matches_target(
    log_path: Path | None,
    target_root: Path,
    *,
    expected_copy_count: int,
) -> tuple[bool, bool, bool, bool]:
    """Return (log_present, log_matches, target_seen, expected_count_seen)."""
    if log_path is None or not log_path.is_file():
        return False, False, False, False
    try:
        raw = log_path.read_bytes()
    except OSError:
        return False, False, False, False
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    target_text = str(target_root)
    expected_counts = {int(match) for match in EXPECTED_FILES_RE.findall(text)}
    target_seen = target_text in text
    expected_count_seen = expected_copy_count in expected_counts
    return True, target_seen and expected_count_seen, target_seen, expected_count_seen


def _expected_partial_target_keys(
    rows: Sequence[dict[str, str]] | None,
    expected_file_count: int,
) -> set[str] | None:
    if rows is None:
        return None
    expected: set[str] = set()
    for row in rows:
        if not is_selected_copy_row(row):
            continue
        target = (row.get("proposed_target_path") or "").strip()
        if not target:
            continue
        expected.add(_path_key(Path(target)))
        if len(expected) >= expected_file_count:
            break
    return expected


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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a public cleanup dry-run plan plus local full-path evidence."""
    target_exists = target_root.exists()
    target_is_dir = target_root.is_dir()
    files = _iter_files(target_root)
    actual_file_keys = {_path_key(path) for path in files}
    expected_file_keys = _expected_partial_target_keys(expected_manifest_rows, expected_file_count)
    unexpected_keys = actual_file_keys - expected_file_keys if expected_file_keys is not None else set()
    missing_expected_keys = expected_file_keys - actual_file_keys if expected_file_keys is not None else set()
    unexpected_files_check_available = expected_file_keys is not None
    unexpected_files_check_passed = (
        unexpected_files_check_available
        and not unexpected_keys
        and not missing_expected_keys
        and len(expected_file_keys) == expected_file_count
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
    unsafe_reasons: list[str] = []
    for root in protected_roots:
        overlaps = _is_inside_or_same(target_root, root.path) or _is_inside_or_same(root.path, target_root)
        protected_checks.append(
            {
                "protected_label": root.label,
                "overlaps_target": overlaps,
            }
        )
        if overlaps:
            unsafe_reasons.append(f"target overlaps protected root: {root.label}")

    labels = {root.label for root in protected_roots}
    log_present, log_matches_target, log_target_seen, log_expected_count_seen = _staging_log_matches_target(
        staging_log,
        target_root,
        expected_copy_count=expected_copy_count,
    )
    file_count_matches = len(files) == expected_file_count
    byte_count_matches = total_bytes == expected_total_bytes
    expected_staging_root = expected_staging_root or target_root
    target_under_expected_staging_root = _is_inside_or_same(target_root, expected_staging_root)
    target_is_dedicated = (
        target_exists
        and target_is_dir
        and target_under_expected_staging_root
        and log_matches_target
        and file_count_matches
        and byte_count_matches
        and (unexpected_files_check_passed if unexpected_files_check_available else True)
    )

    public = {
        "phase": "3.8d-I3",
        "mode": "partial_staging_cleanup_dry_run",
        "target_safe_label": "phase_3_8d_partial_staging",
        "target_exists": _safe_public_bool(target_exists),
        "target_is_directory": _safe_public_bool(target_is_dir),
        "target_is_dedicated_phase38d_target": _safe_public_bool(target_is_dedicated),
        "target_under_expected_staging_root": _safe_public_bool(target_under_expected_staging_root),
        "dedicated_target_evidence": {
            "staging_copy_log_present": log_present,
            "staging_copy_log_matches_target": log_matches_target,
            "staging_copy_log_target_seen": log_target_seen,
            "staging_copy_log_expected_count_seen": log_expected_count_seen,
            "expected_file_count_matches": file_count_matches,
            "expected_total_bytes_matches": byte_count_matches,
            "unexpected_files_check_available": unexpected_files_check_available,
            "unexpected_files_check_passed": unexpected_files_check_passed,
            "unexpected_file_count": len(unexpected_keys),
            "missing_expected_file_count": len(missing_expected_keys),
        },
        "protected_root_checks": protected_checks,
        "target_is_not_source_icloud": not any(check["overlaps_target"] for check in protected_checks if check["protected_label"] in {"source_root", "icloud_source_root"}),
        "target_is_not_repo": not any(check["overlaps_target"] for check in protected_checks if check["protected_label"] == "repo_root"),
        "target_is_not_app_managed_storage": not any(
            check["overlaps_target"]
            for check in protected_checks
            if check["protected_label"] in {"app_storage_root", "app_media_root", "app_originals_root", "app_thumbnails_root"}
        ),
        "required_protected_labels_present": {
            "source_or_icloud": bool(labels & {"source_root", "icloud_source_root"}),
            "repo_root": "repo_root" in labels,
            "app_storage_or_media": bool(labels & {"app_storage_root", "app_media_root", "app_originals_root", "app_thumbnails_root"}),
        },
        "file_count": len(files),
        "actual_copied_file_count": len(files),
        "expected_partial_file_count": expected_file_count,
        "requested_expected_copy_count": expected_copy_count,
        "expected_file_count": expected_file_count,
        "total_bytes": total_bytes,
        "expected_total_bytes": expected_total_bytes,
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
    if not public["target_under_expected_staging_root"]:
        identity_mismatch_reasons.append("target_not_under_expected_staging_root")
    if not log_present:
        identity_mismatch_reasons.append("staging_copy_log_missing")
    if log_present and not log_matches_target:
        identity_mismatch_reasons.append("staging_copy_log_mismatch")
    if not file_count_matches:
        identity_mismatch_reasons.append("expected_partial_file_count_mismatch")
    if not byte_count_matches:
        identity_mismatch_reasons.append("expected_total_bytes_mismatch")
    if unexpected_files_check_available and not unexpected_files_check_passed:
        identity_mismatch_reasons.append("unexpected_or_missing_staging_files")
    public["identity_mismatch_reasons"] = identity_mismatch_reasons

    required_labels_present = public["required_protected_labels_present"]
    if unsafe_reasons:
        public["status"] = "blocked_unsafe_target"
    elif identity_mismatch_reasons:
        public["status"] = "blocked_identity_mismatch"
    elif not (
        required_labels_present["source_or_icloud"]
        and required_labels_present["repo_root"]
        and required_labels_present["app_storage_or_media"]
    ):
        public["status"] = "manual_review_required"
    else:
        public["status"] = "dry_run_passed"

    local = {
        "target_root": str(target_root),
        "expected_staging_root": str(expected_staging_root),
        "protected_roots": [{"label": root.label, "path": str(root.path)} for root in protected_roots],
        "local_file_samples": local_file_samples,
        "staging_log": str(staging_log) if staging_log else None,
        "unexpected_file_keys": sorted(unexpected_keys)[:20],
        "missing_expected_file_keys": sorted(missing_expected_keys)[:20],
    }
    return public, local


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
) -> dict[str, Any]:
    report = {
        "phase": "3.8d-I3",
        "mode": "recovery_cleanup_hydration_backfill_plan",
        "created_at": utc_now(),
        "success": cleanup_dry_run.get("status") == "dry_run_passed",
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
        "controlled_read_probe_hydration_policy": controlled_read_probe_policy(),
        "same_bucket_backfill_policy": backfill_policy,
        "row_98_recovery_semantics": build_row98_recovery_semantics(backfill_policy),
        "resume_vs_cleanup_rerun": resume_vs_cleanup_recommendation(),
        "local_artifacts": {
            "local_details_artifact": local_details_artifact,
            "full_paths_committed": False,
        },
        "safety_confirmation": {
            "actual_cleanup_delete_performed": False,
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


def render_cleanup_markdown(report: Mapping[str, Any]) -> str:
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
        f"- Target under expected staging root: `{report['target_under_expected_staging_root']}`",
        f"- Dedicated Phase 3.8d target: `{report['target_is_dedicated_phase38d_target']}`",
        f"- Actual copied file count: `{report['actual_copied_file_count']}`",
        f"- Expected partial file count: `{report['expected_partial_file_count']}`",
        f"- Requested expected copy count: `{report['requested_expected_copy_count']}`",
        f"- Total bytes: `{report['total_bytes']}`",
        f"- Expected total bytes: `{report['expected_total_bytes']}`",
        "",
        "## Dedicated Target Evidence",
        "",
        f"- Staging copy log present: `{evidence['staging_copy_log_present']}`",
        f"- Staging copy log matches target and expected count: `{evidence['staging_copy_log_matches_target']}`",
        f"- Staging copy log target seen: `{evidence['staging_copy_log_target_seen']}`",
        f"- Staging copy log expected count seen: `{evidence['staging_copy_log_expected_count_seen']}`",
        f"- Expected partial file count matches: `{evidence['expected_file_count_matches']}`",
        f"- Expected total bytes matches: `{evidence['expected_total_bytes_matches']}`",
        f"- Unexpected files check available: `{evidence['unexpected_files_check_available']}`",
        f"- Unexpected files check passed: `{evidence['unexpected_files_check_passed']}`",
        f"- Unexpected file count: `{evidence['unexpected_file_count']}`",
        f"- Missing expected file count: `{evidence['missing_expected_file_count']}`",
        f"- Identity mismatch reasons: `{json.dumps(report['identity_mismatch_reasons'], ensure_ascii=False)}`",
        "",
        "## Safety Proof",
        "",
        f"- Not source/iCloud: `{report['target_is_not_source_icloud']}`",
        f"- Not repo: `{report['target_is_not_repo']}`",
        f"- Not app-managed storage: `{report['target_is_not_app_managed_storage']}`",
        f"- Unsafe reasons: `{json.dumps(report['unsafe_reasons'], ensure_ascii=False)}`",
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
        f"- Actual delete performed: `{cleanup['deletion_plan']['actual_delete_performed']}`",
        f"- Confirmation phrase required: `{cleanup['deletion_plan']['confirmation_phrase_required']}`",
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
    if args.execute_cleanup:
        parser.error("Phase 3.8d-I3 is dry-run-only; actual cleanup requires a later explicit approval stage.")

    manifest_rows = read_manifest(args.manifest)
    cleanup_dry_run, local_cleanup_details = build_cleanup_dry_run(
        target_root=args.target_root,
        expected_staging_root=args.expected_staging_root or args.target_root,
        protected_roots=args.protected_root,
        expected_file_count=args.expected_file_count,
        expected_total_bytes=args.expected_total_bytes,
        expected_copy_count=args.selected_total,
        expected_manifest_rows=manifest_rows,
        execute_cleanup_requested=args.execute_cleanup,
        confirm_cleanup=args.confirm_cleanup,
        staging_log=args.staging_log,
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
    )

    cleanup_report = sanitize_public_obj(
        {
            "phase": "3.8d-I3",
            "created_at": recovery_report["created_at"],
            "success": cleanup_dry_run.get("status") == "dry_run_passed",
            "cleanup_dry_run": cleanup_dry_run,
            "privacy": {"paths_redacted": True, "safe_labels_only": True},
        }
    )
    leaks = find_privacy_leaks(cleanup_report)
    cleanup_report["privacy"]["leaks"] = leaks
    cleanup_report["privacy"]["passed"] = not leaks
    if leaks:
        cleanup_report["success"] = False

    write_json(args.cleanup_report_json, cleanup_report)
    write_text(args.cleanup_report_md, render_cleanup_markdown(cleanup_dry_run))
    write_text(args.recovery_report_md, render_recovery_markdown(recovery_report))
    write_json(
        args.local_details_json,
        {
            "created_at": recovery_report["created_at"],
            "cleanup": local_cleanup_details,
            "manifest": str(args.manifest),
            "target_root": str(args.target_root),
        },
    )
    print(json.dumps(recovery_report, ensure_ascii=False, indent=2))
    return 0 if recovery_report.get("success") and cleanup_report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
