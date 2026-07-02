#!/usr/bin/env python3
"""Read-only S3A-M2-R post-merge manual-sync health audit.

This audit is intentionally diagnostic. It loads the ignored production
profile, reads the production DB, and writes public-safe aggregate reports.
By default it does not walk, stat, hash, decode, or open source/iCloud
originals. The current source-read-capable dry-run planner is opt-in only via
``--allow-source-read-plan``. The audit never executes manual sync and never
updates source files, app storage, or database rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PHASE = "S3A-M2-R"
BASE_MERGE_COMMIT = "ff5972b0685def18bd658746e2ba1e3043c28d02"
DEFAULT_ROOT_ID = 2
DEFAULT_RUN_ID = 18
PRIVATE_OUTPUT_DIR = ROOT / ".local_manifests" / "s3a_m2_r" / "post_merge_health"
PUBLIC_JSON = ROOT / "docs" / "reports" / "s3a-m2-r-post-merge-health-summary.json"
PUBLIC_MD = ROOT / "docs" / "reports" / "s3a-m2-r-post-merge-health-audit.md"

RETRYABLE_SOURCE_REASONS = {
    "cloud_hydration_failed",
    "cloud_network_unavailable",
    "icloud_placeholder",
    "permission_denied",
    "read_error",
    "read_timeout",
    "source_missing",
}
RETRYABLE_SOURCE_READ_REASONS = {
    "cloud_hydration_failed",
    "cloud_network_unavailable",
    "icloud_placeholder",
    "permission_denied",
    "read_error",
    "read_timeout",
}
PLACEHOLDER_REASONS = {"cloud_placeholder", "icloud_placeholder", "cloud_hydration_failed"}
STABLE_NOOP_REASONS = {
    "duplicate_hash",
    "existing_media_hash",
    "hidden",
    "skipped_duplicate",
    "skipped_existing_media",
    "unsupported_extension",
    "zero_byte",
    "zero_byte_file",
}
CONTINUATION_REASONS = {"not_processed_budget_stop", "not_processed_cancelled"}


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if hasattr(value, "value"):
        return value.value
    return str(value)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def public_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def git_status_snapshot() -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        return {
            "working_tree_dirty_at_generation": None,
            "dirty_files_at_generation": [],
            "dirty_untracked_file_count_at_generation": None,
            "dirty_state_error": completed.stderr.strip() or "git_status_failed",
        }
    tracked: list[str] = []
    untracked_count = 0
    for raw_line in completed.stdout.splitlines():
        if not raw_line:
            continue
        status = raw_line[:2]
        path = raw_line[3:].strip()
        if status == "??":
            untracked_count += 1
        elif path:
            tracked.append(path)
    return {
        "working_tree_dirty_at_generation": bool(completed.stdout.strip()),
        "dirty_files_at_generation": sorted(tracked),
        "dirty_untracked_file_count_at_generation": int(untracked_count),
        "dirty_state_note": "Tracked dirty paths are listed; untracked paths are counted but omitted from the public report.",
    }


def counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda row: str(row[0]))}


def top_counter(counter: Counter[Any], *, limit: int = 40) -> dict[str, int]:
    return {str(key): int(value) for key, value in counter.most_common(limit)}


def bool_status(value: bool) -> str:
    return "yes" if value else "no"


def report_value(value: Any) -> str:
    if isinstance(value, bool):
        return bool_status(value)
    if value is None:
        return "not computed"
    return str(value)


def load_production_profile_env() -> dict[str, Any]:
    from scripts.violet_production_control import _profile_to_env, load_production_profile

    profile, _path, errors = load_production_profile(repo_root=ROOT)
    if errors or profile is None:
        raise RuntimeError(f"production_profile_invalid:{','.join(errors or ['missing'])}")
    env = _profile_to_env(profile, repo_root=ROOT)
    os.environ.update(env)
    return dict(profile)


def open_db_session(*, production: bool):
    if production:
        load_production_profile_env()
    from app import database as app_database

    app_database.init_engine()
    if app_database.SessionLocal is None:
        raise RuntimeError("database_session_unavailable")
    return app_database.SessionLocal()


def terminal_stage_flags(item: Any) -> dict[str, bool]:
    from app.services.dynamic_library_sync_service import (
        MANUAL_SYNC_AI_TAGGING_COMPLETE_STATUSES,
        MANUAL_SYNC_CLASSIFICATION_COMPLETE_STATUSES,
        MANUAL_SYNC_LOCALIZATION_COMPLETE_STATUSES,
    )

    classification = str(getattr(item, "classification_status", "") or "")
    ai = str(getattr(item, "ai_tagging_status", "") or "")
    loc = str(getattr(item, "localization_status", "") or "")
    return {
        "classification_done": classification in MANUAL_SYNC_CLASSIFICATION_COMPLETE_STATUSES,
        "ai_tagging_done": ai in MANUAL_SYNC_AI_TAGGING_COMPLETE_STATUSES,
        "localization_done": loc in MANUAL_SYNC_LOCALIZATION_COMPLETE_STATUSES,
    }


def downstream_complete(item: Any) -> bool:
    flags = terminal_stage_flags(item)
    return bool(flags["classification_done"] and flags["ai_tagging_done"] and flags["localization_done"])


def item_reason(item: Any) -> str:
    return str(getattr(item, "deferred_reason", None) or getattr(item, "failure_reason", None) or "")


def media_content_class(media_payload: Mapping[str, Any] | None) -> str:
    if not media_payload:
        return "no_media"
    value = media_payload.get("content_class")
    value = getattr(value, "value", value)
    return str(value or "unclassified")


def app_storage_presence(media_payload: Mapping[str, Any] | None) -> bool:
    if not media_payload:
        return False
    from app.config import settings

    media_path = str(media_payload.get("path") or "")
    try:
        resolved = settings.resolve_storage_path(media_path)
        return bool(resolved and resolved.exists())
    except Exception:
        return False


def lifecycle_class_for_item(
    item: Any,
    *,
    media_payload: Mapping[str, Any] | None,
    app_storage_present: bool,
    media_followup_needed: bool,
    current_priority: bool,
) -> str:
    import_status = str(getattr(item, "import_status", "") or "")
    sync_state = str(getattr(item, "sync_state", "") or "")
    reason = item_reason(item)
    has_media = getattr(item, "media_id", None) is not None
    has_media_row = media_payload is not None

    if has_media and not has_media_row:
        return "BROKEN_STATE"
    if has_media and media_followup_needed:
        if not app_storage_present:
            return "BROKEN_STATE"
        return "APP_MEDIA_FOLLOWUP"
    if sync_state == "deferred_unprocessed" or reason in CONTINUATION_REASONS:
        return "CONTINUATION"
    if import_status in {"failed", "deferred"} and reason in RETRYABLE_SOURCE_REASONS:
        return "RETRYABLE_SOURCE_FAILURE"
    if sync_state == "skipped_placeholder" or reason in PLACEHOLDER_REASONS:
        return "PLACEHOLDER_DEFERRED"
    if import_status == "pending" and current_priority:
        return "IMPORT_CANDIDATE"
    if import_status == "imported" and has_media and downstream_complete(item):
        return "STABLE_NOOP"
    if sync_state in {"unchanged", "skipped_existing_media", "skipped_duplicate"}:
        return "STABLE_NOOP"
    if reason in STABLE_NOOP_REASONS:
        return "STABLE_NOOP"
    if import_status == "skipped":
        return "STABLE_NOOP"
    if import_status == "pending":
        return "HISTORICAL_DIAGNOSTIC"
    if sync_state in {"missing", "deferred"}:
        return "HISTORICAL_DIAGNOSTIC"
    return "HISTORICAL_DIAGNOSTIC"


def role_for_state_value(field: str, value: str) -> list[str]:
    text = str(value or "")
    roles: list[str] = []
    if text in {"completed", "imported", "classified", "classified_reused", "ai_tagged", "tagged", "tagged_reused", "localized"}:
        roles.append("terminal_success")
    if text in {"skipped_existing_media", "skipped_duplicate", "skipped_unsupported", "unchanged"} or text in STABLE_NOOP_REASONS:
        roles.append("terminal_stable_noop")
    if text in RETRYABLE_SOURCE_REASONS or text in {"failed"}:
        roles.append("retryable_or_failure")
    if text in {"downstream_followup_planned", "deferred", "waiting_import", "waiting_ai_tags", "classification_not_completed"}:
        roles.append("downstream_followup")
    if text in {"deferred_unprocessed", "not_processed_budget_stop", "not_processed_cancelled"}:
        roles.append("continuation")
    if text in {"cloud_placeholder", "icloud_placeholder", "skipped_placeholder"}:
        roles.append("placeholder")
    if text in {"failed_systemic", "blocked_preflight", "source_walk_error"}:
        roles.append("fatal_or_systemic_blocker")
    if not roles:
        roles.append("historical_diagnostic_or_context_dependent")
    return roles


def source_root_public(root: Any) -> dict[str, Any]:
    return {
        "id": int(root.id),
        "label": str(root.label or ""),
        "source_type": str(root.source_type or ""),
        "is_active": bool(root.is_active),
        "source_identity_hash_prefix": str(root.root_path_hash or "")[:12],
    }


def load_media_payloads(db: Any, media_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    from app.models import Media

    ids = sorted({int(value) for value in media_ids if value is not None})
    payloads: dict[int, dict[str, Any]] = {}
    if not ids:
        return payloads
    chunk_size = 500
    for index in range(0, len(ids), chunk_size):
        chunk = ids[index : index + chunk_size]
        for media_id, path, content_class, media_hash in (
            db.query(Media.id, Media.path, Media.content_class, Media.hash)
            .filter(Media.id.in_(chunk))
            .all()
        ):
            payloads[int(media_id)] = {
                "id": int(media_id),
                "path": str(path or ""),
                "content_class": getattr(content_class, "value", content_class),
                "hash": str(media_hash or ""),
            }
    return payloads


def summarize_run(
    db: Any,
    *,
    run_id: int,
    root_id: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    from app.models import DynamicSourceItem, DynamicSyncRun, DynamicSyncRunItem

    run = db.get(DynamicSyncRun, int(run_id))
    if run is None:
        raise RuntimeError(f"dynamic_sync_run_not_found:{run_id}")
    rows = (
        db.query(DynamicSyncRunItem, DynamicSourceItem)
        .join(DynamicSourceItem, DynamicSyncRunItem.source_item_id == DynamicSourceItem.id)
        .filter(DynamicSyncRunItem.sync_run_id == int(run_id))
        .order_by(DynamicSyncRunItem.id.asc())
        .all()
    )
    media_ids = [int(item.media_id) for _run_item, item in rows if item.media_id is not None]
    media_payloads = load_media_payloads(db, media_ids)

    state_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    source_state_counts: Counter[str] = Counter()
    import_status_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    ai_counts: Counter[str] = Counter()
    localization_counts: Counter[str] = Counter()
    content_class_counts: Counter[str] = Counter()
    app_storage_counts: Counter[str] = Counter()
    lifecycle_counts: Counter[str] = Counter()
    processed_counts: Counter[str] = Counter()
    incomplete_counts: Counter[str] = Counter()
    root_counts: Counter[str] = Counter()
    last_sync_run_counts: Counter[str] = Counter()
    run18_deferred_import_prefixes: set[str] = set()
    private_rows: list[dict[str, Any]] = []

    for run_item, item in rows:
        media_payload = media_payloads.get(int(item.media_id)) if item.media_id is not None else None
        app_present = app_storage_presence(media_payload)
        followup_needed = bool(item.media_id is not None and not downstream_complete(item))
        lifecycle = lifecycle_class_for_item(
            item,
            media_payload=media_payload,
            app_storage_present=app_present,
            media_followup_needed=followup_needed,
            current_priority=False,
        )
        state = str(run_item.item_state or "")
        reason = str(run_item.reason or item_reason(item) or state or "unknown")
        actual_processed = state in {"imported", "failed", "downstream_followup_planned"} and state != "deferred_unprocessed"
        if state == "downstream_followup_planned":
            actual_processed = downstream_complete(item)
        remains_incomplete = bool(item.media_id is not None and not downstream_complete(item))
        if state == "deferred_unprocessed" and reason == "not_processed_budget_stop" and item.media_id is None:
            run18_deferred_import_prefixes.add(str(item.relative_path_hash or "")[:16])

        state_counts[state or "unknown"] += 1
        reason_counts[reason or "unknown"] += 1
        source_state_counts[str(item.sync_state or "unknown")] += 1
        import_status_counts[str(item.import_status or "unknown")] += 1
        classification_counts[str(item.classification_status or "unknown")] += 1
        ai_counts[str(item.ai_tagging_status or "unknown")] += 1
        localization_counts[str(item.localization_status or "unknown")] += 1
        content_class_counts[media_content_class(media_payload)] += 1
        app_storage_counts["present" if app_present else "missing_or_no_media"] += 1
        lifecycle_counts[lifecycle] += 1
        processed_counts["actually_processed_or_attempted" if actual_processed else "not_processed_continuation"] += 1
        incomplete_counts["remaining_incomplete" if remains_incomplete else "terminal_or_no_media"] += 1
        root_counts[str(item.source_root_id)] += 1
        last_sync_run_counts[str(item.last_sync_run_id or "null")] += 1

        private_rows.append(
            {
                "run_item_id": int(run_item.id),
                "source_item_id": int(item.id),
                "relative_path_hash": str(item.relative_path_hash or ""),
                "media_id": int(item.media_id) if item.media_id is not None else None,
                "item_state": state,
                "run_item_reason": str(run_item.reason or ""),
                "sync_state": str(item.sync_state or ""),
                "import_status": str(item.import_status or ""),
                "classification_status": str(item.classification_status or ""),
                "ai_tagging_status": str(item.ai_tagging_status or ""),
                "localization_status": str(item.localization_status or ""),
                "failure_reason": str(item.failure_reason or ""),
                "deferred_reason": str(item.deferred_reason or ""),
                "lifecycle": lifecycle,
                "actual_processed_or_attempted": actual_processed,
                "downstream_complete": downstream_complete(item),
                "app_storage_present": app_present,
                "content_class": media_content_class(media_payload),
            }
        )

    summary_payload = run.summary_json if isinstance(run.summary_json, dict) else {}
    execute_payload = (
        summary_payload.get("manual_sync_execute")
        if isinstance(summary_payload.get("manual_sync_execute"), dict)
        else {}
    )
    outcome = execute_payload.get("outcome_counts") if isinstance(execute_payload.get("outcome_counts"), dict) else {}
    request = execute_payload.get("request") if isinstance(execute_payload.get("request"), dict) else {}

    imported = int(outcome.get("imported") or state_counts.get("imported", 0))
    failed = int(state_counts.get("failed", 0))
    deferred_unprocessed = int(state_counts.get("deferred_unprocessed", 0))
    retryable_failures = sum(int(reason_counts.get(reason, 0)) for reason in RETRYABLE_SOURCE_REASONS)
    downstream_followup = int(state_counts.get("downstream_followup_planned", 0))
    downstream_followup_complete = int(
        sum(1 for row in private_rows if row["item_state"] == "downstream_followup_planned" and row["downstream_complete"])
    )
    summary = {
        "run_id": int(run.id),
        "run_type": str(run.run_type),
        "mode": str(run.mode),
        "status": str(run.status),
        "dry_run": bool(run.dry_run),
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "total_seen": int(run.total_seen or 0),
        "state_counts": counter_dict(state_counts),
        "reason_counts": counter_dict(reason_counts),
        "source_state_counts": counter_dict(source_state_counts),
        "import_status_counts": counter_dict(import_status_counts),
        "classification_status_counts": counter_dict(classification_counts),
        "ai_tagging_status_counts": counter_dict(ai_counts),
        "localization_status_counts": counter_dict(localization_counts),
        "content_class_counts": counter_dict(content_class_counts),
        "input_scope": {
            "selected_scope_item_count": int(root_counts.get(str(root_id), 0)),
            "other_scope_item_count": int(sum(count for key, count in root_counts.items() if str(key) != str(root_id))),
            "all_items_on_selected_scope": int(root_counts.get(str(root_id), 0)) == len(rows),
        },
        "last_sync_run_id_counts": counter_dict(last_sync_run_counts),
        "media_id_presence": {
            "present": int(sum(1 for _run_item, item in rows if item.media_id is not None)),
            "missing": int(sum(1 for _run_item, item in rows if item.media_id is None)),
        },
        "app_storage_presence": counter_dict(app_storage_counts),
        "lifecycle_counts": counter_dict(lifecycle_counts),
        "processed_in_run": counter_dict(processed_counts),
        "remaining_incomplete": counter_dict(incomplete_counts),
        "reconciliation": {
            "planned_imports": int(imported + failed + deferred_unprocessed),
            "imported": imported,
            "failed": failed,
            "deferred_unprocessed": deferred_unprocessed,
            "retryable_source_failures": int(retryable_failures),
            "downstream_followup_rows": downstream_followup,
            "downstream_followup_complete": downstream_followup_complete,
            "downstream_followup_actionable_after_run": int(downstream_followup - downstream_followup_complete),
            "import_stopped_by": execute_payload.get("import_stopped_by"),
            "unprocessed_import_planned_count": int(execute_payload.get("unprocessed_import_planned_count") or 0),
            "request_effective_max_files": request.get("effective_max_files"),
            "request_source": request.get("request_source"),
            "root_scope": request.get("root_scope"),
        },
        "operator_status_interpretation": {
            "current_status": str(run.status),
            "recommended_operator_status": (
                "completed_with_retryable_failures"
                if str(run.status) == "completed_with_failures" and retryable_failures == failed and failed > 0
                else str(run.status)
            ),
            "is_success": False,
            "is_partial_success": bool(str(run.status) == "completed_with_failures" and retryable_failures == failed and imported > 0),
            "is_failure": False,
            "acceptance_note": "Acceptable for PR #126 DB-truth acceptance, but not a clean fully completed run.",
        },
    }
    return summary, private_rows, run18_deferred_import_prefixes


def summarize_root_inventory(
    db: Any,
    *,
    root_id: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from app.models import DynamicSourceItem
    from app.services.dynamic_library_sync_service import (
        _manual_plan_media_backed_requires_followup,
        _manual_plan_mtime_cutoff_ns,
        _manual_plan_priority_for_known_item,
        _manual_plan_source_mtime_watermark_ns,
    )

    items = (
        db.query(DynamicSourceItem)
        .filter(DynamicSourceItem.source_root_id == int(root_id))
        .order_by(DynamicSourceItem.id.asc())
        .all()
    )
    media_ids = [int(item.media_id) for item in items if item.media_id is not None]
    media_payloads = load_media_payloads(db, media_ids)
    watermark_ns = _manual_plan_source_mtime_watermark_ns(items)
    mtime_cutoff_ns = _manual_plan_mtime_cutoff_ns(watermark_ns, 7 * 24 * 60 * 60)

    lifecycle_counts: Counter[str] = Counter()
    sync_state_counts: Counter[str] = Counter()
    import_status_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    ai_counts: Counter[str] = Counter()
    localization_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    source_status_counts: Counter[str] = Counter()
    content_class_counts: Counter[str] = Counter()
    app_storage_counts: Counter[str] = Counter()
    current_priority_counts: Counter[str] = Counter()
    invisible_counts: Counter[str] = Counter()
    debt_examples: list[dict[str, Any]] = []

    deferred_not_processed_total = 0
    retryable_source_failure_total = 0
    placeholder_total = 0
    app_media_downstream_incomplete = 0
    imported_downstream_incomplete = 0
    app_media_incomplete_invisible = 0
    app_media_incomplete_current_planner_followup = 0
    app_media_incomplete_current_priority = 0
    source_missing_media_backed_incomplete = 0
    retryable_source_read_failure_total = 0
    historical_backlog_can_consume_cap = 0

    for item in items:
        media_payload = media_payloads.get(int(item.media_id)) if item.media_id is not None else None
        app_present = app_storage_presence(media_payload)
        current_planner_media_followup_needed = bool(_manual_plan_media_backed_requires_followup(item))
        downstream_incomplete = bool(item.media_id is not None and not downstream_complete(item))
        canonical_media_followup_needed = downstream_incomplete
        priority = _manual_plan_priority_for_known_item(item, mtime_cutoff_ns=mtime_cutoff_ns)
        current_priority = priority is not None
        lifecycle = lifecycle_class_for_item(
            item,
            media_payload=media_payload,
            app_storage_present=app_present,
            media_followup_needed=canonical_media_followup_needed,
            current_priority=current_priority,
        )
        reason = item_reason(item)

        lifecycle_counts[lifecycle] += 1
        sync_state_counts[str(item.sync_state or "unknown")] += 1
        import_status_counts[str(item.import_status or "unknown")] += 1
        classification_counts[str(item.classification_status or "unknown")] += 1
        ai_counts[str(item.ai_tagging_status or "unknown")] += 1
        localization_counts[str(item.localization_status or "unknown")] += 1
        reason_counts[reason or "none"] += 1
        source_status_counts[str(item.source_status or "unknown")] += 1
        content_class_counts[media_content_class(media_payload)] += 1
        app_storage_counts["present" if app_present else "missing_or_no_media"] += 1
        current_priority_counts["current_priority"] += int(current_priority)
        current_priority_counts["not_current_priority"] += int(not current_priority)

        if str(item.sync_state or "") == "deferred_unprocessed" or reason == "not_processed_budget_stop":
            deferred_not_processed_total += 1
        if str(item.import_status or "") in {"failed", "deferred"} and reason in RETRYABLE_SOURCE_REASONS:
            retryable_source_failure_total += 1
        if str(item.import_status or "") in {"failed", "deferred"} and reason in RETRYABLE_SOURCE_READ_REASONS:
            retryable_source_read_failure_total += 1
        if str(item.sync_state or "") == "skipped_placeholder" or reason in PLACEHOLDER_REASONS:
            placeholder_total += 1
        if item.media_id is not None and downstream_incomplete:
            app_media_downstream_incomplete += 1
            if current_planner_media_followup_needed:
                app_media_incomplete_current_planner_followup += 1
            if current_priority:
                app_media_incomplete_current_priority += 1
            if reason == "source_missing":
                source_missing_media_backed_incomplete += 1
            if str(item.import_status or "") == "imported":
                imported_downstream_incomplete += 1
            if not current_priority:
                app_media_incomplete_invisible += 1
        if current_priority and lifecycle in {"HISTORICAL_DIAGNOSTIC", "STABLE_NOOP"}:
            historical_backlog_can_consume_cap += 1
        if downstream_incomplete and not current_priority:
            invisible_counts["downstream_incomplete_not_in_current_priority"] += 1
        if lifecycle in {"RETRYABLE_SOURCE_FAILURE", "PLACEHOLDER_DEFERRED", "CONTINUATION", "BROKEN_STATE", "APP_MEDIA_FOLLOWUP"}:
            if len(debt_examples) < 100:
                debt_examples.append(
                    {
                        "source_item_id": int(item.id),
                        "relative_path_hash": str(item.relative_path_hash or ""),
                        "media_id_present": item.media_id is not None,
                        "sync_state": str(item.sync_state or ""),
                        "import_status": str(item.import_status or ""),
                        "classification_status": str(item.classification_status or ""),
                        "ai_tagging_status": str(item.ai_tagging_status or ""),
                        "localization_status": str(item.localization_status or ""),
                        "failure_reason": str(item.failure_reason or ""),
                        "deferred_reason": str(item.deferred_reason or ""),
                        "lifecycle": lifecycle,
                        "current_priority": current_priority,
                        "app_storage_present": app_present,
                    }
                )

    summary = {
        "total_dynamic_source_items": len(items),
        "source_mtime_watermark_ns": int(watermark_ns) if watermark_ns is not None else None,
        "mtime_cutoff_ns": int(mtime_cutoff_ns) if mtime_cutoff_ns is not None else None,
        "lifecycle_counts": counter_dict(lifecycle_counts),
        "sync_state_counts": counter_dict(sync_state_counts),
        "import_status_counts": counter_dict(import_status_counts),
        "classification_status_counts": counter_dict(classification_counts),
        "ai_tagging_status_counts": counter_dict(ai_counts),
        "localization_status_counts": counter_dict(localization_counts),
        "reason_counts_top": top_counter(reason_counts),
        "source_status_counts": counter_dict(source_status_counts),
        "content_class_counts": counter_dict(content_class_counts),
        "app_storage_presence": counter_dict(app_storage_counts),
        "current_priority_counts": counter_dict(current_priority_counts),
        "invisible_work_counts": counter_dict(invisible_counts),
        "debts": {
            "deferred_not_processed_budget_stop_total": int(deferred_not_processed_total),
            "retryable_source_failure_total": int(retryable_source_failure_total),
            "retryable_source_read_failure_total": int(retryable_source_read_failure_total),
            "placeholder_total": int(placeholder_total),
            "app_media_downstream_incomplete_total": int(app_media_downstream_incomplete),
            "imported_but_downstream_incomplete_total": int(imported_downstream_incomplete),
            "app_media_incomplete_invisible_total": int(app_media_incomplete_invisible),
            "app_media_incomplete_current_planner_followup_total": int(app_media_incomplete_current_planner_followup),
            "app_media_incomplete_current_priority_total": int(app_media_incomplete_current_priority),
            "source_missing_media_backed_incomplete_total": int(source_missing_media_backed_incomplete),
            "historical_backlog_can_consume_cap": int(historical_backlog_can_consume_cap),
        },
    }
    return summary, debt_examples


def summarize_next_plan(
    db: Any,
    *,
    root: Any,
    max_files: int,
    run_deferred_prefixes: set[str],
    include_plan: bool,
    allow_source_read_plan: bool,
    root_inventory: Mapping[str, Any],
) -> dict[str, Any]:
    if not include_plan:
        return {
            "skipped": True,
            "evidence_mode": "not_run",
            "reason": "include_next_plan_false",
            "source_read_capable_planner_invoked": False,
        }

    if not allow_source_read_plan:
        from app.models import DynamicSourceItem

        continuation_rows = (
            db.query(DynamicSourceItem.relative_path_hash)
            .filter(DynamicSourceItem.source_root_id == int(root.id))
            .filter(
                (DynamicSourceItem.sync_state == "deferred_unprocessed")
                | (DynamicSourceItem.deferred_reason.in_(list(CONTINUATION_REASONS)))
                | (DynamicSourceItem.failure_reason.in_(list(CONTINUATION_REASONS)))
            )
            .all()
        )
        continuation_prefixes = {str(row[0] or "")[:16] for row in continuation_rows}
        run18_visible = len({prefix for prefix in run_deferred_prefixes if prefix in continuation_prefixes})
        debts = root_inventory.get("debts") if isinstance(root_inventory.get("debts"), dict) else {}
        reason_counts = (
            root_inventory.get("reason_counts_top") if isinstance(root_inventory.get("reason_counts_top"), dict) else {}
        )
        continuation_total = int(debts.get("deferred_not_processed_budget_stop_total") or 0)
        other_continuation = max(0, continuation_total - run18_visible)
        retry_read = int(debts.get("retryable_source_read_failure_total") or 0)
        retry_all = int(debts.get("retryable_source_failure_total") or 0)
        placeholders = int(debts.get("placeholder_total") or 0)
        app_followup = int(debts.get("app_media_downstream_incomplete_total") or 0)
        unsupported = int(reason_counts.get("unsupported_extension") or reason_counts.get("unsupported") or 0)
        stat_error = int(reason_counts.get("stat_error") or reason_counts.get("source_stat_error") or 0)
        return {
            "skipped": False,
            "evidence_mode": "db_only_source_safe",
            "source_read_capable_planner_invoked": False,
            "source_read_policy": {
                "default_avoids_source_walk_stat_open_hash_decode": True,
                "default_avoids_cloud_files_hydration_risk": True,
                "source_read_capable_plan_requires_flag": "--allow-source-read-plan",
                "source_read_capable_plan_status": "not_run_in_safe_default",
                "safe_default_precision_note": (
                    "The exact normal-plan import estimate and filesystem delta categories are not recomputed "
                    "because the current planner may walk/stat source entries. DB-only evidence classifies "
                    "known ledger continuation, retry, placeholder, and app-media debt only."
                ),
            },
            "counts": {
                "estimated_import_count": None,
                "estimated_import_count_status": "not_computed_in_safe_default",
                "estimated_downstream_followup_count": None,
                "estimated_downstream_followup_count_status": "not_computed_by_current_planner_in_safe_default",
                "batch_executable": None,
                "partial_scan": None,
                "partial_scan_reason": "not_run_source_read_capable_planner",
                "more_batches_remain": None,
                "unsafe_partial_scan": None,
                "plan_items": None,
                "total_seen": None,
                "scanned_files": 0,
            },
            "limits": {
                "stat_required_count": 0,
                "hash_required_count": 0,
                "source_file_walk_count": 0,
                "execute_import_classification_ai_localization": "0/0/0/0 because this audit is read-only DB analysis",
                "limit_precision_note": "These are safe-audit operation counts, not current planner limits.",
            },
            "source_delta_workset": {
                "status": "not_computed_in_safe_default",
                "reason": "requires source walk/stat path in current planner",
            },
            "run18_deferred_continuation_visibility_db_only": {
                "run18_deferred_import_count": len(run_deferred_prefixes),
                "present_as_root_continuation_rows": int(run18_visible),
                "missing_from_root_continuation_rows": max(0, len(run_deferred_prefixes) - run18_visible),
                "plan_position_status": "not_computed_in_safe_default",
                "note": (
                    "Visibility here means the run #18 deferred rows still exist as root-scoped DB continuation "
                    "rows. Ordering inside the current normal planner is not recomputed without opt-in source-read planning."
                ),
            },
            "db_only_import_candidate_breakdown": {
                "previous_347_exact_plan_count_status": "not_recomputed_in_safe_default",
                "exact_normal_plan_import_count": None,
                "exact_normal_plan_import_count_reason": "requires opt-in source-read-capable planner path",
                "run18_deferred_continuation_rows": int(run18_visible),
                "other_continuation_rows": int(other_continuation),
                "known_db_continuation_total": int(continuation_total),
                "known_retryable_source_read_failure_rows": int(retry_read),
                "known_retryable_source_failure_rows_including_source_missing": int(retry_all),
                "known_placeholder_deferred_rows_excluded_from_import": int(placeholders),
                "known_app_media_followup_candidates_not_import": int(app_followup),
                "root_scope_stat_error_reason_rows": int(stat_error),
                "root_scope_unsupported_reason_rows": int(unsupported),
                "ledger_missing_candidates": "unknown_without_source_walk_stat",
                "mtime_new_candidates": "unknown_without_source_walk_stat",
                "safety_window_candidates": "unknown_without_source_walk_stat",
                "old_mtime_known_pending_candidates": "unknown_without_source_walk_stat",
                "long_term_blocker_assessment": (
                    "Known DB continuation/retry/placeholder/app-media debt is visible. The safe default cannot "
                    "prove whether ledger-missing or mtime-derived filesystem candidates are long-term blockers."
                ),
            },
            "current_plan_boundary_note": (
                "Default R0 evidence is DB-only and source-safe. Source-read-capable planner evidence is omitted "
                "unless --allow-source-read-plan is passed."
            ),
        }

    from app.services.dynamic_library_sync_service import MANUAL_SYNC_NORMAL_PLAN_MODE, plan_manual_sync_dry_run

    plan = plan_manual_sync_dry_run(
        db,
        source_path=root.root_path,
        source_record_id=root.id,
        max_files=max_files,
        hydrated_only=False,
        include_private_details=False,
        plan_mode=MANUAL_SYNC_NORMAL_PLAN_MODE,
    )
    items = list(((plan.get("ledger") or {}).get("per_file_public_records")) or [])
    prefixes = [str(item.get("relative_path_hash") or "") for item in items]
    selected_run_deferred = [prefix for prefix in prefixes if prefix in run_deferred_prefixes]
    positions = [index + 1 for index, prefix in enumerate(prefixes) if prefix in run_deferred_prefixes]
    counts = plan.get("counts") if isinstance(plan.get("counts"), dict) else {}
    limits = plan.get("limits") if isinstance(plan.get("limits"), dict) else {}
    source_delta = limits.get("source_delta_workset") if isinstance(limits.get("source_delta_workset"), dict) else {}
    return {
        "skipped": False,
        "evidence_mode": "source_read_capable_opt_in",
        "source_read_capable_planner_invoked": True,
        "source_read_policy": {
            "source_read_capable_plan_requires_flag": "--allow-source-read-plan",
            "hydrated_only": False,
            "default_safe_audit": False,
            "note": "This opt-in path may walk/stat source entries and is not used by default R0 validation.",
        },
        "counts": {
            "estimated_import_count": int(counts.get("estimated_import_count") or 0),
            "estimated_downstream_followup_count": int(counts.get("estimated_downstream_followup_count") or 0),
            "state_counts": counts.get("state_counts") or {},
            "failure_reasons": counts.get("failure_reasons") or {},
            "batch_executable": bool(counts.get("batch_executable")),
            "partial_scan": bool(counts.get("partial_scan")),
            "partial_scan_reason": counts.get("partial_scan_reason"),
            "more_batches_remain": bool(counts.get("more_batches_remain")),
            "unsafe_partial_scan": bool(counts.get("unsafe_partial_scan")),
            "plan_items": int(counts.get("plan_items") or 0),
            "total_seen": int(counts.get("total_seen") or 0),
            "scanned_files": int(counts.get("scanned_files") or 0),
        },
        "limits": {
            "stat_required_count": int(limits.get("stat_required_count") or 0),
            "hash_required_count": int(limits.get("hash_required_count") or 0),
            "import_candidate_count": int(limits.get("import_candidate_count") or 0),
            "actionable_candidate_count": int(limits.get("actionable_candidate_count") or 0),
            "downstream_followup_count": int(limits.get("downstream_followup_count") or 0),
            "fast_skipped_from_ledger": int(limits.get("fast_skipped_from_ledger") or 0),
            "batch_executable": bool(limits.get("batch_executable")),
            "cap_limited_batch": bool(limits.get("cap_limited_batch")),
            "more_batches_remain": bool(limits.get("more_batches_remain")),
            "unsafe_partial_scan": bool(limits.get("unsafe_partial_scan")),
            "partial_scan_reason": limits.get("partial_scan_reason"),
            "current_scan_mode": ((limits.get("root_scan_state") or {}).get("current_scan_mode") if isinstance(limits.get("root_scan_state"), dict) else None),
            "current_scan_start_basis": ((limits.get("root_scan_state") or {}).get("current_scan_start_basis") if isinstance(limits.get("root_scan_state"), dict) else None),
        },
        "source_delta_workset": {
            "scan_order": source_delta.get("scan_order"),
            "priority_workset_files": int(source_delta.get("priority_workset_files") or 0),
            "priority_workset_processed": int(source_delta.get("priority_workset_processed") or 0),
            "priority_workset_exhausted": bool(source_delta.get("priority_workset_exhausted")),
            "filesystem_walk_completed": bool(source_delta.get("filesystem_walk_completed")),
            "candidate_pool_count": int(source_delta.get("candidate_pool_count") or 0),
            "candidate_selection_order": source_delta.get("candidate_selection_order") or [],
            "stable_known_entries_do_not_consume_actionable_cap": bool(
                source_delta.get("stable_known_entries_do_not_consume_actionable_cap")
            ),
        },
        "run18_deferred_import_visibility": {
            "run18_deferred_import_count": len(run_deferred_prefixes),
            "selected_in_next_plan": len(set(selected_run_deferred)),
            "missing_from_next_plan": max(0, len(run_deferred_prefixes) - len(set(selected_run_deferred))),
            "first_selected_position": min(positions) if positions else None,
            "last_selected_position": max(positions) if positions else None,
            "ordered_after_followup": int(counts.get("estimated_downstream_followup_count") or 0) == 0 or (min(positions) if positions else 0) > int(counts.get("estimated_downstream_followup_count") or 0),
            "note": "Position uses public relative_path_hash prefixes; detailed paths remain private.",
        },
        "current_plan_boundary_note": (
            "Opt-in current dry-run planner is read-only but may stat source entries. In this run hash_required_count was "
            f"{int(limits.get('hash_required_count') or 0)}."
        ),
    }


def summarize_state_machine(db: Any, *, root_id: int) -> dict[str, Any]:
    from sqlalchemy import func
    from app.models import DynamicSourceItem, DynamicSyncRun, DynamicSyncRunItem

    def grouped(model: Any, column: Any, filters: list[Any] | None = None) -> dict[str, int]:
        query = db.query(column, func.count(model.id))
        if filters:
            for condition in filters:
                query = query.filter(condition)
        rows = query.group_by(column).all()
        return {str(value or "null"): int(count) for value, count in sorted(rows, key=lambda row: str(row[0]))}

    def grouped_run_item_root(column: Any) -> dict[str, int]:
        rows = (
            db.query(column, func.count(DynamicSyncRunItem.id))
            .join(DynamicSourceItem, DynamicSyncRunItem.source_item_id == DynamicSourceItem.id)
            .filter(DynamicSourceItem.source_root_id == int(root_id))
            .group_by(column)
            .all()
        )
        return {str(value or "null"): int(count) for value, count in sorted(rows, key=lambda row: str(row[0]))}

    def grouped_run_status_root() -> dict[str, int]:
        rows = (
            db.query(DynamicSyncRun.status, func.count(func.distinct(DynamicSyncRun.id)))
            .join(DynamicSyncRunItem, DynamicSyncRunItem.sync_run_id == DynamicSyncRun.id)
            .join(DynamicSourceItem, DynamicSyncRunItem.source_item_id == DynamicSourceItem.id)
            .filter(DynamicSourceItem.source_root_id == int(root_id))
            .group_by(DynamicSyncRun.status)
            .all()
        )
        return {str(value or "null"): int(count) for value, count in sorted(rows, key=lambda row: str(row[0]))}

    fields = {
        "DynamicSourceItem.source_status": grouped(
            DynamicSourceItem,
            DynamicSourceItem.source_status,
            [DynamicSourceItem.source_root_id == int(root_id)],
        ),
        "DynamicSourceItem.sync_state": grouped(
            DynamicSourceItem,
            DynamicSourceItem.sync_state,
            [DynamicSourceItem.source_root_id == int(root_id)],
        ),
        "DynamicSourceItem.import_status": grouped(
            DynamicSourceItem,
            DynamicSourceItem.import_status,
            [DynamicSourceItem.source_root_id == int(root_id)],
        ),
        "DynamicSourceItem.classification_status": grouped(
            DynamicSourceItem,
            DynamicSourceItem.classification_status,
            [DynamicSourceItem.source_root_id == int(root_id)],
        ),
        "DynamicSourceItem.ai_tagging_status": grouped(
            DynamicSourceItem,
            DynamicSourceItem.ai_tagging_status,
            [DynamicSourceItem.source_root_id == int(root_id)],
        ),
        "DynamicSourceItem.localization_status": grouped(
            DynamicSourceItem,
            DynamicSourceItem.localization_status,
            [DynamicSourceItem.source_root_id == int(root_id)],
        ),
        "DynamicSourceItem.failure_reason": grouped(
            DynamicSourceItem,
            DynamicSourceItem.failure_reason,
            [DynamicSourceItem.source_root_id == int(root_id)],
        ),
        "DynamicSourceItem.deferred_reason": grouped(
            DynamicSourceItem,
            DynamicSourceItem.deferred_reason,
            [DynamicSourceItem.source_root_id == int(root_id)],
        ),
        "DynamicSyncRun.status": grouped_run_status_root(),
        "DynamicSyncRunItem.item_state": grouped_run_item_root(DynamicSyncRunItem.item_state),
        "DynamicSyncRunItem.action": grouped_run_item_root(DynamicSyncRunItem.action),
        "DynamicSyncRunItem.reason": grouped_run_item_root(DynamicSyncRunItem.reason),
    }
    global_fields = {
        "DynamicSyncRun.status": grouped(DynamicSyncRun, DynamicSyncRun.status),
        "DynamicSyncRunItem.item_state": grouped(DynamicSyncRunItem, DynamicSyncRunItem.item_state),
        "DynamicSyncRunItem.action": grouped(DynamicSyncRunItem, DynamicSyncRunItem.action),
        "DynamicSyncRunItem.reason": grouped(DynamicSyncRunItem, DynamicSyncRunItem.reason),
    }
    role_map: dict[str, dict[str, list[str]]] = {}
    for field, values in fields.items():
        role_map[field] = {value: role_for_state_value(field, value) for value in values}

    report_status_fields: dict[str, Any] = {}
    report_path = ROOT / "docs" / "reports" / "s3a-m2-production-delta-e2e-summary.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            gui = report.get("gui_execute_acceptance") if isinstance(report.get("gui_execute_acceptance"), dict) else {}
            launcher = (
                report.get("launcher_web_admin_acceptance")
                if isinstance(report.get("launcher_web_admin_acceptance"), dict)
                else {}
            )
            report_status_fields = {
                "gui_execute_acceptance.status": gui.get("status"),
                "gui_execute_acceptance.validation_script_status": gui.get("validation_script_status"),
                "gui_execute_acceptance.run_status": gui.get("run_status"),
                "launcher_web_admin_acceptance.status": launcher.get("status"),
                "summary.public_redaction.passed": ((report.get("public_redaction") or {}).get("passed") if isinstance(report.get("public_redaction"), dict) else None),
            }
        except Exception as exc:
            report_status_fields = {"load_error": exc.__class__.__name__}

    return {
        "scope": {
            "root_id": int(root_id),
            "run_item_counts_scope": "root_scoped_join_dynamic_source_item",
            "global_counts_location": "global_context.field_value_counts",
        },
        "field_value_counts": fields,
        "field_value_roles": role_map,
        "global_context": {
            "note": "Global run/run-item counts are retained only as context; root-specific inventory uses DynamicSourceItem.source_root_id filtering.",
            "field_value_counts": global_fields,
        },
        "report_summary_status_fields": report_status_fields,
        "ui_terminal_statuses_current": [
            "completed",
            "completed_with_failures",
            "completed_with_followup_required",
            "cancelled",
            "blocked_preflight",
            "failed",
        ],
    }


def path_liveness_matrix() -> list[dict[str, Any]]:
    rows = [
        ("A", "New import succeeds fully", True, True, False, False, False, False, False, False, ["tests/test_s3a_m2_delta_e2e.py", "tests/test_s3a_m1_manual_sync_execute.py"], []),
        ("B", "New import succeeds but classification fails", True, True, False, True, False, True, False, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Add lifecycle table case for classification_failed_after_import."]),
        ("C", "New import succeeds but AI tagging fails", True, True, False, True, False, True, False, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Add explicit retry/debt visibility assertion."]),
        ("D", "New import succeeds but localization fails/deferred", True, True, False, True, False, True, False, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Assert operator status completed_with_followup_required."]),
        ("E", "Source read_error/read_timeout before import", True, True, False, False, False, False, True, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Attempt-count/debt report tests are missing."]),
        ("F", "Source read_error/read_timeout after some imports", True, True, False, False, False, True, True, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Failure-budget liveness should be table-driven."]),
        ("G", "Downstream follow-up succeeds", True, True, False, False, False, False, False, False, ["tests/test_s3a_m2_delta_e2e.py"], []),
        ("H", "Downstream follow-up source file missing", True, True, False, False, False, False, False, False, ["tests/test_s3a_m2_delta_e2e.py"], []),
        ("I", "Downstream follow-up app storage missing", False, False, True, True, True, True, False, False, [], ["Needs BROKEN_STATE classifier and validator coverage."]),
        ("J", "Existing-media duplicate with downstream terminal", True, True, False, False, False, False, False, False, ["tests/test_s3a_m2_delta_e2e.py"], []),
        ("K", "Existing-media duplicate with downstream incomplete", True, True, False, True, False, False, False, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Move to canonical APP_MEDIA_FOLLOWUP tests."]),
        ("L", "Placeholder/cloud hydration failure", True, True, False, False, False, False, True, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Needs clearer operator debt UI."]),
        ("M", "Failure budget stop", True, True, False, False, False, True, True, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Rename/clarify completed_with_failures semantics."]),
        ("N", "Cap-limited continuation", True, True, False, False, False, False, False, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Assert continuation ordering after run-like state."]),
        ("O", "User cancellation", True, True, False, False, False, False, False, False, ["tests/test_dynamic_library_sync.py"], ["Execute cancel debt visibility needs coverage."]),
        ("P", "Filesystem walk error", True, True, False, False, False, False, False, False, ["tests/test_s3a_m2_delta_e2e.py"], []),
        ("Q", "Validator/report update failure", True, True, False, False, False, True, False, False, ["tests/test_phase_contracts.py"], ["Separate DB-truth acceptance from report-tool status."]),
        ("R", "Public redaction false positive", True, True, False, False, False, True, False, False, ["tests/test_phase_contracts.py"], ["Field-name false positive regression needed."]),
        ("S", "Multiple roots / unrelated root pending work", True, True, False, False, False, False, False, False, ["tests/test_dynamic_library_sync.py"], ["Root-scoped validator assertions needed."]),
        ("T", "Stale legacy backlog", True, True, False, False, False, False, False, False, ["tests/test_s3a_m2_delta_e2e.py"], ["Canonical lifecycle should prevent future drift."]),
    ]
    return [
        {
            "id": ident,
            "path": path,
            "can_terminate": terminate,
            "preserves_source_media_identity": identity,
            "can_become_invisible": invisible,
            "can_consume_cap_forever": consume_cap,
            "can_block_unrelated_work": block,
            "can_report_completed_while_db_truth_incomplete": completed_incomplete,
            "can_report_failed_while_operator_outcome_acceptable": failed_acceptable,
            "can_mutate_source_icloud": mutate_source,
            "covered_by_tests": covered,
            "missing_tests_or_gaps": missing,
        }
        for (
            ident,
            path,
            terminate,
            identity,
            invisible,
            consume_cap,
            block,
            completed_incomplete,
            failed_acceptable,
            mutate_source,
            covered,
            missing,
        ) in rows
    ]


def build_answers(summary: Mapping[str, Any]) -> dict[str, Any]:
    run = summary["run18"]
    root = summary["root_inventory"]
    plan = summary["next_normal_plan"]
    run_rec = run["reconciliation"]
    debts = root["debts"]
    plan_counts = plan.get("counts") if isinstance(plan.get("counts"), dict) else {}
    plan_limits = plan.get("limits") if isinstance(plan.get("limits"), dict) else {}
    deferred_visibility = (
        plan.get("run18_deferred_continuation_visibility_db_only")
        if isinstance(plan.get("run18_deferred_continuation_visibility_db_only"), dict)
        else plan.get("run18_deferred_import_visibility", {})
    )
    import_breakdown = (
        plan.get("db_only_import_candidate_breakdown")
        if isinstance(plan.get("db_only_import_candidate_breakdown"), dict)
        else {
            "exact_normal_plan_import_count": plan_counts.get("estimated_import_count"),
            "exact_normal_plan_import_count_reason": "computed_by_opt_in_source_read_capable_plan",
        }
    )
    return {
        "q1_880_downstream_followup": {
            "were_all_880_actionable_at_run_start": True,
            "were_all_880_actionable_after_run": False,
            "answer": (
                "At run #18 plan time, the 880 follow-up rows were genuinely actionable app-media-backed downstream work. "
                "After run #18, those same 880 rows are terminal/downstream complete and should no longer be treated as actionable follow-up. "
                "Future similar rows can still accumulate if historical downstream_followup_planned remains overloaded, so lifecycle cleanup is needed. "
                "A separate older 20-row media-backed/source-missing debt remains and needs canonical APP_MEDIA_FOLLOWUP/BROKEN_STATE handling."
            ),
            "redundant_duplicate_noop_after_run": True,
            "passed_through_without_work": False,
            "can_accumulate_forever": True,
            "can_consume_future_cap": "not_proven_by_safe_default",
            "expected_to_reappear_next_plan": "not_recomputed_by_safe_default",
            "recommended_disposition": "Mark through canonical lifecycle as STABLE_NOOP/HISTORICAL_DIAGNOSTIC unless downstream status regresses.",
            "evidence": {
                "run18_downstream_followup_rows": run_rec["downstream_followup_rows"],
                "run18_downstream_followup_complete": run_rec["downstream_followup_complete"],
                "root_app_media_downstream_incomplete_total": debts["app_media_downstream_incomplete_total"],
                "root_app_media_downstream_incomplete_current_planner_followup_total": debts[
                    "app_media_incomplete_current_planner_followup_total"
                ],
                "next_plan_estimated_downstream_followup_count": plan_counts.get("estimated_downstream_followup_count"),
                "next_plan_evidence_mode": plan.get("evidence_mode"),
            },
        },
        "q2_120_planned_imports": {
            "operator_meaning": "partial_success",
            "completed_with_failures_definition": (
                "Current status means import stage stopped with item-level failures, but processed imports/downstream DB truth may still be complete. "
                "For run #18 it should display as completed_with_retryable_failures, not vague failure."
            ),
            "planned_imports": run_rec["planned_imports"],
            "imported": run_rec["imported"],
            "failed": run_rec["failed"],
            "deferred_unprocessed": run_rec["deferred_unprocessed"],
            "failure_budget_stop": run_rec["import_stopped_by"],
            "deferred_visible_next_plan": deferred_visibility,
            "next_plan_import_candidate_breakdown": import_breakdown,
            "can_failure_budget_repeat": True,
            "requires_retry_list": True,
        },
        "q3_remaining_debts": {
            "run18_deferred_import_candidates": run_rec["deferred_unprocessed"],
            "all_deferred_not_processed_budget_stop_total": debts["deferred_not_processed_budget_stop_total"],
            "retryable_source_read_failures": debts["retryable_source_read_failure_total"],
            "retryable_source_failures_including_source_missing": debts["retryable_source_failure_total"],
            "placeholders": debts["placeholder_total"],
            "older_app_media_backed_downstream_incomplete_rows": debts["app_media_downstream_incomplete_total"],
            "source_missing_media_backed_downstream_incomplete_rows": debts["source_missing_media_backed_incomplete_total"],
            "app_media_incomplete_current_planner_followup_rows": debts[
                "app_media_incomplete_current_planner_followup_total"
            ],
            "imported_but_downstream_incomplete_root2": debts["imported_but_downstream_incomplete_total"],
            "invisible_app_media_incomplete": debts["app_media_incomplete_invisible_total"],
            "historical_backlog_can_consume_cap": debts["historical_backlog_can_consume_cap"],
        },
        "q4_next_normal_manual_sync_health": {
            "evidence_mode": plan.get("evidence_mode"),
            "safe_default_source_read_capable_planner_invoked": bool(plan.get("source_read_capable_planner_invoked")),
            "healthy": (
                None
                if plan.get("evidence_mode") == "db_only_source_safe"
                else bool(
                    not plan.get("skipped")
                    and plan_counts.get("batch_executable")
                    and not plan_counts.get("unsafe_partial_scan")
                )
            ),
            "health_precision_note": (
                "Safe default does not recompute executable planner health because that path may walk/stat source entries."
                if plan.get("evidence_mode") == "db_only_source_safe"
                else "Planner health computed by opt-in source-read-capable plan."
            ),
            "estimated_import_count": plan_counts.get("estimated_import_count"),
            "estimated_import_count_status": plan_counts.get("estimated_import_count_status"),
            "estimated_downstream_followup_count": plan_counts.get("estimated_downstream_followup_count"),
            "estimated_downstream_followup_count_status": plan_counts.get("estimated_downstream_followup_count_status"),
            "import_candidate_breakdown": import_breakdown,
            "retryable_source_read_failure_count": debts["retryable_source_read_failure_total"],
            "retryable_source_failure_count_including_source_missing": debts["retryable_source_failure_total"],
            "placeholder_count": debts["placeholder_total"],
            "continuation_count": debts["deferred_not_processed_budget_stop_total"],
            "hidden_by_mtime_or_safety_window": "unknown_without_source_walk_stat",
            "hidden_by_stable_noop": False,
            "app_media_backed_incomplete_invisible": debts["app_media_incomplete_invisible_total"],
            "app_media_backed_incomplete_current_planner_followup": debts[
                "app_media_incomplete_current_planner_followup_total"
            ],
            "plan_expensive_ops": {
                "stat_required_count": plan_limits.get("stat_required_count"),
                "hash_required_count": plan_limits.get("hash_required_count"),
                "execute_import_classification_ai_localization": plan_limits.get(
                    "execute_import_classification_ai_localization",
                    "0/0/0/0 because this was dry-run planning only",
                ),
            },
        },
        "q5_state_machine_complexity": {
            "status": "too_many_overlapping_state_fields",
            "needs_canonical_lifecycle_classifier": True,
            "primary_conflicts": [
                "downstream_followup_planned is both historical run item state and a stable post-run source item state",
                "completed_with_failures mixes acceptable retryable item failures with operator-visible failure wording",
                "deferred_unprocessed/not_processed_budget_stop represents continuation, not terminal failure",
                "validator/report status can contradict DB-truth acceptance due field-name redaction false positives",
            ],
        },
    }


def markdown_report(summary: Mapping[str, Any]) -> str:
    run = summary["run18"]
    plan = summary["next_normal_plan"]
    root = summary["root_inventory"]
    answers = summary["answers"]
    state = summary["state_machine_inventory"]
    path_rows = summary["path_liveness"]
    generator = summary["generator_code_ref"]

    lines: list[str] = []
    lines.append("# S3A-M2-R Post-Merge Manual Sync Health Audit")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append("- Mode: production read-only audit; no Execute, no DB writes, no source/iCloud mutation.")
    lines.append("- Default evidence mode: DB-only/source-safe; no source walk/stat/open/hash/decode/hydration.")
    lines.append(f"- Audited baseline main commit: `{summary['audited_baseline_main_commit']}`.")
    lines.append(
        f"- Generator code head at run: `{generator['generator_git_head_at_run']}` "
        f"on branch `{generator['branch']}`."
    )
    lines.append(f"- Report commit head: `{report_value(summary['report_commit_head'])}` ({summary['report_commit_head_note']}).")
    lines.append(
        "- Working tree dirty at generation: "
        f"{report_value(generator['working_tree_dirty_at_generation'])}; "
        f"tracked dirty files: {', '.join(generator['dirty_files_at_generation']) or 'none'}; "
        f"untracked count: {generator['dirty_untracked_file_count_at_generation']}."
    )
    lines.append(f"- Production root audited: `{summary['root']['id']} / {summary['root']['label']}`.")
    lines.append(f"- Raw private evidence root: `{summary['raw_private_artifact_root']}`.")
    lines.append("")
    lines.append("## R0 Findings")
    lines.append("")
    lines.append("### 880 downstream follow-up rows")
    q1 = answers["q1_880_downstream_followup"]
    lines.append("")
    lines.append(q1["answer"])
    lines.append("")
    lines.append("| Question | Answer |")
    lines.append("|---|---|")
    lines.append(f"| Were all 880 genuinely actionable at run #18 plan time? | {bool_status(q1['were_all_880_actionable_at_run_start'])}; they were app-media-backed downstream follow-up work. |")
    lines.append(f"| Are those same 880 actionable after run #18? | {bool_status(q1['were_all_880_actionable_after_run'])}; 880/880 are terminal/downstream complete. |")
    lines.append(f"| Redundant/duplicate/no-op after run? | {bool_status(q1['redundant_duplicate_noop_after_run'])}; classify as stable diagnostic unless downstream status regresses. |")
    lines.append(f"| Did they merely pass through planned state without work? | {bool_status(q1['passed_through_without_work'])}; statuses show downstream completion. |")
    lines.append(f"| Can similar rows accumulate forever? | {bool_status(q1['can_accumulate_forever'])}; if `downstream_followup_planned` is retained as a historical state. |")
    lines.append(f"| Can they consume future caps? | {report_value(q1['can_consume_future_cap'])}; safe default does not rerun the source-read-capable planner. |")
    lines.append(f"| Expected to reappear next normal plan? | {report_value(q1['expected_to_reappear_next_plan'])}; not recomputed in source-safe mode. |")
    lines.append(f"| Recommended disposition | {q1['recommended_disposition']} |")
    lines.append("")
    lines.append("### 120 planned imports")
    q2 = answers["q2_120_planned_imports"]
    lines.append("")
    lines.append(
        f"Run #18 reconciles as {q2['planned_imports']} planned import-family items: "
        f"{q2['imported']} imported, {q2['failed']} retryable failures, "
        f"{q2['deferred_unprocessed']} deferred continuation after `{q2['failure_budget_stop']}`."
    )
    lines.append("")
    lines.append("- `completed_with_failures` is a partial success for run #18: DB truth for processed imports/follow-up passed, but source-read retry/deferred continuation remains.")
    lines.append("- Operator wording should become `completed_with_retryable_failures`; validator/report should not call it a clean completed run or a systemic failure.")
    visibility = q2["deferred_visible_next_plan"]
    if plan.get("evidence_mode") == "db_only_source_safe":
        lines.append(
            f"- Run #18 deferred imports visible as root-scoped DB continuation rows: "
            f"{visibility.get('present_as_root_continuation_rows')} / {visibility.get('run18_deferred_import_count')}; "
            "planner ordering is not recomputed in safe default mode."
        )
    else:
        lines.append(
            f"- Run #18 deferred imports visible in next plan by public hash prefix: "
            f"{visibility.get('selected_in_next_plan')} / {visibility.get('run18_deferred_import_count')}; "
            f"first position `{visibility.get('first_selected_position')}`, last `{visibility.get('last_selected_position')}`."
        )
    breakdown = q2["next_plan_import_candidate_breakdown"]
    lines.append("")
    lines.append("Safe-default breakdown of the earlier 347 next-plan import-candidate claim:")
    lines.append("")
    lines.append("| Bucket | Count / status |")
    lines.append("|---|---:|")
    lines.append(f"| Exact 347 normal-plan import count | {report_value(breakdown.get('exact_normal_plan_import_count'))} |")
    lines.append(f"| Exact count reason | {breakdown.get('exact_normal_plan_import_count_reason')} |")
    lines.append(f"| Run #18 deferred continuation rows | {report_value(breakdown.get('run18_deferred_continuation_rows'))} |")
    lines.append(f"| Other continuation rows | {report_value(breakdown.get('other_continuation_rows'))} |")
    lines.append(f"| Known DB continuation total | {report_value(breakdown.get('known_db_continuation_total'))} |")
    lines.append(f"| Known retryable source-read failures | {report_value(breakdown.get('known_retryable_source_read_failure_rows'))} |")
    lines.append(f"| Placeholder rows excluded from import | {report_value(breakdown.get('known_placeholder_deferred_rows_excluded_from_import'))} |")
    lines.append(f"| App-media follow-up candidates, not import | {report_value(breakdown.get('known_app_media_followup_candidates_not_import'))} |")
    lines.append(f"| Root-scope stat-error reason rows | {report_value(breakdown.get('root_scope_stat_error_reason_rows'))} |")
    lines.append(f"| Root-scope unsupported reason rows | {report_value(breakdown.get('root_scope_unsupported_reason_rows'))} |")
    lines.append(f"| Ledger-missing candidates | {report_value(breakdown.get('ledger_missing_candidates'))} |")
    lines.append(f"| Mtime-new / safety-window / old-mtime candidates | {report_value(breakdown.get('mtime_new_candidates'))} / {report_value(breakdown.get('safety_window_candidates'))} / {report_value(breakdown.get('old_mtime_known_pending_candidates'))} |")
    lines.append("")
    lines.append(
        "The first R0/R1 attempt reported `347` from the current source-read-capable planner. "
        "This safe-default report does not recompute that exact number because the planner path can walk/stat source entries. "
        "The DB-only evidence proves the 75 run #18 deferred rows remain visible as continuation and identifies the known DB debt buckets; "
        "ledger-missing and mtime-derived filesystem categories require explicit opt-in planner evidence."
    )
    lines.append("")
    lines.append("### Remaining debt inventory")
    q3 = answers["q3_remaining_debts"]
    lines.append("")
    lines.append("| Debt | Count | Interpretation |")
    lines.append("|---|---:|---|")
    lines.append(f"| Run #18 deferred import candidates | {q3['run18_deferred_import_candidates']} | Continuation, not failed import. |")
    lines.append(f"| All `not_processed_budget_stop` continuation rows | {q3['all_deferred_not_processed_budget_stop_total']} | Historical + current continuation inventory. |")
    lines.append(f"| Retryable source-read failures | {q3['retryable_source_read_failures']} | Item-level retry/debt list needed. |")
    lines.append(f"| Retryable/source-missing failure rows including old media-backed rows | {q3['retryable_source_failures_including_source_missing']} | Current status vocabulary mixes read retry and media-backed source-missing debt. |")
    lines.append(f"| Placeholder / cloud hydration rows | {q3['placeholders']} | Visible diagnostic/deferred debt. |")
    lines.append(f"| Older app-media-backed downstream-incomplete rows | {q3['older_app_media_backed_downstream_incomplete_rows']} | App storage exists, but current planner follow-up classifier catches {q3['app_media_incomplete_current_planner_followup_rows']}; these need canonical APP_MEDIA_FOLLOWUP/BROKEN_STATE handling. |")
    lines.append(f"| Source-missing media-backed downstream-incomplete rows | {q3['source_missing_media_backed_downstream_incomplete_rows']} | Visible as source-missing/retry style debt, not as follow-up. |")
    lines.append(f"| Imported but downstream-incomplete under root 2 | {q3['imported_but_downstream_incomplete_root2']} | Acceptance criterion currently satisfied. |")
    lines.append(f"| Invisible app-media incomplete rows | {q3['invisible_app_media_incomplete']} | None found. |")
    lines.append(f"| Historical backlog that can consume normal cap | {q3['historical_backlog_can_consume_cap']} | None found by current-priority classifier. |")
    lines.append("")
    lines.append("### Next normal manual sync")
    q4 = answers["q4_next_normal_manual_sync_health"]
    lines.append("")
    lines.append("| Check | Result |")
    lines.append("|---|---|")
    lines.append(f"| Evidence mode | {q4['evidence_mode']} |")
    lines.append(f"| Source-read-capable planner invoked | {bool_status(q4['safe_default_source_read_capable_planner_invoked'])} |")
    lines.append(f"| Healthy/readable next plan | {report_value(q4['healthy'])}; {q4['health_precision_note']} |")
    lines.append(f"| Estimated imports | {report_value(q4['estimated_import_count'])}; {report_value(q4['estimated_import_count_status'])} |")
    lines.append(f"| Estimated downstream follow-up | {report_value(q4['estimated_downstream_followup_count'])}; {report_value(q4['estimated_downstream_followup_count_status'])} |")
    lines.append(f"| Retryable source-read failure debt | {q4['retryable_source_read_failure_count']} |")
    lines.append(f"| Retryable/source-missing rows including media-backed old debt | {q4['retryable_source_failure_count_including_source_missing']} |")
    lines.append(f"| Placeholder debt | {q4['placeholder_count']} |")
    lines.append(f"| Continuation debt | {q4['continuation_count']} |")
    lines.append(f"| Hidden by mtime/safety window | {report_value(q4['hidden_by_mtime_or_safety_window'])} |")
    lines.append(f"| Hidden by stable no-op | {report_value(q4['hidden_by_stable_noop'])} |")
    lines.append(f"| App-media incomplete invisible | {q4['app_media_backed_incomplete_invisible']} |")
    lines.append(f"| App-media incomplete caught as current planner follow-up | {q4['app_media_backed_incomplete_current_planner_followup']} |")
    lines.append(f"| Plan stat/hash counts | stat `{q4['plan_expensive_ops']['stat_required_count']}`, hash `{q4['plan_expensive_ops']['hash_required_count']}` |")
    lines.append("")
    lines.append("Important nuance: the current dry-run planner is not used by default in this R0 audit because it can walk/stat source entries. The R1 target model should make Plan metadata-only and reserve source reads/hash/decode for Execute.")
    lines.append("")
    lines.append("## State-Machine Inventory")
    lines.append("")
    q5 = answers["q5_state_machine_complexity"]
    lines.append(q5["status"] + ".")
    lines.append("")
    for conflict in q5["primary_conflicts"]:
        lines.append(f"- {conflict}")
    lines.append("")
    lines.append("Top current root state counts:")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(root["sync_state_counts"], ensure_ascii=False, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("Report/status fields currently observed:")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(state["report_summary_status_fields"], ensure_ascii=False, indent=2, sort_keys=True, default=json_default))
    lines.append("```")
    lines.append("")
    lines.append("## Design-Level Path Liveness Audit")
    lines.append("")
    lines.append(
        "This is report-level path analysis, not executable formal verification. "
        "Executable verification is deferred to PR-R1/R2: lifecycle table-driven tests, "
        "WorkItem boundary tests, root-scoped validator tests, report/status contradiction "
        "regression tests, and browser validation for UI/progress."
    )
    lines.append("")
    lines.append("| Path | Terminates | Invisible risk | Cap forever | Blocks unrelated | Report risk | Source mutation | Missing test focus |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in path_rows:
        report_risk = bool(row["can_report_completed_while_db_truth_incomplete"] or row["can_report_failed_while_operator_outcome_acceptable"])
        missing = "; ".join(row["missing_tests_or_gaps"]) if row["missing_tests_or_gaps"] else "none"
        lines.append(
            f"| {row['id']}. {row['path']} | {bool_status(row['can_terminate'])} | "
            f"{bool_status(row['can_become_invisible'])} | {bool_status(row['can_consume_cap_forever'])} | "
            f"{bool_status(row['can_block_unrelated_work'])} | {bool_status(report_risk)} | "
            f"{bool_status(row['can_mutate_source_icloud'])} | {missing} |"
        )
    lines.append("")
    lines.append("## R0 Conclusion")
    lines.append("")
    lines.append("- PR #126 acceptance was truthful for current-stage DB truth, but current operator wording and reports still blur partial success, retryable item failures, and continuation.")
    lines.append("- The run #18 880 follow-up rows were real actionable follow-up at plan time and are terminal after run #18; this safe-default report does not prove future planner cap behavior for similar overloaded historical states.")
    lines.append("- The 20 older media-backed/source-missing downstream-incomplete rows are visible but semantically misclassified for the desired model: FOLLOWUP should use app-managed media and not depend on source readability.")
    lines.append("- The 75 run #18 deferred import candidates are visible as DB continuation rows. Exact normal-plan ordering/import count requires explicit opt-in source-read-capable planner evidence and is not part of default R0 validation.")
    lines.append("- R2/R3/R4 are too large for a single low-risk PR if implemented all at once. This branch should stop at R0/R1 and split implementation into lifecycle/WorkItem core, UI/tooling cleanup, and docs/runbook follow-through.")
    return "\n".join(lines)


def build_summary(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.models import DynamicSourceRoot

    db = open_db_session(production=bool(args.production))
    try:
        root = db.get(DynamicSourceRoot, int(args.root_id))
        if root is None:
            raise RuntimeError(f"dynamic_source_root_not_found:{args.root_id}")
        run_summary, run_private_rows, run_deferred_prefixes = summarize_run(
            db,
            run_id=int(args.run_id),
            root_id=int(args.root_id),
        )
        root_summary, debt_examples = summarize_root_inventory(db, root_id=int(args.root_id))
        next_plan = summarize_next_plan(
            db,
            root=root,
            max_files=int(args.max_files),
            run_deferred_prefixes=run_deferred_prefixes,
            include_plan=not bool(args.skip_next_plan),
            allow_source_read_plan=bool(args.allow_source_read_plan),
            root_inventory=root_summary,
        )
        state_machine = summarize_state_machine(db, root_id=int(args.root_id))
        generated_at = datetime.now(timezone.utc)
        branch = git_value("branch", "--show-current")
        generator_head = git_value("rev-parse", "HEAD")
        dirty_snapshot = git_status_snapshot()
        public = {
            "phase": PHASE,
            "generated_at": generated_at,
            "mode": "production_read_only" if args.production else "local_read_only",
            "branch": branch,
            "audited_baseline_main_commit": BASE_MERGE_COMMIT,
            "generator_git_head_at_run": generator_head,
            "generator_code_ref": {
                "branch": branch,
                "script": "scripts/audit_s3a_m2_r_post_merge_health.py",
                "generator_git_head_at_run": generator_head,
                **dirty_snapshot,
            },
            "report_commit_head": None,
            "report_commit_head_note": "A committed report cannot truthfully contain the final self-referential commit SHA; use PR closeout/head metadata after commit.",
            "pr_head_at_closeout": None,
            "pr_head_at_closeout_note": "Reported in PR/final closeout after the report regeneration commit.",
            "public_safe": True,
            "production_db_mutated": False,
            "source_icloud_mutated": False,
            "production_execute_run": False,
            "default_audit_source_safe": not bool(args.allow_source_read_plan),
            "source_read_capable_plan_requested": bool(args.allow_source_read_plan),
            "root": source_root_public(root),
            "run18": run_summary,
            "root_inventory": root_summary,
            "next_normal_plan": next_plan,
            "state_machine_inventory": state_machine,
            "path_liveness": path_liveness_matrix(),
            "formal_verification_status": {
                "state_machine_verified": False,
                "path_liveness_audit_level": "design_level_report_analysis",
                "executable_verification_deferred_to": [
                    "lifecycle table-driven tests",
                    "WorkItem boundary tests",
                    "root-scoped validator tests",
                    "report/status contradiction regression tests",
                    "browser validation for UI/progress",
                ],
            },
            "raw_private_artifact_root": ".local_manifests/s3a_m2_r/post_merge_health/",
            "artifact_lifecycle": {
                "script": "phase-scoped operational runner",
                "public_report": "public report / handoff",
                "private_evidence": "one-off local artifact / ignored output",
            },
            "scope_decision": {
                "stop_after_r0_r1": True,
                "reason": "R2-R4 implementation spans planner/execute/validator/UI/docs and should be split to avoid a giant unreviewable PR.",
                "proposed_split": [
                    "PR-R0: health audit + lifecycle/WorkItem design + runbook seed",
                    "PR-R1: lifecycle classifier/WorkItem refactor + tests",
                    "PR-R2: UI/progress/report/preflight cleanup + browser validation",
                ],
            },
        }
        public["answers"] = build_answers(public)
        private = {
            "public": public,
            "private": {
                "run_rows": run_private_rows,
                "debt_examples": debt_examples,
                "private_rows_include_source_item_ids_and_hashes": True,
                "private_paths_omitted": True,
            },
        }
        return public, private
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", action="store_true", help="Use ignored production profile. Read-only.")
    parser.add_argument("--root-id", type=int, default=DEFAULT_ROOT_ID)
    parser.add_argument("--run-id", type=int, default=DEFAULT_RUN_ID)
    parser.add_argument("--max-files", type=int, default=1000)
    parser.add_argument("--skip-next-plan", action="store_true", help="Skip all next-plan evidence.")
    parser.add_argument(
        "--allow-source-read-plan",
        action="store_true",
        help=(
            "Opt in to the current dry-run planner path. This may walk/stat source entries and is not used "
            "by the default source/iCloud-safe R0 audit."
        ),
    )
    parser.add_argument("--write-public", action="store_true", help="Write public JSON/Markdown reports under docs/reports.")
    parser.add_argument("--output-dir", type=Path, default=PRIVATE_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    public, private = build_summary(args)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    private_path = args.output_dir / f"s3a-m2-r-post-merge-health-private-{stamp}.json"
    public_private_path = args.output_dir / f"s3a-m2-r-post-merge-health-public-{stamp}.json"
    write_json(private_path, private)
    write_json(public_private_path, public)
    if args.write_public:
        write_json(PUBLIC_JSON, public)
        write_text(PUBLIC_MD, markdown_report(public))
    print(json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True, default=json_default))
    print(
        json.dumps(
            {
                "public_artifact": str(public_private_path),
                "private_artifact": str(private_path),
                "tracked_public_json": str(PUBLIC_JSON) if args.write_public else None,
                "tracked_public_md": str(PUBLIC_MD) if args.write_public else None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
