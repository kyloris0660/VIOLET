#!/usr/bin/env python3
"""Read-only S3A-M2 DynamicSourceItem priority backlog audit.

This script explains why the normal manual-sync planner can see a very large
priority workset even after a full import. It writes public-safe aggregates and
private row evidence under .local_manifests. It never updates the database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PHASE_SLUG = "s3a_m2_delta_e2e"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG / "priority_backlog_audit"
PUBLIC_SUMMARY = ROOT / "docs" / "reports" / "s3a-m2-priority-backlog-audit-summary.json"
RETRYABLE_REASONS = {"read_error", "read_timeout", "cloud_hydration_failed"}
PLACEHOLDER_REASONS = {"cloud_placeholder", "icloud_placeholder"}
TERMINAL_STABLE_REASONS = {
    "existing_media_hash",
    "duplicate_content_hash",
    "skipped_existing_media",
    "unsupported_extension",
    "hidden",
    "zero_byte",
    "damaged",
    "path_missing",
    "source_missing",
}


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=json_default) + "\n")
            count += 1
    return count


def public_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def git_value(*args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def load_production_profile_env() -> dict[str, Any]:
    from scripts.violet_production_control import _profile_to_env, load_production_profile

    profile, _path, errors = load_production_profile(repo_root=ROOT)
    if errors:
        raise RuntimeError(f"production_profile_invalid:{','.join(errors)}")
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


def counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda row: str(row[0]))}


def age_bucket(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "null"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - value.astimezone(timezone.utc)).total_seconds() / 86400.0)
    if age_days < 1:
        return "<1d"
    if age_days < 7:
        return "1-6d"
    if age_days < 30:
        return "7-29d"
    if age_days < 90:
        return "30-89d"
    return ">=90d"


def quantiles(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"min": 0, "avg": 0.0, "median": 0.0, "p95": 0, "max": 0}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))]
    return {
        "min": int(ordered[0]),
        "avg": round(sum(ordered) / len(ordered), 3),
        "median": round(float(statistics.median(ordered)), 3),
        "p95": int(p95),
        "max": int(ordered[-1]),
    }


def legacy_priority(item: Any, *, requires_followup: bool) -> bool:
    import_status = str(item.import_status or "")
    sync_state = str(item.sync_state or "")
    reason = str(item.deferred_reason or item.failure_reason or "")
    if import_status == "pending":
        return True
    if sync_state == "skipped_placeholder" or reason in PLACEHOLDER_REASONS:
        return True
    if import_status in {"failed", "deferred"} and reason in RETRYABLE_REASONS:
        return True
    if import_status == "imported" and item.media_id is not None and requires_followup:
        return True
    return False


def current_priority(item: Any, *, mtime_cutoff_ns: int | None, requires_followup: bool) -> bool:
    import_status = str(item.import_status or "")
    sync_state = str(item.sync_state or "")
    reason = str(item.deferred_reason or item.failure_reason or "")
    if import_status == "imported" and item.media_id is not None and requires_followup:
        return True
    if sync_state == "skipped_placeholder" or reason in PLACEHOLDER_REASONS:
        return True
    if import_status in {"failed", "deferred"} and reason in RETRYABLE_REASONS:
        return True
    if import_status == "pending":
        item_mtime_ns = getattr(item, "mtime_ns", None)
        return bool(mtime_cutoff_ns is None or (item_mtime_ns is not None and int(item_mtime_ns) >= int(mtime_cutoff_ns)))
    return False


def root_public(root: Any) -> dict[str, Any]:
    return {
        "id": int(root.id),
        "label": str(root.label or ""),
        "root_path_hash_prefix": str(root.root_path_hash or "")[:12],
        "source_type": str(root.source_type or ""),
        "is_active": bool(root.is_active),
    }


def safe_stat(path: Path) -> dict[str, Any]:
    try:
        stat_result = path.stat()
        return {
            "visible": True,
            "file_size": int(stat_result.st_size),
            "mtime_ns": int(stat_result.st_mtime_ns),
            "error": None,
        }
    except FileNotFoundError:
        return {"visible": False, "file_size": None, "mtime_ns": None, "error": "file_missing"}
    except OSError as exc:
        return {"visible": False, "file_size": None, "mtime_ns": None, "error": f"stat_error:{exc.__class__.__name__}"}


def audit(*, production: bool, root_label: str | None, root_id: int | None, output_dir: Path, write_public_summary: bool) -> dict[str, Any]:
    from sqlalchemy import func
    from app.models import DynamicSourceItem, DynamicSourceRoot, DynamicSyncRunItem, Media
    from app.config import settings
    from app.services.dynamic_library_sync_service import (
        _manual_plan_existing_requires_followup,
        _manual_plan_mtime_cutoff_ns,
        _manual_plan_source_mtime_watermark_ns,
    )

    now = datetime.now(timezone.utc)
    db = open_db_session(production=production)
    try:
        roots_query = db.query(DynamicSourceRoot)
        if root_id is not None:
            roots_query = roots_query.filter(DynamicSourceRoot.id == int(root_id))
        elif root_label:
            roots_query = roots_query.filter(DynamicSourceRoot.label == root_label)
        roots = roots_query.order_by(DynamicSourceRoot.id.asc()).all()
        if not roots:
            raise RuntimeError("no_dynamic_source_roots_matched")

        media_rows = db.query(Media.id, Media.hash, Media.path).all()
        media_by_id = {int(media_id): {"hash": media_hash, "path": media_path} for media_id, media_hash, media_path in media_rows}
        media_hashes = {str(media_hash) for _media_id, media_hash, _media_path in media_rows if media_hash}
        all_roots_public: list[dict[str, Any]] = []
        private_rows: list[dict[str, Any]] = []
        root_summaries: list[dict[str, Any]] = []
        storage_presence_cache: dict[int, bool] = {}

        def media_exists(media_id: Any) -> bool:
            return media_id is not None and int(media_id) in media_by_id

        def app_storage_visible(media_id: Any) -> bool:
            if media_id is None:
                return False
            mid = int(media_id)
            if mid in storage_presence_cache:
                return storage_presence_cache[mid]
            media_payload = media_by_id.get(mid) or {}
            resolved = settings.resolve_storage_path(str(media_payload.get("path") or ""))
            visible = bool(resolved and resolved.exists())
            storage_presence_cache[mid] = visible
            return visible

        for root in roots:
            items = (
                db.query(DynamicSourceItem)
                .filter(DynamicSourceItem.source_root_id == root.id)
                .order_by(DynamicSourceItem.id.asc())
                .all()
            )
            all_roots_public.append(root_public(root))
            watermark_ns = _manual_plan_source_mtime_watermark_ns(items)
            mtime_cutoff_ns = _manual_plan_mtime_cutoff_ns(watermark_ns, 7 * 24 * 60 * 60)

            status_x = Counter()
            reason_x = Counter()
            age_x = Counter()
            mtime_window = Counter()
            last_seen_age = Counter()
            first_seen_age = Counter()
            by_import = Counter()
            by_sync = Counter()
            by_classification = Counter()
            by_ai = Counter()
            by_localization = Counter()
            priority_rows = 0
            current_priority_rows = 0
            legacy_pending_changed = 0
            outside_window = 0
            inside_window = 0
            rows_with_media_id = 0
            rows_without_media_id = 0
            rows_matching_existing_media_hash = 0
            rows_imported_but_still_pending_or_changed = 0
            rows_with_terminal_stable_skip_reasons = 0
            rows_with_retryable_failures = 0
            rows_with_downstream_followup_needed = 0
            rows_that_should_be_actionable_now = 0
            rows_that_should_be_stable_noop = 0
            rows_that_need_repair_or_migration = 0
            rows_duplicate_or_existing = 0
            rows_content_hash_null = 0
            legacy_pending_changed_outside_window = 0
            legacy_pending_changed_inside_window = 0
            rows_matching_existing_media = 0
            rows_with_import_status_changed = 0
            rows_with_import_status_imported = 0
            rows_with_import_status_pending = 0
            rows_with_import_status_deferred = 0
            rows_with_import_status_failed = 0
            rows_with_sync_state_imported = 0
            rows_with_sync_state_pending = 0
            rows_with_sync_state_changed = 0
            rows_with_app_storage_media_present = 0
            reconciliation = Counter()
            mtime_values: list[int] = []
            stale_repair_candidates: list[dict[str, Any]] = []
            root_path = Path(str(root.root_path))

            source_item_ids = [int(item.id) for item in items]
            run_item_counts: dict[int, int] = {}
            if source_item_ids:
                for sid, count in (
                    db.query(DynamicSyncRunItem.source_item_id, func.count(DynamicSyncRunItem.id))
                    .filter(DynamicSyncRunItem.source_item_id.in_(source_item_ids))
                    .group_by(DynamicSyncRunItem.source_item_id)
                    .all()
                ):
                    run_item_counts[int(sid)] = int(count)

            for item in items:
                import_status = str(item.import_status or "")
                sync_state = str(item.sync_state or "")
                cls = str(item.classification_status or "")
                ai = str(item.ai_tagging_status or "")
                loc = str(item.localization_status or "")
                reason = str(item.deferred_reason or item.failure_reason or "")
                status_x[(sync_state, import_status, cls, ai, loc)] += 1
                reason_x[(reason or "none", sync_state, import_status)] += 1
                by_import[import_status or "empty"] += 1
                by_sync[sync_state or "empty"] += 1
                by_classification[cls or "empty"] += 1
                by_ai[ai or "empty"] += 1
                by_localization[loc or "empty"] += 1
                age_x[age_bucket(item.updated_at, now)] += 1
                first_seen_age[age_bucket(item.first_seen_at, now)] += 1
                last_seen_age[age_bucket(item.last_seen_at, now)] += 1

                item_mtime_ns = int(item.mtime_ns) if item.mtime_ns is not None else None
                if item_mtime_ns is not None:
                    mtime_values.append(item_mtime_ns)
                in_window = bool(mtime_cutoff_ns is None or (item_mtime_ns is not None and item_mtime_ns >= int(mtime_cutoff_ns)))
                if in_window:
                    inside_window += 1
                    mtime_window["inside_safety_window"] += 1
                else:
                    outside_window += 1
                    mtime_window["outside_safety_window"] += 1

                if import_status == "imported":
                    rows_with_import_status_imported += 1
                elif import_status == "pending":
                    rows_with_import_status_pending += 1
                elif import_status == "deferred":
                    rows_with_import_status_deferred += 1
                elif import_status == "failed":
                    rows_with_import_status_failed += 1
                elif import_status == "changed":
                    rows_with_import_status_changed += 1
                if sync_state == "imported":
                    rows_with_sync_state_imported += 1
                elif sync_state == "pending":
                    rows_with_sync_state_pending += 1
                elif sync_state == "changed":
                    rows_with_sync_state_changed += 1

                current_media_exists = media_exists(item.media_id)
                if current_media_exists:
                    rows_matching_existing_media += 1
                if app_storage_visible(item.media_id):
                    rows_with_app_storage_media_present += 1
                if item.media_id is not None:
                    rows_with_media_id += 1
                else:
                    rows_without_media_id += 1
                if not item.content_hash:
                    rows_content_hash_null += 1
                hash_matches = bool(item.content_hash and item.content_hash in media_hashes)
                if hash_matches:
                    rows_matching_existing_media_hash += 1
                if item.media_id is not None or hash_matches:
                    rows_duplicate_or_existing += 1
                if import_status == "pending" and sync_state in {"changed", "new"} and (item.media_id is not None or hash_matches):
                    rows_imported_but_still_pending_or_changed += 1
                if reason in TERMINAL_STABLE_REASONS or sync_state in {"skipped_existing_media", "skipped_duplicate", "skipped_unsupported"}:
                    rows_with_terminal_stable_skip_reasons += 1
                if import_status in {"failed", "deferred"} and reason in RETRYABLE_REASONS:
                    rows_with_retryable_failures += 1
                followup_needed = bool(
                    import_status == "imported"
                    and item.media_id is not None
                    and _manual_plan_existing_requires_followup(item)
                )
                if followup_needed:
                    rows_with_downstream_followup_needed += 1

                legacy = legacy_priority(item, requires_followup=followup_needed)
                current = current_priority(item, mtime_cutoff_ns=mtime_cutoff_ns, requires_followup=followup_needed)
                if legacy:
                    priority_rows += 1
                if current:
                    current_priority_rows += 1
                    rows_that_should_be_actionable_now += 1
                if legacy and import_status == "pending" and sync_state == "changed":
                    legacy_pending_changed += 1
                    if in_window:
                        legacy_pending_changed_inside_window += 1
                    else:
                        legacy_pending_changed_outside_window += 1
                stable_noop = bool(
                    (item.media_id is not None or hash_matches)
                    and not followup_needed
                    and import_status in {"pending", "imported", "skipped"}
                    and not (import_status in {"failed", "deferred"} and reason in RETRYABLE_REASONS)
                )
                if stable_noop:
                    rows_that_should_be_stable_noop += 1
                needs_repair = bool(
                    import_status == "pending"
                    and sync_state in {"changed", "new"}
                    and not in_window
                    and (item.media_id is not None or hash_matches)
                    and not followup_needed
                )
                if needs_repair:
                    rows_that_need_repair_or_migration += 1
                    if len(stale_repair_candidates) < 500:
                        stale_repair_candidates.append(
                            {
                                "source_item_id": int(item.id),
                                "relative_path_hash": str(item.relative_path_hash or ""),
                                "relative_path_hash_public": public_hash(str(item.relative_path_hash or "")),
                                "root_id": int(root.id),
                                "file_size": int(item.file_size or 0),
                                "mtime_ns": item_mtime_ns,
                                "content_hash_present": bool(item.content_hash),
                                "hash_matches_existing_media": hash_matches,
                                "media_id_present": item.media_id is not None,
                                "sync_state": sync_state,
                                "import_status": import_status,
                                "classification_status": cls,
                                "ai_tagging_status": ai,
                                "localization_status": loc,
                                "run_item_count": int(run_item_counts.get(int(item.id), 0)),
                            }
                        )
                if legacy:
                    source_path = root_path / str(item.relative_path or "")
                    source_stat = safe_stat(source_path)
                    source_visible = bool(source_stat.get("visible"))
                    stored_file_size = int(item.file_size or 0)
                    stored_mtime_ns = item_mtime_ns
                    metadata_matches_source = bool(
                        source_visible
                        and int(source_stat.get("file_size") or -1) == stored_file_size
                        and stored_mtime_ns is not None
                        and int(source_stat.get("mtime_ns") or -2) == int(stored_mtime_ns)
                    )
                    has_existing_evidence = bool(current_media_exists or hash_matches)
                    if source_visible and current_media_exists:
                        reconciliation["file_visible_and_media_exists"] += 1
                    elif source_visible and not current_media_exists:
                        reconciliation["file_visible_but_no_media"] += 1
                    elif not source_visible and current_media_exists:
                        reconciliation["file_missing_but_media_exists"] += 1
                    else:
                        reconciliation["file_missing_and_no_media"] += 1
                    if metadata_matches_source and has_existing_evidence:
                        reconciliation["metadata_matches_existing_import"] += 1
                    if source_visible and not metadata_matches_source and hash_matches:
                        reconciliation["metadata_changed_but_content_already_imported"] += 1
                    if hash_matches and not current_media_exists:
                        reconciliation["duplicate_existing_hash"] += 1
                    if current:
                        reconciliation["real_new_or_changed_candidate"] += 1
                    if followup_needed:
                        reconciliation["downstream_followup_only"] += 1
                    if import_status in {"failed", "deferred"} and reason in RETRYABLE_REASONS:
                        reconciliation["retryable_failure"] += 1
                    if needs_repair:
                        reconciliation["stale_update_check_artifact"] += 1
                    if legacy and not any(
                        [
                            current,
                            followup_needed,
                            import_status in {"failed", "deferred"} and reason in RETRYABLE_REASONS,
                            needs_repair,
                            hash_matches,
                            current_media_exists,
                        ]
                    ):
                        reconciliation["unknown_requires_manual_policy"] += 1
                private_rows.append(
                    {
                        "source_item_id": int(item.id),
                        "root_id": int(root.id),
                        "relative_path": str(item.relative_path or ""),
                        "relative_path_hash": str(item.relative_path_hash or ""),
                        "file_size": int(item.file_size or 0),
                        "mtime_ns": item_mtime_ns,
                        "content_hash": str(item.content_hash or ""),
                        "media_id": int(item.media_id) if item.media_id is not None else None,
                        "sync_state": sync_state,
                        "import_status": import_status,
                        "classification_status": cls,
                        "ai_tagging_status": ai,
                        "localization_status": loc,
                        "failure_reason": str(item.failure_reason or ""),
                        "deferred_reason": str(item.deferred_reason or ""),
                        "legacy_priority": legacy,
                        "current_priority": current,
                        "inside_safety_window": in_window,
                        "hash_matches_existing_media": hash_matches,
                        "followup_needed": followup_needed,
                        "needs_repair_or_migration": needs_repair,
                        "run_item_count": int(run_item_counts.get(int(item.id), 0)),
                        "media_row_exists": current_media_exists,
                        "app_storage_media_present": app_storage_visible(item.media_id),
                    }
                )

            root_summaries.append(
                {
                    "root": root_public(root),
                    "total_dynamic_source_items": len(items),
                    "priority_workset_rows_legacy_pre_fix": priority_rows,
                    "total_priority_workset_rows": priority_rows,
                    "priority_workset_rows_current_normal": current_priority_rows,
                    "legacy_pending_changed_rows": legacy_pending_changed,
                    "legacy_pending_changed_outside_safety_window": legacy_pending_changed_outside_window,
                    "legacy_pending_changed_inside_safety_window": legacy_pending_changed_inside_window,
                    "rows_outside_safety_window": outside_window,
                    "rows_inside_safety_window": inside_window,
                    "rows_with_media_id": rows_with_media_id,
                    "rows_without_media_id": rows_without_media_id,
                    "rows_matching_existing_media": rows_matching_existing_media,
                    "rows_matching_existing_media_hash": rows_matching_existing_media_hash,
                    "rows_imported_but_still_pending_or_changed": rows_imported_but_still_pending_or_changed,
                    "rows_with_import_status_imported": rows_with_import_status_imported,
                    "rows_with_import_status_pending": rows_with_import_status_pending,
                    "rows_with_import_status_changed": rows_with_import_status_changed,
                    "rows_with_import_status_deferred": rows_with_import_status_deferred,
                    "rows_with_import_status_failed": rows_with_import_status_failed,
                    "rows_with_sync_state_imported": rows_with_sync_state_imported,
                    "rows_with_sync_state_pending": rows_with_sync_state_pending,
                    "rows_with_sync_state_changed": rows_with_sync_state_changed,
                    "rows_with_terminal_stable_skip_reasons": rows_with_terminal_stable_skip_reasons,
                    "rows_with_retryable_failures": rows_with_retryable_failures,
                    "rows_with_downstream_followup_needed": rows_with_downstream_followup_needed,
                    "rows_that_should_be_actionable_now": rows_that_should_be_actionable_now,
                    "rows_that_should_be_stable_noop": rows_that_should_be_stable_noop,
                    "rows_that_need_repair_or_migration": rows_that_need_repair_or_migration,
                    "rows_duplicate_or_existing_by_media_id_or_hash": rows_duplicate_or_existing,
                    "rows_content_hash_null": rows_content_hash_null,
                    "rows_with_app_storage_media_present": rows_with_app_storage_media_present,
                    "reconciliation_against_filesystem_and_media": counter_dict(reconciliation),
                    "source_mtime_watermark_ns": int(watermark_ns) if watermark_ns is not None else None,
                    "mtime_cutoff_ns": int(mtime_cutoff_ns) if mtime_cutoff_ns is not None else None,
                    "mtime_ns_distribution": quantiles(mtime_values),
                    "age_distribution_updated_at": counter_dict(age_x),
                    "age_distribution_first_seen_at": counter_dict(first_seen_age),
                    "age_distribution_last_seen_at": counter_dict(last_seen_age),
                    "mtime_window_distribution": counter_dict(mtime_window),
                    "status_cross_tabulation_top": {
                        "|".join(key): int(value)
                        for key, value in status_x.most_common(40)
                    },
                    "reason_cross_tabulation_top": {
                        "|".join(key): int(value)
                        for key, value in reason_x.most_common(40)
                    },
                    "sync_state_counts": counter_dict(by_sync),
                    "import_status_counts": counter_dict(by_import),
                    "classification_status_counts": counter_dict(by_classification),
                    "ai_tagging_status_counts": counter_dict(by_ai),
                    "localization_status_counts": counter_dict(by_localization),
                    "repair_dry_run": {
                        "candidate_count": rows_that_need_repair_or_migration,
                        "candidate_condition": "pending new/changed rows outside safety window with media_id or existing content hash, no downstream follow-up needed",
                        "proposed_target": {
                            "sync_state": "skipped_existing_media",
                            "import_status": "skipped",
                            "deferred_reason": "existing_media_hash",
                            "classification_status": "classified_if_media_present_else_waiting_import",
                            "ai_tagging_status": "ai_tagged_if_media_present_else_waiting_import",
                            "localization_status": "localized_or_not_applicable_if_media_present_else_waiting_ai_tags",
                        },
                        "requires_owner_approval": True,
                        "would_modify_db": False,
                    },
                }
            )

        generated_at = datetime.now(timezone.utc)
        public = {
            "generated_at": generated_at,
            "git_head": git_value("rev-parse", "HEAD"),
            "mode": "production_read_only" if production else "test_read_only",
            "public_safe": True,
            "roots_audited": all_roots_public,
            "root_summaries": root_summaries,
            "source_root_breakdown": {
                str(summary["root"]["id"]): {
                    "label": summary["root"]["label"],
                    "total_dynamic_source_items": summary["total_dynamic_source_items"],
                    "total_priority_workset_rows": summary["total_priority_workset_rows"],
                    "rows_that_should_be_actionable_now": summary["rows_that_should_be_actionable_now"],
                    "rows_that_should_be_stable_noop": summary["rows_that_should_be_stable_noop"],
                    "rows_that_need_repair_or_migration": summary["rows_that_need_repair_or_migration"],
                }
                for summary in root_summaries
            },
            "raw_private_artifact_root": ".local_manifests/s3a_m2_delta_e2e/priority_backlog_audit/",
            "production_db_mutated": False,
            "source_icloud_mutated": False,
        }
        private = {
            **public,
            "private_rows_include_paths_and_hashes": True,
            "rows": private_rows,
        }
        stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
        private_path = output_dir / f"priority-backlog-audit-private-{stamp}.json"
        public_path = output_dir / f"priority-backlog-audit-public-{stamp}.json"
        write_json(private_path, private)
        write_json(public_path, public)
        for summary, root in zip(root_summaries, roots):
            candidates = [
                row
                for row in private_rows
                if int(row["root_id"]) == int(root.id) and row.get("needs_repair_or_migration")
            ]
            write_jsonl(output_dir / f"priority-backlog-repair-candidates-root{root.id}-{stamp}.jsonl", candidates)
            summary["private_repair_candidate_ledger"] = f".local_manifests/s3a_m2_delta_e2e/priority_backlog_audit/priority-backlog-repair-candidates-root{root.id}-{stamp}.jsonl"
        write_json(public_path, public)
        if write_public_summary:
            write_json(PUBLIC_SUMMARY, public)
        return {"public": public, "public_artifact": str(public_path), "private_artifact": str(private_path)}
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", action="store_true", help="Use the persisted production profile. Read-only.")
    parser.add_argument("--root-label", default="icloud-photos-production")
    parser.add_argument("--root-id", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-public-summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = audit(
        production=bool(args.production),
        root_label=args.root_label,
        root_id=args.root_id,
        output_dir=args.output_dir,
        write_public_summary=bool(args.write_public_summary),
    )
    print(json.dumps(result["public"], ensure_ascii=False, indent=2, sort_keys=True, default=json_default))
    print(json.dumps({"public_artifact": result["public_artifact"], "private_artifact": result["private_artifact"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
