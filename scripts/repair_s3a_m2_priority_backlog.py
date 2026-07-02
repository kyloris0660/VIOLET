#!/usr/bin/env python3
"""Repair audited stale S3A-M2 DynamicSourceItem priority backlog rows.

This tool is deliberately narrow. It only terminalizes source-ledger rows that
match the S3A-M2 priority-backlog audit condition: old pending/changed rows on
the production iCloud root that are already represented by existing media/hash
and app-storage evidence. It never deletes rows, imports media, runs providers,
or touches source/iCloud files.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.audit_s3a_m2_priority_backlog import (  # noqa: E402
    load_production_profile_env,
    open_db_session,
    public_hash,
    write_json,
    write_jsonl,
)

PHASE_SLUG = "s3a_m2_delta_e2e"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG / "priority_backlog_repair"
PUBLIC_SUMMARY = ROOT / "docs" / "reports" / "s3a-m2-priority-backlog-repair-summary.json"
RETRYABLE_REASONS = {"read_error", "read_timeout", "cloud_hydration_failed"}
TARGET_SYNC_STATE = "skipped_existing_media"
TARGET_IMPORT_STATUS = "skipped"
TARGET_REASON = "existing_media_hash"


def private_artifact_ref(value: str | Path | None) -> str | None:
    if not value:
        return None
    return f".local_manifests/s3a_m2_delta_e2e/priority_backlog_repair/{Path(value).name}"


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if hasattr(value, "value"):
        return value.value
    return str(value)


def git_value(*args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def storage_visible(settings: Any, media_path: str | None) -> bool:
    if not media_path:
        return False
    try:
        resolved = settings.resolve_storage_path(str(media_path))
        return bool(resolved and resolved.exists())
    except Exception:
        return False


def row_snapshot(item: Any, *, root_label: str, media_hash_matches: bool, app_storage_visible: bool) -> dict[str, Any]:
    return {
        "source_item_id": int(item.id),
        "source_root_id": int(item.source_root_id),
        "source_root_label": root_label,
        "relative_path": str(item.relative_path or ""),
        "relative_path_hash": str(item.relative_path_hash or ""),
        "relative_path_hash_public": public_hash(str(item.relative_path_hash or "")),
        "file_size": int(item.file_size or 0),
        "mtime": item.mtime,
        "mtime_ns": int(item.mtime_ns) if item.mtime_ns is not None else None,
        "content_hash": str(item.content_hash or ""),
        "media_id": int(item.media_id) if item.media_id is not None else None,
        "source_status": str(item.source_status or ""),
        "sync_state": str(item.sync_state or ""),
        "import_status": str(item.import_status or ""),
        "classification_status": str(item.classification_status or ""),
        "ai_tagging_status": str(item.ai_tagging_status or ""),
        "localization_status": str(item.localization_status or ""),
        "failure_reason": str(item.failure_reason or ""),
        "deferred_reason": str(item.deferred_reason or ""),
        "first_seen_at": item.first_seen_at,
        "last_seen_at": item.last_seen_at,
        "last_checked_at": item.last_checked_at,
        "last_imported_at": item.last_imported_at,
        "last_sync_run_id": int(item.last_sync_run_id) if item.last_sync_run_id is not None else None,
        "last_seen_run_id": int(item.last_seen_run_id) if item.last_seen_run_id is not None else None,
        "metadata_json": item.metadata_json,
        "media_hash_matches": bool(media_hash_matches),
        "app_storage_visible": bool(app_storage_visible),
    }


def select_repair_candidates(db: Any, *, root_id: int, output_dir: Path) -> dict[str, Any]:
    from app.config import settings
    from app.models import DynamicSourceItem, DynamicSourceRoot, Media
    from app.services.dynamic_library_sync_service import (
        _manual_plan_mtime_cutoff_ns,
        _manual_plan_source_mtime_watermark_ns,
    )

    root = db.get(DynamicSourceRoot, int(root_id))
    if root is None or not root.is_active:
        raise RuntimeError(f"active_source_root_not_found:{root_id}")

    items = (
        db.query(DynamicSourceItem)
        .filter(DynamicSourceItem.source_root_id == int(root.id))
        .order_by(DynamicSourceItem.id.asc())
        .all()
    )
    media_rows = db.query(Media.id, Media.hash, Media.path).all()
    media_by_id = {int(media_id): {"hash": str(media_hash or ""), "path": str(media_path or "")} for media_id, media_hash, media_path in media_rows}
    media_by_hash: dict[str, list[dict[str, Any]]] = {}
    for media_id, media_hash, media_path in media_rows:
        if media_hash:
            media_by_hash.setdefault(str(media_hash), []).append({"id": int(media_id), "path": str(media_path or "")})

    watermark_ns = _manual_plan_source_mtime_watermark_ns(items)
    mtime_cutoff_ns = _manual_plan_mtime_cutoff_ns(watermark_ns, 7 * 24 * 60 * 60)
    root_path = Path(str(root.root_path))
    candidates: list[Any] = []
    snapshots: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    skipped_uncertain: Counter[str] = Counter()

    for item in items:
        import_status = str(item.import_status or "")
        sync_state = str(item.sync_state or "")
        reason = str(item.deferred_reason or item.failure_reason or "")
        item_mtime_ns = int(item.mtime_ns) if item.mtime_ns is not None else None
        in_window = bool(mtime_cutoff_ns is None or (item_mtime_ns is not None and item_mtime_ns >= int(mtime_cutoff_ns)))
        if not (import_status == "pending" and sync_state in {"changed", "new"}):
            continue
        if in_window:
            skipped_uncertain["inside_safety_window"] += 1
            continue
        if reason in RETRYABLE_REASONS:
            skipped_uncertain["retryable_failure_reason"] += 1
            continue

        media_payload = media_by_id.get(int(item.media_id)) if item.media_id is not None else None
        media_hash_matches = bool(media_payload and item.content_hash and str(item.content_hash) == str(media_payload.get("hash") or ""))
        hash_media_payloads = media_by_hash.get(str(item.content_hash or ""), [])
        hash_storage_visible = any(storage_visible(settings, payload.get("path")) for payload in hash_media_payloads)
        media_storage_visible = bool(media_payload and storage_visible(settings, media_payload.get("path")))
        represented = bool((media_payload and media_storage_visible) or (item.content_hash and hash_storage_visible))
        if not represented:
            skipped_uncertain["no_existing_media_storage_evidence"] += 1
            continue

        source_path = root_path / str(item.relative_path or "")
        try:
            source_stat = source_path.stat()
            source_visible = True
        except OSError:
            source_visible = False
            source_stat = None
        if not source_visible:
            skipped_uncertain["source_file_not_visible"] += 1
            continue
        if source_stat is not None and item.file_size is not None and int(source_stat.st_size) != int(item.file_size):
            skipped_uncertain["source_size_changed"] += 1
            continue
        if source_stat is not None and item_mtime_ns is not None and int(getattr(source_stat, "st_mtime_ns", 0)) != item_mtime_ns:
            skipped_uncertain["source_mtime_changed"] += 1
            continue

        candidates.append(item)
        reason_counts[reason or "none"] += 1
        snapshots.append(
            row_snapshot(
                item,
                root_label=str(root.label or ""),
                media_hash_matches=media_hash_matches or bool(hash_media_payloads),
                app_storage_visible=media_storage_visible or hash_storage_visible,
            )
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = output_dir / f"priority-backlog-pre-repair-root{root.id}-{stamp}.jsonl"
    write_jsonl(snapshot_path, snapshots)

    return {
        "root": {
            "id": int(root.id),
            "label": str(root.label or ""),
            "root_path_hash_prefix": str(root.root_path_hash or "")[:12],
        },
        "watermark": {
            "source_mtime_watermark_ns": watermark_ns,
            "mtime_cutoff_ns": mtime_cutoff_ns,
        },
        "candidate_count": len(candidates),
        "candidate_ids": [int(item.id) for item in candidates],
        "candidate_reason_counts": dict(sorted(reason_counts.items())),
        "uncertain_skipped_counts": dict(sorted(skipped_uncertain.items())),
        "snapshot_path": str(snapshot_path),
    }


def execute_repair(db: Any, *, candidate_ids: list[int]) -> int:
    from app.models import DynamicSourceItem

    now = datetime.now(timezone.utc)
    updated = 0
    for start in range(0, len(candidate_ids), 1000):
        chunk = [int(value) for value in candidate_ids[start : start + 1000]]
        if not chunk:
            continue
        updated += int(
            db.query(DynamicSourceItem)
            .filter(DynamicSourceItem.id.in_(chunk))
            .update(
                {
                    DynamicSourceItem.source_status: "available",
                    DynamicSourceItem.sync_state: TARGET_SYNC_STATE,
                    DynamicSourceItem.import_status: TARGET_IMPORT_STATUS,
                    DynamicSourceItem.classification_status: "classified_reused",
                    DynamicSourceItem.ai_tagging_status: "tagged_reused",
                    DynamicSourceItem.localization_status: "localized",
                    DynamicSourceItem.failure_reason: None,
                    DynamicSourceItem.deferred_reason: TARGET_REASON,
                    DynamicSourceItem.last_checked_at: now,
                },
                synchronize_session=False,
            )
            or 0
        )
    db.commit()
    return updated


def active_sync_blockers(db: Any) -> list[dict[str, Any]]:
    from app.models import DynamicSyncRun

    runs = (
        db.query(DynamicSyncRun)
        .filter(DynamicSyncRun.status.in_(("pending", "running", "cancelling")))
        .order_by(DynamicSyncRun.id.asc())
        .all()
    )
    return [
        {
            "run_id": int(run.id),
            "run_type": str(run.run_type or ""),
            "mode": str(run.mode or ""),
            "status": str(run.status or ""),
        }
        for run in runs
    ]


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.production:
        load_production_profile_env()
    db = open_db_session(production=False)
    try:
        active_blockers = active_sync_blockers(db)
        if active_blockers:
            db.rollback()
            result = {
                "status": "blocked_active_sync_runs_present",
                "active_sync_runs": active_blockers,
                "git_head": git_value("rev-parse", "HEAD"),
            }
            write_json(output_dir / "priority-backlog-repair-blocked-active-runs.json", result)
            if args.write_public_summary:
                write_json(PUBLIC_SUMMARY, result)
            return result
        before = select_repair_candidates(db, root_id=int(args.root_id), output_dir=output_dir)
        expected = int(args.expected_candidates)
        if before["candidate_count"] != expected:
            db.rollback()
            result = {
                "status": "blocked_candidate_count_mismatch",
                "expected_candidates": expected,
                "actual_candidates": before["candidate_count"],
                "before": {key: value for key, value in before.items() if key != "candidate_ids"},
                "git_head": git_value("rev-parse", "HEAD"),
            }
            write_json(output_dir / "priority-backlog-repair-blocked.json", result)
            if args.write_public_summary:
                write_json(PUBLIC_SUMMARY, result)
            return result

        repaired = 0
        after: dict[str, Any] | None = None
        status = "dry_run"
        if args.execute:
            repaired = execute_repair(db, candidate_ids=list(before["candidate_ids"]))
            after = select_repair_candidates(db, root_id=int(args.root_id), output_dir=output_dir)
            status = "repaired"
        else:
            db.rollback()

        result = {
            "status": status,
            "git_head": git_value("rev-parse", "HEAD"),
            "branch": git_value("branch", "--show-current"),
            "production": bool(args.production),
            "execute": bool(args.execute),
            "expected_candidates": expected,
            "repair_criteria": {
                "source_root_id": int(args.root_id),
                "import_status": "pending",
                "sync_state": ["changed", "new"],
                "outside_safety_window": True,
                "requires_existing_media_or_hash_and_storage_evidence": True,
                "requires_source_file_visible_and_metadata_unchanged": True,
                "excludes_retryable_failures": sorted(RETRYABLE_REASONS),
                "deletes_rows": False,
                "runs_import_classification_ai_localization": False,
            },
            "target_state": {
                "sync_state": TARGET_SYNC_STATE,
                "import_status": TARGET_IMPORT_STATUS,
                "classification_status": "classified_reused",
                "ai_tagging_status": "tagged_reused",
                "localization_status": "localized",
                "deferred_reason": TARGET_REASON,
            },
            "before": {key: value for key, value in before.items() if key != "candidate_ids"},
            "rows_repaired": repaired,
            "after": ({key: value for key, value in after.items() if key != "candidate_ids"} if after else None),
            "rollback_snapshot_path": before["snapshot_path"],
            "rollback_strategy": "Use the private JSONL snapshot to restore old DynamicSourceItem status/reason/media/hash fields by source_item_id if rollback is ever approved.",
            "source_icloud_mutation_performed": False,
            "media_import_performed": False,
            "classification_performed": False,
            "ai_tagging_performed": False,
            "localization_performed": False,
        }
        write_json(output_dir / "priority-backlog-repair-result.json", result)
        if args.write_public_summary:
            public = dict(result)
            public["rollback_snapshot_path"] = private_artifact_ref(before["snapshot_path"])
            if isinstance(public.get("before"), dict):
                public["before"] = dict(public["before"])
                public["before"]["snapshot_path"] = private_artifact_ref(public["before"].get("snapshot_path"))
            if isinstance(public.get("after"), dict):
                public["after"] = dict(public["after"])
                public["after"]["snapshot_path"] = private_artifact_ref(public["after"].get("snapshot_path"))
            write_json(PUBLIC_SUMMARY, public)
        return result
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", action="store_true", help="Load the production profile before connecting to the DB.")
    parser.add_argument("--root-id", type=int, default=2)
    parser.add_argument("--expected-candidates", type=int, required=True)
    parser.add_argument("--execute", action="store_true", help="Actually update the bounded candidate rows.")
    parser.add_argument("--write-public-summary", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    result = run(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=json_default))
    return 0 if str(result.get("status")) in {"dry_run", "repaired"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
