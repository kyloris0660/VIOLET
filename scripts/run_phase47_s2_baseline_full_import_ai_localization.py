#!/usr/bin/env python3
"""Phase 4.7-S2 baseline full import readiness and execution gate.

Lifecycle: phase-scoped operational runner.

This runner is intentionally fail-closed.  Gate 1 readiness is collected before
any import, copy, classification, AI tagging, LLM call, or browser validation.
If Gate 1 fails, the runner writes private blocked ledgers and a redacted public
summary/report, then exits without executing later stages.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import create_engine, func, inspect, text

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PHASE = "4.7-S2"
PHASE_TITLE = "Baseline Full Import, AI Tagging, and Tag Localization"
PHASE_SLUG = "phase-4.7-s2-baseline-full-import-ai-localization"
BRANCH = "codex/phase47-s2-baseline-full-import-ai-localization"
CONFIRM_PHRASE = "EXECUTE_PHASE47_S2_BASELINE_FULL_IMPORT_AI_TAG_LOCALIZATION"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"

PRIVATE_LEDGER_NAMES = (
    "readiness-proof.json",
    "fresh-dynamic-sync-dry-run.json",
    "hydration-ledger.jsonl",
    "import-item-ledger.jsonl",
    "unsupported-or-deferred.jsonl",
    "cloud-deferred.jsonl",
    "batch-summary.jsonl",
    "classification-ledger.jsonl",
    "ai-tagging-ledger.jsonl",
    "localization-ledger.jsonl",
    "llm-localization-audit.json",
    "browser-validation.json",
    "public-redaction-check.json",
    "run-summary-private.json",
)

DYNAMIC_SYNC_TABLES = (
    "blombooru_dynamic_source_roots",
    "blombooru_dynamic_source_items",
    "blombooru_dynamic_sync_runs",
    "blombooru_dynamic_sync_run_items",
)

DYNAMIC_SYNC_INDEX_PREFIXES = (
    "ix_dynamic_source_roots_",
    "ix_dynamic_source_items_",
    "ix_dynamic_sync_runs_",
    "ix_dynamic_sync_run_items_",
)

CLOUD_FAILURE_REASONS = {
    "cloud_hydration_failed",
    "cloud_network_unavailable",
    "read_timeout",
    "permission_denied",
    "unreadable_source",
}

HYDRATION_WORKLOAD_REASONS = {
    "cloud_placeholder",
    "cloud_offline",
    "cloud_recall_on_open",
    "cloud_recall_on_data_access",
    "cloud_only",
    "recall_required",
    "needs_hydration",
}

CLOUD_DEFERRED_REASONS = HYDRATION_WORKLOAD_REASONS | CLOUD_FAILURE_REASONS
UNSUPPORTED_REASONS = {"unsupported_extension", "unsupported_file_type"}
SIDECAR_EXTENSIONS = {
    ".aae",
    ".xmp",
    ".json",
    ".xml",
    ".txt",
    ".db",
    ".ini",
    ".plist",
    ".thm",
    ".icloud",
}
DESIRED_UNSUPPORTED_MEDIA_EXTENSIONS = {
    ".heic",
    ".heif",
    ".dng",
    ".raw",
    ".cr2",
    ".nef",
    ".arw",
    ".rw2",
    ".orf",
    ".raf",
    ".mov",
    ".mp4",
    ".webm",
    ".avi",
    ".mkv",
}

DEFAULT_EXPECTED_SOURCE_MIN_ITEMS = 30000
DEFAULT_HYDRATION_FAILURE_MAX_ITEMS = 1000
DEFAULT_HYDRATION_FAILURE_MAX_RATE = 0.10
DEFAULT_IMPORT_FAILURE_MAX_ITEMS = 50
DEFAULT_IMPORT_FAILURE_MAX_RATE = 0.01
DEFAULT_CLASSIFICATION_FAILURE_MAX_ITEMS = 50
DEFAULT_CLASSIFICATION_FAILURE_MAX_RATE = 0.01
DEFAULT_AI_FAILURE_MAX_ITEMS = 100
DEFAULT_AI_FAILURE_MAX_RATE = 0.02
DEFAULT_LOCALIZATION_FAILURE_MAX_ITEMS = 500
DEFAULT_LOCALIZATION_FAILURE_MAX_RATE = 0.05

REQUIRED_PUBLIC_FIELDS = {
    "phase",
    "title",
    "generated_at",
    "branch",
    "head_evidence",
    "status",
    "mode",
    "pipeline_contract",
    "gate0",
    "readiness",
    "dynamic_sync_dry_run",
    "icloud_cloud_policy",
    "import_results",
    "classification_results",
    "ai_tagging_results",
    "localization_results",
    "llm_localization_audit",
    "dynamic_source_item_status",
    "proper_noun_review",
    "browser_validation",
    "private_artifacts",
    "public_redaction",
    "safety",
    "artifact_lifecycle",
}

SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:\\[^\s\"'<>|]+"),
    re.compile(r"\\\\[^\\\s\"'<>|]+\\[^\\\s\"'<>|]+"),
    re.compile(r"(?i)\bfile://[^\s\"'<>]+"),
    re.compile(r"(?i)(sk-[A-Za-z0-9_-]{4,}|ghp_[A-Za-z0-9_]{4,}|github_pat_[A-Za-z0-9_]{4,}|Bearer\s+[A-Za-z0-9._-]{4,})"),
)


class S2BlockedError(RuntimeError):
    """Raised when an S2 structural gate blocks execution."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=json_default) + "\n")
            count += 1
    return count


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=json_default) + "\n")


def git_value(args: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def build_head_evidence(*, validated_run_head_sha: str | None = None) -> dict[str, Any]:
    """Split runtime execution SHA from report-generation and PR-handoff SHA."""

    report_generation_head = git_value(["rev-parse", "HEAD"])
    status_short = git_value(["status", "--short"])
    validated = (validated_run_head_sha or report_generation_head).strip()
    return {
        "validated_run_head_sha": validated,
        "validated_run_head_sha_scope": "production baseline execution/import/classification/AI/localization head; later report-only commits may differ",
        "report_generation_head_sha": report_generation_head,
        "report_generation_head_sha_scope": "git HEAD used while generating this committed public report",
        "current_pr_head_sha": "reported by PR metadata/final delivery after the report refresh commit",
        "current_pr_head_sha_scope": "a committed artifact cannot truthfully contain its own final commit SHA",
        "top_level_head_sha_omitted": True,
        "working_tree_had_uncommitted_changes_at_report_generation": bool(status_short.strip()),
    }


def safe_bool(value: Any) -> bool:
    return bool(value)


def env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"true", "1", "yes", "on"}


def output_dir_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
        allowed_root = DEFAULT_OUTPUT_DIR.resolve()
        resolved.relative_to(allowed_root)
    except ValueError:
        return False
    protected_names = {"media", "data", "backup", "backups"}
    if any(part.lower() in protected_names for part in resolved.parts):
        return False
    return True


def prepare_private_output_dir(args: argparse.Namespace) -> Path:
    output_dir = args.output_dir.resolve()
    if not output_dir_allowed(output_dir):
        raise S2BlockedError("unsafe_output_dir_must_be_phase_private_artifact_dir")
    root_inputs, _root_input_source = phase_source_root_inputs(args)
    for raw_root in root_inputs:
        try:
            overlaps_source_root = paths_overlap(output_dir, Path(raw_root))
        except Exception:
            overlaps_source_root = False
        if overlaps_source_root:
            raise S2BlockedError("unsafe_output_dir_overlaps_source_root")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def is_root_like(path: Path) -> bool:
    resolved = path.resolve()
    if resolved.parent == resolved:
        return True
    anchor = Path(resolved.anchor) if resolved.anchor else None
    return bool(anchor and resolved == anchor)


def schema_snapshot(conn: Any) -> dict[str, Any]:
    inspector = inspect(conn)
    present = [table for table in DYNAMIC_SYNC_TABLES if inspector.has_table(table)]
    missing = [table for table in DYNAMIC_SYNC_TABLES if table not in present]
    indexes: dict[str, list[str]] = {}
    for table in present:
        try:
            indexes[table] = sorted(index.get("name", "") for index in inspector.get_indexes(table) if index.get("name"))
        except Exception:
            indexes[table] = []
    return {
        "tables_present": present,
        "tables_missing": missing,
        "indexes_present": indexes,
        "expected_index_prefixes": list(DYNAMIC_SYNC_INDEX_PREFIXES),
    }


def backup_operator_instructions(expected_db_name: str) -> list[str]:
    return [
        "Create a production PostgreSQL backup before schema setup.",
        f"Recommended command: pg_dump --format=custom --dbname={expected_db_name} --file <private-backup-dir>/<timestamp>-{expected_db_name}.dump",
        "Verify the dump command exits 0 and keep the dump path private.",
        "Record a private JSON proof with dump filename, created_at, database name, command exit code, and recovery notes.",
        "Rerun this S2 runner with --backup-proof-path <private-proof.json> --recovery-notes <operator recovery note>.",
    ]


def _proof_database_name(proof: Mapping[str, Any]) -> str:
    for key in ("expected_database", "database", "db_name"):
        value = str(proof.get(key) or "").strip()
        if value:
            return value
    return ""


def _proof_exit_code(proof: Mapping[str, Any]) -> int | None:
    for key in ("pg_dump_exit_code", "backup_exit_code", "exit_code"):
        if key in proof:
            try:
                return int(proof.get(key))
            except (TypeError, ValueError):
                return None
    return None


def _proof_dump_path(proof: Mapping[str, Any]) -> Path | None:
    for key in ("dump_file", "dump_path", "backup_file"):
        value = str(proof.get(key) or "").strip()
        if value:
            return Path(value).expanduser().resolve()
    return None


def backup_proof_status(args: argparse.Namespace, actual_db_name: str | None = None) -> dict[str, Any]:
    proof_path = Path(args.backup_proof_path).resolve() if args.backup_proof_path else None
    proof_exists = bool(proof_path and proof_path.exists())
    validation_errors: list[str] = []
    proof_data: dict[str, Any] = {}
    dump_path: Path | None = None
    dump_file_exists = False
    dump_file_non_empty: bool | None = None
    proof_database = ""
    exit_code: int | None = None

    if not proof_path:
        validation_errors.append("backup_proof_path_missing")
    elif not proof_exists:
        validation_errors.append("backup_proof_file_missing")
    else:
        try:
            proof_data = json.loads(proof_path.read_text(encoding="utf-8"))
            if not isinstance(proof_data, dict):
                validation_errors.append("backup_proof_not_json_object")
                proof_data = {}
        except Exception:
            validation_errors.append("backup_proof_json_unreadable")
            proof_data = {}

    if proof_data:
        proof_database = _proof_database_name(proof_data)
        if proof_database != args.expected_db_name:
            validation_errors.append("backup_proof_database_mismatch_expected")
        if actual_db_name and proof_database != actual_db_name:
            validation_errors.append("backup_proof_database_mismatch_actual")

        exit_code = _proof_exit_code(proof_data)
        if exit_code != 0:
            validation_errors.append("backup_command_exit_code_not_zero")

        dump_path = _proof_dump_path(proof_data)
        if not dump_path:
            validation_errors.append("backup_dump_path_missing")
        else:
            dump_file_exists = dump_path.exists()
            if not dump_file_exists:
                validation_errors.append("backup_dump_file_missing")
            else:
                try:
                    dump_file_non_empty = dump_path.stat().st_size > 0
                    if not dump_file_non_empty:
                        validation_errors.append("backup_dump_file_empty")
                except OSError:
                    dump_file_non_empty = None

        if not str(proof_data.get("created_at") or "").strip():
            validation_errors.append("backup_proof_created_at_missing")
        if not str(proof_data.get("recovery_notes") or "").strip():
            validation_errors.append("backup_recovery_notes_missing")

    valid = bool(proof_exists and not validation_errors)
    return {
        "proof_supplied": bool(proof_path),
        "proof_exists": proof_exists,
        "valid": valid,
        "validation_error_codes": sorted(set(validation_errors)),
        "expected_database_matches": proof_database == args.expected_db_name if proof_database else False,
        "actual_database_matches": proof_database == actual_db_name if actual_db_name and proof_database else False,
        "backup_command_exit_code_zero": exit_code == 0,
        "dump_file_exists": dump_file_exists,
        "dump_file_non_empty": dump_file_non_empty,
        "created_at_present": bool(proof_data.get("created_at")) if proof_data else False,
        "recovery_path_documented": bool(proof_data.get("recovery_notes")) if proof_data else False,
        "path_redacted": True,
        "private_path": str(proof_path) if proof_path else None,
        "private_dump_path": str(dump_path) if dump_path else None,
        "operator_instructions": backup_operator_instructions(args.expected_db_name) if not valid else [],
    }


def create_backup_proof(args: argparse.Namespace, actual_db_name: str | None = None) -> dict[str, Any]:
    """Optionally run pg_dump and return private proof metadata."""
    from app.config import settings

    if not args.create_backup_proof:
        return backup_proof_status(args, actual_db_name=actual_db_name)
    if args.backup_proof_path and Path(args.backup_proof_path).exists():
        return backup_proof_status(args, actual_db_name=actual_db_name)
    if not args.backup_output_dir:
        raise S2BlockedError("backup_output_dir_required_for_create_backup_proof")
    output_dir = Path(args.backup_output_dir).resolve()
    if not output_dir_allowed(output_dir):
        raise S2BlockedError("backup_output_dir_must_be_phase_private_artifact_dir")
    output_dir.mkdir(parents=True, exist_ok=True)

    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        return {
            **backup_proof_status(args, actual_db_name=actual_db_name),
            "create_backup_attempted": True,
            "created": False,
            "failure_reason": "pg_dump_not_found",
            "operator_instructions": backup_operator_instructions(args.expected_db_name),
        }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = output_dir / f"{timestamp}-{args.expected_db_name}.dump"
    proof_path = output_dir / f"{timestamp}-{args.expected_db_name}-backup-proof.json"
    env = os.environ.copy()
    if settings.DATABASE_URL.password:
        env["PGPASSWORD"] = settings.DATABASE_URL.password
    cmd = [
        pg_dump,
        "--format=custom",
        "--host",
        settings.DATABASE_URL.host or "localhost",
        "--port",
        str(settings.DATABASE_URL.port or 5432),
        "--username",
        settings.DATABASE_URL.username or "postgres",
        "--dbname",
        settings.DATABASE_URL.database or args.expected_db_name,
        "--file",
        str(dump_path),
    ]
    completed = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    proof = {
        "created_at": utc_now(),
        "expected_database": args.expected_db_name,
        "actual_database": actual_db_name or settings.DATABASE_URL.database or args.expected_db_name,
        "database": args.expected_db_name,
        "dump_file": str(dump_path),
        "dump_file_exists": dump_path.exists(),
        "dump_file_size_bytes": dump_path.stat().st_size if dump_path.exists() else 0,
        "pg_dump_exit_code": completed.returncode,
        "pg_dump_stdout_present": bool(completed.stdout.strip()),
        "pg_dump_stderr_present": bool(completed.stderr.strip()),
        "command_redacted": "pg_dump --format=custom --host <redacted> --port <redacted> --username <redacted> --dbname <redacted> --file <private>",
        "recovery_notes": args.recovery_notes,
    }
    write_json(proof_path, proof)
    if completed.returncode != 0 or not dump_path.exists():
        args.backup_proof_path = proof_path
        return {
            **backup_proof_status(args, actual_db_name=actual_db_name),
            "create_backup_attempted": True,
            "created": False,
            "failure_reason": "pg_dump_failed",
            "operator_instructions": backup_operator_instructions(args.expected_db_name),
        }
    args.backup_proof_path = proof_path
    validated_backup = backup_proof_status(args, actual_db_name=actual_db_name or settings.DATABASE_URL.database)
    return {
        **validated_backup,
        "create_backup_attempted": True,
        "created": bool(validated_backup.get("valid")),
        "path_redacted": True,
    }


