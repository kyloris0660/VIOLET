#!/usr/bin/env python3
"""Phase 3.8d-I5b targeted hydration retry for rows 98 and 881.

This operational audit is intentionally narrow: it reads only explicitly
targeted source rows under I5b approval.  It never writes staging files,
changes the manifest, mutates the database, or modifies source file contents.
Cloud provider-side hydration/cache changes may occur as a consequence of the
bounded reads.
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
from run_phase38d_i5_hydration_audit import (  # noqa: E402
    COPY_SELECTION_REASONS,
    _normal_failure_reason,
    _state_dict,
)


DEFAULT_MANIFEST = REPO_ROOT / ".local_manifests" / "phase-3.8c-medium-candidate-manifest.csv"
DEFAULT_REPORT_JSON = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i5b-targeted-hydration-retry-summary.json"
DEFAULT_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i5b-targeted-hydration-retry.md"
DEFAULT_LOCAL_DETAILS = REPO_ROOT / ".local_manifests" / "phase-3.8d-i5b-targeted-hydration-details.json"
DEFAULT_TARGET_ROW_IDS = (98, 881)
DEFAULT_PREFIX_BYTES = 1
DEFAULT_PREFIX_TIMEOUT_SECONDS = 30
DEFAULT_PREFIX_RETRIES = 2
DEFAULT_FULL_TIMEOUT_SECONDS = 180
DEFAULT_FULL_RETRIES = 2
DEFAULT_RETRY_WAIT_SECONDS = 10
DEFAULT_FULL_CHUNK_SIZE = 4 * 1024 * 1024


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


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(payload), encoding="utf-8")


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


def select_target_rows(rows: Sequence[dict[str, str]], target_row_ids: Sequence[int]) -> list[dict[str, str]]:
    selected_by_id = {int(row["row_id"]): row for row in selected_rows(rows)}
    targets: list[dict[str, str]] = []
    for row_id in target_row_ids:
        row = selected_by_id.get(int(row_id))
        if row is not None:
            targets.append(row)
    return targets


def target_record(row: Mapping[str, str]) -> dict[str, Any]:
    source_path = Path(row["source_path"])
    return {
        "row_id": int(row["row_id"]),
        "source_safe_label": safe_row_label(row),
        "extension": (row.get("extension") or source_path.suffix).lower(),
        "bucket": row.get("temporal_bucket") or "unknown",
        "expected_size": row_size(row),
        "source_path": str(source_path),
    }


def _metadata_state(source_path: Path, safe_label: str) -> dict[str, Any]:
    gate = SourceIngestionGate.evaluate_path_source(
        source_path,
        safe_label=safe_label,
        hydration_policy_enabled=True,
    )
    state = gate.cloud_state or CloudFileState(
        path=str(source_path),
        supported_platform=False,
        exists=False,
        is_file=False,
        error_message="source ingestion gate returned no cloud state",
    )
    return {
        "source_ingestion_gate": gate.to_public_dict(),
        "cloud_state": _state_dict(state),
        "source_missing": not bool(state.exists),
        "source_not_file": not bool(state.is_file),
    }


def _proxy_observations() -> dict[str, Any]:
    names = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"]
    detected = {name: bool(os.environ.get(name)) for name in names}
    return {
        "proxy_env_detected": any(detected.values()),
        "proxy_env_names_redacted": detected,
        "note": "Proxy values are local environment details and are intentionally redacted.",
    }


def _safe_exception_result(stage: str, exc: BaseException, attempt: int) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "ok": False,
        "bytes_read": 0,
        "bytes_read_total": 0,
        "duration_seconds": 0.0,
        "error_reason": f"{stage}_exception",
        "error_message": f"{stage} raised {type(exc).__name__}",
    }


def _run_stage_attempts(
    operation: Callable[[], dict[str, Any]],
    *,
    stage: str,
    retries: int,
    wait_seconds: int,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    for attempt_index in range(1, retries + 2):
        started = time.monotonic()
        try:
            result = dict(operation())
        except Exception as exc:
            result = _safe_exception_result(stage, exc, attempt_index)
        result.setdefault("attempt", attempt_index)
        result.setdefault("bytes_read", 0)
        result.setdefault("bytes_read_total", result.get("bytes_read", 0))
        if "duration_seconds" not in result:
            result["duration_seconds"] = time.monotonic() - started
        result["stage_attempt"] = attempt_index
        attempts.append(result)
        final = result
        if result.get("ok"):
            break
        if attempt_index <= retries and wait_seconds > 0:
            sleeper(float(wait_seconds))
    return {
        "stage": stage,
        "retries": retries,
        "wait_seconds": wait_seconds,
        "attempts": attempts,
        "ok": bool(final and final.get("ok")),
        "bytes_read": int(final.get("bytes_read", 0) if final else 0),
        "bytes_read_total": sum(int(attempt.get("bytes_read_total", attempt.get("bytes_read", 0)) or 0) for attempt in attempts),
        "duration_seconds": round(sum(float(attempt.get("duration_seconds", 0.0) or 0.0) for attempt in attempts), 3),
        "error_reason": final.get("error_reason") if final else None,
        "error_message": final.get("error_message") if final else None,
    }


def verify_target_row(
    record: Mapping[str, Any],
    *,
    policy: Mapping[str, int],
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    source_path = Path(str(record["source_path"]))
    base = {
        "row_id": int(record["row_id"]),
        "source_safe_label": record["source_safe_label"],
        "bucket": record["bucket"],
        "extension": record["extension"],
        "expected_size": int(record["expected_size"]),
    }
    metadata_before = _metadata_state(source_path, str(record["source_safe_label"]))
    before_state = metadata_before["cloud_state"]
    skip_due_metadata = bool(metadata_before["source_missing"] or metadata_before["source_not_file"])

    if skip_due_metadata:
        prefix = {
            "stage": "prefix",
            "retries": policy["prefix_retries"],
            "wait_seconds": policy["retry_wait_seconds"],
            "attempts": [],
            "ok": False,
            "bytes_read": 0,
            "bytes_read_total": 0,
            "duration_seconds": 0.0,
            "error_reason": "source_missing" if metadata_before["source_missing"] else "source_not_file",
            "error_message": "source metadata blocked content read",
        }
        full = {
            "stage": "full",
            "retries": policy["full_retries"],
            "wait_seconds": policy["retry_wait_seconds"],
            "attempts": [],
            "ok": False,
            "bytes_read": 0,
            "bytes_read_total": 0,
            "duration_seconds": 0.0,
            "error_reason": prefix["error_reason"],
            "error_message": "full read skipped because source metadata blocked content read",
            "skipped": True,
        }
    else:
        prefix = _run_stage_attempts(
            lambda: read_probe_prefix(
                source_path,
                max_bytes=int(policy["prefix_bytes"]),
                timeout_seconds=int(policy["prefix_timeout_seconds"]),
                retries=0,
            ),
            stage="prefix",
            retries=int(policy["prefix_retries"]),
            wait_seconds=int(policy["retry_wait_seconds"]),
            sleeper=sleeper,
        )
        full = _run_stage_attempts(
            lambda: read_verify_full_content(
                source_path,
                expected_size=int(record["expected_size"]),
                timeout_seconds=int(policy["full_timeout_seconds"]),
                retries=0,
                chunk_size=int(policy["full_chunk_size"]),
            ),
            stage="full",
            retries=int(policy["full_retries"]),
            wait_seconds=int(policy["retry_wait_seconds"]),
            sleeper=sleeper,
        )

    metadata_after = _metadata_state(source_path, str(record["source_safe_label"]))
    prefix_reason = _normal_failure_reason(prefix, before_state)
    full_reason = _normal_failure_reason(full, before_state)
    full_ok = bool(full.get("ok"))
    prefix_ok = bool(prefix.get("ok"))
    failure_reason = None if full_ok else (full_reason or prefix_reason or "generic_read_failed")
    public = {
        **base,
        "metadata_before": metadata_before,
        "prefix_read": {
            "ok": prefix_ok,
            "attempted": not skip_due_metadata,
            "attempt_count": len(prefix["attempts"]),
            "bytes_read": int(prefix.get("bytes_read", 0) or 0),
            "bytes_read_total": int(prefix.get("bytes_read_total", 0) or 0),
            "duration_seconds": round(float(prefix.get("duration_seconds", 0.0) or 0.0), 3),
            "error_reason": prefix_reason,
        },
        "full_read": {
            "ok": full_ok,
            "attempted": not skip_due_metadata,
            "attempt_count": len(full["attempts"]),
            "bytes_read": int(full.get("bytes_read", 0) or 0),
            "bytes_read_total": int(full.get("bytes_read_total", 0) or 0),
            "duration_seconds": round(float(full.get("duration_seconds", 0.0) or 0.0), 3),
            "error_reason": full_reason,
            "ran_even_if_prefix_failed": not prefix_ok and not skip_due_metadata,
        },
        "metadata_after": metadata_after,
        "still_recall_on_data_access": bool(metadata_after["cloud_state"].get("recall_on_data_access")),
        "staging_copy_ready": full_ok,
        "ok": full_ok,
        "failure_reason": failure_reason,
        "audit_bytes_read": int(prefix.get("bytes_read_total", 0) or 0) + int(full.get("bytes_read_total", 0) or 0),
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


def summarize_results(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
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
            for item in failures
        ],
    }


def run_targeted_hydration_retry(
    rows: Sequence[dict[str, str]],
    *,
    target_row_ids: Sequence[int],
    policy: Mapping[str, int],
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    selected = selected_rows(rows)
    targets = [target_record(row) for row in select_target_rows(rows, target_row_ids)]
    target_ids_found = [int(record["row_id"]) for record in targets]
    missing_target_ids = [int(row_id) for row_id in target_row_ids if int(row_id) not in target_ids_found]
    results_public: list[dict[str, Any]] = []
    results_local: list[dict[str, Any]] = []
    for record in targets:
        verified = verify_target_row(record, policy=policy, sleeper=sleeper)
        results_public.append(verified["public"])
        results_local.append(verified["local"])
    summary = summarize_results(results_public)
    failed_ids = [int(item["row_id"]) for item in results_public if not item.get("ok")]
    backfill_plan = plan_same_bucket_backfill(rows, failed_ids) if failed_ids else None
    status = "targeted_retry_succeeded"
    if missing_target_ids:
        status = "blocked_missing_target_rows"
    elif summary["failed_count"]:
        status = "targeted_retry_failed"

    report = {
        "phase": "3.8d-I5b",
        "mode": "targeted_hydration_retry",
        "created_at": utc_now(),
        "status": status,
        "success": status == "targeted_retry_succeeded",
        "duration_seconds": round(time.monotonic() - started, 3),
        "manifest_selected_total": len(selected),
        "target_row_ids": [int(row_id) for row_id in target_row_ids],
        "target_row_ids_found": target_ids_found,
        "missing_target_row_ids": missing_target_ids,
        "policy": {
            "prefix_read_bytes": int(policy["prefix_bytes"]),
            "prefix_timeout_seconds": int(policy["prefix_timeout_seconds"]),
            "prefix_retries": int(policy["prefix_retries"]),
            "full_read_timeout_seconds": int(policy["full_timeout_seconds"]),
            "full_read_retries": int(policy["full_retries"]),
            "full_read_chunk_size": int(policy["full_chunk_size"]),
            "retry_wait_seconds": int(policy["retry_wait_seconds"]),
            "full_read_runs_even_if_prefix_times_out": True,
            "full_read_required_for_copy_readiness": True,
            "cfhydrateplaceholder_called": False,
        },
        "summary": summary,
        "target_results": results_public,
        "row_98_result": next((item for item in results_public if int(item["row_id"]) == 98), None),
        "row_881_result": next((item for item in results_public if int(item["row_id"]) == 881), None),
        "backfill_dry_run": backfill_plan,
        "network_proxy_observations": _proxy_observations(),
        "safety": {
            "source_content_read_for_verification_only": bool(results_public),
            "provider_side_hydration_may_have_occurred": bool(results_public),
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
            "manifest_modified": False,
            "backfill_applied": False,
            "app_managed_storage_mutation": False,
            "push_main": False,
            "merge": False,
        },
        "local_artifacts": {
            "details_artifact": DEFAULT_LOCAL_DETAILS.name,
            "full_paths_in_public_reports": False,
            "must_remain_untracked": True,
        },
        "next_step": (
            "Proceed to Phase 3.8d-I5c full recall-risk verification with the I5b retry policy."
            if status == "targeted_retry_succeeded"
            else "Do not apply backfill automatically; user/ChatGPT must decide backfill, provider/network investigation, lower-level hydration API investigation, or another approved policy."
        ),
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
    local_details = {
        "target_results": results_local,
        "manifest_selected_total": len(selected),
    }
    return public, local_details


def render_row_lines(row: Mapping[str, Any] | None, row_id: int) -> list[str]:
    if not row:
        return [f"- Row `{row_id}` was not found in the selected manifest rows."]
    return [
        f"- Safe label: `{row['source_safe_label']}`",
        f"- Bucket: `{row['bucket']}`",
        f"- Extension: `{row['extension']}`",
        f"- Expected size: `{row['expected_size']}`",
        f"- Metadata before likely cloud placeholder: `{row['metadata_before']['cloud_state'].get('likely_cloud_placeholder')}`",
        f"- Metadata before recall_on_data_access: `{row['metadata_before']['cloud_state'].get('recall_on_data_access')}`",
        f"- Prefix read ok: `{row['prefix_read']['ok']}`",
        f"- Prefix attempts: `{row['prefix_read']['attempt_count']}`",
        f"- Full read ok: `{row['full_read']['ok']}`",
        f"- Full read attempts: `{row['full_read']['attempt_count']}`",
        f"- Full read ran even if prefix failed: `{row['full_read']['ran_even_if_prefix_failed']}`",
        f"- Bytes read: `{row['audit_bytes_read']}`",
        f"- Duration seconds: `{row['duration_seconds']}`",
        f"- Failure reason: `{row['failure_reason']}`",
        f"- Metadata after recall_on_data_access: `{row['metadata_after']['cloud_state'].get('recall_on_data_access')}`",
        f"- Still recall_on_data_access: `{row['still_recall_on_data_access']}`",
        f"- Staging-copy-ready: `{row['staging_copy_ready']}`",
    ]


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Phase 3.8d-I5b Targeted Hydration Retry",
        "",
        "## Summary",
        "",
        f"- Status: `{report['status']}`",
        f"- Success: `{report['success']}`",
        f"- Manifest selected total: `{report['manifest_selected_total']}`",
        f"- Target rows: `{json.dumps(report['target_row_ids'])}`",
        f"- Attempted: `{summary['attempted_count']}`",
        f"- Success count: `{summary['success_count']}`",
        f"- Failed count: `{summary['failed_count']}`",
        f"- Bytes read: `{summary['bytes_read']}`",
        f"- Duration seconds: `{summary['duration_seconds']}`",
        f"- Failure reasons: `{json.dumps(summary['failures_by_reason'], sort_keys=True)}`",
        "",
        "## Retry Policy",
        "",
        f"- Prefix read bytes: `{report['policy']['prefix_read_bytes']}`",
        f"- Prefix timeout seconds: `{report['policy']['prefix_timeout_seconds']}`",
        f"- Prefix retries: `{report['policy']['prefix_retries']}`",
        f"- Full read timeout seconds: `{report['policy']['full_read_timeout_seconds']}`",
        f"- Full read retries: `{report['policy']['full_read_retries']}`",
        f"- Retry wait seconds: `{report['policy']['retry_wait_seconds']}`",
        f"- Full read runs even if prefix times out: `{report['policy']['full_read_runs_even_if_prefix_times_out']}`",
        f"- CfHydratePlaceholder called: `{report['policy']['cfhydrateplaceholder_called']}`",
        "",
        "## Row 98 Result",
        "",
        *render_row_lines(report.get("row_98_result"), 98),
        "",
        "## Row 881 Result",
        "",
        *render_row_lines(report.get("row_881_result"), 881),
        "",
        "## Backfill Dry-run",
        "",
    ]
    backfill = report.get("backfill_dry_run")
    if backfill:
        lines.extend(
            [
                f"- Applied: `False`",
                f"- Replacement count: `{backfill['replacement_count']}`",
                f"- Unresolved count: `{backfill['unresolved_count']}`",
                "- Backfill remains dry-run-only and was not applied to the manifest.",
            ]
        )
        for replacement in backfill.get("replacements", []):
            lines.append(
                f"- Failed `{replacement['failed_safe_label']}` -> replacement `{replacement['replacement_safe_label']}` "
                f"in bucket `{replacement['bucket']}`"
            )
    else:
        lines.append("- No failed target rows requiring same-bucket backfill dry-run.")
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
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--local-details-json", type=Path, default=DEFAULT_LOCAL_DETAILS)
    parser.add_argument("--target-row-id", action="append", type=_positive_int, default=[])
    parser.add_argument("--prefix-bytes", type=_positive_int, default=DEFAULT_PREFIX_BYTES)
    parser.add_argument("--prefix-timeout", type=_positive_int, default=DEFAULT_PREFIX_TIMEOUT_SECONDS)
    parser.add_argument("--prefix-retries", type=_non_negative_int, default=DEFAULT_PREFIX_RETRIES)
    parser.add_argument("--full-timeout", type=_positive_int, default=DEFAULT_FULL_TIMEOUT_SECONDS)
    parser.add_argument("--full-retries", type=_non_negative_int, default=DEFAULT_FULL_RETRIES)
    parser.add_argument("--retry-wait", type=_non_negative_int, default=DEFAULT_RETRY_WAIT_SECONDS)
    parser.add_argument("--full-chunk-size", type=_positive_int, default=DEFAULT_FULL_CHUNK_SIZE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.manifest.is_file():
        parser.error(f"manifest not found: {args.manifest}")
    target_row_ids = args.target_row_id or list(DEFAULT_TARGET_ROW_IDS)
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
    report, local_details = run_targeted_hydration_retry(rows, target_row_ids=target_row_ids, policy=policy)
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
            "target_row_ids": target_row_ids,
            "details": local_details,
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
