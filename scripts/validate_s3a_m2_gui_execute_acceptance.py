#!/usr/bin/env python3
"""Validate the final S3A-M2 Web Admin GUI execute acceptance run.

This script is intentionally read-only. It verifies the latest GUI-created
manual sync execute run after the operator clicks Execute in Web Admin, then
writes private raw evidence under .local_manifests and an optional public-safe
aggregate summary under docs/reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PHASE = "S3A-M2"
PHASE_SLUG = "s3a_m2_delta_e2e"
MIN_EXPECTED_RUN_ID = 8
LOCAL_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG / "gui_acceptance"
PUBLIC_SUMMARY_PATH = ROOT / "docs" / "reports" / "s3a-m2-gui-execute-acceptance-summary.json"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / "s3a-m2-production-delta-e2e-summary.json"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / "s3a-m2-production-delta-e2e.md"


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def public_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def apply_profile_env(profile_path: Path) -> dict[str, Any]:
    if not profile_path.exists():
        return {"loaded": False, "path_redacted": True}
    profile = read_json(profile_path)
    if not isinstance(profile, dict):
        raise RuntimeError("production_profile_json_not_object")
    db = profile.get("db") if isinstance(profile.get("db"), dict) else {}
    env_values = {
        "VIOLET_ENV": "production",
        "BLOMBOORU_DEBUG": "false",
        "APP_PORT": str(profile.get("app_port") or ""),
        "BLOMBOORU_REQUIRE_AUTH": "true" if bool(profile.get("require_auth", True)) else "false",
        "VIOLET_STORAGE_ROOT": str(profile.get("storage_root") or ""),
        "VIOLET_CANONICAL_REPO_ROOT": str(profile.get("repo_root") or ROOT),
        "VIOLET_PRODUCTION_PYTHON": str(profile.get("python") or ""),
        "VIOLET_PRODUCTION_PROFILE_ACTIVE": "true",
        "VIOLET_PRODUCTION_PROFILE_ID": str(profile.get("profile_id") or ""),
        "VIOLET_SKIP_DOTENV": "1",
        "VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP": "true",
        "VIOLET_PRODUCTION_MAINTENANCE_APPROVED": "false",
        "POSTGRES_HOST": str(db.get("host") or "localhost"),
        "POSTGRES_PORT": str(db.get("port") or 5432),
        "POSTGRES_DB": str(db.get("name") or "blombooru"),
        "POSTGRES_USER": str(db.get("user") or "postgres"),
        "POSTGRES_PASSWORD": str(db.get("password") or ""),
        "DYNAMIC_LIBRARY_MANUAL_SYNC_ENABLED": "true" if bool(profile.get("manual_sync_enabled")) else "false",
        "DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_ENABLED": (
            "true" if bool(profile.get("manual_sync_execute_enabled")) else "false"
        ),
    }
    if profile.get("manual_sync_execute_max_files") is not None:
        env_values["DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_MAX_FILES"] = str(profile["manual_sync_execute_max_files"])
    if profile.get("manual_sync_max_duration_seconds") is not None:
        env_values["DYNAMIC_LIBRARY_MANUAL_SYNC_MAX_DURATION_SECONDS"] = str(profile["manual_sync_max_duration_seconds"])
    for key, value in env_values.items():
        if value != "":
            os.environ[key] = value
    return {
        "loaded": True,
        "path_redacted": True,
        "profile_id": str(profile.get("profile_id") or ""),
        "app_port": str(profile.get("app_port") or ""),
        "violet_env": "production",
        "db_name": str(db.get("name") or ""),
        "storage_root_public_marker": public_hash(str(profile.get("storage_root") or "")),
        "manual_sync_enabled": bool(profile.get("manual_sync_enabled")),
        "manual_sync_execute_enabled": bool(profile.get("manual_sync_execute_enabled")),
        "manual_sync_execute_max_files": profile.get("manual_sync_execute_max_files"),
        "automation_flags": {
            key: bool(value)
            for key, value in (profile.get("automation_flags") or {}).items()
            if key in {
                "dynamic_library_auto_sync",
                "ai_auto_tag_after_import",
                "content_classification_auto_after_import",
                "tag_translation_auto",
                "tag_translation_background",
            }
        },
    }


def open_db_session():
    from app import database as app_database

    app_database.init_engine()
    if app_database.SessionLocal is None:
        raise RuntimeError("database_session_not_initialized")
    return app_database.SessionLocal()


def latest_manual_execute_run(db: Any, *, min_run_id: int, run_id: int | None):
    from app.models import DynamicSyncRun

    query = db.query(DynamicSyncRun).filter(
        DynamicSyncRun.run_type == "manual_sync_execute",
        DynamicSyncRun.dry_run == False,  # noqa: E712
    )
    if run_id is not None:
        query = query.filter(DynamicSyncRun.id == int(run_id))
    else:
        query = query.filter(DynamicSyncRun.id > int(min_run_id)).order_by(DynamicSyncRun.id.desc())
    return query.first()


def run_items_summary(db: Any, run_id: int) -> dict[str, Any]:
    from app.models import DynamicSourceItem, DynamicSyncRunItem

    rows = db.query(DynamicSyncRunItem).filter(DynamicSyncRunItem.sync_run_id == int(run_id)).all()
    state_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    source_item_ids: list[int] = []
    media_ids: set[int] = set()
    for row in rows:
        state_counts[str(row.item_state or "unknown")] += 1
        reason_counts[str(row.reason or row.item_state or "unknown")] += 1
        source_item_ids.append(int(row.source_item_id))
        if row.media_id is not None:
            media_ids.add(int(row.media_id))

    status_rows = []
    if source_item_ids:
        status_rows = (
            db.query(
                DynamicSourceItem.import_status,
                DynamicSourceItem.classification_status,
                DynamicSourceItem.ai_tagging_status,
                DynamicSourceItem.localization_status,
            )
            .filter(DynamicSourceItem.id.in_(source_item_ids))
            .all()
        )
    import_status_counts = Counter(str(row.import_status or "unknown") for row in status_rows)
    classification_status_counts = Counter(str(row.classification_status or "unknown") for row in status_rows)
    ai_status_counts = Counter(str(row.ai_tagging_status or "unknown") for row in status_rows)
    localization_status_counts = Counter(str(row.localization_status or "unknown") for row in status_rows)
    remaining_importable = (
        db.query(DynamicSourceItem)
        .filter(DynamicSourceItem.import_status == "pending")
        .filter(DynamicSourceItem.source_status == "available")
        .filter(DynamicSourceItem.sync_state.in_(["new", "changed"]))
        .count()
    )
    remaining_placeholders = (
        db.query(DynamicSourceItem)
        .filter(DynamicSourceItem.source_status == "deferred")
        .filter(DynamicSourceItem.deferred_reason.in_(["cloud_placeholder", "icloud_placeholder"]))
        .count()
    )
    return {
        "run_item_count": len(rows),
        "state_counts": dict(sorted(state_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "media_ids": sorted(media_ids),
        "source_item_count": len(source_item_ids),
        "import_status_counts": dict(sorted(import_status_counts.items())),
        "classification_status_counts": dict(sorted(classification_status_counts.items())),
        "ai_tagging_status_counts": dict(sorted(ai_status_counts.items())),
        "localization_status_counts": dict(sorted(localization_status_counts.items())),
        "remaining_importable_db_pending_count": int(remaining_importable),
        "remaining_placeholder_db_count": int(remaining_placeholders),
    }


def stage_count_from_status(counts: Mapping[str, int], *accepted: str) -> int:
    return sum(int(counts.get(key, 0)) for key in accepted)


def build_validation(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = apply_profile_env(args.profile_json) if args.profile_json else {"loaded": False}

    from app.config import settings
    from scripts.diagnose_s3a_m2_ai_tag_assignments import (
        assignment_rows,
        entity_truth_summary,
        summarize_assignments,
    )
    from scripts.run_s3a_m2_delta_e2e_with_telemetry import scan_public_output

    db = open_db_session()
    try:
        run = latest_manual_execute_run(db, min_run_id=args.min_run_id, run_id=args.run_id)
        if run is None:
            public = {
                "phase": PHASE,
                "status": "blocked_no_gui_execute_run_found",
                "validated": False,
                "min_run_id": int(args.min_run_id),
                "profile": profile,
                "branch": git_value("branch", "--show-current"),
                "head_sha": git_value("rev-parse", "HEAD"),
                "public_safe": True,
            }
            return public, {"public": public, "private": {"run_found": False}}

        item_summary = run_items_summary(db, int(run.id))
        media_ids = item_summary["media_ids"]
        rows = assignment_rows(db, media_ids)
        assignment_summary = summarize_assignments(rows, media_ids, settings)
        entity_summary = entity_truth_summary(db, media_ids)
        run_payload = run.summary_json or {}
        execute_payload = run_payload.get("manual_sync_execute") if isinstance(run_payload, dict) else {}
        execute_payload = execute_payload if isinstance(execute_payload, dict) else {}
        request = execute_payload.get("request") if isinstance(execute_payload.get("request"), dict) else {}
        outcome = execute_payload.get("outcome_counts") if isinstance(execute_payload.get("outcome_counts"), dict) else {}
        localization_payload = execute_payload.get("localization") if isinstance(execute_payload.get("localization"), dict) else {}

        imported = int(outcome.get("imported") or item_summary["state_counts"].get("imported", 0))
        classified = stage_count_from_status(item_summary["classification_status_counts"], "classified", "classified_reused")
        ai_tagged = stage_count_from_status(item_summary["ai_tagging_status_counts"], "ai_tagged", "tagged", "tagged_reused")
        localized = stage_count_from_status(item_summary["localization_status_counts"], "localized")
        expected_total_seen = int(run.total_seen or 0)
        ledger_ok = expected_total_seen == int(item_summary["run_item_count"])
        high_conf_expected = int(assignment_summary.get("high_conf_nonproper_expected_normal_count") or 0) + int(
            assignment_summary.get("high_conf_proper_expected_normal_count") or 0
        )
        high_conf_incorrect = int(assignment_summary.get("high_conf_nonproper_incorrect_suggestion_count") or 0) + int(
            assignment_summary.get("high_conf_proper_incorrect_suggestion_count") or 0
        )
        tag_semantics_ok = (
            bool(rows)
            and high_conf_incorrect == 0
            and not (high_conf_expected > 0 and bool(assignment_summary.get("all_ai_assignments_are_suggestions")))
        )

        blockers: list[str] = []
        if int(run.id) <= int(args.min_run_id):
            blockers.append("gui_run_not_newer_than_min_run_id")
        if str(run.status) != "completed":
            blockers.append("gui_run_not_completed")
        if not ledger_ok:
            blockers.append("ledger_row_count_mismatch")
        if imported <= 0 and not args.allow_zero_import:
            blockers.append("gui_run_imported_zero")
        if imported > 0 and classified < imported:
            blockers.append("classification_incomplete_for_imported_items")
        if imported > 0 and ai_tagged < imported:
            blockers.append("ai_tagging_incomplete_for_imported_items")
        if imported > 0 and localized < imported:
            blockers.append("localization_incomplete_for_imported_items")
        if not tag_semantics_ok:
            blockers.append("ai_tag_assignment_semantics_invalid")
        if int(entity_summary.get("violations_found") or 0) != 0:
            blockers.append("ai_only_entity_truth_violation")
        if item_summary["remaining_importable_db_pending_count"] > 0:
            blockers.append("remaining_importable_db_pending_items")
        if item_summary["remaining_placeholder_db_count"] > 0:
            blockers.append("remaining_placeholder_items")

        public = {
            "phase": PHASE,
            "status": "passed_gui_execute_completed" if not blockers else "blocked_gui_execute_validation_failed",
            "validated": not blockers,
            "branch": git_value("branch", "--show-current"),
            "head_sha": git_value("rev-parse", "HEAD"),
            "profile": profile,
            "gui_execute_run_id": int(run.id),
            "min_run_id": int(args.min_run_id),
            "run_status": str(run.status),
            "run_type": str(run.run_type),
            "run_mode": str(run.mode),
            "dry_run": bool(run.dry_run),
            "total_seen": expected_total_seen,
            "imported": imported,
            "classified": classified,
            "ai_tagged": ai_tagged,
            "localized_source_items": localized,
            "localization_summary": {
                "status": str(localization_payload.get("status") or "unknown"),
                "translated": int(localization_payload.get("translated") or 0),
                "failed": int(localization_payload.get("failed") or 0),
                "skipped": int(localization_payload.get("skipped") or 0),
                "blocked_reason": localization_payload.get("blocked_reason"),
            },
            "ledger": {
                "expected_total_seen": expected_total_seen,
                "run_item_count": int(item_summary["run_item_count"]),
                "passed": ledger_ok,
            },
            "state_counts": item_summary["state_counts"],
            "reason_counts": item_summary["reason_counts"],
            "stage_status_counts": {
                "classification": item_summary["classification_status_counts"],
                "ai_tagging": item_summary["ai_tagging_status_counts"],
                "localization": item_summary["localization_status_counts"],
            },
            "final_inventory": {
                "remaining_importable_db_pending_count": item_summary["remaining_importable_db_pending_count"],
                "remaining_placeholder_db_count": item_summary["remaining_placeholder_db_count"],
            },
            "request": {
                "root_id": request.get("root_id"),
                "max_files": request.get("max_files"),
                "effective_max_files": request.get("effective_max_files"),
                "execute_max_files_cap": request.get("execute_max_files_cap"),
                "hydrated_only": request.get("hydrated_only"),
                "trigger_type": request.get("trigger_type"),
                "production_acceptance_approved": bool(request.get("production_acceptance_approved")),
                "expected_plan_hash_present": bool(request.get("expected_plan_hash")),
            },
            "tag_assignment": {
                "assignment_count": assignment_summary.get("assignment_count"),
                "all_ai_assignments_are_suggestions": assignment_summary.get("all_ai_assignments_are_suggestions"),
                "high_conf_nonproper_expected_normal_count": assignment_summary.get(
                    "high_conf_nonproper_expected_normal_count"
                ),
                "high_conf_nonproper_incorrect_suggestion_count": assignment_summary.get(
                    "high_conf_nonproper_incorrect_suggestion_count"
                ),
                "high_conf_proper_expected_normal_count": assignment_summary.get(
                    "high_conf_proper_expected_normal_count"
                ),
                "high_conf_proper_incorrect_suggestion_count": assignment_summary.get(
                    "high_conf_proper_incorrect_suggestion_count"
                ),
                "category_suggestion_counts": assignment_summary.get("category_suggestion_counts"),
                "passed": tag_semantics_ok,
            },
            "entity_truth": {
                "violations_found": int(entity_summary.get("violations_found") or 0),
                "confirmed_or_locked_ai_entity_assignments": int(
                    entity_summary.get("confirmed_or_locked_ai_entity_assignments") or 0
                ),
                "passed": int(entity_summary.get("violations_found") or 0) == 0,
            },
            "blockers": blockers,
            "public_safe": True,
        }
        redaction = scan_public_output(public, json.dumps(public, ensure_ascii=False, sort_keys=True, default=json_default))
        public["public_redaction"] = redaction
        if not redaction.get("passed"):
            public["status"] = "blocked_public_redaction_failed"
            public["validated"] = False
            public.setdefault("blockers", []).append("public_redaction_failed")
        private = {
            "public": public,
            "private": {
                "run_id": int(run.id),
                "media_ids": media_ids,
                "assignment_summary": assignment_summary,
                "entity_truth": entity_summary,
                "raw_ids_private": True,
            },
        }
        return public, private
    finally:
        db.close()


def maybe_update_main_report(public: Mapping[str, Any]) -> None:
    from scripts.run_s3a_m2_delta_e2e_with_telemetry import (
        public_report_markdown,
        read_json as read_runner_json,
        refresh_completion_claims,
        scan_public_output,
        write_json as write_runner_json,
        write_text,
    )

    if not PUBLIC_REPORT_JSON.exists():
        return
    summary = read_runner_json(PUBLIC_REPORT_JSON)
    if not isinstance(summary, dict):
        raise RuntimeError("s3a_m2_public_summary_not_object")
    launcher = dict(summary.get("launcher_web_admin_acceptance") or {})
    launcher.update(
        {
            "validated": bool(public.get("validated")),
            "status": public.get("status"),
            "target_path": "/admin?tab=content#dynamic-library-sync-section",
            "execute_clicked": bool(public.get("validated")),
            "gui_execute_completed": bool(public.get("validated")),
            "gui_execute_run_id": public.get("gui_execute_run_id"),
            "production_execute_run_id_seen": public.get("gui_execute_run_id"),
            "latest_job_status": public.get("run_status"),
            "latest_job_imported": public.get("imported"),
            "validated_head_sha": public.get("head_sha"),
            "public_source_identity": (summary.get("source") or {}).get("public_source_identity"),
            "raw_artifact": f".local_manifests/{PHASE_SLUG}/gui_acceptance/gui-execute-acceptance-private.json",
            "raw_path_committed": False,
        }
    )
    summary["launcher_web_admin_acceptance"] = launcher
    summary["gui_execute_acceptance"] = public
    refresh_completion_claims(summary)
    markdown = public_report_markdown(summary)
    redaction = scan_public_output(summary, markdown)
    summary["public_redaction"] = redaction
    if not redaction.get("passed"):
        raise RuntimeError("public_redaction_failed")
    write_runner_json(PUBLIC_REPORT_JSON, summary)
    write_text(PUBLIC_REPORT_MD, markdown)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-json", type=Path, default=ROOT / ".local_manifests" / "production_launcher" / "production-profile.json")
    parser.add_argument("--min-run-id", type=int, default=MIN_EXPECTED_RUN_ID)
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--allow-zero-import", action="store_true")
    parser.add_argument("--write-public-summary", action="store_true")
    parser.add_argument("--update-main-report", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=LOCAL_OUTPUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.min_run_id < 0:
        print("ERROR: --min-run-id must be non-negative", file=sys.stderr)
        return 2
    try:
        public, private = build_validation(args)
        private_path = args.output_dir / "gui-execute-acceptance-private.json"
        write_json(private_path, private)
        if args.write_public_summary:
            write_json(PUBLIC_SUMMARY_PATH, public)
        if args.update_main_report:
            maybe_update_main_report(public)
        print(json.dumps(public, ensure_ascii=True, indent=2, sort_keys=True, default=json_default))
        return 0 if public.get("validated") else 2
    except Exception as exc:
        payload = {
            "phase": PHASE,
            "status": "blocked_validation_script_error",
            "error": str(exc)[:1000],
            "public_safe": True,
        }
        write_json(args.output_dir / "gui-execute-acceptance-error-private.json", payload)
        print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