def ensure_dynamic_sync_schema(engine: Any, before: Mapping[str, Any]) -> dict[str, Any]:
    """Run the existing S1 additive dynamic sync migration."""
    from app import database

    before_missing = list(before.get("tables_missing") or [])
    with engine.connect() as conn:
        base_tables = set(inspect(conn).get_table_names())
        if "blombooru_media" not in base_tables:
            return {
                "status": "blocked",
                "ran": False,
                "path_used": "migrate_add_dynamic_library_sync_tables",
                "tables_missing_before": before_missing,
                "tables_missing_after": before_missing,
                "blocker": "base_media_table_missing",
                "additive_only": True,
                "destructive_operations": [],
            }
    database.migrate_add_dynamic_library_sync_tables(engine, inspect(engine))
    with engine.connect() as conn:
        after = schema_snapshot(conn)
    return {
        "status": "completed" if not after["tables_missing"] else "failed",
        "ran": True,
        "path_used": "migrate_add_dynamic_library_sync_tables",
        "tables_missing_before": before_missing,
        "tables_present_before": list(before.get("tables_present") or []),
        "tables_missing_after": after["tables_missing"],
        "tables_present_after": after["tables_present"],
        "indexes_present_after": after["indexes_present"],
        "additive_only": True,
        "destructive_operations": [],
        "production_media_source_mutation": False,
        "import_classification_ai_localization_executed": False,
    }


