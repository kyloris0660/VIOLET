"""S3A-M1 manual sync dry-run/execute runner.

Default mode is public-safe dry-run planning. Execute mode is guarded by the
same service gates as the Admin API and is intended for dev/test validation or
separately approved production acceptance only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / "s3a_m1_manual_sync_execute"
DEFAULT_REPORT_JSON = DEFAULT_OUTPUT_DIR / "manual-sync-runner-report.json"
DEFAULT_REPORT_MD = DEFAULT_OUTPUT_DIR / "manual-sync-runner-report.md"

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import DynamicSourceRoot  # noqa: E402
from app.services.dynamic_library_sync_service import plan_manual_sync_dry_run  # noqa: E402
from app.services.manual_sync_execute_service import (  # noqa: E402
    MANUAL_SYNC_EXECUTE_MAX_FILES,
    ManualSyncExecuteError,
    create_manual_sync_execute_run,
    execute_manual_sync_run,
)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def _write_markdown(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plan = payload.get("plan") or {}
    execution = payload.get("execution") or {}
    counts = plan.get("counts") or {}
    integrity = plan.get("integrity") or {}
    lines = [
        "# S3A-M1 Manual Sync Execute Report",
        "",
        f"- status: `{payload.get('status')}`",
        f"- mode: `{payload.get('mode')}`",
        f"- violet_env: `{settings.VIOLET_ENV}`",
        f"- plan_hash: `{integrity.get('plan_hash', '-')}`",
        f"- total_seen: `{counts.get('total_seen', 0)}`",
        f"- estimated_import_count: `{counts.get('estimated_import_count', 0)}`",
        f"- execute_run_id: `{execution.get('id', '-')}`",
        f"- execute_status: `{execution.get('status', '-')}`",
        f"- production_acceptance_pending: `{payload.get('production_acceptance_pending')}`",
        "",
        "No source paths, filenames, API keys, or original image bytes are included in this public report.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run S3A-M1 manual sync dry-run or guarded execute.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--root-id", type=int, help="Registered DynamicSourceRoot id. Required for execute.")
    source.add_argument("--source-path", help="Ad hoc absolute source path for dry-run planning only.")
    parser.add_argument("--max-files", type=int, default=MANUAL_SYNC_EXECUTE_MAX_FILES)
    parser.add_argument("--stable-age-seconds", type=float, default=0.0)
    parser.add_argument("--hydrated-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute", action="store_true", help="Execute the guarded manual sync after validating a dry-run plan.")
    parser.add_argument("--expected-plan-hash")
    parser.add_argument("--confirmation-phrase")
    parser.add_argument("--plan-created-at")
    parser.add_argument("--production-acceptance-approved", action="store_true")
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--validation-focused-tests-passed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute and args.source_path:
        print("--execute requires --root-id; ad hoc --source-path is dry-run only.", file=sys.stderr)
        return 2
    if args.execute:
        missing = [
            name
            for name, value in {
                "--expected-plan-hash": args.expected_plan_hash,
                "--confirmation-phrase": args.confirmation_phrase,
                "--plan-created-at": args.plan_created_at,
            }.items()
            if not value
        ]
        if missing:
            print(f"--execute missing required arguments: {', '.join(missing)}", file=sys.stderr)
            return 2

    db = SessionLocal()
    try:
        source_record_id = None
        source_path = args.source_path
        if args.root_id:
            root = db.get(DynamicSourceRoot, args.root_id)
            if root is None:
                print("Registered source root not found.", file=sys.stderr)
                return 2
            source_record_id = root.id
            source_path = root.root_path

        plan_created_at = _parse_datetime(args.plan_created_at) if args.execute else None
        plan = plan_manual_sync_dry_run(
            db,
            source_path=source_path or "",
            source_record_id=source_record_id,
            max_files=args.max_files,
            hydrated_only=args.hydrated_only,
            stable_age_seconds=args.stable_age_seconds,
            include_private_details=False,
            now=plan_created_at,
        )
        execution = None
        if args.execute:
            run = create_manual_sync_execute_run(
                db,
                root_id=int(args.root_id),
                max_files=args.max_files,
                hydrated_only=args.hydrated_only,
                stable_age_seconds=args.stable_age_seconds,
                expected_plan_hash=str(args.expected_plan_hash),
                confirmation_phrase=str(args.confirmation_phrase),
                plan_created_at=str(args.plan_created_at),
                production_acceptance_approved=bool(args.production_acceptance_approved),
            )
            approved_plan = ((run.summary_json or {}).get("manual_sync_execute") or {}).get("plan")
            if isinstance(approved_plan, dict):
                plan = approved_plan
            execution = execute_manual_sync_run(db, run_id=run.id)

        payload: Dict[str, Any] = {
            "phase": "S3A-M1",
            "contract_id": "s3a_m1_manual_sync_execute_contract_v1",
            "status": "completed" if not args.execute or (execution and execution.get("status") == "completed") else "blocked",
            "mode": "execute" if args.execute else "dry_run_plan",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "plan": plan,
            "execution": execution,
            "validation": {
                "focused_tests_passed": bool(args.validation_focused_tests_passed),
                "runner_completed": True,
            },
            "safety": {
                "production_execute_performed": bool(settings.IS_PRODUCTION_ENV and args.execute),
                "production_acceptance_pending": not bool(settings.IS_PRODUCTION_ENV and args.production_acceptance_approved),
                "source_mutation_performed": False,
                "automatic_sync_enabled": False,
                "scheduled_sync_enabled": False,
                "startup_sync_enabled": False,
                "llm_calls_performed": False,
            },
            "production_acceptance_pending": not bool(settings.IS_PRODUCTION_ENV and args.production_acceptance_approved),
        }
        _write_json(args.report_json, payload)
        _write_markdown(args.report_md, payload)
        print(json.dumps({"status": payload["status"], "report_json": str(args.report_json), "report_md": str(args.report_md)}, sort_keys=True))
        return 0 if payload["status"] == "completed" else 3
    except ManualSyncExecuteError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 3
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
