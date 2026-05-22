#!/usr/bin/env python3
"""Phase 3.8d-I5c same-bucket backfill validation and local manifest build.

This stage validates only approved replacement rows, then writes a new local
selected manifest artifact if all replacements pass full-read verification.
It never writes staging files, mutates the database, modifies source content,
or applies backfill to committed source manifests.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


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
    read_manifest,
    row_size,
    safe_row_label,
)
from run_phase38d_i5_hydration_audit import COPY_SELECTION_REASONS  # noqa: E402
from run_phase38d_i5b_targeted_hydration_retry import (  # noqa: E402
    DEFAULT_FULL_CHUNK_SIZE,
    DEFAULT_PREFIX_BYTES,
    _non_negative_int,
    _positive_int,
    summarize_results,
    verify_target_row,
)


DEFAULT_MANIFEST = REPO_ROOT / ".local_manifests" / "phase-3.8c-medium-candidate-manifest.csv"
DEFAULT_I5B_SUMMARY = (
    REPO_ROOT / "docs" / "reports" / "phase-3.8d-i5b-targeted-hydration-retry-summary.json"
)
DEFAULT_REPORT_JSON = (
    REPO_ROOT / "docs" / "reports" / "phase-3.8d-i5c-backfill-application-summary.json"
)
DEFAULT_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i5c-backfill-application.md"
DEFAULT_OUTPUT_MANIFEST = (
    REPO_ROOT / ".local_manifests" / "phase-3.8d-i5c-backfilled-selected-manifest.csv"
)
DEFAULT_LOCAL_LEDGER = (
    REPO_ROOT / ".local_manifests" / "phase-3.8d-i5c-deferred-cloud-recovery-ledger.json"
)
DEFAULT_LOCAL_DETAILS = (
    REPO_ROOT / ".local_manifests" / "phase-3.8d-i5c-backfill-validation-details.json"
)
DEFAULT_FAILED_TO_REPLACEMENT = {98: 1029, 881: 1041}
DEFAULT_PREFIX_TIMEOUT_SECONDS = 30
DEFAULT_PREFIX_RETRIES = 1
DEFAULT_FULL_TIMEOUT_SECONDS = 180
DEFAULT_FULL_RETRIES = 1
DEFAULT_RETRY_WAIT_SECONDS = 0
INGESTION_OBSERVABILITY_PRINCIPLE = {
    "name": "per_run_source_item_final_state",
    "scope": "future_production_ingestion_workflow",
    "description": (
        "Every source item in each run, manifest, or job must have a clear final state. "
        "Reporting must be scoped to the current run and must not hide failed rows behind aggregate counts."
    ),
    "required_state_fields": [
        "succeeded",
        "failed",
        "failure_reason",
        "retried",
        "backfilled",
        "deferred_for_cloud_recovery",
        "imported_into_db",
        "excluded_as_ineligible",
        "unresolved",
    ],
    "current_i5c_scope": "principle_and_local_deferred_ledger_only",
    "db_migration_in_this_pr": False,
    "full_production_ledger_in_this_pr": False,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(payload), encoding="utf-8")


def row_id(row: Mapping[str, str]) -> int:
    try:
        return int(row.get("row_id") or 0)
    except ValueError:
        return 0


def is_selected_copy_row(row: Mapping[str, str]) -> bool:
    return (row.get("selection_reason") or "").strip() in COPY_SELECTION_REASONS and not (
        row.get("exclusion_reason") or ""
    ).strip()


def selected_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    return sorted([row for row in rows if is_selected_copy_row(row)], key=row_id)


def selected_distribution(rows: Sequence[dict[str, str]]) -> dict[str, int]:
    return dict(sorted(Counter((row.get("temporal_bucket") or "unknown") for row in selected_rows(rows)).items()))


def selected_extension_distribution(rows: Sequence[dict[str, str]]) -> dict[str, int]:
    return dict(sorted(Counter((row.get("extension") or "").lower() for row in selected_rows(rows)).items()))


def index_rows(rows: Sequence[dict[str, str]]) -> dict[int, dict[str, str]]:
    return {row_id(row): row for row in rows if row_id(row) > 0}


def _path_key(path: str | Path) -> str:
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        resolved = Path(path)
    return os.path.normcase(str(resolved))


def _derive_selected_target_root(rows: Sequence[dict[str, str]]) -> tuple[Path | None, list[str]]:
    roots: set[str] = set()
    for row in selected_rows(rows):
        target = (row.get("proposed_target_path") or "").strip()
        if not target:
            return None, ["selected_row_missing_target_path"]
        target_path = Path(target)
        if not target_path.is_absolute():
            return None, ["selected_row_target_path_not_absolute"]
        roots.add(str(target_path.parent))
    if not roots:
        return None, ["no_selected_target_paths"]
    if len(roots) != 1:
        return None, ["ambiguous_selected_target_roots"]
    return Path(next(iter(roots))), []


def _replacement_record(row: Mapping[str, str]) -> dict[str, Any]:
    source_path = Path(row["source_path"])
    return {
        "row_id": row_id(row),
        "source_safe_label": safe_row_label(row, prefix="replacement"),
        "extension": (row.get("extension") or source_path.suffix).lower(),
        "bucket": row.get("temporal_bucket") or "unknown",
        "expected_size": row_size(row),
        "source_path": str(source_path),
    }


def _fallback_failure_result(row: Mapping[str, str], reason: str) -> dict[str, Any]:
    return {
        "row_id": row_id(row),
        "source_safe_label": safe_row_label(row, prefix="replacement"),
        "bucket": row.get("temporal_bucket") or "unknown",
        "extension": (row.get("extension") or "").lower(),
        "expected_size": row_size(row),
        "metadata_before": {},
        "prefix_read": {
            "ok": False,
            "attempted": False,
            "attempt_count": 0,
            "bytes_read": 0,
            "bytes_read_total": 0,
            "duration_seconds": 0.0,
            "error_reason": reason,
        },
        "full_read": {
            "ok": False,
            "attempted": False,
            "attempt_count": 0,
            "bytes_read": 0,
            "bytes_read_total": 0,
            "duration_seconds": 0.0,
            "error_reason": reason,
            "ran_even_if_prefix_failed": False,
        },
        "metadata_after": {},
        "still_recall_on_data_access": None,
        "staging_copy_ready": False,
        "ok": False,
        "failure_reason": reason,
        "audit_bytes_read": 0,
        "duration_seconds": 0.0,
    }


def validate_mapping(rows: Sequence[dict[str, str]], mapping: Mapping[int, int]) -> tuple[list[str], list[dict[str, Any]]]:
    by_id = index_rows(rows)
    errors: list[str] = []
    public_pairs: list[dict[str, Any]] = []
    used_replacements: set[int] = set()
    for failed_id, replacement_id in mapping.items():
        failed = by_id.get(int(failed_id))
        replacement = by_id.get(int(replacement_id))
        if failed is None:
            errors.append(f"missing_failed_row_{failed_id}")
            continue
        if replacement is None:
            errors.append(f"missing_replacement_row_{replacement_id}")
            continue
        if not is_selected_copy_row(failed):
            errors.append(f"failed_row_not_active_selected_{failed_id}")
        if is_selected_copy_row(replacement):
            errors.append(f"replacement_already_selected_{replacement_id}")
        if (replacement.get("exclusion_reason") or "").strip() != "not_selected_temporal_stratified":
            errors.append(f"replacement_not_temporal_backfill_candidate_{replacement_id}")
        if int(replacement_id) in used_replacements:
            errors.append(f"duplicate_replacement_row_{replacement_id}")
        used_replacements.add(int(replacement_id))
        failed_bucket = failed.get("temporal_bucket") or "unknown"
        replacement_bucket = replacement.get("temporal_bucket") or "unknown"
        if failed_bucket != replacement_bucket:
            errors.append(f"replacement_bucket_mismatch_{failed_id}_{replacement_id}")
        public_pairs.append(
            {
                "failed_row_id": int(failed_id),
                "failed_safe_label": safe_row_label(failed),
                "replacement_row_id": int(replacement_id),
                "replacement_safe_label": safe_row_label(replacement, prefix="replacement"),
                "bucket": failed_bucket,
                "replacement_bucket": replacement_bucket,
                "same_bucket": failed_bucket == replacement_bucket,
            }
        )
    return errors, public_pairs


def _alternate_candidates(
    rows: Sequence[dict[str, str]],
    *,
    failed_row: Mapping[str, str],
    excluded_row_ids: set[int],
    limit: int = 5,
) -> list[dict[str, Any]]:
    bucket = failed_row.get("temporal_bucket") or "unknown"
    candidates = [
        row
        for row in rows
        if row_id(row) not in excluded_row_ids
        and (row.get("temporal_bucket") or "unknown") == bucket
        and (row.get("exclusion_reason") or "").strip() == "not_selected_temporal_stratified"
    ]
    candidates.sort(key=lambda item: (Path(item.get("source_path") or "").name.lower(), row_id(item)))
    return [
        {
            "row_id": row_id(row),
            "replacement_safe_label": safe_row_label(row, prefix="replacement"),
            "bucket": bucket,
            "extension": (row.get("extension") or "").lower(),
            "expected_size": row_size(row),
        }
        for row in candidates[:limit]
    ]


def _validate_backfilled_rows(rows: Sequence[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    selected = selected_rows(rows)
    source_keys: dict[str, int] = {}
    target_keys: dict[str, int] = {}
    for row in selected:
        source = (row.get("source_path") or "").strip()
        target = (row.get("proposed_target_path") or "").strip()
        if not source:
            errors.append(f"selected_row_missing_source_{row_id(row)}")
            continue
        if not target:
            errors.append(f"selected_row_missing_target_{row_id(row)}")
            continue
        source_key = _path_key(source)
        target_key = _path_key(target)
        if source_key in source_keys:
            errors.append(f"duplicate_selected_source_{source_keys[source_key]}_{row_id(row)}")
        source_keys[source_key] = row_id(row)
        if target_key in target_keys:
            errors.append(f"duplicate_selected_target_{target_keys[target_key]}_{row_id(row)}")
        target_keys[target_key] = row_id(row)
    return errors


def apply_backfill_rows(
    rows: Sequence[dict[str, str]],
    mapping: Mapping[int, int],
) -> tuple[list[dict[str, str]] | None, dict[str, Any], list[str]]:
    updated = deepcopy(list(rows))
    by_id = index_rows(updated)
    target_root, root_errors = _derive_selected_target_root(updated)
    if root_errors or target_root is None:
        return None, {"target_root_derivation": "failed", "errors": root_errors}, root_errors

    for failed_id, replacement_id in mapping.items():
        failed = by_id[int(failed_id)]
        replacement = by_id[int(replacement_id)]
        replacement_target = target_root / Path(replacement["source_path"]).name

        failed["selection_reason"] = ""
        failed["exclusion_reason"] = "not_selected_temporal_stratified"
        failed["placeholder_flag"] = "False"
        failed["stat_error"] = "False"

        replacement["selection_reason"] = "new_candidate"
        replacement["exclusion_reason"] = ""
        replacement["placeholder_flag"] = "False"
        replacement["stat_error"] = "False"
        replacement["proposed_target_path"] = str(replacement_target)

    validation_errors = _validate_backfilled_rows(updated)
    details = {
        "target_root_derivation": "passed",
        "target_root_label": "derived_from_selected_manifest_targets",
        "duplicate_checks": "passed" if not validation_errors else "failed",
        "errors": validation_errors,
    }
    if validation_errors:
        return None, details, validation_errors
    return updated, details, []


def write_manifest(path: Path, rows: Sequence[Mapping[str, str]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_i5b_summary(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _i5b_result_by_row(summary: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    return {
        int(item.get("row_id", 0)): item
        for item in summary.get("target_results", [])
        if isinstance(item, Mapping) and item.get("row_id") is not None
    }


def build_deferred_ledger(
    rows: Sequence[dict[str, str]],
    mapping: Mapping[int, int],
    *,
    i5b_summary: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_id = index_rows(rows)
    i5b_by_row = _i5b_result_by_row(i5b_summary)
    public_rows: list[dict[str, Any]] = []
    local_rows: list[dict[str, Any]] = []
    for failed_id, replacement_id in mapping.items():
        row = by_id.get(int(failed_id), {})
        i5b = i5b_by_row.get(int(failed_id), {})
        after_state = (
            i5b.get("metadata_after", {}).get("cloud_state", {}) if isinstance(i5b.get("metadata_after"), Mapping) else {}
        )
        reason = i5b.get("failure_reason") or "cloud_hydration_failed"
        public = {
            "row_id": int(failed_id),
            "source_safe_label": safe_row_label(row) if row else f"source_row_{int(failed_id):04d}",
            "bucket": row.get("temporal_bucket") or i5b.get("bucket") or "unknown",
            "extension": (row.get("extension") or i5b.get("extension") or "").lower(),
            "expected_size": row_size(row) if row else i5b.get("expected_size"),
            "deferred_reason": reason,
            "current_state": {
                "recall_on_data_access": bool(after_state.get("recall_on_data_access", True)),
                "likely_cloud_placeholder": bool(after_state.get("likely_cloud_placeholder", True)),
            },
            "replacement_row_id": int(replacement_id),
            "replacement_safe_label": safe_row_label(by_id.get(int(replacement_id), {}), prefix="replacement"),
            "action": "excluded_from_active_medium_pilot_manifest_via_same_bucket_backfill",
            "per_run_final_state": {
                "succeeded": False,
                "failed": True,
                "failure_reason": reason,
                "retried": True,
                "retry_scope": "I5_sample_gate_and_I5b_targeted_retry",
                "backfilled": True,
                "deferred_for_cloud_recovery": True,
                "imported_into_db": False,
                "excluded_as_ineligible": False,
                "unresolved": False,
                "still_unresolved_for_cloud_recovery": True,
            },
            "future_recovery_options": [
                "provider_or_network_investigation",
                "manual_user_inspection",
                "separate_cfhydrateplaceholder_experiment",
                "retry_after_provider_state_changes",
            ],
        }
        public_rows.append(public)
        local_rows.append({**public, "source_path": row.get("source_path"), "i5b_result": i5b})
    public_ledger = {
        "status": "deferred_not_abandoned",
        "unrecovered_original_rows": [int(row_id) for row_id in mapping],
        "reason": "cloud_hydration_failed_after_I5_and_I5b_bounded_read_based_recovery_attempts",
        "rows": public_rows,
    }
    local_ledger = {
        "created_at": utc_now(),
        "status": "deferred_not_abandoned",
        "rows": local_rows,
    }
    return public_ledger, local_ledger


def run_backfill_application(
    rows: Sequence[dict[str, str]],
    *,
    mapping: Mapping[int, int],
    policy: Mapping[str, int],
    i5b_summary: Mapping[str, Any] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    selected_before = selected_rows(rows)
    mapping_errors, mapping_public = validate_mapping(rows, mapping)

    validation_results_public: list[dict[str, Any]] = []
    validation_results_local: list[dict[str, Any]] = []
    if not mapping_errors:
        by_id = index_rows(rows)
        for replacement_id in mapping.values():
            replacement = by_id[int(replacement_id)]
            try:
                verified = verify_target_row(_replacement_record(replacement), policy=policy, sleeper=sleeper)
                validation_results_public.append(verified["public"])
                validation_results_local.append(verified["local"])
            except Exception as exc:
                fallback = _fallback_failure_result(replacement, f"replacement_validation_exception_{type(exc).__name__}")
                validation_results_public.append(fallback)
                validation_results_local.append({**fallback, "source_path": replacement.get("source_path")})
    else:
        for replacement_id in mapping.values():
            replacement = index_rows(rows).get(int(replacement_id), {"row_id": str(replacement_id), "extension": ""})
            fallback = _fallback_failure_result(replacement, "replacement_mapping_invalid")
            validation_results_public.append(fallback)
            validation_results_local.append({**fallback, "source_path": replacement.get("source_path")})

    validation_summary = summarize_results(validation_results_public)
    replacement_failures = [item for item in validation_results_public if not item.get("ok")]
    status = "backfill_applied"
    backfilled_rows: list[dict[str, str]] | None = None
    manifest_details: dict[str, Any] = {}
    manifest_errors: list[str] = []
    if mapping_errors:
        status = "blocked_replacement_mapping_invalid"
    elif replacement_failures:
        status = "blocked_replacement_validation_failed"
    else:
        backfilled_rows, manifest_details, manifest_errors = apply_backfill_rows(rows, mapping)
        if manifest_errors:
            status = "blocked_manifest_backfill_invalid"

    selected_after_rows = selected_rows(backfilled_rows) if backfilled_rows is not None else selected_before
    public_ledger, local_ledger = build_deferred_ledger(rows, mapping, i5b_summary=i5b_summary or {})
    by_id = index_rows(rows)
    alternate_candidates: dict[str, list[dict[str, Any]]] = {}
    if status != "backfill_applied":
        excluded_ids = set(int(row_id) for row_id in mapping) | set(int(row_id) for row_id in mapping.values())
        for failed_id in mapping:
            failed = by_id.get(int(failed_id))
            if failed:
                alternate_candidates[str(failed_id)] = _alternate_candidates(
                    rows,
                    failed_row=failed,
                    excluded_row_ids=excluded_ids,
                )

    report = {
        "phase": "3.8d-I5c",
        "mode": "validate_and_apply_same_bucket_backfill",
        "created_at": utc_now(),
        "status": status,
        "success": status == "backfill_applied",
        "duration_seconds": round(time.monotonic() - started, 3),
        "input_manifest_selected_total": len(selected_before),
        "selected_total_before": len(selected_before),
        "selected_total_after": len(selected_after_rows),
        "selected_total_preserved": len(selected_before) == len(selected_after_rows),
        "bucket_distribution_before": selected_distribution(rows),
        "bucket_distribution_after": selected_distribution(backfilled_rows) if backfilled_rows is not None else selected_distribution(rows),
        "extension_distribution_before": selected_extension_distribution(rows),
        "extension_distribution_after": (
            selected_extension_distribution(backfilled_rows) if backfilled_rows is not None else selected_extension_distribution(rows)
        ),
        "failed_to_replacement_mapping": mapping_public,
        "mapping_errors": mapping_errors,
        "replacement_validation": {
            "status": "passed" if not replacement_failures and not mapping_errors else "failed",
            "summary": validation_summary,
            "results": validation_results_public,
        },
        "backfill_application": {
            "applied": status == "backfill_applied",
            "local_manifest_written": False,
            "active_backfilled_replacements": [int(row_id) for row_id in mapping.values()] if status == "backfill_applied" else [],
            "unrecovered_original_rows": [int(row_id) for row_id in mapping],
            "manifest_details": manifest_details,
            "manifest_errors": manifest_errors,
            "alternate_candidates_if_blocked": alternate_candidates,
        },
        "deferred_cloud_recovery_ledger": public_ledger,
        "ingestion_observability_principle": INGESTION_OBSERVABILITY_PRINCIPLE,
        "policy": {
            "prefix_read_bytes": int(policy["prefix_bytes"]),
            "prefix_timeout_seconds": int(policy["prefix_timeout_seconds"]),
            "prefix_retries": int(policy["prefix_retries"]),
            "full_read_timeout_seconds": int(policy["full_timeout_seconds"]),
            "full_read_retries": int(policy["full_retries"]),
            "full_read_chunk_size": int(policy["full_chunk_size"]),
            "retry_wait_seconds": int(policy["retry_wait_seconds"]),
            "full_read_required_for_replacement_readiness": True,
            "cfhydrateplaceholder_called": False,
            "backfill_applied_only_after_validation_passes": True,
        },
        "safety": {
            "source_content_read_for_replacement_validation_only": bool(validation_results_public),
            "provider_side_hydration_may_have_occurred": bool(validation_results_public),
            "source_file_content_write_mutation": False,
            "staging_copy": False,
            "staging_write": False,
            "db_import": False,
            "classification": False,
            "ai_tagging": False,
            "localization": False,
            "entity_resolver": False,
            "similarity": False,
            "cleanup_delete": False,
            "manifest_modified_in_repo": False,
            "backfill_applied_to_local_manifest_artifact_only": status == "backfill_applied",
            "app_managed_storage_mutation": False,
            "push_main": False,
            "merge": False,
        },
        "local_artifacts": {
            "backfilled_selected_manifest": DEFAULT_OUTPUT_MANIFEST.name,
            "deferred_cloud_recovery_ledger": DEFAULT_LOCAL_LEDGER.name,
            "validation_details": DEFAULT_LOCAL_DETAILS.name,
            "must_remain_untracked": True,
        },
        "next_step": (
            "Proceed to Phase 3.8d staging copy retry planning if the PR is reviewed and merged."
            if status == "backfill_applied"
            else "Stop: replacement validation or manifest backfill did not pass; user/ChatGPT must choose alternate candidates or provider investigation."
        ),
    }
    public = sanitize_public_obj(report)
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
    local_details = {
        "replacement_validation": validation_results_local,
        "backfilled_rows": backfilled_rows,
        "deferred_cloud_recovery_ledger": local_ledger,
        "manifest_fieldnames": list(rows[0].keys()) if rows else [],
    }
    return public, local_details


def render_replacement_lines(result: Mapping[str, Any]) -> list[str]:
    return [
        f"- Row: `{result['row_id']}`",
        f"- Safe label: `{result['source_safe_label']}`",
        f"- Bucket: `{result['bucket']}`",
        f"- Extension: `{result['extension']}`",
        f"- Expected size: `{result['expected_size']}`",
        f"- Metadata before likely cloud placeholder: `{result.get('metadata_before', {}).get('cloud_state', {}).get('likely_cloud_placeholder')}`",
        f"- Metadata before recall_on_data_access: `{result.get('metadata_before', {}).get('cloud_state', {}).get('recall_on_data_access')}`",
        f"- Prefix read ok: `{result['prefix_read']['ok']}`",
        f"- Full read ok: `{result['full_read']['ok']}`",
        f"- Bytes read: `{result['audit_bytes_read']}`",
        f"- Duration seconds: `{result['duration_seconds']}`",
        f"- Failure reason: `{result['failure_reason']}`",
        f"- Metadata after likely cloud placeholder: `{result.get('metadata_after', {}).get('cloud_state', {}).get('likely_cloud_placeholder')}`",
        f"- Metadata after recall_on_data_access: `{result.get('metadata_after', {}).get('cloud_state', {}).get('recall_on_data_access')}`",
        f"- Staging-copy-ready: `{result['staging_copy_ready']}`",
    ]


def render_markdown(report: Mapping[str, Any]) -> str:
    validation = report["replacement_validation"]
    application = report["backfill_application"]
    lines = [
        "# Phase 3.8d-I5c Backfill Application",
        "",
        "## Summary",
        "",
        f"- Status: `{report['status']}`",
        f"- Success: `{report['success']}`",
        f"- Selected total before: `{report['selected_total_before']}`",
        f"- Selected total after: `{report['selected_total_after']}`",
        f"- Selected total preserved: `{report['selected_total_preserved']}`",
        f"- Bucket distribution before: `{json.dumps(report['bucket_distribution_before'], sort_keys=True)}`",
        f"- Bucket distribution after: `{json.dumps(report['bucket_distribution_after'], sort_keys=True)}`",
        "",
        "## Replacement Validation",
        "",
        f"- Status: `{validation['status']}`",
        f"- Attempted: `{validation['summary']['attempted_count']}`",
        f"- Success count: `{validation['summary']['success_count']}`",
        f"- Failed count: `{validation['summary']['failed_count']}`",
        f"- Bytes read: `{validation['summary']['bytes_read']}`",
        f"- Duration seconds: `{validation['summary']['duration_seconds']}`",
        f"- Failures by reason: `{json.dumps(validation['summary']['failures_by_reason'], sort_keys=True)}`",
        "",
    ]
    for result in validation["results"]:
        lines.extend([f"### Replacement Row {result['row_id']}", "", *render_replacement_lines(result), ""])
    lines.extend(
        [
            "## Backfill Application",
            "",
            f"- Applied: `{application['applied']}`",
            f"- Active backfilled replacements: `{json.dumps(application['active_backfilled_replacements'])}`",
            f"- Unrecovered original rows: `{json.dumps(application['unrecovered_original_rows'])}`",
            f"- Local manifest written: `{application['local_manifest_written']}`",
            "- This is not silent skipping: unrecovered original rows are retained in the deferred cloud recovery ledger.",
            "",
            "## Deferred Cloud Recovery Ledger",
            "",
            f"- Status: `{report['deferred_cloud_recovery_ledger']['status']}`",
        ]
    )
    for row in report["deferred_cloud_recovery_ledger"]["rows"]:
        lines.append(
            f"- Original `{row['source_safe_label']}` is deferred with reason `{row['deferred_reason']}` "
            f"and replacement `{row['replacement_safe_label']}`."
        )
        state = row.get("per_run_final_state", {})
        lines.append(
            f"  Final state: failed=`{state.get('failed')}`, retried=`{state.get('retried')}`, "
            f"backfilled=`{state.get('backfilled')}`, deferred_for_cloud_recovery=`{state.get('deferred_for_cloud_recovery')}`, "
            f"imported_into_db=`{state.get('imported_into_db')}`, unresolved=`{state.get('unresolved')}`."
        )
    lines.extend(
        [
            "",
            "## Ingestion Observability Principle",
            "",
            "- Future production ingestion must record a per-run final state for every source item.",
            "- Reports must answer which source items succeeded, failed, retried, backfilled, deferred for cloud recovery, imported into DB, excluded as ineligible, or remain unresolved.",
            "- Failed cloud-backed items must not be mixed with successfully imported items or hidden behind aggregate totals.",
            "- Reporting must be scoped to the current run, manifest, or job rather than only global library totals.",
            "- I5c records this principle and the current deferred cloud recovery ledger only; it does not add a DB migration or full production ledger.",
        ]
    )
    lines.extend(["", "## Safety", ""])
    for key, value in report["safety"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Privacy",
            "",
            f"- Passed: `{report['privacy']['passed']}`",
            f"- Leaks: `{json.dumps(report['privacy']['leaks'], ensure_ascii=False)}`",
            f"- Local manifest artifact: `{report['local_artifacts']['backfilled_selected_manifest']}`",
            f"- Local ledger artifact: `{report['local_artifacts']['deferred_cloud_recovery_ledger']}`",
            f"- Local validation details: `{report['local_artifacts']['validation_details']}`",
            "",
            "## Next Step",
            "",
            report["next_step"],
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--i5b-summary", type=Path, default=DEFAULT_I5B_SUMMARY)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_OUTPUT_MANIFEST)
    parser.add_argument("--local-ledger-json", type=Path, default=DEFAULT_LOCAL_LEDGER)
    parser.add_argument("--local-details-json", type=Path, default=DEFAULT_LOCAL_DETAILS)
    parser.add_argument("--replacement", action="append", default=[])
    parser.add_argument("--prefix-bytes", type=_positive_int, default=DEFAULT_PREFIX_BYTES)
    parser.add_argument("--prefix-timeout", type=_positive_int, default=DEFAULT_PREFIX_TIMEOUT_SECONDS)
    parser.add_argument("--prefix-retries", type=_non_negative_int, default=DEFAULT_PREFIX_RETRIES)
    parser.add_argument("--full-timeout", type=_positive_int, default=DEFAULT_FULL_TIMEOUT_SECONDS)
    parser.add_argument("--full-retries", type=_non_negative_int, default=DEFAULT_FULL_RETRIES)
    parser.add_argument("--retry-wait", type=_non_negative_int, default=DEFAULT_RETRY_WAIT_SECONDS)
    parser.add_argument("--full-chunk-size", type=_positive_int, default=DEFAULT_FULL_CHUNK_SIZE)
    return parser


def _parse_mapping(values: Sequence[str]) -> dict[int, int]:
    if not values:
        return dict(DEFAULT_FAILED_TO_REPLACEMENT)
    mapping: dict[int, int] = {}
    for value in values:
        if ":" not in value:
            raise argparse.ArgumentTypeError("replacement must be FAILED_ROW:REPLACEMENT_ROW")
        left, right = value.split(":", 1)
        failed = int(left)
        replacement = int(right)
        if failed <= 0 or replacement <= 0:
            raise argparse.ArgumentTypeError("row ids must be positive")
        mapping[failed] = replacement
    return mapping


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.manifest.is_file():
        parser.error(f"manifest not found: {args.manifest}")
    try:
        mapping = _parse_mapping(args.replacement)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))
    policy = {
        "prefix_bytes": args.prefix_bytes,
        "prefix_timeout_seconds": args.prefix_timeout,
        "prefix_retries": args.prefix_retries,
        "full_timeout_seconds": args.full_timeout,
        "full_retries": args.full_retries,
        "retry_wait_seconds": args.retry_wait,
        "full_chunk_size": args.full_chunk_size,
    }
    rows = read_manifest(args.manifest)
    report, local_details = run_backfill_application(
        rows,
        mapping=mapping,
        policy=policy,
        i5b_summary=_load_i5b_summary(args.i5b_summary),
    )
    report["manifest_label"] = args.manifest.name
    report["local_artifacts"]["backfilled_selected_manifest"] = args.output_manifest.name
    report["local_artifacts"]["deferred_cloud_recovery_ledger"] = args.local_ledger_json.name
    report["local_artifacts"]["validation_details"] = args.local_details_json.name

    backfilled_rows = local_details.get("backfilled_rows")
    if report.get("success") and backfilled_rows is not None:
        write_manifest(args.output_manifest, backfilled_rows, local_details["manifest_fieldnames"])
        report["backfill_application"]["local_manifest_written"] = True

    write_json(args.local_ledger_json, local_details["deferred_cloud_recovery_ledger"])
    write_json(
        args.local_details_json,
        {
            "created_at": report["created_at"],
            "manifest": str(args.manifest),
            "policy": policy,
            "mapping": mapping,
            "details": local_details,
        },
    )
    write_json(args.report_json, report)
    write_markdown(args.report_md, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
