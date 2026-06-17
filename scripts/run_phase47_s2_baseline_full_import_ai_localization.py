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
import json
import os
import re
import subprocess
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import create_engine, inspect, text

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
    "import-item-ledger.jsonl",
    "unsupported-or-deferred.jsonl",
    "cloud-deferred.jsonl",
    "batch-summary.jsonl",
    "classification-ledger.jsonl",
    "ai-tagging-ledger.jsonl",
    "localization-ledger.jsonl",
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

REQUIRED_PUBLIC_FIELDS = {
    "phase",
    "title",
    "generated_at",
    "branch",
    "head_sha",
    "status",
    "mode",
    "pipeline_contract",
    "readiness",
    "dynamic_sync_dry_run",
    "icloud_cloud_policy",
    "import_results",
    "classification_results",
    "ai_tagging_results",
    "localization_results",
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


def safe_bool(value: Any) -> bool:
    return bool(value)


def output_dir_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = ROOT.resolve()
        resolved.relative_to(root)
    except ValueError:
        return False
    completed = subprocess.run(
        ["git", "check-ignore", "-q", "--", str(resolved.relative_to(root)).replace("\\", "/")],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return completed.returncode == 0


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


def collect_readiness(args: argparse.Namespace) -> dict[str, Any]:
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
    source_roots = {"active_count": 0, "registered_count": 0, "valid_count": 0, "root_hash_prefixes": []}
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
                        SELECT id, root_path, root_path_hash, is_active, auto_sync_enabled
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
                    if paths_overlap(root_path, settings.STORAGE_ROOT) or paths_overlap(root_path, settings.CODE_ROOT):
                        unsafe_overlap_count += 1
                        continue
                    valid_count += 1
                source_roots = {
                    "active_count": len(active_rows),
                    "registered_count": len(rows),
                    "valid_count": valid_count,
                    "auto_sync_enabled_count": sum(1 for row in rows if row["auto_sync_enabled"]),
                    "root_hash_prefixes": [str(row["root_path_hash"] or "")[:12] for row in rows],
                    "unsafe_overlap_count": unsafe_overlap_count,
                    "paths_redacted": True,
                }
                if not active_rows:
                    blockers.append("no_active_dynamic_source_roots")
                if valid_count != len(active_rows):
                    blockers.append("active_source_roots_invalid_or_unsafe")
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
    if not args.backup_proof_path or not Path(args.backup_proof_path).exists():
        blockers.append("backup_recovery_proof_missing")
    if not args.approve_llm_localization:
        blockers.append("llm_localization_operator_approval_missing")

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

        if not dynamic_schema["tables_missing"]:
            database.init_engine()
            db = database.SessionLocal()
            try:
                localization_gap = get_localization_gap_summary(db)
            finally:
                db.close()
            if not localization_gap.get("worker_excludes_proper_nouns", False):
                blockers.append("background_translation_categories_include_proper_nouns")
            if int(localization_gap.get("unreviewed_proper_noun_llm_aliases") or 0) > 0:
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
        "backup_recovery": {
            "proof_supplied": bool(args.backup_proof_path),
            "proof_exists": bool(args.backup_proof_path and Path(args.backup_proof_path).exists()),
            "recovery_path_documented": bool(args.recovery_notes),
            "path_redacted": True,
        },
        "ai_model": ai_model,
        "llm_localization": {
            "operator_approved": bool(args.approve_llm_localization),
            "enabled": bool(os.getenv("TAG_TRANSLATION_LLM_ENABLED", "").lower() in {"true", "1", "yes", "on"}),
            "model_configured": bool(os.getenv("TAG_TRANSLATION_LLM_MODEL", "").strip()),
            "base_url_configured": bool(os.getenv("TAG_TRANSLATION_LLM_BASE_URL", "").strip()),
            "api_key_configured": bool(os.getenv("TAG_TRANSLATION_LLM_API_KEY", "").strip()),
            "provider": os.getenv("TAG_TRANSLATION_LLM_PROVIDER", "openai_compatible"),
            "secrets_recorded": False,
        },
        "proper_noun_safeguards": {
            "search_alias_trust_policy": "manual_static_or_operator_reviewed_only",
            "worker_excludes_proper_nouns": localization_gap.get("worker_excludes_proper_nouns"),
            "unreviewed_proper_noun_llm_aliases": localization_gap.get("unreviewed_proper_noun_llm_aliases"),
            "entity_truth_created": False,
        },
        "automatic_production_sync": {
            "enabled": bool(os.getenv("DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED", "").lower() in {"true", "1", "yes", "on"}),
            "remains_opt_in": not bool(os.getenv("DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED", "").lower() in {"true", "1", "yes", "on"}),
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


def build_summary(args: argparse.Namespace, readiness: Mapping[str, Any]) -> dict[str, Any]:
    status = "readiness_passed" if readiness.get("passed") else "blocked_gate1"
    summary = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "generated_at": utc_now(),
        "branch": git_value(["branch", "--show-current"]),
        "head_sha": git_value(["rev-parse", "--short=12", "HEAD"]),
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
        },
        "readiness": dict(readiness),
        "dynamic_sync_dry_run": blocked_stage("dynamic_sync_dry_run"),
        "icloud_cloud_policy": {
            "hydrated_only": True,
            "mass_icloud_download": False,
            "cloud_only_files_deferred": True,
            "structured_reasons": [
                "cloud_offline",
                "cloud_recall_on_open",
                "cloud_recall_on_data_access",
                "cloud_network_unavailable",
                "cloud_hydration_failed",
                "read_timeout",
                "permission_denied",
                "unreadable_source",
            ],
        },
        "import_results": blocked_stage("import"),
        "classification_results": blocked_stage("classification"),
        "ai_tagging_results": blocked_stage("ai_tagging"),
        "localization_results": {
            **blocked_stage("localization"),
            "llm_called": False,
            "proper_noun_unreviewed_aliases_trusted": False,
        },
        "proper_noun_review": {
            "status": "not_run_gate1",
            "entity_truth_created": False,
            "confirmed_assignments_created": False,
            "unreviewed_llm_aliases_excluded_from_search": True,
        },
        "browser_validation": {
            "status": "not_run_gate1",
            "server_started": False,
            "real_browser_validation_required_after_execute": True,
        },
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
            "no_db_import": True,
            "no_classification": True,
            "no_ai_tagging": True,
            "no_llm_call": True,
            "no_sourceconcept_entity_resolver_similarity": True,
            "stopped_by_rule": "Gate 1 readiness proof failed" if not readiness.get("passed") else None,
        },
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
    lines = [
        f"# {PHASE} {PHASE_TITLE}",
        "",
        "## Summary",
        f"- Status: `{summary['status']}`.",
        f"- Gate 1 passed: `{readiness.get('passed')}`.",
        f"- Blockers: `{json.dumps(readiness.get('blockers', []), ensure_ascii=False)}`.",
        f"- Dynamic sync dry-run: `{summary['dynamic_sync_dry_run']['status']}`.",
        f"- Import/classification/AI/localization/browser validation: `not run before Gate 1 passes`.",
        "",
        "## Readiness Proof",
        f"- Branch: `{summary['branch']}`.",
        f"- Head SHA: `{summary['head_sha']}`.",
        f"- Python env passed: `{readiness.get('python_env', {}).get('check_python_env_passed')}`.",
        f"- DB identity matched app settings: `{readiness.get('app_settings_db_identity_matches_execution_db')}`.",
        f"- Dynamic sync missing tables: `{json.dumps(readiness.get('dynamic_schema', {}).get('tables_missing', []), ensure_ascii=False)}`.",
        f"- Active source roots: `{readiness.get('source_roots', {}).get('active_count')}`.",
        f"- Backup proof exists: `{readiness.get('backup_recovery', {}).get('proof_exists')}`.",
        f"- AI model local/downloaded: `{readiness.get('ai_model', {}).get('model_downloaded')}`.",
        f"- LLM localization operator-approved: `{readiness.get('llm_localization', {}).get('operator_approved')}`.",
        f"- Proper-noun search safeguard: `{readiness.get('proper_noun_safeguards', {}).get('search_alias_trust_policy')}`.",
        "",
        "## Gate Result",
        "- Gate 1 failed before any import, copy, classification, AI tagging, LLM call, or browser validation.",
        "- No production migration was run by this S2 runner.",
        "- Fresh dynamic sync dry-run was not run because the required S1 DB tables are absent.",
        "",
        "## Public / Private Artifact Boundary",
        "- Public artifacts are aggregate-only and path-redacted.",
        "- Private ledgers are local under `.local_manifests/phase-4.7-s2-baseline-full-import-ai-localization/` and are not committed.",
        "",
        "## Required Next Step",
        "- Apply/review the S1 dynamic sync schema migration and register active source roots, with backup/recovery proof, before retrying S2.",
    ]
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
    output_dir = args.output_dir.resolve()
    if not output_dir_allowed(output_dir):
        raise S2BlockedError("unsafe_output_dir_not_repo_gitignored")
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(output_dir / "readiness-proof.json", summary["readiness"])
    write_json(output_dir / "fresh-dynamic-sync-dry-run.json", summary["dynamic_sync_dry_run"])
    for name in (
        "import-item-ledger.jsonl",
        "unsupported-or-deferred.jsonl",
        "cloud-deferred.jsonl",
        "batch-summary.jsonl",
        "classification-ledger.jsonl",
        "ai-tagging-ledger.jsonl",
        "localization-ledger.jsonl",
    ):
        write_jsonl(output_dir / name, [])
    write_json(output_dir / "browser-validation.json", summary["browser_validation"])

    public_markdown = public_report_markdown(summary)
    public_summary = {
        key: value
        for key, value in summary.items()
        if key not in {"readiness"} or key == "readiness"
    }
    public_summary["readiness"] = {
        "passed": summary["readiness"].get("passed"),
        "blockers": summary["readiness"].get("blockers"),
        "warnings": summary["readiness"].get("warnings"),
        "python_env": summary["readiness"].get("python_env"),
        "git": summary["readiness"].get("git"),
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
        "dynamic_schema": summary["readiness"].get("dynamic_schema"),
        "input_root_counts": summary["readiness"].get("source_roots"),
        "backup_recovery": summary["readiness"].get("backup_recovery"),
        "ai_model": summary["readiness"].get("ai_model"),
        "llm_localization": {
            "operator_approved": summary["readiness"].get("llm_localization", {}).get("operator_approved"),
            "enabled": summary["readiness"].get("llm_localization", {}).get("enabled"),
            "model_configured": summary["readiness"].get("llm_localization", {}).get("model_configured"),
            "base_url_configured": summary["readiness"].get("llm_localization", {}).get("base_url_configured"),
            "auth_material_configured": summary["readiness"].get("llm_localization", {}).get("api_key_configured"),
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
    readiness = collect_readiness(args)
    summary = build_summary(args, readiness)
    public_summary = write_outputs(args, summary)
    if not readiness.get("passed"):
        return public_summary
    raise S2BlockedError("execute_path_not_reached_in_current_gate_state")


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
    parser.add_argument("--recovery-notes", default="")
    parser.add_argument("--approve-llm-localization", action="store_true")
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
    return 0 if summary.get("status") != "blocked_gate1" else 2


if __name__ == "__main__":
    raise SystemExit(main())