def validate_phase_source_root(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    from app.config import settings

    issues: list[str] = []
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        issues.append("source_root_not_absolute")
        resolved = candidate
    else:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
            issues.append("source_root_resolve_failed")
    if not resolved.exists():
        issues.append("source_root_missing")
    elif not resolved.is_dir():
        issues.append("source_root_not_directory")
    if candidate.is_absolute() and resolved.exists() and is_root_like(resolved):
        issues.append("source_root_is_filesystem_root")
    protected_paths = (
        ("repo_code_root", settings.CODE_ROOT),
        ("app_storage_root", settings.STORAGE_ROOT),
        ("runner_output_dir", Path(args.output_dir)),
    )
    for label, protected in protected_paths:
        if candidate.is_absolute() and paths_overlap(resolved, protected):
            issues.append(f"source_root_overlaps_{label}")
    test_storage = os.getenv("VIOLET_TEST_STORAGE_ROOT", "").strip()
    if test_storage and candidate.is_absolute() and paths_overlap(resolved, Path(test_storage)):
        issues.append("source_root_overlaps_test_storage_root")
    return {
        "valid": not issues,
        "issues": issues,
        "label": "",
        "path_redacted": True,
        "private_path": str(resolved) if candidate.is_absolute() else str(candidate),
    }


def phase_source_root_inputs(args: argparse.Namespace) -> tuple[list[str], str]:
    if args.source_root:
        return [str(item) for item in args.source_root], "cli"
    try:
        from app.config import settings

        configured = [str(path) for path in settings.LOCAL_LIBRARY_PATHS]
    except Exception:
        configured = []
    return configured, "LOCAL_LIBRARY_PATHS" if configured else "none"


def register_phase_source_roots(args: argparse.Namespace) -> dict[str, Any]:
    root_inputs, input_source = phase_source_root_inputs(args)
    if not root_inputs:
        return {
            "requested": False,
            "input_source": input_source,
            "registered_count": 0,
            "validated_count": 0,
            "failed_count": 0,
            "roots": [],
            "instructions": [
                "Register at least one active dynamic source root with --register-source-root --source-root <path> --source-label <label>, or via the Admin UI/API.",
                "Re-run readiness after registration; public reports expose only aggregate counts and run-local opaque labels.",
            ],
        }

    validations = [validate_phase_source_root(Path(raw), args) for raw in root_inputs]
    invalid = [item for item in validations if not item["valid"]]
    if invalid or not args.register_source_root:
        return {
            "requested": True,
            "input_source": input_source,
            "registration_requested": bool(args.register_source_root),
            "registered_count": 0,
            "validated_count": sum(1 for item in validations if item["valid"]),
            "failed_count": len(invalid),
            "roots": [
                {
                    "run_local_label": f"root-{index + 1}",
                    "label": args.source_label[index] if args.source_label and index < len(args.source_label) else "",
                    "valid": item["valid"],
                    "issues": item["issues"],
                    "path_redacted": True,
                }
                for index, item in enumerate(validations)
            ],
            "instructions": [] if invalid else ["Pass --register-source-root to register validated roots."],
        }

    from app import database
    from app.models import DynamicSourceRoot
    from app.services.dynamic_library_sync_service import register_source_root

    database.init_engine()
    db = database.SessionLocal()
    registered: list[dict[str, Any]] = []
    deactivated_count = 0
    deactivated_labels: list[str] = []
    try:
        for index, item in enumerate(validations):
            label = args.source_label[index] if args.source_label and index < len(args.source_label) else f"phase47-s2-root-{index + 1}"
            root = register_source_root(
                db,
                path=item["private_path"],
                label=label,
                notes=f"Registered by {PHASE} readiness runner {args.run_id}",
            )
            root.auto_sync_enabled = False
            root.is_active = True
            db.commit()
            db.refresh(root)
            registered.append(
                {
                    "id": root.id,
                    "run_local_label": f"root-{index + 1}",
                    "label": root.label,
                    "is_active": bool(root.is_active),
                    "auto_sync_enabled": bool(root.auto_sync_enabled),
                    "path_redacted": True,
                }
            )
        if args.replace_source_roots:
            registered_ids = {int(root["id"]) for root in registered}
            stale_roots = (
                db.query(DynamicSourceRoot)
                .filter(DynamicSourceRoot.is_active == True)
                .filter(~DynamicSourceRoot.id.in_(registered_ids))
                .order_by(DynamicSourceRoot.id.asc())
                .all()
            )
            for stale_root in stale_roots:
                stale_root.is_active = False
                stale_root.auto_sync_enabled = False
                deactivated_count += 1
                deactivated_labels.append(f"inactive-root-{deactivated_count}")
            if stale_roots:
                db.commit()
    finally:
        db.close()
    return {
        "requested": True,
        "input_source": input_source,
        "registration_requested": True,
        "replace_source_roots": bool(args.replace_source_roots),
        "registered_count": len(registered),
        "validated_count": len(validations),
        "deactivated_other_active_count": deactivated_count,
        "deactivated_other_active_labels": deactivated_labels,
        "failed_count": 0,
        "roots": registered,
        "instructions": [],
    }


def run_gate0_preparation(args: argparse.Namespace) -> dict[str, Any]:
    from app.config import settings

    blockers: list[str] = []
    warnings: list[str] = []
    schema_before = {"tables_present": [], "tables_missing": list(DYNAMIC_SYNC_TABLES), "indexes_present": {}}
    schema_after = schema_before
    schema_ensure = {
        "status": "not_needed",
        "ran": False,
        "approved": bool(args.approve_schema_setup),
        "path_used": None,
        "tables_missing_before": list(DYNAMIC_SYNC_TABLES),
        "tables_missing_after": list(DYNAMIC_SYNC_TABLES),
        "additive_only": True,
        "destructive_operations": [],
    }
    db_identity: dict[str, Any] = {}
    actual_db_name: str | None = None
    storage_identity = {
        "explicitly_set": settings.STORAGE_ROOT_EXPLICITLY_SET,
        "matches_expected": not args.expected_storage_root
        or settings.STORAGE_ROOT.resolve() == Path(args.expected_storage_root).resolve(),
        "paths_redacted": True,
    }

    if settings.VIOLET_ENV != "production":
        blockers.append("VIOLET_ENV_not_production")
    if not settings.STORAGE_ROOT_EXPLICITLY_SET:
        blockers.append("VIOLET_STORAGE_ROOT_not_explicitly_set")
    if args.expected_storage_root and not storage_identity["matches_expected"]:
        blockers.append("production_storage_root_mismatch")

    engine = create_engine(settings.DATABASE_URL)
    try:
        with engine.connect() as conn:
            connected_database = conn.execute(text("SELECT current_database()")).scalar()
            actual_db_name = str(connected_database or "")
            db_identity = safe_db_identity(settings.DATABASE_URL, connected_database=actual_db_name)
            db_identity["violet_env"] = settings.VIOLET_ENV
            if actual_db_name != args.expected_db_name:
                blockers.append("production_db_name_mismatch")
            schema_before = schema_snapshot(conn)
            schema_after = schema_before
    except Exception as exc:
        blockers.append("production_db_identity_query_failed")
        db_identity = {"error_type": type(exc).__name__, "password_value_recorded": False}

    db_identity_blocked = "production_db_identity_query_failed" in blockers or "production_db_name_mismatch" in blockers
    if args.create_backup_proof and db_identity_blocked:
        warnings.append("backup_creation_skipped_until_db_identity_clean")
        backup = backup_proof_status(args, actual_db_name=actual_db_name)
    else:
        backup = create_backup_proof(args, actual_db_name=actual_db_name)
    backup_public = {key: value for key, value in backup.items() if key not in {"private_path", "private_dump_path"}}
    source_root_write_requested = bool(args.register_source_root or args.replace_source_roots)
    if source_root_write_requested and not backup.get("valid"):
        blockers.append("source_root_write_requires_valid_backup_proof")
        if not backup.get("proof_exists"):
            blockers.append("backup_recovery_proof_missing")
        else:
            blockers.append("backup_recovery_proof_invalid")

    if schema_before.get("tables_missing"):
        if not backup.get("valid"):
            blockers.extend(["dynamic_sync_tables_missing", "schema_setup_requires_valid_backup_proof"])
            if not backup.get("proof_exists"):
                blockers.append("backup_recovery_proof_missing")
            else:
                blockers.append("backup_recovery_proof_invalid")
            schema_ensure["status"] = "blocked_backup_required"
        elif not args.approve_schema_setup:
            blockers.extend(["dynamic_sync_tables_missing", "schema_setup_approval_missing"])
            schema_ensure["status"] = "blocked_schema_approval_required"
        elif "production_db_identity_query_failed" not in blockers and "production_db_name_mismatch" not in blockers:
            schema_ensure = ensure_dynamic_sync_schema(engine, schema_before)
            if schema_ensure.get("tables_missing_after"):
                blockers.append("dynamic_sync_schema_ensure_failed")
            with engine.connect() as conn:
                schema_after = schema_snapshot(conn)
        else:
            schema_ensure["status"] = "blocked_db_identity"
    else:
        schema_ensure["tables_missing_before"] = []
        schema_ensure["tables_missing_after"] = []

    source_registration = {
        "requested": False,
        "registered_count": 0,
        "validated_count": 0,
        "failed_count": 0,
        "roots": [],
    }
    source_registration_gate_blockers = {
        "VIOLET_ENV_not_production",
        "VIOLET_STORAGE_ROOT_not_explicitly_set",
        "production_storage_root_mismatch",
        "production_db_identity_query_failed",
        "production_db_name_mismatch",
        "dynamic_sync_schema_ensure_failed",
        "source_root_write_requires_valid_backup_proof",
        "backup_recovery_proof_missing",
        "backup_recovery_proof_invalid",
    }
    root_inputs, _root_input_source = phase_source_root_inputs(args)
    source_registration_allowed = not schema_after.get("tables_missing") and not (
        set(blockers) & source_registration_gate_blockers
    )
    if source_registration_allowed:
        try:
            source_registration = register_phase_source_roots(args)
            if source_registration.get("failed_count"):
                blockers.append("input_root_registration_failed")
        except Exception as exc:
            blockers.append("input_root_registration_failed")
            source_registration = {
                "requested": bool(args.source_root),
                "registration_requested": bool(args.register_source_root),
                "registered_count": 0,
                "validated_count": 0,
                "failed_count": 1,
                "error_type": type(exc).__name__,
                "roots": [],
            }
    elif root_inputs:
        warnings.append("input_root_registration_skipped_until_identity_storage_schema_ready")

    try:
        engine.dispose()
    except Exception:
        pass

    db_identity_public = {key: value for key, value in db_identity.items() if not key.startswith("password")}

    return {
        "status": "passed" if not blockers else "blocked",
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "db_identity": db_identity_public,
        "storage_identity": storage_identity,
        "backup_recovery": backup_public,
        "schema": {
            "before": schema_before,
            "after": schema_after,
            "ensure": schema_ensure,
        },
        "input_root_registration": source_registration,
        "safety": {
            "additive_schema_only": True,
            "drop_truncate_delete_reset": False,
            "production_media_source_mutation": False,
            "import_classification_ai_localization_executed": False,
        },
    }


def public_python_env(expected_python: str | None) -> dict[str, Any]:
    expected = Path(expected_python).resolve() if expected_python else None
    actual = Path(sys.executable).resolve()
    return {
        "expected_python_checked": expected is not None,
        "check_python_env_passed": expected is not None and actual == expected,
        "public_executable_name": actual.name,
        "executable_path_redacted": True,
        "python_version": sys.version.split()[0],
    }


def safe_db_identity(url: Any, connected_database: str | None = None) -> dict[str, Any]:
    return {
        "host": url.host or "localhost",
        "port": int(url.port or 5432),
        "database": str(url.database or ""),
        "connected_database": connected_database,
        "username_present": bool(url.username),
        "password_present": bool(url.password),
        "password_value_recorded": False,
    }


def llm_localization_readiness(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    llm_localization = {
        "operator_approved": bool(args.approve_llm_localization),
        "enabled": env_truthy("TAG_TRANSLATION_LLM_ENABLED"),
        "provider_configured": bool(os.getenv("TAG_TRANSLATION_LLM_PROVIDER", "").strip()),
        "model_configured": bool(os.getenv("TAG_TRANSLATION_LLM_MODEL", "").strip()),
        "base_url_configured": bool(os.getenv("TAG_TRANSLATION_LLM_BASE_URL", "").strip()),
        "api_key_configured": bool(os.getenv("TAG_TRANSLATION_LLM_API_KEY", "").strip()),
        "auto_enabled": env_truthy("TAG_TRANSLATION_AUTO_ENABLED"),
        "background_enabled": env_truthy("TAG_TRANSLATION_BACKGROUND_ENABLED"),
        "provider": os.getenv("TAG_TRANSLATION_LLM_PROVIDER", ""),
        "secrets_recorded": False,
    }
    blockers: list[str] = []
    if not args.approve_llm_localization:
        blockers.append("llm_localization_operator_approval_missing")
        return llm_localization, blockers

    llm_requirements = {
        "TAG_TRANSLATION_LLM_ENABLED_false": llm_localization["enabled"],
        "TAG_TRANSLATION_LLM_PROVIDER_missing": llm_localization["provider_configured"],
        "TAG_TRANSLATION_LLM_MODEL_missing": llm_localization["model_configured"],
        "TAG_TRANSLATION_LLM_BASE_URL_missing": llm_localization["base_url_configured"],
        "TAG_TRANSLATION_LLM_API_KEY_missing": llm_localization["api_key_configured"],
        "tag_translation_execution_path_not_configured": llm_localization["auto_enabled"]
        or llm_localization["background_enabled"],
    }
    blockers.extend(blocker for blocker, ok in llm_requirements.items() if not ok)
    return llm_localization, blockers


def paths_overlap(left: Path, right: Path) -> bool:
    try:
        left_resolved = left.resolve()
        right_resolved = right.resolve()
    except OSError:
        return False
    try:
        left_resolved.relative_to(right_resolved)
        return True
    except ValueError:
        pass
    try:
        right_resolved.relative_to(left_resolved)
        return True
    except ValueError:
        return False


def collect_readiness(args: argparse.Namespace, gate0: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from app.config import settings

    blockers: list[str] = []
    warnings: list[str] = []
    python_env = public_python_env(args.expected_python)
    if not python_env["check_python_env_passed"]:
        blockers.append("python_env_mismatch")

    branch = git_value(["branch", "--show-current"])
    head_sha = git_value(["rev-parse", "HEAD"])
    merge_base = git_value(["merge-base", "HEAD", "origin/main"])
    origin_main = git_value(["rev-parse", "origin/main"])
    if branch != BRANCH:
        blockers.append("wrong_branch")
    if not origin_main or merge_base != origin_main:
        blockers.append("branch_not_based_on_latest_origin_main")
    if settings.VIOLET_ENV != "production":
        blockers.append("VIOLET_ENV_not_production")
    if not settings.STORAGE_ROOT_EXPLICITLY_SET:
        blockers.append("VIOLET_STORAGE_ROOT_not_explicitly_set")
    if args.expected_storage_root:
        expected_storage = Path(args.expected_storage_root).resolve()
        if settings.STORAGE_ROOT.resolve() != expected_storage:
            blockers.append("production_storage_root_mismatch")

    db_identity: dict[str, Any] = {
        "db_resolution": {
            "password_value_recorded": False,
            "runner_matches_app_equivalent": False,
        }
    }
    dynamic_schema = {"tables_present": [], "tables_missing": list(DYNAMIC_SYNC_TABLES)}
    gate0 = gate0 or {}
    blockers.extend(gate0.get("blockers", []) or [])
    warnings.extend(gate0.get("warnings", []) or [])
    source_roots = {"active_count": 0, "registered_count": 0, "valid_count": 0}
    active_jobs = {"classification": 0, "ai_tagging": 0, "translation": 0}
    localization_gap: dict[str, Any] = {}
    try:
        engine = create_engine(settings.DATABASE_URL)
        with engine.connect() as conn:
            connected_database = conn.execute(text("SELECT current_database()")).scalar()
            db_identity = {
                **safe_db_identity(settings.DATABASE_URL, connected_database=str(connected_database or "")),
                "violet_env": settings.VIOLET_ENV,
                "db_resolution": {
                    "password_value_recorded": False,
                    "runner_matches_app_equivalent": True,
                    "urls_match": True,
                    "expected_database": args.expected_db_name,
                },
            }
            if str(connected_database or "") != args.expected_db_name:
                blockers.append("production_db_name_mismatch")
            inspector = inspect(conn)
            present = [table for table in DYNAMIC_SYNC_TABLES if inspector.has_table(table)]
            missing = [table for table in DYNAMIC_SYNC_TABLES if table not in present]
            dynamic_schema = {"tables_present": present, "tables_missing": missing}
            if missing:
                blockers.append("dynamic_sync_tables_missing")
            else:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, root_path, is_active, auto_sync_enabled
                        FROM blombooru_dynamic_source_roots
                        ORDER BY id ASC
                        """
                    )
                ).mappings().all()
                active_rows = [row for row in rows if row["is_active"]]
                valid_count = 0
                unsafe_overlap_count = 0
                for row in active_rows:
                    root_path = Path(str(row["root_path"]))
                    if not root_path.exists() or not root_path.is_dir():
                        continue
                    test_storage = os.getenv("VIOLET_TEST_STORAGE_ROOT", "").strip()
                    if (
                        paths_overlap(root_path, settings.STORAGE_ROOT)
                        or paths_overlap(root_path, settings.CODE_ROOT)
                        or paths_overlap(root_path, Path(args.output_dir))
                        or (test_storage and paths_overlap(root_path, Path(test_storage)))
                    ):
                        unsafe_overlap_count += 1
                        continue
                    valid_count += 1
                source_roots = {
                    "active_count": len(active_rows),
                    "registered_count": len(rows),
                    "valid_count": valid_count,
                    "auto_sync_enabled_count": sum(1 for row in rows if row["auto_sync_enabled"]),
                    "unsafe_overlap_count": unsafe_overlap_count,
                    "paths_redacted": True,
                }
                if not active_rows:
                    blockers.append("no_active_dynamic_input_roots")
                if valid_count != len(active_rows):
                    blockers.append("active_dynamic_input_roots_invalid_or_unsafe")
            for key, table in {
                "classification": "blombooru_classification_jobs",
                "ai_tagging": "blombooru_ai_tag_jobs",
                "translation": "blombooru_tag_translation_jobs",
            }.items():
                if inspector.has_table(table):
                    active_jobs[key] = int(
                        conn.execute(
                            text(f"SELECT COUNT(*) FROM {table} WHERE status IN ('pending', 'running', 'cancelling')")
                        ).scalar()
                        or 0
                    )
    except Exception as exc:
        blockers.append("production_db_readiness_query_failed")
        db_identity["error_type"] = type(exc).__name__
    finally:
        try:
            engine.dispose()  # type: ignore[name-defined]
        except Exception:
            pass

    if any(active_jobs.values()):
        blockers.append("active_background_jobs_present")
    if settings.DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED:
        blockers.append("automatic_production_sync_enabled")
    backup = gate0.get("backup_recovery") or backup_proof_status(args, actual_db_name=db_identity.get("connected_database"))
    if not backup.get("valid"):
        blockers.append("backup_recovery_proof_invalid" if backup.get("proof_exists") else "backup_recovery_proof_missing")

    llm_localization, llm_blockers = llm_localization_readiness(args)
    blockers.extend(llm_blockers)

    ai_model = {"checked": False, "available": False, "model_downloaded": False, "network_download_required": None}
    try:
        from app.services.ai_tagging_service import check_model_status

        model_status = check_model_status()
        ai_model = {
            "checked": True,
            "enabled": bool(model_status.get("enabled")),
            "model_name": model_status.get("model_name"),
            "available": bool(model_status.get("available")),
            "model_downloaded": model_status.get("model_downloaded"),
            "network_download_required": model_status.get("model_downloaded") is not True,
            "thresholds": model_status.get("config"),
        }
        if not ai_model["enabled"]:
            blockers.append("AI_TAGGING_ENABLED_false")
        if not ai_model["available"] or ai_model["model_downloaded"] is not True:
            blockers.append("local_ai_model_not_available_offline")
    except Exception as exc:
        blockers.append("local_ai_model_readiness_failed")
        ai_model = {"checked": True, "available": False, "error_type": type(exc).__name__}

    try:
        from app import database
        from app.services.dynamic_library_sync_service import get_localization_gap_summary
        from app.utils.search_parser import _translation_alias_trusted_for_search

        if not dynamic_schema["tables_missing"]:
            database.init_engine()
            db = database.SessionLocal()
            try:
                localization_gap = get_localization_gap_summary(db)
            finally:
                db.close()
            proper_noun_search_excludes_unreviewed = not _translation_alias_trusted_for_search(
                SimpleNamespace(category="character", source="llm", status="translated", needs_review=True)
            )
            localization_gap["unreviewed_llm_aliases_excluded_from_search"] = proper_noun_search_excludes_unreviewed
            if not localization_gap.get("worker_excludes_proper_nouns", False):
                blockers.append("background_translation_categories_include_proper_nouns")
            if (
                int(localization_gap.get("unreviewed_proper_noun_llm_aliases") or 0) > 0
                and not proper_noun_search_excludes_unreviewed
            ):
                blockers.append("unreviewed_proper_noun_llm_aliases_present")
    except Exception as exc:
        warnings.append(f"localization_gap_query_unavailable:{type(exc).__name__}")

    return {
        "passed": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "python_env": python_env,
        "git": {
            "branch": branch,
            "head_sha": head_sha,
            "origin_main_sha": origin_main,
            "based_on_origin_main": bool(origin_main and merge_base == origin_main),
        },
        "db_identity": db_identity,
        "app_settings_db_identity_matches_execution_db": bool(
            db_identity.get("db_resolution", {}).get("runner_matches_app_equivalent")
        ),
        "production_storage": {
            "explicitly_set": settings.STORAGE_ROOT_EXPLICITLY_SET,
            "label": "app_storage",
            "matches_expected": not args.expected_storage_root
            or settings.STORAGE_ROOT.resolve() == Path(args.expected_storage_root).resolve(),
            "paths_redacted": True,
        },
        "dynamic_schema": dynamic_schema,
        "source_roots": source_roots,
        "active_jobs": active_jobs,
        "gate0": gate0,
        "backup_recovery": backup,
        "ai_model": ai_model,
        "llm_localization": llm_localization,
        "proper_noun_safeguards": {
            "search_alias_trust_policy": "manual_static_or_operator_reviewed_only",
            "worker_excludes_proper_nouns": localization_gap.get("worker_excludes_proper_nouns"),
            "unreviewed_proper_noun_llm_aliases": localization_gap.get("unreviewed_proper_noun_llm_aliases"),
            "unreviewed_llm_aliases_excluded_from_search": localization_gap.get(
                "unreviewed_llm_aliases_excluded_from_search"
            ),
            "entity_truth_created": False,
        },
        "automatic_production_sync": {
            "enabled": env_truthy("DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED"),
            "remains_opt_in": not env_truthy("DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED"),
        },
    }


def blocked_stage(name: str, reason: str = "not_run_gate1") -> dict[str, Any]:
    return {
        "stage": name,
        "status": reason,
        "executed": False,
        "target_met": False,
        "item_failures_recorded": False,
    }


def unresolved_cloud_deferred_row(row: Mapping[str, Any], *, execution_present: bool) -> bool:
    state = str(row.get("state") or row.get("item_state") or row.get("status") or "").strip()
    reason = str(row.get("reason") or row.get("source_state") or row.get("deferred_reason") or "").strip()
    if state in {"hydrated_success", "imported", "reused_existing"}:
        return False
    if state in {
        "hydration_failed",
        "read_timeout",
        "cloud_network_unavailable",
        "cloud_hydration_failed",
        "permission_denied",
        "unreadable_source",
        "deferred_retryable",
    }:
        return True
    if reason in CLOUD_FAILURE_REASONS:
        return True
    if not execution_present and reason in HYDRATION_WORKLOAD_REASONS:
        return True
    return False


def filter_cloud_deferred_rows(rows: Iterable[Mapping[str, Any]], *, execution_present: bool) -> list[Mapping[str, Any]]:
    return [row for row in rows if unresolved_cloud_deferred_row(row, execution_present=execution_present)]


def llm_localization_audit_from_rows(
    *,
    localization_results: Mapping[str, Any],
    execution_ledgers: Mapping[str, Any],
    observed_background: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    localization_rows = list(execution_ledgers.get("localization_rows") or [])
    translated_in_ledger = sum(int(row.get("translated") or 0) for row in localization_rows)
    failed_in_ledger = sum(int(row.get("failed") or 0) for row in localization_rows)
    background = dict(observed_background or {})
    background_observed = bool(background)
    return {
        "status": "provider_calls_audited",
        "dedicated_localization_ledger_rows": len(localization_rows),
        "dedicated_localization_provider_batch_calls": len(localization_rows),
        "dedicated_localization_translated": translated_in_ledger,
        "dedicated_localization_failed": failed_in_ledger,
        "dedicated_localization_llm_called": bool(localization_results.get("llm_called")),
        "background_auto_translation_observed": background_observed,
        "background_auto_translation_calls": int(background.get("translate_call_count") or 0),
        "background_auto_translation_saved": int(background.get("saved_translation_count") or 0),
        "background_auto_translation_already_translated_or_static": int(
            background.get("already_translated_or_static_count") or 0
        ),
        "background_provider_call_accounting": "historical_log_lower_bound" if background_observed else "not_observed",
        "background_provider_calls_ledgered": not background_observed,
        "current_runner_suppresses_auto_translation_during_ai_stage": True,
        "current_runner_llm_localization_path": "explicit_localization_stage_ledgers_only",
        "provider_call_count_lower_bound": len(localization_rows) + int(background.get("translate_call_count") or 0),
        "translated_tag_units_recorded": translated_in_ledger + int(background.get("saved_translation_count") or 0),
        "provider_calls_undercounted": False,
        "paths_redacted": True,
    }


def summarize_dynamic_sync_dry_run(raw: Mapping[str, Any], args: argparse.Namespace, readiness: Mapping[str, Any]) -> dict[str, Any]:
    summary = raw.get("summary") or {}
    pending = raw.get("pending_summary") or summary.get("pending_summary") or {}
    root_summaries = []
    reason_counts: Counter[str] = Counter()
    for index, root in enumerate(summary.get("root_summaries", []) or []):
        root_summaries.append(
            {
                "run_local_root_label": f"root-{index + 1}",
                "partial_scan": bool(root.get("partial_scan")),
                "missing_reconciliation_skipped": bool(root.get("missing_reconciliation_skipped")),
                "missing_reconciliation_reason": root.get("missing_reconciliation_reason"),
                "source_walk_error_count": root.get("source_walk_error_count"),
                "counts": root.get("counts") or {},
                "paths_redacted": True,
            }
        )
    # Reason-level counts are optional because the S1 service exposes only
    # aggregate run serialization; future import ledgers retain item-level detail.
    unsupported = sum(count for reason, count in reason_counts.items() if reason in UNSUPPORTED_REASONS)
    cloud_only = sum(count for reason, count in reason_counts.items() if reason in CLOUD_DEFERRED_REASONS)
    pending_import = int(raw.get("pending_import_items") or pending.get("pending_import") or 0)
    batch_size = max(int(args.estimated_batch_size or 1), 1)
    localization_gap = readiness.get("proper_noun_safeguards") or {}
    return {
        "stage": "dynamic_sync_dry_run",
        "status": "completed" if raw.get("status") == "completed" else str(raw.get("status") or "unknown"),
        "executed": True,
        "target_met": False,
        "dry_run": bool(raw.get("dry_run")),
        "run_id": raw.get("id"),
        "roots_checked": raw.get("roots_checked"),
        "total_seen": int(raw.get("total_seen") or 0),
        "pending_new": int(pending.get("pending_new") or raw.get("new_items") or 0),
        "pending_changed": int(pending.get("pending_changed") or raw.get("changed_items") or 0),
        "pending_deferred": int(pending.get("pending_deferred") or raw.get("deferred_items") or 0),
        "unsupported": unsupported,
        "failed": int(raw.get("failed_items") or 0),
        "missing": int(raw.get("missing_items") or 0),
        "cloud_only_or_icloud_unavailable": cloud_only,
        "hydration_workload_count": cloud_only,
        "actual_cloud_failure_count": 0,
        "duplicates_checked": False,
        "duplicates": None,
        "threshold_reached": bool(raw.get("threshold_reached")),
        "estimated_import_batches": int(math.ceil(pending_import / batch_size)) if pending_import else 0,
        "estimated_ai_tagging_workload": pending_import,
        "estimated_localization_workload": None,
        "proper_noun_gap_needs_review": localization_gap.get("unreviewed_proper_noun_llm_aliases"),
        "root_summaries": root_summaries,
        "reason_counts": dict(reason_counts),
        "paths_redacted": True,
        "item_failures_recorded": True,
        "no_import_copy_ai_or_llm": True,
    }


def dry_run_scope_check(args: argparse.Namespace, dry_run: Mapping[str, Any]) -> dict[str, Any]:
    total_seen = int(dry_run.get("total_seen") or 0)
    expected_min = max(int(args.expected_source_min_items or 0), 0)
    passed = expected_min <= 0 or total_seen >= expected_min
    return {
        "passed": passed,
        "status": "passed" if passed else "source_scope_mismatch",
        "expected_min_items": expected_min,
        "total_seen": total_seen,
        "expected_scale": "tens_of_thousands_roughly_30k_plus",
    }


def dry_run_hydration_workload_check(args: argparse.Namespace, dry_run: Mapping[str, Any]) -> dict[str, Any]:
    total_seen = int(dry_run.get("total_seen") or 0)
    workload_count = int(dry_run.get("hydration_workload_count") or dry_run.get("cloud_only_or_icloud_unavailable") or 0)
    failure_count = int(dry_run.get("actual_cloud_failure_count") or 0)
    rate = (workload_count / total_seen) if total_seen else 0.0
    return {
        "passed": True,
        "status": "hydration_workload_recorded",
        "hydration_workload_count": workload_count,
        "actual_failure_count": failure_count,
        "total_seen": total_seen,
        "hydration_workload_rate": rate,
        "counts_as_failure": False,
        "failure_threshold_applies_after_attempt": True,
    }


def export_dynamic_sync_dry_run_ledgers(db: Any, run_id: int | None) -> dict[str, Any]:
    if not run_id:
        return {
            "unsupported_or_deferred_rows": [],
            "cloud_deferred_rows": [],
            "batch_summary_rows": [],
            "exported_run_id": None,
            "paths_private": True,
        }
    rows = db.execute(
        text(
            """
            SELECT
                ri.id AS run_item_id,
                ri.item_state AS item_state,
                ri.action AS action,
                ri.reason AS reason,
                ri.eligible_for_db_import AS eligible_for_db_import,
                ri.bytes_copied AS bytes_copied,
                si.id AS source_item_id,
                si.import_status AS import_status,
                si.classification_status AS classification_status,
                si.ai_tagging_status AS ai_tagging_status,
                si.localization_status AS localization_status,
                si.failure_reason AS failure_reason,
                si.deferred_reason AS deferred_reason,
                si.file_size AS file_size
            FROM blombooru_dynamic_sync_run_items ri
            JOIN blombooru_dynamic_source_items si ON si.id = ri.source_item_id
            WHERE ri.sync_run_id = :run_id
            ORDER BY ri.id ASC
            """
        ),
        {"run_id": run_id},
    ).mappings().all()
    unsupported_or_deferred_rows: list[dict[str, Any]] = []
    cloud_deferred_rows: list[dict[str, Any]] = []
    summary_counter: Counter[str] = Counter()
    for index, row in enumerate(rows, start=1):
        reason = str(row["reason"] or row["deferred_reason"] or row["failure_reason"] or row["item_state"] or "")
        state = str(row["item_state"] or "")
        summary_counter[reason or state or "unknown"] += 1
        ledger_row = {
            "run_item_label": f"run-item-{index}",
            "source_item_label": f"source-item-{row['source_item_id']}",
            "item_state": state,
            "action": row["action"],
            "reason": reason,
            "import_status": row["import_status"],
            "classification_status": row["classification_status"],
            "ai_tagging_status": row["ai_tagging_status"],
            "localization_status": row["localization_status"],
            "eligible_for_db_import": bool(row["eligible_for_db_import"]),
            "bytes_copied": int(row["bytes_copied"] or 0),
            "file_size": row["file_size"],
            "path_private_or_omitted": True,
        }
        if reason in UNSUPPORTED_REASONS or reason in CLOUD_DEFERRED_REASONS or state in {"deferred", "failed", "missing"}:
            unsupported_or_deferred_rows.append(ledger_row)
        if reason in CLOUD_DEFERRED_REASONS:
            cloud_deferred_rows.append(ledger_row)
    batch_summary_rows = [
        {
            "stage": "dynamic_sync_dry_run",
            "run_id": run_id,
            "reason": reason,
            "count": count,
            "paths_private_or_omitted": True,
        }
        for reason, count in sorted(summary_counter.items())
    ]
    return {
        "unsupported_or_deferred_rows": unsupported_or_deferred_rows,
        "cloud_deferred_rows": cloud_deferred_rows,
        "batch_summary_rows": batch_summary_rows,
        "exported_run_id": run_id,
        "paths_private": True,
    }


def run_fresh_dynamic_sync_dry_run(args: argparse.Namespace, readiness: Mapping[str, Any]) -> dict[str, Any]:
    from app import database
    from app.services.dynamic_library_sync_service import run_update_check

    database.init_engine()
    db = database.SessionLocal()
    try:
        raw = run_update_check(
            db,
            max_files=args.dynamic_sync_max_files,
            hydrated_only=True,
        )
        dry_run = summarize_dynamic_sync_dry_run(raw, args, readiness)
        if raw.get("id"):
            rows = db.execute(
                text(
                    """
                    SELECT COALESCE(reason, item_state) AS reason, COUNT(*) AS count
                    FROM blombooru_dynamic_sync_run_items
                    WHERE sync_run_id = :run_id
                    GROUP BY COALESCE(reason, item_state)
                    """
                ),
                {"run_id": raw["id"]},
            ).mappings().all()
            reason_counts = {str(row["reason"]): int(row["count"]) for row in rows}
            dry_run["reason_counts"] = reason_counts
            dry_run["unsupported"] = sum(count for reason, count in reason_counts.items() if reason in UNSUPPORTED_REASONS)
            dry_run["cloud_only_or_icloud_unavailable"] = sum(
                count for reason, count in reason_counts.items() if reason in CLOUD_DEFERRED_REASONS
            )
            dry_run["hydration_workload_count"] = sum(
                count for reason, count in reason_counts.items() if reason in HYDRATION_WORKLOAD_REASONS
            )
            dry_run["actual_cloud_failure_count"] = sum(
                count for reason, count in reason_counts.items() if reason in CLOUD_FAILURE_REASONS
            )
            dry_run["private_ledgers"] = export_dynamic_sync_dry_run_ledgers(db, int(raw["id"]))
        else:
            dry_run["private_ledgers"] = export_dynamic_sync_dry_run_ledgers(db, None)
        dry_run["source_scope_check"] = dry_run_scope_check(args, dry_run)
        dry_run["hydration_workload_check"] = dry_run_hydration_workload_check(args, dry_run)
        return dry_run
    finally:
        db.close()


def failure_threshold_exceeded(failures: int, attempted: int, *, max_items: int, max_rate: float) -> bool:
    if max_items >= 0 and failures > max_items:
        return True
    if attempted > 0 and max_rate >= 0 and (failures / attempted) > max_rate:
        return True
    return False


def failure_threshold_payload(failures: int, attempted: int, *, max_items: int, max_rate: float) -> dict[str, Any]:
    rate = (failures / attempted) if attempted else 0.0
    return {
        "attempted": attempted,
        "failed": failures,
        "failure_rate": rate,
        "max_items": max_items,
        "max_rate": max_rate,
        "threshold_exceeded": failure_threshold_exceeded(
            failures,
            attempted,
            max_items=max_items,
            max_rate=max_rate,
        ),
    }


def unsupported_kind_for_suffix(suffix: str, reason: str | None = None) -> str:
    lowered = suffix.casefold()
    if reason == "hidden":
        return "hidden_metadata"
    if lowered in SIDECAR_EXTENSIONS:
        return "sidecar_or_metadata"
    if lowered in DESIRED_UNSUPPORTED_MEDIA_EXTENSIONS:
        return "desired_media_support_gap"
    if not lowered:
        return "no_extension"
    return "unsupported_non_media_or_unknown"


def source_item_safe_label(source_item_id: int) -> str:
    return f"source-item-{source_item_id}"


def media_safe_label(media_id: int | None) -> str | None:
    return f"media-{media_id}" if media_id else None


def _resolve_source_item_path(root_path: Path, relative_path: str) -> Path:
    candidate = (root_path / relative_path).resolve()
    root_resolved = root_path.resolve()
    candidate.relative_to(root_resolved)
    return candidate


def query_latest_source_items(db: Any, dry_run: Mapping[str, Any]) -> tuple[list[Any], dict[int, Path]]:
    from app.models import DynamicSourceItem, DynamicSourceRoot

    latest_run_id = dry_run.get("run_id")
    roots = (
        db.query(DynamicSourceRoot)
        .filter(DynamicSourceRoot.is_active == True)
        .order_by(DynamicSourceRoot.id.asc())
        .all()
    )
    root_paths = {root.id: Path(root.root_path).resolve() for root in roots}
    query = (
        db.query(DynamicSourceItem)
        .filter(DynamicSourceItem.source_root_id.in_(root_paths.keys()))
        .order_by(DynamicSourceItem.id.asc())
    )
    if latest_run_id:
        query = query.filter(DynamicSourceItem.last_seen_run_id == int(latest_run_id))
    return query.all(), root_paths


def ensure_import_disk_safety(items: Sequence[Any], root_paths: Mapping[int, Path]) -> dict[str, Any]:
    from app.config import settings
    from app.utils.local_library_scanner import SUPPORTED_EXTENSIONS

    known_candidate_bytes = 0
    candidate_count = 0
    for item in items:
        suffix = Path(item.relative_path).suffix.casefold()
        reason = item.deferred_reason or item.failure_reason
        if suffix not in SUPPORTED_EXTENSIONS:
            continue
        if reason and reason not in HYDRATION_WORKLOAD_REASONS:
            continue
        candidate_count += 1
        known_candidate_bytes += int(item.file_size or 0)
    usage = shutil.disk_usage(settings.ORIGINAL_DIR)
    required_with_buffer = int(known_candidate_bytes * 1.05)
    passed = required_with_buffer <= usage.free
    return {
        "passed": passed,
        "candidate_count": candidate_count,
        "known_candidate_bytes": known_candidate_bytes,
        "free_bytes": usage.free,
        "required_with_buffer_bytes": required_with_buffer,
        "paths_redacted": True,
    }


def mark_source_item_import_outcome(
    item: Any,
    *,
    state: str,
    media_id: int | None = None,
    content_hash: str | None = None,
    failure_reason: str | None = None,
    bytes_copied: int = 0,
) -> None:
    now = datetime.now(timezone.utc)
    if content_hash:
        item.content_hash = content_hash
    if media_id:
        item.media_id = media_id
    item.last_checked_at = now
    if state in {"imported", "reused_existing"}:
        item.import_status = "imported"
        item.source_status = "available"
        item.failure_reason = None
        item.deferred_reason = None
        item.last_imported_at = now
        if item.classification_status in {"waiting_import", "deferred", "failed"}:
            item.classification_status = "pending"
        if item.ai_tagging_status in {"waiting_import", "deferred", "failed"}:
            item.ai_tagging_status = "pending"
        if item.localization_status in {"waiting_ai_tags", "deferred", "failed"}:
            item.localization_status = "waiting_ai_tags"
    elif state in {"unsupported", "unsupported_sidecar", "unsupported_desired_media", "deferred_retryable"}:
        item.import_status = "deferred"
        item.source_status = "deferred"
        item.deferred_reason = state
        item.failure_reason = None
        item.classification_status = "deferred"
        item.ai_tagging_status = "deferred"
        item.localization_status = "deferred"
    else:
        item.import_status = "failed" if state in {"copy_failed", "import_failed"} else "deferred"
        item.source_status = "failed"
        item.failure_reason = failure_reason or state
        item.deferred_reason = None
        item.classification_status = "deferred"
        item.ai_tagging_status = "deferred"
        item.localization_status = "deferred"
    metadata = dict(item.metadata_json or {})
    metadata["phase47_s2_last_state"] = state
    metadata["phase47_s2_bytes_copied"] = bytes_copied
    item.metadata_json = metadata


def commit_source_item_import_outcome(
    db: Any,
    item: Any,
    *,
    state: str,
    media_id: int | None = None,
    content_hash: str | None = None,
    bytes_copied: int = 0,
    failure_reason: str | None = None,
) -> None:
    mark_source_item_import_outcome(
        item,
        state=state,
        media_id=media_id,
        content_hash=content_hash,
        bytes_copied=bytes_copied,
        failure_reason=failure_reason,
    )
    db.commit()


def run_controlled_import(args: argparse.Namespace, dry_run: Mapping[str, Any]) -> dict[str, Any]:
    from app import database
    from app.config import settings
    from app.models import Media
    from app.routes.media import process_and_save_media
    from app.schemas import RatingEnum
    from app.utils.local_library_scanner import (
        SUPPORTED_EXTENSIONS,
        _calculate_file_hash_with_timeout,
    )
    from app.utils.media_helpers import get_unique_filename
    from app.utils.media_processor import calculate_file_hash

    database.init_engine()
    db = database.SessionLocal()
    import_rows: list[dict[str, Any]] = []
    unsupported_rows: list[dict[str, Any]] = []
    hydration_rows: list[dict[str, Any]] = []
    batch_rows: list[dict[str, Any]] = []
    media_work_items: list[dict[str, Any]] = []
    unsupported_breakdown: Counter[str] = Counter()
    unsupported_extension_breakdown: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    bytes_copied_total = 0
    hydration_workload_attempted = 0
    read_attempted = 0
    hydration_failures = 0
    import_attempted = 0
    import_failures = 0
    stopped_by_rule: str | None = None
    checkpoint_dir = prepare_private_output_dir(args)
    checkpoint_files = (
        "hydration-ledger.jsonl",
        "import-item-ledger.jsonl",
        "unsupported-or-deferred.jsonl",
        "cloud-deferred.jsonl",
        "batch-summary.jsonl",
        "classification-ledger.jsonl",
        "ai-tagging-ledger.jsonl",
        "localization-ledger.jsonl",
    )
    for ledger_name in checkpoint_files:
        write_jsonl(checkpoint_dir / ledger_name, [])

    try:
        items, root_paths = query_latest_source_items(db, dry_run)
        disk_safety = ensure_import_disk_safety(items, root_paths)
        if not disk_safety["passed"]:
            return {
                "stage": "import",
                "status": "blocked_disk_space_resource_safety",
                "executed": False,
                "target_met": False,
                "item_failures_recorded": True,
                "disk_safety": disk_safety,
                "attempted": 0,
                "imported": 0,
                "reused_existing": 0,
                "failed": 0,
                "unsupported": 0,
                "stopped_by_rule": "disk_space_resource_safety",
                "private_ledgers": {
                    "import_item_rows": [],
                    "unsupported_or_deferred_rows": [],
                    "cloud_deferred_rows": [],
                    "batch_summary_rows": [],
                },
            }

        batch_size = max(int(args.import_batch_size or 1), 1)
        max_items = max(int(args.max_import_items or 0), 0)
        processed_candidates = 0

        def checkpoint_batch_if_needed(force: bool = False) -> None:
            if processed_candidates <= 0:
                return
            last_processed = int(batch_rows[-1].get("processed_candidates") or 0) if batch_rows else 0
            if not force and processed_candidates % batch_size != 0:
                return
            if last_processed == processed_candidates:
                return
            batch_row = {
                "stage": "import",
                "batch_index": len(batch_rows) + 1,
                "processed_candidates": processed_candidates,
                "status_counts": dict(status_counts),
                "bytes_copied": bytes_copied_total,
                "paths_private_or_omitted": True,
            }
            batch_rows.append(batch_row)
            append_jsonl(checkpoint_dir / "batch-summary.jsonl", batch_row)

        for item in items:
            if max_items and processed_candidates >= max_items:
                stopped_by_rule = "max_import_items_reached"
                break
            suffix = Path(item.relative_path).suffix.casefold()
            reason = item.deferred_reason or item.failure_reason
            safe_item = source_item_safe_label(item.id)
            if item.import_status == "imported" and item.media_id:
                media_work_items.append(
                    {
                        "source_item_id": item.id,
                        "source_item_label": safe_item,
                        "media_id": item.media_id,
                        "reused_existing": True,
                    }
                )
                continue
            if suffix not in SUPPORTED_EXTENSIONS or (reason and reason not in HYDRATION_WORKLOAD_REASONS):
                kind = unsupported_kind_for_suffix(suffix, str(reason or "unsupported_extension"))
                if reason in CLOUD_FAILURE_REASONS:
                    state = "deferred_retryable"
                elif kind == "desired_media_support_gap":
                    state = "unsupported_desired_media"
                else:
                    state = "unsupported_sidecar"
                unsupported_breakdown[kind] += 1
                unsupported_extension_breakdown[suffix or "<none>"] += 1
                status_counts[state] += 1
                commit_source_item_import_outcome(
                    db,
                    item,
                    state=state,
                    failure_reason=reason or "unsupported_extension",
                )
                row = {
                    "run_id": args.run_id,
                    "source_item_label": safe_item,
                    "state": state,
                    "reason": reason or "unsupported_extension",
                    "unsupported_kind": kind,
                    "extension": suffix or "<none>",
                    "eligible_for_db_import": False,
                    "path_private_or_omitted": True,
                }
                unsupported_rows.append(row)
                import_rows.append(row)
                append_jsonl(checkpoint_dir / "unsupported-or-deferred.jsonl", row)
                append_jsonl(checkpoint_dir / "import-item-ledger.jsonl", row)
                continue

            processed_candidates += 1
            root_path = root_paths.get(item.source_root_id)
            source_state = str(reason or "available")
            is_hydration_workload = source_state in HYDRATION_WORKLOAD_REASONS
            if is_hydration_workload:
                hydration_workload_attempted += 1

            row: dict[str, Any] = {
                "run_id": args.run_id,
                "source_item_label": safe_item,
                "state": "attempted",
                "source_state": source_state,
                "extension": suffix,
                "media_label": None,
                "content_hash_available": False,
                "bytes_copied": 0,
                "path_private_or_omitted": True,
            }
            temp_path: Path | None = None
            final_path: Path | None = None
            copy_completed = False
            media_created = False
            try:
                if root_path is None:
                    raise FileNotFoundError("source_root_missing_for_item")
                source_path = _resolve_source_item_path(root_path, item.relative_path)
                timeout_sec = int(args.hydration_timeout_seconds or settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS)
                read_attempted += 1
                hash_status, hash_value = _calculate_file_hash_with_timeout(source_path, timeout_sec)
                if hash_status == "timeout":
                    state = "read_timeout"
                    hydration_failures += 1
                    commit_source_item_import_outcome(db, item, state=state, failure_reason=hash_value)
                    row.update({"state": state, "reason": hash_value})
                    status_counts[state] += 1
                    import_rows.append(row)
                    hydration_rows.append(row)
                    append_jsonl(checkpoint_dir / "import-item-ledger.jsonl", row)
                    append_jsonl(checkpoint_dir / "hydration-ledger.jsonl", row)
                    append_jsonl(checkpoint_dir / "cloud-deferred.jsonl", row)
                    if failure_threshold_exceeded(
                        hydration_failures,
                        max(hydration_workload_attempted, 1),
                        max_items=args.hydration_failure_max_items,
                        max_rate=args.hydration_failure_max_rate,
                    ):
                        stopped_by_rule = "hydration_read_failure_threshold_exceeded"
                        break
                    checkpoint_batch_if_needed()
                    continue
                if hash_status != "ok":
                    state = "hydration_failed" if is_hydration_workload else "unreadable_source"
                    hydration_failures += 1
                    commit_source_item_import_outcome(db, item, state=state, failure_reason=hash_value)
                    row.update({"state": state, "reason": hash_value})
                    status_counts[state] += 1
                    import_rows.append(row)
                    hydration_rows.append(row)
                    append_jsonl(checkpoint_dir / "import-item-ledger.jsonl", row)
                    append_jsonl(checkpoint_dir / "hydration-ledger.jsonl", row)
                    append_jsonl(checkpoint_dir / "cloud-deferred.jsonl", row)
                    if failure_threshold_exceeded(
                        hydration_failures,
                        max(hydration_workload_attempted, 1),
                        max_items=args.hydration_failure_max_items,
                        max_rate=args.hydration_failure_max_rate,
                    ):
                        stopped_by_rule = "hydration_read_failure_threshold_exceeded"
                        break
                    checkpoint_batch_if_needed()
                    continue

                source_hash = str(hash_value)
                row["content_hash_available"] = True
                existing = db.query(Media).filter(Media.hash == source_hash).first()
                if existing:
                    state = "reused_existing"
                    commit_source_item_import_outcome(
                        db,
                        item,
                        state=state,
                        media_id=existing.id,
                        content_hash=source_hash,
                    )
                    row.update({"state": state, "media_label": media_safe_label(existing.id), "reason": "content_hash_match"})
                    status_counts[state] += 1
                    media_work_items.append(
                        {
                            "source_item_id": item.id,
                            "source_item_label": safe_item,
                            "media_id": existing.id,
                            "reused_existing": True,
                        }
                    )
                    import_rows.append(row)
                    append_jsonl(checkpoint_dir / "import-item-ledger.jsonl", row)
                    if is_hydration_workload:
                        hydration_row = {**row, "state": "hydrated_success"}
                        hydration_rows.append(hydration_row)
                        append_jsonl(checkpoint_dir / "hydration-ledger.jsonl", hydration_row)
                    checkpoint_batch_if_needed()
                    continue

                import_attempted += 1
                unique_name = get_unique_filename(settings.ORIGINAL_DIR, source_path.name)
                final_path = settings.ORIGINAL_DIR / unique_name
                temp_path = settings.ORIGINAL_DIR / f".{PHASE_SLUG}-{args.run_id}-{item.id}-{uuid.uuid4().hex}.tmp"
                shutil.copy2(str(source_path), str(temp_path))
                temp_hash = calculate_file_hash(temp_path)
                if temp_hash != source_hash:
                    raise IOError("copy_hash_mismatch")
                bytes_copied = int(temp_path.stat().st_size)
                temp_path.replace(final_path)
                temp_path = None
                copy_completed = True
                media_resp = process_and_save_media(
                    db=db,
                    file_path=final_path,
                    unique_filename=unique_name,
                    rating=RatingEnum.safe,
                    tags="",
                    album_ids=None,
                    source=None,
                    category_hints=None,
                )
                media_created = True
                media_id = int(media_resp.id)
                mark_source_item_import_outcome(
                    item,
                    state="imported",
                    media_id=media_id,
                    content_hash=source_hash,
                    bytes_copied=bytes_copied,
                )
                db.commit()
                final_path = None
                bytes_copied_total += bytes_copied
                row.update({"state": "imported", "media_label": media_safe_label(media_id), "bytes_copied": bytes_copied})
                status_counts["imported"] += 1
                media_work_items.append(
                    {
                        "source_item_id": item.id,
                        "source_item_label": safe_item,
                        "media_id": media_id,
                        "reused_existing": False,
                    }
                )
                import_rows.append(row)
                append_jsonl(checkpoint_dir / "import-item-ledger.jsonl", row)
                if is_hydration_workload:
                    hydration_row = {**row, "state": "hydrated_success"}
                    hydration_rows.append(hydration_row)
                    append_jsonl(checkpoint_dir / "hydration-ledger.jsonl", hydration_row)

            except Exception as exc:
                try:
                    db.rollback()
                except Exception:
                    pass
                if temp_path and temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                if final_path and final_path.exists() and not media_created:
                    try:
                        final_path.unlink()
                    except OSError:
                        pass
                message = str(getattr(exc, "detail", exc))[:500]
                state = "import_failed" if copy_completed or media_created else "copy_failed"
                import_failures += 1
                mark_source_item_import_outcome(item, state=state, failure_reason=message)
                db.commit()
                row.update({"state": state, "reason": message})
                status_counts[state] += 1
                import_rows.append(row)
                append_jsonl(checkpoint_dir / "import-item-ledger.jsonl", row)
                if is_hydration_workload:
                    hydration_rows.append(row)
                    append_jsonl(checkpoint_dir / "hydration-ledger.jsonl", row)
                if failure_threshold_exceeded(
                    import_failures,
                    max(import_attempted, 1),
                    max_items=args.import_failure_max_items,
                    max_rate=args.import_failure_max_rate,
                ):
                    stopped_by_rule = "import_failure_threshold_exceeded"
                    break

            checkpoint_batch_if_needed()

        db.commit()
        checkpoint_batch_if_needed(force=True)

        hydration_budget = failure_threshold_payload(
            hydration_failures,
            hydration_workload_attempted,
            max_items=args.hydration_failure_max_items,
            max_rate=args.hydration_failure_max_rate,
        )
        import_budget = failure_threshold_payload(
            import_failures,
            import_attempted,
            max_items=args.import_failure_max_items,
            max_rate=args.import_failure_max_rate,
        )
        if stopped_by_rule == "hydration_read_failure_threshold_exceeded":
            status = "hydration_read_failure_threshold_exceeded"
        elif stopped_by_rule == "import_failure_threshold_exceeded":
            status = "import_failure_threshold_exceeded"
        elif stopped_by_rule == "max_import_items_reached":
            status = "partial_import_max_items_reached"
        elif import_failures or hydration_failures:
            status = "completed_with_item_failures_within_budget"
        else:
            status = "completed"
        return {
            "stage": "import",
            "status": status,
            "executed": True,
            "target_met": status in {"completed", "completed_with_item_failures_within_budget"},
            "item_failures_recorded": True,
            "per_item_ledgers_written": True,
            "attempted": processed_candidates,
            "read_attempted": read_attempted,
            "hydration_attempted": hydration_workload_attempted,
            "hydrated_success": sum(1 for row in hydration_rows if row.get("state") == "hydrated_success"),
            "hydration_failures": hydration_failures,
            "import_attempted": import_attempted,
            "imported": int(status_counts.get("imported", 0)),
            "reused_existing": int(status_counts.get("reused_existing", 0)),
            "failed": import_failures,
            "unsupported": int(status_counts.get("unsupported_sidecar", 0)) + int(status_counts.get("unsupported_desired_media", 0)),
            "unsupported_sidecar": int(status_counts.get("unsupported_sidecar", 0)),
            "unsupported_desired_media": int(status_counts.get("unsupported_desired_media", 0)),
            "bytes_copied": bytes_copied_total,
            "unsupported_breakdown": dict(unsupported_breakdown),
            "unsupported_extension_breakdown": dict(unsupported_extension_breakdown),
            "hydration_failure_budget": hydration_budget,
            "import_failure_budget": import_budget,
            "disk_safety": disk_safety,
            "stopped_by_rule": stopped_by_rule,
            "media_work_items": media_work_items,
            "private_ledgers": {
                "import_item_rows": import_rows,
                "hydration_rows": hydration_rows,
                "unsupported_or_deferred_rows": unsupported_rows,
                "cloud_deferred_rows": filter_cloud_deferred_rows(hydration_rows, execution_present=True),
                "batch_summary_rows": batch_rows,
            },
        }
    finally:
        db.close()


@contextmanager
def suppress_auto_translation_during_ai_stage() -> Iterable[list[dict[str, Any]]]:
    from app.services import tag_localization_service

    original = tag_localization_service.schedule_auto_translate
    suppressed_calls: list[dict[str, Any]] = []

    def _suppressed_schedule_auto_translate(tag_names: Sequence[str], lang: str = "zh-CN") -> None:
        suppressed_calls.append(
            {
                "tag_count": len(list(tag_names or [])),
                "language": lang,
                "provider_call_prevented": True,
            }
        )

    tag_localization_service.schedule_auto_translate = _suppressed_schedule_auto_translate
    try:
        yield suppressed_calls
    finally:
        tag_localization_service.schedule_auto_translate = original


def run_classification_and_ai(args: argparse.Namespace, import_results: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from app import database
    from app.enums import ContentClassEnum
    from app.models import DynamicSourceItem, Media, blombooru_media_tags
    from app.services.ai_tagging_service import run_ai_tagging
    from app.services.content_classifier import _classify_heuristic_from_db, classify_media

    database.init_engine()
    db = database.SessionLocal()
    checkpoint_dir = prepare_private_output_dir(args)
    classification_rows: list[dict[str, Any]] = []
    ai_rows: list[dict[str, Any]] = []
    classification_counts: Counter[str] = Counter()
    ai_counts: Counter[str] = Counter()
    ai_attempted = 0
    ai_failures = 0
    stopped_by_rule: str | None = None
    work_items = list(import_results.get("media_work_items") or [])
    max_ai_items = max(int(args.max_ai_items or 0), 0)

    def classify_without_network_download(media: Any, source_item: Any | None, source_item_label: str) -> None:
        if getattr(media, "content_class_locked", False):
            classification_counts["skipped_locked"] += 1
            if source_item:
                source_item.classification_status = "classified_reused" if media.content_class is not None else "deferred"
            row = {
                "source_item_label": source_item_label,
                "media_label": media_safe_label(media.id),
                "state": "classification_skipped_locked",
                "content_class": media.content_class.value if hasattr(media.content_class, "value") else str(media.content_class),
                "reason": "content_class_locked",
                "path_private_or_omitted": True,
            }
            classification_rows.append(row)
            append_jsonl(checkpoint_dir / "classification-ledger.jsonl", row)
            return
        if media.content_class is not None:
            state = "reused_existing" if source_item and source_item.media_id == media.id else "already_classified"
            classification_counts[state] += 1
            if source_item:
                source_item.classification_status = "classified_reused" if state == "reused_existing" else "classified"
            row = {
                "source_item_label": source_item_label,
                "media_label": media_safe_label(media.id),
                "state": state,
                "content_class": media.content_class.value if hasattr(media.content_class, "value") else str(media.content_class),
                "path_private_or_omitted": True,
            }
            classification_rows.append(row)
            append_jsonl(checkpoint_dir / "classification-ledger.jsonl", row)
            return
        try:
            # Avoid CLIP model download in the S2 production runner. WD tags are local and already
            # available at this point, so the heuristic classifier gives a retryable local signal.
            if os.getenv("CONTENT_CLASSIFICATION_METHOD", "").casefold() == "heuristic":
                result = classify_media(db, media.id, dry_run=False)
                if result.get("error"):
                    raise RuntimeError(result["error"])
                content_class = result.get("content_class") or result.get("current_class")
                method = result.get("method", "heuristic")
            else:
                heuristic = _classify_heuristic_from_db(db, media.id)
                enum_value = heuristic["content_class"]
                if not isinstance(enum_value, ContentClassEnum):
                    enum_value = ContentClassEnum(str(enum_value))
                media.content_class = enum_value
                media.content_class_confidence = heuristic["confidence"]
                media.content_class_source = "heuristic"
                media.content_class_model = "wd_tag_count_phase47_s2_no_clip_download"
                db.commit()
                content_class = enum_value.value
                method = "heuristic_after_ai_no_clip_download"
            classification_counts["classified"] += 1
            if source_item:
                source_item.classification_status = "classified"
            row = {
                "source_item_label": source_item_label,
                "media_label": media_safe_label(media.id),
                "state": "classified",
                "content_class": content_class,
                "method": method,
                "path_private_or_omitted": True,
            }
            classification_rows.append(row)
            append_jsonl(checkpoint_dir / "classification-ledger.jsonl", row)
        except Exception as exc:
            try:
                db.rollback()
            except Exception:
                pass
            classification_counts["failed"] += 1
            if source_item:
                source_item.classification_status = "failed"
                source_item.failure_reason = str(exc)[:255]
            row = {
                "source_item_label": source_item_label,
                "media_label": media_safe_label(media.id),
                "state": "classification_failed",
                "reason": str(exc)[:500],
                "path_private_or_omitted": True,
            }
            classification_rows.append(row)
            append_jsonl(checkpoint_dir / "classification-ledger.jsonl", row)

    try:
        with suppress_auto_translation_during_ai_stage() as suppressed_auto_translate_calls:
            for work in work_items:
                source_item = db.query(DynamicSourceItem).filter(DynamicSourceItem.id == work["source_item_id"]).first()
                media = db.query(Media).filter(Media.id == work["media_id"]).first()
                if not media:
                    classification_counts["failed"] += 1
                    ai_counts["failed"] += 1
                    row = {
                        "source_item_label": work["source_item_label"],
                        "state": "classification_failed",
                        "reason": "media_missing",
                        "path_private_or_omitted": True,
                    }
                    classification_rows.append(row)
                    append_jsonl(checkpoint_dir / "classification-ledger.jsonl", row)
                    ai_row = {
                        "source_item_label": work["source_item_label"],
                        "state": "ai_failed",
                        "reason": "media_missing",
                        "path_private_or_omitted": True,
                    }
                    ai_rows.append(ai_row)
                    append_jsonl(checkpoint_dir / "ai-tagging-ledger.jsonl", ai_row)
                    if source_item:
                        source_item.classification_status = "failed"
                        source_item.ai_tagging_status = "failed"
                    db.commit()
                    continue

                existing_ai_count = (
                    db.query(blombooru_media_tags)
                    .filter(
                        blombooru_media_tags.c.media_id == media.id,
                        blombooru_media_tags.c.source == "ai_wd",
                    )
                    .count()
                )
                if existing_ai_count:
                    ai_counts["ai_reused"] += 1
                    if source_item:
                        source_item.ai_tagging_status = "tagged_reused"
                        source_item.localization_status = "pending"
                    ai_row = {
                        "source_item_label": work["source_item_label"],
                        "media_label": media_safe_label(media.id),
                        "state": "ai_reused",
                        "existing_ai_tags": existing_ai_count,
                        "path_private_or_omitted": True,
                    }
                    ai_rows.append(ai_row)
                    append_jsonl(checkpoint_dir / "ai-tagging-ledger.jsonl", ai_row)
                    db.commit()
                else:
                    if max_ai_items and ai_attempted >= max_ai_items:
                        stopped_by_rule = "max_ai_items_reached"
                        break
                    ai_attempted += 1
                    try:
                        result = run_ai_tagging(db, media.id, dry_run=False)
                        if result.get("error"):
                            raise RuntimeError(result["error"])
                        ai_counts["ai_tagged"] += 1
                        if source_item:
                            source_item.ai_tagging_status = "tagged"
                            source_item.localization_status = "pending"
                        ai_row = {
                            "source_item_label": work["source_item_label"],
                            "media_label": media_safe_label(media.id),
                            "state": "ai_tagged",
                            "tags_added": result.get("tags_added", 0),
                            "suggestions_added": result.get("suggestions_added", 0),
                            "skipped_locked": result.get("skipped_locked", 0),
                            "path_private_or_omitted": True,
                        }
                        ai_rows.append(ai_row)
                        append_jsonl(checkpoint_dir / "ai-tagging-ledger.jsonl", ai_row)
                    except Exception as exc:
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        ai_failures += 1
                        ai_counts["ai_failed"] += 1
                        if source_item:
                            source_item.ai_tagging_status = "failed"
                            source_item.failure_reason = str(exc)[:255]
                        ai_row = {
                            "source_item_label": work["source_item_label"],
                            "media_label": media_safe_label(media.id),
                            "state": "ai_failed",
                            "reason": str(exc)[:500],
                            "path_private_or_omitted": True,
                        }
                        ai_rows.append(ai_row)
                        append_jsonl(checkpoint_dir / "ai-tagging-ledger.jsonl", ai_row)
                        if failure_threshold_exceeded(
                            ai_failures,
                            ai_attempted,
                            max_items=args.ai_failure_max_items,
                            max_rate=args.ai_failure_max_rate,
                        ):
                            stopped_by_rule = "ai_failure_threshold_exceeded"
                            break
                    db.commit()

                classify_without_network_download(media, source_item, work["source_item_label"])
                db.commit()

        classification_failures = int(classification_counts.get("failed", 0))
        classification_budget = failure_threshold_payload(
            classification_failures,
            len(work_items),
            max_items=args.classification_failure_max_items,
            max_rate=args.classification_failure_max_rate,
        )
        ai_budget = failure_threshold_payload(
            ai_failures,
            ai_attempted,
            max_items=args.ai_failure_max_items,
            max_rate=args.ai_failure_max_rate,
        )
        if classification_budget["threshold_exceeded"]:
            classification_status = "classification_failure_threshold_exceeded"
        else:
            classification_status = "completed" if not classification_failures else "completed_with_item_failures_within_budget"
        if stopped_by_rule == "ai_failure_threshold_exceeded":
            ai_status = "ai_failure_threshold_exceeded"
        elif stopped_by_rule == "max_ai_items_reached":
            ai_status = "partial_ai_max_items_reached"
        elif ai_failures:
            ai_status = "completed_with_item_failures_within_budget"
        else:
            ai_status = "completed"
        return (
            {
                "stage": "classification",
                "status": classification_status,
                "executed": bool(work_items),
                "target_met": classification_status in {"completed", "completed_with_item_failures_within_budget"},
                "item_failures_recorded": True,
                "attempted": len(work_items),
                "classified": int(classification_counts.get("classified", 0)),
                "reused_existing": int(classification_counts.get("reused_existing", 0)),
                "failed": classification_failures,
                "failure_budget": classification_budget,
                "status_counts": dict(classification_counts),
            },
            {
                "stage": "ai_tagging",
                "status": ai_status,
                "executed": bool(work_items),
                "target_met": ai_status in {"completed", "completed_with_item_failures_within_budget"},
                "item_failures_recorded": True,
                "attempted": ai_attempted,
                "tagged": int(ai_counts.get("ai_tagged", 0)),
                "reused_existing": int(ai_counts.get("ai_reused", 0)),
                "failed": ai_failures,
                "failure_budget": ai_budget,
                "status_counts": dict(ai_counts),
                "auto_translation_suppressed_during_ai_stage": True,
                "unledgered_background_provider_calls_prevented": True,
                "suppressed_auto_translate_schedule_count": len(suppressed_auto_translate_calls),
                "suppressed_auto_translate_tag_count": sum(
                    int(row.get("tag_count") or 0) for row in suppressed_auto_translate_calls
                ),
                "stopped_by_rule": stopped_by_rule,
                "private_ledgers": {
                    "classification_rows": classification_rows,
                    "ai_tagging_rows": ai_rows,
                },
            },
        )
    finally:
        db.close()


def backfill_dynamic_source_item_localization_status(
    db: Any,
    *,
    localization_stage_status: str = "completed",
    failure_threshold_exceeded: bool = False,
) -> dict[str, Any]:
    from app.models import DynamicSourceItem

    eligible_statuses = {"pending", "waiting_ai_tags"}
    if localization_stage_status == "completed" and not failure_threshold_exceeded:
        target_status = "localized"
        result_status = "backfilled"
    elif failure_threshold_exceeded:
        target_status = "failed"
        result_status = "marked_failed"
    else:
        target_status = "deferred"
        result_status = "marked_deferred_retryable"
    query = db.query(DynamicSourceItem).filter(
        DynamicSourceItem.import_status == "imported",
        DynamicSourceItem.ai_tagging_status.in_(["tagged", "tagged_reused"]),
        DynamicSourceItem.localization_status.in_(sorted(eligible_statuses)),
    )
    before = int(query.count())
    updated = int(query.update({DynamicSourceItem.localization_status: target_status}, synchronize_session=False))
    status_counts = dict(
        db.query(DynamicSourceItem.localization_status, func.count(DynamicSourceItem.id))
        .group_by(DynamicSourceItem.localization_status)
        .all()
    )
    return {
        "status": result_status if updated else "already_current",
        "semantic": "source-item localization_status reflects tag-localization readiness/completion for imported AI-tagged items",
        "target_status": target_status,
        "localization_stage_status": localization_stage_status,
        "failure_threshold_exceeded": bool(failure_threshold_exceeded),
        "eligible_pending_before": before,
        "updated_to_localized": updated if target_status == "localized" else 0,
        "updated_to_deferred_or_failed": updated if target_status != "localized" else 0,
        "status_counts_after": {str(key): int(value) for key, value in status_counts.items()},
        "paths_redacted": True,
    }


def run_tag_localization(args: argparse.Namespace) -> dict[str, Any]:
    from app import database
    from app.models import TagTranslationJob
    from app.services.tag_localization_service import batch_translate_missing_tags, get_translation_stats, list_missing_translations

    database.init_engine()
    db = database.SessionLocal()
    checkpoint_dir = prepare_private_output_dir(args)
    rows: list[dict[str, Any]] = []
    translated = 0
    failed = 0
    skipped = 0
    processed = 0
    stopped_by_rule: str | None = None
    max_tags = max(int(args.localization_max_tags or 0), 0)
    categories = ["general", "meta"]
    try:
        remaining_before = sum(len(list_missing_translations(db, limit=100000, category=category)) for category in categories)
        job = TagTranslationJob(
            status="running",
            source="phase47_s2",
            language="zh-CN",
            category="general,meta",
            batch_size=args.localization_batch_size,
            max_per_run=max_tags or remaining_before,
            remaining_before=remaining_before,
            started_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        for category in categories:
            while True:
                if max_tags and processed >= max_tags:
                    stopped_by_rule = "localization_max_tags_reached"
                    break
                limit = int(args.localization_batch_size or 50)
                if max_tags:
                    limit = min(limit, max_tags - processed)
                if limit <= 0:
                    break
                missing = list_missing_translations(db, limit=limit, category=category)
                if not missing:
                    break
                result = asyncio.run(
                    batch_translate_missing_tags(
                        db,
                        dry_run=False,
                        max_items=limit,
                        category=category,
                    )
                )
                batch_processed = int(result.get("candidates") or 0)
                batch_translated = int(result.get("translated") or 0)
                batch_failed = int(result.get("failed") or 0)
                batch_skipped = int(result.get("skipped") or 0)
                processed += batch_processed
                translated += batch_translated
                failed += batch_failed
                skipped += batch_skipped
                state = "localized" if batch_failed == 0 else "localization_failed"
                row = {
                    "category": category,
                    "state": state,
                    "candidates": batch_processed,
                    "translated": batch_translated,
                    "failed": batch_failed,
                    "skipped": batch_skipped,
                    "errors": result.get("errors", []),
                    "proper_noun_unreviewed_aliases_trusted": False,
                    "path_private_or_omitted": True,
                }
                rows.append(row)
                append_jsonl(checkpoint_dir / "localization-ledger.jsonl", row)
                job.processed = processed
                job.translated = translated
                job.failed = failed
                job.skipped = skipped
                db.commit()
                if batch_processed == 0 or batch_failed >= batch_processed:
                    break
                if failure_threshold_exceeded(
                    failed,
                    max(processed, 1),
                    max_items=args.localization_failure_max_items,
                    max_rate=args.localization_failure_max_rate,
                ):
                    stopped_by_rule = "localization_failure_threshold_exceeded"
                    break
            if stopped_by_rule in {"localization_failure_threshold_exceeded", "localization_max_tags_reached"}:
                break
        stats_after = get_translation_stats(db)
        remaining_after = sum(len(list_missing_translations(db, limit=100000, category=category)) for category in categories)
        job.processed = processed
        job.translated = translated
        job.failed = failed
        job.skipped = skipped
        job.remaining_after = remaining_after
        budget = failure_threshold_payload(
            failed,
            processed,
            max_items=args.localization_failure_max_items,
            max_rate=args.localization_failure_max_rate,
        )
        if stopped_by_rule == "localization_failure_threshold_exceeded":
            status = "localization_failure_threshold_exceeded"
        elif failed:
            status = "completed_with_gap_visible"
        else:
            status = "completed" if remaining_after == 0 else "completed_with_gap_visible"
        job.finished_at = datetime.now(timezone.utc)
        job.status = "completed" if status in {"completed", "completed_with_gap_visible"} else "failed"
        source_item_status = backfill_dynamic_source_item_localization_status(
            db,
            localization_stage_status=status,
            failure_threshold_exceeded=bool(budget.get("threshold_exceeded")),
        )
        db.commit()
        return {
            "stage": "localization",
            "status": status,
            "executed": processed > 0,
            "target_met": status in {"completed", "completed_with_gap_visible"},
            "item_failures_recorded": True,
            "llm_called": processed > 0,
            "translated": translated,
            "failed": failed,
            "skipped": skipped,
            "remaining_missing": remaining_after,
            "remaining_missing_all_categories": stats_after.get("missing"),
            "gap_report_generated": True,
            "proper_noun_unreviewed_aliases_trusted": False,
            "failure_budget": budget,
            "source_item_status_backfill": source_item_status,
            "job_id": job.id,
            "stopped_by_rule": stopped_by_rule,
            "private_ledgers": {"localization_rows": rows},
        }
    finally:
        db.close()


def run_execute_stages(args: argparse.Namespace, readiness: Mapping[str, Any], dry_run: Mapping[str, Any]) -> dict[str, Any]:
    import_results = run_controlled_import(args, dry_run)
    if import_results.get("status") in {
        "blocked_disk_space_resource_safety",
        "hydration_read_failure_threshold_exceeded",
        "import_failure_threshold_exceeded",
    }:
        return {
            "status": import_results["status"],
            "stopped_by_rule": import_results.get("stopped_by_rule") or import_results["status"],
            "import_results": import_results,
            "classification_results": blocked_stage("classification", f"not_run_{import_results['status']}"),
            "ai_tagging_results": blocked_stage("ai_tagging", f"not_run_{import_results['status']}"),
            "localization_results": {
                **blocked_stage("localization", f"not_run_{import_results['status']}"),
                "llm_called": False,
                "proper_noun_unreviewed_aliases_trusted": False,
            },
            "browser_validation": {
                "status": "not_run_before_execute_completion",
                "server_started": False,
                "real_browser_validation_required_after_execute": True,
            },
            "private_ledgers": import_results.get("private_ledgers", {}),
        }

    classification_results, ai_tagging_results = run_classification_and_ai(args, import_results)
    if classification_results.get("status") == "classification_failure_threshold_exceeded":
        return {
            "status": "classification_failure_threshold_exceeded",
            "stopped_by_rule": "classification_failure_threshold_exceeded",
            "import_results": import_results,
            "classification_results": classification_results,
            "ai_tagging_results": ai_tagging_results,
            "localization_results": {
                **blocked_stage("localization", "not_run_classification_failure_threshold_exceeded"),
                "llm_called": False,
                "proper_noun_unreviewed_aliases_trusted": False,
            },
            "browser_validation": {
                "status": "not_run_before_execute_completion",
                "server_started": False,
                "real_browser_validation_required_after_execute": True,
            },
            "private_ledgers": {
                **(import_results.get("private_ledgers") or {}),
                **(ai_tagging_results.get("private_ledgers") or {}),
            },
        }
    if ai_tagging_results.get("status") == "ai_failure_threshold_exceeded":
        return {
            "status": "ai_failure_threshold_exceeded",
            "stopped_by_rule": ai_tagging_results.get("stopped_by_rule"),
            "import_results": import_results,
            "classification_results": classification_results,
            "ai_tagging_results": ai_tagging_results,
            "localization_results": {
                **blocked_stage("localization", "not_run_ai_failure_threshold_exceeded"),
                "llm_called": False,
                "proper_noun_unreviewed_aliases_trusted": False,
            },
            "browser_validation": {
                "status": "not_run_before_execute_completion",
                "server_started": False,
                "real_browser_validation_required_after_execute": True,
            },
            "private_ledgers": {
                **(import_results.get("private_ledgers") or {}),
                **(ai_tagging_results.get("private_ledgers") or {}),
            },
        }

    localization_results = run_tag_localization(args)
    if localization_results.get("status") == "localization_failure_threshold_exceeded":
        status = "localization_failure_threshold_exceeded"
    else:
        status = "browser_validation_pending"
    return {
        "status": status,
        "stopped_by_rule": "browser_validation_not_run_in_runner" if status == "browser_validation_pending" else localization_results.get("stopped_by_rule"),
        "import_results": import_results,
        "classification_results": classification_results,
        "ai_tagging_results": ai_tagging_results,
        "localization_results": localization_results,
        "browser_validation": {
            "status": "not_run_before_manual_browser_validation",
            "server_started": False,
            "real_browser_validation_required_after_execute": True,
        },
        "private_ledgers": {
            **(import_results.get("private_ledgers") or {}),
            **(ai_tagging_results.get("private_ledgers") or {}),
            **(localization_results.get("private_ledgers") or {}),
        },
    }


def public_stage_result(stage: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(stage).items()
        if key not in {"private_ledgers", "media_work_items", "job_id"}
    }


def status_for_state(
    args: argparse.Namespace,
    readiness: Mapping[str, Any],
    dry_run: Mapping[str, Any] | None,
    execution: Mapping[str, Any] | None = None,
) -> str:
    blockers = set(readiness.get("blockers") or [])
    if not readiness.get("passed"):
        if "schema_setup_requires_valid_backup_proof" in blockers:
            return "blocked_schema_backup_required"
        if "schema_setup_approval_missing" in blockers:
            return "blocked_schema_approval_required"
        if "no_active_dynamic_input_roots" in blockers:
            return "blocked_source_roots"
        return "blocked_gate1"
    if dry_run and dry_run.get("executed"):
        source_scope = dry_run.get("source_scope_check") or {}
        if source_scope and not source_scope.get("passed", True):
            return "source_scope_mismatch"
        if execution:
            return str(execution.get("status") or "execute_status_unknown")
        if args.execute:
            return "ready_to_execute"
        return "dry_run_complete_execute_not_requested"
    return "readiness_passed"


def build_summary(
    args: argparse.Namespace,
    readiness: Mapping[str, Any],
    dry_run: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status = status_for_state(args, readiness, dry_run, execution)
    gate0 = readiness.get("gate0") or {}
    stopped_by_rule = None
    if not readiness.get("passed"):
        stopped_by_rule = "Gate 0/Gate 1 readiness proof failed before baseline execution"
    elif status == "source_scope_mismatch":
        stopped_by_rule = "Fresh dynamic sync dry-run total_seen is below the approved full-library production scope"
    elif execution:
        stopped_by_rule = execution.get("stopped_by_rule")
    elif status == "ready_to_execute":
        stopped_by_rule = "Execute requested and readiness/dry-run passed, but execute stage did not run"
    if not readiness.get("passed"):
        post_readiness_stage_reason = "not_run_gate1"
    elif status == "source_scope_mismatch":
        post_readiness_stage_reason = "not_run_source_scope_mismatch"
    elif execution:
        post_readiness_stage_reason = None
    elif args.execute:
        post_readiness_stage_reason = "not_run_execute_stage_not_reached"
    else:
        post_readiness_stage_reason = "not_run_execute_not_requested"
    dry_run_stage = dry_run or blocked_stage(
        "dynamic_sync_dry_run",
        "not_run_readiness_failed" if not readiness.get("passed") else "not_run_yet",
    )
    import_results = public_stage_result(execution.get("import_results") or {}) if execution else blocked_stage("import", post_readiness_stage_reason)
    classification_results = (
        public_stage_result(execution.get("classification_results") or {})
        if execution
        else blocked_stage("classification", post_readiness_stage_reason)
    )
    ai_tagging_results = (
        public_stage_result(execution.get("ai_tagging_results") or {})
        if execution
        else blocked_stage("ai_tagging", post_readiness_stage_reason)
    )
    localization_results = (
        public_stage_result(execution.get("localization_results") or {})
        if execution
        else {
            **blocked_stage("localization", post_readiness_stage_reason),
            "llm_called": False,
            "proper_noun_unreviewed_aliases_trusted": False,
        }
    )
    browser_validation = (
        public_stage_result(execution.get("browser_validation") or {})
        if execution
        else {
            "status": "not_run_before_execute" if readiness.get("passed") else "not_run_gate1",
            "server_started": False,
            "real_browser_validation_required_after_execute": True,
        }
    )
    import_executed = bool(import_results.get("executed"))
    classification_executed = bool(classification_results.get("executed"))
    ai_tagging_executed = bool(ai_tagging_results.get("executed"))
    localization_executed = bool(localization_results.get("executed"))
    llm_called = bool(localization_results.get("llm_called"))
    execution_ledgers = (execution or {}).get("private_ledgers", {})
    llm_audit = llm_localization_audit_from_rows(
        localization_results=localization_results,
        execution_ledgers=execution_ledgers,
        observed_background=(execution or {}).get("background_auto_translation_audit"),
    )
    source_item_localization_status = localization_results.get("source_item_status_backfill") or {
        "status": "not_run_before_localization",
        "semantic": "source-item localization_status is a source-item readiness/completion marker, not the tag-level translation counter",
        "paths_redacted": True,
    }
    summary = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "generated_at": utc_now(),
        "branch": git_value(["branch", "--show-current"]),
        "head_evidence": build_head_evidence(
            validated_run_head_sha=str((readiness.get("git") or {}).get("head_sha") or "")
        ),
        "status": status,
        "mode": "execute_requested" if args.execute else "readiness",
        "pipeline_contract": {
            "contract_id": "phase47_s2_baseline_contract_v1",
            "status": status,
            "claims": {
                "target_met": False,
                "safe_to_merge": False,
                "full_chain_complete": False,
            },
            "gate1_stop_condition": not readiness.get("passed"),
            "gate0_status": gate0.get("status"),
            "fresh_dry_run_completed": bool(dry_run and dry_run.get("status") == "completed"),
            "source_scope_passed": bool((dry_run or {}).get("source_scope_check", {}).get("passed", False)),
            "hydration_workload_recorded": bool((dry_run or {}).get("hydration_workload_check", {}).get("passed", False)),
            "hydration_failure_threshold_not_exceeded": not bool(
                (import_results.get("hydration_failure_budget") or {}).get("threshold_exceeded")
            ),
            "execute_requested": bool(args.execute),
            "execute_confirmation_present": bool(args.execute and args.confirm_execution == CONFIRM_PHRASE),
        },
        "gate0": gate0,
        "readiness": dict(readiness),
        "dynamic_sync_dry_run": dry_run_stage,
        "icloud_cloud_policy": {
            "hydrated_only": True,
            "controlled_hydration_backfill_approved": bool(args.execute),
            "mass_icloud_download": False,
            "cloud_placeholder_counts_as_failure": False,
            "cloud_only_files_deferred": not bool(args.execute),
            "hydrate_and_import_batches": bool(args.execute),
            "structured_reasons": [
                "cloud_offline",
                "cloud_placeholder",
                "cloud_only",
                "recall_required",
                "needs_hydration",
                "cloud_recall_on_open",
                "cloud_recall_on_data_access",
                "cloud_network_unavailable",
                "cloud_hydration_failed",
                "read_timeout",
                "permission_denied",
                "unreadable_source",
            ],
        },
        "import_results": import_results,
        "classification_results": classification_results,
        "ai_tagging_results": ai_tagging_results,
        "localization_results": localization_results,
        "llm_localization_audit": llm_audit,
        "dynamic_source_item_status": {
            "localization_status": source_item_localization_status,
            "inactive_root_pending_excluded_from_active_backlog": True,
        },
        "proper_noun_review": {
            "status": "gap_report_generated" if localization_results.get("gap_report_generated") else ("not_run_before_localization" if readiness.get("passed") else "not_run_gate1"),
            "entity_truth_created": False,
            "confirmed_assignments_created": False,
            "unreviewed_llm_aliases_excluded_from_search": True,
        },
        "browser_validation": browser_validation,
        "private_artifacts": {
            "private_artifact_root_label": f".local_manifests/{PHASE_SLUG}",
            "private_artifact_names": list(PRIVATE_LEDGER_NAMES),
            "private_artifacts_committed": False,
            "paths_public": False,
        },
        "public_redaction": {"passed": False, "checked_payloads": []},
        "safety": {
            "no_push_main": True,
            "no_merge": True,
            "no_source_icloud_mutation": True,
            "no_cleanup_delete_reset_drop_truncate": True,
            "controlled_icloud_hydration_read_approved": bool(args.execute),
            "no_db_import": not import_executed,
            "no_classification": not classification_executed,
            "no_ai_tagging": not ai_tagging_executed,
            "no_llm_call": not llm_called,
            "no_sourceconcept_entity_resolver_similarity": True,
            "stopped_by_rule": stopped_by_rule,
        },
        "execution_private_ledgers": execution_ledgers,
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "scripts/run_phase47_s2_baseline_full_import_ai_localization.py",
                    "classification": "phase-scoped operational runner",
                    "committed": True,
                },
                {
                    "path": "private ledgers under ignored output directory",
                    "classification": "one-off local artifact / ignored output",
                    "committed": False,
                },
                {
                    "path": f"docs/reports/{PHASE_SLUG}.md",
                    "classification": "public report / handoff",
                    "committed": True,
                },
                {
                    "path": f"docs/reports/{PHASE_SLUG}-summary.json",
                    "classification": "public report / handoff",
                    "committed": True,
                },
            ]
        },
    }
    missing = sorted(REQUIRED_PUBLIC_FIELDS - set(summary))
    if missing:
        raise S2BlockedError("summary_schema_missing:" + ",".join(missing))
    return summary


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    readiness = summary["readiness"]
    gate0 = summary.get("gate0") or {}
    schema_ensure = (gate0.get("schema") or {}).get("ensure") or {}
    source_registration = gate0.get("input_root_registration") or {}
    dry_run = summary.get("dynamic_sync_dry_run") or {}
    import_results = summary.get("import_results") or {}
    classification_results = summary.get("classification_results") or {}
    ai_results = summary.get("ai_tagging_results") or {}
    localization_results = summary.get("localization_results") or {}
    llm_audit = summary.get("llm_localization_audit") or {}
    source_item_status = (summary.get("dynamic_source_item_status") or {}).get("localization_status") or {}
    browser_validation = summary.get("browser_validation") or {}
    head_evidence = summary.get("head_evidence") or {}
    readiness_git = readiness.get("git") or {}
    readiness_head = readiness_git.get("validated_run_head_sha") or readiness_git.get("head_sha")
    root_counts = readiness.get("source_roots") or readiness.get("input_root_counts") or {}
    desired_media_gap = summary.get("desired_media_support_gap") or {}
    claims = (summary.get("pipeline_contract") or {}).get("claims") or {}
    target_met_claim = bool(claims.get("target_met"))
    safe_to_merge_claim = bool(claims.get("safe_to_merge"))
    lines = [
        f"# {PHASE} {PHASE_TITLE}",
        "",
        "## Summary",
        f"- Status: `{summary['status']}`.",
        f"- Gate 0 status: `{gate0.get('status')}`.",
        f"- Gate 1 passed: `{readiness.get('passed')}`.",
        f"- Blockers: `{json.dumps(readiness.get('blockers', []), ensure_ascii=False)}`.",
        f"- Schema ensure ran: `{schema_ensure.get('ran')}`.",
        f"- Backup proof supplied/existing/valid: `{readiness.get('backup_recovery', {}).get('proof_supplied')}` / `{readiness.get('backup_recovery', {}).get('proof_exists')}` / `{readiness.get('backup_recovery', {}).get('valid')}`.",
        f"- Source roots registered/valid: `{root_counts.get('registered_count')}` / `{root_counts.get('valid_count')}`.",
        f"- Fresh dynamic sync dry-run: `{dry_run.get('status')}`.",
        f"- Source scope check: `{(dry_run.get('source_scope_check') or {}).get('status')}`.",
        f"- Hydration workload: `{(dry_run.get('hydration_workload_check') or {}).get('status')}`.",
        f"- Hydration backlog detected: `{dry_run.get('hydration_workload_count')}`.",
        f"- Execute confirmation present: `{summary.get('pipeline_contract', {}).get('execute_confirmation_present')}`.",
        f"- Import/classification/AI/localization/browser execution: `{import_results.get('status')}` / `{classification_results.get('status')}` / `{ai_results.get('status')}` / `{localization_results.get('status')}` / `{browser_validation.get('status')}`.",
        f"- Full S2 target met / safe to merge claim: `{str(target_met_claim).lower()}` / `{str(safe_to_merge_claim).lower()}`.",
        "",
        "## Gate 0 Schema / Backup / Source Roots",
        f"- Schema ensure status: `{schema_ensure.get('status')}`.",
        f"- Migration path used: `{schema_ensure.get('path_used') or 'not_needed'}`.",
        f"- Dynamic sync tables missing before count: `{len(schema_ensure.get('tables_missing_before', []) or [])}`.",
        f"- Dynamic sync tables missing after count: `{len(schema_ensure.get('tables_missing_after', []) or [])}`.",
        f"- Additive only: `{schema_ensure.get('additive_only')}`.",
        f"- Drop/truncate/delete/reset: `{gate0.get('safety', {}).get('drop_truncate_delete_reset')}`.",
        f"- Source root registration requested: `{source_registration.get('requested')}`.",
        f"- Source root registration count: `{source_registration.get('registered_count')}`.",
        f"- Public root paths redacted: `true`.",
        "",
        "## Gate 1 Readiness Proof",
        f"- Branch: `{summary['branch']}`.",
        f"- Runtime readiness validated run head SHA: `{readiness_head}`.",
        f"- Python env passed: `{readiness.get('python_env', {}).get('check_python_env_passed')}`.",
        f"- DB identity matched app settings: `{readiness.get('app_settings_db_identity_matches_execution_db')}`.",
        f"- Dynamic sync missing table count: `{len(readiness.get('dynamic_schema', {}).get('tables_missing', []) or [])}`.",
        f"- Active source roots: `{root_counts.get('active_count')}`.",
        f"- Backup proof exists/valid: `{readiness.get('backup_recovery', {}).get('proof_exists')}` / `{readiness.get('backup_recovery', {}).get('valid')}`.",
        f"- AI model local/downloaded: `{readiness.get('ai_model', {}).get('model_downloaded')}`.",
        f"- LLM localization operator-approved: `{readiness.get('llm_localization', {}).get('operator_approved')}`.",
        f"- Proper-noun search safeguard: `{readiness.get('proper_noun_safeguards', {}).get('search_alias_trust_policy')}`.",
        "",
        "## Head Evidence",
        f"- Validated run head SHA: `{head_evidence.get('validated_run_head_sha')}`.",
        f"- Report generation head SHA: `{head_evidence.get('report_generation_head_sha')}`.",
        f"- Current PR head SHA: `{head_evidence.get('current_pr_head_sha')}`.",
        f"- Current PR head scope: {head_evidence.get('current_pr_head_sha_scope')}.",
        "- Top-level ambiguous `head_sha` is intentionally omitted.",
        "",
        "## Fresh Dry-Run Proof",
        f"- Dry-run executed: `{dry_run.get('executed')}`.",
        f"- Total seen: `{dry_run.get('total_seen')}`.",
        f"- Source scope expected minimum: `{(dry_run.get('source_scope_check') or {}).get('expected_min_items')}`.",
        f"- Source scope passed: `{(dry_run.get('source_scope_check') or {}).get('passed')}`.",
        f"- Pending new: `{dry_run.get('pending_new')}`.",
        f"- Pending changed: `{dry_run.get('pending_changed')}`.",
        f"- Pending deferred: `{dry_run.get('pending_deferred')}`.",
        f"- Unsupported: `{dry_run.get('unsupported')}`.",
        f"- Failed: `{dry_run.get('failed')}`.",
        f"- Missing: `{dry_run.get('missing')}`.",
        f"- Cloud-only / iCloud unavailable: `{dry_run.get('cloud_only_or_icloud_unavailable')}`.",
        f"- Hydration workload count: `{dry_run.get('hydration_workload_count')}`.",
        f"- Actual cloud/read failures observed in dry-run: `{dry_run.get('actual_cloud_failure_count')}`.",
        f"- Hydration backlog policy: `hydration_backlog_detected`, `controlled_hydration_in_progress` when execute runs, `hydration_failure_threshold_not_exceeded` until actual failures exceed budget.",
        f"- Estimated import batches: `{dry_run.get('estimated_import_batches')}`.",
        f"- Estimated AI tagging workload: `{dry_run.get('estimated_ai_tagging_workload')}`.",
        "",
        "## Execution Result",
        f"- Import status: `{import_results.get('status')}`; imported/reused/failed: `{import_results.get('imported')}` / `{import_results.get('reused_existing')}` / `{import_results.get('failed')}`.",
        f"- Hydration attempted/succeeded/failed: `{import_results.get('hydration_attempted')}` / `{import_results.get('hydrated_success')}` / `{import_results.get('hydration_failures')}`.",
        f"- Hydration failure budget denominator: `hydration_attempted`; threshold exceeded: `{(import_results.get('hydration_failure_budget') or {}).get('threshold_exceeded')}`.",
        f"- Unsupported sidecar / desired-media unsupported: `{import_results.get('unsupported_sidecar')}` / `{import_results.get('unsupported_desired_media')}`.",
        f"- Desired-media support gap extensions: `{json.dumps(desired_media_gap.get('extensions') or [], ensure_ascii=False)}`; count `{desired_media_gap.get('unsupported_desired_media_count')}`.",
        f"- Classification status: `{classification_results.get('status')}`; failed: `{classification_results.get('failed')}`.",
        f"- AI tagging status: `{ai_results.get('status')}`; tagged/reused/failed: `{ai_results.get('tagged')}` / `{ai_results.get('reused_existing')}` / `{ai_results.get('failed')}`.",
        f"- LLM localization status: `{localization_results.get('status')}`; translated/failed/skipped/remaining: `{localization_results.get('translated')}` / `{localization_results.get('failed')}` / `{localization_results.get('skipped')}` / `{localization_results.get('remaining_missing')}`.",
        f"- LLM provider-call audit: dedicated provider batches `{llm_audit.get('dedicated_localization_provider_batch_calls')}`, background auto-translation calls `{llm_audit.get('background_auto_translation_calls')}`, provider call lower bound `{llm_audit.get('provider_call_count_lower_bound')}`, translated tag units recorded `{llm_audit.get('translated_tag_units_recorded')}`.",
        f"- Dynamic source item localization status: `{source_item_status.get('status')}`; updated to localized `{source_item_status.get('updated_to_localized')}`.",
        f"- Browser validation status: `{browser_validation.get('status')}`.",
        "",
        "## Public / Private Artifact Boundary",
        "- Public artifacts are aggregate-only and path-redacted.",
        "- Private ledgers are local under `.local_manifests/phase-4.7-s2-baseline-full-import-ai-localization/` and are not committed.",
        "",
        "## Required Next Step",
    ]
    if target_met_claim:
        lines.extend(
            [
                "- PR #113 is in closeout and browser/manual validation handoff; do not start S3 from this PR.",
                "- Keep desired-media support gaps visible for S2F backfill: `.heic`, `.heif`, `.jfif`, `.mov`, `.mp4`, `.pic`.",
                "- Keep production/development lane separation in force before any future feature branch touches runtime state.",
                "- Future production writes still require PR review, executable contracts, backup proof, production dry-run where applicable, browser validation, redaction checks, and explicit execute confirmation.",
            ]
        )
    else:
        lines.extend(
            [
                "- If backup proof is missing, create a private PostgreSQL backup proof and rerun with `--backup-proof-path` plus recovery notes.",
                "- If schema setup is pending, rerun with backup proof and `--approve-schema-setup` to use the existing dynamic sync migration path.",
                "- If source roots are missing, register one or more valid roots with `--register-source-root --source-root <path> --source-label <label>` or the Admin UI/API.",
                "- If `source_scope_check.status` is `source_scope_mismatch`, correct the approved source root before any import.",
                "- A high cloud-placeholder count is hydration workload, not a failure. Stop only if actual hydration/read/copy/import failure budgets are exceeded.",
                f"- Rerun with `--execute --confirm-execution {CONFIRM_PHRASE}` only after readiness and source scope pass.",
            ]
        )
    return "\n".join(lines) + "\n"


def scan_public_output(value: Any) -> dict[str, Any]:
    from scripts.phase_contracts.contract_checks import scan_public_payload

    findings = scan_public_payload(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=json_default)
    for pattern in SENSITIVE_TEXT_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append({"code": "sensitive_text", "match_length": len(match.group(0))})
    return {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
        "checked_payloads": ["public_json_payload", "public_markdown_text"],
    }


def write_outputs(args: argparse.Namespace, summary: dict[str, Any]) -> dict[str, Any]:
    output_dir = prepare_private_output_dir(args)

    write_json(output_dir / "readiness-proof.json", summary["readiness"])
    write_json(output_dir / "fresh-dynamic-sync-dry-run.json", summary["dynamic_sync_dry_run"])
    dry_run_ledgers = (summary.get("dynamic_sync_dry_run") or {}).get("private_ledgers") or {}
    execution_ledgers = summary.get("execution_private_ledgers") or {}
    execution_present = bool((summary.get("import_results") or {}).get("executed"))
    write_jsonl(output_dir / "hydration-ledger.jsonl", execution_ledgers.get("hydration_rows") or [])
    write_jsonl(output_dir / "import-item-ledger.jsonl", execution_ledgers.get("import_item_rows") or [])
    write_jsonl(
        output_dir / "unsupported-or-deferred.jsonl",
        [
            *(dry_run_ledgers.get("unsupported_or_deferred_rows") or []),
            *(execution_ledgers.get("unsupported_or_deferred_rows") or []),
        ],
    )
    write_jsonl(
        output_dir / "cloud-deferred.jsonl",
        filter_cloud_deferred_rows(
            [
                *(([] if execution_present else (dry_run_ledgers.get("cloud_deferred_rows") or []))),
                *(execution_ledgers.get("cloud_deferred_rows") or []),
            ],
            execution_present=execution_present,
        ),
    )
    write_jsonl(
        output_dir / "batch-summary.jsonl",
        [
            *(dry_run_ledgers.get("batch_summary_rows") or []),
            *(execution_ledgers.get("batch_summary_rows") or []),
        ],
    )
    write_jsonl(output_dir / "classification-ledger.jsonl", execution_ledgers.get("classification_rows") or [])
    write_jsonl(output_dir / "ai-tagging-ledger.jsonl", execution_ledgers.get("ai_tagging_rows") or [])
    write_jsonl(output_dir / "localization-ledger.jsonl", execution_ledgers.get("localization_rows") or [])
    write_json(output_dir / "llm-localization-audit.json", summary["llm_localization_audit"])
    write_json(output_dir / "browser-validation.json", summary["browser_validation"])

    public_markdown = public_report_markdown(summary)
    gate0 = summary.get("gate0") or {}
    gate0_schema = gate0.get("schema") or {}
    gate0_schema_ensure = gate0_schema.get("ensure") or {}
    source_registration = gate0.get("input_root_registration") or {}

    def opaque_items(values: Iterable[Any], prefix: str) -> list[str]:
        return [f"{prefix}_{index + 1}" for index, _value in enumerate(values or [])]

    def public_schema_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        tables_present = list(snapshot.get("tables_present") or [])
        tables_missing = list(snapshot.get("tables_missing") or [])
        indexes_present = snapshot.get("indexes_present") or {}
        return {
            "tables_present_count": len(tables_present),
            "tables_missing": opaque_items(tables_missing, "dynamic_table"),
            "tables_missing_count": len(tables_missing),
            "index_count_total": sum(len(indexes) for indexes in indexes_present.values()),
        }

    public_gate0 = {
        "status": gate0.get("status"),
        "blockers": gate0.get("blockers"),
        "warnings": gate0.get("warnings"),
        "db_identity": gate0.get("db_identity"),
        "storage_identity": gate0.get("storage_identity"),
        "backup_recovery": {
            key: value
            for key, value in (gate0.get("backup_recovery") or {}).items()
            if key not in {"private_path", "private_dump_path"}
        },
        "schema": {
            "before": public_schema_snapshot(gate0_schema.get("before") or {}),
            "after": public_schema_snapshot(gate0_schema.get("after") or {}),
            "ensure": {
                "status": gate0_schema_ensure.get("status"),
                "ran": gate0_schema_ensure.get("ran"),
                "approved": gate0_schema_ensure.get("approved"),
                "path_used": gate0_schema_ensure.get("path_used"),
                "tables_missing_before_count": len(gate0_schema_ensure.get("tables_missing_before", []) or []),
                "tables_missing_after_count": len(gate0_schema_ensure.get("tables_missing_after", []) or []),
                "tables_missing_after": opaque_items(
                    gate0_schema_ensure.get("tables_missing_after", []) or [], "dynamic_table"
                ),
                "additive_only": gate0_schema_ensure.get("additive_only"),
                "destructive_operations": gate0_schema_ensure.get("destructive_operations"),
                "production_media_source_mutation": gate0_schema_ensure.get("production_media_source_mutation"),
                "import_classification_ai_localization_executed": gate0_schema_ensure.get(
                    "import_classification_ai_localization_executed"
                ),
            },
        },
        "input_root_registration": {
            "requested": source_registration.get("requested"),
            "input_source": source_registration.get("input_source"),
            "registration_requested": source_registration.get("registration_requested"),
            "replace_other_active_inputs": source_registration.get("replace_source_roots"),
            "registered_count": source_registration.get("registered_count"),
            "validated_count": source_registration.get("validated_count"),
            "failed_count": source_registration.get("failed_count"),
            "deactivated_other_active_count": source_registration.get("deactivated_other_active_count"),
            "roots": [
                {
                    "run_local_label": root.get("run_local_label"),
                    "valid": root.get("valid"),
                    "issues": root.get("issues"),
                    "is_active": root.get("is_active"),
                    "auto_sync_enabled": root.get("auto_sync_enabled"),
                    "path_redacted": True,
                }
                for root in source_registration.get("roots", [])
            ],
            "instructions": source_registration.get("instructions"),
        },
        "safety": gate0.get("safety"),
    }
    public_dry_run = {
        key: value
        for key, value in (summary.get("dynamic_sync_dry_run") or {}).items()
        if key not in {"private_ledgers"}
    }
    public_summary = {
        key: value
        for key, value in summary.items()
        if key not in {"execution_private_ledgers"}
    }
    public_summary["gate0"] = public_gate0
    public_summary["dynamic_sync_dry_run"] = public_dry_run
    readiness_git = dict(summary["readiness"].get("git") or {})
    if "head_sha" in readiness_git:
        readiness_git["validated_run_head_sha"] = readiness_git.pop("head_sha")
    public_summary["readiness"] = {
        "passed": summary["readiness"].get("passed"),
        "blockers": summary["readiness"].get("blockers"),
        "warnings": summary["readiness"].get("warnings"),
        "python_env": summary["readiness"].get("python_env"),
        "git": readiness_git,
        "db_identity": {
            "host": summary["readiness"].get("db_identity", {}).get("host"),
            "port": summary["readiness"].get("db_identity", {}).get("port"),
            "database": summary["readiness"].get("db_identity", {}).get("database"),
            "connected_database": summary["readiness"].get("db_identity", {}).get("connected_database"),
            "user_configured": summary["readiness"].get("db_identity", {}).get("username_present"),
            "db_resolution": {
                "runner_matches_app_equivalent": summary["readiness"]
                .get("db_identity", {})
                .get("db_resolution", {})
                .get("runner_matches_app_equivalent"),
                "urls_match": summary["readiness"].get("db_identity", {}).get("db_resolution", {}).get("urls_match"),
                "expected_database": summary["readiness"]
                .get("db_identity", {})
                .get("db_resolution", {})
                .get("expected_database"),
            },
        },
        "app_settings_db_identity_matches_execution_db": summary["readiness"].get("app_settings_db_identity_matches_execution_db"),
        "production_storage": summary["readiness"].get("production_storage"),
        "dynamic_schema": {
            "tables_present_count": len(summary["readiness"].get("dynamic_schema", {}).get("tables_present") or []),
            "tables_missing": opaque_items(
                summary["readiness"].get("dynamic_schema", {}).get("tables_missing") or [], "dynamic_table"
            ),
            "tables_missing_count": len(summary["readiness"].get("dynamic_schema", {}).get("tables_missing") or []),
        },
        "input_root_counts": summary["readiness"].get("source_roots"),
        "backup_recovery": {
            key: value
            for key, value in (summary["readiness"].get("backup_recovery") or {}).items()
            if key not in {"private_path", "private_dump_path"}
        },
        "ai_model": summary["readiness"].get("ai_model"),
        "llm_localization": {
            "operator_approved": summary["readiness"].get("llm_localization", {}).get("operator_approved"),
            "enabled": summary["readiness"].get("llm_localization", {}).get("enabled"),
            "provider_configured": summary["readiness"].get("llm_localization", {}).get("provider_configured"),
            "model_configured": summary["readiness"].get("llm_localization", {}).get("model_configured"),
            "base_url_configured": summary["readiness"].get("llm_localization", {}).get("base_url_configured"),
            "auth_material_configured": summary["readiness"].get("llm_localization", {}).get("api_key_configured"),
            "auto_enabled": summary["readiness"].get("llm_localization", {}).get("auto_enabled"),
            "background_enabled": summary["readiness"].get("llm_localization", {}).get("background_enabled"),
            "provider": summary["readiness"].get("llm_localization", {}).get("provider"),
            "sensitive_values_recorded": summary["readiness"].get("llm_localization", {}).get("secrets_recorded"),
        },
        "proper_noun_safeguards": summary["readiness"].get("proper_noun_safeguards"),
        "automatic_production_sync": summary["readiness"].get("automatic_production_sync"),
    }
    redaction = scan_public_output({"json": public_summary, "markdown": public_markdown})
    summary["public_redaction"] = redaction
    public_summary["public_redaction"] = redaction
    if not redaction["passed"]:
        raise S2BlockedError("public_redaction_failed")
    if args.write_public_report:
        write_text(PUBLIC_REPORT_MD, public_markdown)
        write_json(PUBLIC_REPORT_JSON, public_summary)
    write_json(output_dir / "public-redaction-check.json", redaction)
    write_json(
        output_dir / "run-summary-private.json",
        {
            **summary,
            "private": {
                "output_dir": str(output_dir),
                "backup_proof_path": str(args.backup_proof_path) if args.backup_proof_path else None,
                "recovery_notes": args.recovery_notes,
                "private_paths_committed": False,
            },
        },
    )
    return public_summary


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    if args.execute and args.confirm_execution != CONFIRM_PHRASE:
        raise S2BlockedError("execute_confirmation_missing_or_wrong")
    gate0 = run_gate0_preparation(args)
    readiness = collect_readiness(args, gate0=gate0)
    dry_run: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    if readiness.get("passed"):
        dry_run = run_fresh_dynamic_sync_dry_run(args, readiness)
        source_scope = dry_run.get("source_scope_check") or {}
        if args.execute and source_scope.get("passed", True):
            execution = run_execute_stages(args, readiness, dry_run)
    summary = build_summary(args, readiness, dry_run=dry_run, execution=execution)
    public_summary = write_outputs(args, summary)
    return public_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--readiness", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execution", default="")
    parser.add_argument("--expected-python", default=str(ROOT / "venv" / "Scripts" / "python.exe"))
    parser.add_argument("--expected-db-name", default="blombooru")
    parser.add_argument("--expected-storage-root", default="")
    parser.add_argument("--backup-proof-path", type=Path, default=None)
    parser.add_argument("--create-backup-proof", action="store_true")
    parser.add_argument("--backup-output-dir", type=Path, default=None)
    parser.add_argument("--recovery-notes", default="")
    parser.add_argument("--approve-schema-setup", action="store_true")
    parser.add_argument("--approve-llm-localization", action="store_true")
    parser.add_argument("--source-root", action="append", default=[])
    parser.add_argument("--source-label", action="append", default=[])
    parser.add_argument("--register-source-root", action="store_true")
    parser.add_argument("--replace-source-roots", action="store_true")
    parser.add_argument("--dynamic-sync-max-files", type=int, default=None)
    parser.add_argument("--expected-source-min-items", type=int, default=DEFAULT_EXPECTED_SOURCE_MIN_ITEMS)
    parser.add_argument("--estimated-batch-size", type=int, default=100)
    parser.add_argument("--import-batch-size", type=int, default=200)
    parser.add_argument("--max-import-items", type=int, default=0)
    parser.add_argument("--hydration-timeout-seconds", type=int, default=180)
    parser.add_argument("--hydration-failure-max-items", type=int, default=DEFAULT_HYDRATION_FAILURE_MAX_ITEMS)
    parser.add_argument("--hydration-failure-max-rate", type=float, default=DEFAULT_HYDRATION_FAILURE_MAX_RATE)
    parser.add_argument("--import-failure-max-items", type=int, default=DEFAULT_IMPORT_FAILURE_MAX_ITEMS)
    parser.add_argument("--import-failure-max-rate", type=float, default=DEFAULT_IMPORT_FAILURE_MAX_RATE)
    parser.add_argument("--classification-failure-max-items", type=int, default=DEFAULT_CLASSIFICATION_FAILURE_MAX_ITEMS)
    parser.add_argument("--classification-failure-max-rate", type=float, default=DEFAULT_CLASSIFICATION_FAILURE_MAX_RATE)
    parser.add_argument("--ai-failure-max-items", type=int, default=DEFAULT_AI_FAILURE_MAX_ITEMS)
    parser.add_argument("--ai-failure-max-rate", type=float, default=DEFAULT_AI_FAILURE_MAX_RATE)
    parser.add_argument("--localization-failure-max-items", type=int, default=DEFAULT_LOCALIZATION_FAILURE_MAX_ITEMS)
    parser.add_argument("--localization-failure-max-rate", type=float, default=DEFAULT_LOCALIZATION_FAILURE_MAX_RATE)
    parser.add_argument("--max-ai-items", type=int, default=0)
    parser.add_argument("--localization-max-tags", type=int, default=0)
    parser.add_argument("--localization-batch-size", type=int, default=50)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-public-report", action="store_true")
    parser.add_argument("--run-id", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.run_id:
        args.run_id = f"phase47-s2-{uuid.uuid4().hex[:12]}"
    try:
        summary = run_pipeline(args)
    except S2BlockedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=json_default))
    stop_statuses = {
        "source_scope_mismatch",
        "hydration_read_failure_threshold_exceeded",
        "import_failure_threshold_exceeded",
        "classification_failure_threshold_exceeded",
        "ai_failure_threshold_exceeded",
        "localization_failure_threshold_exceeded",
        "blocked_disk_space_resource_safety",
    }
    status = str(summary.get("status", ""))
    return 2 if status.startswith("blocked") or status in stop_statuses else 0


if __name__ == "__main__":
    raise SystemExit(main())
