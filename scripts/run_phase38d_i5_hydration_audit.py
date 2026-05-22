#!/usr/bin/env python3
"""Phase 3.8d-I5 controlled read-probe / hydration audit.

This operational audit reads selected source files only under explicit I5
approval.  It never writes staging files, mutates the database, or modifies
source file contents.  Cloud provider-side hydration/cache changes may occur
as a consequence of bounded reads.
"""

from __future__ import annotations

import argparse
import json
import os
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
from app.services.source_ingestion_gate import SourceIngestionGate  # noqa: E402
from app.utils.cloud_files import (  # noqa: E402
    CloudFileState,
    read_probe_prefix,
    read_verify_full_content,
)
from audit_cloud_availability import (  # noqa: E402
    plan_same_bucket_backfill,
    read_manifest,
    row_size,
    safe_row_label,
)


DEFAULT_MANIFEST = REPO_ROOT / ".local_manifests" / "phase-3.8c-medium-candidate-manifest.csv"
DEFAULT_REPORT_JSON = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i5-controlled-hydration-audit-summary.json"
DEFAULT_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i5-controlled-hydration-audit.md"
DEFAULT_LOCAL_DETAILS = REPO_ROOT / ".local_manifests" / "phase-3.8d-i5-hydration-audit-details.json"
COPY_SELECTION_REASONS = {"new_candidate", "existing_tier500"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative integer")
    return parsed


def is_selected_copy_row(row: Mapping[str, str]) -> bool:
    return (row.get("selection_reason") or "").strip() in COPY_SELECTION_REASONS and not (
        row.get("exclusion_reason") or ""
    ).strip()


def selected_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    def _row_id(row: Mapping[str, str]) -> int:
        try:
            return int(row.get("row_id") or 0)
        except ValueError:
            return 0

    return sorted([row for row in rows if is_selected_copy_row(row)], key=_row_id)


def _state_dict(state: CloudFileState | None) -> dict[str, Any]:
    if state is None:
        return {
            "supported_platform": False,
            "exists": False,
            "is_file": False,
            "likely_cloud_placeholder": False,
            "error_message": "source ingestion gate returned no cloud state",
        }
    return state.to_dict(include_path=False)


def build_metadata_records(rows: Sequence[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    local_records: list[dict[str, Any]] = []
    for row in selected_rows(rows):
        source_path = Path(row["source_path"])
        safe_label = safe_row_label(row)
        gate = SourceIngestionGate.evaluate_path_source(
            source_path,
            safe_label=safe_label,
            hydration_policy_enabled=False,
        )
        state = gate.cloud_state
        public = {
            "row_id": int(row.get("row_id") or 0),
            "source_safe_label": safe_label,
            "extension": (row.get("extension") or source_path.suffix).lower(),
            "bucket": row.get("temporal_bucket") or "unknown",
            "expected_size": row_size(row),
            "exists": bool(state and state.exists),
            "is_file": bool(state and state.is_file),
            "likely_cloud_placeholder": bool(state and state.likely_cloud_placeholder),
            "cloud_state": _state_dict(state),
            "source_ingestion_gate": gate.to_public_dict(),
        }
        local = {**public, "source_path": str(source_path)}
        records.append(public)
        local_records.append(local)
    return records, local_records


def summarize_metadata(records: Sequence[Mapping[str, Any]], *, failed_row_id: int) -> dict[str, Any]:
    risky = [record for record in records if record.get("likely_cloud_placeholder")]
    target_failed_row = next((record for record in records if int(record.get("row_id", 0)) == failed_row_id), None)
    return {
        "selected_total": len(records),
        "exists": sum(1 for record in records if record.get("exists")),
        "missing": sum(1 for record in records if not record.get("exists")),
        "offline_count": sum(1 for record in records if record["cloud_state"].get("offline")),
        "reparse_point_count": sum(1 for record in records if record["cloud_state"].get("reparse_point")),
        "recall_on_open_count": sum(1 for record in records if record["cloud_state"].get("recall_on_open")),
        "recall_on_data_access_count": sum(
            1 for record in records if record["cloud_state"].get("recall_on_data_access")
        ),
        "pinned_count": sum(1 for record in records if record["cloud_state"].get("pinned")),
        "unpinned_count": sum(1 for record in records if record["cloud_state"].get("unpinned")),
        "sparse_file_count": sum(1 for record in records if record["cloud_state"].get("sparse_file")),
        "likely_cloud_placeholder_count": len(risky),
        "risky_count_by_bucket": dict(sorted(Counter(str(record["bucket"]) for record in risky).items())),
        "risky_count_by_extension": dict(sorted(Counter(str(record["extension"]) for record in risky).items())),
        "selected_count_by_bucket": dict(sorted(Counter(str(record["bucket"]) for record in records).items())),
        "selected_count_by_extension": dict(sorted(Counter(str(record["extension"]) for record in records).items())),
        "target_failed_row_id": failed_row_id,
        "target_failed_row_safe_label": target_failed_row.get("source_safe_label") if target_failed_row else None,
        "target_failed_row": target_failed_row,
    }


def select_sample_records(
    metadata_records: Sequence[Mapping[str, Any]],
    *,
    failed_row_id: int,
    sample_per_bucket: int,
    max_sample: int,
) -> list[dict[str, Any]]:
    by_row_id = {int(record["row_id"]): dict(record) for record in metadata_records}
    chosen: list[dict[str, Any]] = []
    chosen_ids: set[int] = set()

    risky = [dict(record) for record in metadata_records if record.get("likely_cloud_placeholder")]
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for record in risky:
        by_bucket.setdefault(str(record["bucket"]), []).append(record)
    for bucket_records in by_bucket.values():
        bucket_records.sort(key=lambda item: int(item["row_id"]))

    if failed_row_id in by_row_id and by_row_id[failed_row_id].get("likely_cloud_placeholder"):
        chosen.append(by_row_id[failed_row_id])
        chosen_ids.add(failed_row_id)

    for bucket in sorted(by_bucket):
        picked_in_bucket = 0
        for record in by_bucket[bucket]:
            row_id = int(record["row_id"])
            if row_id in chosen_ids:
                continue
            if len(chosen) >= max_sample:
                return chosen
            chosen.append(record)
            chosen_ids.add(row_id)
            picked_in_bucket += 1
            if picked_in_bucket >= sample_per_bucket:
                break
    return chosen


def _normal_failure_reason(result: Mapping[str, Any] | None, state: Mapping[str, Any]) -> str | None:
    if not result:
        return None
    reason = result.get("error_reason")
    if reason in {"read_probe_timeout", "read_timeout"}:
        return "read_timeout"
    if reason in {"read_probe_no_result", "read_no_result"}:
        return "generic_read_failed"
    if reason in {"invalid_chunk_size", "read_worker_eof"}:
        return str(reason)
    if reason == "generic_copy_failed" and state.get("likely_cloud_placeholder"):
        return "cloud_hydration_failed"
    return str(reason) if reason else None


def verify_record_readability(record: Mapping[str, Any], *, policy: Mapping[str, int]) -> dict[str, Any]:
    source_path = Path(str(record["source_path"]))
    public_base = {
        "row_id": int(record["row_id"]),
        "source_safe_label": record["source_safe_label"],
        "bucket": record["bucket"],
        "extension": record["extension"],
        "expected_size": int(record["expected_size"]),
    }
    metadata_before_gate = SourceIngestionGate.evaluate_path_source(
        source_path,
        safe_label=str(record["source_safe_label"]),
        hydration_policy_enabled=True,
    )
    metadata_before = _state_dict(metadata_before_gate.cloud_state)

    prefix_started = time.monotonic()
    prefix = read_probe_prefix(
        source_path,
        max_bytes=int(policy["prefix_bytes"]),
        timeout_seconds=int(policy["prefix_timeout_seconds"]),
        retries=int(policy["prefix_retries"]),
    )
    prefix["duration_seconds"] = time.monotonic() - prefix_started
    if prefix.get("ok"):
        full = read_verify_full_content(
            source_path,
            expected_size=int(record["expected_size"]),
            timeout_seconds=int(policy["full_timeout_seconds"]),
            retries=int(policy["full_retries"]),
            chunk_size=int(policy["full_chunk_size"]),
        )
    else:
        full = {
            "full_read": False,
            "ok": False,
            "skipped": True,
            "expected_size": int(record["expected_size"]),
            "bytes_read": 0,
            "bytes_read_total": 0,
            "duration_seconds": 0.0,
            "error_reason": _normal_failure_reason(prefix, metadata_before) or "prefix_read_failed",
            "error_message": "full read skipped because prefix read failed",
        }

    metadata_after_gate = SourceIngestionGate.evaluate_path_source(
        source_path,
        safe_label=str(record["source_safe_label"]),
        hydration_policy_enabled=True,
    )
    metadata_after = _state_dict(metadata_after_gate.cloud_state)
    prefix_failure = _normal_failure_reason(prefix, metadata_before)
    full_failure = _normal_failure_reason(full, metadata_before)
    ok = bool(prefix.get("ok") and full.get("ok"))
    public = {
        **public_base,
        "metadata_before": metadata_before,
        "prefix_read": {
            "ok": bool(prefix.get("ok")),
            "bytes_read": int(prefix.get("bytes_read", 0) or 0),
            "duration_seconds": round(float(prefix.get("duration_seconds", 0.0) or 0.0), 3),
            "error_reason": prefix_failure,
        },
        "full_read": {
            "ok": bool(full.get("ok")),
            "skipped": bool(full.get("skipped", False)),
            "bytes_read": int(full.get("bytes_read", 0) or 0),
            "bytes_read_total": int(full.get("bytes_read_total", full.get("bytes_read", 0)) or 0),
            "duration_seconds": round(float(full.get("duration_seconds", 0.0) or 0.0), 3),
            "error_reason": full_failure,
        },
        "metadata_after": metadata_after,
        "ok": ok,
        "failure_reason": None if ok else (full_failure or prefix_failure or "generic_read_failed"),
        "audit_bytes_read": int(prefix.get("bytes_read", 0) or 0)
        + int(full.get("bytes_read_total", full.get("bytes_read", 0)) or 0),
        "duration_seconds": round(
            float(prefix.get("duration_seconds", 0.0) or 0.0)
            + float(full.get("duration_seconds", 0.0) or 0.0),
            3,
        ),
    }
    local = {
        **public,
        "source_path": str(source_path),
        "prefix_read_local": prefix,
        "full_read_local": full,
    }
    return {"public": public, "local": local}


def summarize_verifications(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failures = [result for result in results if not result.get("ok")]
    return {
        "attempted_count": len(results),
        "success_count": len(results) - len(failures),
        "failed_count": len(failures),
        "bytes_read": sum(int(result.get("audit_bytes_read", 0) or 0) for result in results),
        "duration_seconds": round(sum(float(result.get("duration_seconds", 0.0) or 0.0) for result in results), 3),
        "failures_by_reason": dict(sorted(Counter(str(item.get("failure_reason")) for item in failures).items())),
        "failures_by_bucket": dict(sorted(Counter(str(item.get("bucket")) for item in failures).items())),
        "failures_by_extension": dict(sorted(Counter(str(item.get("extension")) for item in failures).items())),
        "failed_rows": [
            {
                "row_id": int(item["row_id"]),
                "source_safe_label": item["source_safe_label"],
                "bucket": item["bucket"],
                "extension": item["extension"],
                "reason": item.get("failure_reason"),
            }
            for item in failures[:20]
        ],
    }


def _proxy_observations() -> dict[str, Any]:
    names = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"]
    detected = {name: bool(os.environ.get(name)) for name in names}
    return {
        "proxy_env_detected": any(detected.values()),
        "proxy_env_names_redacted": detected,
        "note": "Proxy values are local environment details and are intentionally redacted.",
    }


def derive_audit_status(sample_stage: Mapping[str, Any], full_stage: Mapping[str, Any]) -> str:
    sample_status = str(sample_stage.get("status") or "")
    full_status = str(full_stage.get("status") or "")
    if sample_status == "blocked_empty_sample_selection" or full_status == "blocked_empty_sample_selection":
        return "blocked_empty_sample_selection"
    if sample_status == "failed" or full_status == "skipped_sample_gate_failed":
        return "blocked_sample_gate_failed"
    if full_status == "completed" and int(full_stage.get("summary", {}).get("failed_count", 0) or 0) > 0:
        return "blocked_full_recall_verification_failed"
    return "completed"


def derive_audit_success(sample_stage: Mapping[str, Any], full_stage: Mapping[str, Any]) -> bool:
    return not derive_audit_status(sample_stage, full_stage).startswith("blocked_")


def run_hydration_audit(
    rows: Sequence[dict[str, str]],
    *,
    failed_row_id: int,
    stop_after: str,
    sample_per_bucket: int,
    max_sample: int,
    policy: Mapping[str, int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    metadata_records, local_metadata = build_metadata_records(rows)
    metadata_summary = summarize_metadata(metadata_records, failed_row_id=failed_row_id)
    sample_stage: dict[str, Any] = {"status": "not_requested", "results": []}
    full_stage: dict[str, Any] = {"status": "not_requested", "results": []}
    local_details: dict[str, Any] = {"metadata_baseline": local_metadata, "sample": [], "full_recall_verification": []}

    sample_results_public: list[dict[str, Any]] = []
    sample_results_local: list[dict[str, Any]] = []
    if stop_after in {"sample", "full"}:
        sample = select_sample_records(
            metadata_records,
            failed_row_id=failed_row_id,
            sample_per_bucket=sample_per_bucket,
            max_sample=max_sample,
        )
        local_by_id = {int(item["row_id"]): item for item in local_metadata}
        for record in sample:
            verified = verify_record_readability(local_by_id[int(record["row_id"])], policy=policy)
            sample_results_public.append(verified["public"])
            sample_results_local.append(verified["local"])
        sample_summary = summarize_verifications(sample_results_public)
        risky_count = int(metadata_summary["likely_cloud_placeholder_count"])
        if sample_summary["attempted_count"] == 0 and risky_count == 0:
            sample_status = "not_applicable_no_risk"
            sample_reason = "no recall-risk rows were present; sample verification was not required"
        elif sample_summary["attempted_count"] == 0:
            sample_status = "blocked_empty_sample_selection"
            sample_reason = "recall-risk rows existed, but sample selection produced no rows"
        elif sample_summary["failed_count"] == 0:
            sample_status = "passed"
            sample_reason = None
        else:
            sample_status = "failed"
            sample_reason = "sample gate did not pass; full recall verification was not run"
        sample_stage = {
            "status": sample_status,
            "target_failed_row_included": any(
                int(item["row_id"]) == failed_row_id for item in sample_results_public
            ),
            "sample_policy": {
                "sample_per_bucket": sample_per_bucket,
                "max_sample": max_sample,
                "target_failed_row_id": failed_row_id,
                "target_failed_row_required_when_recall_risk": True,
            },
            "summary": sample_summary,
            "results": sample_results_public,
        }
        if sample_reason:
            sample_stage["reason"] = sample_reason
        local_details["sample"] = sample_results_local
        if sample_status == "failed":
            full_stage = {
                "status": "skipped_sample_gate_failed",
                "reason": sample_reason,
                "results": [],
            }
        elif sample_status == "blocked_empty_sample_selection":
            full_stage = {
                "status": "blocked_empty_sample_selection",
                "reason": sample_reason,
                "results": [],
            }
        elif sample_status == "not_applicable_no_risk":
            full_stage = {
                "status": "not_applicable_no_risk",
                "reason": sample_reason,
                "results": [],
            }

    if stop_after == "full":
        if sample_stage.get("status") == "failed":
            full_stage = {
                "status": "skipped_sample_gate_failed",
                "reason": sample_stage.get("reason") or "sample gate did not pass; full recall verification was not run",
                "results": [],
            }
        elif sample_stage.get("status") == "blocked_empty_sample_selection":
            full_stage = {
                "status": "blocked_empty_sample_selection",
                "reason": sample_stage.get("reason") or "recall-risk rows existed, but sample selection produced no rows",
                "results": [],
            }
        elif sample_stage.get("status") == "not_applicable_no_risk":
            full_stage = {
                "status": "not_applicable_no_risk",
                "reason": sample_stage.get("reason") or "no recall-risk rows were present; full recall verification was not required",
                "results": [],
            }
        else:
            post_sample_records, local_post_sample = build_metadata_records(rows)
            risky_after_sample = [record for record in post_sample_records if record.get("likely_cloud_placeholder")]
            local_post_by_id = {int(item["row_id"]): item for item in local_post_sample}
            sample_by_id = {int(item["row_id"]): item for item in sample_results_public if item.get("ok")}
            full_results_public: list[dict[str, Any]] = []
            full_results_local: list[dict[str, Any]] = []
            for record in risky_after_sample:
                row_id = int(record["row_id"])
                if row_id in sample_by_id:
                    reused = {**sample_by_id[row_id], "result_source": "sample_gate_reused"}
                    full_results_public.append(reused)
                    full_results_local.append(reused)
                    continue
                verified = verify_record_readability(local_post_by_id[row_id], policy=policy)
                verified["public"]["result_source"] = "full_recall_verification"
                verified["local"]["result_source"] = "full_recall_verification"
                full_results_public.append(verified["public"])
                full_results_local.append(verified["local"])
            post_full_records, local_post_full = build_metadata_records(rows)
            post_full_summary = summarize_metadata(post_full_records, failed_row_id=failed_row_id)
            full_stage = {
                "status": "completed",
                "recall_count_after_sample": len(risky_after_sample),
                "summary": summarize_verifications(full_results_public),
                "remaining_recall_cloud_count": post_full_summary["likely_cloud_placeholder_count"],
                "remaining_risky_by_bucket": post_full_summary["risky_count_by_bucket"],
                "remaining_risky_by_extension": post_full_summary["risky_count_by_extension"],
                "results": full_results_public,
            }
            local_details["metadata_after_full"] = local_post_full
            local_details["full_recall_verification"] = full_results_local

    failed_ids: list[int] = []
    if full_stage.get("status") == "completed":
        failed_ids = [int(item["row_id"]) for item in full_stage["results"] if not item.get("ok")]
    elif sample_stage.get("status") == "failed":
        failed_ids = [int(item["row_id"]) for item in sample_stage["results"] if not item.get("ok")]
    backfill_plan = plan_same_bucket_backfill(rows, failed_ids) if failed_ids else None

    target_failed_row_result = next(
        (item for item in [*sample_stage.get("results", []), *full_stage.get("results", [])] if int(item["row_id"]) == failed_row_id),
        None,
    )
    target_failed_row_metadata = metadata_summary.get("target_failed_row")
    target_failed_row_safe_label = None
    if isinstance(target_failed_row_result, Mapping):
        target_failed_row_safe_label = target_failed_row_result.get("source_safe_label")
    elif isinstance(target_failed_row_metadata, Mapping):
        target_failed_row_safe_label = target_failed_row_metadata.get("source_safe_label")
    audit_status = derive_audit_status(sample_stage, full_stage)
    report = {
        "phase": "3.8d-I5",
        "mode": "controlled_read_probe_hydration_audit",
        "created_at": utc_now(),
        "status": audit_status,
        "success": derive_audit_success(sample_stage, full_stage),
        "duration_seconds": round(time.monotonic() - started, 3),
        "target_failed_row_id": failed_row_id,
        "target_failed_row_safe_label": target_failed_row_safe_label,
        "policy": {
            "metadata_only_baseline_no_content_read": True,
            "prefix_read_bytes": policy["prefix_bytes"],
            "prefix_timeout_seconds": policy["prefix_timeout_seconds"],
            "prefix_retries": policy["prefix_retries"],
            "full_read_timeout_seconds": policy["full_timeout_seconds"],
            "full_read_retries": policy["full_retries"],
            "full_read_chunk_size": policy["full_chunk_size"],
            "full_read_required_for_copy_readiness": True,
            "cfhydrateplaceholder_called": False,
        },
        "metadata_baseline": metadata_summary,
        "target_failed_row_result": target_failed_row_result,
        "sample_gate": sample_stage,
        "full_recall_verification": full_stage,
        "backfill_plan": backfill_plan,
        "network_proxy_observations": _proxy_observations(),
        "safety": {
            "source_content_read_for_verification_only": bool(sample_results_public),
            "provider_side_hydration_may_have_occurred": bool(sample_results_public),
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
            "app_managed_storage_mutation": False,
        },
        "local_artifacts": {
            "details_artifact": DEFAULT_LOCAL_DETAILS.name,
            "full_paths_in_public_reports": False,
            "must_remain_untracked": True,
        },
    }
    public = sanitize_public_obj(report)
    leaks = find_privacy_leaks(public)
    public["privacy"] = {
        "paths_redacted": True,
        "safe_labels_only": True,
        "proxy_values_redacted": True,
        "leaks": leaks,
        "passed": not leaks,
    }
    if leaks:
        public["status"] = "blocked_privacy_leak"
        public["success"] = False
    return public, local_details


def render_markdown(report: Mapping[str, Any]) -> str:
    baseline = report["metadata_baseline"]
    sample = report["sample_gate"]
    full = report["full_recall_verification"]
    target_failed_row_id = int(report.get("target_failed_row_id") or baseline.get("target_failed_row_id") or 0)
    target_failed_row = report.get("target_failed_row_result")
    lines = [
        "# Phase 3.8d-I5 Controlled Hydration Audit",
        "",
        "## Summary",
        "",
        f"- Status: `{report.get('status', 'unknown')}`",
        f"- Success: `{report['success']}`",
        f"- Mode: `{report['mode']}`",
        f"- Duration seconds: `{report['duration_seconds']}`",
        f"- Selected total: `{baseline['selected_total']}`",
        f"- Baseline recall-risk count: `{baseline['likely_cloud_placeholder_count']}`",
        f"- Sample gate status: `{sample['status']}`",
        f"- Full recall verification status: `{full['status']}`",
        f"- Full read required for copy readiness: `{report['policy']['full_read_required_for_copy_readiness']}`",
        "",
        "## Metadata Baseline",
        "",
        f"- Exists: `{baseline['exists']}`",
        f"- Missing: `{baseline['missing']}`",
        f"- Offline: `{baseline['offline_count']}`",
        f"- Reparse point: `{baseline['reparse_point_count']}`",
        f"- Recall on open: `{baseline['recall_on_open_count']}`",
        f"- Recall on data access: `{baseline['recall_on_data_access_count']}`",
        f"- Pinned: `{baseline['pinned_count']}`",
        f"- Unpinned: `{baseline['unpinned_count']}`",
        f"- Sparse file: `{baseline['sparse_file_count']}`",
        f"- Risky by bucket: `{json.dumps(baseline['risky_count_by_bucket'], sort_keys=True)}`",
        f"- Risky by extension: `{json.dumps(baseline['risky_count_by_extension'], sort_keys=True)}`",
        "",
        f"## Target Failed Row {target_failed_row_id}",
        "",
    ]
    if target_failed_row:
        lines.extend(
            [
                f"- Included in sample/full results: `True`",
                f"- Source label: `{target_failed_row['source_safe_label']}`",
                f"- Prefix read ok: `{target_failed_row['prefix_read']['ok']}`",
                f"- Full read ok: `{target_failed_row['full_read']['ok']}`",
                f"- Bytes read: `{target_failed_row['audit_bytes_read']}`",
                f"- Failure reason: `{target_failed_row['failure_reason']}`",
            ]
        )
    else:
        lines.append(f"- Target failed row {target_failed_row_id} was not verified in this run.")
    lines.extend(
        [
            "",
            "## Sample Gate",
            "",
        ]
    )
    if "summary" in sample:
        lines.extend(
            [
                f"- Attempted: `{sample['summary']['attempted_count']}`",
                f"- Success: `{sample['summary']['success_count']}`",
                f"- Failed: `{sample['summary']['failed_count']}`",
                f"- Bytes read: `{sample['summary']['bytes_read']}`",
                f"- Duration seconds: `{sample['summary']['duration_seconds']}`",
                f"- Failures by reason: `{json.dumps(sample['summary']['failures_by_reason'], sort_keys=True)}`",
            ]
        )
    else:
        lines.append("- Not requested.")
    lines.extend(["", "## Full Recall Verification", ""])
    if "summary" in full:
        lines.extend(
            [
                f"- Recall-risk count after sample: `{full['recall_count_after_sample']}`",
                f"- Attempted: `{full['summary']['attempted_count']}`",
                f"- Success: `{full['summary']['success_count']}`",
                f"- Failed: `{full['summary']['failed_count']}`",
                f"- Bytes read: `{full['summary']['bytes_read']}`",
                f"- Duration seconds: `{full['summary']['duration_seconds']}`",
                f"- Remaining recall-risk count: `{full['remaining_recall_cloud_count']}`",
                f"- Failures by reason: `{json.dumps(full['summary']['failures_by_reason'], sort_keys=True)}`",
                f"- Failures by bucket: `{json.dumps(full['summary']['failures_by_bucket'], sort_keys=True)}`",
                f"- Failures by extension: `{json.dumps(full['summary']['failures_by_extension'], sort_keys=True)}`",
            ]
        )
    else:
        lines.append(f"- Status: `{full['status']}`")
        if full.get("reason"):
            lines.append(f"- Reason: `{full['reason']}`")
    lines.extend(["", "## Backfill Dry-run", ""])
    backfill = report.get("backfill_plan")
    if backfill:
        lines.extend(
            [
                f"- Replacement count: `{backfill['replacement_count']}`",
                f"- Unresolved count: `{backfill['unresolved_count']}`",
                "- Backfill was not applied; this is a future approval decision.",
            ]
        )
    else:
        lines.append("- No failed full-read rows requiring backfill dry-run.")
    lines.extend(
        [
            "",
            "## Proxy / Network",
            "",
            f"- Proxy detected: `{report['network_proxy_observations']['proxy_env_detected']}`",
            "- Proxy values: `redacted`",
            "",
            "## Safety",
            "",
        ]
    )
    for key, value in report["safety"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Privacy",
            "",
            f"- Passed: `{report['privacy']['passed']}`",
            f"- Leaks: `{json.dumps(report['privacy']['leaks'], ensure_ascii=False)}`",
            f"- Local details artifact: `{report['local_artifacts']['details_artifact']}`",
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
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--local-details-json", type=Path, default=DEFAULT_LOCAL_DETAILS)
    parser.add_argument("--failed-row-id", type=_positive_int, default=98)
    parser.add_argument("--stop-after", choices=["metadata", "sample", "full"], default="full")
    parser.add_argument("--sample-per-bucket", type=_positive_int, default=3)
    parser.add_argument("--max-sample", type=_positive_int, default=48)
    parser.add_argument("--prefix-bytes", type=_positive_int, default=1)
    parser.add_argument("--prefix-timeout", type=_positive_int, default=10)
    parser.add_argument("--prefix-retries", type=_non_negative_int, default=1)
    parser.add_argument("--full-timeout", type=_positive_int, default=60)
    parser.add_argument("--full-retries", type=_non_negative_int, default=1)
    parser.add_argument("--full-chunk-size", type=_positive_int, default=4 * 1024 * 1024)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.manifest.is_file():
        parser.error(f"manifest not found: {args.manifest}")
    policy = {
        "prefix_bytes": args.prefix_bytes,
        "prefix_timeout_seconds": args.prefix_timeout,
        "prefix_retries": args.prefix_retries,
        "full_timeout_seconds": args.full_timeout,
        "full_retries": args.full_retries,
        "full_chunk_size": args.full_chunk_size,
    }
    rows = read_manifest(args.manifest)
    report, local_details = run_hydration_audit(
        rows,
        failed_row_id=args.failed_row_id,
        stop_after=args.stop_after,
        sample_per_bucket=args.sample_per_bucket,
        max_sample=args.max_sample,
        policy=policy,
    )
    report["manifest_label"] = args.manifest.name
    report["local_artifacts"]["details_artifact"] = args.local_details_json.name
    write_json(args.report_json, report)
    write_markdown(args.report_md, report)
    write_json(
        args.local_details_json,
        {
            "created_at": report["created_at"],
            "manifest": str(args.manifest),
            "policy": policy,
            "details": local_details,
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
